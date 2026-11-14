"""Router for dictionary lookup preview (Canonical 3-Layer E-Ink display)."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ordpresse.config import OUTPUT_DIR
from ordpresse.db import Database
from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import synthesize_compound_entry
from ordpresse.scrapers import HistoricalScraper
from ordpresse.sources.nordic_1800s import get_nordic_1800s_entry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/preview")
def preview_lookup(request: Request, q: str, format: Optional[str] = None):
    """Preview a dictionary lookup, returning rendered canonical E-Ink HTML."""
    word = q.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="Mangler søgeord 'q'")

    # 1. First, check the most recently compiled StarDict binary in OUTPUT_DIR
    ifo_candidates = sorted(
        list(OUTPUT_DIR.glob("build_*/ordpresse.ifo")) + list(OUTPUT_DIR.glob("ordpresse.ifo")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for ifo in ifo_candidates:
        try:
            from ordpresse.stardict_reader import StarDictReader
            reader = StarDictReader(ifo)
            entry = reader.lookup_entry(word)
            if entry:
                hw, html = entry
                if format == "html":
                    return HTMLResponse(content=html)
                resolution = f"Bøjningsopslag: {q} → {hw}" if q.strip().lower() != hw.strip().lower() else f"Direkte opslag: {hw}"
                return {
                    "word": q,
                    "headword": hw,
                    "found": True,
                    "html": html,
                    "source": "stardict_binary",
                    "resolution": resolution,
                }
        except Exception as e:
            logger.debug(f"Reader failed on {ifo}: {e}")

    # 2. Fallback to curated Nordic 1800s vocabulary (35+ Moberg and 19th c. terms)
    nordic = get_nordic_1800s_entry(word)
    if nordic:
        html = render_canonical_html(nordic)
        if format == "html":
            return HTMLResponse(content=html)
        resolution = f"Bøjningsopslag: {q} → {nordic.headword}" if q.strip().lower() != nordic.headword.strip().lower() else f"Direkte opslag: {nordic.headword}"
        return {
            "word": q,
            "headword": nordic.headword,
            "found": True,
            "html": html,
            "source": "nordic_1800s",
            "resolution": resolution,
        }

    # 3. Fallback to Modern COR-SEM in Database
    db = Database()
    if db.datasets_path.exists():
        cor_defs = db.lookup_cor_lemma_definitions(word)
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
                    definition=f"({normalize_pos(d.get('pos', ''))}) {d['definition']}" if len(poses) > 1 and d.get("pos") else d["definition"],
                    quote=d.get("examples").split("|")[0].strip() if d.get("examples") else None,
                    source_tag="COR-SEM",
                )
                for d in cor_defs[:4]
            ]
            cor_infs = db.lookup_cor_inflections(lemma, poses[0] if len(poses) == 1 else None)
            canonical = CanonicalEntry(
                headword=lemma,
                pos=pos_display,
                inflections=set(cor_infs + [word]),
                summary_hook="",
                modern_senses=modern_senses,
                sources_used=["COR-SEM"],
            )
            html = render_canonical_html(canonical)
            if format == "html":
                return HTMLResponse(content=html)
            resolution = f"Bøjningsopslag: {q} → {lemma}" if q.strip().lower() != lemma.strip().lower() else f"Direkte opslag: {lemma}"
            return {
                "word": q,
                "headword": lemma,
                "found": True,
                "html": html,
                "source": "cor_sem",
                "resolution": resolution,
            }

    # 4. Fallback to morphological compound synthesis (dernedefra, uefterrettelig)
    synth = synthesize_compound_entry(word, db)
    if synth:
        html = render_canonical_html(synth)
        if format == "html":
            return HTMLResponse(content=html)
        resolution = f"Morfologisk opslag: {q} → {synth.headword}" if q.strip().lower() != synth.headword.strip().lower() else f"Direkte opslag: {synth.headword}"
        return {
            "word": q,
            "headword": synth.headword,
            "found": True,
            "html": html,
            "source": "morphology",
            "resolution": resolution,
        }

    # 5. Fallback to Scraper
    scraper = HistoricalScraper(db)
    res = scraper.fetch_word_definition(word, allow_remote=True)
    if res:
        html = res["html_content"]
        if format == "html":
            return HTMLResponse(content=html)
        resolution = f"Opslag ({res.get('source', 'ODS')}): {res['headword']}"
        return {
            "word": q,
            "headword": res["headword"],
            "found": True,
            "html": html,
            "source": res.get("source", "scraper"),
            "resolution": resolution,
        }

    fallback_html = f"<div style='color:#666;font-style:italic;'>Ingen definition fundet for »{q}«.</div>"
    if format == "html":
        return HTMLResponse(content=fallback_html)
    return {
        "word": q,
        "headword": word,
        "found": False,
        "html": fallback_html,
        "source": "none",
        "resolution": f"Ingen match fundet for »{q}«",
    }
