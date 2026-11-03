# AGENT_SPEC_V10_2_ZERO_TRUST_KOREADER_API.md — Strict Two-Zone Boundary + LAN Login Screen + Multi-Client `.koplugin.zip` Provisioning
**Target Project:** `ordpresse` (Den Danske Ordpresse — `192.168.50.5` LAN / `ordpresse.seame.click` Public API)
**Target Agent:** Gemini 3.8 Flash

## ⚠️ STATUS QUO & ARCHITECTURAL GUARDRAILS (READ FIRST)
The V9 Web UI, pedagogical build log, `README.md`, and V8.1 StarDict engine (`36,963` headwords, `121,856` synonyms) are **already implemented and are status quo**.
1. **DO NOT modify or revert any V9 Web UI features, build log logic, or StarDict compiler logic.**
2. **Defense-in-Depth Security Model (Two-Zone + LAN Login):**
   - **Layer 1 — External Zone (Cloudflare Tunnel / Public Internet):** **ONLY** `GET /api/v1/koreader/lookup` is accessible when a request comes from outside the local network (or carries `CF-Connecting-IP` / `Cf-Ray` headers), and it requires a valid `X-API-Key` header. Any external request to ANY other path (including `/`, `/login`, `/api/v1/keys/*`, build/download routes) must immediately return `HTTP 404 Not Found`.
   - **Layer 2 — Internal Zone (LAN-Only + Login Protected):** When accessed from a local/private IP (LAN), the Web UI (`/`), build/download endpoints, and `/api/v1/keys/*` are available **ONLY** after logging in at `/login` with the password configured in `ORDPRESSE_ADMIN_PASSWORD` (via `.env` or `docker-compose.yml`). This ensures other users on the same LAN cannot access the GUI or generate API keys.
3. **Reuse existing lookup & harvesting functions:** Wire `/api/v1/koreader/lookup` directly into Ordpresse's existing lookup/lemmatization/ODS-enrichment pipeline.
4. **Execute in 4 sequential phases**, running `.venv/bin/pytest -v` after each phase.

---

## PHASE 0: Codebase Discovery
Before writing code, inspect the current V9 codebase to identify:
1. Where the FastAPI `app` and existing routes (`/`, `/api/...`, static files) are defined.
2. How the existing Web UI lookup preview endpoint resolves a query (`q` -> `.syn` / `.idx` / SQLite COR & ODS cache) and how live ODS/NAOB harvesting is invoked.
3. Where the SQLite database file is stored and how connections are opened, as well as `docker-compose.yml` / `.env` configuration.

---

## PHASE 1: Two-Zone Boundary Middleware, LAN Login Gate & SQLite API Key Schema

### 1.1. Layer 1: Strict Two-Zone FastAPI Middleware (`enforce_two_zone_boundary`)
Add an HTTP middleware to the FastAPI app:
1. **Detect External / Tunnel Requests:**
   - A request is **External** if:
     - The header `CF-Connecting-IP` or `Cf-Ray` is present, **OR**
     - `request.client.host` is NOT a private/local IP (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `::1`, or `testclient` in pytest).
2. **Block All Non-Lookup Paths Externally:**
   - If the request is **External** AND `request.url.path` is **NOT** `/api/v1/koreader/lookup`:
     - Immediately return `PlainTextResponse("Not Found", status_code=404)`. (Even `/login` and `/static/*` must return `404` externally so the GUI is 100% invisible from the internet).

### 1.2. Layer 2: LAN Login Screen & Session Cookie (`ORDPRESSE_ADMIN_PASSWORD`)
For requests inside the **Internal Zone (LAN)**, protect the Web UI and all management/build/download endpoints with a lightweight password login screen:
1. **Environment Configuration (`.env` / `docker-compose.yml`):**
   - Read `ORDPRESSE_ADMIN_PASSWORD` from environment (or `.env`). Update `docker-compose.yml` (or `.env.example`) to document `ORDPRESSE_ADMIN_PASSWORD` and `ORDPRESSE_SECRET_KEY`.
   - If `ORDPRESSE_ADMIN_PASSWORD` is not set in env during local dev, default to `"ordpresse"` (and log a startup notice), or allow pytest fixtures to authenticate via `/login` or `ORDPRESSE_DISABLE_AUTH=1`.
