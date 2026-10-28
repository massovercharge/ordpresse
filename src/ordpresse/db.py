"""SQLite database manager for streaming datasets and scraper caching."""

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Optional

from ordpresse.config import CACHE_DB_PATH, DATASETS_DB_PATH
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
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


def init_cache_db(conn: sqlite3.Connection) -> None:
    """Create schema for scraped article cache."""
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
            """
        )


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

    def clean_dummy_cached_definitions(self) -> int:
        """Purge any legacy placeholder dummy definitions from cache."""
        if not self.cache_path.exists():
            return 0
        with self._write_lock:
            with self.cache_conn:
                cur = self.cache_conn.execute(
                    "DELETE FROM definitions WHERE html_content LIKE '%Historisk opslag for »%'"
                )
                return cur.rowcount

    def get_cached(self, word: str) -> Optional[dict]:
        with self._write_lock:
            cur = self.cache_conn.cursor()
            cur.execute(
                "SELECT word, source, headword, html_content, synonyms_json FROM definitions WHERE word = ?",
                (word.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            html_content = row["html_content"] or ""
            if "Historisk opslag for »" in html_content:
                with self.cache_conn:
                    self.cache_conn.execute("DELETE FROM definitions WHERE word = ?", (word.lower(),))
                return None
            synonyms = json.loads(row["synonyms_json"]) if row["synonyms_json"] else []
            return {
                "word": row["word"],
                "source": row["source"],
                "headword": row["headword"],
                "html_content": html_content,
                "synonyms": synonyms,
            }

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

    def lookup_cor_forms_batch(self, words: list[str]) -> dict[str, str]:
        """Bulk-lookup inflected words to find their COR lemmas in SQLite."""
        if not self.datasets_path.exists() or not words:
            return {}
        cur = self.datasets_conn.cursor()
        results: dict[str, str] = {}
        unique_words = list({w.lower() for w in words if w})
        chunk_size = 500
        for i in range(0, len(unique_words), chunk_size):
            chunk = unique_words[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(
                f"SELECT lower(fullform), lower(lemma) FROM cor_forms WHERE fullform IN ({placeholders})",
                chunk,
            )
            for ff, lem in cur.fetchall():
                if ff not in results:
                    results[ff] = lem
        return results

    # --- COR queries ---

    def lookup_cor_lemma_definitions(self, lemma_or_word: str) -> list[dict]:
        """Lookup modern DDO/COR definitions and examples for a lemma or fullform."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT cs.lemma_id, cs.lemma, cs.pos, cd.definition, cd.examples
            FROM cor_sem cs
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            LEFT JOIN cor_forms cf ON cf.lemma_id = cs.lemma_id
            WHERE cs.lemma = ? OR cf.fullform = ? OR cf.lemma = ?
            LIMIT 30
            """,
            (lemma_or_word, lemma_or_word, lemma_or_word),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def lookup_cor_inflections(self, lemma: str) -> list[str]:
        """Find all inflected forms for a lemma in COR."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT fullform
            FROM cor_forms
            WHERE lemma = ?
            LIMIT 100
            """,
            (lemma,),
        )
        return [r[0] for r in cur.fetchall()]

    def get_all_cor_lemmas_with_definitions(self, limit: Optional[int] = None) -> list[dict]:
        """Fetch all lemmas that have definitions in COR, along with their inflections."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        limit_clause = f"LIMIT {limit}" if limit else ""
        cur.execute(
            f"""
            SELECT cs.lemma, cs.pos, cd.definition, cd.examples
            FROM cor_sem cs
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            WHERE cs.lemma != ''
            GROUP BY cs.lemma, cd.definition
            {limit_clause}
            """
        )
        rows = cur.fetchall()
        lemma_map: dict[str, dict] = {}
        for r in rows:
            lemma = r["lemma"]
            pos = r["pos"] or ""
            definition = r["definition"]
            examples = r["examples"] or ""
            if lemma not in lemma_map:
                lemma_map[lemma] = {"pos": pos, "defs": []}
            lemma_map[lemma]["defs"].append((definition, examples))

        # Bulk pre-populate inflections in a single fast sequential scan
        inflections_map: dict[str, set[str]] = {}
        cur.execute("SELECT lemma, fullform FROM cor_forms WHERE fullform != ''")
        for row in cur.fetchall():
            lem = row[0]
            if lem in lemma_map:
                if lem not in inflections_map:
                    inflections_map[lem] = set()
                if len(inflections_map[lem]) < 40:
                    inflections_map[lem].add(row[1])

        results = []
        for lemma, data in lemma_map.items():
            pos_norm = normalize_pos(data["pos"])
            modern_senses = [
                SenseItem(
                    definition=d,
                    quote=ex or None,
                    source_tag="COR-SEM",
                )
                for d, ex in data["defs"][:3]
            ]
            inflections = set(inflections_map.get(lemma, set()))
            summary_hook = data["defs"][0][0][:160] if data["defs"] else ""
            if len(summary_hook) > 165:
                summary_hook = summary_hook[:162].rsplit(" ", 1)[0] + "..."

            canonical = CanonicalEntry(
                headword=lemma.lower(),
                pos=pos_norm,
                inflections=inflections,
                summary_hook=summary_hook,
                modern_senses=modern_senses,
                sources_used=["COR-SEM"],
            )

            # False-friend enrichment rule:
            # If word is in curated 1800s vocabulary, enrich with historical senses and realia note
            hw_lower = lemma.lower()
            if hw_lower in NORDIC_1800S_ENTRIES:
                canonical = enrich_canonical_entry_with_historical(
                    canonical, NORDIC_1800S_ENTRIES[hw_lower]
                )

            html = render_canonical_html(canonical)
            results.append({
                "headword": canonical.headword,
                "html_content": html,
                "synonyms": list(canonical.inflections),
                "canonical_entry": canonical,
            })
        return results
