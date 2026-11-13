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

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        request_delay: Optional[float] = None,
    ):
        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT})
        self.request_delay = REQUEST_DELAY_SECONDS if request_delay is None else request_delay

    def scrape_ddo(self, word: str, max_senses: int = 8) -> Optional[dict]:
        """Fetch and parse untruncated DDO definition for word."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        soup = self._fetch_ddo_soup(w_clean)
        if not soup:
            return None

        return self._parse_soup_into_entry(w_clean, soup, max_senses=max_senses)

    def parse_ddo_html(self, word: str, html: str, max_senses: int = 8) -> Optional[dict]:
        """Parse raw DDO HTML (e.g. from bookmarklet or external submission) into a canonical entry."""
        w_clean = word.strip().lower()
        if not w_clean or not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        return self._parse_soup_into_entry(w_clean, soup, max_senses=max_senses)

    def _fetch_ddo_soup(self, word: str) -> Optional[BeautifulSoup]:
        """Fetch DDO HTML trying direct path and query parameter fallback."""
        urls_to_try = [
            f"https://ordnet.dk/ddo/ordbog/{quote(word)}",
            f"{DDO_BASE_URL}{quote(word)}",
        ]
        for url in urls_to_try:
            try:
                if self.request_delay > 0:
                    time.sleep(self.request_delay)
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    logger.warning(f"DDO scraper received HTTP 429 Too Many Requests for {url}")
                    raise DDOScraperRateLimitError(f"HTTP 429 Too Many Requests for {url}")
                if resp.status_code == 403 and ("cloudflare" in resp.text.lower() or "just a moment" in resp.text.lower()):
                    logger.warning(f"DDO scraper hit Cloudflare challenge for {url}")
                    raise DDOScraperRateLimitError(f"Cloudflare challenge encountered for {url}")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    if soup.select(".modern-definition-box, .definitionBox, .artikel"):
                        return soup
                    if soup.find("main") and not any(
                        msg in resp.text.lower() for msg in ("matcher ingen opslag", "intet opslag")
                    ):
                        return soup
                    # Direct HTTP 200 page confirmed no article matches -> no need to retry ?query=
                    return None
            except DDOScraperRateLimitError:
                raise
            except requests.exceptions.ConnectionError as e:
                logger.debug(f"Connection error fetching DDO for {url}: {e}")
                break
            except Exception as e:
                logger.debug(f"Failed to fetch DDO for {url}: {e}")
        return None

    def _extract_article_headword(self, art, fallback: str) -> str:
        """Extract canonical headword string from a DDO .artikel node."""
        hw_tag = (
            art.find(class_=re.compile(r"modern-match|\bmatch\b|\bheadline\b", re.IGNORECASE))
            or art.find("h1")
            or art.find("h2")
        )
        if not hw_tag:
            return fallback
        hw_txt = re.sub(r"^[0-9.]+\s*", "", hw_tag.get_text(" ", strip=True))
        hw_txt = re.sub(r"[,\s\d]+$", "", hw_txt).strip().lower()
        return hw_txt or fallback

    def _extract_article_inflections(self, art) -> Set[str]:
        """Extract authentic inflected forms from DDO #id-boj / .flex-table tables."""
        forms: Set[str] = set()
        for td in art.select("#id-boj .flex-table td, .flex-table td"):
            txt = re.sub(r"\s+", " ", td.get_text("", strip=True)).strip()
            if txt and txt not in ("-", "–") and len(txt) >= 2:
                forms.add(txt.lower())
        return forms

    def _parse_single_article(
        self, w_clean: str, target_art, detected_headword: str, max_senses: int = 8
    ) -> Optional[dict]:
        """Parse one DDO .artikel node into a CanonicalEntry + metadata."""
        # Extract POS
        pos = ""
        pos_tag = target_art.find(class_="part-of-speech")
        if pos_tag:
            parent = pos_tag.parent
            parent_cls = parent.get("class") or [] if parent else []
            if parent and "allow-glossing" in parent_cls and "modern-top-row" not in parent_cls:
                raw_pos = parent.get_text(" ", strip=True)
            else:
                raw_pos = pos_tag.get_text(" ", strip=True)
        else:
            pos_fallback = (
                target_art.find(class_=re.compile(r"\bpos\b|ordklasse", re.IGNORECASE))
                or target_art.find("span", class_="allow-glossing")
            )
            raw_pos = pos_fallback.get_text(" ", strip=True) if pos_fallback else ""

        if raw_pos:
            raw_pos = re.sub(r"\s+", " ", raw_pos).strip()
            raw_pos = re.sub(rf"^{re.escape(detected_headword)}\s*", "", raw_pos, flags=re.IGNORECASE).strip()
            raw_pos = re.sub(rf"^{re.escape(w_clean)}\s*", "", raw_pos, flags=re.IGNORECASE).strip()
            pos = normalize_pos(raw_pos)

        # Extract phonetic IPA if present
        phonetic = ""
        lyd_el = target_art.select_one("#id-udt .lydskrift")
        if lyd_el:
            m_ipa = re.search(r"\[([^\]]+)\]", lyd_el.get_text(" ", strip=True))
            if m_ipa:
                phonetic = re.sub(r"\s+", " ", m_ipa.group(1)).strip()

        # Extract inflections from flex-table
        flex_forms = self._extract_article_inflections(target_art)

        # Extract modern definitions
        def_nodes = target_art.select(".modern-definition-box, .definitionBox")
        if not def_nodes:
            def_nodes = target_art.select(".modern-definition, .definition, .betydning")

        modern_senses: List[SenseItem] = []
        seen_defs: Set[str] = set()

        for dn in def_nodes:
            dn_copy = BeautifulSoup(str(dn), "html.parser")

            # Extract inline usage label (SPROGBRUG ...) without shouting "SPROGBRUG"
            sense_label: Optional[str] = None
            lbl_node = dn_copy.find(class_="modern-inline-label")
            if lbl_node:
                label_parts = []
                sib = lbl_node.find_next_sibling()
                if sib:
                    for ds in sib.find_all(class_="dividerSmall"):
                        ds.replace_with(", ")
                    sib_txt = re.sub(r"\s+", " ", sib.get_text(" ", strip=True)).strip()
                    sib_txt = re.sub(r"\s*,\s*", ", ", sib_txt).strip(" ,")
                    if sib_txt:
                        label_parts.append(sib_txt)
                    sib.decompose()
                lbl_node.decompose()
                if label_parts:
                    sense_label = "; ".join(label_parts)

            # Decompose any nested citation tags inside dn_copy so quotes don't duplicate inside def_text
            for c_nested in dn_copy.find_all(class_=re.compile(r"modern-citat\b|\bcitat\b", re.IGNORECASE)):
                c_nested.decompose()

            # Replace dividerDot with readable separator instead of gluing phrases
            for dot in dn_copy.find_all(class_="dividerDot"):
                dot.replace_with(" · ")

            def_text = dn_copy.get_text(" ", strip=True)
            def_text = re.sub(r"\s+", " ", def_text).strip()
            def_text = re.sub(r"\s+([,.;:!)])", r"\1", def_text)
            def_text = re.sub(r"^(?:\s*·\s*)+|(?:\s*·\s*)+$", "", def_text).strip()

            if not def_text or len(def_text) < 4:
                continue
            if any(bad in def_text for bad in FORBIDDEN_DDO_SUBSTRINGS):
                continue

            def_key = def_text.lower().strip()
            if def_key in seen_defs:
                continue
            seen_defs.add(def_key)

            # Find nearby example quote in parent sense block or container
            quote_text = None
            parent_container = (
                dn.find_parent(class_=re.compile(r"modern-sense-block|modern-row|definition|betydning|box", re.IGNORECASE))
                or target_art
            )
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
                    label=sense_label,
                    quote=quote_text,
                    source_tag="DDO",
                )
            )
            if len(modern_senses) >= max_senses:
                break

        if not modern_senses:
            return None

        # Build canonical entry
        from ordpresse.orthography import sanitize_headword
        clean_hw, extra_syns = sanitize_headword(detected_headword)
        final_hw = (clean_hw or w_clean).lower()
        all_syns = {w_clean, final_hw} | flex_forms | extra_syns
        all_syns.discard("")
        canonical = CanonicalEntry(
            headword=final_hw,
            pos=pos,
            inflections=all_syns,
            summary_hook="",  # Rule: No synthesized summary hook for modern DDO entries
            modern_senses=modern_senses,
            sources_used=["DDO"],
            phonetic=phonetic,
        )

        canonical_html = render_canonical_html(canonical)
        return {
            "headword": final_hw,
            "html_content": canonical_html,
            "synonyms": sorted(list(all_syns)),
            "canonical_entry": canonical,
            "definitions": [s.definition for s in modern_senses],
            "phonetic": phonetic,
            "source": "ddo",
        }

    def _parse_soup_into_entry(self, w_clean: str, soup: BeautifulSoup, max_senses: int = 8) -> Optional[dict]:
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

        # Inspect each article's headword and flex-table inflections
        candidates = []
        for idx, art in enumerate(articles):
            hw_txt = self._extract_article_headword(art, w_clean)
            infs = self._extract_article_inflections(art)
            exact_hw = (hw_txt == w_clean or hw_txt.replace("-", "") == w_clean.replace("-", ""))
            matches_inf = (w_clean in infs)
            candidates.append((art, hw_txt, exact_hw, matches_inf, idx))

        # Prioritize exact headword match, then inflected match, then first article
        primary_cand = next((c for c in candidates if c[2]), None)
        if primary_cand is None:
            primary_cand = next((c for c in candidates if c[3]), None)
        if primary_cand is None:
            primary_cand = candidates[0]

        primary_art, primary_hw, _, _, primary_idx = primary_cand
        primary_parsed = self._parse_single_article(
            w_clean, primary_art, detected_headword=primary_hw, max_senses=max_senses
        )
        if not primary_parsed:
            return None

        # Collect sibling homographs / matching inflected articles on the same page
        additional_entries: List[CanonicalEntry] = []
        additional_phonetics: dict[str, str] = {}
        seen_signatures = {
            (primary_parsed["headword"], primary_parsed["canonical_entry"].pos, tuple(primary_parsed["definitions"][:2]))
        }
        for art, hw_txt, exact_hw, matches_inf, idx in candidates:
            if idx == primary_idx:
                continue
            if not (exact_hw or matches_inf or hw_txt == primary_hw):
                continue
            parsed_sib = self._parse_single_article(
                w_clean, art, detected_headword=hw_txt, max_senses=max_senses
            )
            if parsed_sib:
                sig = (
                    parsed_sib["headword"],
                    parsed_sib["canonical_entry"].pos,
                    tuple(parsed_sib["definitions"][:2]),
                )
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    additional_entries.append(parsed_sib["canonical_entry"])
                    if parsed_sib.get("phonetic"):
                        additional_phonetics[f"{parsed_sib['headword']}:{parsed_sib['canonical_entry'].pos}"] = parsed_sib["phonetic"]
                    if len(additional_entries) >= 2:
                        break

        primary_parsed["additional_entries"] = additional_entries
        primary_parsed["additional_phonetics"] = additional_phonetics
        return primary_parsed


def parse_ddo_html(word: str, html: str) -> Optional[dict]:
    """Parse DDO HTML string directly."""
    scraper = DDOScraper()
    return scraper.parse_ddo_html(word, html)
