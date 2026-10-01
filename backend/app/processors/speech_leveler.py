"""Causal speech leveling with linked stereo gain and bounded envelope history.

The original VAD defines where leveling may act. A fast energy detector also
requires voice to stand above the measured noise floor, while a slower RMS
detector and gain smoother follow changes in phrase level rather than waveform
peaks. The envelope records the actual stage gain, before the runner's global
overload protection; this is an RMS heuristic, not a perceptual loudness meter.
"""

import math

import numpy as np
from numpy.typing import NDArray
from scipy.signal import lfilter

from app.domain.processing import ParameterValue
from app.processors.base import (
    BaseProcessor,
    Block,
    BlockProcessor,
    Parameters,
    number,
    reject_unknown,
    scalar,
)

_INTERVAL_KEYS = ("speech_starts_seconds", "speech_ends_seconds")
_DEFAULTS = {
    "noise_floor_dbfs": (-300.0, 0.0, -60.0),
    "target_rms_dbfs": (-40.0, -12.0, -24.0),
    "max_boost_db": (0.0, 12.0, 8.0),
    "max_cut_db": (0.0, 24.0, 12.0),
    "window_ms": (100.0, 1000.0, 300.0),
    "smoothing_ms": (100.0, 2000.0, 600.0),
}


def _times(params: Parameters, key: str) -> list[float]:
    """Read finite timestamps; bounded length also caps plan storage and search cost."""
    value = params.get(key, [])
    if not isinstance(value, list) or len(value) > 18000:
        raise ValueError(f"{key} debe ser una lista de hasta 18000 tiempos")
    return [number({key: item}, key, 0.0, 1800.0) for item in value]


