"""Signal-based diagnostic checks: positives, confounders and missing evidence.

These synthetic speech-like carriers validate reproducibility and safeguards, not
accuracy on human voices. Loudness is supplied as detector context; independent
FFmpeg/BS.1770 measurement tests live in test_analysis.py.
"""

import json
import math
import re
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import butter, sosfilt

from app.diagnostics import features
from app.diagnostics.detectors import HeadroomDetector, LowLevelDetector
from app.diagnostics.engine import DIAGNOSTICS_VERSION, diagnose_audio
from app.diagnostics.features import extract_features
from app.domain.analysis import AudioAnalysis, Dynamics, Spectrum
from app.domain.diagnostics import DIAGNOSTIC_CODES, Diagnostic
from app.domain.errors import InvalidAudioFile

RATE = 24000


def _tone(
    frequency: float, amplitude: float = 0.1, seconds: float = 4, rate: int = RATE
) -> NDArray[np.float64]:
    return amplitude * np.sin(2 * np.pi * frequency * np.arange(round(seconds * rate)) / rate)


def _speech(seconds: float = 4, rate: int = RATE, fundamental: float = 130) -> NDArray[np.float64]:
    time = np.arange(round(seconds * rate)) / rate
    position = time % 1.2
    envelope = np.zeros_like(time)
    active = (position >= 0.2) & (position < 0.9)
    envelope[active] = np.sin(np.pi * (position[active] - 0.2) / 0.7) ** 0.6
    carrier = sum(
        weight * np.sin(2 * np.pi * fundamental * harmonic * time)
        for harmonic, weight in ((1, 1), (2, 0.55), (3, 0.3), (5, 0.16), (8, 0.1), (15, 0.06))
    )
    return np.asarray(0.3 * envelope * carrier / 2.17)


def _context(samples: NDArray[np.float64], rate: int = RATE) -> AudioAnalysis:
    peak = float(np.max(np.abs(samples)))
    power = float(np.mean(samples * samples))
    peak_db = 20 * math.log10(peak) if peak else None
    rms_db = 10 * math.log10(power) if power else None
    channels = samples.shape[1] if samples.ndim == 2 else 1
    return AudioAnalysis(
        audio_id="synthetic",
        analyzer_version="0.3.0",
        sample_rate=rate,
        channels=channels,
        duration_seconds=len(samples) / rate,
        peak_dbfs=peak_db,
        rms_dbfs=rms_db,
        crest_factor_db=peak_db - rms_db if peak_db is not None and rms_db is not None else None,
        integrated_lufs=rms_db,
        true_peak_dbtp=peak_db,
        dc_offset=[0.0] * channels,
        zero_crossing_rate=0.0,
        silence_percent=100.0 if not power else 0.0,
        silence_threshold_dbfs=-60.0,
        bands=[],
        spectrum=Spectrum([], []),
        dynamics=Dynamics(100.0, []),
    )


def _write(path: Path, samples: NDArray[np.float64], rate: int = RATE) -> Path:
    sf.write(path, samples, rate, subtype="FLOAT")
    return path


def _diagnose(path: Path, samples: NDArray[np.float64], rate: int = RATE) -> dict[str, Diagnostic]:
    observations = diagnose_audio(_write(path, samples, rate), _context(samples, rate)).diagnostics
    assert [observation.code for observation in observations] == list(DIAGNOSTIC_CODES)
    for observation in observations:
        assert 0 <= observation.severity <= 1
        assert 0 <= observation.confidence <= 1
        assert observation.message and observation.evidence and observation.parameters
        assert observation.detected or observation.severity == 0
        json.dumps(asdict(observation), allow_nan=False)
    return {observation.code: observation for observation in observations}


def _number(diagnostic: Diagnostic, key: str) -> float:
    value = diagnostic.evidence[key]
    assert isinstance(value, int | float)
    return float(value)


def _numbers(diagnostic: Diagnostic, key: str) -> list[float]:
    values = diagnostic.evidence[key]
    assert isinstance(values, list)
    result = []
    for value in values:
        assert isinstance(value, int | float)
        result.append(float(value))
    return result


def test_engine_has_fixed_codes_and_reproducible_explanations(tmp_path: Path) -> None:
    samples = _speech()
    first = _diagnose(tmp_path / "voz.wav", samples)
    second = diagnose_audio(tmp_path / "voz.wav", _context(samples)).diagnostics
    assert list(first.values()) == second
    assert DIAGNOSTICS_VERSION == "0.6.0"
    assert all(not result.detected for result in first.values())


