"""Non-destructive rendering of processing plans with before/after measurement.

The original and decoded files are never modified. A rendering lives in the asset's
``processed/`` directory: float32 ``processed.wav``, a 16-bit ``playback.wav`` for the
browser, ``waveform.json`` and ``report.json`` (the processing manifest). Everything
is produced in a staging directory and published by renaming the whole directory, so
a reader never sees a partial rendering. One rendering runs at a time per process;
re-requesting the same executable plan reuses the published result.
"""

import json
import logging
import re
import shutil
import threading
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import soundfile as sf
from pydantic import TypeAdapter

from app.analysis.analyzer import analyze_audio
from app.analysis.artifacts import measure_artifacts
from app.audio.ffmpeg import create_playback
from app.audio.waveform import create_waveform
from app.diagnostics.engine import diagnose_audio
from app.domain.analysis import AudioAnalysis
from app.domain.audio import AudioAsset, Waveform
from app.domain.errors import (
    AudioError,
    AudioNotFound,
    AudioServiceUnavailable,
    InvalidAudioFile,
    ProcessingFailed,
)
from app.domain.processing import (
    AppliedStep,
    ProcessingMetrics,
    ProcessingPlan,
    ProcessingReport,
)
from app.pipeline.noise_plan import recommend_processing_plan
from app.pipeline.registry import ProcessorRegistry, default_registry
from app.pipeline.runner import PreparedStep, prepare, run_plan
from app.services.analysis import AnalysisService

PIPELINE_VERSION = "0.8.0"
logger = logging.getLogger("aurea.processing")
STAGING_ID = re.compile(r"^\.processing-[a-f0-9]{32}$")
# Damage that level or filter changes can hide from a detector but never repair.
# Clipping stays reported until a declipping processor exists (plan phase 11).
UNREPAIRED_DAMAGE = {"clipping": "saturación digital"}


class ProcessingNotFound(AudioError):
    """The asset exists but has no valid rendering from the current pipeline."""

    code = "processing_not_found"
    status_code = 404


class ProcessingServiceUnavailable(AudioError):
    """Processing capacity or its metering dependency is temporarily unavailable."""

    code = "processing_service_unavailable"
    status_code = 503


