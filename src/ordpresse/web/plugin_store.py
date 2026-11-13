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


def build_annas_fetch_zip() -> bytes:
    """Generate in-memory zipball for annas-fetch.koplugin from vendor files."""
    vendor_dir = _get_vendor_annas_dir()
    if not vendor_dir.exists():
        raise FileNotFoundError(f"Missing vendor directory: {vendor_dir}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(vendor_dir.rglob("*")):
            if file_path.is_file():
                rel_path = file_path.relative_to(vendor_dir)
                arcname = str(Path("annas-fetch.koplugin") / rel_path)
                zf.write(file_path, arcname=arcname)
    buf.seek(0)
    return buf.read()


@dataclass
class StorePlugin:
    """Metadata for a curated third-party KOReader plugin."""
    id: str                      # Unikt ID, fx "annas-fetch"
    name: str                    # Brugervenligt navn, fx "Anna's Archive"
    description: str             # Kort beskrivelse (1-2 sætninger, dansk)
    version: str                 # Nyeste version, fx "1.2.0-ordpresse"
    author: str                  # Forfatter / GitHub bruger
    github_repo: str             # "owner/repo" format, fx "right9code/annas-fetch.koplugin"
    koplugin_name: str           # Mappenavn i koreader/plugins/, fx "annas-fetch.koplugin"
    zipball_url: str             # Direkte URL eller tom hvis lokal distribution
    category: str = "utility"    # Kategori: "books", "utility", "ui", "sync"
    min_koreader: str = "2024.07"  # Minimum KOReader version
    size_estimate_kb: int = 150  # Estimeret størrelse i KB
    license: str = "GPL-3.0"     # Licenstype
    install_note: Optional[str] = None  # Valgfri installationsnotat


# -------------------------------------------------------------------
# Kurateret Plugin Katalog
# -------------------------------------------------------------------
PLUGIN_CATALOG: List[StorePlugin] = [
    StorePlugin(
        id="annas-fetch",
        name="📚 Anna's Archive (Ordpresse)",
        description="Dual-source bogsøgning (Libgen + Anna's Archive) med Ordpresse mirror-orkestrering.",
        version="1.2.0-ordpresse",
        author="right9code / Ordpresse",
        github_repo="right9code/annas-fetch.koplugin",
        koplugin_name="annas-fetch.koplugin",
        zipball_url="",
        category="books",
        min_koreader="2024.07",
        size_estimate_kb=150,
        license="GPL-3.0",
        install_note="Forbedret med automatisk spejlstyring og Libgen dual-source.",
    ),
]


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
        }
        for p in PLUGIN_CATALOG
    ]


def get_plugin_by_id(plugin_id: str) -> Optional[StorePlugin]:
    """Find a plugin by its unique ID."""
    for p in PLUGIN_CATALOG:
        if p.id == plugin_id:
            return p
    return None
