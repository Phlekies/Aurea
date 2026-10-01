"""Pure, deterministic decision engine for corrections, dynamics and mastering.

The external ProcessingPreset owns policy. Processors only execute parameters.
Candidate selection and confidence gates are distinct: medium evidence produces
an inactive recommendation retaining its proposed parameters for manual review.
Scores are heuristics validated on synthetic signals, not probabilities.
"""

import math
from dataclasses import replace
from typing import Literal

import numpy as np

from app.domain.analysis import AudioAnalysis
from app.domain.diagnostics import Diagnostic
from app.domain.mastering import MasteringPreset
from app.domain.presets import CorrectiveRules as CorrectiveRules
from app.domain.presets import DynamicsRules as DynamicsRules
from app.domain.presets import GateRules, ProcessingPreset
from app.domain.processing import ParameterValue, ProcessingPlan, ProcessingStep
from app.processors.noise_reduction import ALGORITHMS, STRENGTHS

PLAN_VERSION = "1.0.0-alpha"


RULES = CorrectiveRules()
GATE_RULES = GateRules()


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
    attenuation = min(high, max(low, strongest + rules.dehum_margin_db if enabled else 30.0))
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


DYNAMICS_RULES = DynamicsRules()


def add_dynamics(
    plan: ProcessingPlan,
    analysis: AudioAnalysis,
    rules: DynamicsRules = DYNAMICS_RULES,
    min_diagnostic_confidence: float = RULES.min_confidence,
) -> ProcessingPlan:
    """Append leveling and compression, preserving visible reasons for inactive steps."""
    activity = analysis.speech_activity
    segments = [item for item in activity.segments if item.label == "speech"] if activity else []
    level = activity.speech_rms_dbfs if activity else None
    profile = analysis.noise_profile
    noise = profile.rms_dbfs if profile and profile.rms_dbfs is not None else -60.0
    available = bool(
        activity
        and activity.speech_seconds >= rules.minimum_speech_seconds
        and level is not None
        and level > max(-50.0, noise + rules.minimum_contrast_db)
    )
    # Only windows whose centre is inside original speech regions enter the spread.
    # This is an indicator for the decision, not a loudness or speaker measurement.
    starts = np.asarray([segment.start_seconds for segment in segments])
    levels = []
    for point in analysis.dynamics.points:
        centre = point.start_seconds + point.duration_seconds / 2
        index = int(np.searchsorted(starts, centre, side="right")) - 1
        if (
            point.rms_dbfs is not None
            and point.rms_dbfs > max(-50.0, noise + rules.minimum_contrast_db)
            and index >= 0
            and centre < segments[index].end_seconds
        ):
            levels.append(point.rms_dbfs)
    spread = (
        float(np.percentile(levels, 90) - np.percentile(levels, 10)) if len(levels) >= 3 else 0.0
    )
    varying = available and spread >= rules.variation_threshold_db
    enabled = available and (
        varying
        or level is not None
        and abs(level - rules.target_rms_dbfs) >= rules.level_change_threshold_db
    )
    evidence: dict[str, ParameterValue] = {
        "speech_available": available,
        "speech_seconds": activity.speech_seconds if activity else 0.0,
        "speech_rms_dbfs": level if level is not None else -300.0,
        "speech_level_spread_db": spread,
        "level_change_threshold_db": rules.level_change_threshold_db,
        "variation_threshold_db": rules.variation_threshold_db,
    }
    leveler = ProcessingStep(
        "speech_leveler",
        enabled,
        {
            "speech_starts_seconds": [item.start_seconds for item in segments],
            "speech_ends_seconds": [item.end_seconds for item in segments],
            "noise_floor_dbfs": noise,
            "target_rms_dbfs": rules.target_rms_dbfs,
            "max_boost_db": rules.max_boost_db,
            "max_cut_db": rules.max_cut_db,
            "window_ms": rules.window_ms,
            "smoothing_ms": rules.smoothing_ms,
        },
        (
            "La voz cambia de volumen o se aleja del nivel de referencia; se ajusta "
            "suavemente dentro de sus intervalos, con refuerzo limitado y protección del fondo."
            if enabled
            else "La voz ya tiene un nivel estable y cercano a la referencia. "
            "Puedes probar el nivelador."
            if available
            else "No hay suficiente voz distinguible del fondo para nivelar con seguridad."
        ),
        evidence=evidence,
    )
    headroom = next((d for d in analysis.diagnostics if d.code == "low_headroom"), None)
    hot = bool(headroom and headroom.detected and headroom.confidence >= min_diagnostic_confidence)
    peaky = (
        analysis.crest_factor_db is not None and analysis.crest_factor_db > rules.crest_threshold_db
    )
    compressor = ProcessingStep(
        "compressor",
        available and (varying or hot or peaky),
        {
            "threshold_dbfs": rules.compressor_threshold_dbfs,
            "ratio": rules.compressor_ratio,
            "knee_db": rules.compressor_knee_db,
            "attack_ms": rules.compressor_attack_ms,
            "release_ms": rules.compressor_release_ms,
            "makeup_gain_db": 0.0,
        },
        (
            "Se suavizan los picos y las diferencias de nivel con compresión moderada, "
            "sin añadir ganancia de compensación automática."
            if available and (varying or hot or peaky)
            else "Los niveles no justifican comprimir automáticamente. "
            "Puedes ajustar y probar el compresor."
        ),
        source_diagnostic="low_headroom" if hot and not varying and not peaky else None,
        confidence=headroom.confidence if headroom and hot and not varying and not peaky else None,
        evidence={"speech_available": available, "speech_level_spread_db": spread},
    )
    steps = list(plan.steps)
    # Raising an entire recording would also raise its pauses. Voice gain is now
    # delegated to the leveler; without reliable speech no automatic boost is safe. A negative
    # pre-gain for headroom remains independent and available in manual plans.
    for index, step in enumerate(steps):
        gain = step.parameters.get("gain_db")
        if step.processor == "pre_gain" and isinstance(gain, int | float) and gain > 0:
            steps[index] = replace(
                step,
                enabled=False,
                reason=(
                    "El aumento global se deja desactivado para conservar el fondo de las pausas. "
                    "El nivelador ajusta únicamente los intervalos de voz."
                    if available
                    else "Sin voz distinguible del fondo se evita aumentar automáticamente "
                    "el nivel de toda la grabación."
                ),
            )
    return replace(plan, steps=[*steps, leveler, compressor])


