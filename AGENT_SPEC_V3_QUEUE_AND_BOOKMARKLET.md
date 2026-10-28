# AGENT_SPEC_V3_QUEUE_AND_BOOKMARKLET.md — Global Rate Limiter, Fair-Share Queue & Peer Bookmarklet
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5` (via SSH from Antigravity IDE)
**Public Host:** `https://ordpresse.seame.click`
**Target Agent:** Gemini 3.8 Flash

> **CRITICAL DIRECTIVES FOR THE AI AGENT (READ FIRST):**
> 1. **Match Existing Route Naming:** Inspect `src/ordpresse/web/app.py` before adding endpoints. Existing builds use `build_id` and `/api/build/...`. Support `/api/build/{build_id}/missing-words` (and alias `/api/jobs/{build_id}/missing-words`) so nothing breaks.
> 2. **Browser CSP Bypass on `ordnet.dk`:** Do NOT rely solely on `<script src="https://ordpresse.seame.click/...">` inside the bookmarklet, because Content Security Policy (`script-src`) on `ordnet.dk` may block external `<script>` tags. Instead, make the bookmarklet **100% self-contained inline JavaScript** (using `fetch()`, which is allowed when CORS headers are enabled on `ordpresse`), AND also serve `/static/worker.js` as an optional userscript/loader.
> 3. **CORS Middleware Required:** Because the bookmarklet runs inside the `https://ordnet.dk` origin and calls `https://ordpresse.seame.click/api/...` via `fetch()`, you MUST enable `CORSMiddleware` in FastAPI for `/api/cache/submit` and `/api/build/{build_id}/missing-words` with `allow_origins=["https://ordnet.dk", "http://ordnet.dk", "*"]`.
> 4. **Zero-Cost Cache Hits:** Words that already exist in `COR`, `nordic_1800s.py`, or `cache.sqlite3` must NEVER consume a token from the rate limiter. Only live HTTP requests from the server to `ordnet.dk`/`naob.no` consume tokens.

---

## 1. Architecture Overview

To protect the server's IP address (`192.168.50.5`) from being rate-limited or blocked by `ordnet.dk` when multiple users upload `.epub` files simultaneously, implement a two-pronged system:

1. **Server Fallback (Polite Token Bucket + Fair-Share Queue):**
   - Server-initiated live scraping is capped at **`ORDPRESSE_MAX_HOURLY_REQUESTS = 200`** (1 request every 18 seconds after an initial burst of 10 tokens).
   - If multiple builds are active simultaneously, a **Fair-Share Round-Robin scheduler** alternates 1 word at a time per active `build_id` so a user uploading 8 novels (500+ missing words) never starves a user uploading a short story (5 missing words).
   - Users can click **"Afslut byg nu (spring resterende kø over)"** at any time to immediately compile and download the StarDict `.zip` with all words collected so far.

