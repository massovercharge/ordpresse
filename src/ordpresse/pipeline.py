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
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.scrapers import HistoricalScraper, SEED_HISTORICAL_ENTRIES
from ordpresse.sources.nordic_1800s import (
    NORDIC_1800S_ENTRIES,
    enrich_canonical_entry_with_historical,
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
        scraper = HistoricalScraper(db)

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

        # If full dictionary or seeds already resolved lemmas, only query remaining unresolved tokens
        unresolved_tokens = [t for t in all_tokens if t not in resolved_entries]
        if unresolved_tokens:
            emit(f"Starter opslag for {len(unresolved_tokens)} ekstra ord i COR & ODS...", 50)
            processed = 0
            total = len(unresolved_tokens)

            for token in sorted(unresolved_tokens):
                processed += 1
                if processed % 50 == 0 or processed == total:
                    pct = 50 + int(25 * (processed / total))
                    emit(f"Behandler ord {processed}/{total} ({token})...", pct)

                # Try step 0: Check curated Nordic 1800s vocabulary directly
                nordic_match = get_nordic_1800s_entry(token)
                if nordic_match:
                    hw = nordic_match.headword.lower()
                    ods_infs = db.lookup_ods_headword_inflections(nordic_match.headword) if db else []
                    all_inflections = set(nordic_match.inflections) | set(ods_infs) | set(nordic_match.historical_spellings) | {token}
                    nordic_match.inflections = all_inflections

                    # Check if COR also has modern definitions (false-friend enrichment)
                    cor_defs = db.lookup_cor_lemma_definitions(hw)
                    if cor_defs:
                        modern_senses = [
                            SenseItem(
                                definition=d["definition"],
                                quote=d.get("examples") or None,
                                source_tag="COR-SEM",
                            )
                            for d in cor_defs[:3]
                        ]
                        nordic_match.modern_senses = modern_senses
                        if "COR-SEM" not in nordic_match.sources_used:
                            nordic_match.sources_used.append("COR-SEM")

                    syns = generate_orthographic_synonyms(hw, list(all_inflections))
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=render_canonical_html(nordic_match),
                        synonyms=syns,
                    )
                    continue

                # Try step 1: Check Modern COR in SQLite
                cor_defs = db.lookup_cor_lemma_definitions(token)
                if cor_defs:
                    lemma = cor_defs[0]["lemma"].lower()
                    pos = normalize_pos(cor_defs[0].get("pos", ""))
                    modern_senses = [
                        SenseItem(
                            definition=d["definition"],
                            quote=d.get("examples") or None,
                            source_tag="COR-SEM",
                        )
                        for d in cor_defs[:3]
                    ]
                    cor_inflections = db.lookup_cor_inflections(lemma)
                    ods_inflections = db.lookup_ods_headword_inflections(lemma)
                    summary_hook = modern_senses[0].definition[:160] if modern_senses else ""

                    canonical = CanonicalEntry(
                        headword=lemma,
                        pos=pos,
                        inflections=set(cor_inflections + ods_inflections + [token]),
                        summary_hook=summary_hook,
                        modern_senses=modern_senses,
                        sources_used=["COR-SEM"],
                    )

                    # Check false-friend enrichment from 1800s
                    if lemma in NORDIC_1800S_ENTRIES:
                        canonical = enrich_canonical_entry_with_historical(
                            canonical, NORDIC_1800S_ENTRIES[lemma]
                        )

                    html = render_canonical_html(canonical)
                    synonyms = generate_orthographic_synonyms(lemma, list(canonical.inflections))
                    resolved_entries[lemma] = StarDictEntry(headword=lemma, html_content=html, synonyms=synonyms)
                    continue

                # Try step 2: Check ODS SQLite forms using candidate stems
                stem_candidates = resolve_danish_stem_candidates(token)
                found_historical = False

                for candidate in stem_candidates:
                    # Check 1800s candidate
                    nordic_cand = get_nordic_1800s_entry(candidate)
                    if nordic_cand:
                        hw = nordic_cand.headword.lower()
                        ods_infs = db.lookup_ods_headword_inflections(nordic_cand.headword) if db else []
                        all_inflections = set(nordic_cand.inflections) | set(ods_infs) | set(nordic_cand.historical_spellings) | {token, candidate}
                        nordic_cand.inflections = all_inflections
                        syns = generate_orthographic_synonyms(hw, list(all_inflections))
                        resolved_entries[hw] = StarDictEntry(
                            headword=hw,
                            html_content=render_canonical_html(nordic_cand),
                            synonyms=syns,
                        )
                        found_historical = True
                        break

                    ods_hits = db.lookup_ods_inflection(candidate)
                    if ods_hits:
                        hw = ods_hits[0]["headword"].lower()
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
                        hw = def_data["headword"].lower()
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
            build_id=actual_build_id,
            bookname=self.bookname,
            entry_count=len(resolved_entries),
            synonym_count=total_synonyms,
            output_dir=target_dir,
            zip_path=zip_path,
            files=compiled_files,
        )
