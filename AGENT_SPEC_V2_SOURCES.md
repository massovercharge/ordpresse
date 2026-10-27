# AGENT_SPEC_V2_SOURCES.md (v2.1) — Precision Multi-Source Synthesis & Canonical E-Ink Spec
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5` (deploy via SSH from Antigravity IDE)
**Baseline State (v1):** Currently compiles 33,484 modern DDO headwords and ~480,000 synonyms from `COR` + `COR.SEM.EXT`.
**v2.1 Goal:** Expand coverage to historical Danish (1700–1950) via `ods-fullforms`, `ordnet.dk/ods`, `ordnet.dk/fao` (Feilberg), `naob.no`, and a curated 1800s Nordic/Moberg lexicon, merging all sources through a strict 3-layer Canonical E-Ink HTML template without corrupting the binary `.idx`/`.syn` tables.

---

## 1. Strict StarDict `.idx` / `.syn` Invariants (DO NOT VIOLATE)

When merging modern (`COR`) and historical (`ODS`, `Molbech`, `NAOB`) entries into the binary StarDict compiler (`stardict_writer.py`), enforce these 4 mathematical invariants before writing bytes:

1. **Canonical Headword Casing:**
   - Every `CanonicalEntry.headword` MUST be normalized to lowercase (`entry.headword = raw_headword.strip().lower()`), EXCEPT proper nouns/acronyms if any.
   - If the original historical headword was capitalized (e.g., ODS `Jordebog`, `Lægdslem`, `Banko`) or used `aa` (`Aar`, `Gaardmand`), store the capitalized/`aa` string in `entry.historical_spellings` AND in `entry.inflections`.
2. **Unique `.idx` Keys:**
   - Merge entries that share the exact same lowercased `headword` BEFORE sorting `.idx`. There must NEVER be two entries in `.idx` with the same `headword` string.
3. **Disjoint `.idx` and `.syn` per Entry:**
   - For any entry at index `i` with `headword = H`, remove `H` from its synonym set (`synonyms_i.discard(H)`). A word in `.syn` pointing to index `i` must NEVER equal `entries[i].headword`.
4. **ASCII Case-Folding Sort (`g_ascii_strcasecmp`):**
   - Both `.idx` and `.syn` MUST be sorted by:
     ```python
     def stardict_sort_key(word: str) -> tuple[bytes, bytes]:
         b = word.encode("utf-8")
         return (b.lower(), b)
     ```

---

## 2. Unified Data Model (`src/ordpresse/models.py`)

Create `src/ordpresse/models.py` with these exact dataclasses:

```python
from dataclasses import dataclass, field

@dataclass
class SenseItem:
    definition: str               # Clean, readable definition text (no trailing bibliographic codes)
    label: str | None = None      # e.g. "hist.", "landbo.", "foræld.", "jura", "møntvæsen"
    quote: str | None = None      # Max 1 clean literary quote per sense (no page/vol numbers)
    quote_author: str | None = None # Expanded author name, e.g. "Blicher", "Pontoppidan"
    source_tag: str = "DDO"       # "DDO", "ODS", "Molbech (1859)", "Feilberg", "NAOB", "Salmonsen"

@dataclass
class CanonicalEntry:
    headword: str                 # Always lowercase canonical form, e.g. "lægdslem"
    historical_spellings: list[str] = field(default_factory=list) # e.g. ["Lægdslem", "Lejdslem"]
    pos: str = ""                 # Normalized: "sb. fk.", "sb. itk.", "adj.", "vb.", "adv."
    inflections: set[str] = field(default_factory=set)            # All .syn surface forms
    summary_hook: str = ""        # Layer 1: Crisp 1-sentence TL;DR (<= 165 chars)
    modern_senses: list[SenseItem] = field(default_factory=list)  # Max 3 modern DDO senses
    historical_senses: list[SenseItem] = field(default_factory=list) # Max 4 historical senses
    realia_note: str | None = None # Layer 3: Cultural/historical context (currencies, parish law)
    sources_used: list[str] = field(default_factory=list)
```

---

## 3. Exact DOM Selectors & Sanitization Rules (`src/ordpresse/sources/`)

### 3.1 Grammar POS Normalizer (`normalize_pos(raw_pos: str) -> str`)
Map heterogeneous POS tags across COR, ODS, Molbech, and NAOB into a single standard:
```python
POS_MAP = {
    "sb.fk": "sb. fk.", "sb. fk.": "sb. fk.", "en": "sb. fk.", "en.": "sb. fk.",
    "n. s. c.": "sb. fk.", "substantiv, fælleskøn": "sb. fk.", "fælleskøn": "sb. fk.",
    "sb.itk": "sb. itk.", "sb. itk.": "sb. itk.", "et": "sb. itk.", "et.": "sb. itk.",
    "n. s. n.": "sb. itk.", "substantiv, intetkøn": "sb. itk.", "intetkøn": "sb. itk.",
    "sb": "sb.", "sb.": "sb.", "substantiv": "sb.", "navneord": "sb.",
    "adj": "adj.", "adj.": "adj.", "adject.": "adj.", "tillægsord": "adj.", "ubøjel. adj.": "adj.",
    "vb": "vb.", "vb.": "vb.", "v.": "vb.", "verb.": "vb.", "udsagnsord": "vb.",
    "adv": "adv.", "adv.": "adv.", "biord": "adv.",
}
```

### 3.2 Exact `ordnet.dk/ods` Scraper & DOM Sanitizer (`sources/ods_scraper.py`)
When fetching `[https://ordnet.dk](https://ordnet.dk)