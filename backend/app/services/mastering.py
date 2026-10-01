"""Publish an independently checked master, tied to the current corrective rendering."""

import hashlib
import logging
import re
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import soundfile as sf
from pydantic import TypeAdapter

from app.audio.ffmpeg import create_playback
from app.audio.waveform import create_waveform
from app.domain.audio import Waveform
from app.domain.errors import AudioError, AudioServiceUnavailable
from app.domain.mastering import MasteringReport
from app.mastering.engine import master_audio
from app.mastering.presets import load_presets
from app.services.processing import ProcessingNotFound, ProcessingService

MASTERING_VERSION = "0.9.1"
STAGING_ID = re.compile(r"^\.mastering-[a-f0-9]{32}$")
logger = logging.getLogger("aurea.mastering")


class MasteringNotFound(AudioError):
    code = "mastering_not_found"
    status_code = 404


class InvalidMasteringPreset(AudioError):
    code = "invalid_mastering_preset"
    status_code = 422


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MasteringService:
    def __init__(self, processing_service: ProcessingService) -> None:
        self.processing_service = processing_service
        self.audio_service = processing_service.audio_service
        self.presets = load_presets()
        self.adapter = TypeAdapter(MasteringReport)
        self.waveform_adapter = TypeAdapter(Waveform)
        self._active_staging: set[Path] = set()
        self._staging_lock = threading.Lock()

    def _revision(self, asset_id: str) -> str:
        report = self.processing_service.report(asset_id)
        return hashlib.sha256(self.processing_service.adapter.dump_json(report)).hexdigest()

    def _output(self, asset_id: str) -> Path:
        return self.audio_service.directory(asset_id) / "mastered"

    def report(self, asset_id: str) -> MasteringReport:
        asset = self.processing_service._asset(asset_id)
        try:
            revision = self._revision(asset_id)
            output = self._output(asset_id)
            files = ("master.wav", "playback.wav", "waveform.json", "report.json")
            if output.is_symlink() or not all(
                (output / name).is_file() and not (output / name).is_symlink() for name in files
            ):
                raise ValueError("Missing master files")
            report = self.adapter.validate_json((output / "report.json").read_bytes())
            info = sf.info(output / "master.wav")
            if (
                report.audio_id != asset_id
                or report.mastering_version != MASTERING_VERSION
                or report.source_revision != revision
                or self.presets.get(report.preset.id) != report.preset
                or (report.sample_rate, report.channels) != (asset.sample_rate, asset.channels)
                or abs(report.duration_seconds - asset.duration_seconds) > 1 / asset.sample_rate
                or (info.samplerate, info.channels, info.frames, info.subtype)
                != (report.sample_rate, report.channels, report.frames, "PCM_24")
                or file_digest(output / "master.wav") != report.output_sha256
            ):
                raise ValueError("Stale or altered master")
            return report
        except (OSError, ValueError, sf.LibsndfileError, ProcessingNotFound) as error:
            raise MasteringNotFound(
                "Esta grabación no tiene un máster verificado para las correcciones actuales."
            ) from error

    def master(self, asset_id: str, preset_id: str = "podcast_standard") -> MasteringReport:
        return self._master(asset_id, preset_id, capacity_reserved=False)

    def _master(self, asset_id: str, preset_id: str, *, capacity_reserved: bool) -> MasteringReport:
        self.processing_service._asset(asset_id)
        preset = self.presets.get(preset_id)
        if preset is None:
            raise InvalidMasteringPreset("El objetivo de masterización no existe.")
        # Require a published correction first; never silently choose a corrective plan.
        self._revision(asset_id)
        try:
            previous = self.report(asset_id)
            if previous.preset == preset:
                return previous
        except MasteringNotFound:
            pass
        capacity = self.processing_service.capacity
        if not capacity_reserved and not capacity.acquire(blocking=False):
            raise AudioServiceUnavailable("El procesador está ocupado. Reintenta en un momento.")
        staging = self.audio_service.root / f".mastering-{uuid4().hex}"
        started = time.perf_counter()
        try:
            with self._staging_lock:
                self._active_staging.add(staging)
            output = staging / "mastered"
            output.mkdir(parents=True)
            revision = self._revision(asset_id)
            source = self.audio_service.directory(asset_id) / "processed" / "processed.wav"
            snapshot = staging / "source.wav"
            with source.open("rb") as incoming, snapshot.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            rendered = output / "master.wav"
            result = master_audio(snapshot, rendered, preset, self.audio_service.settings)
            waveform = create_waveform(rendered)
            (output / "waveform.json").write_bytes(self.waveform_adapter.dump_json(waveform))
            create_playback(rendered, output / "playback.wav", self.audio_service.settings)
            assert result.before.integrated_lufs is not None
            report = MasteringReport(
                audio_id=asset_id,
                mastering_version=MASTERING_VERSION,
                source_revision=revision,
                preset=preset,
                sample_rate=result.output.sample_rate,
                channels=result.output.channels,
                frames=result.output.frames,
                duration_seconds=result.output.duration,
                bit_depth=24,
                before=result.before,
                after=result.after,
                qc=result.qc,
                normalization_mode=result.mode,
                requested_gain_db=preset.target_lufs - result.before.integrated_lufs,
                limiter_ceiling_dbtp=result.ceiling,
                attempts=result.attempts,
                processing_seconds=time.perf_counter() - started,
                output_sha256=file_digest(rendered),
            )
            (output / "report.json").write_bytes(self.adapter.dump_json(report))
            self._publish(asset_id, staging, output)
            logger.info(
                "audio_mastered id=%s preset=%s lufs=%.1f tp=%.3f mode=%s attempts=%s seconds=%.3f",
                asset_id,
                preset.id,
                report.after.integrated_lufs,
                report.after.true_peak_dbtp,
                report.normalization_mode,
                report.attempts,
                report.processing_seconds,
            )
            return report
        except (OSError, ValueError, sf.LibsndfileError) as error:
            logger.warning("audio_mastering_failed id=%s", asset_id)
            raise AudioServiceUnavailable(
                "No se pudo completar la masterización. Reintenta; el audio corregido se conserva."
            ) from error
        finally:
            try:
                if staging.exists():
                    self._remove_staging(staging)
            except OSError:
                logger.warning("audio_mastering_cleanup_failed id=%s", asset_id)
            finally:
                with self._staging_lock:
                    self._active_staging.discard(staging)
                if not capacity_reserved:
                    capacity.release()

    def _publish(self, asset_id: str, staging: Path, output: Path) -> None:
        self.processing_service._asset(asset_id)
        target = self._output(asset_id)
        if target.exists():
            target.replace(staging / "previous")
        try:
            output.replace(target)
        except OSError:
            previous = staging / "previous"
            if previous.exists() and not target.exists():
                previous.replace(target)
            raise
        self.processing_service._asset(asset_id)

    def waveform(self, asset_id: str) -> Waveform:
        self.report(asset_id)
        return self.waveform_adapter.validate_json(
            (self._output(asset_id) / "waveform.json").read_bytes()
        )

    def playback(self, asset_id: str) -> Path:
        self.report(asset_id)
        return self._output(asset_id) / "playback.wav"

    def download(self, asset_id: str) -> Path:
        # report() verifies QC, provenance and the SHA-256 of the actual exported WAV.
        self.report(asset_id)
        return self._output(asset_id) / "master.wav"

    def cleanup(self) -> None:
        now = datetime.now(UTC).timestamp()
        cutoff = self.audio_service.settings.command_timeout_seconds * 8 + 3600
        for path in self.audio_service.root.iterdir():
            if not STAGING_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            with self._staging_lock:
                if path in self._active_staging:
                    continue
            try:
                if now - path.stat().st_mtime > cutoff:
                    self._remove_staging(path)
            except OSError:
                logger.warning("audio_mastering_staging_cleanup_failed id=%s", path.name)

    def _remove_staging(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.audio_service.root:
            raise ValueError("Refusing to remove a path outside audio storage")
        shutil.rmtree(path)
