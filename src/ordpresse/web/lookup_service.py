"""Multi-tier lookup service for KOReader online integration (V10.2 Zero-Trust)."""

import asyncio
import json
import logging
import re
import time
from typing import Optional

from bs4 import BeautifulSoup
from fastapi import HTTPException

from ordpresse.config import OUTPUT_DIR
from ordpresse.db import Database
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import synthesize_compound_entry
from ordpresse.sources.nordic_1800s import get_nordic_1800s_entry
from ordpresse.sources.ods_scraper import clean_ods_definition_text

logger = logging.getLogger(__name__)

# Rate limiter for external lookups (Tier 3): minimum 1.5s between external requests
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
        defs = entry.get("definitions", [])
        etym = entry.get("etymology", "")

        header = hw
        if pos:
            header += f" ({pos})"
        lines.append(header)

        if inf:
            lines.append(f"Bøjning: {inf}")

        for d in defs:
            # Strip redundant number prefixes for clean bullets in display_text
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


def canonical_to_response(
    query: str,
    canonical: CanonicalEntry,
    sources: Optional[list[str]] = None,
) -> dict:
    """Convert CanonicalEntry to structured JSON contract."""
    headword = canonical.headword
    pos = canonical.pos or ""
    inflections = ", ".join(sorted(canonical.inflections)) if canonical.inflections else ""
    etymology = canonical.realia_note or ""

    definitions = []
    idx = 1
    if canonical.summary_hook:
        definitions.append(f"{idx}. {canonical.summary_hook}")
        idx += 1

    for s in (canonical.historical_senses or []):
        tag = f"({s.source_tag or 'ODS'} / 1700–1950)"
        lbl = f" ({s.label})" if s.label else ""
        d_text = f"{tag}{lbl} {s.definition}".strip()
        definitions.append(f"{idx}. {d_text}")
        idx += 1

    for s in (canonical.modern_senses or []):
        lbl = f"({s.label}) " if s.label else ""
        definitions.append(f"{idx}. {lbl}{s.definition}".strip())
        idx += 1

    if not definitions and canonical.summary_hook:
        definitions.append(f"1. {canonical.summary_hook}")

    entry_dict = {
        "headword": headword,
        "homonym_index": 1,
        "part_of_speech": pos,
        "inflections": inflections,
        "phonetic": getattr(canonical, "phonetic", "") or "",
        "definitions": definitions,
        "etymology": etymology,
    }

    src_list = sources or (canonical.sources_used if canonical.sources_used else ["ODS", "Molbech", "Salmonsens"])
    display_text = format_display_text(query, headword, [entry_dict], src_list)

    return {
        "query": query,
        "resolved_lemma": headword,
        "display_text": display_text,
        "entries": [entry_dict],
    }


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
        if "Bøjning:" in text_node or "bøjning:" in text_node:
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
        # Fallback to paragraph or plain text parsing
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


