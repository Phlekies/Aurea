"""Analysis API persistence, source preservation, finite contracts, and capacity limits."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.analyzer import analyze_audio
from app.config import Settings
from app.domain.analysis import AudioAnalysis
from app.domain.errors import AudioServiceUnavailable
from app.main import create_app
from app.services.analysis import AnalysisService


def upload(client: TestClient, wav_bytes: bytes) -> str:
    response = client.post("/api/audio", files={"file": ("episode.wav", wav_bytes, "audio/wav")})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def expire(storage: Path, asset_id: str) -> None:
    manifest = storage / asset_id / "asset.json"
    payload = json.loads(manifest.read_text())
    payload["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    manifest.write_text(json.dumps(payload))


def service(client: TestClient) -> AnalysisService:
    return cast(AnalysisService, cast(FastAPI, client.app).state.analysis_service)


def test_analyze_get_cache_restart_and_unchanged_original(
    storage: Path, wav_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(storage_dir=storage)
    with TestClient(create_app(settings)) as client:
        asset_id = upload(client, wav_bytes)
        before = client.get(f"/api/audio/{asset_id}/analysis")
        assert before.status_code == 404
        assert before.json()["code"] == "analysis_not_found"
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        analysis = response.json()
        assert analysis["audio_id"] == asset_id
        assert analysis["sample_rate"] == 44100
        assert analysis["channels"] == 1
        assert analysis["peak_dbfs"] == pytest.approx(-12.0412, abs=0.01)
        assert analysis["rms_dbfs"] == pytest.approx(-15.0515, abs=0.01)
        assert analysis["crest_factor_db"] == pytest.approx(3.0103, abs=0.01)
        json.dumps(analysis, allow_nan=False)
        assert (storage / asset_id / "original.wav").read_bytes() == wav_bytes
        assert client.get(f"/api/audio/{asset_id}/analysis").json() == analysis

        def fail_if_recomputed(
            path: Path, audio_id: str, *, ffmpeg: str, timeout_seconds: int
        ) -> AudioAnalysis:
            raise AssertionError("A valid analysis must be read from its cache")

        monkeypatch.setattr("app.services.analysis.analyze_audio", fail_if_recomputed)
        assert client.post(f"/api/audio/{asset_id}/analyze").json() == analysis
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/audio/{asset_id}/analysis").json() == analysis
        assert restarted.post(f"/api/audio/{asset_id}/analyze").json() == analysis
    assert not list(storage.glob(".analysis-*"))


@pytest.mark.parametrize("endpoint", ["analysis", "analyze"])
def test_missing_invalid_and_expired_assets(storage: Path, wav_bytes: bytes, endpoint: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        request = client.get if endpoint == "analysis" else client.post
        for asset_id in ("not-an-id", "a" * 32):
            missing = request(f"/api/audio/{asset_id}/{endpoint}")
            assert missing.status_code == 404
            assert missing.json()["code"] == "audio_not_found"
        asset_id = upload(client, wav_bytes)
        expire(storage, asset_id)
        expired = request(f"/api/audio/{asset_id}/{endpoint}")
        assert expired.status_code == 410
        assert expired.json()["code"] == "audio_expired"


@pytest.mark.parametrize("invalid", ["version", "asset", "nonfinite", "malformed"])
def test_invalid_cached_analysis_is_rebuilt(storage: Path, wav_bytes: bytes, invalid: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, wav_bytes)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        expected = response.json()
        cache = storage / asset_id / "analysis.json"
        payload = dict(expected)
        if invalid == "version":
            payload["analyzer_version"] = "outdated"
        elif invalid == "asset":
            payload["audio_id"] = "b" * 32
        elif invalid == "nonfinite":
            payload["peak_dbfs"] = float("nan")
        cache.write_text("broken" if invalid == "malformed" else json.dumps(payload))
        missing = client.get(f"/api/audio/{asset_id}/analysis")
        assert missing.status_code == 404
        assert missing.json()["code"] == "analysis_not_found"
        rebuilt = client.post(f"/api/audio/{asset_id}/analyze")
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json() == expected
        assert not list(storage.glob(".analysis-*"))


def test_busy_analyzer_still_serves_cached_results(storage: Path, wav_bytes: bytes) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        cached_id = upload(client, wav_bytes)
        expected = client.post(f"/api/audio/{cached_id}/analyze").json()
        new_id = upload(client, wav_bytes)
        analyzer = service(client)
        assert analyzer.capacity.acquire(blocking=False)
        try:
            busy = client.post(f"/api/audio/{new_id}/analyze")
            assert busy.status_code == 503
            assert busy.json()["code"] == "analysis_service_unavailable"
            assert client.post(f"/api/audio/{cached_id}/analyze").json() == expected
            assert client.get(f"/api/audio/{cached_id}/analysis").json() == expected
        finally:
            analyzer.capacity.release()
        assert client.post(f"/api/audio/{new_id}/analyze").status_code == 200


@pytest.mark.parametrize(
    "invalid",
    ["dc", "psd_length", "psd_order", "nyquist", "zcr", "silence", "band", "window", "gap", "end"],
)
def test_finite_incoherent_cached_analysis_can_be_recovered(
    storage: Path, wav_bytes: bytes, invalid: str
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, wav_bytes)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        expected = response.json()
        payload = json.loads(response.text)
        match invalid:
            case "dc":
                payload["dc_offset"] = []
            case "psd_length":
                payload["spectrum"]["psd_dbfs_per_hz"].pop()
            case "psd_order":
                payload["spectrum"]["frequencies_hz"][1] = payload["spectrum"]["frequencies_hz"][2]
            case "nyquist":
                payload["spectrum"]["frequencies_hz"][-1] = payload["sample_rate"]
            case "zcr":
                payload["zero_crossing_rate"] = 2
            case "silence":
                payload["silence_percent"] = 101
            case "band":
                payload["bands"][0]["percent"] = -1
            case "window":
                payload["dynamics"]["window_ms"] = -100
            case "gap":
                payload["dynamics"]["points"][0]["start_seconds"] = 0.04
            case "end":
                payload["dynamics"]["points"][-1]["duration_seconds"] /= 2
        cache = storage / asset_id / "analysis.json"
        cache.write_text(json.dumps(payload, allow_nan=False))
        missing = client.get(f"/api/audio/{asset_id}/analysis")
        assert missing.status_code == 404
        assert missing.json()["code"] == "analysis_not_found"
        rebuilt = client.post(f"/api/audio/{asset_id}/analyze")
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json() == expected


@pytest.mark.parametrize("inter_sample_peak", [False, True])
def test_semantic_validation_accepts_stereo_silence_and_positive_true_peak(
    storage: Path, inter_sample_peak: bool
) -> None:
    rate = 48000 if inter_sample_peak else 8000
    frames = rate // 5 if inter_sample_peak else 40
    tone = (
        1.2 * np.sin(np.pi / 2 * np.arange(frames) + np.pi / 4)
        if inter_sample_peak
        else np.zeros(frames)
    )
    source = io.BytesIO()
    sf.write(source, np.column_stack((tone, tone)), rate, format="WAV", subtype="FLOAT")
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, source.getvalue())
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["channels"] == 2
        if inter_sample_peak:
            assert report["peak_dbfs"] < 0 < report["true_peak_dbtp"]
        else:
            assert report["peak_dbfs"] is None
            assert report["silence_percent"] == 100
        assert client.get(f"/api/audio/{asset_id}/analysis").json() == report


@pytest.mark.parametrize("failure", [OSError, AudioServiceUnavailable])
def test_failed_analysis_releases_capacity_and_cleans_snapshot(
    storage: Path,
    wav_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, wav_bytes)

        def fail_analysis(
            path: Path, audio_id: str, *, ffmpeg: str, timeout_seconds: int
        ) -> AudioAnalysis:
            raise failure("Simulated failure with a private internal path")

        with monkeypatch.context() as patch:
            patch.setattr("app.services.analysis.analyze_audio", fail_analysis)
            for _ in range(2):
                response = client.post(f"/api/audio/{asset_id}/analyze")
                assert response.status_code == 503
                assert response.json()["code"] == "analysis_service_unavailable"
                assert "private" not in response.text
                assert str(storage) not in response.text
                assert not list(storage.glob(".analysis-*"))
        assert client.post(f"/api/audio/{asset_id}/analyze").status_code == 200


def test_cleanup_failure_does_not_exhaust_analysis_capacity(
    storage: Path, wav_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, wav_bytes)
        analyzer = service(client)

        def fail_cleanup(path: Path) -> None:
            raise PermissionError("Simulated locked snapshot")

        monkeypatch.setattr(analyzer, "_remove_staging", fail_cleanup)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 200, response.text
        assert analyzer.capacity.acquire(blocking=False)
        analyzer.capacity.release()


def test_expiry_during_analysis_discards_result_and_cleans_snapshot(
    storage: Path, wav_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = upload(client, wav_bytes)
        analyzer = service(client)

        def expire_during_analysis(
            path: Path, audio_id: str, *, ffmpeg: str, timeout_seconds: int
        ) -> AudioAnalysis:
            assert path.parent.parent == storage
            assert path.parent.name.startswith(".analysis-")
            expire(storage, audio_id)
            analyzer.audio_service.cleanup()
            # The snapshot remains readable even after cleanup removes its source asset.
            return analyze_audio(path, audio_id, ffmpeg=ffmpeg, timeout_seconds=timeout_seconds)

        monkeypatch.setattr("app.services.analysis.analyze_audio", expire_during_analysis)
        response = client.post(f"/api/audio/{asset_id}/analyze")
        assert response.status_code == 404
        assert response.json()["code"] == "audio_not_found"
        assert not list(storage.iterdir())
        assert analyzer.capacity.acquire(blocking=False)
        analyzer.capacity.release()


def test_cleanup_removes_only_stale_owned_analysis_snapshots(storage: Path) -> None:
    import os

    audio = create_app(Settings(storage_dir=storage)).state.audio_service
    analyzer = AnalysisService(audio)
    stale = storage / (".analysis-" + "a" * 32)
    stale.mkdir()
    (stale / "decoded.wav").write_bytes(b"abandoned")
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))
    recent = storage / (".analysis-" + "b" * 32)
    recent.mkdir()
    unrelated = storage / ".analysis-unrelated"
    unrelated.mkdir()
    analyzer.cleanup()
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()
