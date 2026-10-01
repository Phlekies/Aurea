"""Conservative voice-level decisions after spectral correction, before mastering."""

from dataclasses import dataclass, replace

import numpy as np

from app.domain.analysis import AudioAnalysis
from app.domain.processing import ParameterValue, ProcessingPlan, ProcessingStep


@dataclass(frozen=True)
class DynamicsRules:
    """Engineering defaults, validated with synthetic speech-like signals."""

    target_rms_dbfs: float = -24.0
    level_change_threshold_db: float = 4.0
    variation_threshold_db: float = 6.0
    minimum_speech_seconds: float = 0.3
    minimum_contrast_db: float = 9.0
    max_boost_db: float = 8.0
    max_cut_db: float = 12.0


DYNAMICS_RULES = DynamicsRules()


def add_dynamics(
    plan: ProcessingPlan, analysis: AudioAnalysis, rules: DynamicsRules = DYNAMICS_RULES
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
            "window_ms": 300.0,
            "smoothing_ms": 600.0,
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
    hot = bool(headroom and headroom.detected and headroom.confidence >= 0.55)
    peaky = analysis.crest_factor_db is not None and analysis.crest_factor_db > 18
    compressor = ProcessingStep(
        "compressor",
        available and (varying or hot or peaky),
        {
            "threshold_dbfs": -18.0,
            "ratio": 2.0,
            "knee_db": 6.0,
            "attack_ms": 10.0,
            "release_ms": 150.0,
            "makeup_gain_db": 0.0,
        },
        (
            "Se suavizan los picos y las diferencias de nivel con compresión moderada, "
            "sin añadir ganancia de compensación automática."
            if available and (varying or hot or peaky)
            else "Los niveles no justifican comprimir automáticamente. "
            "Puedes ajustar y probar el compresor."
        ),
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
