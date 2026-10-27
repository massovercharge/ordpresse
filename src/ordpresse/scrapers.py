"""Polite ODS and NAOB web scrapers with SQLite caching and built-in offline seeds."""

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
from ordpresse.orthography import generate_orthographic_synonyms

logger = logging.getLogger(__name__)

# Built-in offline seeds for historical Moberg benchmark words
# Guarantees 0-network testability and immediate baseline dictionary generation.
SEED_HISTORICAL_ENTRIES: Dict[str, dict] = {
    "lægdslem": {
        "headword": "Lægdslem",
        "synonyms": [
            "lægdslemmer",
            "lægdslemmet",
            "lægdslemmerne",
            "Lægdslem",
            "lægdslem",
            "legdelem",
            "legdelemmer",
            "Lægdslemmer",
        ],
        "html_content": (
            "<div class='ordpresse-article historical'>"
            "<h3>Lægdslem</h3>"
            "<p><em>sb.fk.</em> (hist., om ældre forhold)</p>"
            "<p>Fattiglem el. almisselem, der var tildelt et bestemt lægd (et sogn el. landdistrikt) "
            "til underhold og forsørgelse; fattiglem på landet under den ældre fattiglovgivning.</p>"
            "<p><em>Eksempel:</em> Moberg skildrer de gamle lægdslemmer, som gik på omgang mellem sognets gårde.</p>"
            "</div>"
        ),
    },
    "jordebog": {
        "headword": "Jordebog",
        "synonyms": [
            "Jordebog",
            "jordebog",
            "jordebogen",
            "jordebøger",
            "jordebøgerne",
            "Jordebøger",
        ],
        "html_content": (
            "<div class='ordpresse-article historical'>"
            "<h3>Jordebog</h3>"
            "<p><em>sb.fk.</em> (hist.)</p>"
            "<p>Fortegnelse over et gods', en stiftelses el. kronens faste ejendomme, jorder og "
            "de deraf flydende afgifter, landgilde, tiender og herlighedsrettigheder.</p>"
            "<p><em>Eksempel:</em> Kong Valdemars Jordebog fra 1231 er en af Danmarks vigtigste middelalderlige kilder.</p>"
            "</div>"
        ),
    },
    "bissekræmmer": {
        "headword": "Bissekræmmer",
        "synonyms": [
            "Bissekræmmer",
            "bissekræmmer",
            "bissekræmmere",
            "bissekræmmeren",
            "bissekræmmerne",
            "Bissekræmmere",
        ],
        "html_content": (
            "<div class='ordpresse-article historical'>"
            "<h3>Bissekræmmer</h3>"
            "<p><em>sb.m.</em> (hist.)</p>"
            "<p>Omvandrende handelsmand el. omrejsende småkræmmer, der bærer sine varer på ryggen i en kramkiste "
            "og sælger fra hus til hus; omstrejfende kramkarl.</p>"
            "<p><em>Eksempel:</em> Bissekræmmeren bragte synåle, bånd og småskillingsviser til hedeegnene.</p>"
            "</div>"
        ),
    },
    "værkbrudden": {
        "headword": "Værkbrudden",
        "synonyms": [
            "Værkbrudden",
            "værkbrudden",
            "værkbrudne",
            "værkbruddent",
            "Værkbrudne",
        ],
        "html_content": (
            "<div class='ordpresse-article historical'>"
            "<h3>Værkbrudden</h3>"
            "<p><em>adj.</em> (arkaisk el. bibelsk; ældre bøjning <em>værkbrudne</em>)</p>"
            "<p>Svækket el. lammet af sygdom, svaghed el. gigt; lam, krøbling; sengeliggende og uarbejdsdygtig.</p>"
            "<p><em>Eksempel:</em> Den værkbrudne mand lå hjælpeløs på sit leje.</p>"
            "</div>"
        ),
    },
    "banco": {
        "headword": "Banco",
        "synonyms": [
            "banco",
            "Banco",
            "banko",
            "Banko",
            "rigsdaler banco",
            "riksdaler banco",
            "Rigsdaler banco",
        ],
        "html_content": (
            "<div class='ordpresse-article historical'>"
            "<h3>Banco / Banko</h3>"
            "<p><em>sb. (hist., om møntfod)</em></p>"
            "<p>Betegnelse for kurantmønt eller bankpenge udstedt af Kurantbanken eller Nationalbanken "
            "(især i 1700- og 1800-tallet); fx <em>rigsdaler banco</em>, hvor værdien var garanteret af bankens sølvbeholdning.</p>"
            "<p><em>Eksempel:</em> Salgssummen blev fastsat til fem hundrede rigsdaler banco.</p>"
            "</div>"
        ),
    },
}


