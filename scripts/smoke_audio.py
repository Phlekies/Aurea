"""Dependency-free ingestion/diagnosis smoke test for local and Docker APIs."""

import argparse
import io
import json
import math
import struct
import urllib.request
import wave
from pathlib import Path


def sample_audio(seconds: int = 3, diagnostic_demo: bool = False) -> bytes:
    """Generate a deterministic, explicitly synthetic mono sample for reproducible QA."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        frames = bytearray()
        for frame in range(seconds * 44100):
            t = frame / 44100
            envelope = (0.5 + 0.5 * math.sin(2 * math.pi * 2 * t)) * min(t * 8, 1)
            if diagnostic_demo:
                # Deliberately clipped synthetic syllables plus a persistent 50 Hz hum.
                syllable = 0.25 + 0.75 * max(0, math.sin(2 * math.pi * 2 * t))
                voice = sum(
                    gain * math.sin(2 * math.pi * frequency * t)
                    for gain, frequency in ((1.2, 180), (0.35, 360), (0.2, 540))
                )
                signal = voice * syllable + 0.08 * math.sin(2 * math.pi * 50 * t)
                value = round(32767 * max(-1, min(1, signal)))
            else:
                value = int(9000 * envelope * math.sin(2 * math.pi * (220 + 40 * math.sin(t)) * t))
            frames.extend(struct.pack("<h", value))
        audio.writeframes(frames)
    return output.getvalue()


def main() -> None:
    """Upload synthetic audio and check ingestion, metrics and diagnostic contracts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--write-sample", type=Path)
    parser.add_argument("--diagnostic-demo", action="store_true")
    args = parser.parse_args()
    sample = sample_audio(12 if args.write_sample else 3, args.diagnostic_demo)
    if args.write_sample:
        args.write_sample.parent.mkdir(parents=True, exist_ok=True)
        args.write_sample.write_bytes(sample)
        print(f"Synthetic QA audio written to {args.write_sample}")
        return
    boundary = "AureaSmokeBoundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        + sample
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(
        f"{args.base_url}/api/audio",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        assert response.status == 201
        asset = json.load(response)
    assert asset["sample_rate"] == 44100 and asset["channels"] == 1
    assert asset["frames"] == 3 * 44100
    endpoint = f"{args.base_url}/api/audio/{asset['id']}"
    with urllib.request.urlopen(endpoint, timeout=10) as response:
        assert json.load(response) == asset
    with urllib.request.urlopen(endpoint + "/waveform", timeout=10) as response:
        waveform = json.load(response)
        assert len(waveform["peaks"][0]) == 2048
    request = urllib.request.Request(endpoint + "/stream", headers={"Range": "bytes=0-43"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 206 and response.read().startswith(b"RIFF")
    request = urllib.request.Request(endpoint + "/analyze", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        assert response.status == 200
        analysis = json.load(response)
    assert analysis["audio_id"] == asset["id"]
    assert analysis["duration_seconds"] == 3
    assert math.isfinite(analysis["integrated_lufs"])
    assert analysis["peak_dbfs"] < 0 and math.isfinite(analysis["true_peak_dbtp"])
    assert analysis["diagnostics_version"] == "0.4.0"
    expected_codes = {
        "clipping",
        "hum",
        "rumble",
        "low_level",
        "low_headroom",
        "stationary_noise",
        "sibilance",
        "plosives",
    }
    assert len(analysis["diagnostics"]) == 8
    assert {item["code"] for item in analysis["diagnostics"]} == expected_codes
    for item in analysis["diagnostics"]:
        assert isinstance(item["detected"], bool)
        assert 0 <= item["severity"] <= 1 and 0 <= item["confidence"] <= 1
        assert item["message"] and item["evidence"] and item["parameters"]
    json.dumps(analysis, allow_nan=False)
    if args.diagnostic_demo:
        found = {item["code"]: item for item in analysis["diagnostics"]}
        assert found["clipping"]["detected"]
        assert found["hum"]["detected"] and found["hum"]["evidence"]["base_frequency_hz"] == 50
    assert len(analysis["spectrum"]["frequencies_hz"]) == len(
        analysis["spectrum"]["psd_dbfs_per_hz"]
    )
    with urllib.request.urlopen(endpoint + "/analysis", timeout=10) as response:
        assert json.load(response) == analysis
    with urllib.request.urlopen(request, timeout=10) as response:
        assert json.load(response) == analysis
    print("Audio smoke passed: ingestion, streaming, metrics, diagnostics and cached report")


if __name__ == "__main__":
    main()
