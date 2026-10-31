# AGENT_SPEC_V6_INDEX_HYGIENE.md — Headword Sanitization, .syn Deduplication & ESP32 Optimization
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5`
**Target Agent:** Gemini 3.8 Flash

## Context & Findings from Binary Inspection of Latest Build
An inspection of the generated `ordpresse.ifo` (`wordcount=36965`, `synwordcount=454011`), `ordpresse.idx`, and `ordpresse.syn` revealed 4 concrete data-hygiene issues that bloat `.syn` and pollute `.idx`:

1. **50% Bloat in `.syn` from ASCII Title-Case Duplicates:**
   Every synonym in `.syn` is written twice: lowercase (`abbeden`, `aabenraaere`) and capitalized (`Abbeden`, `Aabenraaere`). Because StarDict's `g_ascii_strcasecmp` is already case-insensitive for ASCII `A-Z`, capitalized duplicates starting with ASCII `A-Z` are 100% redundant! Only words starting with non-ASCII UTF-8 characters (`æ`, `ø`, `å`, `é`, `ü`) need both lowercase and uppercase entries (`æble`/`Æble`, `åbning`/`Åbning`) because `g_ascii_strcasecmp` does not fold multibyte UTF-8.
2. **Dirty Headwords in `.idx`:**
   - Roman numeral homograph prefixes leaked into `.idx`: `"iii. kvarre"`, `"iii. skøtte"`, `"i. slave-lænke"`, `"i. styrke-prøve"`, `"i. taag"`, `"i."`.
   - Editorial glosses/commas inside headwords: `"dvæl, i. dvæle"`, `"ingensteds, intetsteds"`, `"kernesund, kærnesund"`, `"kumpan, kumpen"`.
   - Parenthetical optional characters in headwords: `"ba(h)"`, `"bark(e)brød"`, `"be(j)an"`, `"bedrager(i)sk"`, `"befje(d)re"`, `"besigt(ig)e"`, `"besigt(ig)else"`, `"diske(n)springer"`, `"forfører(i)sk"`, `"forræder(i)sk"`, `"fyrstik(ke)"`, `"høhæk(ke)"`, `"høvding(e)"`, `"indvi(e)"`, `"jorderig(e)"`, `"k(j)øbenhavn"`, `"ka(r)nalje"`, `"loft(s)rum"`, `"logi(s)"`, `"logi(s)hus"`.
   - Registered trademark symbols in headwords: `"a38®"`, `"alkolog®"`, `"eksperimentarium®"`, `"eternit®"`, `"jeep®"`, `"karlit®"`.
   - Historical etymological hyphens in headwords: `"aarvaagen-hed"`, `"enfoldig-hed"`, `"efter-tragtelses-værdig"`, `"lands-mand-inde"`, `"ihjel-slaa"`, `"fast-nagle"`, `"band-straale"`, `"lang-skægget"`, `"hurtig-sejlende"`.
3. **Remaining Synthetic Inflections (`agtsommere`, `agtsomst`, `arbejdsommere`, `aabnere`):**
   - Adjectives ending in `-som` (`agtsom`, `arbejdsom`) and nouns like `aabner` still have invalid synthetic forms (`agtsommere`, `agtsomst`, `agtsomste`, `arbejdsommere`, `arbejdsomst`, `aabnere`) in `.syn`.
4. **Genitive Bloat in `.syn`:**
   - Every noun generates 4–6 regular genitive forms (`-ens`, `-enes`, `-ernes`, `-ets`, `-ers`) in `.syn`. E-readers already strip trailing `-s` as fallback, so regular plural/definite genitives (`-ens`, `-enes`, `-ernes`, `-ets`, `-ers`) should be excluded from `.syn`.

---

## Implementation Tasks

### Task 1: Headword Sanitizer (`src/ordpresse/orthography.py`, `pipeline.py`, `nordic_1800s.py`, `molbech_1859.py`)
Create and apply `sanitize_headword(raw_hw: str) -> tuple[str, set[str]]` which returns `(clean_headword, extra_synonyms_for_syn)`:
1. **Strip Leading Roman Numerals & Sense Markers:**
   - Remove leading `r"^(?:[iIvVxX]+\.\s+|\d+\.\s+|i\.\s+)"` (unless it is a real ordinal entry like `"1. g'er"` or `"3. g'er"`). Discard any standalone `"i."` entry.
   - Thus `"iii. kvarre"` -> `"kvarre"`, `"iii. skøtte"` -> `"skøtte"`, `"i. taag"` -> `"taag"`, `"i. slave-lænke"` -> `"slavelænke"`.
2. **Split Comma-Separated & `"i."` Variant Headwords:**
   - If a headword matches `"dvæl, i. dvæle"`, make the primary headword `"dvæle"` and add `"dvæl"` to `inflections`.
   - If a headword contains comma-separated variants (`"ingensteds, intetsteds"`, `"kernesund, kærnesund"`, `"kumpan, kumpen"`), use the first part as `headword` (`"ingensteds"`, `"kernesund"`, `"kumpan"`) and put the second part (`"intetsteds"`, `"kærnesund"`, `"kumpen"`) into `inflections` so both are directly searchable!
3. **Expand Parenthetical Optional Letters:**
   - If a headword contains `r"^([^()]*)\(([^()]+)\)([^()]*)\$"` (e.g. `"k(j)øbenhavn"`, `"fyrstik(ke)"`, `"høhæk(ke)"`, `"be(j)an"`, `"besigt(ig)e"`, `"ba(h)"`, `"bark(e)brød"`, `"loft(s)rum"`, `"logi(s)"`):
     - Generate both `short_form = prefix + suffix` and `long_form = prefix + inside + suffix`.
     - Use the standard modern form as `clean_headword` and add BOTH `short_form` and `long_form` to `inflections`! Never leave parentheses `(...)` inside an `.idx` headword!
4. **Strip `®` Symbol:**
   - Remove `®` from all `.idx` headwords (`"a38®"` -> `"a38"`, `"alkolog®"` -> `"alkolog"`, `"eksperimentarium®"` -> `"eksperimentarium"`, `"eternit®"` -> `"eternit"`, `"jeep®"` -> `"jeep"`, `"karlit®"` -> `"karlit"`).
5. **De-Hyphenate ODS Etymological Headwords:**
   - For historical words containing etymological syllable hyphens (e.g. `"aarvaagen-hed"`, `"enfoldig-hed"`, `"efter-tragtelses-værdig"`, `"lands-mand-inde"`, `"ihjel-slaa"`, `"fast-nagle"`, `"band-straale"`, `"lang-skægget"`, `"hurtig-sejlende"` — i.e., multi-letter lowercase words with hyphens that are NOT modern prefix/abbreviation compounds like `"a-aktie"`, `"e-mail"`, `"tv-program"`, `"65-billet"`, `"gør det selv-mand"`, `"køkken-alrum"`):
   - Use the unhyphenated form as the canonical `.idx` headword (`"aarvaagenhed"`, `"enfoldighed"`, `"eftertragtelsesværdig"`, `"landsmandinde"`, `"ihjelslaa"`, `"fastnagle"`) and keep the hyphenated form in `inflections`.

### Task 2: Deduplicate `.syn` Case Variants & Prune Redundant Genitives (`stardict_writer.py` & `db.py`)
1. **Eliminate ASCII Title-Case Duplicates in `.syn`:**
   - In `stardict_writer.py` (and wherever `inflections` / `aa`-variants are added):
   - Do NOT add a capitalized variant (`form.capitalize()`) if `form[0]` is an ASCII letter (`'a' <= form[0].lower() <= 'z'`), because `g_ascii_strcasecmp` already matches `"Abbeden"` to `"abbeden"` case-insensitively!
   - ONLY generate the capitalized variant when `ord(form[0]) > 127` (i.e. words starting with `æ`, `ø`, `å` -> add both `æ...` and `Æ...`, `ø...` and `Ø...`, `å...` and `Å...`).
2. **Prune Regular Possessive Genitives from `.syn`:**
   - Exclude regular inflected genitive forms ending in `("ens", "enes", "ernes", "ets", "ers", "'s", "s'")` from `.syn` UNLESS the headword itself ends with those characters or is in a small whitelist (`{"almindeligvis", "dags", "års", "aars", "slags", "tilfreds", "tids", "livs", "lands", "havs", "bords", "steds"}`).
3. **Purge Synthetic `-ere`/`-est` Forms from `datasets.sqlite3` / `db.py`:**
   - Filter out comparative/superlative forms (`-ere`, `-est`, `-este`, `-mere`, `-mest`, `-meste`) for adjectives ending in `-som` (`agtsommere`, `arbejdsommere`), `-isk`, `-et`, `-ende`, and never attach `-ere` to nouns ending in `-er` (`aabnere`).

### Task 3: Triage `cache.sqlite3`, Test & Rebuild on `192.168.50.5`
1. Update `triage.py` so cached rows with dirty headwords (`iii. ...`, `®`, `(...)`, etc.) are sanitized or purged.
2. Create `tests/test_index_hygiene.py` and verify with `.venv/bin/pytest -v`:
   - `sanitize_headword("iii. kvarre")[0] == "kvarre"`
   - `sanitize_headword("dvæl, i. dvæle")` -> headword `"dvæle"`, synonym `"dvæl"`
   - `sanitize_headword("eternit®")[0] == "eternit"`
   - `sanitize_headword("fyrstik(ke)")` -> headword `"fyrstikke"`, synonym `"fyrstik"`
   - `sanitize_headword("aarvaagen-hed")[0] == "aarvaagenhed"`
   - Compiled `.syn` does NOT contain ASCII capitalized duplicates (`"Abbeden"`), DOES contain non-ASCII capitalized forms (`"Åbningen"` / `"Æblet"`), and does NOT contain `"agtsommere"`, `"arbejdsommere"`, or `"abbedernes"`.
3. Run `.venv/bin/pytest -v`, commit & push, deploy to `192.168.50.5`, run `triage` on the server, rebuild the full dictionary (`ordpresse build --full --no-network`), and report the new `wordcount`, `synwordcount`, and file sizes of `.idx`, `.syn`, and `.dict.dz`.