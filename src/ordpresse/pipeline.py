import re
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
    MAX_SERVER_SCRAPE_BATCH,
    MIN_EPUB_WORD_FREQUENCY_FOR_SCRAPING,
    OUTPUT_DIR,
)
from ordpresse.db import Database
from ordpresse.epub_scanner import scan_epub_token_counts, scan_epub_tokens
from ordpresse.orthography import (
    classify_external_lookup_priority,
    generate_orthographic_synonyms,
    sanitize_headword,
    synthesize_compound_entry,
)
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.synthesizer import is_valid_canonical_entry
from ordpresse.sources.ddo_scraper import DDOScraper, DDOScraperRateLimitError
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
        scrape_mode: str = "bookmarklet",
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
        resolved_canonical: dict[str, CanonicalEntry] = {}
        cor_count = 0
        cor_id_to_headword: dict[str, str] = {}

        # a) Load all COR definitions from datasets SQLite
        if db.datasets_path.exists() and db.datasets_path.stat().st_size > 1024:
            emit("Indlæser det komplette COR-datasæt...", 15)
            cor_entries = db.get_all_cor_lemmas_with_definitions()
            cor_count = len(cor_entries)
            for item in cor_entries:
                raw_hw = item["headword"]
                clean_hw, extra_syns = sanitize_headword(raw_hw)
                if not clean_hw:
                    continue
                hw = clean_hw.lower()
                syns = generate_orthographic_synonyms(hw, list(item["synonyms"]) + list(extra_syns))
                resolved_entries[hw] = StarDictEntry(
                    headword=hw,
                    html_content=item["html_content"],
                    synonyms=syns,
                )
                if item.get("canonical_entry"):
                    resolved_canonical[hw] = item["canonical_entry"]
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
                    raw_hw = item["headword"]
                    clean_hw, extra_syns = sanitize_headword(raw_hw)
                    if not clean_hw:
                        continue
                    hw = clean_hw.lower()
                    syns = generate_orthographic_synonyms(hw, list(item["synonyms"]) + list(extra_syns))
                    resolved_entries[hw] = StarDictEntry(
                        headword=hw,
                        html_content=item["html_content"],
                        synonyms=syns,
                    )
                    if item.get("canonical_entry"):
                        resolved_canonical[hw] = item["canonical_entry"]
            except Exception as e:
                logger.warning(f"Kunne ikke auto-importere datasæt: {e}")

        # Track distinct historical headwords from 1800s core & cache
        historical_headwords: set[str] = set()

        # b) Always merge all curated entries from nordic_1800s.py and molbech_1859.py
        historical_lexicons = list(MOLBECH_1859_ENTRIES.values()) + list(NORDIC_1800S_ENTRIES.values())
        for canonical_seed in historical_lexicons:
            clean_hw, extra_syns = sanitize_headword(canonical_seed.headword)
            if not clean_hw:
                continue
            canonical_seed.headword = clean_hw
            hw = clean_hw.lower()
            historical_headwords.add(hw)
            ods_infs = db.lookup_ods_headword_inflections(canonical_seed.headword) if db else []
            all_inflections = set(canonical_seed.inflections) | set(ods_infs) | set(canonical_seed.historical_spellings) | extra_syns
            canonical_seed.inflections = all_inflections
            resolved_canonical[hw] = canonical_seed

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
            raw_hw = c_item.get("headword", "").strip()
            raw_word = c_item.get("word", "").strip().lower()
            if not raw_hw:
                raw_hw = raw_word
            clean_hw, extra_syns = sanitize_headword(raw_hw)
            clean_w, _ = sanitize_headword(raw_word)
            if not clean_hw and not clean_w:
                continue
            primary_hw = (clean_hw or clean_w or raw_word).lower()

            all_forms = {raw_word, raw_hw.lower(), primary_hw} | extra_syns
            for s in c_item.get("synonyms", []):
                if s:
                    s_clean, s_extra = sanitize_headword(s)
                    if s_clean:
                        all_forms.add(s_clean.lower())
                    all_forms.update(s_extra)
            all_forms.discard("")

            if c_item.get("source") == "seed" or primary_hw in NORDIC_1800S_ENTRIES:
                if primary_hw in resolved_entries:
                    existing_entry = resolved_entries[primary_hw]
                    existing_entry.synonyms = generate_orthographic_synonyms(
                        primary_hw, list(set(existing_entry.synonyms) | all_forms)
                    )
                continue

            historical_headwords.add(primary_hw)
            if primary_hw in resolved_entries:
                existing_entry = resolved_entries[primary_hw]
                existing_entry.synonyms = generate_orthographic_synonyms(
                    primary_hw, list(set(existing_entry.synonyms) | all_forms)
                )
            else:
                syns = generate_orthographic_synonyms(primary_hw, list(all_forms))
                resolved_entries[primary_hw] = StarDictEntry(
                    headword=primary_hw,
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

            # Step 1: Check curated Nordic 1800s, Molbech 1859, and compound synthesis directly
            still_unresolved: list[str] = []
            for t in unresolved_tokens:
                hist_match = (
                    get_nordic_1800s_entry(t)
                    or get_molbech_1859_entry(t)
                    or synthesize_compound_entry(t, db)
                )
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
            cor_pending_metadata: dict[str, dict] = {}
            token_to_cor_lemma: dict[str, str] = {}

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

                # 1. Match against existing entry by lemma_id (e.g. COR.31633 -> grunde, or moder -> mor)
                attached = False
                for m in matches:
                    lid = m.get("lemma_id")
                    if lid and lid in cor_id_to_headword:
                        target_hw = cor_id_to_headword[lid].lower()
                        if target_hw in resolved_entries:
                            resolved_entries[target_hw].synonyms.append(t)
                            known_surface_strings.add(t)
                            cor_attached_count += 1
                            attached = True
                            break
                if attached:
                    continue

                # 2. Match against existing entry by lemma and POS compatibility
                for m in matches:
                    lem = m["lemma"].lower()
                    m_pos = normalize_pos(m.get("pos", ""))
                    if lem in resolved_entries:
                        entry = resolved_entries[lem]
                        # Invariant: A noun ('sb.') can NEVER take verbal inflections (-ede, -te, -ende, -des)
                        if t.endswith(("ede", "te", "ende", "des")) and not lem.endswith(("ede", "te")):
                            if m_pos.startswith("vb") or not m_pos.startswith("sb"):
                                continue
                        entry.synonyms.append(t)
                        known_surface_strings.add(t)
                        cor_attached_count += 1
                        attached = True
                        break
                if attached:
                    continue

                # 3. Known COR word with 0 definitions in COR-SEM:
                # Rule: A word without definitions must NEVER be treated as finished!
                # COR delivers lemma, POS, and inflections to .syn, but the word must be
                # forwarded to ODS lookup / nordic_1800s.py / scraper.
                best_match = matches[0]
                for m in matches:
                    if m.get("pos") in GRAMMATICAL_POS:
                        best_match = m
                        break

                lem = best_match["lemma"].lower()
                pos_raw = best_match.get("pos", "")
                pos_norm = normalize_pos(pos_raw)
                cor_infs = db.lookup_cor_inflections(lem, pos_norm) if db else []

                if lem not in cor_pending_metadata:
                    cor_pending_metadata[lem] = {
                        "pos": pos_norm,
                        "inflections": set(cor_infs) | {t, lem},
                    }
                else:
                    cor_pending_metadata[lem]["inflections"].add(t)

                token_to_cor_lemma[t] = lem
                unresolved_after_cor.append(t)

            # Step 3: Batch-lookup against ODS forms (1,260,000+ historical inflections in SQLite)
            query_tokens = list(set(unresolved_after_cor) | {token_to_cor_lemma[t] for t in unresolved_after_cor if t in token_to_cor_lemma})
            ods_form_map = db.lookup_ods_forms_batch(query_tokens) if db else {}
            new_ods_words: dict[str, dict] = {}
            ods_attached_count = 0
            non_danish_ignored = 0

            for t in unresolved_after_cor:
                cor_lem = token_to_cor_lemma.get(t)
                match_hw_pos = ods_form_map.get(t) or (ods_form_map.get(cor_lem) if cor_lem else None)
                if match_hw_pos:
                    hw, pos = match_hw_pos
                    hw = hw.lower()
                    if hw in resolved_entries:
                        resolved_entries[hw].synonyms.append(t)
                        if cor_lem and cor_lem in cor_pending_metadata:
                            resolved_entries[hw].synonyms.extend(cor_pending_metadata[cor_lem]["inflections"])
                        known_surface_strings.add(t)
                        ods_attached_count += 1
                    else:
                        cnt = token_counts.get(t, 1)
                        is_cust = t in custom_tokens_set or hw in custom_tokens_set or (cor_lem in custom_tokens_set if cor_lem else False)
                        infs = {t, hw}
                        if cor_lem and cor_lem in cor_pending_metadata:
                            infs.update(cor_pending_metadata[cor_lem]["inflections"])
                            if not pos and cor_pending_metadata[cor_lem]["pos"]:
                                pos = cor_pending_metadata[cor_lem]["pos"]
                        if hw not in new_ods_words:
                            new_ods_words[hw] = {
                                "pos": pos,
                                "inflections": infs,
                                "frequency": cnt,
                                "is_custom": is_cust,
                            }
                        else:
                            new_ods_words[hw]["inflections"].update(infs)
                            new_ods_words[hw]["frequency"] += cnt
                            if is_cust:
                                new_ods_words[hw]["is_custom"] = True
                elif cor_lem:
                    # Not found in ODS fullforms, but we have the COR lemma:
                    # Pass the COR lemma on to ODS scraping / Nordic 1800s / cache lookup!
                    cnt = token_counts.get(t, 1)
                    is_cust = t in custom_tokens_set or cor_lem in custom_tokens_set
                    cor_meta = cor_pending_metadata.get(cor_lem, {})
                    infs = set(cor_meta.get("inflections", {t, cor_lem})) | {t, cor_lem}
                    if cor_lem in resolved_entries:
                        resolved_entries[cor_lem].synonyms.extend(infs)
                        known_surface_strings.add(t)
                        cor_attached_count += 1
                    else:
                        if cor_lem not in new_ods_words:
                            new_ods_words[cor_lem] = {
                                "pos": cor_meta.get("pos", ""),
                                "inflections": infs,
                                "frequency": cnt,
                                "is_custom": is_cust,
                            }
                        else:
                            new_ods_words[cor_lem]["inflections"].update(infs)
                            new_ods_words[cor_lem]["frequency"] += cnt
                            if is_cust:
                                new_ods_words[cor_lem]["is_custom"] = True
                elif t in custom_tokens_set or synthesize_compound_entry(t, db):
                    cnt = token_counts.get(t, 1)
                    synth = synthesize_compound_entry(t, db)
                    pos = synth.pos if synth else ""
                    if t not in new_ods_words:
                        new_ods_words[t] = {
                            "pos": pos,
                            "inflections": {t},
                            "frequency": cnt,
                            "is_custom": True,
                        }
                    else:
                        new_ods_words[t]["inflections"].add(t)
                        if pos and not new_ods_words[t]["pos"]:
                            new_ods_words[t]["pos"] = pos
                else:
                    non_danish_ignored += 1

            emit(
                f"SQLite-matching fuldført: {cor_attached_count} knyttet til COR, {ods_attached_count} knyttet til ODS, "
                f"{len(new_ods_words)} nye historiske opslag fundet ({non_danish_ignored} egennavne/uindekserede sprunget over).",
                60,
            )

            # Step 4: Create canonical historical entries for new ODS headwords
            if db:
                db.clean_corrupted_cached_definitions()

            cached_map: dict[str, dict] = {}
            if db:
                for c in db.get_all_cached_definitions():
                    if "Historisk opslag for »" in c.get("html_content", ""):
                        continue
                    w = c.get("word", "").lower().strip()
                    hw = c.get("headword", "").lower().strip()
                    clean_hw = re.sub(r"^(?:[IVXLCDM]+|[0-9]+)\.\s*", "", hw, flags=re.I).replace("-", "").strip()
                    if w:
                        cached_map[w] = c
                    if hw:
                        cached_map[hw] = c
                    if clean_hw:
                        cached_map[clean_hw] = c
                    for s in c.get("synonyms", []):
                        if s:
                            cached_map[s.lower().strip()] = c

            not_found_words = db.get_not_found_words() if db else set()

            from ordpresse.scrapers import HistoricalScraper
            scraper = HistoricalScraper(db) if db else None

            total_ods = len(new_ods_words)
            uncached_words = [
                hw for hw in new_ods_words
                if hw not in cached_map and hw not in not_found_words
            ]
            cached_words = [hw for hw in new_ods_words if hw in cached_map]

            # 1. Instantly resolve all already-cached words
            for hw in cached_words:
                c_item = cached_map[hw]
                raw_hw = c_item.get("headword", "").strip()
                raw_word = c_item.get("word", "").strip().lower()
                clean_hw, extra_syns = sanitize_headword(raw_hw or raw_word)
                if not clean_hw:
                    clean_hw = hw
                actual_hw = clean_hw.lower()

                all_forms = set(new_ods_words[hw]["inflections"]) | set(c_item.get("synonyms", [])) | {hw, actual_hw, raw_word} | extra_syns
                all_forms.discard("")
                syns = generate_orthographic_synonyms(actual_hw, list(all_forms))
                resolved_entries[actual_hw] = StarDictEntry(
                    headword=actual_hw,
                    html_content=c_item["html_content"],
                    synonyms=syns,
                )

            # 2. Resolve via offline curated lexicons and morphological synthesis (Phase 1 & 3)
            offline_resolved: list[str] = []
            for hw in uncached_words:
                local_entry = (
                    get_nordic_1800s_entry(hw)
                    or get_molbech_1859_entry(hw)
                    or synthesize_compound_entry(hw, db)
                )
                if local_entry:
                    actual_hw = local_entry.headword.lower()
                    all_forms = set(new_ods_words[hw]["inflections"]) | set(local_entry.inflections) | {hw, actual_hw}
                    syns = generate_orthographic_synonyms(actual_hw, list(all_forms))
                    resolved_entries[actual_hw] = StarDictEntry(
                        headword=actual_hw,
                        html_content=render_canonical_html(local_entry),
                        synonyms=syns,
                    )
                    offline_resolved.append(hw)

            if offline_resolved:
                uncached_words = [hw for hw in uncached_words if hw not in offline_resolved]

            # 3. Build 3-tier queue: Priority 1 (completely missing/empty skeletons) before Priority 2 (cliffhanger truncated)
            priority_1_items: list[dict] = []
            priority_2_items: list[dict] = []

            # Priority 1: Uncached historical headwords from new_ods_words
            for hw in uncached_words:
                priority_1_items.append({
                    "word": hw,
                    "target": "ods",
                    "priority": 1,
                    "frequency": new_ods_words[hw].get("frequency", 1),
                    "is_custom": new_ods_words[hw].get("is_custom", False),
                })

            # Check candidate headwords from EPUB/custom words against resolved_canonical for Priority 1 / Priority 2
            candidate_headwords: set[str] = set()
            for t in all_tokens:
                if t in resolved_canonical:
                    candidate_headwords.add(t)
                elif t in token_to_cor_lemma and token_to_cor_lemma[t] in resolved_canonical:
                    candidate_headwords.add(token_to_cor_lemma[t])
                elif t in resolved_entries and resolved_entries[t].headword in resolved_canonical:
                    candidate_headwords.add(resolved_entries[t].headword)
            for cw in custom_tokens_set:
                if cw in resolved_canonical:
                    candidate_headwords.add(cw)

            for hw in candidate_headwords:
                prio = classify_external_lookup_priority(resolved_canonical[hw])
                if prio == 1:
                    if hw not in cached_map and hw not in not_found_words:
                        priority_1_items.append({
                            "word": hw,
                            "target": "ddo",
                            "priority": 1,
                            "frequency": token_counts.get(hw, 1),
                            "is_custom": hw in custom_tokens_set,
                        })
                elif prio == 2:
                    # Priority 2: Cliffhanger truncation — check cache first to resolve offline
                    cached_item = db.get_cached_definition(hw) if db else None
                    if cached_item and "Historisk opslag for »" not in cached_item.get("html_content", ""):
                        c_html = cached_item.get("html_content", "")
                        if len(c_html) > 50 and not c_html.strip().endswith(("...", "…")):
                            actual_hw = cached_item.get("headword", hw).lower()
                            cur_entry = resolved_entries.get(actual_hw)
                            syns = cur_entry.synonyms if cur_entry else [actual_hw]
                            resolved_entries[actual_hw] = StarDictEntry(
                                headword=actual_hw,
                                html_content=c_html,
                                synonyms=syns,
                            )
                            continue
                    priority_2_items.append({
                        "word": hw,
                        "target": "ddo",
                        "priority": 2,
                        "frequency": token_counts.get(hw, 1),
                        "is_custom": hw in custom_tokens_set,
                    })
                # Priority 3 words are excluded from the auto EPUB queue per specification

            priority_1_items.sort(
                key=lambda it: (1 if it.get("is_custom") else 0, it.get("frequency", 1), it["word"]),
                reverse=True,
            )
            priority_2_items.sort(
                key=lambda it: (1 if it.get("is_custom") else 0, it.get("frequency", 1), it["word"]),
                reverse=True,
            )

            seen_queue_words = set()
            dedup_combined_items: list[dict] = []
            for it in (priority_1_items + priority_2_items):
                w_low = it["word"].lower()
                if w_low not in seen_queue_words:
                    seen_queue_words.add(w_low)
                    dedup_combined_items.append(it)

            items_to_scrape: list[dict] = []
            if allow_network and dedup_combined_items:
                items_to_scrape = dedup_combined_items[:MAX_ONLINE_SCRAPE_BATCH]
            elif not dedup_combined_items and cached_words:
                emit(
                    f"Alle historiske ord fra bøgerne er allerede i cachen ({len(cached_words)} genbrugt).",
                    75,
                )

            if items_to_scrape:
                total_to_scrape = len(items_to_scrape)
                words_to_scrape = [it["word"] for it in items_to_scrape]
                coordinator.register_build(actual_build_id, words_to_scrape, initial_mode=scrape_mode, items=items_to_scrape)

                p1_items_in_batch = [it for it in items_to_scrape if it.get("priority", 1) == 1]
                p2_items_in_batch = [it for it in items_to_scrape if it.get("priority", 1) == 2]
                p1_total = len(p1_items_in_batch)
                p2_total = len(p2_items_in_batch)

                if scrape_mode in ("bookmarklet", "wait_choice"):
                    emit(
                        f"Klar til berigelse af op til {total_to_scrape} prioriterede ord "
                        f"({p1_total} Prio 1, {p2_total} Prio 2). Vælg mængde og kør Lyn-Henter...",
                        60,
                    )
                else:
                    emit(
                        f"Henter {total_to_scrape} prioriterede ord over nettet "
                        f"({p1_total} Prio 1, {p2_total} Prio 2, {len(cached_words)} genbrugt fra cache)...",
                        60,
                    )

                completed = 0
                server_scraped_count = 0
                p1_idx = 0
                p2_idx = 0
                last_emit_time = time.time()
                last_logged_resolved = -1
                last_wait_emit_time = 0.0
                ddo_scraper = DDOScraper()

                try:
                    for idx, it in enumerate(items_to_scrape, start=1):
                        hw = it["word"]
                        target = it.get("target", "ods")
                        prio = it.get("priority", 1)

                        if prio == 1:
                            p1_idx += 1
                            step_desc = f"Henter manglende ord {p1_idx}/{p1_total} ({hw}) [Prio 1 — mangler helt]..."
                        else:
                            p2_idx += 1
                            step_desc = f"Fuldender afkortet definition {p2_idx}/{p2_total} ({hw}) [Prio 2 — kan springes over]..."

                        if is_cancelled and is_cancelled():
                            raise RuntimeError("Byggeprocessen blev afbrudt af brugeren.")

                        if coordinator.should_skip_remaining(actual_build_id):
                            resolved_cnt = coordinator.get_resolved_count(actual_build_id)
                            emit(
                                f"Lyn-Henter fuldført ({resolved_cnt} ord modtaget). Færdiggør og kompilerer ordbog...",
                                79,
                            )
                            break

                        # Check if peer bookmarklet already submitted it
                        cached_entry = db.get_cached_definition(hw) if db else None
                        if cached_entry and "Historisk opslag for »" not in cached_entry.get("html_content", ""):
                            c_html = cached_entry.get("html_content", "")
                            if (prio == 1 and len(c_html) > 20) or (prio == 2 and len(c_html) > 50 and not c_html.strip().endswith(("...", "…"))):
                                coordinator.mark_word_resolved(hw, actual_build_id)
                                clean_hw, extra_syns = sanitize_headword(cached_entry.get("headword", ""))
                                actual_hw = (clean_hw or hw).lower()
                                infs = list(new_ods_words.get(hw, {}).get("inflections", []))
                                if not infs and actual_hw in resolved_entries:
                                    infs = list(resolved_entries[actual_hw].synonyms)
                                syns = generate_orthographic_synonyms(
                                    actual_hw,
                                    infs + cached_entry.get("synonyms", []) + list(extra_syns) + [hw],
                                )
                                resolved_entries[actual_hw] = StarDictEntry(
                                    headword=actual_hw,
                                    html_content=c_html,
                                    synonyms=syns,
                                )
                                completed += 1
                                continue

                        # Rate-limit acquisition with peer-resolve wakeup
                        current_pct = 60 + int(20 * (idx / total_to_scrape))
                        def on_wait(wait_s: float, pending: int):
                            nonlocal last_logged_resolved, last_wait_emit_time
                            cur_mode = coordinator.get_build_mode(actual_build_id)
                            resolved_cnt = coordinator.get_resolved_count(actual_build_id)
                            now = time.time()

                            if cur_mode in ("bookmarklet", "wait_choice"):
                                if resolved_cnt > 0:
                                    if resolved_cnt != last_logged_resolved and (resolved_cnt % 5 == 0 or (now - last_wait_emit_time) >= 2.0):
                                        emit(
                                            f"⚡ Lyn-Henter aktiv: {resolved_cnt} ord modtaget og gemt i ordbogen...",
                                            current_pct,
                                        )
                                        last_logged_resolved = resolved_cnt
                                        last_wait_emit_time = now
                                else:
                                    if (now - last_wait_emit_time) >= 5.0 or last_wait_emit_time == 0.0:
                                        emit(
                                            "Venter på Lyn-Henter — kør Bookmarklet på ordnet.dk eller klik 'Start server-kø'!",
                                            current_pct,
                                        )
                                        last_wait_emit_time = now
                            else:
                                if (now - last_wait_emit_time) >= 3.0:
                                    emit(
                                        f"Venter på server-kvote ({wait_s:.0f}s, {completed} ord hentet) — brug Bookmarklet for lyn-hentning!",
                                        current_pct,
                                    )
                                    last_wait_emit_time = now

                        should_scrape = _acquire_token(
                            coordinator, actual_build_id, hw, on_wait_cb=on_wait, loop=loop
                        )

                        if not should_scrape:
                            # Resolved by peer or skipped
                            cached_entry = db.get_cached_definition(hw) if db else None
                            if cached_entry:
                                clean_hw, extra_syns = sanitize_headword(cached_entry.get("headword", ""))
                                actual_hw = (clean_hw or hw).lower()
                                infs = list(new_ods_words.get(hw, {}).get("inflections", []))
                                if not infs and actual_hw in resolved_entries:
                                    infs = list(resolved_entries[actual_hw].synonyms)
                                syns = generate_orthographic_synonyms(
                                    actual_hw,
                                    infs + cached_entry.get("synonyms", []) + list(extra_syns) + [hw],
                                )
                                resolved_entries[actual_hw] = StarDictEntry(
                                    headword=actual_hw,
                                    html_content=cached_entry["html_content"],
                                    synonyms=syns,
                                )
                            if coordinator.should_skip_remaining(actual_build_id):
                                resolved_cnt = coordinator.get_resolved_count(actual_build_id)
                                emit(
                                    f"Lyn-Henter fuldført ({resolved_cnt} ord modtaget). Færdiggør og kompilerer ordbog...",
                                    79,
                                )
                                break
                            completed += 1
                            continue

                        server_scraped_count += 1
                        if server_scraped_count > MAX_SERVER_SCRAPE_BATCH:
                            emit(
                                f"Server-kvoten på {MAX_SERVER_SCRAPE_BATCH} ord er nået for dette job. "
                                f"Afslutter server-kø og færdiggør ordbog...",
                                79,
                            )
                            break

                        def_data = None
                        try:
                            if target == "ddo":
                                def_data = ddo_scraper.scrape_ddo(hw)
                            else:
                                def_data = scraper.fetch_word_definition(hw, allow_remote=True) if scraper else None
                        except (ODSScraperRateLimitError, DDOScraperRateLimitError) as rle:
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
                            clean_hw, extra_syns = sanitize_headword(def_data.get("headword", ""))
                            actual_hw = (clean_hw or hw).lower()
                            def_data["headword"] = clean_hw or hw
                            if extra_syns:
                                def_data.setdefault("synonyms", []).extend(extra_syns)
                            if target == "ddo":
                                if db:
                                    db.set_cached(
                                        word=hw,
                                        source="ddo",
                                        headword=def_data["headword"],
                                        html_content=def_data["html_content"],
                                        synonyms=def_data.get("synonyms", []),
                                    )
                                    if def_data.get("definitions"):
                                        db.update_cor_definition(hw, def_data["definitions"][0])
                                existing_syns = resolved_entries[actual_hw].synonyms if actual_hw in resolved_entries else []
                                syns = generate_orthographic_synonyms(
                                    actual_hw,
                                    list(existing_syns) + def_data.get("synonyms", []) + [hw],
                                )
                                resolved_entries[actual_hw] = StarDictEntry(
                                    headword=actual_hw,
                                    html_content=def_data["html_content"],
                                    synonyms=syns,
                                )
                            else:
                                infs = list(new_ods_words.get(hw, {}).get("inflections", []))
                                syns = generate_orthographic_synonyms(
                                    actual_hw,
                                    infs + def_data.get("synonyms", []) + [hw],
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
                            emit(step_desc, pct)
                            last_emit_time = now
                finally:
                    coordinator.unregister_build(actual_build_id)

        # Hard Compiler Guard (Phase 1.2): Filter out any entry without genuine definition
        valid_entries = []
        for e in resolved_entries.values():
            if getattr(e, "canonical_entry", None) is not None:
                if not is_valid_canonical_entry(e.canonical_entry):
                    continue
            html = (e.html_content or "").strip()
            if not html:
                continue
            if not any(marker in html for marker in ["<ol>", "<li>", "<p>", "[Historisk kontekst]"]):
                continue
            valid_entries.append(e)

        emit(f"Opslag fuldført. Kompilerer {len(valid_entries)} ordbogsartikler til StarDict v2.4.2...", 80)

        # 3. Compile StarDict binaries
        writer = StarDictWriter(
            bookname=self.bookname,
            author=self.author,
            description=self.description,
        )
        base_name = "ordpresse"
        compiled_files = writer.compile(
            entries=valid_entries,
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
