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
            elif demo in ("activity", "dynamics"):
                # Syllable-like phrases (0.6 s of every second) over a steady hiss.
                position = t % 1.0
                phrase = math.sin(math.pi * position / 0.6) ** 0.6 if position < 0.6 else 0.0
                pitch = 140 + 12 * math.sin(2 * math.pi * 0.7 * t)
                voice = sum(
                    math.sin(2 * math.pi * pitch * harmonic * t) / harmonic
                    for harmonic in range(1, 9)
                )
                gain = (
                    (0.035 if t < 4 else 0.28 if t < 8 else 0.06 + 0.22 * (t - 8) / 4)
                    if demo == "dynamics"
                    else 0.12
                )
                signal = gain * phrase * voice + background.gauss(
                    0.0, 0.0015 if demo == "dynamics" else 0.006
                )
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
    demos.add_argument("--dynamics-demo", action="store_true")
    args = parser.parse_args()
    demo = (
        "diagnostic"
        if args.diagnostic_demo
        else "activity"
        if args.activity_demo
        else "dynamics"
        if args.dynamics_demo
        else None
    )
    seconds = 12 if args.write_sample or args.dynamics_demo else 3
    sample = sample_audio(seconds, demo)
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
    assert asset["frames"] == seconds * 44100
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
    assert analysis["duration_seconds"] == seconds
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
    assert abs(activity["segments"][-1]["end_seconds"] - seconds) < 1e-6
    for left, right in zip(activity["segments"], activity["segments"][1:], strict=False):
        assert left["label"] != right["label"]
        assert abs(left["end_seconds"] - right["start_seconds"]) < 1e-6
    assert len(profile["frequencies_hz"]) == len(profile["psd_dbfs_per_hz"])
    if args.activity_demo:
        assert {segment["label"] for segment in activity["segments"]} == {
            "speech",
            "noise",
        }
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
        "noise_reduction",
        "pre_gain",
        "speech_leveler",
        "compressor",
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
    assert report["pipeline_version"] == "0.8.0" and report["plan"]["steps"] == plan["steps"]
    assert report["duration_seconds"] == seconds and report["sample_rate"] == 44100
    assert report["after"]["peak_dbfs"] is None or report["after"]["peak_dbfs"] <= 0
    if args.activity_demo:
        assert any(
            step["processor"] == "noise_reduction" and step["enabled"] for step in report["steps"]
        )
        assert report["artifacts"] is not None
        assert report["artifacts"]["background_reduction_db"] > 2
    if args.diagnostic_demo:
        assert "hum" in report["before"]["detected"] and "hum" not in report["after"]["detected"]
    if args.dynamics_demo:
        assert {curve["processor"] for curve in report["gain_envelopes"]} == {
            "speech_leveler",
            "compressor",
        }
        for curve in report["gain_envelopes"]:
            assert curve["times_seconds"][0] == 0
            assert curve["times_seconds"][-1] < seconds
            assert len(curve["times_seconds"]) == len(curve["gain_db"]) <= 18002
        with urllib.request.urlopen(endpoint + "/processed/stream", timeout=10) as response:
            rendered = response.read()
        before_gap = level_gap(sample)
        after_gap = level_gap(rendered)
        assert after_gap < before_gap - 3, (before_gap, after_gap)
        print(f"Speech level gap: {before_gap:.1f} -> {after_gap:.1f} dB")
    with urllib.request.urlopen(endpoint + "/processing", timeout=10) as response:
        assert json.load(response) == report
    ranged = urllib.request.Request(endpoint + "/processed/stream", headers={"Range": "bytes=0-43"})
    with urllib.request.urlopen(ranged, timeout=10) as response:
        assert response.status == 206 and response.read().startswith(b"RIFF")
    print(
        "Audio smoke passed: ingestion, streaming, metrics, diagnostics, activity, "
        "corrections and cache"
    )


def level_gap(wav: bytes) -> float:
    """Compare matching synthetic phrases of quiet and loud speakers, in RMS dB."""
    with wave.open(io.BytesIO(wav)) as audio:
        frames = audio.readframes(audio.getnframes())
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        rate = audio.getframerate()
    levels = []
    for start in (2.15, 6.15):
        region = samples[round(start * rate) : round((start + 0.3) * rate)]
        levels.append(10 * math.log10(sum(value * value for value in region) / len(region)))
    return abs(levels[1] - levels[0])


if __name__ == "__main__":
    main()
