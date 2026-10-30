"""End-to-end regression suite verifying the 5 Moberg benchmark words in a simulated EPUB."""

from pathlib import Path
import sqlite3
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
        assert "•" in res_hw, f"Layer 1 summary hook missing for '{hw}'"
        assert "▸" not in res_hw, f"Disallowed ▸ found in '{hw}'"
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
    assert res_off["source"] in ("molbech", "nordic_1800s")
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


def test_frequency_prioritization_and_batch_limits(tmp_path):
    """Verify that EPUB tokens are counted by frequency and batch-scraped with priority."""
    import zipfile
    from ordpresse.epub_scanner import scan_epub_token_counts

    # Create dummy in-memory EPUB
    epub_path = tmp_path / "test_book.epub"
    with zipfile.ZipFile(epub_path, "w") as zf:
        html_content = """
        <html><body>
            <p>bovlam bovlam bovlam</p>
            <p>uefterrettelige</p>
            <p>sjældenhed</p>
        </body></html>
        """
        zf.writestr("OEBPS/chapter1.xhtml", html_content)

    counts = scan_epub_token_counts(epub_path)
    assert counts["bovlam"] == 3
    assert counts["uefterrettelige"] == 1
    assert counts["sjældenhed"] == 1

def test_alderstegen_and_taellelyset_resolution(tmp_path: Path):
    """Verify that 'tællelyset' resolves to 'tællelys' with animal fat/tallow definition,
    and 'alderstegen' resolves with pos 'adj.' and clean spacing without 'adjektiv' or 'SITATER'."""
    output_dir = tmp_path / "output_alder_lys"
    pipeline = Pipeline(output_dir=output_dir, bookname="Test Alder og Lys")

    result = pipeline.run(
        custom_words=["tællelyset", "alderstegen"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])

    # 1. Test "tællelyset" resolves to "tællelys"
    def_taellelyset = reader.lookup("tællelyset")
    assert def_taellelyset is not None, "Lookup for 'tællelyset' failed in .syn!"
    assert "<b>tællelys</b>" in def_taellelyset
    assert "sb. itk." in def_taellelyset
    assert any(term in def_taellelyset.lower() for term in ["dyrefedt", "tælle", "talg"])
    assert "Kilde: ODS" in def_taellelyset or "ODS" in def_taellelyset

    def_taellelys = reader.lookup("tællelys")
    assert def_taellelys is not None, "Lookup for headword 'tællelys' failed in .idx!"
    assert any(term in def_taellelys.lower() for term in ["dyrefedt", "tælle", "talg"])

    # 2. Test "alderstegen"
    def_alder = reader.lookup("alderstegen")
    assert def_alder is not None, "Lookup for 'alderstegen' failed!"
    assert "<b>alderstegen</b>" in def_alder
    assert "<i>adj.</i>" in def_alder
    # Strictly forbid stuck text or unnormalized 'adjektiv' or 'SITATER'
    assert "alderstegenadjektiv" not in def_alder
    assert "<i>adjektiv</i>" not in def_alder
    assert "litterærtsvært" not in def_alder
    assert "SITATER" not in def_alder
    assert any(term in def_alder.lower() for term in ["alder", "ældgammel", "højt oppe i årene"])


def test_naob_parser_spacing_and_citation_stripping():
    """Verify NAOB parser decomposes SITATER, handles get_text spacing, and strips headword from POS."""
    from ordpresse.scrapers import HistoricalScraper

    raw_html = """
    <article>
        <h1>alderstegen</h1>
        <div class="ordklasse">alderstegenadjektiv</div>
        <div class="definition">
            <span class="label">litterært</span>svært gammel; tilårskommen
            <div class="sitater">
                SITATER
                <p>derinde sidder kæmpen (Elisabe</p>
            </div>
        </div>
    </article>
    """
    parsed = HistoricalScraper.parse_naob_html("alderstegen", raw_html)
    assert parsed is not None
    assert parsed["headword"] == "alderstegen"
    canonical = parsed["canonical_entry"]
    assert canonical.pos == "adj."
    assert len(canonical.historical_senses) >= 1
    first_def = canonical.historical_senses[0].definition
    # Space between label and definition
    assert "litterært svært gammel; tilårskommen" in first_def
    assert "SITATER" not in first_def
    assert "(Elisabe" not in first_def
    assert "adjektiv" not in canonical.pos or canonical.pos == "adj."


