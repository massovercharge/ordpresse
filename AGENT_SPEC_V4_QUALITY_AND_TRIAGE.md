# AGENT_SPEC_V4_QUALITY_AND_TRIAGE.md — Scraping Quality, Empty-Skeleton Prevention, Morphological Synthesis & Zero-Compromise Cache Triage
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5` (via SSH from Antigravity IDE)
**Public Host:** `https://ordpresse.seame.click`
**Target Agent:** Gemini 3.8 Flash

> **CRITICAL DIRECTIVE:** Execute all 6 phases below sequentially. Do not skip the local + remote `cache.sqlite3` triage, and do not compromise on definition quality: if a cached entry cannot be repaired with 100% certainty using local data, purge it so it can be re-fetched cleanly.

---

## Phase 1: Inspect Current Parsers & Prevent "Empty COR Skeletons" (`tællelys` bug)

### Problem
Words like `tællelys` (looked up via `tællelyset`) exist in the full-forms `COR` dataset (~80,000 lemmas with POS and inflections), but do **not** exist in `COR-SEM` (the ~33,484 lemmas that have modern definitions). Currently, `pipeline.py` / `synthesizer.py` sees that `tællelys` is in `COR` and emits a skeleton entry containing only `tællelys sb. (tællelysenes) | Kilde: COR` with **zero definition**!

### Fix (`src/ordpresse/pipeline.py`, `src/ordpresse/synthesizer.py`, `src/ordpresse/sources/nordic_1800s.py`)
1. **Never Treat Definition-Less `COR` Lemmas as Complete:**
   - A lemma found in `COR` may provide the canonical `headword`, normalized `pos`, and `inflections` for `.syn`, **but** if it has 0 definitions (`not entry.modern_senses and not entry.historical_senses and not entry.summary_hook`), it MUST be treated as **unresolved** and routed through:
     a) `nordic_1800s.py`,
     b) `cache.sqlite3`,
     c) Morphological synthesis (`orthography.py`), and
     d) ODS / NAOB scraping.
2. **Hard Compiler Guard:**
   - Before passing entries to `stardict_writer.py`, filter out any `CanonicalEntry` that still has no definition (`not entry.modern_senses and not entry.historical_senses and not entry.summary_hook`), EXCEPT if it is being queued for scraping. Never write an empty skeleton to `.idx`/`.dict.dz`.
3. **Add `tællelys` and `alderstegen` to `nordic_1800s.py`:**
   - **`tællelys`** (`pos="sb. itk."`, inflections: `{"tællelyset", "tællelysene", "tællelysenes", "Tællelys", "Tællelyset"}`):
     - `summary_hook`: `"Lys støbt eller dyppet af smeltet dyrefedt (tælle/talg fra kvæg eller får); datidens almindelige belysning i bondehjem før stearin- og parafinlys."`
   - **`alderstegen`** (`pos="adj."`, inflections: `{"alderstegne", "alderstegent", "Alderstegen", "Alderstegne"}`):
     - `summary_hook`: `"Højt oppe i årene; ældgammel; svækket eller mærket af høj alder."`

---

## Phase 2: Fix HTML Text Glue & `SITATER` Noise in NAOB & ODS Scrapers (`alderstegen` bug)

### Problem
In the NAOB/ODS scrapers, looking up `alderstegen` produced:
- POS: `alderstegenadjektiv` (headword and POS glued together without space or normalization).
- Definition: `litterærtsvært gammel; tilårskommenSITATERderinde sidder kæmpen, den alderstegne Koll(J.S. WelhavenSamlede Digterverker )en alderstegen kvindelig person(Elisabe` (adjacent HTML tags concatenated without space separator, `SITATER` block dumped into definition, and mid-sentence truncation).

### Fix (`src/ordpresse/sources/` scrapers & `formatter.py`)
1. **Always Use Space Separator in BeautifulSoup:**
   - Replace every call to `.get_text()` or `.get_text(strip=True)` across the entire codebase with `.get_text(" ", strip=True)`, followed by whitespace normalization: `re.sub(r"\s+", " ", text).strip()` and cleanup of space before punctuation (`re.sub(r"\s+([,.;:!)])", r"\1", text)`).
2. **Strip Headword from POS & Always Normalize POS:**
   - When extracting POS from NAOB or ODS headers, strip the headword itself if attached (e.g. `re.sub(f"^{re.escape(headword)}\\s*", "", raw_pos, flags=re.I)`), and ALWAYS pass the result through `normalize_pos()` so `"adjektiv"` becomes `"adj."`, `"substantiv"` becomes `"sb."`, etc.
3. **Decompose `SITATER` & Quote Blocks in NAOB Before Extracting Text:**
   - In the NAOB parser, `.decompose()` all quote, example, and citation containers before reading definition text.
   - As an explicit safeguard on any NAOB text string, split and discard anything from `"SITATER"` or `"EKSEMPLER"` onwards:
     `text = re.split(r"\b(?:SITATER|EKSEMPLER)\b", text)[0].strip()`
