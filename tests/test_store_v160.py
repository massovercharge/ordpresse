"""Comprehensive tests for Ordpresse Plugin & Patch Store v1.6.0 specification."""

import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import pytest

from ordpresse.db import Database
from ordpresse.web.plugin_builder import BRIDGE_LUA, MAIN_LUA, META_LUA, PLUGIN_VERSION
from ordpresse.web.plugin_store import (
    PLUGIN_CATALOG,
    flatten_plugin_zip,
    get_catalog_json,
    get_patch_bytes,
    get_plugin_zip,
)


def _check_lua_syntax(name: str, code: str) -> None:
    if not shutil.which("luac"):
        return
    with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False, encoding="utf-8") as f:
        f.write(code)
        fname = f.name
    try:
        res = subprocess.run(["luac", "-p", fname], capture_output=True, text=True)
        assert res.returncode == 0, f"Lua syntax error in {name}: {res.stderr}"
    finally:
        Path(fname).unlink(missing_ok=True)


def test_v160_catalog_contains_all_patches_and_plugins():
    """Verify catalog includes all 3 User Patches and all 4 new v1.6.0 plugins with proper metadata."""
    catalog = {item["id"]: item for item in get_catalog_json()}

    expected_patches = {
        "patch-clean-bookshelf": "2-ordpresse-clean-bookshelf.lua",
        "patch-clean-statusbar": "2-ordpresse-clean-statusbar.lua",
        "patch-exlibris-screensaver": "2-ordpresse-exlibris-screensaver.lua",
    }
    for pid, target_name in expected_patches.items():
        assert pid in catalog
        pkg = catalog[pid]
        assert pkg["pkg_type"] == "patch"
        assert pkg["target_type"] == "patch"
        assert pkg["target_dir"] == "patches"
        assert pkg["target_name"] == target_name
        assert pkg["can_uninstall"] is True

    expected_plugins = {
        "ordpresse-kosync": "ordpresse-kosync.koplugin",
        "ordpresse-udklip": "ordpresse-udklip.koplugin",
        "ordpresse-avisen": "ordpresse-avisen.koplugin",
        "project-title": "projecttitle.koplugin",
    }
    for pid, target_name in expected_plugins.items():
        assert pid in catalog
        pkg = catalog[pid]
        assert pkg["pkg_type"] == "plugin"
        assert pkg["target_dir"] == "plugins"
        assert pkg["target_name"] == target_name


def test_v160_client_lifecycle_and_lua_syntax():
    """Verify ordpresse.koplugin v1.6.0 has pause/resume/uninstall/patch support and valid Lua syntax."""
    assert PLUGIN_VERSION == "1.6.0"
    assert "togglePackageEnable" in MAIN_LUA
    assert "uninstallPackage" in MAIN_LUA
    assert ".disabled" in MAIN_LUA
    assert "@version" in MAIN_LUA
    assert 'target_type == "patch"' in MAIN_LUA
    assert "[||] Deaktiver" in MAIN_LUA
    assert "[>] Aktiver" in MAIN_LUA
    assert "[×] Afinstaller" in MAIN_LUA
    assert 'plugin.id == "ordpresse"' in MAIN_LUA

    _check_lua_syntax("MAIN_LUA", MAIN_LUA)
    _check_lua_syntax("BRIDGE_LUA", BRIDGE_LUA)
    _check_lua_syntax("META_LUA", META_LUA)

    for lua_file in sorted(Path("src/ordpresse/vendor/patches").glob("*.lua")):
        _check_lua_syntax(str(lua_file), lua_file.read_text(encoding="utf-8"))

    for pdir in ("ordpresse-kosync.koplugin", "ordpresse-udklip.koplugin", "ordpresse-avisen.koplugin"):
        for lua_file in sorted((Path("src/ordpresse/vendor") / pdir).glob("*.lua")):
            _check_lua_syntax(str(lua_file), lua_file.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "patch_id,expected_filename",
    [
        ("patch-clean-bookshelf", "2-ordpresse-clean-bookshelf.lua"),
        ("patch-clean-statusbar", "2-ordpresse-clean-statusbar.lua"),
        ("patch-exlibris-screensaver", "2-ordpresse-exlibris-screensaver.lua"),
    ],
)
def test_user_patch_download_serves_raw_lua(client, auth_client, patch_id, expected_filename):
    """Verify KOReader and Web UI store endpoints stream valid raw .lua files for patches."""
    db = Database()
    raw_key, _ = db.create_api_key("Kindle Patch Tester")

    res = client.get(
        f"/api/v1/koreader/lookup?action=store_download&id={patch_id}",
        headers={"X-API-Key": raw_key},
    )
    assert res.status_code == 200
    assert res.headers["x-package-type"] == "patch"
    assert expected_filename in res.headers["content-disposition"]
    lua_src = res.text
    assert "@version 1.6.0" in lua_src
    _check_lua_syntax(patch_id, lua_src)

    admin_res = auth_client.get(f"/api/v1/store/download/{patch_id}")
    assert admin_res.status_code == 200
    assert admin_res.content == get_patch_bytes(patch_id)


