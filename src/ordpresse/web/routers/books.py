"""Router for Central Book Archive management."""

from fastapi import APIRouter, HTTPException, Request

from ordpresse.db import Database
from ordpresse.services.opds_service import get_books_dir
from ordpresse.web.auth import extract_request_api_key, verify_api_key, verify_session_token

router = APIRouter()


@router.get("/api/books")
def api_list_books(request: Request):
    """List all downloaded books in the local archive (session or API key protected)."""
    db = Database()
    token = request.cookies.get("ordpresse_session")
    is_valid_session = token and verify_session_token(token)
    if not is_valid_session:
        raw_key = extract_request_api_key(request)
        if raw_key:
            verify_api_key(raw_key, db)
        else:
            raise HTTPException(status_code=401, detail="Log venligst ind for at se bogarkivet.")
    return db.list_downloaded_books()


@router.delete("/api/books/{md5}")
def api_delete_book(md5: str, request: Request):
    """Delete a book from database and physical disk (admin session required)."""
    token = request.cookies.get("ordpresse_session")
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=401, detail="Kræver administrator-adgang.")

    cleaned_md5 = md5.strip().lower()
    db = Database()
    book = db.get_downloaded_book(cleaned_md5)
    if book:
        books_dir = get_books_dir()
        target_file = books_dir / book["filename"]
        target_file.unlink(missing_ok=True)
        db.delete_downloaded_book(cleaned_md5)
        return {"status": "ok", "message": f"Bogen '{book['title']}' blev slettet fra arkivet."}
    return {"status": "ok", "message": "Bogen fandtes ikke i arkivet."}

