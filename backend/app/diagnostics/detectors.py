"""Conservative, explainable signal heuristics; scores are not probabilities.

Thresholds are versioned engineering defaults for spoken-word recordings, not
clinical labels or calibrated quality scores. Temporal speech candidates are an
internal spectral proxy: this phase does not expose a VAD or a noise profile.
"""

import math
from collections.abc import Callable
from typing import Protocol

from app.diagnostics.features import DiagnosticFeatures, WindowFeatures
from app.domain.analysis import AudioAnalysis
from app.domain.diagnostics import Diagnostic, EvidenceValue

MAX_EVENT_TIMES = 30


class Detector(Protocol):
    """Shared interface for a diagnostic using features and measured context."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        """Return one observation with finite evidence and versioned thresholds."""
        ...


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _db(power: float) -> float | None:
    return 10 * math.log10(power) if power > 0 else None


def _ratio(part: float, whole: float) -> float:
    return part / whole if whole > 0 else 0.0


def _duration(audio: DiagnosticFeatures) -> float:
    return audio.frames / audio.sample_rate


def _evidence_windows(
    audio: DiagnosticFeatures,
) -> tuple[list[WindowFeatures], list[WindowFeatures]]:
    return (
        [window for window in audio.windows if window.speech_candidate],
        [window for window in audio.windows if window.low_activity],
    )


def _energy(windows: list[WindowFeatures], field: str = "power") -> float:
    return sum(float(getattr(window, field)) * window.duration_seconds for window in windows)


def _average_power(windows: list[WindowFeatures], field: str = "power") -> float:
    return _ratio(_energy(windows, field), sum(window.duration_seconds for window in windows))


def _usable(audio: DiagnosticFeatures, context: AudioAnalysis, minimum: float) -> bool:
    return (
        _duration(audio) >= minimum
        and context.rms_dbfs is not None
        and context.silence_percent < 99
    )


def _result(
    code: str,
    detected: bool,
    severity: float,
    confidence: float,
    message: str,
    evidence: dict[str, EvidenceValue],
    parameters: dict[str, EvidenceValue],
) -> Diagnostic:
    return Diagnostic(
        code,
        detected,
        _clamp(severity) if detected else 0.0,
        _clamp(confidence),
        message,
        evidence,
        parameters,
    )


class ClippingDetector:
    """Distinguish repeated near-rail plateaus from isolated sinusoidal peaks."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        clipping = audio.clipping
        denominator = max(1, clipping.sample_count_per_channel * audio.channels)
        hard_percent = 100 * sum(clipping.hard_counts) / denominator
        near_percent = 100 * sum(clipping.near_counts) / denominator
        flat_run = max(clipping.max_flat_top_runs, default=0)
        hard_run = max(clipping.max_same_sign_hard_runs, default=0)
        near_run = max(clipping.max_near_runs, default=0)
        flat_count = sum(clipping.flat_top_counts)
        # A unit-amplitude sinusoid has isolated near-rail samples. A flat top
        # and repeated same-sign rail samples provide stronger clipping evidence.
        plateau = flat_run >= 3 and flat_count >= 3
        concentration = near_percent >= 20 and near_run >= 3
        detected = plateau or concentration
        evidence: dict[str, EvidenceValue] = {
            "hard_samples_per_channel": [float(n) for n in clipping.hard_counts],
            "near_samples_per_channel": [float(n) for n in clipping.near_counts],
            "hard_sample_percent": hard_percent,
            "near_sample_percent": near_percent,
            "max_hard_run_samples": hard_run,
            "max_near_run_samples": near_run,
            "flat_top_samples": flat_count,
            "max_flat_top_run_samples": flat_run,
        }
        parameters: dict[str, EvidenceValue] = {
            "hard_amplitude_threshold": 0.999,
            "near_amplitude_threshold": 0.98,
            "flat_top_difference_threshold": 1e-6,
            "minimum_flat_top_run_samples": 3,
            "near_concentration_threshold_percent": 20.0,
            "minimum_near_run_samples": 3,
            "full_confidence_minimum_duration_seconds": 0.1,
        }
        confidence = 0.88 if plateau else 0.58 if concentration else 0.8
        if context.peak_dbfs is None:
            confidence, message = 0.15, "No hay señal para comprobar la saturación."
        elif detected:
            message = (
                "Hay crestas aplanadas cerca del límite digital; posible clipping."
                if plateau
                else "Muchas muestras se concentran cerca del límite digital; posible saturación."
            )
        else:
            message = (
                "No se han encontrado crestas aplanadas ni concentración anormal "
                "en el límite digital."
            )
        if _duration(audio) < 0.1:
            confidence = min(confidence, 0.35)
            message += " La grabación es demasiado breve para valorar su frecuencia."
        severity = max(hard_percent / 5, near_percent / 50, min(flat_run / 50, 0.7))
        return _result("clipping", detected, severity, confidence, message, evidence, parameters)


