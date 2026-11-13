"""Multi-tier lookup service for KOReader online integration (V10.2 Zero-Trust).

Guarantees:
- Always performs a live lookup against ordnet.dk (DDO + ODS) first on every online query.
- Saves authentic, untruncated live results into both the definitions cache (cache.sqlite3)
  and online_lookup_cache.
- Merges same-lemma DDO modern senses + ODS historical senses into a single CanonicalEntry,
  and preserves distinct homographs / inflected lemma matches as multiple entries.
- Falls back to local datasets (Nordic 1800s, definitions cache, StarDict, COR-SEM, compound synthesis)
  only when ordnet.dk is unreachable or returns no match.
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote

from bs4 import BeautifulSoup
from fastapi import HTTPException

from ordpresse.config import OUTPUT_DIR
from ordpresse.db import Database
from ordpresse.formatter import (
    normalize_pos,
    render_canonical_html,
    select_display_inflections,
)
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import (
    generate_orthographic_synonyms,
    historical_aa_to_modern,
    synthesize_compound_entry,
)
from ordpresse.sources.ddo_scraper import (
    DDOScraper,
    DDOScraperRateLimitError,
)
from ordpresse.sources.nordic_1800s import (
    enrich_canonical_entry_with_historical,
    get_nordic_1800s_entry,
)
from ordpresse.sources.ods_scraper import (
    ODSScraper,
    ODSScraperRateLimitError,
    clean_ods_definition_text,
)
from ordpresse.synthesizer import should_render_summary_hook

logger = logging.getLogger(__name__)

# Pacing lock for external ordnet.dk lookups (minimum 1.0s between consecutive successful live queries)
_EXTERNAL_LOCK = asyncio.Lock()
_LAST_EXTERNAL_CALL_TIME = 0.0


def sanitize_query(q: Optional[str]) -> str:
    """Sanitize and validate search query according to specification."""
    cleaned = (q or "").strip().strip('"\'«»„”()[]{}_.,;:!?-–—/\\')
    if not cleaned or len(cleaned) > 64 or "\n" in cleaned or "\r" in cleaned:
        raise HTTPException(
            status_code=422,
            detail="Søgestrengen skal være mellem 1 og 64 tegn.",
        )
    return cleaned


def _is_truncated_payload(payload: Optional[dict]) -> bool:
    """Return True if cached payload contains cliffhanger or ellipsis truncation."""
    if not payload:
        return True
    payload_str = json.dumps(payload, ensure_ascii=False)
    if "…" in payload_str or "\\u2026" in payload_str:
        return True
    if "Historisk opslag for »" in payload_str or "ingen betydningsangivelse" in payload_str:
        return True
    return False


def format_display_text(
    query: str,
    resolved_lemma: Optional[str],
    entries: list[dict],
    sources: Optional[list[str]] = None,
) -> str:
    """Format structured dictionary entries into plain text for KOReader TextViewer."""
    lines = []
    if resolved_lemma and query.strip().lower() != resolved_lemma.strip().lower():
        lines.append(f"Opslag: {query} → {resolved_lemma}")
        lines.append("")

    for i, entry in enumerate(entries):
        hw = entry.get("headword", query)
        pos = entry.get("part_of_speech", "")
        inf = entry.get("inflections", "")
        phonetic = entry.get("phonetic", "")
        defs = entry.get("definitions", [])
        etym = entry.get("etymology", "")

        prefix = f"{entry.get('homonym_index', i + 1)}. " if len(entries) > 1 else ""
        header = f"{prefix}{hw}"
        if pos:
            header += f" ({pos})"
        if phonetic:
            header += f" [{phonetic}]"
        lines.append(header)

        if inf:
            lines.append(f"Bøjning: {inf}")

        for d in defs:
            d_clean = re.sub(r"^\d+\.\s*", "", d)
            lines.append(f"  • {d_clean}")

        if etym:
            lines.append(f"  [Historisk kontekst]: {etym}")

        if i < len(entries) - 1:
            lines.append("")
            lines.append("────────────────────────────")
            lines.append("")

    if sources:
        lines.append(f"  Kilde: {' · '.join(sources)}")
    elif entries:
        lines.append("  Kilde: Den Danske Ordpresse")

    return "\n".join(lines).strip()


def _canonical_to_entry_dict(
    canonical: CanonicalEntry,
    homonym_index: int = 1,
    include_source_tags: bool = True,
) -> dict:
    """Format a CanonicalEntry into a single dictionary entry contract."""
    headword = canonical.headword
    pos = canonical.pos or ""

    # Clean display inflections (up to 6 representative forms, excluding headword and artificial hyphens)
    disp_infs = select_display_inflections(canonical.headword, canonical.inflections, limit=6)
    inf_parts = []
    if disp_infs:
        inf_parts.append(", ".join(disp_infs))
    if canonical.historical_spellings:
        inf_parts.append("ældre: " + ", ".join(canonical.historical_spellings[:2]))
    inflections = "; ".join(inf_parts)

    definitions: List[str] = []
    idx = 1

    # Layer 1: TL;DR Hook (only if non-redundant)
    if should_render_summary_hook(canonical):
        definitions.append(f"{idx}. {canonical.summary_hook}")
        idx += 1

    has_ddo = any((s.source_tag or "").upper() == "DDO" for s in (canonical.modern_senses or []))
    has_ods = bool(canonical.historical_senses)

    # Modern senses (DDO / COR-SEM)
    for s in (canonical.modern_senses or [])[:6]:
        src_tag = (s.source_tag or "DDO").upper()
        lbl_parts = []
        if include_source_tags and has_ods and src_tag == "DDO":
            lbl_parts.append("DDO")
        if s.label:
            lbl_parts.append(s.label)
        lbl_str = f"({ ' · '.join(lbl_parts) }) " if lbl_parts else ""

        q_str = ""
        if s.quote:
            clean_q = s.quote.split("|")[0].strip()
            if clean_q:
                q_auth = f" ({s.quote_author})" if s.quote_author else ""
                q_str = f" — »{clean_q}«{q_auth}"

        definitions.append(f"{idx}. {lbl_str}{s.definition}{q_str}".strip())
        idx += 1

    # Historical senses (ODS)
    for s in (canonical.historical_senses or [])[:6]:
        tag = "(ODS / 1700–1950)" if include_source_tags and has_ddo else ""
        lbl = f"({s.label})" if s.label else ""
        prefix_parts = [p for p in (tag, lbl) if p]
        prefix_str = f"{' '.join(prefix_parts)} " if prefix_parts else ""

        q_str = ""
        if s.quote:
            clean_q = s.quote.split("|")[0].strip()
            if clean_q:
                q_auth = f" ({s.quote_author})" if s.quote_author else ""
                q_str = f" — »{clean_q}«{q_auth}"

        definitions.append(f"{idx}. {prefix_str}{s.definition}{q_str}".strip())
        idx += 1

    if not definitions and canonical.summary_hook:
        definitions.append(f"1. {canonical.summary_hook}")

    return {
        "headword": headword,
        "homonym_index": homonym_index,
        "part_of_speech": pos,
        "inflections": inflections,
        "phonetic": getattr(canonical, "phonetic", "") or "",
        "definitions": definitions,
        "etymology": canonical.realia_note or "",
    }


def entries_to_response(
    query: str,
    canonicals: List[CanonicalEntry],
    sources: Optional[list[str]] = None,
) -> dict:
    """Build multi-entry JSON contract from a list of CanonicalEntry objects."""
    if not canonicals:
        return {
            "query": query,
            "resolved_lemma": None,
            "display_text": f"Ingen resultater fundet for '{query}'.",
            "entries": [],
            "cached_not_found": False,
        }

    # Order entries: primary headword match first
    q_norm = historical_aa_to_modern(query.lower().replace("-", ""))
    canonicals_sorted = sorted(
        canonicals,
        key=lambda c: (
            0 if historical_aa_to_modern(c.headword.lower().replace("-", "")) == q_norm else 1,
            c.headword.lower(),
        ),
    )

    include_src_tags = len(canonicals_sorted) > 0 and any(
        bool(c.historical_senses) and bool(c.modern_senses) for c in canonicals_sorted
    )
    entries = [
        _canonical_to_entry_dict(c, homonym_index=idx, include_source_tags=include_src_tags)
        for idx, c in enumerate(canonicals_sorted, start=1)
    ]

    all_sources = []
    if sources:
        all_sources = sources
    else:
        for c in canonicals_sorted:
            for s in (c.sources_used or []):
                if s not in all_sources:
                    all_sources.append(s)

    resolved_lemma = entries[0]["headword"] if entries else query
    display_text = format_display_text(query, resolved_lemma, entries, all_sources)

    return {
        "query": query,
        "resolved_lemma": resolved_lemma,
        "display_text": display_text,
        "entries": entries,
    }


def canonical_to_response(
    query: str,
    canonical: CanonicalEntry,
    sources: Optional[list[str]] = None,
) -> dict:
    """Convert a single CanonicalEntry to structured JSON contract (backwards-compatible)."""
    return entries_to_response(query, [canonical], sources=sources)


def parse_html_to_response(
    query: str,
    headword: str,
    html: str,
    source: str = "ODS",
) -> dict:
    """Parse HTML dictionary article into structured JSON contract."""
    soup = BeautifulSoup(html, "html.parser")

    # Extract part of speech
    pos = ""
    pos_tag = soup.find("i")
    if pos_tag:
        pos = normalize_pos(pos_tag.get_text(strip=True))

    # Extract inflections
    inflections = ""
    for text_node in soup.find_all(string=True):
        if "bøjning:" in text_node.lower():
            inflections = text_node.split(":", 1)[-1].strip()
            break

    # Extract definitions
    definitions = []
    lis = soup.find_all("li")
    if lis:
        for i, li in enumerate(lis, 1):
            txt = clean_ods_definition_text(li.get_text(" ", strip=True))
            if txt:
                definitions.append(f"{i}. {txt}")
    else:
        ps = soup.find_all("p")
        if ps:
            for i, p in enumerate(ps, 1):
                txt = clean_ods_definition_text(p.get_text(" ", strip=True))
                if txt and not txt.startswith("Kilde:") and not txt.startswith("Bøjning:"):
                    definitions.append(f"{i}. {txt}")

    if not definitions:
        raw_txt = soup.get_text(" ", strip=True)
        if raw_txt:
            definitions.append(f"1. {raw_txt}")

    # Extract etymology / realia if present
    etymology = ""
    for div in soup.find_all("div"):
        if "Historisk kontekst" in div.get_text() or "etymologi" in div.get_text().lower():
            txt = div.get_text(" ", strip=True)
            txt = re.sub(r"^\[?Historisk kontekst\]?:\s*", "", txt, flags=re.IGNORECASE)
            etymology = txt
            break

    entry_dict = {
        "headword": headword,
        "homonym_index": 1,
        "part_of_speech": pos,
        "inflections": inflections,
        "phonetic": "",
        "definitions": definitions,
        "etymology": etymology,
    }

    display_text = format_display_text(query, headword, [entry_dict], [source])

    return {
        "query": query,
        "resolved_lemma": headword,
        "display_text": display_text,
        "entries": [entry_dict],
    }


def _norm_lemma_key(hw: str) -> str:
    """Normalize headword string for same-lemma comparison (aa<->å, hyphens stripped)."""
    return historical_aa_to_modern((hw or "").strip().lower().replace("-", ""))


def _extract_canonicals_from_scraper_result(res: dict, fallback_hw: str) -> List[CanonicalEntry]:
    """Extract CanonicalEntry objects from a scraper result dict, supporting mocks without canonical_entry."""
    if not res:
        return []
    canonicals: List[CanonicalEntry] = []
    if res.get("canonical_entry") and isinstance(res["canonical_entry"], CanonicalEntry):
        canonicals.append(res["canonical_entry"])
    else:
        # Fallback for minimal mocks returning only html_content
        hw = res.get("headword") or fallback_hw
        parsed_resp = parse_html_to_response(hw, hw, res.get("html_content") or "", source=res.get("source", "ODS"))
        if parsed_resp.get("entries"):
            e0 = parsed_resp["entries"][0]
            can = CanonicalEntry(
                headword=hw,
                pos=e0.get("part_of_speech", ""),
                inflections=set(res.get("synonyms") or []),
                historical_senses=[
                    SenseItem(definition=re.sub(r"^\d+\.\s*", "", d), source_tag=res.get("source", "ODS"))
                    for d in e0.get("definitions", [])
                ],
                sources_used=[res.get("source", "ODS").upper()],
            )
            canonicals.append(can)

    # Attach any additional homograph entries returned by the scraper
    for add_can in res.get("additional_entries", []):
        if isinstance(add_can, CanonicalEntry):
            canonicals.append(add_can)

    return canonicals


def _merge_ddo_and_ods_canonicals(
    ddo_canonicals: List[CanonicalEntry],
    ods_canonicals: List[CanonicalEntry],
) -> List[CanonicalEntry]:
    """Merge same-lemma DDO and ODS entries into unified CanonicalEntry objects."""
    merged: List[CanonicalEntry] = []
    used_ods_indices: Set[int] = set()

    for d_can in ddo_canonicals:
        d_key = _norm_lemma_key(d_can.headword)
        # Look for matching ODS entry with same normalized lemma
        match_idx = None
        for idx, o_can in enumerate(ods_canonicals):
            if idx in used_ods_indices:
                continue
            if _norm_lemma_key(o_can.headword) == d_key:
                match_idx = idx
                break

        if match_idx is not None:
            used_ods_indices.add(match_idx)
            o_can = ods_canonicals[match_idx]
            combined = enrich_canonical_entry_with_historical(d_can, o_can)
            if "DDO" not in combined.sources_used:
                combined.sources_used.insert(0, "DDO")
            if "ODS" not in combined.sources_used:
                combined.sources_used.append("ODS")
            merged.append(combined)
        else:
            if "DDO" not in d_can.sources_used:
                d_can.sources_used.append("DDO")
            merged.append(d_can)

    # Append any remaining ODS entries that did not match a DDO lemma
    for idx, o_can in enumerate(ods_canonicals):
        if idx not in used_ods_indices:
            if "ODS" not in o_can.sources_used:
                o_can.sources_used.append("ODS")
            merged.append(o_can)

    return merged


def _scrape_ordnet_live_worker(
    candidates: List[str],
    lemma_hints: List[str],
) -> Tuple[Optional[dict], Optional[dict], bool, bool]:
    """
    Synchronous worker executing live DDO and ODS scrapers in a background thread.
    Returns (ddo_res, ods_res, net_error, rate_limited).
    """
    ddo_scraper = DDOScraper(request_delay=0.0)
    ods_scraper = ODSScraper(request_delay=0.0)

    ddo_res = None
    ods_res = None
    net_error = False
    rate_limited = False

    all_cands = []
    for c in candidates + lemma_hints:
        c_clean = c.strip().lower()
        if c_clean and c_clean not in all_cands:
            all_cands.append(c_clean)

    for cand in all_cands:
        # 1. Scrape DDO
        if ddo_res is None:
            try:
                res = ddo_scraper.scrape_ddo(cand, max_senses=8)
                if res and (res.get("canonical_entry") or res.get("html_content")):
                    ddo_res = res
            except DDOScraperRateLimitError:
                rate_limited = True
            except Exception as e:
                net_error = True
                logger.debug(f"DDO scrape error for '{cand}': {e}")

        # 2. Scrape ODS
        if ods_res is None:
            try:
                res = ods_scraper.scrape_ods(cand, max_senses=8)
                if res and (res.get("canonical_entry") or res.get("html_content")):
                    ods_res = res
            except ODSScraperRateLimitError:
                rate_limited = True
            except Exception as e:
                net_error = True
                logger.debug(f"ODS scrape error for '{cand}': {e}")

        if ddo_res is not None and ods_res is not None:
            break

    return ddo_res, ods_res, net_error, rate_limited


async def perform_koreader_lookup(query: str, db: Database) -> dict:
    """
    Multi-tier lookup pipeline:
    Tier 1 (LIVE FIRST): Query ordnet.dk (DDO + ODS) live.
                         On success, store untruncated result in definitions and online_lookup_cache.
    Tier 2 (LOCAL FALLBACK): Only if ordnet.dk has no match or is unreachable, check:
         - Curated Nordic 1800s vocabulary
         - Scraped definitions cache (definitions in cache.sqlite3)
         - Non-truncated online_lookup_cache
         - Molbech 1859 lexicon
         - StarDict binary
         - COR-SEM in datasets.sqlite3
         - Morphological compound synthesis
         - Negative cache (NOT_FOUND)
    """
    cleaned = sanitize_query(query)
    q_lower = cleaned.lower()
    q_aa = historical_aa_to_modern(q_lower) if "aa" in q_lower else q_lower

    lookup_candidates = [q_lower]
    if q_aa != q_lower:
        lookup_candidates.append(q_aa)

    # Gather local morphological lemma hints in case query is an inflected genitive or suffix form
    lemma_hints: List[str] = []
    if db.datasets_path.exists():
        forms_to_check = [q_lower, q_aa]
        for w in [q_lower, q_aa]:
            if w.endswith("'s") or w.endswith("’s"):
                forms_to_check.append(w[:-2])
            elif w.endswith("s") and len(w) > 4:
                forms_to_check.append(w[:-1])

        cor_map = db.lookup_cor_forms_batch(forms_to_check)
        for _, entries in cor_map.items():
            for e in entries:
                lem = e.get("lemma", "").strip().lower()
                if lem and lem not in lookup_candidates and lem not in lemma_hints:
                    lemma_hints.append(lem)
                    if len(lemma_hints) >= 2:
                        break

    # -------------------------------------------------------------
    # Tier 1: ALWAYS query ordnet.dk live first (DDO + ODS)
    # -------------------------------------------------------------
    global _LAST_EXTERNAL_CALL_TIME
    ddo_res = None
    ods_res = None
    net_error = False
    rate_limited = False

    async with _EXTERNAL_LOCK:
        now = time.time()
        if _LAST_EXTERNAL_CALL_TIME > 0:
            elapsed = now - _LAST_EXTERNAL_CALL_TIME
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

        ddo_res, ods_res, net_error, rate_limited = await asyncio.to_thread(
            _scrape_ordnet_live_worker,
            lookup_candidates,
            lemma_hints,
        )

        if ddo_res is not None or ods_res is not None:
            _LAST_EXTERNAL_CALL_TIME = time.time()

    # If at least one live source returned an article, merge and save to local caches
    if ddo_res is not None or ods_res is not None:
        ddo_canonicals = _extract_canonicals_from_scraper_result(ddo_res, q_lower) if ddo_res else []
        ods_canonicals = _extract_canonicals_from_scraper_result(ods_res, q_lower) if ods_res else []

        merged_canonicals = _merge_ddo_and_ods_canonicals(ddo_canonicals, ods_canonicals)

        if merged_canonicals:
            # Build unified response
            live_resp = entries_to_response(cleaned, merged_canonicals)

            # 1. Save each resolved CanonicalEntry into the definitions table
            saved_headwords = set()
            for can in merged_canonicals:
                hw_key = can.headword.lower()
                saved_headwords.add(hw_key)
                # Collect union of all synonyms including queries and inflections
                inf_set = set(can.inflections) | {hw_key, q_lower, q_aa}
                if can.historical_spellings:
                    inf_set.update(s.lower() for s in can.historical_spellings)
                all_syns = generate_orthographic_synonyms(can.headword, sorted(list(inf_set)))

                html_art = render_canonical_html(can)
                src_label = "+".join(s.lower() for s in can.sources_used) or "online"
                db.set_cached(
                    word=hw_key,
                    source=src_label,
                    headword=can.headword,
                    html_content=html_art,
                    synonyms=all_syns,
                )

            # Clean stale unnormalized definition row for q_lower if it had an incorrect headword
            if q_lower not in saved_headwords:
                existing_row = db.get_cached(q_lower)
                if existing_row and existing_row.get("headword", "").lower() not in saved_headwords:
                    db.delete_cached(q_lower)

            # 2. Save structured JSON into online_lookup_cache under queries and headwords
            payload_json = json.dumps(live_resp, ensure_ascii=False)
            keys_to_cache = {q_lower, q_aa, live_resp["resolved_lemma"].lower()}
            for can in merged_canonicals:
                if can.headword:
                    keys_to_cache.add(can.headword.lower())

            for k in keys_to_cache:
                db.set_online_cache(k, "FOUND", payload_json)

            return live_resp

    # -------------------------------------------------------------
    # Tier 2: Local Fallbacks (Only reached when live scraping missed)
    # -------------------------------------------------------------

    # 2.1 Curated Nordic 1800s Vocabulary
    for cand in lookup_candidates:
        nordic = get_nordic_1800s_entry(cand)
        if nordic:
            resp = canonical_to_response(cleaned, nordic)
            db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
            return resp

    # 2.2 Previously scraped definitions in cache.sqlite3
    for cand in lookup_candidates:
        cached_row = db.get_cached_definition(cand)
        if cached_row:
            hw = cached_row.get("headword") or cand
            html = cached_row.get("html_content") or ""
            src = cached_row.get("source") or "ODS"
            resp = parse_html_to_response(cleaned, hw, html, source=src)
            if not _is_truncated_payload(resp):
                db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
                if cand != q_lower:
                    db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
                return resp

    # 2.3 Non-truncated entry in online_lookup_cache
    for cand in lookup_candidates:
        cached = db.get_online_cache(cand)
        if cached and cached["status"] == "FOUND" and cached.get("payload_json"):
            try:
                payload = json.loads(cached["payload_json"])
                if not _is_truncated_payload(payload):
                    payload["query"] = cleaned
                    return payload
            except Exception as e:
                logger.debug(f"Failed to decode cached payload for {cand}: {e}")

    # 2.4 Molbech 1859 Lexicon
    from ordpresse.sources.molbech_1859 import get_molbech_1859_entry

    for cand in lookup_candidates:
        molbech = get_molbech_1859_entry(cand)
        if molbech:
            resp = canonical_to_response(cleaned, molbech, sources=["Molbech (1859)"])
            db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
            return resp

    # 2.5 Prebuilt StarDict binary in OUTPUT_DIR
    ifo_candidates = sorted(
        list(OUTPUT_DIR.glob("build_*/ordpresse.ifo"))
        + list(OUTPUT_DIR.glob("ordpresse.ifo"))
        + list(OUTPUT_DIR.parent.glob("ordpresse-moberg-eksempel/ordpresse.ifo")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for ifo in ifo_candidates:
        try:
            from ordpresse.stardict_reader import StarDictReader

            reader = StarDictReader(ifo)
            for cand in lookup_candidates:
                entry = reader.lookup_entry(cand)
                if entry:
                    hw, html = entry
                    resp = parse_html_to_response(cleaned, hw, html, source="StarDict")
                    if not _is_truncated_payload(resp):
                        db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
                        if cand != q_lower:
                            db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
                        return resp
        except Exception as e:
            logger.debug(f"Reader failed on {ifo}: {e}")

    # 2.6 Modern COR-SEM in datasets.sqlite3
    if db.datasets_path.exists():
        for cand in lookup_candidates:
            cor_defs = db.lookup_cor_lemma_definitions(cand)
            if cor_defs:
                lemma = cor_defs[0]["lemma"].lower()
                poses = []
                for d in cor_defs:
                    p = normalize_pos(d.get("pos", ""))
                    if p and p not in poses:
                        poses.append(p)
                pos_display = " / ".join(poses) if poses else normalize_pos(cor_defs[0].get("pos", ""))

                modern_senses = [
                    SenseItem(
                        definition=(
                            f"({normalize_pos(d.get('pos', ''))}) {d['definition']}"
                            if len(poses) > 1 and d.get("pos")
                            else d["definition"]
                        ),
                        quote=d.get("examples").split("|")[0].strip() if d.get("examples") else None,
                        source_tag="COR-SEM",
                    )
                    for d in cor_defs[:4]
                ]
                cor_infs = db.lookup_cor_inflections(lemma, poses[0] if len(poses) == 1 else None)
                canonical = CanonicalEntry(
                    headword=lemma,
                    pos=pos_display,
                    inflections=set(cor_infs + [cleaned]),
                    summary_hook="",
                    modern_senses=modern_senses,
                    sources_used=["COR-SEM"],
                )
                resp = canonical_to_response(cleaned, canonical, sources=["COR-SEM"])
                if not _is_truncated_payload(resp):
                    db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
                    if cand != q_lower:
                        db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
                    return resp

    # 2.7 Morphological compound synthesis
    for cand in lookup_candidates:
        synth = synthesize_compound_entry(cand, db)
        if synth:
            resp = canonical_to_response(cleaned, synth, sources=["Morfologi", "COR"])
            db.set_online_cache(cand, "FOUND", json.dumps(resp, ensure_ascii=False))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp, ensure_ascii=False))
            return resp

    # 2.8 Negative cache handling (NOT_FOUND)
    # Check if this query is already marked as not found
    for cand in lookup_candidates:
        cached = db.get_online_cache(cand)
        if cached and cached["status"] == "NOT_FOUND":
            return {
                "query": cleaned,
                "resolved_lemma": None,
                "display_text": f"Ingen resultater fundet for '{cleaned}'.",
                "entries": [],
                "cached_not_found": True,
            }

    # Store negative cache (30-day TTL if live ordnet.dk was reachable and had no entry; 300s TTL on net error)
    ttl = 300 if net_error else 30 * 86400
    if not rate_limited:
        for cand in lookup_candidates:
            db.set_online_cache(cand, "NOT_FOUND", ttl_seconds=ttl)

    return {
        "query": cleaned,
        "resolved_lemma": None,
        "display_text": f"Ingen resultater fundet for '{cleaned}'.",
        "entries": [],
        "cached_not_found": False,
    }
