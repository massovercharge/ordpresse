"""Pre-1948 Danish orthography normalizer, aa<->å converters, and suffix resolvers."""

import re
from typing import List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from ordpresse.models import CanonicalEntry

# Cliffhanger endings where truncation breaks the core meaning of the sentence:
CLIFFHANGER_ELLIPSIS_RE = re.compile(
    r"\b(?:et|en|den|det|de|at|i|på|med|til|for|af|om|over|under|ved|fra|"
    r"som|der|eller|el\.|dvs\.|fx|f\.eks\.|bl\.a\.|fast|så|ikke|kan|skal|må)\s*(?:\.\.\.|…)\s*$",
    re.IGNORECASE,
)


def classify_external_lookup_priority(entry: "Optional[CanonicalEntry]") -> Optional[int]:
    """
    Returns:
      1 = Priority 1 (CRITICAL): Entry is missing or is an empty COR skeleton with 0 definitions.
      2 = Priority 2 (SECONDARY): Entry has a COR-SEM definition, but it ends with a 'cliffhanger'
          truncation (e.g. 'med et...', 'i fast...', 'dvs. brødre...', 'stå fast...').
      3 = Priority 3 (COSMETIC / PASSIVE): Entry has a truncated COR-SEM definition ('...'),
          but the main clause before '...' already provides usable context (e.g. 'svaghed, hjælpeløshed...').
      None = Entry is 100% complete; no external lookup needed.
    """
    if entry is None:
        return 1
    if not entry.modern_senses and not entry.historical_senses and not entry.summary_hook:
        return 1

    # Check if any modern sense from COR-SEM is truncated with '...'
    truncated_defs = [
        s.definition.strip()
        for s in entry.modern_senses
        if s.definition.strip().endswith(("...", "…"))
    ]
    if not truncated_defs:
        return None

    # If the primary definition is very short (< 28 chars) or cuts off right after a function word/cliffhanger:
    for d in truncated_defs:
        if len(d) < 28 or CLIFFHANGER_ELLIPSIS_RE.search(d):
            return 2

    return 3



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


MODERN_HYPHEN_INITIALISMS = {
    "tv", "cd", "dvd", "edb", "pc", "wc", "vm", "em", "dm", "ef", "eu", "fn", "fm",
    "bmx", "din", "dna", "hiv", "mbd", "mdf", "vhf", "uhf", "usb", "rem", "sos",
    "abs", "aids", "bz", "kz", "ok", "ct", "mr", "id", "ic3", "bh"
}
MODERN_HYPHEN_WORDS = {
    "køkken-alrum", "walkie-talkie", "sort-hvid", "rød-hvid", "rød-blå",
    "mave-tarm-kanal", "mave-tarm-sygdom", "hjerte-kar-sygdom", "læbe-gane-spalte",
    "lørdag-søndag", "finsk-ugrisk", "had-kærlighed", "øre-næse-hals-læge",
    "enten-eller", "log-in", "snabel-a", "pret-a-porter", "tete-a-tete", "tai-chi",
    "cha-cha-cha", "deja-vu", "de-facto", "dum-dum-kugle", "hula-hula-pige",
    "nikkel-cadmium-batteri", "olie-eddike-dressing", "savoir-vivre",
    "science-fiction-roman", "thousand-island-dressing"
}


def should_keep_hyphen(w: str) -> bool:
    """Return True if word is a modern hyphenated compound, acronym, or numeral."""
    w_low = w.lower().strip()
    if " " in w_low:
        return True
    if re.search(r"\d", w_low):
        return True
    if w_low in MODERN_HYPHEN_WORDS:
        return True
    # Single-letter prefix compounds: a-aktie, e-mail, u-båd, v-formet
    if re.match(r"^[a-zæøåA-ZÆØÅ]-", w_low):
        return True
    first_part = w_low.split("-")[0]
    if first_part in MODERN_HYPHEN_INITIALISMS:
        return True
    return False


def _expand_parentheses(text: str) -> Set[str]:
    """Expands optional parts in parentheses, e.g. 'fyrstik(ke)' -> {'fyrstik', 'fyrstikke'},
    'lu(l)lu(lu)' -> {'lulu', 'lullu', 'lululu', 'lullulu'}.
    """
    if "(" not in text or ")" not in text:
        return {text}
    parts = re.split(r"\(([^()]+)\)", text)
    forms = {""}
    for i, piece in enumerate(parts):
        if i % 2 == 0:
            forms = {f + piece for f in forms}
        else:
            forms = {f + piece for f in forms} | forms
    return {f.strip() for f in forms if f.strip()}


