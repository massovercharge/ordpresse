"""End-to-end regression suite verifying the 5 Moberg benchmark words in a simulated EPUB."""

from pathlib import Path
import zipfile
import pytest

from ordpresse.pipeline import Pipeline
from ordpresse.stardict_writer import StarDictReader


def create_simulated_moberg_epub(dest_path: Path) -> Path:
    """Create a minimal valid EPUB containing the 5 Moberg benchmark words."""
    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Moberg Test Roman</dc:title>
    <dc:identifier id="pub-id">urn:uuid:12345</dc:identifier>
    <dc:language>da</dc:language>
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>"""

    # Text containing all 5 Moberg benchmark words with soft-hyphens and historical contexts
    chapter1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Kapitel 1</title></head>
<body>
  <h1>Kapitel 1: På den gamle hede</h1>
  <p>
    I sognet vandrede de udstødte <em>lægds&shy;lemmer</em> fra gård til gård for at modtage deres nådsensbrød.
    Præsten slog op i sognets ældgamle <strong>jordebog</strong> for at efterse tiendeafgifterne.
    Langs grusvejen nærmede sig en omrejsende <strong>bissekræmmer</strong> med sin tunge kramkiste på ryggen.
    Hjemme i den fugtige stue sad den <strong>værkbrudne</strong> fader med gigt i alle lemmer,
    og han mindedes dengang, en ko kostede fyrretyve rigsdaler <strong>banco</strong>.
  </p>
</body>
</html>"""

    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/chapter1.xhtml", chapter1_xhtml)

    return dest_path


def test_full_pipeline_moberg_words_regression(tmp_path: Path):
    """End-to-end pipeline test on a simulated EPUB with the 5 Moberg words."""
    epub_file = tmp_path / "moberg_test.epub"
    create_simulated_moberg_epub(epub_file)

    output_dir = tmp_path / "output"
    pipeline = Pipeline(output_dir=output_dir, bookname="Moberg StarDict Test")

    # Run full build pipeline using built-in offline seeds (no external network needed)
    result = pipeline.run(
        epub_files=[epub_file],
        include_seeds=True,
        allow_network=False,
    )

    assert result.entry_count >= 5
    assert result.zip_path.exists()
    assert result.files["ifo"].exists()
    assert result.files["idx"].exists()
    assert result.files["syn"].exists()

    # Open with StarDictReader and test lookup of all 5 surface strings
    reader = StarDictReader(result.files["ifo"])

    # 1. Surface word: "lægdslemmer" (plural of lægdslem)
    def_laegdslemmer = reader.lookup("lægdslemmer")
    assert def_laegdslemmer is not None, "Lookup for 'lægdslemmer' failed!"
    assert any(term in def_laegdslemmer.lower() for term in ["fattiglem", "lægd", "forsørgelse"])

    # 2. Surface word: "jordebog"
    def_jordebog = reader.lookup("jordebog")
    assert def_jordebog is not None, "Lookup for 'jordebog' failed!"
    assert any(term in def_jordebog.lower() for term in ["jordebog", "ejendom", "gods"])

    # 3. Surface word: "bissekræmmer"
    def_bissekraemmer = reader.lookup("bissekræmmer")
    assert def_bissekraemmer is not None, "Lookup for 'bissekræmmer' failed!"
    assert any(term in def_bissekraemmer.lower() for term in ["kræmmer", "handelsmand", "kramkiste"])

    # 4. Surface word: "værkbrudne" (inflection of værkbrudden)
    def_vaerkbrudne = reader.lookup("værkbrudne")
    assert def_vaerkbrudne is not None, "Lookup for 'værkbrudne' failed!"
    assert any(term in def_vaerkbrudne.lower() for term in ["svækket", "lam", "gigt", "værkbrudden"])

    # 5. Surface word: "banco"
    def_banco = reader.lookup("banco")
    assert def_banco is not None, "Lookup for 'banco' failed!"
    assert any(term in def_banco.lower() for term in ["banco", "banko", "møntfod", "kurantbanken"])


def test_all_nordic_1800s_vocabulary_binary_lookup(tmp_path: Path):
    """Verify that all 35+ Nordic 1800s words and inflections resolve via pure binary search."""
    from ordpresse.sources.nordic_1800s import NORDIC_1800S_ENTRIES
    from ordpresse.stardict_reader import StarDictReader

    output_dir = tmp_path / "output_all"
    pipeline = Pipeline(output_dir=output_dir, bookname="Nordic 1800s Benchmark")

    result = pipeline.run(
        include_seeds=True,
        allow_network=False,
    )

    assert result.entry_count >= 35
    reader = StarDictReader(result.files["ifo"])

    # Test all 35+ headwords and check 3-layer E-Ink rendering
    for hw, entry in NORDIC_1800S_ENTRIES.items():
        # 1. Lookup headword
        res_hw = reader.lookup(hw)
        assert res_hw is not None, f"Binary lookup for headword '{hw}' failed!"
        assert f"<b>{hw}</b>" in res_hw
        assert "▸" in res_hw, f"Layer 1 summary hook missing for '{hw}'"
        assert "HISTORISK BETYDNING" in res_hw, f"Layer 2A historical senses missing for '{hw}'"
        assert "[Historisk kontekst]:" in res_hw, f"Layer 3 realia note missing for '{hw}'"

        # 2. Lookup inflected forms from .syn
        for inf in entry.inflections:
            if inf.lower() != hw:
                res_inf = reader.lookup(inf)
                assert res_inf is not None, f"Binary lookup for inflection '{inf}' of '{hw}' failed in .syn!"
                assert f"<b>{hw}</b>" in res_inf


