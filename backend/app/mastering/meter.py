"""FFmpeg R128 I/M/S/LRA plus independent >=4x / >=192 kHz true-peak estimation."""

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.config import Settings
from app.domain.errors import AudioServiceUnavailable
from app.domain.mastering import LoudnessMeasurements, LoudnessPoint

BLOCK_FRAMES = 65536
NUMBER = r"(?:[-+]?\d+(?:\.\d+)?|[-+]?inf|nan)"


@dataclass(frozen=True)
class PcmFacts:
    sample_rate: int
    channels: int
    frames: int
    peak: float
    power: float
    finite: bool
    clipped_samples: int
    subtype: str

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def inspect_pcm(path: Path) -> PcmFacts:
    """Scan the actual file in bounded blocks, including the final partial block."""
    with sf.SoundFile(path) as audio:
        peak, power, frames, clipped, finite = 0.0, 0.0, 0, 0, True
        for block in audio.blocks(blocksize=BLOCK_FRAMES, dtype="float64", always_2d=True):
            samples = np.asarray(block)
            valid = bool(np.isfinite(samples).all())
            finite = finite and valid
            if valid:
                peak = max(peak, float(np.max(np.abs(samples))))
                power += float(np.sum(samples * samples))
                clipped += int(np.count_nonzero(np.abs(samples) >= 1 - 2**-23))
            frames += len(samples)
        if frames < 1 or frames != len(audio):
            raise ValueError("Incomplete PCM file")
        return PcmFacts(
            audio.samplerate,
            audio.channels,
            frames,
            peak,
            power / (frames * audio.channels),
            finite,
            clipped,
            audio.subtype,
        )


def run_ffmpeg(path: Path, arguments: list[str], settings: Settings) -> str:
    """Fixed local WAV input, no shell, hidden Windows process, bounded command time."""
    command = [
        settings.ffmpeg,
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
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=settings.command_timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioServiceUnavailable(
            "El motor de masterización no está disponible o agotó su tiempo."
        ) from error
    if result.returncode:
        raise AudioServiceUnavailable("No se pudo medir o masterizar el audio. Reintenta.")
    return result.stderr


def _nullable(value: float, time: float, window: float, rate: int) -> float | None:
    if not math.isfinite(value):
        raise ValueError("Nonfinite loudness meter output")
    # FFmpeg rounds logged timestamps to microseconds; include that rounding tolerance.
    return value if time + 1 / rate + 1e-6 >= window and value > -120 else None


def measure_loudness(
    path: Path, settings: Settings, facts: PcmFacts | None = None
) -> LoudnessMeasurements:
    facts = facts or inspect_pcm(path)
    if not facts.finite:
        raise ValueError("Nonfinite PCM")
    oversampled_rate = max(192000, facts.sample_rate * 4)
    filters = (
        "[0:a:0]asplit=2[loud][peak];[loud]ebur128=framelog=info[loudout];"
        f"[peak]apad=pad_len=128,aresample={oversampled_rate}:osf=dbl:filter_size=64:phase_shift=10,"
        "astats=metadata=0:reset=0:measure_perchannel=none:measure_overall=Peak_level[peakout]"
    )
    log = run_ffmpeg(
        path,
        ["-filter_complex", filters, "-map", "[loudout]", "-map", "[peakout]", "-f", "null", "-"],
        settings,
    )
    summary = re.findall(
        rf"Integrated loudness:\s+I:\s*({NUMBER}) LUFS\s+Threshold:\s*({NUMBER}) LUFS", log
    )
    ranges = re.findall(rf"Loudness range:\s+LRA:\s*({NUMBER}) LU", log)
    peaks = re.findall(rf"Peak level dB:\s*({NUMBER})", log)
    if not summary or not ranges or not peaks:
        raise ValueError("Missing loudness summary")
    integrated, threshold = map(float, summary[-1])
    lra, true_peak = float(ranges[-1]), float(peaks[-1])
    if (
        not all(math.isfinite(value) for value in (integrated, threshold, lra))
        or math.isnan(true_peak)
        or true_peak == math.inf
    ):
        raise ValueError("Invalid loudness summary")
    integrated_value = (
        integrated if facts.duration >= 0.4 and not (threshold == 0 and integrated <= -70) else None
    )
    points: list[LoudnessPoint] = []
    m_values, s_values = [], []
    previous_bucket = -1
    for time, momentary, short in re.findall(
        rf"\bt:\s*({NUMBER}).*?\bM:\s*({NUMBER})\s+S:\s*({NUMBER})", log
    ):
        timestamp = min(float(time), facts.duration)
        point = LoudnessPoint(
            timestamp,
            _nullable(float(momentary), timestamp, 0.4, facts.sample_rate),
            _nullable(float(short), timestamp, 3.0, facts.sample_rate),
        )
        if point.momentary_lufs is not None:
            m_values.append(point.momentary_lufs)
        if point.short_term_lufs is not None:
            s_values.append(point.short_term_lufs)
        # FFmpeg's integer frame size can exceed 10 updates/s at rates such as 8004 Hz.
        # Keep the latest actual measurement in each absolute 100 ms interval.
        bucket = int((timestamp + 1 / facts.sample_rate + 1e-6) * 10)
        if bucket == previous_bucket:
            points[-1] = point
        else:
            points.append(point)
            previous_bucket = bucket
    sample_peak = 20 * math.log10(facts.peak) if facts.peak > 0 else None
    return LoudnessMeasurements(
        integrated_value,
        max(m_values, default=None),
        max(s_values, default=None),
        lra if integrated_value is not None and facts.duration >= 3 else None,
        facts.duration >= 60,
        max(sample_peak, true_peak) if sample_peak is not None else None,
        sample_peak,
        10 * math.log10(facts.power) if facts.power > 0 else None,
        points,
    )
