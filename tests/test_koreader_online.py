"""Automated tests for V10.2 Zero-Trust Two-Zone KOReader integration & API key provisioning."""

import io
import zipfile
import pytest
from fastapi.testclient import TestClient

from ordpresse.db import Database
from ordpresse.web.app import app
from ordpresse.web.auth import ORDPRESSE_ADMIN_PASSWORD, create_session_token


@pytest.fixture
def client():
    """Unauthenticated client for security boundary verification."""
    return TestClient(app)


@pytest.fixture
def auth_client():
    """Authenticated LAN client with a signed ordpresse_session cookie."""
    c = TestClient(app)
    c.cookies.set("ordpresse_session", create_session_token())
    return c


# ---------------------------------------------------------------------------
# 1. Layer 1 — Strict Two-Zone Boundary (External / Cloudflare Tunnel)
# ---------------------------------------------------------------------------

def test_external_tunnel_requests_return_404_for_non_lookup_paths(client):
    """External tunnel requests must return HTTP 404 for all non-lookup paths."""
    tunnel_headers = {"CF-Connecting-IP": "203.0.113.10"}

    # Browser UI /
    res_index = client.get("/", headers=tunnel_headers)
    assert res_index.status_code == 404
    assert res_index.text == "Not Found"

    # Login screen /login
    res_login = client.get("/login", headers=tunnel_headers)
    assert res_login.status_code == 404

    # API keys listing /api/v1/keys
    res_keys = client.get("/api/v1/keys", headers=tunnel_headers)
    assert res_keys.status_code == 404

    # API key provisioning /api/v1/keys/provision
    res_prov = client.post(
        "/api/v1/keys/provision",
        json={"device_name": "External Kindle"},
        headers=tunnel_headers,
    )
    assert res_prov.status_code == 404

    # Even with a valid session cookie attached, external requests must still return 404
    cookie_token = create_session_token()
    client.cookies.set("ordpresse_session", cookie_token)
    res_with_cookie = client.get("/api/v1/keys", headers=tunnel_headers)
    assert res_with_cookie.status_code == 404