class HumDetector:
    """Compare persistent 50/60 Hz lines with neighboring spectral energy."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        candidates = []
        for candidate in audio.hum:
            strong = [
                line
                for line in candidate.harmonics
                if line.contrast_db >= 10 and line.persistence >= 0.6
            ]
            fundamental = next(
                (line for line in strong if line.frequency_hz == candidate.base_hz), None
            )
            percent = 100 * _ratio(sum(line.power for line in strong), candidate.total_ac_power)
            # Require the fundamental: harmonic male speech at 100/120 Hz must
            # not be labelled as mains hum merely because it has higher harmonics.
            # A lone sinusoidal line needs stronger contrast and near-constant
            # presence, because intermittent speech or music rarely keeps both.
            detected = (
                fundamental is not None
                and percent >= 1
                and (
                    len(strong) >= 2
                    or fundamental.contrast_db >= 20
                    and fundamental.persistence >= 0.9
                )
            )
            score = percent * (len(strong) + 1)
            candidates.append((detected, score, candidate, strong, percent))
        selected = max(candidates, key=lambda row: (row[0], row[1])) if candidates else None
        available = selected is not None and selected[2].window_count >= 2
        parameters: dict[str, EvidenceValue] = {
            "candidate_frequencies_hz": [50.0, 60.0],
            "harmonic_contrast_threshold_db": 10.0,
            "harmonic_persistence_threshold": 0.6,
            "minimum_harmonic_energy_percent": 1.0,
            "minimum_duration_seconds": 2.0,
            "minimum_analyzed_windows": 2,
            "minimum_strong_harmonics": 2,
            "fundamental_required": True,
            "single_fundamental_contrast_threshold_db": 20.0,
            "single_fundamental_persistence_threshold": 0.9,
            "maximum_silence_percent": 99.0,
            "frequency_tolerance_hz": 2.0,
        }
        evidence: dict[str, EvidenceValue] = {
            "base_frequency_hz": selected[2].base_hz if selected else None,
            "harmonic_frequencies_hz": [line.frequency_hz for line in selected[3]]
            if selected
            else [],
            "harmonic_contrast_db": [line.contrast_db for line in selected[3]] if selected else [],
            "harmonic_persistence": [line.persistence for line in selected[3]] if selected else [],
            "harmonic_energy_percent": selected[4] if selected else 0.0,
            "analyzed_windows": selected[2].window_count if selected else 0,
        }
        if not available or not _usable(audio, context, 2.0):
            return _result(
                "hum",
                False,
                0,
                0.15,
                "Falta señal o duración para comprobar un zumbido estable de 50 o 60 Hz.",
                evidence,
                parameters,
            )
        assert selected is not None
        detected, _, candidate, strong, percent = selected
        confidence = (0.82 if len(strong) >= 2 else 0.6) if detected else 0.7
        message = (
            f"Se observa un patrón persistente de {candidate.base_hz:g} Hz "
            "compatible con zumbido eléctrico."
            if detected
            else "No se observa un patrón persistente de 50 o 60 Hz con suficiente evidencia."
        )
        return _result("hum", detected, percent / 35, confidence, message, evidence, parameters)


class RumbleDetector:
    """Check persistent 20--80 Hz energy outside speech-like activity."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        speech, _ = _evidence_windows(audio)
        # Very strong sub-bass may keep pauses above a relative amplitude gate.
        # Low-frequency dominance without a speech candidate still supplies
        # conservative low-voice-probability evidence for this detector only.
        quiet = [
            w
            for w in audio.windows
            if w.low_activity or (not w.speech_candidate and _ratio(w.low_power, w.power) >= 0.7)
        ]
        all_windows = list(audio.windows)
        global_percent = 100 * _ratio(_energy(all_windows, "low_power"), _energy(all_windows))
        quiet_percent = 100 * _ratio(_energy(quiet, "low_power"), _energy(quiet))
        quiet_db = _db(_average_power(quiet, "low_power"))
        evidence: dict[str, EvidenceValue] = {
            "low_band_energy_percent": global_percent,
            "low_activity_low_band_percent": quiet_percent,
            "low_activity_low_rms_dbfs": quiet_db,
            "speech_windows": len(speech),
            "low_activity_windows": len(quiet),
        }
        parameters: dict[str, EvidenceValue] = {
            "band_low_hz": 20.0,
            "band_high_hz": 80.0,
            "global_energy_threshold_percent": 8.0,
            "low_activity_energy_threshold_percent": 40.0,
            "low_voice_probability_low_band_threshold_percent": 70.0,
            "minimum_low_rms_dbfs": -55.0,
            "minimum_low_activity_seconds": 0.3,
            "minimum_duration_seconds": 1.0,
            "maximum_silence_percent": 99.0,
        }
        available = _usable(audio, context, 1) and sum(w.duration_seconds for w in quiet) >= 0.3
        if not available:
            return _result(
                "rumble",
                False,
                0,
                0.2,
                "Faltan intervalos de baja actividad para distinguir ruido grave de una voz grave.",
                evidence,
                parameters,
            )
        detected = (
            global_percent >= 8 and quiet_percent >= 40 and quiet_db is not None and quiet_db >= -55
        )
        return _result(
            "rumble",
            detected,
            global_percent / 50,
            0.7 if detected else 0.6,
            "Hay energía grave persistente durante intervalos de baja actividad; "
            "posible ruido de fondo grave."
            if detected
            else "No hay suficiente energía grave persistente fuera de la voz para señalar rumble.",
            evidence,
            parameters,
        )


