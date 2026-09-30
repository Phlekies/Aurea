"""Central configuration for bounded audio ingestion and local temporary storage."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Upload limits in bytes/seconds/Hz, overridable through AUREA_* variables."""

    storage_dir: Path
    max_upload_bytes: int = 100 * 1024 * 1024
    max_duration_seconds: int = 1800
    min_sample_rate: int = 8000
    max_sample_rate: int = 96000
    max_decoded_bytes: int = 1536 * 1024 * 1024
    retention_seconds: int = 86400
    command_timeout_seconds: int = 180
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    def __post_init__(self) -> None:
        if (
            any(
                value <= 0
                for value in (
                    self.max_upload_bytes,
                    self.max_duration_seconds,
                    self.min_sample_rate,
                    self.max_sample_rate,
                    self.max_decoded_bytes,
                    self.retention_seconds,
                    self.command_timeout_seconds,
                )
            )
            or self.min_sample_rate > self.max_sample_rate
        ):
            raise ValueError("Audio limits must be positive and sample rates ordered")

    @classmethod
    def from_env(cls) -> "Settings":
        """Read deployment configuration without requiring a machine-specific path."""
        return cls(
            storage_dir=Path(os.getenv("AUREA_STORAGE_DIR", "data/audio")).resolve(),
            max_upload_bytes=int(os.getenv("AUREA_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
            max_duration_seconds=int(os.getenv("AUREA_MAX_DURATION_SECONDS", "1800")),
            min_sample_rate=int(os.getenv("AUREA_MIN_SAMPLE_RATE", "8000")),
            max_sample_rate=int(os.getenv("AUREA_MAX_SAMPLE_RATE", "96000")),
            max_decoded_bytes=int(os.getenv("AUREA_MAX_DECODED_BYTES", str(1536 * 1024 * 1024))),
            retention_seconds=int(os.getenv("AUREA_RETENTION_SECONDS", "86400")),
            command_timeout_seconds=int(os.getenv("AUREA_COMMAND_TIMEOUT_SECONDS", "180")),
            ffmpeg=os.getenv("AUREA_FFMPEG", "ffmpeg"),
            ffprobe=os.getenv("AUREA_FFPROBE", "ffprobe"),
        )
