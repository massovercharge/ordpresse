"""Curated OTA Plugin, User Patch, and Resource Store catalog for KOReader devices (v1.6.0)."""

from dataclasses import dataclass
import io
import logging
import os
from pathlib import Path
from typing import List, Optional
import zipfile

import httpx

logger = logging.getLogger(__name__)


def _get_vendor_root() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "vendor",
        Path("/app/src/ordpresse/vendor"),
        Path.cwd() / "src" / "ordpresse" / "vendor",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _get_vendor_annas_dir() -> Path:
    return _get_vendor_root() / "annas-fetch.koplugin"


def _get_plugin_cache_dir() -> Path:
    d = Path(os.getenv("ORDPRESSE_DATA_DIR", "data")) / "plugin_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


VENDOR_ANNAS_DIR = _get_vendor_annas_dir()


@dataclass
class StorePlugin:
    """Metadata for a curated KOReader plugin, user patch, or typography/system resource."""
    id: str                             # Unikt ID, fx "annas-fetch", "patch-exlibris-screensaver", "hyph-da"
    name: str                           # Brugervenligt navn med universelle Kindle e-ink-tags
    description: str                    # Kort beskrivelse (1-2 sætninger, dansk)
    version: str                        # Nyeste version, fx "1.6.0"
    author: str                         # Forfatter / Kilde
    github_repo: str                    # "owner/repo" format eller kildelink
    koplugin_name: str                  # Mappenavn ("foo.koplugin") ELLER filnavn ("2-foo.lua")
    zipball_url: str = ""               # Ekstern URL eller tom ved lokal vendor-distribution
    category: str = "utility"           # "books", "sync", "ui", "utility", "resources", "patch"
    min_koreader: str = "2024.07"       # Minimum KOReader version
    size_estimate_kb: int = 150         # Estimeret størrelse i KB
    license: str = "GPL-3.0"            # Licenstype
    install_note: Optional[str] = None  # Valgfri installationsnotat
    target_type: str = "plugin"         # "plugin", "patch", "font", "hyph"
    target_dir: Optional[str] = None    # "plugins", "patches", "fonts", "data/hyph"
    can_uninstall: bool = True          # False kun for selve "ordpresse" kerne-pluginet

    @property
    def pkg_type(self) -> str:
        return self.target_type

    @property
    def target_name(self) -> str:
        return self.koplugin_name


