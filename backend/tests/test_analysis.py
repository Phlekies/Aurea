"""Numerical contracts from analytic signals and independent FFmpeg meter references."""

import json
import math
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray

from app.analysis import analyzer
from app.analysis.analyzer import ANALYZER_VERSION, analyze_audio
from app.domain.analysis import AudioAnalysis
from app.domain.errors import AudioServiceUnavailable, InvalidAudioFile


def _write(path: Path, samples: NDArray[np.float64], sample_rate: int = 48000) -> Path:
    sf.write(path, samples, sample_rate, subtype="FLOAT")
    return path


def _tone(
    frequency: float = 1000.0,
    amplitude: float = 0.5,
    duration: float = 2.0,
    sample_rate: int = 48000,
) -> NDArray[np.float64]:
    return amplitude * np.sin(
        2 * np.pi * frequency * np.arange(round(duration * sample_rate)) / sample_rate
    )


def _assert_finite_json(analysis: AudioAnalysis) -> None:
    # This covers every nested scalar including silence, short clips and zero PSD bins.
    json.dumps(asdict(analysis), allow_nan=False)


def _reference_meter(path: Path, filter_name: str) -> str:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-i",
            str(path),
            "-af",
            filter_name,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stderr


def test_sine_has_analytic_peak_rms_crest_crossings_and_band(tmp_path: Path) -> None:
    path = _write(tmp_path / "grabación de prueba.wav", _tone())
    result = analyze_audio(path, "sine")
    assert result.audio_id == "sine"
    assert result.analyzer_version == ANALYZER_VERSION
    assert result.sample_rate == 48000
    assert result.channels == 1
    assert result.duration_seconds == 2
    assert result.peak_dbfs == pytest.approx(20 * math.log10(0.5), abs=1e-6)
    assert result.rms_dbfs == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)), abs=1e-6)
    assert result.crest_factor_db == pytest.approx(10 * math.log10(2), abs=1e-6)
    assert result.dc_offset == pytest.approx([0.0], abs=1e-8)
    assert result.zero_crossing_rate == pytest.approx(2000 / 48000, abs=2 / 96000)
    assert result.silence_percent == 0
    band = next(item for item in result.bands if item.name == "Medios")
    assert band.percent > 99.99
    assert sum(item.percent for item in result.bands) == pytest.approx(100)
    assert sum(item.power for item in result.bands) == pytest.approx(0.125, abs=1e-5)
    density = result.spectrum.psd_dbfs_per_hz
    dominant = max(range(len(density)), key=lambda i: density[i] or -math.inf)
    assert result.spectrum.frequencies_hz[dominant] == pytest.approx(1000, abs=48000 / 2048)
    assert len(density) == 1025
    assert len(result.dynamics.points) == 20
    assert result.dynamics.window_ms == 100
    for point in result.dynamics.points:
        assert point.peak_dbfs == pytest.approx(result.peak_dbfs, abs=1e-6)
        assert point.rms_dbfs == pytest.approx(result.rms_dbfs, abs=1e-6)
    _assert_finite_json(result)


def test_gain_changes_levels_without_changing_shape_or_crest(tmp_path: Path) -> None:
    full = analyze_audio(_write(tmp_path / "full.wav", _tone()), "full")
    half = analyze_audio(_write(tmp_path / "half.wav", _tone(amplitude=0.25)), "half")
    gain_db = 20 * math.log10(0.5)
    assert full.peak_dbfs is not None and half.peak_dbfs is not None
    assert full.rms_dbfs is not None and half.rms_dbfs is not None
    assert full.integrated_lufs is not None and half.integrated_lufs is not None
    assert full.true_peak_dbtp is not None and half.true_peak_dbtp is not None
    assert half.peak_dbfs - full.peak_dbfs == pytest.approx(gain_db)
    assert half.rms_dbfs - full.rms_dbfs == pytest.approx(gain_db)
    assert half.true_peak_dbtp - full.true_peak_dbtp == pytest.approx(gain_db, abs=1e-5)
    assert half.integrated_lufs - full.integrated_lufs == pytest.approx(gain_db, abs=0.11)
    assert half.crest_factor_db == pytest.approx(full.crest_factor_db)
    assert [band.percent for band in half.bands] == pytest.approx(
        [band.percent for band in full.bands]
    )


