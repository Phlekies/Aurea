"""Streaming analysis; no denoising, normalization or channel downmix is performed.

Definitions and reproducibility (analyzer version 0.3.0):
* Peak is the largest absolute native sample across channels. RMS is the square root
  of mean sample power across time and channels; crest factor is peak dB minus RMS dB.
* DC is the signed time mean per channel. ZCR averages adjacent sign-bit changes per
  channel (zero counts as nonnegative); a one-frame recording has zero crossings.
* Silence is duration-weighted RMS <= -60 dBFS in every channel, in nonoverlapping
  20 ms windows rounded to native samples, including the final incomplete window.
* PSD is a channel-power average of 2048-sample periodic Hann periodograms with 50%
  overlap, no detrending, and one-sided density scaling. As in Welch, incomplete
  terminal segments are omitted. Clips shorter than 2048 use all samples through a
  rectangular window and zero padding. Integrating PSD bins gives band power;
  intervals select bin centers and include DC and Nyquist exactly once.
* Dynamics uses >=100 ms native-sample intervals, widened as needed to keep <=2000
  points. Peaks are maxima and RMS sums the original squared samples, so aggregation
  preserves transients and does not average dB values.
* FFmpeg ebur128 measures BS.1770 / EBU R128 integrated loudness (K weighting,
  400 ms blocks / 75% overlap, -70 LUFS absolute and -10 LU relative gates), without
  dual-mono compensation. Its final summary resolves 0.1 LUFS. No accepted gating
  blocks, including clips <400 ms, are represented by None rather than its -70 sentinel.
* True peak is measured in float64 by FFmpeg libswresample's 64-tap interpolation at
  >=4 times the native rate and >=192 kHz, then astats. 128 trailing zero samples
  flush the interpolation tail even for one-frame clips. Sample peak is a lower bound.
  This is an interpolated estimate, not a claim of full ITU meter certification.

References:
https://www.itu.int/rec/R-REC-BS.1770-5-202311-I/en
https://ffmpeg.org/ffmpeg-filters.html#ebur128
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html
"""

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import get_window

from app.domain.analysis import AudioAnalysis, BandEnergy, Dynamics, DynamicsPoint, Spectrum
from app.domain.errors import AudioServiceUnavailable, InvalidAudioFile

ANALYZER_VERSION = "0.3.0"
BLOCK_FRAMES = 65536
FFT_SIZE = 2048
MAX_DYNAMICS_POINTS = 2000
SILENCE_THRESHOLD_DBFS = -60.0
_BANDS = (
    ("Subgraves", 0.0, 80.0),
    ("Graves", 80.0, 250.0),
    ("Medios", 250.0, 2000.0),
    ("Presencia", 2000.0, 6000.0),
    ("Brillo", 6000.0, 12000.0),
    ("Aire", 12000.0, math.inf),
)
_NUMBER = r"(?:[-+]?\d+(?:\.\d+)?|[-+]?inf|nan)"


def _power_db(power: float) -> float | None:
    """A zero-power logarithm is absent, without an arbitrary numerical floor."""
    return 10.0 * math.log10(power) if power > 0 else None


def _amplitude_db(amplitude: float) -> float | None:
    return 20.0 * math.log10(amplitude) if amplitude > 0 else None


@dataclass
class _TemporalAccumulator:
    """Constant-size summary arrays and at most one partial silence window."""

    sample_rate: int
    channels: int
    frames: int

    def __post_init__(self) -> None:
        self.silence_window = max(1, round(self.sample_rate * 0.02))
        self.silence_pending: NDArray[np.float64] = np.empty((0, self.channels))
        self.silent_frames = 0
        self.dynamics_window = max(
            1, round(self.sample_rate * 0.1), math.ceil(self.frames / MAX_DYNAMICS_POINTS)
        )
        count = math.ceil(self.frames / self.dynamics_window)
        self.dynamics_power: NDArray[np.float64] = np.zeros(count)
        self.dynamics_peak: NDArray[np.float64] = np.zeros(count)
        self.dynamics_count: NDArray[np.int64] = np.zeros(count, dtype=np.int64)
        self.processed = 0

    def add(self, block: NDArray[np.float64]) -> None:
        silence = np.concatenate((self.silence_pending, block), axis=0)
        complete = len(silence) // self.silence_window
        if complete:
            windows = silence[: complete * self.silence_window].reshape(
                complete, self.silence_window, self.channels
            )
            power = np.mean(windows * windows, axis=1)
            self.silent_frames += (
                int(np.count_nonzero(np.all(power <= 1e-6, axis=1))) * self.silence_window
            )
        self.silence_pending = silence[complete * self.silence_window :].copy()
        position = 0
        while position < len(block):
            index = self.processed // self.dynamics_window
            take = min(
                len(block) - position,
                self.dynamics_window - self.processed % self.dynamics_window,
            )
            samples = block[position : position + take]
            self.dynamics_power[index] += float(np.sum(samples * samples))
            self.dynamics_peak[index] = max(
                self.dynamics_peak[index], float(np.max(np.abs(samples)))
            )
            self.dynamics_count[index] += take
            position += take
            self.processed += take

    def finish(self) -> tuple[float, Dynamics]:
        if len(self.silence_pending):
            power = np.mean(self.silence_pending * self.silence_pending, axis=0)
            if np.all(power <= 1e-6):
                self.silent_frames += len(self.silence_pending)
        points = [
            DynamicsPoint(
                start_seconds=index * self.dynamics_window / self.sample_rate,
                duration_seconds=int(count) / self.sample_rate,
                peak_dbfs=_amplitude_db(float(self.dynamics_peak[index])),
                rms_dbfs=_power_db(
                    float(self.dynamics_power[index]) / (int(count) * self.channels)
                ),
            )
            for index, count in enumerate(self.dynamics_count)
        ]
        return (
            100.0 * self.silent_frames / self.frames,
            Dynamics(1000.0 * self.dynamics_window / self.sample_rate, points),
        )