class LowLevelDetector:
    """Report low measured loudness, with an explicit RMS fallback."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        evidence: dict[str, EvidenceValue] = {
            "integrated_lufs": context.integrated_lufs,
            "rms_dbfs": context.rms_dbfs,
            "silence_percent": context.silence_percent,
        }
        parameters: dict[str, EvidenceValue] = {
            "loudness_threshold_lufs": -26.0,
            "fallback_rms_threshold_dbfs": -32.0,
            "minimum_duration_seconds": 0.4,
            "maximum_silence_percent": 95.0,
        }
        if not _usable(audio, context, 0.4) or context.silence_percent >= 95:
            return _result(
                "low_level",
                False,
                0,
                0.15,
                "La señal es demasiado breve o casi silenciosa "
                "para valorar el nivel de la grabación.",
                evidence,
                parameters,
            )
        lufs, rms = context.integrated_lufs, context.rms_dbfs
        assert rms is not None
        level, threshold = (lufs, -26.0) if lufs is not None else (rms, -32.0)
        detected = level < threshold
        message = (
            "El nivel general de la grabación es bajo y puede dificultar la escucha."
            if detected
            else "El nivel general no está por debajo del umbral de esta comprobación."
        )
        if lufs is None:
            message += " Se ha usado RMS porque el loudness integrado no está disponible."
        return _result(
            "low_level",
            detected,
            (threshold - level) / 18,
            0.85 if lufs is not None else 0.5,
            message,
            evidence,
            parameters,
        )


class HeadroomDetector:
    """Report estimated true peaks with no more than 1 dB of digital headroom."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        peak = context.true_peak_dbtp
        evidence: dict[str, EvidenceValue] = {
            "true_peak_dbtp": peak,
            "headroom_db": -peak if peak is not None else None,
        }
        parameters: dict[str, EvidenceValue] = {
            "true_peak_threshold_dbtp": -1.0,
            "true_peak_is_estimate": True,
            "full_confidence_minimum_duration_seconds": 0.1,
        }
        if peak is None:
            return _result(
                "low_headroom",
                False,
                0,
                0.15,
                "No hay señal suficiente para medir el margen hasta la saturación.",
                evidence,
                parameters,
            )
        detected = peak >= -1
        return _result(
            "low_headroom",
            detected,
            0.2 + (peak + 1) / 3,
            0.8 if _duration(audio) >= 0.1 else 0.35,
            "Los picos dejan 1 dB de margen o menos; "
            "existe riesgo de saturación al aumentar el nivel."
            if detected
            else "Los picos conservan más de 1 dB de margen según el true peak estimado.",
            evidence,
            parameters,
        )