2. **Signed Session Cookie (Standard Library `hmac` + `hashlib`):**
   - On successful login, issue an `HttpOnly`, `SameSite=Lax` cookie named `ordpresse_session` containing `f"{issued_ts}.{hmac_sha256_hex}"` (valid for 30 days).
3. **Login & Logout Routes (LAN-Only):**
   - `GET /login`: Renders a clean, dark-themed login card matching Ordpresse's Nordic literary design (`#0d131f` slate background, `#f59e0b` amber button, password input field, and error alert if login failed).
   - `POST /login`: Verifies the submitted password against `ORDPRESSE_ADMIN_PASSWORD` using `hmac.compare_digest`. On match, sets `ordpresse_session` and redirects (`303 See Other`) to `/`. On mismatch, returns `GET /login` with `"Forkert adgangskode"` (`HTTP 401`).
   - `POST /logout` (and `GET /logout`): Clears `ordpresse_session` and redirects to `/login`.
   - Add a subtle `"🔒 Log ud"` button in the top navbar of the Web UI.
4. **LAN Route Protection:**
   - Unauthenticated LAN browser requests to `/` redirect (`302`) to `/login`.
   - Unauthenticated LAN requests to `/api/*` (except `/api/v1/koreader/lookup` which uses `X-API-Key`) return `HTTP 401 Unauthorized`.

