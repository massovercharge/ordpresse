"""Phase 5 Comprehensive Lexical Integrity & Priority Verification Tests."""

from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

from ordpresse.db import Database, init_datasets_db
from ordpresse.formatter import render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import classify_external_lookup_priority
from ordpresse.pipeline import Pipeline
from ordpresse.sources.rate_limiter import coordinator
from ordpresse.stardict_reader import StarDictReader
from ordpresse.web.app import app


def test_priority_classification_tiers():
    """Verify that classify_external_lookup_priority strictly maps entries to the 3 tiers:
    - Tier 1: Completely missing / 0 senses (empty skeleton).
    - Tier 2: Cliffhanger truncation (< 28 chars or ending in a function word ellipsis).
    - Tier 3: Passive comma-list truncation with usable main clause.
    - None: Complete, untruncated entries.
    """
    # 1. Missing or None -> Priority 1
    assert classify_external_lookup_priority(None) == 1

    # 2. Empty skeleton (0 modern, 0 historical, 0 summary_hook) -> Priority 1
    empty_entry = CanonicalEntry(
        headword="tællelys",
        pos="sb. itk.",
        inflections={"tællelys"},
        summary_hook="",
        modern_senses=[],
        historical_senses=[],
    )
    assert classify_external_lookup_priority(empty_entry) == 1

    # 3. Cliffhanger truncated entries -> Priority 2
    # Cliffhanger preposition/conjunction ending
    cliff_1 = CanonicalEntry(
        headword="prioritetslån",
        pos="sb. itk.",
        inflections={"prioritetslån"},
        summary_hook="",
        modern_senses=[
            SenseItem(
                definition="lån der ydes mod panteret i fast...",
                quote=None,
                source_tag="COR-SEM",
            )
        ],
    )
    assert classify_external_lookup_priority(cliff_1) == 2

    # Short definition (< 28 chars) with ellipsis
    cliff_2 = CanonicalEntry(
        headword="søskende",
        pos="sb. pl.",
        inflections={"søskende"},
        summary_hook="",
        modern_senses=[
            SenseItem(
                definition="dvs. brødre...",
                quote=None,
                source_tag="COR-SEM",
            )
        ],
    )
    assert classify_external_lookup_priority(cliff_2) == 2

    # Another cliffhanger function word ("over for...")
    cliff_3 = CanonicalEntry(
        headword="respektløs",
        pos="adj.",
        inflections={"respektløs"},
        summary_hook="",
        modern_senses=[
            SenseItem(
                definition="som ikke viser hensyn eller ærbødighed over for...",
                quote=None,
                source_tag="COR-SEM",
            )
        ],
    )
    assert classify_external_lookup_priority(cliff_3) == 2

    # 4. Usable context / comma-list truncation -> Priority 3 (Passive)
    passive_entry = CanonicalEntry(
        headword="stakkels",
        pos="adj.",
        inflections={"stakkels"},
        summary_hook="",
        modern_senses=[
            SenseItem(
                definition="som vækker medlidenhed pga. sin svaghed, hjælpeløshed...",
                quote=None,
                source_tag="COR-SEM",
            )
        ],
    )
    assert classify_external_lookup_priority(passive_entry) == 3

    # 5. Complete entry -> None
    complete_entry = CanonicalEntry(
        headword="stakkels",
        pos="adj.",
        inflections={"stakkels"},
        summary_hook="",
        modern_senses=[
            SenseItem(
                definition="som vækker medlidenhed pga. sin svaghed, hjælpeløshed eller ulykkelige situation",
                quote=None,
                source_tag="DDO",
            )
        ],
    )
    assert classify_external_lookup_priority(complete_entry) is None


