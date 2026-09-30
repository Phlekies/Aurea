"""Bounded, versioned audio analysis with atomic persistence and expiry checks."""

import json
import logging
import math
import re
import shutil
import threading
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from app.analysis.analyzer import ANALYZER_VERSION, analyze_audio
from app.diagnostics.engine import DIAGNOSTICS_VERSION, diagnose_audio
from app.domain.analysis import AudioAnalysis
from app.domain.audio import AudioAsset
from app.domain.diagnostics import DIAGNOSTIC_CODES
from app.domain.errors import AnalysisFailed, AudioError, AudioNotFound, AudioServiceUnavailable
from app.services.ingestion import AudioService

logger = logging.getLogger("aurea.analysis")
STAGING_ID = re.compile(r"^\.analysis-[a-f0-9]{32}$")


class AnalysisNotFound(AudioError):
    """The asset exists but has no valid analysis from the current analyzer."""

    code = "analysis_not_found"
    status_code = 404


class AnalysisServiceUnavailable(AudioError):
    """Analysis capacity, storage, or its metering dependency is unavailable."""

    code = "analysis_service_unavailable"
    status_code = 503


class AnalysisService:
    """One concurrent analysis per application; cached results survive restarts."""

    def __init__(self, audio_service: AudioService) -> None:
        self.audio_service = audio_service
        self.capacity = threading.BoundedSemaphore(1)
        self.adapter = TypeAdapter(AudioAnalysis)
        self._active_staging: set[Path] = set()
        self._staging_lock = threading.Lock()

    def get(self, asset_id: str) -> AudioAnalysis:
        """Read a valid cached result, without starting any audio computation."""
        asset = self._asset(asset_id)
        cached = self._cached(asset)
        self._asset(asset_id)
        if cached is None:
            raise AnalysisNotFound("Esta grabación todavía no tiene un análisis disponible.")
        return cached

    def analyze(self, asset_id: str) -> AudioAnalysis:
        """Analyze an immutable snapshot, then publish only while the asset is valid."""
        asset = self._asset(asset_id)
        cached = self._cached(asset)
        if cached is not None:
            self._asset(asset_id)
            return cached
        if not self.capacity.acquire(blocking=False):
            raise AnalysisServiceUnavailable(
                "El analizador está ocupado. Reintenta dentro de un momento."
            )
        staging = self.audio_service.root / f".analysis-{uuid4().hex}"
        started = time.perf_counter()
        try:
            # A second request can finish between the initial cache read and acquisition.
            asset = self._asset(asset_id)
            cached = self._cached(asset)
            if cached is not None:
                self._asset(asset_id)
                return cached
            with self._staging_lock:
                self._active_staging.add(staging)
            staging.mkdir()
            snapshot = staging / "decoded.wav"
            source = self.audio_service.directory(asset_id) / "decoded.wav"
            # Keep cleanup of the expiring asset independent of an ongoing measurement.
            with source.open("rb") as incoming, snapshot.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            settings = self.audio_service.settings
            result = analyze_audio(
                snapshot,
                asset_id,
                ffmpeg=settings.ffmpeg,
                timeout_seconds=settings.command_timeout_seconds,
            )
            try:
                observations = diagnose_audio(snapshot, result)
            except (OSError, ValueError, ArithmeticError) as error:
                logger.warning("audio_diagnosis_failed id=%s", asset_id)
                raise AnalysisFailed(
                    "No se pudo completar el diagnóstico. Reintenta dentro de un momento."
                ) from error
            result = replace(
                result, diagnostics_version=DIAGNOSTICS_VERSION, diagnostics=observations
            )
            if not self._matches(result, asset):
                raise ValueError("Analysis does not match its source asset")
            payload = json.dumps(asdict(result), allow_nan=False, separators=(",", ":"))
            self._asset(asset_id)
            temporary = staging / "analysis.json"
            temporary.write_text(payload, encoding="utf-8")
            # replace is atomic and never recreates an expired/deleted asset directory.
            self._asset(asset_id)
            temporary.replace(self.audio_service.directory(asset_id) / "analysis.json")
            self._asset(asset_id)
            logger.info(
                "audio_analyzed id=%s seconds=%.3f version=%s diagnostics=%s detected=%d",
                asset_id,
                time.perf_counter() - started,
                ANALYZER_VERSION,
                DIAGNOSTICS_VERSION,
                sum(item.detected for item in observations),
            )
            return result
        except AudioServiceUnavailable as error:
            raise AnalysisServiceUnavailable(
                "No se pudo medir el audio. El analizador no está disponible; reintenta."
            ) from error
        except (OSError, ValueError) as error:
            # Return expiry/missing errors when cleanup caused a storage race.
            self._asset(asset_id)
            logger.warning("audio_analysis_failed id=%s", asset_id)
            raise AnalysisServiceUnavailable(
                "No se pudo guardar o completar el análisis. Reintenta dentro de un momento."
            ) from error
        finally:
            try:
                if staging.exists():
                    self._remove_staging(staging)
            except OSError:
                logger.warning("audio_analysis_cleanup_failed id=%s", asset_id)
            finally:
                with self._staging_lock:
                    self._active_staging.discard(staging)
                self.capacity.release()

    def _cached(self, asset: AudioAsset) -> AudioAnalysis | None:
        path = self.audio_service.directory(asset.id) / "analysis.json"
        if not path.is_file() or path.is_symlink():
            return None
        try:
            cached = self.adapter.validate_json(path.read_bytes())
            # JSON's NaN/Infinity extensions and invalid nested values must never escape.
            json.dumps(asdict(cached), allow_nan=False)
        except (OSError, ValueError):
            logger.warning("audio_analysis_cache_invalid id=%s", asset.id)
            return None
        return cached if self._matches(cached, asset) else None

    @staticmethod
    def _matches(result: AudioAnalysis, asset: AudioAsset) -> bool:
        identity_matches = (
            result.audio_id == asset.id
            and result.analyzer_version == ANALYZER_VERSION
            and result.diagnostics_version == DIAGNOSTICS_VERSION
            and tuple(item.code for item in result.diagnostics) == DIAGNOSTIC_CODES
            and result.sample_rate == asset.sample_rate
            and result.channels == asset.channels
            and abs(result.duration_seconds - asset.duration_seconds) <= 1 / asset.sample_rate
        )
        if not identity_matches:
            return False
        tolerance = 1e-7
        time_tolerance = 1 / asset.sample_rate + 1e-9

        def levels_valid(peak: float | None, rms: float | None) -> bool:
            if peak is None or rms is None:
                return peak is None and rms is None
            return (
                math.isfinite(peak)
                and math.isfinite(rms)
                and peak <= tolerance
                and rms <= (peak + tolerance)
            )

        if not (
            levels_valid(result.peak_dbfs, result.rms_dbfs)
            and len(result.dc_offset) == result.channels
            and all(
                math.isfinite(value) and abs(value) <= 1 + tolerance for value in result.dc_offset
            )
            and 0 <= result.zero_crossing_rate <= 1
            and 0 <= result.silence_percent <= 100 + tolerance
        ):
            return False
        if result.peak_dbfs is None or result.rms_dbfs is None:
            if not (
                result.crest_factor_db is None
                and result.true_peak_dbtp is None
                and result.integrated_lufs is None
                and abs(result.silence_percent - 100) <= tolerance
            ):
                return False
        elif not (
            result.crest_factor_db is not None
            and abs(result.crest_factor_db - (result.peak_dbfs - result.rms_dbfs)) <= tolerance
            and result.true_peak_dbtp is not None
            and math.isfinite(result.true_peak_dbtp)
            and result.true_peak_dbtp >= result.peak_dbfs - tolerance
        ):
            return False
        frequencies = result.spectrum.frequencies_hz
        density = result.spectrum.psd_dbfs_per_hz
        nyquist = result.sample_rate / 2
        if not (
            1 <= len(frequencies) <= 4097
            and len(frequencies) == len(density)
            and all(math.isfinite(value) and 0 <= value <= nyquist for value in frequencies)
            and all(right > left for left, right in pairwise(frequencies))
            and all(value is None or math.isfinite(value) for value in density)
            and 1 <= len(result.bands) <= 32
            and len({band.name for band in result.bands}) == len(result.bands)
        ):
            return False
        previous_high = 0.0
        total_power = sum(band.power for band in result.bands)
        if not math.isfinite(total_power) or (result.peak_dbfs is None and total_power != 0):
            return False
        for band in result.bands:
            expected_percent = 100 * band.power / total_power if total_power else 0.0
            if not (
                abs(band.low_hz - previous_high) <= tolerance
                and band.low_hz < band.high_hz <= nyquist
                and band.power >= 0
                and 0 <= band.percent <= 100 + tolerance
                and abs(band.percent - expected_percent) <= tolerance
            ):
                return False
            previous_high = band.high_hz
        if abs(previous_high - nyquist) > tolerance:
            return False
        points = result.dynamics.points
        window_seconds = result.dynamics.window_ms / 1000
        if not (math.isfinite(window_seconds) and window_seconds > 0 and 1 <= len(points) <= 2000):
            return False
        expected_start = 0.0
        for index, point in enumerate(points):
            if not (
                abs(point.start_seconds - expected_start) <= time_tolerance
                and 0 < point.duration_seconds <= window_seconds + time_tolerance
                and (
                    index == len(points) - 1
                    or abs(point.duration_seconds - window_seconds) <= time_tolerance
                )
                and levels_valid(point.peak_dbfs, point.rms_dbfs)
                and (
                    point.peak_dbfs is None
                    or (
                        result.peak_dbfs is not None
                        and point.peak_dbfs <= result.peak_dbfs + tolerance
                    )
                )
            ):
                return False
            expected_start = point.start_seconds + point.duration_seconds
        return abs(expected_start - result.duration_seconds) <= time_tolerance

    def _asset(self, asset_id: str) -> AudioAsset:
        try:
            return self.audio_service.get(asset_id)
        except FileNotFoundError as error:
            # Cleanup can unlink the manifest between its existence check and read.
            raise AudioNotFound("No se encontró esta grabación.") from error

    def cleanup(self) -> None:
        """Remove only stale analysis snapshots left behind by interrupted processes."""
        now = datetime.now(UTC).timestamp()
        cutoff = self.audio_service.settings.command_timeout_seconds * 3 + 3600
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
                logger.warning("audio_analysis_staging_cleanup_failed id=%s", path.name)

    def _remove_staging(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.audio_service.root:
            raise ValueError("Refusing to remove a path outside audio storage")
        shutil.rmtree(path)