### 1.3. SQLite Schema (`api_keys` & `online_lookup_cache`)
Ensure SQLite connections enable `PRAGMA journal_mode=WAL;` and `timeout=15.0`. Initialize two tables:

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,          -- First 8 chars, e.g. "ord_a7f2"
    key_hash TEXT NOT NULL UNIQUE,     -- SHA-256 hex digest of full key (NEVER store raw key!)
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,          -- ISO-8601 UTC timestamp
    revoked_at TEXT,                   -- ISO-8601 UTC timestamp or NULL
    last_used_at TEXT,                 -- ISO-8601 UTC timestamp or NULL
    lookup_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS online_lookup_cache (
    lookup_key TEXT PRIMARY KEY,       -- Normalized lowercase query/lemma
    status TEXT NOT NULL,              -- 'FOUND' or 'NOT_FOUND'
    payload_json TEXT,                 -- Cached JSON response string (NULL if NOT_FOUND)
    created_at INTEGER NOT NULL,       -- Unix epoch seconds
    expires_at INTEGER                 -- NULL for FOUND (permanent), created_at + (30 * 86400) for NOT_FOUND
);
```

### 1.4. API Key Generation & Constant-Time Verification
- **Key generation (`create_api_key(device_name: str) -> tuple[str, dict]`):**
  - Generate `raw_key = f"ord_{secrets.token_hex(24)}"` (52 chars).
  - Compute `key_prefix = raw_key[:8]` and `key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()`.
  - Insert into `api_keys` and return `(raw_key, row_dict)`. The raw key must **never** be written to disk or logs.
- **Key verification (`verify_api_key(raw_key: str) -> dict`):**
  - Read `X-API-Key` header. If missing, not starting with `"ord_"`, or `> 128` chars -> raise `HTTPException(status_code=401, detail="Manglende eller ugyldig API-nøgle.")`.
  - Compute `candidate_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()`.
  - Compare against active keys (`WHERE is_active = 1`) using `hmac.compare_digest(row["key_hash"], candidate_hash)`.
  - If matched: increment `lookup_count = lookup_count + 1` and update `last_used_at = UTC_NOW`, then return the key record.
  - If no match (or key is revoked): raise `HTTPException(status_code=401, detail="Ugyldig eller tilbagekaldt API-nøgle.")`.

---

## PHASE 2: Online Lookup Pipeline & JSON Contract (`GET /api/v1/koreader/lookup`)

Create `GET /api/v1/koreader/lookup?q=...` authenticated strictly via `X-API-Key` (accessible from both LAN and Cloudflare Tunnel):

### 2.1. Input Sanitization (`HTTP 422`)
- Strip whitespace and surrounding punctuation/quotes:
  `cleaned = (q or "").strip().strip('"\'«»„”()[]{}_.,;:!?-–—/\\')`
- If `not cleaned` or `len(cleaned) > 64` or `"\n" in cleaned` or `"\r" in cleaned`:
  - Raise `HTTPException(status_code=422, detail="Søgestrengen skal være mellem 1 og 64 tegn.")`.

### 2.2. Multi-Tier Lookup & Queue-Controlled External Fallback
1. **Orthography & Lemmatization:**
   - Use Ordpresse's existing `.syn` / `.idx` / COR lemmatizer and pre-1948 `aa` -> `å` normalizer (`cleaned.lower().replace("aa", "å")`) to resolve the canonical lemma(s).
2. **Tier 1 — Local StarDict / SQLite Database & Positive Cache:**
   - Check `online_lookup_cache` (`status == 'FOUND'`).
   - Check Ordpresse's local StarDict binary / SQLite database. If found, format into the JSON contract below and return in `< 10 ms`.
3. **Tier 2 — Negative Cache (`NOT_FOUND` with 30-day TTL):**
   - Check `online_lookup_cache` for `status == 'NOT_FOUND'` where `expires_at > int(time.time())`.
   - If hit, return `{"query": cleaned, "resolved_lemma": None, "display_text": f"Ingen resultater fundet for '{cleaned}'.", "entries": [], "cached_not_found": true}` immediately without external HTTP calls.
4. **Tier 3 — Rate-Limited Live Fallback (`ordnet.dk`):**
   - Guard all outgoing external calls with a module-level `asyncio.Lock()` and enforce a minimum interval of `1.5` seconds between external requests.
   - Fetch from `ordnet.dk` using Ordpresse's existing ODS/DDO harvester.
   - **Synergy:** Save newly fetched ODS definitions into Ordpresse's main ODS cache table (so future offline StarDict builds automatically include them!) AND into `online_lookup_cache` (`status = 'FOUND'`).
   - If 0 hits externally, record `status = 'NOT_FOUND'` in `online_lookup_cache` with `expires_at = int(time.time()) + 30 * 86400`.

### 2.3. JSON Response Contract
Return both the structured `entries` array AND a pre-formatted `display_text` string (stripped of HTML tags, with clean line breaks and bullet points ready for KOReader's `TextViewer`):

```json
{
  "query": "lægdslemmer",
  "resolved_lemma": "lægdslem",
  "display_text": "Opslag: lægdslemmer → lægdslem\n\nlægdslem (sb. itk.)\nBøjning: legdelem, legdelemmer, lægdslemmer\n  • Fattiglem tildelt et bestemt lægd til underhold og forsørgelse under den ældre fattiglovgivning.\n  • (ODS / 1700–1950) (hist., landbo.) Fattiglem el. almisselem på landet, der forsørgedes af lægdet, ofte ved omgangsforsørgelse fra gård til gård.\n  [Historisk kontekst]: I 1700- og 1800-tallets Danmark og Sverige (rotehjon) var fattigvæsenet inddelt i lægder eller roder.\n  Kilde: ODS · Molbech · Salmonsens",
  "entries": [
    {
      "headword": "lægdslem",
      "homonym_index": 1,
      "part_of_speech": "sb. itk.",
      "inflections": "legdelem, legdelemmer, lægdslemmer",
      "phonetic": "",
      "definitions": [
        "1. Fattiglem tildelt et bestemt lægd til underhold og forsørgelse under den ældre fattiglovgivning.",
        "2. (ODS / 1700–1950) (hist., landbo.) Fattiglem el. almisselem på landet, der forsørgedes af lægdet, ofte ved omgangsforsørgelse fra gård til gård."
      ],
      "etymology": "I 1700- og 1800-tallets Danmark og Sverige (rotehjon) var fattigvæsenet inddelt i lægder eller roder."
    }
  ]
}
```

---

## PHASE 3: API Key Management UI (LAN + Logged In) & In-Memory `.koplugin.zip` Provisioning

### 3.1. Key Management REST Endpoints (LAN-Only + Session Cookie Required)
1. `GET /api/v1/keys` — Returns JSON list of all keys (`id`, `device_name`, `key_prefix`, `is_active`, `created_at`, `revoked_at`, `last_used_at`, `lookup_count`) ordered by `is_active DESC, id DESC`. Never expose `key_hash`.
2. `POST /api/v1/keys/provision` — Accepts `{"device_name": "Boox Palma 2", "endpoint_url": "https://ordpresse.seame.click/api/v1/koreader/lookup"}`:
   - Validates `device_name` is non-empty.
   - Generates a new raw API key, stores only its `SHA-256` hash in `api_keys`, builds `ordpresse.koplugin.zip` in-memory (`io.BytesIO`), and returns the `.zip` archive (`application/zip`, `Content-Disposition: attachment; filename="ordpresse-koplugin-<device_slug>.zip"`).
3. `POST /api/v1/keys/{key_id}/revoke` — Deactivates a key (`is_active = 0`, `revoked_at = UTC_NOW`) and returns `{"status": "revoked", "id": key_id}`.
4. `DELETE /api/v1/keys/{key_id}` — Permanently deletes a key row from `api_keys`.

### 3.2. Web UI Section: "🔌 KOReader Online-Plugin & Enhedsnøgler"
Add a dedicated, intuitive management card in the Web UI (using async `fetch()` calls so the table updates live without full page reloads):
1. **Provisioning Form:**
   - Input field: **Enhedsnavn** (placeholder: `f.eks. Kindle Paperwhite, Boox Palma 2, Kones Kindle`).
   - Input field: **Offentligt API Endpoint URL** (pre-filled with `https://ordpresse.seame.click/api/v1/koreader/lookup`).
   - Primary Button: **⚡ Generer API-nøgle & Hent `.koplugin.zip`**
     - On click: Submits `POST /api/v1/keys/provision`, triggers the browser download of `ordpresse-koplugin-<device>.zip`, clears the input field, and immediately refreshes the key management table below.
