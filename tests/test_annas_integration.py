import asyncio
import io
import zipfile
import httpx
from starlette.testclient import TestClient

from ordpresse.db import Database
from ordpresse.services.mirror_prober import MirrorProberService
from ordpresse.web.app import app
from ordpresse.web.plugin_store import build_annas_fetch_zip


def test_mirror_prober_filters_parked_and_ranks_healthy(monkeypatch):
    prober = MirrorProberService(ttl_seconds=3600, timeout=1.0)

    async def mock_get(self, url: str, **kwargs):
        if "annas-archive.li" in url or "annas-archive.org" in url:
            return httpx.Response(200, text="<html><body>This domain may be for sale!</body></html>")
        if "annas-archive.pk" in url:
            return httpx.Response(
                302,
                headers={"server": "ddos-guard", "set-cookie": "__ddg8_=abc; Path=/"},
                text="",
            )
        if "libgen.li" in url:
            return httpx.Response(200, text="<html><body><form action='index.php'>LibGen</form></body></html>")
        raise httpx.ConnectTimeout("Connection timed out")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = asyncio.run(prober.get_mirrors(force_refresh=True))
    assert result["status"] == "ok"
    assert "https://libgen.li" in result["flat_mirrors"]["libgen"]
    assert "https://annas-archive.pk" in result["flat_mirrors"]["annas"]
    assert "https://annas-archive.li" not in result["flat_mirrors"]["annas"]
    assert "https://annas-archive.org" not in result["flat_mirrors"]["annas"]


def test_store_download_builds_valid_koplugin_zip(tmp_path, monkeypatch):
    vendor_dir = tmp_path / "annas-fetch.koplugin"
    (vendor_dir / "annas").mkdir(parents=True)
    (vendor_dir / "src").mkdir(parents=True)
    (vendor_dir / "_meta.lua").write_text('return { version = "1.2.1-ordpresse" }')
    (vendor_dir / "annas" / "ota.lua").write_text("plugins/annas%-fetch(%.koplugin)?/?$")
    (vendor_dir / "src" / "scraper.lua").write_text("return {}")

    monkeypatch.setattr("ordpresse.web.plugin_store.VENDOR_ANNAS_DIR", vendor_dir)

    zip_bytes = build_annas_fetch_zip()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = zf.namelist()
        assert "annas-fetch.koplugin/_meta.lua" in names
        assert "annas-fetch.koplugin/annas/ota.lua" in names
        assert "annas-fetch.koplugin/src/scraper.lua" in names
        meta_content = zf.read("annas-fetch.koplugin/_meta.lua").decode("utf-8")
        assert "1.2.1-ordpresse" in meta_content


def test_api_annas_mirrors_endpoint(monkeypatch):
    client = TestClient(app)
    db = Database()
    raw_key, _ = db.create_api_key("Annas Mirrors Tester")
    headers = {"X-API-Key": raw_key}

    async def mock_get_mirrors(self, force_refresh=False):
        return {
            "status": "ok",
            "updated_at": 1794576000,
            "ttl_seconds": 3600,
            "sources": {
                "libgen": [{"url": "https://libgen.li", "latency_ms": 120, "healthy": True}],
                "annas": [{"url": "https://annas-archive.pk", "latency_ms": 250, "healthy": True, "ddos_guard": True}],
            },
            "flat_mirrors": {
                "libgen": ["https://libgen.li"],
                "annas": ["https://annas-archive.pk"],
            },
        }

    monkeypatch.setattr(MirrorProberService, "get_mirrors", mock_get_mirrors)

    res = client.get("/api/v1/koreader/lookup?action=annas_mirrors", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["flat_mirrors"]["libgen"] == ["https://libgen.li"]
    assert data["flat_mirrors"]["annas"] == ["https://annas-archive.pk"]


def test_api_store_download_annas_fetch():
    client = TestClient(app)
    db = Database()
    raw_key, _ = db.create_api_key("Annas Download Tester")
    headers = {"X-API-Key": raw_key}

    res = client.get("/api/v1/koreader/lookup?action=store_download&id=annas-fetch", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["x-plugin-id"] == "annas-fetch"
    assert res.headers["x-plugin-version"] == "1.2.1-ordpresse"
    assert 'filename="annas-fetch.koplugin.zip"' in res.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(res.content), "r") as zf:
        names = zf.namelist()
        assert "annas-fetch.koplugin/_meta.lua" in names
        assert "annas-fetch.koplugin/main.lua" in names
        assert "annas-fetch.koplugin/annas/ota.lua" in names
        assert "annas-fetch.koplugin/src/scraper.lua" in names

        meta = zf.read("annas-fetch.koplugin/_meta.lua").decode("utf-8")
        assert "1.2.1-ordpresse" in meta
        ota = zf.read("annas-fetch.koplugin/annas/ota.lua").decode("utf-8")
        assert "plugins/annas%-fetch(%.koplugin)?/?$" in ota
        scraper = zf.read("annas-fetch.koplugin/src/scraper.lua").decode("utf-8")
        assert "getOrdpresseEndpoint" in scraper


def test_api_annas_search_and_ticket_endpoints(monkeypatch):
    client = TestClient(app)
    db = Database()
    raw_key, _ = db.create_api_key("Annas Search Tester")
    headers = {"X-API-Key": raw_key}

    async def mock_search_books(self, query):
        return {
            "status": "ok",
            "query": query,
            "count": 1,
            "results": [
                {
                    "title": "Frankenstein",
                    "author": "Mary Shelley",
                    "format": "epub",
                    "extension": "epub",
                    "md5": "e5e5e721582d86e5b5295b6f78fe4256",
                    "download_page": "https://libgen.li/ads.php?md5=e5e5e721582d86e5b5295b6f78fe4256",
                }
            ],
        }

    async def mock_resolve_ticket(self, md5):
        return {
            "status": "ok",
            "md5": md5,
            "download_url": f"https://libgen.li/get.php?md5={md5}&key=TESTKEY",
        }

    monkeypatch.setattr(MirrorProberService, "search_books", mock_search_books)
    monkeypatch.setattr(MirrorProberService, "resolve_download_ticket", mock_resolve_ticket)

    res_search = client.get("/api/v1/koreader/lookup?action=annas_search&q=frankenstein", headers=headers)
    assert res_search.status_code == 200
    data_search = res_search.json()
    assert data_search["status"] == "ok"
    assert data_search["count"] == 1
    assert data_search["results"][0]["title"] == "Frankenstein"

    res_ticket = client.get("/api/v1/koreader/lookup?action=annas_download_ticket&md5=e5e5e721582d86e5b5295b6f78fe4256", headers=headers)
    assert res_ticket.status_code == 200
    data_ticket = res_ticket.json()
    assert data_ticket["status"] == "ok"
    assert "TESTKEY" in data_ticket["download_url"]
