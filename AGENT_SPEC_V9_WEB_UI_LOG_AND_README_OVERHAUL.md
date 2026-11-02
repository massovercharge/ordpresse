# AGENT_SPEC_V9_WEB_UI_LOG_AND_README_OVERHAUL.md — Pedagogical Build Log, Intuitive Web UI & README Sync
**Target Project:** `ordpresse` (Den Danske Ordpresse — `ordpresse.seame.click`)
**Target Server:** `192.168.50.5`
**Target Agent:** Gemini 3.8 Flash

## Context & Objectives
Based on a complete review of the live Web UI, the build log telemetry, and the V8.1 dictionary build (`wordcount=36963`, `synwordcount=121856`), implement the following four improvements:
1. **Upgrade the Live Build Log & Telemetry Funnel:** Preserve the terminal build log and its rich numerical metrics, but make the messages much more pedagogical, explain the funnel math clearly, add live summary counter cards above the terminal, and stop spamming duplicate polling lines.
2. **Web UI/UX Overhaul (`ordpresse.seame.click`):** Add a 1-click download for the pre-built complete dictionary in the Hero section, implement clean progressive disclosure in the builder form, and place the Bookmarklet instructions in context.
3. **Sync `README.md`:** Replace the outdated `lægdslem` article example and old stats in `README.md` with the exact 3-layer canonical `lægdslem` output from the Web UI and V8.1 metrics. (**Do NOT modify `lægdslem` in the backend** — its current 3-layer Web UI rendering is perfect).
4. **Minor ODS Tail Polish (`overstrø`):** Suppress identical headword parentheses like `overstrø vb. (overstrø)` and strip dangling `; især i forb. m. præp.` tails on single-layer ODS entries.

---

## PART 1: Pedagogical Build Log & Live Telemetry Funnel

The user loves following the build log and seeing concrete numbers, but current messages like:
`[20.48.29] SQLite-matching fuldført: 2251 knyttet til COR, 725 knyttet til ODS, 3422 nye historiske opslag fundet (5055 egennavne/uindekserede sprunget over).`
`[20.48.50] Klar til berigelse af op til 2701 prioriterede ord.`
`[20.48.50] Venter på Lyn-Henter – kør Bookmarklet på ordnet.dk eller klik 'Start server-kø'!`
`[20.48.56] Venter på Lyn-Henter – kør Bookmarklet på ordnet.dk eller klik 'Start server-kø'!`
use internal database jargon, don't explain how `3.422` becomes `2.701`, and spam duplicate waiting lines.

### Task 1.1: Add a 4-Stage Visual Funnel Bar Above the Terminal Log
Inside the `"Bygger ordbog..."` card, right above the dark terminal log box, render 4 compact live telemetry stat-cards that update as the build progresses:
1. **📚 Scannede bøger & ord:** Shows book count and unique word forms (e.g., `8 bøger · 13.164 unikke ordformer`).
2. **⚡ Genkendt i lokal base:** Shows words already covered by local COR/ODS cache (e.g., `2.976 ord allerede i lokal ordbog`).
3. **🕰️ Nye historiske ord:** Shows uncached archaic/historical words discovered in the EPUBs (e.g., `2.701 ord kan beriges fra ODS`).
4. **📦 Færdig StarDict-pakke:** Shows final `.idx` headwords and `.syn` inflection forms when compilation finishes.

### Task 1.2: Rewrite Build Log Messages to Be Pedagogical, Clear & Number-Rich
Format numbers with Danish thousand-separators (e.g., `13.164` instead of `13164`) and structure the log output into clear, explanatory steps:
- **Step 1 (EPUB Scanning):**
  - `[HH:MM:SS] 📖 Scannet EPUB #6 ("..."): Fandt 10.695 unikke ordformer.`
  - `[HH:MM:SS] 📊 EPUB-scanning fuldført: Fandt i alt 13.164 unikke ordformer på tværs af bøgerne (efter frasortering af almindelige småord og tal).`
- **Step 2 (Morphology & Local Cache Matching — explain the buckets clearly!):**
  - Instead of `"SQLite-matching fuldført..."`, output a clear breakdown explaining what happened to the 13.164 words:
  - `[HH:MM:SS] 🔍 Analyserer bøjningsformer og slår op i lokal ordbogsbase (COR & ODS)...`
  - `[HH:MM:SS] ✅ Lokal analyse fuldført:`
  - `   • 2.251 bøjningsformer blev koblet til kendte grundord via Det Centrale Ordregister (COR).`
  - `   • 725 ældre/historiske ord lå allerede færdighentet i den lokale ODS-cache.`
  - `   • 3.422 ord blev identificeret som nye historiske opslagsord i ODS-registret (heraf 2.701 prioriterede ord, som endnu ikke ligger i cachen).`
  - `   • 5.055 ord blev sprunget over (egennavne, stednavne, slåfejl eller moderne hverdagsord uden historisk særstatus).`
