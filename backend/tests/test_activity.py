"""Speech activity, noise profile and SNR estimate: frame rules and synthetic signals.

Frame-level tests pin down the hysteresis and smoothing rules exactly. Signal tests
use harmonic, syllable-gated carriers with known Gaussian backgrounds; they validate
reproducibility and safeguards, not accuracy on human voices.
"""

import math
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray

from app.analysis.frames import WINDOW_SECONDS, WindowFeatures
from app.analysis.noise import estimate_snr_db
from app.analysis.vad import (
    ActivityInput,
    EnergyVoiceActivityDetector,
    activity_from_mask,
    create_vad,
    frame_labels,
)
from app.diagnostics import features
from app.diagnostics.engine import diagnose_audio
from app.diagnostics.features import extract_features
from app.domain.activity import ActivitySegment, NoiseProfile, SpeechActivity
from app.domain.analysis import AudioAnalysis, Dynamics, Spectrum

RATE = 16000


def _frame(index: int, level_db: float, voiced: bool = True) -> WindowFeatures:
    power = 10 ** (level_db / 10) if level_db > -200 else 0.0
    return WindowFeatures(
        start_seconds=index * WINDOW_SECONDS,
        duration_seconds=WINDOW_SECONDS,
        power=power,
        low_power=0.0,
        voice_power=power if voiced else power * 0.05,
        sibilance_power=0.0 if voiced else power * 0.95,
        flatness=0.1 if voiced else 0.8,
        zero_crossing_rate=0.05 if voiced else 0.6,
        onset_db=0.0,
    )


def _detect(frames: list[WindowFeatures]) -> SpeechActivity:
    duration = len(frames) * WINDOW_SECONDS
    return EnergyVoiceActivityDetector().detect(
        ActivityInput(Path("unused.wav"), RATE, 1, duration, tuple(frames))
    )


def _speech_mask(activity: SpeechActivity, frames: list[WindowFeatures]) -> list[bool]:
    return [label == "speech" for label in frame_labels(activity, frames)]


def _timeline(levels: list[float], voiced: list[bool] | None = None) -> list[WindowFeatures]:
    flags = voiced if voiced is not None else [True] * len(levels)
    return [
        _frame(index, level, flag)
        for index, (level, flag) in enumerate(zip(levels, flags, strict=True))
    ]


def test_hysteresis_keeps_quieter_unvoiced_tail_after_voiced_onset() -> None:
    levels = [-70.0] * 20 + [-20.0] * 10 + [-30.0] * 10 + [-70.0] * 30
    voiced = [True] * 30 + [False] * 10 + [True] * 30
    frames = _timeline(levels, voiced)
    activity = _detect(frames)
    mask = _speech_mask(activity, frames)
    # Entry needs a voiced frame; the -30 dB unvoiced tail stays above the stay level.
    assert all(mask[20:40])
    assert activity.parameters["enter_threshold_dbfs"] == pytest.approx(-34.0)
    assert activity.parameters["stay_threshold_dbfs"] == pytest.approx(-40.0)


def test_loud_unvoiced_frames_cannot_start_speech() -> None:
    levels = [-70.0] * 20 + [-20.0] * 10 + [-70.0] * 30
    voiced = [True] * 20 + [False] * 10 + [True] * 30
    frames = _timeline(levels, voiced)
    assert not any(_speech_mask(_detect(frames), frames))


def test_smoothing_bridges_pauses_drops_bursts_and_adds_hangover() -> None:
    levels = [-70.0] * 100
    for start, end in ((10, 20), (25, 35), (50, 60), (80, 82)):
        levels[start:end] = [-20.0] * (end - start)
    frames = _timeline(levels)
    mask = _speech_mask(_detect(frames), frames)
    speech = [index for index, active in enumerate(mask) if active]
    # 0.15 s pause bridged, 0.45 s pause kept, 0.06 s burst removed.
    # Hangover adds 2 frames before and 4 frames after each run.
    assert speech == list(range(8, 39)) + list(range(48, 64))


def test_recording_without_level_contrast_has_no_speech() -> None:
    frames = _timeline([-25.0] * 100)
    activity = _detect(frames)
    assert activity.speech_seconds == 0
    assert [segment.label for segment in activity.segments] == ["noise"]


def test_digital_silence_is_its_own_label_and_thresholds_are_undefined() -> None:
    frames = _timeline([-300.0] * 40)
    activity = _detect(frames)
    assert [segment.label for segment in activity.segments] == ["silence"]
    assert activity.segments[0].end_seconds == pytest.approx(1.2)
    assert activity.parameters["enter_threshold_dbfs"] is None
    assert activity.speech_rms_dbfs is None


