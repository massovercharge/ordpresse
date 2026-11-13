"""Pytest configuration and test isolation for Den Danske Ordpresse."""

import pytest
from starlette.testclient import TestClient

from ordpresse.web.auth import create_session_token


@pytest.fixture(autouse=True)
def auto_auth_for_legacy_tests(request, monkeypatch):
    """
    Ensure existing V9 tests (test_api, test_rate_limiter_and_peer, etc.)
    remain green by default by attaching an authenticated LAN session cookie.
    New V10.2 security tests in test_koreader_online are excluded so they test
    unauthenticated requests and login flows authentically.
    """
    if "test_koreader_online" in request.node.nodeid or "test_opds_and_books" in request.node.nodeid:
        return

    orig_init = TestClient.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.cookies.set("ordpresse_session", create_session_token())

    monkeypatch.setattr(TestClient, "__init__", patched_init)