2. **Interactive API Key Management Table:**
   - Columns:
     - **Enhed:** `device_name` (bold)
     - **Nøgle-ID:** Monospace badge showing `key_prefix` + `...` (e.g., `ord_a7f2...`)
     - **Status:** Green badge `● Aktiv` or Red/Muted badge `○ Tilbagekaldt`
     - **Opslag:** `lookup_count` (formatted number)
     - **Sidst brugt:** Formatted timestamp (`last_used_at` or *"Aldrig brugt"*)
     - **Handling:**
       - If active: **`Tilbagekald`** button (calls `POST /api/v1/keys/{id}/revoke`).
       - If revoked: **`Slet`** button (calls `DELETE /api/v1/keys/{id}`).
3. **Collapsible Installation Guide (`<details>`):**
   - *"📖 Sådan installerer du pluginet på din e-læser (Zero-Config)"*:
     1. Udpak den hentede `.zip`-fil.
     2. Kopiér mappen `ordpresse.koplugin` over i `koreader/plugins/` på din e-læser.
     3. Genstart KOReader — enhedens unikke API-nøgle og server-URL er allerede indkodet i `config.lua`.

### 3.3. KOReader Plugin Files Inside `ordpresse.koplugin/`
The generated `.zip` must contain a top-level folder `ordpresse.koplugin/` with:

1. **`ordpresse.koplugin/_meta.lua`:**
```lua
local _ = require("gettext")
return {
    name = "ordpresse",
    fullname = _("Den Danske Ordpresse (Online Opslag)"),
    description = _("Slå danske og historiske 1700-1900-tals ord op via din private Ordpresse-server."),
}
```

2. **`ordpresse.koplugin/config.lua`:**
```lua
return {
    endpoint_url = "<ENDPOINT_URL>",
    api_key = "<RAW_API_KEY>",
    device_name = "<DEVICE_NAME>",
    timeout_seconds = 5,
}
```

