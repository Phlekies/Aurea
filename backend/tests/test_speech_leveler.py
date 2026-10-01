"""Speech-leveling behavior, noise protection and streaming invariants."""

from itertools import pairwise

import numpy as np
import pytest
from numpy.typing import NDArray

from app.domain.audio import AudioBuffer
from app.domain.processing import ParameterValue
from app.processors.speech_leveler import SpeechLevelerProcessor, SpeechLevelerStream

RATE = 16000


def _tone(seconds: float, amplitude: float, rate: int = RATE) -> NDArray[np.float64]:
    positions = np.arange(round(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * 190 * positions)


def _rms_db(samples: NDArray[np.float64]) -> float:
    return float(10 * np.log10(np.mean(samples * samples)))


def _params(seconds: float) -> dict[str, ParameterValue]:
    return {"speech_starts_seconds": [0.0], "speech_ends_seconds": [seconds]}


def test_quiet_and_loud_speakers_move_toward_a_consistent_level() -> None:
    samples = np.concatenate((_tone(5, 0.022), _tone(5, 0.24)))[:, None]
    output = SpeechLevelerProcessor().open(_params(10), RATE, 1).process(samples)
    quiet, loud = slice(3 * RATE, 4 * RATE), slice(8 * RATE, 9 * RATE)
    input_difference = _rms_db(samples[loud]) - _rms_db(samples[quiet])
    output_difference = _rms_db(output[loud]) - _rms_db(output[quiet])
    assert input_difference > 20
    assert abs(output_difference) < 5
    assert _rms_db(output[quiet]) > _rms_db(samples[quiet]) + 7
    assert _rms_db(output[loud]) == pytest.approx(-24, abs=0.5)
    assert np.max(np.abs(output)) < 1


def test_progressively_changing_voice_is_leveled_smoothly() -> None:
    seconds = 12
    times = np.arange(seconds * RATE) / RATE
    amplitude = 0.025 * np.power(10, times / seconds)
    samples = (amplitude * np.sin(2 * np.pi * 190 * times))[:, None]
    stream = SpeechLevelerProcessor().open(_params(seconds), RATE, 1)
    output = stream.process(samples)
    input_levels = [_rms_db(samples[n * RATE : (n + 1) * RATE]) for n in range(2, 11)]
    output_levels = [_rms_db(output[n * RATE : (n + 1) * RATE]) for n in range(2, 11)]
    assert np.ptp(output_levels) < np.ptp(input_levels) * 0.5
    assert isinstance(stream, SpeechLevelerStream)
    _, gains = stream.gain_envelope()
    # Omit the explicit final VAD fade: phrase interiors follow slow changes.
    assert np.max(np.abs(np.diff(gains[10:-2]))) < 0.3


def test_pauses_and_noise_outside_vad_are_unchanged_exactly() -> None:
    rng = np.random.default_rng(701)
    samples = rng.normal(0, 0.001, (5 * RATE, 1))
    samples[RATE : 3 * RATE, 0] += _tone(2, 0.025)
    params: dict[str, ParameterValue] = {
        "speech_starts_seconds": [1.0],
        "speech_ends_seconds": [3.0],
        "noise_floor_dbfs": -60.0,
    }
    stream = SpeechLevelerProcessor().open(params, RATE, 1)
    output = stream.process(samples)
    np.testing.assert_array_equal(output[:RATE], samples[:RATE])
    np.testing.assert_array_equal(output[3 * RATE :], samples[3 * RATE :])
    assert (
        _rms_db(output[2 * RATE : int(2.8 * RATE)])
        > _rms_db(samples[2 * RATE : int(2.8 * RATE)]) + 6
    )


@pytest.mark.parametrize("noise_db", [-90.0, -60.0, -35.0])
def test_noise_only_is_not_boosted_even_if_vad_marks_it_as_speech(noise_db: float) -> None:
    samples = np.random.default_rng(702).normal(0, 10 ** (noise_db / 20), (4 * RATE, 2))
    params = _params(4)
    params["noise_floor_dbfs"] = noise_db
    output = SpeechLevelerProcessor().open(params, RATE, 2).process(samples)
    np.testing.assert_array_equal(output, samples)


def test_silence_is_unchanged_even_if_vad_marks_it_as_speech() -> None:
    samples = np.zeros((RATE, 2))
    stream = SpeechLevelerProcessor().open(_params(1), RATE, 2)
    np.testing.assert_array_equal(stream.process(samples), samples)
    assert isinstance(stream, SpeechLevelerStream)
    assert not any(stream.gain_envelope()[1])


def test_noise_after_speech_returns_to_unity_during_vad_hangover() -> None:
    samples = np.random.default_rng(703).normal(0, 0.001, (6 * RATE, 1))
    samples[: 3 * RATE, 0] += _tone(3, 0.03)
    stream = SpeechLevelerProcessor().open(_params(6), RATE, 1)
    output = stream.process(samples)
    # A causal 10 ms detector carries speech energy briefly; once it has decayed,
    # the long VAD hangover must not keep boosting the remaining noise-only region.
    tail = slice(round(3.05 * RATE), None)
    np.testing.assert_array_equal(output[tail], samples[tail])


def test_no_speech_intervals_produces_unity_gain() -> None:
    samples = _tone(1, 0.05)[:, None]
    output = SpeechLevelerProcessor().open({}, RATE, 1).process(samples)
    np.testing.assert_array_equal(output, samples)


def test_stereo_uses_one_gain_and_preserves_channel_balance() -> None:
    tone = _tone(3, 0.04)
    samples = np.column_stack((tone, tone * 0.3))
    output = SpeechLevelerProcessor().open(_params(3), RATE, 2).process(samples)
    np.testing.assert_allclose(output[:, 1], output[:, 0] * 0.3, rtol=1e-14, atol=1e-15)


def test_output_and_envelope_do_not_depend_on_block_boundaries() -> None:
    samples = np.column_stack((_tone(2, 0.04), _tone(2, 0.06)))
    params: dict[str, ParameterValue] = {
        "speech_starts_seconds": [0.1, 1.0],
        "speech_ends_seconds": [0.7, 1.9],
    }
    whole = SpeechLevelerProcessor().open(params, RATE, 2)
    split = SpeechLevelerProcessor().open(params, RATE, 2)
    expected = whole.process(samples)
    edges = [0, 1, 12, 1599, 1600, 1601, 3003, 11000, 16000, 21111, len(samples)]
    actual = np.concatenate([split.process(samples[a:b]) for a, b in pairwise(edges)])
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-14)
    assert isinstance(whole, SpeechLevelerStream) and isinstance(split, SpeechLevelerStream)
    assert split.gain_envelope() == whole.gain_envelope()


