"""Router for KOReader Online Lookup and Anna Fetch / Store Orchestration."""

import logging
import re
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ordpresse.db import Database
from ordpresse.services.opds_service import canonical_filename, get_books_dir
from ordpresse.web.auth import verify_api_key
from ordpresse.web.lookup_service import perform_koreader_lookup
from ordpresse.web.plugin_store import build_annas_fetch_zip, get_catalog_json, get_plugin_by_id
from ordpresse.web.state import mirror_prober

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
