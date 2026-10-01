"""Podcast mastering targets, measurements and mandatory output quality checks."""

import math
import re
from dataclasses import dataclass
from itertools import pairwise


@dataclass(frozen=True)
class MasteringPreset:
    id: str
    name: str
    target_lufs: float
    max_true_peak_dbtp: float
    target_lra_lu: float = 11.0
    loudness_tolerance_lu: float = 0.5

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.id)
            or not self.name.strip()
            or not -40 <= self.target_lufs <= -5
            or not -8 <= self.max_true_peak_dbtp <= -0.1
            or not 1 <= self.target_lra_lu <= 50
            or not 0 < self.loudness_tolerance_lu <= 1
        ):
            raise ValueError("Invalid mastering preset")


@dataclass(frozen=True)
class LoudnessPoint:
    """End of a 100 ms metering interval, with 400 ms M and 3 s S windows."""

    time_seconds: float
    momentary_lufs: float | None
    short_term_lufs: float | None

    def __post_init__(self) -> None:
        if self.time_seconds < 0 or any(
            value is not None and not math.isfinite(value)
            for value in (self.time_seconds, self.momentary_lufs, self.short_term_lufs)
        ):
            raise ValueError("Invalid loudness point")


@dataclass(frozen=True)
class LoudnessMeasurements:
    integrated_lufs: float | None
    momentary_max_lufs: float | None
    short_term_max_lufs: float | None
    loudness_range_lu: float | None
    lra_stable: bool
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None
    rms_dbfs: float | None
    points: list[LoudnessPoint]

    def __post_init__(self) -> None:
        values = (
            self.integrated_lufs,
            self.momentary_max_lufs,
            self.short_term_max_lufs,
            self.loudness_range_lu,
            self.true_peak_dbtp,
            self.sample_peak_dbfs,
            self.rms_dbfs,
        )
        if (
            any(value is not None and not math.isfinite(value) for value in values)
            or self.loudness_range_lu is not None
            and self.loudness_range_lu < 0
            or len(self.points) > 18002
            or any(a.time_seconds >= b.time_seconds for a, b in pairwise(self.points))
        ):
            raise ValueError("Invalid loudness measurements")


@dataclass(frozen=True)
class QualityCheck:
    code: str
    passed: bool
    observed: float | int | bool | str | None
    expected: str

    def __post_init__(self) -> None:
        if (
            not self.code
            or not self.expected
            or (isinstance(self.observed, float) and not math.isfinite(self.observed))
        ):
            raise ValueError("Invalid quality check")


QC_CODES = frozenset(
    {
        "loudness",
        "true_peak",
        "clipping",
        "duration",
        "channels",
        "sample_rate",
        "finite",
        "not_silent",
    }
)


@dataclass(frozen=True)
class OutputQC:
    passed: bool
    checks: list[QualityCheck]

    def __post_init__(self) -> None:
        if (
            len(self.checks) != len(QC_CODES)
            or {check.code for check in self.checks} != QC_CODES
            or self.passed != all(check.passed for check in self.checks)
        ):
            raise ValueError("Invalid output QC")


@dataclass(frozen=True)
class MasteringReport:
    audio_id: str
    mastering_version: str
    source_revision: str
    preset: MasteringPreset
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    bit_depth: int
    before: LoudnessMeasurements
    after: LoudnessMeasurements
    qc: OutputQC
    normalization_mode: str
    requested_gain_db: float
    limiter_ceiling_dbtp: float
    attempts: int
    processing_seconds: float
    output_sha256: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-f0-9]{32}", self.audio_id)
            or not re.fullmatch(r"[a-f0-9]{64}", self.source_revision)
            or not re.fullmatch(r"[a-f0-9]{64}", self.output_sha256)
            or not 8000 <= self.sample_rate <= 96000
            or self.channels not in (1, 2)
            or self.frames < 1
            or self.bit_depth != 24
            or not math.isfinite(self.duration_seconds)
            or abs(self.duration_seconds - self.frames / self.sample_rate) > 1e-9
            or not self.qc.passed
            or self.normalization_mode not in ("linear", "dynamic")
            or not 1 <= self.attempts <= 3
            or not math.isfinite(self.processing_seconds)
            or self.processing_seconds < 0
            or not math.isfinite(self.requested_gain_db)
            or not math.isfinite(self.limiter_ceiling_dbtp)
            or self.limiter_ceiling_dbtp > self.preset.max_true_peak_dbtp
            or self.after.integrated_lufs is None
            or abs(self.after.integrated_lufs - self.preset.target_lufs)
            > self.preset.loudness_tolerance_lu
            or self.after.true_peak_dbtp is None
            or self.after.true_peak_dbtp > self.preset.max_true_peak_dbtp + 0.02
            or any(
                point.time_seconds > self.duration_seconds
                for point in (*self.before.points, *self.after.points)
            )
        ):
            raise ValueError("Invalid mastering report")
