"""Automated tests for Ordpresse Bogvaskeri & Bogdoktor v2.0."""

import io
import json
from pathlib import Path
import zipfile
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from PIL import Image

from ordpresse.db import Database
from ordpresse.services.epub_laundry import (
    DefaultHistoricalDictionaryService,
    EpubLaundryService,
    LaundryOptions,
    LaundryResult,
)
from ordpresse.web.app import app
from ordpresse.web.plugin_store import get_catalog_json, get_plugin_by_id, get_plugin_zip


@pytest.fixture
def client():
    return TestClient(app)


def create_minimal_test_epub(
    html_content: str,
    css_content: str = "body { font-family: 'Times'; color: #000; }",
    include_toc: bool = True,
    image_png: bytes | None = None,
) -> bytes:
    """Helper der bygger en gyldig minimal in-memory EPUB til testformål."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype skal være stored
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

        container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        zf.writestr("META-INF/container.xml", container_xml)

        opf_xml = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Bog</dc:title>
    <dc:language>und</dc:language>
    <dc:identifier id="BookId">urn:uuid:test-12345</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap1"/>
  </spine>
</package>"""
        zf.writestr("OEBPS/content.opf", opf_xml)

        if include_toc:
            ncx_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:test-12345"/></head>
  <docTitle><text>Test Bog</text></docTitle>
  <navMap>
    <navPoint id="np-1" playOrder="1">
      <navLabel><text>Start</text></navLabel>
      <content src="chap1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"""
            zf.writestr("OEBPS/toc.ncx", ncx_xml)

        zf.writestr("OEBPS/style.css", css_content)
        zf.writestr("OEBPS/chap1.xhtml", html_content)

        if image_png:
            zf.writestr("OEBPS/cover.png", image_png)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Enhedstest af CSS-Sanitering (tinycss2)
# ---------------------------------------------------------------------------

def test_css_sanitizing_removes_blocked_properties_and_handles_media():
    service = EpubLaundryService()
    raw_css = b"""
    body { font-family: "Georgia", serif; font-size: 12pt; color: #333; margin: 10px; }
    h1 { font-family: "Arial"; font-size: 1.6em; margin-bottom: 0.5em; }
    p { line-height: 1.6; text-indent: 1.5em; background-color: white; }
    @media screen {
        .kindle-only { font: 11pt "Baskerville"; color: red; }
        .visible-box { border: 1px solid black; }
    }
    """
    cleaned = service._sanitize_css_ast(raw_css).decode("utf-8")

    # Kontrollér at låste fonte og farver er fjernet
    assert 'font-family: "Georgia"' not in cleaned
    assert "color: #333" not in cleaned
    assert "line-height: 1.6" not in cleaned
    assert "background-color: white" not in cleaned
    assert 'font: 11pt "Baskerville"' not in cleaned

    # Kontrollér at relative font-størrelser på overskrifter (h1) bevares
    assert "font-size: 1.6em" in cleaned

    # Kontrollér at ikke-låste egenskaber bevares
    assert "text-indent: 1.5em" in cleaned
    assert "border: 1px solid black" in cleaned

    # Kontrollér at E-Ink basisregler er injiceret
    assert "orphans: 2; widows: 2; hyphens: auto;" in cleaned


# ---------------------------------------------------------------------------
# 2. Enhedstest af OCR-Heling (DOM Afsnitssammensmeltning & Ankerbevarelse)
# ---------------------------------------------------------------------------

