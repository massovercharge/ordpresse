# Den Danske Ordpresse (`ordpresse`)

**Den Danske Ordpresse** er en specialiseret ordbogsbygger til **KOReader** e-bogslæsere (Kindle, Kobo, Android e-ink). Projektet scanner EPUB-bøger (fx Vilhelm Moberg, Henrik Pontoppidan, Steen Steensen Blicher) for historiske, arkaiske og sjældne danske ord, samler definitioner fra autoritative ordbogskilder, og genererer en binær **StarDict v2.4.2** ordbog (`.ifo`, `.idx`, `.dict.dz`, `.syn`).

---

## 🌟 Hovedfunktioner

1. **StarDict GLib Binary Sortering (`g_ascii_strcasecmp`):**
   - KOReader og `sdcv` benytter GLibs `g_ascii_strcasecmp` til binærsøgning.
   - Bevarer nøjagtig binær sorteringsorden for danske specialtegn som `Æ`, `Ø` og `Å` uden korrupt binærsøgning.
2. **Pre-1948 Retskrivning & Bøjningsopløsning:**
   - Automatisk konvertering af `aa` <-> `å` (fx *gaardmand* <-> *gårdmand*, *Aar* <-> *år*).
   - Fjernelse af bløde bindestreger (`\u00ad`), nul-bredde tegn (`\u200b`, `\ufeff`) og linjeskifts-ombrydninger.
   - Dansk bøjnings- og sammensætningsanalyse (*lægdslemmer* -> *lægdslem*, *værkbrudne* -> *værkbrudden*, *jordebøger* -> *jordebog*).
3. **Multi-Homograf ODS & NAOB Scraper:**
   - Samler automatisk alle homografer fra *Ordbog over det danske Sprog* (ODS).
   - NAOB-fallback for 1800-tals dansk-norske termer.
   - SQLite-baseret caching (`cache.sqlite3`), så intet ord forespørges to gange.
   - Indbyggede offline-frø for øjeblikkelig offline-funktionalitet.
4. **Hukommelsessikker Streaming (< 250 MB RAM):**
   - DSLs COR og ODS fuldformer indeholder 3+ millioner rækker.
   - Alle datasæt streames linje-for-linje direkte ind i SQLite med WAL-tilstand og batch-indsættelse (10.000 rækker pr. batch).
5. **Asynkron Bygge-Pipeline (Cloudflare Tunnel Safe):**
   - Bygger ordbogen asynkront i baggrunden med Server-Sent Events (SSE) for at undgå Cloudflare HTTP 524-timeouts.
   - Responsiv webbrugerflade med HTMX og Tailwind CSS.

---

## 🚀 Kom godt i gang

### Lokal kørsel med Python 3.12+

```bash
# Opret venv og installer afhængigheder
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Kør testsuite
pytest -v

# Start webbrugerfladen
ordpresse serve --port 8088
```

Gå til [http://localhost:8088](http://localhost:8088).

---

## 🛠️ CLI Kommandoer

```bash
# 1. Hent og indekser officielle DSL COR og ODS datasæt
ordpresse fetch-datasets

# 2. Byg ordbog fra en eller flere EPUB-filer
ordpresse build --epub /sti/til/bog.epub --bookname "Den Danske Ordpresse (1700-nu)"

# 3. Byg ordbog med specifikke ord
ordpresse build --words lægdslemmer jordebog bissekræmmer værkbrudne banco

# 4. Start webserveren
ordpresse serve --host 0.0.0.0 --port 8088
```

---

## 🐳 Docker & Docker Compose

Byg og start containeren:

```bash
docker compose up -d --build
```

Containeren kører internt på port `8088`, som via Docker Compose er mappet til værtsport `18088` (for at undgå portkollisioner på serveren). Data gemmes persistent i `./data`.

### Cloudflare Zero Trust Tunnel Setup

I Cloudflare Zero Trust Dashboard oprettes en Public Hostname rute:
- **Service:** `HTTP`
- **URL:** `192.168.50.5:18088` (eller container-navn/intern IP)

---

## 📱 Installation i KOReader

1. Download den genererede `ordpresse.zip` fra webinterfacet eller find den i `data/output/`.
2. Udpak mappen `ordpresse/` til din e-bogslæser:
   - **Kindle / Kobo / Android:** `koreader/data/dict/ordpresse/`
3. Åbn KOReader &rarr; **Ordbogsindstillinger** &rarr; Aktivér **Den Danske Ordpresse**.

---

## 🧪 Tests

```bash
pytest -v
```

Inkluderer:
- `tests/test_stardict_writer.py`: Verificerer GLib `g_ascii_strcasecmp` sorteringsloven og StarDict binærlæseren.
- `tests/test_orthography.py`: Verificerer `aa`<->`å`, bløde bindestreger og bøjningsopløsning.
- `tests/test_moberg_words.py`: End-to-end regressionstest af de 5 Moberg-ord i en simuleret EPUB.
- `tests/test_api.py`: Verificerer FastAPI endpoints og sundhedstjek.

---

## 📜 Lovpligtig kreditering og kildelicenser (Attribution & Licensing)

Den Danske Ordpresse er et uafhængigt open-source projekt udviklet til personlig, ikke-kommerciel læsning på e-bogslæsere. Ordbogsdata bygger på åbne sprogressourcer fra følgende institutioner og projekter:

1. **Det Danske Sprog- og Litteraturselskab (DSL):**
   - Data fra **Ordbog over det danske Sprog (ODS)** samt **ODS fuldformsleksikon** (udarbejdet af Thomas Troelsgård, DSL) benyttes i overensstemmelse med [DSL's åbne sprogressourcer](https://korpus.dsl.dk/resources/licences/dsl-open.html). Besøg [dsl.dk](https://dsl.dk) og [ordnet.dk/ods](https://ordnet.dk/ods).
   - *Henstilling:* DSL krediteres i overensstemmelse med licensen. DSL støtter eller godkender ikke dette tredjepartsværktøj eller dets anvendelse.
2. **Det Centrale Ordregister (COR & COR-SEM):**
   - Udviklet og udgivet af **Det Danske Sprog- og Litteraturselskab (DSL)** og **Center for Sprogteknologi (CST, Københavns Universitet)**. Distribueret via [ordregister.dk](https://ordregister.dk) under **[Creative Commons CC0 1.0 Universal (Public Domain Dedication)](https://creativecommons.org/publicdomain/zero/1.0/)**.
3. **Det Norske Akademi for Språk og Litteratur (NAOB) & Public Domain-kilder:**
   - Supplerende referencer for fælles dansk-norske og nordiske 1800-talstermer fra [naob.no](https://naob.no), Chr. Molbechs *Dansk Ordbog* (1859) og *Salmonsens Konversationsleksikon* (2. udg., Project Runeberg).

*Ansvarsfraskrivelse:* Den Danske Ordpresse udgives ikke i kommerciel konkurrence med DSL's egne digitale ordbøger (fx ordnet.dk) og er udelukkende beregnet til privat brug på personlige e-bogslæsere.

---

## 📄 Projektlicens

Kildekoden til Den Danske Ordpresse er licenseret under MIT License.

