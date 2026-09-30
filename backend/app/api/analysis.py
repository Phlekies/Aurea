"""Phase 2 analysis requests and persistent metric retrieval."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request

from app.api.schemas import AudioAnalysisResponse
from app.domain.analysis import AudioAnalysis
from app.services.analysis import AnalysisService

router = APIRouter(prefix="/api/audio", tags=["analysis"])


def analysis_service(request: Request) -> AnalysisService:
    """Retrieve the isolated analysis service belonging to this application."""
    return cast(AnalysisService, request.app.state.analysis_service)


Service = Annotated[AnalysisService, Depends(analysis_service)]


@router.post("/{asset_id}/analyze", response_model=AudioAnalysisResponse)
def analyze_audio(asset_id: str, service: Service) -> AudioAnalysis:
    """Run bounded analysis in FastAPI's worker thread, reusing valid cached results."""
    return service.analyze(asset_id)


@router.get("/{asset_id}/analysis", response_model=AudioAnalysisResponse)
def get_analysis(asset_id: str, service: Service) -> AudioAnalysis:
    """Retrieve stored measurements; this route never initiates analysis."""
    return service.get(asset_id)
