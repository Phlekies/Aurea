"""Pre-gain: a constant level change before level-sensitive modules.

Input: float blocks ``(frames, channels)``; parameter ``gain_db`` in [-24, +24] dB
(amplitude factor 10^(gain_db/20)). The decision engine chooses a gain that keeps the
estimated true peak below its ceiling; the runner still verifies the rendered peak.
"""

from app.domain.processing import ParameterValue
from app.processors.base import (
    BaseProcessor,
    Block,
    BlockProcessor,
    Parameters,
    number,
    reject_unknown,
)

MAX_GAIN_DB = 24.0


class _Scale:
    def __init__(self, factor: float) -> None:
        self.factor = factor

    def process(self, block: Block) -> Block:
        return block * self.factor


class PreGainProcessor(BaseProcessor):
    """Multiply every sample by one constant factor."""

    name = "pre_gain"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Accept a bounded gain in dB."""
        reject_unknown(params, {"gain_db"})
        return {"gain_db": number(params, "gain_db", -MAX_GAIN_DB, MAX_GAIN_DB)}

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Gain has no state; block boundaries are irrelevant."""
        gain = self.validate(params, sample_rate, channels)["gain_db"]
        assert isinstance(gain, float)
        return _Scale(10 ** (gain / 20))
