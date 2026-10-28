"""Polite ODS and NAOB web scrapers with SQLite caching, multi-homograph merging, and citation cleaning."""

import logging
import re
import time
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import requests

from ordpresse.config import (
    NAOB_BASE_URL,
    ODS_BASE_URL,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from ordpresse.db import Database
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import generate_orthographic_synonyms
from ordpresse.sources.nordic_1800s import (
    NORDIC_1800S_ENTRIES,
    get_nordic_1800s_entry,
)

from ordpresse.sources.molbech_1859 import get_molbech_1859_entry
from ordpresse.sources.ods_scraper import ODSScraper

logger = logging.getLogger(__name__)

# Author expansions for literary quotes in ODS
AUTHOR_EXPANSIONS = [
    (re.compile(r"\bBlich\.(?!\w)", re.IGNORECASE), "St. St. Blicher"),
    (re.compile(r"\bPont\.(?!\w)", re.IGNORECASE), "H. Pontoppidan"),
    (re.compile(r"\bHolb\.(?!\w)", re.IGNORECASE), "L. Holberg"),
    (re.compile(r"\bGrundtv\.(?!\w)", re.IGNORECASE), "N.F.S. Grundtvig"),
    (re.compile(r"\bAarest\.(?!\w)", re.IGNORECASE), "Emil Aarestrup"),
    (re.compile(r"\bHeib\.(?!\w)", re.IGNORECASE), "J.L. Heiberg"),
    (re.compile(r"\bOehl\.(?!\w)", re.IGNORECASE), "A. Oehlenschläger"),
    (re.compile(r"\bIngem\.(?!\w)", re.IGNORECASE), "B.S. Ingemann"),
    (re.compile(r"\bPal\.-Müll\.(?!\w)", re.IGNORECASE), "Fr. Paludan-Müller"),
    (re.compile(r"\bWinth\.(?!\w)", re.IGNORECASE), "Chr. Winther"),
    (re.compile(r"\bDrachm\.(?!\w)", re.IGNORECASE), "H. Drachmann"),
    (re.compile(r"\bSchand\.(?!\w)", re.IGNORECASE), "Sophus Schandorph"),
    (re.compile(r"\bGoldschm\.(?!\w)", re.IGNORECASE), "M. Goldschmidt"),
    (re.compile(r"\bHauch\.(?!\w)", re.IGNORECASE), "Carsten Hauch"),
    (re.compile(r"\bMolb\.(?!\w)", re.IGNORECASE), "Chr. Molbech"),
    (re.compile(r"\bFeilb\.(?!\w)", re.IGNORECASE), "H.F. Feilberg"),
]

# Bibliographic sigils to strip from definitions and quotes
BIBLIO_PATTERNS = [
    re.compile(r"\bMoth\.[A-Za-z0-9.\-]+", re.IGNORECASE),
    re.compile(r"\bChrV\.[0-9.\-]+", re.IGNORECASE),
    re.compile(r"\bVSO\b|\bV\.S\.O\.", re.IGNORECASE),
    re.compile(r"\bDL\.[0-9.\-]+", re.IGNORECASE),
    re.compile(r"\b[IVXLCDM]+\.?\s*\d+(?:[.,]\s*\d+)*"),
    re.compile(r"\b(?:sp|s)\.\s*\d+", re.IGNORECASE),
]


def sanitize_ods_text(text: str) -> str:
    """Strip bibliographic sigils and expand author abbreviations in quotes and definitions."""
    if not text:
        return ""
    # Expand author abbreviations first
    for pattern, full_name in AUTHOR_EXPANSIONS:
        text = pattern.sub(full_name, text)
    # Strip bibliographic sigils
    for pattern in BIBLIO_PATTERNS:
        text = pattern.sub("", text)
    # Collapse excess whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Pre-populate SEED_HISTORICAL_ENTRIES with all 35+ cornerstone terms from nordic_1800s.py
SEED_HISTORICAL_ENTRIES: Dict[str, dict] = {}
for _hw, _entry in NORDIC_1800S_ENTRIES.items():
    _syns = sorted(list(_entry.inflections | set(_entry.historical_spellings)))
    SEED_HISTORICAL_ENTRIES[_hw] = {
        "headword": _entry.headword,
        "synonyms": _syns,
        "html_content": render_canonical_html(_entry),
        "canonical_entry": _entry,
    }


class HistoricalScraper:
    """Scrapes ODS and NAOB with cache fallbacks, multi-homograph merging, and citation cleaning."""

    def __init__(self, db: Database):
        self.db = db
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.ods_scraper = ODSScraper(session=self.session)

    def fetch_word_definition(self, word: str, allow_remote: bool = True) -> Optional[dict]:
        """Fetch word definition via Cache -> Nordic 1800s -> Seed -> ODS -> Molbech -> NAOB chain."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        # 1. Check local cache
        cached = self.db.get_cached(w_clean)
        if cached:
            return cached

        # 2. Check curated 1800s vocabulary (35+ Moberg and historical cornerstone terms)
        nordic_entry = get_nordic_1800s_entry(w_clean)
        if nordic_entry:
            html = render_canonical_html(nordic_entry)
            all_syns = generate_orthographic_synonyms(
                nordic_entry.headword,
                list(nordic_entry.inflections) + nordic_entry.historical_spellings,
            )
            self.db.set_cached(
                word=w_clean,
                source="nordic_1800s",
                headword=nordic_entry.headword,
                html_content=html,
                synonyms=all_syns,
            )
            return {
                "word": w_clean,
                "source": "nordic_1800s",
                "headword": nordic_entry.headword,
                "html_content": html,
                "synonyms": all_syns,
                "canonical_entry": nordic_entry,
            }

        # 3. Check built-in historical seed entries
        for seed_key, seed_data in SEED_HISTORICAL_ENTRIES.items():
            if w_clean == seed_key or w_clean in [s.lower() for s in seed_data["synonyms"]]:
                all_syns = generate_orthographic_synonyms(seed_data["headword"], seed_data["synonyms"])
                self.db.set_cached(
                    word=w_clean,
                    source="seed",
                    headword=seed_data["headword"],
                    html_content=seed_data["html_content"],
                    synonyms=all_syns,
                )
                return {
                    "word": w_clean,
                    "source": "seed",
                    "headword": seed_data["headword"],
                    "html_content": seed_data["html_content"],
                    "synonyms": all_syns,
                    "canonical_entry": seed_data.get("canonical_entry"),
                }

        # 4. Scrape ODS (Ordbog over det danske Sprog) online
        if allow_remote:
            ods_result = self._scrape_ods(w_clean)
            if ods_result:
                all_syns = generate_orthographic_synonyms(ods_result["headword"], ods_result["synonyms"])
                self.db.set_cached(
                    word=w_clean,
                    source="ods",
                    headword=ods_result["headword"],
                    html_content=ods_result["html_content"],
                    synonyms=all_syns,
                )
                return {
                    "word": w_clean,
                    "source": "ods",
                    "headword": ods_result["headword"],
                    "html_content": ods_result["html_content"],
                    "synonyms": all_syns,
                    "canonical_entry": ods_result.get("canonical_entry"),
                }

        # 5. Check Molbech (1859) offline lexicon fallback
        molbech_entry = get_molbech_1859_entry(w_clean)
        if molbech_entry:
            html = render_canonical_html(molbech_entry)
            all_syns = generate_orthographic_synonyms(
                molbech_entry.headword,
                list(molbech_entry.inflections) + molbech_entry.historical_spellings,
            )
            self.db.set_cached(
                word=w_clean,
                source="molbech",
                headword=molbech_entry.headword,
                html_content=html,
                synonyms=all_syns,
            )
            return {
                "word": w_clean,
                "source": "molbech",
                "headword": molbech_entry.headword,
                "html_content": html,
                "synonyms": all_syns,
                "canonical_entry": molbech_entry,
            }

        # 6. Scrape NAOB fallback online
        if allow_remote:
            naob_result = self._scrape_naob(w_clean)
            if naob_result:
                all_syns = generate_orthographic_synonyms(naob_result["headword"], naob_result["synonyms"])
                self.db.set_cached(
                    word=w_clean,
                    source="naob",
                    headword=naob_result["headword"],
                    html_content=naob_result["html_content"],
                    synonyms=all_syns,
                )
                return {
                    "word": w_clean,
                    "source": "naob",
                    "headword": naob_result["headword"],
                    "html_content": naob_result["html_content"],
                    "synonyms": all_syns,
                    "canonical_entry": naob_result.get("canonical_entry"),
                }

        return None

    def _scrape_ods(self, word: str) -> Optional[dict]:
        """Scrape ODS articles using robust ODSScraper."""
        return self.ods_scraper.scrape_ods(word)

    def _scrape_naob(self, word: str) -> Optional[dict]:
        """Scrape NAOB (Det Norske Akademis ordbok) for Dano-Norwegian 19th c. terms."""
        url = f"{NAOB_BASE_URL}{word}"
        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            article = soup.find("article") or soup.find("div", class_="article")
            if not article:
                return None

            h1 = article.find("h1")
            headword = h1.get_text(strip=True) if h1 else word.capitalize()
            headword_clean = re.sub(r"[,\s\d]+$", "", headword).strip().lower()

            pos_elem = article.find(class_=re.compile(r"pos|ordklasse"))
            pos = normalize_pos(pos_elem.get_text(strip=True)) if pos_elem else ""

            def_nodes = article.find_all(class_=re.compile(r"definition|betydning|expl"))
            senses: List[SenseItem] = []
            for dn in def_nodes:
                text = sanitize_ods_text(dn.get_text(strip=True))
                if text and len(text) > 10:
                    senses.append(SenseItem(definition=text, label="nordisk", source_tag="NAOB"))
                    if len(senses) >= 3:
                        break

            if not senses:
                senses.append(
                    SenseItem(
                        definition=sanitize_ods_text(article.get_text(strip=True))[:180],
                        label="nordisk",
                        source_tag="NAOB",
                    )
                )

            summary_hook = senses[0].definition[:160]

            canonical = CanonicalEntry(
                headword=headword_clean,
                pos=pos,
                inflections={word, headword_clean},
                summary_hook=summary_hook,
                historical_senses=senses[:3],
                realia_note="Supplerende opslag fra NAOB for fælles 1800-tals dansk-norsk sprogbrug.",
                sources_used=["NAOB"],
            )

            canonical_html = render_canonical_html(canonical)

            return {
                "headword": headword_clean,
                "html_content": canonical_html,
                "synonyms": [word, headword_clean],
                "canonical_entry": canonical,
            }
        except Exception as exc:
            logger.warning(f"Error scraping NAOB for '{word}': {exc}")
            return None
