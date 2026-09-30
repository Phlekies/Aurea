"""Liveness endpoint shared by the UI and deployment health checks."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Stable, typed liveness response; does not claim audio processing readiness."""

    status: Literal["ok"] = "ok"
    service: Literal["aurea"] = "aurea"
    version: str


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Return HTTP 200 while the API can serve requests."""
    from importlib.metadata import version

    return HealthResponse(version=version("aurea-backend"))
