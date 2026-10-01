"""Full automatic execution, target integrity and ownership of rendering capacity."""

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from test_dynamics_api import _episode
from test_processing_api import _episode as hum_episode

from app.config import Settings
from app.main import create_app
from app.mastering.engine import MasteringQCFailed
from app.services.mastering import MasteringService


def ready(client: TestClient, wav: bytes | None = None) -> str:
    response = client.post("/api/audio", files={"file": ("synthetic.wav", wav or _episode()[0])})
    assert response.status_code == 201, response.text
    asset = str(response.json()["id"])
    assert client.post(f"/api/audio/{asset}/analyze").status_code == 200
    return asset


@pytest.mark.parametrize("preset", ["natural", "balanced", "studio"])
@pytest.mark.parametrize("target", ["podcast_standard", "broadcast_r128"])
def test_one_action_presets_qc_cache_and_original(storage: Path, preset: str, target: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        policies = client.get("/api/audio/processing/presets").json()
        assert [p["id"] for p in policies] == ["natural", "balanced", "studio"]
        asset = ready(client)
        original = (storage / asset / "original.wav").read_bytes()
        route = f"/api/audio/{asset}"
        plan = client.get(
            route + "/processing/plan", params={"preset": preset, "mastering_preset": target}
        ).json()
        assert plan["preset"] == preset and plan["mastering_preset"] == target
        response = client.post(route + "/auto-process", json={"plan": plan})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["processing"]["plan"] == plan
        master = result["mastering"]
        assert master["preset"]["id"] == target and master["qc"]["passed"]
        assert len(master["qc"]["checks"]) == 8
        assert abs(master["after"]["integrated_lufs"] - master["preset"]["target_lufs"]) <= 0.5
        downloaded = client.get(route + "/mastered/download")
        assert downloaded.status_code == 200
        info = sf.info(io.BytesIO(downloaded.content))
        assert (info.subtype, info.frames, info.samplerate, info.channels) == (
            "PCM_24",
            192000,
            16000,
            1,
        )
        assert client.post(route + "/auto-process", json={"plan": plan}).json() == result
        assert (storage / asset / "original.wav").read_bytes() == original
        assert not list(storage.glob(".processing-*")) and not list(storage.glob(".mastering-*"))


def test_recommendation_is_respected_and_manual_acceptance_changes_execution(storage: Path) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset = ready(client, hum_episode(hum=True, dc=0.02))
        route = f"/api/audio/{asset}"
        plan = client.get(route + "/processing/plan").json()
        hum = next(s for s in plan["steps"] if s["processor"] == "dehum")
        assert hum["decision"] == "recommended" and not hum["enabled"]
        automatic = client.post(route + "/auto-process", json={"plan": plan})
        assert automatic.status_code == 200, automatic.text
        assert "hum" in automatic.json()["processing"]["after"]["detected"]
        hum["enabled"], hum["decision"] = True, "manual"
        reviewed = client.post(route + "/auto-process", json={"plan": plan})
        assert reviewed.status_code == 200, reviewed.text
        assert "hum" not in reviewed.json()["processing"]["after"]["detected"]
        assert (
            reviewed.json()["mastering"]["source_revision"]
            != automatic.json()["mastering"]["source_revision"]
        )


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "preset_version",
        "preset",
        "goal",
        "missing_terminal",
        "limiter_off",
        "alter_target",
        "processor",
    ],
)
def test_invalid_complete_plan_never_publishes_a_partial_render(storage: Path, damage: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset = ready(client)
        route = f"/api/audio/{asset}"
        plan = client.get(route + "/processing/plan").json()
        if damage in ("version", "preset_version", "preset"):
            plan[damage] = "unknown"
        elif damage == "goal":
            plan["mastering_preset"] = "unknown"
        elif damage == "missing_terminal":
            plan["mastering_steps"] = []
        elif damage == "limiter_off":
            plan["mastering_steps"][1]["enabled"] = False
        elif damage == "alter_target":
            plan["mastering_steps"][0]["parameters"]["target_lufs"] = -5
        else:
            plan["steps"][0]["processor"] = "unknown"
        response = client.post(route + "/auto-process", json={"plan": plan})
        assert response.status_code == 422
        assert client.get(route + "/processing").status_code == 404
        assert not (storage / asset / "mastered").exists()


def test_capacity_is_held_across_both_stages_and_released_on_qc_failure(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(storage_dir=storage))
    with TestClient(app) as client:
        asset = ready(client)
        route = f"/api/audio/{asset}"

        def fail(
            service: MasteringService, asset_id: str, preset: str, *, capacity_reserved: bool
        ) -> None:
            assert capacity_reserved
            assert not service.processing_service.capacity.acquire(blocking=False)
            raise MasteringQCFailed("El máster no supera el control de calidad.")

        monkeypatch.setattr(MasteringService, "_master", fail)
        response = client.post(route + "/auto-process")
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "mastering_qc_failed"
        assert client.get(route + "/processing").status_code == 200
        assert client.get(route + "/mastered/download").status_code == 404
        capacity = app.state.processing_service.capacity
        assert capacity.acquire(blocking=False)
        try:
            assert client.post(route + "/auto-process").status_code == 503
        finally:
            capacity.release()


def test_silent_audio_rejects_automatic_master_but_allows_corrections(storage: Path) -> None:
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(64000), 16000, format="WAV", subtype="PCM_16")
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset = ready(client, buffer.getvalue())
        assert client.post(f"/api/audio/{asset}/auto-process").status_code == 422
        assert client.post(f"/api/audio/{asset}/process").status_code == 200
