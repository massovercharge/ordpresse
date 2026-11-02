"""Unit tests for AGENT_SPEC_V7: ODS sub-sense extraction, meta-header filtering, and 1800s coverage."""

from pathlib import Path
import pytest

from ordpresse.pipeline import Pipeline
from ordpresse.stardict_reader import StarDictReader
from ordpresse.sources.ods_scraper import (
    is_meta_header_not_definition,
    clean_ods_definition_text,
    parse_ods_html,
)


def test_is_meta_header_not_definition_classifies_correctly():
    """Verify that grammatical section headers are detected and definitions are preserved."""
    # Grammatical routing phrases must be rejected
    assert is_meta_header_not_definition("som verbum.")
    assert is_meta_header_not_definition("(nu sj.) som verbum.")
    assert is_meta_header_not_definition("1. (hist.) (nu sj.) som verbum.")
    assert is_meta_header_not_definition("brugt som adj., (jf.")
    assert is_meta_header_not_definition("2. (hist.) brugt som adj., (jf.")
    assert is_meta_header_not_definition("i perf. part.")
    assert is_meta_header_not_definition("i pass.")
    assert is_meta_header_not_definition("refl.")
    assert is_meta_header_not_definition("jf. se under")

    # Real definitions must NOT be rejected
    assert not is_meta_header_not_definition("leve til ende; tilbringe eller afslutte sit liv.")
    assert not is_meta_header_not_definition("svækket af høj alder, slid eller udsvævelser; affældig, udslidt.")
    assert not is_meta_header_not_definition("bringe i armod og forfald; udpine; ruinere.")
    assert not is_meta_header_not_definition("gøre fattig; ruinere.")
    assert not is_meta_header_not_definition("behandle et råmateriale så der dannes et anvendeligt produkt.")


def test_clean_ods_definition_text():
    """Verify clean_ods_definition_text strips routing prefixes and unclosed references."""
    # Leading routing prefix stripped
    cleaned = clean_ods_definition_text("part. udlevet brugt som adj., (jf. aflevet) svækket af høj alder")
    assert "svækket af høj alder" in cleaned
    assert "brugt som adj" not in cleaned

    # Trailing unclosed (jf. stripped
    cleaned_ref = clean_ods_definition_text("leve sit liv til ende (jf. udleve)")
    assert cleaned_ref == "leve sit liv til ende"

    cleaned_unclosed = clean_ods_definition_text("leve sit liv til ende (jf.")
    assert cleaned_unclosed == "leve sit liv til ende"


def test_udlevede_has_real_definitions_and_no_meta_headers(tmp_path: Path):
    """Lookup 'udlevede' -> resolves to 'udleve', contains modern & historical senses, and no meta-headers."""
    output_dir = tmp_path / "output_udleve"
    pipeline = Pipeline(output_dir=output_dir, bookname="Udleve Test")

    result = pipeline.run(
        custom_words=["udlevede", "udleve"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])

    # 1. Lookup inflected form 'udlevede' in .syn
    html_inf = reader.lookup("udlevede")
    assert html_inf is not None, "Lookup for 'udlevede' failed!"
    assert any(term in html_inf for term in ["udleve", "udlevet"])
    assert "som verbum" not in html_inf
    assert "(jf." not in html_inf
    assert any(term in html_inf.lower() for term in ["drømme", "lyster", "handling", "alder", "slid", "affældig"])

    # 2. Lookup headword 'udleve' in .idx
    html_hw = reader.lookup("udleve")
    assert html_hw is not None, "Lookup for 'udleve' failed!"
    assert "<b>udleve</b>" in html_hw
    assert "som verbum" not in html_hw
    assert "(jf." not in html_hw
    # Verify both modern and historical sections
    assert "MODERNE BETYDNING" in html_hw or "COR-SEM" in html_hw or "drømme" in html_hw
    assert "HISTORISK BETYDNING" in html_hw