# -------------------------------------------------------------------
# Kurateret Plugin-, User Patch- & Ressource-Katalog (v1.6.0)
# Bemærk: Alle strenge anvender udelukkende universelle Kindle e-ink-tags
# -------------------------------------------------------------------
PLUGIN_CATALOG: List[StorePlugin] = [
    StorePlugin(
        id="bogdoktor",
        name="[Vask] Ordpresse Bogdoktor (EPUB Vaskeri)",
        description="Dybdegående EPUB-restaurering: fletter knækkede OCR-afsnit, opgraderer til dansk bogtrykker-typografi og renser låst forlagslayout.",
        version="2.0.0",
        author="Ordpresse Team",
        github_repo="ordpresse/bogdoktor.koplugin",
        koplugin_name="bogdoktor.koplugin",
        zipball_url="",
        category="books",
        min_koreader="2023.01",
        size_estimate_kb=45,
        license="GPL-3.0",
        install_note="Aktiveres i topmenuen under Læseværktøjer -> [Vask] Ordpresse Bogdoktor når en EPUB er åben.",
        target_type="plugin",
        target_dir="plugins",
    ),
    StorePlugin(
        id="annas-fetch",
        name="[Bog] Anna's Archive (Ordpresse)",
        description="Dual-source bogsøgning (Libgen + Anna's Archive) med Ordpresse server-search og mirror-orkestrering.",
        version="1.2.7-ordpresse",
        author="right9code / Ordpresse",
        github_repo="right9code/annas-fetch.koplugin",
        koplugin_name="annas-fetch.koplugin",
        zipball_url="",
        category="books",
        min_koreader="2024.07",
        size_estimate_kb=150,
        license="GPL-3.0",
        install_note="Forbedret med automatisk spejlstyring og Libgen dual-source.",
        target_type="plugin",
        target_dir="plugins",
    ),
    # --- PUNKT 5: NYE PLUGINS (plugins/) ---
    StorePlugin(
        id="ordpresse-kosync",
        name="[Synk] Ordpresse Læse-Synk (KoSync & CrossPoint)",
        description="1-kliks synkronisering af læseposition på tværs af Kindle, Kobo, CrossPoint og mobil – nul login påkrævet.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/ordpresse-kosync.koplugin",
        koplugin_name="ordpresse-kosync.koplugin",
        zipball_url="",
        category="sync",
        min_koreader="2023.01",
        size_estimate_kb=22,
        license="MIT",
        install_note="Bruger automatisk din Ordpresse API-nøgle til at synkronisere side og procent.",
        target_type="plugin",
        target_dir="plugins",
    ),
    StorePlugin(
        id="ordpresse-udklip",
        name="[Noter] Højdepunkter & Glosebog-Synk",
        description="Synkroniserer dine markerede citater, noter og opslåede gloser direkte til Ordpresse Web UI.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/ordpresse-udklip.koplugin",
        koplugin_name="ordpresse-udklip.koplugin",
        zipball_url="",
        category="sync",
        min_koreader="2023.01",
        size_estimate_kb=25,
        license="MIT",
        install_note="Synkroniserer automatisk i baggrunden når du lukker en bog, eller manuelt via menuen.",
        target_type="plugin",
        target_dir="plugins",
    ),
    StorePlugin(
        id="ordpresse-avisen",
        name="[Avis] Web-til-EPUB ('Læs Senere' & Artikel-Henter)",
        description="Hent lange danske og engelske artikler gemt i Ordpresse Web UI som færdigvaskede E-Ink EPUB-bøger.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/ordpresse-avisen.koplugin",
        koplugin_name="ordpresse-avisen.koplugin",
        zipball_url="",
        category="books",
        min_koreader="2023.01",
        size_estimate_kb=20,
        license="MIT",
        install_note="Artikler dukker også automatisk op i dit Ordpresse OPDS-katalog under 'Læs Senere'.",
        target_type="plugin",
        target_dir="plugins",
    ),
    StorePlugin(
        id="project-title",
        name="[UI] Project: Title (Moderne Bogreol-UI)",
        description="Det populære community-plugin der forvandler KOReaders filbrowser til en visuel bogreol med seriegruppering.",
        version="2.8.0-ordpresse",
        author="joshuacant / Ordpresse",
        github_repo="joshuacant/ProjectTitle",
        koplugin_name="projecttitle.koplugin",
        zipball_url="https://api.github.com/repos/joshuacant/ProjectTitle/releases/latest",
        category="ui",
        min_koreader="2024.07",
        size_estimate_kb=420,
        license="GPL-3.0",
        install_note="Kræver genstart af KOReader. Kan når som helst sættes på pause med [||] Deaktiver.",
        target_type="plugin",
        target_dir="plugins",
    ),
    # --- PUNKT 3: KOREADER USER PATCHES (patches/) ---
    StorePlugin(
        id="patch-clean-bookshelf",
        name="[Patch] Pænere Bogreol & Læse-Badges",
        description="Forfiner KOReaders mosaik-bogreol med skarpe E-Ink rammer, tydelige læseprocent-badges og skjulte filendelser.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/patches",
        koplugin_name="2-ordpresse-clean-bookshelf.lua",
        zipball_url="",
        category="patch",
        min_koreader="2023.01",
        size_estimate_kb=6,
        license="MIT",
        install_note="Lægges i koreader/patches/. Letvægts-alternativ hvis man ikke bruger Project: Title.",
        target_type="patch",
        target_dir="patches",
    ),
    StorePlugin(
        id="patch-clean-statusbar",
        name="[Patch] Typografisk Bundlinje (Statusbar)",
        description="Gør bundlinjen under læsning roligere med bogtrykker-separatorer (·), kapitel-resttid og højere kontrast.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/patches",
        koplugin_name="2-ordpresse-clean-statusbar.lua",
        zipball_url="",
        category="patch",
        min_koreader="2023.01",
        size_estimate_kb=5,
        license="MIT",
        install_note="Lægges i koreader/patches/. Virker med det samme efter genstart af KOReader.",
        target_type="patch",
        target_dir="patches",
    ),
    StorePlugin(
        id="patch-exlibris-screensaver",
        name="[Patch] 'Ex Libris' Pausedisplay & Omslagskort",
        description="Tilføjer et elegant dansk Ex Libris-kort med titel, forfatter og læseprocent hen over pauseskærmen i dvale.",
        version="1.6.0",
        author="Ordpresse",
        github_repo="ordpresse/patches",
        koplugin_name="2-ordpresse-exlibris-screensaver.lua",
        zipball_url="",
        category="patch",
        min_koreader="2023.01",
        size_estimate_kb=8,
        license="MIT",
        install_note="Lægges i koreader/patches/. Viser dit læsekort når Kindle/Kobo sættes i dvale.",
        target_type="patch",
        target_dir="patches",
    ),
    # --- TYPOGRAFI & SYSTEM-RESSOURCER ---
    StorePlugin(
        id="hyph-da",
        name="[DK] Dansk Orddeling (Hyphenation Pattern)",
        description="Officielt dansk orddelingsmønster (hyph-da) for korrekt og flydende stavelsesdeling i KOReader.",
        version="2024.1",
        author="Frank Jensen / TeX DK",
        github_repo="koreader/koreader",
        koplugin_name="hyph-da",
        zipball_url="",
        category="resources",
        min_koreader="2023.01",
        size_estimate_kb=45,
        license="LPPL / GPL-2.0",
        install_note="Installeres direkte i koreader/data/hyph/ for smuk dansk typografi.",
        target_type="hyph",
        target_dir="data/hyph",
    ),
    StorePlugin(
        id="font-chareink",
        name="[Aa] ChareInk 6SP (Vægtet E-Ink Serif)",
        description="Modificeret Charis SIL med forøget stregtykkelse og optimerede punktur-afstande specielt til e-blæk.",
        version="6.0",
        author="SIL / MobileRead Community",
        github_repo="mobileread/chareink",
        koplugin_name="ChareInk",
        zipball_url="",
        category="resources",
        min_koreader="2023.01",
        size_estimate_kb=290,
        license="OFL-1.1",
        install_note="Perfekt til Kindle og ældre skærme med lav kontrast. Giver dybere, sortere tekst.",
        target_type="font",
        target_dir="fonts",
    ),
    StorePlugin(
        id="font-atkinson",
        name="[Aa] Atkinson Hyperlegible (Skrifttype)",
        description="Udviklet af Braille Institute for maksimal tegnadskillelse og høj læsbarhed på e-ink skærme.",
        version="1.006",
        author="Braille Institute",
        github_repo="googlefonts/atkinson-hyperlegible",
        koplugin_name="Atkinson-Hyperlegible",
        zipball_url="",
        category="resources",
        min_koreader="2023.01",
        size_estimate_kb=125,
        license="OFL-1.1",
        install_note="Installeres i koreader/fonts/ og er klar i skrifttype-vælgeren efter genstart.",
        target_type="font",
        target_dir="fonts",
    ),
    StorePlugin(
        id="font-literata",
        name="[Aa] Literata Book (Skrifttype)",
        description="Googles skræddersyede e-bogsskrifttype fra TypeTogether, optimeret til fordybende langformslæsning.",
        version="3.103",
        author="TypeTogether / Google",
        github_repo="googlefonts/literata",
        koplugin_name="Literata",
        zipball_url="",
        category="resources",
        min_koreader="2023.01",
        size_estimate_kb=560,
        license="OFL-1.1",
        install_note="Eminent læselighed på e-blæk med balanceret x-højde og afstemt stregkontrast.",
        target_type="font",
        target_dir="fonts",
    ),
    StorePlugin(
        id="opds-catalog",
        name="[OPDS] Ordpresse Bogkatalog",
        description="Forbinder dit private Ordpresse bogarkiv direkte til KOReaders indbyggede OPDS-browser med 1-klik.",
        version="1.0.0",
        author="Ordpresse Team",
        github_repo="ordpresse/ordpresse_opds.koplugin",
        koplugin_name="ordpresse_opds.koplugin",
        zipball_url="",
        category="books",
        min_koreader="2023.01",
        size_estimate_kb=15,
        license="MIT",
        install_note="Registrerer automatisk 'Den Danske Ordpresse' i KOReaders OPDS-kataloger.",
        target_type="plugin",
        target_dir="plugins",
    ),
]