def test_timeline_is_contiguous_merged_and_accounts_for_all_time() -> None:
    levels = [-300.0] * 10 + [-70.0] * 20 + [-20.0] * 30 + [-70.0] * 25 + [-300.0] * 15
    frames = _timeline(levels)
    activity = _detect(frames)
    assert [segment.label for segment in activity.segments] == [
        "silence",
        "noise",
        "speech",
        "noise",
        "silence",
    ]
    assert activity.segments[0].start_seconds == 0
    assert activity.segments[-1].end_seconds == pytest.approx(3.0)
    total = activity.speech_seconds + activity.noise_seconds + activity.silence_seconds
    assert total == pytest.approx(3.0)
    assert activity.speech_percent == pytest.approx(100 * activity.speech_seconds / 3.0)
    assert activity.speech_rms_dbfs is not None and activity.speech_rms_dbfs < -20


def test_domain_rejects_gaps_and_unmerged_segments() -> None:
    frames = _timeline([-70.0] * 20 + [-20.0] * 20)
    activity = _detect(frames)
    with pytest.raises(ValueError):
        replace(activity, segments=[ActivitySegment("noise", 0, 1), ActivitySegment("noise", 1, 2)])
    with pytest.raises(ValueError):
        replace(
            activity, segments=[ActivitySegment("noise", 0, 1), ActivitySegment("speech", 1.5, 2)]
        )
    with pytest.raises(ValueError):
        ActivitySegment("music", 0, 1)
    with pytest.raises(ValueError):
        NoiseProfile(1, 0.03, None, None, None, None, None, [], [])


def test_unknown_backend_is_rejected() -> None:
    assert isinstance(create_vad("energy"), EnergyVoiceActivityDetector)
    with pytest.raises(ValueError):
        create_vad("silero")


def _speech_carrier(seconds: float, amplitude: float, rate: int = RATE) -> NDArray[np.float64]:
    time = np.arange(round(seconds * rate)) / rate
    gate = (time % 1.0) < 0.6
    carrier = sum(np.sin(2 * np.pi * 140 * h * time) / h for h in range(1, 9))
    return np.asarray(amplitude * gate * carrier / 2.0)


def _noise(seconds: float, sigma: float, seed: int, rate: int = RATE) -> NDArray[np.float64]:
    return np.random.default_rng(seed).normal(0.0, sigma, round(seconds * rate))


def _write(path: Path, samples: NDArray[np.float64], rate: int = RATE) -> Path:
    sf.write(path, samples, rate, subtype="FLOAT")
    return path


def test_noise_profile_recovers_known_background_level_and_spectrum(tmp_path: Path) -> None:
    sigma = 0.005
    samples = _speech_carrier(8, 0.2) + _noise(8, sigma, 11)
    result = extract_features(_write(tmp_path / "voice-noise.wav", samples))
    profile = diagnose_audio(tmp_path / "voice-noise.wav", _context(samples)).noise_profile
    assert 35 <= result.activity.speech_percent <= 80
    assert profile.duration_seconds > 1.5
    assert profile.rms_dbfs == pytest.approx(20 * math.log10(sigma), abs=0.5)
    assert profile.floor_dbfs is not None and profile.rms_dbfs is not None
    assert profile.floor_dbfs <= profile.rms_dbfs
    assert profile.spectral_stability is not None and profile.spectral_stability > 0.85
    assert profile.spectral_flatness is not None and profile.spectral_flatness > 0.45
    expected_density = 10 * math.log10(2 * sigma**2 / RATE)
    band = [
        value
        for frequency, value in zip(profile.frequencies_hz, profile.psd_dbfs_per_hz, strict=True)
        if 200 <= frequency <= 7000 and value is not None
    ]
    assert float(np.mean(band)) == pytest.approx(expected_density, abs=1.0)


def test_speech_tails_and_loud_pause_events_do_not_contaminate_profile(tmp_path: Path) -> None:
    samples = _speech_carrier(8, 0.2) + _noise(8, 0.004, 3)
    clean = diagnose_audio(_write(tmp_path / "clean.wav", samples), _context(samples))
    knocked = samples.copy()
    for second in (1, 3, 5):
        start = round((second + 0.75) * RATE)
        knocked[start : start + 800] += _noise(0.05, 0.15, second)
    events = diagnose_audio(_write(tmp_path / "knock.wav", knocked), _context(knocked))
    assert clean.noise_profile.rms_dbfs is not None
    assert events.noise_profile.rms_dbfs == pytest.approx(clean.noise_profile.rms_dbfs, abs=0.5)


