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


def test_api_preview_endpoint(client):
    # Test valid lookup of Moberg word via Nordic 1800s
    res = client.get("/api/preview?q=lægdslemmer")
    assert res.status_code == 200
    data = res.json()
    assert data["found"] is True
    assert "lægdslem" in data["html"]
    assert "HISTORISK BETYDNING" in data["html"] or "•" in data["html"]

    # Test html format param
    res_html = client.get("/api/preview?q=lægdslemmer&format=html")
    assert res_html.status_code == 200
    assert "<div><b>lægdslem</b>" in res_html.text

    # Test non-existent word
    res_missing = client.get("/api/preview?q=ikkeeksisterendeord999")
    assert res_missing.status_code == 200
    data_missing = res_missing.json()
    assert data_missing["found"] is False


def test_cancel_build_endpoint(client):
    # Test cancelling non-existent job
    res_404 = client.post("/api/build/cancel/nonexistent123")
    assert res_404.status_code == 404

    # Test cancelling an active job in ACTIVE_BUILDS
    from ordpresse.web.app import ACTIVE_BUILDS, BuildJob
    job = BuildJob(build_id="test_cancel_id", status="building")
    ACTIVE_BUILDS["test_cancel_id"] = job

    res_cancel = client.post("/api/build/cancel/test_cancel_id")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"
    assert job.cancelled is True
    assert job.status == "cancelled"