def test_external_tunnel_request_to_lookup_succeeds_with_api_key(client):
    """Simulated tunnel requests to GET /api/v1/koreader/lookup succeed when authenticated."""
    db = Database()
    raw_key, key_row = db.create_api_key("Cloudflare Test Device")

    tunnel_headers = {
        "CF-Connecting-IP": "203.0.113.10",
        "X-API-Key": raw_key,
    }

    res = client.get("/api/v1/koreader/lookup?q=lægdslemmer", headers=tunnel_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "lægdslemmer"
    assert data["resolved_lemma"] == "lægdslem"
    assert "lægdslem" in data["display_text"]


# ---------------------------------------------------------------------------
# 2. Layer 2 — LAN Login Protection
# ---------------------------------------------------------------------------

def test_unauthenticated_lan_root_redirects_to_login(client):
    """Unauthenticated browser request to / on LAN must redirect (302) to /login."""
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_unauthenticated_lan_api_returns_401(client):
    """Unauthenticated requests to /api/v1/keys and provision must return HTTP 401."""
    res_keys = client.get("/api/v1/keys")
    assert res_keys.status_code == 401

    res_prov = client.post("/api/v1/keys/provision", json={"device_name": "Test Device"})
    assert res_prov.status_code == 401


def test_lan_login_flow(client):
    """Wrong password returns 401; correct password sets cookie and grants access."""
    # Mismatch password
    res_fail = client.post("/login", data={"password": "wrong_password_123"})
    assert res_fail.status_code == 401
    assert "Forkert adgangskode" in res_fail.text
    assert "ordpresse_session" not in client.cookies

    # Match password
    res_ok = client.post("/login", data={"password": ORDPRESSE_ADMIN_PASSWORD}, follow_redirects=False)
    assert res_ok.status_code == 303
    assert res_ok.headers["location"] == "/"
    assert "ordpresse_session" in client.cookies

    # Session cookie now grants access to LAN UI and APIs
    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "Den Danske Ordpresse" in res_index.text

    res_keys = client.get("/api/v1/keys")
    assert res_keys.status_code == 200
    assert isinstance(res_keys.json(), list)


# ---------------------------------------------------------------------------
# 3. API Key Provisioning & Management Lifecycle
# ---------------------------------------------------------------------------

def test_api_key_provisioning_and_lifecycle(auth_client):
    """Test generating, inspecting, revoking, and deleting an API key."""
    db = Database()

    # 1. Provision key and receive customized .koplugin.zip
    prov_res = auth_client.post(
        "/api/v1/keys/provision",
        json={
            "device_name": "Boox Palma 2",
            "endpoint_url": "https://ordpresse.seame.click/api/v1/koreader/lookup",
        },
    )
    assert prov_res.status_code == 200
    assert prov_res.headers["content-type"] == "application/zip"
    assert "ordpresse-koplugin-boox-palma-2.zip" in prov_res.headers["content-disposition"]

    # Inspect zip contents
    zip_bytes = prov_res.content
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        file_list = zf.namelist()
        assert "ordpresse.koplugin/_meta.lua" in file_list
        assert "ordpresse.koplugin/config.lua" in file_list
        assert "ordpresse.koplugin/main.lua" in file_list

        config_lua = zf.read("ordpresse.koplugin/config.lua").decode("utf-8")
        assert "https://ordpresse.seame.click/api/v1/koreader/lookup" in config_lua
        assert "Boox Palma 2" in config_lua
        assert 'api_key = "ord_' in config_lua

        # Verify KOReader plugin hooks in main.lua
        main_lua = zf.read("ordpresse.koplugin/main.lua").decode("utf-8")
        assert "addToDictButtons" in main_lua
        assert "addToHighlightDialog" in main_lua
        assert "onDictButtonsReady" in main_lua
        assert "onReaderReady" in main_lua
        assert "addToMainMenu" in main_lua
        assert "checkForUpdates" in main_lua
        assert "downloadAndApplyUpdate" in main_lua

        # Extract generated raw key from config.lua
        raw_key = None
        for line in config_lua.splitlines():
            if 'api_key = "' in line:
                raw_key = line.split('"')[1]
                break
        assert raw_key is not None
        assert raw_key.startswith("ord_")
        assert len(raw_key) == 52

    # 2. Database verification: only 64-char hex hash is stored, never raw key
    key_hash = None
    all_keys = db.list_api_keys()
    target_key = next((k for k in all_keys if k["device_name"] == "Boox Palma 2"), None)
    assert target_key is not None
    assert target_key["key_prefix"] == raw_key[:8]
    assert target_key["is_active"] == 1
    assert target_key["lookup_count"] == 0
    assert "key_hash" not in target_key  # Must never be exposed by list_api_keys

    key_id = target_key["id"]

    # 3. Successful lookup with the newly provisioned key
    lookup_res = auth_client.get(
        "/api/v1/koreader/lookup?q=lægdslemmer",
        headers={"X-API-Key": raw_key},
    )
    assert lookup_res.status_code == 200
    assert lookup_res.json()["resolved_lemma"] == "lægdslem"

    # Verify lookup count was incremented
    updated_key = next((k for k in db.list_api_keys() if k["id"] == key_id), None)
    assert updated_key["lookup_count"] == 1
    assert updated_key["last_used_at"] is not None

    # 4. Revoke key
    revoke_res = auth_client.post(f"/api/v1/keys/{key_id}/revoke")
    assert revoke_res.status_code == 200
    assert revoke_res.json()["status"] == "revoked"

    # Subsequent lookup with revoked key must fail with HTTP 401
    revoked_lookup = auth_client.get(
        "/api/v1/koreader/lookup?q=lægdslemmer",
        headers={"X-API-Key": raw_key},
    )
    assert revoked_lookup.status_code == 401
    assert "tilbagekaldt" in revoked_lookup.json()["detail"].lower()

    # 5. Delete key
    del_res = auth_client.delete(f"/api/v1/keys/{key_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    remaining = [k for k in db.list_api_keys() if k["id"] == key_id]
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# 4. Lookup Pipeline & Validation
# ---------------------------------------------------------------------------

def test_lookup_missing_or_invalid_api_key(client):
    """Missing or invalid API key returns HTTP 401."""
    # Missing header
    res_missing = client.get("/api/v1/koreader/lookup?q=bog")
    assert res_missing.status_code == 401

    # Malformed key (not starting with ord_)
    res_bad = client.get("/api/v1/koreader/lookup?q=bog", headers={"X-API-Key": "bearer 12345"})
    assert res_bad.status_code == 401

    # Non-existent key
    res_fake = client.get(
        "/api/v1/koreader/lookup?q=bog",
        headers={"X-API-Key": "ord_0123456789abcdef0123456789abcdef0123456789abcdef"},
    )
    assert res_fake.status_code == 401


def test_lookup_input_sanitization_and_validation(client):
    """Test validation errors (empty or > 64 chars) and punctuation trimming."""
    db = Database()
    raw_key, _ = db.create_api_key("Validation Tester")
    headers = {"X-API-Key": raw_key}

    # Empty / whitespace-only query -> HTTP 422
    res_empty = client.get("/api/v1/koreader/lookup?q=", headers=headers)
    assert res_empty.status_code == 422

    res_ws = client.get("/api/v1/koreader/lookup?q=%20%20%20", headers=headers)
    assert res_ws.status_code == 422

    # Query > 64 characters -> HTTP 422
    long_q = "a" * 65
    res_long = client.get(f"/api/v1/koreader/lookup?q={long_q}", headers=headers)
    assert res_long.status_code == 422

    # Query with line breaks -> HTTP 422
    res_newline = client.get("/api/v1/koreader/lookup", params={"q": "ord\nandetord"}, headers=headers)
    assert res_newline.status_code == 422

    # Punctuation-wrapped query "«lægdslemmer!»" sanitizes and resolves to lægdslem
    res_punct = client.get("/api/v1/koreader/lookup?q=«lægdslemmer!»", headers=headers)
    assert res_punct.status_code == 200
    data = res_punct.json()
    assert data["query"] == "lægdslemmer"
    assert data["resolved_lemma"] == "lægdslem"
    assert len(data["entries"]) >= 1
    assert "•" in data["display_text"]


def test_lookup_pre_1948_aa_orthography(client):
    """Pre-1948 orthography 'daabskjole' resolves to 'dåbskjole'."""
    db = Database()
    raw_key, _ = db.create_api_key("Orthography Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get("/api/v1/koreader/lookup?q=daabskjole", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["resolved_lemma"] == "dåbskjole"


def test_lookup_negative_caching_for_unknown_words(client):
    """Unknown nonsense word is stored in negative cache and hits immediately on retry."""
    import uuid

    db = Database()
    raw_key, _ = db.create_api_key("Negative Cache Tester")
    headers = {"X-API-Key": raw_key}

    nonsense = f"xyzzynotaword_{uuid.uuid4().hex[:8]}"

    # First lookup: misses local, goes to negative cache
    res1 = client.get(f"/api/v1/koreader/lookup?q={nonsense}", headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["resolved_lemma"] is None
    assert data1["entries"] == []
    assert f"Ingen resultater fundet for '{nonsense}'." in data1["display_text"]
    assert data1.get("cached_not_found") is False

    # Second lookup: must immediately hit the negative cache
    res2 = client.get(f"/api/v1/koreader/lookup?q={nonsense}", headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["resolved_lemma"] is None
    assert data2["cached_not_found"] is True


def test_change_admin_password_flow(client, auth_client):
    """Test changing the admin password in the UI, input validation, and login with new password."""
    # 1. Unauthenticated change attempt returns 401
    res_unauth = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "any",
            "new_password": "new_secret_pwd",
            "confirm_password": "new_secret_pwd",
        },
    )
    assert res_unauth.status_code == 401

    # 2. Authenticated attempt with mismatched confirm password returns 400
    res_mismatch = auth_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": ORDPRESSE_ADMIN_PASSWORD,
            "new_password": "passwordA",
            "confirm_password": "passwordB",
        },
    )
    assert res_mismatch.status_code == 400
    assert "stemmer ikke overens" in res_mismatch.json()["detail"]

    # 3. Authenticated attempt with wrong current password returns 400
    res_wrong_cur = auth_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "wrong_current_password",
            "new_password": "new_secret_pwd_123",
            "confirm_password": "new_secret_pwd_123",
        },
    )
    assert res_wrong_cur.status_code == 400
    assert "forkert" in res_wrong_cur.json()["detail"].lower()

    # 4. Successful password change
    new_pwd = "helt_nyt_kodeord_2026"
    res_success = auth_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": ORDPRESSE_ADMIN_PASSWORD,
            "new_password": new_pwd,
            "confirm_password": new_pwd,
        },
    )
    assert res_success.status_code == 200
    assert res_success.json()["status"] == "ok"

    # 5. Verify old password no longer works
    login_client = TestClient(app)
    res_old_login = login_client.post("/login", data={"password": ORDPRESSE_ADMIN_PASSWORD})
    assert res_old_login.status_code == 401
    assert "Forkert adgangskode" in res_old_login.text

    # 6. Verify new password works
    res_new_login = login_client.post("/login", data={"password": new_pwd}, follow_redirects=False)
    assert res_new_login.status_code == 303
    assert res_new_login.headers["location"] == "/"
    assert "ordpresse_session" in login_client.cookies

    # Reset back to default for other tests if necessary
    db = Database()
    db.set_admin_password(ORDPRESSE_ADMIN_PASSWORD)


