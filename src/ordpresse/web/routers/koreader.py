"""Router for KOReader Online Lookup and Anna Fetch / Store Orchestration."""

import logging
from pathlib import Path
import re
import sys
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ordpresse.db import Database
from ordpresse.services.epub_laundry import LaundryOptions
from ordpresse.services.opds_service import canonical_filename, get_books_dir
from ordpresse.web.auth import verify_api_key
from ordpresse.web.lookup_service import perform_koreader_lookup
from ordpresse.web.plugin_store import build_annas_fetch_zip, get_catalog_json, get_plugin_by_id, get_plugin_zip
from ordpresse.web.state import laundry_service, mirror_prober

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_plugin_by_id(plugin_id: str):
    app_module = sys.modules.get("ordpresse.web.app")
    if app_module and hasattr(app_module, "get_plugin_by_id"):
        return app_module.get_plugin_by_id(plugin_id)
    return get_plugin_by_id(plugin_id)


@router.get("/api/v1/koreader/lookup")
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
        from ordpresse.web.plugin_builder import PLUGIN_RELEASE_NOTES, PLUGIN_VERSION
        return {
            "latest_version": PLUGIN_VERSION,
            "release_notes": PLUGIN_RELEASE_NOTES,
        }
    elif action == "download_update":
        import hashlib
        from ordpresse.web.plugin_builder import BRIDGE_LUA, MAIN_LUA, META_LUA, PLUGIN_VERSION
        return {
            "version": PLUGIN_VERSION,
            "main_lua": MAIN_LUA,
            "meta_lua": META_LUA,
            "bridge_lua": BRIDGE_LUA,
            "sha256": hashlib.sha256(MAIN_LUA.encode("utf-8")).hexdigest(),
        }
    elif action == "annas_mirrors":
        refresh = request.query_params.get("refresh", "false").lower() in ("true", "1", "yes")
        payload = await mirror_prober.get_mirrors(force_refresh=refresh)
        return JSONResponse(content=payload)
    elif action == "annas_search":
        q = request.query_params.get("q") or request.query_params.get("query") or ""
        cat = request.query_params.get("category") or request.query_params.get("topics") or "all_books"
        langs = request.query_params.getlist("lang") or request.query_params.getlist("language")
        if not langs:
            single_lang = request.query_params.get("lang") or request.query_params.get("language")
            if single_lang:
                langs = [single_lang]
        payload = await mirror_prober.search_books(q, category=cat, languages=langs)
        return JSONResponse(content=payload)
    elif action == "annas_download_ticket":
        md5 = request.query_params.get("md5", "").strip()
        payload = await mirror_prober.resolve_download_ticket(md5)
        return JSONResponse(content=payload)
    elif action == "annas_download":
        md5 = request.query_params.get("md5", "").strip().lower()
        if not re.match(r"^[0-9a-fA-F]{32}$", md5):
            raise HTTPException(status_code=400, detail="Ugyldigt MD5-hash.")

        raw_title = request.query_params.get("title", "").strip()
        raw_author = request.query_params.get("author", "").strip()
        raw_fn = request.query_params.get("filename", "").strip()

        books_dir = get_books_dir()

        # 1. Check if already cached in database and on disk
        existing = db.get_downloaded_book(md5)
        if existing and (books_dir / existing["filename"]).is_file():
            target_path = books_dir / existing["filename"]
            return FileResponse(
                target_path,
                media_type="application/epub+zip",
                filename=existing["filename"],
                headers={
                    "X-MD5": md5,
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": f'attachment; filename="{existing["filename"]}"',
                },
            )

        # 2. Determine standardized filename
        if raw_fn and raw_fn.lower().endswith(".epub"):
            fn = canonical_filename(raw_fn[:-5], "", "epub")
        else:
            fn = canonical_filename(raw_title or "Book", raw_author, "epub")

        target_path = books_dir / fn
        if target_path.is_file():
            db.save_downloaded_book(
                md5=md5,
                title=raw_title or fn[:-5].replace("_", " "),
                author=raw_author or "Ukendt",
                filename=fn,
                size_bytes=target_path.stat().st_size,
            )
            return FileResponse(
                target_path,
                media_type="application/epub+zip",
                filename=fn,
                headers={
                    "X-MD5": md5,
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": f'attachment; filename="{fn}"',
                },
            )

        # 3. Resolve ticket and stream from mirror while caching to disk
        ticket_res = await mirror_prober.resolve_download_ticket(md5)
        if ticket_res.get("status") != "ok" or not ticket_res.get("download_url"):
            raise HTTPException(status_code=404, detail="Kunne ikke finde aktivt download-link.")

        download_url = ticket_res["download_url"]
        from ordpresse.services.mirror_prober import BROWSER_UA
        import httpx

        temp_part = books_dir / f"{md5}.part"

        async def stream_and_cache_generator():
            p_file = None
            try:
                p_file = open(temp_part, "wb")
            except Exception as e:
                logger.warning(f"Kunne ikke åbne temp-fil til bogarkiv: {e}")

            total_bytes = 0
            async with httpx.AsyncClient(
                verify=False,
                headers={"User-Agent": BROWSER_UA},
                follow_redirects=True,
                timeout=httpx.Timeout(120.0, connect=15.0),
            ) as client:
                async with client.stream("GET", download_url) as resp:
                    if resp.status_code != 200:
                        if p_file:
                            p_file.close()
                            temp_part.unlink(missing_ok=True)
                        raise HTTPException(status_code=502, detail="Fejl ved hentning fra spejl.")
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if p_file:
                            p_file.write(chunk)
                        total_bytes += len(chunk)
                        yield chunk

            if p_file:
                p_file.close()
                if total_bytes > 1000:
                    if fn.lower().endswith(".epub"):
                        try:
                            epub_raw = temp_part.read_bytes()
                            wash_res = await laundry_service.process_epub(
                                epub_raw, LaundryOptions(heal_ocr_paragraphs=False)
                            )
                            temp_part.write_bytes(wash_res.epub_bytes)
                            total_bytes = len(wash_res.epub_bytes)
                        except Exception as e:
                            logger.warning(f"Standard bogvask i download fejlede: {e}")
                    temp_part.replace(target_path)
                    db.save_downloaded_book(
                        md5=md5,
                        title=raw_title or fn[:-5].replace("_", " "),
                        author=raw_author or "Ukendt",
                        filename=fn,
                        size_bytes=total_bytes,
                    )
                else:
                    temp_part.unlink(missing_ok=True)

        return StreamingResponse(
            stream_and_cache_generator(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{fn}"',
                "X-MD5": md5,
            },
        )
    elif action == "store_catalog":
        return JSONResponse(content=get_catalog_json())
    elif action == "store_download":
        plugin_id = request.query_params.get("id", "").strip()
        plugin = _get_plugin_by_id(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' ikke fundet i kataloget.")

        # 1. Tjek om pakken distribueres lokalt fra vendor/ (fx annas-fetch, hyph-da, font-chareink)
        local_zip = get_plugin_zip(plugin_id)
        if local_zip:
            return Response(
                content=local_zip,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{plugin.koplugin_name}.zip"',
                    "X-Plugin-Id": plugin.id,
                    "X-Plugin-Version": plugin.version,
                    "X-Koplugin-Name": plugin.koplugin_name,
                },
            )

        if not plugin.zipball_url:
            raise HTTPException(status_code=404, detail=f"Pakken '{plugin_id}' mangler download-kilde.")

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