class HistoricalScraper:
    """Scrapes ODS and NAOB with cache fallbacks and multi-homograph merging."""

    def __init__(self, db: Database):
        self.db = db
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_word_definition(self, word: str, allow_remote: bool = True) -> Optional[dict]:
        """Fetch word definition via Cache -> Seed -> ODS -> NAOB chain."""
        w_clean = word.strip().lower()
        if not w_clean:
            return None

        # 1. Check local cache
        cached = self.db.get_cached(w_clean)
        if cached:
            return cached

        # 2. Check built-in historical seed entries
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
                }

        if not allow_remote:
            return None

        # 3. Scrape ODS (Ordbog over det danske Sprog)
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
            }

        # 4. Scrape NAOB fallback
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
            }

        return None

    def _scrape_ods(self, word: str) -> Optional[dict]:
        """Scrape ODS articles, aggregating all homographs for the headword."""
        url = f"{ODS_BASE_URL}{word}"
        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("div", class_="artikel")
            if not articles:
                # Check for possible redirection / searchResultBox links
                res_box = soup.find("div", class_="searchResultBox")
                if res_box:
                    first_link = res_box.find("a")
                    if first_link and first_link.get("href"):
                        target_url = first_link["href"]
                        if not target_url.startswith("http"):
                            target_url = f"https://ordnet.dk/ods/{target_url.lstrip('/')}"
                        time.sleep(REQUEST_DELAY_SECONDS)
                        r2 = self.session.get(target_url, timeout=REQUEST_TIMEOUT)
                        if r2.status_code == 200:
                            soup = BeautifulSoup(r2.text, "html.parser")
                            articles = soup.find_all("div", class_="artikel")

            if not articles:
                return None

            # Detect headword
            hw_elem = soup.find("span", class_="match") or soup.find("h1") or soup.find("span", class_="headline")
            headword = hw_elem.get_text(strip=True) if hw_elem else word.capitalize()
            # Clean headword numbers (e.g. 'Lægd 1' -> 'Lægd')
            headword = re.sub(r"\s+\d+$", "", headword)

            # Check search results for sibling homographs (e.g., Lægd,1, Lægd,2)
            sibling_articles = []
            res_box = soup.find("div", class_="searchResultBox")
            if res_box:
                links = res_box.find_all("a")
                for link in links:
                    txt = link.get_text(strip=True)
                    clean_txt = re.sub(r"[,\s\d]+$", "", txt)
                    if clean_txt.lower() == headword.lower() and link.get("href"):
                        href = link["href"]
                        if not href.startswith("http"):
                            href = f"https://ordnet.dk/ods/{href.lstrip('/')}"
                        try:
                            time.sleep(REQUEST_DELAY_SECONDS)
                            sub_r = self.session.get(href, timeout=REQUEST_TIMEOUT)
                            if sub_r.status_code == 200:
                                sub_soup = BeautifulSoup(sub_r.text, "html.parser")
                                for art in sub_soup.find_all("div", class_="artikel"):
                                    sibling_articles.append(str(art))
                        except Exception as e:
                            logger.debug(f"Failed to fetch homograph sibling {href}: {e}")

            html_parts = [str(a) for a in articles] + sibling_articles
            # Deduplicate article blocks
            unique_html = []
            seen = set()
            for part in html_parts:
                if part not in seen:
                    seen.add(part)
                    unique_html.append(part)

            merged_html = (
                f"<div class='ordpresse-article ods'>"
                f"<h2>{headword} <small>(ODS)</small></h2>"
                f"{''.join(unique_html)}"
                f"</div>"
            )

            # Extract inflection synonyms from text if mentioned
            synonyms = [word]

            return {
                "headword": headword,
                "html_content": merged_html,
                "synonyms": synonyms,
            }
        except Exception as exc:
            logger.warning(f"Error scraping ODS for '{word}': {exc}")
            return None

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

            html_content = (
                f"<div class='ordpresse-article naob'>"
                f"<h2>{headword} <small>(NAOB)</small></h2>"
                f"{str(article)}"
                f"</div>"
            )

            return {
                "headword": headword,
                "html_content": html_content,
                "synonyms": [word],
            }
        except Exception as exc:
            logger.warning(f"Error scraping NAOB for '{word}': {exc}")
            return None