2. **Client Fast-Track (Peer Bookmarklet on `ordnet.dk`):**
   - While a build has pending missing words, the Web UI displays a **"⚡ Spring køen over (Bookmarklet)"** box.
   - The user drags the bookmarklet to their bookmarks bar, clicks **"Åbn ordnet.dk med dit Job-ID"** (which opens `https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id={build_id}&ordpresse_origin={origin}`), and clicks the bookmarklet in their bookmarks bar.
   - Running directly inside `ordnet.dk` (same-origin to `ordnet.dk`, using the user's own residential IP), the bookmarklet fetches the missing words from `ordpresse`, queries `ordnet.dk/ods/ordbog?query={word}` at 350 ms intervals, and POSTs the raw `.artikel` HTML back to `POST /api/cache/submit`.
   - As soon as the server receives a word via `/api/cache/submit`, it parses and stores it in `cache.sqlite3`, marks the word as resolved for the active `build_id`, and wakes up the build pipeline immediately without spending any server tokens!

---

## 2. Module-by-Module Implementation Plan

### 2.1 Global Token Bucket & Fair-Share Coordinator (`src/ordpresse/sources/rate_limiter.py`)

Create `src/ordpresse/sources/rate_limiter.py` with an async singleton `ScrapeCoordinator`:

```python
import asyncio
import os
import time
from collections import deque
from typing import Callable, Awaitable

class ScrapeCoordinator:
    def __init__(self) -> None:
        self.max_per_hour: int = int(os.getenv("ORDPRESSE_MAX_HOURLY_REQUESTS", "200"))
        self.burst_capacity: int = int(os.getenv("ORDPRESSE_BURST_TOKENS", "10"))
        self.refill_interval: float = 3600.0 / max(1, self.max_per_hour)  # 18.0s at 200/hr
        self.tokens: float = float(self.burst_capacity)
        self.last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

        # Active builds tracking: build_id -> set of remaining missing words
        self.missing_by_build: dict[str, list[str]] = {}
        self.resolved_words: dict[str, set[str]] = {}
        self.skip_remaining_flags: set[str] = set()
        self.word_resolved_events: dict[str, asyncio.Event] = {}

        # Round-robin queue of build_ids waiting for a server scrape token
        self._rr_queue: deque[str] = deque()

    def register_build(self, build_id: str, missing_words: list[str]) -> None:
        # Preserve order, deduplicate
        unique_words = list(dict.fromkeys(w.strip() for w in missing_words if w.strip()))
        self.missing_by_build[build_id] = unique_words
        self.resolved_words[build_id] = set()
        self.word_resolved_events[build_id] = asyncio.Event()

    def unregister_build(self, build_id: str) -> None:
        self.missing_by_build.pop(build_id, None)
        self.resolved_words.pop(build_id, None)
        self.skip_remaining_flags.discard(build_id)
        self.word_resolved_events.pop(build_id, None)

    def get_pending_words(self, build_id: str) -> list[str]:
        all_missing = self.missing_by_build.get(build_id, [])
        done = self.resolved_words.get(build_id, set())
        return [w for w in all_missing if w not in done]

    def mark_word_resolved(self, word: str, build_id: str | None = None) -> None:
        w_low = word.strip().lower()
        for bid, words in self.missing_by_build.items():
            for candidate in words:
                if candidate.lower() == w_low:
                    self.resolved_words.setdefault(bid, set()).add(candidate)
            if bid in self.word_resolved_events:
                self.word_resolved_events[bid].set()

    def trigger_skip_remaining(self, build_id: str) -> None:
        self.skip_remaining_flags.add(build_id)
        if build_id in self.word_resolved_events:
            self.word_resolved_events[build_id].set()

    def should_skip_remaining(self, build_id: str) -> bool:
        return build_id in self.skip_remaining_flags

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        added = elapsed / self.refill_interval
        if added > 0:
            self.tokens = min(float(self.burst_capacity), self.tokens + added)
            self.last_refill = now

    async def acquire_token_or_peer_resolve(
        self,
        build_id: str,
        word: str,
        on_wait_callback: Callable[[float, int], Awaitable[None]] | None = None,
    ) -> bool:
        """
        Returns True if the server should scrape `word` now.
        Returns False if `word` was already resolved by the peer bookmarklet OR the user clicked skip.
        """
        while True:
            if self.should_skip_remaining(build_id):
                return False
            if word in self.resolved_words.get(build_id, set()):
                return False

            async with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                wait_seconds = (1.0 - self.tokens) * self.refill_interval

            pending_count = len(self.get_pending_words(build_id))
            if on_wait_callback:
                await on_wait_callback(wait_seconds, pending_count)

            # Wait up to min(wait_seconds, 2.0s) OR wake up immediately if peer bookmarklet submits a word!
            evt = self.word_resolved_events.get(build_id)
            if evt:
                evt.clear()
                try:
                    await asyncio.wait_for(evt.wait(), timeout=min(wait_seconds, 2.0))
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(min(wait_seconds, 2.0))

coordinator = ScrapeCoordinator()
```

---

### 2.2 Pipeline Integration (`src/ordpresse/pipeline.py`)

Update the extra/EPUB word lookup loop in `pipeline.py`:
1. **Pre-Filter Before Scraping (CRITICAL OPTIMIZATION):**
   - Before queuing any unknown EPUB words for live scraping, check:
     a) Is the word already a headword or inflection in the loaded `COR` + `nordic_1800s` entries?
     b) Does the word (or its `orthography.py` normalized lemma / `ods-fullforms` headword) already exist in `cache.sqlite3`?
   - Only words that are genuinely missing from both `COR`, `nordic_1800s`, and `cache.sqlite3` are registered in `coordinator.register_build(build_id, missing_words)`.
