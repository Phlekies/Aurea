"""Application factory; keeps API composition independent of future audio engines."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from app.api.analysis import router as analysis_router
from app.api.audio import router as audio_router
from app.api.health import router as health_router
from app.api.schemas import ErrorResponse
from app.config import Settings
from app.domain.errors import AudioError
from app.services.analysis import AnalysisService
from app.services.ingestion import AudioService
from app.upload_limits import UploadLimitMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated application instance for servers and integration tests."""
    config = settings or Settings.from_env()
    server_handlers = logging.getLogger("uvicorn.error").handlers
    for name in ("aurea.ingestion", "aurea.analysis"):
        service_logger = logging.getLogger(name)
        service_logger.setLevel(logging.INFO)
        if server_handlers and not service_logger.handlers:
            # Embedded callers can configure their own handlers.
            service_logger.handlers.extend(server_handlers)
            service_logger.propagate = False
    service = AudioService(config)
    analysis_service = AnalysisService(service)

    async def clean_expired() -> None:
        while True:
            await asyncio.sleep(60)
            await run_in_threadpool(service.cleanup)
            await run_in_threadpool(analysis_service.cleanup)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await run_in_threadpool(service.cleanup)
        await run_in_threadpool(analysis_service.cleanup)
        task = asyncio.create_task(clean_expired())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    application = FastAPI(
        title="Aurea — Podcast Audio Doctor",
        description="Explainable audio restoration and podcast mastering.",
        version=version("aurea-backend"),
        lifespan=lifespan,
    )
    application.state.audio_service = service
    application.state.analysis_service = analysis_service
    application.add_middleware(UploadLimitMiddleware, max_bytes=config.max_upload_bytes + 65536)

    @application.exception_handler(AudioError)
    async def audio_error_handler(request: Request, error: AudioError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(code=error.code, message=str(error)).model_dump(),
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        logging.getLogger("aurea.api").exception("Unexpected request failure")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="internal_error", message="No se pudo completar la operación."
            ).model_dump(),
        )

    application.include_router(health_router)
    application.include_router(audio_router)
    application.include_router(analysis_router)
    return application