@pytest.mark.parametrize("sigma", [0.02, 0.006, 0.002])
def test_snr_estimate_tracks_known_speech_to_noise_ratio(tmp_path: Path, sigma: float) -> None:
    speech = _speech_carrier(10, 0.2)
    samples = speech + _noise(10, sigma, 5)
    diagnosis = diagnose_audio(_write(tmp_path / "snr.wav", samples), _context(samples))
    active = speech[np.abs(speech) > 0]
    expected = 10 * math.log10(float(np.mean(active**2)) / sigma**2)
    assert diagnosis.estimated_snr_db is not None
    # Hangover frames contain background only, which biases the estimate slightly low.
    assert expected - 3 <= diagnosis.estimated_snr_db <= expected + 1


def test_snr_is_undefined_without_both_speech_and_background() -> None:
    assert estimate_snr_db(1e-2, 5.0, 1e-4, 0.2) is None
    assert estimate_snr_db(1e-2, 0.2, 1e-4, 5.0) is None
    assert estimate_snr_db(1e-4, 5.0, 1e-4, 5.0) is None
    assert estimate_snr_db(1e-2, 5.0, 1e-4, 5.0) == pytest.approx(10 * math.log10(99))


def test_segmentation_and_profile_are_reproducible_across_read_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = _speech_carrier(4, 0.2) + _noise(4, 0.004, 21)
    path = _write(tmp_path / "blocks.wav", samples)
    first = diagnose_audio(path, _context(samples))
    assert diagnose_audio(path, _context(samples)) == first
    monkeypatch.setattr(features, "BLOCK_FRAMES", 1013)
    assert asdict(diagnose_audio(path, _context(samples))) == asdict(first)


def test_opposed_stereo_matches_mono_segmentation(tmp_path: Path) -> None:
    samples = _speech_carrier(4, 0.2) + _noise(4, 0.004, 8)
    mono = diagnose_audio(_write(tmp_path / "mono.wav", samples), _context(samples))
    stereo_samples = np.column_stack((samples, -samples))
    stereo = diagnose_audio(_write(tmp_path / "stereo.wav", stereo_samples), _context(samples))
    assert stereo.speech_activity.segments == mono.speech_activity.segments
    assert stereo.noise_profile.rms_dbfs == pytest.approx(mono.noise_profile.rms_dbfs, abs=1e-6)


class _AllNoiseDetector:
    """A stand-in backend proving diagnostics consume whichever VAD is injected."""

    name = "test-all-noise"
    version = "test"

    def detect(self, audio: ActivityInput) -> SpeechActivity:
        return activity_from_mask(
            self.name, self.version, audio.frames, [False] * len(audio.frames), {}
        )


def test_diagnostics_and_profile_follow_an_injected_backend(tmp_path: Path) -> None:
    samples = _speech_carrier(6, 0.2) + _noise(6, 0.012, 2)
    path = _write(tmp_path / "swap.wav", samples)
    default = diagnose_audio(path, _context(samples))
    swapped = diagnose_audio(path, _context(samples), _AllNoiseDetector())
    stationary = {item.code: item for item in swapped.diagnostics}["stationary_noise"]
    assert default.speech_activity.detector == "energy" and default.speech_activity.speech_seconds
    assert swapped.speech_activity.detector == "test-all-noise"
    assert [segment.label for segment in swapped.speech_activity.segments] == ["noise"]
    assert swapped.estimated_snr_db is None
    assert not stationary.detected and stationary.confidence < 0.4


def _context(samples: NDArray[np.float64]) -> AudioAnalysis:
    power = float(np.mean(samples * samples))
    peak = float(np.max(np.abs(samples)))
    return AudioAnalysis(
        audio_id="synthetic",
        analyzer_version="0.3.0",
        sample_rate=RATE,
        channels=1,
        duration_seconds=len(samples) / RATE,
        peak_dbfs=20 * math.log10(peak),
        rms_dbfs=10 * math.log10(power),
        crest_factor_db=20 * math.log10(peak) - 10 * math.log10(power),
        integrated_lufs=10 * math.log10(power),
        true_peak_dbtp=20 * math.log10(peak),
        dc_offset=[0.0],
        zero_crossing_rate=0.0,
        silence_percent=0.0,
        silence_threshold_dbfs=-60.0,
        bands=[],
        spectrum=Spectrum([], []),
        dynamics=Dynamics(100.0, []),
    )
