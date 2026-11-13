import base64
from pathlib import Path
from starlette.testclient import TestClient

from ordpresse.db import Database
from ordpresse.services.opds_service import (
    canonical_filename,
    format_size_human,
    generate_opds_feed,
    get_books_dir,
)
from ordpresse.web.app import app
from ordpresse.web.auth import create_session_token


def test_canonical_filename_formatting():
    # Simple title and author
    assert canonical_filename("Frankenstein", "Mary Shelley") == "Frankenstein_Mary_Shelley.epub"
    # Special characters stripped, whitespace converted to underscores
    assert canonical_filename("Udvandrerne (Del 1)", "Vilhelm Moberg!") == "Udvandrerne_Del_1_Vilhelm_Moberg.epub"
    # Extension handling
    assert canonical_filename("Titel", "Forfatter", ext="pdf") == "Titel_Forfatter.pdf"
    # Long title/author truncated to max limits
    long_title = "A" * 100
    long_author = "B" * 60
    fn = canonical_filename(long_title, long_author)
    assert len(fn) <= 60 + 1 + 40 + 5
    assert fn.endswith(".epub")
    # Empty fallbacks
    assert canonical_filename("", "") == "Book.epub"


def test_format_size_human():
    assert format_size_human(0) == "0 KB"
    assert format_size_human(-10) == "0 KB"
    assert format_size_human(512 * 1024) == "512 KB"
    assert format_size_human(1572864) == "1.5 MB"
    assert format_size_human(10 * 1024 * 1024) == "10.0 MB"


def test_generate_opds_feed_xml():
    books = [
        {
            "md5": "e5e5e721582d86e5b5295b6f78fe4256",
            "title": "Frankenstein & The Modern Prometheus",
            "author": "Mary Shelley <Author>",
            "filename": "Frankenstein_Mary_Shelley.epub",
            "size_bytes": 1048576,
            "format": "epub",
            "downloaded_at": "2026-11-13T20:00:00Z",
        }
    ]
    base_url = "https://ordpresse.seame.click"
    api_key = "ord_testapikey123"

    feed = generate_opds_feed(books, base_url=base_url, api_key=api_key)

    # Check XML root and namespaces
    assert '<?xml version="1.0" encoding="utf-8"?>' in feed
    assert '<feed xmlns="http://www.w3.org/2005/Atom"' in feed
    assert 'xmlns:dc="http://purl.org/dc/terms/"' in feed
    assert 'xmlns:opds="http://opds-spec.org/2010/catalog"' in feed

    # Check feed metadata and self link with api_key
    assert f'<link rel="self" href="https://ordpresse.seame.click/opds?api_key={api_key}"' in feed
    assert f'<link rel="start" href="https://ordpresse.seame.click/opds?api_key={api_key}"' in feed

    # Check entry content
    assert "<title>Frankenstein &amp; The Modern Prometheus</title>" in feed
    assert "<name>Mary Shelley &lt;Author&gt;</name>" in feed
    assert "<id>urn:ordpresse:book:e5e5e721582d86e5b5295b6f78fe4256</id>" in feed
    assert 'rel="http://opds-spec.org/acquisition"' in feed
    assert f'href="https://ordpresse.seame.click/opds/download/e5e5e721582d86e5b5295b6f78fe4256?api_key={api_key}"' in feed
    assert 'type="application/epub+zip"' in feed


def test_db_downloaded_books_crud():
    db = Database()
    test_md5 = "11223344556677889900aabbccddeeff"

    # Save
    saved = db.save_downloaded_book(
        md5=test_md5,
        title="Test Bog",
        author="Test Forfatter",
        filename="Test_Bog_Test_Forfatter.epub",
        size_bytes=2048,
        format="epub",
        download_source="mirror_libgen",
    )
    assert saved is not None
    assert saved["md5"] == test_md5

    # Get
    book = db.get_downloaded_book(test_md5)
    assert book is not None
    assert book["title"] == "Test Bog"
    assert book["author"] == "Test Forfatter"
    assert book["filename"] == "Test_Bog_Test_Forfatter.epub"
    assert book["size_bytes"] == 2048

    # List
    all_books = db.list_downloaded_books()
    found = [b for b in all_books if b["md5"] == test_md5]
    assert len(found) == 1

    # Delete
    del_res = db.delete_downloaded_book(test_md5)
    assert del_res is True
    assert db.get_downloaded_book(test_md5) is None


def test_opds_endpoints_authentication():
    client = TestClient(app)
    db = Database()
    raw_key, _ = db.create_api_key("OPDS Device Tester")

    # 1. Without credentials -> 401
    res_no_auth = client.get("/opds")
    assert res_no_auth.status_code == 401

    # 2. With query param ?api_key=... -> 200
    res_query = client.get(f"/opds?api_key={raw_key}")
    assert res_query.status_code == 200
    assert "application/atom+xml" in res_query.headers["content-type"]
    assert f"?api_key={raw_key}" in res_query.text

    # 3. With X-API-Key header -> 200
    res_header = client.get("/opds", headers={"X-API-Key": raw_key})
    assert res_header.status_code == 200

    # 4. With HTTP Basic Auth -> 200
    basic_creds = base64.b64encode(f"{raw_key}:".encode("utf-8")).decode("utf-8")
    res_basic = client.get("/opds", headers={"Authorization": f"Basic {basic_creds}"})
    assert res_basic.status_code == 200

    # 5. With admin session cookie -> 200
    session_token = create_session_token()
    res_session = client.get("/opds", cookies={"ordpresse_session": session_token})
    assert res_session.status_code == 200


def test_opds_book_download_and_api(tmp_path, monkeypatch):
    client = TestClient(app)
    db = Database()
    raw_key, _ = db.create_api_key("OPDS Download Book Tester")

    test_md5 = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    test_filename = "Udvandrerne_Vilhelm_Moberg.epub"
    test_content = b"PK\x03\x04test_epub_binary_payload"

    # Put mock book in storage
    books_dir = get_books_dir()
    test_file = books_dir / test_filename
    test_file.write_bytes(test_content)

    try:
        db.save_downloaded_book(
            md5=test_md5,
            title="Udvandrerne",
            author="Vilhelm Moberg",
            filename=test_filename,
            size_bytes=len(test_content),
            format="epub",
        )

        # 1. Download via /opds/download/{md5} with ?api_key=...
        res_dl = client.get(f"/opds/download/{test_md5}?api_key={raw_key}")
        assert res_dl.status_code == 200
        assert res_dl.content == test_content
        assert res_dl.headers["accept-ranges"] == "bytes"
        assert res_dl.headers["x-md5"] == test_md5
        assert f'filename="{test_filename}"' in res_dl.headers["content-disposition"]

        # 2. Invalid MD5 -> 400
        res_bad_md5 = client.get("/opds/download/invalidmd5", headers={"X-API-Key": raw_key})
        assert res_bad_md5.status_code == 400

        # 3. GET /api/books with session
        session_token = create_session_token()
        res_books = client.get("/api/books", cookies={"ordpresse_session": session_token})
        assert res_books.status_code == 200
        books_list = res_books.json()
        assert any(b["md5"] == test_md5 for b in books_list)

        # 4. DELETE /api/books/{md5} with session
        res_del = client.delete(f"/api/books/{test_md5}", cookies={"ordpresse_session": session_token})
        assert res_del.status_code == 200
        assert not test_file.exists()
        assert db.get_downloaded_book(test_md5) is None

    finally:
        if test_file.exists():
            test_file.unlink(missing_ok=True)
