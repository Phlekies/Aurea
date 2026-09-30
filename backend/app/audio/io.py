"""PCM conversion utilities. No restoration or loudness processing is applied here."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import resample_poly

from app.domain.audio import AudioBuffer
from app.domain.errors import InvalidAudioFile


def convert_to_float(samples: NDArray[Any]) -> NDArray[np.float32]:
    """Convert signed/unsigned PCM or floats to float32 with a channel dimension.

    Integer full scale maps to [-1, 1]; floating inputs are never silently clipped.
    """
    if samples.dtype.kind == "i":
        result = samples.astype(np.float64) / (2 ** (np.iinfo(samples.dtype).bits - 1))
    elif samples.dtype.kind == "u":
        midpoint = 2 ** (np.iinfo(samples.dtype).bits - 1)
        result = (samples.astype(np.float64) - midpoint) / midpoint
    elif samples.dtype.kind == "f":
        result = samples
    else:
        raise InvalidAudioFile("El tipo de muestras no es compatible.")
    converted = np.asarray(result, dtype=np.float32)
    return converted[:, None] if converted.ndim == 1 else converted


def load_audio(path: Path) -> AudioBuffer:
    """Read a decoded PCM file at its original rate, preserving mono/stereo."""
    try:
        samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        return AudioBuffer(samples=samples, sample_rate=sample_rate)
    except (sf.LibsndfileError, OSError) as error:
        raise InvalidAudioFile("No se pudo leer el archivo de audio.") from error


def save_audio(path: Path, audio: AudioBuffer) -> None:
    """Write lossless float32 WAV; caller must provide a fresh output path."""
    if path.exists():
        raise FileExistsError("Audio output must not overwrite an existing file")
    sf.write(path, audio.samples, audio.sample_rate, subtype="FLOAT", format="WAV")


def to_mono(audio: AudioBuffer) -> AudioBuffer:
    """Average channels into mono; preserve length, sample rate, and float32 dtype."""
    samples = np.mean(audio.samples, axis=1, keepdims=True, dtype=np.float32)
    return AudioBuffer(samples=samples, sample_rate=audio.sample_rate)


def resample(audio: AudioBuffer, sample_rate: int) -> AudioBuffer:
    """Polyphase resampling in Hz using SciPy's anti-alias filter.

    Output length is ceil(input frames * target/native). Filter ringing may exceed
    full scale; this explicit conversion clips that overshoot to the PCM range.
    Not called by ingestion, which always keeps the source sample rate.
    Reference: https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html
    """
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive")
    if sample_rate == audio.sample_rate:
        return audio
    divisor = math.gcd(audio.sample_rate, sample_rate)
    samples = resample_poly(
        audio.samples, sample_rate // divisor, audio.sample_rate // divisor, axis=0
    )
    return AudioBuffer(samples=np.clip(samples, -1, 1).astype(np.float32), sample_rate=sample_rate)
