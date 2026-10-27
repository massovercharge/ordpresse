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
            include_seeds=True,
            full_dictionary=full_dictionary,
            allow_network=allow_network,
            progress_cb=sync_progress,
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

    # Run build in a background thread/task to avoid blocking FastAPI event loop
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _run_background_build,
        job,
        saved_paths,
        parsed_words,
        bookname,
        full_dictionary,
        allow_network,
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
        # First send initial status
        initial_data = json.dumps(
            {
                "status": job.status,
                "percent": job.progress,
                "message": job.message,
            }
        )
        yield f"data: {initial_data}\n\n"

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
