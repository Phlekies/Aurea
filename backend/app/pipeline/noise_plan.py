"""Compatibility facade for the centralized, versioned decision engine."""

from app.domain.analysis import AudioAnalysis
from app.domain.processing import ProcessingPlan
from app.mastering.presets import load_presets
from app.pipeline.decision_engine import decide
from app.pipeline.presets import load_processing_presets


def recommend_processing_plan(
    analysis: AudioAnalysis, algorithm: str = "wiener", strength: str = "balanced"
) -> ProcessingPlan:
    return decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()["balanced"],
        load_presets()["podcast_standard"],
        algorithm=algorithm,
        strength=strength,
    )
