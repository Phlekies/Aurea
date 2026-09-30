"""De-hum: narrow cuts at the mains fundamental and its harmonics.

Input: float blocks ``(frames, channels)``. Parameters: ``fundamental_hz`` in [40, 70]
Hz (normally the detected 50 or 60 Hz), ``harmonics`` 1--10 (lines above 0.45 x sample
rate are skipped), ``q`` 5--100 for the fundamental and ``attenuation_db`` 3--60.

Each line is an RBJ peaking biquad with gain -attenuation_db, so the depth at the
line centre is exactly the requested attenuation rather than an unbounded notch. Line
k uses Q = k x q, keeping one bandwidth (fundamental / q, e.g. 1.7 Hz at 50 Hz, q=30)
for every harmonic because mains harmonics are equally narrow in Hz. Causal, starts
from rest; very narrow cuts ring briefly after abrupt hum onsets.

Reference: R. Bristow-Johnson, "Cookbook formulae for audio EQ biquad filter
coefficients" (peaking EQ).
"""

import math

import numpy as np
from numpy.typing import NDArray

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


def peaking_section(frequency: float, q: float, gain_db: float, sample_rate: int) -> list[float]:
    """One normalized second-order section ``[b0, b1, b2, 1, a1, a2]``."""
    amplitude = 10 ** (gain_db / 40)
    omega = 2 * math.pi * frequency / sample_rate
    alpha = math.sin(omega) / (2 * q)
    cosine = math.cos(omega)
    a0 = 1 + alpha / amplitude
    return [
        (1 + alpha * amplitude) / a0,
        -2 * cosine / a0,
        (1 - alpha * amplitude) / a0,
        1.0,
        -2 * cosine / a0,
        (1 - alpha / amplitude) / a0,
    ]


class DeHumProcessor(BaseProcessor):
    """Attenuate mains hum lines while leaving neighbouring frequencies intact."""

    name = "dehum"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Mains-range fundamental and bounded depth/count."""
        reject_unknown(params, {"fundamental_hz", "harmonics", "q", "attenuation_db"})
        return {
            "fundamental_hz": number(params, "fundamental_hz", 40.0, 70.0),
            "harmonics": integer(params, "harmonics", tuple(range(1, 11)), 4),
            "q": number(params, "q", 5.0, 100.0, 30.0),
            "attenuation_db": number(params, "attenuation_db", 3.0, 60.0, 30.0),
        }

    @staticmethod
    def sections(values: dict[str, ParameterValue], sample_rate: int) -> NDArray[np.float64]:
        """Cascade of one peaking cut per audible harmonic below 0.45 x sample rate."""
        fundamental = scalar(values, "fundamental_hz")
        rows = [
            peaking_section(
                fundamental * harmonic,
                scalar(values, "q") * harmonic,
                -scalar(values, "attenuation_db"),
                sample_rate,
            )
            for harmonic in range(1, int(scalar(values, "harmonics")) + 1)
            if fundamental * harmonic < 0.45 * sample_rate
        ]
        return np.asarray(rows, dtype=np.float64)

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Design all sections for this sample rate."""
        values = self.validate(params, sample_rate, channels)
        return SosStream(self.sections(values, sample_rate), channels)