class StationaryNoiseDetector:
    """Compare the spectral shape and power stability of low-activity frames."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        speech, _ = _evidence_windows(audio)
        noise = audio.noise
        speech_power = _average_power(speech)
        level_gap = _db(_ratio(speech_power, noise.power)) if noise.power > 0 else None
        noise_db = _db(noise.power)
        evidence: dict[str, EvidenceValue] = {
            "noise_rms_dbfs": noise_db,
            "speech_to_background_level_gap_db": level_gap,
            "psd_similarity": noise.psd_stationarity,
            "spectral_flatness": noise.flatness,
            "relative_power_std": noise.relative_power_std,
            "noise_windows": noise.window_count,
            "speech_windows": len(speech),
            "low_activity_duration_seconds": noise.duration_seconds,
        }
        parameters: dict[str, EvidenceValue] = {
            "minimum_duration_seconds": 2.0,
            "minimum_low_activity_seconds": 0.3,
            "minimum_speech_seconds": 0.3,
            "psd_similarity_threshold": 0.8,
            "spectral_flatness_threshold": 0.1,
            "maximum_relative_power_std": 0.6,
            "noise_rms_threshold_dbfs": -55.0,
            "level_gap_threshold_db": 25.0,
            "speech_probability_is_heuristic": True,
            "maximum_silence_percent": 99.0,
        }
        available = (
            _usable(audio, context, 2.0)
            and noise.duration_seconds >= 0.3
            and sum(w.duration_seconds for w in speech) >= 0.3
        )
        if not available:
            return _result(
                "stationary_noise",
                False,
                0,
                0.2,
                "Faltan intervalos comparables de voz y baja actividad "
                "para valorar ruido estacionario.",
                evidence,
                parameters,
            )
        detected = (
            noise_db is not None
            and noise_db >= -55
            and (level_gap is None or level_gap <= 25)
            and noise.psd_stationarity >= 0.8
            and noise.flatness >= 0.1
            and noise.relative_power_std <= 0.6
        )
        severity = (25 - (level_gap if level_gap is not None else 0)) / 30
        return _result(
            "stationary_noise",
            detected,
            severity,
            0.7 if detected else 0.6,
            "El fondo conserva un espectro y un nivel similares entre pausas; "
            "posible ruido estacionario."
            if detected
            else "Las pausas no muestran suficiente ruido estable para señalar este problema.",
            evidence,
            parameters,
        )


def _event_groups(
    windows: tuple[WindowFeatures, ...],
    predicate: Callable[[int, WindowFeatures], bool],
) -> list[list[WindowFeatures]]:
    groups: list[list[WindowFeatures]] = []
    current: list[WindowFeatures] = []
    for index, window in enumerate(windows):
        if predicate(index, window):
            current.append(window)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _near_speech(audio: DiagnosticFeatures, index: int, radius: int = 6) -> bool:
    return any(
        w.speech_candidate for w in audio.windows[max(0, index - radius) : index + radius + 1]
    )


class SibilanceDetector:
    """Locate short 4--10 kHz excesses near an internal speech proxy."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        speech, _ = _evidence_windows(audio)
        background = [
            w
            for w in audio.windows
            if w.low_activity or (not w.speech_candidate and _ratio(w.voice_power, w.power) < 0.02)
        ]
        background_power = _average_power(background, "sibilance_power")
        has_background = sum(w.duration_seconds for w in background) >= 0.3
        ratios = [_ratio(w.sibilance_power, w.power) for w in audio.windows]
        groups = _event_groups(
            audio.windows,
            lambda i, w: (
                ratios[i] >= 0.45
                and w.voice_power >= 1e-6
                and _near_speech(audio, i)
                and (not has_background or w.sibilance_power >= 4 * background_power)
            ),
        )
        events = [g for g in groups if 0.03 <= sum(w.duration_seconds for w in g) <= 0.4]
        evidence: dict[str, EvidenceValue] = {
            "event_count": len(events),
            "event_times_seconds": [g[0].start_seconds for g in events[:MAX_EVENT_TIMES]],
            "max_band_energy_percent": 100 * max(ratios, default=0),
            "speech_windows": len(speech),
            "background_sibilance_rms_dbfs": _db(background_power),
        }
        parameters: dict[str, EvidenceValue] = {
            "band_low_hz": 4000.0,
            "band_high_hz": min(10000.0, audio.sample_rate / 2),
            "band_energy_threshold_percent": 45.0,
            "minimum_voice_power_dbfs": -60.0,
            "minimum_event_seconds": 0.03,
            "maximum_event_seconds": 0.4,
            "minimum_duration_seconds": 1.0,
            "minimum_nyquist_hz": 6000.0,
            "minimum_background_contrast_db": 6.0,
            "maximum_background_voice_band_percent": 2.0,
            "maximum_event_times": MAX_EVENT_TIMES,
            "minimum_speech_windows": 5,
            "maximum_silence_percent": 99.0,
        }
        if not _usable(audio, context, 1) or len(speech) < 5 or audio.sample_rate / 2 < 6000:
            return _result(
                "sibilance",
                False,
                0,
                0.2,
                "Faltan voz, duración o ancho de banda "
                "para valorar sibilancia con esta heurística.",
                evidence,
                parameters,
            )
        detected = bool(events)
        severity = min(
            0.9,
            sum(sum(w.duration_seconds for w in g) for g in events)
            / max(0.3, sum(w.duration_seconds for w in speech))
            * 3,
        )
        return _result(
            "sibilance",
            detected,
            severity,
            0.6 if detected else 0.55,
            "Hay ráfagas de energía aguda próximas a la voz; posible sibilancia. "
            "Conviene escuchar esos momentos."
            if detected
            else "No se observan ráfagas agudas suficientes para señalar sibilancia.",
            evidence,
            parameters,
        )