def test_full_dictionary_build_without_epub_contains_historical_words(tmp_path: Path):
    """Verify that a 'Komplet ordbog' build without EPUB contains all curated 1800s terms in binary StarDict."""
    from ordpresse.stardict_reader import StarDictReader

    output_dir = tmp_path / "output_full"
    pipeline = Pipeline(output_dir=output_dir, bookname="Komplet Ordbog Test")

    # Run full dictionary build without EPUB and without custom words
    result = pipeline.run(
        epub_files=None,
        custom_words=None,
        full_dictionary=True,
        allow_network=False,
    )

    assert result.entry_count >= 35
    assert result.zip_path.exists()
    assert result.files["ifo"].exists()
    assert result.files["idx"].exists()
    assert result.files["syn"].exists()
    assert result.files["dict"].exists()

    reader = StarDictReader(result.files["ifo"])

    # Required words to verify in the finished binary dictionary:
    # 'lægdslemmer', 'jordebog', 'bissekræmmer', 'værkbrudne', 'banco', 'husforhør', 'rustholder'
    required_words = [
        ("lægdslemmer", "lægdslem"),
        ("jordebog", "jordebog"),
        ("bissekræmmer", "bissekræmmer"),
        ("værkbrudne", "værkbrudden"),
        ("banco", "banco"),
        ("husforhør", "husforhør"),
        ("rustholder", "rustholder"),
    ]

    for search_term, expected_headword in required_words:
        html = reader.lookup(search_term)
        assert html is not None, f"StarDictReader lookup for '{search_term}' failed in compiled binary!"
        assert f"<b>{expected_headword}</b>" in html, f"Expected <b>{expected_headword}</b> in lookup for '{search_term}'"


def test_uefterrettelig_historical_definition_not_dummy(tmp_path: Path):
    """Verify that 'uefterrettelig' and 'uefterrettelige' resolve to authentic definitions and NOT dummy text."""
    from ordpresse.db import Database
    from ordpresse.scrapers import HistoricalScraper
    from ordpresse.sources.ods_scraper import ODSScraper
    from ordpresse.stardict_reader import StarDictReader

    output_dir = tmp_path / "output_uefterrettelig"
    pipeline = Pipeline(output_dir=output_dir, bookname="Uefterrettelig Test")

    # 1. Run pipeline with inflected surface form 'uefterrettelige'
    result = pipeline.run(
        custom_words=["uefterrettelige"],
        include_seeds=True,
        allow_network=False,
    )

    assert result.files["ifo"].exists()
    reader = StarDictReader(result.files["ifo"])

    # Lookup headword 'uefterrettelig'
    html_hw = reader.lookup("uefterrettelig")
    assert html_hw is not None, "Lookup for headword 'uefterrettelig' failed!"
    assert "Historisk opslag for »" not in html_hw, "Found dummy placeholder in 'uefterrettelig'!"
    assert any(
        term in html_hw.lower()
        for term in ["rette sig efter", "upålidelig", "upaalidelig", "ulydig"]
    ), f"Real definition missing in: {html_hw}"

    # Lookup inflected form 'uefterrettelige' via .syn
    html_inf = reader.lookup("uefterrettelige")
    assert html_inf is not None, "Lookup for inflected 'uefterrettelige' in .syn failed!"
    assert "Historisk opslag for »" not in html_inf, "Found dummy placeholder in 'uefterrettelige'!"
    assert any(
        term in html_inf.lower()
        for term in ["rette sig efter", "upålidelig", "upaalidelig", "ulydig"]
    ), f"Real definition missing in: {html_inf}"

    # 2. Verify HistoricalScraper offline fallback to Molbech (1859)
    cache_db_path = tmp_path / "empty_cache.sqlite3"
    db = Database(cache_path=cache_db_path)
    scraper = HistoricalScraper(db)
    res_off = scraper.fetch_word_definition("uefterrettelig", allow_remote=False)
    assert res_off is not None, "Offline HistoricalScraper failed for 'uefterrettelig'!"
    assert res_off["source"] == "molbech"
    assert "Historisk opslag for »" not in res_off["html_content"]
    assert any(
        term in res_off["html_content"].lower()
        for term in ["rette sig efter", "upålidelig", "ulydig"]
    )

    # 3. Verify online ODS scraping when network is available
    ods_scraper = ODSScraper()
    res_ods = ods_scraper.scrape_ods("uefterrettelig")
    if res_ods:
        assert "Historisk opslag for »" not in res_ods["html_content"]
        assert len(res_ods["canonical_entry"].historical_senses) >= 1
        assert any(
            term in res_ods["html_content"].lower()
            for term in ["rette sig efter", "upaalidelig", "upålidelig"]
        )


