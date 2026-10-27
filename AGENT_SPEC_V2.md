# AGENT_SPEC_V2.md — Multi-Source Expansion & Canonical E-Ink Spec for `ordpresse`
**Target Project:** `ordpresse` (Den Danske Ordpresse)
**Target Server:** `192.168.50.5` (via SSH from Antigravity IDE)
**Baseline v1 State:** 33,484 headwords and ~480,000 synonyms compiled from `COR` / `COR-SEM` (`ordregister.dk`), `ODS` + `ODS fuldformsleksikon` (`korpus.dsl.dk`), and `NAOB` (`naob.no`).

---

## 1. Core Objectives of v2
1. **Expand Headword Coverage Beyond the 33,484 Modern `COR-SEM` Lemmas:**
   - Currently, `ODS fuldformsleksikon` (~225,000 lemmas / ~3M inflected forms) is underutilized when a lemma lacks a definition in `COR-SEM`.
   - In v2, promote historical lemmas into standalone `.idx` headwords (and enrich "false friend" modern headwords like *lægd*, *fæste*, *skilling*, *kram*) by integrating open datasets under the **DSL Open Licence** (`korpus.dsl.dk`), **CC0** (`ordregister.dk`), and **Public Domain** (Chr. Molbech 1859 / Salmonsens / Curated 1800s Nordic Lexicon).
2. **Enforce a Single Streamlined E-Ink Presentation (`CanonicalEntry`):**
   - Regardless of whether an entry comes from `COR-SEM`, `ODS`, `Moths Ordbog`, `Molbech (1859)`, or `NAOB`, normalize all grammar tags, strip bibliographic sigils (`Moth.L21`, `ChrV.3-19-1`), and render through one unified 3-layer KOReader template.
3. **Preserve Legal Attribution & Non-Endorsement Notice:**
   - Embed the exact DSL, COR/CST, Public Domain, and NAOB attribution and non-endorsement notice in the Web UI footer, `README.md`, and StarDict `.ifo` description.

---

## 2. Source Hierarchy & Role Assignment (`src/ordpresse/sources/`)

Instead of dumping raw text from multiple dictionaries, assign each source a strict role inside `CanonicalEntry`:

