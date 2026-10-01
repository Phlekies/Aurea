"""Processing plans and reports, independent of HTTP, storage and DSP implementations."""

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

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
    decision: Literal["automatic", "recommended", "disabled", "manual"] = "manual"

    def __post_init__(self) -> None:
        if (
            not self.processor.strip()
            or not self.reason.strip()
            or not all(_finite(value) for value in self.parameters.values())
            or not all(_finite(value) for value in self.evidence.values())
            or (self.confidence is not None and not 0 <= self.confidence <= 1)
            or self.decision not in ("automatic", "recommended", "disabled", "manual")
        ):
            raise ValueError("Invalid processing step")


@dataclass(frozen=True)
class ProcessingPlan:
    """Ordered chain; disabled steps stay visible so the decision remains explainable."""

    preset: str
    version: str
    steps: list[ProcessingStep]
    preset_version: str = "legacy"
    mastering_preset: str = "podcast_standard"
    mastering_steps: list[ProcessingStep] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            not self.preset.strip()
            or not self.version.strip()
            or not self.preset_version.strip()
            or not self.mastering_preset.strip()
            or len(self.steps) > 32
            or len(self.mastering_steps) > 2
        ):
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
class ArtifactMetrics:
    """Approximate guards for the whole chain, on the original activity regions."""

    total_reduction_db: float | None
    speech_energy_loss_db: float | None
    background_reduction_db: float | None
    musical_noise_score: float | None
    excessive_reduction: bool
    significant_speech_loss: bool
    possible_musical_noise: bool

    def __post_init__(self) -> None:
        values = (
            self.total_reduction_db,
            self.speech_energy_loss_db,
            self.background_reduction_db,
            self.musical_noise_score,
        )
        if (
            any(value is not None and not math.isfinite(value) for value in values)
            or self.musical_noise_score is not None
            and not 0 <= self.musical_noise_score <= 1
        ):
            raise ValueError("Invalid artifact metrics")


@dataclass(frozen=True)
class GainEnvelope:
    """Applied stage gain sampled every 0.1 s plus the last sample, before safety gain."""

    processor: str
    step_index: int
    times_seconds: list[float]
    gain_db: list[float]

    def __post_init__(self) -> None:
        if (
            not self.processor.strip()
            or self.step_index < 0
            or not 1 <= len(self.times_seconds) <= 18002
            or len(self.times_seconds) != len(self.gain_db)
            or any(not math.isfinite(value) for value in (*self.times_seconds, *self.gain_db))
            or self.times_seconds[0] != 0
            or any(left >= right for left, right in pairwise(self.times_seconds))
        ):
            raise ValueError("Invalid gain envelope")


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
    artifacts: ArtifactMetrics | None = None
    gain_envelopes: list[GainEnvelope] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            self.sample_rate < 8000
            or self.channels not in (1, 2)
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds <= 0
            or not math.isfinite(self.safety_gain_db)
            or self.safety_gain_db > 0
            or not math.isfinite(self.processing_seconds)
            or self.processing_seconds < 0
            or not math.isfinite(self.real_time_factor)
            or self.real_time_factor < 0
            or len(self.steps) != len(self.plan.steps)
            or any(not math.isfinite(step.seconds) or step.seconds < 0 for step in self.steps)
            or any(len(metrics.dc_offset) != self.channels for metrics in (self.before, self.after))
            or len({curve.step_index for curve in self.gain_envelopes}) != len(self.gain_envelopes)
            or any(
                curve.times_seconds[-1] >= self.duration_seconds
                or curve.step_index >= len(self.steps)
                or not self.steps[curve.step_index].enabled
                or self.steps[curve.step_index].processor != curve.processor
                for curve in self.gain_envelopes
            )
        ):
            raise ValueError("Invalid processing report")