def find_existing_book_in_archive(
    books_dir: Path,
    db: Database,
    x_book_md5: Optional[str] = None,
    x_book_filename: Optional[str] = None,
) -> Optional[bytes]:
    """Finder en eksisterende bog i data/books/ ud fra MD5, partial MD5 eller filnavn."""
    if not books_dir.is_dir():
        return None

    # 1. Match via database opslag
    if x_book_md5:
        clean_md5 = x_book_md5.strip().lower()
        rec = db.get_downloaded_book(clean_md5)
        if rec and (books_dir / rec["filename"]).is_file():
            try:
                return (books_dir / rec["filename"]).read_bytes()
            except Exception:
                pass

    # 2. Match direkte på filnavn
    if x_book_filename:
        clean_fn = Path(x_book_filename).name
        target = books_dir / clean_fn
        if target.is_file():
            try:
                return target.read_bytes()
            except Exception:
                pass

    # 3. Match mod filer på disken via fuld MD5 eller KOReader partial MD5
    if x_book_md5:
        clean_md5 = x_book_md5.strip().lower()
        import hashlib
        for p in books_dir.glob("*.epub"):
            if not p.is_file():
                continue
            try:
                content = p.read_bytes()
                if hashlib.md5(content).hexdigest().lower() == clean_md5:
                    return content
                sz = len(content)
                if sz <= 3072:
                    p_bytes = content
                else:
                    mid = sz // 2
                    p_bytes = content[:1024] + content[mid:mid + 1024] + content[-1024:]
                if hashlib.md5(p_bytes).hexdigest().lower() == clean_md5:
                    return content
            except Exception:
                continue

    return None


