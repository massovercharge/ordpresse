# Teknisk Implementeringsspecifikation: CrossPoint OPDS Bibliotek & Multi-Device Synkronisering

Denne tekniske implementeringsspecifikation fastlægger arkitekturen, datamodellerne, sikkerhedskontrakterne og API-specifikationerne for etablering af et **centralt bogbibliotek med OPDS 1.2-katalog** i Ordpresse, målrettet problemfri synkronisering mellem **KOReader (på Kindle)** og **CrossPoint OS (på mikro-e-ink læsere som Xteink X4 Pro)** via `https://ordpresse.seame.click`.

---

## 1. Systemarkitektur & Problemstilling

### 1.1 Mål & Problemstilling
Brugeren anvender to e-bogslæsere i samspil:
1. **Kindle (KOReader)**: Primær e-bogslæser med Anna Fetch (`1.2.5-ordpresse`) til direkte søgning og download.
2. **CrossPoint Læser (f.eks. Xteink X4 Pro)**: Kompakt mobil e-bogslæser til farten.
3. **CrossPoint Sync (KOSync-kompatibel)**: Synkroniserer læseposition, fremdrift og bogmærker mellem enhederne.

**Udfordringen:** KOSync matcher dokumenter baseret på **filens indholdshash (delvist MD5-hash)** samt **filnavn**. Hvis bogen kun downloades lokalt på Kindlen, eller hvis filnavnet/indholdet varierer på de to enheder, fejler synkroniseringen. Desuden befinder CrossPoint-enheden sig ofte uden for hjemmenetværket og skal kunne tilgå biblioteket sikkert via det offentlige HTTPS-domæne `https://ordpresse.seame.click`.

### 1.2 Arkitekturdiagram

```
 ┌────────────────────────────────────────────────────────┐
 │                    EKSTERNE SPEJLE                     │
 │          (Libgen / Anna's Archive / CDN)               │
 └──────────────────────────┬─────────────────────────────┘
                            │ HTTPS (TLS 1.3 / HTTP/2)
                            ▼
 ┌────────────────────────────────────────────────────────┐
 │            ORDPRESSE SERVER (192.168.50.5)             │
 │                                                        │
 │   ┌───────────────────────┐  ┌─────────────────────┐   │
 │   │ SQLite: cache.sqlite3 │  │ data/books/         │   │
 │   │ (downloaded_books)    │  │ (Fysiske EPUB-filer)│   │
 │   └──────────┬────────────┘  └──────────┬──────────┘   │
 │              │                          │              │
 │   ┌──────────┴──────────────────────────┴──────────┐   │
 │   │        FastAPI Application Core                │   │
 │   │  • /api/v1/koreader/lookup?action=annas_download│   │
 │   │  • /opds (Atom OPDS 1.2 Acquisition Feed)      │   │
 │   │  • /opds/download/{md5}                        │   │
 │   │  • Web UI: "Bibliotek & Bøger"                 │   │
 │   └──────────────────────────┬─────────────────────┘   │
 └──────────────────────────────┼─────────────────────────┘
                                │ Cloudflare Zero Trust Tunnel
                                ▼
                 https://ordpresse.seame.click
               (Offentlig HTTPS overalt i verden)
                    │                       │
      ┌─────────────┴───────────┐ ┌─────────┴─────────────┐
      │     KINDLE (KOReader)   │ │  CROSSPOINT LÆSER     │
      │  • Anna Fetch Plugin    │ │  • OPDS Browser       │
      │  • KOSync Klient        │ │  • CrossPoint Sync    │
      └─────────────────────────┘ └───────────────────────┘
```

---

## 2. Datamodel & Persistent Lagring

### 2.1 Database Schema (`data/cache.sqlite3`)
I den eksisterende cache-database tilføjes en tabel `downloaded_books` i `cache_conn`:

