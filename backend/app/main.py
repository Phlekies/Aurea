"""Application factory; keeps API composition independent of future audio engines."""

from importlib.metadata import version

from fastapi import FastAPI

from app.api.health import router as health_router


def create_app() -> FastAPI:
    """Build an isolated application instance for servers and integration tests."""
    application = FastAPI(
        title="Aurea — Podcast Audio Doctor",
        description="Explainable audio restoration and podcast mastering.",
        version=version("aurea-backend"),
    )
    application.include_router(health_router)
    return application
