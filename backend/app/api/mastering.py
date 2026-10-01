"""Preset selection, verified mastering, playback and QC-protected PCM24 download."""

from typing import Annotated, cast

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import FileResponse

from app.api.schemas import MasteringReportResponse, MasterRequest, WaveformResponse
from app.domain.audio import Waveform
from app.domain.mastering import MasteringPreset, MasteringReport
from app.services.mastering import MasteringService

router = APIRouter(prefix="/api/audio", tags=["mastering"])


def mastering_service(request: Request) -> MasteringService:
    return cast(MasteringService, request.app.state.mastering_service)


Service = Annotated[MasteringService, Depends(mastering_service)]


@router.get("/mastering/presets")
def get_presets(service: Service) -> list[MasteringPreset]:
    return list(service.presets.values())


@router.post("/{asset_id}/master", response_model=MasteringReportResponse)
def master_audio(
    asset_id: str, service: Service, body: Annotated[MasterRequest | None, Body()] = None
) -> MasteringReport:
    return service.master(asset_id, body.preset if body else "podcast_standard")


@router.get("/{asset_id}/mastering", response_model=MasteringReportResponse)
def get_mastering(asset_id: str, service: Service) -> MasteringReport:
    return service.report(asset_id)


@router.get("/{asset_id}/mastered/waveform", response_model=WaveformResponse)
def get_waveform(asset_id: str, service: Service) -> Waveform:
    return service.waveform(asset_id)


@router.get("/{asset_id}/mastered/stream", response_class=FileResponse)
def stream_mastered(asset_id: str, service: Service) -> FileResponse:
    return FileResponse(
        service.playback(asset_id), media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )


@router.get("/{asset_id}/mastered/download", response_class=FileResponse)
def download_mastered(asset_id: str, service: Service) -> FileResponse:
    return FileResponse(
        service.download(asset_id),
        media_type="audio/wav",
        filename=f"aurea-master-{asset_id}.wav",
        headers={"Cache-Control": "no-store"},
    )
