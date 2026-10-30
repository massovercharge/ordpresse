# AGENT_SPEC_V5_LEXICAL_INTEGRITY.md — Priority Scrape Queue, True Inflections, Full DDO De-Truncation & Zero-Compromise Triage
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5` (via SSH from Antigravity IDE)
**Public Host:** `https://ordpresse.seame.click`
**Target Agent:** Gemini 3.8 Flash

> **CRITICAL ARCHITECTURAL RULES (DO NOT VIOLATE):**
> 1. **STRICT 3-TIER QUEUE PRIORITY:** Completely missing definitions (**Priority 1**) MUST ALWAYS be fetched before truncated `COR-SEM` articles (**Priority 2**). Truncated articles that already convey clear context (**Priority 3**) must NOT block or enter the automatic EPUB build queue at all.
> 2. **GRACEFUL FALLBACK FOR TRUNCATED ARTICLES:** If a Priority 2 or 3 word (`COR-SEM` entry with `...`) has not yet been upgraded from `ordnet.dk/ddo` (e.g. `--no-network`, rate limit reached, or user clicks "Skip queue"), **keep its existing `COR-SEM` definition intact**! Never discard a `COR-SEM` entry just because its upgrade hasn't been fetched yet.
> 3. **NO COSMETIC REGEX CHOPPING OF `...` DEFINITIONS:** Never hide a truncated `COR-SEM` definition (`...`) by blindly deleting `...` or chopping off the trailing word. Only replace it when a full definition from `ordnet.dk/ddo` or `ordnet.dk/ods` is available.
> 4. **NO SYNTHETIC INFLECTION GUESSING:** Never invent inflected forms in Python (`stakkelsere`, `stakkelsest`, or attaching `grundede` to `grund sb.`). Rely strictly on verified lexical forms from `COR`, `ODS fuldformsleksikon`, and curated entries.

---

## Phase 1: 3-Tier Lookup Priority System (`rate_limiter.py`, `pipeline.py`, `orthography.py`)

When scanning EPUB files or user-supplied word lists in `pipeline.py`, classify every candidate word that needs external enrichment into one of 3 priority tiers:

```python
import re

# Cliffhanger endings where truncation breaks the core meaning of the sentence:
CLIFFHANGER_ELLIPSIS_RE = re.compile(
    r"\b(?:et|en|den|det|de|at|i|på|med|til|for|af|om|over|under|ved|fra|"
    r"som|der|eller|el\.|dvs\.|fx|f\.eks\.|bl\.a\.|fast|så|ikke|kan|skal|må)\s*(?:\.\.\.|…)\s*$",
    re.IGNORECASE,
)

def classify_external_lookup_priority(entry: CanonicalEntry | None) -> int | None:
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
```

### Queue Ordering & Execution Rules in `pipeline.py` & `ScrapeCoordinator`
1. **Queue Registration (`coordinator.register_build`):**
   - Collect all **Priority 1** words (`target="ods"` / `"ddo"`) and sort them **first** in the build's lookup queue.
   - Append **Priority 2** words (`target="ddo"`) **after** all Priority 1 words.
   - **Exclude Priority 3 words** from the automatic EPUB build scrape queue so builds stay fast and focused on words that actually lack usable meaning.
2. **Progress Reporting in Web UI (SSE):**
   - Clearly distinguish the two tiers in the progress message so the user knows why lookups are happening:
     - While in Priority 1: `"Henter manglende ord {i}/{p1_total} ({word}) [Prio 1 — mangler helt]..."`
     - While in Priority 2: `"Fuldender afkortet definition {j}/{p2_total} ({word}) [Prio 2 — kan springes over]..."`
3. **Graceful Fallback on Skip / Offline:**
   - If `allow_network=False` or the user clicks **"Afslut byg nu uden resterende kø"** (`/api/build/{build_id}/skip-queue`):
     - Any un-fetched **Priority 1** word with 0 definitions is omitted from `.idx` (never write empty `COR` skeletons like `tællelys sb.` with 0 definitions!).
     - Any un-fetched **Priority 2** or **Priority 3** word retains its existing `COR-SEM` `modern_senses` and is compiled into the StarDict dictionary normally.

---

## Phase 2: Eliminate Fake Synthetic Inflections, Homograph Collisions & Header Noise

