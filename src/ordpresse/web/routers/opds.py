"""Router for OPDS 1.2 Acquisition Feed & Direct Book Download for CrossPoint and e-readers."""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ordpresse.db import Database
from ordpresse.services.opds_service import generate_opds_feed, get_books_dir
from ordpresse.web.auth import extract_request_api_key, verify_api_key, verify_session_token
from ordpresse.web.state import mirror_prober

router = APIRouter()


@router.get("/opds")
@router.get("/api/v1/opds")
async def get_opds_catalog_endpoint(request: Request):
    """
    Standard OPDS 1.2 Acquisition Feed for CrossPoint Reader and KOReader.
    Accepts ?api_key= query param, X-API-Key header, HTTP Basic Auth, or session cookie.
    """
    db = Database()
    token = request.cookies.get("ordpresse_session")
    is_valid_session = token and verify_session_token(token)
    raw_key = extract_request_api_key(request)
    if not is_valid_session:
        verify_api_key(raw_key, db)

    books = db.list_downloaded_books()

    # Resolve base URL with external hostname preference
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "ordpresse.seame.click"
    proto = request.headers.get("x-forwarded-proto") or ("https" if "seame.click" in host else "http")
    base_url = f"{proto}://{host}"

    feed_xml = generate_opds_feed(books, base_url=base_url, api_key=raw_key)
    return Response(
        content=feed_xml,
        media_type="application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.get("/opds/download/{md5}")
async def opds_download_book_endpoint(md5: str, request: Request):
    """
    Direct authenticated OPDS book acquisition endpoint for CrossPoint and e-readers.
    Serves the exact cached binary EPUB file with Accept-Ranges: bytes.
    Accepts ?api_key= query param, X-API-Key header, HTTP Basic Auth, or session cookie.
    """
    cleaned_md5 = md5.strip().lower()
    if not re.match(r"^[0-9a-fA-F]{32}$", cleaned_md5):
        raise HTTPException(status_code=400, detail="Ugyldigt MD5-hash.")

    db = Database()
    token = request.cookies.get("ordpresse_session")
    is_valid_session = token and verify_session_token(token)
    raw_key = extract_request_api_key(request)
    if not is_valid_session:
        verify_api_key(raw_key, db)

    books_dir = get_books_dir()
    book = db.get_downloaded_book(cleaned_md5)

    if book and (books_dir / book["filename"]).is_file():
        file_path = books_dir / book["filename"]
        return FileResponse(
            file_path,
            media_type="application/epub+zip",
            filename=book["filename"],
            headers={
                "X-MD5": cleaned_md5,
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{book["filename"]}"',
            },
        )

    # Fallback: if not yet cached, resolve ticket and download
    ticket = await mirror_prober.resolve_download_ticket(cleaned_md5)
    if ticket.get("status") != "ok" or not ticket.get("download_url"):
        raise HTTPException(status_code=404, detail="Bogen blev ikke fundet i arkivet eller på aktive spejle.")

    from ordpresse.services.mirror_prober import BROWSER_UA
    import httpx

    fn = book["filename"] if book else f"{cleaned_md5}.epub"
    target_path = books_dir / fn
    async with httpx.AsyncClient(
        verify=False,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=15.0),
    ) as client:
        r = await client.get(ticket["download_url"])
        if r.status_code == 200 and len(r.content) > 1000:
            target_path.write_bytes(r.content)
            db.save_downloaded_book(
                md5=cleaned_md5,
                title=book["title"] if book else fn[:-5],
                author=book["author"] if book else "Ukendt",
                filename=fn,
                size_bytes=len(r.content),
            )
            return FileResponse(
                target_path,
                media_type="application/epub+zip",
                filename=fn,
                headers={
                    "X-MD5": cleaned_md5,
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": f'attachment; filename="{fn}"',
                },
            )
        raise HTTPException(status_code=502, detail="Fejl ved hentning af bog fra spejl.")