@pytest.mark.parametrize("rate", [8001, 8004, 44101, 95999])
def test_odd_rate_schedule_is_exact_and_independent_of_blocks(rate: int) -> None:
    samples = _tone(1.03, 0.03, rate)[:, None]
    whole = SpeechLevelerProcessor().open(_params(1.03), rate, 1)
    split = SpeechLevelerProcessor().open(_params(1.03), rate, 1)
    expected = whole.process(samples)
    edges = [0, 1, rate // 10, rate // 10 + 1, rate // 2, len(samples)]
    output = np.concatenate([split.process(samples[a:b]) for a, b in pairwise(edges)])
    np.testing.assert_allclose(output, expected, rtol=0, atol=1e-14)
    assert isinstance(whole, SpeechLevelerStream) and isinstance(split, SpeechLevelerStream)
    assert split.gain_envelope() == whole.gain_envelope()
    expected_frames = [(n * rate + 9) // 10 for n in range(11)] + [len(samples) - 1]
    times, gains = whole.gain_envelope()
    assert times == [frame / rate for frame in expected_frames]
    assert len(gains) == len(expected_frames)


def test_thirty_minute_odd_rate_envelope_stays_within_report_point_limit() -> None:
    rate = 8004
    frames = 1800 * rate
    stream = SpeechLevelerProcessor().open({}, rate, 1)
    block_frames = 65536
    for position in range(0, frames, block_frames):
        stream.process(np.zeros((min(block_frames, frames - position), 1)))
    assert isinstance(stream, SpeechLevelerStream)
    times, gains = stream.gain_envelope()
    assert len(times) == len(gains) == 18001
    assert times[0] == 0
    assert times[-1] == (frames - 1) / rate
    assert all(a < b for a, b in pairwise(times))
    assert not any(gains)


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000, 96000])
@pytest.mark.parametrize("channels", [1, 2])
def test_tiny_blocks_stay_finite_at_native_sample_rates(rate: int, channels: int) -> None:
    samples = np.full((17, channels), 0.04)
    stream = SpeechLevelerProcessor().open(_params(0.01), rate, channels)
    output = np.concatenate([stream.process(samples[n : n + 1]) for n in range(len(samples))])
    assert output.shape == samples.shape
    assert np.isfinite(output).all()
    assert isinstance(stream, SpeechLevelerStream)
    times, gains = stream.gain_envelope()
    assert times == [0.0, 16 / rate]
    assert len(times) == len(gains)


