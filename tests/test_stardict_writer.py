"""Tests for StarDict v2.4.2 writer, GLib sorting law, and KOReader binary search reader."""

from pathlib import Path
import pytest

from ordpresse.stardict_writer import (
    StarDictEntry,
    StarDictReader,
    StarDictWriter,
    stardict_sort_key,
)


def test_glib_ascii_strcasecmp_sorting_law():
    """Verify that stardict_sort_key strictly follows GLib g_ascii_strcasecmp rules."""
    words = ["Aabenraa", "Ålborg", "Ægir", "Ørsted", "ålborg", "ægir", "ørsted"]
    sorted_words = sorted(words, key=stardict_sort_key)

    # In UTF-8:
    # 'Æ' = 0xC3 0x86
    # 'Ø' = 0xC3 0x98
    # 'æ' = 0xC3 0xA6
    # 'ø' = 0xC3 0xB8
    # 'å' = 0xC3 0xA5
    # 'Å' = 0xC3 0x85
    # ASCII bytes fold: 'A' -> 'a'.
    # Bytes >= 0x80 remain untouched!
    # Therefore: 0xC3 0x86 (Ægir) < 0xC3 0x98 (Ørsted) < 0xC3 0xA6 (ægir)
    ægir_idx = sorted_words.index("Ægir")
    ørsted_idx = sorted_words.index("Ørsted")
    lower_ægir_idx = sorted_words.index("ægir")

    assert ægir_idx < ørsted_idx, f"Ægir ({ægir_idx}) must sort before Ørsted ({ørsted_idx})"
    assert ørsted_idx < lower_ægir_idx, f"Ørsted ({ørsted_idx}) must sort before ægir ({lower_ægir_idx})"


def test_stardict_compilation_and_binary_search_reader(tmp_path: Path):
    """Compile a sample dictionary and verify exact lookup using the binary reader."""
    writer = StarDictWriter(
        bookname="Test Ordbog",
        author="Tester",
        description="En test ordbog",
    )

    entries = [
        StarDictEntry(
            headword="Jordebog",
            html_content="<p>Fortegnelse over fast ejendom.</p>",
            synonyms=["jordebog", "jordebøger", "jordebogen"],
        ),
        StarDictEntry(
            headword="Lægdslem",
            html_content="<p>Fattiglem under ældre fattiglovgivning.</p>",
            synonyms=["lægdslem", "lægdslemmer", "lægdslemmet"],
        ),
        StarDictEntry(
            headword="Ægir",
            html_content="<p>Jætte og personificering af havet.</p>",
            synonyms=["ægir"],
        ),
    ]

    compiled = writer.compile(
        entries=entries,
        output_dir=tmp_path,
        base_filename="testdict",
        compress_dict=False,
    )

    assert compiled["ifo"].exists()
    assert compiled["idx"].exists()
    assert compiled["dict"].exists()
    assert compiled["syn"].exists()

    # Read using binary search StarDictReader
    reader = StarDictReader(compiled["ifo"])

    # Verify headwords in .idx
    def_jordebog = reader.lookup("Jordebog")
    assert def_jordebog is not None
    assert "Fortegnelse over fast ejendom" in def_jordebog

    def_aegir = reader.lookup("Ægir")
    assert def_aegir is not None
    assert "Jætte og personificering" in def_aegir

    # Verify inflections and synonyms in .syn
    def_syn_lemmer = reader.lookup("lægdslemmer")
    assert def_syn_lemmer is not None
    assert "Fattiglem under ældre" in def_syn_lemmer

    def_syn_boeger = reader.lookup("jordebøger")
    assert def_syn_boeger is not None
    assert "Fortegnelse over fast ejendom" in def_syn_boeger

    # Non-existent word returns None
    assert reader.lookup("ikkeeksisterende") is None