```sql
CREATE TABLE IF NOT EXISTS downloaded_books (
    md5 TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    format TEXT NOT NULL DEFAULT 'epub',
    cover_url TEXT DEFAULT NULL,
    download_source TEXT DEFAULT 'libgen',
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_books_downloaded_at ON downloaded_books(downloaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_books_title_author ON downloaded_books(title, author);
```

### 2.2 Fysisk Filstruktur (`data/books/`)
- Mappe: `/app/data/books` i containeren (mappet til `./data/books` på værten).
- Format: Bøger gemmes som `data/books/<filename>`.
- Filnavne-standardisering:
  ```python
  clean_title = re.sub(r'[^\w\s.-]', '', title).strip().replace(' ', '_')[:60]
  clean_author = re.sub(r'[^\w\s.-]', '', author).strip().replace(' ', '_')[:40]
  canonical_filename = f"{clean_title}_{clean_author}.{ext}"
  ```
  Denne standard er 100% kompatibel med FAT32, ext4, Kindle OS og CrossPoints filsystem uden ulovlige tegn (`/ \ : * ? " < > |`).

---

## 3. Autentificering for CrossPoint & OPDS

Da CrossPoint er en e-ink enhed med begrænset input og forskellige OPDS-klientimplementationer, understøtter Ordpresse **tre parallelle autentificeringsmetoder** for de samme `ord_...` API-nøgler:

1. **Query Parameter (Anbefalet til CrossPoint OPDS)**:
   ```text
   https://ordpresse.seame.click/opds?api_key=ord_06861dbc46b19c4f640cae83da51fdc087eb8fa575bdf01d
   ```
   *Alle links i OPDS-feedet forsynes automatisk med den samme `api_key`-parameter, så CrossPoint kan downloade uden ekstra dialogbokse.*
2. **HTTP Basic Auth**:
   - Header: `Authorization: Basic base64(username:password)`
   - Serveren accepterer, hvis enten `username` eller `password` matcher en gyldig aktiv `ord_...` nøgle.
3. **HTTP Header (`X-API-Key`)**:
   - Standardmetoden anvendt af KOReader.

### 3.1 Nøgleudtrækningslogik (`extract_api_key`)
```python
def extract_request_api_key(request: Request) -> Optional[str]:
    # 1. Header
    if "X-API-Key" in request.headers:
        return request.headers["X-API-Key"].strip()
    # 2. Query param
    if "api_key" in request.query_params:
        return request.query_params["api_key"].strip()
    # 3. HTTP Basic Auth
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("basic "):
        import base64
        try:
            raw = base64.b64decode(auth[6:].strip()).decode("utf-8")
            u, _, p = raw.partition(":")
            if u.strip().startswith("ord_"):
                return u.strip()
            if p.strip().startswith("ord_"):
                return p.strip()
        except Exception:
            pass
    return None
```

---

## 4. API Specifikation

### 4.1 OPDS 1.2 Acquisition Feed (`GET /opds`)

#### Request
```http
GET /opds?api_key=ord_... HTTP/1.1
Host: ordpresse.seame.click
Accept: application/atom+xml, application/xml, */*
```

#### Response (`200 OK`)
```xml
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:ordpresse:opds:catalog</id>
  <title>Ordpresse Bibliotek</title>
  <updated>2026-11-13T20:47:58Z</updated>
  <icon>https://ordpresse.seame.click/static/favicon.ico</icon>
  <author>
    <name>Ordpresse</name>
    <uri>https://ordpresse.seame.click</uri>
  </author>
  <link rel="self" href="https://ordpresse.seame.click/opds?api_key=ord_..." type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="https://ordpresse.seame.click/opds?api_key=ord_..." type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>

  <entry>
    <title>A Column of Fire</title>
    <id>urn:ordpresse:book:9730450e1ea1d6e15d72a2bcdf46c090</id>
    <author><name>Ken Follett</name></author>
    <dc:language>en</dc:language>
    <updated>2026-11-13T20:47:58Z</updated>
    <summary type="text">A Column of Fire af Ken Follett (EPUB - 3.1 MB)</summary>
    <content type="text">A Column of Fire af Ken Follett</content>
    <link rel="http://opds-spec.org/acquisition"
          href="https://ordpresse.seame.click/opds/download/9730450e1ea1d6e15d72a2bcdf46c090?api_key=ord_..."
          type="application/epub+zip"/>
  </entry>
</feed>
```

