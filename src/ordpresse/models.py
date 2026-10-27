"""Canonical data models for Den Danske Ordpresse v2."""

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class SenseItem:
    """A single sense or definition item with optional label, literary quote, and source."""

    definition: str  # Clean prose definition (no bibliographic codes)
    label: Optional[str] = None  # e.g. "hist.", "landbo.", "foræld.", "jura", "møntvæsen"
    quote: Optional[str] = None  # Max 1 clean literary quote per sense
    quote_author: Optional[str] = None  # Full author name, e.g. "St. St. Blicher", "H. Pontoppidan"
    source_tag: str = "COR-SEM"  # "COR-SEM", "ODS", "Moth", "Molbech", "NAOB"


@dataclass
class CanonicalEntry:
    """Unified entry structure rendered through the KOReader E-Ink template."""

    headword: str  # Always lowercase canonical key for .idx, e.g. "lægdslem"
    historical_spellings: List[str] = field(default_factory=list)  # e.g. ["Lægdslem", "Lejdslem"]
    pos: str = ""  # Normalized: "sb. fk.", "sb. itk.", "adj.", "vb.", "adv."
    inflections: Set[str] = field(default_factory=set)  # Written to .syn (excluding headword)
    summary_hook: str = ""  # Layer 1: 1-sentence TL;DR hook (<= 165 chars)
    historical_senses: List[SenseItem] = field(default_factory=list)  # Layer 2A: Max 4 items
    modern_senses: List[SenseItem] = field(default_factory=list)  # Layer 2B: Max 3 items
    realia_note: Optional[str] = None  # Layer 3: Encyclopedic context (currencies, parish law)
    sources_used: List[str] = field(default_factory=list)
