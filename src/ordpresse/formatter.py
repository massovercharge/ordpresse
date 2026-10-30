"""Canonical E-Ink HTML formatter and POS normalizer for KOReader."""

import re
from ordpresse.models import CanonicalEntry
from ordpresse.synthesizer import should_render_summary_hook, truncate_summary_hook


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

    if p in ("adj", "adj.", "adject.", "adjektiv", "adjektiv.", "tillægsord", "tillaegsord", "ubøjel. adj.", "uboejel. adj."):
        return "adj."
    if p.startswith("adj.") or p.startswith("adj ") or p.startswith("adjektiv"):
        return "adj."

    if p in ("vb", "vb.", "v.", "verb.", "verbum", "verbum.", "udsagnsord"):
        return "vb."
    if p.startswith("vb.") or p.startswith("vb ") or p.startswith("v.") or p.startswith("verbum"):
        return "vb."

    if p in ("adv", "adv.", "adverbium", "adverbium.", "biord"):
        return "adv."
    if p.startswith("adv.") or p.startswith("adv ") or p.startswith("adverbium"):
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


def select_display_inflections(
    headword: str,
    inflections: set[str] | list[str],
    limit: int = 3,
) -> list[str]:
    """Select the best representative display inflections for KOReader header.

    Rules:
    - Never include forms starting or ending with a hyphen ('grund-', 'søskende-').
    - Exclude forms identical to headword (case-insensitive).
    - Deprioritize genitives (-ens, -ets, -enes, -ernes).
    - Sort by: (is_capitalized_variant, is_genitive, len(form), form.lower())
    """
    hw_lower = headword.strip().lower()
    clean_forms = []
    seen = set()

    for raw_f in inflections:
        f = raw_f.strip()
        if not f:
            continue
        if f.startswith("-") or f.endswith("-"):
            continue
        if f.lower() == hw_lower:
            continue
        if f.lower() in seen:
            continue
        seen.add(f.lower())

        is_capitalized_variant = 1 if (f[0].isupper() and f.lower() != f) else 0
        is_genitive = 1 if f.lower().endswith(("ens", "ets", "enes", "ernes")) else 0
        clean_forms.append((is_capitalized_variant, is_genitive, len(f), f.lower(), f))

    clean_forms.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [item[4] for item in clean_forms[:limit]]


def render_canonical_html(entry: CanonicalEntry) -> str:
    """Render a CanonicalEntry into KOReader-optimized E-Ink HTML."""
    meta_parts = []
    sample_forms = select_display_inflections(entry.headword, entry.inflections, limit=3)
    if sample_forms:
        meta_parts.append(", ".join(sample_forms))
    if entry.historical_spellings:
        meta_parts.append("ældre: " + ", ".join(entry.historical_spellings[:2]))
    meta_str = f" <small>({'; '.join(meta_parts)})</small>" if meta_parts else ""

    pos_str = f" <i>{entry.pos}</i>" if entry.pos else ""
    parts = [f"<div><b>{entry.headword}</b>{pos_str}{meta_str}</div>"]

    # Layer 1: Immediate 2-second TL;DR Hook (<= 165 chars)
    # Smart handling: Omit if single definition or identical to first definition
    if should_render_summary_hook(entry):
        parts.append(
            f'<div style="margin:3px 0;padding:2px 6px;border-left:2px solid #555;">'
            f"<b>•</b> {entry.summary_hook}</div>"
        )

    # Layer 2A: Historical Senses (ODS / 1700–1950)
    if entry.historical_senses:
        parts.append('<div><small><b>HISTORISK BETYDNING (ODS / 1700–1950)</b></small><ol>')
        for s in entry.historical_senses[:4]:
            lbl = f"<i>({s.label})</i> " if s.label else ""
            q_auth = f" ({s.quote_author})" if s.quote_author else ""
            q = ""
            if s.quote:
                clean_q = s.quote.split("|")[0].strip()
                if clean_q:
                    q = f'<br><small><i>»{clean_q}«</i>{q_auth}</small>'
            parts.append(f"<li>{lbl}{s.definition}{q}</li>")
        parts.append("</ol></div>")

    # Layer 2B: Modern Senses (COR-SEM)
    if entry.modern_senses:
        hdr = '<small><b>NUTIDSDANSK (COR-SEM)</b></small>' if entry.historical_senses else ""
        parts.append(f"<div>{hdr}<ol>")
        for s in entry.modern_senses[:3]:
            q = ""
            if s.quote:
                clean_q = s.quote.split("|")[0].strip()
                if clean_q:
                    q = f'<br><small><i>»{clean_q}«</i></small>'
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
