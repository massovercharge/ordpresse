# AGENT_SPEC_V8_AA_SYNONYMS_AND_GENITIVE_PRUNING.md — Full `aa` Orthography Coverage & Genitive Pruning in `.syn`
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5`
**Target Agent:** Gemini 3.8 Flash

## Context: Binary Inspection of 2026.11.01 Build (`wordcount=36963`, `synwordcount=197350`)
The V6/V7 build successfully cleaned `.idx` headwords, added `forarme`/`forarmet`, and removed ASCII title-case duplicates in `.syn`. However, binary inspection of `ordpresse.syn` revealed two critical issues:

1. **Missing `aa` Orthography Variants for Words Not Starting with `Å`:**
   In `ordpresse.syn`, words starting with `aa` (`aaben`, `aarhundred`, `aarsag`) exist, and compound verbs like `afgaa`/`forstaa` exist, but **classic 1800s/early-1900s spellings with internal `aa` (`baad`, `daab`, `faar`, `gaade`, `gaard`, `haab`, `haar`, `laas`, `maane`, `saar`, `taage`, `taarne`) are completely missing from `.syn`!**
   When reading pre-1948 Danish literature (Pontoppidan, Bang, Jensen, Kirkegaard, Moberg translations, etc.), looking up `gaard`, `baad`, `haab`, `maane`, or `taage` fails unless the word happens to be a separate historical headword in `.idx`.
2. **Genitive Bloat Still Present in `.syn`:**
   Every noun in `ordpresse.syn` still has 3–4 regular genitive inflections (`abbedens`, `abbedernes`, `abbeders`, `abehusenes`, `abehuses`, `abehusets`). Both KOReader and CrossPoint automatically strip trailing possessive `-s` during lookup, so these ~85,000 genitive rows in `.syn` are pure bloat for the ESP32-S3 (`Xteink X4 Pro`).
3. **Abbreviation & Fragment Noise in `.syn`:**
   Entries like `"adm."`, `"afd."`, and `"aarh"` are present in `ordpresse.syn`.

---

## Implementation Tasks

### Task 1: Generate `aa` Synonym Variants for ALL Headwords & Inflections Containing `å` (`stardict_writer.py` & `orthography.py`)
1. When building the synonym map in `stardict_writer.py` for each `CanonicalEntry`:
   - For the `headword` itself AND every kept inflection in `entry.inflections`:
   - If the string contains `"å"` (anywhere in the word — e.g., `"båd"`, `"både"`, `"båden"`, `"gård"`, `"gårde"`, `"gården"`, `"håb"`, `"håbe"`, `"håbede"`, `"måne"`, `"månen"`, `"tåge"`, `"tågen"`, `"forstå"`, `"forstår"`):
     - Generate `aa_form = form.replace("å", "aa")` (lowercase) and add it to the `.syn` index pointing to this entry's `.idx` index (unless `aa_form == entry.headword.lower()`).
   - Ensure we do NOT generate redundant genitives of `aa_form` either (apply the genitive filter BEFORE generating `aa_form`).

### Task 2: Prune Regular Genitives & Abbreviations from `.syn` (`stardict_writer.py`)
1. **Genitive Filter (`is_redundant_genitive(form: str, headword: str) -> bool`):**
   - Before adding any inflection `form` to `.syn`:
   - If `form == headword.lower()` or `form in GENITIVE_WHITELIST` (`{"almindeligvis", "dags", "års", "aars", "slags", "tilfreds", "tids", "livs", "lands", "havs", "bords", "steds", "fredags", "mandags", "tirsdags", "onsdags", "torsdags", "lørdags", "søndags"}`), keep it.
   - Otherwise, if `form` ends with `("ens", "enes", "ernes", "ets", "ers", "'s", "s'")` and `not headword.lower().endswith(form[-3:])`, skip it!
   - Also, if `form.endswith("s")` and `form[:-1] == headword.lower()` (regular indefinite singular genitive like `abbeds`, `bils`, `gårds`) and `pos == "sb."`, skip it!
2. **Abbreviation & Fragment Filter:**
   - Never emit a `.syn` key that ends with a period `.` (e.g. `"adm."`, `"afd."`, `"bl.a."`) or is the truncated fragment `"aarh"`.

### Task 3: Verify with Pytest & Rebuild on `192.168.50.5`
1. Add unit tests in `tests/test_aa_and_genitives.py`:
   - Verify that compiling an entry for `"gård"` (`inflections={"gårde", "gården", "gårdene", "gårds", "gårdenes"}`) produces `.syn` keys `"gårde"`, `"gården"`, `"gårdene"`, `"gaard"`, `"gaarde"`, `"gaarden"`, `"gaardene"`, and does **NOT** produce `"gårds"` or `"gårdenes"`.
   - Verify that `"baad"`, `"daab"`, `"haab"`, `"maane"`, `"taage"`, `"saar"`, and `"laas"` are present in the compiled `.syn` (or `.idx`) and resolve to `"båd"`, `"dåb"`, `"håb"`, `"måne"`, `"tåge"`, `"sår"`, and `"lås"`.
   - Verify that `"abbedens"`, `"abbedernes"`, `"adm."`, and `"afd."` are NOT in `.syn`.
2. Run `.venv/bin/pytest -v`, commit & push, deploy to `192.168.50.5`, rebuild the full dictionary (`ordpresse build --full --no-network`), and report the new `wordcount` and `synwordcount`.