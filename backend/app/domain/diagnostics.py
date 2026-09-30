"""Explainable observations independent of storage, HTTP, and signal processing."""

import math
from dataclasses import dataclass

type EvidenceValue = str | int | float | bool | None | list[str] | list[float]

DIAGNOSTIC_CODES = (
    "clipping",
    "hum",
    "rumble",
    "low_level",
    "low_headroom",
    "stationary_noise",
    "sibilance",
    "plosives",
)


@dataclass(frozen=True)
class Diagnostic:
    """Severity/confidence are bounded heuristic scores, not calibrated probabilities."""

    code: str
    detected: bool
    severity: float
    confidence: float
    message: str
    evidence: dict[str, EvidenceValue]
    parameters: dict[str, EvidenceValue]

    def __post_init__(self) -> None:
        if (
            self.code not in DIAGNOSTIC_CODES
            or not isinstance(self.detected, bool)
            or not 0 <= self.severity <= 1
            or not 0 <= self.confidence <= 1
            or not self.message.strip()
            or not self.evidence
            or not self.parameters
        ):
            raise ValueError("Invalid diagnostic observation")
        for record in (self.evidence, self.parameters):
            for key, value in record.items():
                if not key:
                    raise ValueError("Evidence keys cannot be empty")
                values = value if isinstance(value, list) else [value]
                if any(
                    not isinstance(item, str | int | float | bool | type(None))
                    or isinstance(item, float)
                    and not math.isfinite(item)
                    for item in values
                ):
                    raise ValueError("Diagnostic evidence must contain finite JSON scalars")