---

### 4.2 Direkte Bog-Download (`GET /opds/download/{md5}`)

#### Request
```http
GET /opds/download/9730450e1ea1d6e15d72a2bcdf46c090?api_key=ord_... HTTP/1.1
Host: ordpresse.seame.click
```

#### Response (`200 OK`)
```http
HTTP/1.1 200 OK
Content-Type: application/epub+zip
Content-Length: 3159213
Content-Disposition: attachment; filename="A_Column_of_Fire_Ken_Follett.epub"
Accept-Ranges: bytes
X-MD5: 9730450e1ea1d6e15d72a2bcdf46c090
Cache-Control: public, max-age=86400
```
*Hvis filen allerede findes i `data/books/`, returneres den via `FileResponse` med `Accept-Ranges: bytes` (gør det muligt for CrossPoint at genoptage afbrudte downloads).*

---

### 4.3 Opdatering af KOReader Anna Fetch Stream (`action=annas_download`)

Når Kindlen downloader en bog via `action=annas_download`, udvides endpointet i `app.py`:
1. Bogen downloades/streames fra spejlet til Kindlen.
2. Samtidig gemmes en komplet kopi på serveren i `data/books/<filename>`.
3. Bogen registreres i `downloaded_books`-tabellen med titel, forfatter, filstørrelse og MD5.
4. Bogen dukker øjeblikkeligt op i `/opds`-feedet og på Web UI.

---

## 5. Web UI: "Bibliotek & CrossPoint"

På webinterfacet (`index.html`) tilføjes en dedikeret sektion:
1. **Personligt CrossPoint OPDS-link**: En letkopieret URL med brugerens aktuelle API-nøgle:
   `https://ordpresse.seame.click/opds?api_key=ord_...`
2. **Katalogtabel**:
   - Viser alle downloadede bøger sorteret kronologisk.
   - Kolonner: Titel, Forfatter, Størrelse, Tilføjet dato, Status (*"Klar til CrossPoint"*).
   - Handlinger: "⬇️ Download EPUB", "Slet fra arkiv".
3. **Manuel bog-søgning**: Mulighed for at søge og tilføje bøger direkte fra browseren uden Kindlen tændt.

---

## 6. Kritisk Engineering Review

Nedenfor gennemgås en udtømmende kritisk vurdering af specifikationen med identifikation af risici, edge cases og afbødende foranstaltninger (mitigations).

---

### 6.1 KOSync / CrossPoint Sync Protokolkompatibilitet
* **Kritisk punkt**: KOSync beregner et hash ud fra bogens første 4096 bytes og sidste 4096 bytes. Hvis serveren modificerer EPUB-filen (f.eks. injicerer metadata eller ændrer zip-komprimering), ændres dokument-hashet, og KOSync opfatter bogen som to forskellige dokumenter.
* **Vurdering**: Serveren må **aldrig** genkomprimere eller ændre en eneste byte i den downloadede EPUB-fil. Den rå binære strøm fra Libgen skal gemmes uændret.
* **Mitigation**: Ordpresse gemmer og serverer filen som en ren binær bit-for-bit klon. Både Kindle og CrossPoint modtager nøjagtig samme fil.

---

