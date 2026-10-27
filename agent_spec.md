# AGENT_SPEC.md — Implementation Specification for `ordpresse` (v2.0)
**Project Title:** Den Danske Ordpresse (`ordpresse`)
**Target Agent:** Gemini 3.8 Flash (Antigravity IDE)
**Language & Runtime:** Python 3.12+, FastAPI, Uvicorn, HTMX + Tailwind (CDN), SQLite3, Docker Compose

> **CRITICAL DIRECTIVES FOR THE AI AGENT (READ FIRST):**
> 1. **Memory Safety (< 250 MB RAM):** `ods-fullforms.tsv` and `COR` contain 3+ million rows combined. NEVER load entire TSV files into Python `dict`/`set` structures in RAM. Stream all TSV datasets into indexed SQLite tables (`data/datasets.sqlite3`) using batched inserts (`executemany` with 10,000 rows per batch).
> 2. **COR vs COR.EXT ID Parsing:** In `COR`, IDs look like `COR.68025.110.01`. In `COR.EXT`, IDs look like `COR.EXT.142887.120.01`. ALWAYS extract the lemma ID using `cor_id.rsplit(".", 2)[0]` — NEVER `split(".")[:2]`.
> 3. **StarDict Binary Sorting Law (`g_ascii_strcasecmp`):** KOReader (`sdcv`) sorts `.idx` and `.syn` by ASCII-only case-folding (`bytes.lower()`), NOT Unicode `str.lower()`. Using `str.lower()` corrupts binary search for Danish `Æ`, `Ø`, `Å`. Follow Section 5.1 strictly.
> 4. **Async Build Pipeline:** Never run builds synchronously inside a FastAPI route handler (prevents 100s HTTP 524 timeouts behind Cloudflare Tunnels). Use `asyncio.create_task` + Server-Sent Events (SSE).

---

## 1. Repository Structure & `pyproject.toml`

Create this exact file tree:

```text
ordpresse/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .gitignore
├── README.md
├── AGENT_SPEC.md
├── src/
│   └── ordpresse/
│       ├── __init__.py
│       ├── config.py           # Paths, URLs, env vars, rate-limits
│       ├── db.py               # SQLite manager for datasets.sqlite3 & cache.sqlite3
│       ├── datasets.py         # Streaming downloader & TSV->SQLite importer for COR & ODS
│       ├── orthography.py      # Pre-1948 normalizer (aa<->å, case, soft-hyphens, suffixes)
│       ├── epub_scanner.py     # In-memory EPUB token extractor (strips \u00ad before regex)
│       ├── scrapers.py         # Polite ODS (all homographs) & NAOB scrapers + offline seeds
│       ├── stardict_writer.py  # Native StarDict v2.4.2 binary compiler (.ifo, .idx, .dict.dz, .syn)
│       ├── pipeline.py         # Orchestrator uniting SQLite datasets, EPUB tokens, scrapers, compiler
│       ├── cli.py              # CLI (`ordpresse fetch-datasets`, `ordpresse build`, `ordpresse serve`)
│       └── web/
│           ├── app.py          # FastAPI app, SSE progress stream, ZIP download, healthcheck
│           └── templates/
│               └── index.html  # Single-page HTMX + Tailwind UI
└── tests/
    ├── test_orthography.py
    ├── test_stardict_writer.py
    └── test_moberg_words.py    # End-to-end regression suite for the 5 Moberg words
```

