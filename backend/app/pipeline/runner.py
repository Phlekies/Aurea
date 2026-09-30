"""Streaming execution of a processing plan with output quality guards.

The runner reads the trusted decoded float WAV in blocks, applies the enabled steps in
plan order and writes float32 WAV at the same sample rate, length and channel count.
After every processor it rejects non-finite samples. If the rendered peak exceeds full
scale, a second pass applies one constant safety gain so the peak lands at -0.1 dBFS:
nothing is clipped, and the gain is reported as a warning. Memory depends on the block
size and the filter states, not on the recording length.
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.domain.errors import InvalidProcessingPlan, ProcessingFailed
from app.domain.processing import ParameterValue, ProcessingPlan
from app.pipeline.registry import ProcessorRegistry

BLOCK_FRAMES = 65536
SAFETY_CEILING_DBFS = -0.1


@dataclass(frozen=True)
class PreparedStep:
    """A plan step whose processor exists and whose parameters were validated."""

    processor: str
    enabled: bool
    parameters: dict[str, ParameterValue]


@dataclass(frozen=True)
class RunResult:
    """Execution facts for the processing report."""

    frames: int
    step_seconds: list[float]
    rendered_peak: float
    safety_gain_db: float


def prepare(
    plan: ProcessingPlan, registry: ProcessorRegistry, sample_rate: int, channels: int
) -> list[PreparedStep]:
    """Validate every step, enabled or not, against this recording's format."""
    prepared = []
    for index, step in enumerate(plan.steps):
        try:
            parameters = registry.create(step.processor).validate(
                step.parameters, sample_rate, channels
            )
        except ValueError as error:
            raise InvalidProcessingPlan(f"Paso {index + 1} ({step.processor}): {error}") from error
        prepared.append(PreparedStep(step.processor, step.enabled, parameters))
    return prepared


def run_plan(
    source: Path,
    destination: Path,
    steps: list[PreparedStep],
    registry: ProcessorRegistry,
    block_frames: int = BLOCK_FRAMES,
) -> RunResult:
    """Render ``steps`` from ``source`` into a new float32 WAV at ``destination``."""
    raw = destination.with_name(destination.stem + ".raw.wav")
    seconds = [0.0] * len(steps)
    peak = 0.0
    with sf.SoundFile(source) as audio:
        sample_rate, channels, frames = audio.samplerate, audio.channels, len(audio)
        stages = [
            (index, registry.create(step.processor).open(step.parameters, sample_rate, channels))
            for index, step in enumerate(steps)
            if step.enabled
        ]
        written = 0
        with sf.SoundFile(
            raw, "x", samplerate=sample_rate, channels=channels, subtype="FLOAT", format="WAV"
        ) as output:
            for block in audio.blocks(blocksize=block_frames, dtype="float64", always_2d=True):
                samples = np.asarray(block, dtype=np.float64)
                shape = samples.shape
                for index, stage in stages:
                    started = time.perf_counter()
                    samples = stage.process(samples)
                    seconds[index] += time.perf_counter() - started
                    if samples.shape != shape or not np.isfinite(samples).all():
                        raise ProcessingFailed(
                            f"El paso {steps[index].processor} ha generado muestras no válidas."
                        )
                peak = max(peak, float(np.max(np.abs(samples))))
                output.write(samples.astype(np.float32))
                written += len(samples)
    if written != frames:
        raise ProcessingFailed("La renderización no conserva la duración del audio.")
    safety_gain_db = 0.0
    if peak > 1:
        factor = 10 ** (SAFETY_CEILING_DBFS / 20) / peak
        safety_gain_db = 20 * math.log10(factor)
        with (
            sf.SoundFile(raw) as rendered,
            sf.SoundFile(
                destination,
                "x",
                samplerate=sample_rate,
                channels=channels,
                subtype="FLOAT",
                format="WAV",
            ) as output,
        ):
            for block in rendered.blocks(blocksize=block_frames, dtype="float64", always_2d=True):
                output.write((np.asarray(block) * factor).astype(np.float32))
        raw.unlink()
    else:
        raw.replace(destination)
    return RunResult(frames, seconds, peak, safety_gain_db)
