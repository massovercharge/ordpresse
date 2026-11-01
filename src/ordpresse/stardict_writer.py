"""Native StarDict v2.4.2 binary compiler.

Implements GLib g_ascii_strcasecmp binary sorting law for KOReader (sdcv) compatibility
and enforces the 4 structural invariants:
1. Unique lowercase headword in .idx
2. No headword appears in its own .syn-set
3. .idx sorted with stardict_sort_key
4. .syn sorted with stardict_sort_key
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Dict, List, Optional, Set, Tuple

# Re-export StarDictReader and stardict_sort_key for backwards compatibility
from ordpresse.models import CanonicalEntry
from ordpresse.orthography import (
    GENITIVE_WHITELIST,
    historical_aa_to_modern,
    is_invalid_synthetic_form,
    is_redundant_genitive,
    is_regular_genitive,
    sanitize_headword,
)
from ordpresse.stardict_reader import StarDictReader, stardict_sort_key  # noqa: F401
from ordpresse.synthesizer import is_valid_canonical_entry


@dataclass
class StarDictEntry:
    headword: str
    html_content: str
    synonyms: List[str] = field(default_factory=list)
    canonical_entry: Optional[CanonicalEntry] = None


class StarDictWriter:
    """Compiles a collection of dictionary entries into StarDict v2.4.2 files."""

    def __init__(
        self,
        bookname: str = "Den Danske Ordpresse (1700-nu)",
        author: str = "DSL / CST / Den Danske Ordpresse (ordpresse)",
        description: str = "Historisk og moderne dansk ordbog presset med ordpresse",
        date_str: Optional[str] = None,
    ):
        self.bookname = bookname
        self.author = author
        self.description = description
        self.date_str = date_str or date.today().strftime("%Y.%m.%d")

    def compile(
        self,
        entries: List[StarDictEntry],
        output_dir: Path,
        base_filename: str = "ordpresse",
        compress_dict: bool = True,
    ) -> Dict[str, Path]:
        """Compile entries into .ifo, .idx, .dict (.dict.dz), and .syn files."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Deduplicate entries by lowercase headword (Invariant #1)
        # Safety guard: Strictly forbid empty skeleton entries from being written to .idx / .dict.dz
        merged: Dict[str, StarDictEntry] = {}
        for e in entries:
            raw_hw = e.headword.strip()
            if not raw_hw:
                continue

            clean_hw, extra_syns = sanitize_headword(raw_hw)
            if not clean_hw:
                continue

            # Check canonical entry validity if available
            if getattr(e, "canonical_entry", None) is not None:
                if not is_valid_canonical_entry(e.canonical_entry):
                    continue

            # Check HTML: Must contain at least one definition container, quote, or summary hook
            html = (e.html_content or "").strip()
            if not html:
                continue
            if not any(marker in html for marker in ["<ol>", "<li>", "▸", "•", "<p>", "[Historisk kontekst]"]):
                continue

            hw_lower = clean_hw.lower()

            # Preserve capitalized or original casing in synonyms if different
            orig_syns: List[str] = list(e.synonyms) + list(extra_syns)
            if clean_hw != hw_lower and clean_hw not in orig_syns:
                orig_syns.append(clean_hw)

            if hw_lower in merged:
                existing = merged[hw_lower]
                # Avoid redundant HTML duplication
                if e.html_content not in existing.html_content:
                    existing.html_content += f"<hr/><br/>{e.html_content}"
                for s in orig_syns:
                    if s not in existing.synonyms:
                        existing.synonyms.append(s)
                if not existing.canonical_entry and e.canonical_entry:
                    existing.canonical_entry = e.canonical_entry
            else:
                merged[hw_lower] = StarDictEntry(
                    headword=hw_lower,
                    html_content=e.html_content,
                    synonyms=orig_syns,
                    canonical_entry=e.canonical_entry,
                )

        # 2. Sort entries by stardict_sort_key(headword) (Invariant #3)
        sorted_entries = sorted(merged.values(), key=lambda x: stardict_sort_key(x.headword))

        # 3. Build .dict payload and compute byte offsets for .idx
        dict_data = bytearray()
        idx_records = bytearray()

        for entry in sorted_entries:
            content_bytes = entry.html_content.encode("utf-8")
            offset = len(dict_data)
            size = len(content_bytes)
            dict_data.extend(content_bytes)

            # Record in .idx: <headword>\0 + offset (uint32_be) + size (uint32_be)
            idx_records.extend(entry.headword.encode("utf-8"))
            idx_records.append(0)
            idx_records.extend(struct.pack(">II", offset, size))

        # 4. Build .syn file (Invariants #2 & #4)
        # Each synonym maps to entry index i (0 <= i < len(sorted_entries))
        # Exclude exact case-insensitive match to entry headword
        syn_records_list: List[Tuple[str, int]] = []
        for i, entry in enumerate(sorted_entries):
            seen_syns: Set[str] = set()
            hw_low = entry.headword.lower()
            pos = ""
            if entry.canonical_entry and entry.canonical_entry.pos:
                pos = entry.canonical_entry.pos

            raw_forms: List[str] = list(entry.synonyms)
            if entry.canonical_entry and entry.canonical_entry.inflections:
                for inf in entry.canonical_entry.inflections:
                    if inf not in raw_forms:
                        raw_forms.append(inf)

            kept_forms: List[str] = []
            for s in raw_forms:
                s_clean = s.strip()
                if not s_clean:
                    continue
                # Invariant #2: Intet headword må optræde i sit eget .syn-sæt
                if s_clean.lower() == hw_low:
                    continue
                if s_clean.startswith("-") or s_clean.endswith("-"):
                    continue

                # Task 2.2: Abbreviation & Fragment Filter:
                # Never emit a .syn key that ends with a period '.' (e.g. "adm.", "afd.", "bl.a.")
                # or is the truncated fragment "aarh"
                if s_clean.endswith(".") or s_clean.lower() == "aarh":
                    continue

                # 1. Eliminate ASCII Title-Case Duplicates in .syn:
                # If first char is ASCII letter, only keep lowercase form in .syn!
                if ord(s_clean[0]) <= 127 and s_clean[0].isalpha():
                    s_clean = s_clean.lower()
                    if s_clean == hw_low:
                        continue

                # Task 2.1: Prune Regular Possessive Genitives from .syn:
                if is_redundant_genitive(form=s_clean, headword=hw_low, pos=pos):
                    continue

                # Purge Synthetic -ere/-est Forms from .syn:
                if is_invalid_synthetic_form(hw_low, s_clean.lower()):
                    continue

                if s_clean not in kept_forms:
                    kept_forms.append(s_clean)

            # Task 1: Generate aa synonym variants for headword itself AND every kept inflection
            # If the string contains "å" (anywhere in the word), generate aa_form = form.replace("å", "aa") (lowercase)
            # Ensure we do NOT generate redundant genitives of aa_form either.
            all_for_aa = [entry.headword] + list(kept_forms)
            for form in all_for_aa:
                f_low = form.lower()
                if "å" in f_low:
                    aa_form = f_low.replace("å", "aa")
                    if aa_form == hw_low:
                        continue
                    if aa_form.endswith(".") or aa_form == "aarh":
                        continue
                    if is_redundant_genitive(form=aa_form, headword=hw_low, pos=pos):
                        continue
                    if is_invalid_synthetic_form(hw_low, aa_form):
                        continue
                    if aa_form not in kept_forms:
                        kept_forms.append(aa_form)
                elif "aa" in f_low:
                    ao_form = historical_aa_to_modern(f_low).lower()
                    if ao_form != hw_low and not ao_form.endswith(".") and ao_form != "aarh":
                        if not is_redundant_genitive(form=ao_form, headword=hw_low, pos=pos):
                            if not is_invalid_synthetic_form(hw_low, ao_form):
                                if ao_form not in kept_forms:
                                    kept_forms.append(ao_form)

            for s_clean in kept_forms:
                if s_clean in seen_syns or s_clean.lower() == hw_low:
                    continue
                seen_syns.add(s_clean)
                syn_records_list.append((s_clean, i))

        # Invariant #4: Sort synonyms strictly by stardict_sort_key
        syn_records_list.sort(key=lambda item: stardict_sort_key(item[0]))

        syn_records = bytearray()
        for syn_word, entry_idx in syn_records_list:
            syn_records.extend(syn_word.encode("utf-8"))
            syn_records.append(0)
            syn_records.extend(struct.pack(">I", entry_idx))

        # 5. Write .dict
        dict_path = output_dir / f"{base_filename}.dict"
        dict_path.write_bytes(dict_data)

        # Compress to .dict.dz if dictzip is available
        final_dict_path = dict_path
        if compress_dict and shutil.which("dictzip"):
            try:
                subprocess.run(["dictzip", "-f", str(dict_path)], check=True, capture_output=True)
                dz_path = output_dir / f"{base_filename}.dict.dz"
                if dz_path.exists():
                    final_dict_path = dz_path
            except Exception:
                pass

        # 6. Write .idx
        idx_path = output_dir / f"{base_filename}.idx"
        idx_path.write_bytes(idx_records)

        # 7. Write .syn
        syn_path = output_dir / f"{base_filename}.syn"
        syn_path.write_bytes(syn_records)

        # 8. Write .ifo
        ifo_path = output_dir / f"{base_filename}.ifo"
        ifo_content = (
            "StarDict's dict ifo file\n"
            "version=2.4.2\n"
            f"wordcount={len(sorted_entries)}\n"
            f"synwordcount={len(syn_records_list)}\n"
            f"idxfilesize={len(idx_records)}\n"
            f"bookname={self.bookname}\n"
            f"author={self.author}\n"
            f"description={self.description}\n"
            f"date={self.date_str}\n"
            "sametypesequence=h\n"
        )
        ifo_path.write_text(ifo_content, encoding="utf-8")

        return {
            "ifo": ifo_path,
            "idx": idx_path,
            "dict": final_dict_path,
            "syn": syn_path,
        }
