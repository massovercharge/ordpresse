"""Tests for AGENT_SPEC_V6_INDEX_HYGIENE: Headword Sanitization, .syn Deduplication & ESP32 Optimization."""

from pathlib import Path
import pytest

from ordpresse.orthography import sanitize_headword
from ordpresse.stardict_reader import StarDictReader
from ordpresse.stardict_writer import StarDictEntry, StarDictWriter


def test_sanitize_headword_rules():
    """Verify all 5 headword sanitization rules from AGENT_SPEC_V6 Task 1."""
    # 1. Strip Leading Roman Numerals & Sense Markers
    assert sanitize_headword("iii. kvarre")[0] == "kvarre"
    assert sanitize_headword("iii. skøtte")[0] == "skøtte"
    assert sanitize_headword("i. taag")[0] == "taag"
    clean_hw, syns = sanitize_headword("i. slave-lænke")
    assert clean_hw == "slavelænke"
    assert "slave-lænke" in syns

    # Discard standalone "i." or roman numerals
    assert sanitize_headword("i.") == ("", set())
    assert sanitize_headword("iii.") == ("", set())

    # Ordinal exceptions
    assert sanitize_headword("1. g'er")[0] == "1. g'er"
    assert sanitize_headword("3. g'er")[0] == "3. g'er"

    # 2. Split Comma-Separated & "i." Variant Headwords
    hw_dvael, syns_dvael = sanitize_headword("dvæl, i. dvæle")
    assert hw_dvael == "dvæle"
    assert "dvæl" in syns_dvael

    hw_ingen, syns_ingen = sanitize_headword("ingensteds, intetsteds")
    assert hw_ingen == "ingensteds"
    assert "intetsteds" in syns_ingen

    hw_kerne, syns_kerne = sanitize_headword("kernesund, kærnesund")
    assert hw_kerne == "kernesund"
    assert "kærnesund" in syns_kerne

    # 3. Expand Parenthetical Optional Letters
    hw_fyr, syns_fyr = sanitize_headword("fyrstik(ke)")
    assert hw_fyr == "fyrstikke"
    assert "fyrstik" in syns_fyr

    hw_kbh, syns_kbh = sanitize_headword("k(j)øbenhavn")
    assert hw_kbh == "københavn"
    assert "kjøbenhavn" in syns_kbh

    hw_loft, syns_loft = sanitize_headword("loft(s)rum")
    assert hw_loft == "loftsrum"
    assert "loftrum" in syns_loft

    # 4. Strip ® Symbol
    assert sanitize_headword("eternit®")[0] == "eternit"
    assert sanitize_headword("a38®")[0] == "a38"
    assert sanitize_headword("jeep®")[0] == "jeep"

    # 5. De-Hyphenate ODS Etymological Headwords
    hw_aar, syns_aar = sanitize_headword("aarvaagen-hed")
    assert hw_aar == "aarvaagenhed"
    assert "aarvaagen-hed" in syns_aar

    hw_enfold, syns_enfold = sanitize_headword("enfoldig-hed")
    assert hw_enfold == "enfoldighed"
    assert "enfoldig-hed" in syns_enfold

    hw_efter, syns_efter = sanitize_headword("efter-tragtelses-værdig")
    assert hw_efter == "eftertragtelsesværdig"
    assert "efter-tragtelses-værdig" in syns_efter

    # Preserve modern compounds
    assert sanitize_headword("a-aktie")[0] == "a-aktie"
    assert sanitize_headword("e-mail")[0] == "e-mail"
    assert sanitize_headword("tv-program")[0] == "tv-program"
    assert sanitize_headword("65-billet")[0] == "65-billet"
    assert sanitize_headword("køkken-alrum")[0] == "køkken-alrum"


