"""High-pass: Butterworth IIR as cascaded second-order sections.

Input: float blocks ``(frames, channels)``. Parameters: ``cutoff_hz`` in [20, 300] Hz
and below 0.45 x sample rate; ``order`` 2, 4 or 6 (12/24/36 dB per octave), default 4.
The -3 dB point is at the cutoff; the passband is maximally flat. The filter is causal
(no look-ahead) and starts from rest, so it adds low-frequency phase shift but no
latency; each channel is filtered independently.

Reference: S. Butterworth, "On the theory of filter amplifiers", Wireless Engineer 7,
1930; SciPy ``butter(..., output="sos")`` and ``sosfilt``.
"""

import numpy as np
from scipy.signal import butter

from app.domain.processing import ParameterValue
from app.processors.base import (
    BaseProcessor,
    BlockProcessor,
    Parameters,
    SosStream,
    integer,
    number,
    reject_unknown,
    scalar,
)

ORDERS = (2, 4, 6)


class HighPassProcessor(BaseProcessor):
    """Remove rumble, handling noise and DC below the cutoff."""

    name = "high_pass"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Cutoff must be audible-bass range and well below Nyquist."""
        reject_unknown(params, {"cutoff_hz", "order"})
        cutoff = number(params, "cutoff_hz", 20.0, min(300.0, 0.45 * sample_rate))
        return {"cutoff_hz": cutoff, "order": integer(params, "order", ORDERS, 4)}

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Design the filter for this sample rate."""
        values = self.validate(params, sample_rate, channels)
        sos = butter(
            int(scalar(values, "order")),
            scalar(values, "cutoff_hz"),
            btype="highpass",
            fs=sample_rate,
            output="sos",
        )
        return SosStream(np.asarray(sos, dtype=np.float64), channels)
