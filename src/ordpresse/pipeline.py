"""Pipeline orchestrator linking SQLite datasets, EPUB scanners, scrapers, and StarDict compiler."""

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import uuid
import zipfile
from typing import Callable, List, Optional, Sequence, Union

from ordpresse.config import (
    DEFAULT_AUTHOR,
    DEFAULT_BOOKNAME,
    DEFAULT_DESCRIPTION,
    OUTPUT_DIR,
)
from ordpresse.db import Database
from ordpresse.epub_scanner import scan_epub_tokens
from ordpresse.orthography import (
    generate_orthographic_synonyms,
    resolve_danish_stem_candidates,
)
from ordpresse.scrapers import HistoricalScraper, SEED_HISTORICAL_ENTRIES
from ordpresse.stardict_writer import StarDictEntry, StarDictWriter

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    build_id: str
    bookname: str
    entry_count: int
    synonym_count: int
    output_dir: Path
    zip_path: Path
    files: dict[str, Path]


class Pipeline:
    """End-to-end dictionary build pipeline."""

    def __init__(
        self,
        db: Optional[Database] = None,
        output_dir: Path = OUTPUT_DIR,
        bookname: str = DEFAULT_BOOKNAME,
        author: str = DEFAULT_AUTHOR,
        description: str = DEFAULT_DESCRIPTION,
    ):
        self._db = db
        self.output_dir = Path(output_dir)
        self.bookname = bookname
        self.author = author
        self.description = description

    def get_db(self) -> Database:
        if self._db is None:
            self._db = Database()
        return self._db

    def run(
        self,
        epub_files: Optional[Sequence[Union[Path, str, bytes]]] = None,
        custom_words: Optional[Sequence[str]] = None,
        include_seeds: bool = True,
        full_dictionary: bool = False,
        allow_network: bool = True,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> BuildResult:
        """Execute the dictionary building process."""
        build_id = uuid.uuid4().hex[:8]
        target_dir = self.output_dir / f"build_{build_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        def emit(msg: str, pct: int):
            if progress_cb:
                progress_cb(msg, pct)
            logger.info(f"[{pct}%] {msg}")

        emit("Initialiserer ordbogsbygger...", 5)
        db = self.get_db()
        scraper = HistoricalScraper(db)

        resolved_entries: dict[str, StarDictEntry] = {}

        # If full dictionary is requested, load all COR definitions
        if full_dictionary:
            emit("Henter alle definerede opslagsord fra det lokale COR-datasæt...", 15)
            cor_entries = db.get_all_cor_lemmas_with_definitions()
            for item in cor_entries:
                hw = item["headword"]
                syns = generate_orthographic_synonyms(hw, item["synonyms"])
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=item["html_content"],
                    synonyms=syns,
                )
            emit(f"Indlæst {len(resolved_entries)} hovedord fra COR.", 25)

        # 1. Collect tokens from EPUBs and custom words
        all_tokens: set[str] = set()

        if include_seeds:
            for seed_word in SEED_HISTORICAL_ENTRIES.keys():
                all_tokens.add(seed_word)

        if custom_words:
            for cw in custom_words:
                w = cw.strip().lower()
                if w:
                    all_tokens.add(w)

        if epub_files:
            emit(f"Scanner {len(epub_files)} EPUB fil(er)...", 10)
            for idx, epub in enumerate(epub_files, start=1):
                tokens = scan_epub_tokens(epub)
                all_tokens.update(tokens)
                emit(f"Scannet EPUB #{idx}: Fandt {len(tokens)} ord.", 10 + int(20 * (idx / len(epub_files))))

        emit(f"Identificerede {len(all_tokens)} ekstra opslagsord. Starter opslag i COR & ODS...", 30)

        # 2. Resolve entries
        processed = 0
        total = max(len(all_tokens), 1)

        for token in sorted(all_tokens):
            processed += 1
            if processed % 50 == 0 or processed == total:
                pct = 30 + int(45 * (processed / total))
                emit(f"Behandler ord {processed}/{total} ({token})...", pct)

            # Try step 1: Check Modern COR in SQLite
            cor_defs = db.lookup_cor_lemma_definitions(token)
            if cor_defs:
                lemma = cor_defs[0]["lemma"]
                pos = cor_defs[0].get("pos", "")
                def_items = "".join(
                    f"<li>{d['definition']}"
                    + (f" <br/><em>{d['examples']}</em>" if d.get("examples") else "")
                    + "</li>"
                    for d in cor_defs
                )
                html = (
                    f"<div class='ordpresse-article cor'>"
                    f"<h2>{lemma} <small>({pos})</small></h2>"
                    f"<ol>{def_items}</ol>"
                    f"</div>"
                )
                cor_inflections = db.lookup_cor_inflections(lemma)
                synonyms = generate_orthographic_synonyms(lemma, cor_inflections + [token])
                resolved_entries[lemma] = StarDictEntry(headword=lemma, html_content=html, synonyms=synonyms)
                continue

            # Try step 2: Check ODS SQLite forms using candidate stems
            stem_candidates = resolve_danish_stem_candidates(token)
            found_historical = False

            for candidate in stem_candidates:
                ods_hits = db.lookup_ods_inflection(candidate)
                if ods_hits:
                    hw = ods_hits[0]["headword"]
                    # Fetch definition via scraper / seed / cache
                    def_data = scraper.fetch_word_definition(hw, allow_remote=allow_network)
                    if def_data:
                        ods_inflections = db.lookup_ods_headword_inflections(hw)
                        syns = generate_orthographic_synonyms(
                            hw,
                            def_data.get("synonyms", []) + ods_inflections + [token, candidate],
                        )
                        resolved_entries[hw] = StarDictEntry(
                            headword=hw,
                            html_content=def_data["html_content"],
                            synonyms=syns,
                        )
                        found_historical = True
                        break

            if found_historical:
                continue

            # Try step 3: Direct scraper fallback (checks cache & seed entries)
            for candidate in stem_candidates:
                def_data = scraper.fetch_word_definition(candidate, allow_remote=allow_network)
                if def_data:
                    hw = def_data["headword"]
                    syns = generate_orthographic_synonyms(
                        hw,
                        def_data.get("synonyms", []) + [token, candidate],
                    )
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=def_data["html_content"],
                        synonyms=syns,
                    )
                    break

        emit(f"Opslag fuldført. Kompilerer {len(resolved_entries)} ordbogsartikler til StarDict v2.4.2...", 80)

        # 3. Compile StarDict binaries
        writer = StarDictWriter(
            bookname=self.bookname,
            author=self.author,
            description=self.description,
        )
        base_name = "ordpresse"
        compiled_files = writer.compile(
            entries=list(resolved_entries.values()),
            output_dir=target_dir,
            base_filename=base_name,
            compress_dict=True,
        )

        emit("Genererer KOReader installations-ZIP...", 95)
        # 4. Create ZIP package for KOReader
        zip_path = target_dir / f"{base_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f_key, f_path in compiled_files.items():
                if f_path.exists():
                    zf.write(f_path, arcname=f"{base_name}/{f_path.name}")

        # Count total synonyms
        total_synonyms = sum(len(e.synonyms) for e in resolved_entries.values())
        emit("Ordbogen er færdigbygget og klar til download!", 100)

        return BuildResult(
            build_id=build_id,
            bookname=self.bookname,
            entry_count=len(resolved_entries),
            synonym_count=total_synonyms,
            output_dir=target_dir,
            zip_path=zip_path,
            files=compiled_files,
        )
