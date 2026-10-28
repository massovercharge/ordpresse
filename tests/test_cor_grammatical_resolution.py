"""Unit and regression tests verifying COR grammatical and modern word resolution."""

from pathlib import Path
import sqlite3
import pytest

from ordpresse.db import Database, init_datasets_db
from ordpresse.pipeline import Pipeline
from ordpresse.stardict_reader import StarDictReader


def test_cor_forms_batch_lookup_with_aa_mapping(tmp_path: Path):
    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)

    with conn:
        conn.executemany(
            "INSERT INTO cor_forms (lemma_id, lemma, pos, fullform) VALUES (?, ?, ?, ?)",
            [
                ("COR.00947", "til", "præp.", "til"),
                ("COR.00831", "en", "art.", "en"),
                ("COR.01276", "vi", "pron.", "vi"),
                ("COR.01276", "vi", "pron.", "vores"),
                ("COR.00948", "på", "præp.", "på"),
                ("COR.71558", "mandfolk", "sb.", "mandfolk"),
                ("COR.42076", "moder", "sb.", "moder"),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)
    res = db.lookup_cor_forms_batch(["til", "vores", "paa", "mandfolk", "ukendtord"])

    assert "til" in res
    assert res["til"][0]["lemma"] == "til"
    assert res["til"][0]["pos"] == "præp."

    assert "vores" in res
    assert res["vores"][0]["lemma"] == "vi"

    # Pre-1948 'paa' automatically resolves via 'på'
    assert "paa" in res
    assert res["paa"][0]["lemma"] == "på"

    assert "mandfolk" in res
    assert res["mandfolk"][0]["lemma"] == "mandfolk"

    # Non-existent word
    assert "ukendtord" not in res


def test_pipeline_resolves_grammatical_and_modern_words_locally(tmp_path: Path):
    db_path = tmp_path / "datasets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    init_datasets_db(conn)

    with conn:
        # 1. Modern definition for 'mor'
        conn.execute(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            ("COR.SEM.42076.01", "COR.42076", "mor", "sb."),
        )
        conn.execute(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            ("COR.SEM.42076.01", "Kvindelig forælder", "min kære mor"),
        )
        # 2. Modern definition for 'mandfolk'
        conn.execute(
            "INSERT INTO cor_sem (sense_id, lemma_id, lemma, pos) VALUES (?, ?, ?, ?)",
            ("COR.SEM.71558.01", "COR.71558", "mandfolk", "sb."),
        )
        conn.execute(
            "INSERT INTO cor_definitions (sense_id, definition, examples) VALUES (?, ?, ?)",
            ("COR.SEM.71558.01", "Voksen mandsperson eller modig mand", "et ægte mandfolk"),
        )
        # Forms
        conn.executemany(
            "INSERT INTO cor_forms (lemma_id, lemma, pos, fullform) VALUES (?, ?, ?, ?)",
            [
                ("COR.42076", "moder", "sb.", "mor"),
                ("COR.42076", "moder", "sb.", "moder"),
                ("COR.01276", "vi", "pron.", "vi"),
                ("COR.01276", "vi", "pron.", "vores"),
                ("COR.71558", "mandfolk", "sb.", "mandfolk"),
                ("COR.99999", "flink", "adj.", "flink"),
            ],
        )
    conn.close()

    db = Database(datasets_path=db_path)
    output_dir = tmp_path / "output"
    pipeline = Pipeline(db=db, output_dir=output_dir, bookname="Test COR Local Resolution")

    # Run with custom words including function words, moder, and modern adjectives
    result = pipeline.run(
        custom_words=["vores", "moder", "mandfolk", "flink"],
        include_seeds=True,
        allow_network=False,
    )

    reader = StarDictReader(result.files["ifo"])

    # 1. 'moder' attaches to 'mor' via lemma_id COR.42076
    def_mor = reader.lookup("mor")
    assert def_mor is not None
    assert "Kvindelig forælder" in def_mor

    def_moder = reader.lookup("moder")
    assert def_moder is not None
    assert "Kvindelig forælder" in def_moder

    # 2. 'vores' attaches to 'vi'
    def_vores = reader.lookup("vores")
    # If 'vi' had no definition in cor_sem, it is not emitted as empty skeleton
    
    # 3. 'mandfolk' resolved with real definition
    def_mandfolk = reader.lookup("mandfolk")
    assert def_mandfolk is not None
    assert "Voksen mandsperson" in def_mandfolk

    # 4. 'flink' only in cor_forms (0 definitions in cor_sem) must NEVER be emitted as empty skeleton
    def_flink = reader.lookup("flink")
    assert def_flink is None, "Empty skeleton article for 'flink' without definition must be dropped!"