def test_koreader_plugin_update_endpoints(client):
    """Test action=check_update and action=download_update on /api/v1/koreader/lookup."""
    db = Database()
    raw_key, _ = db.create_api_key("Update Tester")
    headers = {"X-API-Key": raw_key}

    # 1. Unauthenticated request fails with 401
    unauth = client.get("/api/v1/koreader/lookup?action=check_update")
    assert unauth.status_code == 401

    # 2. Authenticated check_update
    res_check = client.get("/api/v1/koreader/lookup?action=check_update", headers=headers)
    assert res_check.status_code == 200
    check_data = res_check.json()
    assert "latest_version" in check_data
    assert check_data["latest_version"] == "1.2.2"
    assert "release_notes" in check_data

    # 3. Authenticated download_update
    res_dl = client.get("/api/v1/koreader/lookup?action=download_update", headers=headers)
    assert res_dl.status_code == 200
    dl_data = res_dl.json()
    assert dl_data["version"] == "1.2.2"
    assert "main_lua" in dl_data
    assert len(dl_data["main_lua"]) > 1000
    assert "checkForUpdates" in dl_data["main_lua"]
    assert "openPluginStore" in dl_data["main_lua"]
    assert "registerToMainMenu" in dl_data["main_lua"]
    assert "meta_lua" in dl_data
    assert 'version = "1.2.2"' in dl_data["meta_lua"]
    assert "sha256" in dl_data


