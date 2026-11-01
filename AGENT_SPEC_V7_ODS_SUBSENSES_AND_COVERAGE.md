# AGENT_SPEC_V7_ODS_SUBSENSES_AND_COVERAGE.md — ODS Sub-Sense Extraction, Grammatical Header Filtering & Missing 1800s Coverage
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5`
**Target Agent:** Gemini 3.8 Flash

## Context: 3 Bugs Exposed on E-Reader Testing (`forarmet`, `forarbejde`, `udlevede`)

1. **Bug A — ODS Hierarchical "Meta-Headers" Mistaken for Definitions (`udleve` bug):**
   Looking up `udlevede` -> `udleve` on the e-reader displays:
   ```
   HISTORISK BETYDNING (ODS / 1700-1950)
   1. (hist.) (nu sj.) som verbum.
   2. (hist.) brugt som adj., (jf.
   ```
   **Why this happened:**
   - In ODS (`ordnet.dk/ods`), verbs with participial adjectives (like `udleve`, `forarme`, `bedaget`, `udslidt`) use top-level numbers (`1.`, `2.`) as purely **grammatical section headers** (`"1. som verbum"`, `"2. (i perf. part.) brugt som adj."`), while the **actual semantic definitions** live inside the sub-senses (`1.1`, `1.2`, `2.1`, `2.2`)!
   - Furthermore, a naive sentence/parenthesis splitter in `ods_scraper.py` cut `"2. (hist.) brugt som adj., (jf."` right at the period after `jf.`, leaving an unclosed parenthesis `(jf.`!
   - Finally, `udleve` ALSO has a modern sense in `COR-SEM` (`"omsætte sine følelser, drømme el. lyster i handling"`), which was lost when the cached ODS entry replaced the `CanonicalEntry` instead of merging with `COR-SEM`!

2. **Bug B — Missing Literary Words (`forarmet` / `forarme`):**
   Looking up `forarmet` in Moberg produced 22 fuzzy prefix matches (`forarbejde`, etc.) because neither `forarme` nor `forarmet` was in `ordpresse.idx`!
   - `forarme` / `forarmet` (and `udlevet`) are classic literary words that must be present in both the base dictionary (`nordic_1800s.py`) and properly scraped from ODS/DDO.

3. **Bug C — Cliffhanger Truncations Still Present in Full Build (`forarbejde`):**
   In `forarbejde`, sense 1 ends with `"så der dannes et anvendeligt..."` and sense 2 ends with `"analysere og forholde sig..."`.
   - Even in `--full` builds, any `COR-SEM` sense in `datasets.sqlite3` that matches `CLIFFHANGER_ELLIPSIS_RE` (`anvendeligt...`, `forholde sig...`, `med et...`, `i fast...`) makes the sentence ungrammatical on the e-reader.

---

## Implementation Tasks

### Task 1: Fix `ods_scraper.py` — Reject Grammatical Meta-Headers & Extract Sub-Senses
1. **Detect & Skip Grammatical Section Headers in `ods_scraper.py`:**
   - Create a validator `is_meta_header_not_definition(text: str) -> bool`:
   - If an extracted ODS sense text (after stripping usage labels like `(hist.)`, `(nu sj.)`, `(jf. ...)` and trailing fragments) consists only of grammatical routing phrases such as:
     - `"som verbum"`, `"som adj."`, `"som subst."`, `"som adv."`
     - `"brugt som adj."`, `"i perf. part."`, `"i præs. part."`, `"i pass."`, `"refl."`, `"abs."`, `"intr."`, `"tr."`
     - or has fewer than 12 characters of actual lexical explanation after removing `(hist.)`, `(nu sj.)`, `(jf. ...)`, `som vb./adj./sb.`:
     **Do NOT emit that header as a `SenseItem`!** Instead, descend into its child/sub-sense `.definitionBox` / `.betydning` nodes (`1.1`, `1.2`, `2.1`, `2.2`) and extract the actual semantic definitions from those sub-senses!
2. **Fix Abbreviations & Unclosed Parentheses (`(jf.` bug):**
   - Never split or truncate a definition at a period belonging to an abbreviation (`jf.`, `bl.a.`, `f.eks.`, `fx.`, `dvs.`, `osv.`, `m.m.`, `el.`, `alm.`, `egl.`, `især`, `navnlig`, `ofte`, `sj.`, `jf`).
   - Strip trailing cross-reference parentheses like `r"\(\s*(?:jf\.\vert{}se)\b[^)]*\)?"` cleanly from the end of definitions, and ensure balanced parentheses (never output an unclosed `(` in any definition).
3. **Always Merge `COR-SEM` Modern Senses + `ODS` Historical Senses (`synthesizer.py` & `pipeline.py`):**
   - When a word like `udleve` exists in BOTH `COR-SEM` (modern meaning: *"omsætte indre impulser, ønsker eller forestillinger i handling"*) AND `cache.sqlite3` / `ODS` (historical meaning: *"leve til ende; (som adj. udlevet) svækket af høj alder eller udsvævelser, affældig, udslidt"*), the final `CanonicalEntry` MUST include **both** `modern_senses` (from `COR-SEM`/`DDO`) and `historical_senses` (from `ODS`)! Never let a cached ODS entry wipe out valid modern `COR-SEM` senses.

### Task 2: Add Curated Literary & Participial Entries (`forarme`, `forarmet`, `udleve`, `udlevet`)
In `src/ordpresse/sources/nordic_1800s.py` (and `datasets.sqlite3`), add/update complete, high-quality entries:
1. **`forarme`** (`pos="vb."`, inflections: `{"forarmer", "forarmede", "forarmet", "forarmedes"}`):
   - `summary_hook`: `"Gøre fattig; bringe i armod og forfald; udpine (f.eks. jord eller befolkning)."`
2. **`forarmet`** (`pos="adj."`, inflections: `{"forarmede"}`):
   - `summary_hook`: `"Bragt til dyb fattigdom og armod; udpint, forfalden eller ribbet for ressourcer."`
3. **`udleve`** (`pos="vb."`, inflections: `{"udlever", "udlevede", "udlevet"}`):
   - Modern sense: `"Omsætte sine drømme, lyster eller følelser i handling; gennemleve fuldt ud."`
   - Historical sense (`ODS`): `"Leve til ende; tilbringe eller afslutte sit liv; i perf. part. »udlevet« (pl. »udlevede«): svækket af høj alder, slid eller udsvævelser; affældig, udslidt."`

### Task 3: Triage & Purge Meta-Header Rows in `cache.sqlite3` (`triage.py`)
1. Update `src/ordpresse/triage.py` (`KEEP` rules):
   - Any cached row in `cache.sqlite3` whose definition matches `is_meta_header_not_definition` (e.g., contains `"som verbum."`, `"brugt som adj."`, or an unclosed `"(jf."`) MUST fail `KEEP`!
   - Either `SALVAGE` it deterministically (if in `nordic_1800s` / `COR-SEM`) or `PURGE` it and re-scrape it with the fixed `ods_scraper.py`!
2. Cure the truncated `forarbejde` senses in `datasets.sqlite3` (`cor_definitions`):
   - Sense 1: `"behandle et råmateriale så der dannes et anvendeligt produkt; bearbejde"`
   - Sense 2: `"arbejde mentalt med noget; analysere og forholde sig til"`
   - Also scan `cor_definitions` in `datasets.sqlite3` for other verbs/adjectives where a sense ends with `"et anvendeligt..."` or `"forholde sig..."` and repair them.

### Task 4: Execute V6 Index Hygiene + V7 Fixes, Test & Deploy to `192.168.50.5`
1. Ensure all tasks from `AGENT_SPEC_V6_INDEX_HYGIENE.md` (headword sanitization of `iii.`, `®`, `(...)`, and `.syn` ASCII case-deduplication) are also applied if not yet run.
2. Add unit tests in `tests/test_ods_subsenses_and_coverage.py`:
   - `test_udlevede_has_real_definitions_and_no_meta_headers`: Lookup `"udlevede"` -> resolves to `"udleve"`, contains both modern and historical (*affældig / svækket af alder*) meanings, and does NOT contain `"som verbum."` or `"(jf."`.
   - `test_forarmet_lookup_hits_exact_entry`: Lookup `"forarmet"` and `"forarmede"` -> resolves directly to `"forarmet"` / `"forarme"` with definition about poverty (*fattigdom / armod*).
   - `test_forarbejde_not_truncated`: Lookup `"forarbejde"` -> no `"anvendeligt..."` or `"forholde sig..."`.
3. Run `.venv/bin/pytest -v`, commit & push, deploy to `192.168.50.5`, run `triage` on the server's `cache.sqlite3`, rebuild the full dictionary on `192.168.50.5`, and verify `/api/preview?q=...` for `forarmet`, `udlevede`, and `forarbejde`.