def test_ocr_paragraph_healing_and_anchor_preservation():
    service = EpubLaundryService()
    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <p class="bodytext">Dette er en lang sætning, som ved en fejl er knækket midt i en</p>
        <p class="bodytext" id="sec_target_anchor">sætning på grund af dårlig OCR eller konvertering fra PDF.</p>
        
        <p class="poem">Dette er et digt på vers</p>
        <p class="poem">og denne linje må aldrig flettes.</p>
        
        <p class="regular">Dette er et normalt afsnit med orddelt linje-</p>
        <p class="regular">skift der fortsætter flydende her i teksten.</p>
      </body>
    </html>"""

    soup = BeautifulSoup(html, "lxml-xml")
    healed = service._heal_broken_paragraphs_in_dom(soup)

    # 2 afsnitspar skal flettes sammen
    assert healed == 2

    # Verificér at id="sec_target_anchor" ikke er gået tabt (Acceptance Criterion 3)
    span_anchor = soup.find("span", id="sec_target_anchor")
    assert span_anchor is not None, "Ankeret på det flettede afsnit skal bevares som et <span>"

    # Verificér at digtet IKKE blev flettet
    poem_paragraphs = soup.find_all("p", class_="poem")
    assert len(poem_paragraphs) == 2, "Digte/vers må aldrig flettes sammen"

    # Verificér orddelt bindestregshåndtering
    regular_p = soup.find("p", class_="regular")
    assert "linjeskift der fortsætter" in regular_p.get_text()


# ---------------------------------------------------------------------------
# 3. Enhedstest af Dansk og Engelsk Typografi (Anførselstegn hen over tags)
# ---------------------------------------------------------------------------

def test_typography_quotes_paired_across_inline_tags():
    service = EpubLaundryService()
    opts = LaundryOptions(danish_quote_style="guillemets")

    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <!-- XML kommentar der ikke må røres -->
        <p>"<em>Godmorgen,</em>" sagde han.</p>
        <p>Her er en tankestreg -- og en forkortelse som f.eks. dette.</p>
      </body>
    </html>"""

    soup = BeautifulSoup(html, "lxml-xml")
    word_freq = {}
    service._upgrade_dom_typography_and_collect_words(soup, lang="da", opts=opts, word_freq_out=word_freq)

    result_html = str(soup)

    # Acceptance Criterion 4: Deterministic »...« across inline tags
    assert "»<em>Godmorgen,</em>«" in result_html or "»<em>Godmorgen,</em>« sagde han." in result_html
    # Tankestreg opgraderet til &nbsp;–
    assert "\u00a0– " in result_html
    # Forkortelse f.eks. har hårdt mellemrum
    assert "f.eks.\u00a0dette" in result_html
    # XML kommentar er intakt
    assert "<!-- XML kommentar der ikke må røres -->" in result_html


def test_typography_baseline_quotes_option():
    service = EpubLaundryService()
    opts = LaundryOptions(danish_quote_style="baseline")

    html = '<p>"Dette er på dansk med baseline citattegn."</p>'
    soup = BeautifulSoup(html, "lxml-xml")
    service._upgrade_dom_typography_and_collect_words(soup, lang="da", opts=opts, word_freq_out={})

    assert "„Dette er på dansk med baseline citattegn.“" in str(soup)


# ---------------------------------------------------------------------------
# 4. Enhedstest af Billedoptimering og Format-Troskab (MIME-bevarelse)
# ---------------------------------------------------------------------------

