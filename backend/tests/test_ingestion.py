"""Real FFmpeg integration: all formats, preservation, errors, persistence, and expiry."""

import io
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.errors import InvalidAudioFile
from app.main import create_app
from app.services.ingestion import AudioService


def make_client(storage: Path, **overrides: Any) -> TestClient:
    return TestClient(create_app(replace(Settings(storage_dir=storage), **overrides)))


@pytest.mark.parametrize("rate,channels", [(44100, 1), (48000, 2)])
def test_wav_upload_metadata_waveform_stream_and_restart(
    storage: Path, rate: int, channels: int
) -> None:
    source = io.BytesIO()
    samples = np.zeros((rate // 10, channels), dtype=np.float32)
    samples[12, :] = 0.4
    sf.write(source, samples, rate, format="WAV", subtype="PCM_16")
    original = source.getvalue()
    with make_client(storage) as client:
        response = client.post(
            "/api/audio", files={"file": ("../../episode.wav", original, "audio/wav")}
        )
        assert response.status_code == 201, response.text
        asset = response.json()
        asset_id = asset["id"]
        assert asset["filename"] == "episode.wav"
        assert asset["sample_rate"] == rate
        assert asset["channels"] == channels
        assert asset["frames"] == rate // 10
        assert asset["size_bytes"] == len(original)
        assert (storage / asset_id / "original.wav").read_bytes() == original
        assert sf.info(storage / asset_id / "decoded.wav").subtype == "FLOAT"
        assert client.get(f"/api/audio/{asset_id}").json() == asset
        wave = client.get(f"/api/audio/{asset_id}/waveform").json()
        assert len(wave["peaks"]) == channels
        assert len(wave["peaks"][0]) == 2048
        assert max(wave["peaks"][0]) == pytest.approx(0.4, abs=1e-4)
        stream = client.get(f"/api/audio/{asset_id}/stream", headers={"Range": "bytes=0-43"})
        assert stream.status_code == 206
        assert len(stream.content) == 44
        assert stream.content.startswith(b"RIFF")
        assert stream.headers["accept-ranges"] == "bytes"
        assert stream.headers["cache-control"] == "no-store"
    with make_client(storage) as restarted:
        assert restarted.get(f"/api/audio/{asset_id}").status_code == 200


@pytest.mark.parametrize(
    "extension,codec,mime",
    [
        ("flac", "flac", "audio/flac"),
        ("mp3", "libmp3lame", "audio/mpeg"),
        ("m4a", "aac", "audio/mp4"),
        ("ogg", "libvorbis", "audio/ogg"),
    ],
)
def test_compressed_formats(
    storage: Path, tmp_path: Path, wav_bytes: bytes, extension: str, codec: str, mime: str
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(wav_bytes)
    encoded = tmp_path / f"source.{extension}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-c:a", codec, str(encoded)],
        check=True,
        capture_output=True,
    )
    original = encoded.read_bytes()
    with make_client(storage) as client:
        response = client.post(
            "/api/audio", files={"file": (f"episode.{extension}", original, mime)}
        )
        assert response.status_code == 201, response.text
        asset = response.json()
        assert asset["format"] == extension
        assert asset["sample_rate"] == 44100
        assert asset["channels"] == 1
        assert (storage / asset["id"] / f"original.{extension}").read_bytes() == original
        assert client.get(f"/api/audio/{asset['id']}/stream").status_code == 200


@pytest.mark.parametrize(
    "name,mime,content,status",
    [
        ("test.exe", "application/octet-stream", b"x", 415),
        ("test.wav", "image/png", b"x", 415),
        ("test.wav", "audio/wav", b"corrupt", 422),
        ("test.wav", "audio/wav", b"", 422),
    ],
)
def test_invalid_uploads_are_safe_and_leave_no_files(
    storage: Path, name: str, mime: str, content: bytes, status: int
) -> None:
    with make_client(storage) as client:
        response = client.post("/api/audio", files={"file": (name, content, mime)})
        assert response.status_code == status
        assert set(response.json()) == {"code", "message"}
        assert str(storage) not in response.text
        assert not list(storage.iterdir())


def test_disguised_container_rejected(storage: Path, wav_bytes: bytes) -> None:
    with make_client(storage) as client:
        response = client.post("/api/audio", files={"file": ("wrong.mp3", wav_bytes, "audio/mpeg")})
        assert response.status_code == 415
        assert not list(storage.iterdir())


def test_upload_size_and_declared_body_limits(storage: Path, wav_bytes: bytes) -> None:
    with make_client(storage, max_upload_bytes=100) as client:
        assert (
            client.post(
                "/api/audio", files={"file": ("episode.wav", wav_bytes, "audio/wav")}
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/api/audio", content=b"x", headers={"Content-Length": "999999999"}
            ).status_code
            == 413
        )
        assert not list(storage.iterdir())


def test_chunked_body_limit(storage: Path) -> None:
    body = (
        b'--a\r\nContent-Disposition: form-data; name="file"; filename="big.wav"\r\n'
        b"Content-Type: audio/wav\r\n\r\n" + b"x" * 70000 + b"\r\n--a--\r\n"
    )
    with make_client(storage, max_upload_bytes=100) as client:
        response = client.post(
            "/api/audio",
            content=iter([body[:100], body[100:]]),
            headers={"Content-Type": "multipart/form-data; boundary=a"},
        )
        assert response.status_code == 413, response.text
        assert not list(storage.iterdir())


def test_duration_limit_and_missing_decoder(storage: Path, wav_bytes: bytes) -> None:
    with make_client(storage, max_duration_seconds=1) as client:
        source = io.BytesIO()
        sf.write(source, np.zeros(44100 * 2), 44100, format="WAV")
        assert (
            client.post(
                "/api/audio", files={"file": ("long.wav", source.getvalue(), "audio/wav")}
            ).status_code
            == 413
        )
    with make_client(storage, ffprobe="missing-aurea-decoder") as client:
        response = client.post("/api/audio", files={"file": ("test.wav", wav_bytes, "audio/wav")})
        assert response.status_code == 503
        assert not list(storage.iterdir())


@pytest.mark.parametrize("rate,channels", [(4000, 1), (44100, 3)])
def test_rate_and_channel_validation(storage: Path, rate: int, channels: int) -> None:
    source = io.BytesIO()
    sf.write(source, np.zeros((rate // 10, channels)), rate, format="WAV")
    with make_client(storage) as client:
        assert (
            client.post(
                "/api/audio", files={"file": ("invalid.wav", source.getvalue(), "audio/wav")}
            ).status_code
            == 422
        )


def test_expiry_and_cleanup_leave_unrelated_files(storage: Path, wav_bytes: bytes) -> None:
    settings = Settings(storage_dir=storage)
    with make_client(storage) as client:
        asset = client.post(
            "/api/audio", files={"file": ("test.wav", wav_bytes, "audio/wav")}
        ).json()
        manifest = storage / asset["id"] / "asset.json"
        import json

        payload = json.loads(manifest.read_text())
        payload["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        manifest.write_text(json.dumps(payload))
        assert client.get(f"/api/audio/{asset['id']}/stream").status_code == 410
        keep = storage / "unrelated"
        keep.mkdir()
        AudioService(settings).cleanup()
        assert not manifest.parent.exists()
        assert keep.is_dir()


def test_ids_do_not_resolve_filesystem_paths(storage: Path) -> None:
    with make_client(storage) as client:
        assert client.get("/api/audio/not-an-id").status_code == 404
        assert client.get("/api/audio/" + "a" * 32).status_code == 404
        assert client.get("/api/audio/config").json()["max_upload_bytes"] == 100 * 1024 * 1024


def test_decoded_size_budget(storage: Path, wav_bytes: bytes) -> None:
    with make_client(storage, max_decoded_bytes=1000) as client:
        response = client.post("/api/audio", files={"file": ("test.wav", wav_bytes, "audio/wav")})
        assert response.status_code == 413
        assert not list(storage.iterdir())


def test_silence_is_valid_and_nonfinite_audio_is_rejected(storage: Path) -> None:
    with make_client(storage) as client:
        for value, expected in [(0.0, 201), (float("nan"), 422)]:
            source = io.BytesIO()
            sf.write(source, np.full(4410, value), 44100, subtype="FLOAT", format="WAV")
            response = client.post(
                "/api/audio", files={"file": ("test.wav", source.getvalue(), "audio/wav")}
            )
            assert response.status_code == expected, response.text
            if expected == 201:
                waveform = client.get(f"/api/audio/{response.json()['id']}/waveform").json()
                assert not any(waveform["peaks"][0])


def test_failed_staging_cleanup_does_not_exhaust_capacity(
    storage: Path, wav_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AudioService(Settings(storage_dir=storage))

    def fail_cleanup(path: Path) -> None:
        raise PermissionError("Simulated locked staging file")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_remove", fail_cleanup)
        for _ in range(2):
            with pytest.raises(InvalidAudioFile):
                service.ingest(io.BytesIO(b"corrupt"), "bad.wav", "audio/wav")
    assert service.ingest(io.BytesIO(wav_bytes), "valid.wav", "audio/wav").frames == 4410
