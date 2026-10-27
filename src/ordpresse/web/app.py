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
from fastapi.templating import Jinja2Templates

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

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Den Danske Ordpresse",
    description="Historisk og moderne dansk StarDict-ordbogsbygger til KOReader",
    version=__version__,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# In-memory build state manager for SSE streams
@dataclass
class BuildJob:
    build_id: str
    status: str = "queued"  # queued, building, done, error
    progress: int = 0
    message: str = "Venter i kø..."
    result: Optional[BuildResult] = None
    error: Optional[str] = None
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

    # 1. Check curated Nordic 1800s vocabulary (35+ Moberg and 19th c. terms)
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

    # 2. Try any existing compiled StarDict binary in data/output/ (newest first)
    dict_dirs = sorted(OUTPUT_DIR.glob("build_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for b_dir in dict_dirs:
        ifo = b_dir / "ordpresse.ifo"
        if ifo.exists():
            try:
                from ordpresse.stardict_reader import StarDictReader
                reader = StarDictReader(ifo)
                html = reader.lookup(word)
                if html:
                    if format == "html":
                        return HTMLResponse(content=html)
                    return {
                        "word": q,
                        "headword": word,
                        "found": True,
                        "html": html,
                        "source": "stardict_binary",
                    }
            except Exception as e:
                logger.debug(f"Reader failed on {ifo}: {e}")

    # 3. Check Database and Scraper
    from ordpresse.db import Database
    from ordpresse.scrapers import HistoricalScraper
    db = Database()
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
            include_seeds=not full_dictionary,
            full_dictionary=full_dictionary,
            allow_network=allow_network,
            progress_cb=sync_progress,
            build_id=job.build_id,
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
        if job.status in ("done", "error"):
            return

        while True:
            try:
                # Wait for next event with a 15-second heartbeat ping to keep Cloudflare happy
                msg = await asyncio.wait_for(job.events.get(), timeout=15.0)
                yield f"data: {msg}\n\n"
                parsed = json.loads(msg)
                if parsed.get("status") in ("done", "error"):
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


@app.api_route("/api/download/{build_id}", methods=["GET", "HEAD"])
async def download_dict(build_id: str):
    """Download the generated StarDict zip archive."""
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
