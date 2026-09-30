"""Native-channel evidence for version 0.5 diagnostic heuristics.

One pass reads the trusted decoded WAV to collect clipping runs, the shared 30 ms
frame features (app.analysis.frames) and one-second hum spectra. The configured voice
activity detector then labels frames as speech, noise or silence, and a second pass
estimates the background noise profile from selected non-speech frames
(app.analysis.noise). Detectors receive those labels as ``speech_candidate`` and
``low_activity`` flags, so diagnostics, timeline and noise profile agree.

Hum uses complete, nonoverlapping one-second windows (1 Hz resolution); shorter files
provide no hum evidence. Line power is integrated within +/-2 Hz, compared with mean
density 5--10 Hz away multiplied by the number of line bins. Persistence requires
>=6 dB contrast and >=0.1% of that window's AC power. A relative 1e-12 AC power floor
only makes line contrast finite; it is not a claimed acoustic noise floor.

Clipping keeps native channels. Hard samples are |x|>=.999; near-rail samples are
|x|>=.98. Flat tops require near-rail adjacent same-sign samples differing by <=1e-6;
isolated peaks do not count as plateaus. These numerical thresholds are heuristic,
not calibrated clinical measures or guaranteed speech detection.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray

from app.analysis.frames import FrameAccumulator, WindowFeatures, density
from app.analysis.noise import (
    NoiseFeatures,
    estimate_snr_db,
    measure_noise,
    select_noise_frames,
)
from app.analysis.vad import ActivityInput, VoiceActivityDetector, create_vad, frame_labels
from app.domain.activity import SpeechActivity
from app.domain.errors import InvalidAudioFile

BLOCK_FRAMES = 65536
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
class DiagnosticFeatures:
    sample_rate: int
    channels: int
    frames: int
    clipping: ClippingFeatures
    windows: tuple[WindowFeatures, ...]
    hum: tuple[HumFeatures, ...]
    noise: NoiseFeatures
    sibilance_bandwidth_hz: float
    activity: SpeechActivity
    estimated_snr_db: float | None


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
            spectrum = density(
                samples[start : start + self.sample_rate], self.sample_rate, self.sample_rate
            )
            ac_power = float(np.sum(spectrum[1:]))
            self.ac_power += ac_power
            for base in self.lines:
                for harmonic, (line, neighbor) in enumerate(
                    zip(self.line_masks[base], self.neighbor_masks[base], strict=True)
                ):
                    power = float(np.sum(spectrum[line]))
                    neighborhood = float(np.mean(spectrum[neighbor])) * np.count_nonzero(line)
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


def extract_features(
    path: Path, detector: VoiceActivityDetector | None = None
) -> DiagnosticFeatures:
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
            temporal = FrameAccumulator(sample_rate, channels)
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
        raw = temporal.finish()
        vad = detector if detector is not None else create_vad()
        activity = vad.detect(ActivityInput(path, sample_rate, channels, frames / sample_rate, raw))
        labels = frame_labels(activity, raw)
        selected = select_noise_frames(raw, labels)
        noise = measure_noise(path, sample_rate, channels, raw, selected, BLOCK_FRAMES)
        windows = tuple(
            replace(window, speech_candidate=label == "speech", low_activity=chosen)
            for window, label, chosen in zip(raw, labels, selected, strict=True)
        )
        speech_power = (
            10 ** (activity.speech_rms_dbfs / 10) if activity.speech_rms_dbfs is not None else 0.0
        )
        return DiagnosticFeatures(
            sample_rate,
            channels,
            frames,
            clipping.finish(),
            windows,
            hum.finish(),
            noise,
            max(0.0, min(10000.0, sample_rate / 2) - 4000.0),
            activity,
            estimate_snr_db(
                speech_power, activity.speech_seconds, noise.power, noise.duration_seconds
            ),
        )
    except (OSError, sf.LibsndfileError) as error:
        raise InvalidAudioFile("No se pudo leer el audio para diagnosticar.") from error
