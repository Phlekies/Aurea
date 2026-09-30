"""Isolated temporary storage and deterministic audio fixtures for ingestion tests."""

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def wav_bytes() -> bytes:
    """A short 44.1 kHz mono tone; no licensed or private audio is needed."""
    buffer = io.BytesIO()
    samples = 0.25 * np.sin(2 * np.pi * 440 * np.arange(4410) / 44100)
    sf.write(buffer, samples, 44100, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    return tmp_path / "assets"
