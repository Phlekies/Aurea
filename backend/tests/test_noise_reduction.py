"""DSP properties, known-reference improvement, alignment and buffered pipelines."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.analysis.artifacts import measure_artifacts
from app.domain.activity import ActivitySegment, SpeechActivity
from app.domain.audio import AudioBuffer
from app.domain.processing import ParameterValue, ProcessingPlan, ProcessingStep
from app.pipeline.registry import default_registry
from app.pipeline.runner import prepare, run_plan
from app.processors.noise_reduction import ALGORITHMS, STRENGTHS, NoiseReducer


def profile(
    rate: int, sigma: float = 0.01, algorithm: str = "wiener", strength: str = "balanced"
) -> dict[str, ParameterValue]:
    level = 10 * np.log10(2 * sigma**2 / rate) if sigma else -300.0
    return {
        "algorithm": algorithm,
        "strength": strength,
        "noise_frequencies_hz": [0.0, rate / 2],
        "noise_psd_dbfs_per_hz": [float(level), float(level)],
    }


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("rate,frames", [(8000, 1), (16000, 8193), (44100, 13231), (96000, 48001)])
def test_identity_alignment_tail_and_native_stereo(algorithm: str, rate: int, frames: int) -> None:
    samples = np.random.default_rng(7).normal(0, 0.03, (frames, 1)).astype(np.float32)
    samples = np.concatenate((samples, -samples), axis=1)
    audio = AudioBuffer(samples, rate)
    processor = NoiseReducer()
    output = processor.process(audio, profile(rate, 0, algorithm))
    assert output.sample_rate == rate and output.samples.shape == samples.shape
    np.testing.assert_allclose(output.samples, samples, atol=1e-7, rtol=1e-6)
    stream = processor.open(profile(rate, 0, algorithm), rate, 2)
    parts = [
        stream.process(samples[start : start + 137].astype(np.float64))
        for start in range(0, frames, 137)
    ]
    parts.append(stream.finish())
    np.testing.assert_allclose(np.concatenate(parts), samples, atol=1e-7)
    assert not len(stream.finish())


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("strength", STRENGTHS)
def test_known_background_reduction_and_voice_preservation(algorithm: str, strength: str) -> None:
    rate = 16000
    time = np.arange(rate * 4) / rate
    clean = np.zeros(len(time))
    speaking = (time >= 1) & (time < 3)
    clean[speaking] = 0.1 * np.sin(2 * np.pi * 220 * time[speaking]) + 0.04 * np.sin(
        2 * np.pi * 440 * time[speaking]
    )
    noise = np.random.default_rng(13).normal(0, 0.012, len(time))
    mixed = (clean + noise).astype(np.float32)
    reducer = NoiseReducer()
    params = profile(rate, 0.012, algorithm, strength)
    output = reducer.process(AudioBuffer(mixed[:, None], rate), params).samples[:, 0]
    background = (time > 0.4) & (time < 0.9)
    reduction = 10 * np.log10(np.mean(mixed[background] ** 2) / np.mean(output[background] ** 2))
    assert reduction > 3
    # Reference error includes attenuation/distortion, not only a quieter background.
    assert np.mean((output[speaking] - clean[speaking]) ** 2) < np.mean(noise[speaking] ** 2)
    correlation = np.corrcoef(clean[speaking], output[speaking])[0, 1]
    assert correlation > 0.98
    stream = reducer.open(params, rate, 1)
    chunks = [
        stream.process(mixed[start : start + 997, None].astype(np.float64))
        for start in range(0, len(mixed), 997)
    ]
    chunks.append(stream.finish())
    np.testing.assert_allclose(np.concatenate(chunks)[:, 0], output, atol=1e-7)


def test_invalid_profiles_and_algorithms_are_rejected() -> None:
    reducer = NoiseReducer()
    changes: list[dict[str, ParameterValue]] = [
        {"algorithm": "unknown"},
        {"strength": "extreme"},
        {"noise_frequencies_hz": [0.0, 0.0]},
        {"noise_psd_dbfs_per_hz": [float("nan"), -90]},
        {"noise_psd_dbfs_per_hz": [True, -90]},
        {"unexpected": 1},
    ]
    for change in changes:
        with pytest.raises(ValueError):
            reducer.validate({**profile(16000), **change}, 16000, 1)


def test_buffered_stage_drains_through_downstream_filters_and_preserves_original(
    tmp_path: Path,
) -> None:
    rate = 16000
    samples = np.random.default_rng(2).normal(0, 0.01, (rate + 31, 2))
    source = tmp_path / "source.wav"
    sf.write(source, samples, rate, subtype="FLOAT")
    original = source.read_bytes()
    plan = ProcessingPlan(
        "light",
        "0.7.0",
        [
            ProcessingStep("noise_reduction", True, profile(rate, 0), "Identity test"),
            ProcessingStep("pre_gain", True, {"gain_db": -6.0}, "Gain test"),
        ],
    )
    registry = default_registry()
    for size in (117, 65536):
        target = tmp_path / f"render-{size}.wav"
        result = run_plan(source, target, prepare(plan, registry, rate, 2), registry, size)
        output, actual_rate = sf.read(target, always_2d=True)
        assert result.frames == len(samples) and actual_rate == rate
        np.testing.assert_allclose(output, samples * 10 ** (-6 / 20), atol=1e-8)
    assert source.read_bytes() == original


def test_artifact_guards_use_aligned_original_voice_regions(tmp_path: Path) -> None:
    rate = 16000
    samples = np.random.default_rng(3).normal(0, 0.02, rate * 2)
    source, rendered = tmp_path / "before.wav", tmp_path / "after.wav"
    sf.write(source, samples, rate, subtype="FLOAT")
    sf.write(rendered, samples * 0.05, rate, subtype="FLOAT")
    activity = SpeechActivity(
        "test",
        "1",
        0.03,
        1,
        1,
        0,
        50,
        -30,
        [ActivitySegment("speech", 0, 1), ActivitySegment("noise", 1, 2)],
        {},
    )
    metrics = measure_artifacts(source, rendered, activity)
    assert metrics.significant_speech_loss and metrics.excessive_reduction
    assert metrics.speech_energy_loss_db == pytest.approx(26.0206, abs=0.001)
    assert not metrics.possible_musical_noise
    assert measure_artifacts(source, source, None).speech_energy_loss_db is None


def test_musical_noise_guard_flags_new_isolated_tones(tmp_path: Path) -> None:
    rate = 16000
    source, rendered = tmp_path / "noise.wav", tmp_path / "tonal.wav"
    samples = np.random.default_rng(5).normal(0, 0.01, rate * 2)
    tonal = 0.01 * np.sin(2 * np.pi * 250 * np.arange(len(samples)) / rate)
    sf.write(source, samples, rate, subtype="FLOAT")
    sf.write(rendered, tonal, rate, subtype="FLOAT")
    activity = SpeechActivity(
        "test", "1", 0.03, 0, 2, 0, 0, None, [ActivitySegment("noise", 0, 2)], {}
    )
    assert measure_artifacts(source, rendered, activity).possible_musical_noise