def get_patch_bytes(patch_id: str) -> Optional[bytes]:
    """Return raw Lua file bytes for a locally vendored KOReader User Patch."""
    plugin = get_plugin_by_id(patch_id)
    if not plugin or plugin.target_type != "patch":
        return None

    vendor_root = VENDOR_ANNAS_DIR.parent
    candidates = [
        vendor_root / "patches" / plugin.koplugin_name,
        Path(__file__).resolve().parent.parent / "vendor" / "patches" / plugin.koplugin_name,
    ]
    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    return None


def flatten_plugin_zip(raw_zip_bytes: bytes, target_name: str) -> bytes:
    """
    Ensure a third-party KOReader plugin zip (e.g. Project: Title from GitHub)
    has a flat root structure: <target_name>/main.lua and <target_name>/_meta.lua.
    Strips any nested wrapper directories such as ProjectTitle-main/projecttitle.koplugin/.
    """
    clean_target = target_name.strip("/\\")
    with zipfile.ZipFile(io.BytesIO(raw_zip_bytes), "r") as src_zf:
        names = [n for n in src_zf.namelist() if not n.startswith("__MACOSX/")]

        # Locate the directory containing main.lua (preferring one that also has _meta.lua)
        main_candidates: list[str] = []
        for n in names:
            norm = n.lstrip("./")
            if norm == "main.lua" or norm.endswith("/main.lua"):
                prefix = norm[: -len("main.lua")]
                main_candidates.append(prefix)

        if not main_candidates:
            raise ValueError("ZIP-arkivet indeholder ingen main.lua.")

        chosen_prefix = main_candidates[0]
        for pref in main_candidates:
            if f"{pref}_meta.lua" in {n.lstrip("./") for n in names}:
                chosen_prefix = pref
                break

        out_buf = io.BytesIO()
        written = 0
        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst_zf:
            for info in src_zf.infolist():
                raw_name = info.filename
                if raw_name.startswith("__MACOSX/") or info.is_dir():
                    continue
                norm = raw_name.lstrip("./")
                if chosen_prefix and not norm.startswith(chosen_prefix):
                    continue
                rel = norm[len(chosen_prefix):].lstrip("/")
                if not rel or ".." in Path(rel).parts:
                    continue
                arcname = f"{clean_target}/{rel}"
                dst_zf.writestr(arcname, src_zf.read(raw_name))
                written += 1

        if written == 0:
            raise ValueError("Ingen gyldige plugin-filer blev fundet i ZIP-arkivet.")

        out_buf.seek(0)
        return out_buf.read()


