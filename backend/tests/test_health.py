"""Integration tests for the public liveness and OpenAPI contracts."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Use a fresh application and execute its lifecycle for every test."""
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_response(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "aurea", "version": "0.1.0"}


def test_health_is_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/health"]["get"]["responses"]["200"]["content"]
    assert schema["info"]["version"] == client.get("/health").json()["version"]


def test_unknown_route_returns_safe_error(client: TestClient) -> None:
    response = client.get("/api/audio/nonexistent")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