def test_koreader_plugin_lua_extraction_and_update_hardening():
    """Verify generated plugin Lua avoids ffi/archiver getPath crash and uses safe staging/atomic writes."""
    from ordpresse.web.plugin_builder import MAIN_LUA, META_LUA, PLUGIN_VERSION

    assert f'local PLUGIN_VERSION = "{PLUGIN_VERSION}"' in MAIN_LUA
    assert f'version = "{PLUGIN_VERSION}"' in META_LUA

    # Regression check: ffi/archiver entries are plain tables with .path, never :getPath()
    assert "getPath" not in MAIN_LUA
    assert "entry.path" in MAIN_LUA

    # Extraction safety & staging checks (staging dir must NOT end in .koplugin)
    assert 'local staging_dir = plugins_dir .. "/." .. safe_koplugin .. ".tmp"' in MAIN_LUA
    assert '".tmp"' in MAIN_LUA
    assert "entry_size == 0" in MAIN_LUA
    assert "util.makePath" in MAIN_LUA
    assert "ffiUtil.purgeDir" in MAIN_LUA
    assert "checkLuaSyntax" in MAIN_LUA
    assert "writeFileAtomic" in MAIN_LUA
    assert "closeWidgetSafe" in MAIN_LUA
    assert "triggerRestart" in MAIN_LUA

    # Verify Python->Lua backslash escaping for shell escape helper
    assert "'\\\\''" in MAIN_LUA
    # No unescaped literal newlines inside Lua string concatenations (all should be \n inside Lua strings)
    assert '"\n"' not in MAIN_LUA


# ---------------------------------------------------------------------------
# 6. Plugin Store OTA Catalog & Download
# ---------------------------------------------------------------------------

def test_store_catalog_requires_api_key(client):
    """store_catalog action requires valid API key."""
    res = client.get("/api/v1/koreader/lookup?action=store_catalog")
    assert res.status_code == 401


def test_store_catalog_returns_plugin_list(client):
    """Authenticated store_catalog returns a non-empty list of plugins."""
    db = Database()
    raw_key, _ = db.create_api_key("Store Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get("/api/v1/koreader/lookup?action=store_catalog", headers=headers)
    assert res.status_code == 200
    catalog = res.json()
    assert isinstance(catalog, list)
    assert len(catalog) >= 1

    # Verify first plugin has required fields
    plugin = catalog[0]
    required_fields = ["id", "name", "description", "version", "author",
                       "github_repo", "koplugin_name", "category"]
    for field in required_fields:
        assert field in plugin, f"Missing field: {field}"

    # Verify annas-fetch is in catalog
    ids = [p["id"] for p in catalog]
    assert "annas-fetch" in ids


def test_store_download_invalid_id_returns_404(client):
    """store_download with non-existent plugin ID returns 404."""
    db = Database()
    raw_key, _ = db.create_api_key("Store DL Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get(
        "/api/v1/koreader/lookup?action=store_download&id=nonexistent-plugin",
        headers=headers,
    )
    assert res.status_code == 404
    assert "ikke fundet" in res.json()["detail"].lower()


def test_store_catalog_admin_endpoint(auth_client):
    """LAN admin endpoint returns the same catalog."""
    res = auth_client.get("/api/v1/store/catalog")
    assert res.status_code == 200
    catalog = res.json()
    assert isinstance(catalog, list)
    assert len(catalog) >= 1


def test_store_catalog_via_external_tunnel(client):
    """External tunnel request to store_catalog requires API key like other actions."""
    db = Database()
    raw_key, _ = db.create_api_key("Tunnel Store Tester")

    # Without API key through tunnel → 401
    tunnel_headers = {"CF-Connecting-IP": "203.0.113.10"}
    res_unauth = client.get(
        "/api/v1/koreader/lookup?action=store_catalog",
        headers=tunnel_headers,
    )
    assert res_unauth.status_code == 401

    # With API key through tunnel → 200
    tunnel_headers["X-API-Key"] = raw_key
    res_auth = client.get(
        "/api/v1/koreader/lookup?action=store_catalog",
        headers=tunnel_headers,
    )
    assert res_auth.status_code == 200
    assert isinstance(res_auth.json(), list)


def test_store_download_success_mocked(client, monkeypatch):
    """store_download proxies zipball from GitHub successfully."""
    import httpx

    fake_zip_bytes = b"PK\x03\x04fakezipcontent"

    class MockResponse:
        content = fake_zip_bytes
        def raise_for_status(self):
            pass

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url, headers=None):
            return MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    db = Database()
    raw_key, _ = db.create_api_key("Mock DL Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get(
        "/api/v1/koreader/lookup?action=store_download&id=annas-fetch",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.content == fake_zip_bytes
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "annas-fetch"
    assert res.headers["x-koplugin-name"] == "annas-fetch.koplugin"
    assert 'filename="annas-fetch.koplugin.zip"' in res.headers["content-disposition"]


def test_store_download_github_error_returns_502(client, monkeypatch):
    """store_download returns 502 when GitHub request fails."""
    import httpx

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url, headers=None):
            raise httpx.RequestError("GitHub unreachable")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    db = Database()
    raw_key, _ = db.create_api_key("Mock Err Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get(
        "/api/v1/koreader/lookup?action=store_download&id=annas-fetch",
        headers=headers,
    )
    assert res.status_code == 502
    assert "Kunne ikke hente plugin fra GitHub" in res.json()["detail"]


