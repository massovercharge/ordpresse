"""Tests for AGENT_SPEC_V8: Full aa Orthography Coverage & Genitive Pruning in .syn."""

from pathlib import Path
import pytest

from ordpresse.orthography import (
    GENITIVE_WHITELIST,
    generate_orthographic_synonyms,
    is_artificial_ods_synonym,
    is_redundant_genitive,
)
from ordpresse.stardict_reader import StarDictReader
from ordpresse.stardict_writer import StarDictEntry, StarDictWriter


def test_is_redundant_genitive_unit():
    """Verify is_redundant_genitive rules individually."""
    # 1. Exact match to headword or in GENITIVE_WHITELIST must NOT be pruned
    assert not is_redundant_genitive("univers", "univers")
    assert not is_redundant_genitive("års", "år")
    assert not is_redundant_genitive("aars", "år")
    assert not is_redundant_genitive("dags", "dag")
    assert not is_redundant_genitive("fredags", "fredag")

    # 2. Endings in ("ens", "enes", "ernes", "ets", "ers", "'s", "s'") pruned
    assert is_redundant_genitive("abbedens", "abbed")
    assert is_redundant_genitive("abbedernes", "abbed")
    assert is_redundant_genitive("abbeders", "abbed")
    assert is_redundant_genitive("abehusenes", "abehus")
    assert is_redundant_genitive("abehusets", "abehus")
    assert is_redundant_genitive("abehus's", "abehus")
    assert is_redundant_genitive("abehus'", "abehus")
    assert is_redundant_genitive("universets", "univers")

    # But if headword stem itself ends with it, keep it
    assert not is_redundant_genitive("spets", "spets")

    # 3. Headword + s singular genitive pruned for nouns
    assert is_redundant_genitive("abbeds", "abbed", pos="sb.")
    assert is_redundant_genitive("bils", "bil", pos="sb.")
    assert is_redundant_genitive("gårds", "gård", pos="sb.")
    assert is_redundant_genitive("gaards", "gård", pos="sb.")

    # Passive verb forms with +s kept when pos is verb
    assert not is_redundant_genitive("spises", "spise", pos="vb.")
    assert not is_redundant_genitive("høres", "høre", pos="vb.")

    # Legitimate non-genitive inflections kept
    assert not is_redundant_genitive("gårde", "gård")
    assert not is_redundant_genitive("gården", "gård")
    assert not is_redundant_genitive("gårdene", "gård")
    assert not is_redundant_genitive("abbeder", "abbed")
    assert not is_redundant_genitive("abbederne", "abbed")


def test_gaard_compilation_and_genitives(tmp_path: Path):
    """Task 3.1: Verify compiling entry for 'gård' produces correct aa variants and prunes genitives."""
    writer = StarDictWriter(bookname="Gaard Test")

    entry = StarDictEntry(
        headword="gård",
        html_content="<ol><li>Landbrugsejendom.</li></ol>",
        synonyms=["gårde", "gården", "gårdene", "gårds", "gårdenes"],
    )

    compiled = writer.compile([entry], output_dir=tmp_path / "gaard_dict", compress_dict=False)
    reader = StarDictReader(compiled["ifo"])

    syn_words = {s[0] for s in reader.syn_entries}

    # Verify expected .syn keys: gårde, gården, gårdene, gaard, gaarde, gaarden, gaardene
    expected_syns = {"gårde", "gården", "gårdene", "gaard", "gaarde", "gaarden", "gaardene"}
    for exp in expected_syns:
        assert exp in syn_words, f"Expected '{exp}' in .syn"

    # Verify pruned: gårds, gårdenes, gaards, gaardenes
    assert "gårds" not in syn_words
    assert "gårdenes" not in syn_words
    assert "gaards" not in syn_words
    assert "gaardenes" not in syn_words


