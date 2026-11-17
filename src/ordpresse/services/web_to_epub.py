"""Web-to-EPUB ('Læs Senere') pipeline for Den Danske Ordpresse (v1.6.0).

Extracts clean article content from URLs or raw HTML/text (using trafilatura when available
with BeautifulSoup4 fallback), builds a minimal EPUB 3 archive, washes it through
EpubLaundryService for crisp E-Ink typography, and stores it in data/books/articles/.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import io
import logging
import re
from typing import Optional
from urllib.parse import urlparse
import uuid
import zipfile

from bs4 import BeautifulSoup
import httpx

from ordpresse.db import Database
from ordpresse.services.epub_laundry import EpubLaundryService, LaundryOptions
from ordpresse.services.opds_service import canonical_filename, get_articles_dir

logger = logging.getLogger(__name__)

ARTICLE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class ExtractedArticle:
    title: str
    author: str
    date: str
    source_url: str
    body_xhtml: str
    language: str = "da"


def _guess_language_hint(text: str) -> str:
    """Quick Danish vs English heuristic used before EpubLaundryService refines it."""
    sample = (text or "").lower()
    da_markers = sum(sample.count(ch) for ch in ("æ", "ø", "å"))
    da_words = len(re.findall(r"\b(og|det|ikke|til|med|for|på|som|fra|af)\b", sample))
    en_words = len(re.findall(r"\b(the|and|that|with|from|this|have|which|their)\b", sample))
    if da_markers >= 3 or da_words >= en_words:
        return "da"
    return "en"


def _extract_with_trafilatura(raw_html: str, url: str = "") -> Optional[ExtractedArticle]:
    """Attempt extraction with optional trafilatura dependency."""
    try:
        import trafilatura  # type: ignore
    except ImportError:
        return None

    try:
        meta = trafilatura.extract_metadata(raw_html, default_url=url or None)
        extracted_xml = trafilatura.extract(
            raw_html,
            url=url or None,
            output_format="xml",
            include_formatting=True,
            include_links=False,
        )
        if not extracted_xml:
            return None

        soup = BeautifulSoup(extracted_xml, "xml")
        parts: list[str] = []
        for elem in soup.find_all(["head", "p", "quote", "item"]):
            txt = elem.get_text(" ", strip=True)
            if not txt:
                continue
            esc = html.escape(txt)
            if elem.name == "head":
                parts.append(f"<h2>{esc}</h2>")
            elif elem.name == "quote":
                parts.append(f"<blockquote><p>{esc}</p></blockquote>")
            elif elem.name == "item":
                parts.append(f"<p>• {esc}</p>")
            else:
                parts.append(f"<p>{esc}</p>")

        if not parts:
            return None

        title = (meta.title if meta and getattr(meta, "title", None) else "") or ""
        author = (meta.author if meta and getattr(meta, "author", None) else "") or ""
        date_str = (meta.date if meta and getattr(meta, "date", None) else "") or ""
        if not author and url:
            author = urlparse(url).netloc.replace("www.", "")
        body_xhtml = "\n".join(parts)
        lang = _guess_language_hint(BeautifulSoup(body_xhtml, "html.parser").get_text(" "))
        return ExtractedArticle(
            title=title.strip() or "Web-artikel",
            author=author.strip() or "Web-artikel",
            date=date_str.strip(),
            source_url=url,
            body_xhtml=body_xhtml,
            language=lang,
        )
    except Exception as exc:
        logger.debug(f"trafilatura extraction fallback triggered: {exc}")
        return None


def extract_article_from_html(
    raw_html: str,
    url: str = "",
    title_override: str = "",
    author_override: str = "",
) -> ExtractedArticle:
    """Extract clean article XHTML from HTML (trafilatura with BeautifulSoup4 fallback)."""
    trafi = _extract_with_trafilatura(raw_html, url=url)
    if trafi is not None:
        if title_override.strip():
            trafi.title = title_override.strip()
        if author_override.strip():
            trafi.author = author_override.strip()
        return trafi

    soup = BeautifulSoup(raw_html, "lxml")

    # Metadata extraction
    title = title_override.strip()
    if not title:
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
    if not title:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(" ", strip=True)
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    title = title or "Web-artikel"

    author = author_override.strip()
    if not author:
        meta_author = (
            soup.find("meta", attrs={"name": "author"})
            or soup.find("meta", attrs={"property": "article:author"})
        )
        if meta_author and meta_author.get("content"):
            author = str(meta_author["content"]).strip()
    if not author and url:
        author = urlparse(url).netloc.replace("www.", "")
    author = author or "Web-artikel"

    date_str = ""
    meta_date = (
        soup.find("meta", attrs={"property": "article:published_time"})
        or soup.find("time")
    )
    if meta_date:
        date_str = str(meta_date.get("content") or meta_date.get("datetime") or meta_date.get_text(strip=True))[:10]

    # Strip non-content boilerplate
    for tag in soup.find_all(["script", "style", "noscript", "nav", "footer", "aside", "form", "iframe", "svg"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup

    parts: list[str] = []
    seen_texts: set[str] = set()
    for elem in container.find_all(["h2", "h3", "h4", "p", "blockquote", "li"]):
        # Avoid duplicating text inside nested <p> within <blockquote> or <li>
        if elem.name == "p" and elem.find_parent(["blockquote", "li"]):
            continue
        txt = re.sub(r"\s+", " ", elem.get_text(" ", strip=True)).strip()
        if len(txt) < 2 or txt in seen_texts:
            continue
        if elem.name == "p" and len(txt) < 12 and not txt.endswith((".", "!", "?", ":")):
            continue
        seen_texts.add(txt)
        esc = html.escape(txt)
        if elem.name in ("h2", "h3", "h4"):
            parts.append(f"<{elem.name}>{esc}</{elem.name}>")
        elif elem.name == "blockquote":
            parts.append(f"<blockquote><p>{esc}</p></blockquote>")
        elif elem.name == "li":
            parts.append(f"<p>• {esc}</p>")
        else:
            parts.append(f"<p>{esc}</p>")

    if not parts:
        # Plain text fallback
        plain = re.sub(r"\r\n?", "\n", container.get_text("\n", strip=True))
        for para in re.split(r"\n{2,}", plain):
            clean = re.sub(r"\s+", " ", para).strip()
            if clean:
                parts.append(f"<p>{html.escape(clean)}</p>")

    body_xhtml = "\n".join(parts) if parts else "<p>Ingen artikeltekst fundet.</p>"
    plain_text = BeautifulSoup(body_xhtml, "html.parser").get_text(" ")
    lang = _guess_language_hint(plain_text)

    return ExtractedArticle(
        title=title,
        author=author,
        date=date_str,
        source_url=url,
        body_xhtml=body_xhtml,
        language=lang,
    )


def extract_article_from_plain_text(
    raw_text: str,
    title: str = "",
    author: str = "",
    url: str = "",
) -> ExtractedArticle:
    """Convert pasted plain text (e.g. from paywalled articles) into structured XHTML."""
    normalized = re.sub(r"\r\n?", "\n", (raw_text or "").strip())
    blocks = [b.strip() for b in re.split(r"\n{2,}", normalized) if b.strip()]
    resolved_title = title.strip()
    if not resolved_title and blocks:
        first_line = blocks[0].splitlines()[0].strip()
        if len(first_line) <= 140:
            resolved_title = first_line
            if len(blocks) > 1 and blocks[0].strip() == first_line:
                blocks = blocks[1:]
    resolved_title = resolved_title or "Gemt Artikel"

    parts: list[str] = []
    for block in blocks:
        clean = re.sub(r"\s+", " ", block).strip()
        if clean:
            parts.append(f"<p>{html.escape(clean)}</p>")

    body_xhtml = "\n".join(parts) or "<p>Tom artikel.</p>"
    resolved_author = author.strip() or (urlparse(url).netloc.replace("www.", "") if url else "Web-artikel")
    lang = _guess_language_hint(normalized)
    return ExtractedArticle(
        title=resolved_title,
        author=resolved_author or "Web-artikel",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        source_url=url,
        body_xhtml=body_xhtml,
        language=lang,
    )


def build_minimal_epub(article: ExtractedArticle) -> bytes:
    """Build a valid minimal EPUB 3 archive from an ExtractedArticle."""
    book_uuid = f"urn:uuid:{uuid.uuid4()}"
    modified_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    esc_title = html.escape(article.title)
    esc_author = html.escape(article.author)
    esc_lang = html.escape(article.language or "da")

    meta_line_parts = [esc_author]
    if article.date:
        meta_line_parts.append(html.escape(article.date))
    if article.source_url:
        host = urlparse(article.source_url).netloc.replace("www.", "")
        if host and host.lower() not in article.author.lower():
            meta_line_parts.append(html.escape(host))
    meta_subtitle = " · ".join(p for p in meta_line_parts if p)

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" xml:lang="{esc_lang}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{book_uuid}</dc:identifier>
    <dc:title>{esc_title}</dc:title>
    <dc:creator>{esc_author}</dc:creator>
    <dc:language>{esc_lang}</dc:language>
    <meta property="dcterms:modified">{modified_iso}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="article" href="article.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="article"/>
  </spine>
</package>
"""

    nav_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{esc_lang}" lang="{esc_lang}">