3. **`ordpresse.koplugin/main.lua`:**
```lua
local DictQuickLookup = require("ui/widget/dictquicklookup")
local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local TextViewer = require("ui/widget/textviewer")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local json = require("json")
local ltn12 = require("ltn12")
local socket = require("socket")
local http = require("socket.http")
local url = require("socket.url")
local _ = require("gettext")

local plugin_dir = (debug.getinfo(1, "S").source:match("@?(.*/)") or "./")
local ok_cfg, config = pcall(dofile, plugin_dir .. "config.lua")
if not ok_cfg or type(config) ~= "table" then
    config = { endpoint_url = "", api_key = "", timeout_seconds = 5 }
end

local Ordpresse = WidgetContainer:extend{
    name = "ordpresse",
    is_doc_only = false,
}

function Ordpresse:init()
    self:registerHighlightMenu()
    self:hookDictQuickLookup()
end

function Ordpresse:registerHighlightMenu()
    if not self.ui or not self.ui.highlight then return end
    pcall(function()
        self.ui.highlight:addToHighlightDialog("09_ordpresse_online", function(this)
            return {
                text = _("⚡ Slå op i Ordpresse (Online)"),
                enabled = true,
                callback = function()
                    local selected = this.selected_text and this.selected_text.text or ""
                    this:onClose()
                    self:performLookup(selected)
                end,
            }
        end)
    end)
end

function Ordpresse:hookDictQuickLookup()
    if DictQuickLookup._ordpresse_hooked then return end
    DictQuickLookup._ordpresse_hooked = true

    local orig_tweak = DictQuickLookup.tweakButtons
    DictQuickLookup.tweakButtons = function(dict_popup, buttons)
        if orig_tweak then
            pcall(orig_tweak, dict_popup, buttons)
        end
        pcall(function()
            if type(buttons) == "table" then
                table.insert(buttons, 1, {
                    {
                        id = "ordpresse_online_btn",
                        text = _("⚡ Ordpresse Online"),
                        font_bold = true,
                        callback = function()
                            local word = dict_popup.word or dict_popup.lookupword or ""
                            self:performLookup(word)
                        end,
                    }
                })
            end
        end)
    end
end

function Ordpresse:formatFallback(data)
    if not data or type(data.entries) ~= "table" or #data.entries == 0 then
        return _("Ingen resultater fundet i Den Danske Ordpresse for: ") .. tostring(data and data.query or "")
    end
    local lines = {}
    for i, entry in ipairs(data.entries) do
        local header = entry.headword or data.query or ""
        if entry.homonym_index and #data.entries > 1 then
            header = string.format("%d. %s", entry.homonym_index, header)
        end
        if entry.part_of_speech and entry.part_of_speech ~= "" then
            header = header .. " (" .. entry.part_of_speech .. ")"
        end
        table.insert(lines, header)
        if entry.inflections and entry.inflections ~= "" then
            table.insert(lines, "Bøjning: " .. entry.inflections)
        end
        if type(entry.definitions) == "table" then
            for _, def in ipairs(entry.definitions) do
                table.insert(lines, "  • " .. tostring(def))
            end
        end
        if entry.etymology and entry.etymology ~= "" then
            table.insert(lines, "  [Etymologi / Kontekst]: " .. entry.etymology)
        end
        if i < #data.entries then
            table.insert(lines, "\n" .. string.rep("─", 28) .. "\n")
        end
    end
    return table.concat(lines, "\n")
end

function Ordpresse:performLookup(raw_word)
    local word = (raw_word or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if word == "" then
        UIManager:show(InfoMessage:new{ text = _("Intet ord valgt.") })
        return
    end
    NetworkMgr:runWhenOnline(function()
        self:fetchFromServer(word)
    end)
end

function Ordpresse:fetchFromServer(word)
    local req_url = string.format("%s?q=%s", config.endpoint_url, url.escape(word))
    local resp_body = {}
    local timeout = config.timeout_seconds or 5
    socket.http.TIMEOUT = timeout

    local request_fn = http.request
    if req_url:sub(1, 8) == "https://" then
        local ok_https, https = pcall(require, "ssl.https")
        if ok_https and https then
            https.TIMEOUT = timeout
            request_fn = https.request
        end
    end

    local ok, res, status_code = pcall(function()
        return request_fn({
            url = req_url,
            method = "GET",
            headers = {
                ["X-API-Key"] = config.api_key,
                ["Accept"] = "application/json",
                ["User-Agent"] = "KOReader-Ordpresse/1.0",
            },
            sink = ltn12.sink.table(resp_body),
        })
    end)

    if not ok or not res then
        UIManager:show(InfoMessage:new{
            text = _("Kunne ikke kontakte Ordpresse-serveren (timeout eller netværksfejl)."),
        })
        return
    end

    if status_code == 401 then
        UIManager:show(InfoMessage:new{
            text = _("Adgang nægtet (HTTP 401): Enhedens API-nøgle er udløbet eller tilbagekaldt."),
        })
        return
    elseif status_code == 422 then
        UIManager:show(InfoMessage:new{
            text = _("Ugyldigt opslag (for lang markering eller ugyldige tegn)."),
        })
        return
    elseif status_code ~= 200 then
        UIManager:show(InfoMessage:new{
            text = _("Fejl fra Ordpresse-server (HTTP ") .. tostring(status_code) .. ").",
        })
        return
    end

    local decode_ok, parsed = pcall(json.decode, table.concat(resp_body))
    if not decode_ok or type(parsed) ~= "table" then
        UIManager:show(InfoMessage:new{ text = _("Ugyldigt svar fra Ordpresse-serveren.") })
        return
    end

    local text = parsed.display_text
    if not text or text == "" then
        text = self:formatFallback(parsed)
    end

    UIManager:show(TextViewer:new{
        title = _("Den Danske Ordpresse: ") .. word,
        text = text,
    })
end

return Ordpresse
```