def test_opposed_stereo_retains_energy_and_stereo_loudness(tmp_path: Path) -> None:
    samples = _tone()
    mono = analyze_audio(_write(tmp_path / "mono.wav", samples), "mono")
    stereo = analyze_audio(
        _write(tmp_path / "stereo.wav", np.column_stack((samples, -samples))), "stereo"
    )
    assert stereo.channels == 2
    assert stereo.peak_dbfs == pytest.approx(mono.peak_dbfs)
    assert stereo.rms_dbfs == pytest.approx(mono.rms_dbfs)
    assert stereo.true_peak_dbtp == pytest.approx(mono.true_peak_dbtp)
    assert stereo.dc_offset == pytest.approx([0, 0], abs=1e-8)
    assert stereo.zero_crossing_rate == pytest.approx(mono.zero_crossing_rate, abs=1 / 96000)
    assert [band.power for band in stereo.bands] == pytest.approx([b.power for b in mono.bands])
    assert mono.integrated_lufs is not None and stereo.integrated_lufs is not None
    assert stereo.integrated_lufs - mono.integrated_lufs == pytest.approx(
        10 * math.log10(2), abs=0.11
    )


def test_silent_channel_halves_rms_power_without_reducing_sample_peak(tmp_path: Path) -> None:
    samples = _tone()
    mono = analyze_audio(_write(tmp_path / "mono.wav", samples), "mono")
    stereo = analyze_audio(
        _write(tmp_path / "stereo.wav", np.column_stack((samples, np.zeros_like(samples)))),
        "stereo",
    )
    assert stereo.peak_dbfs == mono.peak_dbfs
    assert mono.rms_dbfs is not None and stereo.rms_dbfs is not None
    assert stereo.rms_dbfs - mono.rms_dbfs == pytest.approx(-10 * math.log10(2))
    assert sum(band.power for band in stereo.bands) == pytest.approx(
        0.5 * sum(band.power for band in mono.bands)
    )
    assert stereo.integrated_lufs == mono.integrated_lufs


def test_silence_has_null_decibels_zero_energy_and_finite_json(tmp_path: Path) -> None:
    result = analyze_audio(_write(tmp_path / "silent.wav", np.zeros((48000, 2))), "silence")
    assert result.peak_dbfs is None
    assert result.rms_dbfs is None
    assert result.crest_factor_db is None
    assert result.integrated_lufs is None
    assert result.true_peak_dbtp is None
    assert result.dc_offset == [0, 0]
    assert result.zero_crossing_rate == 0
    assert result.silence_percent == 100
    assert all(band.power == band.percent == 0 for band in result.bands)
    assert all(value is None for value in result.spectrum.psd_dbfs_per_hz)
    assert all(point.peak_dbfs is point.rms_dbfs is None for point in result.dynamics.points)
    _assert_finite_json(result)


def test_dc_is_measured_without_detrending_spectrum(tmp_path: Path) -> None:
    result = analyze_audio(_write(tmp_path / "dc.wav", np.full((96000, 2), [0.125, -0.125])), "dc")
    assert result.dc_offset == [0.125, -0.125]
    assert result.peak_dbfs == result.rms_dbfs == pytest.approx(20 * math.log10(0.125))
    assert result.crest_factor_db == 0
    assert result.zero_crossing_rate == 0
    assert result.silence_percent == 0
    assert result.bands[0].percent == pytest.approx(100)
    assert sum(band.power for band in result.bands) == pytest.approx(0.125**2)
    _assert_finite_json(result)


def test_silence_uses_duration_weights_for_final_partial_window(tmp_path: Path) -> None:
    samples = np.concatenate((np.zeros(320), np.full(80, 0.1)))
    result = analyze_audio(_write(tmp_path / "partial.wav", samples, 8000), "partial")
    assert result.silence_percent == 80
    assert result.silence_threshold_dbfs == -60
    assert result.integrated_lufs is None
    assert result.dynamics.points[0].duration_seconds == 0.05
    assert result.rms_dbfs == pytest.approx(20 * math.log10(0.1 * math.sqrt(0.2)), abs=1e-6)
    _assert_finite_json(result)


