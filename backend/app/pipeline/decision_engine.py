"""Deterministic, centralized rules that turn an analysis into a processing plan.

Pipeline version 0.7.0 covers the corrective stage only, in this order:
DC removal -> high-pass -> de-hum -> pre-gain. Every step is always present so the
user sees what was considered; ``enabled`` is false when no evidence supports it.
Diagnostics below ``MIN_CONFIDENCE`` never enable a step (confidence gating).

High-pass cutoff: among the presets, the lowest cutoff whose Butterworth response
reduces the background energy between 20 and 80 Hz (the rumble band) by at least
``rumble_reduction_db`` is chosen. The background spectrum is the noise profile's
0.25 s low-frequency PSD (4 Hz bins); without it the 30 ms PSD (~33 Hz bins) is
used, whose leakage biases the choice towards higher cutoffs, and the evidence says
which one was used. When even the highest preset falls short it is used and the
shortfall is reported. The share of total recording energy removed is also reported.

All thresholds live in ``RULES`` so behaviour changes without code changes. They are
engineering defaults validated on synthetic signals, not tuned on a podcast corpus.
"""

import math
from dataclasses import dataclass

from app.domain.analysis import AudioAnalysis
from app.domain.diagnostics import Diagnostic
from app.domain.processing import ParameterValue, ProcessingPlan, ProcessingStep

PLAN_VERSION = "0.8.0"


@dataclass(frozen=True)
class CorrectiveRules:
    """Tunable thresholds of the corrective stage."""

    min_confidence: float = 0.55
    dc_threshold: float = 0.001
    highpass_presets_hz: tuple[float, ...] = (60.0, 70.0, 80.0, 100.0)
    highpass_order: int = 4
    rumble_band_hz: tuple[float, float] = (20.0, 80.0)
    rumble_reduction_db: float = 10.0
    dehum_q: float = 30.0
    dehum_margin_db: float = 6.0
    dehum_attenuation_db: tuple[float, float] = (12.0, 48.0)
    pregain_target_lufs: float = -24.0
    pregain_ceiling_dbtp: float = -3.0
    pregain_minimum_db: float = 1.0


RULES = CorrectiveRules()


def _es(value: float, digits: int = 1) -> str:
    """Spanish decimal comma for user-facing reasons."""
    return f"{value:.{digits}f}".replace(".", ",")


def _diagnostic(analysis: AudioAnalysis, code: str) -> Diagnostic | None:
    return next((item for item in analysis.diagnostics if item.code == code), None)


def _supported(diagnostic: Diagnostic | None, rules: CorrectiveRules) -> bool:
    return (
        diagnostic is not None
        and diagnostic.detected
        and diagnostic.confidence >= rules.min_confidence
    )


def _butterworth_power(frequency: float, cutoff: float, order: int) -> float:
    """|H(f)|^2 of an analog Butterworth high-pass (bilinear warping is negligible)."""
    return 1 / (1 + (cutoff / frequency) ** (2 * order)) if frequency > 0 else 0.0


def _power(values: list[float | None]) -> list[float]:
    return [10 ** (value / 10) if value is not None else 0.0 for value in values]


def _reduction_db(
    frequencies: list[float], power: list[float], cutoff: float, rules: CorrectiveRules
) -> float | None:
    low, high = rules.rumble_band_hz
    band = [(f, p) for f, p in zip(frequencies, power, strict=True) if low <= f <= high]
    before = sum(p for _, p in band)
    after = sum(p * _butterworth_power(f, cutoff, rules.highpass_order) for f, p in band)
    if before <= 0:
        return None
    return 10 * math.log10(before / after) if after > 0 else 120.0


def _removed_percent(analysis: AudioAnalysis, cutoff: float, rules: CorrectiveRules) -> float:
    frequencies = analysis.spectrum.frequencies_hz
    power = _power(analysis.spectrum.psd_dbfs_per_hz)
    total = sum(p for f, p in zip(frequencies, power, strict=True) if f > 0)
    kept = sum(
        p * _butterworth_power(f, cutoff, rules.highpass_order)
        for f, p in zip(frequencies, power, strict=True)
        if f > 0
    )
    return 100 * (1 - kept / total) if total > 0 else 0.0


