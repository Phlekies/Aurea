"""Phase 1 upload, metadata, waveform, and range-capable audio playback endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas import AudioAssetResponse, AudioConfigResponse, WaveformResponse
from app.domain.audio import AudioAsset, AudioConfig, Waveform
from app.services.ingestion import AudioService

router = APIRouter(prefix="/api/audio", tags=["audio"])


def audio_service(request: Request) -> AudioService:
    """Retrieve this application's service rather than a global mutable store."""
    return cast(AudioService, request.app.state.audio_service)


Service = Annotated[AudioService, Depends(audio_service)]


@router.get("/config", response_model=AudioConfigResponse)
def get_config(service: Service) -> AudioConfig:
    """Read available formats and deployment upload/retention limits."""
    return service.config()


@router.post("", response_model=AudioAssetResponse, status_code=201)
def upload_audio(file: Annotated[UploadFile, File()], service: Service) -> AudioAsset:
    """Ingest in FastAPI's worker thread, keeping the event loop available."""
    try:
        return service.ingest(file.file, file.filename, file.content_type)
    finally:
        file.file.close()


@router.get("/{asset_id}", response_model=AudioAssetResponse)
def get_audio(asset_id: str, service: Service) -> AudioAsset:
    """Return technical metadata without exposing filesystem paths."""
    return service.get(asset_id)


@router.get("/{asset_id}/waveform", response_model=WaveformResponse)
def get_waveform(asset_id: str, service: Service) -> Waveform:
    """Return cached, finite channel peaks for waveform rendering."""
    return service.waveform(asset_id)


@router.get("/{asset_id}/stream", response_class=FileResponse)
def stream_audio(asset_id: str, service: Service) -> FileResponse:
    """Serve browser-compatible PCM WAV with HTTP byte-range seeking support."""
    service.get(asset_id)
    return FileResponse(
        service.directory(asset_id) / "playback.wav",
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
