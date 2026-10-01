"""Real FFmpeg metering, two-pass rendering and mandatory output quality gates."""

import io
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mastering.engine import MasteringInputError, MasteringQCFailed, master_audio
from app.mastering.meter import inspect_pcm, measure_loudness
from app.mastering.presets import load_presets
from app.mastering.qc import output_qc
from app.services.mastering import MasteringService


def tone(rate: int = 48000, seconds: float = 4, amplitude: float = 0.1) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * 1000 * np.arange(round(rate * seconds)) / rate)


def write(path: Path, samples: np.ndarray, rate: int = 48000) -> Path:
    sf.write(path, samples, rate, subtype="FLOAT")
    return path


def test_meter_windows_reference_level_and_silence(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path)
    source = write(tmp_path / "tone.wav", tone())
    meter = measure_loudness(source, settings)
    assert meter.integrated_lufs == pytest.approx(-23, abs=0.15)
    assert meter.momentary_max_lufs == pytest.approx(-23, abs=0.15)
    assert meter.short_term_max_lufs == pytest.approx(-23, abs=0.15)
    assert meter.true_peak_dbtp == pytest.approx(-20, abs=0.02)
    assert meter.loudness_range_lu == 0 and not meter.lra_stable
    assert len(meter.points) == 40
    assert all(p.momentary_lufs is None for p in meter.points[:3])
    assert all(p.short_term_lufs is None for p in meter.points[:29])
    silence = measure_loudness(write(tmp_path / "silent.wav", np.zeros(192000)), settings)
    assert silence.integrated_lufs is silence.true_peak_dbtp is silence.rms_dbfs is None
    assert all(p.momentary_lufs is p.short_term_lufs is None for p in silence.points)


def test_meter_detects_intersample_peak(tmp_path: Path) -> None:
    samples = 0.98 * np.sin(np.pi / 2 * np.arange(48000) + np.pi / 4)
    path = write(tmp_path / "isp.wav", samples)
    meter = measure_loudness(path, Settings(storage_dir=tmp_path))
    assert meter.true_peak_dbtp is not None and meter.sample_peak_dbfs is not None
    assert meter.true_peak_dbtp > meter.sample_peak_dbfs + 2.5


@pytest.mark.parametrize("rate,channels", [(8004, 1), (44100, 2), (48000, 1), (96000, 2)])
@pytest.mark.parametrize("preset_id", ["podcast_standard", "broadcast_r128"])
def test_final_pcm24_meets_target_and_preserves_shape(
    tmp_path: Path, rate: int, channels: int, preset_id: str
) -> None:
    samples = tone(rate, 4.037, 0.007)
    if channels == 2:
        samples = np.column_stack((samples, samples * 0.6))
    source = write(tmp_path / "source.wav", samples, rate)
    original = source.read_bytes()
    preset = load_presets()[preset_id]
    result = master_audio(source, tmp_path / "master.wav", preset, Settings(storage_dir=tmp_path))
    assert result.qc.passed and result.output.subtype == "PCM_24"
    assert result.output.frames == len(samples)
    assert (result.output.sample_rate, result.output.channels) == (rate, channels)
    assert result.after.integrated_lufs == pytest.approx(preset.target_lufs, abs=0.5)
    assert result.after.true_peak_dbtp is not None
    assert result.after.true_peak_dbtp <= preset.max_true_peak_dbtp + 0.02
    assert source.read_bytes() == original
    if channels == 2:
        output, _ = sf.read(tmp_path / "master.wav", always_2d=True)
        assert np.linalg.norm(output[:, 1]) / np.linalg.norm(output[:, 0]) == pytest.approx(
            0.6, abs=0.001
        )


def test_dynamic_limiter_controls_transients_at_native_rate(tmp_path: Path) -> None:
    samples = tone(seconds=6, amplitude=0.045)
    for start in (12000, 96000, 180000):
        samples[start : start + 240] += 0.85 * np.sin(np.pi / 2 * np.arange(240) + np.pi / 4)
    source = write(tmp_path / "peaky.wav", samples)
    result = master_audio(
        source,
        tmp_path / "master.wav",
        load_presets()["podcast_standard"],
        Settings(storage_dir=tmp_path),
    )
    assert result.mode == "dynamic" and result.qc.passed
    assert result.after.true_peak_dbtp is not None and result.after.true_peak_dbtp <= -0.98
    assert result.output.clipped_samples == 0 and result.output.frames == len(samples)


