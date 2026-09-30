"""Phase-preserving, stereo-linked spectral reducers with bounded STFT state.

Sine analysis/synthesis windows, 50% overlap, satisfy sum(w**2)=1. A half-window
left pad and an explicit drain give exact alignment and length, including tails.
Noise is a one-sided density PSD in dBFS/Hz, interpolated onto the FFT grid and
converted to unnormalised squared FFT magnitude. Channels share a real gain;
their complex phase and relative level are retained, including opposed stereo.

Wiener uses decision-directed a-priori SNR (Ephraim/Malah 1984, equation 21)
and gain xi/(1+xi); this is a Wiener filter, not their MMSE-STSA estimator.
"""

import math
from collections.abc import Mapping

import numpy as np
from scipy.ndimage import uniform_filter1d

from app.domain.audio import AudioBuffer
from app.domain.errors import ProcessingFailed
from app.domain.processing import ParameterValue
from app.processors.base import BaseProcessor, Block, Parameters, reject_unknown

ALGORITHMS = ("spectral_subtraction", "spectral_gate", "wiener")
STRENGTHS = ("light", "balanced", "strong")
# Oversubtraction, floor (amplitude), gate threshold (power ratio).
PRESETS = {"light": (1.0, 0.35, 1.5), "balanced": (1.5, 0.18, 2.0), "strong": (2.5, 0.08, 3.0)}


class NoiseReducer(BaseProcessor):
    """Common noise-reduction interface; presets hide internal DSP parameters."""

    name = "noise_reduction"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        """Accept one algorithm, strength and a finite, ordered density profile."""
        reject_unknown(
            params, {"algorithm", "strength", "noise_frequencies_hz", "noise_psd_dbfs_per_hz"}
        )
        algorithm, strength = params.get("algorithm", "wiener"), params.get("strength", "balanced")
        if algorithm not in ALGORITHMS or strength not in STRENGTHS:
            raise ValueError("Algoritmo o intensidad de reducción de ruido no admitidos")
        frequencies, density = (
            params.get("noise_frequencies_hz"),
            params.get("noise_psd_dbfs_per_hz"),
        )
        if (
            not isinstance(frequencies, list)
            or not isinstance(density, list)
            or not 2 <= len(frequencies) <= 4097
            or len(frequencies) != len(density)
        ):
            raise ValueError("El perfil debe contener entre 2 y 4097 frecuencias y niveles")
        for values in (frequencies, density):
            if any(
                isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
                for v in values
            ):
                raise ValueError("El perfil de ruido debe contener números finitos")
        if (
            frequencies[0] != 0
            or abs(frequencies[-1] - sample_rate / 2) > 1e-6
            or any(a >= b for a, b in zip(frequencies, frequencies[1:], strict=False))
        ):
            raise ValueError("Las frecuencias deben crecer desde 0 hasta Nyquist")
        if any(not -300 <= v <= 0 for v in density):
            raise ValueError("PSD fuera del rango −300…0 dBFS/Hz")
        return {
            "algorithm": str(algorithm),
            "strength": str(strength),
            "noise_frequencies_hz": [float(v) for v in frequencies],
            "noise_psd_dbfs_per_hz": [float(v) for v in density],
        }

    def open(self, params: Parameters, sample_rate: int, channels: int) -> "SpectralStream":
        """Create independent overlap, gain and SNR state."""
        return SpectralStream(self.validate(params, sample_rate, channels), sample_rate, channels)

    def process(self, audio: AudioBuffer, params: Parameters) -> AudioBuffer:
        """Process and drain; return an aligned buffer of the original shape."""
        stream = self.open(params, audio.sample_rate, audio.samples.shape[1])
        output = np.concatenate((stream.process(audio.samples.astype(np.float64)), stream.finish()))
        if (
            output.shape != audio.samples.shape
            or not np.isfinite(output).all()
            or np.max(np.abs(output)) > 1 + 1e-12
        ):
            raise ProcessingFailed("La reducción de ruido ha generado muestras no válidas")
        return AudioBuffer(output.astype(np.float32), audio.sample_rate)