def test_forarmet_lookup_hits_exact_entry(tmp_path: Path):
    """Lookup 'forarmet' and 'forarmede' -> resolves directly with definitions about poverty/ruin."""
    output_dir = tmp_path / "output_forarmet"
    pipeline = Pipeline(output_dir=output_dir, bookname="Forarmet Test")

    result = pipeline.run(
        custom_words=["forarmet", "forarmede", "forarme"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])

    # 1. Direct headword lookup for 'forarmet'
    html_forarmet = reader.lookup("forarmet")
    assert html_forarmet is not None, "Lookup for 'forarmet' failed!"
    assert "<b>forarmet</b>" in html_forarmet
    assert any(term in html_forarmet.lower() for term in ["fattigdom", "armod", "nød", "udpin"])

    # 2. Inflection lookup for 'forarmede'
    html_forarmede = reader.lookup("forarmede")
    assert html_forarmede is not None, "Lookup for 'forarmede' failed!"
    assert any(hw in html_forarmede for hw in ["<b>forarmet</b>", "<b>forarme</b>"])
    assert any(term in html_forarmede.lower() for term in ["fattigdom", "armod", "nød", "udpin"])

    # 3. Base verb lookup for 'forarme'
    html_forarme = reader.lookup("forarme")
    assert html_forarme is not None, "Lookup for 'forarme' failed!"
    assert "<b>forarme</b>" in html_forarme
    assert any(term in html_forarme.lower() for term in ["fattig", "armod", "udpin", "ruin"])


