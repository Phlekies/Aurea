"""Finite, transport-independent measurements of a complete decoded recording."""

from dataclasses import dataclass, field

from app.domain.activity import NoiseProfile, SpeechActivity
from app.domain.diagnostics import Diagnostic


@dataclass(frozen=True)
class BandEnergy:
    """Integrated channel-averaged PSD, in squared full-scale units and percent."""

    name: str
    low_hz: float
    high_hz: float
    power: float
    percent: float


@dataclass(frozen=True)
class Spectrum:
    """One-sided power spectral density; zero power is an undefined dB value."""

    frequencies_hz: list[float]
    psd_dbfs_per_hz: list[float | None]


@dataclass(frozen=True)
class DynamicsPoint:
    """Native-channel sample peak and mean-square RMS over the indicated interval."""

    start_seconds: float
    duration_seconds: float
    peak_dbfs: float | None
    rms_dbfs: float | None


@dataclass(frozen=True)
class Dynamics:
    """Contiguous intervals; only the final interval may be shorter than window_ms."""

    window_ms: float
    points: list[DynamicsPoint]


@dataclass(frozen=True)
class AudioAnalysis:
    """Null dB metrics signify zero energy or no accepted loudness gating blocks."""

    audio_id: str
    analyzer_version: str
    sample_rate: int
    channels: int
    duration_seconds: float
    peak_dbfs: float | None
    rms_dbfs: float | None
    crest_factor_db: float | None
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    dc_offset: list[float]
    zero_crossing_rate: float
    silence_percent: float
    silence_threshold_dbfs: float
    bands: list[BandEnergy]
    spectrum: Spectrum
    dynamics: Dynamics
    diagnostics_version: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    speech_activity: SpeechActivity | None = None
    noise_profile: NoiseProfile | None = None
    estimated_snr_db: float | None = None
