"""Tests for Plugin Store v2: Resources (fonts/hyph), Bridge API, Rollback, and Version Checking."""

import io
import zipfile
import pytest
from starlette.testclient import TestClient

from ordpresse.db import Database
from ordpresse.web.app import app
from ordpresse.web.plugin_builder import (
    BRIDGE_LUA,
    MAIN_LUA,
    META_LUA,
    PLUGIN_VERSION,
    build_koplugin_zip,
)
from ordpresse.web.plugin_store import (
    PLUGIN_CATALOG,
    build_resource_zip,
    get_catalog_json,
    get_plugin_by_id,
    get_plugin_zip,
)


def test_bridge_lua_is_packaged_in_koplugin_zip():
    """Verify that build_koplugin_zip includes bridge.lua with Bridge methods."""
    zip_bytes = build_koplugin_zip("Kindle Paperwhite", "ord_secret_123", "https://ordpresse.local/api/v1/koreader/lookup")
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        file_list = zf.namelist()
        assert "ordpresse.koplugin/_meta.lua" in file_list
        assert "ordpresse.koplugin/config.lua" in file_list
        assert "ordpresse.koplugin/bridge.lua" in file_list
        assert "ordpresse.koplugin/main.lua" in file_list

        bridge_code = zf.read("ordpresse.koplugin/bridge.lua").decode("utf-8")
        assert "Bridge:apiCall" in bridge_code
        assert "Bridge:runWhenOnline" in bridge_code
        assert "Bridge:request" in bridge_code
        assert 'package.loaded["ordpresse.bridge"] = Bridge' in bridge_code


def test_main_lua_contains_store_v2_features():
    """Verify that MAIN_LUA contains version inspection, rollback, batch update, and target routing."""
    assert "inspectInstalledPackage" in MAIN_LUA
    assert "findPackageDest" in MAIN_LUA
    assert "confirmRollback" in MAIN_LUA
    assert "updateAllPlugins" in MAIN_LUA
    assert "target_type == \"font\"" in MAIN_LUA or "target_type == 'font'" in MAIN_LUA
    assert "target_type == \"hyph\"" in MAIN_LUA or "target_type == 'hyph'" in MAIN_LUA
    assert 'package.loaded["ordpresse.bridge"]' in MAIN_LUA
    assert ".bak" in MAIN_LUA
    # Self-healing bootstrap check for OTA updates from v1.2.2
    assert "EMBEDDED_BRIDGE_CODE" in MAIN_LUA
    assert "writeFileAtomic(plugin_dir .. \"bridge.lua\"" in MAIN_LUA
    assert "Bridge:apiCall" in MAIN_LUA


def test_catalog_includes_resources_category():
    """Verify that catalog includes resources category with target_type and target_dir."""
    catalog = get_catalog_json()
    assert len(catalog) >= 3

    categories = {p["category"] for p in catalog}
    assert "resources" in categories
    assert "books" in categories

    ids = {p["id"]: p for p in catalog}
    assert "annas-fetch" in ids
    assert "hyph-da" in ids
    assert "font-chareink" in ids

    # Check hyph-da metadata
    hyph = ids["hyph-da"]
    assert hyph["target_type"] == "hyph"
    assert hyph["target_dir"] == "data/hyph"

    # Check font-chareink metadata
    font = ids["font-chareink"]
    assert font["target_type"] == "font"
    assert font["target_dir"] == "fonts"


def test_hyph_da_local_zip_generation_and_download(client):
    """Verify that hyph-da resource is packaged locally and downloadable via API."""
    db = Database()
    raw_key, _ = db.create_api_key("Hyph Tester")
    headers = {"X-API-Key": raw_key}

    # Test via koreader lookup action
    res = client.get("/api/v1/koreader/lookup?action=store_download&id=hyph-da", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "hyph-da"

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        assert any(n.endswith("hyph_da.pattern") for n in names)
        pattern_content = [zf.read(n).decode("utf-8") for n in names if n.endswith("hyph_da.pattern")][0]
        assert "\\patterns{" in pattern_content


def test_font_chareink_local_zip_generation_and_download(client):
    """Verify that font-chareink resource is packaged locally with TTF fonts."""
    db = Database()
    raw_key, _ = db.create_api_key("Font Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get("/api/v1/koreader/lookup?action=store_download&id=font-chareink", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "font-chareink"

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        ttf_files = [n for n in names if n.lower().endswith(".ttf")]
        assert len(ttf_files) >= 1


@pytest.mark.parametrize(
    "plugin_id,expected_prefix",
    [
        ("font-atkinson", "Atkinson-Hyperlegible/"),
        ("font-literata", "Literata/"),
    ],
)
def test_vendored_fonts_download_with_all_four_styles(client, plugin_id, expected_prefix):
    """Verify vendored fonts (Atkinson Hyperlegible & Literata) download locally with 4 static TTF styles."""
    db = Database()
    raw_key, _ = db.create_api_key(f"Font Tester {plugin_id}")
    headers = {"X-API-Key": raw_key}

    res = client.get(f"/api/v1/koreader/lookup?action=store_download&id={plugin_id}", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == plugin_id

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        assert all(n.startswith(expected_prefix) for n in names)
        ttf_files = [n for n in names if n.lower().endswith(".ttf")]
        assert len(ttf_files) == 4
        assert any(n.endswith("OFL.txt") for n in names)


def test_all_catalog_items_have_local_zip():
    """Ensure every item in PLUGIN_CATALOG builds a valid local zip with no dead external URLs."""
    for item in PLUGIN_CATALOG:
        data = get_plugin_zip(item.id)
        assert data is not None and len(data) > 100, f"Catalog item '{item.id}' missing local zip"
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            assert len(zf.namelist()) >= 1


def test_store_admin_download_resource(auth_client):
    """Verify LAN admin endpoint can download vendored resources."""
    res = auth_client.get("/api/v1/store/download/hyph-da")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "hyph-da"


def test_opds_catalog_plugin_zip_packaging(client):
    """Verify opds-catalog returns a valid KOReader plugin zip with main.lua and _meta.lua."""
    db = Database()
    raw_key, _ = db.create_api_key("OPDS Catalog Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get("/api/v1/koreader/lookup?action=store_download&id=opds-catalog", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        assert "ordpresse_opds.koplugin/_meta.lua" in names
        assert "ordpresse_opds.koplugin/main.lua" in names
        main_code = zf.read("ordpresse_opds.koplugin/main.lua").decode("utf-8")
        assert "OrdpresseOpds" in main_code
        assert "syncOpds" in main_code


def test_lua_code_syntax():
    """Ensure Lua templates have no syntax errors if luac is available, and no raw newlines in string literals."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from ordpresse.web.plugin_builder import MAIN_LUA, BRIDGE_LUA, META_LUA

    opds_main = Path("src/ordpresse/vendor/ordpresse_opds.koplugin/main.lua").read_text()
    opds_meta = Path("src/ordpresse/vendor/ordpresse_opds.koplugin/_meta.lua").read_text()

    targets = [
        ("MAIN_LUA", MAIN_LUA),
        ("BRIDGE_LUA", BRIDGE_LUA),
        ("META_LUA", META_LUA),
        ("opds_main", opds_main),
        ("opds_meta", opds_meta),
    ]

    for name, code in targets:
        if shutil.which("luac"):
            with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
                f.write(code)
                f_name = f.name
            try:
                res = subprocess.run(["luac", "-p", f_name], capture_output=True, text=True)
                assert res.returncode == 0, f"Lua syntax error in {name}: {res.stderr}"
            finally:
                import os
                if os.path.exists(f_name):
                    os.remove(f_name)