2. **Loop Over Truly Missing Words:**
   - For each missing `word`:
     - Check if `db.get_cached_definition(word)` now exists (because the user's bookmarklet just submitted it!). If yes, call `coordinator.mark_word_resolved(word, build_id)` and merge the cached entry immediately (0 tokens used).
     - Otherwise, call `should_scrape = await coordinator.acquire_token_or_peer_resolve(build_id, word, on_wait_callback=...)`.
     - Inside `on_wait_callback`, emit an SSE progress update:
       `f"Venter på server-kvote ({wait_s:.0f}s, {pending} ord i kø) — brug Bookmarklet for lyn-hentning!"`
     - If `should_scrape` is `False`:
       - Re-check `db.get_cached_definition(word)` (in case the bookmarklet resolved it while waiting). If present, merge it!
       - If `coordinator.should_skip_remaining(build_id)` is `True`, break out of the scrape loop and proceed directly to StarDict binary compilation.
     - If `should_scrape` is `True`:
       - Scrape via `ods_scraper` / `naob`, save to `cache.sqlite3`, and call `coordinator.mark_word_resolved(word, build_id)`.
3. Always call `coordinator.unregister_build(build_id)` in a `finally:` block when the build finishes.

---

### 2.3 FastAPI Endpoints & CORS (`src/ordpresse/web/app.py`)

1. **Enable CORS Middleware:**
   ```python
   from fastapi.middleware.cors import CORSMiddleware

   app.add_middleware(
       CORSMiddleware,
       allow_origins=["*"],
       allow_credentials=False,
       allow_methods=["GET", "POST", "OPTIONS"],
       allow_headers=["*"],
   )
   ```

2. **`GET /api/build/{build_id}/missing-words` (and alias `GET /api/jobs/{build_id}/missing-words`):**
   - Returns:
     ```json
     {
       "build_id": "abc123",
       "missing_count": 14,
       "words": ["uefterrettelig", "dårekiste", "..."]
     }
     ```

3. **`POST /api/cache/submit`:**
   - Accepts JSON payload:
     ```json
     {
       "word": "uefterrettelig",
       "html": "<div class=\"artikel\">...</div>",
       "build_id": "abc123"
     }
     ```
   - Validates that `word` is non-empty (`<= 80` chars) and `html` is non-empty (`<= 500_000` chars).
   - Passes `html` through the exact same ODS parser/cleaner (`parse_ods_html(word, html)`) used by the server scraper so all citation stripping, homograph extraction, and grammar normalization rules apply identically.
   - Saves the cleaned entry to `cache.sqlite3` (if valid definition text was extracted).
   - Calls `coordinator.mark_word_resolved(word, build_id)` so any waiting build loop wakes up immediately.
   - Returns `{"status": "ok", "word": word, "remaining": len(coordinator.get_pending_words(build_id))}`.

4. **`POST /api/build/{build_id}/skip-queue`:**
   - Calls `coordinator.trigger_skip_remaining(build_id)` and returns `{"status": "skipping"}` so the user can finish and download their dictionary immediately if they don't want to wait for the remaining uncached words.

---

### 2.4 Self-Contained Bookmarklet & Helper Portal (`index.html`)

Because `ordnet.dk` is an external site, the user needs a frictionless 2-step flow in `index.html` whenever a build has missing words (`missing_count > 0`):

1. **Step 1 — Drag Bookmarklet once (`⚡ Ordpresse Lyn-Henter`):**
   Generate a self-contained inline `javascript:(function(){...})()` bookmarklet URL in `index.html` that dynamically reads `location.hash` (or prompts if missing), fetches `/api/build/{build_id}/missing-words`, loops through each word on `https://ordnet.dk/ods/ordbog?query={word}`, extracts the page HTML, and POSTs it to `/api/cache/submit`:

   ```javascript
   javascript:(async function(){
     const m = location.hash.match(/ordpresse_id=([^&]+)/);
     const o = location.hash.match(/ordpresse_origin=([^&]+)/);
     const bid = m ? m[1] : prompt("Indtast Ordpresse Build-ID:");
     const host = o ? decodeURIComponent(o[1]) : "[http://192.168.50.5:18088](http://192.168.50.5:18088)";
     if (!bid) return;
     if (!location.hostname.includes("ordnet.dk")) {
       location.href = "[https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=](https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=)" + bid + "&ordpresse_origin=" + encodeURIComponent(location.origin);
       return;
     }
     const box = document.createElement("div");
     box.style = "position:fixed;bottom:20px;right:20px;z-index:999999;background:#111827;color:#fef3c7;padding:16px 20px;border:2px solid #f59e0b;border-radius:10px;font-family:sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.5);min-width:280px;";
     document.body.appendChild(box);
     const res = await fetch(host + "/api/build/" + bid + "/missing-words");
     const data = await res.json();
     const words = data.words || [];
     for (let i = 0; i < words.length; i++) {
       const w = words[i];
       box.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter " + (i+1) + " / " + words.length + ": <b>" + w + "</b>";
       try {
         const r = await fetch("[https://ordnet.dk/ods/ordbog?query=](https://ordnet.dk/ods/ordbog?query=)" + encodeURIComponent(w));
         const html = await r.text();
         await fetch(host + "/api/cache/submit", {
           method: "POST",
           headers: {"Content-Type": "application/json"},
           body: JSON.stringify({word: w, html: html, build_id: bid})
         });
       } catch (e) { console.error(e); }
       await new Promise(r => setTimeout(r, 350));
     }
     box.innerHTML = "<b>✅ Færdig!</b> Alle " + words.length + " ord er sendt til Ordpresse.<br>Du kan lukke denne fane nu.";
   })();
   ```

2. **Step 2 — Action Buttons during Build in `index.html`:**
   When a build starts and has missing words waiting for ODS lookup, show an interactive panel beneath the progress bar:
   - **Button 1 (Open `ordnet.dk` with Job Hash):**
     `<a id="open-ordnet-link" target="_blank" href="https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id={build_id}&ordpresse_origin={window.location.origin}">1. Åbn ordnet.dk (med dit Job-ID) ↗</a>` — and instruct the user: *"Klik derefter på bogmærket **⚡ Ordpresse Lyn-Henter** i din bogmærkelinje for at hente ordene via din egen IP."*
   - **Button 2 (Skip Remaining Queue & Finish Now):**
     `<button onclick="skipQueue(currentBuildId)">Afslut byg nu uden resterende kø</button>` (calls `POST /api/build/{build_id}/skip-queue`).

---

## 3. Required Unit & Integration Tests (`tests/test_rate_limiter_and_peer.py`)

Create `tests/test_rate_limiter_and_peer.py` and verify with `pytest -v`:
1. **`test_token_bucket_burst_and_rate_limit`:**
   - Verify that `ScrapeCoordinator` allows immediate acquisition up to `burst_capacity`, and then enforces waiting when tokens are depleted.
2. **`test_peer_cache_submit_wakes_waiting_build`:**
   - Register a build with missing word `"uefterrettelig"`, deplete the token bucket (`coordinator.tokens = 0`), and start an async task calling `acquire_token_or_peer_resolve`.
   - Concurrently send a `POST /api/cache/submit` request with sample ODS HTML for `"uefterrettelig"`.
   - Assert that:
     a) `/api/cache/submit` parses and stores the cleaned definition in `cache.sqlite3`,
     b) `GET /api/build/{build_id}/missing-words` drops `"uefterrettelig"` from the pending list,
     c) The waiting `acquire_token_or_peer_resolve` task wakes up immediately (< 0.2s) and returns `False` (0 server tokens spent!).
3. **`test_skip_queue_endpoint`:**
   - Verify that calling `POST /api/build/{build_id}/skip-queue` sets the skip flag and immediately releases any waiting build task to compile the StarDict dictionary.

---

## 4. Deployment & Verification on `192.168.50.5`
1. Run `.venv/bin/pytest -v` locally until 100% of old and new tests pass.
2. Commit and push to `origin/main`.
3. Deploy via SSH to `root@192.168.50.5`:
   ```bash
   ssh root@192.168.50.5 "cd /root/ordpresse && git pull && docker compose up -d --build"
   ```
4. Verify with `curl -i -X OPTIONS http://192.168.50.5:18088/api/cache/submit -H "Origin: https://ordnet.dk" -H "Access-Control-Request-Method: POST"` that CORS headers (`access-control-allow-origin: *`) are present.