"""Reproducible synthetic-reference comparison; does not measure perceived quality.

Run with the project's editable Python environment. The noise-only PSD is an ideal
reference; separate API tests exercise the estimated VAD profile. No model/network
cost: all methods use CPU FFTs, with bounded streaming state in normal rendering.
"""

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np
from scipy.signal import lfilter, welch

from app.domain.audio import AudioBuffer
from app.domain.processing import ParameterValue
from app.processors.noise_reduction import ALGORITHMS, STRENGTHS, NoiseReducer


def benchmark() -> dict[str, object]:
    """Return nine configurations over white and coloured stationary backgrounds."""
    rate = 16000
    seconds = 4
    timeline = np.arange(rate * seconds) / rate
    speaking = (timeline >= 1) & (timeline < 3)
    clean = speaking * sum(
        0.1 / harmonic * np.sin(2 * np.pi * 220 * harmonic * timeline) for harmonic in range(1, 9)
    )
    random = np.random.default_rng(2026)
    white = random.normal(0, 0.012, len(timeline))
    coloured = np.asarray(lfilter([1], [1, -0.85], white))
    coloured *= 0.012 / np.std(coloured)
    rows: list[dict[str, object]] = []
    for label, noise in (("white", white), ("coloured", coloured)):
        frequencies, density = welch(noise, fs=rate, nperseg=1024)
        mixed = clean + noise
        audio = AudioBuffer(mixed[:, None].astype(np.float32), rate)
        background = (timeline > 0.4) & (timeline < 0.9)
        before_error = float(np.mean(noise[speaking] ** 2))
        for algorithm in ALGORITHMS:
            for strength in STRENGTHS:
                params: dict[str, ParameterValue] = {
                    "algorithm": algorithm,
                    "strength": strength,
                    "noise_frequencies_hz": frequencies.tolist(),
                    "noise_psd_dbfs_per_hz": (10 * np.log10(np.maximum(density, 1e-30))).tolist(),
                }
                elapsed = []
                output = mixed
                for _ in range(3):
                    started = time.perf_counter()
                    output = NoiseReducer().process(audio, params).samples[:, 0]
                    elapsed.append(time.perf_counter() - started)
                error = float(np.mean((output[speaking] - clean[speaking]) ** 2))
                rows.append(
                    {
                        "background": label,
                        "algorithm": algorithm,
                        "strength": strength,
                        "reference_error_improvement_db": round(
                            float(10 * np.log10(before_error / error)), 3
                        ),
                        "background_reduction_db": round(
                            float(
                                10
                                * np.log10(
                                    np.mean(noise[background] ** 2)
                                    / np.mean(output[background] ** 2)
                                )
                            ),
                            3,
                        ),
                        "voice_correlation": round(
                            float(np.corrcoef(clean[speaking], output[speaking])[0, 1]),
                            5,
                        ),
                        "median_seconds": round(float(np.median(elapsed)), 6),
                        "real_time_factor": round(float(np.median(elapsed)) / seconds, 6),
                        "external_calls": 0,
                        "model_bytes": 0,
                    }
                )
    return {
        "pipeline_version": "0.7.0",
        "python": platform.python_version(),
        "platform": platform.system(),
        "processor": platform.machine(),
        "sample_rate": rate,
        "seconds": seconds,
        "seed": 2026,
        "profile": "ideal noise-only Welch PSD",
        "runs_per_configuration": 3,
        "rows": rows,
    }


def main() -> None:
    """Write finite machine-readable results, or print them."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(benchmark(), ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(f"Benchmark saved: {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
