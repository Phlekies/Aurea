"""Processor contract: the plan's whole-buffer interface plus bounded block streaming.

Every processor implements ``open(params, sample_rate, channels)``, returning a stateful
block processor. The whole-buffer ``process`` applies the same stream and, for
buffered spectral stages, drains its tail. Blocks are float64 ``(frames, channels)`` at
the native sample rate. Processors never change length, channel count or sample rate;
IIR state carries across blocks, so results do not depend on block boundaries.
"""

import math
from collections.abc import Mapping
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from scipy.signal import sosfilt

from app.domain.audio import AudioBuffer
from app.domain.errors import ProcessingFailed
from app.domain.processing import ParameterValue

type Block = NDArray[np.float64]
type Parameters = Mapping[str, ParameterValue]


class BlockProcessor(Protocol):
    """Stateful stage. Ordinary stages preserve shape; spectral stages drain a tail."""

    def process(self, block: Block) -> Block:
        """Transform the next contiguous block."""
        ...


class AudioProcessor(Protocol):
    """Plan contract: transform a whole buffer without changing its sample rate."""

    name: str

    def process(self, audio: AudioBuffer, params: Parameters) -> AudioBuffer:
        """Return a new buffer; raises ProcessingFailed on invalid output."""
        ...


class StreamingProcessor(AudioProcessor, Protocol):
    """Registrable processor with parameter validation and bounded-memory streaming."""

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Return complete, normalized parameters or raise ValueError."""
        ...

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Create independent streaming state for one rendering."""
        ...


class BaseProcessor:
    """Shared whole-buffer implementation in terms of ``open``."""

    name = "base"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Subclasses define their parameter schema."""
        raise NotImplementedError

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        """Subclasses create their streaming state."""
        raise NotImplementedError

    def process(self, audio: AudioBuffer, params: Parameters) -> AudioBuffer:
        """Apply the stream to the whole buffer; output must stay finite and in [-1, 1]."""
        samples = audio.samples.astype(np.float64)
        output = self.open(params, audio.sample_rate, samples.shape[1]).process(samples)
        if output.shape != samples.shape or not np.isfinite(output).all():
            raise ProcessingFailed(f"El procesador {self.name} ha generado muestras no válidas.")
        if np.max(np.abs(output)) > 1:
            raise ProcessingFailed(f"El procesador {self.name} supera la escala completa.")
        return AudioBuffer(output.astype(np.float32), audio.sample_rate)


class SosStream:
    """Cascaded biquads with per-channel state carried between blocks."""

    def __init__(self, sos: NDArray[np.float64], channels: int) -> None:
        self.sos = sos
        self.state = np.zeros((sos.shape[0], 2, channels))

    def process(self, block: Block) -> Block:
        """Filter causally from rest; identical for any block partition."""
        if not len(block):
            return block
        output, self.state = sosfilt(self.sos, block, axis=0, zi=self.state)
        return np.asarray(output, dtype=np.float64)


def reject_unknown(params: Parameters, allowed: set[str]) -> None:
    """Refuse parameters a processor does not define."""
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"Parámetros no admitidos: {', '.join(unknown)}")


def number(
    params: Parameters, key: str, low: float, high: float, default: float | None = None
) -> float:
    """Finite real within [low, high]; booleans are not numbers here."""
    value = params.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{key} debe ser un número entre {low:g} y {high:g}")
    return float(value)


def integer(params: Parameters, key: str, choices: tuple[int, ...], default: int) -> int:
    """Integer from an explicit set of supported values."""
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value not in choices:
        raise ValueError(f"{key} debe ser uno de {', '.join(map(str, choices))}")
    return int(value)


def numbers(params: Parameters, key: str, length: int, low: float, high: float) -> list[float]:
    """List of finite reals, one per channel."""
    value = params.get(key)
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{key} debe tener {length} valores")
    return [number({key: item}, key, low, high) for item in value]


def scalar(values: Mapping[str, ParameterValue], key: str) -> float:
    """Read an already validated numeric parameter."""
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} is not numeric")
    return float(value)
