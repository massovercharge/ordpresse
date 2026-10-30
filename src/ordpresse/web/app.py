"""FastAPI application for Den Danske Ordpresse with async SSE builds and Cloudflare compatibility."""

import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List, Optional
import uuid

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ordpresse.db import Database
from ordpresse.sources.rate_limiter import coordinator
from ordpresse.sources.ods_scraper import parse_ods_html

from ordpresse import __version__
from ordpresse.config import (
    BASE_DIR,
    CACHE_DB_PATH,
    DATASETS_DB_PATH,
    DEFAULT_BOOKNAME,
    OUTPUT_DIR,
)
from ordpresse.datasets import DatasetImporter
from ordpresse.pipeline import BuildResult, Pipeline

from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db = Database()
        purged = db.clean_corrupted_cached_definitions()
        if purged > 0:
            logger.info(f"Startup: Cleaned {purged} corrupted definition entries from cache.sqlite3")
    except Exception as e:
        logger.warning(f"Startup cache cleanup warning: {e}")
    yield


app = FastAPI(
    title="Den Danske Ordpresse",
    description="Historisk og moderne dansk StarDict-ordbogsbygger til KOReader",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class CacheSubmitPayload(BaseModel):
    word: str = Field(..., min_length=1, max_length=80)
    html: str = Field(..., min_length=1, max_length=500_000)
    build_id: Optional[str] = None

# In-memory build state manager for SSE streams
@dataclass
class BuildJob:
    build_id: str
    status: str = "queued"  # queued, building, done, error, cancelled
    progress: int = 0
    message: str = "Venter i kø..."
    result: Optional[BuildResult] = None
    error: Optional[str] = None
    cancelled: bool = False
    events: asyncio.Queue = field(default_factory=asyncio.Queue)


ACTIVE_BUILDS: Dict[str, BuildJob] = {}


@app.get("/health")
def healthcheck():
    """Healthcheck endpoint for Docker and monitoring."""
    return {
        "status": "ok",
        "version": __version__,
        "datasets_ready": DATASETS_DB_PATH.exists(),
        "cache_ready": CACHE_DB_PATH.exists(),
    }


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    """Render the main dashboard."""
    datasets_ready = DATASETS_DB_PATH.exists() and DATASETS_DB_PATH.stat().st_size > 1024
    cache_ready = CACHE_DB_PATH.exists()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": __version__,
            "datasets_ready": datasets_ready,
            "cache_ready": cache_ready,
            "default_bookname": DEFAULT_BOOKNAME,
        },
    )


@app.get("/api/stats")
def get_stats():
    """Get system and dataset status."""
    return {
        "datasets_sqlite_exists": DATASETS_DB_PATH.exists(),
        "datasets_sqlite_size_mb": round(DATASETS_DB_PATH.stat().st_size / (1024 * 1024), 2)
        if DATASETS_DB_PATH.exists()
        else 0,
        "cache_sqlite_exists": CACHE_DB_PATH.exists(),
        "cache_sqlite_size_mb": round(CACHE_DB_PATH.stat().st_size / (1024 * 1024), 2)
        if CACHE_DB_PATH.exists()
        else 0,
    }


