"""Versioned orchestration of speech activity, noise profile and eight diagnostics."""

from dataclasses import dataclass
from pathlib import Path

from app.analysis.noise import noise_profile
from app.analysis.vad import VoiceActivityDetector
from app.diagnostics.detectors import (
    ClippingDetector,
    Detector,
    HeadroomDetector,
    HumDetector,
    LowLevelDetector,
    PlosiveDetector,
    RumbleDetector,
    SibilanceDetector,
    StationaryNoiseDetector,
)
from app.diagnostics.features import extract_features
from app.domain.activity import NoiseProfile, SpeechActivity
from app.domain.analysis import AudioAnalysis
from app.domain.diagnostics import Diagnostic

DIAGNOSTICS_VERSION = "0.5.0"
DETECTORS: tuple[Detector, ...] = (
    ClippingDetector(),
    HumDetector(),
    RumbleDetector(),
    LowLevelDetector(),
    HeadroomDetector(),
    StationaryNoiseDetector(),
    SibilanceDetector(),
    PlosiveDetector(),
)


@dataclass(frozen=True)
class Diagnosis:
    """Segmentation, background estimate and observations sharing one frame labelling."""

    speech_activity: SpeechActivity
    noise_profile: NoiseProfile
    estimated_snr_db: float | None
    diagnostics: list[Diagnostic]


def diagnose_audio(
    path: Path, analysis: AudioAnalysis, detector: VoiceActivityDetector | None = None
) -> Diagnosis:
    """Extract bounded native-channel evidence and return a fixed diagnostic code order."""
    features = extract_features(path, detector)
    return Diagnosis(
        speech_activity=features.activity,
        noise_profile=noise_profile(features.noise),
        estimated_snr_db=features.estimated_snr_db,
        diagnostics=[item.analyze(features, analysis) for item in DETECTORS],
    )
