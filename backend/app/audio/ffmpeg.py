"""Bounded FFmpeg invocation with fixed arguments and restricted input protocols."""

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain.errors import (
    AudioServiceUnavailable,
    AudioTooLarge,
    InvalidAudioFile,
    UnsupportedAudioFormat,
)

FORMATS: dict[str, tuple[set[str], set[str]]] = {
    "wav": ({"audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave"}, {"wav"}),
    "flac": ({"audio/flac", "audio/x-flac"}, {"flac"}),
    "mp3": ({"audio/mpeg", "audio/mp3"}, {"mp3"}),
    "m4a": ({"audio/mp4", "audio/x-m4a", "video/mp4"}, {"mov", "mp4", "m4a"}),
    "ogg": ({"audio/ogg", "application/ogg", "audio/opus"}, {"ogg"}),
}
INPUT_OPTIONS = [
    "-protocol_whitelist",
    "file,pipe",
    "-format_whitelist",
    "wav,flac,mp3,mov,mp4,m4a,3gp,3g2,mj2,ogg",
]


@dataclass(frozen=True)
class ProbeResult:
    """Only trusted, validated fields needed to create the decoded representation."""

    sample_rate: int
    channels: int
    codec: str
    bitrate: int | None


def validate_filename(filename: str | None, mime: str | None) -> tuple[str, str]:
    """Strip path components and check both filename extension and supplied MIME."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(name).suffix.lower().lstrip(".")
    content_type = (mime or "application/octet-stream").split(";", 1)[0].lower().strip()
    if extension not in FORMATS or content_type not in (
        FORMATS[extension][0] | {"application/octet-stream"}
    ):
        raise UnsupportedAudioFormat("Usa un archivo WAV, FLAC, MP3, M4A u OGG válido.")
    if len(name) > 240 or any(ord(character) < 32 for character in name):
        raise InvalidAudioFile("El nombre del archivo no es válido.")
    return name, extension


def run_command(command: list[str], settings: Settings) -> str:
    """Execute without a shell, hide console windows on Windows, and enforce timeout."""
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.command_timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise AudioServiceUnavailable(
            "El decodificador no está disponible o ha agotado su tiempo. Reintenta la carga."
        ) from error
    if result.returncode != 0:
        raise InvalidAudioFile("El archivo está dañado o no contiene audio compatible.")
    return result.stdout


def probe_audio(path: Path, extension: str, settings: Settings) -> ProbeResult:
    """Validate actual container, codec metadata, native channels, rate, and duration."""
    output = run_command(
        [
            settings.ffprobe,
            "-v",
            "error",
            *INPUT_OPTIONS,
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration,bit_rate:format=format_name,duration,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        settings,
    )
    try:
        data = json.loads(output)
        streams = data["streams"]
        if len(streams) != 1:
            raise InvalidAudioFile("El archivo debe contener una única pista de audio.")
        stream = streams[0]
        container = data["format"]
        if not set(container["format_name"].split(",")) & FORMATS[extension][1]:
            raise UnsupportedAudioFormat("El contenido no coincide con la extensión del archivo.")
        rate, channels = int(stream["sample_rate"]), int(stream["channels"])
        if not settings.min_sample_rate <= rate <= settings.max_sample_rate:
            raise InvalidAudioFile("La frecuencia de muestreo está fuera del rango permitido.")
        if channels not in (1, 2):
            raise InvalidAudioFile("Solo se admiten archivos mono o estéreo.")
        duration_raw = stream.get("duration", container.get("duration"))
        if duration_raw is not None and duration_raw != "N/A":
            duration = float(duration_raw)
            if not math.isfinite(duration) or duration <= 0:
                raise InvalidAudioFile("El audio no tiene una duración válida.")
            if duration > settings.max_duration_seconds + 0.1:
                raise AudioTooLarge("El audio supera la duración máxima permitida.")
        bitrate_raw = stream.get("bit_rate", container.get("bit_rate"))
        bitrate = int(bitrate_raw) if bitrate_raw not in (None, "N/A") else None
        if bitrate is not None and bitrate <= 0:
            bitrate = None
        return ProbeResult(rate, channels, str(stream["codec_name"]), bitrate)
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidAudioFile("No se pudieron validar los metadatos del audio.") from error


def decode_audio(source: Path, destination: Path, settings: Settings) -> None:
    """Decode to float32 WAV with no channel/rate change; never overwrite an original."""
    run_command(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-threads",
            "1",
            *INPUT_OPTIONS,
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-t",
            str(settings.max_duration_seconds + 1),
            "-fs",
            str(settings.max_decoded_bytes + 65536),
            "-c:a",
            "pcm_f32le",
            "-f",
            "wav",
            str(destination),
        ],
        settings,
    )


def create_playback(source: Path, destination: Path, settings: Settings) -> None:
    """Create browser-compatible 16-bit WAV; native channels/rate remain unchanged."""
    run_command(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-threads",
            "1",
            *INPUT_OPTIONS,
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(destination),
        ],
        settings,
    )