---

## PHASE 4: Automated Tests (`tests/test_koreader_online.py`) & Deployment

Create `tests/test_koreader_online.py` verifying:
1. **Layer 1 — Strict Two-Zone Boundary (External / Tunnel Requests):**
   - Simulated tunnel requests (with `CF-Connecting-IP: 203.0.113.10` header) to `/`, `/login`, `/api/v1/keys`, and `/api/v1/keys/provision` ALL return `HTTP 404 Not Found` (even if a valid session cookie is attached!).
   - Simulated tunnel requests to `GET /api/v1/koreader/lookup?q=lægdslemmer` with a valid `X-API-Key` succeed (`HTTP 200`).
2. **Layer 2 — LAN Login Protection:**
   - Unauthenticated LAN request (no `CF-Connecting-IP`, no session cookie) to `GET /` redirects (`302`) to `/login`.
   - Unauthenticated LAN request to `GET /api/v1/keys` or `POST /api/v1/keys/provision` returns `HTTP 401`.
   - `POST /login` on LAN with wrong password returns `401`; with correct `ORDPRESSE_ADMIN_PASSWORD` sets `ordpresse_session` cookie and grants access to `/` and `/api/v1/keys`.
3. **API Key Provisioning & Management Lifecycle:**
   - Authenticated LAN `POST /api/v1/keys/provision` returns a valid `.zip` containing `ordpresse.koplugin/_meta.lua`, `ordpresse.koplugin/main.lua`, and `ordpresse.koplugin/config.lua` with the generated `ord_...` key; database stores only the 64-char SHA-256 hex hash.
   - `GET /api/v1/keys` lists the key with `key_prefix`, `is_active = 1`, `lookup_count = 0`, and never exposes `key_hash`.
   - `POST /api/v1/keys/{id}/revoke` sets `is_active = 0`, and subsequent lookup with that key returns `HTTP 401`.
   - `DELETE /api/v1/keys/{id}` deletes the key record.
4. **Lookup Pipeline & Validation:**
   - Missing or wrong `X-API-Key` -> `HTTP 401`.
   - Empty `q` or `len(q) > 64` -> `HTTP 422`.
   - Punctuation-wrapped query `q="«lægdslemmer!»"` sanitizes to `lægdslemmer`, resolves to `lægdslem`, returns both `display_text` and `entries`, and increments `lookup_count` to `1`.
   - Pre-1948 `q=daabskjole` resolves to `dåbskjole`.
   - Unknown nonsense word is stored in `online_lookup_cache` (`status='NOT_FOUND'`), and a second lookup hits the negative cache immediately.

### Final Deployment Steps
1. Run `.venv/bin/pytest -v` and confirm all existing V9 tests + new V10.2 tests pass.
2. Commit & push, deploy to `192.168.50.5` (user is "root"), ensure `ORDPRESSE_ADMIN_PASSWORD` is configured in `.env` / service config, restart the service, and verify both LAN login (`http://192.168.50.5:<port>/login`) and public API lookup (`https://ordpresse.seame.click/api/v1/koreader/lookup`).