"""Synthesis module for dictionary entries, smart summary hooks, and definition integrity."""

import logging
import re
from typing import List, Optional

from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.sources.nordic_1800s import enrich_canonical_entry_with_historical

logger = logging.getLogger(__name__)

# Curated completed definitions for COR-SEM entries whose DSL export ended in "…" (U+2026)
COR_SEM_DEFINITION_COMPLETIONS: dict[str, str] = {
    "prioritetslån": "lån der ydes mod panteret i fast ejendom typisk et realkreditlån",
    "nybegynder": "person der netop er begyndt at beskæftige sig med et bestemt fag, en bestemt sport, hobby el.lign., men næsten ikke har noget kendskab dertil",
    "søskende": "personer som er børn af samme forældre, dvs. brødre og søstre",
}

# Mapping by exact COR.SEM ID
COR_SEM_ID_COMPLETIONS: dict[str, str] = {
    "COR.SEM.1006235.01": "lån der ydes mod panteret i fast ejendom typisk et realkreditlån",
    "COR.SEM.87422.01": "person der netop er begyndt at beskæftige sig med et bestemt fag, en bestemt sport, hobby el.lign., men næsten ikke har noget kendskab dertil",
    "COR.SEM.68312.01": "personer som er børn af samme forældre, dvs. brødre og søstre",
}


def truncate_summary_hook(text: str, max_chars: int = 165) -> str:
    """Shorten summary hook ONLY at a natural sentence period (.) or semicolon (;).

    NEVER truncate mid-sentence after an arbitrary character limit.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text

    # Look for natural sentence boundaries (. or ;) within max_chars
    candidates: List[str] = []
    for m in re.finditer(r"[.;](\s+|$)", text[: max_chars + 1]):
        end_idx = m.start()
        candidate = text[:end_idx].strip()
        if len(candidate) > 15:
            candidates.append(candidate)

    if candidates:
        best = candidates[-1]
        if not best.endswith("."):
            best += "."
        return best

    # If no period or semicolon exists within max_chars, check for the first natural boundary beyond
    first_punct = re.search(r"[.;](\s+|$)", text)
    if first_punct and first_punct.start() > 15:
        clause = text[: first_punct.start()].strip()
        if not clause.endswith("."):
            clause += "."
        return clause

    # If no natural punctuation exists, strictly FORBID arbitrary mid-sentence slicing with "...".
    # Preserve the full sentence intact.
    return text


def should_render_summary_hook(entry: CanonicalEntry) -> bool:
    """Determine if summary_hook should be rendered in the HTML.

    Rules:
    - Omit if empty.
    - If entry has only 1 definition or hook is identical to senses[0].definition,
      OMIT to prevent double repetition ("dobbeltkonfekt").
    - Only show if there are multiple meanings, or if there is a distinct historical resume.
    """
    if not entry.summary_hook or not entry.summary_hook.strip():
        return False

    all_senses = entry.historical_senses + entry.modern_senses
    if not all_senses:
        return False

    hook_clean = re.sub(r"[\s.,;…]+$", "", entry.summary_hook.strip().lower())
    first_def = all_senses[0].definition.strip()
    first_def_clean = re.sub(r"[\s.,;…]+$", "", first_def.lower())

    # Redundancy check: if hook is identical or prefix/suffix of the first definition
    if hook_clean == first_def_clean or first_def_clean.startswith(hook_clean) or hook_clean.startswith(first_def_clean):
        return False

    if len(all_senses) == 1:
        # Single definition: show ONLY if it is a distinct historical resume
        return bool(entry.historical_senses)

    # Multiple senses: show summary hook
    return True


def synthesize_cor_sem_summary_hook(definitions: list[tuple[str, str]]) -> str:
    """Synthesize summary_hook for COR-SEM entries.

    - If 1 definition: return empty string (omit hook to avoid repeating the definition).
    - If multiple definitions: synthesize a clean multi-sense hook with natural clause boundaries.
    """
    if len(definitions) <= 1:
        return ""

    clauses = []
    for i, (d, _) in enumerate(definitions[:3], 1):
        clean_d = truncate_summary_hook(d, max_chars=80)
        clean_d = clean_d.rstrip(".;")
        if clean_d:
            clauses.append(f"{i}) {clean_d}")

    if not clauses:
        return ""

    hook = "; ".join(clauses)
    if len(hook) > 165:
        hook = truncate_summary_hook(hook, max_chars=165)
    return hook