def test_studio_labels_every_published_evidence_and_parameter(tmp_path: Path) -> None:
    panel = Path(__file__).parents[2] / "frontend/src/features/audio/Measurements.tsx"
    if not panel.exists():
        pytest.skip("frontend sources are not available")
    labels = set(re.findall(r"^\s+([a-z0-9_]+): \{ label:", panel.read_text("utf-8"), re.M))
    published = {
        key
        for name, samples in (("voz.wav", _speech()), ("silencio.wav", np.zeros(RATE)))
        for observation in _diagnose(tmp_path / name, samples).values()
        for key in (*observation.evidence, *observation.parameters)
    }
    assert published <= labels, f"Missing studio labels: {sorted(published - labels)}"


@pytest.mark.parametrize("frequency", [100.0, 1000.0])
def test_full_scale_sinusoid_is_not_clipping(tmp_path: Path, frequency: float) -> None:
    result = _diagnose(tmp_path / "seno.wav", _tone(frequency, 1.0))
    assert not result["clipping"].detected
    assert result["low_headroom"].detected


def test_hard_clipping_is_detected_with_plateau_evidence(tmp_path: Path) -> None:
    samples = np.clip(_tone(997, 1.0) * 2, -1, 1)
    clipping = _diagnose(tmp_path / "clipping.wav", samples)["clipping"]
    assert clipping.detected and clipping.confidence >= 0.8
    assert _number(clipping, "flat_top_samples") > 0
    assert _number(clipping, "max_flat_top_run_samples") >= 3
    assert _number(clipping, "hard_sample_percent") > 20


def test_near_rail_flat_tops_are_detected_without_hard_rail_samples(tmp_path: Path) -> None:
    samples = np.clip(_tone(700, 1.0) * 2, -0.985, 0.985)
    clipping = _diagnose(tmp_path / "near.wav", samples)["clipping"]
    assert clipping.detected
    assert clipping.evidence["hard_sample_percent"] == 0
    assert _number(clipping, "near_sample_percent") > 20


