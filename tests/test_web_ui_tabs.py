"""Tests for restructured Web UI tabs and modular routers."""

import pytest
from starlette.testclient import TestClient

from ordpresse.web.app import app


@pytest.fixture
def auth_client():
    client = TestClient(app)
    # Simulate authenticated LAN admin session
    from ordpresse.web.auth import create_session_token
    token = create_session_token()
    client.cookies.set("ordpresse_session", token)
    return client


def test_index_page_contains_all_five_tabs(auth_client):
    """Verify that index.html renders all 5 tab panels and buttons."""
    resp = auth_client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Tab navigation buttons
    assert 'data-target="tab-dictionary"' in html
    assert 'data-target="tab-build"' in html
    assert 'data-target="tab-devices"' in html
    assert 'data-target="tab-store"' in html
    assert 'data-target="tab-admin"' in html

    # Tab panels
    assert 'id="tab-dictionary"' in html
    assert 'id="tab-build"' in html
    assert 'id="tab-devices"' in html
    assert 'id="tab-store"' in html
    assert 'id="tab-admin"' in html

    # Script tags for modular JS
    assert '/static/js/utils.js' in html
    assert '/static/js/tabs.js' in html
    assert '/static/js/preview.js' in html
    assert '/static/js/build.js' in html
    assert '/static/js/devices.js' in html
    assert '/static/js/store.js' in html
    assert '/static/js/admin.js' in html


def test_api_stats_endpoint(auth_client):
    """Verify /api/stats returns expected keys."""
    resp = auth_client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "datasets_sqlite_exists" in data
    assert "cache_sqlite_exists" in data

