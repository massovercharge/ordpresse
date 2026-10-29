"""Tests for Phase 5: Scraping Quality, Empty-Skeleton Prevention & Cache Triage."""

import json
from pathlib import Path
import sqlite3

from ordpresse.pipeline import Pipeline
from ordpresse.stardict_reader import StarDictReader
from ordpresse.triage import run_triage, is_clean_keep_row, extract_definition_text


def test_alderstegen_clean_pos_and_no_sitater(tmp_path: Path):
    """Verify that looking up 'alderstegen' produces normalized POS 'adj.', proper word spacing, and zero 'SITATER' noise."""
    output_dir = tmp_path / "output_alderstegen"
    pipeline = Pipeline(output_dir=output_dir, bookname="Alderstegen Test")

    result = pipeline.run(
        custom_words=["alderstegen"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    html = reader.lookup("alderstegen")
    assert html is not None, "Lookup for 'alderstegen' failed!"
    assert "<b>alderstegen</b>" in html
    assert "<i>adj.</i>" in html
    assert "alderstegenadjektiv" not in html
    assert "<i>adjektiv</i>" not in html
    assert "SITATER" not in html
    assert any(term in html.lower() for term in ["alder", "ældgammel", "højt oppe i årene"])


def test_taellelys_not_empty_cor_skeleton(tmp_path: Path):
    """Verify that looking up 'tællelyset' resolves to 'tællelys', has POS 'sb. itk.',
    and includes a full definition about tallow/animal fat candles (never a blank Kilde: COR card)."""
    output_dir = tmp_path / "output_taellelys"
    pipeline = Pipeline(output_dir=output_dir, bookname="Taellelys Test")

    result = pipeline.run(
        custom_words=["tællelyset"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    # 1. Lookup inflected form in .syn
    html_inf = reader.lookup("tællelyset")
    assert html_inf is not None, "Lookup for 'tællelyset' failed in .syn!"
    assert "<b>tællelys</b>" in html_inf
    assert "sb. itk." in html_inf
    assert any(term in html_inf.lower() for term in ["dyrefedt", "tælle", "talg"])
    assert "Kilde: COR</div>" not in html_inf

    # 2. Lookup headword in .idx
    html_hw = reader.lookup("tællelys")
    assert html_hw is not None, "Lookup for 'tællelys' failed in .idx!"
    assert any(term in html_hw.lower() for term in ["dyrefedt", "tælle", "talg"])


def test_dernedefra_no_ddo_placeholder_and_has_meaning(tmp_path: Path):
    """Verify that 'dernedefra' has POS 'adv.', does NOT contain 'ingen betydningsangivelse',
    and provides a clear explanation (fra et sted længere nede / dernede)."""
    output_dir = tmp_path / "output_dernedefra"
    pipeline = Pipeline(output_dir=output_dir, bookname="Dernedefra Test")

    result = pipeline.run(
        custom_words=["dernedefra"],
        include_seeds=False,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    html = reader.lookup("dernedefra")
    assert html is not None, "Lookup for 'dernedefra' failed!"
    assert "<b>dernedefra</b>" in html
    assert "adv." in html
    assert "ingen betydningsangivelse" not in html
    assert "ORD I NÆRHEDEN" not in html
    assert any(term in html.lower() for term in ["fra et sted længere nede", "dernede", "nedefra"])


def test_uefterrettelig_has_real_definition(tmp_path: Path):
    """Verify that 'uefterrettelige' -> 'uefterrettelig' has a real definition and never 'Historisk opslag for'."""
    output_dir = tmp_path / "output_uefterrettelig"
    pipeline = Pipeline(output_dir=output_dir, bookname="Uefterrettelig Test")

    result = pipeline.run(
        custom_words=["uefterrettelige"],
        include_seeds=False,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    html = reader.lookup("uefterrettelige")
    assert html is not None, "Lookup for 'uefterrettelige' failed!"
    assert "Historisk opslag for" not in html
    assert any(term in html.lower() for term in ["rette sig efter", "upålidelig", "ulydig"])


def test_cache_triage_keep_salvage_purge(tmp_path: Path):
    """Test triage classification into KEEP, SALVAGED, and PURGED buckets."""
    cache_path = tmp_path / "test_cache.sqlite3"
    purged_path = tmp_path / "purged_words.txt"

    conn = sqlite3.connect(cache_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE definitions (
                word TEXT PRIMARY KEY,
                source TEXT,
                headword TEXT,
                html_content TEXT,
                synonyms_json TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # a) Clean KEEP row
        clean_html = (
            "<div><b>banco</b> <i>sb.</i> <small>(banko)</small></div>"
            '<div style="margin:3px 0;padding:2px 6px;border-left:2px solid #555;">'
            "<b>▸</b> Betegnelse for kurantmønt garanteret af nationalbanken.</div>"
            "<div><ol><li>Møntbetegnelse knyttet til bankpenge el. sedler.</li></ol></div>"
            '<div><small style="color:#666;">Kilde: ODS</small></div>'
        )
        conn.execute(
            "INSERT INTO definitions (word, source, headword, html_content, synonyms_json) VALUES (?, ?, ?, ?, ?)",
            ("banco", "ods", "banco", clean_html, json.dumps(["banko"])),
        )

        # b) NAOB entry with glued alderstegenadjektiv and SITATER noise (SALVAGE)
        salvageable_html = (
            "<div><b>alderstegen</b> <i>alderstegenadjektiv</i></div>"
            "<div><ol><li>litterært svært gammel; tilårskommen SITATER derinde sidder kæmpen Koll...</li></ol></div>"
            '<div><small style="color:#666;">Kilde: NAOB</small></div>'
        )
        conn.execute(
            "INSERT INTO definitions (word, source, headword, html_content, synonyms_json) VALUES (?, ?, ?, ?, ?)",
            ("alderstegen", "naob", "alderstegen", salvageable_html, json.dumps(["alderstegne"])),
        )

        # c) Dummy placeholder entries (PURGE)
        dummy_ddo_html = (
            "<div><b>dummy_ddo</b></div>"
            "<div>Dette ord har foreløbig ingen betydningsangivelse. Se eventuelt: dernede</div>"
            "<div>ORD I NÆRHEDEN: nedenfra, fra neden</div>"
        )
        conn.execute(
            "INSERT INTO definitions (word, source, headword, html_content, synonyms_json) VALUES (?, ?, ?, ?, ?)",
            ("dummy_ddo", "ods", "dummy_ddo", dummy_ddo_html, json.dumps([])),
        )

        dummy_hist_html = (
            "<div><b>dummy_hist</b></div>"
            "<div>Historisk opslag for »dummy_hist«</div>"
        )
        conn.execute(
            "INSERT INTO definitions (word, source, headword, html_content, synonyms_json) VALUES (?, ?, ?, ?, ?)",
            ("dummy_hist", "ods", "dummy_hist", dummy_hist_html, json.dumps([])),
        )
    conn.close()

    res = run_triage(
        cache_db_path=cache_path,
        purged_output_path=purged_path,
        dry_run=False,
    )

    assert res.total_before == 4
    assert res.keep_count == 1
    assert res.salvaged_count == 1
    assert res.purged_count == 2

    # Verify database contents after triage
    conn2 = sqlite3.connect(cache_path)
    cur = conn2.cursor()
    cur.execute("SELECT word, html_content FROM definitions ORDER BY word")
    remaining_rows = cur.fetchall()
    conn2.close()

    assert len(remaining_rows) == 2
    words_remaining = [r[0] for r in remaining_rows]
    assert "banco" in words_remaining
    assert "alderstegen" in words_remaining
    assert "dummy_ddo" not in words_remaining
    assert "dummy_hist" not in words_remaining

    # Verify salvaged row has clean POS and no SITATER
    salvaged_html = [r[1] for r in remaining_rows if r[0] == "alderstegen"][0]
    assert "alderstegenadjektiv" not in salvaged_html
    assert "SITATER" not in salvaged_html
    assert "<i>adj.</i>" in salvaged_html
    assert any(term in salvaged_html.lower() for term in ["gammel", "tilårskommen", "alder", "højt oppe"])

    # Verify purged words file
    purged_content = purged_path.read_text(encoding="utf-8")
    assert "dummy_ddo" in purged_content
    assert "dummy_hist" in purged_content


def test_headword_prefix_not_truncated(tmp_path: Path):
    """Verify that headwords beginning with letters in [ivxlcdm] (like lægdslem, værkbrudden, city, mandfolk)
    do not lose their first letter due to the Roman numeral cleanup regex."""
    output_dir = tmp_path / "output_prefixes"
    pipeline = Pipeline(output_dir=output_dir, bookname="Prefix Test")

    result = pipeline.run(
        custom_words=["lægdslem", "værkbrudden", "city", "mandfolk"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    # Verify exact headwords exist in .idx
    hw_list = [hw for hw, _, _ in reader.idx_entries]
    assert "lægdslem" in hw_list, "lægdslem was corrupted to ægdslem!"
    assert "ægdslem" not in hw_list
    assert "værkbrudden" in hw_list, "værkbrudden was corrupted to ærkbrudden!"
    assert "ærkbrudden" not in hw_list


def test_typographic_spacing_repairs():
    """Verify that sanitize_ods_text repairs glued OCR typography around parentheses and abbreviations."""
    from ordpresse.sources.ods_scraper import sanitize_ods_text

    raw1 = "(stor) drengell. (især)ungt menneske(paa en 17-18 aar)"
    cleaned1 = sanitize_ods_text(raw1)
    assert "dreng ell." in cleaned1
    assert "(især) ungt" in cleaned1
    assert "menneske (paa" in cleaned1

    raw2 = "volde uroell.plage"
    cleaned2 = sanitize_ods_text(raw2)
    assert "uro ell. plage" in cleaned2

    raw3 = "merke medtjærekors; jf.avtørke"
    cleaned3 = sanitize_ods_text(raw3)
    assert "jf. avtørke" in cleaned3


def test_author_as_definition_rejected_and_ejendomsfaellesskab_salvaged(tmp_path: Path):
    """Verify that author signatures like 'Anna Larssen Bjørner' are rejected as definitions,
    and 'ejendomsfællesskab' is salvaged to its authentic historical definition."""
    from ordpresse.sources.ods_scraper import is_valid_definition

    assert not is_valid_definition("Anna Larssen Bjørner")
    assert not is_valid_definition("H.F. Feilberg")
    assert not is_valid_definition("Goldschm.. Aakj..")
    assert not is_valid_definition("L. Holberg Ep..")
    assert not is_valid_definition("Romneya Coulteri.")
    assert is_valid_definition("Fællesskab om ejendom el. formue mellem ægtefæller.")

    output_dir = tmp_path / "output_ejendomsfaellesskab"
    pipeline = Pipeline(output_dir=output_dir, bookname="Ejendomsfaellesskab Test")
    result = pipeline.run(
        custom_words=["ejendomsfællesskab"],
        include_seeds=True,
        allow_network=False,
    )
    reader = StarDictReader(result.files["ifo"])
    html = reader.lookup("ejendomsfællesskab")
    assert html is not None
    assert "Anna Larssen Bjørner" not in html
    assert any(term in html.lower() for term in ["ejendom", "formue", "fællesskab"])


def test_recovered_words_natlig_runge_skraeve_vildsom(tmp_path: Path):
    """Verify that 'natlig', 'runge', 'skræve', 'vildsom' are built with rich definitions."""
    output_dir = tmp_path / "output_recovered"
    pipeline = Pipeline(output_dir=output_dir, bookname="Recovered Words Test")
    result = pipeline.run(
        custom_words=["natlig", "runge", "skræve", "vildsom"],
        include_seeds=True,
        allow_network=False,
    )
    reader = StarDictReader(result.files["ifo"])
    for w in ["natlig", "runge", "skræve", "vildsom"]:
        html = reader.lookup(w)
        assert html is not None, f"Word '{w}' was not found in dictionary!"
        assert "HISTORISK BETYDNING" in html or "▸" in html