def test_linear_master_preserves_level_differences_when_headroom_allows(tmp_path: Path) -> None:
    samples = tone(seconds=12, amplitude=0.02)
    samples[len(samples) // 2 :] *= 2
    source = write(tmp_path / "levels.wav", samples)
    result = master_audio(
        source,
        tmp_path / "master.wav",
        load_presets()["podcast_standard"],
        Settings(storage_dir=tmp_path),
    )
    assert result.mode == "linear" and result.qc.passed
    output, _ = sf.read(tmp_path / "master.wav")
    assert np.linalg.norm(output[6 * 48000 :]) / np.linalg.norm(
        output[: 6 * 48000]
    ) == pytest.approx(2, abs=0.001)


@pytest.mark.parametrize("samples", [np.zeros(192000), tone(seconds=0.2)])
def test_silent_and_too_short_inputs_are_not_mastered(tmp_path: Path, samples: np.ndarray) -> None:
    source = write(tmp_path / "source.wav", samples)
    with pytest.raises(MasteringInputError):
        master_audio(
            source,
            tmp_path / "master.wav",
            load_presets()["podcast_standard"],
            Settings(storage_dir=tmp_path),
        )
    assert not (tmp_path / "master.wav").exists()


def test_qc_detects_each_invalid_output(tmp_path: Path) -> None:
    path = write(tmp_path / "source.wav", tone(amplitude=0.224))
    facts = inspect_pcm(path)
    metrics = measure_loudness(path, Settings(storage_dir=tmp_path))
    preset = load_presets()["podcast_standard"]
    assert output_qc(facts, facts, metrics, preset).passed
    cases = [
        (replace(facts, frames=facts.frames - 1), metrics, "duration"),
        (replace(facts, channels=2), metrics, "channels"),
        (replace(facts, sample_rate=44100), metrics, "sample_rate"),
        (replace(facts, clipped_samples=1), metrics, "clipping"),
        (replace(facts, finite=False), metrics, "finite"),
        (replace(facts, power=0), metrics, "not_silent"),
        (facts, replace(metrics, integrated_lufs=-20), "loudness"),
        (facts, replace(metrics, true_peak_dbtp=0), "true_peak"),
    ]
    for output, measured, code in cases:
        qc = output_qc(facts, output, measured, preset)
        assert not qc.passed
        assert {check.code for check in qc.checks if not check.passed} == {code}


def ready(client: TestClient) -> str:
    buffer = io.BytesIO()
    sf.write(buffer, tone(), 48000, format="WAV", subtype="PCM_16")
    uploaded = client.post("/api/audio", files={"file": ("voice.wav", buffer.getvalue())})
    assert uploaded.status_code == 201, uploaded.text
    asset_id = str(uploaded.json()["id"])
    assert client.post(f"/api/audio/{asset_id}/analyze").status_code == 200
    assert client.post(f"/api/audio/{asset_id}/process").status_code == 200
    return asset_id


def test_mastering_api_presets_cache_playback_and_verified_download(storage: Path) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        presets = client.get("/api/audio/mastering/presets").json()
        assert {p["id"] for p in presets} == {"podcast_standard", "broadcast_r128"}
        asset_id = ready(client)
        corrected = (storage / asset_id / "processed" / "processed.wav").read_bytes()
        assert client.get(f"/api/audio/{asset_id}/mastered/download").status_code == 404
        response = client.post(f"/api/audio/{asset_id}/master")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["qc"]["passed"] and all(c["passed"] for c in report["qc"]["checks"])
        assert len(report["qc"]["checks"]) == 8 and report["mastering_version"] == "0.9.0"
        assert client.post(f"/api/audio/{asset_id}/master").json() == report
        assert client.get(f"/api/audio/{asset_id}/mastering").json() == report
        download = client.get(f"/api/audio/{asset_id}/mastered/download")
        assert download.status_code == 200 and download.content.startswith(b"RIFF")
        assert "attachment" in download.headers["content-disposition"]
        assert sf.info(io.BytesIO(download.content)).subtype == "PCM_24"
        partial = client.get(
            f"/api/audio/{asset_id}/mastered/stream", headers={"Range": "bytes=0-43"}
        )
        assert partial.status_code == 206 and partial.content.startswith(b"RIFF")
        assert client.get(f"/api/audio/{asset_id}/mastered/waveform").json()["channels"] == 1
        assert (storage / asset_id / "processed" / "processed.wav").read_bytes() == corrected
        assert not list(storage.glob(".mastering-*"))
        alternate = client.post(f"/api/audio/{asset_id}/master", json={"preset": "broadcast_r128"})
        assert alternate.status_code == 200, alternate.text
        assert alternate.json()["after"]["integrated_lufs"] == pytest.approx(-23, abs=0.5)


@pytest.mark.parametrize("target", ["file", "report", "corrections"])
def test_altered_or_stale_masters_cannot_be_downloaded(storage: Path, target: str) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = ready(client)
        assert client.post(f"/api/audio/{asset_id}/master").status_code == 200
        output = storage / asset_id / "mastered"
        if target == "file":
            data = bytearray((output / "master.wav").read_bytes())
            data[-10] ^= 1
            (output / "master.wav").write_bytes(data)
        elif target == "report":
            report = json.loads((output / "report.json").read_text())
            report["qc"]["checks"][0]["passed"] = False
            (output / "report.json").write_text(json.dumps(report))
        else:
            plan = client.get(f"/api/audio/{asset_id}/processing/plan").json()
            for step in plan["steps"]:
                step["enabled"] = step["processor"] == "pre_gain"
                if step["processor"] == "pre_gain":
                    step["parameters"] = {"gain_db": -2}
            assert (
                client.post(f"/api/audio/{asset_id}/process", json={"plan": plan}).status_code
                == 200
            )
        for suffix in ("mastering", "mastered/download", "mastered/stream", "mastered/waveform"):
            assert client.get(f"/api/audio/{asset_id}/{suffix}").status_code == 404
        assert client.post(f"/api/audio/{asset_id}/master").status_code == 200
        assert client.get(f"/api/audio/{asset_id}/mastered/download").status_code == 200


def test_master_requires_correction_and_valid_preset(storage: Path, wav_bytes: bytes) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = client.post("/api/audio", files={"file": ("s.wav", wav_bytes)}).json()["id"]
        assert client.post(f"/api/audio/{asset_id}/master").status_code == 404
        assert (
            client.post(f"/api/audio/{asset_id}/master", json={"preset": "../other"}).status_code
            == 422
        )
        assert client.post(f"/api/audio/{asset_id}/master", json={"target": -16}).status_code == 422
        assert client.get("/api/audio/invalid/mastered/download").status_code == 404


def test_busy_and_failed_qc_do_not_publish_and_release_capacity(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(storage_dir=storage))
    with TestClient(app) as client:
        asset_id = ready(client)
        service: MasteringService = app.state.mastering_service
        capacity = service.processing_service.capacity
        assert capacity.acquire(blocking=False)
        try:
            assert client.post(f"/api/audio/{asset_id}/master").status_code == 503
        finally:
            capacity.release()

        def fail(*args: object) -> None:
            raise MasteringQCFailed("Salida rechazada por QC.")

        monkeypatch.setattr("app.services.mastering.master_audio", fail)
        response = client.post(f"/api/audio/{asset_id}/master")
        assert response.status_code == 422 and response.json()["code"] == "mastering_qc_failed"
        assert client.get(f"/api/audio/{asset_id}/mastered/download").status_code == 404
        assert not list(storage.glob(".mastering-*"))
        assert capacity.acquire(blocking=False)
        capacity.release()


def test_presets_live_outside_algorithm_and_reject_invalid_rules(tmp_path: Path) -> None:
    config = tmp_path / "presets.toml"
    config.write_text(
        '[custom]\nname="Custom"\ntarget_lufs=-18\nmax_true_peak_dbtp=-2\n', encoding="utf-8"
    )
    assert load_presets(config)["custom"].target_lufs == -18
    config.write_text('[bad]\nname="Bad"\ntarget_lufs=nan\nmax_true_peak_dbtp=-1\n')
    with pytest.raises(ValueError):
        load_presets(config)


def test_integer_meter_cadence_stays_bounded_for_30_minutes_at_8004_hz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.mastering.meter import PcmFacts

    facts = PcmFacts(8004, 1, 8004 * 1800, 0.1, 0.005, True, 0, "FLOAT")
    log = "\n".join(
        f"t: {(i * 800 - 1) / 8004:.6f} M: -23.0 S: -23.0"
        for i in range(1, facts.frames // 800 + 1)
    )
    log += (
        "\nIntegrated loudness:\nI: -23.0 LUFS\nThreshold: -33.0 LUFS\n"
        "Loudness range:\nLRA: 0.0 LU\nPeak level dB: -20.0\n"
    )
    monkeypatch.setattr("app.mastering.meter.run_ffmpeg", lambda *args: log)
    measured = measure_loudness(tmp_path / "unused.wav", Settings(storage_dir=tmp_path), facts)
    assert measured.lra_stable and 17999 <= len(measured.points) <= 18001
    assert all(
        a.time_seconds < b.time_seconds
        for a, b in zip(measured.points, measured.points[1:], strict=False)
    )


def test_failed_replacement_restores_previous_verified_master(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = ready(client)
        previous = client.post(f"/api/audio/{asset_id}/master").json()
        replace_path = Path.replace

        def refuse(source: Path, target: Path) -> Path:
            if source.name == "mastered" and source.parent.name.startswith(".mastering-"):
                raise OSError("Simulated rename failure")
            return replace_path(source, target)

        monkeypatch.setattr(Path, "replace", refuse)
        response = client.post(f"/api/audio/{asset_id}/master", json={"preset": "broadcast_r128"})
        assert response.status_code == 503
        assert client.get(f"/api/audio/{asset_id}/mastering").json() == previous
        assert client.get(f"/api/audio/{asset_id}/mastered/download").status_code == 200
        assert not list(storage.glob(".mastering-*"))


def test_unavailable_meter_has_safe_error_and_keeps_correction(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(storage_dir=storage))) as client:
        asset_id = ready(client)
        source = storage / asset_id / "processed" / "processed.wav"
        previous = source.read_bytes()

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("Private command path should not reach the API")

        monkeypatch.setattr("app.mastering.meter.subprocess.run", fail)
        response = client.post(f"/api/audio/{asset_id}/master")
        assert response.status_code == 503 and "Private command" not in response.text
        assert source.read_bytes() == previous and not list(storage.glob(".mastering-*"))
