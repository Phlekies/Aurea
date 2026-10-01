"""Validated decision policy; independent of processors and storage."""

import math
from dataclasses import asdict, dataclass


def _finite_record(record: object) -> None:
    for value in asdict(record).values():  # type: ignore[call-overload]
        values = value if isinstance(value, tuple) else (value,)
        if any(not isinstance(v, int | float) or not math.isfinite(v) for v in values):
            raise ValueError("Non-finite decision threshold")


@dataclass(frozen=True)
class CorrectiveRules:
    min_confidence: float = 0.55
    dc_threshold: float = 0.001
    highpass_presets_hz: tuple[float, ...] = (60.0, 70.0, 80.0, 100.0)
    highpass_order: int = 4
    rumble_band_hz: tuple[float, float] = (20.0, 80.0)
    rumble_reduction_db: float = 10.0
    dehum_q: float = 30.0
    dehum_margin_db: float = 6.0
    dehum_attenuation_db: tuple[float, float] = (12.0, 48.0)
    pregain_target_lufs: float = -24.0
    pregain_ceiling_dbtp: float = -3.0
    pregain_minimum_db: float = 1.0

    def __post_init__(self) -> None:
        _finite_record(self)
        if (
            not 0 <= self.min_confidence <= 1
            or not 0 < self.dc_threshold < 1
            or not self.highpass_presets_hz
            or list(self.highpass_presets_hz) != sorted(set(self.highpass_presets_hz))
            or not all(20 <= f <= 120 for f in self.highpass_presets_hz)
            or self.highpass_order not in (2, 4)
            or not 0 < self.rumble_band_hz[0] < self.rumble_band_hz[1] <= 120
            or not 0 < self.rumble_reduction_db <= 24
            or not 1 <= self.dehum_q <= 100
            or not 0 <= self.dehum_margin_db <= 12
            or not 0 < self.dehum_attenuation_db[0] <= self.dehum_attenuation_db[1] <= 48
            or not -40 <= self.pregain_target_lufs <= -12
            or not -12 <= self.pregain_ceiling_dbtp <= -1
            or not 0 < self.pregain_minimum_db <= 6
        ):
            raise ValueError("Invalid corrective rules")


@dataclass(frozen=True)
class DynamicsRules:
    target_rms_dbfs: float = -24.0
    level_change_threshold_db: float = 4.0
    variation_threshold_db: float = 6.0
    minimum_speech_seconds: float = 0.3
    minimum_contrast_db: float = 9.0
    max_boost_db: float = 8.0
    max_cut_db: float = 12.0
    window_ms: float = 300.0
    smoothing_ms: float = 600.0
    compressor_threshold_dbfs: float = -18.0
    compressor_ratio: float = 2.0
    compressor_knee_db: float = 6.0
    compressor_attack_ms: float = 10.0
    compressor_release_ms: float = 150.0
    crest_threshold_db: float = 18.0

    def __post_init__(self) -> None:
        _finite_record(self)
        if (
            not -40 <= self.target_rms_dbfs <= -12
            or not 0 < self.level_change_threshold_db <= 12
            or not 0 < self.variation_threshold_db <= 24
            or not 0.3 <= self.minimum_speech_seconds <= 10
            or not 9 <= self.minimum_contrast_db <= 30
            or not 0 <= self.max_boost_db <= 12
            or not 0 <= self.max_cut_db <= 24
            or not 50 <= self.window_ms <= 1000
            or not 50 <= self.smoothing_ms <= 2000
            or not -60 <= self.compressor_threshold_dbfs <= 0
            or not 1 <= self.compressor_ratio <= 20
            or not 0 <= self.compressor_knee_db <= 24
            or not 0.1 <= self.compressor_attack_ms <= 200
            or not 10 <= self.compressor_release_ms <= 2000
            or not 6 <= self.crest_threshold_db <= 30
        ):
            raise ValueError("Invalid dynamics rules")


@dataclass(frozen=True)
class GateRules:
    recommended_confidence: float = 0.55
    automatic_confidence: float = 0.85
    minimum_severity: float = 0.05
    minimum_background_seconds: float = 0.3
    minimum_background_frames: int = 10

    def __post_init__(self) -> None:
        _finite_record(self)
        if (
            not 0.55 <= self.recommended_confidence <= self.automatic_confidence <= 1
            or not 0 <= self.minimum_severity <= 1
            or not 0.3 <= self.minimum_background_seconds <= 10
            or not 10 <= self.minimum_background_frames <= 1000
        ):
            raise ValueError("Invalid confidence policy")


@dataclass(frozen=True)
class ProcessingPreset:
    id: str
    name: str
    description: str
    version: str
    noise_algorithm: str
    noise_strength: str
    corrective: CorrectiveRules
    dynamics: DynamicsRules
    gating: GateRules

    def __post_init__(self) -> None:
        if (
            self.id not in ("natural", "balanced", "studio")
            or not self.name.strip()
            or not self.description.strip()
            or not self.version.strip()
            or self.noise_algorithm not in ("wiener", "spectral_subtraction", "spectral_gate")
            or self.noise_strength not in ("light", "balanced", "strong")
            or self.corrective.min_confidence != self.gating.recommended_confidence
        ):
            raise ValueError("Invalid processing preset")