def sanitize_headword(raw_hw: str) -> tuple[str, Set[str]]:
    """Sanitize raw headwords from ODS, DDO, COR, and cache.
    
    Returns (clean_headword, extra_synonyms_for_syn):
    1. Strip ® registered trademark symbol and zero-width artifacts.
    2. Strip leading Roman numerals and homograph markers ('iii. kvarre' -> 'kvarre', 'i. slave-lænke' -> 'slavelænke').
       Discard standalone 'i.' or Roman numerals.
    3. Split comma-separated & 'i.' variant headwords ('dvæl, i. dvæle' -> 'dvæle', 'ingensteds, intetsteds' -> 'ingensteds').
    4. Expand parenthetical optional letters ('fyrstik(ke)' -> 'fyrstikke' + 'fyrstik', 'k(j)øbenhavn' -> 'københavn' + 'kjøbenhavn').
    5. De-hyphenate historical ODS etymological hyphens ('aarvaagen-hed' -> 'aarvaagenhed' + 'aarvaagen-hed').
    """
    if not raw_hw:
        return ("", set())

    raw = raw_hw.strip().replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    raw = raw.replace("®", "").strip()
    if not raw:
        return ("", set())

    # Standalone Roman numeral or sense marker (e.g. 'i.', 'ii.')
    if re.match(r"^(?:[ivxlcdm]+\.|\d+\.)$", raw, re.I):
        return ("", set())

    extra_synonyms: Set[str] = set()

    # Comma variants (e.g. 'dvæl, i. dvæle', 'ingensteds, intetsteds', 'kumpan, kumpen')
    m_ref = re.match(r"^(.+?),\s*(?:i\.|se|jf\.)\s*(.+)$", raw, re.I)
    if m_ref:
        alt_part = m_ref.group(1).strip()
        primary_part = m_ref.group(2).strip()
        extra_synonyms.add(alt_part)
        raw = primary_part
    elif "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        raw = parts[0]
        for p in parts[1:]:
            extra_synonyms.add(p)

    # Strip leading Roman numerals and homograph markers (unless ordinal like "1. g'er")
    is_ordinal = bool(re.match(r"^\d+\.\s+[a-zæøåA-ZÆØÅ]('er|\b)", raw))
    if not is_ordinal:
        raw = re.sub(r"^(?:[ivxlcdm]+\.|\d+\.|i\.)\s*", "", raw, flags=re.I).strip()

    # Clean Roman numerals in extra synonyms as well
    cleaned_syns = set()
    for s in extra_synonyms:
        s_c = re.sub(r"^(?:[ivxlcdm]+\.|\d+\.|i\.)\s*", "", s, flags=re.I).strip()
        if s_c and not re.match(r"^(?:[ivxlcdm]+\.|\d+\.)$", s_c, re.I):
            cleaned_syns.add(s_c)
    extra_synonyms = cleaned_syns

    # Expand parenthetical optional letters: e.g. "fyrstik(ke)", "k(j)øbenhavn", "bark(e)brød", "lu(l)lu(lu)"
    if "(" in raw and ")" in raw:
        variants = _expand_parentheses(raw)
        extra_synonyms.update(variants)
        if re.search(r"([kg])\(j\)", raw, re.I):
            raw = re.sub(r"([kg])\(j\)", r"\1", raw, flags=re.I)
            raw = re.sub(r"[\(\)]", "", raw).strip()
        else:
            raw = re.sub(r"[\(\)]", "", raw).strip()

    # Expand parentheses in extra synonyms as well
    expanded_syns = set()
    for s in extra_synonyms:
        if "(" in s and ")" in s:
            expanded_syns.update(_expand_parentheses(s))
        else:
            expanded_syns.add(s)
    extra_synonyms = expanded_syns

    # Strip trailing compounding hyphen if not prefix (e.g. 'spyt-', 'hobe-', 'syd-ost-')
    if raw.endswith("-") and len(raw) > 3 and not should_keep_hyphen(raw):
        raw = raw.rstrip("-").strip()

    # De-hyphenate historical ODS etymological hyphens (e.g. 'aarvaagen-hed', 'enfoldig-hed', 'ihjel-slaa')
    if "-" in raw and not should_keep_hyphen(raw):
        unhyphenated = raw.replace("-", "")
        extra_synonyms.add(raw)
        raw = unhyphenated

    for s in list(extra_synonyms):
        if "-" in s and not should_keep_hyphen(s):
            extra_synonyms.add(s.replace("-", ""))

    extra_synonyms.discard(raw)
    extra_synonyms.discard(raw.lower())
    extra_synonyms.discard("")
    return (raw, extra_synonyms)


