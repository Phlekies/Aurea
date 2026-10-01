"""Stereo-linked, soft-knee feed-forward compressor with bounded streaming state.

The detector uses sample peaks across both channels. A release peak-hold followed
by an attack one-pole smooths gain reduction in dB; this is a causal compressor,
without lookahead or a peak limiter. Fixed absolute timestamps capture the gain
actually applied, including makeup gain, independently of input block boundaries.
"""

import math

import numpy as np
from numpy.typing import NDArray
from scipy.signal import lfilter

from app.domain.processing import ParameterValue
from app.processors.base import BaseProcessor, Block, Parameters, number, reject_unknown, scalar


def gain_reduction_db(
    level_db: NDArray[np.float64], threshold_dbfs: float, ratio: float, knee_db: float
) -> NDArray[np.float64]:
    """Static soft-knee transfer: output level minus input level, always <= 0 dB."""
    excess = level_db - threshold_dbfs
    slope = 1.0 / ratio - 1.0
    if knee_db == 0:
        return np.asarray(slope * np.maximum(excess, 0.0), dtype=np.float64)
    knee = slope * np.square(np.maximum(excess + knee_db / 2, 0.0)) / (2 * knee_db)
    return np.asarray(np.where(excess < knee_db / 2, knee, slope * excess), dtype=np.float64)


class CompressorStream:
    """Sample-accurate gain ballistics; storage scales with 10 Hz report points."""

    def __init__(self, values: dict[str, ParameterValue], sample_rate: int) -> None:
        self.threshold_dbfs = scalar(values, "threshold_dbfs")
        self.ratio = scalar(values, "ratio")
        self.knee_db = scalar(values, "knee_db")
        self.makeup_gain_db = scalar(values, "makeup_gain_db")
        self.attack_alpha = math.exp(-1 / (sample_rate * scalar(values, "attack_ms") / 1000))
        self.release_alpha = math.exp(-1 / (sample_rate * scalar(values, "release_ms") / 1000))
        self.sample_rate = sample_rate
        self._report_index = 0
        self._last_report_frame = -1
        # Scaling the release recurrence avoids Python loops over audio samples.
        # Restrict each vector to exp(32), even at the fastest supported release.
        self.chunk_frames = max(1, min(2048, int(32 / -math.log(self.release_alpha))))
        self.release_state = 0.0
        self.attack_state = np.zeros(1, dtype=np.float64)
        self.frames = 0
        self._times: list[float] = []
        self._gains: list[float] = []
        self._last_gain = 0.0

    def process(self, block: Block) -> Block:
        """Apply the same gain to each channel, preserving stereo balance and shape."""
        if not len(block):
            return block
        peaks = np.max(np.abs(block), axis=1)
        levels = 20 * np.log10(np.maximum(peaks, 1e-15))
        requested = gain_reduction_db(levels, self.threshold_dbfs, self.ratio, self.knee_db)
        reduction = np.empty(len(block), dtype=np.float64)
        for start in range(0, len(block), self.chunk_frames):
            end = min(start + self.chunk_frames, len(block))
            weights = self.release_alpha ** np.arange(1, end - start + 1)
            held = weights * np.minimum(
                self.release_state, np.minimum.accumulate(requested[start:end] / weights)
            )
            self.release_state = float(held[-1])
            smooth, state = lfilter(
                [1 - self.attack_alpha], [1, -self.attack_alpha], held, zi=self.attack_state
            )
            self.attack_state = np.asarray(state, dtype=np.float64)
            reduction[start:end] = smooth
        gains = reduction + self.makeup_gain_db
        # ceil(k * rate / 10) maintains exact 10 Hz cadence even when rate/10
        # is fractional. A rounded fixed hop would slowly drift at odd rates.
        last_report_index = ((self.frames + len(block) - 1) * 10) // self.sample_rate
        ordinals = np.arange(self._report_index, last_report_index + 1, dtype=np.int64)
        absolute = (ordinals * self.sample_rate + 9) // 10
        positions = absolute - self.frames
        self._times.extend((absolute / self.sample_rate).tolist())
        self._gains.extend(gains[positions].tolist())
        self._report_index = last_report_index + 1
        if len(absolute):
            self._last_report_frame = int(absolute[-1])
        self.frames += len(block)
        self._last_gain = float(gains[-1])
        return np.asarray(block * np.power(10.0, gains[:, None] / 20), dtype=np.float64)

    def gain_envelope(self) -> tuple[list[float], list[float]]:
        """Return 10 Hz gain samples plus the last rendered sample, in seconds/dB."""
        times, gains = self._times.copy(), self._gains.copy()
        if self.frames and self.frames - 1 != self._last_report_frame:
            times.append((self.frames - 1) / self.sample_rate)
            gains.append(self._last_gain)
        return times, gains


class CompressorProcessor(BaseProcessor):
    """Conservative compressor defaults; parameter changes require a new stream."""

    name = "compressor"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Normalize parameters, refusing nonfinite numbers and unsupported controls."""
        reject_unknown(
            params,
            {"threshold_dbfs", "ratio", "knee_db", "attack_ms", "release_ms", "makeup_gain_db"},
        )
        if not 8000 <= sample_rate <= 96000 or channels not in (1, 2):
            raise ValueError("El compresor admite audio mono o estéreo entre 8 y 96 kHz")
        return {
            "threshold_dbfs": number(params, "threshold_dbfs", -60, 0, -18),
            "ratio": number(params, "ratio", 1, 20, 2),
            "knee_db": number(params, "knee_db", 0, 24, 6),
            "attack_ms": number(params, "attack_ms", 0.1, 200, 10),
            "release_ms": number(params, "release_ms", 10, 2000, 150),
            "makeup_gain_db": number(params, "makeup_gain_db", -12, 12, 0),
        }

    def open(self, params: Parameters, sample_rate: int, channels: int) -> CompressorStream:
        """Create independent gain detector, ballistics and report state."""
        return CompressorStream(self.validate(params, sample_rate, channels), sample_rate)
