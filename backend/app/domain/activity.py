"""Speech/non-speech segmentation and background noise estimates, independent of HTTP."""

import math
from dataclasses import dataclass, field
from itertools import pairwise

ACTIVITY_LABELS = ("speech", "noise", "silence")


def _finite_or_none(*values: float | None) -> bool:
    return all(value is None or math.isfinite(value) for value in values)


@dataclass(frozen=True)
class ActivitySegment:
    """Half-open interval [start, end) in seconds with one activity label."""

    label: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if (
            self.label not in ACTIVITY_LABELS
            or not _finite_or_none(self.start_seconds, self.end_seconds)
            or not 0 <= self.start_seconds < self.end_seconds
        ):
            raise ValueError("Invalid activity segment")


@dataclass(frozen=True)
class SpeechActivity:
    """Contiguous timeline covering the recording; speech is a heuristic decision.

    ``parameters`` records the thresholds actually applied so a decision can be
    reproduced; dB thresholds are ``None`` when the recording has no usable level.
    """

    detector: str
    version: str
    frame_seconds: float
    speech_seconds: float
    noise_seconds: float
    silence_seconds: float
    speech_percent: float
    speech_rms_dbfs: float | None
    segments: list[ActivitySegment]
    parameters: dict[str, float | None]

    def __post_init__(self) -> None:
        durations = (self.speech_seconds, self.noise_seconds, self.silence_seconds)
        if (
            not self.detector.strip()
            or not self.version.strip()
            or not self.segments
            or not _finite_or_none(self.frame_seconds, self.speech_percent, self.speech_rms_dbfs)
            or not _finite_or_none(*durations)
            or not self.frame_seconds > 0
            or any(value < 0 for value in durations)
            or not 0 <= self.speech_percent <= 100
            or not _finite_or_none(*self.parameters.values())
        ):
            raise ValueError("Invalid speech activity")
        for left, right in pairwise(self.segments):
            if left.label == right.label or abs(left.end_seconds - right.start_seconds) > 1e-9:
                raise ValueError("Activity segments must be contiguous and merged")


@dataclass(frozen=True)
class NoiseProfile:
    """Background estimate from selected non-speech frames; empty when unavailable.

    Levels are AC mean-square values in dBFS. ``relative_power_std`` is the frame
    power coefficient of variation and ``spectral_stability`` the mean cosine
    similarity of consecutive grouped PSDs, both describing temporal stability.
    The ``low_*`` spectrum (0--300 Hz, 4 Hz bins) comes from 0.25 s windows of
    uninterrupted background and is empty when no such window exists.
    """

    frame_count: int
    duration_seconds: float
    rms_dbfs: float | None
    floor_dbfs: float | None
    spectral_flatness: float | None
    relative_power_std: float | None
    spectral_stability: float | None
    frequencies_hz: list[float]
    psd_dbfs_per_hz: list[float | None]
    low_window_count: int = 0
    low_frequencies_hz: list[float] = field(default_factory=list)
    low_psd_dbfs_per_hz: list[float | None] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            self.frame_count < 0
            or self.low_window_count < 0
            or len(self.low_frequencies_hz) != len(self.low_psd_dbfs_per_hz)
            or (self.low_window_count == 0) != (not self.low_frequencies_hz)
            or not _finite_or_none(*self.low_frequencies_hz, *self.low_psd_dbfs_per_hz)
            or not _finite_or_none(self.duration_seconds, self.rms_dbfs, self.floor_dbfs)
            or not _finite_or_none(self.spectral_flatness, self.relative_power_std)
            or not _finite_or_none(self.spectral_stability, *self.frequencies_hz)
            or not _finite_or_none(*self.psd_dbfs_per_hz)
            or self.duration_seconds < 0
            or len(self.frequencies_hz) != len(self.psd_dbfs_per_hz)
            or (self.frame_count == 0) != (self.rms_dbfs is None)
        ):
            raise ValueError("Invalid noise profile")