def test_classic_1800s_aa_words_coverage(tmp_path: Path):
    """Task 3.1: Verify baad, daab, haab, maane, taage, saar, laas resolve to modern entries."""
    writer = StarDictWriter(bookname="1800s Aa Test")

    test_words = [
        ("båd", "<ol><li>Fartøj.</li></ol>", ["båden", "både"]),
        ("dåb", "<ol><li>Indvielse.</li></ol>", ["dåben"]),
        ("håb", "<ol><li>Forventning.</li></ol>", ["håbet", "håber", "håbede"]),
        ("måne", "<ol><li>Himmelligeme.</li></ol>", ["månen", "måner"]),
        ("tåge", "<ol><li>Vanddamp.</li></ol>", ["tågen"]),
        ("sår", "<ol><li>Læsion.</li></ol>", ["såret", "sårene"]),
        ("lås", "<ol><li>Lukkeindretning.</li></ol>", ["låsen", "låse"]),
    ]

    entries = [
        StarDictEntry(headword=hw, html_content=html, synonyms=syns)
        for hw, html, syns in test_words
    ]

    compiled = writer.compile(entries, output_dir=tmp_path / "aa_test_dict", compress_dict=False)
    reader = StarDictReader(compiled["ifo"])

    lookups = [
        ("baad", "båd"),
        ("daab", "dåb"),
        ("haab", "håb"),
        ("maane", "måne"),
        ("taage", "tåge"),
        ("saar", "sår"),
        ("laas", "lås"),
    ]

    for aa_word, modern_hw in lookups:
        res = reader.lookup(aa_word)
        assert res is not None, f"Lookup for '{aa_word}' failed (should resolve to '{modern_hw}')"

    # Also verify inflections with aa:
    assert reader.lookup("baaden") is not None
    assert reader.lookup("baade") is not None
    assert reader.lookup("daaben") is not None
    assert reader.lookup("haabet") is not None
    assert reader.lookup("haabede") is not None
    assert reader.lookup("maanen") is not None
    assert reader.lookup("taagen") is not None
    assert reader.lookup("saaret") is not None
    assert reader.lookup("laasen") is not None


def test_abbreviations_and_genitives_pruned(tmp_path: Path):
    """Task 3.1: Verify abbedens, abbedernes, adm., and afd. are NOT in .syn."""
    writer = StarDictWriter(bookname="Abbr Test")

    entry = StarDictEntry(
        headword="abbed",
        html_content="<ol><li>Klosterforstander.</li></ol>",
        synonyms=["abbeden", "abbedens", "abbederne", "abbedernes", "adm.", "afd.", "aarh"],
    )

    compiled = writer.compile([entry], output_dir=tmp_path / "abbr_test_dict", compress_dict=False)
    reader = StarDictReader(compiled["ifo"])

    syn_words = {s[0] for s in reader.syn_entries}

    assert "abbedens" not in syn_words
    assert "abbedernes" not in syn_words
    assert "adm." not in syn_words
    assert "afd." not in syn_words
    assert "aarh" not in syn_words

    # Kept forms
    assert "abbeden" in syn_words
    assert "abbederne" in syn_words


def test_is_artificial_ods_synonym_unit():
    """Task 2.1: Verify is_artificial_ods_synonym rules individually."""
    # 1. Parenthesis Rule: forms with '(' or ')' return True
    assert is_artificial_ods_synonym("bark(e)brød", "barkebrød")
    assert is_artificial_ods_synonym("bark-(e-)brød", "barkebrød")
    assert is_artificial_ods_synonym("be(j)an", "bejan")
    assert is_artificial_ods_synonym("befje(d)re", "befjedre")

    # 2. Artificial Morpheme Hyphen Rule:
    assert is_artificial_ods_synonym("bag-ben", "bagben")
    assert is_artificial_ods_synonym("aar-gammel", "aargammel")
    assert is_artificial_ods_synonym("aar-gammel", "årgammel")
    assert is_artificial_ods_synonym("aarvaagen-hed", "aarvaagenhed")
    assert is_artificial_ods_synonym("døds-angst", "dødsangst")
    assert is_artificial_ods_synonym("efter-abe", "efterabe")
    assert is_artificial_ods_synonym("bark-brød", "barkebrød")
    assert is_artificial_ods_synonym("bark-e-brød", "barkebrød")
    assert is_artificial_ods_synonym("avind-s-mand", "avindsmand")
    assert is_artificial_ods_synonym("diske--springer", "diskenspringer")

    # 3. Legitimate forms must NOT be pruned (return False)
    assert not is_artificial_ods_synonym("a-indkomsten", "a-indkomst")
    assert not is_artificial_ods_synonym("a-38", "a38")
    assert not is_artificial_ods_synonym("co₂-afgift", "co₂afgift")
    assert not is_artificial_ods_synonym("drive-in-biograf", "driveinbiograf")
    assert not is_artificial_ods_synonym("baad", "båd")
    assert not is_artificial_ods_synonym("baaden", "båd")
    assert not is_artificial_ods_synonym("a conto-beløb", "acontobeløb")
    assert not is_artificial_ods_synonym("clara frijs-pære", "clarafrijspære")
    assert not is_artificial_ods_synonym("blonde-bh'en", "blondebh")


