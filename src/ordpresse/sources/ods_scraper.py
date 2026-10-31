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
from urllib.parse import quote
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

    def __init__(self, session: Optional[requests.Session] = None):
        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT})

    def scrape_ods(self, word: str) -> Optional[dict]:
        """Scrape ODS articles, aggregating all homographs and following cross-references."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        soup = self._fetch_ods_soup(w_clean)
        if not soup:
            return None

        return self._parse_soup_into_entry(w_clean, soup)

    def parse_ods_html(self, word: str, html: str) -> Optional[dict]:
        """Parse raw ODS HTML (e.g. from bookmarklet or external submission) into a canonical entry."""
        w_clean = word.strip().lower()
        if not w_clean or not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        return self._parse_soup_into_entry(w_clean, soup)

    def _parse_soup_into_entry(self, w_clean: str, soup: BeautifulSoup) -> Optional[dict]:
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
                    time.sleep(REQUEST_DELAY_SECONDS)
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

        # Detect headword
        hw_elem = soup.find("span", class_="match") or soup.find("h1") or soup.find("span", class_="headline")
        headword_raw = hw_elem.get_text(" ", strip=True) if hw_elem else w_clean.capitalize()
        headword = re.sub(r"^[0-9.]+\s*", "", headword_raw)
        headword = re.sub(r"[,\s\d]+$", "", headword).strip()
        if not headword:
            headword = w_clean

        # Collect all homograph soups from search result box
        all_soups = [soup]
        res_box = soup.find("div", class_="searchResultBox")
        if res_box:
            links = res_box.find_all("a")
            for link in links:
                txt = link.get_text(" ", strip=True)
                clean_txt = re.sub(r"^[0-9.]+\s*", "", txt)
                clean_txt = re.sub(r"[,\s\d]+$", "", clean_txt).strip()
                if clean_txt.lower() == headword.lower() and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"https://ordnet.dk/ods/{href.lstrip('/')}"
                    try:
                        time.sleep(REQUEST_DELAY_SECONDS)
                        sub_r = self.session.get(href, timeout=REQUEST_TIMEOUT)
                        if sub_r.status_code == 200:
                            sub_soup = BeautifulSoup(sub_r.text, "html.parser")
                            all_soups.append(sub_soup)
                    except Exception as e:
                        logger.debug(f"Failed to fetch homograph sibling {href}: {e}")

        historical_senses: List[SenseItem] = []
        detected_pos = ""
        historical_spellings: List[str] = []
        visited_refs: Set[str] = {w_clean, headword.lower()}

        for sp in all_soups:
            # Extract definitions from .artikel nodes
            for art in sp.find_all("div", class_="artikel"):
                parsed_senses, art_pos, spellings = self._parse_article(art, visited_refs)
                if not detected_pos and art_pos:
                    detected_pos = art_pos
                historical_spellings.extend(spellings)

                for sense in parsed_senses:
                    if not any(sense.definition == existing.definition for existing in historical_senses):
                        historical_senses.append(sense)
                        if len(historical_senses) >= 4:
                            break
                if len(historical_senses) >= 4:
                    break

        # STRICT PROHIBITION AGAINST DUMMY DEFINITIONS:
        # If no authentic historical senses could be extracted, return None to trigger fallbacks!
        if not historical_senses:
            return None

        from ordpresse.orthography import sanitize_headword
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
            historical_senses=historical_senses[:4],
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
                time.sleep(REQUEST_DELAY_SECONDS)
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
            except ODSScraperRateLimitError:
                raise
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")
        return None

    def _parse_article(
        self,
        art: BeautifulSoup,
        visited_refs: Set[str],
        depth: int = 0,
    ) -> tuple[List[SenseItem], str, List[str]]:
        """Parse an ODS .artikel node, extracting senses, POS, and following cross-references."""
        senses: List[SenseItem] = []
        detected_pos = ""
        historical_spellings: List[str] = []

        # 1. Detect cross-reference link before cleaning DOM
        raw_text = art.get_text(" ", strip=True)
        ref_match = re.search(r"\bse\s+(?:u\.\s+|under\s+)?([A-ZÆØÅa-zæøå0-9.,\- ]+)", raw_text)
        ref_link = None
        for a in art.find_all("a"):
            href = a.get("href", "")
            if href and not href.startswith("mailto:") and not href.startswith("#"):
                ref_link = href
                break

        # 2. Extract POS and archaic spellings if available in match header
        headword_match = art.find("span", class_="match")
        if headword_match:
            # Detect POS from elements immediately following match
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

            # Archaic spelling in dagger / parentheses: († uefterretlig)
            dag_elem = art.find(title=re.compile(r"forældet", re.IGNORECASE))
            if dag_elem and dag_elem.parent:
                b_elem = dag_elem.parent.find("b")
                if b_elem:
                    historical_spellings.append(b_elem.get_text(" ", strip=True).rstrip("."))

        # 3. Clean unwanted DOM elements
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

        # 4. Extract structured definitions from .betydning
        betyd_nodes = art.find_all(class_=re.compile(r"\bbetydning\b"))
        if betyd_nodes:
            for bn in betyd_nodes:
                clean_def = sanitize_ods_text(bn.get_text(" ", strip=True))
                if is_valid_definition(clean_def) and not clean_def.lower().startswith("se "):
                    label = self._extract_label(clean_def)
                    if label:
                        clean_def = re.sub(r"^\([^)]+\)\s*", "", clean_def).strip()
                    senses.append(SenseItem(definition=clean_def, label=label or "hist.", source_tag="ODS"))

        # 5. Extract structured definitions from .semem / .semdel
        if not senses:
            semems = art.find_all(class_="semem")
            for s in semems:
                quote_text = None
                quote_author = None
                def_text = ""
                last_text = None

                # Extract definition and quote by inspecting children in sequence
                for child in s.children:
                    tag_name = getattr(child, "name", None)
                    if tag_name == "i":
                        txt = sanitize_ods_text(child.get_text(" ", strip=True))
                        # First substantial non-citation italic tag is definition
                        if not def_text and is_valid_definition(txt) and not re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+", txt):
                            def_text = txt
                        elif def_text and last_text and not quote_text:
                            # Check if this italic element contains an author citation for last_text
                            for p, author_name in AUTHOR_EXPANSIONS:
                                if p.search(txt):
                                    quote_text = last_text
                                    quote_author = author_name
                                    break
                    elif tag_name is None:
                        # Text node
                        node_str = sanitize_ods_text(str(child))
                        if len(node_str) > 10:
                            last_text = node_str

                if not def_text:
                    full_s = sanitize_ods_text(s.get_text(" ", strip=True))
                    full_s = re.sub(r"^[0-9]+[.)]\s*", "", full_s)
                    def_text = full_s

                if is_valid_definition(def_text) and not def_text.lower().startswith("se "):
                    label = self._extract_label(def_text)
                    if label:
                        def_text = re.sub(r"^\([^)]+\)\s*", "", def_text).strip()

                    senses.append(
                        SenseItem(
                            definition=def_text,
                            label=label or "hist.",
                            quote=quote_text,
                            quote_author=quote_author,
                            source_tag="ODS",
                        )
                    )

        # 6. Fallback: Check for inline editorial gloss (: ...) or body text
        if not senses:
            # Phase 3.2: Check for editorial gloss pattern r"\(\s*:\s*([^)]+)\)"
            gloss_match = re.search(r"\(\s*:\s*([^)]+)\)", raw_text)
            if gloss_match:
                gloss_text = gloss_match.group(1).strip()
                gloss_text = sanitize_ods_text(gloss_text)
                if is_valid_definition(gloss_text):
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
            # Check <i> tags across article
            for it in art.find_all("i"):
                t = sanitize_ods_text(it.get_text(" ", strip=True))
                if is_valid_definition(t) and not re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+", t):
                    label = self._extract_label(t)
                    if label:
                        t = re.sub(r"^\([^)]+\)\s*", "", t).strip()
                    senses.append(SenseItem(definition=t, label=label or "hist.", source_tag="ODS"))
                    break

            if not senses:
                art_text = sanitize_ods_text(art.get_text(" ", strip=True))
                # Clean leading POS like 'en .', 'adj .'
                art_text = re.sub(r"^(?:en|et|adj\.|sb\.|v\.|adv\.)\s*[.]?\s*", "", art_text).strip()
                if is_valid_definition(art_text) and not art_text.lower().startswith("se "):
                    # Ensure it's not a raw citation
                    if not re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+", art_text):
                        label = self._extract_label(art_text)
                        if label:
                            art_text = re.sub(r"^\([^)]+\)\s*", "", art_text).strip()
                        senses.append(SenseItem(definition=art_text, label=label or "hist.", source_tag="ODS"))

        # 7. Follow cross-reference articles (e.g. 'I. Lægd, en. se I. Lejd.')
        is_pure_ref = (not senses) or (
            len(senses) == 1 and re.match(r"^(?:se\s+|[I|V|X\d.\s]+se\s+)", senses[0].definition, re.IGNORECASE)
        )
        if is_pure_ref and depth < 2 and (ref_link or ref_match):
            target_ref = ref_link.split("#")[0] if ref_link else ref_match.group(1).strip()
            target_ref = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", target_ref, flags=re.I).strip()
            target_ref = re.sub(r"[,\s\d]+$", "", target_ref).strip()
            if target_ref and target_ref.lower() not in visited_refs:
                visited_refs.add(target_ref.lower())
                ref_soup = self._fetch_ods_soup(target_ref)
                if ref_soup:
                    for sub_art in ref_soup.find_all("div", class_="artikel"):
                        sub_senses, sub_pos, sub_spellings = self._parse_article(
                            sub_art, visited_refs, depth=depth + 1
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
