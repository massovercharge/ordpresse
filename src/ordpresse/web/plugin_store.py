"""Curated OTA Plugin Store catalog for KOReader devices."""

from dataclasses import dataclass
import io
from pathlib import Path
from typing import List, Optional
import zipfile


def _get_vendor_annas_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "vendor" / "annas-fetch.koplugin",
        Path("/app/src/ordpresse/vendor/annas-fetch.koplugin"),
        Path.cwd() / "src" / "ordpresse" / "vendor" / "annas-fetch.koplugin",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


VENDOR_ANNAS_DIR = _get_vendor_annas_dir()


@dataclass
class StorePlugin:
    """Metadata for a curated third-party KOReader plugin or typography/system resource."""
    id: str                      # Unikt ID, fx "annas-fetch", "hyph-da", "font-chareink"
    name: str                    # Brugervenligt navn, fx "[Bog] Anna's Archive (Ordpresse)"
    description: str             # Kort beskrivelse (1-2 sætninger, dansk)
    version: str                 # Nyeste version, fx "1.2.7-ordpresse"
    author: str                  # Forfatter / Kilde
    github_repo: str             # "owner/repo" format eller kildelink
    koplugin_name: str           # Mappenavn i målmappe, fx "annas-fetch.koplugin" eller "ChareInk"
    zipball_url: str = ""        # Direkte URL eller tom hvis lokal distribution
    category: str = "utility"    # Kategori: "books", "utility", "ui", "sync", "resources"
    min_koreader: str = "2024.07"  # Minimum KOReader version
    size_estimate_kb: int = 150  # Estimeret størrelse i KB
    license: str = "GPL-3.0"     # Licenstype
    install_note: Optional[str] = None  # Valgfri installationsnotat
    target_type: str = "plugin"  # "plugin", "font", "hyph"
    target_dir: Optional[str] = None  # "plugins", "fonts", "data/hyph"


# -------------------------------------------------------------------
# Kurateret Plugin & Ressource Katalog
# -------------------------------------------------------------------
PLUGIN_CATALOG: List[StorePlugin] = [
    StorePlugin(
        id="bogdoktor",
        name="[🩺] Ordpresse Bogdoktor (EPUB Vaskeri)",
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
        install_note="Aktiveres i topmenuen under Læseværktøjer -> 🩺 Ordpresse Bogdoktor når en EPUB er åben.",
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
        name="[📡] Ordpresse OPDS Bogkatalog",
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


def build_resource_zip(resource_id: str) -> Optional[bytes]:
    """Generate in-memory zipball for a locally vendored resource or plugin."""
    plugin = get_plugin_by_id(resource_id)
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
    """Find and build in-memory zip bytes for a local plugin or resource."""
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
            "category": p.category,
            "min_koreader": p.min_koreader,
            "size_estimate_kb": p.size_estimate_kb,
            "license": p.license,
            "install_note": p.install_note,
            "target_type": p.target_type,
            "target_dir": p.target_dir or (
                "fonts" if p.target_type == "font"
                else "data/hyph" if p.target_type == "hyph"
                else "plugins"
            ),
        }
        for p in PLUGIN_CATALOG
    ]


def get_plugin_by_id(plugin_id: str) -> Optional[StorePlugin]:
    """Find a plugin by its unique ID."""
    for p in PLUGIN_CATALOG:
        if p.id == plugin_id:
            return p
    return None