- **Step 3 (Enrichment / Lyn-Henter Queue):**
  - `[HH:MM:SS] ⚡ Klar til berigelse: 2.701 nye historiske ord fra dine bøger kan nu hentes fra Ordbog over det danske Sprog (ordnet.dk).`
  - `[HH:MM:SS] ⏸️ Venter på dit valg ovenfor: Vælg hvor mange ord du vil hente (f.eks. Top 250 mest hyppige i bogen), og start Lyn-Henter eller server-kø.`
  - **CRITICAL BUGFIX:** Deduplicate consecutive waiting/polling messages! Never print `"Venter på Lyn-Henter..."` more than ONCE. While waiting or fetching, update a single live progress line or only log when new words arrive:
  - `[HH:MM:SS] 🚀 Lyn-Henter aktiv: Hentet 50 / 250 historiske artikler (seneste: "lægdslem", "rustholder")...`
- **Step 4 (StarDict Compilation):**
  - `[HH:MM:SS] 🛠️ Kompilerer binære StarDict v2.4.2 filer (GLib-sorteret)...`
  - `[HH:MM:SS] 🎉 Ordbog færdigbygget! Indeholder X opslagsord (.idx) og Y bøjningsformer (.syn) inkl. pre-1948 aa-retskrivning.`

---

## PART 2: Web UI/UX Overhaul (`ordpresse.seame.click`)

### Task 2.1: Hero Section & Navbar — Add Instant Download for the Complete Dictionary
1. **Navbar:** Replace `Sortering: GLib Law` with a live dictionary scope badge (`36.963 opslagsord · 121.856 bøjninger`) and a quick `⬇ Hent komplet ordbog` button.
2. **Hero Card:**
   - Add a primary CTA row right inside the Hero card:
     - **Primary Button (Amber):** `⬇ Hent komplet StarDict-ordbog (.zip)` — immediately downloads the pre-built canonical StarDict package (`ordpresse.ifo`, `ordpresse.idx`, `ordpresse.dict.dz`, `ordpresse.syn`).
     - **Secondary Button (Slate):** `📚 Skræddersy med egne EPUB-bøger` (scrolls to the builder).
   - Add a collapsible `<details>` guide right below: `📖 Sådan installerer du ordbogen i KOReader på 30 sekunder`.

### Task 2.2: Lookup Preview Card (`⚡ Afprøv opslag`)
1. **Disable Spellcheck Squiggles:** Add `spellcheck="false" autocomplete="off" autocorrect="off" autocapitalize="off"` to `#lookup-input` and the custom words textarea.
2. **Add Orthography & Inflection Chips:** Alongside the Moberg chips (`lægdslemmer`, `jordebog`, `bissekræmmer`, `værkbrudne`, `banco`, `husforhør`, `rustholder`, `lægd`), add chips for `baad`, `daabskjole`, `overstrøet`, and `efteraarsferie`.
3. **Human-Friendly Popup Header:** Replace `Kilde: stardict_binary` with a clear lookup resolution indicator (e.g., `Bøjningsopslag: lægdslemmer → lægdslem` when resolved via `.syn`, or `Direkte opslag: jordebog` when resolved via `.idx`).

### Task 2.3: Builder Form Progressive Disclosure & Bookmarklet Placement
1. **Dynamic Scope Toggle (`Vælg ordbogens omfang`):**
   - When **`Fokuseret / EPUB-uddrag`** is selected: Show Step 1 (EPUB dropzone with individual file pills + `×` remove buttons) and Step 2 (Specific words textarea).
   - When **`Komplet ordbog (generel brug)`** is selected: Hide Step 1 and Step 2 (since it requires no EPUBs!) and show a clean info box pointing to the instant download button or allowing a custom-titled full build.
2. **Move "Ordpresse Lyn-Henter" Into Context:**
   - Remove the standalone orange *Lyn-Henter* banner from above *"Vælg ordbogens omfang"*.
   - Place the *Ordpresse Lyn-Henter* bookmarklet button and 3-step guide directly inside/below the **`Metode for ukendte historiske ord`** section (and inside the paused build action box when waiting for bookmarklet fetches).
3. **Group Live-Lookup Checkbox:** Move `☑ Tillad live-opslag (ODS & NAOB) for ord, der mangler i cachen` out of the title row and into the header of the *"Metode for ukendte historiske ord"* box.

---

## PART 3: Sync `README.md` & Polish Single-Layer ODS Tails

1. **Update `README.md`:**
   - Replace the outdated `lægdslem` example in `README.md` with the exact 3-layer canonical article shown in the Web UI (`lægdslem sb. itk. (legdelem, legdelemmer, lægdslemmer; ældre: Lægdslem, Lejdslem)`, core definition bullet, `HISTORISK BETYDNING (ODS / 1700–1950)`, `[Historisk kontekst]`, and `Kilde: ODS · Molbech · Salmonsens`).
   - Update all word/synonym counts in `README.md` to match V8.1 (`36.963` headwords, `121.856` synonyms).
2. **Single-Layer ODS Polish (`overstrø`):**
   - Do **NOT** alter 3-layer articles like `lægdslem`.
   - On single-layer ODS entries like `overstrø vb. (overstrø)`, suppress the parenthetical if it is 100% identical (case-insensitively) to the headword, and strip dangling syntax tails caused by example removal (`; især i forb. m. præp.`, `; især i forb. m.`, `; bl. a. i forb.`).

## Verification & Deployment
1. Run `.venv/bin/pytest -v` and verify all tests pass.
2. Commit & push, deploy to `192.168.50.5`, rebuild the canonical StarDict files, and restart the web service.