4. **Strict Source Priority (ODS Before NAOB):**
   - Always query Danish **ODS** (`ordnet.dk/ods`) BEFORE Norwegian **NAOB** (`naob.no`). Only fall back to NAOB if ODS yields no valid definition.
5. **No Mid-Sentence Truncation:**
   - Never truncate `SenseItem.definition` with `...`.

---

## Phase 3: Handle Unfinished DDO Placeholders, ODS Editorial Glosses & Compound Adverbs (`dernedefra` & `uefterrettelig` bugs)

### Problem
1. On `ordnet.dk/ddo`, some headwords (like `dernedefra`) are unfinished placeholders that only say:
   `"Dette ord har foreløbig ingen betydningsangivelse. Se eventuelt: dernede"` (along with `"ORD I NÆRHEDEN: nedenfra, fra neden, hernedefra"`).
2. On `ordnet.dk/ods`, short adverbs or reference entries (like `dernedefra` or `uefterrettelig`) often lack a standalone `.definition` span. Instead, `dernedefra` has an inline editorial gloss inside a parenthesis with a colon `(: fra gaden)` followed by quotes (`Drachm.F.II.74`), or `uefterrettelig` references its root word (`efterrettelig`).

### Fix (`src/ordpresse/sources/ods_scraper.py`, `src/ordpresse/orthography.py`, `src/ordpresse/synthesizer.py`)
1. **Reject DDO Placeholder & Dummy Texts:**
   - Never accept text containing `"ingen betydningsangivelse"`, `"Historisk opslag for"`, `"Mente du:"`, or `"ORD I NÆRHEDEN"` as a definition.
   - If a DDO page contains `"Se eventuelt: <link>"` or `"ORD I NÆRHEDEN"`, extract those linked target words as cross-reference metadata and fall through to ODS and morphological synthesis.
2. **Separate ODS Editorial Glosses `(: ...)` from Quotes (`ods_scraper.py`):**
   - Never dump raw literary quotes or author/volume codes (`Drachm.F.II.74`) directly into `SenseItem.definition`.
   - When an ODS article lacks a standard `.definition` node:
     a) Check for an editorial gloss pattern `r"\(:\s*([^)]+)\)"` (e.g. `(: fra gaden)`).
     b) Check for cross-reference links (`se X`, `jf. X`) or prefix negation (`u-` + root word, e.g. `uefterrettelig` -> `efterrettelig`). If it is a `u-` negated adjective/participle or a pure reference entry without its own prose definition, fetch/lookup the referenced root word (`efterrettelig`) and synthesize the definition.
     c) Place literary quotes strictly inside `SenseItem.quote` (with expanded `quote_author`, e.g. `"H. Drachmann"`), never as the raw definition itself.
3. **Morphological Synthesis for Directional Adverbs & Compounds (`orthography.py`):**
   - Implement `synthesize_compound_entry(word: str, db) -> CanonicalEntry | None`:
   - **Rule A — Directional/Locative Adverbs:** Match pattern `^(der|her)(nede|oppe|inde|ude|ovre|henne|hjemme|borte|omkringe?|under|over)(fra|til|hen|om)?\$`:
     - Look up the base stem (e.g. `dernede`, `hernede`, `derinde`) in local `COR-SEM`.
     - For suffix `-fra` (e.g. `dernedefra`):
       - `pos = "adv."`
       - `definition = "Fra et sted længere nede (eller sydpå) i forhold til udgangspunktet; nedefra det pågældende sted (jf. dernede, nedenfra)."`
     - If ODS also has a quote or editorial gloss (e.g. `»dernedefra (: fra gaden) op til ham« (H. Drachmann)`), attach it as `quote` on the sense!
   - **Rule B — Negated `u-` Adjectives (`uefterrettelig`):**
     - Ensure `uefterrettelig` resolves with a real definition: `"Som man ikke kan rette sig efter eller stole på; upålidelig, ulydig eller genstridig over for påbud."`

---

## Phase 4: Zero-Compromise Triage of `cache.sqlite3` (Local & Server `192.168.50.5`)

We want to salvage existing cached lookups in `cache.sqlite3` where possible **without compromising quality**, and strictly delete any cached rows that cannot be repaired locally with 100% certainty so the user can re-fetch them cleanly.

Create `src/ordpresse/triage.py` (and a CLI command / script `python3 -m ordpresse.triage`) that inspects every row in `cache.sqlite3` (`definitions` table) and classifies each row into one of three buckets:

### Bucket 1: `KEEP` (100% Clean Rows)
Keep the row unchanged ONLY if it passes ALL of these checks:
- Has a non-empty definition text (`len >= 15` chars after stripping HTML tags).
- Does NOT contain any forbidden substrings:
  - `"Historisk opslag for"`
  - `"ingen betydningsangivelse"`
  - `"SITATER"`
  - `"EKSEMPLER"`
  - `"ORD I NÆRHEDEN"`
  - `"Mente du:"`
