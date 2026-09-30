"""Analytical signals exercise finite and native-channel diagnostic evidence."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray

from app.diagnostics import features
from app.diagnostics.features import extract_features
from app.domain.errors import InvalidAudioFile


def _write(path: Path, samples: NDArray[np.float64], sample_rate: int = 16000) -> Path:
    sf.write(path, samples, sample_rate, subtype="FLOAT")
    return path


def _all_finite(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return all(_all_finite(item) for item in value)
    if isinstance(value, float):
        return bool(np.isfinite(value))
    return True


@pytest.mark.parametrize("sample_rate", [8000, 16000, 44100, 96000])
@pytest.mark.parametrize("sample_count", [1, 311])
def test_short_silence_is_finite_without_hum_or_activity(
    tmp_path: Path, sample_rate: int, sample_count: int
) -> None:
    result = extract_features(_write(tmp_path / "silent.wav", np.zeros(sample_count), sample_rate))
    assert _all_finite(asdict(result))
    assert result.frames == sample_count
    assert sum(window.duration_seconds for window in result.windows) == pytest.approx(
        sample_count / sample_rate
    )
    assert all(not window.speech_candidate and not window.low_activity for window in result.windows)
    assert result.noise.window_count == 0
    assert all(candidate.window_count == 0 for candidate in result.hum)
    assert result.clipping.hard_counts == (0,)


def test_rail_plateau_spanning_file_blocks_and_channels_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(features, "BLOCK_FRAMES", 32)
    samples = np.zeros((100, 2))
    samples[30:35, 0] = 1.0
    samples[62:66, 1] = -0.99
    samples[90, 0] = 1.0  # A lone impulse is counted near-rail but is no flat top.
    clipping = extract_features(_write(tmp_path / "plateau.wav", samples)).clipping
    assert clipping.hard_counts == (6, 0)
    assert clipping.near_counts == (6, 4)
    assert clipping.max_hard_runs == (5, 0)
    assert clipping.max_near_runs == (5, 4)
    assert clipping.max_same_sign_hard_runs == (5, 0)
    assert clipping.flat_top_counts == (5, 4)
    assert clipping.max_flat_top_runs == (5, 4)


def test_nyquist_alternating_rails_are_not_flat_plateaus(tmp_path: Path) -> None:
    samples = np.tile(np.array([1.0, -1.0]), 2000)
    clipping = extract_features(_write(tmp_path / "nyquist.wav", samples, 8000)).clipping
    assert clipping.max_hard_runs == (4000,)
    assert clipping.max_same_sign_hard_runs == (1,)
    assert clipping.max_flat_top_runs == (0,)
    assert clipping.flat_top_counts == (0,)


def test_clean_unit_peak_sinusoid_has_no_flat_top(tmp_path: Path) -> None:
    samples = np.sin(2 * np.pi * 100 * np.arange(16000) / 16000)
    clipping = extract_features(_write(tmp_path / "unit.wav", samples)).clipping
    assert clipping.hard_counts[0] > 0
    assert clipping.max_same_sign_hard_runs[0] > 1
    assert clipping.max_flat_top_runs == (0,)
    assert clipping.flat_top_counts == (0,)


def test_opposed_stereo_preserves_native_channel_power(tmp_path: Path) -> None:
    samples = 0.2 * np.sin(2 * np.pi * 1000 * np.arange(16000) / 16000)
    stereo = np.column_stack((samples, -samples))
    result = extract_features(_write(tmp_path / "opposed.wav", stereo))
    voiced = [window.voice_power for window in result.windows if window.duration_seconds >= 0.03]
    assert np.mean(voiced) == pytest.approx(0.02, rel=0.02)
    assert np.mean([window.power for window in result.windows]) == pytest.approx(0.02, rel=0.03)
    assert any(window.speech_candidate for window in result.windows)


@pytest.mark.parametrize("base_hz", [50, 60])
def test_hum_resolution_separates_mains_patterns_and_tracks_persistence(
    tmp_path: Path, base_hz: int
) -> None:
    sample_rate = 16000
    time = np.arange(sample_rate * 4) / sample_rate
    samples = np.asarray(
        sum(
            0.05 / harmonic * np.sin(2 * np.pi * base_hz * harmonic * time)
            for harmonic in range(1, 5)
        ),
        dtype=np.float64,
    )
    result = extract_features(_write(tmp_path / "hum.wav", samples))
    matched = next(candidate for candidate in result.hum if candidate.base_hz == base_hz)
    other = next(candidate for candidate in result.hum if candidate.base_hz != base_hz)
    assert matched.window_count == 4
    assert matched.harmonics[0].frequency_hz == base_hz
    assert matched.harmonics[0].power == pytest.approx(0.05**2 / 2, rel=0.01)
    assert matched.harmonics[0].contrast_db > 60
    assert matched.harmonics[0].persistence == 1
    assert other.harmonics[0].persistence == 0
    assert sum(line.power for line in matched.harmonics) <= matched.total_ac_power * 1.001


def test_hum_line_present_in_one_second_has_partial_persistence(tmp_path: Path) -> None:
    samples = np.zeros(64000)
    samples[:16000] = 0.1 * np.sin(2 * np.pi * 50 * np.arange(16000) / 16000)
    result = extract_features(_write(tmp_path / "brief_hum.wav", samples))
    candidate = next(candidate for candidate in result.hum if candidate.base_hz == 50)
    assert candidate.harmonics[0].persistence == 0.25


def test_dc_and_bass_voice_do_not_supply_a_hum_fundamental(tmp_path: Path) -> None:
    sample_rate = 16000
    time = np.arange(sample_rate * 3) / sample_rate
    samples = np.asarray(
        0.25
        + sum(
            0.05 / harmonic * np.sin(2 * np.pi * 100 * harmonic * time) for harmonic in range(1, 7)
        ),
        dtype=np.float64,
    )
    result = extract_features(_write(tmp_path / "bass_voice.wav", samples))
    candidate = next(candidate for candidate in result.hum if candidate.base_hz == 50)
    assert candidate.harmonics[0].persistence == 0
    assert candidate.harmonics[0].power < candidate.total_ac_power * 1e-10
    assert result.noise.window_count == 0


def test_constant_dc_does_not_become_low_frequency_or_hum_power(tmp_path: Path) -> None:
    result = extract_features(_write(tmp_path / "dc.wav", np.full(32000, 0.2)))
    assert all(window.low_power == 0 for window in result.windows)
    assert all(candidate.total_ac_power == 0 for candidate in result.hum)
    assert result.noise.window_count == 0


def test_low_activity_noise_psd_is_stable_and_not_contaminated_by_voice(tmp_path: Path) -> None:
    sample_rate = 16000
    time = np.arange(sample_rate * 5) / sample_rate
    rng = np.random.default_rng(7)
    background = rng.normal(0, 0.008, len(time))
    active = (time % 1) < 0.6
    voice = active * sum(
        0.08 / harmonic * np.sin(2 * np.pi * 130 * harmonic * time) for harmonic in range(1, 9)
    )
    result = extract_features(_write(tmp_path / "voice_noise.wav", background + voice))
    assert sum(window.speech_candidate for window in result.windows) > 50
    assert result.noise.window_count > 30
    assert result.noise.duration_seconds > 1
    assert result.noise.power == pytest.approx(0.008**2, rel=0.18)
    assert result.noise.relative_power_std < 0.2
    assert result.noise.psd_stationarity > 0.85
    assert result.noise.flatness > 0.45
    assert _all_finite(asdict(result))


def test_dc_does_not_inflate_estimated_background_level(tmp_path: Path) -> None:
    time = np.arange(80000) / 16000
    noise = np.random.default_rng(9).normal(0, 0.01, len(time))
    voice = (time % 1 < 0.6) * 0.1 * np.sin(2 * np.pi * 130 * time)
    plain = extract_features(_write(tmp_path / "plain.wav", noise + voice))
    offset = extract_features(_write(tmp_path / "offset.wav", noise + voice + 0.2))
    assert offset.noise.power == pytest.approx(plain.noise.power, rel=0.01)
    assert offset.noise.power < 0.001
    assert sum(window.low_activity for window in offset.windows) > 20


def test_extraction_is_independent_of_read_block_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    time = np.arange(17600) / 16000
    samples = 0.03 * np.sin(2 * np.pi * 50 * time)
    samples += np.random.default_rng(4).normal(0, 0.006, len(time))
    path = _write(tmp_path / "boundaries.wav", samples)
    normal = extract_features(path)
    monkeypatch.setattr(features, "BLOCK_FRAMES", 537)
    partitioned = extract_features(path)
    assert asdict(normal) == asdict(partitioned)


def test_continuous_white_noise_is_low_probability_voice_evidence(tmp_path: Path) -> None:
    noise = np.random.default_rng(5).normal(0, 0.01, 64000)
    result = extract_features(_write(tmp_path / "white.wav", noise))
    assert result.noise.duration_seconds > 3
    assert result.noise.psd_stationarity > 0.85
    assert sum(window.speech_candidate for window in result.windows) < len(result.windows) * 0.05


def test_odd_native_sample_rate_preserves_one_sided_hum_power(tmp_path: Path) -> None:
    sample_rate = 8001
    time = np.arange(sample_rate * 2) / sample_rate
    samples = 0.1 * np.sin(2 * np.pi * 60 * time)
    result = extract_features(_write(tmp_path / "odd.wav", samples, sample_rate))
    hum = next(candidate for candidate in result.hum if candidate.base_hz == 60)
    assert hum.total_ac_power == pytest.approx(0.1**2 / 2, rel=0.001)
    assert hum.harmonics[0].power == pytest.approx(hum.total_ac_power, rel=0.001)


def test_burst_features_retain_onset_and_high_band_power(tmp_path: Path) -> None:
    sample_rate = 24000
    time = np.arange(sample_rate * 2) / sample_rate
    voice = 0.03 * np.sin(2 * np.pi * 150 * time)
    voice[time < 0.5] = 0
    voice[(time > 0.8) & (time < 1.2)] = 0
    plosive = (time >= 0.48) & (time < 0.57)
    sibilance = (time >= 1.3) & (time < 1.42)
    samples = voice + plosive * 0.2 * np.sin(2 * np.pi * 45 * time)
    samples += sibilance * 0.07 * np.sin(2 * np.pi * 6000 * time)
    result = extract_features(_write(tmp_path / "bursts.wav", samples, sample_rate))
    assert any(window.low_power > 0.005 and window.onset_db > 8 for window in result.windows)
    assert any(
        window.sibilance_power > window.voice_power and window.speech_candidate
        for window in result.windows
    )


def test_nyquist_at_4khz_is_not_usable_sibilance_band(tmp_path: Path) -> None:
    samples = 0.1 * np.tile(np.array([1.0, -1.0]), 8000)
    result = extract_features(_write(tmp_path / "8k.wav", samples, 8000))
    assert result.sibilance_bandwidth_hz == 0
    assert all(window.sibilance_power == 0 for window in result.windows)


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf, 1.1])
def test_invalid_native_samples_are_rejected(tmp_path: Path, bad_value: float) -> None:
    samples = np.zeros(1000)
    samples[31] = bad_value
    with pytest.raises(InvalidAudioFile):
        extract_features(_write(tmp_path / "bad.wav", samples))


@pytest.mark.parametrize("sample_rate,channels", [(4000, 1), (192000, 1), (16000, 3)])
def test_unsupported_representations_are_rejected(
    tmp_path: Path, sample_rate: int, channels: int
) -> None:
    with pytest.raises(InvalidAudioFile):
        extract_features(
            _write(tmp_path / "unsupported.wav", np.zeros((100, channels)), sample_rate)
        )
