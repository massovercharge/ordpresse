"""Unit and regression tests verifying definition integrity and smart summary_hook handling."""

from pathlib import Path
import sqlite3
import pytest

from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.formatter import render_canonical_html
from ordpresse.synthesizer import (
    truncate_summary_hook,
    should_render_summary_hook,
    synthesize_cor_sem_summary_hook,
    COR_SEM_DEFINITION_COMPLETIONS,
)
from ordpresse.db import Database, init_datasets_db


def test_truncate_summary_hook_only_at_sentence_boundaries():
    # 1. Fits within max_chars: preserved completely without ellipsis
    short = "Kort og præcis forklaring."
    assert truncate_summary_hook(short, max_chars=165) == short

    # 2. Text without period or semicolon exceeding max_chars:
    # Rule: NEVER slice mid-sentence with "..." after arbitrary character count!
    long_no_punct = "Dette er en meget lang sætning som forklarer et begreb i detaljer uden noget punktum eller semikolon overhovedet og som fortsætter ud over grænsen"
    assert "..." not in truncate_summary_hook(long_no_punct, max_chars=50)
    assert truncate_summary_hook(long_no_punct, max_chars=50) == long_no_punct

    # 3. Text with natural semicolon: cuts at semicolon
    text_with_semi = "Lån der ydes mod panteret i fast ejendom; typisk et realkreditlån med lang løbetid til køb af bolig"
    res_semi = truncate_summary_hook(text_with_semi, max_chars=50)
    assert res_semi == "Lån der ydes mod panteret i fast ejendom."
    assert "..." not in res_semi

    # 4. Text with natural period: cuts at period
    text_with_period = "Person som udfører havearbejde. Vedkommende luger ukrudt og slår græs."
    res_period = truncate_summary_hook(text_with_period, max_chars=40)
    assert res_period == "Person som udfører havearbejde."
    assert "..." not in res_period


def test_should_render_summary_hook_smart_handling():
    # Case 1: Single definition identical to summary_hook -> MUST BE OMITTED
    entry_single_identical = CanonicalEntry(
        headword="prioritetslån",
        pos="sb. itk.",
        summary_hook="lån der ydes mod panteret i fast ejendom typisk et realkreditlån",
        modern_senses=[
            SenseItem(definition="lån der ydes mod panteret i fast ejendom typisk et realkreditlån")
        ],
    )
    assert should_render_summary_hook(entry_single_identical) is False
    html = render_canonical_html(entry_single_identical)
    assert "▸" not in html
    assert "lån der ydes mod panteret i fast ejendom" in html

    # Case 2: Single modern definition (even if slightly different in punctuation) -> MUST BE OMITTED
    entry_single_modern = CanonicalEntry(
        headword="nybegynder",
        pos="sb. fk.",
        summary_hook="person der netop er begyndt på et fag",
        modern_senses=[
            SenseItem(definition="person der netop er begyndt at beskæftige sig med et fag")
        ],
    )
    assert should_render_summary_hook(entry_single_modern) is False
    html_nyb = render_canonical_html(entry_single_modern)
    assert "▸" not in html_nyb

    # Case 3: Multiple definitions -> MUST BE SHOWN
    entry_multiple = CanonicalEntry(
        headword="bord",
        pos="sb. itk.",
        summary_hook="1) møbel med plan flade; 2) serveret mad",
        modern_senses=[
            SenseItem(definition="møbel bestående af en vandret plade båret af et eller flere ben"),
            SenseItem(definition="mad og drikke der serveres ved et måltid"),
        ],
    )
    assert should_render_summary_hook(entry_multiple) is True
    html_multi = render_canonical_html(entry_multiple)
    assert "▸" in html_multi
    assert "1) møbel med plan flade; 2) serveret mad" in html_multi

    # Case 4: Single definition with distinct historical resume (e.g. curated 1800s entry) -> MUST BE SHOWN
    entry_historical = CanonicalEntry(
        headword="lægdslem",
        pos="sb. itk.",
        summary_hook="Fattiglem tildelt et bestemt lægd til underhold og forsørgelse under den ældre fattiglovgivning.",
        historical_senses=[
            SenseItem(
                definition="fattiglem der ifølge den ældre fattiglovgivning var henvist til forsørgelse og omgangslæg hos beboerne i et bestemt lægd.",
                label="hist.",
                source_tag="ODS",
            )
        ],
        sources_used=["ODS"],
    )
    assert should_render_summary_hook(entry_historical) is True
    html_hist = render_canonical_html(entry_historical)
    assert "▸" in html_hist
    assert "Fattiglem tildelt et bestemt lægd" in html_hist


def test_cor_sem_definition_completion_and_full_sentence_preservation(tmp_path: Path):
    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)

    with conn:
        conn.executemany(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            [
                ("COR.SEM.1006235.01", "11040832", "prioritetslån", "sb. itk."),
                ("COR.SEM.87422.01", "11036310", "nybegynder", "sb. fk."),
            ],
        )
        # Note: raw COR-SEM definitions stored with ellipsis from DSL
        conn.executemany(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            [
                ("COR.SEM.1006235.01", "lån der ydes mod panteret i fast…", "Prioritetslån ydes kun..."),
                ("COR.SEM.87422.01", "person der netop er begyndt at beskæftige sig med et…", "god af en nybegynder"),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)

    # 1. lookup_cor_lemma_definitions completes the definition
    res_prioritet = db.lookup_cor_lemma_definitions("prioritetslån")
    assert len(res_prioritet) == 1
    assert "ejendom" in res_prioritet[0]["definition"]
    assert not res_prioritet[0]["definition"].endswith("…")

    res_nybegynder = db.lookup_cor_lemma_definitions("nybegynder")
    assert len(res_nybegynder) == 1
    assert "område el.lign." in res_nybegynder[0]["definition"] or "kendskab dertil" in res_nybegynder[0]["definition"]

    # 2. get_all_cor_lemmas_with_definitions builds full sentences and omits double confection
    all_cor = db.get_all_cor_lemmas_with_definitions()
    by_hw = {e["headword"]: e for e in all_cor}

    assert "prioritetslån" in by_hw
    prioritet_entry = by_hw["prioritetslån"]
    assert "fast ejendom" in prioritet_entry["html_content"]
    assert "▸" not in prioritet_entry["html_content"]  # Single definition: hook omitted!

    assert "nybegynder" in by_hw
    nyb_entry = by_hw["nybegynder"]
    assert "▸" not in nyb_entry["html_content"]  # Single definition: hook omitted!
    assert "beskæftige sig med et" in nyb_entry["html_content"]