class SpeechLevelerStream:
    """Stateful filters, absolute VAD positions and 10 Hz applied-gain samples."""

    def __init__(self, values: dict[str, ParameterValue], sample_rate: int) -> None:
        starts = values["speech_starts_seconds"]
        ends = values["speech_ends_seconds"]
        assert isinstance(starts, list) and isinstance(ends, list)
        self.starts = np.rint(np.asarray(starts) * sample_rate).astype(np.int64)
        self.ends = np.rint(np.asarray(ends) * sample_rate).astype(np.int64)
        self.rate = sample_rate
        self.fade_frames = max(1, round(0.05 * sample_rate))
        self.position = 0
        self.target_db = scalar(values, "target_rms_dbfs")
        self.max_boost_db = scalar(values, "max_boost_db")
        self.max_cut_db = scalar(values, "max_cut_db")
        self.gate_db = max(-50.0, scalar(values, "noise_floor_dbfs") + 9.0)
        self.level_alpha = math.exp(-1 / (sample_rate * scalar(values, "window_ms") / 1000))
        self.gain_alpha = math.exp(-1 / (sample_rate * scalar(values, "smoothing_ms") / 1000))
        self.fast_alpha = math.exp(-1 / (sample_rate * 0.01))
        self.level_state = np.zeros(1)
        self.fast_state = np.zeros(1)
        self.gain_state = np.zeros(1)
        self.report_points = 0
        self.last_report_frame = -1
        self.envelope_times: list[float] = []
        self.envelope_gains: list[float] = []
        self.last_gain = 0.0

    def _activity(self, positions: NDArray[np.int64]) -> NDArray[np.float64]:
        """Raised-cosine VAD edges reach unity without modifying any pause sample."""
        if not len(self.ends):
            return np.zeros(len(positions))
        indices = np.searchsorted(self.ends, positions, side="right")
        safe = np.minimum(indices, len(self.ends) - 1)
        starts, ends = self.starts[safe], self.ends[safe]
        inside = (indices < len(self.ends)) & (positions >= starts) & (positions < ends)
        distance = np.minimum(positions - starts, ends - 1 - positions)
        phase = np.clip(distance / self.fade_frames, 0.0, 1.0)
        return np.asarray(inside * (0.5 - 0.5 * np.cos(np.pi * phase)), dtype=np.float64)

    def process(self, block: Block) -> Block:
        """Level one contiguous block with fixed shape and no block-size dependence."""
        if not len(block):
            return block.copy()
        power = np.mean(block * block, axis=1)
        level, self.level_state = lfilter(
            [1 - self.level_alpha], [1, -self.level_alpha], power, zi=self.level_state
        )
        fast, self.fast_state = lfilter(
            [1 - self.fast_alpha], [1, -self.fast_alpha], power, zi=self.fast_state
        )
        level_db = 10 * np.log10(np.maximum(level, 1e-30))
        fast_db = 10 * np.log10(np.maximum(fast, 1e-30))
        positions = np.arange(self.position, self.position + len(block), dtype=np.int64)
        activity = self._activity(positions)
        # A 6 dB soft energy transition avoids a hard gain step near the gate.
        energy = np.clip((fast_db - self.gate_db) / 6.0, 0.0, 1.0)
        weight = activity * energy
        # Fast rising energy prevents the slower detector requesting a large boost
        # at a loud phrase onset, while attenuation still follows the mid-term RMS.
        boost_level = np.maximum(level_db, fast_db)
        requested = self.target_db - np.where(level_db < self.target_db, boost_level, level_db)
        desired = np.clip(requested, -self.max_cut_db, self.max_boost_db) * weight
        smooth, self.gain_state = lfilter(
            [1 - self.gain_alpha], [1, -self.gain_alpha], desired, zi=self.gain_state
        )
        applied = np.asarray(smooth * weight, dtype=np.float64)
        # ceil(k * native_rate / 10) keeps exactly 10 points/s even when the
        # native rate is not divisible by 10; a rounded hop drifts on long files.
        final_point = (self.position + len(block) - 1) * 10 // self.rate
        point_indices = np.arange(self.report_points, final_point + 1, dtype=np.int64)
        report_frames = (point_indices * self.rate + 9) // 10
        selected = report_frames - self.position
        self.envelope_times.extend((report_frames / self.rate).tolist())
        self.envelope_gains.extend(applied[selected].tolist())
        self.report_points += len(report_frames)
        if len(report_frames):
            self.last_report_frame = int(report_frames[-1])
        self.last_gain = float(applied[-1])
        self.position += len(block)
        return np.asarray(block * np.power(10.0, applied[:, None] / 20), dtype=np.float64)

    def gain_envelope(self) -> tuple[list[float], list[float]]:
        """Return sampled applied dB gains plus the exact final sample, without mutation."""
        times, gains = self.envelope_times.copy(), self.envelope_gains.copy()
        if self.position and self.last_report_frame != self.position - 1:
            times.append((self.position - 1) / self.rate)
            gains.append(self.last_gain)
        return times, gains


class SpeechLevelerProcessor(BaseProcessor):
    """Adjust slow speech level changes, leaving non-speech samples at unity gain."""

    name = "speech_leveler"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Normalize bounded RMS/gain settings and sorted, non-overlapping VAD intervals."""
        reject_unknown(params, {*_INTERVAL_KEYS, *_DEFAULTS})
        if not 8000 <= sample_rate <= 96000 or channels not in (1, 2):
            raise ValueError("El nivelador admite audio mono o estéreo entre 8 y 96 kHz")
        starts, ends = (_times(params, key) for key in _INTERVAL_KEYS)
        if len(starts) != len(ends):
            raise ValueError("Los inicios y finales de voz deben tener la misma longitud")
        if any(start >= end for start, end in zip(starts, ends, strict=True)) or any(
            end > start for end, start in zip(ends[:-1], starts[1:], strict=True)
        ):
            raise ValueError("Los tramos de voz deben estar ordenados, separados y tener duración")
        values: dict[str, ParameterValue] = {
            "speech_starts_seconds": starts,
            "speech_ends_seconds": ends,
        }
        for key, (low, high, default) in _DEFAULTS.items():
            values[key] = number(params, key, low, high, default)
        return values

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Create fresh detector, smoother and history state for one native-rate render."""
        return SpeechLevelerStream(self.validate(params, sample_rate, channels), sample_rate)
