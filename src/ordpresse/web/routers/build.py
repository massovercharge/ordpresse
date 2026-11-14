"""Router for dictionary build pipeline, SSE progress streaming, and Lyn-Henter peer support."""

import asyncio
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile
from typing import List, Optional
import uuid

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from ordpresse.config import DEFAULT_BOOKNAME, OUTPUT_DIR
from ordpresse.db import Database
from ordpresse.pipeline import Pipeline
from ordpresse.sources.ods_scraper import parse_ods_html
from ordpresse.sources.rate_limiter import coordinator
from ordpresse.web.state import ACTIVE_BUILDS, BuildJob, CacheSubmitPayload

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.post("/api/build")
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


@router.get("/api/build/events/{build_id}")
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


@router.post("/api/build/cancel/{build_id}")
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


@router.api_route("/api/download/{build_id}", methods=["GET", "HEAD"])
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


@router.get("/api/build/{build_id}/missing-words")
@router.get("/api/jobs/{build_id}/missing-words")
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


def _get_database():
    app_module = sys.modules.get("ordpresse.web.app")
    if app_module and hasattr(app_module, "Database"):
        return app_module.Database()
    return Database()


@router.post("/api/cache/submit")
async def submit_cache(payload: CacheSubmitPayload):
    """Receive scraped HTML from peer bookmarklet, parse, store in cache, and wake waiting builds."""
    word = payload.word.strip()
    html = payload.html.strip()
    build_id = payload.build_id.strip() if payload.build_id else None

    if not word or len(word) > 80:
        raise HTTPException(status_code=400, detail="Ugyldigt 'word'")
    if not html or len(html) > 500_000:
        raise HTTPException(status_code=400, detail="Ugyldigt 'html'")

    db = _get_database()
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


@router.post("/api/build/{build_id}/skip-queue")
@router.post("/api/jobs/{build_id}/skip-queue")
async def skip_queue(build_id: str):
    """Signal active build to skip remaining queue and finish compiling StarDict immediately."""
    coordinator.trigger_skip_remaining(build_id)
    return {
        "status": "skipping",
        "build_id": build_id,
    }


@router.get("/static/worker.js")
async def get_worker_js():
    """Serve worker.js directly."""
    worker_path = Path(__file__).parent.parent / "static" / "worker.js"
    if worker_path.exists():
        return FileResponse(worker_path, media_type="application/javascript")
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


@router.post("/api/build/{build_id}/start-server-scrape")
@router.post("/api/jobs/{build_id}/start-server-scrape")
async def start_server_scrape(build_id: str):
    """Explicitly switch build mode to server token-bucket scrape upon user request."""
    coordinator.set_build_mode(build_id, "server")
    return {"status": "server_scraping", "build_id": build_id}


@router.post("/api/build/{build_id}/set-mode")
@router.post("/api/jobs/{build_id}/set-mode")
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