def _candidate_plan(
    analysis: AudioAnalysis,
    algorithm: str = "wiener",
    strength: str = "balanced",
    corrective: CorrectiveRules = RULES,
    dynamics: DynamicsRules = DYNAMICS_RULES,
    gating: GateRules = GATE_RULES,
) -> ProcessingPlan:
    """Add an explainable, confidence-gated reducer with a reusable density PSD."""
    if algorithm not in ALGORITHMS or strength not in STRENGTHS:
        raise ValueError("Algoritmo o intensidad no admitidos")
    plan = recommend_corrective_plan(analysis, corrective)
    profile = analysis.noise_profile
    diagnosis = next(
        (item for item in analysis.diagnostics if item.code == "stationary_noise"), None
    )
    available = (
        profile is not None
        and profile.duration_seconds >= gating.minimum_background_seconds
        and profile.frame_count >= gating.minimum_background_frames
        and bool(profile.frequencies_hz)
        and profile.rms_dbfs is not None
    )
    enabled = (
        available
        and diagnosis is not None
        and diagnosis.detected
        and diagnosis.confidence >= gating.recommended_confidence
    )
    frequencies = (
        list(profile.frequencies_hz) if available and profile else [0.0, analysis.sample_rate / 2]
    )
    density = (
        [max(-300.0, value) if value is not None else -300.0 for value in profile.psd_dbfs_per_hz]
        if available and profile
        else [-300.0, -300.0]
    )
    # Odd-length FFTs have no Nyquist bin. A one-sided endpoint has half
    # the interior-bin density; extend without mutating the analysis profile.
    if frequencies[-1] < analysis.sample_rate / 2:
        frequencies.append(analysis.sample_rate / 2)
        density.append(max(-300.0, density[-1] - 3.01029995664))
    step = ProcessingStep(
        processor="noise_reduction",
        enabled=enabled,
        parameters={
            "algorithm": algorithm,
            "strength": strength,
            "noise_frequencies_hz": frequencies,
            "noise_psd_dbfs_per_hz": density,
        },
        reason=(
            "Se detectó un fondo estacionario; se reduce usando su perfil espectral. "
            "Compara la voz antes y después."
            if enabled
            else "Hay perfil de fondo, pero el diagnóstico no justifica activar la reducción "
            "automáticamente. Puedes probarla y comparar."
            if available
            else "No hay suficientes intervalos de fondo para estimar "
            "una reducción de ruido fiable."
        ),
        source_diagnostic="stationary_noise",
        confidence=diagnosis.confidence if diagnosis else None,
        evidence={
            "profile_available": available,
            "background_seconds": profile.duration_seconds if profile else 0.0,
            "noise_profile_reference": "original; propagado por los filtros anteriores",
        },
    )
    return add_dynamics(
        replace(plan, preset=strength, steps=[*plan.steps[:-1], step, plan.steps[-1]]),
        analysis,
        dynamics,
        gating.recommended_confidence,
    )


def _voice_confidence(analysis: AudioAnalysis, rules: DynamicsRules) -> float:
    """Evidence score for voice decisions, never a calibrated VAD probability."""
    activity, profile = analysis.speech_activity, analysis.noise_profile
    if not activity or activity.speech_rms_dbfs is None:
        return 0.0
    noise = profile.rms_dbfs if profile and profile.rms_dbfs is not None else -60.0
    contrast = activity.speech_rms_dbfs - noise
    if activity.speech_seconds < rules.minimum_speech_seconds or activity.speech_rms_dbfs <= max(
        -50.0, noise + rules.minimum_contrast_db
    ):
        return 0.0
    return round(
        min(
            0.95,
            0.55
            + 0.20 * min(1.0, (contrast - rules.minimum_contrast_db) / 15)
            + 0.10 * min(1.0, activity.speech_seconds / 3)
            + 0.10 * min(1.0, profile.duration_seconds / 2 if profile else 0),
        ),
        6,
    )