def _dc_step(analysis: AudioAnalysis, rules: CorrectiveRules) -> ProcessingStep:
    largest = max(abs(value) for value in analysis.dc_offset)
    enabled = largest >= rules.dc_threshold
    offsets = [round(value, 9) if enabled else 0.0 for value in analysis.dc_offset]
    return ProcessingStep(
        processor="dc_removal",
        enabled=enabled,
        parameters={"offsets": offsets},
        reason=(
            f"La señal tiene un desplazamiento de continua de {_es(largest, 4)} a escala "
            "completa; restarlo recupera margen y evita un transitorio en los filtros."
            if enabled
            else "El desplazamiento de continua es despreciable; no hace falta corregirlo."
        ),
        evidence={"max_abs_dc_offset": largest, "threshold": rules.dc_threshold},
    )


def _background_spectrum(analysis: AudioAnalysis) -> tuple[list[float], list[float], str]:
    profile = analysis.noise_profile
    if profile is None or not profile.frame_count:
        return [], [], "none"
    if profile.low_window_count:
        return profile.low_frequencies_hz, _power(profile.low_psd_dbfs_per_hz), "0.25 s"
    return profile.frequencies_hz, _power(profile.psd_dbfs_per_hz), "30 ms"


def _numbers(value: ParameterValue | list[str] | None) -> list[float]:
    if not isinstance(value, list):
        return []
    return [float(item) for item in value if isinstance(item, int | float)]


def _highpass_step(analysis: AudioAnalysis, rules: CorrectiveRules) -> ProcessingStep:
    rumble = _diagnostic(analysis, "rumble")
    frequencies, power, resolution = _background_spectrum(analysis)
    reductions = {
        cutoff: _reduction_db(frequencies, power, cutoff, rules)
        for cutoff in rules.highpass_presets_hz
    }
    choice = next(
        (c for c, db in reductions.items() if db is not None and db >= rules.rumble_reduction_db),
        rules.highpass_presets_hz[-1],
    )
    enabled = _supported(rumble, rules) and reductions[choice] is not None
    reduction = reductions[choice]
    cutoff = choice if enabled else rules.highpass_presets_hz[0]
    evidence: dict[str, ParameterValue] = {
        "candidate_cutoffs_hz": list(rules.highpass_presets_hz),
        "rumble_reduction_db": [
            round(value, 2) if value is not None else 0.0 for value in reductions.values()
        ],
        "target_reduction_db": rules.rumble_reduction_db,
        "estimated_energy_removed_percent": round(_removed_percent(analysis, cutoff, rules), 3),
        "background_spectrum_window": resolution,
    }
    if not enabled and _supported(rumble, rules):
        reason = (
            "Se detectó ruido grave, pero no hay pausas con espectro suficiente para elegir "
            f"un corte. Si lo activas se usará el corte más suave ({cutoff:g} Hz)."
        )
    elif not enabled:
        reason = (
            "No se ha detectado ruido grave con confianza suficiente; el filtro queda "
            f"desactivado. Si lo activas se usará el corte más suave ({cutoff:g} Hz)."
        )
    elif reduction is not None and reduction >= rules.rumble_reduction_db:
        reason = (
            f"Hay ruido grave en las pausas. {cutoff:g} Hz es el corte más bajo que lo "
            f"reduce al menos {rules.rumble_reduction_db:g} dB (≈{_es(reduction, 1)} dB)."
        )
    else:
        # Enabled implies the chosen preset has a defined reduction.
        achieved = reduction if reduction is not None else 0.0
        reason = (
            f"Hay ruido grave en las pausas que se extiende por encima de los cortes "
            f"suaves; {cutoff:g} Hz es el más alto admitido y lo reduce ≈{_es(achieved, 1)} dB."
        )
    return ProcessingStep(
        processor="high_pass",
        enabled=enabled,
        parameters={"cutoff_hz": cutoff, "order": rules.highpass_order},
        reason=reason,
        source_diagnostic="rumble",
        confidence=rumble.confidence if rumble else None,
        evidence=evidence,
    )