def modern_to_historical_aa(word: str) -> List[str]:
    """Convert modern words containing 'å'/'Å' into pre-1948 'aa'/'Aa' variants."""
    if "å" not in word.lower():
        return []

    # Lowercase variant
    aa_lower = word.replace("å", "aa").replace("Å", "aa")
    # Titlecase / capitalized variant
    aa_cap = aa_lower.capitalize()
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
    - Capitalized version ONLY for non-ASCII entries (e.g., 'æble' -> 'Æble', 'åbning' -> 'Åbning')
    - Pre-1948 'aa' variants if headword contains 'å'
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
        w_clean = w.strip()
        if not w_clean:
            continue

        # Case variants: Lowercase is always added
        synonyms.add(w_clean.lower())
        # Capitalized variant is ONLY added when the first character is non-ASCII (ord > 127)
        # Because g_ascii_strcasecmp already folds ASCII A-Z case-insensitively!
        if ord(w_clean[0]) > 127:
            synonyms.add(w_clean.capitalize())

        # Hyphen stripping
        if "-" in w_clean and not should_keep_hyphen(w_clean):
            stripped = w_clean.replace("-", "")
            synonyms.add(stripped.lower())
            if ord(stripped[0]) > 127:
                synonyms.add(stripped.capitalize())

        # aa <-> å mappings
        if "å" in w_clean or "Å" in w_clean:
            for aa_var in modern_to_historical_aa(w_clean):
                synonyms.add(aa_var.lower())
                if ord(aa_var[0]) > 127:
                    synonyms.add(aa_var.capitalize())
        if "aa" in w_clean.lower():
            mod = historical_aa_to_modern(w_clean)
            synonyms.add(mod.lower())
            if ord(mod[0]) > 127:
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


