"""Router for HTML pages, login, session auth, stats, and healthcheck."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ordpresse import __version__
from ordpresse.config import (
    CACHE_DB_PATH,
    DATASETS_DB_PATH,
    DEFAULT_BOOKNAME,
)
from ordpresse.db import Database
from ordpresse.web.auth import (
    ORDPRESSE_ADMIN_PASSWORD,
    create_session_token,
    is_external_request,
    verify_session_token,
)
from ordpresse.web.plugin_builder import PLUGIN_RELEASE_NOTES, PLUGIN_VERSION
from ordpresse.web.plugin_store import get_catalog_json
from ordpresse.web.state import ChangePasswordPayload, templates

router = APIRouter()


@router.get("/health")
def healthcheck():
    """Healthcheck endpoint for Docker and monitoring."""
    return {
        "status": "ok",
        "version": __version__,
        "datasets_ready": DATASETS_DB_PATH.exists(),
        "cache_ready": CACHE_DB_PATH.exists(),
    }


@router.get("/login", response_class=HTMLResponse)
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


@router.post("/login", response_class=HTMLResponse)
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


@router.get("/logout")
@router.post("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key="ordpresse_session")
    return resp


@router.get("/", response_class=HTMLResponse)
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
            "store_catalog": get_catalog_json(),
            "datasets_ready": datasets_ready,
            "cache_ready": cache_ready,
            "default_bookname": DEFAULT_BOOKNAME,
        },
    )


@router.get("/api/stats")
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


@router.post("/api/v1/auth/change-password")
def change_admin_password_endpoint(payload: ChangePasswordPayload):
    """Update administrator password in database (LAN-Only, session protected)."""
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="De to nye adgangskoder stemmer ikke overens.")

    db = Database()
    if not db.verify_admin_password(payload.current_password, fallback_password=ORDPRESSE_ADMIN_PASSWORD):
        raise HTTPException(status_code=400, detail="Den nuværende adgangskode er forkert.")

    db.set_admin_password(payload.new_password)
    return {"status": "ok", "message": "Adgangskoden er opdateret!"}