@router.post("/api/v1/koreader/laundry")
async def run_epub_laundry_endpoint(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_book_md5: Optional[str] = Header(default=None),
    x_book_filename: Optional[str] = Header(default=None),
    x_laundry_heal_ocr: str = Header(default="true"),
    x_laundry_quote_style: str = Header(default="guillemets"),
):
    """
    Kører Ordpresse Bogvaskeri & Restaurerings-pipeline (v2.0) for KOReader.
    Understøtter zero-upload fast-path via X-Book-MD5 / X-Book-Filename og chunked streaming.
    """
    api_key = (
        x_api_key
        or request.headers.get("X-API-Key")
        or request.headers.get("x-api-key")
        or request.query_params.get("api_key")
    )
    db = Database()
    verify_api_key(api_key, db)

    raw_bytes: Optional[bytes] = None
    books_dir = get_books_dir()

    # 1. Tjek om bogen allerede ligger i serverens data/books/ (0 bytes upload!)
    if x_book_md5 or x_book_filename:
        raw_bytes = find_existing_book_in_archive(books_dir, db, x_book_md5, x_book_filename)

    # 2. Ellers modtag den streamede EPUB-body fra KOReader
    if raw_bytes is None:
        raw_bytes = await request.body()
        if len(raw_bytes) < 100 or not raw_bytes.startswith(b"PK\x03\x04"):
            raise HTTPException(
                status_code=404 if (x_book_md5 or x_book_filename) else 400,
                detail="Bogen findes ikke på serveren, eller uploadet fil er ikke en gyldig EPUB.",
            )

    opts = LaundryOptions(
        heal_ocr_paragraphs=(str(x_laundry_heal_ocr).lower() in ("true", "1", "yes")),
        rebuild_missing_toc=(str(x_laundry_heal_ocr).lower() in ("true", "1", "yes")),
        danish_quote_style="baseline" if str(x_laundry_quote_style).lower() == "baseline" else "guillemets",
    )

    result = await laundry_service.process_epub(raw_bytes, opts)

    # Gem/opdater den vaskede master-kopi i data/books/ så KOReader og CrossPoint (OPDS)
    # altid deler den nøjagtig samme binære fil til læsesynkronisering (CrossPoint Sync)
    if x_book_filename or x_book_md5:
        target_fn = Path(x_book_filename).name if x_book_filename else None
        if not target_fn and x_book_md5:
            rec = db.get_downloaded_book(x_book_md5.strip().lower())
            if rec:
                target_fn = rec.get("filename")
        if target_fn:
            target_file = books_dir / target_fn
            try:
                target_file.write_bytes(result.epub_bytes)
                db.save_downloaded_book(
                    md5=result.book_md5,
                    title=target_file.stem.replace("_", " "),
                    author="Ukendt",
                    filename=target_fn,
                    size_bytes=len(result.epub_bytes),
                )
                if x_book_md5 and x_book_md5.strip().lower() != result.book_md5:
                    db.save_downloaded_book(
                        md5=x_book_md5.strip().lower(),
                        title=target_file.stem.replace("_", " "),
                        author="Ukendt",
                        filename=target_fn,
                        size_bytes=len(result.epub_bytes),
                    )
            except Exception as e:
                logger.warning(f"Kunne ikke synkronisere vasket bog til server-arkiv: {e}")

    return Response(
        content=result.epub_bytes,
        media_type="application/epub+zip",
        headers={
            "X-Ordpresse-Lang": result.detected_language,
            "X-Ordpresse-MD5": result.book_md5,
            "X-Ordpresse-Size-Before-KB": str(result.original_size_kb),
            "X-Ordpresse-Size-After-KB": str(result.polished_size_kb),
            "X-Ordpresse-Paragraphs-Healed": str(result.paragraphs_healed),
            "X-Ordpresse-TOC-Rebuilt": str(result.toc_entries_rebuilt),
            "X-Ordpresse-Prewarmed-Words": str(result.prewarmed_words_count),
            "X-Ordpresse-Time-Ms": str(result.processing_ms),
        },
    )