def test_compiled_syn_and_idx_hygiene(tmp_path: Path):
    """Verify that compiled .syn and .idx strictly conform to AGENT_SPEC_V6:
    - .syn does NOT contain ASCII capitalized duplicates ('Abbeden')
    - .syn DOES contain non-ASCII capitalized forms ('Åbningen' / 'Æblet')
    - .syn does NOT contain 'agtsommere', 'arbejdsommere', 'aabnere', or 'abbedernes'
    - Dirty headwords ('iii. kvarre', 'dvæl, i. dvæle', 'fyrstik(ke)', 'eternit®') are sanitized in .idx
    """
    writer = StarDictWriter(bookname="Hygiene Test")

    entries = [
        StarDictEntry(
            headword="abbed",
            html_content="<ol><li>Forstander for et kloster.</li></ol>",
            synonyms=["abbeden", "Abbeden", "abbederne", "abbedernes", "abbedens"],
        ),
        StarDictEntry(
            headword="åbning",
            html_content="<ol><li>Hul eller passage.</li></ol>",
            synonyms=["åbningen", "Åbningen", "aabning"],
        ),
        StarDictEntry(
            headword="æble",
            html_content="<ol><li>Frugt fra æbletræet.</li></ol>",
            synonyms=["æblet", "Æblet", "æbler", "æblerne", "æblernes"],
        ),
        StarDictEntry(
            headword="agtsom",
            html_content="<ol><li>Opmærksom og forsigtig.</li></ol>",
            synonyms=["agtsomt", "agtsomme", "agtsommere", "agtsomst", "agtsomste"],
        ),
        StarDictEntry(
            headword="arbejdsom",
            html_content="<ol><li>Flittig med sit arbejde.</li></ol>",
            synonyms=["arbejdsomt", "arbejdsomme", "arbejdsommere", "arbejdsomst"],
        ),
        StarDictEntry(
            headword="aabner",
            html_content="<ol><li>Redskab til at åbne med.</li></ol>",
            synonyms=["aabneren", "aabnere", "åbnere"],
        ),
        StarDictEntry(
            headword="iii. kvarre",
            html_content="<ol><li>Historisk udtryk.</li></ol>",
            synonyms=[],
        ),
        StarDictEntry(
            headword="dvæl, i. dvæle",
            html_content="<ol><li>Tøve eller opholde sig.</li></ol>",
            synonyms=[],
        ),
        StarDictEntry(
            headword="fyrstik(ke)",
            html_content="<ol><li>Tændstik.</li></ol>",
            synonyms=[],
        ),
        StarDictEntry(
            headword="eternit®",
            html_content="<ol><li>Byggemateriale.</li></ol>",
            synonyms=[],
        ),
        StarDictEntry(
            headword="aarvaagen-hed",
            html_content="<ol><li>Årvågenhed.</li></ol>",
            synonyms=[],
        ),
        StarDictEntry(
            headword="i.",
            html_content="<ol><li>Ugyldigt opslag.</li></ol>",
            synonyms=[],
        ),
    ]

    output_dir = tmp_path / "hygiene_out"
    compiled = writer.compile(entries, output_dir=output_dir, compress_dict=False)
    reader = StarDictReader(compiled["ifo"])

    # 1. Verify .idx headwords are 100% clean
    idx_headwords = {item[0] for item in reader.idx_entries}
    assert "kvarre" in idx_headwords
    assert "iii. kvarre" not in idx_headwords
    assert "dvæle" in idx_headwords
    assert "dvæl, i. dvæle" not in idx_headwords
    assert "fyrstikke" in idx_headwords
    assert "fyrstik(ke)" not in idx_headwords
    assert "eternit" in idx_headwords
    assert "eternit®" not in idx_headwords
    assert "aarvaagenhed" in idx_headwords
    assert "aarvaagen-hed" not in idx_headwords
    assert "i." not in idx_headwords

    # 2. Verify .syn contents
    syn_words = {s[0] for s in reader.syn_entries}

    # ASCII title-case duplicate MUST NOT be present
    assert "Abbeden" not in syn_words
    assert "abbeden" in syn_words

    # Multibyte UTF-8 non-ASCII capitalized forms MUST be present
    assert "Åbningen" in syn_words
    assert "åbningen" in syn_words
    assert "Æblet" in syn_words
    assert "æblet" in syn_words

    # Regular possessive genitives MUST NOT be present
    assert "abbedernes" not in syn_words
    assert "abbedens" not in syn_words
    assert "æblernes" not in syn_words

    # Synthetic comparative/superlative forms MUST NOT be present
    assert "agtsommere" not in syn_words
    assert "agtsomst" not in syn_words
    assert "agtsomste" not in syn_words
    assert "arbejdsommere" not in syn_words
    assert "arbejdsomst" not in syn_words
    assert "aabnere" not in syn_words
    assert "åbnere" not in syn_words

    # Extra synonyms generated during sanitization MUST be present
    assert "dvæl" in syn_words
    assert "fyrstik" in syn_words
    assert "aarvaagen-hed" in syn_words

    # 3. Verify reader lookups
    # Looking up "Abbeden" resolves because KOReader / StarDict folds ASCII case
    assert reader.lookup("Abbeden") is not None
    assert reader.lookup("kvarre") is not None
    assert reader.lookup("dvæl") is not None
    assert reader.lookup("fyrstik") is not None
    assert reader.lookup("aarvaagen-hed") is not None
