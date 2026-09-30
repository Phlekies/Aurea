"""Versioned orchestration of the eight phase-3 explainable diagnostics."""

from pathlib import Path

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
from app.domain.analysis import AudioAnalysis
from app.domain.diagnostics import Diagnostic

DIAGNOSTICS_VERSION = "0.4.0"
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


def diagnose_audio(path: Path, analysis: AudioAnalysis) -> list[Diagnostic]:
    """Extract bounded native-channel evidence and return a fixed diagnostic code order."""
    features = extract_features(path)
    return [detector.analyze(features, analysis) for detector in DETECTORS]
