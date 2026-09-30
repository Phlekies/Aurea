"""Dependency-free ingestion, diagnosis and speech-activity smoke test for local/Docker APIs."""

import argparse
import io
import json
import math
import random
import struct
import urllib.request
import wave
from pathlib import Path


def sample_audio(seconds: int = 3, demo: str | None = None) -> bytes:
    """Generate a deterministic, explicitly synthetic mono sample for reproducible QA."""
    background = random.Random(2026)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        frames = bytearray()
        for frame in range(seconds * 44100):
            t = frame / 44100
            envelope = (0.5 + 0.5 * math.sin(2 * math.pi * 2 * t)) * min(t * 8, 1)
            if demo == "diagnostic":
                # Deliberately clipped synthetic syllables plus a persistent 50 Hz hum.
                syllable = 0.25 + 0.75 * max(0, math.sin(2 * math.pi * 2 * t))
                voice = sum(
                    gain * math.sin(2 * math.pi * frequency * t)
                    for gain, frequency in ((1.2, 180), (0.35, 360), (0.2, 540))
                )
                signal = voice * syllable + 0.08 * math.sin(2 * math.pi * 50 * t)
                value = round(32767 * max(-1, min(1, signal)))
            elif demo == "activity":
                # Syllable-like phrases (0.6 s of every second) over a steady hiss.
                position = t % 1.0
                phrase = math.sin(math.pi * position / 0.6) ** 0.6 if position < 0.6 else 0.0
                pitch = 140 + 12 * math.sin(2 * math.pi * 0.7 * t)
                voice = sum(
                    math.sin(2 * math.pi * pitch * harmonic * t) / harmonic
                    for harmonic in range(1, 9)
                )
                signal = 0.12 * phrase * voice + background.gauss(0.0, 0.006)
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
    demos = parser.add_mutually_exclusive_group()
    demos.add_argument("--diagnostic-demo", action="store_true")
    demos.add_argument("--activity-demo", action="store_true")
    args = parser.parse_args()
    demo = "diagnostic" if args.diagnostic_demo else "activity" if args.activity_demo else None
    sample = sample_audio(12 if args.write_sample else 3, demo)
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
    assert analysis["diagnostics_version"] == "0.6.0"
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
    activity, profile = analysis["speech_activity"], analysis["noise_profile"]
    assert activity["version"] == "0.5.0" and activity["segments"][0]["start_seconds"] == 0
    assert abs(activity["segments"][-1]["end_seconds"] - 3) < 1e-6
    for left, right in zip(activity["segments"], activity["segments"][1:], strict=False):
        assert left["label"] != right["label"]
        assert abs(left["end_seconds"] - right["start_seconds"]) < 1e-6
    assert len(profile["frequencies_hz"]) == len(profile["psd_dbfs_per_hz"])
    if args.activity_demo:
        assert {segment["label"] for segment in activity["segments"]} == {"speech", "noise"}
        assert 40 < activity["speech_percent"] < 90
        assert abs(profile["rms_dbfs"] - 20 * math.log10(0.006)) < 2
        assert 10 < analysis["estimated_snr_db"] < 40
    assert len(analysis["spectrum"]["frequencies_hz"]) == len(
        analysis["spectrum"]["psd_dbfs_per_hz"]
    )
    with urllib.request.urlopen(endpoint + "/analysis", timeout=10) as response:
        assert json.load(response) == analysis
    with urllib.request.urlopen(request, timeout=10) as response:
        assert json.load(response) == analysis
    with urllib.request.urlopen(endpoint + "/processing/plan", timeout=10) as response:
        plan = json.load(response)
    assert [step["processor"] for step in plan["steps"]] == [
        "dc_removal",
        "high_pass",
        "dehum",
        "pre_gain",
    ]
    assert all(step["reason"] for step in plan["steps"])
    request = urllib.request.Request(
        endpoint + "/process",
        data=json.dumps({"plan": plan}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        report = json.load(response)
    assert report["pipeline_version"] == "0.6.0" and report["plan"]["steps"] == plan["steps"]
    assert report["duration_seconds"] == 3 and report["sample_rate"] == 44100
    assert report["after"]["peak_dbfs"] is None or report["after"]["peak_dbfs"] <= 0
    if args.diagnostic_demo:
        assert "hum" in report["before"]["detected"] and "hum" not in report["after"]["detected"]
    with urllib.request.urlopen(endpoint + "/processing", timeout=10) as response:
        assert json.load(response) == report
    ranged = urllib.request.Request(endpoint + "/processed/stream", headers={"Range": "bytes=0-43"})
    with urllib.request.urlopen(ranged, timeout=10) as response:
        assert response.status == 206 and response.read().startswith(b"RIFF")
    print(
        "Audio smoke passed: ingestion, streaming, metrics, diagnostics, activity, "
        "corrections and cache"
    )


if __name__ == "__main__":
    main()