### 2.1 Remove Synthetic Inflection Generators & Fix `grund` vs. `grunde` (`orthography.py`, `db.py`)
1. Inspect `src/ordpresse/orthography.py`, `src/ordpresse/db.py`, and `src/ordpresse/datasets.py` to find where fake forms like `stakkelsere`, `stakkelsest`, `stakkelseste` (and `grundede` under `grund sb.`) originate.
2. Remove any synthetic suffix generator that blindly appends `-ere`, `-est`, `-este`, `-ede`, `-te` to words.
3. **Strict POS / Lemma-ID Joining for `COR` and `COR-SEM`:**
   - In `db.py`, ensure `COR` fullforms are joined to lemmas strictly by their `COR` lemma ID or `(lemma, pos)` pair — NEVER by raw string stem alone!
   - Verbal inflections (`grunder`, `grundede`, `grundet`) belong strictly to `grunde` (`vb.`), never to `grund` (`sb.`).
   - Ensure homographs and multi-POS lemmas in `COR-SEM` (such as `grund sb.` and `grunde vb.`, including both homographs of `grunde vb.`: *"tænke dybt over noget; gruble"* and *"lægge grunden til; basere"*) are all preserved and never overwrite each other via naive dictionary assignment.

### 2.2 Strip Hyphenated Stems (`grund-`, `søskende-`) & Clean Display Inflections (`formatter.py`)
1. In `db.py` and `stardict_writer.py`, filter out any inflection form starting or ending with a hyphen (`f.startswith("-") or f.endswith("-")`, such as `grund-`, `søskende-`).
2. In `formatter.py`, replace the naive alphabetical slice `sorted(...)[:3]` with `select_display_inflections(headword, inflections, limit=3)` that deprioritizes genitives (`-ens`, `-ets`, `-enes`, `-ernes`) and sorts by `(is_capitalized_variant, is_genitive, len(form), form.lower())` so `prioritetslån` displays `(prioritetslånet, prioritetslånene)` instead of `(prioritetslånene, prioritetslånenes, prioritetslånet)`.
3. In `formatter.py` and `db.py`:
   - Split multi-quote fields on `|` and keep only the first quote: `quote = raw_quote.split("|")[0].strip()`.
   - Never use the `▸` (`\u25b8`) character (which renders as `` on CrossPoint/ESP32 fonts).
   - Never auto-generate a `summary_hook` by concatenating `modern_senses[0]` and `modern_senses[1]`. Only render `summary_hook` when a distinct historical/curated summary exists.

---

## Phase 3: Fix HTML Text Glue, `SITATER` Noise, DDO Placeholders & Compound Adverbs

1. **Always Use Space Separator in BeautifulSoup (`scrapers.py`, `ods_scraper.py`, `ddo_scraper.py`):**
   - Replace every `.get_text()` / `.get_text(strip=True)` with `.get_text(" ", strip=True)` followed by `re.sub(r"\s+", " ", text).strip()`.
2. **NAOB & ODS Cleanup (`alderstegen` bug):**
   - Strip the headword from extracted POS strings (`alderstegenadjektiv` -> `adjektiv` -> `normalize_pos()` -> `adj.`).
   - Decompose quote/citation blocks in NAOB and explicitly strip everything from `"SITATER"` or `"EKSEMPLER"` onwards: `text = re.split(r"\b(?:SITATER|EKSEMPLER)\b", text)[0].strip()`.
   - Always query Danish **ODS** (`ordnet.dk/ods`) before falling back to Norwegian **NAOB** (`naob.no`).
3. **Create `src/ordpresse/sources/ddo_scraper.py` for Priority 2 Upgrades (`ordnet.dk/ddo`):**
   - Fetch `https://ordnet.dk/ddo/ordbog?query={word}`.
   - Reject unfinished DDO placeholder pages containing `"Dette ord har foreløbig ingen betydningsangivelse"`.
   - Extract complete, untruncated definitions (`.definitionBox` / `.definition`) and at most 1 clean example quote per sense.
   - When an upgraded DDO definition is fetched (via server or Bookmarklet `POST /api/cache/submit`), save it in `cache.sqlite3` AND update the truncated `cor_sem` row in `datasets.sqlite3` so it is permanently cured!
   - Update `GET /api/build/{build_id}/missing-words` and the Bookmarklet in `index.html` so each queued item carries `"target": "ods"` (Priority 1 historical) or `"target": "ddo"` (Priority 2 truncated modern), and the Bookmarklet fetches `/ods/ordbog?query=...` or `/ddo/ordbog?query=...` accordingly in strict Priority 1 -> Priority 2 order.
