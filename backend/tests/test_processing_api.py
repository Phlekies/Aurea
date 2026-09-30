"""Processing API: recommendation, rendering, manifest, playback, cache and failures."""

import io
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

RATE = 48000


def _episode(hum: bool = False, dc: float = 0.0) -> bytes:
    time = np.arange(RATE * 5) / RATE
    voice = ((time % 1.0) < 0.6) * sum(
        0.1 / h * np.sin(2 * np.pi * 140 * h * time) for h in range(1, 9)
    )
    samples = voice + np.random.default_rng(4).normal(0, 0.002, len(time)) + dc
    if hum:
        samples += sum(0.02 / h * np.sin(2 * np.pi * 50 * h * time) for h in range(1, 4))
    buffer = io.BytesIO()
    sf.write(buffer, samples, RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _ready(client: TestClient, wav: bytes) -> str:
    response = client.post("/api/audio", files={"file": ("episode.wav", wav, "audio/wav")})
    assert response.status_code == 201, response.text
    asset_id = str(response.json()["id"])
    assert client.post(f"/api/audio/{asset_id}/analyze").status_code == 200
    return asset_id


def test_plan_and_processing_require_an_analysis(storage: Path) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        response = client.post("/api/audio", files={"file": ("e.wav", _episode(), "audio/wav")})
        asset_id = response.json()["id"]
        assert client.get(f"/api/audio/{asset_id}/processing/plan").status_code == 404
        assert client.post(f"/api/audio/{asset_id}/process").status_code == 404
        missing = client.get(f"/api/audio/{asset_id}/processing")
        assert missing.status_code == 404 and missing.json()["code"] == "processing_not_found"
        assert client.get(f"/api/audio/{asset_id}/processed/stream").status_code == 404


def test_recommended_rendering_removes_hum_and_dc_without_touching_the_original(
    storage: Path,
) -> None:
    wav = _episode(hum=True, dc=0.02)
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, wav)
        decoded = (storage / asset_id / "decoded.wav").read_bytes()
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        enabled = {step["processor"] for step in plan["steps"] if step["enabled"]}
        assert enabled == {"dc_removal", "dehum"}
        response = client.post(f"/api/audio/{asset_id}/process")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["pipeline_version"] == "0.7.0" and report["plan"]["steps"] == plan["steps"]
        assert "hum" in report["before"]["detected"] and "hum" not in report["after"]["detected"]
        assert abs(report["after"]["dc_offset"][0]) < 1e-4 < abs(report["before"]["dc_offset"][0])
        assert report["warnings"] == [] and report["safety_gain_db"] == 0
        assert [step["processor"] for step in report["steps"]] == [
            "dc_removal",
            "high_pass",
            "dehum",
            "noise_reduction",
            "pre_gain",
        ]
        assert all(step["seconds"] >= 0 for step in report["steps"])
        assert 0 < report["real_time_factor"] < 5
        json.dumps(report, allow_nan=False)
        assert (storage / asset_id / "original.wav").read_bytes() == wav
        assert (storage / asset_id / "decoded.wav").read_bytes() == decoded
        rendered, rate = sf.read(storage / asset_id / "processed" / "processed.wav")
        assert rate == RATE and len(rendered) == RATE * 5 and np.isfinite(rendered).all()
        assert client.get(f"/api/audio/{asset_id}/processing").json() == report
        partial = client.get(
            f"/api/audio/{asset_id}/processed/stream", headers={"Range": "bytes=0-43"}
        )
        assert partial.status_code == 206 and partial.content.startswith(b"RIFF")
        waveform = client.get(f"/api/audio/{asset_id}/processed/waveform").json()
        assert waveform["channels"] == 1 and len(waveform["peaks"][0]) == 2048
        assert not list(storage.glob(".processing-*"))


def test_same_plan_reuses_and_a_new_plan_replaces_the_rendering(storage: Path) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, _episode(hum=True))
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        first = client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).json()
        assert client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).json() == first
        for step in plan["steps"]:
            step["enabled"] = step["processor"] == "pre_gain"
            if step["processor"] == "pre_gain":
                step["parameters"] = {"gain_db": -6}
        second = client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).json()
        assert second != first
        assert second["after"]["peak_dbfs"] == pytest.approx(
            second["before"]["peak_dbfs"] - 6, abs=0.01
        )
        assert "hum" in second["after"]["detected"]
        assert client.get(f"/api/audio/{asset_id}/processing").json() == second


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"processor": "reverb"}, 422),
        ({"parameters": {"cutoff_hz": 4}}, 422),
        ({"parameters": {"cutoff_hz": 80, "shell": "rm -rf"}}, 422),
        ({"parameters": {"cutoff_hz": "80"}}, 422),
        ({"confidence": 3}, 422),
    ],
)
def test_invalid_plans_are_rejected_before_rendering(
    storage: Path, change: dict[str, object], status: int
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, _episode())
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        plan["steps"][1].update(change)
        response = client.post(f"/api/audio/{asset_id}/process", json={"plan": plan})
        assert response.status_code == status, response.text
        assert not (storage / asset_id / "processed").exists()
        extra = client.post(f"/api/audio/{asset_id}/process", json={"plan": None, "mode": "x"})
        assert extra.status_code == 422