@app.get("/api/preview")
def preview_lookup(request: Request, q: str, format: Optional[str] = None):
    """Preview a dictionary lookup, returning rendered canonical E-Ink HTML."""
    word = q.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="Mangler søgeord 'q'")

    # 1. First, check the most recently compiled StarDict binary in OUTPUT_DIR
    ifo_candidates = sorted(
        list(OUTPUT_DIR.glob("build_*/ordpresse.ifo")) + list(OUTPUT_DIR.glob("ordpresse.ifo")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for ifo in ifo_candidates:
        try:
            from ordpresse.stardict_reader import StarDictReader
            reader = StarDictReader(ifo)
            entry = reader.lookup_entry(word)
            if entry:
                hw, html = entry
                if format == "html":
                    return HTMLResponse(content=html)
                return {
                    "word": q,
                    "headword": hw,
                    "found": True,
                    "html": html,
                    "source": "stardict_binary",
                }
        except Exception as e:
            logger.debug(f"Reader failed on {ifo}: {e}")

    # 2. Fallback to curated Nordic 1800s vocabulary (35+ Moberg and 19th c. terms)
    from ordpresse.sources.nordic_1800s import get_nordic_1800s_entry
    nordic = get_nordic_1800s_entry(word)
    if nordic:
        from ordpresse.formatter import render_canonical_html
        html = render_canonical_html(nordic)
        if format == "html":
            return HTMLResponse(content=html)
        return {
            "word": q,
            "headword": nordic.headword,
            "found": True,
            "html": html,
            "source": "nordic_1800s",
        }

    # 3. Fallback to Modern COR-SEM in Database
    from ordpresse.db import Database
    from ordpresse.models import CanonicalEntry, SenseItem
    from ordpresse.formatter import render_canonical_html, normalize_pos
    db = Database()
    if db.datasets_path.exists():
        cor_defs = db.lookup_cor_lemma_definitions(word)
        if cor_defs:
            lemma = cor_defs[0]["lemma"].lower()
            poses = []
            for d in cor_defs:
                p = normalize_pos(d.get("pos", ""))
                if p and p not in poses:
                    poses.append(p)
            pos_display = " / ".join(poses) if poses else normalize_pos(cor_defs[0].get("pos", ""))

            modern_senses = [
                SenseItem(
                    definition=f"({normalize_pos(d.get('pos', ''))}) {d['definition']}" if len(poses) > 1 and d.get("pos") else d["definition"],
                    quote=d.get("examples").split("|")[0].strip() if d.get("examples") else None,
                    source_tag="COR-SEM",
                )
                for d in cor_defs[:4]
            ]
            cor_infs = db.lookup_cor_inflections(lemma, poses[0] if len(poses) == 1 else None)
            canonical = CanonicalEntry(
                headword=lemma,
                pos=pos_display,
                inflections=set(cor_infs + [word]),
                summary_hook="",
                modern_senses=modern_senses,
                sources_used=["COR-SEM"],
            )
            html = render_canonical_html(canonical)
            if format == "html":
                return HTMLResponse(content=html)
            return {
                "word": q,
                "headword": lemma,
                "found": True,
                "html": html,
                "source": "cor_sem",
            }

    # 4. Fallback to morphological compound synthesis (dernedefra, uefterrettelig)
    from ordpresse.orthography import synthesize_compound_entry
    synth = synthesize_compound_entry(word, db)
    if synth:
        html = render_canonical_html(synth)
        if format == "html":
            return HTMLResponse(content=html)
        return {
            "word": q,
            "headword": synth.headword,
            "found": True,
            "html": html,
            "source": "morphology",
        }

    # 5. Fallback to Scraper
    from ordpresse.scrapers import HistoricalScraper
    scraper = HistoricalScraper(db)
    res = scraper.fetch_word_definition(word, allow_remote=True)
    if res:
        html = res["html_content"]
        if format == "html":
            return HTMLResponse(content=html)
        return {
            "word": q,
            "headword": res["headword"],
            "found": True,
            "html": html,
            "source": res.get("source", "scraper"),
        }

    fallback_html = f"<div style='color:#666;font-style:italic;'>Ingen definition fundet for »{q}«.</div>"
    if format == "html":
        return HTMLResponse(content=fallback_html)
    return {
        "word": q,
        "headword": word,
        "found": False,
        "html": fallback_html,
        "source": "none",
    }


def _run_background_build(
    job: BuildJob,
    saved_epub_paths: List[Path],
    custom_words: List[str],
    bookname: str,
    full_dictionary: bool,
    allow_network: bool,
    temp_dir: Path,
    main_loop: asyncio.AbstractEventLoop,
    scrape_mode: str = "bookmarklet",
):
    """Worker function executed asynchronously to prevent Cloudflare 524 timeouts."""

    def sync_progress(msg: str, pct: int):
        job.progress = pct
        job.message = msg
        data = json.dumps({"status": "progress", "percent": pct, "message": msg})
        main_loop.call_soon_threadsafe(job.events.put_nowait, data)

    try:
        job.status = "building"
        pipeline = Pipeline(
            output_dir=OUTPUT_DIR,
            bookname=bookname,
        )
        result = pipeline.run(
            epub_files=saved_epub_paths,
            custom_words=custom_words,
            include_seeds=True,
            full_dictionary=full_dictionary,
            allow_network=allow_network,
            progress_cb=sync_progress,
            is_cancelled=lambda: job.cancelled,
            build_id=job.build_id,
            loop=main_loop,
            scrape_mode=scrape_mode,
        )
        job.status = "done"
        job.result = result
        data = json.dumps(
            {
                "status": "done",
                "percent": 100,
                "message": "Færdig!",
                "entry_count": result.entry_count,
                "synonym_count": result.synonym_count,
                "download_url": f"/api/download/{job.build_id}",
            }
        )
        main_loop.call_soon_threadsafe(job.events.put_nowait, data)
    except Exception as exc:
        if job.cancelled:
            logger.info(f"Build {job.build_id} was cancelled by user")
            job.status = "cancelled"
            job.message = "Afbrudt af brugeren"
            data = json.dumps({
                "status": "cancelled",
                "percent": job.progress,
                "message": "Byggeprocessen blev afbrudt af brugeren.",
            })
            main_loop.call_soon_threadsafe(job.events.put_nowait, data)
        else:
            logger.exception("Build failed")
            job.status = "error"
            job.error = str(exc)
            data = json.dumps({"status": "error", "error": str(exc)})
            main_loop.call_soon_threadsafe(job.events.put_nowait, data)
    finally:
        # Cleanup temporary uploaded files
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@app.post("/api/build")
async def start_build(
    epub_files: List[UploadFile] = [],
    custom_words: str = Form(""),
    bookname: str = Form(DEFAULT_BOOKNAME),
    full_dictionary: bool = Form(False),
    allow_network: bool = Form(True),
    scrape_mode: str = Form("bookmarklet"),
):
    """Trigger an async build job."""
    build_id = uuid.uuid4().hex[:8]
    job = BuildJob(build_id=build_id)
    ACTIVE_BUILDS[build_id] = job

    # Save uploaded files into a temp directory
    temp_upload_dir = Path(tempfile.mkdtemp(prefix=f"ordpresse_upload_{build_id}_"))
    saved_paths: List[Path] = []

    for upload in epub_files:
        if upload.filename and upload.filename.lower().endswith(".epub"):
            dest = temp_upload_dir / upload.filename
            content = await upload.read()
            dest.write_bytes(content)
            saved_paths.append(dest)

    parsed_words = [w.strip() for w in custom_words.replace("\n", ",").split(",") if w.strip()]
    is_full = str(full_dictionary).strip().lower() in ("true", "1", "yes", "on")
    is_network = str(allow_network).strip().lower() in ("true", "1", "yes", "on")

    # Run build in a background thread/task to avoid blocking FastAPI event loop
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _run_background_build,
        job,
        saved_paths,
        parsed_words,
        bookname,
        is_full,
        is_network,
        temp_upload_dir,
        loop,
        scrape_mode,
    )

    return {"build_id": build_id, "status": "started"}


@app.get("/api/build/events/{build_id}")
async def build_events(build_id: str):
    """Server-Sent Events (SSE) endpoint for live progress streaming."""
    if build_id not in ACTIVE_BUILDS:
        raise HTTPException(status_code=404, detail="Byggejob ikke fundet")

    job = ACTIVE_BUILDS[build_id]

    async def event_generator():
        # Send initial status
        initial_payload = {
            "status": job.status,
            "percent": job.progress,
            "message": job.message,
        }
        if job.status == "done":
            initial_payload["download_url"] = f"/api/download/{job.build_id}"
            if job.result:
                initial_payload["entry_count"] = job.result.entry_count
                initial_payload["synonym_count"] = job.result.synonym_count
        elif job.status == "error":
            initial_payload["error"] = job.error or "Byggefejl"

        yield f"data: {json.dumps(initial_payload)}\n\n"

        # If job already completed, finish stream immediately
        if job.status in ("done", "error", "cancelled"):
            return

        while True:
            try:
                # Wait for next event with a 15-second heartbeat ping to keep Cloudflare happy
                msg = await asyncio.wait_for(job.events.get(), timeout=15.0)
                yield f"data: {msg}\n\n"
                parsed = json.loads(msg)
                if parsed.get("status") in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                # Cloudflare tunnel keepalive comment
                yield ": keepalive-ping\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/build/cancel/{build_id}")