def test_queue_orders_priority_1_before_priority_2():
    """Verify that when registering a build with mixed Priority 1 and Priority 2 words:
    - GET /api/build/{build_id}/missing-words lists all Priority 1 (ods) words before Priority 2 (ddo) words.
    - Excludes Priority 3 words from the scrape queue.
    """
    client = TestClient(app)
    build_id = "test_queue_prio_ordering"

    mixed_items = [
        {"word": "historiskord1", "target": "ods", "priority": 1},
        {"word": "prioritetslån", "target": "ddo", "priority": 2},
        {"word": "historiskord2", "target": "ods", "priority": 1},
        {"word": "nybegynder", "target": "ddo", "priority": 2},
        {"word": "stakkels", "target": "ddo", "priority": 3},  # Must be excluded!
    ]

    try:
        coordinator.register_build(
            build_id=build_id,
            missing_words=[it["word"] for it in mixed_items],
            initial_mode="bookmarklet",
            items=mixed_items,
        )

        resp = client.get(f"/api/build/{build_id}/missing-words")
        assert resp.status_code == 200
        data = resp.json()

        words = data["words"]
        items = data["items"]

        # Priority 3 word ('stakkels') must be excluded
        assert "stakkels" not in words
        assert not any(it["word"] == "stakkels" for it in items)

        # All Priority 1 items must come before Priority 2 items
        p1_words = [it["word"] for it in items if it["priority"] == 1]
        p2_words = [it["word"] for it in items if it["priority"] == 2]

        assert set(p1_words) == {"historiskord1", "historiskord2"}
        assert set(p2_words) == {"prioritetslån", "nybegynder"}

        # Verify exact sequence: P1 items first, then P2
        expected_sequence = p1_words + p2_words
        assert words == expected_sequence

        for it in items[:len(p1_words)]:
            assert it["priority"] == 1
            assert it["target"] == "ods"

        for it in items[len(p1_words):]:
            assert it["priority"] == 2
            assert it["target"] == "ddo"

    finally:
        coordinator.unregister_build(build_id)