### 6.2 Sikkerhed & API-nøgle i URL
* **Kritisk punkt**: Ved at sende `api_key` som en query-parameter (`?api_key=ord_...`) risikerer nøglen at blive logget i webserver-access-logs, proxyer eller browserhistorik.
* **Vurdering**: CrossPoint OS's simple HTTP-stak understøtter sjældent custom HTTP-headers i sit OPDS-modul. Query-parameteren er en nødvendighed for e-ink interoperabilitet.
* **Mitigations**:
  1. Forbindelsen mellem CrossPoint og Ordpresse er altid krypteret via HTTPS (Cloudflare SSL/TLS), så nøglen kan ikke opsnappes i transit.
  2. Nøgler i Ordpresse har begrænsede rettigheder (kun opslag og download, ingen admin-rettigheder som sletning af database eller brugerstyring).
  3. Serverlog-maskering: Uvicorn/FastAPI-logging kan maskere `api_key` i logs, så rå nøgler ikke skrives til disk.

---

### 6.3 CrossPoint Filnavns- og Tegnsætsbegrænsninger
* **Kritisk punkt**: CrossPoint OS kører på ESP32/embedded hardware med LittleFS eller FAT32 på microSD-kort. Visse specialtegn (`æ`, `ø`, `å`, apostroffer, emojis, kolon) kan give filsystemfejl eller korrupte filnavne.
* **Vurdering**: Hvis filnavnet på Kindlen afviger med bare ét tegn fra filnavnet på CrossPoint, kan CrossPoint Sync fejle med at matche bogen efter filnavn.
* **Mitigations**:
  1. Anvend en streng sanitiseringsfunktion: Erstatter alle ikke-alfanumeriske tegn med underscore eller fjerner dem: `[^\w.-]`.
  2. Begræns titellængden til 60 tegn og forfatter til 40 tegn (maksimal samlet filnavnlængde under 120 tegn, langt under FAT32's 255-tegns grænse).
  3. Den eksakt samme sanitiseringslogik anvendes i både Anna Fetch (`scraper.lua`) og Ordpresse backend (`app.py`).

---

### 6.4 Håndtering af Afbrudte Downloads & Byte-Range Support
* **Kritisk punkt**: Mobile e-ink læsere på svag Wi-Fi afbryder ofte forbindelsen midt i en 5–10 MB download. Hvis serveren ikke understøtter HTTP Byte-Range requests (`Range: bytes=X-Y`), skal læseren starte forfra hver gang.
* **Vurdering**: For store bøger er genoptagelse (resume) afgørende for en god brugeroplevelse.
* **Mitigation**: Ved at gemme filen i `data/books/` og bruge FastAPIs `FileResponse` med `Accept-Ranges: bytes`, håndterer FastAPI automatisk `Range`-forespørgsler og `206 Partial Content` out-of-the-box.

---

### 6.5 Diskplads & Ressourcestyring på Serveren
* **Kritisk punkt**: Hvis serveren gemmer alle bøger permanent, kan disken på en lille server fyldes over tid, hvis der hentes mange tegneserier (CBZ) eller store PDF'er.
* **Vurdering**: En typisk EPUB fylder kun 1–3 MB. 500 bøger fylder cirka 1 GB, hvilket er uproblematisk på en moderne server med 32–500 GB lager.
* **Mitigations**:
  1. Bøger deduplikeres altid på `md5`: Hvis samme bog hentes to gange, overskrives/genbruges filen uden ekstra pladsforbrug.
  2. Implementer en simpel slette-knap i Web UI'et.

---

### 6.6 Konklusion på Review
Specifikationen opfylder samtlige krav fra brugeren:
1. **Global adgang**: Fuld HTTPS via `https://ordpresse.seame.click`.
2. **Sømløs autentificering**: Genbruger eksisterende `ord_...` API-nøgler uden behov for ny brugeradministration.
3. **CrossPoint-kompatibilitet**: Standard OPDS 1.2-feed, der lader CrossPoints indbyggede OPDS-browser hente bøger trådløst.
4. **KOSync-harmoni**: Garanterer identiske filhashes og standardiserede filnavne for 100% pålidelig læsesynkronisering.
