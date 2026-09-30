"""Corrective processors: invariants, measured responses and parameter validation."""

import math
from itertools import pairwise

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy.signal import sosfreqz

from app.domain.audio import AudioBuffer
from app.domain.errors import ProcessingFailed
from app.domain.processing import ParameterValue
from app.processors.base import BaseProcessor, StreamingProcessor
from app.processors.dc_removal import DcRemovalProcessor
from app.processors.dehum import DeHumProcessor
from app.processors.highpass import HighPassProcessor
from app.processors.pregain import PreGainProcessor

RATE = 48000
PROCESSORS: list[tuple[StreamingProcessor, dict[str, ParameterValue]]] = [
    (DcRemovalProcessor(), {"offsets": [0.01, -0.02]}),
    (HighPassProcessor(), {"cutoff_hz": 80.0, "order": 4}),
    (DeHumProcessor(), {"fundamental_hz": 50.0, "harmonics": 4, "q": 30.0, "attenuation_db": 30.0}),
    (PreGainProcessor(), {"gain_db": -6.0}),
]


def _buffer(samples: NDArray[np.float64], rate: int = RATE) -> AudioBuffer:
    shaped = samples[:, None] if samples.ndim == 1 else samples
    return AudioBuffer(shaped.astype(np.float32), rate)


def _tone(frequency: float, amplitude: float = 0.25, seconds: float = 2.0) -> NDArray[np.float64]:
    time = np.arange(round(seconds * RATE)) / RATE
    return amplitude * np.sin(2 * np.pi * frequency * time)


def _level_db(samples: NDArray[np.float64]) -> float:
    return 10 * math.log10(float(np.mean(samples**2)))


def _stereo_noise(seconds: float = 1.0, seed: int = 3) -> NDArray[np.float64]:
    return np.random.default_rng(seed).normal(0.0, 0.1, (round(seconds * RATE), 2))


@pytest.mark.parametrize(("processor", "params"), PROCESSORS, ids=lambda item: str(item)[:20])
def test_processors_preserve_shape_rate_and_finiteness(
    processor: StreamingProcessor, params: dict[str, ParameterValue]
) -> None:
    source = _buffer(_stereo_noise() * 0.5)
    result = processor.process(source, params)
    assert result.sample_rate == source.sample_rate
    assert result.samples.shape == source.samples.shape
    assert result.samples.dtype == np.float32
    assert np.isfinite(result.samples).all()


@pytest.mark.parametrize(("processor", "params"), PROCESSORS[1:], ids=lambda item: str(item)[:20])
def test_linear_processors_map_silence_to_silence(
    processor: StreamingProcessor, params: dict[str, ParameterValue]
) -> None:
    silence = _buffer(np.zeros((4800, 2)))
    assert not np.any(processor.process(silence, params).samples)


@pytest.mark.parametrize(("processor", "params"), PROCESSORS, ids=lambda item: str(item)[:20])
def test_streaming_is_independent_of_block_boundaries(
    processor: StreamingProcessor, params: dict[str, ParameterValue]
) -> None:
    samples = _stereo_noise(0.5)
    whole = processor.open(params, RATE, 2).process(samples)
    stream = processor.open(params, RATE, 2)
    edges = [0, 1, 333, 4096, 4097, 17000, len(samples)]
    parts = [stream.process(samples[start:end]) for start, end in pairwise(edges)]
    np.testing.assert_allclose(np.concatenate(parts), whole, rtol=0, atol=1e-12)


def test_dc_removal_subtracts_each_channel_offset_exactly() -> None:
    samples = np.column_stack((_tone(440) + 0.05, _tone(440) - 0.03))
    result = DcRemovalProcessor().process(_buffer(samples), {"offsets": [0.05, -0.03]})
    np.testing.assert_allclose(np.mean(result.samples, axis=0), [0, 0], atol=1e-6)


@pytest.mark.parametrize("gain_db", [-12.0, -1.5, 6.0])
def test_pre_gain_changes_level_by_exactly_the_requested_decibels(gain_db: float) -> None:
    source = _tone(1000, 0.1)
    result = PreGainProcessor().process(_buffer(source), {"gain_db": gain_db})
    measured = _level_db(result.samples[:, 0].astype(np.float64)) - _level_db(source)
    assert measured == pytest.approx(gain_db, abs=0.001)