Use this exact `pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "ordpresse"
version = "0.2.0"
description = "Den Danske Ordpresse — Historisk og moderne dansk StarDict-ordbogsbygger til KOReader"
readme = "README.md"
requires-python = ">=3.12"
license = {text = "MIT"}
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "requests>=2.32.0",
    "beautifulsoup4>=4.12.0",
    "jinja2>=3.1.4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2.0",
    "httpx>=0.27.0",
]

[project.scripts]
ordpresse = "ordpresse.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

---

## 2. Exact Dataset URLs & SQLite Indexing (`datasets.py` & `db.py`)

### 2.1 Official Download URLs (`config.py`)
Configure these primary URLs (with fallback support if the user places `.tsv` or `.zip` files manually in `./data/raw/`):
- `COR_BASE_URL`: `[https://ordregister.dk/files/cor1.5.1.0.tsv](https://ordregister.dk/files/cor1.5.1.0.tsv)` (Fallback: `[https://ordregister.dk/files/cor1.01b.tsv](https://ordregister.dk/files/cor1.01b.tsv)`)
- `COR_EXT_URL`: `[https://ordregister.dk/files/corext1.0.tsv](https://ordregister.dk/files/corext1.0.tsv)`
- `COR_SEM_URL`: `[https://ordregister.dk/files/cor.sem.1.0.tsv](https://ordregister.dk/files/cor.sem.1.0.tsv)`
- `COR_SEM_EXT_URL`: `[https://ordregister.dk/files/cor.sem.ext.1.0.tsv](https://ordregister.dk/files/cor.sem.ext.1.0.tsv)`
- `ODS_FULLFORMS_URL`: `[https://korpus.dsl.dk/resources/files/ods-fullforms.zip](https://korpus.dsl.dk/resources/files/ods-fullforms.zip)` (or manual drop in `./data/raw/`)

### 2.2 Exact TSV Schemas & SQLite Import Rules
Stream each downloaded TSV line-by-line (`csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE)`) into `./data/datasets.sqlite3` using `PRAGMA synchronous = OFF; PRAGMA journal_mode = WAL;`:

1. **`cor1.5.1.0.tsv` & `corext1.0.tsv` (6 columns):**
   - `col[0]`: `cor_id` (e.g., `COR.68025.110.01` or `COR.EXT.142887.120.01`). Extract `lemma_id = col[0].rsplit(".", 2)[0]`.
   - `col[1]`: `lemma` (e.g., `bissekræmmer`)
   - `col[2]`: `gloss`
   - `col[3]`: `gram` (e.g., `sb.fk.sg.ubest` -> extract POS prefix `sb.`, `vb.`, `adj.`)
   - `col[4]`: `fullform` (e.g., `bissekræmmerne`)
   - `col[5]`: `norm_status`

2. **`cor.sem.1.0.tsv` & `cor.sem.ext.1.0.tsv` (Semantic Definitions):**
   - Inspect header row dynamically (or column patterns).
   - `cor.sem.1.0.tsv` maps `sense_id` / `ddo_id` to `cor_lemma_id` (or `lemma` + `pos`).
   - `cor.sem.ext.1.0.tsv` maps `sense_id` / `ddo_id` to the DDO **definition text** and **usage examples**.
   - Join them in SQLite so querying by `lemma` (or `lemma_id`) returns all definitions and examples formatted as `<ol><li>...</li></ol>`.

3. **`ods-fullforms.tsv` (5 columns):**
   - `col[0]`: `inflected_form` (e.g., `Lægdslemmer`, `værkbrudne`, `Jordebøger`, `Aar`)
   - `col[1]`: `headword` (e.g., `Lægdslem`, `værkbrudden`, `Jordebog`, `Aar`)
   - `col[2]`: `homograph_num` (optional integer/string)
   - `col[3]`: `pos` (e.g., `sb.`, `adj.`)
   - `col[4]`: `ods_id` (e.g., `60159262`)
   - Store in SQLite table `ods_forms(inflected_lower TEXT, inflected_raw TEXT, headword TEXT, homograph TEXT, pos TEXT, ods_id TEXT)` with an index on `inflected_lower` and `headword`.

---

## 3. EPUB Scanner & Pre-1948 Orthography Engine (`epub_scanner.py` & `orthography.py`)

### 3.1 Soft-Hyphen & Line-Break Sanitization (`epub_scanner.py`)
Before extracting word tokens from EPUB XHTML files:
1. Strip all Unicode soft hyphens (`\u00ad`), zero-width spaces (`\u200b`, `\ufeff`), and `&shy;` entities:
   `text = text.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")`
2. Rejoin words broken across lines by hyphen + whitespace:
   `text = re.sub(r"([a-zæøåA-ZÆØÅ])-\s+([a-zæøå])", r"\1\2", text)`
3. Extract unique lowercased tokens (`len >= 2`) via `re.findall(r"[a-zæøåA-ZÆØÅ]+(?:-[a-zæøåA-ZÆØÅ]+)*", text)`.

### 3.2 Pre-1948 Orthography & Suffix Resolver (`orthography.py`)
1. **`aa` <-> `å` Bidirectional Mapping:**
   - Given a modern word with `å` (`gårdmand`, `år`), generate the pre-1948 `aa` / `Aa` candidates (`gaardmand`, `Gaardmand`, `aar`, `Aar`).
   - Given an ODS headword or inflection with `aa` / `Aa`, generate the modern `å` synonym for the `.syn` file.
2. **Noun Capitalization & Hyphen Stripping:**
   - ODS capitalizes all nouns (`Jordebog`, `Lægdslem`). Always generate `.lower()` synonyms in `.syn`.
   - Strip internal hyphens as additional `.syn` keys (`Lægds-lem` -> `lægdslem`).
3. **Danish Inflection & Compound Fallback Chain (for unknown EPUB tokens):**
   When an EPUB token `w` is not in modern `COR.SEM.EXT`:
   - **Step A:** Look up `w.lower()` and `aa_variant(w.lower())` in SQLite `ods_forms.inflected_lower`.
   - **Step B:** Strip common Danish suffixes and re-check `ods_forms`:
     - `-lemmer`, `-lemmerne`, `-lemmet` -> `-lem` (resolves `lægdslemmer` -> `lægdslem`)
     - `-brudne` -> `-brudden` (resolves `værkbrudne` -> `værkbrudden`)
     - `-bøger`, `-bøgerne` -> `-bog` (resolves `jordebøger` -> `jordebog`)
     - `-erne`, `-ene`, `-ere`, `-er`, `-en`, `-et`, `-ne`, `-e`, `-s`
   - **Step C:** Dano-Norwegian / Translation Fallback (`lægdslem` -> `legdelem`, `banco` -> `banko`).

---

## 4. Multi-Homograph ODS & NAOB Scrapers (`scrapers.py`)

Store all fetched articles in `./data/cache.sqlite3` (`definitions` table) so no word is ever requested twice.

1. **ODS Scraper (`[https://ordnet.dk/ods/ordbog?query=](https://ordnet.dk/ods/ordbog?query=){word}`):**
   - **Multi-Homograph Rule:** Many ODS words (like `Lægd` or `Bisse`) have multiple homographs (`1. Lægd` = fold in clothing, `2. Lægd` = military/parish district).
   - When fetching `?query={word}`, extract **all** `.artikel` divs on the page. Furthermore, check the sidebar/search result list (`.searchResultBox a`) for sibling homographs matching the exact same headword (e.g., `Lægd,1`, `Lægd,2`) and fetch/merge their `.artikel` blocks so the user gets all homograph meanings in one StarDict lookup.
   - **Sub-entry / Compound Extraction:** If querying a compound (e.g., `lægdslem`) redirects to or is contained inside a parent article (`Lægd`), highlight or extract the compound section alongside the parent definition.
2. **NAOB Fallback (`[https://naob.no/ordbok/](https://naob.no/ordbok/){word}`):**
   - Used when a Dano-Norwegian 19th-century term (such as `lægdslem` / `legdelem`) has a fuller dedicated article in NAOB than in ODS.
3. **Built-in Offline Seed (`SEED_HISTORICAL_ENTRIES`):**
   - Pre-populate `scrapers.py` with rich, authentic HTML definitions and inflection sets for the Moberg benchmark vocabulary (`lægdslem` -> `["lægdslemmer", "lægdslemmet", "lægdslemmerne", "Lægdslem"]`, `jordebog` -> `["Jordebog", "jordebogen", "jordebøger", "jordebøgerne"]`, `bissekræmmer` -> `["Bissekræmmer", "bissekræmmere", "bissekræmmeren", "bissekræmmerne"]`, `værkbrudden` -> `["værkbrudne", "værkbruddent", "Værkbrudden"]`, `banco` -> `["banko", "Banko", "rigsdaler banco", "riksdaler banco"]`) so unit tests and offline builds always succeed with 0 network dependency.

---

## 5. Pure-Python StarDict v2.4.2 Binary Compiler (`stardict_writer.py`)

### 5.1 The `g_ascii_strcasecmp` Sorting Law (MANDATORY)
KOReader (`sdcv`) binary-searches `.idx` and `.syn` using GLib's `g_ascii_strcasecmp`, which ONLY lowercases ASCII bytes `0x41..0x5A` (`A-Z`) and compares all UTF-8 bytes `>= 0x80` (`Æ`, `Ø`, `Å`) as raw unsigned bytes.
You MUST use this exact sort key for both `.idx` and `.syn`:
```python
def stardict_sort_key(word: str) -> tuple[bytes, bytes]:
    raw_bytes = word.encode("utf-8")
    # bytes.lower() in Python ONLY folds ASCII A-Z (0x41..0x5A -> 0x61..0x7A),
    # matching GLib's g_ascii_strcasecmp byte-for-byte!
    return (raw_bytes.lower(), raw_bytes)
```
**NEVER** use `word.lower().encode("utf-8")` — doing so breaks binary search for `Æ`, `Ø`, `Å` in KOReader!

### 5.2 Binary File Layout
1. **`.dict` / `.dict.dz`:** UTF-8 HTML payloads concatenated back-to-back (no trailing null byte). Compress with `dictzip -f <bookname>.dict` if `shutil.which("dictzip")` is present.
2. **`.idx`:** Sorted by `stardict_sort_key(headword)`. Each record:
   `headword.encode("utf-8") + b"\x00" + struct.pack(">II", offset, size)`
3. **`.syn`:** Collect all unique synonyms/inflections for entry index `i` (`0 <= i < len(entries)`), excluding the exact `headword` string itself. Sort all `(synonym, i)` pairs by `stardict_sort_key(synonym)`. Each record:
   `synonym.encode("utf-8") + b"\x00" + struct.pack(">I", i)`
4. **`.ifo`:**
   ```ini
   StarDict's dict ifo file
   version=2.4.2
   wordcount={len(entries)}
   synwordcount={len(synonyms)}
   idxfilesize={idx_size_bytes}
   bookname=Den Danske Ordpresse (1700-nu)
   author=DSL / CST / Den Danske Ordpresse (ordpresse)
   description=Historisk og moderne dansk ordbog presset med ordpresse
   date={YYYY.MM.DD}
   sametypesequence=h
   ```

---

## 6. Required `pytest` Verification Suite (`tests/`)

1. **`tests/test_stardict_writer.py`:**
   - Must test `stardict_sort_key` on `["Aabenraa", "Ålborg", "Ægir", "Ørsted", "ålborg", "ægir", "ørsted"]` and assert that `Ægir` (`0xC3 0x86`) sorts BEFORE `Ørsted` (`0xC3 0x98`), which sorts BEFORE `ægir` (`0xC3 0xA6`).
   - Must implement a binary search reader in the test that simulates KOReader's `.idx` and `.syn` lookup and verifies exact byte offsets.
2. **`tests/test_orthography.py`:**
   - Must verify soft-hyphen stripping (`"lægds\u00adlemmer"` -> `"lægdslemmer"`), `aa` <-> `å` conversion, and suffix resolution (`"værkbrudne"` -> `"værkbrudden"`, `"lægdslemmer"` -> `"lægdslem"`).
3. **`tests/test_moberg_words.py`:**
   - Must run the full pipeline on a simulated EPUB containing the 5 Moberg words (`lægdslemmer`, `jordebog`, `bissekræmmer`, `værkbrudne`, `banco`), compile the StarDict files, and assert that looking up each of the 5 surface strings in `.idx`/`.syn` returns the expected Danish historical definition.