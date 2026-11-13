"""Robust ODS (Ordbog over det danske Sprog) web scraper and parser.

Handles:
- Full extraction of ODS definitions from .betydning, .semem, and .artikel/.tekstmedium
- Following cross-reference articles ('se X', 'se u. Y')
- Multi-homograph merging (1. Lægd, 2. Lægd)
- Expanding literary author abbreviations and cleaning bibliographic sigils
- Strict prohibition of dummy placeholder definitions
"""

import logging
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qs, quote, unquote, urlparse
from bs4 import BeautifulSoup
import requests

from ordpresse.config import (
    ODS_BASE_URL,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.synthesizer import truncate_summary_hook

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
    (re.compile(r"\bBrandes\.(?!\w)", re.IGNORECASE), "Georg Brandes"),
    (re.compile(r"\bIbsen\.(?!\w)", re.IGNORECASE), "Henrik Ibsen"),
    (re.compile(r"\bKierk\.(?!\w)", re.IGNORECASE), "Søren Kierkegaard"),
    (re.compile(r"\bHCAnd\.(?!\w)", re.IGNORECASE), "H.C. Andersen"),
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


FORBIDDEN_DEFINITION_SUBSTRINGS = (
    "Historisk opslag for",
    "ingen betydningsangivelse",
    "SITATER",
    "EKSEMPLER",
    "ORD I NÆRHEDEN",
    "Mente du:",
)

COMMON_DANISH_DEF_WORDS = {
    "som", "der", "at", "i", "paa", "på", "af", "til", "eller", "ell.", "el.",
    "en", "et", "og", "om", "for", "med", "fra", "ved", "mod", "over", "under",
    "især", "egl.", "betegner", "angiver", "udtrykker", "person", "genstand", "handling"
}


def sanitize_ods_text(text: str) -> str:
    """Strip bibliographic sigils, expand author abbreviations, and repair OCR/spacing glitches."""
    if not text:
        return ""
    for pattern, full_name in AUTHOR_EXPANSIONS:
        text = pattern.sub(full_name, text)
    for pattern in BIBLIO_PATTERNS:
        text = pattern.sub("", text)

    # Typographic spacing fixes
    text = re.sub(r"([a-zæøåA-ZÆØÅ0-9])\(", r"\1 (", text)
    text = re.sub(r"\)([a-zæøåA-ZÆØÅ0-9])", r") \1", text)
    text = re.sub(r"([a-zæøåA-ZÆØÅ0-9])(ell?\.)", r"\1 \2", text)
    text = re.sub(r"\b(ell?\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", text)
    text = re.sub(r"([a-zæøåA-ZÆØÅ0-9])(jf\.)", r"\1 \2", text)
    text = re.sub(r"\b(jf\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", text)
    text = re.sub(r"\b(m\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", text)
    text = re.sub(r"\b(f\.eks\.|fx)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", text)

    # Whitespace cleanup around punctuation
    text = re.sub(r"\s+([,.;:!)])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


GRAMMATICAL_ROUTING_RE = re.compile(
    r"\b(?:som\s+(?:verbum|adjektiv|substantiv|adverbium|vb|adj|sb|adv)\.?|"
    r"brugt\s+som\s+(?:adjektiv|substantiv|verbum|adverbium|adj|sb|vb|adv)\.?|"
    r"(?:i\s+)?perf\.?\s*part\.?|(?:i\s+)?præs\.?\s*part\.?|"
    r"i\s+pass\.?|refl\.?|abs\.?|intr\.?|tr\.?|part\.?|"
    r"jf\.\s*(?:dial\.|u\.|under|se)?|"
    r"som\s+part\.?)\b",
    re.IGNORECASE,
)


USAGE_LABELS_RE = re.compile(
    r"\b(?:hist|nu\s+sj|foræld|dial|sj|egl|overf|fig|bogst|vulg|poet|sjæld)\.?",
    re.IGNORECASE,
)


def is_meta_header_not_definition(text: str) -> bool:
    """Return True if text is a grammatical section header or routing phrase rather than a definition."""
    if not text:
        return True
    t = text.strip()
    if not t:
        return True

    cleaned = re.sub(r"^[0-9]+[.)]\s*", "", t)
    # Strip trailing unclosed reference parentheses like '(jf.'
    cleaned = re.sub(r"\(\s*(?:jf\.|se)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned_words = cleaned.replace("(", " ").replace(")", " ").strip()

    has_routing = bool(GRAMMATICAL_ROUTING_RE.search(cleaned_words))
    lexical = GRAMMATICAL_ROUTING_RE.sub("", cleaned_words)
    lexical = USAGE_LABELS_RE.sub("", lexical)
    lexical = re.sub(r"[^\w\s]", "", lexical).strip()

    if has_routing and len(lexical) < 12:
        return True

    if len(lexical) < 4:
        return True

    return False


DANGLING_SYNTAX_TAIL_RE = re.compile(
    r"[;,]\s*(?:især|bl\.?\s*a\.?|ofte|sjældnere)?\s*(?:og\s+egl\.\s*)?i\s+forb\.\s*(?:m\.)?(?:\s*præp\.?)?(?:\s+[a-zæøåA-ZÆØÅ\-]+)?\.?$",
    re.IGNORECASE,
)


def clean_ods_definition_text(text: str) -> str:
    """Clean grammatical prefixes, strip trailing cross-references, and ensure balanced parentheses."""
    if not text:
        return ""
    t = text.strip()

    # Strip leading routing prefixes like 'part. udlevet brugt som adj., (jf. aflevet)'
    t = re.sub(
        r"^(?:part\.?\s+)?(?:[a-zæøåA-ZÆØÅ\-]+\s+)?brugt\s+som\s+(?:adj\.|sb\.|adjektiv|substantiv)[,;]?\s*(?:\([^)]*\))?\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    # Strip leading 'til II. arm 2:'
    t = re.sub(r"^til\s+[IVXLCDM\d.]+\s+[a-zæøåA-ZÆØÅ\-]+\s+\d+:\s*", "", t, flags=re.IGNORECASE)
    # Strip leading '(jf. ...)'
    t = re.sub(r"^\(\s*(?:jf\.|se)\b[^)]*\)\s*", "", t, flags=re.IGNORECASE)

    # Strip trailing cross-reference parentheses like (jf. ...) or (jf.
    while True:
        prev = t
        t = re.sub(r"[,;]?\s*\(\s*(?:jf\.|se\b)[^)]*\)?\s*$", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"[,;]?\s*\(\s*(?:jf\.|se\b).*$", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"[,;]?\s*jf\.\s*$", "", t, flags=re.IGNORECASE).strip()
        if t == prev:
            break

    # Strip any dangling opening parenthesis at the end
    t = re.sub(r"[,;]?\s*\([^)]*$", "", t).strip()

    # Strip dangling syntax tails caused by example removal (; især i forb. m. præp., ; især i forb. m., ; bl. a. i forb.)
    if DANGLING_SYNTAX_TAIL_RE.search(t):
        t = DANGLING_SYNTAX_TAIL_RE.sub("", t).strip().rstrip(";,").strip()
        if t and not t.endswith((".", "!", "?")):
            t += "."

    # Strip trailing empty parentheses left by removed cross-refs/sources (e.g. 'knyttet til et kapel ()')
    t = re.sub(r"\s*\(\s*\)", "", t)

    # Balance parentheses if any unclosed '(' remains
    open_count = t.count("(")
    close_count = t.count(")")
    if open_count > close_count:
        t += ")" * (open_count - close_count)
    elif close_count > open_count:
        while t.startswith(")"):
            t = t[1:].lstrip()

    # Trim trailing punctuation and ensure proper ending if needed
    t = re.sub(r"\s+([,.;:!)])", r"\1", t).strip()
    t = re.sub(r":\s*$", ".", t)
    return t


def is_valid_definition(text: str) -> bool:
    """Check if candidate definition is genuine explanatory text and not an author or citation."""
    if not text:
        return False
    t = text.strip()
    if len(t) < 4:
        return False
    if any(bad in t for bad in FORBIDDEN_DEFINITION_SUBSTRINGS):
        return False

    clean_no_label = re.sub(r"^\([^)]+\)\s*", "", t).strip()

    # Reject if string is simply an author name or expansion
    for _, full_name in AUTHOR_EXPANSIONS:
        if clean_no_label.lower() == full_name.lower() or clean_no_label.lower() == f"{full_name.lower()}.":
            return False

    # Reject bibliographic / publication citations (e.g. "Goldschm.. Aakj..", "Jydske Tidende.")
    if (
        re.search(r"\b[A-ZÆØÅ][a-zæøå]*\.\.", clean_no_label)
        or re.search(r"\.\.\s*$", clean_no_label)
        or re.search(r"\b(?:Saml|Tidende|Ugeskr|Aarb|Oversk)\.", clean_no_label)
    ):
        return False

    # Reject if string is solely an author signature or citation title (e.g. "Anna Larssen Bjørner", "Romneya Coulteri.")
    author_or_title_pattern = re.compile(
        r"^[A-ZÆØÅ][a-zæøåA-ZÆØÅ0-9.\-]+(?:\s+[A-ZÆØÅ][a-zæøåA-ZÆØÅ0-9.\-]+)+\.?$"
    )
    if author_or_title_pattern.match(clean_no_label):
        return False

    return True


class ODSScraperRateLimitError(Exception):
    """Raised when ordnet.dk returns HTTP 429 or a Cloudflare challenge."""
    pass



class ODSScraper:
    """Robust scraper for Ordbog over det danske Sprog (ordnet.dk/ods)."""

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

    def scrape_ods(self, word: str, max_senses: int = 8) -> Optional[dict]:
        """Scrape ODS articles, aggregating all homographs and following cross-references."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        soup = self._fetch_ods_soup(w_clean)
        if not soup:
            return None

        return self._parse_soup_into_entry(w_clean, soup, max_senses=max_senses)

    def parse_ods_html(self, word: str, html: str, max_senses: int = 8) -> Optional[dict]:
        """Parse raw ODS HTML (e.g. from bookmarklet or external submission) into a canonical entry."""
        w_clean = word.strip().lower()
        if not w_clean or not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        return self._parse_soup_into_entry(w_clean, soup, max_senses=max_senses)

    @staticmethod
    def _extract_raw_art_hw(art) -> str:
        """Extract stripped article headline lowercase, preserving trailing '-' for prefix detection."""
        m = art.find("span", class_="match") or art.find("span", class_="headline") or art.find("h1")
        if not m:
            return ""
        hw = m.get_text(" ", strip=True)
        hw = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s+", "", hw, flags=re.IGNORECASE)
        hw = re.sub(r"[,\s\d]+$", "", hw).strip().lower()
        return hw

    def _parse_soup_into_entry(self, w_clean: str, soup: BeautifulSoup, max_senses: int = 8) -> Optional[dict]:
        articles = soup.find_all("div", class_="artikel")
        if not articles:
            if soup.name == "div" and "artikel" in soup.get("class", []):
                articles = [soup]
            else:
                art = soup.find(class_=re.compile(r"\bartikel\b"))
                if art:
                    articles = [art]

        if not articles:
            # Check searchResultBox for redirection link
            res_box = soup.find("div", class_="searchResultBox")
            if res_box:
                first_link = res_box.find("a")
                if first_link and first_link.get("href"):
                    target_url = first_link["href"]
                    if not target_url.startswith("http"):
                        target_url = f"https://ordnet.dk/ods/{target_url.lstrip('/')}"
                    if self.request_delay > 0:
                        time.sleep(self.request_delay)
                    try:
                        r2 = self.session.get(target_url, timeout=REQUEST_TIMEOUT)
                        if r2.status_code == 200:
                            soup = BeautifulSoup(r2.text, "html.parser")
                            articles = soup.find_all("div", class_="artikel")
                    except Exception as e:
                        logger.debug(f"Failed to follow searchResultBox redirection link: {e}")

        if not articles:
            return None

        # Check for DDO dummy/placeholder text inside the article nodes (Phase 3.1)
        art_text = " ".join(a.get_text(" ", strip=True) for a in articles)
        if any(bad in art_text for bad in [
            "ingen betydningsangivelse",
            "Dette ord har foreløbig ingen betydningsangivelse",
            "Historisk opslag for »",
        ]):
            from ordpresse.orthography import synthesize_compound_entry
            synth = synthesize_compound_entry(w_clean)
            if synth:
                canonical_html = render_canonical_html(synth)
                return {
                    "headword": synth.headword,
                    "html_content": canonical_html,
                    "synonyms": sorted(list(synth.inflections)),
                    "canonical_entry": synth,
                }
            return None

        # Detect headword and select matching standalone article(s), skipping compound prefixes ending in '-'
        from ordpresse.orthography import sanitize_headword
        matching_articles = []
        hw_elem = None
        for art in articles:
            art_hw = self._extract_raw_art_hw(art)
            if not art_hw:
                continue
            if art_hw.endswith("-") and not w_clean.endswith("-"):
                continue
            clean_art_hw, _ = sanitize_headword(art_hw)
            if (
                art_hw == w_clean
                or art_hw.replace("-", "") == w_clean.replace("-", "")
                or clean_art_hw.lower() == w_clean
                or clean_art_hw.replace("-", "").lower() == w_clean.replace("-", "")
            ):
                matching_articles.append(art)
                if hw_elem is None:
                    hw_elem = art.find("span", class_="match") or art.find("span", class_="headline") or art.find("h1")

        if matching_articles:
            articles = matching_articles
            headword_raw = hw_elem.get_text(" ", strip=True)
        else:
            non_prefix = [a for a in articles if not self._extract_raw_art_hw(a).endswith("-")]
            if non_prefix:
                articles = non_prefix
            first_art = articles[0]
            hw_elem = (
                first_art.find("span", class_="match")
                or first_art.find("span", class_="headline")
                or first_art.find("h1")
                or soup.find("span", class_="match")
                or soup.find("h1")
            )
            headword_raw = hw_elem.get_text(" ", strip=True) if hw_elem else w_clean.capitalize()
        headword = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", headword_raw, flags=re.IGNORECASE)
        headword = re.sub(r"[,\s\d]+$", "", headword).strip()
        if not headword:
            headword = w_clean

        # Track already-rendered article headers in soup to avoid redundant sibling fetches
        seen_match_headers: Set[str] = set()
        for art in articles:
            m_tag = art.find("span", class_="match") or art.find("span", class_="headline") or art.find("h1")
            if m_tag:
                m_norm = re.sub(r"[,;\s]+$", "", m_tag.get_text(" ", strip=True).lower()).strip()
                if m_norm:
                    seen_match_headers.add(m_norm)

        # Collect homograph soups from search result box (capped for fast interactive latency)
        all_soups = [soup]
        res_box = soup.find("div", class_="searchResultBox")
        if res_box:
            sibling_fetches = 0
            seen_hrefs: Set[str] = set()
            for link in res_box.find_all("a"):
                if sibling_fetches >= 2:
                    break
                txt = link.get_text(" ", strip=True)
                txt_norm = re.sub(r"[,;\s]+$", "", txt.lower()).strip()
                if txt_norm in seen_match_headers:
                    continue
                clean_txt = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", txt, flags=re.IGNORECASE)
                clean_txt = re.sub(r"[,\s\d]+$", "", clean_txt).strip()
                if clean_txt.lower() == headword.lower() and not clean_txt.endswith("-") and link.get("href"):
                    href = link["href"]
                    if "fejlrapport" in href.lower() or href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)
                    if not href.startswith("http"):
                        href = f"https://ordnet.dk/ods/{href.lstrip('/')}"
                    try:
                        if self.request_delay > 0:
                            time.sleep(self.request_delay)
                        sub_r = self.session.get(href, timeout=REQUEST_TIMEOUT)
                        sibling_fetches += 1
                        if sub_r.status_code == 200:
                            sub_soup = BeautifulSoup(sub_r.text, "html.parser")
                            all_soups.append(sub_soup)
                    except requests.exceptions.ConnectionError:
                        break
                    except Exception as e:
                        logger.debug(f"Failed to fetch homograph sibling {href}: {e}")

        historical_senses: List[SenseItem] = []
        detected_pos = ""
        historical_spellings: List[str] = []
        visited_refs: Set[str] = {w_clean, headword.lower()}
        hw_norm_cmp = headword.lower().replace("-", "")

        for sp in all_soups:
            art_list = matching_articles if (sp is soup and matching_articles) else sp.find_all("div", class_="artikel")
            if not art_list and sp is soup:
                art_list = articles
            for art in art_list:
                raw_hw = self._extract_raw_art_hw(art)
                if raw_hw:
                    if raw_hw.endswith("-") and not w_clean.endswith("-"):
                        continue
                    clean_art_hw, _ = sanitize_headword(raw_hw)
                    if not (
                        raw_hw == headword.lower()
                        or raw_hw.replace("-", "") == hw_norm_cmp
                        or clean_art_hw.lower() == headword.lower()
                        or clean_art_hw.replace("-", "").lower() == hw_norm_cmp
                    ):
                        continue
                parsed_senses, art_pos, spellings = self._parse_article(
                    art, visited_refs, max_senses=max_senses
                )
                if not detected_pos and art_pos:
                    detected_pos = art_pos
                historical_spellings.extend(spellings)

                for sense in parsed_senses:
                    if not any(sense.definition == existing.definition for existing in historical_senses):
                        historical_senses.append(sense)
                        if len(historical_senses) >= max_senses:
                            break
                if len(historical_senses) >= max_senses:
                    break
            if len(historical_senses) >= max_senses:
                break

        # STRICT PROHIBITION AGAINST DUMMY DEFINITIONS:
        # If no authentic historical senses could be extracted, return None to trigger fallbacks!
        if not historical_senses:
            return None

        clean_hw, extra_syns = sanitize_headword(headword)
        headword_norm = (clean_hw or w_clean).lower()
        all_synonyms = {w_clean, headword_norm, headword_norm.replace("-", "")} | extra_syns
        all_synonyms.discard("")

        if len(historical_senses) > 1:
            summary_hook = truncate_summary_hook(historical_senses[0].definition, max_chars=165)
        else:
            summary_hook = ""

        canonical = CanonicalEntry(
            headword=headword_norm,
            historical_spellings=sorted(list(set(historical_spellings))),
            pos=detected_pos or "sb.",
            inflections=all_synonyms,
            summary_hook=summary_hook,
            historical_senses=historical_senses[:max_senses],
            sources_used=["ODS"],
        )

        canonical_html = render_canonical_html(canonical)

        return {
            "headword": headword_norm,
            "html_content": canonical_html,
            "synonyms": sorted(list(all_synonyms)),
            "canonical_entry": canonical,
        }

    def _fetch_ods_soup(self, word: str) -> Optional[BeautifulSoup]:
        """Fetch ODS page soup trying direct path and query parameter fallback."""
        urls_to_try = [
            f"https://ordnet.dk/ods/ordbog/{quote(word)}",
            f"{ODS_BASE_URL}{quote(word)}",
        ]
        for url in urls_to_try:
            try:
                if self.request_delay > 0:
                    time.sleep(self.request_delay)
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    logger.warning(f"ODS scraper received HTTP 429 Too Many Requests for {url}")
                    raise ODSScraperRateLimitError(f"HTTP 429 Too Many Requests for {url}")
                if resp.status_code == 403 and ("cloudflare" in resp.text.lower() or "just a moment" in resp.text.lower()):
                    logger.warning(f"ODS scraper hit Cloudflare challenge for {url}")
                    raise ODSScraperRateLimitError(f"Cloudflare challenge encountered for {url}")
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    if soup.find("div", class_="artikel") or soup.find("div", class_="searchResultBox"):
                        return soup
                    # HTTP 200 confirmed no ODS article on ordnet.dk -> skip duplicate ?query= URL
                    return None
            except ODSScraperRateLimitError:
                raise
            except requests.exceptions.ConnectionError as e:
                logger.debug(f"Connection error fetching ODS for {url}: {e}")
                break
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")
        return None

    @staticmethod
    def _is_bare_etymology_fragment(text: str) -> bool:
        """Reject bare ODS etymology/cross-reference fragments like 'af I. Mare', 'af Marie', 'se Lejd'."""
        if not text:
            return True
        t = re.sub(r"^\([^)]+\)\s*", "", text.strip()).strip()
        if re.match(r"^(?:[Ss]e\b|[IVXLCDM]+\.\s*[Ss]e\b|\d+\.\s*[Ss]e\b)", t):
            return True
        if re.match(r"^(?:af|fra|afl\.?\s+af|samme\s+ord\s+som)\s+(?:[IVXLCDM]+\.\s*)?[A-ZÆØÅ][a-zæøåA-ZÆØÅ\-]*\.?$", t):
            return True
        if re.match(r"^(?:af|fra)\s+(?:ty\.|lty\.|hty\.|lat\.|gr\.|fr\.|eng\.|oldn\.|sv\.|no\.)\b", t, re.IGNORECASE) and len(t) < 40:
            return True
        return False

    @staticmethod
    def _extract_ods_ref_target(art: BeautifulSoup, clean_raw_text: str) -> Optional[str]:
        """Extract clean cross-reference headword from cleaned ODS article."""
        ref_match = re.search(
            r"\bse\s+(?:u\.\s+|under\s+)?([A-ZÆØÅa-zæøå][A-ZÆØÅa-zæøå0-9.,\- ]{0,40})",
            clean_raw_text,
        )
        for a in art.find_all("a"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            low_href = href.lower()
            if any(skip in low_href for skip in ("fejlrapport", "om-ods", "forkortelser", "hjaelp", "login")):
                continue
            cand = ""
            if "ordbog" in low_href or "query=" in low_href or "select=" in low_href:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                if qs.get("query") and qs["query"][0].strip():
                    cand = qs["query"][0].strip()
                elif qs.get("select") and qs["select"][0].strip():
                    cand = qs["select"][0].strip()
                elif "/ods/ordbog/" in low_href:
                    cand = unquote(parsed.path.split("/ods/ordbog/")[-1]).strip()
                else:
                    cand = a.get_text(" ", strip=True)
            elif ref_match:
                link_txt = a.get_text(" ", strip=True)
                if link_txt and link_txt.rstrip(".,") in ref_match.group(1):
                    cand = link_txt
            if cand:
                cand = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", cand, flags=re.I).strip()
                cand = re.sub(r"[,\s\d]+$", "", cand).strip()
                if (
                    cand
                    and len(cand) <= 50
                    and "/" not in cand
                    and "?" not in cand
                    and re.match(r"^[a-zæøåA-ZÆØÅ\- ]+$", cand)
                ):
                    return cand

        if ref_match:
            cand = ref_match.group(1).strip()
            cand = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", cand, flags=re.I).strip()
            cand = re.sub(r"[,\s\d]+$", "", cand).strip()
            if (
                cand
                and len(cand) <= 50
                and "/" not in cand
                and "?" not in cand
                and re.match(r"^[a-zæøåA-ZÆØÅ\- ]+$", cand)
            ):
                return cand
        return None

    def _parse_article(
        self,
        art: BeautifulSoup,
        visited_refs: Set[str],
        depth: int = 0,
        max_senses: int = 8,
    ) -> tuple[List[SenseItem], str, List[str]]:
        """Parse an ODS .artikel node, extracting senses, POS, and following cross-references."""
        senses: List[SenseItem] = []
        detected_pos = ""
        historical_spellings: List[str] = []

        raw_text = art.get_text(" ", strip=True)

        # 1. Extract POS and archaic spellings if available in match header
        headword_match = art.find("span", class_="match")
        if headword_match:
            header_text = ""
            for sib in headword_match.next_siblings:
                if getattr(sib, "name", None) == "span" and "semdel" in getattr(sib, "get", lambda _: [])("class", []):
                    break
                header_text += " " + (sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib))
                if len(header_text) > 80:
                    break
            pos_match = re.search(r"\b(adj\.|sb\.|v\.|adv\.|pron\.|præp\.|konj\.|interj\.|en\b|et\b)", header_text, re.IGNORECASE)
            if pos_match:
                detected_pos = normalize_pos(pos_match.group(1))

            dag_elem = art.find(title=re.compile(r"forældet", re.IGNORECASE))
            if dag_elem and dag_elem.parent:
                b_elem = dag_elem.parent.find("b")
                if b_elem:
                    historical_spellings.append(b_elem.get_text(" ", strip=True).rstrip("."))

        # 2. Clean unwanted DOM elements before extracting cross-reference targets or senses
        for unwanted in art.find_all(["script", "style", "nav", "header", "footer"]):
            unwanted.decompose()
        for unwanted_class in [
            "udtale",
            "kilde",
            "kilde-group",
            "dfooter",
            "fejlrapport",
            "hiddenTitle",
            "hiddenStructure",
        ]:
            for node in art.find_all(class_=re.compile(rf"\b{unwanted_class}\b")):
                node.decompose()

        if headword_match:
            headword_match.decompose()

        clean_raw_text = art.get_text(" ", strip=True)
        ref_target = self._extract_ods_ref_target(art, clean_raw_text)

        # 3. Extract structured definitions from .semem / .subsem
        semems = art.find_all(class_="semem")
        if semems:
            for s in semems:
                subsems = s.find_all(class_="subsem")
                candidate_nodes = subsems if subsems else [s]
                for c_node in candidate_nodes:
                    c_soup = BeautifulSoup(str(c_node), "html.parser").find(True)
                    if not c_soup:
                        continue
                    for num in c_soup.find_all(class_=re.compile(r"(?:hovedbetydning|underbetydning)")):
                        num.decompose()
                    for sub in c_soup.find_all(class_="subsem"):
                        sub.decompose()

                    def_tokens = []
                    quote_text = None
                    quote_author = None
                    hit_cit = False

                    for ch in c_soup.children:
                        tag_name = getattr(ch, "name", None)
                        txt = ch.get_text(" ", strip=True) if hasattr(ch, "get_text") else str(ch).strip()
                        if not txt:
                            continue

                        if tag_name:
                            cls_list = [c.lower() for c in (ch.get("class") or [])]
                            if "citat" in cls_list or "cit" in cls_list:
                                hit_cit = True
                                q_clean = re.sub(r"^[*\s]+", "", txt).strip()
                                if not quote_text and len(q_clean) > 4:
                                    quote_text = q_clean.strip("»« ")
                                continue

                        if txt.startswith("*") or hit_cit:
                            hit_cit = True
                            if not quote_text and txt.startswith("*"):
                                quote_text = txt.lstrip("*").strip()
                            elif not quote_author:
                                for p, author_name in AUTHOR_EXPANSIONS:
                                    if p.search(txt):
                                        quote_author = author_name
                                        break
                            continue

                        if tag_name is None and def_tokens:
                            if "»" in txt or (
                                len(txt) > 20
                                and not txt.startswith(("I.", "II.", "III."))
                                and (
                                    re.search(r"^[A-ZÆØÅ][a-zæøå]+\.\s", txt)
                                    or any(p.search(txt) for p, _ in AUTHOR_EXPANSIONS)
                                )
                            ):
                                hit_cit = True
                                if not quote_text:
                                    quote_text = txt.strip("»«* ")
                                continue

                        def_tokens.append(txt)

                    raw_def = sanitize_ods_text(" ".join(def_tokens))
                    clean_def = clean_ods_definition_text(raw_def)

                    if (
                        not is_meta_header_not_definition(clean_def)
                        and is_valid_definition(clean_def)
                        and not self._is_bare_etymology_fragment(clean_def)
                    ):
                        label = self._extract_label(clean_def)
                        if label:
                            clean_def = re.sub(r"^\([^)]+\)\s*", "", clean_def).strip()
                        clean_def = clean_ods_definition_text(clean_def)
                        if (
                            clean_def
                            and not is_meta_header_not_definition(clean_def)
                            and not self._is_bare_etymology_fragment(clean_def)
                        ):
                            if not any(clean_def == existing.definition for existing in senses):
                                senses.append(
                                    SenseItem(
                                        definition=clean_def,
                                        label=label or "hist.",
                                        quote=quote_text,
                                        quote_author=quote_author,
                                        source_tag="ODS",
                                    )
                                )
                                if len(senses) >= max_senses:
                                    break
                if len(senses) >= max_senses:
                    break

        # 4. Fallback: Extract from .betydning or .definition
        if not senses:
            betyd_nodes = art.find_all(class_=re.compile(r"\b(?:betydning|definition)\b"))
            if betyd_nodes:
                for bn in betyd_nodes:
                    clean_def = clean_ods_definition_text(sanitize_ods_text(bn.get_text(" ", strip=True)))
                    if (
                        not is_meta_header_not_definition(clean_def)
                        and is_valid_definition(clean_def)
                        and not self._is_bare_etymology_fragment(clean_def)
                    ):
                        label = self._extract_label(clean_def)
                        if label:
                            clean_def = re.sub(r"^\([^)]+\)\s*", "", clean_def).strip()
                        clean_def = clean_ods_definition_text(clean_def)
                        if (
                            clean_def
                            and not is_meta_header_not_definition(clean_def)
                            and not self._is_bare_etymology_fragment(clean_def)
                        ):
                            if not any(clean_def == existing.definition for existing in senses):
                                senses.append(SenseItem(definition=clean_def, label=label or "hist.", source_tag="ODS"))
                                if len(senses) >= max_senses:
                                    break

        # 5. Fallback: Check for inline editorial gloss (: ...) or body text
        if not senses:
            gloss_match = re.search(r"\(\s*:\s*([^)]+)\)", raw_text)
            if gloss_match:
                gloss_text = gloss_match.group(1).strip()
                gloss_text = clean_ods_definition_text(sanitize_ods_text(gloss_text))
                if (
                    not is_meta_header_not_definition(gloss_text)
                    and is_valid_definition(gloss_text)
                    and not self._is_bare_etymology_fragment(gloss_text)
                ):
                    label = self._extract_label(gloss_text)
                    if label:
                        gloss_text = re.sub(r"^\([^)]+\)\s*", "", gloss_text).strip()
                    quote_txt = None
                    quote_auth = None
                    for p, author_name in AUTHOR_EXPANSIONS:
                        if p.search(raw_text):
                            quote_auth = author_name
                            break
                    q_m = re.search(r"»([^«]+)«", raw_text)
                    if q_m:
                        quote_txt = q_m.group(1).strip()
                    senses.append(
                        SenseItem(
                            definition=gloss_text,
                            label=label or "hist.",
                            quote=quote_txt,
                            quote_author=quote_auth,
                            source_tag="ODS",
                        )
                    )

        if not senses:
            for it in art.find_all("i"):
                t = clean_ods_definition_text(sanitize_ods_text(it.get_text(" ", strip=True)))
                if (
                    not is_meta_header_not_definition(t)
                    and is_valid_definition(t)
                    and not self._is_bare_etymology_fragment(t)
                    and not re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+", t)
                ):
                    label = self._extract_label(t)
                    if label:
                        t = re.sub(r"^\([^)]+\)\s*", "", t).strip()
                    if t and not self._is_bare_etymology_fragment(t):
                        senses.append(SenseItem(definition=t, label=label or "hist.", source_tag="ODS"))
                        break

            if not senses:
                art_text = clean_ods_definition_text(sanitize_ods_text(art.get_text(" ", strip=True)))
                art_text = re.sub(r"^(?:en|et|adj\.|sb\.|v\.|adv\.)\s*[.]?\s*", "", art_text).strip()
                if (
                    len(art_text) >= 10
                    and not is_meta_header_not_definition(art_text)
                    and is_valid_definition(art_text)
                    and not self._is_bare_etymology_fragment(art_text)
                ):
                    if not re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+", art_text):
                        label = self._extract_label(art_text)
                        if label:
                            art_text = re.sub(r"^\([^)]+\)\s*", "", art_text).strip()
                        if art_text and not self._is_bare_etymology_fragment(art_text):
                            senses.append(SenseItem(definition=art_text, label=label or "hist.", source_tag="ODS"))

        # 6. Follow cross-reference articles (e.g. 'I. Lægd, en. se I. Lejd.') — capped cleanly
        is_pure_ref = (not senses) or (
            len(senses) == 1 and re.match(r"^(?:se\s+|[IVXLCDM]+\.\s*se\s+|\d+\.\s*se\s+)", senses[0].definition, re.IGNORECASE)
        )
        if is_pure_ref and depth < 1 and len(visited_refs) < 5 and ref_target:
            if ref_target.lower() not in visited_refs:
                visited_refs.add(ref_target.lower())
                ref_soup = self._fetch_ods_soup(ref_target)
                if ref_soup:
                    for sub_art in ref_soup.find_all("div", class_="artikel"):
                        sub_hw = self._extract_raw_art_hw(sub_art)
                        if sub_hw.endswith("-"):
                            continue
                        sub_senses, sub_pos, sub_spellings = self._parse_article(
                            sub_art, visited_refs, depth=depth + 1, max_senses=max_senses
                        )
                        if sub_senses:
                            senses = sub_senses
                            if not detected_pos and sub_pos:
                                detected_pos = sub_pos
                            historical_spellings.extend(sub_spellings)
                            break

        return senses, detected_pos, historical_spellings

    def _extract_label(self, text: str) -> Optional[str]:
        """Extract domain / historical label from definition text prefix."""
        m = re.match(
            r"^\((hist\.|landbo\.|foræld\.|jura|jur\.|møntvæsen|bibelsk|bibl\.|militær|dial\.|arkaisk)\)",
            text,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).rstrip(".")
        return None


def parse_ods_html(word: str, html: str) -> Optional[dict]:
    """Top-level helper to parse ODS HTML identically to the live scraper."""
    scraper = ODSScraper()
    return scraper.parse_ods_html(word, html)
