"""Curated OTA Plugin Store catalog for KOReader devices."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StorePlugin:
    """Metadata for a curated third-party KOReader plugin."""
    id: str                      # Unikt ID, fx "annas-fetch"
    name: str                    # Brugervenligt navn, fx "Anna's Archive"
    description: str             # Kort beskrivelse (1-2 sætninger, dansk)
    version: str                 # Nyeste version, fx "1.1.0"
    author: str                  # Forfatter / GitHub bruger
    github_repo: str             # "owner/repo" format, fx "right9code/annas-fetch.koplugin"
    koplugin_name: str           # Mappenavn i koreader/plugins/, fx "annas-fetch.koplugin"
    zipball_url: str             # Direkte URL til GitHub zipball (tag eller branch)
    category: str = "utility"    # Kategori: "books", "utility", "ui", "sync"
    min_koreader: str = "2024.07"  # Minimum KOReader version
    size_estimate_kb: int = 50   # Estimeret størrelse i KB
    license: str = "GPL-3.0"     # Licenstype
    install_note: Optional[str] = None  # Valgfri installationsnotat


# -------------------------------------------------------------------
# Kurateret Plugin Katalog
# -------------------------------------------------------------------
# VIGTIGT: Kun plugins der IKKE er inkluderet som standard i KOReader.
# Calibre, Wallabag, OPDS, etc. er allerede standard og skal IKKE med.
# -------------------------------------------------------------------

PLUGIN_CATALOG: List[StorePlugin] = [
    StorePlugin(
        id="annas-fetch",
        name="📚 Anna's Archive",
        description="Søg og download bøger direkte fra Anna's Archive. "
                    "Understøtter sprogfiltrering, formatvalg og streaming til disk.",
        version="1.1.0",
        author="right9code",
        github_repo="right9code/annas-fetch.koplugin",
        koplugin_name="annas-fetch.koplugin",
        zipball_url="https://api.github.com/repos/right9code/annas-fetch.koplugin/zipball/v1.1.0",
        category="books",
        min_koreader="2024.07",
        size_estimate_kb=85,
        license="GPL-3.0",
        install_note="Kræver Wi-Fi. Brug DNS 1.1.1.1 hvis downloads fejler.",
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

