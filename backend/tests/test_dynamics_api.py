"""Voice leveling through the real API: audible levels, pauses, curves and cache."""

import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from app.config import Settings
from app.domain.processing import GainEnvelope
from app.main import create_app

RATE = 16000


def _episode() -> tuple[bytes, NDArray[np.float64]]:
    time = np.arange(RATE * 12) / RATE
    phrase = np.where(time % 2 < 1.6, np.sin(np.pi * np.minimum(time % 2, 1.6) / 1.6), 0)
    voice = sum(np.sin(2 * np.pi * 145 * harmonic * time) / harmonic for harmonic in range(1, 8))
    gain = np.where(time < 4, 0.035, np.where(time < 8, 0.28, 0.06 + 0.22 * (time - 8) / 4))
    samples = gain * phrase * voice + np.random.default_rng(2026).normal(0, 0.0015, len(time))
    buffer = io.BytesIO()
    sf.write(buffer, samples, RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue(), samples


def _ready(client: TestClient) -> str:
    wav, _samples = _episode()
    response = client.post("/api/audio", files={"file": ("two-voices.wav", wav, "audio/wav")})
    assert response.status_code == 201
    asset_id = str(response.json()["id"])
    assert client.post(f"/api/audio/{asset_id}/analyze").status_code == 200
    return asset_id


def _rms(samples: NDArray[np.float64], start: float, end: float) -> float:
    return float(10 * np.log10(np.mean(samples[round(start * RATE) : round(end * RATE)] ** 2)))


def test_adaptive_chain_reduces_speaker_gap_and_preserves_pauses_and_original(
    storage: Path,
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client)
        original = (storage / asset_id / "original.wav").read_bytes()
        source = np.asarray(sf.read(storage / asset_id / "decoded.wav", dtype="float64")[0])
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        steps = {step["processor"]: step for step in plan["steps"]}
        assert steps["speech_leveler"]["enabled"] and steps["compressor"]["enabled"]
        assert not steps["pre_gain"]["enabled"]
        response = client.post(f"/api/audio/{asset_id}/process", json={"plan": plan})
        assert response.status_code == 200, response.text
        report = response.json()
        rendered = np.asarray(sf.read(storage / asset_id / "processed/processed.wav")[0])
        assert rendered.shape == source.shape and np.max(np.abs(rendered)) <= 1
        before_gap = abs(_rms(source, 2.4, 3.2) - _rms(source, 6.4, 7.2))
        after_gap = abs(_rms(rendered, 2.4, 3.2) - _rms(rendered, 6.4, 7.2))
        assert after_gap < before_gap - 6, (before_gap, after_gap)
        assert _rms(rendered, 3.85, 4) <= _rms(source, 3.85, 4) + 0.1
        curves = report["gain_envelopes"]
        assert [curve["processor"] for curve in curves] == ["speech_leveler", "compressor"]
        for curve in curves:
            assert curve["times_seconds"][0] == 0
            assert curve["times_seconds"][-1] == pytest.approx((len(source) - 1) / RATE)
            assert len(curve["times_seconds"]) == len(curve["gain_db"]) == 121
            assert np.isfinite(curve["gain_db"]).all()
            step = report["steps"][curve["step_index"]]
            assert step["enabled"] and step["processor"] == curve["processor"]
        assert min(curves[0]["gain_db"]) < -2
        assert max(curves[1]["gain_db"]) <= 0
        assert (storage / asset_id / "original.wav").read_bytes() == original
        assert client.get(f"/api/audio/{asset_id}/processing").json() == report
        assert client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).json() == report


def test_noise_only_is_never_boosted_by_the_recommended_chain(storage: Path) -> None:
    samples = np.random.default_rng(25).normal(0, 0.003, RATE * 4)
    buffer = io.BytesIO()
    sf.write(buffer, samples, RATE, format="WAV", subtype="PCM_16")
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = client.post(
            "/api/audio", files={"file": ("noise.wav", buffer.getvalue(), "audio/wav")}
        ).json()["id"]
        analysis = client.post(f"/api/audio/{asset_id}/analyze").json()
        assert analysis["speech_activity"]["speech_seconds"] == 0
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        for step in plan["steps"]:
            if step["processor"] in ("pre_gain", "speech_leveler", "compressor"):
                assert not step["enabled"]
        report = client.post(f"/api/audio/{asset_id}/process").json()
        assert report["after"]["rms_dbfs"] <= report["before"]["rms_dbfs"] + 0.01
        assert report["gain_envelopes"] == []


@pytest.mark.parametrize("damage", ["missing", "duplicate", "times", "length", "step", "duration"])
def test_corrupt_gain_curve_is_not_served_and_recomputes(storage: Path, damage: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client)
        report = client.post(f"/api/audio/{asset_id}/process").json()
        curves = report["gain_envelopes"]
        match damage:
            case "missing":
                report["gain_envelopes"] = []
            case "duplicate":
                curves.append(curves[0])
            case "times":
                curves[0]["times_seconds"][1] = 0
            case "length":
                curves[0]["gain_db"].pop()
            case "step":
                curves[0]["step_index"] = 0
            case "duration":
                curves[0]["times_seconds"][-1] = 12.0
        path = storage / asset_id / "processed/report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        assert client.get(f"/api/audio/{asset_id}/processing").status_code == 404
        assert client.post(f"/api/audio/{asset_id}/process").status_code == 200
        assert client.get(f"/api/audio/{asset_id}/processing").status_code == 200


@pytest.mark.parametrize(
    ("times", "gains"), [([], []), ([0.1], [0.0]), ([0, 0], [0, 0]), ([0], [float("inf")])]
)
def test_gain_envelope_rejects_invalid_history(times: list[float], gains: list[float]) -> None:
    with pytest.raises(ValueError):
        GainEnvelope("compressor", 0, times, gains)
