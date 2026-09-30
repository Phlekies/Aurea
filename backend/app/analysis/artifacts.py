"""Aligned pre/post energy and isolated spectral-peak proxies, not listening scores."""

import math
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d

from app.domain.activity import SpeechActivity
from app.domain.processing import ArtifactMetrics


def measure_artifacts(
    source: Path, rendered: Path, activity: SpeechActivity | None
) -> ArtifactMetrics:
    """Compare the same original VAD regions, retaining only block accumulators."""
    totals = np.zeros((2, 3))
    counts = np.zeros(3)
    peaks = np.zeros(2)
    spectral_count = 0
    segment_index = 0
    offset = 0
    with sf.SoundFile(source) as original, sf.SoundFile(rendered) as output:
        if (len(original), original.samplerate, original.channels) != (
            len(output),
            output.samplerate,
            output.channels,
        ):
            raise ValueError("Artifact comparison requires aligned recordings")
        for block in original.blocks(blocksize=65536, dtype="float64", always_2d=True):
            other = output.read(len(block), dtype="float64", always_2d=True)
            labels = np.zeros(len(block), dtype=np.int8)
            if activity:
                while (
                    segment_index + 1 < len(activity.segments)
                    and activity.segments[segment_index].end_seconds * original.samplerate <= offset
                ):
                    segment_index += 1
                index = segment_index
                while index < len(activity.segments):
                    segment = activity.segments[index]
                    start = max(0, round(segment.start_seconds * original.samplerate) - offset)
                    end = min(len(block), round(segment.end_seconds * original.samplerate) - offset)
                    if start >= len(block):
                        break
                    labels[start:end] = (
                        1 if segment.label == "speech" else 2 if segment.label == "noise" else 0
                    )
                    index += 1
            for label in range(3):
                selected = labels == label
                counts[label] += int(np.count_nonzero(selected)) * original.channels
                for index, samples in enumerate((block, other)):
                    totals[index, label] += float(np.sum(samples[selected] ** 2))
            # Use only uninterrupted background chunks, same regions before/after.
            for start in range(0, len(block) - 2048 + 1, 2048):
                if not np.all(labels[start : start + 2048] == 2):
                    continue
                for index, samples in enumerate((block, other)):
                    spectrum = np.fft.rfft(
                        samples[start : start + 2048] * np.hanning(2048)[:, None], axis=0
                    )
                    power = np.mean(np.abs(spectrum) ** 2, axis=1)[1:]
                    neighbours = uniform_filter1d(power, size=9, mode="nearest")
                    isolated = power > 5 * np.maximum(neighbours, 1e-24)
                    peaks[index] += float(
                        np.sum(power[isolated]) / max(float(np.sum(power)), 1e-24)
                    )
                spectral_count += 1
            offset += len(block)

    def reduction(label: int | None) -> float | None:
        before = float(np.sum(totals[0])) if label is None else float(totals[0, label])
        after = float(np.sum(totals[1])) if label is None else float(totals[1, label])
        return 10 * math.log10(before / max(after, before * 1e-30)) if before > 1e-20 else None

    total, speech, background = reduction(None), reduction(1), reduction(2)
    musical = max(0.0, float(peaks[1] - peaks[0]) / spectral_count) if spectral_count else None
    return ArtifactMetrics(
        total,
        speech,
        background,
        musical,
        bool(total is not None and total > 18),
        bool(speech is not None and speech > 6),
        bool(musical is not None and musical > 0.08),
    )