class SpectralStream:
    """Buffered spectral stream: callers must drain exactly once with ``finish``."""

    def __init__(
        self, values: Mapping[str, ParameterValue], sample_rate: int, channels: int
    ) -> None:
        self.channels = channels
        self.algorithm = str(values["algorithm"])
        self.alpha, self.floor, self.threshold = PRESETS[str(values["strength"])]
        self.size = max(256, min(4096, 2 ** round(math.log2(sample_rate * 0.032))))
        self.hop = self.size // 2
        self.window = np.sin(np.pi * (np.arange(self.size) + 0.5) / self.size)
        frequencies = np.asarray(values["noise_frequencies_hz"], dtype=np.float64)
        density = 10 ** (np.asarray(values["noise_psd_dbfs_per_hz"], dtype=np.float64) / 10)
        grid = np.fft.rfftfreq(self.size, 1 / sample_rate)
        self.noise = (
            np.interp(grid, frequencies, density) * sample_rate * float(np.sum(self.window**2)) / 2
        )
        self.noise[[0, -1]] *= 2
        self.pending = np.zeros((self.hop, channels))
        self.overlap = np.zeros((self.size, channels))
        self.previous_gain = np.ones(len(grid))
        self.previous_gamma = np.zeros(len(grid))
        self.attack = math.exp(-self.hop / (sample_rate * 0.012))
        self.release = math.exp(-self.hop / (sample_rate * 0.080))
        self.received = self.emitted = self.frame_count = 0
        self.closed = False

    def _frame(self, frame: Block) -> Block:
        spectrum = np.fft.rfft(frame * self.window[:, None], axis=0)
        power = np.mean(np.abs(spectrum) ** 2, axis=1)
        gamma = power / np.maximum(self.noise, 1e-24)
        if self.algorithm == "spectral_subtraction":
            gain = np.sqrt(np.maximum(self.floor**2, 1 - self.alpha / np.maximum(gamma, 1e-12)))
        elif self.algorithm == "spectral_gate":
            # Continuous sigmoid, never a binary spectral gate.
            ratio_db = 10 * np.log10(np.maximum(gamma, 1e-12) / self.threshold)
            gain = self.floor + (1 - self.floor) / (1 + np.exp(-np.clip(ratio_db / 3, -60, 60)))
        else:
            xi = 0.92 * self.previous_gain**2 * self.previous_gamma + 0.08 * np.maximum(
                gamma - self.alpha, 0
            )
            gain = np.maximum(self.floor, xi / (xi + self.alpha))
        gain = uniform_filter1d(gain, size=3, mode="nearest")
        # Fast opening preserves onsets; slower closing avoids flutter.
        smoothing = np.where(gain > self.previous_gain, self.attack, self.release)
        gain = np.clip(smoothing * self.previous_gain + (1 - smoothing) * gain, self.floor, 1)
        # A numerically absent noise profile must be an identity transform.
        gain = np.where(self.noise <= 1e-20, 1, gain)
        self.previous_gain, self.previous_gamma = gain, gamma
        self.overlap += (
            np.fft.irfft(spectrum * gain[:, None], n=self.size, axis=0) * self.window[:, None]
        )
        result = self.overlap[: self.hop].copy()
        self.overlap[: -self.hop] = self.overlap[self.hop :]
        self.overlap[-self.hop :] = 0
        self.frame_count += 1
        return result if self.frame_count > 1 else result[:0]

    def _consume(self) -> Block:
        chunks = []
        position = 0
        while len(self.pending) - position >= self.size:
            result = self._frame(self.pending[position : position + self.size])
            count = min(len(result), self.received - self.emitted)
            if count:
                chunks.append(result[:count])
                self.emitted += count
            position += self.hop
        self.pending = self.pending[position:].copy()
        return np.concatenate(chunks) if chunks else np.empty((0, self.channels))

    def process(self, block: Block) -> Block:
        """Accept contiguous samples; output may be delayed by one FFT window."""
        if self.closed:
            raise ValueError("Spectral stream is already closed")
        self.received += len(block)
        self.pending = np.concatenate((self.pending, block))
        return self._consume()

    def finish(self) -> Block:
        """Flush zero-padded tail and remove padding without shifting the audio."""
        if self.closed:
            return np.empty((0, self.channels))
        self.closed = True
        self.pending = np.concatenate((self.pending, np.zeros((self.size * 2, self.channels))))
        return self._consume()
