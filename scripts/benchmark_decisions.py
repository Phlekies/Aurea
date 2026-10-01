"""Reproducible synthetic decision audit; no podcast corpus or perceptual claims.

Run with the project's Python: scripts/benchmark_decisions.py --output data/qa/decisions.json
"""

import argparse
import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.analysis.analyzer import analyze_audio
from app.config import Settings
from app.diagnostics.engine import diagnose_audio
from app.mastering.engine import MasteringQCFailed, master_audio
from app.mastering.presets import load_presets
from app.pipeline.decision_engine import decide
from app.pipeline.presets import load_processing_presets
from app.pipeline.registry import default_registry
from app.pipeline.runner import prepare, run_plan


def audit(output: Path) -> None:
    rate = 16000
    time = np.arange(rate * 6) / rate
    envelope = np.where(time % 1 < 0.6, 1.0, 0.0)
    voice = envelope * sum(np.sin(2 * np.pi * 145 * h * time) / h for h in range(1, 8))
    noise = np.random.default_rng(2026).normal(0, 1, len(time))
    cases = {
        "clean_voice": 0.12 * voice + 0.001 * noise,
        "weak_hum": 0.12 * voice + 0.015 * np.sin(2 * np.pi * 50 * time) + 0.002 * noise,
        "stationary_noise": 0.12 * voice + 0.008 * noise,
        "variable_voice": np.where(time < 3, 0.035, 0.28) * voice + 0.0015 * noise,
        "noise_only": 0.003 * noise,
        "silence": np.zeros(len(time)),
    }
    results: list[dict[str, Any]] = []
    presets, target = load_processing_presets(), load_presets()["podcast_standard"]
    with tempfile.TemporaryDirectory(prefix="aurea-decision-audit-") as temporary:
        root = Path(temporary)
        settings, registry = Settings(storage_dir=root / "assets"), default_registry()
        for name, samples in cases.items():
            source = root / "source.wav"
            sf.write(source, samples, rate, subtype="PCM_16")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            analysis = analyze_audio(source, "a" * 32, ffmpeg=settings.ffmpeg)
            diagnoses = diagnose_audio(source, analysis)
            analysis = replace(
                analysis,
                diagnostics=diagnoses.diagnostics,
                speech_activity=diagnoses.speech_activity,
                noise_profile=diagnoses.noise_profile,
                estimated_snr_db=diagnoses.estimated_snr_db,
            )
            for preset in presets.values():
                plan = decide(analysis, analysis.diagnostics, preset, target)
                assert plan == decide(
                    analysis, list(reversed(analysis.diagnostics)), preset, target
                )
                prepared = prepare(plan, registry, rate, 1)
                corrected = root / "corrected.wav"
                run_plan(source, corrected, prepared, registry)
                measured = sf.info(corrected)
                assert (measured.frames, measured.samplerate, measured.channels) == (
                    len(time),
                    rate,
                    1,
                )
                entry: dict[str, Any] = {
                    "case": name,
                    "preset": preset.id,
                    "configuration_version": preset.version,
                    "detections": [
                        {"code": d.code, "severity": d.severity, "confidence": d.confidence}
                        for d in analysis.diagnostics
                        if d.detected
                    ],
                    "decisions": [
                        {
                            "processor": s.processor,
                            "enabled": s.enabled,
                            "decision": s.decision,
                            "confidence": s.confidence,
                        }
                        for s in plan.steps
                    ],
                    "mastering": None,
                }
                if all(s.enabled for s in plan.mastering_steps):
                    try:
                        result = master_audio(
                            corrected, root / f"{name}-{preset.id}.wav", target, settings
                        )
                        assert result.qc.passed
                        entry["mastering"] = {
                            "lufs": result.after.integrated_lufs,
                            "true_peak_dbtp": result.after.true_peak_dbtp,
                            "qc_passed": result.qc.passed,
                            "attempts": result.attempts,
                            "normalization_mode": result.mode,
                        }
                    except MasteringQCFailed as error:
                        entry["mastering"] = {"qc_passed": False, "reason": str(error)}
                        output.with_name(f"{name}-{preset.id}-rejected.wav").parent.mkdir(
                            parents=True, exist_ok=True
                        )
                        output.with_name(f"{name}-{preset.id}-corrected.wav").write_bytes(
                            corrected.read_bytes()
                        )
                        output.with_name(f"{name}-{preset.id}-rejected.wav").write_bytes(
                            (root / f"{name}-{preset.id}.wav").read_bytes()
                        )
                if name == "noise_only":
                    assert not any(
                        s.enabled
                        for s in plan.steps
                        if s.processor in ("speech_leveler", "compressor", "pre_gain")
                    )
                assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
                results.append(entry)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, allow_nan=False), "utf-8")
    for row in results:
        active = [s["processor"] for s in row["decisions"] if s["enabled"]]
        review = [s["processor"] for s in row["decisions"] if s["decision"] == "recommended"]
        print(
            row["case"],
            row["preset"],
            "auto=" + ",".join(active),
            "review=" + ",".join(review),
            row["mastering"],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/qa/decisions.json"))
    audit(parser.parse_args().output)