def test_corrupted_cache_cleanup_migration(tmp_path: Path):
    """Verify migration function purges rows with SITATER, adjektiv, or empty definitions."""
    import sqlite3
    from ordpresse.db import Database, clean_corrupted_cached_definitions

    cache_path = tmp_path / "cache.sqlite3"
    conn = sqlite3.connect(str(cache_path))
    conn.execute("""
        CREATE TABLE definitions (
            word TEXT PRIMARY KEY,
            source TEXT,
            headword TEXT,
            html_content TEXT,
            synonyms_json TEXT
        )
    """)
    conn.executemany("""
        INSERT INTO definitions (word, source, headword, html_content, synonyms_json)
        VALUES (?, ?, ?, ?, ?)
    """, [
        ("bad1", "naob", "bad1", "<div><b>bad1</b> <i>adjektiv</i></div><ol><li>def SITATER quote</li></ol>", "[]"),
        ("bad2", "naob", "bad2", "<div><b>bad2</b> <i>bad2adjektiv</i></div><ol><li>alderstegenadjektiv</li></ol>", "[]"),
        ("bad3", "cor", "bad3", "<div><b>bad3</b> <i>sb.</i></div><div><small>Kilde: COR</small></div>", "[]"),
        ("good", "ods", "good", "<div><b>good</b> <i>sb. fk.</i></div><ol><li>En gyldig definition.</li></ol>", "[]"),
        ("hvordan", "ods", "hvordan", "<div><b>hvordan</b> <i>adj.</i></div><ol><li>(hist.) sj.) substantiveret, om den maade</li></ol>", "[]"),
        ("hvad", "ods", "hvad", "<div><b>hvad</b> <i>pron.</i></div><ol><li>knyttet til substantiv ell. substantivisk ord</li></ol>", "[]"),
    ])
    conn.commit()

    deleted = clean_corrupted_cached_definitions(conn)
    assert deleted >= 3

    cur = conn.cursor()
    remaining = sorted(r[0] for r in cur.execute("SELECT word FROM definitions").fetchall())
    assert remaining == sorted(["good", "hvordan", "hvad"])

    # Also verify Database.get_cached does not delete valid entries with 'substantiv' in definition
    db_test = Database(cache_path=cache_path)
    assert db_test.get_cached("hvordan") is not None
    assert db_test.get_cached("hvad") is not None
    conn.close()

def test_cache_multi_key_resolution_and_negative_cache(tmp_path: Path):
    """Verify that cache hits succeed on query word, hyphenated ODS headwords, and negative cache."""
    from ordpresse.db import Database

    cache_path = tmp_path / "test_multi_cache.sqlite3"
    db = Database(cache_path=cache_path)
    
    # Store word with hyphenated ODS headword (e.g. fra-stød for frastøde)
    db.set_cached(
        word="frastøde",
        source="ods",
        headword="fra-stød",
        html_content="<div class='artikel'><p>At støde fra.</p></div>",
        synonyms=["frastøder"],
    )
    
    # Verify get_cached finds it by exact word, by raw headword, and by unhyphenated headword
    assert db.get_cached("frastøde") is not None
    assert db.get_cached("fra-stød") is not None
    assert db.get_cached("frastød") is not None
    
    # Negative cache testing
    assert "ukendt_ord_xyz" not in db.get_not_found_words()
    db.set_not_found("ukendt_ord_xyz")
    assert "ukendt_ord_xyz" in db.get_not_found_words()


