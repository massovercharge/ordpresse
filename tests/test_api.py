"""Tests for FastAPI endpoints and healthcheck."""

from fastapi.testclient import TestClient
import pytest

from ordpresse.web.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Den Danske Ordpresse" in response.text
    assert "KOReader" in response.text


def test_api_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "datasets_sqlite_exists" in data
    assert "cache_sqlite_exists" in data