def test_single_rail_sample_and_alternating_nyquist_are_not_flat_tops(tmp_path: Path) -> None:
    impulse = np.zeros(RATE)
    impulse[RATE // 2] = 1
    result = _diagnose(tmp_path / "impulse.wav", impulse)["clipping"]
    assert not result.detected
    # An alternating waveform has no same-polarity plateau, but its excessive
    # near-rail concentration is reported with lower heuristic confidence.
    alternating = np.tile(np.array([1.0, -1.0]), RATE)
    result = _diagnose(tmp_path / "alternating.wav", alternating)["clipping"]
    assert result.evidence["max_flat_top_run_samples"] == 0
    assert result.detected and result.confidence < 0.8


@pytest.mark.parametrize("base", [50, 60])
def test_hum_finds_dominant_mains_base_and_harmonic_persistence(tmp_path: Path, base: int) -> None:
    samples = _speech() + sum(_tone(base * harmonic, 0.02 / harmonic) for harmonic in range(1, 5))
    hum = _diagnose(tmp_path / f"hum{base}.wav", samples)["hum"]
    assert hum.detected
    assert hum.evidence["base_frequency_hz"] == base
    assert len(_numbers(hum, "harmonic_frequencies_hz")) >= 2
    assert min(_numbers(hum, "harmonic_persistence")) >= 0.6


@pytest.mark.parametrize("fundamental", [70.0, 100.0, 120.0])
def test_male_harmonic_voice_does_not_become_mains_hum(tmp_path: Path, fundamental: float) -> None:
    result = _diagnose(tmp_path / "grave.wav", _speech(fundamental=fundamental))
    assert not result["hum"].detected
    assert not result["rumble"].detected


def test_persistent_pure_mains_line_is_hum_with_reduced_confidence(tmp_path: Path) -> None:
    hum = _diagnose(tmp_path / "pure-hum.wav", _speech() + _tone(50, 0.02))["hum"]
    assert hum.detected and hum.evidence["base_frequency_hz"] == 50
    assert _numbers(hum, "harmonic_frequencies_hz") == [50.0]
    assert 0.5 <= hum.confidence < 0.8


def test_intermittent_pure_mains_line_is_not_hum(tmp_path: Path) -> None:
    line = _tone(50, 0.02)
    line[RATE : 3 * RATE] = 0
    assert not _diagnose(tmp_path / "gapped-hum.wav", _speech() + line)["hum"].detected


def test_brief_mains_tone_has_insufficient_hum_evidence(tmp_path: Path) -> None:
    result = _diagnose(tmp_path / "short-hum.wav", _tone(50, seconds=0.9))["hum"]
    assert not result.detected and result.confidence < 0.4


def test_rumble_requires_persistent_graves_in_low_activity_regions(tmp_path: Path) -> None:
    samples = _speech() + _tone(35, 0.07)
    rumble = _diagnose(tmp_path / "rumble.wav", samples)["rumble"]
    assert rumble.detected
    assert _number(rumble, "low_activity_low_band_percent") > 40


def test_low_level_uses_loudness_or_explicit_rms_fallback(tmp_path: Path) -> None:
    samples = _speech() * 0.05
    path = _write(tmp_path / "quiet.wav", samples)
    audio = extract_features(path)
    loudness = LowLevelDetector().analyze(audio, replace(_context(samples), integrated_lufs=-39.0))
    assert loudness.detected and loudness.confidence >= 0.8
    fallback = LowLevelDetector().analyze(audio, replace(_context(samples), integrated_lufs=None))
    assert fallback.detected and fallback.confidence < loudness.confidence
    assert "RMS" in fallback.message


def test_low_level_does_not_label_almost_silence_as_normal(tmp_path: Path) -> None:
    samples = _tone(1000, 0.00001)
    audio = extract_features(_write(tmp_path / "floor.wav", samples))
    result = LowLevelDetector().analyze(audio, replace(_context(samples), silence_percent=100.0))
    assert not result.detected and result.confidence < 0.4
    assert "silenciosa" in result.message


def test_headroom_uses_true_peak_context_instead_of_only_sample_peak(tmp_path: Path) -> None:
    samples = _tone(500, 0.3)
    audio = extract_features(_write(tmp_path / "headroom.wav", samples))
    result = HeadroomDetector().analyze(audio, replace(_context(samples), true_peak_dbtp=0.2))
    assert result.detected and result.evidence["headroom_db"] == -0.2
    assert result.parameters["true_peak_is_estimate"] is True


def test_headroom_inclusive_boundary_matches_published_message(tmp_path: Path) -> None:
    samples = _tone(500, 0.3)
    audio = extract_features(_write(tmp_path / "headroom-boundary.wav", samples))
    detector = HeadroomDetector()
    at_threshold = detector.analyze(audio, replace(_context(samples), true_peak_dbtp=-1.0))
    below_threshold = detector.analyze(audio, replace(_context(samples), true_peak_dbtp=-1.01))
    assert at_threshold.detected and "1 dB de margen o menos" in at_threshold.message
    assert not below_threshold.detected and "más de 1 dB" in below_threshold.message


def test_stationary_background_is_compared_between_quiet_regions(tmp_path: Path) -> None:
    noise = np.random.default_rng(47).normal(0.0, 0.012, RATE * 6)
    result = _diagnose(tmp_path / "noise.wav", _speech(6) + noise)["stationary_noise"]
    assert result.detected
    assert _number(result, "psd_similarity") > 0.8
    assert _number(result, "noise_windows") > 10
    assert _number(result, "estimated_snr_db") < 25


def test_pure_noise_is_not_claimed_as_speech_noise_comparison(tmp_path: Path) -> None:
    samples = np.random.default_rng(1).normal(0.0, 0.025, RATE * 4)
    result = _diagnose(tmp_path / "only-noise.wav", samples)["stationary_noise"]
    assert not result.detected and result.confidence < 0.4


def test_background_with_changing_power_is_not_stationary(tmp_path: Path) -> None:
    speech = _speech(6)
    raw = np.random.default_rng(24).normal(0.0, 1.0, len(speech))
    time = np.arange(len(speech)) / RATE
    envelope = np.where(time % 1.2 < 0.2, 0.0001, 0.03)
    result = _diagnose(tmp_path / "changing.wav", speech + raw * envelope)["stationary_noise"]
    assert not result.detected


def test_sibilance_bursts_are_localized_near_speech(tmp_path: Path) -> None:
    speech = _speech()
    raw = np.random.default_rng(93).normal(0.0, 1.0, len(speech))
    sos = butter(4, [5000, 8500], btype="bandpass", fs=RATE, output="sos")
    hiss = np.asarray(sosfilt(sos, raw), dtype=np.float64)
    samples = speech.copy()
    for start in (0.45, 1.65, 2.85):
        first, count = round(start * RATE), round(0.12 * RATE)
        samples[first : first + count] += 0.35 * hiss[first : first + count] * np.hanning(count)
    result = _diagnose(tmp_path / "esses.wav", samples)["sibilance"]
    assert result.detected and _number(result, "event_count") >= 2
    assert _numbers(result, "event_times_seconds")[0] == pytest.approx(0.45, abs=0.1)


def test_low_nyquist_does_not_claim_sibilance_absent(tmp_path: Path) -> None:
    result = _diagnose(tmp_path / "narrow.wav", _speech(rate=8000), 8000)["sibilance"]
    assert not result.detected and result.confidence < 0.4
    assert result.parameters["band_high_hz"] == 4000


def test_continuous_high_background_is_not_labelled_sibilance(tmp_path: Path) -> None:
    speech = _speech()
    raw = np.random.default_rng(19).normal(0.0, 1.0, len(speech))
    sos = butter(4, [5000, 8500], btype="bandpass", fs=RATE, output="sos")
    hiss = np.asarray(sosfilt(sos, raw), dtype=np.float64) * 0.2
    result = _diagnose(tmp_path / "steady-hiss.wav", speech + hiss)["sibilance"]
    assert not result.detected


def test_plosive_low_bursts_are_associated_with_voice_onsets(tmp_path: Path) -> None:
    samples = _speech()
    for start in (0.15, 1.35, 2.55):
        first, count = round(start * RATE), round(0.12 * RATE)
        samples[first : first + count] += _tone(38, 0.3, 0.12) * np.hanning(count)
    result = _diagnose(tmp_path / "pops.wav", samples)["plosives"]
    assert result.detected and _number(result, "event_count") >= 2
    assert _numbers(result, "event_times_seconds")[0] == pytest.approx(0.15, abs=0.1)


def test_continuous_low_noise_does_not_become_a_plosive(tmp_path: Path) -> None:
    result = _diagnose(tmp_path / "steady.wav", _speech() + _tone(35, 0.05))["plosives"]
    assert not result.detected


def test_bass_burst_without_voice_has_insufficient_plosive_evidence(tmp_path: Path) -> None:
    samples = np.zeros(RATE * 4)
    samples[RATE : RATE + round(0.12 * RATE)] = _tone(35.0, 0.3, 0.12) * np.hanning(
        round(0.12 * RATE)
    )
    result = _diagnose(tmp_path / "thump.wav", samples)["plosives"]
    assert not result.detected and result.confidence < 0.4


def test_localized_event_evidence_is_bounded(tmp_path: Path) -> None:
    samples = _speech(42)
    for start in np.arange(0.15, 42, 1.2):
        first, count = round(float(start) * RATE), round(0.12 * RATE)
        if first + count <= len(samples):
            samples[first : first + count] += _tone(38, 0.3, 0.12) * np.hanning(count)
    result = _diagnose(tmp_path / "many-pops.wav", samples)["plosives"]
    assert _number(result, "event_count") > 30
    assert len(_numbers(result, "event_times_seconds")) == 30


def test_silence_returns_eight_explicit_insufficient_observations(tmp_path: Path) -> None:
    result = _diagnose(tmp_path / "silence.wav", np.zeros((RATE, 2)))
    assert all(not item.detected and item.confidence < 0.4 for item in result.values())


def test_short_nonzero_recording_has_limited_diagnostic_confidence(tmp_path: Path) -> None:
    result = _diagnose(tmp_path / "short.wav", _tone(440, seconds=0.02))
    assert all(not item.detected and item.confidence < 0.4 for item in result.values())


def test_opposed_stereo_does_not_cancel_diagnostic_evidence(tmp_path: Path) -> None:
    samples = _speech() + sum(_tone(60 * h, 0.02 / h) for h in range(1, 5))
    mono = _diagnose(tmp_path / "mono.wav", samples)
    stereo = _diagnose(tmp_path / "stereo.wav", np.column_stack((samples, -samples)))
    for code in DIAGNOSTIC_CODES:
        assert stereo[code].detected == mono[code].detected
        assert stereo[code].severity == pytest.approx(mono[code].severity, abs=1e-7)
    assert stereo["hum"].detected


def test_read_boundaries_preserve_clipping_and_spectral_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = _speech() + _tone(50, 0.02) + _tone(100, 0.01)
    samples[996:1010] = 1.0
    path = _write(tmp_path / "boundaries.wav", samples)
    context = _context(samples)
    first = diagnose_audio(path, context)
    monkeypatch.setattr(features, "BLOCK_FRAMES", 997)
    second = diagnose_audio(path, context)
    assert first == second


def test_features_keep_only_scalar_windows_for_long_audio(tmp_path: Path) -> None:
    samples = _speech(seconds=61.0, rate=8000)
    result = extract_features(_write(tmp_path / "long.wav", samples, 8000))
    assert len(result.windows) == math.ceil(61 / 0.03)
    assert not any(isinstance(value, np.ndarray) for value in asdict(result.windows[0]).values())
    assert len(result.noise.psd) <= 2049


@pytest.mark.parametrize("bad", [math.nan, math.inf, 1.1])
def test_features_reject_untrusted_decoded_samples(tmp_path: Path, bad: float) -> None:
    with pytest.raises(InvalidAudioFile):
        extract_features(_write(tmp_path / "bad.wav", np.array([0.0, bad])))
