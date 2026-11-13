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
import hmac

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ordpresse.db import Database
from ordpresse.sources.rate_limiter import coordinator
from ordpresse.sources.ods_scraper import parse_ods_html
from ordpresse.web.auth import (
    ORDPRESSE_ADMIN_PASSWORD,
    ORDPRESSE_DISABLE_AUTH,
    create_session_token,
    verify_session_token,
    is_external_request,
    verify_api_key,
)
from ordpresse.web.lookup_service import perform_koreader_lookup
from ordpresse.web.plugin_builder import (
    PLUGIN_RELEASE_NOTES,
    PLUGIN_VERSION,
    build_koplugin_zip,
    slugify_device_name,
)
from ordpresse.services.mirror_prober import MirrorProberService
from ordpresse.web.plugin_store import build_annas_fetch_zip, get_catalog_json, get_plugin_by_id

mirror_prober = MirrorProberService()

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


@app.middleware("http")
async def enforce_two_zone_boundary_and_auth(request: Request, call_next):
    """
    Two-Zone Defense-in-Depth Middleware:
    - Layer 1 (External / Cloudflare Tunnel): ONLY /api/v1/koreader/lookup is accessible.
      All other endpoints return HTTP 404 Not Found.
    - Layer 2 (LAN Zone): Requires admin password login via session cookie for management UI/APIs.
    """
    path = request.url.path

    # Layer 1: Strict External Zone Boundary
    if is_external_request(request):
        if path.rstrip("/") != "/api/v1/koreader/lookup":
            return PlainTextResponse("Not Found", status_code=404)
        return await call_next(request)

    # Layer 2: LAN Zone
    if ORDPRESSE_DISABLE_AUTH:
        return await call_next(request)

    if request.method == "OPTIONS":
        return await call_next(request)

    # Unauthenticated LAN routes
    if (
        path in ("/login", "/logout", "/health")
        or path.startswith("/static/")
        or path.rstrip("/") == "/api/v1/koreader/lookup"
    ):
        return await call_next(request)

    # Verify LAN session cookie
    session_cookie = request.cookies.get("ordpresse_session")
    if session_cookie and verify_session_token(session_cookie):
        return await call_next(request)

    # LAN Unauthenticated redirect or 401
    if path == "/" or ("text/html" in request.headers.get("accept", "") and not path.startswith("/api/")):
        return RedirectResponse(url="/login", status_code=302)

    return JSONResponse(status_code=401, content={"detail": "Kræver godkendelse."})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the LAN login screen."""
    if not is_external_request(request):
        cookie = request.cookies.get("ordpresse_session")
        if cookie and verify_session_token(cookie):
            return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form("")):
    """Verify admin password (from database or environment) and issue signed session cookie."""
    db = Database()
    if db.verify_admin_password(password, fallback_password=ORDPRESSE_ADMIN_PASSWORD):
        token = create_session_token()
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(
            key="ordpresse_session",
            value=token,
            max_age=30 * 86400,
            httponly=True,
            samesite="lax",
        )
        return resp

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Forkert adgangskode"},
        status_code=401,
    )


@app.get("/logout")
@app.post("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key="ordpresse_session")
    return resp


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
            "plugin_version": PLUGIN_VERSION,
            "plugin_release_notes": PLUGIN_RELEASE_NOTES,
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
                resolution = f"Bøjningsopslag: {q} → {hw}" if q.strip().lower() != hw.strip().lower() else f"Direkte opslag: {hw}"
                return {
                    "word": q,
                    "headword": hw,
                    "found": True,
                    "html": html,
                    "source": "stardict_binary",
                    "resolution": resolution,
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
        resolution = f"Bøjningsopslag: {q} → {nordic.headword}" if q.strip().lower() != nordic.headword.strip().lower() else f"Direkte opslag: {nordic.headword}"
        return {
            "word": q,
            "headword": nordic.headword,
            "found": True,
            "html": html,
            "source": "nordic_1800s",
            "resolution": resolution,
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
            resolution = f"Bøjningsopslag: {q} → {lemma}" if q.strip().lower() != lemma.strip().lower() else f"Direkte opslag: {lemma}"
            return {
                "word": q,
                "headword": lemma,
                "found": True,
                "html": html,
                "source": "cor_sem",
                "resolution": resolution,
            }

    # 4. Fallback to morphological compound synthesis (dernedefra, uefterrettelig)
    from ordpresse.orthography import synthesize_compound_entry
    synth = synthesize_compound_entry(word, db)
    if synth:
        html = render_canonical_html(synth)
        if format == "html":
            return HTMLResponse(content=html)
        resolution = f"Morfologisk opslag: {q} → {synth.headword}" if q.strip().lower() != synth.headword.strip().lower() else f"Direkte opslag: {synth.headword}"
        return {
            "word": q,
            "headword": synth.headword,
            "found": True,
            "html": html,
            "source": "morphology",
            "resolution": resolution,
        }

    # 5. Fallback to Scraper
    from ordpresse.scrapers import HistoricalScraper
    scraper = HistoricalScraper(db)
    res = scraper.fetch_word_definition(word, allow_remote=True)
    if res:
        html = res["html_content"]
        if format == "html":
            return HTMLResponse(content=html)
        resolution = f"Opslag ({res.get('source', 'ODS')}): {res['headword']}"
        return {
            "word": q,
            "headword": res["headword"],
            "found": True,
            "html": html,
            "source": res.get("source", "scraper"),
            "resolution": resolution,
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
        "resolution": f"Ingen match fundet for »{q}«",
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

    def sync_progress(msg: str, pct: int, telemetry: Optional[dict] = None):
        job.progress = pct
        job.message = msg
        payload = {"status": "progress", "percent": pct, "message": msg}
        if telemetry:
            payload["telemetry"] = telemetry
        data = json.dumps(payload)
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
    pending_items = coordinator.get_pending_items(build_id)
    total_count = len(pending_items)
    resolved_count = coordinator.get_resolved_count(build_id)
    if limit is not None and limit > 0:
        pending_items = pending_items[:limit]
    words = [it["word"] for it in pending_items]
    return {
        "build_id": build_id,
        "missing_count": len(words),
        "total_pending": total_count,
        "resolved_count": resolved_count,
        "words": words,
        "items": pending_items,
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

    db = Database()
    is_ods = "ods/ordbog" in html or "Ordbog over det danske Sprog" in html
    is_ddo = ("ddo/ordbog" in html or "modern-definition" in html or "class=\"definitionBox\"" in html) and not is_ods
    parsed = None
    if is_ddo:
        try:
            from ordpresse.sources.ddo_scraper import parse_ddo_html
            parsed = parse_ddo_html(word, html)
            if parsed:
                db.set_cached(
                    word=word.lower(),
                    source="ddo",
                    headword=parsed["headword"],
                    html_content=parsed["html_content"],
                    synonyms=parsed.get("synonyms", []),
                )
                if parsed.get("definitions"):
                    db.update_cor_definition(word, parsed["definitions"][0])
        except Exception as e:
            logger.debug(f"parse_ddo_html failed: {e}")

    if not parsed:
        parsed = parse_ods_html(word, html)
        if parsed:
            db.set_cached(
                word=word.lower(),
                source="ods",
                headword=parsed["headword"],
                html_content=parsed["html_content"],
                synonyms=parsed.get("synonyms", []),
            )
        elif not is_ddo:
            try:
                from ordpresse.sources.ddo_scraper import parse_ddo_html
                parsed = parse_ddo_html(word, html)
                if parsed:
                    db.set_cached(
                        word=word.lower(),
                        source="ddo",
                        headword=parsed["headword"],
                        html_content=parsed["html_content"],
                        synonyms=parsed.get("synonyms", []),
                    )
                    if parsed.get("definitions"):
                        db.update_cor_definition(word, parsed["definitions"][0])
            except Exception as e:
                logger.debug(f"parse_ddo_html fallback failed: {e}")

    if not parsed:
        # Negative cache: word was looked up on ordnet.dk and has no article
        w_low = word.lower().strip()
        db.set_not_found(word=w_low)
        if "aa" in w_low:
            db.set_not_found(word=w_low.replace("aa", "å"))
        elif "å" in w_low:
            db.set_not_found(word=w_low.replace("å", "aa"))

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
  const items = data.items || (data.words || []).map(w => ({word: w, target: "ods", priority: 1}));

  if (items.length === 0) {
    box.innerHTML = "<b>✅ Ingen ord mangler!</b> Alle ord er allerede fundet.";
    return;
  }

  const contentDiv = document.createElement("div");
  box.appendChild(contentDiv);
  box.appendChild(stopBtn);

  // Progressive pacing and jitter based on batch size
  let baseDelay = 350;
  let jitterRange = 150;
  if (items.length > 500) {
    baseDelay = 1100;
    jitterRange = 700;
  } else if (items.length > 250) {
    baseDelay = 650;
    jitterRange = 450;
  } else if (items.length > 100) {
    baseDelay = 450;
    jitterRange = 250;
  }

  for (let i = 0; i < items.length; i++) {
    if (stopped) break;
    const it = items[i];
    const w = typeof it === "string" ? it : it.word;
    const target = (it && it.target === "ddo") ? "ddo" : "ods";
    const prioLabel = (it && it.priority === 2) ? " [Prio 2: DDO]" : " [Prio 1: ODS]";
    contentDiv.innerHTML = "<b>⚡ Ordpresse Lyn-Henter</b><br>Henter <b>" + (i+1) + " / " + items.length + "</b>" + prioLabel + ": <code style='color:#f59e0b;font-size:14px;'>" + w + "</code>";

    try {
      const endpoint = target === "ddo" ? "https://ordnet.dk/ddo/ordbog?query=" : "https://ordnet.dk/ods/ordbog?query=";
      const r = await fetch(endpoint + encodeURIComponent(w));

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
    box.innerHTML = "<b>✅ Færdig!</b> Alle " + items.length + " ord er sendt til Ordpresse.<br>Serveren kompilerer nu din ordbog færdig.";
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


# ---------------------------------------------------------------------------
# KOReader Online Lookup & Multi-Client API Key Management (V10.2 Zero-Trust)
# ---------------------------------------------------------------------------

class ProvisionKeyPayload(BaseModel):
    device_name: str = Field(..., min_length=1, max_length=100)
    endpoint_url: Optional[str] = "https://ordpresse.seame.click/api/v1/koreader/lookup"


@app.get("/api/v1/koreader/lookup")
async def koreader_lookup_endpoint(request: Request, q: str = "", action: Optional[str] = None):
    """
    Public KOReader Online Lookup Endpoint.
    Accessible externally through Cloudflare Tunnel or locally via LAN.
    Strictly authenticated via X-API-Key header.
    Supports in-app plugin updates via action=check_update and action=download_update.
    """
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    db = Database()
    verify_api_key(api_key, db)

    if action == "check_update":
        from ordpresse.web.plugin_builder import PLUGIN_VERSION, PLUGIN_RELEASE_NOTES
        return {
            "latest_version": PLUGIN_VERSION,
            "release_notes": PLUGIN_RELEASE_NOTES,
        }
    elif action == "download_update":
        import hashlib
        from ordpresse.web.plugin_builder import MAIN_LUA, META_LUA, PLUGIN_VERSION
        return {
            "version": PLUGIN_VERSION,
            "main_lua": MAIN_LUA,
            "meta_lua": META_LUA,
            "sha256": hashlib.sha256(MAIN_LUA.encode("utf-8")).hexdigest(),
        }
    elif action == "annas_mirrors":
        refresh = request.query_params.get("refresh", "false").lower() in ("true", "1", "yes")
        payload = await mirror_prober.get_mirrors(force_refresh=refresh)
        return JSONResponse(content=payload)
    elif action == "annas_search":
        q = request.query_params.get("q") or request.query_params.get("query") or ""
        payload = await mirror_prober.search_books(q)
        return JSONResponse(content=payload)
    elif action == "annas_download_ticket":
        md5 = request.query_params.get("md5", "").strip()
        payload = await mirror_prober.resolve_download_ticket(md5)
        return JSONResponse(content=payload)
    elif action == "store_catalog":
        return JSONResponse(content=get_catalog_json())
    elif action == "store_download":
        plugin_id = request.query_params.get("id", "").strip()
        plugin = get_plugin_by_id(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' ikke fundet i kataloget.")

        if plugin_id == "annas-fetch":
            try:
                zip_bytes = build_annas_fetch_zip()
            except FileNotFoundError as exc:
                logger.error(f"Plugin store: Fejl ved pakning af {plugin_id}: {exc}")
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{plugin.koplugin_name}.zip"',
                    "X-Plugin-Id": plugin.id,
                    "X-Plugin-Version": plugin.version,
                    "X-Koplugin-Name": plugin.koplugin_name,
                },
            )

        # Proxy-download fra GitHub zipball og stream til KOReader-enheden
        import httpx
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                github_resp = await client.get(
                    plugin.zipball_url,
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "Ordpresse-PluginStore/1.0"},
                )
                github_resp.raise_for_status()
        except Exception as e:
            logger.error(f"Plugin store: Fejl ved download af {plugin_id} fra GitHub: {e}")
            raise HTTPException(status_code=502, detail=f"Kunne ikke hente plugin fra GitHub: {e}")

        return Response(
            content=github_resp.content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{plugin.koplugin_name}.zip"',
                "X-Plugin-Id": plugin.id,
                "X-Plugin-Version": plugin.version,
                "X-Koplugin-Name": plugin.koplugin_name,
            },
        )

    return await perform_koreader_lookup(q, db)


@app.get("/api/v1/keys")
def get_api_keys():
    """List all registered API keys (LAN-Only, session protected). Never exposes key hashes."""
    db = Database()
    return db.list_api_keys()


@app.post("/api/v1/keys/provision")
def provision_api_key(payload: ProvisionKeyPayload):
    """
    Generate a new API key for an e-reader device and return a customized ordpresse.koplugin.zip.
    The raw key is only returned inside the zip configuration file and never stored unhashed.
    """
    name = (payload.device_name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enhedsnavn må ikke være tomt.")

    endpoint_url = (payload.endpoint_url or "").strip() or "https://ordpresse.seame.click/api/v1/koreader/lookup"
    db = Database()
    raw_key, key_row = db.create_api_key(name)
    zip_bytes = build_koplugin_zip(name, raw_key, endpoint_url)
    slug = slugify_device_name(name)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="ordpresse-koplugin-{slug}.zip"',
            "X-Plugin-Version": PLUGIN_VERSION,
        },
    )


@app.post("/api/v1/keys/{key_id}/revoke")
def revoke_api_key_endpoint(key_id: int):
    """Revoke an active API key, blocking future lookup requests immediately."""
    db = Database()
    revoked = db.revoke_api_key(key_id)
    return {"status": "revoked" if revoked else "not_found", "id": key_id}


@app.delete("/api/v1/keys/{key_id}")
def delete_api_key_endpoint(key_id: int):
    """Permanently delete an API key record."""
    db = Database()
    deleted = db.delete_api_key(key_id)
    return {"status": "deleted" if deleted else "not_found", "id": key_id}


@app.get("/api/v1/store/catalog")
def get_store_catalog_admin():
    """List all plugins in the curated store catalog (LAN-Only, session protected)."""
    return get_catalog_json()


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=4, max_length=128)
    confirm_password: str = Field(..., min_length=4, max_length=128)


@app.post("/api/v1/auth/change-password")
def change_admin_password_endpoint(payload: ChangePasswordPayload):
    """Update administrator password in database (LAN-Only, session protected)."""
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="De to nye adgangskoder stemmer ikke overens.")

    db = Database()
    if not db.verify_admin_password(payload.current_password, fallback_password=ORDPRESSE_ADMIN_PASSWORD):
        raise HTTPException(status_code=400, detail="Den nuværende adgangskode er forkert.")

    db.set_admin_password(payload.new_password)
    return {"status": "ok", "message": "Adgangskoden er opdateret!"}


