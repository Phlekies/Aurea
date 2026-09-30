"""Corrective plan recommendation, non-destructive rendering and processed playback."""

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import FileResponse

from app.api.schemas import (
    ProcessingPlanResponse,
    ProcessingReportResponse,
    ProcessRequest,
    WaveformResponse,
)
from app.domain.audio import Waveform
from app.domain.processing import ProcessingPlan, ProcessingReport
from app.services.processing import ProcessingService

router = APIRouter(prefix="/api/audio", tags=["processing"])


def processing_service(request: Request) -> ProcessingService:
    """Retrieve the isolated processing service belonging to this application."""
    return cast(ProcessingService, request.app.state.processing_service)


Service = Annotated[ProcessingService, Depends(processing_service)]


@router.get("/{asset_id}/processing/plan", response_model=ProcessingPlanResponse)
def get_plan(
    asset_id: str,
    service: Service,
    algorithm: Literal["spectral_subtraction", "spectral_gate", "wiener"] = "wiener",
    strength: Literal["light", "balanced", "strong"] = "balanced",
) -> ProcessingPlan:
    """Recommend an explainable corrective chain from the stored analysis."""
    return service.recommend(asset_id, algorithm, strength)


@router.post("/{asset_id}/process", response_model=ProcessingReportResponse)
def process_audio(
    asset_id: str,
    service: Service,
    body: Annotated[ProcessRequest | None, Body()] = None,
) -> ProcessingReport:
    """Render the given or recommended plan in a worker thread; the original is kept."""
    return service.process(asset_id, body.plan if body else None)


@router.get("/{asset_id}/processing", response_model=ProcessingReportResponse)
def get_processing(asset_id: str, service: Service) -> ProcessingReport:
    """Retrieve the published processing manifest; never starts a rendering."""
    return service.report(asset_id)


@router.get("/{asset_id}/processed/waveform", response_model=WaveformResponse)
def get_processed_waveform(asset_id: str, service: Service) -> Waveform:
    """Peaks of the processed rendering."""
    return service.waveform(asset_id)


@router.get("/{asset_id}/processed/stream", response_class=FileResponse)
def stream_processed(asset_id: str, service: Service) -> FileResponse:
    """Serve the processed 16-bit WAV with HTTP byte-range support."""
    return FileResponse(
        service.playback(asset_id), media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )
