"""Automated tests for V10.2 Zero-Trust Two-Zone KOReader integration & API key provisioning."""

import io
import json
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
    from ordpresse.web.plugin_builder import PLUGIN_VERSION
    assert prov_res.headers.get("x-plugin-version") == PLUGIN_VERSION

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


def test_index_page_displays_plugin_and_server_versions(auth_client):
    """Verify dashboard displays live PLUGIN_VERSION and __version__ rather than hardcoded v10.2."""
    import html
    from ordpresse import __version__
    from ordpresse.web.plugin_builder import PLUGIN_RELEASE_NOTES, PLUGIN_VERSION

    res = auth_client.get("/")
    assert res.status_code == 200
    assert f"Server v{__version__}" in res.text
    assert f"Plugin v{PLUGIN_VERSION}" in res.text
    assert f".koplugin.zip (v{PLUGIN_VERSION})" in res.text
    assert html.escape(PLUGIN_RELEASE_NOTES, quote=False) in res.text
    assert "Zero-Trust v10.2" not in res.text


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
    from ordpresse.web.plugin_store import StorePlugin

    fake_plugin = StorePlugin(
        id="dummy-github",
        name="Dummy",
        description="Dummy",
        version="1.0.0",
        author="dummy",
        github_repo="dummy/dummy",
        koplugin_name="dummy.koplugin",
        zipball_url="https://api.github.com/repos/dummy/dummy/zipball/v1.0.0",
    )
    monkeypatch.setattr("ordpresse.web.app.get_plugin_by_id", lambda pid: fake_plugin if pid == "dummy-github" else None)

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
        "/api/v1/koreader/lookup?action=store_download&id=dummy-github",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.content == fake_zip_bytes
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "dummy-github"
    assert res.headers["x-koplugin-name"] == "dummy.koplugin"
    assert 'filename="dummy.koplugin.zip"' in res.headers["content-disposition"]


def test_store_download_github_error_returns_502(client, monkeypatch):
    """store_download returns 502 when GitHub request fails."""
    import httpx
    from ordpresse.web.plugin_store import StorePlugin

    fake_plugin = StorePlugin(
        id="dummy-github",
        name="Dummy",
        description="Dummy",
        version="1.0.0",
        author="dummy",
        github_repo="dummy/dummy",
        koplugin_name="dummy.koplugin",
        zipball_url="https://api.github.com/repos/dummy/dummy/zipball/v1.0.0",
    )
    monkeypatch.setattr("ordpresse.web.app.get_plugin_by_id", lambda pid: fake_plugin if pid == "dummy-github" else None)

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
        "/api/v1/koreader/lookup?action=store_download&id=dummy-github",
        headers=headers,
    )
    assert res.status_code == 502
    assert "Kunne ikke hente plugin fra GitHub" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 7. Live ordnet.dk Scraping, Multi-Homograph Merging, and De-truncation
# ---------------------------------------------------------------------------

def test_ddo_scraper_parse_inflected_kapellanen():
    """Verify DDO scraper resolves inflected query 'kapellanen' -> 'kapellan' with IPA and inflections."""
    from ordpresse.sources.ddo_scraper import DDOScraper

    html = """
    <div class="artikel modern-article">
      <h1 class="modern-match">kapellan</h1>
      <span class="allow-glossing"><span class="part-of-speech">substantiv</span>, fælleskøn</span>
      <div id="id-udt"><span class="lydskrift">[ˈkʰɑb̥əˌlɑn]</span></div>
      <div id="id-boj">
        <table class="flex-table">
          <tr>
            <td>kapellan<span class="mark-flex">en</span></td>
            <td>kapellan<span class="mark-flex">er</span></td>
            <td>kapellan<span class="mark-flex">erne</span></td>
          </tr>
        </table>
      </div>
      <div class="modern-sense-block">
        <div class="modern-definition-box">
          <span class="modern-inline-label">SPROGBRUG</span>
          <span class="text-normal">historisk</span>
          <span class="dividerDot"></span>
          præst som er ansat som hjælper for en sognepræst
        </div>
        <div class="modern-citat">
          »han var kapellan i sognet« <span class="modern-source">Kilde</span>
        </div>
      </div>
    </div>
    """
    scraper = DDOScraper()
    res = scraper.parse_ddo_html("kapellanen", html)
    assert res is not None
    assert res["headword"] == "kapellan"
    assert res["phonetic"] == "ˈkʰɑb̥əˌlɑn"
    can = res["canonical_entry"]
    assert can.pos == "sb. fk."
    assert "kapellanen" in can.inflections
    assert "kapellaner" in can.inflections
    assert len(can.modern_senses) >= 1
    s0 = can.modern_senses[0]
    assert "præst som er ansat som hjælper" in s0.definition
    assert s0.label == "historisk"
    assert s0.quote == "»han var kapellan i sognet«"


def test_ddo_scraper_parse_griller_multi_articles():
    """Verify DDO scraper collects sibling homographs / inflected matching articles on same page."""
    from ordpresse.sources.ddo_scraper import DDOScraper

    html = """
    <div>
      <div class="artikel">
        <h1 class="modern-match">1. grill</h1>
        <span class="allow-glossing"><span class="part-of-speech">substantiv</span>, fælleskøn</span>
        <div id="id-boj"><table class="flex-table"><tr><td>grill<span class="mark-flex">er</span></td></tr></table></div>
        <div class="modern-sense-block"><div class="modern-definition-box">apparat til at grille mad på</div></div>
      </div>
      <div class="artikel">
        <h1 class="modern-match">2. grille</h1>
        <span class="allow-glossing"><span class="part-of-speech">verbum</span></span>
        <div id="id-boj"><table class="flex-table"><tr><td>grill<span class="mark-flex">er</span></td></tr></table></div>
        <div class="modern-sense-block"><div class="modern-definition-box">tilberede kød eller grøntsager på en grill</div></div>
      </div>
    </div>
    """
    scraper = DDOScraper()
    res = scraper.parse_ddo_html("griller", html)
    assert res is not None
    assert res["headword"] == "grill"
    assert len(res["additional_entries"]) >= 1
    assert res["additional_entries"][0].headword == "grille"


def test_ods_scraper_skips_hyphen_prefix_and_merges_mare_homographs():
    """Verify ODS scraper skips prefix 'Mare-' and parses authentic homographs without etymology stubs."""
    from ordpresse.sources.ods_scraper import ODSScraper

    html = """
    <div>
      <div class="artikel">
        <span class="match">Mare-</span>
        <div class="semem"><span class="definition">i sms. som Marehalm, Marelok.</span></div>
      </div>
      <div class="artikel">
        <span class="match">I. Mare,</span>
        <span class="allow-glossing">en.</span>
        <div class="semem">
          <span class="definition">ondt kvindeligt væsen der i folketroen plager sovende mennesker og dyr ved at ride på deres bryst.</span>
          * Blicher. »Maren red ham«
        </div>
      </div>
      <div class="artikel">
        <span class="match">II. mare,</span>
        <span class="allow-glossing">en.</span>
        <div class="semem">
          <span class="definition">af Marie. kvindenavn.</span>
        </div>
      </div>
    </div>
    """
    scraper = ODSScraper()
    res = scraper.parse_ods_html("mare", html)
    assert res is not None
    assert res["headword"] == "mare"
    can = res["canonical_entry"]
    # Mare- prefix must NOT be headword
    assert not can.headword.endswith("-")
    # Authentic sense must be present
    assert any("ondt kvindeligt væsen" in s.definition for s in can.historical_senses)
    # Bare etymology fragment "af Marie." must be filtered
    assert not any(s.definition.strip() == "af Marie." for s in can.historical_senses)


def test_koreader_lookup_always_calls_live_first_and_replaces_truncated_cache(client, monkeypatch):
    """
    Core User Requirement:
    Every lookup through KOReader must ALWAYS query ordnet.dk live first,
    never short-circuiting to stale/truncated entries, and save the full result to local cache.
    """
    from ordpresse.formatter import render_canonical_html
    from ordpresse.models import CanonicalEntry, SenseItem
    from ordpresse.sources.ddo_scraper import DDOScraper
    from ordpresse.sources.ods_scraper import ODSScraper

    db = Database()
    raw_key, _ = db.create_api_key("Live Priority Tester")
    headers = {"X-API-Key": raw_key}

    # 1. Pre-seed online_lookup_cache with a truncated StarDict entry (with '…')
    stale_payload = {
        "query": "kapellanen",
        "resolved_lemma": "kapellan",
        "display_text": "kapellan (sb.)\n  • præst som er ansat som…\n  Kilde: StarDict",
        "entries": [{
            "headword": "kapellan",
            "homonym_index": 1,
            "part_of_speech": "sb.",
            "inflections": "kapellan",
            "phonetic": "",
            "definitions": ["1. præst som er ansat som…"],
            "etymology": "",
        }],
    }
    db.set_online_cache("kapellanen", "FOUND", json.dumps(stale_payload, ensure_ascii=False))

    # Also seed a corrupted row in definitions table
    db.set_cached(
        word="kapellanen",
        source="stardict",
        headword="kapellan",
        html_content="<div><b>kapellan</b> <i>substantiv</i><ol><li>præst som er ansat som…</li></ol></div>",
        synonyms=["kapellanen"],
    )

    # 2. Mock live scrapers returning rich, untruncated DDO and ODS articles
    live_ddo_called = []
    live_ods_called = []

    def mock_scrape_ddo(self, word, max_senses=8):
        live_ddo_called.append(word)
        can = CanonicalEntry(
            headword="kapellan",
            pos="sb. fk.",
            inflections={"kapellan", "kapellanen", "kapellaner", "kapellanerne"},
            modern_senses=[
                SenseItem(
                    definition="præst som er ansat som hjælper for en sognepræst i folkekirken",
                    label="historisk",
                    quote="han virkede som kapellan",
                    quote_author="Pastoratet",
                    source_tag="DDO",
                )
            ],
            sources_used=["DDO"],
            phonetic="ˈkʰɑb̥əˌlɑn",
        )
        return {
            "headword": "kapellan",
            "html_content": render_canonical_html(can),
            "synonyms": list(can.inflections),
            "canonical_entry": can,
            "phonetic": "ˈkʰɑb̥əˌlɑn",
            "source": "ddo",
        }

    def mock_scrape_ods(self, word, max_senses=8):
        live_ods_called.append(word)
        can = CanonicalEntry(
            headword="kapellan",
            pos="sb. fk.",
            historical_senses=[
                SenseItem(
                    definition="hjælpepræst; præst i et embede af anden rang under en sognepræst",
                    label="hist.",
                    quote="i Kapellanens Gaard",
                    quote_author="Blicher",
                    source_tag="ODS",
                )
            ],
            sources_used=["ODS"],
        )
        return {
            "headword": "kapellan",
            "html_content": render_canonical_html(can),
            "synonyms": ["kapellan", "kapellanen"],
            "canonical_entry": can,
            "source": "ods",
        }

    monkeypatch.setattr(DDOScraper, "scrape_ddo", mock_scrape_ddo)
    monkeypatch.setattr(ODSScraper, "scrape_ods", mock_scrape_ods)

    # 3. Perform lookup
    res = client.get("/api/v1/koreader/lookup?q=kapellanen", headers=headers)
    assert res.status_code == 200
    data = res.json()

    # 4. Verify live scrapers WERE called (never short-circuited to truncated cache!)
    assert len(live_ddo_called) >= 1
    assert "kapellanen" in live_ddo_called
    assert len(live_ods_called) >= 1
    assert "kapellanen" in live_ods_called

    # 5. Verify response contains untruncated DDO + ODS merged data
    assert data["query"] == "kapellanen"
    assert data["resolved_lemma"] == "kapellan"
    assert "præst som er ansat som hjælper for en sognepræst i folkekirken" in data["display_text"]
    assert "hjælpepræst; præst i et embede af anden rang" in data["display_text"]
    assert "…" not in data["display_text"]
    assert "StarDict" not in data["display_text"]
    assert "DDO" in data["display_text"]
    assert "ODS" in data["display_text"]
    assert "[ˈkʰɑb̥əˌlɑn]" in data["display_text"]
    assert data["entries"][0]["phonetic"] == "ˈkʰɑb̥əˌlɑn"

    # 6. Verify result was written into local database cache (both definitions & online_lookup_cache)
    cached_def = db.get_cached("kapellan")
    assert cached_def is not None
    assert "præst som er ansat som hjælper" in cached_def["html_content"]
    assert "hjælpepræst" in cached_def["html_content"]

    # Querying inflected form 'kapellanen' in DB also resolves to 'kapellan' via synonyms_json
    cached_inf = db.get_cached("kapellanen")
    assert cached_inf is not None
    assert cached_inf["headword"] == "kapellan"

    # online_lookup_cache updated with untruncated JSON
    online_row = db.get_online_cache("kapellanen")
    assert online_row is not None
    assert online_row["status"] == "FOUND"
    assert "præst som er ansat som hjælper" in online_row["payload_json"]
    assert "…" not in online_row["payload_json"]


def test_koreader_lookup_offline_fallback_skips_truncated_cache(client, monkeypatch):
    """When live scrapers are offline, lookup must ignore truncated cached rows and use clean fallbacks."""
    from ordpresse.sources.ddo_scraper import DDOScraper
    from ordpresse.sources.ods_scraper import ODSScraper

    db = Database()
    raw_key, _ = db.create_api_key("Fallback Tester")
    headers = {"X-API-Key": raw_key}

    # Pre-seed online_lookup_cache with a truncated row
    db.set_online_cache(
        "lægdslemmer",
        "FOUND",
        json.dumps({
            "query": "lægdslemmer",
            "resolved_lemma": "lægdslem",
            "display_text": "lægdslem (sb.)\n  • Fattiglem tildelt et…\n  Kilde: StarDict",
            "entries": [],
        }, ensure_ascii=False),
    )

    # Scrapers return None (simulating offline / network failure)
    monkeypatch.setattr(DDOScraper, "scrape_ddo", lambda self, w, max_senses=8: None)
    monkeypatch.setattr(ODSScraper, "scrape_ods", lambda self, w, max_senses=8: None)

    res = client.get("/api/v1/koreader/lookup?q=lægdslemmer", headers=headers)
    assert res.status_code == 200
    data = res.json()

    # Must resolve via clean Nordic 1800s entry, not the truncated row
    assert data["resolved_lemma"] == "lægdslem"
    assert "…" not in data["display_text"]
    assert "Fattiglem el. almisselem på landet" in data["display_text"]



