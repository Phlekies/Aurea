"""Unit contracts for representation, conversion, and bounded waveform generation."""

from pathlib import Path

import numpy as np
import pytest

from app.audio.io import convert_to_float, load_audio, resample, save_audio, to_mono
from app.audio.waveform import create_waveform
from app.domain.audio import AudioBuffer
from app.domain.errors import InvalidAudioFile


def test_signed_pcm_conversion() -> None:
    converted = convert_to_float(np.array([-32768, 0, 32767], dtype=np.int16))
    assert converted.dtype == np.float32
    assert converted.shape == (3, 1)
    np.testing.assert_allclose(converted[:, 0], [-1, 0, 32767 / 32768])


def test_unsigned_pcm_conversion() -> None:
    converted = convert_to_float(np.array([0, 128, 255], dtype=np.uint8))
    np.testing.assert_allclose(converted[:, 0], [-1, 0, 127 / 128])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1.01])
def test_invalid_samples_rejected(bad: float) -> None:
    with pytest.raises(InvalidAudioFile):
        AudioBuffer(np.array([[bad]], dtype=np.float32), 44100)


def test_mono_and_polyphase_resampling() -> None:
    samples = np.tile(np.array([[0.1, -0.1]], dtype=np.float32), (480, 1))
    audio = AudioBuffer(samples, 48000)
    mono = to_mono(audio)
    assert mono.samples.shape == (480, 1)
    assert np.count_nonzero(mono.samples) == 0
    converted = resample(audio, 44100)
    assert converted.samples.shape == (441, 2)
    assert converted.samples.dtype == np.float32
    assert np.isfinite(converted.samples).all()
    assert resample(audio, 48000) is audio
    with pytest.raises(ValueError):
        resample(audio, 0)


def test_float_wav_roundtrip_and_no_overwrite(tmp_path: Path) -> None:
    samples = np.array([[-0.5, 0.25], [0.75, -0.2]], dtype=np.float32)
    path = tmp_path / "test.wav"
    save_audio(path, AudioBuffer(samples, 48000))
    loaded = load_audio(path)
    np.testing.assert_array_equal(loaded.samples, samples)
    assert loaded.sample_rate == 48000
    with pytest.raises(FileExistsError):
        save_audio(path, loaded)


def test_waveform_keeps_peaks_and_silent_channel(tmp_path: Path) -> None:
    path = tmp_path / "test.wav"
    samples = np.zeros((100, 2), dtype=np.float32)
    samples[13, 0] = -0.8
    save_audio(path, AudioBuffer(samples, 48000))
    waveform = create_waveform(path, points=10)
    assert waveform.channels == 2
    assert len(waveform.peaks[0]) == 10
    assert max(waveform.peaks[0]) == pytest.approx(0.8)
    assert waveform.peaks[1] == [0] * 10
    assert waveform.duration_seconds == 100 / 48000
