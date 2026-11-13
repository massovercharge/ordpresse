# Teknisk Implementeringsspecifikation: Anna Fetch Integration & Mirror-Orkestrering via Ordpresse

Denne tekniske implementeringsspecifikation fastlægger arkitekturen, API-kontrakterne og kodeændringerne for integrationen mellem **Ordpresse**-serveren og **Anna Fetch**-pluginnet (`annas-fetch.koplugin`) på KOReader.

---

## 1. Backend-specifikation (`ordpresse`)

### 1.1 API-kontrakt for Mirror Authority

Ordpresse udvides med en dedikeret handling på det eksisterende opslags-endpoint for at centralisere sundhedstjek af spejle og aflaste Kindle-klientens CPU og batteri.

#### Request
```http
GET /api/v1/koreader/lookup?action=annas_mirrors&refresh=false HTTP/1.1
Host: 192.168.50.5:8088
Accept: application/json
```

#### Response (`200 OK`)
Serveren returnerer en prioriteret JSON-struktur, hvor de hurtigste og mest stabile domæner står øverst i både en detaljeret liste og et fladt array til hurtig Lua-parsing:

```json
{
  "status": "ok",
  "updated_at": 1794576000,
  "ttl_seconds": 3600,
  "sources": {
    "libgen": [
      {
        "url": "https://libgen.li",
        "latency_ms": 135,
        "healthy": true
      },
      {
        "url": "https://libgen.is",
        "latency_ms": 210,
        "healthy": true
      }
    ],
    "annas": [
      {
        "url": "https://annas-archive.pk",
        "latency_ms": 310,
        "healthy": true,
        "ddos_guard": true
      },
      {
        "url": "https://annas-archive.gl",
        "latency_ms": 390,
        "healthy": true,
        "ddos_guard": true
      }
    ]
  },
  "flat_mirrors": {
    "libgen": [
      "https://libgen.li",
      "https://libgen.is"
    ],
    "annas": [
      "https://annas-archive.pk",
      "https://annas-archive.gl"
    ]
  }
}
```

---

### 1.2 Mirror Prober og Cache-logik

Et nyt servicemodul i `src/ordpresse/services/mirror_prober.py` håndterer asynkron validering af kandidatdomænerne med følgende valideringsmatrix:

| Kilde | Kandidatdomæner | Tilladte HTTP-koder | Påkrævet signatur | Afvisningsmønster |
| :--- | :--- | :--- | :--- | :--- |
| **Libgen** | `libgen.li`, `libgen.is`, `libgen.rs`, `libgen.gs` | `200` | `index.php` eller `LibGen` | `domain may be for sale` |
| **Anna's Archive** | `annas-archive.pk`, `annas-archive.gl`, `annas-archive.gd` | `200`, `302`, `403` | `Anna's Archive` eller `ddos-guard` | `parked` eller `for sale` |
| **Udfasede spejle** | `annas-archive.li`, `annas-archive.org`, `annas-archive.se` | Ingen | Ingen | Altid afvist ved parkering |

#### Driftsregler for Prober-servicen:
* **Cache-levetid:** Verificerede spejllister caches i hukommelsen (og kan persisteres i SQLite) med en TTL på 3600 sekunder, så klientkald besvares på under 10 ms.
* **Anti-parkering:** Svar med `HTTP 200` scannes for parkeringsfraser, så overtagne domæner som `annas-archive.li` frasorteres øjeblikkeligt.
* **DDoS-Guard-rangering:** Spejle med direkte `HTTP 200` rangeres over spejle, der returnerer `HTTP 302` eller `403` med DDoS-Guard-headers, og sorteres sekundært efter laveste latenstid.
* **Timeouts og backoff:** Hvert prober-kald har en timeout på 3,5 sekunder, og fejlede domæner underlægges eksponentiel backoff (15, 60 og 240 minutter) for at undgå unødig netværkstrafik.

#### Implementering (`src/ordpresse/services/mirror_prober.py`):

```python
import asyncio
import time
from typing import Any
import httpx

LIBGEN_CANDIDATES = [
    "https://libgen.li",
    "https://libgen.is",
    "https://libgen.rs",
    "https://libgen.gs",
]