- Does NOT have glued POS tags (e.g. `[a-zæøå]+(adjektiv|substantiv|verbum|adverbium)` without space).
- Does NOT have a definition that ends with mid-sentence truncation `"..."`.
- Does NOT consist solely of an unparsed bibliographic citation code or raw quote without definition text.

### Bucket 2: `SALVAGE` (100% Lossless Local Repair — Zero Compromise)
If a row fails `KEEP`, attempt deterministic local repair:
1. **Case A — NAOB/ODS `SITATER` or Glued POS Repair:**
   - If the cached entry contains `"SITATER"` or a glued POS tag (`{word}adjektiv`), check if there is a complete, non-truncated definition sentence **before** `"SITATER"`.
   - Strip the glued headword from POS, normalize POS via `normalize_pos()`, insert missing space separators between HTML elements, strip everything from `"SITATER"` onwards, and re-render via `render_canonical_html()`.
   - Verify that the resulting definition is clean, `>= 12` chars, does not end with `"..."`, and contains normal Danish/Norwegian prose. If valid -> **UPDATE row in `cache.sqlite3`** (`SALVAGED`).
2. **Case B — Deterministic Morphological / `nordic_1800s` / Local `COR-SEM` Synthesis:**
   - If the word exists in `nordic_1800s.py` or can be deterministically synthesized via `synthesize_compound_entry(word, db)` using local `COR-SEM` data (such as `dernedefra`, `hernedefra`, `tællelys`, `alderstegen`, `uefterrettelig`), rebuild its HTML via `render_canonical_html()` and **UPDATE row in `cache.sqlite3`** (`SALVAGED`).

### Bucket 3: `PURGE` (Delete Without Hesitation)
- If a row fails `KEEP` and cannot be 100% losslessly repaired in `SALVAGE` (for example: dummy `"Historisk opslag for..."` rows, `"Dette ord har foreløbig ingen betydningsangivelse"`, definitions truncated with `"..."` where the tail is lost, or empty `COR` skeletons with no definition):
  - **DELETE the row** from `cache.sqlite3`.
  - Append the deleted word to `data/purged_words.txt` (one word per line, deduplicated) so the user has an exact list of words that were purged and can re-fetch them.

---

## Phase 5: Automated Tests (`tests/test_quality_and_triage.py`)

Create `tests/test_quality_and_triage.py` and verify with `.venv/bin/pytest -v`:
1. **`test_alderstegen_clean_pos_and_no_sitater`:**
   - Verify that looking up `"alderstegen"` produces normalized POS `"adj."` (never `"alderstegenadjektiv"`), proper word spacing, and zero `"SITATER"` noise.
2. **`test_taellelys_not_empty_cor_skeleton`:**
   - Verify that looking up `"tællelyset"` resolves to `"tællelys"`, has POS `"sb. itk."`, and includes a full definition about tallow/animal fat candles (never a blank `Kilde: COR` card).
3. **`test_dernedefra_no_ddo_placeholder_and_has_meaning`:**
   - Verify that `"dernedefra"` has POS `"adv."`, does NOT contain `"ingen betydningsangivelse"`, and provides a clear explanation (`fra et sted længere nede` / `dernede`).
4. **`test_uefterrettelig_has_real_definition`:**
   - Verify that `"uefterrettelige"` -> `"uefterrettelig"` has a real definition and never `"Historisk opslag for"`.
5. **`test_cache_triage_keep_salvage_purge`:**
   - Create a temporary SQLite cache containing:
     a) 1 clean entry -> assert classified as `KEEP`,
     b) 1 NAOB entry with glued `alderstegenadjektiv` and `SITATER...` -> assert classified as `SALVAGED` and verify the updated HTML is clean,
     c) 1 entry with `"Dette ord har foreløbig ingen betydningsangivelse"` and 1 with `"Historisk opslag for »x«"` -> assert classified as `PURGED` and removed from the DB.

---

## Phase 6: Execute Triage on Local + `192.168.50.5`, Rebuild & Report

1. Run `.venv/bin/pytest -v` locally until all tests pass.
2. Run `triage.py` on local `data/cache.sqlite3`.
3. Commit and push all changes to `origin/main`.
4. Deploy via SSH to `root@192.168.50.5`:
   - Pull latest code and rebuild container (`docker compose up -d --build`).
   - Run `triage.py` inside the container against `/root/ordpresse/data/cache.sqlite3`.
   - Rebuild the full StarDict dictionary on the server (`docker exec ordpresse ordpresse build --full --no-network`).
5. Print a final summary table in your response showing:
   - Total rows in server `cache.sqlite3` before triage
   - Count of **KEEP** rows
   - Count of **SALVAGED** rows (with 3–5 concrete before/after word examples)
   - Count of **PURGED** rows (and the list of purged words saved to `purged_words.txt`)
   - Live `curl` verification against `http://192.168.50.5:18088/api/preview?q=...` for `alderstegen`, `tællelyset`, `dernedefra`, and `uefterrettelige`.