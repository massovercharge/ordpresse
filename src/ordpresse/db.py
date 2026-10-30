"""SQLite database manager for streaming datasets and scraper caching."""

import json
import re
from pathlib import Path
import sqlite3
import threading
from typing import Any, Optional

from ordpresse.config import CACHE_DB_PATH, DATASETS_DB_PATH
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.synthesizer import (
    COR_SEM_DEFINITION_COMPLETIONS,
    COR_SEM_ID_COMPLETIONS,
    synthesize_cor_sem_summary_hook,
)
from ordpresse.sources.nordic_1800s import (
    NORDIC_1800S_ENTRIES,
    enrich_canonical_entry_with_historical,
)


def get_connection(db_path: Path, read_only: bool = False, synchronous_off: bool = False) -> sqlite3.Connection:
    """Open an optimized SQLite connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and db_path.exists():
        uri = f"file:{db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)

    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    if synchronous_off:
        cursor.execute("PRAGMA synchronous = OFF;")
    else:
        cursor.execute("PRAGMA synchronous = NORMAL;")
    cursor.execute("PRAGMA cache_size = -64000;")  # 64MB cache
    cursor.close()
    return conn


def init_datasets_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes for COR and ODS datasets."""
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cor_forms (
                lemma_id TEXT,
                lemma TEXT,
                gloss TEXT,
                gram TEXT,
                pos TEXT,
                fullform TEXT,
                norm_status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cor_fullform ON cor_forms(fullform);
            CREATE INDEX IF NOT EXISTS idx_cor_lemma ON cor_forms(lemma);
            CREATE INDEX IF NOT EXISTS idx_cor_lemma_id ON cor_forms(lemma_id);

            CREATE TABLE IF NOT EXISTS cor_sem (
                sense_id TEXT,
                lemma_id TEXT,
                lemma TEXT,
                pos TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cor_sem_lemma_id ON cor_sem(lemma_id);
            CREATE INDEX IF NOT EXISTS idx_cor_sem_sense_id ON cor_sem(sense_id);
            CREATE INDEX IF NOT EXISTS idx_cor_sem_lemma ON cor_sem(lemma);

            CREATE TABLE IF NOT EXISTS cor_definitions (
                sense_id TEXT PRIMARY KEY,
                definition TEXT,
                examples TEXT
            );

            CREATE TABLE IF NOT EXISTS ods_forms (
                inflected_lower TEXT,
                inflected_raw TEXT,
                headword TEXT,
                homograph TEXT,
                pos TEXT,
                ods_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ods_inflected_lower ON ods_forms(inflected_lower);
            CREATE INDEX IF NOT EXISTS idx_ods_headword ON ods_forms(headword);
            CREATE INDEX IF NOT EXISTS idx_ods_id ON ods_forms(ods_id);
            """
        )


def clean_corrupted_cached_definitions(conn: sqlite3.Connection) -> int:
    """Purge corrupted scraper entries, SITATER/adjektiv leaks, author signatures, and repair glued typography."""
    from bs4 import BeautifulSoup
    from ordpresse.sources.ods_scraper import is_valid_definition
    corrupted_pos_pattern = re.compile(r"<i>[^<]*(?:adjektiv|substantiv)</i>")
    with conn:
        cur = conn.execute(
            """
            DELETE FROM definitions
            WHERE html_content LIKE '%SITATER%'
               OR html_content LIKE '%Historisk opslag for »%'
               OR html_content LIKE '%ingen betydningsangivelse%'
               OR html_content IS NULL
               OR trim(html_content) = ''
            """
        )
        deleted = cur.rowcount

        cur = conn.execute("SELECT word, html_content FROM definitions")
        to_delete = []
        to_update = []
        for row in cur.fetchall():
            word = row[0]
            html = (row[1] or "").strip()
            if not html:
                to_delete.append(word)
                continue
            if corrupted_pos_pattern.search(html):
                to_delete.append(word)
                continue
            if not any(marker in html for marker in ["<ol>", "<li>", "▸", "<p>", "[Historisk kontekst]"]):
                to_delete.append(word)
                continue

            # Check if definition is merely an author signature or unparsed citation
            soup = BeautifulSoup(html, "html.parser")
            has_valid_sense = False
            for li in soup.find_all("li"):
                li_txt = re.sub(r"^\([^)]+\)\s*", "", li.get_text(" ", strip=True)).strip()
                if is_valid_definition(li_txt):
                    has_valid_sense = True
                    break
            if not has_valid_sense and "▸" not in html and "[Historisk kontekst]" not in html:
                to_delete.append(word)
                continue

            # Repair typography spacing in cached html
            repaired_html = html
            if re.search(r"[a-zæøåA-ZÆØÅ0-9]ell\.", repaired_html) or re.search(r"\b(?:m\.|jf\.)[a-zæøåA-ZÆØÅ0-9]", repaired_html):
                repaired_html = re.sub(r"([a-zæøåA-ZÆØÅ0-9])(ell?\.)", r"\1 \2", repaired_html)
                repaired_html = re.sub(r"\b(ell?\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", repaired_html)
                repaired_html = re.sub(r"([a-zæøåA-ZÆØÅ0-9])(jf\.)", r"\1 \2", repaired_html)
                repaired_html = re.sub(r"\b(jf\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", repaired_html)
                repaired_html = re.sub(r"\b(m\.)([a-zæøåA-ZÆØÅ0-9])", r"\1 \2", repaired_html)
                to_update.append((repaired_html, word))

        if to_delete:
            conn.executemany("DELETE FROM definitions WHERE word = ?", [(w,) for w in to_delete])
            deleted += len(to_delete)

        if to_update:
            conn.executemany("UPDATE definitions SET html_content = ? WHERE word = ?", to_update)

        return deleted


def init_cache_db(conn: sqlite3.Connection) -> None:
    """Create schema for scraped article cache and clean legacy corrupted rows."""
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS definitions (
                word TEXT PRIMARY KEY,
                source TEXT,
                headword TEXT,
                html_content TEXT,
                synonyms_json TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_cache_headword ON definitions(headword);
            CREATE TABLE IF NOT EXISTS not_found_words (
                word TEXT PRIMARY KEY,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_not_found_word ON not_found_words(word);
            """
        )
    clean_corrupted_cached_definitions(conn)


class Database:
    """Convenient context wrapper for datasets and cache SQLite interactions."""

    def __init__(
        self,
        datasets_path: Path = DATASETS_DB_PATH,
        cache_path: Path = CACHE_DB_PATH,
        bulk_mode: bool = False,
    ):
        self.datasets_path = datasets_path
        self.cache_path = cache_path
        self.bulk_mode = bulk_mode
        self._write_lock = threading.Lock()
        self._datasets_conn: Optional[sqlite3.Connection] = None
        self._cache_conn: Optional[sqlite3.Connection] = None

    @property
    def datasets_conn(self) -> sqlite3.Connection:
        if self._datasets_conn is None:
            self._datasets_conn = get_connection(
                self.datasets_path,
                synchronous_off=self.bulk_mode,
            )
            init_datasets_db(self._datasets_conn)
        return self._datasets_conn

    @property
    def cache_conn(self) -> sqlite3.Connection:
        if self._cache_conn is None:
            self._cache_conn = get_connection(self.cache_path)
            init_cache_db(self._cache_conn)
        return self._cache_conn

    def close(self) -> None:
        if self._datasets_conn:
            self._datasets_conn.close()
            self._datasets_conn = None
        if self._cache_conn:
            self._cache_conn.close()
            self._cache_conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # --- Cache queries ---

    def clean_corrupted_cached_definitions(self) -> int:
        """Purge corrupted scraper entries, SITATER/adjektiv leaks, and empty skeleton definitions."""
        if not self.cache_path.exists():
            return 0
        with self._write_lock:
            return clean_corrupted_cached_definitions(self.cache_conn)

    def clean_dummy_cached_definitions(self) -> int:
        """Alias for clean_corrupted_cached_definitions."""
        return self.clean_corrupted_cached_definitions()

    def get_cached_definition(self, word: str) -> Optional[dict]:
        """Alias for get_cached to match v3 spec."""
        return self.get_cached(word)

    def get_cached(self, word: str) -> Optional[dict]:
        with self._write_lock:
            cur = self.cache_conn.cursor()
            w = word.strip().lower()
            cur.execute(
                "SELECT word, source, headword, html_content, synonyms_json FROM definitions WHERE word = ? OR LOWER(headword) = ? LIMIT 1",
                (w, w),
            )
            row = cur.fetchone()
            if not row:
                w_nohyphen = w.replace("-", "")
                cur.execute(
                    "SELECT word, source, headword, html_content, synonyms_json FROM definitions WHERE REPLACE(LOWER(headword), '-', '') = ? LIMIT 1",
                    (w_nohyphen,),
                )
                row = cur.fetchone()
            if not row:
                return None
            html_content = row["html_content"] or ""
            if ("Historisk opslag for »" in html_content or
                "SITATER" in html_content or
                bool(re.search(r"<i>[^<]*(?:adjektiv|substantiv)</i>", html_content)) or
                not any(m in html_content for m in ["<ol>", "<li>", "▸", "<p>", "[Historisk kontekst]"])):
                with self.cache_conn:
                    self.cache_conn.execute("DELETE FROM definitions WHERE word = ?", (row["word"],))
                return None
            synonyms = json.loads(row["synonyms_json"]) if row["synonyms_json"] else []
            return {
                "word": row["word"],
                "source": row["source"],
                "headword": row["headword"],
                "html_content": html_content,
                "synonyms": synonyms,
            }

    def set_not_found(self, word: str) -> None:
        """Record that a word was queried online and has no article on ODS/ordnet.dk."""
        w = word.strip().lower()
        if not w:
            return
        with self._write_lock:
            with self.cache_conn:
                self.cache_conn.execute(
                    "INSERT OR IGNORE INTO not_found_words (word) VALUES (?)", (w,)
                )

    def get_not_found_words(self) -> set[str]:
        """Fetch all words known to have no article on ODS."""
        if not self.cache_path.exists():
            return set()
        cur = self.cache_conn.cursor()
        cur.execute("SELECT word FROM not_found_words")
        return {r[0] for r in cur.fetchall()}

    def set_cached(
        self,
        word: str,
        source: str,
        headword: str,
        html_content: str,
        synonyms: list[str],
    ) -> None:
        with self._write_lock:
            with self.cache_conn:
                self.cache_conn.execute(
                    """
                    INSERT OR REPLACE INTO definitions (word, source, headword, html_content, synonyms_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        word.lower(),
                        source,
                        headword,
                        html_content,
                        json.dumps(synonyms, ensure_ascii=False),
                    ),
                )

    def get_all_cached_definitions(self) -> list[dict]:
        """Fetch all previously scraped or cached definitions from cache.sqlite3."""
        if not self.cache_path.exists():
            return []
        cur = self.cache_conn.cursor()
        cur.execute(
            "SELECT word, source, headword, html_content, synonyms_json FROM definitions"
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            synonyms = json.loads(r["synonyms_json"]) if r["synonyms_json"] else []
            results.append({
                "word": r["word"],
                "source": r["source"],
                "headword": r["headword"],
                "html_content": r["html_content"],
                "synonyms": synonyms,
            })
        return results

    # --- ODS Form queries ---

    def lookup_ods_inflection(self, lower_word: str) -> list[dict]:
        """Lookup an inflected form or word in ODS fullforms table."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT headword, homograph, pos, ods_id, inflected_raw
            FROM ods_forms
            WHERE inflected_lower = ?
            LIMIT 20
            """,
            (lower_word,),
        )
        return [dict(r) for r in cur.fetchall()]

    def lookup_ods_headword_inflections(self, headword: str) -> list[str]:
        """Find all inflected surface forms recorded for a given ODS headword."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT inflected_raw
            FROM ods_forms
            WHERE headword = ?
            LIMIT 100
            """,
            (headword,),
        )
        return [r[0] for r in cur.fetchall()]

    def lookup_ods_forms_batch(self, words: list[str]) -> dict[str, tuple[str, str]]:
        """Bulk-lookup inflected words to find their ODS headwords and POS in SQLite."""
        if not self.datasets_path.exists() or not words:
            return {}
        cur = self.datasets_conn.cursor()
        results: dict[str, tuple[str, str]] = {}
        unique_words = list({w.lower() for w in words if w})
        chunk_size = 500
        for i in range(0, len(unique_words), chunk_size):
            chunk = unique_words[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(
                f"SELECT lower(inflected_lower), lower(headword), pos FROM ods_forms WHERE inflected_lower IN ({placeholders})",
                chunk,
            )
            for inf, hw, pos in cur.fetchall():
                if inf not in results:
                    results[inf] = (hw, pos or "")
        return results

    def lookup_cor_forms_batch(self, words: list[str]) -> dict[str, list[dict[str, str]]]:
        """Bulk-lookup inflected words to find their COR lemmas, POS, and lemma_id in SQLite."""
        if not self.datasets_path.exists() or not words:
            return {}
        from ordpresse.orthography import historical_aa_to_modern

        cur = self.datasets_conn.cursor()
        results: dict[str, list[dict[str, str]]] = {}

        # Build query set and map normalized/modern variants back to the original token
        query_map: dict[str, set[str]] = {}
        for w in words:
            if not w:
                continue
            wl = w.lower()
            query_map.setdefault(wl, set()).add(wl)
            if "aa" in wl:
                mod_aa = historical_aa_to_modern(wl).lower()
                query_map.setdefault(mod_aa, set()).add(wl)

        unique_queries = list(query_map.keys())
        chunk_size = 500
        for i in range(0, len(unique_queries), chunk_size):
            chunk = unique_queries[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(
                f"""
                SELECT lower(fullform), lower(lemma), pos, lemma_id
                FROM cor_forms
                WHERE fullform IN ({placeholders})
                """,
                chunk,
            )
            for ff, lem, pos, lid in cur.fetchall():
                entry = {"lemma": lem, "pos": pos or "", "lemma_id": lid or ""}
                for original_w in query_map.get(ff, []):
                    if original_w not in results:
                        results[original_w] = []
                    if not any(e["lemma"] == lem and e["pos"] == (pos or "") for e in results[original_w]):
                        results[original_w].append(entry)
        return results

    # --- COR queries ---

    def lookup_cor_lemma_definitions(self, lemma_or_word: str) -> list[dict]:
        """Lookup modern DDO/COR definitions and examples for a lemma or fullform."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT cs.sense_id, cs.lemma_id, cs.lemma, cs.pos, cd.definition, cd.examples
            FROM cor_sem cs
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            LEFT JOIN cor_forms cf ON cf.lemma_id = cs.lemma_id
            WHERE cs.lemma = ? OR cf.fullform = ? OR cf.lemma = ?
            LIMIT 30
            """,
            (lemma_or_word, lemma_or_word, lemma_or_word),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            sid = d.get("sense_id", "")
            lem = d.get("lemma", "").lower()
            if sid in COR_SEM_ID_COMPLETIONS and (d["definition"].endswith("…") or d["definition"].endswith("...")):
                d["definition"] = COR_SEM_ID_COMPLETIONS[sid]
            elif lem in COR_SEM_DEFINITION_COMPLETIONS and (d["definition"].endswith("…") or d["definition"].endswith("...")):
                d["definition"] = COR_SEM_DEFINITION_COMPLETIONS[lem]
            if d.get("examples") and "|" in d["examples"]:
                d["examples"] = d["examples"].split("|")[0].strip()
            results.append(d)
        return results

    def lookup_cor_inflections(self, lemma: str, pos: Optional[str] = None) -> list[str]:
        """Find all inflected forms for a lemma in COR, optionally filtered by POS."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT fullform, pos
            FROM cor_forms
            WHERE lemma = ?
            LIMIT 100
            """,
            (lemma,),
        )
        rows = cur.fetchall()
        norm_filter_pos = normalize_pos(pos) if pos else None
        results = []
        for r in rows:
            ff = r[0]
            r_pos = normalize_pos(r[1] or "")
            if norm_filter_pos and r_pos:
                if not (r_pos == norm_filter_pos or r_pos.startswith(norm_filter_pos[:2])):
                    continue
            if norm_filter_pos and norm_filter_pos.startswith("sb"):
                added = ff[len(lemma):].lower()
                if added in ("ede", "te", "ende", "des"):
                    continue
            results.append(ff)
        return results

    def get_all_cor_lemmas_with_definitions(self, limit: Optional[int] = None) -> list[dict]:
        """Fetch all lemmas that have definitions in COR, along with their inflections."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        limit_clause = f"LIMIT {limit}" if limit else ""
        cur.execute(
            f"""
            SELECT cs.sense_id, cs.lemma_id, cs.lemma, cs.pos, cd.definition, cd.examples
            FROM cor_sem cs
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            WHERE cs.lemma != ''
            {limit_clause}
            """
        )
        rows = cur.fetchall()
        lemma_map: dict[str, dict] = {}
        id_to_lemma: dict[str, str] = {}
        for r in rows:
            lemma = r["lemma"]
            sid = r["sense_id"]
            lid = r["lemma_id"]
            pos = r["pos"] or ""
            definition = r["definition"]
            lem_lower = lemma.lower()
            if sid in COR_SEM_ID_COMPLETIONS and (definition.endswith("…") or definition.endswith("...")):
                definition = COR_SEM_ID_COMPLETIONS[sid]
            elif lem_lower in COR_SEM_DEFINITION_COMPLETIONS and (definition.endswith("…") or definition.endswith("...")):
                definition = COR_SEM_DEFINITION_COMPLETIONS[lem_lower]
            examples = r["examples"] or ""
            if examples and "|" in examples:
                examples = examples.split("|")[0].strip()

            if lem_lower not in lemma_map:
                lemma_map[lem_lower] = {
                    "lemma": lemma,
                    "poses": [],
                    "defs": [],
                    "lemma_ids": set(),
                    "seen_defs": set(),
                }
            pos_norm = normalize_pos(pos)
            if pos_norm and pos_norm not in lemma_map[lem_lower]["poses"]:
                lemma_map[lem_lower]["poses"].append(pos_norm)
            if lid:
                lemma_map[lem_lower]["lemma_ids"].add(lid)
                id_to_lemma[lid] = lem_lower

            def_key = definition.strip().lower()
            if def_key not in lemma_map[lem_lower]["seen_defs"]:
                lemma_map[lem_lower]["seen_defs"].add(def_key)
                lemma_map[lem_lower]["defs"].append((definition, examples, pos_norm))

        # Bulk pre-populate inflections in a single fast sequential scan (linking by lemma and lemma_id)
        inflections_map: dict[str, set[str]] = {}
        cur.execute("SELECT lemma, lemma_id, pos, fullform FROM cor_forms WHERE fullform != ''")
        for row in cur.fetchall():
            lem = row[0].lower()
            lid = row[1]
            row_pos = normalize_pos(row[2] or "")
            fullform = row[3]

            target_lem = None
            if lid and lid in id_to_lemma:
                target_lem = id_to_lemma[lid]
            elif lem in lemma_map:
                if row_pos and row_pos in lemma_map[lem]["poses"]:
                    target_lem = lem

            if target_lem:
                # Invariant: A noun ('sb.') can NEVER take verbal inflections (-ede, -te, -ende, -des)
                target_poses = lemma_map[target_lem]["poses"]
                is_pure_noun = all(p.startswith("sb") for p in target_poses)
                if is_pure_noun:
                    added = fullform[len(target_lem):].lower()
                    if added in ("ede", "te", "ende", "des"):
                        continue
                    if row_pos and not row_pos.startswith("sb"):
                        continue

                if target_lem not in inflections_map:
                    inflections_map[target_lem] = set()
                if len(inflections_map[target_lem]) < 40:
                    inflections_map[target_lem].add(fullform)

        results = []
        for lem_lower, data in lemma_map.items():
            pos_norm = " / ".join(data["poses"]) if data["poses"] else ""

            # If multiple POS exist, ensure top definitions from each POS are included
            if len(data["poses"]) > 1:
                selected_defs = []
                by_pos: dict[str, list] = {}
                for d, ex, p in data["defs"]:
                    by_pos.setdefault(p, []).append((d, ex, p))
                for p, p_defs in by_pos.items():
                    selected_defs.extend(p_defs[:2])
            else:
                selected_defs = data["defs"][:4]

            modern_senses = [
                SenseItem(
                    definition=f"({d_pos}) {d}" if len(data["poses"]) > 1 and d_pos else d,
                    quote=ex.split("|")[0].strip() if ex else None,
                    source_tag="COR-SEM",
                )
                for d, ex, d_pos in selected_defs
            ]
            inflections = set(inflections_map.get(lem_lower, set()))
            summary_hook = ""  # Rule: No synthesized summary hook for COR-SEM entries

            canonical = CanonicalEntry(
                headword=lem_lower,
                pos=pos_norm,
                inflections=inflections,
                summary_hook=summary_hook,
                modern_senses=modern_senses,
                sources_used=["COR-SEM"],
            )

            # False-friend enrichment rule:
            # If word is in curated 1800s vocabulary, enrich with historical senses and realia note
            if lem_lower in NORDIC_1800S_ENTRIES:
                canonical = enrich_canonical_entry_with_historical(
                    canonical, NORDIC_1800S_ENTRIES[lem_lower]
                )

            html = render_canonical_html(canonical)
            results.append({
                "headword": canonical.headword,
                "html_content": html,
                "synonyms": list(canonical.inflections),
                "canonical_entry": canonical,
                "lemma_ids": list(data.get("lemma_ids", set())),
            })
        return results
