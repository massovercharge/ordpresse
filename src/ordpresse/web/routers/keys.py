"""Router for KOReader Device Provisioning and API Keys Management."""

from fastapi import APIRouter, HTTPException, Response

from ordpresse.db import Database
from ordpresse.web.plugin_builder import (
    PLUGIN_VERSION,
    build_koplugin_zip,
    slugify_device_name,
)
from ordpresse.web.state import ProvisionKeyPayload

router = APIRouter()


@router.get("/api/v1/keys")
def get_api_keys():
    """List all registered API keys (LAN-Only, session protected). Never exposes key hashes."""
    db = Database()
    return db.list_api_keys()


@router.post("/api/v1/keys/provision")
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
    raw_key, key_row = db.create_api_key(name, custom_key=payload.custom_key)
    zip_bytes = build_koplugin_zip(name, raw_key, endpoint_url)
    slug = slugify_device_name(name)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="ordpresse-koplugin-{slug}.zip"',
            "X-Plugin-Version": PLUGIN_VERSION,
            "X-Raw-Api-Key": raw_key,
        },
    )


@router.post("/api/v1/keys/{key_id}/revoke")
def revoke_api_key_endpoint(key_id: int):
    """Revoke an active API key, blocking future lookup requests immediately."""
    db = Database()
    revoked = db.revoke_api_key(key_id)
    return {"status": "revoked" if revoked else "not_found", "id": key_id}


@router.delete("/api/v1/keys/{key_id}")
def delete_api_key_endpoint(key_id: int):
    """Permanently delete an API key record."""
    db = Database()
    deleted = db.delete_api_key(key_id)
    return {"status": "deleted" if deleted else "not_found", "id": key_id}

