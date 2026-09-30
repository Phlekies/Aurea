"""Voice activity detection: a replaceable interface and an energy-based first backend.

The energy detector (version 0.5.0) classifies the shared 30 ms frames in four steps:

1. Levels: frame AC power in dBFS, clamped at -100 dB. The 10th percentile is the
   floor level and the 90th the active level, over all frames including silence.
2. Hysteresis: a frame enters speech when its level reaches
   ``max(-60 dBFS, active - 14 dB, floor + 6 dB)`` and its spectrum looks voiced
   (>=12% of 20 Hz--10 kHz band power within 80--4000 Hz, flatness < 0.6,
   ZCR < 0.45). It stays in speech while the level is >= ``max(enter - 6 dB,
   floor + 3 dB)``; unvoiced consonants therefore remain speech after an onset.
3. Smoothing: non-speech gaps shorter than 0.21 s between speech are bridged,
   speech runs shorter than 0.09 s removed, then speech is extended 0.06 s before
   and 0.12 s after each run (hangover), keeping tails out of the noise estimate.
4. Labels: remaining frames are ``silence`` at <= -90 dBFS, otherwise ``noise``.

The floor gate means a recording without level contrast (continuous noise or a
steady tone) has no speech. The spectral gate rejects white-like noise, but colored
noise louder than speech minus 14 dB and floor plus 6 dB can be accepted.

References: L. R. Rabiner and M. R. Sambur, "An algorithm for determining the
endpoints of isolated utterances", Bell System Technical Journal 54(2), 1975
(energy/ZCR thresholds with hysteresis); M. H. Moattar and M. M. Homayounpour,
"A simple but efficient real-time voice activity detection algorithm", EUSIPCO 2009
(energy, spectral flatness and minimum-duration smoothing).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.analysis.frames import WINDOW_SECONDS, WindowFeatures
from app.domain.activity import ActivitySegment, SpeechActivity

ACTIVITY_VERSION = "0.5.0"
SILENCE_POWER = 1e-9
LEVEL_CLAMP_DB = -100.0
ABSOLUTE_MINIMUM_DBFS = -60.0
FLOOR_PERCENTILE = 10.0
ACTIVE_PERCENTILE = 90.0
ACTIVE_MARGIN_DB = 14.0
FLOOR_MARGIN_DB = 6.0
HYSTERESIS_DB = 6.0
STAY_FLOOR_MARGIN_DB = 3.0
MIN_VOICE_BAND_RATIO = 0.12
MAX_FLATNESS = 0.6
MAX_ZERO_CROSSING_RATE = 0.45
BRIDGE_SECONDS = 0.21
MIN_SPEECH_SECONDS = 0.09
HANGOVER_BEFORE_SECONDS = 0.06
HANGOVER_AFTER_SECONDS = 0.12


@dataclass(frozen=True)
class ActivityInput:
    """A trusted decoded WAV plus the frame features already extracted from it.

    Frame-based backends reuse ``frames``; sample-based backends (for example
    WebRTC or Silero) can read ``path`` in bounded blocks instead.
    """

    path: Path
    sample_rate: int
    channels: int
    duration_seconds: float
    frames: tuple[WindowFeatures, ...]


class VoiceActivityDetector(Protocol):
    """Replaceable speech/non-speech segmentation backend."""

    name: str
    version: str

    def detect(self, audio: ActivityInput) -> SpeechActivity:
        """Return a contiguous timeline covering the whole recording."""
        ...


def _level_db(power: float) -> float:
    return max(LEVEL_CLAMP_DB, 10 * math.log10(power)) if power > 0 else LEVEL_CLAMP_DB


def _frames(seconds: float) -> int:
    return max(1, round(seconds / WINDOW_SECONDS))


def _runs(mask: list[bool]) -> list[tuple[bool, int, int]]:
    """Maximal runs as (value, start, end) with half-open frame indices."""
    runs: list[tuple[bool, int, int]] = []
    start = 0
    for index in range(1, len(mask) + 1):
        if index == len(mask) or mask[index] != mask[start]:
            runs.append((mask[start], start, index))
            start = index
    return runs


def smooth_speech(mask: list[bool]) -> list[bool]:
    """Bridge short pauses, drop short bursts, then add asymmetric hangover."""
    result = list(mask)
    bridge = _frames(BRIDGE_SECONDS)
    for value, start, end in _runs(result):
        if not value and start > 0 and end < len(result) and end - start < bridge:
            result[start:end] = [True] * (end - start)
    minimum = _frames(MIN_SPEECH_SECONDS)
    for value, start, end in _runs(result):
        if value and end - start < minimum:
            result[start:end] = [False] * (end - start)
    extended = list(result)
    before, after = _frames(HANGOVER_BEFORE_SECONDS), _frames(HANGOVER_AFTER_SECONDS)
    for value, start, end in _runs(result):
        if value:
            low, high = max(0, start - before), min(len(result), end + after)
            extended[low:high] = [True] * (high - low)
    return extended


def activity_from_mask(
    detector: str,
    version: str,
    frames: Sequence[WindowFeatures],
    speech: Sequence[bool],
    parameters: dict[str, float | None],
) -> SpeechActivity:
    """Build a merged timeline and summary levels from a per-frame speech mask."""
    if not frames or len(frames) != len(speech):
        raise ValueError("A speech mask must describe every frame")
    labels = [
        "speech" if active else "silence" if frame.power <= SILENCE_POWER else "noise"
        for frame, active in zip(frames, speech, strict=True)
    ]
    segments: list[ActivitySegment] = []
    start = 0
    for index in range(1, len(frames) + 1):
        if index == len(frames) or labels[index] != labels[start]:
            last = frames[index - 1]
            segments.append(
                ActivitySegment(
                    labels[start],
                    frames[start].start_seconds,
                    last.start_seconds + last.duration_seconds,
                )
            )
            start = index
    totals = dict.fromkeys(("speech", "noise", "silence"), 0.0)
    speech_energy = 0.0
    for frame, label in zip(frames, labels, strict=True):
        totals[label] += frame.duration_seconds
        if label == "speech":
            speech_energy += frame.power * frame.duration_seconds
    duration = sum(totals.values())
    speech_power = speech_energy / totals["speech"] if totals["speech"] else 0.0
    return SpeechActivity(
        detector=detector,
        version=version,
        frame_seconds=WINDOW_SECONDS,
        speech_seconds=totals["speech"],
        noise_seconds=totals["noise"],
        silence_seconds=totals["silence"],
        speech_percent=min(100.0, 100 * totals["speech"] / duration) if duration else 0.0,
        speech_rms_dbfs=10 * math.log10(speech_power) if speech_power > 0 else None,
        segments=segments,
        parameters=parameters,
    )


def frame_labels(activity: SpeechActivity, frames: Sequence[WindowFeatures]) -> list[str]:
    """Label each frame by the segment containing its midpoint (any backend)."""
    labels: list[str] = []
    segment = 0
    for frame in frames:
        middle = frame.start_seconds + frame.duration_seconds / 2
        while (
            segment < len(activity.segments) - 1
            and activity.segments[segment].end_seconds <= middle
        ):
            segment += 1
        labels.append(activity.segments[segment].label)
    return labels


class EnergyVoiceActivityDetector:
    """Energy, spectral-flatness and ZCR VAD with hysteresis and duration smoothing."""

    name = "energy"
    version = ACTIVITY_VERSION

    def detect(self, audio: ActivityInput) -> SpeechActivity:
        """Classify frames deterministically; the result depends only on the frames."""
        frames = audio.frames
        levels = np.asarray([_level_db(frame.power) for frame in frames])
        audible = any(frame.power > SILENCE_POWER for frame in frames)
        floor = float(np.percentile(levels, FLOOR_PERCENTILE)) if audible else None
        active = float(np.percentile(levels, ACTIVE_PERCENTILE)) if audible else None
        enter = stay = None
        speech = [False] * len(frames)
        if floor is not None and active is not None:
            enter = max(ABSOLUTE_MINIMUM_DBFS, active - ACTIVE_MARGIN_DB, floor + FLOOR_MARGIN_DB)
            stay = max(enter - HYSTERESIS_DB, floor + STAY_FLOOR_MARGIN_DB)
            state = False
            for index, (frame, level) in enumerate(zip(frames, levels, strict=True)):
                if frame.power <= SILENCE_POWER:
                    state = False
                elif state:
                    state = bool(level >= stay)
                else:
                    state = bool(level >= enter) and self._voiced(frame)
                speech[index] = state
            speech = smooth_speech(speech)
        return activity_from_mask(
            self.name,
            self.version,
            frames,
            speech,
            {
                "frame_seconds": WINDOW_SECONDS,
                "silence_threshold_dbfs": 10 * math.log10(SILENCE_POWER),
                "floor_level_dbfs": floor,
                "active_level_dbfs": active,
                "enter_threshold_dbfs": enter,
                "stay_threshold_dbfs": stay,
                "absolute_minimum_dbfs": ABSOLUTE_MINIMUM_DBFS,
                "active_margin_db": ACTIVE_MARGIN_DB,
                "floor_margin_db": FLOOR_MARGIN_DB,
                "hysteresis_db": HYSTERESIS_DB,
                "minimum_voice_band_ratio": MIN_VOICE_BAND_RATIO,
                "maximum_spectral_flatness": MAX_FLATNESS,
                "maximum_zero_crossing_rate": MAX_ZERO_CROSSING_RATE,
                "bridge_gap_seconds": BRIDGE_SECONDS,
                "minimum_speech_seconds": MIN_SPEECH_SECONDS,
                "hangover_before_seconds": HANGOVER_BEFORE_SECONDS,
                "hangover_after_seconds": HANGOVER_AFTER_SECONDS,
            },
        )

    @staticmethod
    def _voiced(frame: WindowFeatures) -> bool:
        band_power = frame.low_power + frame.voice_power + frame.sibilance_power
        return (
            frame.duration_seconds >= WINDOW_SECONDS / 2
            and band_power > 0
            and frame.voice_power / band_power >= MIN_VOICE_BAND_RATIO
            and frame.flatness < MAX_FLATNESS
            and frame.zero_crossing_rate < MAX_ZERO_CROSSING_RATE
        )


VAD_BACKENDS: dict[str, type[VoiceActivityDetector]] = {"energy": EnergyVoiceActivityDetector}
DEFAULT_VAD_BACKEND = "energy"


def create_vad(name: str = DEFAULT_VAD_BACKEND) -> VoiceActivityDetector:
    """Instantiate a registered backend by name."""
    try:
        return VAD_BACKENDS[name]()
    except KeyError as error:
        raise ValueError(f"Unknown voice activity backend: {name}") from error
