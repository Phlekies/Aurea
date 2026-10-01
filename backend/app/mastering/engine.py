"""Two-pass loudnorm with a 192 kHz true-peak limiter and independent final QC."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain.errors import AudioError
from app.domain.mastering import LoudnessMeasurements, MasteringPreset, OutputQC
from app.mastering.meter import PcmFacts, inspect_pcm, measure_loudness, run_ffmpeg
from app.mastering.qc import output_qc


class MasteringInputError(AudioError):
    code = "mastering_input_invalid"
    status_code = 422


class MasteringQCFailed(AudioError):
    code = "mastering_qc_failed"
    status_code = 422


@dataclass(frozen=True)
class MasterResult:
    source: PcmFacts
    output: PcmFacts
    before: LoudnessMeasurements
    after: LoudnessMeasurements
    qc: OutputQC
    mode: str
    ceiling: float
    attempts: int


def _statistics(log: str) -> dict[str, str]:
    matches = re.findall(r'\{\s*"input_i".*?\}', log, flags=re.DOTALL)
    if not matches:
        raise ValueError("Missing loudnorm statistics")
    raw = json.loads(matches[-1])
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError("Invalid loudnorm statistics")
    return raw


def _number(stats: dict[str, str], key: str) -> float:
    value = float(stats[key])
    if not math.isfinite(value):
        raise ValueError("Nonfinite loudnorm statistics")
    return value


def master_audio(
    source: Path, destination: Path, preset: MasteringPreset, settings: Settings
) -> MasterResult:
    """Always remeasure the quantized result; a failed candidate is never publishable."""
    facts = inspect_pcm(source)
    if not facts.finite or facts.peak > 1:
        raise MasteringInputError("El audio corregido contiene muestras inválidas. Reprocésalo.")
    before = measure_loudness(source, settings, facts)
    if before.integrated_lufs is None:
        raise MasteringInputError(
            "No hay loudness medible: se necesita al menos 0,4 s de audio no silencioso."
        )
    ceiling = preset.max_true_peak_dbtp - 0.3
    target = f"I={preset.target_lufs}:LRA={preset.target_lra_lu}"
    first = _statistics(
        run_ffmpeg(
            source,
            ["-af", f"loudnorm={target}:TP={ceiling}:print_format=json", "-f", "null", "-"],
            settings,
        )
    )
    measured = ":".join(
        f"{parameter}={_number(first, field)}"
        for parameter, field in (
            ("measured_I", "input_i"),
            ("measured_LRA", "input_lra"),
            ("measured_TP", "input_tp"),
            ("measured_thresh", "input_thresh"),
        )
    )
    offset = _number(first, "target_offset")
    render_target_lufs = preset.target_lufs
    for attempt in range(1, 4):
        target = f"I={render_target_lufs}:LRA={preset.target_lra_lu}"
        filters = (
            f"loudnorm={target}:TP={ceiling}:{measured}:offset={offset}:"
            "linear=true:print_format=json,"
            f"aresample={facts.sample_rate},atrim=end_sample={facts.frames}"
        )
        stats = _statistics(
            run_ffmpeg(
                source,
                [
                    "-y",
                    "-map",
                    "0:a:0",
                    "-af",
                    filters,
                    "-ar",
                    str(facts.sample_rate),
                    "-ac",
                    str(facts.channels),
                    "-c:a",
                    "pcm_s24le",
                    "-f",
                    "wav",
                    str(destination),
                ],
                settings,
            )
        )
        output = inspect_pcm(destination)
        after = measure_loudness(destination, settings, output)
        qc = output_qc(facts, output, after, preset)
        mode = stats.get("normalization_type", "")
        if mode not in ("linear", "dynamic"):
            raise ValueError("Unknown loudnorm mode")
        if qc.passed:
            return MasterResult(facts, output, before, after, qc, mode, ceiling, attempt)
        # Retry from the same source, never cascade lossy intermediate candidates.
        if after.true_peak_dbtp is not None:
            ceiling -= max(0, after.true_peak_dbtp - preset.max_true_peak_dbtp + 0.1)
        if after.integrated_lufs is not None:
            correction = preset.target_lufs - after.integrated_lufs
            if mode == "linear":
                # FFmpeg's linear initialization replaces offset with I - measured_I.
                # Changing offset would render the same failing candidate again. Adjust
                # the internal render target; QC still uses the requested publication target.
                render_target_lufs = min(-5, max(-70, render_target_lufs + correction))
            else:
                offset += correction
        ceiling = max(-9, ceiling)
        offset = min(99, max(-99, offset))
    failed = ", ".join(check.code for check in qc.checks if not check.passed)
    raise MasteringQCFailed(
        f"La salida no supera el control de calidad ({failed}). "
        "No se ha publicado ni habilitado su descarga. Prueba otro objetivo o ajuste de dinámica."
    )