4. **Morphological Synthesis & Curated Additions (`orthography.py`, `nordic_1800s.py`):**
   - Implement `synthesize_compound_entry(word: str, db)` in `orthography.py` for directional adverbs matching `^(der|her)(nede|oppe|inde|ude|ovre|henne|hjemme|borte)(fra|til|hen|om)?\$` (e.g. `dernedefra` -> `adv.`: `"Fra et sted længere nede (eller sydpå) i forhold til udgangspunktet; nedefra det pågældende sted (jf. dernede, nedenfra)."`).
   - Add/verify complete entries in `nordic_1800s.py` / `datasets.sqlite3` for:
     - `tællelys` (`sb. itk.`): `"Lys støbt eller dyppet af smeltet dyrefedt (tælle/talg fra kvæg eller får); datidens almindelige belysning i bondehjem før stearin- og parafinlys."`
     - `alderstegen` (`adj.`): `"Højt oppe i årene; ældgammel; svækket eller mærket af høj alder."`
     - `uefterrettelig` (`adj.`): `"Som man ikke kan rette sig efter eller stole på; upålidelig, ulydig eller genstridig over for øvrighedens påbud."`
   - Permanently update the benchmark `cor_sem` rows in `datasets.sqlite3` for `stakkels`, `prioritetslån`, `nybegynder`, `søskende`, `grund`, and `grunde` with their complete untruncated DDO definitions.

---

## Phase 4: Zero-Compromise Triage of `cache.sqlite3` (`src/ordpresse/triage.py`)

Create `src/ordpresse/triage.py` and run it against `data/cache.sqlite3` (both locally and on `192.168.50.5`):
1. **`KEEP`:** Rows with clean definition text (`len >= 15`), no forbidden substrings (`"Historisk opslag for"`, `"ingen betydningsangivelse"`, `"SITATER"`, `"EKSEMPLER"`, `"ORD I NÆRHEDEN"`, `"Mente du:"`), no glued POS (`[a-zæøå]+(adjektiv|substantiv|verbum|adverbium)`), and not ending with `"..."`.
2. **`SALVAGE` (100% Lossless Local Repair):**
   - Rows with `"SITATER"` or glued POS where a complete, non-truncated definition exists *before* `"SITATER"`: strip `"SITATER"` tail, normalize POS, fix spacing, and re-render via `render_canonical_html()`.
   - Words solvable deterministically via `nordic_1800s.py` or `synthesize_compound_entry(word, db)` (`dernedefra`, `tællelys`, `alderstegen`, `uefterrettelig`): re-render and update in `cache.sqlite3`.
3. **`PURGE` (Delete Without Hesitation):**
   - Any row failing `KEEP` that cannot be 100% losslessly repaired in `SALVAGE` (dummy placeholders, `"ingen betydningsangivelse"`, empty skeletons, or raw quote noise without definition).
   - Delete from `cache.sqlite3` and write the purged headwords to `data/purged_words.txt`.

---

## Phase 5: Automated Tests & Rollout to `192.168.50.5`

Create `tests/test_lexical_integrity.py` and verify with `.venv/bin/pytest -v`:
1. **`test_priority_classification_tiers`:**
   - Assert an empty `COR` skeleton (`tællelys` with 0 senses) returns **Priority 1**.
   - Assert a cliffhanger truncated entry (`"lån der ydes mod panteret i fast..."` or `"dvs. brødre..."`) returns **Priority 2**.
   - Assert a non-cliffhanger comma-list truncation (`"som vækker medlidenhed pga. sin svaghed, hjælpeløshed..."`) returns **Priority 3**.
   - Assert a complete entry returns `None`.
2. **`test_queue_orders_priority_1_before_priority_2`:**
   - Verify that when registering a build with mixed Priority 1 and Priority 2 words, `GET /api/build/{build_id}/missing-words` lists all Priority 1 (`ods`) words before any Priority 2 (`ddo`) words, and excludes Priority 3 words.
3. **`test_stakkels_grundede_taellelys_dernedefra_uefterrettelig`:**
   - Verify via `StarDictReader` on a compiled build that:
     - `"stakkels"` has no `stakkelsere`/`stakkelsest` and no `"..."`.
     - `"grundede"` resolves to `"grunde"` (`vb.`, thinking/pondering), never `"grund"` (`sb.`).
     - `"tællelyset"` resolves to `"tællelys"` (`sb. itk.`) with a real definition.
     - `"dernedefra"` has POS `"adv."` and a clear directional definition.
     - `"uefterrettelige"` resolves to `"uefterrettelig"` with a real definition.
     - `"alderstegen"` has POS `"adj."` with proper spacing and zero `"SITATER"` noise.
     - No quote contains `"|"` and no HTML contains `"▸"` or hyphenated stems (`"grund-"`, `"søskende-"`).

### Deployment Steps
1. Run `.venv/bin/pytest -v` locally until 100% pass.
2. Run `python3 -m ordpresse.triage` locally, commit, and push to `origin/main`.
3. SSH to `root@192.168.50.5`, pull, rebuild container (`docker compose up -d --build`), run `triage` on `/root/ordpresse/data/cache.sqlite3`, apply the `datasets.sqlite3` benchmark repairs, and run a full dictionary build (`docker exec ordpresse ordpresse build --full --no-network`).
4. Report the triage numbers (`KEEP` / `SALVAGED` / `PURGED`) and live `curl` preview results from `192.168.50.5:18088`.