async def fetch_and_flatten_external_plugin(plugin: StorePlugin) -> bytes:
    """
    Fetch an external GitHub plugin (release asset or zipball), flatten its structure
    so main.lua and _meta.lua sit directly under <koplugin_name>/, and cache on disk.
    """
    cache_dir = _get_plugin_cache_dir()
    safe_ver = "".join(c for c in plugin.version if c.isalnum() or c in "._-")
    cache_path = cache_dir / f"{plugin.id}-{safe_ver}.zip"
    if cache_path.is_file() and cache_path.stat().st_size > 256:
        return cache_path.read_bytes()

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Ordpresse-PluginStore/1.6.0",
    }
    download_url = plugin.zipball_url

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        if "/releases/latest" in download_url:
            rel_resp = await client.get(download_url, headers=headers)
            rel_resp.raise_for_status()
            rel_data = rel_resp.json()
            assets = rel_data.get("assets") or []
            zip_asset = next(
                (a.get("browser_download_url") for a in assets if str(a.get("name", "")).lower().endswith(".zip")),
                None,
            )
            download_url = zip_asset or rel_data.get("zipball_url") or ""
            if not download_url:
                raise ValueError(f"Ingen ZIP-release fundet for {plugin.id}.")

        pkg_resp = await client.get(download_url, headers=headers)
        pkg_resp.raise_for_status()
        raw_content = pkg_resp.content
        if zipfile.is_zipfile(io.BytesIO(raw_content)):
            flattened = flatten_plugin_zip(raw_content, plugin.koplugin_name)
        elif plugin.id == "project-title":
            raise ValueError("Hentet Project: Title-fil er ikke et gyldigt ZIP-arkiv.")
        else:
            return raw_content

    try:
        cache_path.write_bytes(flattened)
    except Exception as exc:
        logger.warning(f"Kunne ikke cache {plugin.id} til disk: {exc}")

    return flattened


