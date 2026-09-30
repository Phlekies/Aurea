"""Nonoverlapping 30 ms frame features shared by the VAD, noise profile and diagnostics.

Each frame keeps scalars only; memory is bounded by the frame count (at most 60,000
records at the 30 minute ingestion limit), not by samples or per-frame spectra.

Band powers integrate channel-averaged, one-sided Hann density periodograms after
removing each channel's DC. Bands are [20,80), [80,4000), [4000,min(10000,Nyquist)] Hz.
Frame power is the unweighted AC mean square after this same per-channel centering.
Flatness is the geometric/arithmetic PSD mean ratio above 20 Hz, with a numerical
floor 1e-12 relative to mean PSD. ZCR keeps native channels, avoiding stereo
cancellation. Zero padding improves bin placement, not frequency resolution.

PSD convention reference:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.periodogram.html
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.fft import next_fast_len
from scipy.signal import get_window

WINDOW_SECONDS = 0.03


@dataclass(frozen=True, slots=True)
class WindowFeatures:
    """Scalar evidence for one frame; activity flags are set after segmentation."""

    start_seconds: float
    duration_seconds: float
    power: float
    low_power: float
    voice_power: float
    sibilance_power: float
    flatness: float
    zero_crossing_rate: float
    onset_db: float
    speech_candidate: bool = False
    low_activity: bool = False


def frame_length(sample_rate: int) -> int:
    """Native samples per analysis frame."""
    return max(1, round(WINDOW_SECONDS * sample_rate))


def density(samples: NDArray[np.float64], sample_rate: int, nfft: int) -> NDArray[np.float64]:
    """One-sided Hann PSD, power-averaged over native channels (never downmixed)."""
    centered = samples - np.mean(samples, axis=0, keepdims=True)
    window = np.asarray(get_window("hann", len(samples), fftbins=True), dtype=np.float64)
    transformed = np.fft.rfft(centered * window[:, None], n=nfft, axis=0)
    power = np.mean(transformed.real**2 + transformed.imag**2, axis=1)
    power /= sample_rate * float(np.sum(window**2))
    power[1:-1] *= 2.0
    if nfft % 2:
        power[-1] *= 2.0
    return np.asarray(power, dtype=np.float64)


def flatness(power: NDArray[np.float64]) -> float:
    """Spectral flatness in [0, 1]; zero for an all-zero spectrum."""
    mean = float(np.mean(power)) if len(power) else 0.0
    if not mean:
        return 0.0
    geometric = float(np.exp(np.mean(np.log(np.maximum(power, mean * 1e-12)))))
    return min(1.0, max(0.0, geometric / mean))


class FrameAccumulator:
    """Stream blocks of native samples into contiguous frame features."""

    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.window_frames = frame_length(sample_rate)
        self.nfft = int(next_fast_len(self.window_frames))
        self.frequencies = np.fft.rfftfreq(self.nfft, 1.0 / sample_rate)
        self.bin_hz = sample_rate / self.nfft
        self.low = (self.frequencies >= 20) & (self.frequencies < 80)
        self.voice = (self.frequencies >= 80) & (self.frequencies < 4000)
        self.sibilance = (self.frequencies >= 4000) & (self.frequencies <= 10000)
        # At Nyquist=4000 there is no physical sibilance band to assess.
        if sample_rate == 8000:
            self.sibilance[:] = False
        self.ac = self.frequencies >= 20
        self.pending: NDArray[np.float64] = np.empty((0, channels))
        self.windows: list[WindowFeatures] = []
        self.processed = 0
        self.previous_power = 0.0

    def add(self, block: NDArray[np.float64]) -> None:
        """Consume a block; a trailing partial frame waits for the next block."""
        samples = np.concatenate((self.pending, block), axis=0)
        complete = len(samples) // self.window_frames
        for index in range(complete):
            start = index * self.window_frames
            self._window(samples[start : start + self.window_frames])
        self.pending = samples[complete * self.window_frames :].copy()

    def _window(self, samples: NDArray[np.float64]) -> None:
        psd = density(samples, self.sample_rate, self.nfft)
        centered = samples - np.mean(samples, axis=0, keepdims=True)
        power = float(np.mean(centered**2))
        changes = np.count_nonzero((samples[1:] < 0) != (samples[:-1] < 0))
        onset = max(0.0, 10 * math.log10(max(power, 1e-12) / max(self.previous_power, 1e-12)))
        self.windows.append(
            WindowFeatures(
                self.processed / self.sample_rate,
                len(samples) / self.sample_rate,
                power,
                float(np.sum(psd[self.low])) * self.bin_hz,
                float(np.sum(psd[self.voice])) * self.bin_hz,
                float(np.sum(psd[self.sibilance])) * self.bin_hz,
                flatness(psd[self.ac]),
                float(changes) / (max(1, len(samples) - 1) * self.channels),
                onset,
            )
        )
        self.processed += len(samples)
        self.previous_power = power

    def finish(self) -> tuple[WindowFeatures, ...]:
        """Flush the final partial frame and return all frames in time order."""
        if len(self.pending):
            self._window(self.pending)
            self.pending = np.empty((0, self.channels))
        return tuple(self.windows)
