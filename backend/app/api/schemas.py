"""Pydantic HTTP contracts wrapping framework-independent domain records."""

from pydantic import BaseModel, ConfigDict, RootModel

from app.domain.analysis import AudioAnalysis
from app.domain.audio import AudioAsset, AudioConfig, Waveform


class ErrorResponse(BaseModel):
    """Stable public error contract with no traceback or decoder output."""

    code: str
    message: str


class AudioAssetResponse(RootModel[AudioAsset]):
    """Recording metadata serialized as a flat, path-free JSON object."""

    model_config = ConfigDict(allow_inf_nan=False)


class WaveformResponse(RootModel[Waveform]):
    """Absolute amplitude maxima per equal time bucket, one series per channel."""

    model_config = ConfigDict(allow_inf_nan=False)


class AudioConfigResponse(RootModel[AudioConfig]):
    """Limits consumed by the UI; the backend remains the authority."""


class AudioAnalysisResponse(RootModel[AudioAnalysis]):
    """Finite metrics, nullable logarithmic values, and explainable diagnostics."""

    model_config = ConfigDict(allow_inf_nan=False)
