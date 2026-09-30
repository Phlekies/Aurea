"""Centralised noise-reduction selection, after corrections and before pre-gain."""

from dataclasses import replace

from app.domain.analysis import AudioAnalysis
from app.domain.processing import ProcessingPlan, ProcessingStep
from app.pipeline.decision_engine import recommend_corrective_plan
from app.processors.noise_reduction import ALGORITHMS, STRENGTHS


def recommend_processing_plan(
    analysis: AudioAnalysis, algorithm: str = "wiener", strength: str = "balanced"
) -> ProcessingPlan:
    """Add an explainable, confidence-gated reducer with a reusable density PSD."""
    if algorithm not in ALGORITHMS or strength not in STRENGTHS:
        raise ValueError("Algoritmo o intensidad no admitidos")
    plan = recommend_corrective_plan(analysis)
    profile = analysis.noise_profile
    diagnosis = next(
        (item for item in analysis.diagnostics if item.code == "stationary_noise"), None
    )
    available = (
        profile is not None
        and profile.duration_seconds >= 0.3
        and profile.frame_count >= 10
        and bool(profile.frequencies_hz)
        and profile.rms_dbfs is not None
    )
    enabled = (
        available and diagnosis is not None and diagnosis.detected and diagnosis.confidence >= 0.55
    )
    frequencies = (
        list(profile.frequencies_hz) if available and profile else [0.0, analysis.sample_rate / 2]
    )
    density = (
        [value if value is not None else -300.0 for value in profile.psd_dbfs_per_hz]
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
    return replace(plan, preset=strength, steps=[*plan.steps[:-1], step, plan.steps[-1]])
