"""OPDS 1.2 Catalog and local book archive management for Ordpresse."""

import html
import os
from pathlib import Path
import re
from typing import Any, Optional
from datetime import datetime, timezone

from ordpresse.db import Database

DEFAULT_BOOKS_DIR = Path(os.getenv("ORDPRESSE_DATA_DIR", "data")) / "books"


def get_books_dir() -> Path:
    """Return the absolute path to the persistent books storage directory."""
    d = Path(os.getenv("ORDPRESSE_DATA_DIR", "data")) / "books"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_articles_dir() -> Path:
    """Return the absolute path to the persistent Web-to-EPUB articles directory."""
    d = get_books_dir() / "articles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def canonical_filename(title: str, author: str, ext: str = "epub") -> str:
    """
    Produce a deterministic, filesystem-safe filename shared between
    Kindle KOReader (Anna Fetch) and CrossPoint OS.
    Max 60 chars for title, 40 for author, no special filesystem characters.
    """
    clean_title = re.sub(r"[^\w\s.-]", "", title or "Book").strip().replace(" ", "_")[:60]
    clean_author = re.sub(r"[^\w\s.-]", "", author or "").strip().replace(" ", "_")[:40]
    clean_ext = ext.lstrip(".").lower() or "epub"
    if not clean_title:
        clean_title = "Book"
    if clean_author:
        base = f"{clean_title}_{clean_author}".strip("._")
    else:
        base = clean_title.strip("._")
    return f"{base}.{clean_ext}"


def format_size_human(size_bytes: int) -> str:
    """Convert bytes to human-readable size string (e.g. '3.1 MB', '800 KB')."""
    if not size_bytes or size_bytes <= 0:
        return "0 KB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes // 1024} KB"


def generate_opds_feed(
    books: list[dict[str, Any]],
    base_url: str,
    api_key: Optional[str] = None,
) -> str:
    """
    Generate an OPDS 1.2 compliant Atom XML catalog.
    If api_key is provided, all entry acquisition links will carry it,
    allowing zero-prompt one-click downloads in CrossPoint OPDS browser.
    """
    clean_base = base_url.rstrip("/")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build self and start links
    self_href = f"{clean_base}/opds"
    if api_key:
        self_href += f"?api_key={html.escape(api_key)}"

    entries_xml = []
    for b in books:
        md5 = html.escape(b.get("md5", ""))
        title = html.escape(b.get("title", "Ukendt titel"))
        author = html.escape(b.get("author", "Ukendt forfatter"))
        fmt = b.get("format", "epub").lower()
        size_str = format_size_human(b.get("size_bytes", 0))

        mime_type = "application/epub+zip" if fmt == "epub" else "application/octet-stream"

        dl_href = f"{clean_base}/opds/download/{md5}"
        if api_key:
            dl_href += f"?api_key={html.escape(api_key)}"

        book_date = b.get("downloaded_at") or now_iso
        if not book_date.endswith("Z") and "+" not in book_date:
            book_date += "Z"

        summary = html.escape(f"{b.get('title', '')} af {b.get('author', '')} ({fmt.upper()} - {size_str})")

        entry = f"""  <entry>
    <title>{title}</title>
    <id>urn:ordpresse:book:{md5}</id>
    <author><name>{author}</name></author>
    <updated>{book_date}</updated>
    <summary type="text">{summary}</summary>
    <content type="text">{summary}</content>
    <link rel="http://opds-spec.org/acquisition" href="{dl_href}" type="{mime_type}"/>
  </entry>"""
        entries_xml.append(entry)

    feed_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:ordpresse:opds:catalog</id>
  <title>Ordpresse Bibliotek</title>
  <subtitle>Personligt bogarkiv til KOReader og CrossPoint</subtitle>
  <updated>{now_iso}</updated>
  <author>
    <name>Ordpresse</name>
    <uri>{clean_base}</uri>
  </author>
  <link rel="self" href="{self_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="{self_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="subsection" href="{clean_base}/opds/articles{('?api_key=' + html.escape(api_key)) if api_key else ''}" type="application/atom+xml;profile=opds-catalog;kind=acquisition" title="[Avis] Læs Senere (Artikler)"/>
{chr(10).join(entries_xml)}
</feed>"""
    return feed_xml


def generate_articles_opds_feed(
    articles: list[dict[str, Any]],
    base_url: str,
    api_key: Optional[str] = None,
) -> str:
    """Generate an OPDS 1.2 Acquisition Feed for Web-to-EPUB 'Læs Senere' articles."""
    clean_base = base_url.rstrip("/")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    key_qs = f"?api_key={html.escape(api_key)}" if api_key else ""
    self_href = f"{clean_base}/opds/articles{key_qs}"
    start_href = f"{clean_base}/opds{key_qs}"

    entries_xml = []
    for art in articles:
        md5 = html.escape(art.get("md5", ""))
        title = html.escape(art.get("title", "Web-artikel"))
        author = html.escape(art.get("author", "Web-artikel"))
        size_str = format_size_human(art.get("size_bytes", 0))
        dl_href = f"{clean_base}/opds/articles/download/{md5}{key_qs}"

        art_date = art.get("created_at") or now_iso
        if not art_date.endswith("Z") and "+" not in art_date:
            art_date += "Z"

        status_tag = "[Læst] " if art.get("is_read") else ""
        summary = html.escape(f"{status_tag}{art.get('title', '')} af {art.get('author', '')} (EPUB - {size_str})")

        entry = f"""  <entry>
    <title>{html.escape(status_tag)}{title}</title>
    <id>urn:ordpresse:article:{md5}</id>
    <author><name>{author}</name></author>
    <updated>{art_date}</updated>
    <summary type="text">{summary}</summary>
    <content type="text">{summary}</content>
    <link rel="http://opds-spec.org/acquisition" href="{dl_href}" type="application/epub+zip"/>
  </entry>"""
        entries_xml.append(entry)

    feed_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:ordpresse:opds:articles</id>
  <title>[Avis] Læs Senere (Artikler)</title>
  <subtitle>Gemte web-artikler vasket til E-Ink EPUB via Den Danske Ordpresse</subtitle>
  <updated>{now_iso}</updated>
  <author>
    <name>Ordpresse</name>
    <uri>{clean_base}</uri>
  </author>
  <link rel="self" href="{self_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="{start_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
{chr(10).join(entries_xml)}
</feed>"""
    return feed_xml
