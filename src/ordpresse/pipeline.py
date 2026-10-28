import asyncio
from ordpresse.sources.rate_limiter import coordinator, ScrapeCoordinator
"""Pipeline orchestrator linking SQLite datasets, EPUB scanners, scrapers, and StarDict compiler."""

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import uuid
import zipfile
from typing import Callable, List, Optional, Sequence, Union

from collections import Counter
import time

from ordpresse.config import (
    DEFAULT_AUTHOR,
    DEFAULT_BOOKNAME,
    DEFAULT_DESCRIPTION,
    MAX_ONLINE_SCRAPE_BATCH,
    MIN_EPUB_WORD_FREQUENCY_FOR_SCRAPING,
    OUTPUT_DIR,
)
from ordpresse.db import Database
from ordpresse.epub_scanner import scan_epub_token_counts, scan_epub_tokens
from ordpresse.orthography import generate_orthographic_synonyms
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.sources.molbech_1859 import (
    MOLBECH_1859_ENTRIES,
    get_molbech_1859_entry,
)
from ordpresse.sources.nordic_1800s import (
    NORDIC_1800S_ENTRIES,
    get_nordic_1800s_entry,
)
from ordpresse.sources.ods_scraper import ODSScraperRateLimitError
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



def _acquire_token(
    coord: ScrapeCoordinator,
    build_id: str,
    word: str,
    on_wait_cb: Optional[Callable[[float, int], None]] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> bool:
    """Run coordinator.acquire_token_or_peer_resolve safely across threads and event loops."""
    async def async_wait_cb(wait_s: float, pending: int):
        if on_wait_cb:
            on_wait_cb(wait_s, pending)

    coro = coord.acquire_token_or_peer_resolve(
        build_id, word, on_wait_callback=async_wait_cb
    )

    if loop is not None and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result()

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop and current_loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


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
        is_cancelled: Optional[Callable[[], bool]] = None,
        build_id: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> BuildResult:
        """Execute the dictionary building process."""
        actual_build_id = build_id or uuid.uuid4().hex[:8]
        target_dir = self.output_dir / f"build_{actual_build_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        def emit(msg: str, pct: int):
            if is_cancelled and is_cancelled():
                raise RuntimeError("Byggeprocessen blev afbrudt af brugeren.")
            if progress_cb:
                progress_cb(msg, pct)
            logger.info(f"[{pct}%] {msg}")

        emit("Initialiserer ordbogsbygger...", 5)
        db = self.get_db()

        resolved_entries: dict[str, StarDictEntry] = {}
        cor_count = 0
        cor_id_to_headword: dict[str, str] = {}

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
                for lid in item.get("lemma_ids", []):
                    cor_id_to_headword[lid] = hw
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

        # b) Always merge all curated entries from nordic_1800s.py and molbech_1859.py
        historical_lexicons = list(NORDIC_1800S_ENTRIES.values()) + list(MOLBECH_1859_ENTRIES.values())
        for canonical_seed in historical_lexicons:
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
        token_counts: Counter = Counter()
        custom_tokens_set: set[str] = set()
        if custom_words:
            for cw in custom_words:
                w = cw.strip().lower()
                if w:
                    all_tokens.add(w)
                    custom_tokens_set.add(w)
                    token_counts[w] += 999999

        if epub_files:
            emit(f"Scanner {len(epub_files)} EPUB fil(er)...", 38)
            for idx, epub in enumerate(epub_files, start=1):
                counts = scan_epub_token_counts(epub)
                token_counts.update(counts)
                all_tokens.update(counts.keys())
                emit(f"Scannet EPUB #{idx}: Fandt {len(counts)} ord.", 38 + int(10 * (idx / len(epub_files))))

        # Collect all existing headwords and synonyms
        known_surface_strings: set[str] = set(resolved_entries.keys())
        for entry in resolved_entries.values():
            for s in entry.synonyms:
                known_surface_strings.add(s.lower())

        unresolved_tokens = [t for t in all_tokens if t not in known_surface_strings]
        if unresolved_tokens:
            emit(f"Matcher {len(unresolved_tokens)} ord fra EPUBs mod COR & ODS...", 45)

            # Step 1: Check curated Nordic 1800s and Molbech 1859 vocabulary directly
            still_unresolved: list[str] = []
            for t in unresolved_tokens:
                hist_match = get_nordic_1800s_entry(t) or get_molbech_1859_entry(t)
                if hist_match:
                    hw = hist_match.headword.lower()
                    if hw in resolved_entries:
                        resolved_entries[hw].synonyms.append(t)
                    else:
                        ods_infs = db.lookup_ods_headword_inflections(hist_match.headword) if db else []
                        all_inflections = set(hist_match.inflections) | set(ods_infs) | {t}
                        hist_match.inflections = all_inflections
                        resolved_entries[hw] = StarDictEntry(
                            headword=hw,
                            html_content=render_canonical_html(hist_match),
                            synonyms=generate_orthographic_synonyms(hw, list(all_inflections)),
                        )
                    known_surface_strings.add(t)
                else:
                    still_unresolved.append(t)

            # Step 2: Batch-lookup against COR forms (618,000+ inflections in SQLite)
            cor_form_map = db.lookup_cor_forms_batch(still_unresolved) if db else {}
            unresolved_after_cor: list[str] = []
            cor_attached_count = 0

            GRAMMATICAL_POS = {
                "art.", "art",
                "præp.", "præp",
                "pron.", "pron",
                "konj.", "konj",
                "talord.", "talord",
                "adv.", "adv",
                "udråbsord.", "udråbsord",
                "lydord.", "lydord",
                "infmærke.", "formsubj.", "fork.", "fork",
            }

            for t in still_unresolved:
                matches = cor_form_map.get(t, [])
                if not matches:
                    unresolved_after_cor.append(t)
                    continue

                # 1. Match against existing entry by lemma
                attached = False
                for m in matches:
                    lem = m["lemma"]
                    if lem in resolved_entries:
                        resolved_entries[lem].synonyms.append(t)
                        known_surface_strings.add(t)
                        cor_attached_count += 1
                        attached = True
                        break
                if attached:
                    continue

                # 2. Match against existing entry by lemma_id (e.g. moder -> mor)
                for m in matches:
                    lid = m.get("lemma_id")
                    if lid and lid in cor_id_to_headword:
                        target_hw = cor_id_to_headword[lid]
                        if target_hw in resolved_entries:
                            resolved_entries[target_hw].synonyms.append(t)
                            known_surface_strings.add(t)
                            cor_attached_count += 1
                            attached = True
                            break
                if attached:
                    continue

                # 3. Known modern COR word without definitions (grammatical word or modern lemma)
                # Create clean lightweight entry so it is never treated as an unknown historical word
                best_match = matches[0]
                for m in matches:
                    if m.get("pos") in GRAMMATICAL_POS:
                        best_match = m
                        break

                lem = best_match["lemma"].lower()
                pos_raw = best_match.get("pos", "")
                pos_norm = normalize_pos(pos_raw)

                if lem in resolved_entries:
                    resolved_entries[lem].synonyms.append(t)
                else:
                    canonical = CanonicalEntry(
                        headword=lem,
                        pos=pos_norm,
                        inflections={t, lem},
                        summary_hook=f"{lem} ({pos_norm})" if pos_norm else lem,
                        sources_used=["COR"],
                    )
                    resolved_entries[lem] = StarDictEntry(
                        headword=lem,
                        html_content=render_canonical_html(canonical),
                        synonyms=generate_orthographic_synonyms(lem, [t, lem]),
                    )
                known_surface_strings.add(t)
                cor_attached_count += 1

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
                        cnt = token_counts.get(t, 1)
                        is_cust = t in custom_tokens_set or hw.lower() in custom_tokens_set
                        if hw not in new_ods_words:
                            new_ods_words[hw] = {
                                "pos": pos,
                                "inflections": {t, hw},
                                "frequency": cnt,
                                "is_custom": is_cust,
                            }
                        else:
                            new_ods_words[hw]["inflections"].add(t)
                            new_ods_words[hw]["frequency"] += cnt
                            if is_cust:
                                new_ods_words[hw]["is_custom"] = True
                else:
                    non_danish_ignored += 1

            emit(
                f"SQLite-matching fuldført: {cor_attached_count} knyttet til COR, {ods_attached_count} knyttet til ODS, "
                f"{len(new_ods_words)} nye historiske opslag fundet ({non_danish_ignored} egennavne/uindekserede sprunget over).",
                60,
            )

            # Step 4: Create canonical historical entries for new ODS headwords
            if db:
                db.clean_dummy_cached_definitions()

            cached_map = {
                c["headword"].lower(): c
                for c in db.get_all_cached_definitions()
                if "Historisk opslag for »" not in c.get("html_content", "")
            } if db else {}

            from ordpresse.scrapers import HistoricalScraper
            scraper = HistoricalScraper(db) if db else None

            total_ods = len(new_ods_words)
            uncached_words = [hw for hw in new_ods_words if hw not in cached_map]
            cached_words = [hw for hw in new_ods_words if hw in cached_map]

            # 1. Instantly resolve all already-cached words
            for hw in cached_words:
                c_item = cached_map[hw]
                syns = generate_orthographic_synonyms(
                    hw, list(new_ods_words[hw]["inflections"]) + c_item.get("synonyms", [])
                )
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=c_item["html_content"],
                    synonyms=syns,
                )

            # 2. Sort uncached words: custom words first, then by frequency descending
            uncached_words.sort(
                key=lambda hw: (
                    1 if new_ods_words[hw].get("is_custom") else 0,
                    new_ods_words[hw].get("frequency", 1),
                    hw,
                ),
                reverse=True,
            )

            # Filter eligible words to scrape online (custom words or words appearing >= threshold)
            frequent_uncached = [
                hw for hw in uncached_words
                if new_ods_words[hw].get("is_custom")
                or new_ods_words[hw].get("frequency", 1) >= MIN_EPUB_WORD_FREQUENCY_FOR_SCRAPING
            ]

            words_to_scrape = []
            if allow_network and uncached_words:
                if frequent_uncached:
                    words_to_scrape = frequent_uncached[:MAX_ONLINE_SCRAPE_BATCH]
                elif all(new_ods_words[hw].get("frequency", 1) == 1 for hw in new_ods_words):
                    # Corpus has only single-occurrence words (e.g. short sample / wordlist)
                    words_to_scrape = uncached_words[:MAX_ONLINE_SCRAPE_BATCH]
                else:
                    emit(
                        f"Alle hyppige historiske ord fra bøgerne (frekvens ≥ {MIN_EPUB_WORD_FREQUENCY_FOR_SCRAPING}) "
                        f"er allerede i cachen ({len(cached_words)} genbrugt).",
                        75,
                    )

            if words_to_scrape:
                total_to_scrape = len(words_to_scrape)
                coordinator.register_build(actual_build_id, words_to_scrape)
                emit(
                    f"Henter {total_to_scrape} prioriterede historiske ord over nettet "
                    f"({len(cached_words)} genbrugt fra cache, {len(uncached_words) - total_to_scrape} afventer)...",
                    60,
                )

                completed = 0
                last_emit_time = time.time()
                try:
                    for idx, hw in enumerate(words_to_scrape, start=1):
                        if is_cancelled and is_cancelled():
                            raise RuntimeError("Byggeprocessen blev afbrudt af brugeren.")

                        if coordinator.should_skip_remaining(actual_build_id):
                            emit("Bruger valgte at afslutte byg og springe resterende kø over.", 79)
                            break

                        # Check if peer bookmarklet already submitted it
                        cached_entry = db.get_cached_definition(hw) if db else None
                        if cached_entry:
                            coordinator.mark_word_resolved(hw, actual_build_id)
                            actual_hw = cached_entry["headword"].lower()
                            syns = generate_orthographic_synonyms(
                                actual_hw,
                                list(new_ods_words[hw]["inflections"]) + cached_entry.get("synonyms", []) + [hw],
                            )
                            resolved_entries[actual_hw] = StarDictEntry(
                                headword=actual_hw,
                                html_content=cached_entry["html_content"],
                                synonyms=syns,
                            )
                            completed += 1
                            continue

                        # Rate-limit acquisition with peer-resolve wakeup
                        current_pct = 60 + int(20 * (idx / total_to_scrape))
                        def on_wait(wait_s: float, pending: int):
                            emit(
                                f"Venter på server-kvote ({wait_s:.0f}s, {pending} ord i kø) — brug Bookmarklet for lyn-hentning!",
                                current_pct,
                            )

                        should_scrape = _acquire_token(
                            coordinator, actual_build_id, hw, on_wait_cb=on_wait, loop=loop
                        )

                        if not should_scrape:
                            # Resolved by peer or skipped
                            cached_entry = db.get_cached_definition(hw) if db else None
                            if cached_entry:
                                actual_hw = cached_entry["headword"].lower()
                                syns = generate_orthographic_synonyms(
                                    actual_hw,
                                    list(new_ods_words[hw]["inflections"]) + cached_entry.get("synonyms", []) + [hw],
                                )
                                resolved_entries[actual_hw] = StarDictEntry(
                                    headword=actual_hw,
                                    html_content=cached_entry["html_content"],
                                    synonyms=syns,
                                )
                            if coordinator.should_skip_remaining(actual_build_id):
                                emit("Afslutter online-kø efter brugeranmodning...", 79)
                                break
                            completed += 1
                            continue

                        def_data = None
                        try:
                            def_data = scraper.fetch_word_definition(hw, allow_remote=True) if scraper else None
                        except ODSScraperRateLimitError as rle:
                            pct = 60 + int(20 * (idx / total_to_scrape))
                            emit(
                                f"Advarsel: ordnet.dk anmodede om pause ({rle}). Online-hentning afbrudt pænt; "
                                f"ordbogen færdiggøres med de indhentede ord.",
                                pct,
                            )
                            logger.warning(f"Rate limit reached on {hw}: {rle}. Halting online scraping.")
                            break
                        except Exception as e:
                            logger.debug(f"Error fetching {hw}: {e}")

                        if def_data:
                            coordinator.mark_word_resolved(hw, actual_build_id)
                            actual_hw = def_data["headword"].lower()
                            syns = generate_orthographic_synonyms(
                                actual_hw,
                                list(new_ods_words[hw]["inflections"]) + def_data.get("synonyms", []) + [hw],
                            )
                            resolved_entries[actual_hw] = StarDictEntry(
                                headword=actual_hw,
                                html_content=def_data["html_content"],
                                synonyms=syns,
                            )

                        completed += 1
                        now = time.time()
                        if completed % 5 == 0 or completed == total_to_scrape or (now - last_emit_time) >= 1.5:
                            pct = 60 + int(20 * (completed / total_to_scrape))
                            freq = new_ods_words[hw].get("frequency", 1)
                            emit(
                                f"Henter historiske ODS-opslag: {completed}/{total_to_scrape} ({pct}%) — »{hw}« (frekvens: {freq})",
                                pct,
                            )
                            last_emit_time = now
                finally:
                    coordinator.unregister_build(actual_build_id)

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
