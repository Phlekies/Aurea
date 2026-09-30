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
from scipy.signal import sosfreqz

from app.domain.errors import InvalidProcessingPlan, ProcessingFailed
from app.domain.processing import ParameterValue, ProcessingPlan
from app.pipeline.registry import ProcessorRegistry
from app.processors.base import Block, BlockProcessor, SosStream
from app.processors.noise_reduction import NoiseReducer, SpectralStream

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
    if sum(step.processor == "noise_reduction" for step in plan.steps) > 1:
        raise InvalidProcessingPlan("Solo se admite un reductor de ruido por cadena.")
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
        stages: list[tuple[int, BlockProcessor]] = []
        for index, step in enumerate(steps):
            if not step.enabled:
                continue
            processor = registry.create(step.processor)
            parameters = dict(step.parameters)
            if isinstance(processor, NoiseReducer):
                frequencies = np.asarray(parameters["noise_frequencies_hz"], dtype=np.float64)
                density = np.asarray(parameters["noise_psd_dbfs_per_hz"], dtype=np.float64)
                # The estimate belongs to the input. Propagate it through earlier
                # linear stages before comparing it to the corrected signal.
                for previous_index, previous in stages:
                    if isinstance(previous, SosStream):
                        _, response = sosfreqz(previous.sos, worN=frequencies, fs=sample_rate)
                        density += 20 * np.log10(np.maximum(np.abs(response), 1e-12))
                    elif steps[previous_index].processor == "pre_gain":
                        gain = steps[previous_index].parameters["gain_db"]
                        assert isinstance(gain, int | float)
                        density += gain
                    elif steps[previous_index].processor == "dc_removal":
                        density[0] = -300
                parameters["noise_psd_dbfs_per_hz"] = np.clip(density, -300, 0).tolist()
            stages.append((index, processor.open(parameters, sample_rate, channels)))
        written = 0
        with sf.SoundFile(
            raw, "x", samplerate=sample_rate, channels=channels, subtype="FLOAT", format="WAV"
        ) as output:

            def transform(samples: Block, start: int = 0) -> Block:
                for index, stage in stages[start:]:
                    shape = samples.shape
                    started = time.perf_counter()
                    samples = stage.process(samples)
                    seconds[index] += time.perf_counter() - started
                    valid_shape = samples.ndim == 2 and samples.shape[1] == channels
                    if not isinstance(stage, SpectralStream):
                        valid_shape = valid_shape and samples.shape == shape
                    if not valid_shape or not np.isfinite(samples).all():
                        raise ProcessingFailed(
                            f"El paso {steps[index].processor} ha generado muestras no válidas."
                        )
                return samples

            def write(samples: Block) -> None:
                nonlocal peak, written
                if len(samples):
                    peak = max(peak, float(np.max(np.abs(samples))))
                    output.write(samples.astype(np.float32))
                    written += len(samples)

            for block in audio.blocks(blocksize=block_frames, dtype="float64", always_2d=True):
                samples = np.asarray(block, dtype=np.float64)
                write(transform(samples))
            for position, (index, stage) in enumerate(stages):
                if isinstance(stage, SpectralStream):
                    started = time.perf_counter()
                    tail = stage.finish()
                    seconds[index] += time.perf_counter() - started
                    write(transform(tail, position + 1))
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
