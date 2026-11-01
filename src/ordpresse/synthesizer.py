"""Synthesis module for dictionary entries, smart summary hooks, and definition integrity."""

import logging
import re
from typing import List, Optional

from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.sources.nordic_1800s import enrich_canonical_entry_with_historical

logger = logging.getLogger(__name__)

# Curated completed definitions for COR-SEM entries whose DSL export ended in "…" (U+2026)
COR_SEM_DEFINITION_COMPLETIONS: dict[str, str] = {
    "stakkels": "som vækker medlidenhed pga. sin svaghed, hjælpeløshed eller ulykkelige situation",
    "prioritetslån": "lån der ydes mod panteret i fast ejendom typisk et realkreditlån",
    "nybegynder": "person der netop er begyndt at beskæftige sig med et bestemt fag, en bestemt sport, hobby el.lign., men næsten ikke har noget kendskab dertil",
    "søskende": "personer som er børn af samme forældre, dvs. brødre og søstre",
    "grund": "landjorden som et underlag man kan stå fast, gå eller færdes på",
    "grunde": "tænke dybt og grundigt over noget; gruble; fundere",
    "forarbejde": "behandle et råmateriale så der dannes et anvendeligt produkt; bearbejde",
    "forarbejdning": "behandling af et råmateriale så der dannes et anvendeligt produkt; bearbejdning",
}

# Mapping by exact COR.SEM ID
COR_SEM_ID_COMPLETIONS: dict[str, str] = {
    "COR.SEM.24428.01": "som vækker medlidenhed pga. sin svaghed, hjælpeløshed eller ulykkelige situation",
    "COR.SEM.1006235.01": "lån der ydes mod panteret i fast ejendom typisk et realkreditlån",
    "COR.SEM.87422.01": "person der netop er begyndt at beskæftige sig med et bestemt fag, en bestemt sport, hobby el.lign., men næsten ikke har noget kendskab dertil",
    "COR.SEM.68312.01": "personer som er børn af samme forældre, dvs. brødre og søstre",
    "COR.SEM.1002897.01": "landjorden som et underlag man kan stå fast, gå eller færdes på",
    "COR.SEM.1002897.02": "grundlag som giver (u)sikkerhed; udgangspunkt hvorfra noget udvikler sig eller kan bedømmes",
    "COR.SEM.1002897.03": "landområde (hvorpå noget bygges)",
    "COR.SEM.1002897.04": "grundlag som forårsager, motiverer eller forklarer et fænomen, en handling, holdning e.l.; årsag",
    "COR.SEM.1002898.01": "tænke dybt og grundigt over noget; gruble; fundere",
    "COR.SEM.35019.01": "behandle et råmateriale så der dannes et anvendeligt produkt; bearbejde",
    "COR.SEM.35019.02": "arbejde mentalt med noget; analysere og forholde sig til",
    "COR.SEM.84184.01": "behandling af et råmateriale så der dannes et anvendeligt produkt; bearbejdning",
}

ABBREVIATIONS_RE = re.compile(
    r"\b(?:jf|bl\.a|f\.eks|fx|dvs|osv|m\.m|el|ell|alm|egl|især|navnlig|ofte|sj|nr|sp|s|st|ca|bl|a|o\.lign|o\.a)\s*$",
    re.IGNORECASE,
)


def truncate_summary_hook(text: str, max_chars: int = 165) -> str:
    """Shorten summary hook ONLY at a natural sentence period (.) or semicolon (;).

    NEVER truncate mid-sentence after an arbitrary character limit.
    NEVER split or truncate at a period belonging to an abbreviation (jf., bl.a., f.eks., fx., dvs., osv., m.m., etc.).
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
        prefix = text[:end_idx].strip()
        punct = text[end_idx]

        # Never split at a period belonging to an abbreviation
        if punct == "." and ABBREVIATIONS_RE.search(prefix):
            continue

        # Never split if inside unclosed parentheses
        if prefix.count("(") > prefix.count(")"):
            continue

        if len(prefix) > 15:
            candidates.append(prefix)

    if candidates:
        best = candidates[-1]
        best = re.sub(r"[,;]?\s*\(\s*(?:jf\.|se\b)[^)]*\)?\s*$", "", best, flags=re.I).strip()
        best = re.sub(r"[,;]?\s*\([^)]*$", "", best).strip()
        open_c = best.count("(")
        close_c = best.count(")")
        if open_c > close_c:
            best += ")" * (open_c - close_c)
        if not best.endswith("."):
            best += "."
        return best

    # If no period or semicolon exists within max_chars, check for the first natural boundary beyond
    for m in re.finditer(r"[.;](\s+|$)", text):
        end_idx = m.start()
        prefix = text[:end_idx].strip()
        punct = text[end_idx]

        if punct == "." and ABBREVIATIONS_RE.search(prefix):
            continue

        if prefix.count("(") > prefix.count(")"):
            continue

        if len(prefix) > 15:
            clause = prefix
            clause = re.sub(r"[,;]?\s*\(\s*(?:jf\.|se\b)[^)]*\)?\s*$", "", clause, flags=re.I).strip()
            clause = re.sub(r"[,;]?\s*\([^)]*$", "", clause).strip()
            open_c = clause.count("(")
            close_c = clause.count(")")
            if open_c > close_c:
                clause += ")" * (open_c - close_c)
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


def synthesize_cor_sem_summary_hook(definitions: list) -> str:
    """Synthesize summary_hook for COR-SEM entries.

    Rule: Never synthesize a summary hook by merely concatenating modern definitions
    (to prevent double repetition and wasted screen space on e-ink readers).
    Returns an empty string so the article goes directly to the numbered definitions list.
    """
    return ""


def is_valid_canonical_entry(entry: CanonicalEntry) -> bool:
    """Return True only if the entry has at least one authentic definition or genuine summary hook.

    Enforces the strict rule: No CanonicalEntry may be written to .idx/.dict.dz if modern_senses,
    historical_senses, and summary_hook are all empty, or if summary_hook is merely an echo of headword + pos.
    """
    if entry.modern_senses:
        return True
    if entry.historical_senses:
        return True
    hook = (entry.summary_hook or "").strip()
    if not hook:
        return False
    # If summary_hook is merely "headword" or "headword (pos)", it is a skeleton dummy
    clean_hook = re.sub(r"\s*\([^)]*\)$", "", hook).strip().lower()
    if clean_hook == entry.headword.strip().lower():
        return False
    return True
