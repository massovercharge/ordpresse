"""Router for Plugin & Patch Store catalog, Mirror Prober, Web-to-EPUB Articles, Udklip & Sync."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ordpresse.db import Database
from ordpresse.services.opds_service import get_articles_dir
from ordpresse.services.web_to_epub import create_washed_article_epub
from ordpresse.web.plugin_store import (
    build_annas_fetch_zip,
    fetch_and_flatten_external_plugin,
    get_catalog_json,
    get_patch_bytes,
    get_plugin_by_id,
    get_plugin_zip,
)
from ordpresse.web.state import laundry_service, mirror_prober

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveArticlePayload(BaseModel):
    url: str = ""
    raw_content: str = ""
    title: str = ""
    author: str = ""


@router.get("/api/v1/store/catalog")
def get_store_catalog_admin():
    """List all plugins, patches and resources in the curated store catalog (LAN-Only)."""
    return get_catalog_json()


@router.get("/api/v1/store/mirrors")
async def get_store_mirrors_admin(refresh: bool = False):
    """Get active mirror health and latency metrics (LAN-Only, session protected)."""
    return await mirror_prober.get_mirrors(force_refresh=refresh)


@router.get("/api/v1/store/search")
async def search_store_books_admin(q: str = "", category: str = "all_books", lang: Optional[str] = None):
    """Test server-assisted book search against active mirrors (LAN-Only, session protected)."""
    languages = [lang] if lang and lang != "all" else None
    return await mirror_prober.search_books(q, category=category, languages=languages)


@router.get("/api/v1/store/download/annas-fetch")
def download_annas_fetch_admin():
    """Direct web browser download of curated annas-fetch.koplugin.zip."""
    plugin = get_plugin_by_id("annas-fetch")
    plugin_version = plugin.version if plugin else "1.2.7-ordpresse"
    try:
        zip_bytes = build_annas_fetch_zip()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="annas-fetch.koplugin.zip"',
            "X-Plugin-Id": "annas-fetch",
            "X-Plugin-Version": plugin_version,
        },
    )


@router.get("/api/v1/store/download/{plugin_id}")
async def download_store_plugin(plugin_id: str):
    """Download a plugin, user patch (.lua), resource, or flattened GitHub release by ID."""
    plugin = get_plugin_by_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' ikke fundet i kataloget.")

    # 0. KOReader User Patch (.lua)
    if plugin.target_type == "patch":
        patch_bytes = get_patch_bytes(plugin_id)
        if not patch_bytes:
            raise HTTPException(status_code=404, detail=f"Patch-filen for '{plugin_id}' blev ikke fundet.")
        return Response(
            content=patch_bytes,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{plugin.koplugin_name}"',
                "X-Plugin-Id": plugin.id,
                "X-Plugin-Version": plugin.version,
                "X-Koplugin-Name": plugin.koplugin_name,
                "X-Package-Type": "patch",
            },
        )

    # 1. Tjek om pakken distribueres lokalt fra vendor/
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
                "X-Package-Type": plugin.target_type,
            },
        )

    if not plugin.zipball_url:
        raise HTTPException(status_code=404, detail=f"Pakken '{plugin_id}' mangler download-kilde.")

    try:
        flattened_zip = await fetch_and_flatten_external_plugin(plugin)
    except Exception as e:
        logger.error(f"Plugin store: Fejl ved download/udfladning af {plugin_id} fra GitHub: {e}")
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente plugin fra GitHub: {e}")

    return Response(
        content=flattened_zip,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{plugin.koplugin_name}.zip"',
            "X-Plugin-Id": plugin.id,
            "X-Plugin-Version": plugin.version,
            "X-Koplugin-Name": plugin.koplugin_name,
            "X-Package-Type": plugin.target_type,
        },
    )


@router.delete("/api/v1/store/packages/{plugin_id}")
def guard_core_plugin_uninstall(plugin_id: str):
    """API-niveau sikkerhedslås: blokerer ethvert forsøg på at afinstallere kerne-pluginet 'ordpresse'."""
    clean_id = plugin_id.strip().lower()
    if clean_id in ("ordpresse", "ordpresse.koplugin"):
        raise HTTPException(
            status_code=403,
            detail="Kerne-pluginet 'Den Danske Ordpresse' kan ikke afinstalleres.",
        )
    plugin = get_plugin_by_id(clean_id)
    if plugin and not plugin.can_uninstall:
        raise HTTPException(
            status_code=403,
            detail=f"Pakken '{plugin.id}' er beskyttet og kan ikke afinstalleres.",
        )
    return {"status": "allowed", "plugin_id": clean_id}


# -------------------------------------------------------------------
# v1.6.0: Web-to-EPUB ('Læs Senere'), Udklip & Læse-Synk (LAN Web UI)
# -------------------------------------------------------------------


@router.post("/api/v1/articles")
async def save_web_article_admin(payload: SaveArticlePayload):
    """Konverter en artikel-URL eller indsat tekst/HTML til en vasket E-Ink EPUB til 'Læs Senere'."""
    db = Database()
    try:
        record = await create_washed_article_epub(
            db=db,
            laundry_service=laundry_service,
            url=payload.url,
            raw_content=payload.raw_content,
            title=payload.title,
            author=payload.author,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Fejl ved Web-til-EPUB konvertering: {exc}")
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente eller vaske artiklen: {exc}") from exc

    return {"status": "ok", "article": record}


@router.get("/api/v1/articles")
def list_web_articles_admin(unread_only: bool = False):
    """List alle gemte Web-til-EPUB artikler."""
    db = Database()
    return {"articles": db.list_articles(unread_only=unread_only)}


@router.delete("/api/v1/articles/{md5}")
def delete_web_article_admin(md5: str):
    """Slet en gemt Web-til-EPUB artikel fra disk og database."""
    db = Database()
    article = db.get_article(md5)
    if article:
        target = get_articles_dir() / article["filename"]
        target.unlink(missing_ok=True)
        db.delete_article(md5)
    return {"status": "ok", "md5": md5.strip().lower()}


@router.get("/api/v1/udklip")
def list_udklip_admin():
    """Returner synkroniserede højdepunkter/noter og glosebog-ord til Web UI."""
    db = Database()
    return {
        "highlights": db.list_reader_highlights(),
        "vocab_words": db.list_reader_vocab(),
    }


@router.get("/api/v1/sync/progress")
def list_reading_progress_admin():
    """Returner seneste synkroniserede læsepositioner på tværs af enheder."""
    db = Database()
    return {"progress": db.list_reading_progress()}
