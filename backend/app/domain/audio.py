"""Sample and asset models; sample positions always precede the channel dimension."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from app.domain.errors import InvalidAudioFile


@dataclass(frozen=True)
class AudioAsset:
    """Recording metadata independent of HTTP, with no user-controlled storage path."""

    id: str
    filename: str
    format: str
    codec: str
    bitrate: int | None
    size_bytes: int
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class Waveform:
    """Equal-time absolute amplitude buckets, one finite series per native channel."""

    duration_seconds: float
    sample_rate: int
    channels: int
    peaks: list[list[float]]


@dataclass(frozen=True)
class AudioConfig:
    """Public limits in bytes/seconds/Hz, alongside the accepted filename extensions."""

    formats: list[str]
    max_upload_bytes: int
    max_duration_seconds: int
    retention_seconds: int
    min_sample_rate: int
    max_sample_rate: int
    max_channels: int = 2


@dataclass(frozen=True)
class AudioBuffer:
    """Finite float32 samples in [-1, 1], shaped (frames, channels), at native Hz."""

    samples: NDArray[np.float32]
    sample_rate: int

    def __post_init__(self) -> None:
        if (
            self.sample_rate <= 0
            or self.samples.dtype != np.float32
            or self.samples.ndim != 2
            or self.samples.shape[1] not in (1, 2)
            or self.samples.shape[0] == 0
            or not np.isfinite(self.samples).all()
            or np.max(np.abs(self.samples)) > 1
        ):
            raise InvalidAudioFile("El audio no tiene una representación válida.")
