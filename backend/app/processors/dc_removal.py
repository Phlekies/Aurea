"""DC removal: subtract each channel's measured mean.

Input: float blocks ``(frames, channels)``; parameter ``offsets`` holds one signed
full-scale value per channel, |offset| <= 0.25, normally the phase-2 ``dc_offset``
of the whole recording. Output keeps length, channels and sample rate.

Subtracting the global mean is exact, zero-phase and has no start-up transient, unlike
a DC-blocking filter. It does not follow a DC level that drifts during the recording;
a following high-pass filter removes any residual low-frequency drift.
"""

from app.domain.processing import ParameterValue
from app.processors.base import (
    BaseProcessor,
    Block,
    BlockProcessor,
    Parameters,
    numbers,
    reject_unknown,
)

MAX_OFFSET = 0.25


class _Subtract:
    def __init__(self, offsets: list[float]) -> None:
        self.offsets = offsets

    def process(self, block: Block) -> Block:
        return block - self.offsets


class DcRemovalProcessor(BaseProcessor):
    """Remove a constant offset per native channel."""

    name = "dc_removal"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Require exactly one offset per channel."""
        reject_unknown(params, {"offsets"})
        return {"offsets": numbers(params, "offsets", channels, -MAX_OFFSET, MAX_OFFSET)}

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Constant subtraction has no state; block boundaries are irrelevant."""
        offsets = self.validate(params, sample_rate, channels)["offsets"]
        assert isinstance(offsets, list)
        return _Subtract(offsets)
