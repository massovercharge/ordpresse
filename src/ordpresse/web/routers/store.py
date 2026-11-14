"""Router for Plugin Store catalog, Mirror Prober, and direct plugin downloads."""

import logging
from fastapi import APIRouter, HTTPException, Response

from ordpresse.web.plugin_store import build_annas_fetch_zip, get_catalog_json, get_plugin_by_id
from ordpresse.web.state import mirror_prober

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/store/catalog")
def get_store_catalog_admin():
    """List all plugins in the curated store catalog (LAN-Only, session protected)."""
    return get_catalog_json()


@router.get("/api/v1/store/mirrors")
async def get_store_mirrors_admin(refresh: bool = False):
    """Get active mirror health and latency metrics (LAN-Only, session protected)."""
    return await mirror_prober.get_mirrors(force_refresh=refresh)


@router.get("/api/v1/store/search")
async def search_store_books_admin(q: str = "", category: str = "all_books"):
    """Test server-assisted book search against active mirrors (LAN-Only, session protected)."""
    return await mirror_prober.search_books(q, category=category)


@router.get("/api/v1/store/download/annas-fetch")
def download_annas_fetch_admin():
    """Direct web browser download of curated annas-fetch.koplugin.zip."""
    plugin = get_plugin_by_id("annas-fetch")
    plugin_version = plugin.version if plugin else "1.2.6-ordpresse"
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
    """Download or proxy-download a plugin by ID from catalog."""
    plugin = get_plugin_by_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' ikke fundet i kataloget.")

    if plugin_id == "annas-fetch":
        return download_annas_fetch_admin()

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

