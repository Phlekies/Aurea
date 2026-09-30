"""Report contracts (metrics, diagnostics, activity), cache migration and safe recovery."""

import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.diagnostics import DIAGNOSTIC_CODES
from app.main import create_app


def _upload(client: TestClient, wav_bytes: bytes) -> str:
    response = client.post("/api/audio", files={"file": ("episode.wav", wav_bytes, "audio/wav")})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_complete_diagnostic_report_retains_all_metrics_and_original(
    storage: Path, wav_bytes: bytes
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _upload(client, wav_bytes)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["diagnostics_version"] == "0.5.0"
        assert tuple(item["code"] for item in report["diagnostics"]) == DIAGNOSTIC_CODES
        assert report["peak_dbfs"] < 0
        for item in report["diagnostics"]:
            assert isinstance(item["detected"], bool)
            assert 0 <= item["severity"] <= 1
            assert 0 <= item["confidence"] <= 1
            assert item["message"] and item["evidence"] and item["parameters"]
        json.dumps(report, allow_nan=False)
        assert (storage / asset_id / "original.wav").read_bytes() == wav_bytes
        assert client.get(f"/api/audio/{asset_id}/analysis").json() == report


@pytest.mark.parametrize(
    "invalid",
    [
        "phase2",
        "old_version",
        "missing",
        "duplicate",
        "score",
        "nonfinite",
        "evidence",
        "phase3",
        "activity_version",
        "activity_coverage",
        "activity_label",
        "profile_mismatch",
        "snr_nonfinite",
    ],
)
def test_obsolete_or_invalid_diagnostic_cache_is_recomputed(
    storage: Path, wav_bytes: bytes, invalid: str
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _upload(client, wav_bytes)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        expected = response.json()
        stale = json.loads(response.text)
        match invalid:
            case "phase2":
                stale.pop("diagnostics")
                stale.pop("diagnostics_version")
            case "old_version":
                stale["diagnostics_version"] = "outdated"
            case "missing":
                stale["diagnostics"].pop()
            case "duplicate":
                stale["diagnostics"][1] = stale["diagnostics"][0]
            case "score":
                stale["diagnostics"][0]["confidence"] = 2
            case "nonfinite":
                stale["diagnostics"][0]["severity"] = float("nan")
            case "evidence":
                stale["diagnostics"][0]["evidence"]["invalid"] = {"unexpected": "object"}
            case "phase3":
                for key in ("speech_activity", "noise_profile", "estimated_snr_db"):
                    stale.pop(key)
            case "activity_version":
                stale["speech_activity"]["version"] = "0.4.0"
            case "activity_coverage":
                stale["speech_activity"]["segments"][-1]["end_seconds"] += 1
            case "activity_label":
                stale["speech_activity"]["segments"][0]["label"] = "music"
            case "profile_mismatch":
                stale["noise_profile"]["psd_dbfs_per_hz"].append(None)
            case "snr_nonfinite":
                stale["estimated_snr_db"] = float("inf")
        (storage / asset_id / "analysis.json").write_text(json.dumps(stale), encoding="utf-8")
        assert client.get(f"/api/audio/{asset_id}/analysis").status_code == 404
        rebuilt = client.post(f"/api/audio/{asset_id}/analyze")
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json() == expected


def test_detector_failure_is_safe_and_retry_preserves_original(
    storage: Path, wav_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _upload(client, wav_bytes)
        with monkeypatch.context() as patch:
            patch.setattr(
                "app.services.analysis.diagnose_audio",
                lambda *args: (_ for _ in ()).throw(OSError("private internal path")),
            )
            for _ in range(2):
                response = client.post(f"/api/audio/{asset_id}/analyze")
                assert response.status_code == 503
                assert response.json()["code"] == "analysis_failed"
                assert "private" not in response.text and str(storage) not in response.text
                assert not list(storage.glob(".analysis-*"))
                assert not (storage / asset_id / "analysis.json").exists()
        assert client.post(f"/api/audio/{asset_id}/analyze").status_code == 200
        assert (storage / asset_id / "original.wav").read_bytes() == wav_bytes


def test_report_publishes_timeline_noise_profile_and_approximate_snr(storage: Path) -> None:
    rate = 16000
    time = np.arange(rate * 4) / rate
    voice = ((time % 1.0) < 0.6) * sum(
        0.1 / h * np.sin(2 * np.pi * 140 * h * time) for h in range(1, 7)
    )
    samples = voice + np.random.default_rng(3).normal(0.0, 0.004, len(time))
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _upload(client, buffer.getvalue())
        report = client.post(f"/api/audio/{asset_id}/analyze").json()
        activity, profile = report["speech_activity"], report["noise_profile"]
        assert activity["detector"] == "energy" and activity["version"] == "0.5.0"
        assert {segment["label"] for segment in activity["segments"]} == {"speech", "noise"}
        assert activity["segments"][0]["start_seconds"] == 0
        assert activity["segments"][-1]["end_seconds"] == pytest.approx(4)
        assert 40 < activity["speech_percent"] < 90
        assert profile["frame_count"] > 10 and profile["rms_dbfs"] == pytest.approx(-48, abs=1)
        assert len(profile["frequencies_hz"]) == len(profile["psd_dbfs_per_hz"])
        assert 15 < report["estimated_snr_db"] < 35
        assert client.get(f"/api/audio/{asset_id}/analysis").json() == report