def test_silence_threshold_uses_both_channel_powers(tmp_path: Path) -> None:
    # One audible channel at -58 dBFS must not be silenced by a -61 dBFS stereo average.
    samples = np.column_stack((np.zeros(4800), np.full(4800, 10 ** (-58 / 20))))
    result = analyze_audio(_write(tmp_path / "threshold.wav", samples), "threshold")
    assert result.silence_percent == 0


@pytest.mark.parametrize("samples", [np.array([0.25]), np.array([0.25, -0.25]), np.zeros(3)])
def test_very_short_clips_have_no_invented_loudness(
    tmp_path: Path, samples: NDArray[np.float64]
) -> None:
    result = analyze_audio(_write(tmp_path / "short.wav", samples), "short")
    assert result.duration_seconds == len(samples) / 48000
    assert result.integrated_lufs is None
    assert result.dc_offset == pytest.approx([float(np.mean(samples))])
    if np.any(samples):
        assert result.peak_dbfs == result.rms_dbfs == pytest.approx(20 * math.log10(0.25))
        assert sum(band.power for band in result.bands) == pytest.approx(float(np.mean(samples**2)))
    _assert_finite_json(result)


def test_below_gate_loudness_is_null_while_sample_levels_remain_finite(tmp_path: Path) -> None:
    result = analyze_audio(_write(tmp_path / "quiet.wav", _tone(amplitude=1e-5)), "quiet")
    assert result.integrated_lufs is None
    assert result.peak_dbfs == pytest.approx(-100, abs=1e-5)
    assert result.rms_dbfs is not None
    assert result.true_peak_dbtp is not None
    assert result.silence_percent == 100
    _assert_finite_json(result)


@pytest.mark.parametrize("sample_rate", [8000, 44100, 96000])
def test_bands_cover_each_native_nyquist_once(tmp_path: Path, sample_rate: int) -> None:
    samples = _tone(1000, sample_rate=sample_rate, duration=0.1)
    result = analyze_audio(_write(tmp_path / "rate.wav", samples, sample_rate), "rate")
    assert result.bands[0].low_hz == 0
    assert result.bands[-1].high_hz == sample_rate / 2
    assert all(
        left.high_hz == right.low_hz
        for left, right in zip(result.bands, result.bands[1:], strict=False)
    )
    assert all(band.low_hz < band.high_hz for band in result.bands)
    assert sum(band.percent for band in result.bands) == pytest.approx(100)
    assert result.spectrum.frequencies_hz[-1] == sample_rate / 2
    _assert_finite_json(result)


def test_loudness_matches_independent_loudnorm_and_known_1khz_level(tmp_path: Path) -> None:
    path = _write(tmp_path / "r128.wav", _tone(duration=3))
    result = analyze_audio(path, "r128")
    reference = _reference_meter(path, "loudnorm=I=-23:LRA=7:TP=-1:print_format=json")
    metadata, _ = json.JSONDecoder().raw_decode(reference[reference.rfind("{") :])
    assert result.integrated_lufs == pytest.approx(float(metadata["input_i"]), abs=0.11)
    # 0.5-peak, 1 kHz mono: -9.0309 dBFS RMS and approximately -9.1 LUFS after K weighting.
    assert result.integrated_lufs == pytest.approx(-9.1, abs=0.15)


@pytest.mark.parametrize("sample_rate", [48000, 96000])
def test_true_peak_detects_intersample_sine_and_matches_ebur128(
    tmp_path: Path, sample_rate: int
) -> None:
    count = sample_rate
    samples = 0.7 * np.sin(2 * np.pi * 0.25 * np.arange(count) + np.pi / 4)
    ramp = round(sample_rate * 0.02)
    envelope = np.ones(count)
    envelope[:ramp] = np.linspace(0, 1, ramp)
    envelope[-ramp:] = np.linspace(1, 0, ramp)
    path = _write(tmp_path / "intersample.wav", samples * envelope, sample_rate)
    result = analyze_audio(path, "intersample")
    assert result.peak_dbfs == pytest.approx(20 * math.log10(0.7 / math.sqrt(2)), abs=1e-6)
    assert result.true_peak_dbtp == pytest.approx(20 * math.log10(0.7), abs=0.03)
    assert result.true_peak_dbtp is not None and result.peak_dbfs is not None
    assert result.true_peak_dbtp - result.peak_dbfs > 2.9
    reference = _reference_meter(path, "ebur128=peak=true:framelog=verbose")
    measured = re.findall(r"True peak:\s+Peak:\s*([-+]?\d+(?:\.\d+)?) dBFS", reference)
    assert result.true_peak_dbtp == pytest.approx(float(measured[-1]), abs=0.12)