def test_stakkels_grundede_taellelys_dernedefra_uefterrettelig(tmp_path: Path):
    """Verify compiled StarDict dictionary lexical integrity:
    1. 'stakkels' has no fake synthetic inflections ('stakkelsere', 'stakkelsest') and no '...'.
    2. 'grundede' resolves strictly to 'grunde' (vb., thinking/pondering), never 'grund' (sb.).
    3. 'tællelyset' resolves to 'tællelys' (sb. itk.) with full definition about dyrefedt/talg.
    4. 'dernedefra' has POS 'adv.' and a clear directional definition.
    5. 'uefterrettelige' resolves to 'uefterrettelig' with a real definition.
    6. 'alderstegen' has POS 'adj.' with proper spacing and zero 'SITATER' noise.
    7. Clean typography: no quote contains '|', no HTML contains '▸', and no hyphenated stems ('grund-', 'søskende-').
    """
    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)

    with conn:
        # COR-SEM definitions
        conn.executemany(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            [
                ("COR.SEM.71618.01", "COR.71618", "stakkels", "adj."),
                ("COR.SEM.1002897.01", "COR.43648", "grund", "sb."),
                ("COR.SEM.1002898.01", "COR.31633", "grunde", "vb."),
            ],
        )
        conn.executemany(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            [
                ("COR.SEM.71618.01", "som vækker medlidenhed pga. sin svaghed, hjælpeløshed…", "de stakkels børn"),
                ("COR.SEM.1002897.01", "landjorden som et underlag man kan stå fast…", "90 pct. af alle drukneulykker sker mindre end 10 meter fra fast grund"),
                ("COR.SEM.1002898.01", "tænke nøje over", "[hun] tog et års arbejde på et posthus, mens hun grundede over fremtiden"),
            ],
        )
        conn.executemany(
            "INSERT INTO cor_forms (lemma_id, lemma, gloss, gram, pos, fullform, norm_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("COR.71618", "stakkels", "", "adj.", "adj.", "stakkels", ""),
                ("COR.71618", "stakkels", "", "adj.", "adj.", "stakkelsere", "U"),
                ("COR.71618", "stakkels", "", "adj.", "adj.", "stakkelsest", "U"),
                ("COR.43648", "grund", "", "sb.", "sb.", "grund", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grunden", ""),
                ("COR.43648", "grund", "", "sb.", "sb.", "grund-", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grunde", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grundede", ""),
                ("COR.31633", "grunde", "", "vb.", "vb.", "grundet", ""),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)
    output_dir = tmp_path / "output_lexical_integrity"
    pipeline = Pipeline(db=db, output_dir=output_dir, bookname="Lexical Integrity Benchmark")

    result = pipeline.run(
        custom_words=[
            "stakkels",
            "grundede",
            "grund",
            "tællelyset",
            "dernedefra",
            "uefterrettelige",
            "alderstegen",
        ],
        include_seeds=True,
        full_dictionary=True,
        allow_network=False,
    )

    assert result.files["ifo"].exists()
    reader = StarDictReader(result.files["ifo"])

    # 1. 'stakkels': no fake comparatives, complete definition, no '...'
    html_stakkels = reader.lookup("stakkels")
    assert html_stakkels is not None, "Lookup for 'stakkels' failed!"
    assert "stakkelsere" not in html_stakkels
    assert "stakkelsest" not in html_stakkels
    assert "..." not in html_stakkels
    assert "…" not in html_stakkels
    assert any(term in html_stakkels.lower() for term in ["medlidenhed", "hjælpeløshed", "svaghed"])
    # Fake forms with norm_status='U' must not exist in .syn either
    assert reader.lookup("stakkelsere") is None
    assert reader.lookup("stakkelsest") is None

    # 2. 'grundede' resolves to 'grunde' (vb., thinking/pondering), never 'grund' (sb.)
    html_grundede = reader.lookup("grundede")
    assert html_grundede is not None, "Lookup for 'grundede' failed!"
    assert "<b>grunde</b>" in html_grundede
    assert "<i>vb.</i>" in html_grundede
    assert any(term in html_grundede.lower() for term in ["tænke", "gruble", "overveje"])
    assert "<i>sb.</i>" not in html_grundede

    # 'grund' noun entry must not contain verbal inflections
    html_grund = reader.lookup("grund")
    assert html_grund is not None, "Lookup for 'grund' failed!"
    assert "<b>grund</b>" in html_grund
    assert "<i>sb." in html_grund
    assert "grundede" not in html_grund
    assert "grunder" not in html_grund

    # 3. 'tællelyset' resolves to 'tællelys' (sb. itk.) with genuine definition
    html_taellelyset = reader.lookup("tællelyset")
    assert html_taellelyset is not None, "Lookup for 'tællelyset' failed!"
    assert "<b>tællelys</b>" in html_taellelyset
    assert "sb. itk." in html_taellelyset
    assert any(term in html_taellelyset.lower() for term in ["dyrefedt", "tælle", "talg"])

    # 4. 'dernedefra' has POS 'adv.' and clear directional definition
    html_dernedefra = reader.lookup("dernedefra")
    assert html_dernedefra is not None, "Lookup for 'dernedefra' failed!"
    assert "<b>dernedefra</b>" in html_dernedefra
    assert "<i>adv.</i>" in html_dernedefra
    assert any(term in html_dernedefra.lower() for term in ["længere nede", "nedefra", "udgangspunkt"])

    # 5. 'uefterrettelige' resolves to 'uefterrettelig' with real definition
    html_uefterrettelig = reader.lookup("uefterrettelige")
    assert html_uefterrettelig is not None, "Lookup for 'uefterrettelige' failed!"
    assert "<b>uefterrettelig</b>" in html_uefterrettelig
    assert "<i>adj.</i>" in html_uefterrettelig
    assert any(term in html_uefterrettelig.lower() for term in ["rette sig efter", "upålidelig", "ulydig"])

    # 6. 'alderstegen' has POS 'adj.', proper spacing, and zero 'SITATER' noise
    html_alderstegen = reader.lookup("alderstegen")
    assert html_alderstegen is not None, "Lookup for 'alderstegen' failed!"
    assert "<b>alderstegen</b>" in html_alderstegen
    assert "<i>adj.</i>" in html_alderstegen
    assert "alderstegenadjektiv" not in html_alderstegen
    assert "SITATER" not in html_alderstegen
    assert any(term in html_alderstegen.lower() for term in ["alder", "ældgammel", "højt oppe i årene"])

    # 7. Global typography & syntax integrity checks
    test_words = [
        "stakkels", "grunde", "grund", "tællelys", "dernedefra", "uefterrettelig", "alderstegen"
    ]
    for w in test_words:
        h = reader.lookup(w)
        if h:
            assert "▸" not in h, f"CrossPoint-incompatible '▸' character found in '{w}'!"
            assert " |" not in h and "| " not in h, f"Un-split '|' citation delimiter found in '{w}'!"
            assert "grund-" not in h, f"Hyphenated stem 'grund-' leaked into '{w}'!"
            assert "søskende-" not in h, f"Hyphenated stem 'søskende-' leaked into '{w}'!"