def _dehum_step(analysis: AudioAnalysis, rules: CorrectiveRules) -> ProcessingStep:
    hum = _diagnostic(analysis, "hum")
    enabled = _supported(hum, rules)
    evidence = hum.evidence if hum else {}
    base = evidence.get("base_frequency_hz")
    lines = _numbers(evidence.get("harmonic_frequencies_hz"))
    contrasts = _numbers(evidence.get("harmonic_contrast_db"))
    fundamental = float(base) if isinstance(base, int | float) and enabled else 50.0
    harmonics = max(1, min(10, round(max(lines) / fundamental))) if enabled and lines else 4
    strongest = max(contrasts, default=0.0)
    low, high = rules.dehum_attenuation_db
    attenuation = min(high, max(low, strongest + rules.dehum_margin_db)) if enabled else 30.0
    return ProcessingStep(
        processor="dehum",
        enabled=enabled,
        parameters={
            "fundamental_hz": fundamental,
            "harmonics": harmonics,
            "q": rules.dehum_q,
            "attenuation_db": round(attenuation, 1),
        },
        reason=(
            f"Se detectó zumbido de {fundamental:g} Hz; se atenúan {harmonics} "
            f"{'línea' if harmonics == 1 else 'líneas'} {_es(attenuation, 0)} dB, lo suficiente "
            "para bajar la más destacada al nivel de sus vecinas."
            if enabled
            else "No se ha detectado zumbido eléctrico con confianza suficiente."
        ),
        source_diagnostic="hum",
        confidence=hum.confidence if hum else None,
        evidence={"strongest_line_contrast_db": round(strongest, 2)},
    )


def _pregain_step(analysis: AudioAnalysis, rules: CorrectiveRules) -> ProcessingStep:
    low_level = _diagnostic(analysis, "low_level")
    headroom = _diagnostic(analysis, "low_headroom")
    lufs, peak = analysis.integrated_lufs, analysis.true_peak_dbtp
    gain, source, reason = 0.0, None, "El nivel de entrada es adecuado para el procesado."
    if _supported(low_level, rules) and lufs is not None and peak is not None:
        gain = min(rules.pregain_target_lufs - lufs, rules.pregain_ceiling_dbtp - peak)
        source = low_level
        reason = (
            f"La grabación es baja ({_es(lufs, 1)} LUFS); se sube hacia "
            f"{rules.pregain_target_lufs:g} LUFS sin superar {rules.pregain_ceiling_dbtp:g} dBTP."
        )
    elif _supported(headroom, rules) and peak is not None:
        gain = rules.pregain_ceiling_dbtp - peak
        source = headroom
        reason = (
            f"Los picos llegan a {_es(peak, 1)} dBTP; se bajan a "
            f"{rules.pregain_ceiling_dbtp:g} dBTP para dejar margen al procesado."
        )
    gain = max(-24.0, min(24.0, gain))
    enabled = abs(gain) >= rules.pregain_minimum_db
    if source is not None and not enabled:
        reason = "El ajuste de nivel posible es menor de 1 dB; no merece la pena aplicarlo."
    return ProcessingStep(
        processor="pre_gain",
        enabled=enabled,
        parameters={"gain_db": round(gain, 2) if enabled else 0.0},
        reason=reason,
        source_diagnostic=source.code if source else None,
        confidence=source.confidence if source else None,
        evidence={
            "integrated_lufs": lufs if lufs is not None else "no medido",
            "true_peak_dbtp": peak if peak is not None else "no medido",
            "target_lufs": rules.pregain_target_lufs,
            "ceiling_dbtp": rules.pregain_ceiling_dbtp,
        },
    )


def recommend_corrective_plan(
    analysis: AudioAnalysis, rules: CorrectiveRules = RULES
) -> ProcessingPlan:
    """Build the explainable corrective chain; identical inputs give identical plans."""
    return ProcessingPlan(
        preset="corrective",
        version=PLAN_VERSION,
        steps=[
            _dc_step(analysis, rules),
            _highpass_step(analysis, rules),
            _dehum_step(analysis, rules),
            _pregain_step(analysis, rules),
        ],
    )
