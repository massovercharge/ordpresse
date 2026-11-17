"""FastAPI application for Den Danske Ordpresse with modular routers and Two-Zone defense."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ordpresse import __version__
from ordpresse.db import Database
from ordpresse.web.plugin_store import get_plugin_by_id
from ordpresse.sources.rate_limiter import coordinator
from ordpresse.web.auth import (
    ORDPRESSE_DISABLE_AUTH,
    is_external_request,
    verify_session_token,
)
from ordpresse.web.routers import (
    books,
    build,
    keys,
    koreader,
    opds,
    pages,
    preview,
    store,
)
from ordpresse.web.routers.build import submit_cache
from ordpresse.web.state import (
    ACTIVE_BUILDS,
    BuildJob,
    CacheSubmitPayload,
    ChangePasswordPayload,
    ProvisionKeyPayload,
    mirror_prober,
    templates,
)

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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.middleware("http")
async def enforce_two_zone_boundary_and_auth(request: Request, call_next):
    """
    Two-Zone Defense-in-Depth Middleware:
    - Layer 1 (External / Cloudflare Tunnel): ONLY /api/v1/koreader/lookup is accessible.
      All other endpoints return HTTP 404 Not Found.
    - Layer 2 (LAN Zone): Requires admin password login via session cookie for management UI/APIs.
    """
    path = request.url.path
    clean_path = path.rstrip("/")
    is_opds_or_lookup = (
        clean_path in (
            "/api/v1/koreader/lookup",
            "/api/v1/koreader/laundry",
            "/api/v1/koreader/sync/progress",
            "/api/v1/koreader/udklip/sync",
            "/api/v1/koreader/articles",
            "/opds",
            "/api/v1/opds",
            "/opds/articles",
            "/api/v1/opds/articles",
        )
        or path.startswith("/api/v1/koreader/articles/")
        or path.startswith("/opds/download/")
        or path.startswith("/opds/articles/download/")
    )

    # Layer 1: Strict External Zone Boundary
    if is_external_request(request):
        if not is_opds_or_lookup:
            return PlainTextResponse("Not Found", status_code=404)
        return await call_next(request)

    # Layer 2: LAN Zone
    if ORDPRESSE_DISABLE_AUTH:
        return await call_next(request)

    if request.method == "OPTIONS":
        return await call_next(request)

    # Unauthenticated / Self-authenticating LAN routes
    if (
        path in ("/login", "/logout", "/health")
        or path.startswith("/static/")
        or path.startswith("/api/books")
        or is_opds_or_lookup
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


# Include modular routers
app.include_router(pages.router)
app.include_router(preview.router)
app.include_router(build.router)
app.include_router(keys.router)
app.include_router(store.router)
app.include_router(books.router)
app.include_router(opds.router)
app.include_router(koreader.router)

# Re-exports for backward compatibility and test monkeypatching
__all__ = [
    "app",
    "Database",
    "get_plugin_by_id",
    "templates",
    "ACTIVE_BUILDS",
    "BuildJob",
    "CacheSubmitPayload",
    "ProvisionKeyPayload",
    "ChangePasswordPayload",
    "coordinator",
    "submit_cache",
    "mirror_prober",
]
