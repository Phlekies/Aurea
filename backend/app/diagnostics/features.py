"""Native-channel evidence for version 0.4 diagnostic heuristics.

The 30 ms windows are internal evidence, not a VAD or a public noise profile. Their
speech/no-speech flags are deliberately only candidates. A second file pass averages
PSDs from selected low-activity windows without keeping a spectrogram in memory.
Window scalar records use slots; audio buffers, FFTs and noise accumulators are bounded
by the read block/sample rate, not recording duration. At the default 30 minute limit,
there are at most 60,000 scalar window records (no audio samples or per-window PSDs).

Band powers integrate channel-averaged, one-sided Hann density periodograms after
removing each channel's DC. Bands are [20,80), [80,4000), [4000,min(10000,Nyquist)] Hz.
Window power is the unweighted AC mean square after this same per-channel centering;
the phase-2 context retains full raw-sample RMS and signed DC offsets separately.
Zero padding improves bin placement, not physical frequency resolution. Hum uses
complete, nonoverlapping one-second windows (1 Hz resolution); shorter files provide
no hum evidence. Line power is integrated within +/-2 Hz, compared with mean density
5--10 Hz away multiplied by the number of line bins. Persistence requires >=6 dB
contrast and >=0.1% of that window's AC power. A relative 1e-12 AC power floor only
makes line contrast finite; it is not a claimed acoustic noise floor.

Flatness uses the arithmetic/geometric PSD means above 20 Hz, with a numerical floor
1e-12 relative to mean PSD. ZCR and clipping keep native channels, avoiding stereo
cancellation. Hard samples are |x|>=.999; near-rail samples are |x|>=.98. Flat tops
require near-rail adjacent same-sign samples differing by <=1e-6; isolated peaks do
not count as plateaus. These numerical thresholds are heuristic, not calibrated
clinical measures, guaranteed speech detection or measured signal-to-noise ratios.

PSD convention reference:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.periodogram.html
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.fft import next_fast_len
from scipy.signal import get_window

from app.domain.errors import InvalidAudioFile

BLOCK_FRAMES = 65536
WINDOW_SECONDS = 0.03
HARD_THRESHOLD = 0.999
NEAR_THRESHOLD = 0.98
FLAT_TOP_TOLERANCE = 1e-6
HUM_CONTRAST_DB = 6.0
HUM_MIN_POWER_RATIO = 0.001


@dataclass(frozen=True, slots=True)
class ClippingFeatures:
    sample_count_per_channel: int
    hard_counts: tuple[int, ...]
    near_counts: tuple[int, ...]
    max_hard_runs: tuple[int, ...]
    max_near_runs: tuple[int, ...]
    max_same_sign_hard_runs: tuple[int, ...]
    flat_top_counts: tuple[int, ...]
    max_flat_top_runs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WindowFeatures:
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


@dataclass(frozen=True, slots=True)
class HarmonicFeatures:
    frequency_hz: float
    power: float
    neighbor_power: float
    contrast_db: float
    persistence: float


@dataclass(frozen=True, slots=True)
class HumFeatures:
    base_hz: int
    window_count: int
    duration_seconds: float
    harmonics: tuple[HarmonicFeatures, ...]
    total_ac_power: float


@dataclass(frozen=True, slots=True)
class NoiseFeatures:
    window_count: int
    duration_seconds: float
    power: float
    flatness: float
    relative_power_std: float
    psd_stationarity: float
    frequencies_hz: tuple[float, ...]
    psd: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticFeatures:
    sample_rate: int
    channels: int
    frames: int
    clipping: ClippingFeatures
    windows: tuple[WindowFeatures, ...]
    hum: tuple[HumFeatures, ...]
    noise: NoiseFeatures
    sibilance_bandwidth_hz: float


def _run_summary(
    values: NDArray[np.int8], previous_category: int, previous_run: int
) -> tuple[int, int, int, int]:
    """Maximum, trailing category/run, new nonzero runs; join file-block boundaries."""
    starts = np.concatenate(([0], np.flatnonzero(values[1:] != values[:-1]) + 1))
    lengths = np.diff(np.concatenate((starts, [len(values)])))
    categories = values[starts]
    valid = categories != 0
    continuation = int(categories[0]) == previous_category and previous_category != 0
    if continuation:
        lengths[0] += previous_run
    maximum = int(np.max(lengths[valid])) if np.any(valid) else 0
    new_runs = int(np.count_nonzero(valid)) - int(continuation)
    last_category = int(categories[-1])
    return maximum, last_category, int(lengths[-1]) if last_category else 0, new_runs


class _ClippingAccumulator:
    def __init__(self, channels: int) -> None:
        self.channels = channels
        self.hard_counts = [0] * channels
        self.near_counts = [0] * channels
        self.hard_max = [0] * channels
        self.near_max = [0] * channels
        self.signed_hard_max = [0] * channels
        self.flat_counts = [0] * channels
        self.flat_max = [0] * channels
        self.carries = [[[0, 0] for _ in range(channels)] for _ in range(4)]
        self.previous: NDArray[np.float64] | None = None
        self.frames = 0

    def add(self, samples: NDArray[np.float64]) -> None:
        for channel in range(self.channels):
            x = samples[:, channel]
            absolute = np.abs(x)
            hard = absolute >= HARD_THRESHOLD
            near = absolute >= NEAR_THRESHOLD
            self.hard_counts[channel] += int(np.count_nonzero(hard))
            self.near_counts[channel] += int(np.count_nonzero(near))
            signed_hard = np.where(hard, np.where(x >= 0, 1, -1), 0).astype(np.int8)
            previous = self.previous[channel] if self.previous is not None else 0.0
            adjacent = np.concatenate(([previous], x[:-1]))
            flat = (
                near
                & (np.abs(adjacent) >= NEAR_THRESHOLD)
                & ((x >= 0) == (adjacent >= 0))
                & (np.abs(x - adjacent) <= FLAT_TOP_TOLERANCE)
            )
            if self.previous is None:
                flat[0] = False
            for index, values in enumerate((hard, near, signed_hard, flat)):
                category, carry = self.carries[index][channel]
                maximum, category, carry, new_runs = _run_summary(
                    values.astype(np.int8), category, carry
                )
                self.carries[index][channel] = [category, carry]
                if index == 0:
                    self.hard_max[channel] = max(self.hard_max[channel], maximum)
                elif index == 1:
                    self.near_max[channel] = max(self.near_max[channel], maximum)
                elif index == 2:
                    self.signed_hard_max[channel] = max(self.signed_hard_max[channel], maximum)
                else:
                    self.flat_counts[channel] += int(np.count_nonzero(flat)) + new_runs
                    self.flat_max[channel] = max(
                        self.flat_max[channel], maximum + 1 if maximum else 0
                    )
        self.previous = samples[-1].copy()
        self.frames += len(samples)

    def finish(self) -> ClippingFeatures:
        return ClippingFeatures(
            self.frames,
            tuple(self.hard_counts),
            tuple(self.near_counts),
            tuple(self.hard_max),
            tuple(self.near_max),
            tuple(self.signed_hard_max),
            tuple(self.flat_counts),
            tuple(self.flat_max),
        )


def _density(samples: NDArray[np.float64], sample_rate: int, nfft: int) -> NDArray[np.float64]:
    """Power mean over native channels; never downmix potentially opposed signals."""
    centered = samples - np.mean(samples, axis=0, keepdims=True)
    window = np.asarray(get_window("hann", len(samples), fftbins=True), dtype=np.float64)
    transformed = np.fft.rfft(centered * window[:, None], n=nfft, axis=0)
    power = np.mean(transformed.real**2 + transformed.imag**2, axis=1)
    power /= sample_rate * float(np.sum(window**2))
    power[1:-1] *= 2.0
    if nfft % 2:
        power[-1] *= 2.0
    return np.asarray(power, dtype=np.float64)


def _flatness(power: NDArray[np.float64]) -> float:
    mean = float(np.mean(power)) if len(power) else 0.0
    if not mean:
        return 0.0
    geometric = float(np.exp(np.mean(np.log(np.maximum(power, mean * 1e-12)))))
    return min(1.0, max(0.0, geometric / mean))


class _TemporalAccumulator:
    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.window_frames = max(1, round(WINDOW_SECONDS * sample_rate))
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
        samples = np.concatenate((self.pending, block), axis=0)
        complete = len(samples) // self.window_frames
        for index in range(complete):
            start = index * self.window_frames
            self._window(samples[start : start + self.window_frames])
        self.pending = samples[complete * self.window_frames :].copy()

    def _window(self, samples: NDArray[np.float64]) -> None:
        density = _density(samples, self.sample_rate, self.nfft)
        centered = samples - np.mean(samples, axis=0, keepdims=True)
        power = float(np.mean(centered**2))
        changes = np.count_nonzero((samples[1:] < 0) != (samples[:-1] < 0))
        onset = max(0.0, 10 * math.log10(max(power, 1e-12) / max(self.previous_power, 1e-12)))
        self.windows.append(
            WindowFeatures(
                self.processed / self.sample_rate,
                len(samples) / self.sample_rate,
                power,
                float(np.sum(density[self.low])) * self.bin_hz,
                float(np.sum(density[self.voice])) * self.bin_hz,
                float(np.sum(density[self.sibilance])) * self.bin_hz,
                _flatness(density[self.ac]),
                float(changes) / (max(1, len(samples) - 1) * self.channels),
                onset,
            )
        )
        self.processed += len(samples)
        self.previous_power = power

    def finish(self) -> tuple[WindowFeatures, ...]:
        if len(self.pending):
            self._window(self.pending)
        powers = np.asarray([window.power for window in self.windows])
        quiet, active = (float(value) for value in np.quantile(powers, [0.2, 0.85]))
        windows = []
        for window in self.windows:
            band_power = window.low_power + window.voice_power + window.sibilance_power
            voice_ratio = window.voice_power / band_power if band_power else 0.0
            speech = (
                window.duration_seconds >= WINDOW_SECONDS / 2
                and window.power >= max(1e-6, active * 0.04)
                and voice_ratio >= 0.12
                and window.flatness < 0.60
                and window.zero_crossing_rate < 0.45
            )
            quiet_candidate = window.power <= quiet * 1.6 and window.power <= active * 0.16
            low_activity = (
                window.duration_seconds >= WINDOW_SECONDS / 2
                and not speech
                and window.power > 1e-12
                and (
                    quiet_candidate
                    or (window.flatness >= 0.45 and window.zero_crossing_rate >= 0.18)
                )
            )
            windows.append(replace(window, speech_candidate=speech, low_activity=low_activity))
        return tuple(windows)


class _HumAccumulator:
    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.pending: NDArray[np.float64] = np.empty((0, channels))
        self.frequencies = np.fft.rfftfreq(sample_rate, 1.0 / sample_rate)
        self.lines = {
            base: [float(base * harmonic) for harmonic in range(1, 9) if base * harmonic <= 400]
            for base in (50, 60)
        }
        self.line_masks = {
            base: [np.abs(self.frequencies - hz) <= 2 for hz in lines]
            for base, lines in self.lines.items()
        }
        self.neighbor_masks = {
            base: [
                (np.abs(self.frequencies - hz) >= 5) & (np.abs(self.frequencies - hz) <= 10)
                for hz in lines
            ]
            for base, lines in self.lines.items()
        }
        self.power = {base: np.zeros(len(lines)) for base, lines in self.lines.items()}
        self.neighbor = {base: np.zeros(len(lines)) for base, lines in self.lines.items()}
        self.persistent = {
            base: np.zeros(len(lines), dtype=np.int64) for base, lines in self.lines.items()
        }
        self.count = 0
        self.ac_power = 0.0

    def add(self, block: NDArray[np.float64]) -> None:
        samples = np.concatenate((self.pending, block), axis=0)
        complete = len(samples) // self.sample_rate
        for index in range(complete):
            start = index * self.sample_rate
            density = _density(
                samples[start : start + self.sample_rate], self.sample_rate, self.sample_rate
            )
            ac_power = float(np.sum(density[1:]))
            self.ac_power += ac_power
            for base in self.lines:
                for harmonic, (line, neighbor) in enumerate(
                    zip(self.line_masks[base], self.neighbor_masks[base], strict=True)
                ):
                    power = float(np.sum(density[line]))
                    neighborhood = float(np.mean(density[neighbor])) * np.count_nonzero(line)
                    self.power[base][harmonic] += power
                    self.neighbor[base][harmonic] += neighborhood
                    contrast = _contrast(power, neighborhood, ac_power)
                    if (
                        contrast >= HUM_CONTRAST_DB
                        and power >= ac_power * HUM_MIN_POWER_RATIO
                        and power > 0
                    ):
                        self.persistent[base][harmonic] += 1
            self.count += 1
        self.pending = samples[complete * self.sample_rate :].copy()

    def finish(self) -> tuple[HumFeatures, ...]:
        count = max(1, self.count)
        ac_power = self.ac_power / count
        return tuple(
            HumFeatures(
                base,
                self.count,
                float(self.count),
                tuple(
                    HarmonicFeatures(
                        hz,
                        float(self.power[base][index]) / count,
                        float(self.neighbor[base][index]) / count,
                        _contrast(
                            float(self.power[base][index]) / count,
                            float(self.neighbor[base][index]) / count,
                            ac_power,
                        ),
                        float(self.persistent[base][index]) / count,
                    )
                    for index, hz in enumerate(lines)
                ),
                ac_power,
            )
            for base, lines in self.lines.items()
        )


def _contrast(power: float, neighborhood: float, total: float) -> float:
    if not power or not total:
        return 0.0
    return 10 * math.log10(max(power, total * 1e-12) / max(neighborhood, total * 1e-12))


def _noise(
    path: Path, sample_rate: int, channels: int, windows: tuple[WindowFeatures, ...]
) -> NoiseFeatures:
    window_frames = max(1, round(WINDOW_SECONDS * sample_rate))
    nfft = int(next_fast_len(window_frames))
    frequencies = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
    mean_psd = np.zeros(len(frequencies))
    previous_psd: NDArray[np.float64] | None = None
    group_psd = np.zeros(len(frequencies))
    group_count = 0
    coherence_sum = 0.0
    coherence_count = 0
    count = 0
    duration = 0.0
    weighted_power = 0.0
    power_sum = 0.0
    squared_sum = 0.0
    flatness_sum = 0.0
    pending: NDArray[np.float64] = np.empty((0, channels))
    index = 0

    def compare_group() -> None:
        nonlocal previous_psd, coherence_sum, coherence_count, group_count
        if group_count < 5:
            return
        density = group_psd / group_count
        if previous_psd is not None:
            norm = float(np.linalg.norm(density) * np.linalg.norm(previous_psd))
            if norm:
                coherence_sum += min(1.0, max(0.0, float(np.dot(density, previous_psd)) / norm))
                coherence_count += 1
        previous_psd = density.copy()
        group_psd[:] = 0
        group_count = 0

    def add(samples: NDArray[np.float64], window: WindowFeatures) -> None:
        nonlocal count, duration, group_count
        nonlocal weighted_power, power_sum, squared_sum, flatness_sum
        if not window.low_activity:
            return
        density = _density(samples, sample_rate, nfft)
        mean_psd[:] += density
        group_psd[:] += density
        group_count += 1
        if group_count == 10:
            compare_group()
        count += 1
        duration += window.duration_seconds
        weighted_power += window.power * window.duration_seconds
        power_sum += window.power
        squared_sum += window.power**2
        flatness_sum += window.flatness

    with sf.SoundFile(path) as audio:
        expected_frames = round(sum(window.duration_seconds for window in windows) * sample_rate)
        if (
            audio.samplerate != sample_rate
            or audio.channels != channels
            or len(audio) != expected_frames
        ):
            raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
        for block in audio.blocks(blocksize=BLOCK_FRAMES, dtype="float64", always_2d=True):
            samples = np.concatenate((pending, np.asarray(block, dtype=np.float64)), axis=0)
            if not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1:
                raise InvalidAudioFile("El audio contiene muestras no válidas para diagnosticar.")
            complete = len(samples) // window_frames
            for position in range(complete):
                if index >= len(windows):
                    raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
                start = position * window_frames
                add(samples[start : start + window_frames], windows[index])
                index += 1
            pending = samples[complete * window_frames :].copy()
        if len(pending):
            if index >= len(windows):
                raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
            add(pending, windows[index])
            index += 1
    if index != len(windows):
        raise InvalidAudioFile("El audio ha cambiado durante el diagnóstico.")
    compare_group()
    mean = power_sum / count if count else 0.0
    std = math.sqrt(max(0.0, squared_sum / count - mean**2)) if count else 0.0
    return NoiseFeatures(
        count,
        duration,
        weighted_power / duration if duration else 0.0,
        flatness_sum / count if count else 0.0,
        std / mean if mean else 0.0,
        coherence_sum / coherence_count if coherence_count else 0.0,
        tuple(float(value) for value in frequencies),
        tuple(float(value) / count if count else 0.0 for value in mean_psd),
    )


def extract_features(path: Path) -> DiagnosticFeatures:
    """Read a trusted decoded WAV; all samples and returned numerical values are finite."""
    try:
        with sf.SoundFile(path) as audio:
            sample_rate, channels, frames = audio.samplerate, audio.channels, len(audio)
            if audio.format not in ("WAV", "WAVEX", "RF64") or not (
                8000 <= sample_rate <= 96000 and channels in (1, 2) and frames > 0
            ):
                raise InvalidAudioFile(
                    "El audio no tiene una representación válida para diagnosticar."
                )
            clipping = _ClippingAccumulator(channels)
            temporal = _TemporalAccumulator(sample_rate, channels)
            hum = _HumAccumulator(sample_rate, channels)
            for block in audio.blocks(blocksize=BLOCK_FRAMES, dtype="float64", always_2d=True):
                samples = np.asarray(block, dtype=np.float64)
                if not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1:
                    raise InvalidAudioFile(
                        "El audio contiene muestras no válidas para diagnosticar."
                    )
                clipping.add(samples)
                temporal.add(samples)
                hum.add(samples)
            if clipping.frames != frames:
                raise InvalidAudioFile("El audio está incompleto y no se puede diagnosticar.")
        windows = temporal.finish()
        return DiagnosticFeatures(
            sample_rate,
            channels,
            frames,
            clipping.finish(),
            windows,
            hum.finish(),
            _noise(path, sample_rate, channels, windows),
            max(0.0, min(10000.0, sample_rate / 2) - 4000.0),
        )
    except (OSError, sf.LibsndfileError) as error:
        raise InvalidAudioFile("No se pudo leer el audio para diagnosticar.") from error