def build_resource_zip(resource_id: str) -> Optional[bytes]:
    """Generate in-memory zipball for a locally vendored resource, plugin, or patch."""
    plugin = get_plugin_by_id(resource_id)
    if plugin and plugin.target_type == "patch":
        patch_bytes = get_patch_bytes(resource_id)
        if patch_bytes is None:
            return None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(plugin.koplugin_name, patch_bytes)
        buf.seek(0)
        return buf.read()

    arc_name = plugin.koplugin_name if plugin else resource_id
    lookup_id = "ordpresse_opds.koplugin" if resource_id == "opds-catalog" else resource_id

    vendor_root = VENDOR_ANNAS_DIR.parent
    candidates = [
        vendor_root / lookup_id,
        vendor_root / f"{lookup_id}.koplugin",
        vendor_root / "resources" / lookup_id,
        Path(__file__).resolve().parent.parent / "vendor" / "resources" / lookup_id,
        Path(__file__).resolve().parent.parent / "vendor" / lookup_id,
        Path(__file__).resolve().parent.parent / "vendor" / f"{lookup_id}.koplugin",
    ]
    target_dir = None
    for c in candidates:
        if c.is_dir():
            target_dir = c
            break

    if not target_dir or not target_dir.exists():
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(target_dir.rglob("*")):
            if file_path.is_file():
                rel_path = file_path.relative_to(target_dir)
                arcname = str(Path(arc_name) / rel_path)
                zf.write(file_path, arcname=arcname)
    buf.seek(0)
    return buf.read()


def build_annas_fetch_zip() -> bytes:
    """Generate in-memory zipball for annas-fetch.koplugin from vendor files."""
    data = build_resource_zip("annas-fetch")
    if data is None:
        raise FileNotFoundError(f"Missing vendor directory for annas-fetch: {VENDOR_ANNAS_DIR}")
    return data


def get_plugin_zip(plugin_id: str) -> Optional[bytes]:
    """Find and build in-memory zip bytes for a local plugin, patch, or resource."""
    return build_resource_zip(plugin_id)


def get_catalog_json() -> list[dict]:
    """Return catalog as JSON-serializable list of dicts for API response."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "version": p.version,
            "author": p.author,
            "github_repo": p.github_repo,
            "koplugin_name": p.koplugin_name,
            "target_name": p.target_name,
            "pkg_type": p.pkg_type,
            "category": p.category,
            "min_koreader": p.min_koreader,
            "size_estimate_kb": p.size_estimate_kb,
            "license": p.license,
            "install_note": p.install_note,
            "target_type": p.target_type,
            "target_dir": p.target_dir or (
                "patches" if p.target_type == "patch"
                else "fonts" if p.target_type == "font"
                else "data/hyph" if p.target_type == "hyph"
                else "plugins"
            ),
            "can_uninstall": p.can_uninstall,
        }
        for p in PLUGIN_CATALOG
    ]


def get_plugin_by_id(plugin_id: str) -> Optional[StorePlugin]:
    """Find a plugin or patch by its unique ID."""
    for p in PLUGIN_CATALOG:
        if p.id == plugin_id:
            return p
    return None
