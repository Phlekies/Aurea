"""Processing plans and reports, independent of HTTP, storage and DSP implementations."""

import math
from dataclasses import dataclass, field

type ParameterValue = bool | int | float | str | list[float]


def _finite(value: object) -> bool:
    values = value if isinstance(value, list) else [value]
    return all(not isinstance(item, float) or math.isfinite(item) for item in values)


@dataclass(frozen=True)
class ProcessingStep:
    """One processor decision; ``reason`` explains it and ``evidence`` supports it."""

    processor: str
    enabled: bool
    parameters: dict[str, ParameterValue]
    reason: str
    source_diagnostic: str | None = None
    confidence: float | None = None
    evidence: dict[str, ParameterValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not self.processor.strip()
            or not self.reason.strip()
            or not all(_finite(value) for value in self.parameters.values())
            or not all(_finite(value) for value in self.evidence.values())
            or (self.confidence is not None and not 0 <= self.confidence <= 1)
        ):
            raise ValueError("Invalid processing step")


@dataclass(frozen=True)
class ProcessingPlan:
    """Ordered chain; disabled steps stay visible so the decision remains explainable."""

    preset: str
    version: str
    steps: list[ProcessingStep]

    def __post_init__(self) -> None:
        if not self.preset.strip() or not self.version.strip() or len(self.steps) > 32:
            raise ValueError("Invalid processing plan")


@dataclass(frozen=True)
class ProcessingMetrics:
    """Levels and detections of one rendering; dB values are ``None`` for silence."""

    peak_dbfs: float | None
    rms_dbfs: float | None
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    dc_offset: list[float]
    subbass_percent: float
    noise_rms_dbfs: float | None
    estimated_snr_db: float | None
    detected: list[str]


@dataclass(frozen=True)
class AppliedStep:
    """A plan step as executed, with validated parameters and wall-clock seconds."""

    processor: str
    enabled: bool
    parameters: dict[str, ParameterValue]
    seconds: float


@dataclass(frozen=True)
class ProcessingReport:
    """Manifest of one processed rendering: plan, timings, warnings and before/after."""

    audio_id: str
    pipeline_version: str
    plan: ProcessingPlan
    steps: list[AppliedStep]
    sample_rate: int
    channels: int
    duration_seconds: float
    safety_gain_db: float
    warnings: list[str]
    processing_seconds: float
    real_time_factor: float
    before: ProcessingMetrics
    after: ProcessingMetrics