<head>
  <meta charset="utf-8"/>
  <title>Indhold</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Indhold</h1>
    <ol>
      <li><a href="article.xhtml">{esc_title}</a></li>
    </ol>
  </nav>
</body>
</html>
"""

    article_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{esc_lang}" lang="{esc_lang}">
<head>
  <meta charset="utf-8"/>
  <title>{esc_title}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <header class="article-header">
    <h1>{esc_title}</h1>
    <p class="article-meta">{meta_subtitle}</p>
  </header>
  <article>
{article.body_xhtml}
  </article>
</body>
</html>
"""

    style_css = """body { font-family: serif; line-height: 1.5; margin: 1em; }
.article-header { margin-bottom: 1.5em; border-bottom: 1px solid #000; padding-bottom: 0.5em; }
.article-meta { font-style: italic; font-size: 0.9em; }
blockquote { margin: 1em 1.5em; font-style: italic; }
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/article.xhtml", article_xhtml, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", style_css, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


async def create_washed_article_epub(
    db: Database,
    laundry_service: EpubLaundryService,
    url: str = "",
    raw_content: str = "",
    title: str = "",
    author: str = "",
) -> dict:
    """
    Full Web-to-EPUB pipeline:
    1. Fetch URL (if provided without raw_content) or parse pasted HTML/text.
    2. Extract clean article structure.
    3. Build minimal EPUB 3 and polish through EpubLaundryService.
    4. Persist in data/books/articles/ and register in SQLite saved_articles.
    """
    clean_url = (url or "").strip()
    content = (raw_content or "").strip()

    if not content:
        if not clean_url or not clean_url.lower().startswith(("http://", "https://")):
            raise ValueError("Angiv enten en gyldig artikel-URL (http/https) eller indsæt artikeltekst.")
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(25.0, connect=10.0),
            headers={"User-Agent": ARTICLE_UA, "Accept-Language": "da,en;q=0.8"},
        ) as client:
            resp = await client.get(clean_url)
            resp.raise_for_status()
            content = resp.text

    looks_like_html = bool(re.search(r"<(html|body|article|p|div|h[1-6])\b", content, re.I))
    if looks_like_html:
        article = extract_article_from_html(
            content,
            url=clean_url,
            title_override=title,
            author_override=author,
        )
    else:
        article = extract_article_from_plain_text(
            content,
            title=title,
            author=author,
            url=clean_url,
        )

    raw_epub = build_minimal_epub(article)
    wash_opts = LaundryOptions(
        heal_ocr_paragraphs=False,
        rebuild_missing_toc=True,
        danish_quote_style="guillemets",
        generate_lexical_cache=True,
    )
    washed = await laundry_service.process_epub(raw_epub, wash_opts)

    final_bytes = washed.epub_bytes
    final_md5 = washed.book_md5 or hashlib.md5(final_bytes).hexdigest()
    filename = canonical_filename(article.title, article.author, ext="epub")

    articles_dir = get_articles_dir()
    target_path = articles_dir / filename
    target_path.write_bytes(final_bytes)

    record = db.save_article(
        md5=final_md5,
        title=article.title,
        author=article.author,
        source_url=clean_url,
        filename=filename,
        size_bytes=len(final_bytes),
        language=washed.detected_language or article.language or "da",
    )
    record["laundry"] = {
        "detected_language": washed.detected_language,
        "prewarmed_words": washed.prewarmed_words_count,
        "processing_ms": washed.processing_ms,
    }
    return record
