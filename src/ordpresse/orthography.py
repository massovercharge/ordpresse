"""Pre-1948 Danish orthography normalizer, aa<->å converters, and suffix resolvers."""

import re
from typing import List, Set


def sanitize_text(text: str) -> str:
    """Strip soft hyphens, zero-width spaces, shy entities and rejoin hyphenated line breaks."""
    # Strip soft hyphens and zero-width artifacts
    text = (
        text.replace("\u00ad", "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("&shy;", "")
    )
    # Rejoin words broken across linebreaks by hyphen + whitespace
    text = re.sub(r"([a-zæøåA-ZÆØÅ])-\s+([a-zæøåA-ZÆØÅ])", r"\1\2", text)
    return text


def extract_word_tokens(text: str) -> List[str]:
    """Extract lowercased Danish word tokens (len >= 2) after sanitization."""
    clean = sanitize_text(text)
    # Match words possibly containing internal hyphens
    tokens = re.findall(r"[a-zæøåA-ZÆØÅ]+(?:-[a-zæøåA-ZÆØÅ]+)*", clean)
    return [t.lower() for t in tokens if len(t) >= 2]


def modern_to_historical_aa(word: str) -> List[str]:
    """Convert modern words containing 'å'/'Å' into pre-1948 'aa'/'Aa' variants."""
    if "å" not in word.lower():
        return []

    # Lowercase variant
    aa_lower = word.replace("å", "aa").replace("Å", "aa")
    # Titlecase / capitalized variant
    aa_cap = aa_lower.capitalize()
    # If first letter was capitalized
    if word and word[0].isupper():
        aa_lead = "Aa" + aa_lower[2:] if aa_lower.startswith("aa") else aa_lower.capitalize()
        variants = [aa_lower, aa_cap, aa_lead]
    else:
        variants = [aa_lower, aa_cap]

    # Deduplicate while preserving order
    seen = set()
    result = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def historical_aa_to_modern(word: str) -> str:
    """Convert pre-1948 'aa'/'Aa' forms to modern 'å'/'Å'."""
    # Replace Aa with Å at beginning
    res = re.sub(r"^Aa", "Å", word)
    res = re.sub(r"^AA", "Å", res)
    res = re.sub(r"^aa", "å", res)
    # Replace internal aa with å
    res = res.replace("aa", "å").replace("AA", "Å")
    return res


def generate_orthographic_synonyms(headword: str, existing_synonyms: List[str] = None) -> List[str]:
    """Generate all standard Danish orthographical variants for .syn file.
    
    Includes:
    - Lowercase version for capitalized nouns (e.g., 'Jordebog' -> 'jordebog')
    - Capitalized version for lowercase entries
    - Pre-1948 'aa' / 'Aa' variants if headword contains 'å'
    - Modern 'å' / 'Å' variants if headword contains 'aa'
    - De-hyphenated forms ('Lægds-lem' -> 'lægdslem')
    """
    synonyms: Set[str] = set()
    if existing_synonyms:
        synonyms.update(existing_synonyms)

    candidates = [headword] + list(synonyms)
    for w in candidates:
        if not w:
            continue
        # Case variants
        synonyms.add(w.lower())
        synonyms.add(w.capitalize())

        # Hyphen stripping
        if "-" in w:
            stripped = w.replace("-", "")
            synonyms.add(stripped)
            synonyms.add(stripped.lower())
            synonyms.add(stripped.capitalize())

        # aa <-> å mappings
        if "å" in w or "Å" in w:
            for aa_var in modern_to_historical_aa(w):
                synonyms.add(aa_var)
                synonyms.add(aa_var.lower())
                synonyms.add(aa_var.capitalize())
        if "aa" in w.lower():
            mod = historical_aa_to_modern(w)
            synonyms.add(mod)
            synonyms.add(mod.lower())
            synonyms.add(mod.capitalize())

    # Do not include the headword itself in the synonym list
    synonyms.discard(headword)
    return sorted(list(synonyms))


# Known irregular stems and Dano-Norwegian loan equivalents
HISTORICAL_STEM_MAP = {
    "lægdslemmer": "lægdslem",
    "lægdslemmerne": "lægdslem",
    "lægdslemmet": "lægdslem",
    "legdelemmer": "lægdslem",
    "legdelem": "lægdslem",
    "værkbrudne": "værkbrudden",
    "værkbruddent": "værkbrudden",
    "jordebøger": "jordebog",
    "jordebøgerne": "jordebog",
    "bissekræmmere": "bissekræmmer",
    "bissekræmmeren": "bissekræmmer",
    "bissekræmmerne": "bissekræmmer",
    "banko": "banco",
    "rigsdaler banco": "banco",
    "riksdaler banco": "banco",
}

# Danish suffix stripping patterns in descending order of specificity
SUFFIX_RULES = [
    # Specific compound plural/definites
    ("-lemmerne", "-lem"),
    ("-lemmer", "-lem"),
    ("-lemmet", "-lem"),
    ("-brudne", "-brudden"),
    ("-bøgerne", "-bog"),
    ("-bøger", "-bog"),
    # General Danish inflections
    ("erne", ""),
    ("erne", "e"),
    ("ene", ""),
    ("ene", "e"),
    ("ere", ""),
    ("ere", "er"),
    ("er", ""),
    ("en", ""),
    ("et", ""),
    ("ne", ""),
    ("ne", "n"),
    ("e", ""),
    ("s", ""),
]


def resolve_danish_stem_candidates(word: str) -> List[str]:
    """Generate candidate lemma stems for a Danish word token."""
    w = word.lower().strip()
    candidates: List[str] = [w]

    # 1. Direct irregular / historical mappings
    if w in HISTORICAL_STEM_MAP:
        candidates.append(HISTORICAL_STEM_MAP[w])

    # 2. Modern å to aa conversions
    if "å" in w:
        for aa_form in modern_to_historical_aa(w):
            candidates.append(aa_form.lower())
    if "aa" in w:
        candidates.append(historical_aa_to_modern(w).lower())

    # 3. Apply suffix stripping rules
    for suffix, replacement in SUFFIX_RULES:
        if suffix.startswith("-"):
            # Substring match at end
            stem_end = suffix[1:]
            if w.endswith(stem_end):
                base = w[: -len(stem_end)] + replacement[1:]
                if len(base) >= 2:
                    candidates.append(base)
        else:
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                base = w[: -len(suffix)] + replacement
                if len(base) >= 2:
                    candidates.append(base)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result
