"""End-to-end selection, profile validation, rendering and versioned cache."""

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.processors.noise_reduction import ALGORITHMS, STRENGTHS


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("strength", STRENGTHS)
@pytest.mark.parametrize("rate", [16000, 44100])
def test_noise_selection_rendering_and_safe_cache(
    storage: Path, algorithm: str, strength: str, rate: int
) -> None:
    time = np.arange(rate * 4) / rate
    clean = ((time % 1) < 0.6) * sum(
        0.12 / h * np.sin(2 * np.pi * 150 * h * time) for h in range(1, 7)
    )
    samples = clean + np.random.default_rng(15).normal(0, 0.008, len(time))
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    original = buffer.getvalue()
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset = client.post(
            "/api/audio", files={"file": ("noise.wav", original, "audio/wav")}
        ).json()["id"]
        assert client.post(f"/api/audio/{asset}/analyze").status_code == 200
        route = f"/api/audio/{asset}/processing/plan"
        plan_response = client.get(route, params={"algorithm": algorithm, "strength": strength})
        assert plan_response.status_code == 200
        plan = plan_response.json()
        noise = next(step for step in plan["steps"] if step["processor"] == "noise_reduction")
        assert noise["enabled"] and noise["parameters"]["algorithm"] == algorithm
        assert noise["parameters"]["strength"] == strength
        response = client.post(f"/api/audio/{asset}/process", json={"plan": plan})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["pipeline_version"] == "0.9.0"
        assert report["artifacts"]["background_reduction_db"] > 2
        assert report["artifacts"]["speech_energy_loss_db"] < 6
        assert (storage / asset / "original.wav").read_bytes() == original
        processed, actual_rate = sf.read(storage / asset / "processed" / "processed.wav")
        assert len(processed) == len(samples) and actual_rate == rate
        assert client.post(f"/api/audio/{asset}/process", json={"plan": plan}).json() == report
        assert client.get(route, params={"strength": "unknown"}).status_code == 422
        noise["parameters"]["noise_frequencies_hz"] = [0, 0]
        assert client.post(f"/api/audio/{asset}/process", json={"plan": plan}).status_code == 422
        assert client.get(f"/api/audio/{asset}/processing").json() == report
