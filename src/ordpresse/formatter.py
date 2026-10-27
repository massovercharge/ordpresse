"""Canonical E-Ink HTML formatter and POS normalizer for KOReader."""

import re
from ordpresse.models import CanonicalEntry


def normalize_pos(pos: str) -> str:
    """Normalize POS tags from COR, ODS, Moths, Molbech, and NAOB.
    
    Standardized tags:
    - sb. fk.
    - sb. itk.
    - sb.
    - adj.
    - vb.
    - adv.
    """
    if not pos:
        return ""

    p = pos.strip().lower()
    # Remove surrounding parens, e.g. "(sb.fk.)" -> "sb.fk."
    p = re.sub(r"^[(\[]+|[)\]]+$", "", p).strip()

    # Match exact or leading grammatical keywords
    if p in ("sb.fk", "sb.fk.", "sb. fk.", "sb. fk", "en", "en.", "n. s. c.", "fælleskøn", "faelleskoen", "sb.m.", "sb.m", "sb.f.", "sb.f", "mask.", "fem."):
        return "sb. fk."
    if p.startswith("sb.fk") or p.startswith("sb. fk") or p.startswith("sb.m") or p.startswith("sb.f"):
        return "sb. fk."

    if p in ("sb.itk", "sb.itk.", "sb. itk.", "sb. itk", "et", "et.", "n. s. n.", "intetkøn", "intetkoen", "neutr.", "n."):
        return "sb. itk."
    if p.startswith("sb.itk") or p.startswith("sb. itk") or p.startswith("sb.n"):
        return "sb. itk."

    if p in ("sb", "sb.", "substantiv", "subst.", "navneord"):
        return "sb."
    if p.startswith("sb.") or p.startswith("sb "):
        return "sb."

    if p in ("adj", "adj.", "adject.", "tillægsord", "tillaegsord", "ubøjel. adj.", "uboejel. adj."):
        return "adj."
    if p.startswith("adj.") or p.startswith("adj "):
        return "adj."

    if p in ("vb", "vb.", "v.", "verb.", "udsagnsord"):
        return "vb."
    if p.startswith("vb.") or p.startswith("vb ") or p.startswith("v."):
        return "vb."

    if p in ("adv", "adv.", "biord"):
        return "adv."
    if p.startswith("adv.") or p.startswith("adv "):
        return "adv."

    # Fallback to normalized prefix if starts with sb/adj/vb/adv
    m = re.match(r"^(sb\.?|adj\.?|vb\.?|adv\.?)", p)
    if m:
        base = m.group(1).rstrip(".")
        if "fk" in p:
            return "sb. fk."
        if "itk" in p:
            return "sb. itk."
        return f"{base}."

    return p


def render_canonical_html(entry: CanonicalEntry) -> str:
    """Render a CanonicalEntry into KOReader-optimized E-Ink HTML."""
    meta_parts = []
    sample_forms = sorted(f for f in entry.inflections if f.lower() != entry.headword.lower())[:3]
    if sample_forms:
        meta_parts.append(", ".join(sample_forms))
    if entry.historical_spellings:
        meta_parts.append("ældre: " + ", ".join(entry.historical_spellings[:2]))
    meta_str = f" <small>({'; '.join(meta_parts)})</small>" if meta_parts else ""

    pos_str = f" <i>{entry.pos}</i>" if entry.pos else ""
    parts = [f"<div><b>{entry.headword}</b>{pos_str}{meta_str}</div>"]

    # Layer 1: Immediate 2-second TL;DR Hook (<= 165 chars)
    if entry.summary_hook:
        parts.append(
            f'<div style="margin:3px 0;padding:2px 6px;border-left:2px solid #555;">'
            f"<b>▸</b> {entry.summary_hook}</div>"
        )

    # Layer 2A: Historical Senses (ODS / 1700–1950)
    if entry.historical_senses:
        parts.append('<div><small><b>HISTORISK BETYDNING (ODS / 1700–1950)</b></small><ol>')
        for s in entry.historical_senses[:4]:
            lbl = f"<i>({s.label})</i> " if s.label else ""
            q_auth = f" ({s.quote_author})" if s.quote_author else ""
            q = f'<br><small><i>»{s.quote}«</i>{q_auth}</small>' if s.quote else ""
            parts.append(f"<li>{lbl}{s.definition}{q}</li>")
        parts.append("</ol></div>")

    # Layer 2B: Modern Senses (COR-SEM)
    if entry.modern_senses:
        hdr = '<small><b>NUTIDSDANSK (COR-SEM)</b></small>' if entry.historical_senses else ""
        parts.append(f"<div>{hdr}<ol>")
        for s in entry.modern_senses[:3]:
            q = f'<br><small><i>»{s.quote}«</i></small>' if s.quote else ""
            parts.append(f"<li>{s.definition}{q}</li>")
        parts.append("</ol></div>")

    # Layer 3: Cultural & Historical Realia Note
    if entry.realia_note:
        parts.append(
            f'<div style="margin-top:3px;"><small><b>[Historisk kontekst]:</b> {entry.realia_note}</small></div>'
        )

    # Footer: Compact Source Attribution
    if entry.sources_used:
        unique_srcs = " · ".join(dict.fromkeys(entry.sources_used))
        parts.append(f'<div><small style="color:#666;">Kilde: {unique_srcs}</small></div>')

    return "".join(parts)