def test_repeat_analysis_is_deterministic_and_read_boundaries_do_not_change_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = _tone(997, duration=3.027)
    path = _write(tmp_path / "boundaries.wav", samples)
    first = analyze_audio(path, "same")
    assert analyze_audio(path, "same") == first
    monkeypatch.setattr(analyzer, "BLOCK_FRAMES", 997)
    split = analyze_audio(path, "same")
    assert split.peak_dbfs == first.peak_dbfs
    assert split.rms_dbfs == pytest.approx(first.rms_dbfs)
    assert split.zero_crossing_rate == first.zero_crossing_rate
    assert split.silence_percent == first.silence_percent
    assert split.dc_offset == pytest.approx(first.dc_offset)
    assert [band.power for band in split.bands] == pytest.approx([b.power for b in first.bands])
    assert split.spectrum.psd_dbfs_per_hz == pytest.approx(first.spectrum.psd_dbfs_per_hz, abs=1e-9)
    for expected, actual in zip(first.dynamics.points, split.dynamics.points, strict=True):
        assert actual.start_seconds == expected.start_seconds
        assert actual.duration_seconds == expected.duration_seconds
        assert actual.peak_dbfs == expected.peak_dbfs
        assert actual.rms_dbfs == pytest.approx(expected.rms_dbfs)


def test_dynamics_is_bounded_and_keeps_final_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate the streaming numerical pass; separate meter reference tests cover FFmpeg.
    monkeypatch.setattr(analyzer, "_loudness_and_true_peak", lambda *args: (None, -1.9382))
    sample_rate = 8000
    count = round(203.123 * sample_rate)
    samples = np.zeros(count)
    samples[-1] = 0.8
    result = analyze_audio(_write(tmp_path / "long.wav", samples, sample_rate), "long")
    assert len(result.dynamics.points) <= 2000
    assert result.dynamics.window_ms > 100
    assert result.dynamics.points[-1].peak_dbfs == pytest.approx(20 * math.log10(0.8), abs=1e-6)
    assert sum(point.duration_seconds for point in result.dynamics.points) == pytest.approx(
        result.duration_seconds
    )
    last = result.dynamics.points[-1]
    assert last.start_seconds + last.duration_seconds == pytest.approx(result.duration_seconds)
    assert result.rms_dbfs == pytest.approx(10 * math.log10(0.8**2 / count), abs=1e-6)
    _assert_finite_json(result)


@pytest.mark.parametrize("bad", [math.nan, math.inf, 1.01])
def test_nonfinite_or_out_of_range_decoded_samples_are_rejected(tmp_path: Path, bad: float) -> None:
    path = _write(tmp_path / "invalid.wav", np.array([bad, 0.0]))
    with pytest.raises(InvalidAudioFile):
        analyze_audio(path, "invalid")


def test_empty_missing_and_incompatible_channels_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidAudioFile):
        analyze_audio(tmp_path / "missing.wav", "missing")
    with pytest.raises(InvalidAudioFile):
        analyze_audio(_write(tmp_path / "empty.wav", np.zeros(0)), "empty")
    with pytest.raises(InvalidAudioFile):
        analyze_audio(_write(tmp_path / "surround.wav", np.zeros((4800, 3))), "surround")


def test_missing_ffmpeg_has_safe_recoverable_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "input.wav", _tone(duration=0.1))
    with pytest.raises(AudioServiceUnavailable, match="no está disponible"):
        analyze_audio(path, "input", ffmpeg=str(tmp_path / "missing-decoder"))


def test_meter_timeout_has_safe_recoverable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "input.wav", _tone(duration=0.1))

    def timed_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(AudioServiceUnavailable, match="agotado su tiempo"):
        analyze_audio(path, "input", timeout_seconds=1)
