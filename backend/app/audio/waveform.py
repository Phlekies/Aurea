"""Time-bucket amplitude preview without loading an entire podcast into RAM."""

from pathlib import Path

import numpy as np
import soundfile as sf

from app.domain.audio import Waveform
from app.domain.errors import InvalidAudioFile


def create_waveform(path: Path, points: int = 2048) -> Waveform:
    """Read bounded blocks; retain absolute peak amplitude per bucket and channel.

    Peaks are linear full-scale amplitude [0, 1], not dB or loudness. No sample
    rate assumptions; this is a visual approximation, not an analysis metric.
    """
    if points <= 0:
        raise ValueError("Waveform points must be positive")
    try:
        with sf.SoundFile(path) as audio:
            if audio.frames == 0:
                raise InvalidAudioFile("El archivo no contiene muestras de audio.")
            boundaries = np.linspace(0, audio.frames, min(points, audio.frames) + 1, dtype=int)
            peaks: list[list[float]] = [[] for _ in range(audio.channels)]
            for length in np.diff(boundaries):
                block = audio.read(int(length), dtype="float32", always_2d=True)
                if not np.isfinite(block).all() or np.max(np.abs(block)) > 1:
                    raise InvalidAudioFile(
                        "El audio contiene muestras no válidas o fuera de rango."
                    )
                for channel, peak in zip(peaks, np.max(np.abs(block), axis=0), strict=True):
                    channel.append(float(peak))
            return Waveform(
                duration_seconds=audio.frames / audio.samplerate,
                sample_rate=audio.samplerate,
                channels=audio.channels,
                peaks=peaks,
            )
    except (sf.LibsndfileError, OSError) as error:
        raise InvalidAudioFile("No se pudo generar la vista del audio.") from error