ANNAS_CANDIDATES = [
    "https://annas-archive.pk",
    "https://annas-archive.gl",
    "https://annas-archive.gd",
    "https://annas-archive.se",
    "https://annas-archive.org",
]

PARKING_MARKERS = (
    "this domain may be for sale",
    "sedoparking",
    "parked-content",
    "buy this domain",
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class MirrorProberService:
    def __init__(self, ttl_seconds: int = 3600, timeout: float = 3.5) -> None:
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, Any] | None = None
        self._last_updated: float = 0.0
        self._lock = asyncio.Lock()
        self._backoff_until: dict[str, float] = {}
        self._fail_counts: dict[str, int] = {}

    def _is_parked(self, body_lower: str) -> bool:
        return any(marker in body_lower for marker in PARKING_MARKERS)

    def _record_failure(self, url: str) -> None:
        count = self._fail_counts.get(url, 0) + 1
        self._fail_counts[url] = count
        backoff_seconds = min(900 * (2 ** (count - 1)), 14400)
        self._backoff_until[url] = time.time() + backoff_seconds

    def _record_success(self, url: str) -> None:
        self._fail_counts.pop(url, None)
        self._backoff_until.pop(url, None)

    async def _probe_libgen(self, client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
        if time.time() < self._backoff_until.get(base_url, 0):
            return None
        start = time.perf_counter()
        try:
            resp = await client.get(f"{base_url}/", follow_redirects=True)
            latency_ms = round((time.perf_counter() - start) * 1000)
            body_lower = resp.text[:8192].lower()
            if resp.status_code == 200 and not self._is_parked(body_lower):
                if "libgen" in body_lower or "index.php" in body_lower or "<form" in body_lower:
                    self._record_success(base_url)
                    return {"url": base_url, "latency_ms": latency_ms, "healthy": True}
        except httpx.HTTPError:
            pass
        self._record_failure(base_url)
        return None

    async def _probe_annas(self, client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
        if time.time() < self._backoff_until.get(base_url, 0):
            return None
        start = time.perf_counter()
        try:
            resp = await client.get(base_url, follow_redirects=False)
            latency_ms = round((time.perf_counter() - start) * 1000)
            body_lower = resp.text[:8192].lower()
            server_hdr = resp.headers.get("server", "").lower()
            is_ddg = "ddos-guard" in server_hdr or any(
                k.startswith("__ddg") for k in resp.cookies.keys()
            )

            if self._is_parked(body_lower):
                self._record_failure(base_url)
                return None

            if resp.status_code == 200 and ("anna" in body_lower or "/search" in body_lower):
                self._record_success(base_url)
                return {
                    "url": base_url,
                    "latency_ms": latency_ms,
                    "healthy": True,
                    "ddos_guard": is_ddg,
                }

            if is_ddg and resp.status_code in (200, 302, 403):
                self._record_success(base_url)
                return {
                    "url": base_url,
                    "latency_ms": latency_ms,
                    "healthy": True,
                    "ddos_guard": True,
                }
        except httpx.HTTPError:
            pass
        self._record_failure(base_url)
        return None

    async def get_mirrors(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cache and (now - self._last_updated) < self.ttl_seconds:
            return self._cache

        async with self._lock:
            if not force_refresh and self._cache and (time.time() - self._last_updated) < self.ttl_seconds:
                return self._cache

            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": BROWSER_UA},
                verify=True,
            ) as client:
                libgen_tasks = [self._probe_libgen(client, u) for u in LIBGEN_CANDIDATES]
                annas_tasks = [self._probe_annas(client, u) for u in ANNAS_CANDIDATES]
                libgen_raw, annas_raw = await asyncio.gather(
                    asyncio.gather(*libgen_tasks),
                    asyncio.gather(*annas_tasks),
                )

            libgen_ok = sorted(
                [r for r in libgen_raw if r is not None],
                key=lambda x: x["latency_ms"],
            )
            annas_ok = sorted(
                [r for r in annas_raw if r is not None],
                key=lambda x: (x.get("ddos_guard", False), x["latency_ms"]),
            )

            if not libgen_ok:
                libgen_ok = [{"url": "https://libgen.li", "latency_ms": 999, "healthy": False}]
            if not annas_ok:
                annas_ok = [{"url": "https://annas-archive.pk", "latency_ms": 999, "healthy": False, "ddos_guard": True}]

            self._last_updated = time.time()
            self._cache = {
                "status": "ok",
                "updated_at": int(self._last_updated),
                "ttl_seconds": self.ttl_seconds,
                "sources": {
                    "libgen": libgen_ok,
                    "annas": annas_ok,
                },
                "flat_mirrors": {
                    "libgen": [m["url"] for m in libgen_ok],
                    "annas": [m["url"] for m in annas_ok],
                },
            }
            return self._cache
```

---

### 1.3 Distribution via Ordpresse Plugin Store

For at undgå afhængighed af det fejlramte upstream-repository på GitHub, pakker og streamer Ordpresse-serveren selv den kuraterede udgave:

* **Katalog-metadata:** Plugin Store-kataloget i `src/ordpresse/web/plugin_store.py` registrerer `annas-fetch` med versionsstrengen `1.2.0-ordpresse`.
* **In-memory pakning:** Serveren læser kildefilerne fra mappen `src/ordpresse/vendor/annas-fetch.koplugin` og genererer et zip-arkiv i hukommelsen med rodmappen `annas-fetch.koplugin/`.
* **Direkte streaming:** Kald til `store_download` med `id=annas-fetch` streamer zip-arkivet direkte til KOReader med korrekt `Content-Disposition`-header.

#### Implementering i `src/ordpresse/web/plugin_store.py`:

```python
import io
import zipfile
from pathlib import Path

VENDOR_ANNAS_DIR = Path(__file__).resolve().parent.parent / "vendor" / "annas-fetch.koplugin"


def build_annas_fetch_zip() -> bytes:
    if not VENDOR_ANNAS_DIR.exists():
        raise FileNotFoundError(f"Missing vendor directory: {VENDOR_ANNAS_DIR}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(VENDOR_ANNAS_DIR.rglob("*")):
            if file_path.is_file():
                rel_path = file_path.relative_to(VENDOR_ANNAS_DIR)
                arcname = str(Path("annas-fetch.koplugin") / rel_path)
                zf.write(file_path, arcname=arcname)
    buf.seek(0)
    return buf.read()


def get_store_catalog_entry(base_endpoint: str) -> dict:
    return {
        "id": "annas-fetch",
        "name": "Anna Fetch (Ordpresse Edition)",
        "version": "1.2.0-ordpresse",
        "description": "Dual-source bogsøgning (Libgen + Anna's Archive) med Ordpresse mirror-orkestrering.",
        "download_url": f"{base_endpoint}?action=store_download&id=annas-fetch",
    }
```

#### Udvidelse af router i `src/ordpresse/web/app.py`:

```python
import io
from fastapi import Query, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from ordpresse.services.mirror_prober import MirrorProberService
from ordpresse.web.plugin_store import build_annas_fetch_zip

mirror_prober = MirrorProberService()


@app.get("/api/v1/koreader/lookup")
async def koreader_lookup(
    action: str | None = Query(default=None),
    id: str | None = Query(default=None),
    refresh: bool = Query(default=False),
):
    if action == "annas_mirrors":
        payload = await mirror_prober.get_mirrors(force_refresh=refresh)
        return JSONResponse(content=payload)

    if action == "store_download" and id == "annas-fetch":
        try:
            zip_bytes = build_annas_fetch_zip()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="annas-fetch.koplugin.zip"'
            },
        )
```

---

## 2. Klient-specifikation (`annas-fetch.koplugin`)

### 2.1 Opdatering af Metadata og OTA-validering

For at løse sti-fejlen ved OTA-opdateringer og koble pluginnet på Ordpresse Plugin Store foretages følgende rettelser i klientkoden:

* **Versionsløft:** Versionsfeltet i `_meta.lua` opdateres til `1.2.0-ordpresse`.
* **Sti-validering:** Regelmønsteret i `annas/ota.lua` (linje 268) rettes, så KOReaders påkrævede suffiks `.koplugin` accepteres.
* **Ordpresse OTA-kilde:** OTA-modulet benytter søskende-opdagelse til at hente zip-pakken fra Ordpresse `store_download`.

#### `_meta.lua`:

```lua
local _ = require("gettext")
return {
    name = "annas-fetch",
    fullname = _("Anna Fetch (Ordpresse)"),
    description = _("Search and download books via Libgen and Anna's Archive with Ordpresse mirror orchestration."),
    version = "1.2.0-ordpresse",
}
```

#### Rettelse i `annas/ota.lua` (omkring linje 268):

```lua
local function getOrdpresseEndpoint()
    local ok, cfg = pcall(dofile, "plugins/ordpresse.koplugin/config.lua")
    if ok and type(cfg) == "table" and cfg.endpoint_url then
        return cfg.endpoint_url
    end
    return nil
end

-- Tillad både 'plugins/annas-fetch' og 'plugins/annas-fetch.koplugin'
if not string.match(plugin_path_from_main, "plugins/annas%-fetch(%.koplugin)?/?$") then
    local err_msg = string.format(
        T("Unsupported plugin path for OTA update: %s. Expected 'plugins/annas-fetch.koplugin'."),
        plugin_path_from_main
    )
    logger.err("Annas:Ota.startUpdateProcess - " .. err_msg)
    _show_ota_final_message(err_msg, true)
    return
end

local ordpresse_url = getOrdpresseEndpoint()
local download_url = ordpresse_url
    and (ordpresse_url .. "?action=store_download&id=annas-fetch")
    or nil
```

---

### 2.2 Søskende-opdagelse og Mirror-Hierarki

Klienten lokaliserer automatisk Ordpresse-serveren via nabo-pluginnet og opretholder en lokal cache til brug uden for hjemmenetværket:

1. **Zero-config opslag:** Funktionen `getOrdpresseEndpoint()` indlæser `endpoint_url` fra `plugins/ordpresse.koplugin/config.lua` via `pcall` uden krav om brugerindtastning.
2. **Lokal cache-fil:** Ved succesfuldt svar fra Ordpresse-serveren gemmes spejllisten i `plugins/annas-fetch.koplugin/mirrors_cache.json` med en lokal levetid på 1 time (`3600` sekunder).
3. **Fallback-kæde:** Hvis Kindle-enheden er uden for hjemmenetværket (timeout på 2 sekunder mod lokal LAN-IP), indlæses først `mirrors_cache.json` og dernæst en indlejret statisk domæneliste.

---

### 2.3 Dual-Source Scraping og Cookie-Håndtering (`src/scraper.lua`)

Filen `src/scraper.lua` omskrives til at udnytte Kindle-enhedens opgraderede `curl 8.17.0` med `OpenSSL 3.5.4` og dual-source scraping:

* **Netværkskald:** Alle `curl`-kald afvikles med desktop `User-Agent` samt cookie-jar-flaggene `-c` og `-b` mod `/tmp/annas_cookies.txt` for at bevare DDoS-Guard-sessioner ved redirects.
* **Primær Libgen-scraper:** Søgninger udføres først mod aktive Libgen-spejle, som leverer rå HTML uden JavaScript-challenges og direkte download-links via `get.php`.
* **Sekundær Anna-scraper:** Kun hvis Libgen returnerer 0 hits eller fejler på alle spejle, falder scraperen tilbage til Anna's Archive-spejlene.

#### Komplet implementering af `src/scraper.lua`:

```lua
local json = require("json")
local logger = require("logger")

local Scraper = {}

local COOKIE_JAR = "/tmp/annas_cookies.txt"
local CACHE_FILE = "plugins/annas-fetch.koplugin/mirrors_cache.json"
local CACHE_TTL = 3600

local USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

local DEFAULT_MIRRORS = {
    libgen = {
        "https://libgen.li",
        "https://libgen.is",
        "https://libgen.rs",
    },
    annas = {
        "https://annas-archive.pk",
        "https://annas-archive.gl",
        "https://annas-archive.gd",
    },
}

local function shell_quote(str)
    return "'" .. tostring(str):gsub("'", "'\\''") .. "'"
end

local function url_encode(str)
    if not str then return "" end
    str = string.gsub(str, "\n", "\r\n")
    str = string.gsub(str, "([^%w %-%_%.%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
    return string.gsub(str, " ", "+")
end

function Scraper.getOrdpresseEndpoint()
    local ok, cfg = pcall(dofile, "plugins/ordpresse.koplugin/config.lua")
    if ok and type(cfg) == "table" and cfg.endpoint_url then
        return cfg.endpoint_url
    end
    return nil
end

function Scraper.execCurl(url, max_time)
    local timeout = max_time or 20
    local cmd = string.format(
        "curl -L -s --max-time %d -A %s -c %s -b %s %s 2>&1",
        timeout,
        shell_quote(USER_AGENT),
        shell_quote(COOKIE_JAR),
        shell_quote(COOKIE_JAR),
        shell_quote(url)
    )
    local handle = io.popen(cmd, "r")
    if not handle then
        return nil
    end
    local output = handle:read("*a")
    handle:close()
    if not output or #output == 0 then
        return nil
    end
    return output
end

local function readLocalMirrorCache()
    local f = io.open(CACHE_FILE, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    local ok, data = pcall(json.decode, content)
    if ok and type(data) == "table" and data.flat_mirrors then
        return data
    end
    return nil
end

local function writeLocalMirrorCache(data)
    local f = io.open(CACHE_FILE, "w")
    if not f then return end
    local ok, encoded = pcall(json.encode, data)
    if ok then
        f:write(encoded)
    end
    f:close()
end

function Scraper.getActiveMirrors()
    local cached = readLocalMirrorCache()
    local now = os.time()
    if cached and cached.local_saved_at and (now - cached.local_saved_at) < CACHE_TTL then
        return cached.flat_mirrors
    end

    local endpoint = Scraper.getOrdpresseEndpoint()
    if endpoint then
        local sep = string.find(endpoint, "?", 1, true) and "&" or "?"
        local api_url = endpoint .. sep .. "action=annas_mirrors"
        local raw = Scraper.execCurl(api_url, 2)
        if raw then
            local ok, payload = pcall(json.decode, raw)
            if ok and type(payload) == "table" and payload.flat_mirrors then
                payload.local_saved_at = now
                writeLocalMirrorCache(payload)
                return payload.flat_mirrors
            end
        end
    end

    if cached and cached.flat_mirrors then
        logger.info("AnnasFetch: Ordpresse unreachable, using offline mirror cache")
        return cached.flat_mirrors
    end

    logger.warn("AnnasFetch: Using hardcoded fallback mirrors")
    return DEFAULT_MIRRORS
end

function Scraper.parseLibgenHtml(html, base_url)
    local results = {}
    if not html or string.find(html:lower(), "this domain may be for sale", 1, true) then
        return results
    end

    for row in string.gmatch(html, "<tr[^>]*>(.-)</tr>") do
        local md5 = string.match(row, "md5=([0-9a-fA-F]+)")
        if md5 and #md5 == 32 then
            local title = string.match(row, "<a[^>]-title=[^>]->(.-)</a>")
                or string.match(row, "edition%.php[^>]->(.-)</a>")
                or "Unknown Title"
            title = title:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")

            local ext = string.match(row, "<td>%s*(epub)%s*</td>")
                or string.match(row, "<td>%s*(pdf)%s*</td>")
                or string.match(row, "<td>%s*(mobi)%s*</td>")
                or "epub"

            local dl_path = string.match(row, 'href="(get%.php%?md5=[0-9a-fA-F]+[^"]*)"')
            local dl_url = dl_path and (base_url .. "/" .. dl_path:gsub("^/", ""))
                or (base_url .. "/ads.php?md5=" .. md5)

            if #title > 0 then
                table.insert(results, {
                    title = title,
                    md5 = md5:lower(),
                    extension = ext:lower(),
                    source = "libgen",
                    mirror = base_url,
                    download_page = dl_url,
                })
            end
        end
    end
    return results
end

function Scraper.parseAnnasHtml(html, base_url)
    local results = {}
    if not html then return results end
    local lower = html:lower()
    if string.find(lower, "this domain may be for sale", 1, true)
        or string.find(lower, "ddos-guard/js-challenge", 1, true) then
        return results
    end

    local split_pattern = "pt%-3 pb%-3 border%-b last:border%-b%-0 border%-gray%-100"
    for block in string.gmatch(html, split_pattern .. "(.-)<div class=\"pt%-3") do
        local md5 = string.match(block, "/md5/([0-9a-fA-F]+)")
        local title = string.match(block, "<h3[^>]*>(.-)</h3>")
        if md5 and title then
            title = title:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
            table.insert(results, {
                title = title,
                md5 = md5:lower(),
                extension = string.match(block, "%.(epub)") or string.match(block, "%.(pdf)") or "epub",
                source = "annas",
                mirror = base_url,
                download_page = base_url .. "/md5/" .. md5:lower(),
            })
        end
    end
    return results
end

function Scraper.search(query)
    local mirrors = Scraper.getActiveMirrors()
    local encoded_q = url_encode(query)

    for _, base_url in ipairs(mirrors.libgen or DEFAULT_MIRRORS.libgen) do
        local search_url = string.format("%s/index.php?req=%s&res=25", base_url, encoded_q)
        local html = Scraper.execCurl(search_url, 15)
        local hits = Scraper.parseLibgenHtml(html, base_url)
        if #hits > 0 then
            logger.info("AnnasFetch: Found " .. #hits .. " hits via Libgen mirror " .. base_url)
            return hits
        end
    end

    for _, base_url in ipairs(mirrors.annas or DEFAULT_MIRRORS.annas) do
        local search_url = string.format("%s/search?q=%s", base_url, encoded_q)
        local html = Scraper.execCurl(search_url, 20)
        local hits = Scraper.parseAnnasHtml(html, base_url)
        if #hits > 0 then
            logger.info("AnnasFetch: Found " .. #hits .. " hits via Anna mirror " .. base_url)
            return hits
        end
    end

    return {}
end

return Scraper
```

---

## 3. Verifikations- og Testplan

### 3.1 Automatiserede Backend-tests (`tests/test_annas_integration.py`)

Følgende test-suite verificerer API-kontrakten, frasortering af parkerede domæner, cache-adfærd og zip-pakning i `pytest`:

```python
import io
import zipfile
import httpx
import pytest
from ordpresse.services.mirror_prober import MirrorProberService
from ordpresse.web.plugin_store import build_annas_fetch_zip


@pytest.mark.asyncio
async def test_mirror_prober_filters_parked_and_ranks_healthy(monkeypatch):
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

    result = await prober.get_mirrors(force_refresh=True)
    assert result["status"] == "ok"
    assert "https://libgen.li" in result["flat_mirrors"]["libgen"]
    assert "https://annas-archive.pk" in result["flat_mirrors"]["annas"]
    assert "https://annas-archive.li" not in result["flat_mirrors"]["annas"]
    assert "https://annas-archive.org" not in result["flat_mirrors"]["annas"]


def test_store_download_builds_valid_koplugin_zip(tmp_path, monkeypatch):
    vendor_dir = tmp_path / "annas-fetch.koplugin"
    (vendor_dir / "annas").mkdir(parents=True)
    (vendor_dir / "src").mkdir(parents=True)
    (vendor_dir / "_meta.lua").write_text('return { version = "1.2.0-ordpresse" }')
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
        assert "1.2.0-ordpresse" in meta_content
```

---

### 3.2 End-to-End Verifikation på KOReader

End-to-end testen gennemføres på Kindle Paperwhite i følgende sekvens:

1. **Plugin-installation:** Åbn Ordpresse Plugin Butik i KOReader, vælg *Anna Fetch (1.2.0-ordpresse)*, installer pakken, og genstart KOReader.
2. **OTA-stivalidering:** Åbn Anna Fetch-menuen, tryk på *Check for updates*, og verificer at stien `plugins/annas-fetch.koplugin` valideres uden fejlmeddelelse.
3. **LAN-søgning:** Søg efter `"frankenstein"` på hjemmenetværket, og bekræft at resultatlisten vises på under 3 sekunder via Libgen, samt at filen `mirrors_cache.json` oprettes i plugin-mappen.
4. **Offline-resiliens:** Afbryd forbindelsen til Ordpresse-serveren (eller skift til mobilt hotspot), gentag søgningen på `"frankenstein"`, og bekræft at pluginnet falder tilbage til `mirrors_cache.json` efter 2 sekunder.
5. **EPUB-download:** Vælg en EPUB-udgave af *Frankenstein* fra søgeresultaterne, download bogen, og kontroller at filen åbner korrekt i KOReader.