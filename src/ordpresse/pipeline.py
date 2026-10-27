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
from ordpresse.orthography import generate_orthographic_synonyms
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.sources.nordic_1800s import (
    NORDIC_1800S_ENTRIES,
    get_nordic_1800s_entry,
)
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
        build_id: Optional[str] = None,
    ) -> BuildResult:
        """Execute the dictionary building process."""
        actual_build_id = build_id or uuid.uuid4().hex[:8]
        target_dir = self.output_dir / f"build_{actual_build_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        def emit(msg: str, pct: int):
            if progress_cb:
                progress_cb(msg, pct)
            logger.info(f"[{pct}%] {msg}")

        emit("Initialiserer ordbogsbygger...", 5)
        db = self.get_db()

        resolved_entries: dict[str, StarDictEntry] = {}
        cor_count = 0

        # a) Load all COR definitions from datasets SQLite
        if db.datasets_path.exists() and db.datasets_path.stat().st_size > 1024:
            emit("Indlæser det komplette COR-datasæt...", 15)
            cor_entries = db.get_all_cor_lemmas_with_definitions()
            cor_count = len(cor_entries)
            for item in cor_entries:
                hw = item["headword"]
                syns = generate_orthographic_synonyms(hw, item["synonyms"])
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=item["html_content"],
                    synonyms=syns,
                )
        elif full_dictionary and (allow_network or any(db.datasets_path.parent.glob("raw/*.tsv"))):
            emit("SQLite datasæt mangler. Starter automatisk import fra rå filer...", 16)
            try:
                from ordpresse.datasets import DatasetImporter
                importer = DatasetImporter(db_path=db.datasets_path)
                importer.import_all(progress_cb=lambda msg: emit(msg, 18))
                cor_entries = db.get_all_cor_lemmas_with_definitions()
                cor_count = len(cor_entries)
                for item in cor_entries:
                    hw = item["headword"]
                    syns = generate_orthographic_synonyms(hw, item["synonyms"])
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=item["html_content"],
                        synonyms=syns,
                    )
            except Exception as e:
                logger.warning(f"Kunne ikke auto-importere datasæt: {e}")

        # Track distinct historical headwords from 1800s core & cache
        historical_headwords: set[str] = set()

        # b) Always merge all curated entries from nordic_1800s.py (enriching COR entries or adding new)
        for seed_word, canonical_seed in NORDIC_1800S_ENTRIES.items():
            hw = canonical_seed.headword.lower()
            historical_headwords.add(hw)
            ods_infs = db.lookup_ods_headword_inflections(canonical_seed.headword) if db else []
            all_inflections = set(canonical_seed.inflections) | set(ods_infs) | set(canonical_seed.historical_spellings)
            canonical_seed.inflections = all_inflections

            # If entry already exists from COR-SEM, enrich it
            if hw in resolved_entries:
                existing_entry = resolved_entries[hw]
                canonical_seed.sources_used = list(dict.fromkeys(canonical_seed.sources_used + ["COR-SEM"]))
                merged_syns = generate_orthographic_synonyms(hw, list(all_inflections) + existing_entry.synonyms)
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=render_canonical_html(canonical_seed),
                    synonyms=merged_syns,
                )
            else:
                syns = generate_orthographic_synonyms(hw, list(all_inflections))
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=render_canonical_html(canonical_seed),
                    synonyms=syns,
                )

        # c) Always merge all previously cached historical definitions from cache.sqlite3
        cached_defs = db.get_all_cached_definitions() if db else []
        for c_item in cached_defs:
            c_hw = c_item["headword"].lower()
            if c_item.get("source") == "seed" or c_hw in NORDIC_1800S_ENTRIES:
                if c_hw in resolved_entries:
                    existing_entry = resolved_entries[c_hw]
                    existing_entry.synonyms = generate_orthographic_synonyms(
                        c_hw, existing_entry.synonyms + c_item.get("synonyms", [])
                    )
                continue

            historical_headwords.add(c_hw)
            c_syns = c_item.get("synonyms", [])
            if c_hw in resolved_entries:
                existing_entry = resolved_entries[c_hw]
                existing_entry.synonyms = generate_orthographic_synonyms(
                    c_hw, existing_entry.synonyms + c_syns
                )
            else:
                syns = generate_orthographic_synonyms(c_hw, c_syns)
                resolved_entries[c_hw] = StarDictEntry(
                    headword=c_hw,
                    html_content=c_item["html_content"],
                    synonyms=syns,
                )

        historical_count = len(historical_headwords)
        emit(
            f"Indlæst {cor_count} hovedord fra COR + {historical_count} historiske opslag fra 1800-talskernen & ODS-cache.",
            35,
        )

        all_tokens: set[str] = set()
        if custom_words:
            for cw in custom_words:
                w = cw.strip().lower()
                if w:
                    all_tokens.add(w)

        if epub_files:
            emit(f"Scanner {len(epub_files)} EPUB fil(er)...", 38)
            for idx, epub in enumerate(epub_files, start=1):
                tokens = scan_epub_tokens(epub)
                all_tokens.update(tokens)
                emit(f"Scannet EPUB #{idx}: Fandt {len(tokens)} ord.", 38 + int(10 * (idx / len(epub_files))))

        # Collect all existing headwords and synonyms
        known_surface_strings: set[str] = set(resolved_entries.keys())
        for entry in resolved_entries.values():
            for s in entry.synonyms:
                known_surface_strings.add(s.lower())

        unresolved_tokens = [t for t in all_tokens if t not in known_surface_strings]
        if unresolved_tokens:
            emit(f"Matcher {len(unresolved_tokens)} ord fra EPUBs mod COR & ODS...", 45)

            # Step 1: Check curated Nordic 1800s vocabulary directly
            still_unresolved: list[str] = []
            for t in unresolved_tokens:
                nordic_match = get_nordic_1800s_entry(t)
                if nordic_match:
                    hw = nordic_match.headword.lower()
                    if hw in resolved_entries:
                        resolved_entries[hw].synonyms.append(t)
                    else:
                        ods_infs = db.lookup_ods_headword_inflections(nordic_match.headword) if db else []
                        all_inflections = set(nordic_match.inflections) | set(ods_infs) | {t}
                        nordic_match.inflections = all_inflections
                        resolved_entries[hw] = StarDictEntry(
                            headword=hw,
                            html_content=render_canonical_html(nordic_match),
                            synonyms=generate_orthographic_synonyms(hw, list(all_inflections)),
                        )
                    known_surface_strings.add(t)
                else:
                    still_unresolved.append(t)

            # Step 2: Batch-lookup against COR forms (618,000+ inflections in SQLite)
            cor_form_map = db.lookup_cor_forms_batch(still_unresolved) if db else {}
            unresolved_after_cor: list[str] = []
            cor_attached_count = 0

            for t in still_unresolved:
                lem = cor_form_map.get(t)
                if lem and lem in resolved_entries:
                    resolved_entries[lem].synonyms.append(t)
                    known_surface_strings.add(t)
                    cor_attached_count += 1
                else:
                    unresolved_after_cor.append(t)

            # Step 3: Batch-lookup against ODS forms (1,260,000+ historical inflections in SQLite)
            ods_form_map = db.lookup_ods_forms_batch(unresolved_after_cor) if db else {}
            new_ods_words: dict[str, dict] = {}
            ods_attached_count = 0
            non_danish_ignored = 0

            for t in unresolved_after_cor:
                if t in ods_form_map:
                    hw, pos = ods_form_map[t]
                    if hw in resolved_entries:
                        resolved_entries[hw].synonyms.append(t)
                        known_surface_strings.add(t)
                        ods_attached_count += 1
                    else:
                        if hw not in new_ods_words:
                            new_ods_words[hw] = {"pos": pos, "inflections": {t, hw}}
                        else:
                            new_ods_words[hw]["inflections"].add(t)
                else:
                    non_danish_ignored += 1

            emit(
                f"SQLite-matching fuldført: {cor_attached_count} knyttet til COR, {ods_attached_count} knyttet til ODS, "
                f"{len(new_ods_words)} nye historiske opslag fundet ({non_danish_ignored} egennavne/uindekserede sprunget over).",
                60,
            )

            # Step 4: Create canonical historical entries for new ODS headwords
            cached_map = {c["headword"].lower(): c for c in db.get_all_cached_definitions()} if db else {}

            for hw, data in new_ods_words.items():
                if hw in cached_map:
                    c_item = cached_map[hw]
                    syns = generate_orthographic_synonyms(hw, list(data["inflections"]) + c_item.get("synonyms", []))
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=c_item["html_content"],
                        synonyms=syns,
                    )
                else:
                    canonical = CanonicalEntry(
                        headword=hw,
                        pos=normalize_pos(data["pos"]),
                        inflections=data["inflections"],
                        summary_hook="Historisk opslag fra Ordbog over det danske Sprog (ODS).",
                        historical_senses=[
                            SenseItem(
                                definition=f"Historisk opslag for »{hw}« i Ordbog over det danske Sprog.",
                                label="hist.",
                                source_tag="ODS",
                            )
                        ],
                        sources_used=["ODS"],
                    )
                    html = render_canonical_html(canonical)
                    syns = generate_orthographic_synonyms(hw, list(data["inflections"]))
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=html,
                        synonyms=syns,
                    )

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
            build_id=actual_build_id,
            bookname=self.bookname,
            entry_count=len(resolved_entries),
            synonym_count=total_synonyms,
            output_dir=target_dir,
            zip_path=zip_path,
            files=compiled_files,
        )
