"""Zero-Trust authentication, session security, and Two-Zone network boundary utilities."""

import hashlib
import hmac
import ipaddress
import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, Request

from ordpresse.db import Database

logger = logging.getLogger(__name__)

ORDPRESSE_ADMIN_PASSWORD = os.getenv("ORDPRESSE_ADMIN_PASSWORD", "ordpresse")
if ORDPRESSE_ADMIN_PASSWORD == "ordpresse":
    logger.info("Sikkerhedsmeddelelse: ORDPRESSE_ADMIN_PASSWORD er sat til standard ('ordpresse').")

ORDPRESSE_SECRET_KEY = os.getenv("ORDPRESSE_SECRET_KEY", "")
if not ORDPRESSE_SECRET_KEY:
    ORDPRESSE_SECRET_KEY = hashlib.sha256(
        f"ordpresse_salt_{ORDPRESSE_ADMIN_PASSWORD}".encode()
    ).hexdigest()

ORDPRESSE_DISABLE_AUTH = os.getenv("ORDPRESSE_DISABLE_AUTH", "0") == "1"


def create_session_token() -> str:
    """Generate a signed session cookie valid for 30 days: f'{issued_ts}.{hmac_sha256}'."""
    issued_ts = int(time.time())
    sig = hmac.new(
        ORDPRESSE_SECRET_KEY.encode(),
        str(issued_ts).encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{issued_ts}.{sig}"


def verify_session_token(token: Optional[str]) -> bool:
    """Verify that a session token was signed with ORDPRESSE_SECRET_KEY and is not expired."""
    if not token or "." not in token:
        return False
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    ts_str, sig = parts
    try:
        issued_ts = int(ts_str)
    except ValueError:
        return False

    now = int(time.time())
    # Valid for 30 days (30 * 86400 seconds), allow 300s clock drift in future
    if issued_ts > now + 300 or (now - issued_ts) > (30 * 86400):
        return False

    expected_sig = hmac.new(
        ORDPRESSE_SECRET_KEY.encode(),
        ts_str.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def is_external_request(request: Request) -> bool:
    """
    Detect whether a request originates externally (e.g. via Cloudflare Tunnel or public IP).

    A request is External if:
    1. The header CF-Connecting-IP or Cf-Ray is present, OR
    2. request.client.host is NOT a private/local IP (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1, or testclient).
    """
    headers = request.headers
    if "cf-connecting-ip" in headers or "cf-ray" in headers:
        return True

    client = request.client
    if not client or not client.host:
        return True

    host = client.host.strip()
    if host in ("testclient", "localhost"):
        return False

    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback)
    except ValueError:
        return True


def verify_api_key(raw_key: Optional[str], db: Database) -> dict:
    """
    Verify an X-API-Key header in constant time.
    Increments lookup_count and sets last_used_at on success.
    Raises HTTPException(401) on failure.
    """
    if not raw_key or not isinstance(raw_key, str):
        raise HTTPException(status_code=401, detail="Manglende eller ugyldig API-nøgle.")

    clean_key = raw_key.strip()
    if not clean_key.startswith("ord_") or len(clean_key) > 128:
        raise HTTPException(status_code=401, detail="Manglende eller ugyldig API-nøgle.")

    candidate_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()
    key_row = db.get_api_key_by_hash(candidate_hash)

    if not key_row or not key_row.get("is_active"):
        raise HTTPException(status_code=401, detail="Ugyldig eller tilbagekaldt API-nøgle.")

    # Constant-time comparison
    if not hmac.compare_digest(key_row["key_hash"], candidate_hash):
        raise HTTPException(status_code=401, detail="Ugyldig eller tilbagekaldt API-nøgle.")

    db.record_api_key_usage(key_row["id"])
    return key_row

