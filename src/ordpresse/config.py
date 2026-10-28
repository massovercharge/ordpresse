"""Configuration settings, paths, URLs, and constants for ordpresse."""

import os
from pathlib import Path

# Base project paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.getenv("ORDPRESSE_DATA_DIR", str(BASE_DIR / "data")))
RAW_DATA_DIR = DATA_DIR / "raw"
OUTPUT_DIR = DATA_DIR / "output"

# SQLite databases
DATASETS_DB_PATH = DATA_DIR / "datasets.sqlite3"
CACHE_DB_PATH = DATA_DIR / "cache.sqlite3"

# Ensure essential directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Official Dataset URLs
COR_BASE_URL = os.getenv("COR_BASE_URL", "https://ordregister.dk/files/cor1.5.1.0.tsv")
COR_BASE_FALLBACK_URL = "https://ordregister.dk/files/cor1.01b.tsv"
COR_EXT_URL = os.getenv("COR_EXT_URL", "https://ordregister.dk/files/corext1.0.tsv")
COR_SEM_URL = os.getenv("COR_SEM_URL", "https://ordregister.dk/files/cor.sem.1.0.tsv")
COR_SEM_EXT_URL = os.getenv("COR_SEM_EXT_URL", "https://ordregister.dk/files/cor.sem.ext.1.0.tsv")
ODS_FULLFORMS_URL = os.getenv("ODS_FULLFORMS_URL", "https://korpus.dsl.dk/download/ods-fullform.zip")

# Scraper endpoints
ODS_BASE_URL = "https://ordnet.dk/ods/ordbog?query="
NAOB_BASE_URL = "https://naob.no/ordbok/"

# HTTP client parameters and scraping limits
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 15
REQUEST_DELAY_SECONDS = float(os.getenv("ORDPRESSE_REQUEST_DELAY", "0.5"))  # Polite crawl delay between requests
MAX_ONLINE_SCRAPE_BATCH = int(os.getenv("ORDPRESSE_MAX_ONLINE_BATCH", "200"))  # Max new words fetched online per build
MIN_EPUB_WORD_FREQUENCY_FOR_SCRAPING = int(os.getenv("ORDPRESSE_MIN_WORD_FREQ", "2"))  # Min occurrences to auto-scrape

# StarDict metadata defaults with compliant DSL/CST attribution
DEFAULT_BOOKNAME = "Den Danske Ordpresse (1700-nu)"
DEFAULT_AUTHOR = "DSL / CST / Den Danske Ordpresse"
DEFAULT_DESCRIPTION = (
    "Historisk og moderne dansk ordbog til KOReader. Baseret på sprogressourcer fra "
    "Det Danske Sprog- og Litteraturselskab (dsl.dk), COR (ordregister.dk, CC0) og ODS (ordnet.dk/ods). "
    "Dette uafhængige open-source projekt er hverken støttet eller godkendt af DSL."
)

# Web server settings
DEFAULT_PORT = int(os.getenv("PORT", "8088"))
DEFAULT_HOST = os.getenv("HOST", "0.0.0.0")
