"""Compressor curve, causal ballistics, linked stereo and streaming invariants."""

import math
from itertools import pairwise

import numpy as np
import pytest

from app.domain.audio import AudioBuffer
from app.domain.errors import ProcessingFailed
from app.domain.processing import ParameterValue
from app.processors.compressor import CompressorProcessor, gain_reduction_db


def test_hard_knee_has_requested_threshold_and_ratio() -> None:
    levels = np.array([-50.0, -24.0, -18.0, -6.0, 0.0])
    reduction = gain_reduction_db(levels, -24, 4, 0)
    np.testing.assert_allclose(levels + reduction, [-50, -24, -22.5, -19.5, -18])


def test_soft_knee_is_continuous_with_matching_slopes_at_both_edges() -> None:
    threshold, knee, ratio, epsilon = -18.0, 6.0, 3.0, 1e-5
    low, high = threshold - knee / 2, threshold + knee / 2
    levels = np.array([low - epsilon, low, low + epsilon, high - epsilon, high, high + epsilon])
    reduction = gain_reduction_db(levels, threshold, ratio, knee)
    assert reduction[1] == 0
    assert reduction[4] == pytest.approx((1 / ratio - 1) * knee / 2)
    assert reduction[2] == pytest.approx(0, abs=1e-10)
    assert (reduction[5] - reduction[4]) / epsilon == pytest.approx(1 / ratio - 1)
    assert (reduction[4] - reduction[3]) / epsilon == pytest.approx(1 / ratio - 1, abs=1e-5)
    assert np.all(reduction <= 0)


