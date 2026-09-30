"""Pydantic HTTP contracts wrapping framework-independent domain records."""

from pydantic import BaseModel, ConfigDict, RootModel

from app.domain.analysis import AudioAnalysis
from app.domain.audio import AudioAsset, AudioConfig, Waveform
from app.domain.processing import ProcessingPlan, ProcessingReport


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


class ProcessingPlanResponse(RootModel[ProcessingPlan]):
    """Ordered processor decisions with parameters, reasons and evidence."""

    model_config = ConfigDict(allow_inf_nan=False)


class ProcessingReportResponse(RootModel[ProcessingReport]):
    """Processing manifest: executed plan, timings, warnings and before/after metrics."""

    model_config = ConfigDict(allow_inf_nan=False)


class ProcessRequest(BaseModel):
    """Optional plan to render; omitted means the recommended corrective plan."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    plan: ProcessingPlan | None = None