def test_grundede_resolution_and_clean_formatting(tmp_path: Path):
    """Verify that 'grundede' resolves to 'grunde' (vb.), definitions are completed,

    quotes have no raw | separators, ▸ is replaced by • or removed, and summary_hook does not repeat.
    """
    from ordpresse.db import Database, init_datasets_db

    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)

    with conn:
        # 1. Insert cor_sem rows for both 'grund' (sb.) and 'grunde' (vb.)
        conn.executemany(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            [
                ("COR.SEM.1002897.01", "COR.43648", "grund", "sb."),
                ("COR.SEM.1002897.02", "COR.43648", "grund", "sb."),
                ("COR.SEM.1002897.04", "COR.43648", "grund", "sb."),
                ("COR.SEM.1002898.01", "COR.31633", "grunde", "vb."),
            ],
        )
        # 2. Insert definitions with ellipsis and multiple quotes separated by |
        conn.executemany(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            [
                (
                    "COR.SEM.1002897.01",
                    "landjorden som et underlag man kan stå fast…",
                    "90 pct. af alle drukneulykker sker mindre end 10 meter fra fast grund",
                ),
                (
                    "COR.SEM.1002897.02",
                    "grundlag som giver (u)sikkerhed; udgangspunkt hvorfra noget udvikler…",
                    "alle tre talere holdt sig på sikker grund|Thomas Møller var på usikker grund|Christian bygger nu videre",
                ),
                (
                    "COR.SEM.1002897.04",
                    "grundlag som forårsager, motiverer el. forklarer et…",
                    "Hvad er grunden til at du har valgt tysk som fag?|Jeg holder på at man altid skal sige sandheden",
                ),
                (
                    "COR.SEM.1002898.01",
                    "tænke nøje over",
                    "[hun] tog et års arbejde på et posthus, mens hun grundede over fremtiden",
                ),
            ],
        )
        # 3. Insert cor_forms: include adjective 'grundede' that must NOT attach to 'grund' sb.
        conn.executemany(
            "INSERT INTO cor_forms (lemma_id, lemma, gloss, gram, pos, fullform, norm_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("COR.17760", "grund", "", "adj.", "adj.", "grundede", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grund", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grunden", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grunde", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grund-", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grunde", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grundede", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grundet", ""),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)
    output_dir = tmp_path / "output_test"
    pipeline = Pipeline(
        db=db,
        output_dir=output_dir,
        bookname="Test Grundede StarDict",
    )
    result = pipeline.run(
        custom_words=["grundede", "grund"],
        include_seeds=False,
        full_dictionary=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])

    # --- Verification 1: 'grundede' MUST resolve to 'grunde' (vb.), NOT 'grund sb.' ---
    html_grundede = reader.lookup("grundede")
    assert html_grundede is not None, "Lookup for 'grundede' in compiled StarDict returned None!"
    assert "<b>grunde</b>" in html_grundede, f"Expected 'grundede' to resolve to 'grunde', but got: {html_grundede}"
    assert "<i>vb.</i>" in html_grundede
    assert "<b>grund</b> <i>sb.</i>" not in html_grundede

    # Verify meaning about thinking deeply / gruble / fundere is included
    assert any(term in html_grundede for term in ["tænke", "gruble", "fundere", "overveje"]), (
        f"Missing thinking/pondering sense in 'grunde': {html_grundede}"
    )

    # Verify no raw | separator in quotes
    assert "|" not in html_grundede, f"Raw '|' quote separator leaked into 'grunde': {html_grundede}"

    # Verify no disallowed ▸ symbol
    assert "▸" not in html_grundede, f"Disallowed ▸ character found in 'grunde': {html_grundede}"

    # --- Verification 2: 'grund' sb. must NOT contain 'grundede' in its inflections ---
    html_grund = reader.lookup("grund")
    assert html_grund is not None, "Lookup for 'grund' in compiled StarDict returned None!"
    assert "<b>grund</b>" in html_grund
    assert "<i>sb.</i>" in html_grund

    # The header before the definitions (<ol>) must NOT include 'grundede'
    header_html = html_grund.split("<ol>")[0]
    assert "grundede" not in header_html, f"Nouns cannot have 'grundede'! Found in header: {header_html}"

    # Verify definitions in 'grund' are completed and do not end in '…' or '...'
    assert "…" not in html_grund, f"Truncated definition with '…' found in 'grund': {html_grund}"
    assert "..." not in html_grund, f"Truncated definition with '...' found in 'grund': {html_grund}"
    assert "landjorden som et underlag man kan stå fast, gå eller færdes på" in html_grund

    # Verify quotes in 'grund' have no raw '|'
    assert "|" not in html_grund, f"Raw '|' quote separator leaked into 'grund': {html_grund}"

    # Verify no disallowed ▸ symbol in 'grund'
    assert "▸" not in html_grund, f"Disallowed ▸ character found in 'grund': {html_grund}"

    # Verify summary_hook does not repeat definition 1 + 2
    assert "1) grundlag som" not in html_grund, f"Redundant concatenated summary hook found in 'grund': {html_grund}"