class _SpectrumAccumulator:
    """All complete Welch segments, independent of the file read block boundaries."""

    def __init__(self, sample_rate: int, channels: int, frames: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.segment = min(FFT_SIZE, frames)
        self.hop = max(1, self.segment // 2)
        self.window: NDArray[np.float64] = (
            np.asarray(get_window("hann", self.segment, fftbins=True), dtype=np.float64)
            if frames >= FFT_SIZE
            else np.ones(self.segment)
        )
        self.scale = sample_rate * float(np.sum(self.window * self.window))
        self.pending: NDArray[np.float64] = np.empty((0, channels))
        self.power: NDArray[np.float64] = np.zeros(FFT_SIZE // 2 + 1)
        self.segments = 0

    def add(self, block: NDArray[np.float64]) -> None:
        samples = np.concatenate((self.pending, block), axis=0)
        if len(samples) < self.segment:
            self.pending = samples
            return
        windows = np.lib.stride_tricks.sliding_window_view(samples, self.segment, axis=0)[
            :: self.hop
        ]
        transformed = np.fft.rfft(windows * self.window, n=FFT_SIZE, axis=-1)
        power = transformed.real * transformed.real + transformed.imag * transformed.imag
        reduced = np.sum(power, axis=(0, 1)) / (self.channels * self.scale)
        reduced[1:-1] *= 2.0
        self.power += reduced
        self.segments += len(windows)
        self.pending = samples[len(windows) * self.hop :].copy()

    def finish(self) -> tuple[list[BandEnergy], Spectrum]:
        density = self.power / self.segments
        frequencies = np.fft.rfftfreq(FFT_SIZE, d=1.0 / self.sample_rate)
        bin_hz = self.sample_rate / FFT_SIZE
        total_power = float(np.sum(density)) * bin_hz
        nyquist = self.sample_rate / 2.0
        bands: list[BandEnergy] = []
        for name, low, high in _BANDS:
            if low >= nyquist:
                break
            high = min(high, nyquist)
            included = (frequencies >= low) & (
                frequencies <= high if high == nyquist else frequencies < high
            )
            power = float(np.sum(density[included])) * bin_hz
            bands.append(
                BandEnergy(
                    name,
                    low,
                    high,
                    power,
                    min(100.0, max(0.0, 100.0 * power / total_power)) if total_power else 0.0,
                )
            )
        return bands, Spectrum(frequencies.tolist(), [_power_db(float(power)) for power in density])


def _loudness_and_true_peak(
    path: Path, sample_rate: int, duration: float, ffmpeg: str, timeout_seconds: int
) -> tuple[float | None, float | None]:
    """Use native channels for R128 and a separate interpolation branch for true peak."""
    oversampled_rate = max(192000, sample_rate * 4)
    filters = (
        "[0:a:0]asplit=2[loudness][peak];"
        "[loudness]ebur128=framelog=verbose[loudnessout];"
        f"[peak]apad=pad_len=128,aresample={oversampled_rate}:osf=dbl:"
        "filter_size=64:phase_shift=10,"
        "astats=metadata=0:reset=0:measure_perchannel=none:measure_overall=Peak_level[peakout]"
    )
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-nostats",
                "-loglevel",
                "info",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "wav",
                "-i",
                str(path),
                "-filter_complex_threads",
                "1",
                "-filter_complex",
                filters,
                "-map",
                "[loudnessout]",
                "-map",
                "[peakout]",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioServiceUnavailable(
            "El analizador de loudness no está disponible o ha agotado su tiempo. Reintenta."
        ) from error
    loudness = re.findall(
        rf"Integrated loudness:\s+I:\s*({_NUMBER}) LUFS\s+Threshold:\s*({_NUMBER}) LUFS",
        result.stderr,
    )
    true_peaks = re.findall(rf"Peak level dB:\s*({_NUMBER})", result.stderr)
    if result.returncode or not loudness or not true_peaks:
        raise AudioServiceUnavailable("No se pudieron obtener las medidas de loudness del audio.")
    integrated, threshold = (float(value) for value in loudness[-1])
    true_peak = float(true_peaks[-1])
    if not math.isfinite(integrated) or not math.isfinite(threshold) or math.isnan(true_peak):
        raise AudioServiceUnavailable("El analizador ha devuelto medidas de loudness no válidas.")
    if true_peak == math.inf:
        raise AudioServiceUnavailable("El analizador ha devuelto un pico no válido.")
    # FFmpeg initializes integrated loudness to -70 when no blocks pass its gate;
    # threshold remains zero, so even a valid rounded -70 measurement is distinguishable.
    accepted_loudness = (
        integrated if duration >= 0.4 and not (threshold == 0 and integrated <= -70) else None
    )
    return accepted_loudness, true_peak if math.isfinite(true_peak) else None


def analyze_audio(
    path: Path,
    audio_id: str,
    *,
    ffmpeg: str = "ffmpeg",
    timeout_seconds: int = 180,
) -> AudioAnalysis:
    """Read trusted decoded float WAV by blocks, with memory independent of duration."""
    try:
        with sf.SoundFile(path) as audio:
            sample_rate, channels, frames = audio.samplerate, audio.channels, len(audio)
            if audio.format not in ("WAV", "WAVEX", "RF64") or not (
                8000 <= sample_rate <= 96000 and channels in (1, 2) and frames > 0
            ):
                raise InvalidAudioFile("El audio no tiene una representación válida para analizar.")
            temporal = _TemporalAccumulator(sample_rate, channels, frames)
            spectral = _SpectrumAccumulator(sample_rate, channels, frames)
            peak = 0.0
            power_sum = 0.0
            dc_sum: NDArray[np.float64] = np.zeros(channels)
            crossings = 0
            previous: NDArray[np.bool_] | None = None
            for block in audio.blocks(blocksize=BLOCK_FRAMES, dtype="float64", always_2d=True):
                samples = np.asarray(block, dtype=np.float64)
                absolute = np.abs(samples)
                if not np.isfinite(samples).all() or np.max(absolute) > 1:
                    raise InvalidAudioFile("El audio contiene muestras no válidas para analizar.")
                peak = max(peak, float(np.max(absolute)))
                power_sum += float(np.sum(samples * samples))
                dc_sum += np.sum(samples, axis=0)
                negative = samples < 0
                crossings += int(np.count_nonzero(negative[1:] != negative[:-1]))
                if previous is not None:
                    crossings += int(np.count_nonzero(negative[0] != previous))
                previous = negative[-1]
                temporal.add(samples)
                spectral.add(samples)
            if temporal.processed != frames:
                raise InvalidAudioFile("El audio está incompleto y no se puede analizar.")
    except (OSError, sf.LibsndfileError) as error:
        raise InvalidAudioFile(
            "No se pudo leer la representación de audio para analizar."
        ) from error
    silence_percent, dynamics = temporal.finish()
    bands, spectrum = spectral.finish()
    duration = frames / sample_rate
    loudness, true_peak = _loudness_and_true_peak(
        path, sample_rate, duration, ffmpeg, timeout_seconds
    )
    peak_dbfs = _amplitude_db(peak)
    rms_dbfs = _power_db(power_sum / (frames * channels))
    return AudioAnalysis(
        audio_id=audio_id,
        analyzer_version=ANALYZER_VERSION,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=duration,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        crest_factor_db=(
            peak_dbfs - rms_dbfs if peak_dbfs is not None and rms_dbfs is not None else None
        ),
        integrated_lufs=loudness,
        true_peak_dbtp=(
            max(peak_dbfs, true_peak)
            if peak_dbfs is not None and true_peak is not None
            else peak_dbfs
        ),
        dc_offset=(dc_sum / frames).tolist(),
        zero_crossing_rate=crossings / (max(1, frames - 1) * channels),
        silence_percent=silence_percent,
        silence_threshold_dbfs=SILENCE_THRESHOLD_DBFS,
        bands=bands,
        spectrum=spectrum,
        dynamics=dynamics,
    )