def test_attack_time_is_one_exponential_time_constant_in_gain_db() -> None:
    rate = 48000
    frames = rate // 10
    source = np.full((frames, 1), 10 ** (-8 / 20))
    stream = CompressorProcessor().open(
        {"threshold_dbfs": -24, "ratio": 4, "knee_db": 0, "attack_ms": 10}, rate, 1
    )
    output = stream.process(source)
    actual_db = 20 * np.log10(output[:, 0] / source[:, 0])
    expected_db = -12 * (1 - np.exp(-np.arange(1, frames + 1) / (rate * 0.01)))
    np.testing.assert_allclose(actual_db, expected_db, rtol=0, atol=1e-10)
    assert actual_db[rate // 100 - 1] == pytest.approx(-12 * (1 - math.exp(-1)), abs=1e-10)


def test_release_recovers_slowly_and_does_not_boost_quiet_audio() -> None:
    rate = 48000
    stream = CompressorProcessor().open(
        {"threshold_dbfs": -24, "ratio": 4, "knee_db": 0, "attack_ms": 0.1, "release_ms": 100},
        rate,
        1,
    )
    stream.process(np.full((rate, 1), 10 ** (-8 / 20)))
    source = np.full((rate // 2, 1), 0.001)
    output = stream.process(source)
    actual_db = 20 * np.log10(output[:, 0] / source[:, 0])
    assert np.all(np.diff(actual_db) >= -1e-12)
    assert actual_db[0] < -11.9
    assert actual_db[rate // 10 - 1] == pytest.approx(-12 / math.e, abs=0.01)
    assert actual_db[-1] == pytest.approx(-12 * math.exp(-5), abs=0.001)
    assert np.all(output <= source)


def test_stereo_peak_detector_applies_identical_gain_to_both_channels() -> None:
    samples = np.tile([0.5, 0.025], (48000, 1))
    stream = CompressorProcessor().open({}, 48000, 2)
    output = stream.process(samples)
    np.testing.assert_allclose(output[:, 0] / output[:, 1], 20, rtol=0, atol=1e-12)
    assert output[-1, 0] < 0.3
    assert output[-1, 1] < 0.02


def test_makeup_gain_is_applied_and_recorded_below_threshold() -> None:
    stream = CompressorProcessor().open({"makeup_gain_db": 3}, 48000, 1)
    samples = np.full((4801, 1), 0.01)
    np.testing.assert_allclose(stream.process(samples), samples * 10 ** (3 / 20))
    times, gains = stream.gain_envelope()
    assert times == [0, 0.1]
    assert gains == [3, 3]


def test_default_maps_silence_to_silence_and_never_records_positive_gain() -> None:
    stream = CompressorProcessor().open({}, 48000, 2)
    assert not np.any(stream.process(np.zeros((10000, 2))))
    assert stream.gain_envelope()[1] == [0, 0, 0, 0]


@pytest.mark.parametrize("rate", [8000, 8004, 8005, 44100, 44101, 48000, 95999, 96000])
def test_partition_boundaries_do_not_change_output_or_applied_gain_curve(rate: int) -> None:
    time = np.arange(rate // 2 + 23) / rate
    amplitude = np.where(time < 0.15, 0.025, np.where(time < 0.35, 0.7, 0.08))
    samples = np.column_stack(
        (amplitude * np.sin(2 * np.pi * 220 * time), amplitude * np.cos(2 * np.pi * 440 * time))
    )
    params: dict[str, ParameterValue] = {"attack_ms": 1.3, "release_ms": 10, "makeup_gain_db": 2}
    whole = CompressorProcessor().open(params, rate, 2)
    expected = whole.process(samples)
    divided = CompressorProcessor().open(params, rate, 2)
    edges = [0, 1, 19, 64, 333, 2048, 4096, len(samples)]
    parts = [divided.process(samples[start:end]) for start, end in pairwise(edges)]
    np.testing.assert_allclose(np.concatenate(parts), expected, rtol=0, atol=2e-12)
    actual_times, actual_gains = divided.gain_envelope()
    expected_times, expected_gains = whole.gain_envelope()
    assert actual_times == expected_times
    np.testing.assert_allclose(actual_gains, expected_gains, rtol=0, atol=2e-10)
    assert actual_times[0] == 0
    assert actual_times[-1] == (len(samples) - 1) / rate
    assert len(actual_times) <= math.ceil(len(samples) / rate * 10) + 1


def test_fractional_native_rate_keeps_exact_report_cadence_for_maximum_duration() -> None:
    rate, frames, block_frames = 8004, 8004 * 1800, 65536
    stream = CompressorProcessor().open({}, rate, 1)
    block = np.zeros((block_frames, 1))
    for start in range(0, frames, block_frames):
        stream.process(block[: min(block_frames, frames - start)])
    times, gains = stream.gain_envelope()
    positions = (np.arange(18000, dtype=np.int64) * rate + 9) // 10
    expected = [*(positions / rate).tolist(), (frames - 1) / rate]
    assert times == expected
    assert len(times) == len(gains) == 18001
    assert not any(gains)


@pytest.mark.parametrize("rate", [8000, 96000])
def test_single_sample_blocks_and_empty_blocks_stay_finite(rate: int) -> None:
    stream = CompressorProcessor().open({"attack_ms": 0.1, "release_ms": 10}, rate, 1)
    assert stream.gain_envelope() == ([], [])
    assert stream.process(np.empty((0, 1))).shape == (0, 1)
    for _ in range(128):
        output = stream.process(np.array([[0.95]]))
        assert output.shape == (1, 1)
        assert np.isfinite(output).all()
        assert 0 <= output[0, 0] <= 0.95
    times, gains = stream.gain_envelope()
    assert times == [0, 127 / rate]
    assert all(math.isfinite(value) and value <= 0 for value in gains)


def test_ratio_one_is_transparent_even_above_threshold() -> None:
    samples = np.random.default_rng(13).uniform(-0.9, 0.9, (10000, 2))
    output = CompressorProcessor().open({"ratio": 1}, 48000, 2).process(samples)
    np.testing.assert_array_equal(output, samples)


def test_whole_buffer_preserves_native_rate_channels_and_dtype() -> None:
    source = AudioBuffer(np.full((24000, 2), 0.4, dtype=np.float32), 48000)
    output = CompressorProcessor().process(source, {})
    assert output.sample_rate == source.sample_rate
    assert output.samples.shape == source.samples.shape
    assert output.samples.dtype == np.float32
    assert np.isfinite(output.samples).all()


def test_whole_buffer_refuses_makeup_gain_beyond_full_scale() -> None:
    source = AudioBuffer(np.full((240, 1), 0.9, dtype=np.float32), 48000)
    with pytest.raises(ProcessingFailed):
        CompressorProcessor().process(source, {"ratio": 1, "makeup_gain_db": 12})


@pytest.mark.parametrize(
    "params",
    [
        {"threshold_dbfs": -61},
        {"threshold_dbfs": 1},
        {"ratio": 0.9},
        {"ratio": 21},
        {"knee_db": -1},
        {"knee_db": 25},
        {"attack_ms": 0},
        {"attack_ms": 201},
        {"release_ms": 9},
        {"release_ms": 2001},
        {"makeup_gain_db": 13},
        {"ratio": True},
        {"ratio": "2"},
        {"attack_ms": float("nan")},
        {"release_ms": float("inf")},
        {"ratio": [2.0]},
        {"extra": 0},
    ],
)
def test_invalid_controls_are_rejected(params: dict[str, ParameterValue]) -> None:
    with pytest.raises(ValueError):
        CompressorProcessor().validate(params, 48000, 2)


@pytest.mark.parametrize(("rate", "channels"), [(7999, 1), (96001, 2), (48000, 0), (48000, 3)])
def test_unsupported_audio_configuration_is_rejected(rate: int, channels: int) -> None:
    with pytest.raises(ValueError):
        CompressorProcessor().open({}, rate, channels)