def _es(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def processing_metrics(analysis: AudioAnalysis) -> ProcessingMetrics:
    """Summarize one rendering's levels, background and detected problems."""
    profile = analysis.noise_profile
    return ProcessingMetrics(
        peak_dbfs=analysis.peak_dbfs,
        rms_dbfs=analysis.rms_dbfs,
        integrated_lufs=analysis.integrated_lufs,
        true_peak_dbtp=analysis.true_peak_dbtp,
        dc_offset=analysis.dc_offset,
        subbass_percent=analysis.bands[0].percent if analysis.bands else 0.0,
        noise_rms_dbfs=profile.rms_dbfs if profile else None,
        estimated_snr_db=analysis.estimated_snr_db,
        detected=[item.code for item in analysis.diagnostics if item.detected],
    )


class ProcessingService:
    """App-scoped renderer built on the ingestion and analysis services."""

    def __init__(
        self,
        analysis_service: AnalysisService,
        registry: ProcessorRegistry | None = None,
    ) -> None:
        self.analysis_service = analysis_service
        self.audio_service = analysis_service.audio_service
        self.registry = registry or default_registry()
        self.capacity = threading.BoundedSemaphore(1)
        self.adapter = TypeAdapter(ProcessingReport)
        self.waveform_adapter = TypeAdapter(Waveform)
        self._active_staging: set[Path] = set()
        self._staging_lock = threading.Lock()

    def recommend(
        self, asset_id: str, algorithm: str = "wiener", strength: str = "balanced"
    ) -> ProcessingPlan:
        """Recommended corrective plan; requires a current analysis report."""
        return recommend_processing_plan(self.analysis_service.get(asset_id), algorithm, strength)

    def report(self, asset_id: str) -> ProcessingReport:
        """Published processing manifest, without starting any rendering."""
        asset = self._asset(asset_id)
        report = self._published(asset)
        if report is None:
            raise ProcessingNotFound("Esta grabación todavía no tiene una versión procesada.")
        return report

    def waveform(self, asset_id: str) -> Waveform:
        """Peaks of the published rendering."""
        self.report(asset_id)
        return self.waveform_adapter.validate_json(
            (self._output(asset_id) / "waveform.json").read_bytes()
        )

    def playback(self, asset_id: str) -> Path:
        """Browser-compatible WAV of the published rendering."""
        self.report(asset_id)
        return self._output(asset_id) / "playback.wav"

    def process(self, asset_id: str, plan: ProcessingPlan | None = None) -> ProcessingReport:
        """Render ``plan`` (or the recommended plan) and publish it with its manifest."""
        asset = self._asset(asset_id)
        analysis = self.analysis_service.get(asset_id)
        requested = plan or recommend_processing_plan(analysis)
        prepared = prepare(requested, self.registry, asset.sample_rate, asset.channels)
        requested = replace(
            requested,
            steps=[
                replace(step, parameters=ready.parameters)
                for step, ready in zip(requested.steps, prepared, strict=True)
            ],
        )
        published = self._published(asset)
        if published is not None and self._same_execution(published, prepared):
            return published
        if not self.capacity.acquire(blocking=False):
            raise ProcessingServiceUnavailable(
                "El procesador está ocupado. Reintenta dentro de un momento."
            )
        staging = self.audio_service.root / f".processing-{uuid4().hex}"
        started = time.perf_counter()
        try:
            with self._staging_lock:
                self._active_staging.add(staging)
            output = staging / "processed"
            output.mkdir(parents=True)
            snapshot = staging / "decoded.wav"
            source = self.audio_service.directory(asset_id) / "decoded.wav"
            with source.open("rb") as incoming, snapshot.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            rendered = output / "processed.wav"
            result = run_plan(snapshot, rendered, prepared, self.registry)
            after = self._measure(rendered, asset)
            settings = self.audio_service.settings
            waveform = create_waveform(rendered)
            (output / "waveform.json").write_bytes(self.waveform_adapter.dump_json(waveform))
            create_playback(rendered, output / "playback.wav", settings)
            warnings = []
            if result.safety_gain_db:
                warnings.append(
                    "La señal procesada superaba la escala completa; se aplicó una "
                    f"reducción de seguridad de {_es(-result.safety_gain_db)} dB sin recortar."
                )
            before = processing_metrics(analysis)
            after_metrics = processing_metrics(after)
            artifacts = (
                measure_artifacts(snapshot, rendered, analysis.speech_activity)
                if any(step.enabled and step.processor == "noise_reduction" for step in prepared)
                else None
            )
            if artifacts:
                if artifacts.significant_speech_loss:
                    warnings.append(
                        "La energía de las regiones de voz baja más de 6 dB. "
                        "El nivelado o la compresión también pueden producir este cambio. "
                        "Escucha la voz; si se deteriora, prueba ajustes más suaves."
                    )
                if artifacts.excessive_reduction:
                    warnings.append(
                        "La energía total baja más de 18 dB; puede haber una reducción excesiva."
                    )
                if artifacts.possible_musical_noise:
                    warnings.append(
                        "Aumentan los picos espectrales aislados del fondo; posible ruido musical. "
                        "Compara escuchando las pausas."
                    )
            for code, name in UNREPAIRED_DAMAGE.items():
                if code in before.detected and code not in after_metrics.detected:
                    after_metrics = replace(after_metrics, detected=[*after_metrics.detected, code])
                    warnings.append(
                        f"La {name} del original no se repara en esta versión: un cambio de "
                        "nivel puede ocultarla al detector, pero las crestas siguen recortadas."
                    )
            elapsed = time.perf_counter() - started
            report = ProcessingReport(
                audio_id=asset_id,
                pipeline_version=PIPELINE_VERSION,
                plan=requested,
                steps=[
                    AppliedStep(step.processor, step.enabled, step.parameters, seconds)
                    for step, seconds in zip(prepared, result.step_seconds, strict=True)
                ],
                sample_rate=asset.sample_rate,
                channels=asset.channels,
                duration_seconds=asset.duration_seconds,
                safety_gain_db=result.safety_gain_db,
                warnings=warnings,
                processing_seconds=elapsed,
                real_time_factor=elapsed / asset.duration_seconds,
                before=before,
                after=after_metrics,
                artifacts=artifacts,
                gain_envelopes=result.gain_envelopes,
            )
            payload = json.dumps(asdict(report), allow_nan=False, separators=(",", ":"))
            (output / "report.json").write_text(payload, encoding="utf-8")
            self._publish(asset_id, staging, output)
            logger.info(
                "audio_processed id=%s seconds=%.3f rtf=%.4f pipeline=%s steps=%s "
                "step_seconds=%s safety_gain_db=%.2f",
                asset_id,
                elapsed,
                report.real_time_factor,
                PIPELINE_VERSION,
                ",".join(step.processor for step in prepared if step.enabled) or "none",
                ",".join(f"{seconds:.3f}" for seconds in result.step_seconds),
                result.safety_gain_db,
            )
            return report
        except AudioServiceUnavailable as error:
            raise ProcessingServiceUnavailable(
                "No se pudo medir el audio procesado. Reintenta dentro de un momento."
            ) from error
        except (OSError, ValueError, sf.LibsndfileError, InvalidAudioFile) as error:
            self._asset(asset_id)
            logger.warning("audio_processing_failed id=%s", asset_id)
            raise ProcessingFailed(
                "No se pudo completar el procesado. El original no se ha modificado."
            ) from error
        finally:
            try:
                if staging.exists():
                    self._remove_staging(staging)
            except OSError:
                logger.warning("audio_processing_cleanup_failed id=%s", asset_id)
            finally:
                with self._staging_lock:
                    self._active_staging.discard(staging)
                self.capacity.release()

    def _measure(self, rendered: Path, asset: AudioAsset) -> AudioAnalysis:
        settings = self.audio_service.settings
        measured = analyze_audio(
            rendered,
            asset.id,
            ffmpeg=settings.ffmpeg,
            timeout_seconds=settings.command_timeout_seconds,
        )
        diagnosis = diagnose_audio(rendered, measured)
        return replace(
            measured,
            diagnostics=diagnosis.diagnostics,
            speech_activity=diagnosis.speech_activity,
            noise_profile=diagnosis.noise_profile,
            estimated_snr_db=diagnosis.estimated_snr_db,
        )

    def _publish(self, asset_id: str, staging: Path, output: Path) -> None:
        """Swap directories so readers see either the old or the new rendering."""
        self._asset(asset_id)
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
        self._asset(asset_id)

    def _output(self, asset_id: str) -> Path:
        return self.audio_service.directory(asset_id) / "processed"

    def _published(self, asset: AudioAsset) -> ProcessingReport | None:
        output = self._output(asset.id)
        path = output / "report.json"
        if output.is_symlink() or not path.is_file():
            return None
        try:
            report = self.adapter.validate_json(path.read_bytes())
            json.dumps(asdict(report), allow_nan=False)
        except (OSError, ValueError):
            logger.warning("audio_processing_report_invalid id=%s", asset.id)
            return None
        files = ("processed.wav", "playback.wav", "waveform.json")
        if (
            report.audio_id != asset.id
            or report.pipeline_version != PIPELINE_VERSION
            or (report.sample_rate, report.channels) != (asset.sample_rate, asset.channels)
            or abs(report.duration_seconds - asset.duration_seconds) > 1 / asset.sample_rate
            or (
                any(step.enabled and step.processor == "noise_reduction" for step in report.steps)
                != (report.artifacts is not None)
            )
            or not all((output / name).is_file() for name in files)
            or {curve.step_index for curve in report.gain_envelopes}
            != {
                index
                for index, step in enumerate(report.steps)
                if step.enabled and step.processor in ("speech_leveler", "compressor")
            }
        ):
            return None
        try:
            validated = prepare(report.plan, self.registry, asset.sample_rate, asset.channels)
        except AudioError:
            return None
        if not self._same_execution(report, validated):
            return None
        return report

    @staticmethod
    def _same_execution(report: ProcessingReport, prepared: list[PreparedStep]) -> bool:
        return [(s.processor, s.enabled, s.parameters) for s in report.steps] == [
            (s.processor, s.enabled, s.parameters) for s in prepared
        ]

    def _asset(self, asset_id: str) -> AudioAsset:
        try:
            return self.audio_service.get(asset_id)
        except FileNotFoundError as error:
            raise AudioNotFound("No se encontró esta grabación.") from error

    def cleanup(self) -> None:
        """Remove stale renderings left behind by interrupted processes."""
        now = datetime.now(UTC).timestamp()
        cutoff = self.audio_service.settings.command_timeout_seconds * 4 + 3600
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
                logger.warning("audio_processing_staging_cleanup_failed id=%s", path.name)

    def _remove_staging(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.audio_service.root:
            raise ValueError("Refusing to remove a path outside audio storage")
        shutil.rmtree(path)