def test_artificial_ods_synonyms_compilation(tmp_path: Path):
    """Task 2.1: Verify compilation filters artificial ODS hyphens and parens, while preserving legitimate entries."""
    writer = StarDictWriter(bookname="ODS Syn Cleanup Test")

    entries = [
        StarDictEntry(
            headword="barkebrød",
            html_content="<ol><li>Brød af bark.</li></ol>",
            synonyms=["bark(e)brød", "bark-(e-)brød", "bark-brød", "bark-e-brød", "barkebrødet"],
        ),
        StarDictEntry(
            headword="bagben",
            html_content="<ol><li>Bagerste ben.</li></ol>",
            synonyms=["bag-ben", "bagbenet"],
        ),
        StarDictEntry(
            headword="bejan",
            html_content="<ol><li>Grønskolling.</li></ol>",
            synonyms=["be(j)an", "bejanen"],
        ),
        StarDictEntry(
            headword="befjedre",
            html_content="<ol><li>Forsyne med fjer.</li></ol>",
            synonyms=["befje(d)re", "befjedrede"],
        ),
        StarDictEntry(
            headword="dødsangst",
            html_content="<ol><li>Angst for døden.</li></ol>",
            synonyms=["døds-angst", "dødsangsten"],
        ),
        StarDictEntry(
            headword="efterabe",
            html_content="<ol><li>Efterligne.</li></ol>",
            synonyms=["efter-abe", "efteraber"],
        ),
        StarDictEntry(
            headword="a-indkomst",
            html_content="<ol><li>Indkomst A.</li></ol>",
            synonyms=["a-indkomsten"],
        ),
        StarDictEntry(
            headword="a38",
            html_content="<ol><li>Syrnet mælkeprodukt.</li></ol>",
            synonyms=["a-38"],
        ),
        StarDictEntry(
            headword="co₂afgift",
            html_content="<ol><li>Klimaafgift.</li></ol>",
            synonyms=["co₂-afgift"],
        ),
        StarDictEntry(
            headword="driveinbiograf",
            html_content="<ol><li>Biograf for biler.</li></ol>",
            synonyms=["drive-in-biograf"],
        ),
        StarDictEntry(
            headword="båd",
            html_content="<ol><li>Fartøj.</li></ol>",
            synonyms=["båden"],
        ),
    ]

    compiled = writer.compile(entries, output_dir=tmp_path / "ods_cleanup_dict", compress_dict=False)
    reader = StarDictReader(compiled["ifo"])

    syn_words = {s[0] for s in reader.syn_entries}

    # Artificial forms must NOT be emitted to .syn:
    assert "bag-ben" not in syn_words
    assert "aar-gammel" not in syn_words
    assert "bark(e)brød" not in syn_words
    assert "bark-(e-)brød" not in syn_words
    assert "bark-brød" not in syn_words
    assert "bark-e-brød" not in syn_words
    assert "be(j)an" not in syn_words
    assert "befje(d)re" not in syn_words
    assert "døds-angst" not in syn_words
    assert "efter-abe" not in syn_words

    # Legitimate forms ARE emitted to .syn:
    assert "a-indkomsten" in syn_words
    assert "a-38" in syn_words
    assert "co₂-afgift" in syn_words
    assert "drive-in-biograf" in syn_words
    assert "baad" in syn_words
    assert "baaden" in syn_words

    # Lookups resolve successfully
    assert reader.lookup("a-38") is not None
    assert reader.lookup("co₂-afgift") is not None
    assert reader.lookup("drive-in-biograf") is not None
    assert reader.lookup("baad") is not None
    assert reader.lookup("baaden") is not None
    assert reader.lookup("barkebrødet") is not None

