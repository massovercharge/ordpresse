"""Robust DDO (Den Danske Ordbog) web scraper and parser for Priority 2 de-truncation.

Handles:
- Full extraction of complete, untruncated DDO definitions from modern (.modern-definition-box)
  and legacy (.definitionBox) DOM layouts.
- Rejection of dummy placeholder pages containing 'Dette ord har foreløbig ingen betydningsangivelse'.
- Extracting clean literary example quotes (max 1 per sense) and stripping '|' delimiter noise.
- Normalizing POS tags to canonical format.
"""

import logging
import re
import time
from typing import List, Optional, Set
from urllib.parse import quote
from bs4 import BeautifulSoup
import requests

from ordpresse.config import (
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem

logger = logging.getLogger(__name__)

DDO_BASE_URL = "https://ordnet.dk/ddo/ordbog?query="

FORBIDDEN_DDO_SUBSTRINGS = (
    "Dette ord har foreløbig ingen betydningsangivelse",
    "ingen betydningsangivelse",
)


class DDOScraperRateLimitError(Exception):
    """Raised when ordnet.dk returns HTTP 429 or a Cloudflare challenge."""
    pass


class DDOScraper:
    """Scraper for Den Danske Ordbog (ordnet.dk/ddo) modern definitions."""

    def __init__(self, session: Optional[requests.Session] = None):
        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT})

    def scrape_ddo(self, word: str) -> Optional[dict]:
        """Fetch and parse untruncated DDO definition for word."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        soup = self._fetch_ddo_soup(w_clean)
        if not soup:
            return None

        return self._parse_soup_into_entry(w_clean, soup)

    def parse_ddo_html(self, word: str, html: str) -> Optional[dict]:
        """Parse raw DDO HTML (e.g. from bookmarklet or external submission) into a canonical entry."""
        w_clean = word.strip().lower()
        if not w_clean or not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        return self._parse_soup_into_entry(w_clean, soup)

    def _fetch_ddo_soup(self, word: str) -> Optional[BeautifulSoup]:
        """Fetch DDO HTML trying direct query parameter and path fallback."""
        urls_to_try = [
            f"https://ordnet.dk/ddo/ordbog/{quote(word)}",
            f"{DDO_BASE_URL}{quote(word)}",
        ]
        for url in urls_to_try:
            try:
                time.sleep(REQUEST_DELAY_SECONDS)
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    logger.warning(f"DDO scraper received HTTP 429 Too Many Requests for {url}")
                    raise DDOScraperRateLimitError(f"HTTP 429 Too Many Requests for {url}")
                if resp.status_code == 403 and ("cloudflare" in resp.text.lower() or "just a moment" in resp.text.lower()):
                    logger.warning(f"DDO scraper hit Cloudflare challenge for {url}")
                    raise DDOScraperRateLimitError(f"Cloudflare challenge encountered for {url}")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    if soup.select(".modern-definition-box, .definitionBox, .artikel, main"):
                        return soup
            except DDOScraperRateLimitError:
                raise
            except Exception as e:
                logger.debug(f"Failed to fetch DDO for {url}: {e}")
        return None

    def _parse_soup_into_entry(self, w_clean: str, soup: BeautifulSoup) -> Optional[dict]:
        # Reject unfinished DDO placeholder pages
        page_text = soup.get_text(" ", strip=True)
        if any(bad in page_text for bad in FORBIDDEN_DDO_SUBSTRINGS):
            return None

        articles = soup.find_all("div", class_="artikel")
        if not articles:
            articles = soup.find_all("article")
        if not articles:
            main_el = soup.find("main")
            if main_el:
                articles = [main_el]
            else:
                articles = [soup]

        # Select target article matching the queried word if multiple exist
        target_art = articles[0]
        detected_headword = w_clean
        for art in articles:
            hw_tag = art.find(class_=re.compile(r"match|headline", re.IGNORECASE)) or art.find(["h1", "h2"])
            if hw_tag:
                hw_txt = re.sub(r"^[0-9.]+\s*", "", hw_tag.get_text(" ", strip=True))
                hw_txt = re.sub(r"[,\s\d]+$", "", hw_txt).strip().lower()
                if hw_txt == w_clean:
                    target_art = art
                    detected_headword = hw_txt
                    break

        # Extract POS
        pos = ""
        pos_tag = (
            target_art.find(class_=re.compile(r"part-of-speech|pos|ordklasse", re.IGNORECASE))
            or target_art.find("span", class_="allow-glossing")
        )
        if pos_tag:
            raw_pos = pos_tag.get_text(" ", strip=True)
            raw_pos = re.sub(r"\s+", " ", raw_pos).strip()
            raw_pos = re.sub(rf"^{re.escape(w_clean)}\s*", "", raw_pos, flags=re.IGNORECASE).strip()
            pos = normalize_pos(raw_pos)

        # Extract modern definitions
        def_nodes = target_art.select(".modern-definition-box, .definitionBox")
        if not def_nodes:
            def_nodes = target_art.select(".modern-definition, .definition, .betydning")

        modern_senses: List[SenseItem] = []
        seen_defs: Set[str] = set()

        for dn in def_nodes:
            def_text = dn.get_text(" ", strip=True)
            def_text = re.sub(r"\s+", " ", def_text).strip()
            def_text = re.sub(r"\s+([,.;:!)])", r"\1", def_text)

            if not def_text or len(def_text) < 4:
                continue
            if any(bad in def_text for bad in FORBIDDEN_DDO_SUBSTRINGS):
                continue

            def_key = def_text.lower().strip()
            if def_key in seen_defs:
                continue
            seen_defs.add(def_key)

            # Find nearby example quote in parent or container
            quote_text = None
            parent_container = dn.find_parent(class_=re.compile(r"modern-row|definition|betydning|box", re.IGNORECASE)) or target_art
            citat_tag = None
            if parent_container:
                citat_tag = parent_container.find(class_=re.compile(r"modern-citat\b|\bcitat\b", re.IGNORECASE))
                if not citat_tag:
                    citat_tag = parent_container.find(class_=re.compile(r"inline-example|example", re.IGNORECASE))
            if citat_tag:
                citat_soup = BeautifulSoup(str(citat_tag), "html.parser")
                for s in citat_soup.find_all(class_=re.compile(r"modern-source|source|kilde|author", re.IGNORECASE)):
                    s.decompose()
                raw_q = citat_soup.get_text(" ", strip=True)
                raw_q = re.sub(r"\s+", " ", raw_q).strip()
                if raw_q and len(raw_q) > 4:
                    quote_text = raw_q.split("|")[0].strip()

            modern_senses.append(
                SenseItem(
                    definition=def_text,
                    quote=quote_text,
                    source_tag="DDO",
                )
            )
            if len(modern_senses) >= 4:
                break

        if not modern_senses:
            return None

        # Build canonical entry
        all_syns = {w_clean, detected_headword}
        canonical = CanonicalEntry(
            headword=detected_headword,
            pos=pos or "adj.",
            inflections=all_syns,
            summary_hook="",  # Rule: No synthesized summary hook for modern DDO entries
            modern_senses=modern_senses,
            sources_used=["DDO"],
        )

        canonical_html = render_canonical_html(canonical)
        return {
            "headword": detected_headword,
            "html_content": canonical_html,
            "synonyms": sorted(list(all_syns)),
            "canonical_entry": canonical,
            "definitions": [s.definition for s in modern_senses],
            "source": "ddo",
        }


def parse_ddo_html(word: str, html: str) -> Optional[dict]:
    """Parse DDO HTML string directly."""
    scraper = DDOScraper()
    return scraper.parse_ddo_html(word, html)
