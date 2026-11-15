"""Router for Central Book Archive management, manual EPUB upload, and Bogvaskeri."""

import hashlib
import io
from pathlib import Path
import re
import zipfile

from bs4 import BeautifulSoup
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ordpresse.db import Database
from ordpresse.services.epub_laundry import LaundryOptions
from ordpresse.services.opds_service import canonical_filename, get_books_dir
from ordpresse.web.auth import extract_request_api_key, verify_api_key, verify_session_token
from ordpresse.web.state import laundry_service

router = APIRouter()


def extract_epub_metadata(epub_bytes: bytes, fallback_filename: str = "") -> tuple[str, str]:
    """Extract (title, author) from EPUB OPF metadata or fallback to clean filename stem."""
    title = ""
    author = ""
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
            opf_path = None
            if "META-INF/container.xml" in zf.namelist():
                container = zf.read("META-INF/container.xml").decode("utf-8", errors="ignore")
                m = re.search(r'full-path=["\']([^"\']+\.opf)["\']', container, re.I)
                if m:
                    opf_path = m.group(1).strip()
            if not opf_path:
                for name in zf.namelist():
                    if name.lower().endswith(".opf"):
                        opf_path = name
                        break
            if opf_path and opf_path in zf.namelist():
                opf_data = zf.read(opf_path).decode("utf-8", errors="ignore")
                try:
                    soup = BeautifulSoup(opf_data, "xml")
                except Exception:
                    soup = BeautifulSoup(opf_data, "html.parser")
                t_elem = soup.find("dc:title") or soup.find("title")
                if t_elem and t_elem.get_text(strip=True):
                    title = t_elem.get_text(strip=True)
                c_elem = soup.find("dc:creator") or soup.find("creator") or soup.find("author")
                if c_elem and c_elem.get_text(strip=True):
                    author = c_elem.get_text(strip=True)
    except Exception:
        pass

    if not title:
        stem = Path(fallback_filename).stem if fallback_filename else "Ukendt titel"
        title = stem.replace("_", " ").strip() or "Ukendt titel"
    if not author:
        author = "Ukendt forfatter"

    return title, author


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


@router.post("/api/books/upload")
async def api_upload_book(
    request: Request,
    file: UploadFile = File(...),
    wash: bool = Form(default=True),
):
    """
    Upload an EPUB book to the central archive.
    If wash=True (default), the EPUB is automatically processed through
    Bogvaskeri v2.0 (DOM OCR paragraph healing, Danish typography, CSS sanitizing,
    e-ink image downsampling, and pre-warmed lexical dictionary cache).
    Saves to data/books/<canonical_filename> and registers in OPDS downloaded_books.
    """
    db = Database()
    token = request.cookies.get("ordpresse_session")
    is_valid_session = token and verify_session_token(token)
    if not is_valid_session:
        raw_key = extract_request_api_key(request)
        if raw_key:
            verify_api_key(raw_key, db)
        else:
            raise HTTPException(status_code=401, detail="Log venligst ind for at uploade bøger.")

    filename = file.filename or "book.epub"
    if not filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Kun .epub filer understøttes.")

    raw_bytes = await file.read()
    if len(raw_bytes) < 100 or not raw_bytes.startswith(b"PK\x03\x04"):
        raise HTTPException(status_code=400, detail="Filen er ikke en gyldig EPUB-fil (ugyldig ZIP header).")

    books_dir = get_books_dir()
    laundry_stats = None

    if wash:
        opts = LaundryOptions(
            heal_ocr_paragraphs=True,
            rebuild_missing_toc=True,
            danish_quote_style="guillemets",
            generate_lexical_cache=True,
        )
        result = await laundry_service.process_epub(raw_bytes, opts)
        final_bytes = result.epub_bytes
        final_md5 = result.book_md5
        laundry_stats = {
            "paragraphs_healed": result.paragraphs_healed,
            "toc_entries_rebuilt": result.toc_entries_rebuilt,
            "prewarmed_words": result.prewarmed_words_count,
            "detected_language": result.detected_language,
            "original_size_kb": result.original_size_kb,
            "polished_size_kb": result.polished_size_kb,
            "processing_ms": result.processing_ms,
        }
    else:
        final_bytes = raw_bytes
        final_md5 = hashlib.md5(raw_bytes).hexdigest()

    title, author = extract_epub_metadata(final_bytes, fallback_filename=filename)
    clean_fn = canonical_filename(title, author, ext="epub")
    target_path = books_dir / clean_fn
    target_path.write_bytes(final_bytes)

    db.save_downloaded_book(
        md5=final_md5,
        title=title,
        author=author,
        filename=clean_fn,
        size_bytes=len(final_bytes),
        download_source="web_upload",
    )

    return {
        "status": "ok",
        "message": f"Bogen '{title}' af {author} er uploadet{' og vasket' if wash else ''}!",
        "book": {
            "md5": final_md5,
            "title": title,
            "author": author,
            "filename": clean_fn,
            "size_bytes": len(final_bytes),
            "washed": wash,
            "laundry": laundry_stats,
        },
    }


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