def test_forarbejde_not_truncated(tmp_path: Path):
    """Lookup 'forarbejde' -> no trailing cliffhangers like 'anvendeligt…' or 'forholde sig…'."""
    import sqlite3
    from ordpresse.db import Database, init_datasets_db

    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)
    with conn:
        conn.executemany(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            [
                ("COR.SEM.35019.01", "11014166", "forarbejde", "vb."),
                ("COR.SEM.35019.02", "11014166", "forarbejde", "vb."),
            ],
        )
        conn.executemany(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            [
                ("COR.SEM.35019.01", "behandle et råmateriale el.lign. så der dannes et anvendeligt…", None),
                ("COR.SEM.35019.02", "arbejde mentalt med noget ved at analysere og forholde sig…", None),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)
    output_dir = tmp_path / "output_forarbejde"
    pipeline = Pipeline(db=db, output_dir=output_dir, bookname="Forarbejde Test")

    result = pipeline.run(
        custom_words=["forarbejde"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])
    html = reader.lookup("forarbejde")
    assert html is not None, "Lookup for 'forarbejde' failed!"
    assert "<b>forarbejde</b>" in html

    # Verify cliffhanger completions
    assert "anvendeligt…" not in html
    assert "anvendeligt..." not in html
    assert "forholde sig…" not in html
    assert "forholde sig..." not in html
    assert any(term in html for term in ["anvendeligt produkt", "bearbejde", "forholde sig til"])


def test_ods_subsense_extraction_and_header_skipping():
    """Verify parse_ods_html skips top-level grammatical headers and descends into child sub-senses."""
    raw_html = '''
    <html><body>
    <div class="artikel">
      <div class="definition">
        <span class="match">udleve</span>
        <span class="allow-glossing">vb.</span>
        <div class="semem">
          <span class="def">1. (hist.) (nu sj.) som verbum.</span>
          <div class="subsem">
            <span class="def">1.1. leve sit liv fuldt ud til ende; tilbringe tiden.</span>
          </div>
        </div>
        <div class="semem">
          <span class="def">2. (hist.) brugt som adj., (jf.</span>
          <div class="subsem">
            <span class="def">2.1. svækket af høj alder og udsvævelser; affældig.</span>
            * Blicher. »en udlevet mand«
          </div>
        </div>
      </div>
    </div>
    </body></html>
    '''
    res = parse_ods_html("udleve", raw_html)
    assert res is not None
    html_out = res["html_content"]

    # Must NOT contain grammatical headers
    assert "som verbum." not in html_out
    assert "(jf." not in html_out

    # Must contain the semantic sub-senses
    assert "leve sit liv fuldt ud til ende" in html_out
    assert "svækket af høj alder og udsvævelser" in html_out
    assert "Blicher" in html_out


def test_overstroe_single_layer_polish_and_tail_stripping(tmp_path: Path):
    """Task 3.2: Verify single-layer ODS tail polish and parenthetical suppression for overstrø."""
    import sqlite3
    from ordpresse.formatter import render_canonical_html
    from ordpresse.models import CanonicalEntry, SenseItem
    from ordpresse.db import clean_corrupted_cached_definitions

    # 1. clean_ods_definition_text strips dangling syntax tails
    t1 = clean_ods_definition_text("dække (helt ell. delvis) med noget, der strøs paa; bestrø (over det hele); især i forb. m. præp.")
    assert t1 == "dække (helt ell. delvis) med noget, der strøs paa; bestrø (over det hele)."
    assert "; især i forb. m. præp." not in t1

    t2 = clean_ods_definition_text("dække med aske; især i forb. m.")
    assert t2 == "dække med aske."
    assert "; især i forb. m." not in t2

    t3 = clean_ods_definition_text("udbrede noget; bl. a. i forb.")
    assert t3 == "udbrede noget."
    assert "; bl. a. i forb." not in t3

    # 2. render_canonical_html suppresses parenthetical if 100% identical to headword
    entry_overstroe = CanonicalEntry(
        headword="overstrø",
        pos="vb.",
        inflections={"overstrø", "over-strø"},
        historical_senses=[
            SenseItem(
                definition="dække (helt ell. delvis) med noget, der strøs paa; bestrø (over det hele).",
                label="hist.",
                source_tag="ODS",
            )
        ],
        sources_used=["ODS"],
    )
    html_overstroe = render_canonical_html(entry_overstroe)
    assert "<div><b>overstrø</b> <i>vb.</i></div>" in html_overstroe
    assert "(overstrø)" not in html_overstroe
    assert "(over-strø)" not in html_overstroe

    # 3. 3-layer article like lægdslem preserves its full parenthetical and layers
    entry_laegdslem = CanonicalEntry(
        headword="lægdslem",
        pos="sb. itk.",
        inflections={"legdelem", "legdelemmer", "lægdslemmer"},
        historical_spellings=["Lægdslem", "Lejdslem"],
        summary_hook="Fattiglem tildelt et bestemt lægd til underhold og forsørgelse under den ældre fattiglovgivning.",
        historical_senses=[
            SenseItem(
                definition="Fattiglem el. almisselem på landet, der forsørgedes af lægdet, ofte ved omgangsforsørgelse fra gård til gård.",
                label="hist., landbo.",
                source_tag="ODS",
            )
        ],
        realia_note="I 1700- og 1800-tallets Danmark og Sverige (rotehjon) var fattigvæsenet inddelt i lægder eller roder.",
        sources_used=["ODS", "Molbech", "Salmonsens"],
    )
    html_laegdslem = render_canonical_html(entry_laegdslem)
    assert "<small>(legdelem, legdelemmer, lægdslemmer; ældre: Lægdslem, Lejdslem)</small>" in html_laegdslem
    assert "<b>•</b>" in html_laegdslem
    assert "[Historisk kontekst]" in html_laegdslem

    # 4. clean_corrupted_cached_definitions cleans cached overstrø row while preserving lægdslem
    db_file = tmp_path / "test_cache.sqlite3"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE definitions (word TEXT PRIMARY KEY, source TEXT, headword TEXT, html_content TEXT, synonyms_json TEXT)")

    dirty_overstroe = (
        '<div><b>overstrø</b> <i>vb.</i> <small>(overstrø)</small></div>'
        '<div><small><b>HISTORISK BETYDNING (ODS / 1700–1950)</b></small><ol>'
        '<li><i>(hist.)</i> dække (helt ell. delvis) med noget, der strøs paa; bestrø (over det hele); især i forb. m. præp.</li>'
        '</ol></div><div><small style="color:#666;">Kilde: ODS</small></div>'
    )
    clean_laegdslem = html_laegdslem

    conn.execute("INSERT INTO definitions VALUES ('overstrø', 'ods', 'overstrø', ?, '[]')", (dirty_overstroe,))
    conn.execute("INSERT INTO definitions VALUES ('lægdslem', 'ods', 'lægdslem', ?, '[]')", (clean_laegdslem,))
    conn.commit()

    clean_corrupted_cached_definitions(conn)

    cur = conn.cursor()
    cur.execute("SELECT html_content FROM definitions WHERE word = 'overstrø'")
    row_overstroe = cur.fetchone()[0]
    assert "<small>(overstrø)</small>" not in row_overstroe
    assert "; især i forb. m. præp." not in row_overstroe
    assert "<div><b>overstrø</b> <i>vb.</i></div>" in row_overstroe
    assert "bestrø (over det hele)." in row_overstroe

    cur.execute("SELECT html_content FROM definitions WHERE word = 'lægdslem'")
    row_laegdslem = cur.fetchone()[0]
    assert row_laegdslem == clean_laegdslem, "Lægdslem was unexpectedly modified!"
    conn.close()

