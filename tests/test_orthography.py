"""Tests for soft-hyphen sanitization, aa<->å converters, and suffix resolvers."""

import pytest

from ordpresse.orthography import (
    extract_word_tokens,
    generate_orthographic_synonyms,
    historical_aa_to_modern,
    modern_to_historical_aa,
    resolve_danish_stem_candidates,
    sanitize_text,
)


def test_soft_hyphen_and_line_break_sanitization():
    """Verify that soft-hyphens, zero-width chars, and line breaks are sanitized."""
    raw = "De gamle lægds\u00adlemmer gik om\u200bkring i sog\ufeffnet. En bisse-\n  kræmmer kom forbi."
    clean = sanitize_text(raw)

    assert "lægdslemmer" in clean
    assert "\u00ad" not in clean
    assert "\u200b" not in clean
    assert "\ufeff" not in clean
    assert "bissekræmmer" in clean

    tokens = extract_word_tokens(raw)
    assert "lægdslemmer" in tokens
    assert "bissekræmmer" in tokens


def test_aa_and_aae_conversions():
    """Verify bidirectional pre-1948 aa <-> modern å conversions."""
    # modern å -> historical aa
    hist = modern_to_historical_aa("gårdmand")
    assert "gaardmand" in hist
    assert "Gaardmand" in hist

    hist_aar = modern_to_historical_aa("år")
    assert "aar" in hist_aar

    # historical aa -> modern å
    assert historical_aa_to_modern("Aar") == "År"
    assert historical_aa_to_modern("aar") == "år"
    assert historical_aa_to_modern("gaardmand") == "gårdmand"


def test_suffix_and_stem_resolution():
    """Verify suffix resolution for historical Danish forms."""
    stems_vaerkbrudne = resolve_danish_stem_candidates("værkbrudne")
    assert "værkbrudden" in stems_vaerkbrudne

    stems_laegdslemmer = resolve_danish_stem_candidates("lægdslemmer")
    assert "lægdslem" in stems_laegdslemmer

    stems_jordeboeger = resolve_danish_stem_candidates("jordebøger")
    assert "jordebog" in stems_jordeboeger

    stems_banco = resolve_danish_stem_candidates("banco")
    assert "banko" in stems_banco or "banco" in stems_banco


def test_orthographic_synonyms_generation():
    """Verify generated synonyms for StarDict .syn entries."""
    syns = generate_orthographic_synonyms("Jordebog", ["jordebøger"])
    assert "jordebog" in syns
    assert "jordebøger" in syns
    assert "Jordebog" not in syns  # Headword itself must not be in its own syn list
