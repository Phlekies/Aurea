"""Background noise profile from VAD non-speech frames, and an approximate SNR.

Selection: frames labelled ``noise`` (neither speech nor digital silence), at least
half a frame long, whose power is <= 4x (+6 dB) the median power of all noise
frames. This rejects isolated loud events (door, cough) that are not background.

A second bounded pass over the file averages the one-sided PSD of the selected
frames (a Welch-style mean of 30 ms Hann periodograms). Stability combines the
frame power coefficient of variation with the mean cosine similarity between PSDs
averaged over consecutive groups of 10 frames. The floor is the 10th percentile of
selected frame levels; the RMS level is the duration-weighted mean power.

The SNR estimate subtracts noise power from speech-frame power:
``10 log10((P_speech - P_noise) / P_noise)``, requiring >=0.3 s of each. Without a
clean reference it is an approximation, and it is undefined (``None``) when
speech frames are not louder than the background. It is not a measured SNR.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.fft import next_fast_len

from app.analysis.frames import WINDOW_SECONDS, WindowFeatures, density, frame_length
from app.domain.activity import NoiseProfile
from app.domain.errors import InvalidAudioFile

TRANSIENT_POWER_RATIO = 4.0
STABILITY_GROUP_FRAMES = 10
MIN_STABILITY_GROUP_FRAMES = 5
MIN_ESTIMATE_SECONDS = 0.3


@dataclass(frozen=True, slots=True)
class NoiseFeatures:
    """Linear-power background evidence; zeros when no frame was selected."""

    window_count: int
    duration_seconds: float
    power: float
    floor_power: float
    flatness: float
    relative_power_std: float
    psd_stationarity: float
    frequencies_hz: tuple[float, ...]
    psd: tuple[float, ...]


def select_noise_frames(frames: Sequence[WindowFeatures], labels: Sequence[str]) -> list[bool]:
    """Choose background frames: non-speech, audible, and not transient outliers."""
    candidates = [
        label == "noise" and frame.duration_seconds >= WINDOW_SECONDS / 2
        for frame, label in zip(frames, labels, strict=True)
    ]
    powers = [frame.power for frame, chosen in zip(frames, candidates, strict=True) if chosen]
    if not powers:
        return candidates
    limit = float(np.median(powers)) * TRANSIENT_POWER_RATIO
    return [
        chosen and frame.power <= limit for frame, chosen in zip(frames, candidates, strict=True)
    ]


def measure_noise(
    path: Path,
    sample_rate: int,
    channels: int,
    frames: Sequence[WindowFeatures],
    selected: Sequence[bool],
    block_frames: int,
) -> NoiseFeatures:
    """Average selected-frame PSDs in a second pass; memory is one block and one PSD."""
    window_frames = frame_length(sample_rate)
    nfft = int(next_fast_len(window_frames))
    # Exact bin centers: rfftfreq can round the Nyquist bin a hair above sample_rate / 2.
    frequencies = np.arange(nfft // 2 + 1) * sample_rate / nfft
    mean_psd = np.zeros(len(frequencies))
    group_psd = np.zeros(len(frequencies))
    previous_psd: NDArray[np.float64] | None = None
    group_count = 0
    similarity_sum = 0.0
    similarity_count = 0
    count = 0
    index = 0

    def compare_group() -> None:
        nonlocal previous_psd, similarity_sum, similarity_count, group_count
        if group_count < MIN_STABILITY_GROUP_FRAMES:
            return
        current = group_psd / group_count
        if previous_psd is not None:
            norm = float(np.linalg.norm(current) * np.linalg.norm(previous_psd))
            if norm:
                similarity_sum += min(1.0, max(0.0, float(np.dot(current, previous_psd)) / norm))
                similarity_count += 1
        previous_psd = current.copy()
        group_psd[:] = 0
        group_count = 0

    def add(samples: NDArray[np.float64]) -> None:
        nonlocal index, count, group_count
        if index >= len(frames):
            raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
        if selected[index]:
            psd = density(samples, sample_rate, nfft)
            mean_psd[:] += psd
            group_psd[:] += psd
            group_count += 1
            count += 1
            if group_count == STABILITY_GROUP_FRAMES:
                compare_group()
        index += 1

    pending: NDArray[np.float64] = np.empty((0, channels))
    with sf.SoundFile(path) as audio:
        expected = round(sum(frame.duration_seconds for frame in frames) * sample_rate)
        if audio.samplerate != sample_rate or audio.channels != channels or len(audio) != expected:
            raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
        for block in audio.blocks(blocksize=block_frames, dtype="float64", always_2d=True):
            samples = np.concatenate((pending, np.asarray(block, dtype=np.float64)), axis=0)
            if not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1:
                raise InvalidAudioFile("El audio contiene muestras no válidas para diagnosticar.")
            complete = len(samples) // window_frames
            for position in range(complete):
                start = position * window_frames
                add(samples[start : start + window_frames])
            pending = samples[complete * window_frames :].copy()
        if len(pending):
            add(pending)
    if index != len(frames):
        raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
    compare_group()
    chosen = [frame for frame, keep in zip(frames, selected, strict=True) if keep]
    duration = sum(frame.duration_seconds for frame in chosen)
    powers = np.asarray([frame.power for frame in chosen])
    mean = float(np.mean(powers)) if count else 0.0
    return NoiseFeatures(
        window_count=count,
        duration_seconds=duration,
        power=sum(f.power * f.duration_seconds for f in chosen) / duration if duration else 0.0,
        floor_power=float(np.percentile(powers, 10)) if count else 0.0,
        flatness=float(np.mean([frame.flatness for frame in chosen])) if count else 0.0,
        relative_power_std=float(np.std(powers)) / mean if mean else 0.0,
        psd_stationarity=similarity_sum / similarity_count if similarity_count else 0.0,
        frequencies_hz=tuple(float(value) for value in frequencies),
        psd=tuple(float(value) / count if count else 0.0 for value in mean_psd),
    )


def _db(power: float) -> float | None:
    return 10 * math.log10(power) if power > 0 else None


def noise_profile(noise: NoiseFeatures) -> NoiseProfile:
    """Publishable dB profile; statistics are ``None`` without selected frames."""
    if not noise.window_count:
        return NoiseProfile(0, 0.0, None, None, None, None, None, [], [])
    return NoiseProfile(
        frame_count=noise.window_count,
        duration_seconds=noise.duration_seconds,
        rms_dbfs=_db(noise.power),
        floor_dbfs=_db(noise.floor_power),
        spectral_flatness=noise.flatness,
        relative_power_std=noise.relative_power_std,
        spectral_stability=noise.psd_stationarity,
        frequencies_hz=list(noise.frequencies_hz),
        psd_dbfs_per_hz=[_db(value) for value in noise.psd],
    )


def estimate_snr_db(
    speech_power: float, speech_seconds: float, noise_power: float, noise_seconds: float
) -> float | None:
    """Power-subtraction SNR estimate, or ``None`` when evidence is insufficient."""
    if (
        speech_seconds < MIN_ESTIMATE_SECONDS
        or noise_seconds < MIN_ESTIMATE_SECONDS
        or noise_power <= 0
        or speech_power <= noise_power
    ):
        return None
    return 10 * math.log10((speech_power - noise_power) / noise_power)