def test_rendering_failure_is_safe_and_retry_succeeds(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, _episode())
        with monkeypatch.context() as patch:
            patch.setattr(
                "app.services.processing.run_plan",
                lambda *args: (_ for _ in ()).throw(OSError("private internal path")),
            )
            response = client.post(f"/api/audio/{asset_id}/process")
            assert response.status_code == 503
            assert response.json()["code"] == "processing_failed"
            assert "private" not in response.text and str(storage) not in response.text
            assert not list(storage.glob(".processing-*"))
            assert not (storage / asset_id / "processed").exists()
        assert client.post(f"/api/audio/{asset_id}/process").status_code == 200


@pytest.mark.parametrize(
    "damage", ["version", "missing_file", "invalid_json", "duration", "timing", "execution"]
)
def test_stale_or_damaged_rendering_is_not_served(storage: Path, damage: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, _episode())
        assert client.post(f"/api/audio/{asset_id}/process").status_code == 200
        output = storage / asset_id / "processed"
        match damage:
            case "version":
                report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                report["pipeline_version"] = "0.5.0"
                (output / "report.json").write_text(json.dumps(report), encoding="utf-8")
            case "missing_file":
                (output / "playback.wav").unlink()
            case "invalid_json":
                (output / "report.json").write_text("{", encoding="utf-8")
            case "duration" | "timing" | "execution":
                report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                if damage == "duration":
                    report["duration_seconds"] += 1
                elif damage == "timing":
                    report["steps"][0]["seconds"] = -1
                else:
                    report["steps"][0]["enabled"] = not report["steps"][0]["enabled"]
                (output / "report.json").write_text(json.dumps(report), encoding="utf-8")
        assert client.get(f"/api/audio/{asset_id}/processing").status_code == 404
        assert client.get(f"/api/audio/{asset_id}/processed/stream").status_code == 404
        assert client.post(f"/api/audio/{asset_id}/process").status_code == 200
        assert client.get(f"/api/audio/{asset_id}/processing").status_code == 200


def test_publication_failure_restores_previous_render(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, _episode())
        previous = client.post(f"/api/audio/{asset_id}/process").json()
        old_audio = (storage / asset_id / "processed" / "processed.wav").read_bytes()
        plan = json.loads(json.dumps(previous["plan"]))
        plan["steps"][-1]["enabled"] = True
        plan["steps"][-1]["parameters"] = {"gain_db": -4.0}
        original_replace = Path.replace

        def fail_publication(path: Path, target: str | Path) -> Path:
            if path.name == "processed" and path.parent.name.startswith(".processing-"):
                raise OSError("Synthetic publication failure")
            return original_replace(path, target)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "replace", fail_publication)
            assert (
                client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).status_code
                == 503
            )
        assert client.get(f"/api/audio/{asset_id}/processing").json() == previous
        assert (storage / asset_id / "processed" / "processed.wav").read_bytes() == old_audio
        assert not list(storage.glob(".processing-*"))
        assert client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).status_code == 200


def test_hidden_but_unrepaired_clipping_stays_reported(storage: Path) -> None:
    time = np.arange(RATE * 4) / RATE
    voice = ((time % 1.0) < 0.6) * 1.6 * np.sin(2 * np.pi * 180 * time)
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(voice, -1, 1), RATE, format="WAV", subtype="PCM_16")
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = _ready(client, buffer.getvalue())
        plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
        gain = next(step for step in plan["steps"] if step["processor"] == "pre_gain")
        assert gain["enabled"] and gain["parameters"]["gain_db"] < 0
        report = client.post(f"/api/audio/{asset_id}/process").json()
        assert "clipping" in report["before"]["detected"]
        assert "clipping" in report["after"]["detected"]
        assert any("no se repara" in warning for warning in report["warnings"])
