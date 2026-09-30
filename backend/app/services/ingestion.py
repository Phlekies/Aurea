"""Atomic local audio assets, bounded ingestion, and expiry-based cleanup."""

import logging
import re
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import soundfile as sf
from pydantic import TypeAdapter

from app.audio.ffmpeg import (
    FORMATS,
    create_playback,
    decode_audio,
    probe_audio,
    validate_filename,
)
from app.audio.waveform import create_waveform
from app.config import Settings
from app.domain.audio import AudioAsset, AudioConfig, Waveform
from app.domain.errors import (
    AudioExpired,
    AudioNotFound,
    AudioServiceUnavailable,
    AudioTooLarge,
    InvalidAudioFile,
)

logger = logging.getLogger("aurea.ingestion")
ASSET_ID = re.compile(r"^[a-f0-9]{32}$")


class AudioService:
    """One app-scoped service; JSON assets survive restart until their expiry."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.storage_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = threading.BoundedSemaphore(2)
        self.asset_adapter = TypeAdapter(AudioAsset)
        self.waveform_adapter = TypeAdapter(Waveform)

    def config(self) -> AudioConfig:
        """Expose the deployment's actual limits to the upload screen."""
        return AudioConfig(
            formats=list(FORMATS),
            max_upload_bytes=self.settings.max_upload_bytes,
            max_duration_seconds=self.settings.max_duration_seconds,
            retention_seconds=self.settings.retention_seconds,
            min_sample_rate=self.settings.min_sample_rate,
            max_sample_rate=self.settings.max_sample_rate,
        )

    def ingest(self, source: BinaryIO, filename: str | None, mime: str | None) -> AudioAsset:
        """Save unchanged upload, decode, preview, then atomically publish its manifest."""
        name, extension = validate_filename(filename, mime)
        if not self.capacity.acquire(blocking=False):
            raise AudioServiceUnavailable(
                "El servicio está ocupado. Reintenta dentro de un momento."
            )
        asset_id = uuid4().hex
        staging = self.root / f".pending-{asset_id}"
        started = time.perf_counter()
        try:
            staging.mkdir()
            original = staging / f"original.{extension}"
            size = self._copy_upload(source, original)
            probe = probe_audio(original, extension, self.settings)
            decoded = staging / "decoded.wav"
            decode_audio(original, decoded, self.settings)
            info = sf.info(decoded)
            duration = info.frames / info.samplerate
            if duration <= 0 or (info.samplerate, info.channels) != (
                probe.sample_rate,
                probe.channels,
            ):
                raise InvalidAudioFile("El audio decodificado no coincide con los metadatos.")
            if duration > self.settings.max_duration_seconds + 1 / info.samplerate:
                raise AudioTooLarge("El audio supera la duración máxima permitida.")
            if decoded.stat().st_size > self.settings.max_decoded_bytes:
                raise AudioTooLarge("El audio decodificado supera el límite permitido.")
            waveform = create_waveform(decoded)
            create_playback(decoded, staging / "playback.wav", self.settings)
            created = datetime.now(UTC)
            asset = AudioAsset(
                id=asset_id,
                filename=name,
                format=extension,
                codec=probe.codec,
                bitrate=probe.bitrate,
                size_bytes=size,
                sample_rate=info.samplerate,
                channels=info.channels,
                frames=info.frames,
                duration_seconds=duration,
                created_at=created,
                expires_at=created + timedelta(seconds=self.settings.retention_seconds),
            )
            (staging / "waveform.json").write_bytes(self.waveform_adapter.dump_json(waveform))
            (staging / "asset.json").write_bytes(self.asset_adapter.dump_json(asset))
            staging.rename(self.root / asset_id)
            logger.info(
                "audio_ingested id=%s seconds=%.3f frames=%d rate=%d channels=%d bytes=%d",
                asset_id,
                time.perf_counter() - started,
                info.frames,
                info.samplerate,
                info.channels,
                size,
            )
            return asset
        except (sf.LibsndfileError, OSError) as error:
            logger.warning("audio_storage_failed id=%s", asset_id)
            raise InvalidAudioFile("No se pudo guardar o decodificar el audio.") from error
        finally:
            try:
                if staging.exists():
                    self._remove(staging)
            except OSError:
                logger.warning("audio_staging_cleanup_failed id=%s", asset_id)
            finally:
                self.capacity.release()

    def _copy_upload(self, source: BinaryIO, destination: Path) -> int:
        size = 0
        with destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > self.settings.max_upload_bytes:
                    raise AudioTooLarge("El archivo supera el tamaño máximo permitido.")
                target.write(chunk)
        if size == 0:
            raise InvalidAudioFile("El archivo está vacío.")
        return size

    def get(self, asset_id: str) -> AudioAsset:
        """Return an unexpired manifest using only a validated opaque identifier."""
        path = self.directory(asset_id) / "asset.json"
        if not path.is_file():
            raise AudioNotFound("No se encontró esta grabación.")
        asset = self.asset_adapter.validate_json(path.read_bytes())
        if asset.expires_at <= datetime.now(UTC):
            raise AudioExpired("La grabación ha caducado. Vuelve a subir el archivo.")
        return asset

    def directory(self, asset_id: str) -> Path:
        """Resolve an asset path, refusing traversal and symlinks outside storage."""
        if not ASSET_ID.fullmatch(asset_id):
            raise AudioNotFound("No se encontró esta grabación.")
        path = self.root / asset_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise AudioNotFound("No se encontró esta grabación.")
        return path

    def waveform(self, asset_id: str) -> Waveform:
        """Read the cached waveform only after checking asset expiry."""
        self.get(asset_id)
        return self.waveform_adapter.validate_json(
            (self.directory(asset_id) / "waveform.json").read_bytes()
        )

    def cleanup(self) -> None:
        """Delete expired assets and abandoned staging dirs within owned storage only."""
        now = datetime.now(UTC)
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            try:
                if ASSET_ID.fullmatch(path.name):
                    asset = self.asset_adapter.validate_json((path / "asset.json").read_bytes())
                    if asset.expires_at <= now:
                        self._remove(path)
                elif re.fullmatch(r"\.pending-[a-f0-9]{32}", path.name):
                    # Three timed decoder commands plus margin; never remove a live upload.
                    if now.timestamp() - path.stat().st_mtime > (
                        self.settings.command_timeout_seconds * 3 + 3600
                    ):
                        self._remove(path)
            except (OSError, ValueError):
                logger.warning("audio_cleanup_failed id=%s", path.name)

    def _remove(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Refusing to remove a path outside audio storage")
        shutil.rmtree(path)