def test_image_optimization_preserves_png_format():
    import os
    service = EpubLaundryService()

    # Opret et stort 1200x1200 RGBA PNG i RAM med støj så det fylder > 200 KB
    raw_data = os.urandom(800 * 800 * 4)
    img = Image.frombytes("RGBA", (800, 800), raw_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_png = buf.getvalue()
    assert len(raw_png) > 200_000, f"Billede skal overstige threshold, fylder {len(raw_png)}"

    # Optimer billedet
    opt_bytes, was_opt = service._optimize_image_preserve_format(raw_png, "diagram.png", is_cover=False)

    # Verificér at billedet blev optimeret og fylder mindre
    assert was_opt is True
    assert len(opt_bytes) < len(raw_png)

    # Acceptance Criterion 2: PNG forbliver PNG (ikke konverteret til JPEG)
    with Image.open(io.BytesIO(opt_bytes)) as res_img:
        assert res_img.format == "PNG"


# ---------------------------------------------------------------------------
# 5. Enhedstest af TOC-Rekonstruktion
# ---------------------------------------------------------------------------

def test_sparse_toc_reconstruction():
    service = EpubLaundryService()

    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <h1>Kapitel 1: En mærkelig begyndelse</h1>
        <p>Der var engang en mand som boede i en gammel mølle ved åen.</p>
        <h2>Kapitel 2: Den mørke skov</h2>
        <p>Natten faldt på og stjernerne tændtes en efter en over trætoppene.</p>
      </body>
    </html>"""

    # Lav en EPUB uden fyldestgørende TOC (< 2 navPoints)
    raw_epub = create_minimal_test_epub(html, include_toc=False)

    opts = LaundryOptions(rebuild_missing_toc=True, heal_ocr_paragraphs=False)
    result = service._run_pipeline_sync(raw_epub, opts)

    assert result.toc_entries_rebuilt >= 2

    with zipfile.ZipFile(io.BytesIO(result.epub_bytes)) as zf:
        ncx_name = next((n for n in zf.namelist() if n.endswith(".ncx")), None)
        assert ncx_name is not None
        ncx_content = zf.read(ncx_name).decode("utf-8")
        assert "Kapitel 1" in ncx_content
        assert "Kapitel 2" in ncx_content


# ---------------------------------------------------------------------------
# 6. Enhedstest af Lexical Pre-Warming Cache Indlejring
# ---------------------------------------------------------------------------

def test_lexical_prewarm_cache_embedding():
    service = EpubLaundryService()

    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <p>På den gamle gård boede en brav bonde som havde adskillige lægdslemmer i sit brød.</p>
        <p>Dette er en dansk fortælling om livet i Danmark for to hundrede år siden.</p>
      </body>
    </html>"""

    raw_epub = create_minimal_test_epub(html)
    opts = LaundryOptions(generate_lexical_cache=True)
    result = service._run_pipeline_sync(raw_epub, opts)

    with zipfile.ZipFile(io.BytesIO(result.epub_bytes)) as zf:
        assert "META-INF/ordpresse_lexcache.json" in zf.namelist()
        cache_data = json.loads(zf.read("META-INF/ordpresse_lexcache.json").decode("utf-8"))
        assert isinstance(cache_data, dict)


# ---------------------------------------------------------------------------
# 7. Integrationstest af FastAPI Endpoint /api/v1/koreader/laundry
# ---------------------------------------------------------------------------

def test_koreader_laundry_endpoint_unauthenticated(client):
    res = client.post("/api/v1/koreader/laundry", content=b"PK\x03\x04test")
    assert res.status_code == 401


def test_koreader_laundry_endpoint_binary_stream(client):
    db = Database()
    raw_key, _ = db.create_api_key("Bogdoktor Device")

    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <p>"Dette er en fremragende dansk roman."</p>
      </body>
    </html>"""
    raw_epub = create_minimal_test_epub(html)

    headers = {
        "X-API-Key": raw_key,
        "X-Laundry-Heal-OCR": "true",
        "X-Laundry-Quote-Style": "guillemets",
    }

    res = client.post("/api/v1/koreader/laundry", content=raw_epub, headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/epub+zip"
    assert "X-Ordpresse-MD5" in res.headers
    assert res.headers["X-Ordpresse-Lang"] == "da"

    # Verificér gyldigt ZIP-arkiv modtaget
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert "mimetype" in zf.namelist()
        assert zf.read("mimetype").strip() == b"application/epub+zip"


def test_koreader_laundry_zero_upload_fast_path(client, tmp_path):
    db = Database()
    raw_key, _ = db.create_api_key("Fastpath Device")

    books_dir = Path("data/books")
    books_dir.mkdir(parents=True, exist_ok=True)

    test_fn = "pre_existing_test_book.epub"
    test_path = books_dir / test_fn

    html = """<?xml version="1.0" encoding="utf-8"?>
    <html>
      <body>
        <p>"Serverbog der allerede eksisterer på disken."</p>
      </body>
    </html>"""
    raw_epub = create_minimal_test_epub(html)
    test_path.write_bytes(raw_epub)

    try:
        # Fast path via X-Book-Filename med 0 bytes upload
        headers = {
            "X-API-Key": raw_key,
            "X-Book-Filename": test_fn,
            "Content-Length": "0",
        }

        res = client.post("/api/v1/koreader/laundry", headers=headers)
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/epub+zip"
        assert len(res.content) > 500
    finally:
        test_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 8. Test af Plugin Store Katalog og Bogdoktor Distribution
# ---------------------------------------------------------------------------

def test_bogdoktor_plugin_store_presence_and_download(client):
    catalog = get_catalog_json()
    bogdoktor_entry = next((p for p in catalog if p["id"] == "bogdoktor"), None)
    assert bogdoktor_entry is not None
    assert bogdoktor_entry["koplugin_name"] == "bogdoktor.koplugin"

    db = Database()
    raw_key, _ = db.create_api_key("Store Tester")

    # Download bogdoktor.koplugin fra store
    res = client.get(
        "/api/v1/koreader/lookup?action=store_download&id=bogdoktor",
        headers={"X-API-Key": raw_key},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        namelist = zf.namelist()
        assert "bogdoktor.koplugin/main.lua" in namelist
        assert "bogdoktor.koplugin/_meta.lua" in namelist