| Priority | Source | License | Role in `CanonicalEntry` |
|---|---|---|---|
| **1. Summary Hook** | **Molbech (1859) / Curated 1800s / First Clean Clause** | Public Domain / DSL Open | **Layer 1 (`summary_hook`):** Crisp 1-sentence explanation (`<= 165` chars) readable in 2 seconds on Kindle without scrolling. |
| **2. Historical Senses** | **ODS (`ordnet.dk/ods` & `ods-fullforms`) + Moths/Kalkar (`korpus.dsl.dk`)** | [DSL Open Licence](https://korpus.dsl.dk/resources/licences/dsl-open.html) | **Layer 2A (`historical_senses`):** Up to 4 numbered historical meanings (1700–1950) + max 1 cleaned literary quote per sense. |
| **3. Modern Senses** | **COR + COR-SEM + COR.SOEGEHJAELP (`ordregister.dk`)** | [CC0 1.0 Public Domain](https://creativecommons.org/publicdomain/zero/1.0/) | **Layer 2B (`modern_senses`):** Up to 3 modern Danish definitions (1950–now) + full modern inflectional forms and spelling variants. |
| **4. Nordic & Realia** | **NAOB (`naob.no`) + Salmonsens (2nd ed.)** | Supplemental Lookup / Public Domain | **Layer 3 (`realia_note`):** Explains shared Dano-Norwegian / Swedish 1800s parish, legal, and currency terms (e.g., *lægdslem/rotehjon*, *banco*, *husforhør*, *rustholder*). |

---

## 3. Unified Data Model & Formatter (`models.py` & `formatter.py`)

### 3.1 `CanonicalEntry` Dataclass (`src/ordpresse/models.py`)
```python
from dataclasses import dataclass, field

@dataclass
class SenseItem:
    definition: str               # Clean prose definition (no bibliographic codes)
    label: str | None = None      # e.g. "hist.", "landbo.", "foræld.", "jura", "møntvæsen"
    quote: str | None = None      # Max 1 clean literary quote per sense
    quote_author: str | None = None # Full author name, e.g. "St. St. Blicher", "H. Pontoppidan"
    source_tag: str = "COR-SEM"   # "COR-SEM", "ODS", "Moth", "Molbech", "NAOB"

@dataclass
class CanonicalEntry:
    headword: str                 # Always lowercase canonical key for .idx, e.g. "lægdslem"
    historical_spellings: list[str] = field(default_factory=list) # e.g. ["Lægdslem", "Lejdslem"]
    pos: str = ""                 # Normalized: "sb. fk.", "sb. itk.", "adj.", "vb.", "adv."
    inflections: set[str] = field(default_factory=set)            # Written to .syn (excluding headword)
    summary_hook: str = ""        # Layer 1: 1-sentence TL;DR hook (<= 165 chars)
    historical_senses: list[SenseItem] = field(default_factory=list) # Layer 2A: Max 4 items
    modern_senses: list[SenseItem] = field(default_factory=list)     # Layer 2B: Max 3 items
    realia_note: str | None = None # Layer 3: Encyclopedic context (currencies, parish law)
    sources_used: list[str] = field(default_factory=list)
```

### 3.2 Grammar POS Normalizer (`normalize_pos`)
Normalize all POS tags from `COR`, `ODS fuldformsleksikon`, `Moth`, `Molbech`, and `NAOB`:
- `"sb.fk"`, `"en"`, `"en."`, `"n. s. c."`, `"fælleskøn"` $\rightarrow$ `"sb. fk."`
- `"sb.itk"`, `"et"`, `"et."`, `"n. s. n."`, `"intetkøn"` $\rightarrow$ `"sb. itk."`
- `"sb"`, `"sb."`, `"substantiv"`, `"navneord"` $\rightarrow$ `"sb."`
- `"adj"`, `"adj."`, `"adject."`, `"tillægsord"`, `"ubøjel. adj."` $\rightarrow$ `"adj."`
- `"vb"`, `"vb."`, `"v."`, `"verb."`, `"udsagnsord"` $\rightarrow$ `"vb."`
- `"adv"`, `"adv."`, `"biord"` $\rightarrow$ `"adv."`

### 3.3 Canonical E-Ink HTML Renderer (`src/ordpresse/formatter.py`)
Every entry in the compiled `.dict.dz` MUST be rendered by `render_canonical_html(entry: CanonicalEntry) -> str`:

```python
def render_canonical_html(entry: CanonicalEntry) -> str:
    meta_parts = []
    sample_forms = sorted(f for f in entry.inflections if f.lower() != entry.headword.lower())[:3]
    if sample_forms:
        meta_parts.append(", ".join(sample_forms))
    if entry.historical_spellings:
        meta_parts.append("ældre: " + ", ".join(entry.historical_spellings[:2]))
    meta_str = f" <small>({'; '.join(meta_parts)})</small>" if meta_parts else ""

    pos_str = f" <i>{entry.pos}</i>" if entry.pos else ""
    parts = [f"<div><b>{entry.headword}</b>{pos_str}{meta_str}</div>"]

    # Layer 1: Immediate 2-second TL;DR Hook
    if entry.summary_hook:
        parts.append(
            f'<div style="margin:3px 0;padding:2px 6px;border-left:2px solid #555;">'
            f"<b>▸</b> {entry.summary_hook}</div>"
        )

    # Layer 2A: Historical Senses (1700–1950)
    if entry.historical_senses:
        parts.append('<div><small><b>HISTORISK BETYDNING (ODS / 1700–1950)</b></small><ol>')
        for s in entry.historical_senses[:4]:
            lbl = f"<i>({s.label})</i> " if s.label else ""
            q_auth = f" ({s.quote_author})" if s.quote_author else ""
            q = f'<br><small><i>»{s.quote}«</i>{q_auth}</small>' if s.quote else ""
            parts.append(f"<li>{lbl}{s.definition}{q}</li>")
        parts.append("</ol></div>")

    # Layer 2B: Modern Senses (COR-SEM)
    if entry.modern_senses:
        hdr = '<small><b>NUTIDSDANSK (COR-SEM)</b></small>' if entry.historical_senses else ""
        parts.append(f"<div>{hdr}<ol>")
        for s in entry.modern_senses[:3]:
            q = f'<br><small><i>»{s.quote}«</i></small>' if s.quote else ""
            parts.append(f"<li>{s.definition}{q}</li>")
        parts.append("</ol></div>")

    # Layer 3: Cultural & Historical Realia Note
    if entry.realia_note:
        parts.append(
            f'<div style="margin-top:3px;"><small><b>[Historisk kontekst]:</b> {entry.realia_note}</small></div>'
        )

    # Footer: Compact Source Attribution
    if entry.sources_used:
        unique_srcs = " · ".join(dict.fromkeys(entry.sources_used))
        parts.append(f'<div><small style="color:#666;">Kilde: {unique_srcs}</small></div>')

    return "".join(parts)
```

---

## 4. Deep ODS Integration, Sanitization & Curated 1800s Core

### 4.1 Bridging `ODS fuldformsleksikon` (`ods-fullforms`) to Definitions
1. Ensure all rows from `ODS fuldformsleksikon` are indexed in SQLite (`ods_forms` table indexed on `inflected_lower` and `headword_lower`).
2. **Pre-1948 Orthography & Suffix Resolver:**
   - Map `aa` $\leftrightarrow$ `å`, lowercase all ODS capitalized nouns (`Jordebog` $\rightarrow$ `jordebog` in `.idx`, keeping `Jordebog` in `.syn`), and strip soft hyphens (`\u00ad`) from EPUB tokens.
   - When an EPUB token (e.g. `lægdslemmer`, `værkbrudne`, `jordebøger`) is looked up in `ods_forms`, attach **all** sibling inflected forms of that `ods_id` / `headword` from `ODS fuldformsleksikon` into `entry.inflections`.

### 4.2 Multi-Homograph & Citation Sanitizer for `ordnet.dk/ods`
1. When fetching `[https://ordnet.dk/ods/ordbog?query=](https://ordnet.dk/ods/ordbog?query=){word}`, also inspect `.searchResultBox a` for sibling homographs (`1. Lægd`, `2. Lægd`) and merge them so `2. Lægd` (parish/military district) is never lost behind `1. Lægd` (fold in cloth).
2. Decompose `.udtale`, `.henvisning`, and `.kilde` DOM nodes, and strip inline bibliographic citations (`Moth.L21`, `VSO`, `Molb.`, `Feilb.`, `ChrV.3-19-1`, Roman volume/page numbers) via regex while expanding author abbreviations (`Blich.` $\rightarrow$ `St. St. Blicher`, `Pont.` $\rightarrow$ `H. Pontoppidan`, `Holb.` $\rightarrow$ `L. Holberg`, `Grundtv.` $\rightarrow$ `N.F.S. Grundtvig`).

### 4.3 Built-in 1800s & Moberg Core Module (`src/ordpresse/sources/nordic_1800s.py`)
Include a pre-populated dictionary of at least **35 cornerstone 19th-century & Moberg terms** (`lægdslem`, `jordebog`, `bissekræmmer`, `værkbrudden`, `banco`, `rigsgæld`, `skilling`, `rigsdaler`, `lægd`, `husforhør`, `rustholder`, `aftægt`, `undentag`, `fæstebonde`, `fæste`, `hoveri`, `tiende`, `hartkorn`, `landgilde`, `sognefoged`, `herredsfoged`, `lænsmand`, `fattigauktion`, `skudsmålsbog`, `almisselem`, `husmand`, `indsidder`, `boelsmand`, `karlekammer`, `aftægtsstue`, `kram`, `købstad`, `sogn`, `udvandrer`, `amerika-feber`).
- **False-Friend Enrichment Rule:** If a word in `nordic_1800s.py` (or an EPUB-triggered ODS lookup) ALREADY exists among the 33,484 `COR-SEM` entries (e.g., `bissekræmmer`, `lægd`, `fæste`, `skilling`), **enrich** the existing `CanonicalEntry` by adding `historical_senses`, `realia_note`, `summary_hook`, and unioning all `ODS fuldformsleksikon` inflections — never discard the existing modern senses or let modern senses hide the 1800s meaning!

---

## 5. Mandatory Legal Attribution Block (UI Footer, `.ifo`, and `README.md`)

Display this exact attribution section in the Web App footer (`index.html`) and `README.md`:

1. **Det Danske Sprog- og Litteraturselskab (DSL):**
   - Data fra **Ordbog over det danske Sprog (ODS)** samt **ODS fuldformsleksikon** (udarbejdet af Thomas Troelsgård, DSL) benyttes i overensstemmelse med [DSL's åbne sprogressourcer](https://korpus.dsl.dk/resources/licences/dsl-open.html). Besøg [dsl.dk](https://dsl.dk) og [ordnet.dk/ods](https://ordnet.dk/ods).
   - *Henstilling:* DSL krediteres i overensstemmelse med licensen. DSL støtter eller godkender ikke dette tredjepartsværktøj eller dets anvendelse.
2. **Det Centrale Ordregister (COR & COR-SEM):**
   - Udviklet og udgivet af **Det Danske Sprog- og Litteraturselskab (DSL)** og **Center for Sprogteknologi (CST, Københavns Universitet)**. Distribueret via [ordregister.dk](https://ordregister.dk) under **[Creative Commons CC0 1.0 Universal (Public Domain Dedication)](https://creativecommons.org/publicdomain/zero/1.0/)**.
3. **Det Norske Akademi for Språk og Litteratur (NAOB) & Public Domain-kilder:**
   - Supplerende referencer for fælles dansk-norske og nordiske 1800-talstermer fra [naob.no](https://naob.no), Chr. Molbechs *Dansk Ordbog* (1859) og *Salmonsens Konversationsleksikon* (2. udg., Project Runeberg).

---

## 6. Verification & Rollout to `192.168.50.5`
1. Implement a pure-Python `stardict_reader.py` performing true `g_ascii_strcasecmp` binary search on `.idx` and `.syn`.
2. Add `GET /api/preview?q={word}` and an interactive **"Afprøv opslag (KOReader E-Ink Preview)"** card on `index.html` with clickable chips (`lægdslemmer`, `jordebog`, `bissekræmmer`, `værkbrudne`, `banco`, `husforhør`, `rustholder`, `lægd`).
3. Run `pytest` locally, deploy via SSH to `192.168.50.5`, rebuild the Docker container and default dictionary bundle, and verify `[http://192.168.50.5:8000/api/preview?q=lægdslemmer](http://192.168.50.5:8000/api/preview?q=lægdslemmer)`.