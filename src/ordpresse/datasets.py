"""Streaming downloader and TSV->SQLite importer for COR and ODS datasets.

Follows strict memory-safety rules (< 250 MB RAM) by streaming lines and batch-inserting
10,000 rows at a time into SQLite with WAL mode.
"""

import csv
import io
import logging
from pathlib import Path
import re
from typing import Callable, Generator, List, Optional, Tuple
import zipfile
import requests

from ordpresse.config import (
    COR_BASE_FALLBACK_URL,
    COR_BASE_URL,
    COR_EXT_URL,
    COR_SEM_EXT_URL,
    COR_SEM_URL,
    DATASETS_DB_PATH,
    ODS_FULLFORMS_URL,
    RAW_DATA_DIR,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from ordpresse.db import Database

logger = logging.getLogger(__name__)

BATCH_SIZE = 10000


def download_file_stream(url: str, dest_path: Path, progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    """Download a file with streaming chunks, keeping memory low."""
    headers = {"User-Agent": USER_AGENT}
    try:
        if progress_cb:
            progress_cb(f"Downloader {dest_path.name} fra {url}...")
        with requests.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT) as r:
            if r.status_code != 200:
                logger.warning(f"Download fejlede for {url} (HTTP {r.status_code})")
                return False
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as exc:
        logger.warning(f"Fejl ved download af {url}: {exc}")
        return False


def extract_cor_lemma_id(cor_id: str) -> str:
    """CRITICAL: Extract lemma ID from COR or COR.EXT ID.
    
    In COR: COR.68025.110.01 -> COR.68025
    In COR.EXT: COR.EXT.142887.120.01 -> COR.EXT.142887
    ALWAYS uses cor_id.rsplit('.', 2)[0] (NEVER split('.')[:2]).
    """
    parts = cor_id.strip().rsplit(".", 2)
    return parts[0] if parts else cor_id.strip()


def extract_pos(gram: str) -> str:
    """Extract part of speech prefix (e.g. 'sb.' from 'sb.fk.sg.ubest')."""
    if not gram:
        return ""
    m = re.match(r"^([a-zæøå]+\.)", gram.strip().lower())
    return m.group(1) if m else gram.split(".")[0] + "."


def stream_cor_forms(file_path: Path) -> Generator[Tuple[str, str, str, str, str, str, str], None, None]:
    """Stream cor1.5.1.0.tsv or corext1.0.tsv (6 columns) line-by-line."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in reader:
            if not row or len(row) < 5:
                continue
            cor_id = row[0].strip()
            if cor_id.startswith("#") or "cor_id" in cor_id.lower():
                continue
            lemma_id = extract_cor_lemma_id(cor_id)
            lemma = row[1].strip()
            gloss = row[2].strip() if len(row) > 2 else ""
            gram = row[3].strip() if len(row) > 3 else ""
            pos = extract_pos(gram)
            fullform = row[4].strip() if len(row) > 4 else lemma
            norm_status = row[5].strip() if len(row) > 5 else ""
            yield (lemma_id, lemma, gloss, gram, pos, fullform, norm_status)


def stream_cor_sem(file_path: Path) -> Generator[Tuple[str, str, str, str], None, None]:
    """Stream cor.sem.1.0.tsv line-by-line mapping sense_id to lemma_id, lemma, pos."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        first_row = True
        sense_col = 0
        lemma_id_col = 1
        lemma_col = 2
        pos_col = 3
        for row in reader:
            if not row or len(row) < 2:
                continue
            if first_row:
                first_row = False
                header = [c.lower().strip() for c in row]
                if any("sense" in c or "id" in c or "ddo" in c for c in header):
                    # Header present, dynamically detect columns
                    for idx, c in enumerate(header):
                        if "sense" in c or "ddo_id" in c:
                            sense_col = idx
                        elif "lemma_id" in c or "cor_id" in c:
                            lemma_id_col = idx
                        elif "lemma" in c:
                            lemma_col = idx
                        elif "pos" in c:
                            pos_col = idx
                    continue

            sense_id = row[sense_col].strip() if sense_col < len(row) else ""
            raw_lid = row[lemma_id_col].strip() if lemma_id_col < len(row) else ""
            lemma_id = extract_cor_lemma_id(raw_lid)
            lemma = row[lemma_col].strip() if lemma_col < len(row) else ""
            pos = row[pos_col].strip() if pos_col < len(row) else ""
            yield (sense_id, lemma_id, lemma, pos)


def stream_cor_sem_ext(file_path: Path) -> Generator[Tuple[str, str, str], None, None]:
    """Stream cor.sem.ext.1.0.tsv mapping sense_id to definition and examples."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        first_row = True
        sense_col = 0
        def_col = 1
        ex_col = 2
        for row in reader:
            if not row or len(row) < 2:
                continue
            if first_row:
                first_row = False
                header = [c.lower().strip() for c in row]
                if any("sense" in c or "betydning" in c or "def" in c for c in header):
                    for idx, c in enumerate(header):
                        if "sense" in c or "id" in c:
                            sense_col = idx
                        elif "def" in c or "betydning" in c:
                            def_col = idx
                        elif "ex" in c or "eksempel" in c:
                            ex_col = idx
                    continue

            sense_id = row[sense_col].strip() if sense_col < len(row) else ""
            definition = row[def_col].strip() if def_col < len(row) else ""
            examples = row[ex_col].strip() if ex_col < len(row) else ""
            yield (sense_id, definition, examples)


def stream_ods_forms(tsv_file_obj) -> Generator[Tuple[str, str, str, str, str, str], None, None]:
    """Stream ods-fullforms.tsv (5 columns) line-by-line."""
    reader = csv.reader(tsv_file_obj, delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if not row or len(row) < 2:
            continue
        inflected = row[0].strip()
        if inflected.startswith("#") or "inflected" in inflected.lower():
            continue
        headword = row[1].strip() if len(row) > 1 else inflected
        homograph = row[2].strip() if len(row) > 2 else ""
        pos = row[3].strip() if len(row) > 3 else ""
        ods_id = row[4].strip() if len(row) > 4 else ""
        inflected_lower = inflected.lower()
        yield (inflected_lower, inflected, headword, homograph, pos, ods_id)


class DatasetImporter:
    """Manages downloading raw data and streaming imports into datasets.sqlite3."""

    def __init__(self, raw_dir: Path = RAW_DATA_DIR, db_path: Path = DATASETS_DB_PATH):
        self.raw_dir = raw_dir
        self.db_path = db_path
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def download_datasets(self, progress_cb: Optional[Callable[[str], None]] = None) -> None:
        """Download missing raw dataset files into ./data/raw/."""
        files_to_download = [
            (self.raw_dir / "cor1.5.1.0.tsv", COR_BASE_URL, COR_BASE_FALLBACK_URL),
            (self.raw_dir / "corext1.0.tsv", COR_EXT_URL, None),
            (self.raw_dir / "cor.sem.1.0.tsv", COR_SEM_URL, None),
            (self.raw_dir / "cor.sem.ext.1.0.tsv", COR_SEM_EXT_URL, None),
            (self.raw_dir / "ods-fullforms.zip", ODS_FULLFORMS_URL, None),
        ]

        for target_file, primary_url, fallback_url in files_to_download:
            if target_file.exists() and target_file.stat().st_size > 0:
                if progress_cb:
                    progress_cb(f"Fundet eksisterende {target_file.name}")
                continue

            success = download_file_stream(primary_url, target_file, progress_cb)
            if not success and fallback_url:
                if progress_cb:
                    progress_cb(f"Prøver fallback URL: {fallback_url}")
                download_file_stream(fallback_url, target_file, progress_cb)

    def import_all(self, progress_cb: Optional[Callable[[str], None]] = None) -> None:
        """Stream import all downloaded raw datasets into SQLite."""
        with Database(datasets_path=self.db_path, bulk_mode=True) as db:
            conn = db.datasets_conn

            # 1. Import COR base forms
            for cor_filename in ["cor1.5.1.0.tsv", "cor1.01b.tsv", "corext1.0.tsv"]:
                cor_path = self.raw_dir / cor_filename
                if cor_path.exists():
                    if progress_cb:
                        progress_cb(f"Importerer {cor_filename}...")
                    batch = []
                    cursor = conn.cursor()
                    for item in stream_cor_forms(cor_path):
                        batch.append(item)
                        if len(batch) >= BATCH_SIZE:
                            cursor.executemany(
                                "INSERT INTO cor_forms VALUES (?, ?, ?, ?, ?, ?, ?)",
                                batch,
                            )
                            conn.commit()
                            batch = []
                    if batch:
                        cursor.executemany(
                            "INSERT INTO cor_forms VALUES (?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        conn.commit()

            # 2. Import COR semantic mappings
            sem_path = self.raw_dir / "cor.sem.1.0.tsv"
            if sem_path.exists():
                if progress_cb:
                    progress_cb("Importerer cor.sem.1.0.tsv...")
                batch = []
                cursor = conn.cursor()
                for item in stream_cor_sem(sem_path):
                    batch.append(item)
                    if len(batch) >= BATCH_SIZE:
                        cursor.executemany("INSERT INTO cor_sem VALUES (?, ?, ?, ?)", batch)
                        conn.commit()
                        batch = []
                if batch:
                    cursor.executemany("INSERT INTO cor_sem VALUES (?, ?, ?, ?)", batch)
                    conn.commit()

            # 3. Import COR definitions
            sem_ext_path = self.raw_dir / "cor.sem.ext.1.0.tsv"
            if sem_ext_path.exists():
                if progress_cb:
                    progress_cb("Importerer cor.sem.ext.1.0.tsv...")
                batch = []
                cursor = conn.cursor()
                for item in stream_cor_sem_ext(sem_ext_path):
                    batch.append(item)
                    if len(batch) >= BATCH_SIZE:
                        cursor.executemany(
                            "INSERT OR REPLACE INTO cor_definitions VALUES (?, ?, ?)",
                            batch,
                        )
                        conn.commit()
                        batch = []
                if batch:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO cor_definitions VALUES (?, ?, ?)",
                        batch,
                    )
                    conn.commit()

            # 4. Import ODS Fullforms
            ods_zip_path = self.raw_dir / "ods-fullforms.zip"
            ods_tsv_path = self.raw_dir / "ods-fullforms.tsv"

            if ods_tsv_path.exists():
                if progress_cb:
                    progress_cb("Importerer ods-fullforms.tsv...")
                with open(ods_tsv_path, "r", encoding="utf-8", errors="replace") as f:
                    batch = []
                    cursor = conn.cursor()
                    for item in stream_ods_forms(f):
                        batch.append(item)
                        if len(batch) >= BATCH_SIZE:
                            cursor.executemany("INSERT INTO ods_forms VALUES (?, ?, ?, ?, ?, ?)", batch)
                            conn.commit()
                            batch = []
                    if batch:
                        cursor.executemany("INSERT INTO ods_forms VALUES (?, ?, ?, ?, ?, ?)", batch)
                        conn.commit()

            elif ods_zip_path.exists():
                if progress_cb:
                    progress_cb("Åbner og streamer ods-fullforms.zip...")
                try:
                    with zipfile.ZipFile(ods_zip_path, "r") as zf:
                        # Find tsv inside zip
                        tsv_names = [n for n in zf.namelist() if n.endswith(".tsv") or "ods" in n.lower()]
                        if tsv_names:
                            with zf.open(tsv_names[0]) as zf_entry:
                                stream = io.TextIOWrapper(zf_entry, encoding="utf-8", errors="replace")
                                batch = []
                                cursor = conn.cursor()
                                for item in stream_ods_forms(stream):
                                    batch.append(item)
                                    if len(batch) >= BATCH_SIZE:
                                        cursor.executemany("INSERT INTO ods_forms VALUES (?, ?, ?, ?, ?, ?)", batch)
                                        conn.commit()
                                        batch = []
                                if batch:
                                    cursor.executemany("INSERT INTO ods_forms VALUES (?, ?, ?, ?, ?, ?)", batch)
                                    conn.commit()
                except Exception as exc:
                    logger.warning(f"Fejl ved streaming fra ods-fullforms.zip: {exc}")

            if progress_cb:
                progress_cb("Færdiggør SQLite indeksering...")
            conn.execute("ANALYZE;")
            conn.commit()