def _gate(
    step: ProcessingStep, analysis: AudioAnalysis, preset: ProcessingPreset
) -> ProcessingStep:
    confidence = step.confidence
    if step.processor in ("speech_leveler", "compressor"):
        voice_confidence = _voice_confidence(analysis, preset.dynamics)
        confidence = (
            min(voice_confidence, confidence) if confidence is not None else voice_confidence
        )
    diagnostic = _diagnostic(analysis, step.source_diagnostic) if step.source_diagnostic else None
    # Negative pre-gain is justified by the measured peak margin itself; unlike
    # hum/noise identification it does not depend on a classifier's confidence.
    gain = step.parameters.get("gain_db", 0)
    direct = step.processor == "pre_gain" and isinstance(gain, int | float) and gain < 0
    if direct:
        confidence = None
    severity_ok = (
        direct or diagnostic is None or diagnostic.severity >= preset.gating.minimum_severity
    )
    automatic = (
        step.enabled
        and severity_ok
        and (confidence is None or confidence >= preset.gating.automatic_confidence)
    )
    recommended = (
        step.enabled
        and severity_ok
        and not automatic
        and (confidence is not None and confidence >= preset.gating.recommended_confidence)
    )
    decision: Literal["automatic", "recommended", "disabled"] = (
        "automatic" if automatic else "recommended" if recommended else "disabled"
    )
    reason = step.reason
    if recommended:
        reason += " La evidencia es moderada: revisa este ajuste y actívalo si lo necesitas."
    elif step.enabled and not automatic:
        reason += " La evidencia o la severidad no alcanza el umbral de este preset."
    return replace(
        step,
        enabled=automatic,
        decision=decision,
        confidence=confidence,
        source_diagnostic=None if direct else step.source_diagnostic,
        reason=reason,
        evidence={
            **step.evidence,
            "automatic_confidence_threshold": preset.gating.automatic_confidence,
            "recommended_confidence_threshold": preset.gating.recommended_confidence,
            "minimum_severity": preset.gating.minimum_severity,
            "direct_measurement_rule": direct or step.processor == "dc_removal",
        },
    )


def mastering_decisions(analysis: AudioAnalysis, preset: MasteringPreset) -> list[ProcessingStep]:
    available = analysis.integrated_lufs is not None and analysis.duration_seconds >= 0.4
    parameters: dict[str, ParameterValue] = {
        "target_lufs": preset.target_lufs,
        "max_true_peak_dbtp": preset.max_true_peak_dbtp,
        "target_lra_lu": preset.target_lra_lu,
        "loudness_tolerance_lu": preset.loudness_tolerance_lu,
    }
    return [
        ProcessingStep(
            "loudness_normalization",
            available,
            parameters,
            f"Se remide el audio corregido y se normaliza hacia {preset.target_lufs:g} LUFS "
            f"con tolerancia ±{preset.loudness_tolerance_lu:g} LU."
            if available
            else "No hay loudness integrado medible o el clip es demasiado breve para masterizar.",
            evidence={"input_loudness_available": available},
            decision="automatic" if available else "disabled",
        ),
        ProcessingStep(
            "true_peak_limiter",
            available,
            {"max_true_peak_dbtp": preset.max_true_peak_dbtp},
            f"Se protege el techo de {preset.max_true_peak_dbtp:g} dBTP y se comprueba "
            "el WAV PCM24 con los ocho controles de calidad antes de publicarlo."
            if available
            else "La protección final se aplica junto con una masterización medible.",
            evidence={"output_qc_required": True},
            decision="automatic" if available else "disabled",
        ),
    ]


def decide(
    analysis: AudioAnalysis,
    diagnostics: list[Diagnostic],
    preset: ProcessingPreset,
    mastering_preset: MasteringPreset,
    *,
    algorithm: str | None = None,
    strength: str | None = None,
) -> ProcessingPlan:
    """Same analysis, diagnostics and configuration always yield the same full plan."""
    if len({item.code for item in diagnostics}) != len(diagnostics):
        raise ValueError("Duplicate diagnostics")
    context = replace(analysis, diagnostics=diagnostics)
    candidates = _candidate_plan(
        context,
        algorithm or preset.noise_algorithm,
        strength or preset.noise_strength,
        preset.corrective,
        preset.dynamics,
        preset.gating,
    )
    return ProcessingPlan(
        preset=preset.id,
        version=PLAN_VERSION,
        steps=[_gate(step, context, preset) for step in candidates.steps],
        preset_version=preset.version,
        mastering_preset=mastering_preset.id,
        mastering_steps=mastering_decisions(context, mastering_preset),
    )