def test_envelope_contains_actual_applied_gains_and_only_final_extra_sample() -> None:
    samples = _tone(1.03, 0.03)[:, None]
    stream = SpeechLevelerProcessor().open(_params(1.03), RATE, 1)
    output = stream.process(samples)
    assert isinstance(stream, SpeechLevelerStream)
    times, gains = stream.gain_envelope()
    assert times == [n / 10 for n in range(11)] + [(len(samples) - 1) / RATE]
    assert len(times) == len(gains) == 12
    assert stream.gain_envelope() == (times, gains)  # reading does not append history
    for time, gain in zip(times, gains, strict=True):
        position = round(time * RATE)
        assert output[position, 0] == pytest.approx(samples[position, 0] * 10 ** (gain / 20))


def test_gain_bounds_apply_to_the_stored_and_applied_envelope() -> None:
    samples = np.concatenate((_tone(3, 0.015), _tone(4, 0.4)))[:, None]
    params = _params(7)
    params.update({"max_boost_db": 3.0, "max_cut_db": 4.0})
    stream = SpeechLevelerProcessor().open(params, RATE, 1)
    stream.process(samples)
    assert isinstance(stream, SpeechLevelerStream)
    _, gains = stream.gain_envelope()
    assert min(gains) >= -4
    assert max(gains) <= 3
    assert min(gains) < -3.8
    assert max(gains) > 2.8


def test_whole_buffer_contract_preserves_format() -> None:
    source = AudioBuffer(_tone(3, 0.025).astype(np.float32)[:, None], RATE)
    output = SpeechLevelerProcessor().process(source, _params(3))
    assert output.samples.dtype == np.float32
    assert output.samples.shape == source.samples.shape
    assert output.sample_rate == source.sample_rate


@pytest.mark.parametrize(
    "params",
    [
        {"speech_starts_seconds": [0.0]},
        {"speech_starts_seconds": [1.0], "speech_ends_seconds": [1.0]},
        {"speech_starts_seconds": [1.0], "speech_ends_seconds": [0.5]},
        {"speech_starts_seconds": [0.5, 0.0], "speech_ends_seconds": [0.9, 0.4]},
        {"speech_starts_seconds": [0.0, 0.5], "speech_ends_seconds": [0.6, 0.9]},
        {"speech_starts_seconds": [-1.0], "speech_ends_seconds": [1.0]},
        {"speech_starts_seconds": [0.0], "speech_ends_seconds": [1800.01]},
        {"speech_starts_seconds": [False], "speech_ends_seconds": [1.0]},
        {"speech_starts_seconds": [float("nan")], "speech_ends_seconds": [1.0]},
        {"speech_starts_seconds": "0"},
        {"target_rms_dbfs": -8},
        {"noise_floor_dbfs": 1},
        {"max_boost_db": 13},
        {"max_cut_db": -1},
        {"window_ms": 99},
        {"smoothing_ms": 2001},
        {"max_boost_db": True},
        {"target_rms_dbfs": float("inf")},
        {"command": "unsupported"},
    ],
)
def test_invalid_parameters_are_rejected(params: dict[str, ParameterValue]) -> None:
    with pytest.raises(ValueError):
        SpeechLevelerProcessor().validate(params, RATE, 1)


def test_touching_valid_intervals_and_defaults_are_accepted() -> None:
    params: dict[str, ParameterValue] = {
        "speech_starts_seconds": [0.0, 0.5],
        "speech_ends_seconds": [0.5, 1.0],
    }
    values = SpeechLevelerProcessor().validate(params, RATE, 1)
    assert values["target_rms_dbfs"] == -24
    assert values["max_boost_db"] == 8
    assert values["window_ms"] == 300


@pytest.mark.parametrize(("rate", "channels"), [(7999, 1), (96001, 1), (16000, 0), (16000, 3)])
def test_invalid_native_format_is_rejected(rate: int, channels: int) -> None:
    with pytest.raises(ValueError):
        SpeechLevelerProcessor().validate({}, rate, channels)