class PlosiveDetector:
    """Locate short low-band bursts with onset evidence close to speech."""

    def analyze(self, audio: DiagnosticFeatures, context: AudioAnalysis) -> Diagnostic:
        speech, _ = _evidence_windows(audio)
        ratios = [_ratio(w.low_power, w.power) for w in audio.windows]
        groups = _event_groups(
            audio.windows,
            lambda i, w: ratios[i] >= 0.45 and w.low_power >= 1e-5 and _near_speech(audio, i),
        )
        events = [
            g
            for g in groups
            if (
                0.03 <= sum(w.duration_seconds for w in g) <= 0.24
                and max(w.onset_db for w in g) >= 6
            )
        ]
        evidence: dict[str, EvidenceValue] = {
            "event_count": len(events),
            "event_times_seconds": [g[0].start_seconds for g in events[:MAX_EVENT_TIMES]],
            "max_band_energy_percent": 100 * max(ratios, default=0),
            "speech_windows": len(speech),
        }
        parameters: dict[str, EvidenceValue] = {
            "band_low_hz": 20.0,
            "band_high_hz": 80.0,
            "band_energy_threshold_percent": 45.0,
            "minimum_low_power_dbfs": -50.0,
            "onset_threshold_db": 6.0,
            "minimum_event_seconds": 0.03,
            "maximum_event_seconds": 0.24,
            "minimum_duration_seconds": 1.0,
            "speech_neighborhood_seconds": 0.18,
            "maximum_event_times": MAX_EVENT_TIMES,
            "minimum_speech_windows": 5,
            "maximum_silence_percent": 99.0,
        }
        if not _usable(audio, context, 1) or len(speech) < 5:
            return _result(
                "plosives",
                False,
                0,
                0.2,
                "Faltan voz o duración para relacionar ráfagas graves con el inicio de la voz.",
                evidence,
                parameters,
            )
        detected = bool(events)
        return _result(
            "plosives",
            detected,
            min(0.9, len(events) * 0.2),
            0.62 if detected else 0.55,
            "Hay ráfagas graves repentinas próximas al inicio de la voz; posibles plosivas. "
            "Conviene escucharlas."
            if detected
            else "No se observan ráfagas graves repentinas suficientes para señalar plosivas.",
            evidence,
            parameters,
        )