@pytest.mark.parametrize("cutoff", [60.0, 70.0, 80.0, 100.0])
def test_high_pass_attenuates_rumble_and_keeps_the_voice_band(cutoff: float) -> None:
    processor = HighPassProcessor()
    rumble = processor.process(_buffer(_tone(cutoff / 2)), {"cutoff_hz": cutoff})
    voice = processor.process(_buffer(_tone(cutoff * 4)), {"cutoff_hz": cutoff})
    settle = RATE // 2  # discard the start-up transient of the causal filter
    rumble_loss = _level_db(_tone(cutoff / 2)[settle:]) - _level_db(
        rumble.samples[settle:, 0].astype(np.float64)
    )
    voice_loss = _level_db(_tone(cutoff * 4)[settle:]) - _level_db(
        voice.samples[settle:, 0].astype(np.float64)
    )
    # 4th-order Butterworth: 24 dB/octave below the cutoff, flat two octaves above.
    assert rumble_loss == pytest.approx(10 * math.log10(1 + 2**8), abs=0.3)
    assert voice_loss < 0.01


def test_dehum_cuts_every_line_by_the_requested_depth_and_spares_neighbours() -> None:
    params: dict[str, ParameterValue] = {
        "fundamental_hz": 60.0,
        "harmonics": 5,
        "q": 30.0,
        "attenuation_db": 24.0,
    }
    values = DeHumProcessor().validate(params, RATE, 1)
    sections = DeHumProcessor.sections(values, RATE)
    lines = [60.0 * k for k in range(1, 6)]
    between = [90.0, 150.0, 210.0, 270.0, 1000.0]
    _, response = sosfreqz(sections, worN=lines + between, fs=RATE)
    gains = 20 * np.log10(np.abs(response))
    np.testing.assert_allclose(gains[:5], -24.0, atol=0.2)
    assert np.all(gains[5:] > -0.5)


def test_dehum_skips_harmonics_near_nyquist() -> None:
    values = DeHumProcessor().validate({"fundamental_hz": 60.0, "harmonics": 10}, 8000, 1)
    assert len(DeHumProcessor.sections(values, 8000)) == 10
    # 0.45 x 1200 Hz = 540 Hz: harmonics 1-8 (<= 480 Hz) remain, 540 Hz is excluded.
    values = DeHumProcessor().validate({"fundamental_hz": 60.0, "harmonics": 10}, 1200, 1)
    assert len(DeHumProcessor.sections(values, 1200)) == 8


@pytest.mark.parametrize(
    ("processor", "params"),
    [
        (DcRemovalProcessor(), {"offsets": [0.1]}),
        (DcRemovalProcessor(), {"offsets": [0.5, 0.0]}),
        (HighPassProcessor(), {"cutoff_hz": 5}),
        (HighPassProcessor(), {"cutoff_hz": 80, "order": 3}),
        (HighPassProcessor(), {"cutoff_hz": True}),
        (HighPassProcessor(), {"cutoff_hz": float("nan")}),
        (DeHumProcessor(), {"fundamental_hz": 120}),
        (DeHumProcessor(), {"fundamental_hz": 50, "harmonics": 0}),
        (PreGainProcessor(), {"gain_db": 40}),
        (PreGainProcessor(), {"gain_db": "6"}),
        (PreGainProcessor(), {"gain_db": 3, "command": "rm"}),
    ],
)
def test_invalid_parameters_are_rejected(
    processor: StreamingProcessor, params: dict[str, ParameterValue]
) -> None:
    with pytest.raises(ValueError):
        processor.validate(params, RATE, 2)


def test_high_pass_cutoff_must_stay_below_nyquist_margin() -> None:
    with pytest.raises(ValueError):
        HighPassProcessor().validate({"cutoff_hz": 250}, 500, 1)


def test_whole_buffer_contract_refuses_output_beyond_full_scale() -> None:
    with pytest.raises(ProcessingFailed):
        PreGainProcessor().process(_buffer(_tone(440, 0.9)), {"gain_db": 6.0})


def test_processors_share_one_base_implementation() -> None:
    assert all(isinstance(processor, BaseProcessor) for processor, _ in PROCESSORS)
    assert {processor.name for processor, _ in PROCESSORS} == {
        "dc_removal",
        "high_pass",
        "dehum",
        "pre_gain",
    }
