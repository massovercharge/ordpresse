"""SQLite database manager for streaming datasets and scraper caching."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ordpresse.config import CACHE_DB_PATH, DATASETS_DB_PATH


def get_connection(db_path: Path, read_only: bool = False, synchronous_off: bool = False) -> sqlite3.Connection:
    """Open an optimized SQLite connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and db_path.exists():
        uri = f"file:{db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    else:
        conn = sqlite3.connect(str(db_path), timeout=30.0)

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

    def get_cached(self, word: str) -> Optional[dict]:
        cur = self.cache_conn.cursor()
        cur.execute(
            "SELECT word, source, headword, html_content, synonyms_json FROM definitions WHERE word = ?",
            (word.lower(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        synonyms = json.loads(row["synonyms_json"]) if row["synonyms_json"] else []
        return {
            "word": row["word"],
            "source": row["source"],
            "headword": row["headword"],
            "html_content": row["html_content"],
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

    # --- COR queries ---

    def lookup_cor_lemma_definitions(self, lemma_or_word: str) -> list[dict]:
        """Lookup modern DDO/COR definitions and examples for a lemma or fullform."""
        if not self.datasets_path.exists():
            return []
        cur = self.datasets_conn.cursor()
        # Find lemma IDs from fullform or lemma
        cur.execute(
            """
            SELECT DISTINCT cf.lemma_id, cf.lemma, cf.pos, cd.definition, cd.examples
            FROM cor_forms cf
            JOIN cor_sem cs ON cs.lemma_id = cf.lemma_id
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            WHERE cf.fullform = ? OR cf.lemma = ?
            LIMIT 30
            """,
            (lemma_or_word, lemma_or_word),
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
            SELECT cf.lemma, cf.pos, cd.definition, cd.examples
            FROM cor_forms cf
            JOIN cor_sem cs ON cs.lemma_id = cf.lemma_id
            JOIN cor_definitions cd ON cd.sense_id = cs.sense_id
            GROUP BY cf.lemma, cd.definition
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

        results = []
        for lemma, data in lemma_map.items():
            def_items = "".join(
                f"<li>{d}" + (f" <br/><em>{ex}</em>" if ex else "") + "</li>"
                for d, ex in data["defs"]
            )
            html = (
                f"<div class='ordpresse-article cor'>"
                f"<h2>{lemma} <small>({data['pos']})</small></h2>"
                f"<ol>{def_items}</ol>"
                f"</div>"
            )
            inflections = self.lookup_cor_inflections(lemma)
            results.append({
                "headword": lemma,
                "html_content": html,
                "synonyms": inflections,
            })
        return results