async def cancel_build(build_id: str):
    """Cancel an active build job."""
    if build_id not in ACTIVE_BUILDS:
        raise HTTPException(status_code=404, detail="Byggejob ikke fundet")

    job = ACTIVE_BUILDS[build_id]
    if job.status in ("done", "error", "cancelled"):
        return {"status": job.status, "message": "Job er allerede afsluttet"}

    job.cancelled = True
    job.status = "cancelled"
    job.message = "Afbrudt af brugeren"
    data = json.dumps({
        "status": "cancelled",
        "percent": job.progress,
        "message": "Byggeprocessen blev afbrudt af brugeren.",
    })
    job.events.put_nowait(data)
    return {"status": "cancelled", "build_id": build_id}


@app.api_route("/api/download/{build_id}", methods=["GET", "HEAD"])
async def download_dict(build_id: str):
    if build_id == "latest":
        candidates = sorted(
            list(OUTPUT_DIR.glob("build_*/ordpresse.zip")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return FileResponse(
                path=str(candidates[0]),
                filename="ordpresse.zip",
                media_type="application/zip",
            )
        raise HTTPException(status_code=404, detail="Ingen ordbogsbyg fundet")

    target_dir = OUTPUT_DIR / f"build_{build_id}"
    zip_path = target_dir / "ordpresse.zip"
    if not zip_path.exists():
        # Check active builds
        if build_id in ACTIVE_BUILDS and ACTIVE_BUILDS[build_id].result:
            zip_path = ACTIVE_BUILDS[build_id].result.zip_path

    if not zip_path or not zip_path.exists():
        raise HTTPException(status_code=404, detail="Ordbogsarkiv ikke fundet")

    return FileResponse(
        path=str(zip_path),
        filename="ordpresse.zip",
        media_type="application/zip",
    )


@app.get("/api/build/{build_id}/missing-words")
@app.get("/api/jobs/{build_id}/missing-words")
async def get_missing_words(build_id: str, limit: Optional[int] = None):
    """Get currently pending words for a specific build awaiting resolution, optionally capped by limit."""
    words = coordinator.get_pending_words(build_id)
    total_count = len(words)
    resolved_count = coordinator.get_resolved_count(build_id)
    if limit is not None and limit > 0:
        words = words[:limit]
    return {
        "build_id": build_id,
        "missing_count": len(words),
        "total_pending": total_count,
        "resolved_count": resolved_count,
        "words": words,
    }


@app.post("/api/cache/submit")
async def submit_cache(payload: CacheSubmitPayload):
    """Receive scraped HTML from peer bookmarklet, parse, store in cache, and wake waiting builds."""
    word = payload.word.strip()
    html = payload.html.strip()
    build_id = payload.build_id.strip() if payload.build_id else None

    if not word or len(word) > 80:
        raise HTTPException(status_code=400, detail="Ugyldigt 'word'")
    if not html or len(html) > 500_000:
        raise HTTPException(status_code=400, detail="Ugyldigt 'html'")

    parsed = parse_ods_html(word, html)
    db = Database()
    if parsed:
        db.set_cached(
            word=word.lower(),
            source="ods",
            headword=parsed["headword"],
            html_content=parsed["html_content"],
            synonyms=parsed.get("synonyms", []),
        )
    else:
        # Negative cache: word was looked up on ordnet.dk and has no article
        db.set_not_found(word=word.lower())

    coordinator.mark_word_resolved(word, build_id)
    remaining = len(coordinator.get_pending_words(build_id)) if build_id else 0
    return {
        "status": "ok",
        "word": word,
        "remaining": remaining,
    }


@app.post("/api/build/{build_id}/skip-queue")
@app.post("/api/jobs/{build_id}/skip-queue")
async def skip_queue(build_id: str):
    """Signal active build to skip remaining queue and finish compiling StarDict immediately."""
    coordinator.trigger_skip_remaining(build_id)
    return {
        "status": "skipping",
        "build_id": build_id,
    }

@app.get("/static/worker.js")
async def get_worker_js():
    """Serve worker.js directly."""
    worker_path = Path(__file__).parent / "static" / "worker.js"
    if worker_path.exists():
        return FileResponse(worker_path, media_type="application/javascript")
    from fastapi.responses import Response
    fallback = r'''// Ordpresse Lyn-Henter (Userscript / Bookmarklet worker)
(async function() {
  const m = location.hash.match(/ordpresse_id=([^&]+)/);
  const o = location.hash.match(/ordpresse_origin=([^&]+)/);
  const l = location.hash.match(/limit=(\d+)/);
  const bid = m ? m[1] : prompt("Indtast Ordpresse Build-ID:");
  const host = o ? decodeURIComponent(o[1]) : (window.location.origin || "https://ordpresse.seame.click");
  if (!bid) return;
  if (!location.hostname.includes("ordnet.dk")) {
    location.href = "https://ordnet.dk/ods/ordbog?query=ordbog#ordpresse_id=" + bid + "&ordpresse_origin=" + encodeURIComponent(location.origin) + (l ? "&limit=" + l[1] : "");
    return;
  }
  const box = document.createElement("div");
  box.style = "position:fixed;bottom:20px;right:20px;z-index:999999;background:#111827;color:#fef3c7;padding:16px 20px;border:2px solid #f59e0b;border-radius:12px;font-family:sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.5);min-width:300px;max-width:400px;";
  document.body.appendChild(box);

  let stopped = false;
  const stopBtn = document.createElement("button");
  stopBtn.innerText = "⏹ Stop & afslut ordbog nu";
  stopBtn.style = "margin-top:10px;padding:6px 12px;background:#b91c1c;color:#fff;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:12px;display:block;width:100%;";
  stopBtn.onclick = async () => {
    stopped = true;
    box.innerHTML = "<b>⏹ Stopper...</b> Sender afslutning til serveren...";
    try {
      await fetch(host + "/api/build/" + bid + "/skip-queue", { method: "POST" });
    } catch(e) {}
    box.innerHTML = "<b>✅ Stoppet!</b> Ordbogen færdiggøres nu med de hentede ord.<br>Du kan lukke denne fane.";
  };

  const url = host + "/api/build/" + bid + "/missing-words" + (l ? "?limit=" + l[1] : "");
  const res = await fetch(url);
  const data = await res.json();
  const words = data.words || [];

  if (words.length === 0) {
    box.innerHTML = "<b>✅ Ingen ord mangler!</b> Alle ord er allerede fundet.";
    return;
  }

  const contentDiv = document.createElement("div");
  box.appendChild(contentDiv);
  box.appendChild(stopBtn);

  // Progressive pacing and jitter based on batch size
  let baseDelay = 350;
  let jitterRange = 150;
  if (words.length > 500) {
    baseDelay = 1100;
    jitterRange = 700;
  } else if (words.length > 250) {
    baseDelay = 650;
    jitterRange = 450;
  } else if (words.length > 100) {
    baseDelay = 450;
    jitterRange = 250;
  }

  for (let i = 0; i < words.length; i++) {
    if (stopped) break;
    const w = words[i];
    contentDiv.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter <b>" + (i+1) + " / " + words.length + "</b>: <code style='color:#f59e0b;font-size:14px;'>" + w + "</code>";

    try {
      const r = await fetch("https://ordnet.dk/ods/ordbog?query=" + encodeURIComponent(w));

      if (r.status === 429) {
        contentDiv.innerHTML = "<b>⚠️ ordnet.dk bad om en pause (HTTP 429).</b><br>Venter 20 sekunder før automatisk genoptagelse...";
        baseDelay += 400;
        jitterRange += 300;
        await new Promise(res => setTimeout(res, 20000));
        i--;
        continue;
      }

      if (r.ok) {
        const html = await r.text();
        await fetch(host + "/api/cache/submit", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({word: w, html: html, build_id: bid})
        });
      } else {
        console.warn("Uventet HTTP status for " + w + ": " + r.status);
      }
    } catch (e) {
      console.error("Fejl under hentning af " + w + ":", e);
    }

    const progressiveExtra = (i > 500) ? Math.min(500, Math.floor((i - 500) / 100) * 50) : 0;
    const actualDelay = baseDelay + progressiveExtra + Math.floor(Math.random() * jitterRange);
    await new Promise(r => setTimeout(r, actualDelay));
  }

  if (!stopped) {
    try {
      await fetch(host + "/api/build/" + bid + "/skip-queue", { method: "POST" });
    } catch(e) {}
    box.innerHTML = "<b>✅ Færdig!</b> Alle " + words.length + " ord er sendt til Ordpresse.<br>Serveren kompilerer nu din ordbog færdig.";
  }
})();
'''
    return Response(content=fallback, media_type="application/javascript")


@app.post("/api/build/{build_id}/start-server-scrape")
@app.post("/api/jobs/{build_id}/start-server-scrape")
async def start_server_scrape(build_id: str):
    """Explicitly switch build mode to server token-bucket scrape upon user request."""
    coordinator.set_build_mode(build_id, "server")
    return {"status": "server_scraping", "build_id": build_id}


@app.post("/api/build/{build_id}/set-mode")
@app.post("/api/jobs/{build_id}/set-mode")
async def set_build_mode_endpoint(build_id: str, request: Request):
    """Update build scraping mode dynamically (server, bookmarklet, skip)."""
    content_type = request.headers.get("content-type", "")
    mode = "bookmarklet"
    if "application/json" in content_type:
        data = await request.json()
        mode = data.get("mode", "bookmarklet")
    else:
        form = await request.form()
        mode = form.get("mode", "bookmarklet")
    if mode not in ("server", "bookmarklet", "skip"):
        raise HTTPException(status_code=400, detail="Ugyldig mode. Tilladte: server, bookmarklet, skip")
    coordinator.set_build_mode(build_id, mode)
    return {"status": "ok", "build_id": build_id, "mode": mode}