@pytest.mark.parametrize(
    "plugin_id,expected_dir",
    [
        ("ordpresse-kosync", "ordpresse-kosync.koplugin"),
        ("ordpresse-udklip", "ordpresse-udklip.koplugin"),
        ("ordpresse-avisen", "ordpresse-avisen.koplugin"),
    ],
)
def test_new_v160_plugins_zero_config_packaging(client, plugin_id, expected_dir):
    """Verify Punkt 5 plugins package cleanly with _meta.lua, main.lua, and zero-config sibling sharing."""
    db = Database()
    raw_key, _ = db.create_api_key("Kindle Plugin Tester")

    res = client.get(
        f"/api/v1/koreader/lookup?action=store_download&id={plugin_id}",
        headers={"X-API-Key": raw_key},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        assert f"{expected_dir}/_meta.lua" in names
        assert f"{expected_dir}/main.lua" in names
        main_src = zf.read(f"{expected_dir}/main.lua").decode("utf-8")
        assert "plugins/ordpresse.koplugin/config.lua" in main_src


def test_project_title_zip_flattening():
    """Verify nested GitHub zipball (ProjectTitle-main/projecttitle.koplugin/...) is flattened to root."""
    nested_buf = io.BytesIO()
    with zipfile.ZipFile(nested_buf, "w") as zf:
        zf.writestr("ProjectTitle-2.8.0/README.md", "# Project Title")
        zf.writestr("ProjectTitle-2.8.0/projecttitle.koplugin/_meta.lua", 'return { name = "projecttitle", version = "2.8.0" }')
        zf.writestr("ProjectTitle-2.8.0/projecttitle.koplugin/main.lua", "return {}")
        zf.writestr("ProjectTitle-2.8.0/projecttitle.koplugin/icons/cover.svg", "<svg/>")

    flat_bytes = flatten_plugin_zip(nested_buf.getvalue(), "projecttitle.koplugin")
    with zipfile.ZipFile(io.BytesIO(flat_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "projecttitle.koplugin/main.lua" in names
        assert "projecttitle.koplugin/_meta.lua" in names
        assert "projecttitle.koplugin/icons/cover.svg" in names
        assert not any("ProjectTitle-2.8.0" in n for n in names)

    # Invalid archive without main.lua must raise ValueError
    bad_buf = io.BytesIO()
    with zipfile.ZipFile(bad_buf, "w") as zf:
        zf.writestr("README.md", "no lua here")
    with pytest.raises(ValueError):
        flatten_plugin_zip(bad_buf.getvalue(), "projecttitle.koplugin")


def test_core_plugin_uninstall_blocked_in_api(auth_client):
    """Verify API refuses uninstalling the core 'ordpresse' plugin."""
    res = auth_client.delete("/api/v1/store/packages/ordpresse")
    assert res.status_code == 403

    res2 = auth_client.delete("/api/v1/store/packages/ordpresse.koplugin")
    assert res2.status_code == 403

    res3 = auth_client.delete("/api/v1/store/packages/ordpresse-kosync")
    assert res3.status_code == 200


def test_kosync_reading_progress_push_and_cross_format_pull(client):
    """Verify PUT/GET /api/v1/koreader/sync/progress works by partial_md5 and normalized title+author, including external access."""
    db = Database()
    kindle_key, _ = db.create_api_key("Kindle Paperwhite")
    crosspoint_key, _ = db.create_api_key("CrossPoint Lommelæser")

    # Push from CrossPoint (external Cloudflare header simulated)
    push_res = client.put(
        "/api/v1/koreader/sync/progress",
        headers={
            "X-API-Key": crosspoint_key,
            "CF-Connecting-IP": "198.51.100.10",
        },
        json={
            "partial_md5": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "title": "Lykke-Per",
            "author": "Henrik Pontoppidan",
            "percentage": 64.2,
            "xpointer": "/body/DocFragment[5]/body/p[12]",
            "chapter_index": 5,
            "paragraph_index": 12,
        },
    )
    assert push_res.status_code == 200
    assert push_res.json()["status"] == "ok"

    # Pull from Kindle using exact partial_md5
    pull_md5 = client.get(
        "/api/v1/koreader/sync/progress?md5=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        headers={"X-API-Key": kindle_key, "CF-Connecting-IP": "198.51.100.11"},
    )
    assert pull_md5.status_code == 200
    data = pull_md5.json()
    assert data["found"] is True
    assert data["percentage"] == pytest.approx(64.2)
    assert data["device_name"] == "CrossPoint Lommelæser"

    # Pull from another device with different binary hash, matching by title + author
    pull_norm = client.get(
        "/api/v1/koreader/sync/progress?md5=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb&title=Lykke-Per&author=Henrik+Pontoppidan",
        headers={"X-API-Key": kindle_key},
    )
    assert pull_norm.status_code == 200
    assert pull_norm.json()["found"] is True
    assert pull_norm.json()["percentage"] == pytest.approx(64.2)

    # Unauthorized request must be rejected
    unauth = client.get("/api/v1/koreader/sync/progress?md5=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert unauth.status_code == 401


def test_udklip_highlights_and_vocab_sync(client, auth_client):
    """Verify POST /api/v1/koreader/udklip/sync persists and deduplicates highlights and vocab builder entries."""
    import uuid

    db = Database()
    raw_key, _ = db.create_api_key("Kindle Oasis")
    uniq = uuid.uuid4().hex[:8]
    test_word = f"kummerlig_{uniq}"
    test_quote = f"Mikkel Thøgersen stod ved vinduet ({uniq})."

    payload = {
        "book_title": "Kongens Fald",
        "book_author": "Johannes V. Jensen",
        "annotations": [
            {
                "text": test_quote,
                "note": "Smuk åbning",
                "chapter": "Forårets Død",
                "pageno": 14,
            }
        ],
        "vocab_words": [
            {
                "word": test_word,
                "context": f"det var en [{test_word}] vinter",
                "book_title": "Kongens Fald",
                "created_at": 1700000000,
            }
        ],
    }

    res1 = client.post(
        "/api/v1/koreader/udklip/sync",
        headers={"X-API-Key": raw_key, "CF-Connecting-IP": "203.0.113.5"},
        json=payload,
    )
    assert res1.status_code == 200
    assert res1.json()["highlights_saved"] == 1
    assert res1.json()["vocab_saved"] == 1

    # Re-syncing identical vocab should deduplicate cleanly
    res2 = client.post(
        "/api/v1/koreader/udklip/sync",
        headers={"X-API-Key": raw_key},
        json=payload,
    )
    assert res2.status_code == 200
    assert res2.json()["vocab_saved"] == 0

    # Verify visible in LAN Web UI endpoint
    lan_res = auth_client.get("/api/v1/udklip")
    assert lan_res.status_code == 200
    body = lan_res.json()
    assert any(h["quote_text"] == test_quote for h in body["highlights"])
    assert any(v["word"] == test_word for v in body["vocab_words"])


def test_web_to_epub_articles_and_opds_feed(client, auth_client):
    """Verify saving HTML/text article -> washed EPUB -> /opds/articles feed -> KOReader download & mark read."""
    db = Database()
    raw_key, _ = db.create_api_key("Kindle Avis")

    sample_html = """
    <html>
      <head>
        <title>Lang dansk artikel om bogtrykkerkunst</title>
        <meta name="author" content="Weekendavisen"/>
      </head>
      <body>
        <article>
          <h1>Lang dansk artikel om bogtrykkerkunst</h1>
          <p>I gamle dage sagde bogtrykkerne "god typografi kan ikke ses, den mærkes" når de satte blytyper i København.</p>
          <p>I dag lever traditionen videre på moderne e-ink skærme med knivskarp kontrast og rolig orddeling på dansk.</p>
        </article>
      </body>
    </html>
    """

    save_res = auth_client.post(
        "/api/v1/articles",
        json={"raw_content": sample_html, "url": "https://weekendavisen.example.dk/kultur/bogtryk"},
    )
    assert save_res.status_code == 200
    art = save_res.json()["article"]
    md5 = art["md5"]
    assert art["title"] == "Lang dansk artikel om bogtrykkerkunst"
    assert art["filename"].endswith(".epub")

    # Check KOReader articles API
    ko_list = client.get(
        "/api/v1/koreader/articles?unread_only=true",
        headers={"X-API-Key": raw_key, "CF-Connecting-IP": "198.51.100.22"},
    )
    assert ko_list.status_code == 200
    assert any(a["md5"] == md5 for a in ko_list.json()["articles"])

    # Check /opds/articles OPDS feed (with universal Kindle title — no color emojis!)
    opds_res = client.get(
        f"/opds/articles?api_key={raw_key}",
        headers={"CF-Connecting-IP": "198.51.100.22"},
    )
    assert opds_res.status_code == 200
    feed_xml = opds_res.text
    assert "[Avis] Læs Senere (Artikler)" in feed_xml
    assert "Lang dansk artikel om bogtrykkerkunst" in feed_xml
    assert all(ord(ch) <= 0xFFFF and ord(ch) != 0xFE0F for ch in feed_xml)

    # Download washed EPUB via /opds/articles/download/{md5}
    dl_res = client.get(f"/opds/articles/download/{md5}?api_key={raw_key}")
    assert dl_res.status_code == 200
    assert dl_res.content.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(io.BytesIO(dl_res.content), "r") as zf:
        xhtml_files = [n for n in zf.namelist() if n.endswith(".xhtml") and "article" in n]
        assert xhtml_files
        article_html = zf.read(xhtml_files[0]).decode("utf-8")
        assert "»" in article_html  # Guillemets from EpubLaundryService!

    # Mark article read from KOReader
    mark_res = client.post(
        f"/api/v1/koreader/articles/{md5}/read",
        headers={"X-API-Key": raw_key},
    )
    assert mark_res.status_code == 200
    ko_unread = client.get("/api/v1/koreader/articles?unread_only=true", headers={"X-API-Key": raw_key})
    assert not any(a["md5"] == md5 for a in ko_unread.json()["articles"])

    # Cleanup saved article
    del_res = auth_client.delete(f"/api/v1/articles/{md5}")
    assert del_res.status_code == 200


def test_two_zone_blocks_external_access_to_new_lan_endpoints(client):
    """Security: external Cloudflare requests must get 404 on all new v1.6.0 LAN-only admin routes."""
    ext_headers = {"CF-Connecting-IP": "198.51.100.99"}

    for method, path in [
        ("GET", "/api/v1/articles"),
        ("POST", "/api/v1/articles"),
        ("DELETE", "/api/v1/articles/00000000000000000000000000000000"),
        ("GET", "/api/v1/udklip"),
        ("GET", "/api/v1/sync/progress"),
        ("DELETE", "/api/v1/store/packages/ordpresse"),
    ]:
        res = client.request(method, path, headers=ext_headers)
        assert res.status_code == 404, f"External {method} {path} should return 404, got {res.status_code}"


def test_project_title_release_fetch_flatten_and_cache(client, auth_client, tmp_path, monkeypatch):
    """Verify /releases/latest asset resolution, nested ZIP flattening, and disk cache hit for project-title."""
    import httpx

    monkeypatch.setenv("ORDPRESSE_DATA_DIR", str(tmp_path))

    # Build a realistic doubly-nested ProjectTitle GitHub release zip
    nested_buf = io.BytesIO()
    with zipfile.ZipFile(nested_buf, "w") as zf:
        zf.writestr("ProjectTitle-2.8.0/projecttitle.koplugin/_meta.lua", 'return { name = "projecttitle", version = "2.8.0" }')
        zf.writestr("ProjectTitle-2.8.0/projecttitle.koplugin/main.lua", "return { ok = true }")
    nested_bytes = nested_buf.getvalue()

    calls = []

    class MockResp:
        def __init__(self, json_data=None, content=b""):
            self._json = json_data
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            calls.append(url)
            if "/releases/latest" in url:
                return MockResp(
                    json_data={
                        "assets": [
                            {"name": "projecttitle.koplugin.zip", "browser_download_url": "https://github.com/joshuacant/ProjectTitle/releases/download/v2.8.0/projecttitle.koplugin.zip"}
                        ]
                    }
                )
            return MockResp(content=nested_bytes)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    db = Database()
    raw_key, _ = db.create_api_key("ProjectTitle Tester")

    # First call: fetches from GitHub, flattens, and caches on disk
    res1 = client.get(
        "/api/v1/koreader/lookup?action=store_download&id=project-title",
        headers={"X-API-Key": raw_key},
    )
    assert res1.status_code == 200
    assert len(calls) == 2
    with zipfile.ZipFile(io.BytesIO(res1.content), "r") as zf:
        assert "projecttitle.koplugin/main.lua" in zf.namelist()
        assert "projecttitle.koplugin/_meta.lua" in zf.namelist()

    # Second call via admin download: served straight from local disk cache (0 new HTTP calls!)
    res2 = auth_client.get("/api/v1/store/download/project-title")
    assert res2.status_code == 200
    assert len(calls) == 2
    assert res2.content == res1.content


def test_web_to_epub_url_fetch_plaintext_and_error_paths(client, auth_client, monkeypatch):
    """Verify URL fetching, plain-text paywall paste, LAN listings, OPDS auth/404, and sync error paths."""
    import httpx

    # 1. Empty payload -> 400
    bad_art = auth_client.post("/api/v1/articles", json={"url": "", "raw_content": ""})
    assert bad_art.status_code == 400

    # 2. Plain text article (paywalled paste) -> washed EPUB + listed in GET /api/v1/articles
    plain_res = auth_client.post(
        "/api/v1/articles",
        json={"raw_content": "Min Overskrift\n\nFørste afsnit om \"litteratur\" på dansk.\n\nAndet afsnit."},
    )
    assert plain_res.status_code == 200
    plain_md5 = plain_res.json()["article"]["md5"]
    assert plain_res.json()["article"]["title"] == "Min Overskrift"

    # 3. URL fetch via mocked httpx
    class MockUrlResp:
        text = "<html><head><title>URL Artikel</title></head><body><article><p>En fin artikel hentet fra nettet om bøger og skrift.</p></article></body></html>"

        def raise_for_status(self):
            pass

    class MockUrlClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            return MockUrlResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockUrlClient)
    url_res = auth_client.post("/api/v1/articles", json={"url": "https://example.dk/artikel"})
    assert url_res.status_code == 200
    url_md5 = url_res.json()["article"]["md5"]
    assert url_res.json()["article"]["title"] == "URL Artikel"

    # Verify LAN GET /api/v1/articles & /api/v1/sync/progress
    list_res = auth_client.get("/api/v1/articles")
    assert list_res.status_code == 200
    assert any(a["md5"] == plain_md5 for a in list_res.json()["articles"])

    prog_list = auth_client.get("/api/v1/sync/progress")
    assert prog_list.status_code == 200
    assert "progress" in prog_list.json()

    # Cleanup articles
    auth_client.delete(f"/api/v1/articles/{plain_md5}")
    auth_client.delete(f"/api/v1/articles/{url_md5}")

    # 4. OPDS articles auth & error codes
    assert client.get("/opds/articles").status_code == 401
    db = Database()
    raw_key, _ = db.create_api_key("Error Path Tester")
    assert client.get(f"/opds/articles/download/not-an-md5?api_key={raw_key}").status_code == 400
    assert client.get(f"/opds/articles/download/00000000000000000000000000000000?api_key={raw_key}").status_code == 404

    # 5. KOReader sync & article error paths
    assert client.put("/api/v1/koreader/sync/progress", headers={"X-API-Key": raw_key}, json={}).status_code == 400
    not_found = client.get("/api/v1/koreader/sync/progress?md5=ffffffffffffffffffffffffffffffff", headers={"X-API-Key": raw_key})
    assert not_found.status_code == 200
    assert not_found.json() == {"found": False}
    assert client.post("/api/v1/koreader/articles/00000000000000000000000000000000/read", headers={"X-API-Key": raw_key}).status_code == 404