async def perform_koreader_lookup(query: str, db: Database) -> dict:
    """
    Multi-tier lookup pipeline:
    1. Check online_lookup_cache (positive & negative).
    2. Check local Nordic 1800s, StarDict binary, COR-SEM, compound synthesis, DB cache.
    3. Rate-limited live fallback to ordnet.dk (ODS/DDO).
    """
    cleaned = sanitize_query(query)
    q_lower = cleaned.lower()
    q_aa = q_lower.replace("aa", "å")

    lookup_candidates = [q_lower]
    if q_aa != q_lower:
        lookup_candidates.append(q_aa)

    # -------------------------------------------------------------
    # Tier 0: Online Lookup Cache (Positive and Negative)
    # -------------------------------------------------------------
    for cand in lookup_candidates:
        cached = db.get_online_cache(cand)
        if cached:
            if cached["status"] == "FOUND" and cached.get("payload_json"):
                try:
                    payload = json.loads(cached["payload_json"])
                    payload["query"] = cleaned  # Preserve caller's original casing/query
                    return payload
                except Exception as e:
                    logger.debug(f"Failed to decode cached payload for {cand}: {e}")
            elif cached["status"] == "NOT_FOUND":
                return {
                    "query": cleaned,
                    "resolved_lemma": None,
                    "display_text": f"Ingen resultater fundet for '{cleaned}'.",
                    "entries": [],
                    "cached_not_found": True,
                }

    # -------------------------------------------------------------
    # Tier 1: Local StarDict / SQLite Database & Curated Vocabulary
    # -------------------------------------------------------------

    # 1.1 Curated Nordic 1800s Vocabulary
    for cand in lookup_candidates:
        nordic = get_nordic_1800s_entry(cand)
        if nordic:
            resp = canonical_to_response(cleaned, nordic)
            db.set_online_cache(cand, "FOUND", json.dumps(resp))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp))
            return resp

    # 1.2 Most recently compiled StarDict binary in OUTPUT_DIR
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
                    db.set_online_cache(cand, "FOUND", json.dumps(resp))
                    if cand != q_lower:
                        db.set_online_cache(q_lower, "FOUND", json.dumps(resp))
                    return resp
        except Exception as e:
            logger.debug(f"Reader failed on {ifo}: {e}")

    # 1.3 Modern COR-SEM in datasets.sqlite3
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
                db.set_online_cache(cand, "FOUND", json.dumps(resp))
                if cand != q_lower:
                    db.set_online_cache(q_lower, "FOUND", json.dumps(resp))
                return resp

    # 1.4 Morphological compound synthesis
    for cand in lookup_candidates:
        synth = synthesize_compound_entry(cand, db)
        if synth:
            resp = canonical_to_response(cleaned, synth, sources=["Morfologi", "COR"])
            db.set_online_cache(cand, "FOUND", json.dumps(resp))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp))
            return resp

    # 1.5 Previously scraped definitions in cache.sqlite3
    for cand in lookup_candidates:
        cached_row = db.get_cached_definition(cand)
        if cached_row:
            hw = cached_row.get("headword") or cand
            html = cached_row.get("html_content") or ""
            src = cached_row.get("source") or "ODS"
            resp = parse_html_to_response(cleaned, hw, html, source=src)
            db.set_online_cache(cand, "FOUND", json.dumps(resp))
            if cand != q_lower:
                db.set_online_cache(q_lower, "FOUND", json.dumps(resp))
            return resp

    # -------------------------------------------------------------
    # Tier 3: Rate-Limited Live Fallback to ordnet.dk
    # -------------------------------------------------------------
    global _LAST_EXTERNAL_CALL_TIME
    from ordpresse.scrapers import HistoricalScraper

    async with _EXTERNAL_LOCK:
        now = time.time()
        elapsed = now - _LAST_EXTERNAL_CALL_TIME
        if elapsed < 1.5:
            await asyncio.sleep(1.5 - elapsed)
        _LAST_EXTERNAL_CALL_TIME = time.time()

        scraper = HistoricalScraper(db)
        res = None
        for cand in lookup_candidates:
            res = scraper.fetch_word_definition(cand, allow_remote=True)
            if res:
                break

    if res:
        hw = res.get("headword") or cleaned
        html = res.get("html_content") or ""
        src = res.get("source") or "ODS"
        resp = parse_html_to_response(cleaned, hw, html, source=src)
        for cand in lookup_candidates:
            db.set_online_cache(cand, "FOUND", json.dumps(resp))
        return resp

    # Not found anywhere -> Store negative cache (30-day TTL)
    for cand in lookup_candidates:
        db.set_online_cache(cand, "NOT_FOUND", ttl_seconds=30 * 86400)

    return {
        "query": cleaned,
        "resolved_lemma": None,
        "display_text": f"Ingen resultater fundet for '{cleaned}'.",
        "entries": [],
        "cached_not_found": False,
    }