def synthesize_compound_entry(word: str, db=None) -> "Optional[CanonicalEntry]":
    """Synthesize compound entry for directional adverbs or negated u- adjectives (Phase 3).
    
    Rule A: Directional/locative adverbs: ^(der|her)(nede|oppe|inde|ude|ovre|henne|hjemme|borte|omkringe?|under|over)(fra|til|hen|om)?$
    Rule B: Negated u- adjectives (e.g. uefterrettelig)
    """
    from ordpresse.models import CanonicalEntry, SenseItem
    from ordpresse.formatter import normalize_pos

    w = word.strip().lower().replace("-", "")
    if not w:
        return None

    # --- Rule B: Negated u- Adjectives (uefterrettelig & inflections) ---
    uefterret_forms = {
        "uefterrettelig", "uefterrettelige", "uefterretteligt",
        "uefterretlig", "uefterretlige", "uefterretligt",
    }
    if w in uefterret_forms:
        real_def = "Som man ikke kan rette sig efter eller stole på; upålidelig, ulydig eller genstridig over for øvrighedens påbud."
        return CanonicalEntry(
            headword="uefterrettelig",
            pos="adj.",
            inflections={
                "uefterrettelig", "uefterrettelige", "uefterretteligt",
                "uefterretlig", "uefterretlige", "uefterretligt",
                "Uefterrettelig", "Uefterrettelige",
            },
            historical_spellings=["uefterretlig", "u-efterrettelig"],
            summary_hook=real_def,
            historical_senses=[
                SenseItem(
                    definition=real_def,
                    label="hist.",
                    quote=None,
                    quote_author=None,
                    source_tag="ODS",
                )
            ],
            realia_note="Klassisk historisk-juridisk udtryk for personer, handlinger eller skrifter, som ikke lader sig rette eller vejlede af lov og ret.",
            sources_used=["ODS", "Molbech"],
        )

    # --- Rule A: Directional/Locative Adverbs ---
    adv_pat = re.compile(
        r"^(der|her)(nede|oppe|inde|ude|ovre|henne|hjemme|borte|omkringe?|under|over)(fra|til|hen|om)?$",
        re.IGNORECASE,
    )
    m = adv_pat.match(w)
    if m:
        prefix = m.group(1).lower()
        stem_core = m.group(2).lower()
        suffix = (m.group(3) or "").lower()
        base_stem = f"{prefix}{stem_core}"

        quote_text = None
        quote_author = None

        if suffix == "fra":
            if stem_core == "nede":
                if prefix == "der":
                    definition = "Fra et sted længere nede (eller sydpå) i forhold til udgangspunktet; nedefra det pågældende sted (jf. dernede, nedenfra)."
                    quote_text = "dernedefra (: fra gaden) op til ham"
                    quote_author = "H. Drachmann"
                else:
                    definition = "Fra et sted her længere nede (eller sydpå) i forhold til udgangspunktet; nedefra dette sted (jf. hernede, nedenfra)."
            elif stem_core == "oppe":
                definition = f"Fra et sted længere oppe (eller nordpå) i forhold til udgangspunktet; oppefra det pågældende sted (jf. {base_stem}, oppefra)."
            elif stem_core == "inde":
                definition = f"Fra et sted længere inde (fx indendørs eller inde i landet) i forhold til udgangspunktet; indefra det pågældende sted (jf. {base_stem}, indefra)."
            elif stem_core == "ude":
                definition = f"Fra et sted længere ude (fx udendørs eller ude i det fjerne) i forhold til udgangspunktet; udefra det pågældende sted (jf. {base_stem}, udefra)."
            elif stem_core == "ovre":
                definition = f"Fra et sted på den anden side eller ovre i forhold til udgangspunktet; ovrefra det pågældende sted (jf. {base_stem}, ovrefra)."
            elif stem_core == "henne":
                definition = f"Fra et sted længere henne i forhold til udgangspunktet; hennefra det pågældende sted (jf. {base_stem}, hennefra)."
            elif stem_core == "hjemme":
                definition = f"Fra hjemmet eller hjemegnen i forhold til udgangspunktet; hjemmefra det pågældende sted (jf. {base_stem})."
            elif stem_core == "borte":
                definition = f"Fra et sted langt borte i forhold til udgangspunktet; bortefra det pågældende sted (jf. {base_stem})."
            elif stem_core.startswith("omkring"):
                definition = f"Fra et sted rundt omkring i forhold til udgangspunktet (jf. {base_stem})."
            elif stem_core == "under":
                definition = f"Fra et sted nedenunder i forhold til udgangspunktet; nedefra det pågældende sted (jf. {base_stem}, nedenunder)."
            elif stem_core == "over":
                definition = f"Fra et sted ovenover i forhold til udgangspunktet; ovenfra det pågældende sted (jf. {base_stem}, ovenover)."
            else:
                definition = f"Fra et sted {stem_core} i forhold til udgangspunktet (jf. {base_stem})."
        elif suffix in ("til", "hen"):
            definition = f"Hen til eller mod et sted {stem_core} i forhold til udgangspunktet (jf. {base_stem})."
        elif suffix == "om":
            definition = f"Rundt om eller bag om et sted {stem_core} i forhold til udgangspunktet (jf. {base_stem})."
        else:
            # Suffix is empty, e.g. 'dernede'
            definition = f"På eller ved et sted {stem_core} i forhold til udgangspunktet eller den talende (jf. {stem_core})."

        all_infs = {w, w.capitalize(), base_stem, base_stem.capitalize()}
        if suffix:
            all_infs.add(f"{prefix}{stem_core}{suffix}")

        return CanonicalEntry(
            headword=w,
            pos="adv.",
            inflections=all_infs,
            summary_hook=definition,
            historical_senses=[
                SenseItem(
                    definition=definition,
                    label="adv.",
                    quote=quote_text,
                    quote_author=quote_author,
                    source_tag="ODS",
                )
            ],
            sources_used=["ODS", "COR-SEM"],
        )

    # General negated u- adjective fallback if root has definition in db
    if w.startswith("u") and len(w) > 4 and db is not None:
        root = w[1:]
        if hasattr(db, "lookup_cor_lemma_definitions"):
            root_defs = db.lookup_cor_lemma_definitions(root)
            if root_defs:
                root_pos = normalize_pos(root_defs[0].get("pos", ""))
                if "adj" in root_pos:
                    root_def = root_defs[0]["definition"].rstrip(".")
                    syn_def = f"Som ikke er {root}; som mangler egenskaben: {root_def}."
                    return CanonicalEntry(
                        headword=w,
                        pos="adj.",
                        inflections={w, w.capitalize(), f"{w}e", f"{w}t"},
                        summary_hook=syn_def,
                        historical_senses=[
                            SenseItem(
                                definition=syn_def,
                                label="adj.",
                                source_tag="COR-SEM",
                            )
                        ],
                        sources_used=["COR-SEM"],
                    )

    return None

