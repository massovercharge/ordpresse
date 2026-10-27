"""Native StarDict v2.4.2 binary compiler and binary reader.

Implements GLib g_ascii_strcasecmp binary sorting law for KOReader (sdcv) compatibility.
"""

from dataclasses import dataclass, field
from datetime import date
import os
from pathlib import Path
import shutil
import struct
import subprocess
from typing import List, Optional, Tuple


@dataclass
class StarDictEntry:
    headword: str
    html_content: str
    synonyms: List[str] = field(default_factory=list)


def stardict_sort_key(word: str) -> Tuple[bytes, bytes]:
    """GLib g_ascii_strcasecmp sorting law for StarDict binary search (.idx & .syn).
    
    KOReader and sdcv sort by GLib's g_ascii_strcasecmp, which ONLY lowercases
    ASCII bytes 0x41..0x5A ('A'-'Z') and treats non-ASCII bytes (UTF-8 >= 0x80,
    such as Danish 'Æ', 'Ø', 'Å') as raw unsigned bytes.
    Python's bytes.lower() implements exact ASCII-only case folding byte-for-byte!
    """
    raw_bytes = word.encode("utf-8")
    return (raw_bytes.lower(), raw_bytes)


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
    ) -> dict[str, Path]:
        """Compile entries into .ifo, .idx, .dict (.dict.dz), and .syn files."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Deduplicate entries by headword (merge html if duplicate)
        merged: dict[str, StarDictEntry] = {}
        for e in entries:
            hw = e.headword.strip()
            if not hw:
                continue
            if hw in merged:
                # Merge HTML content & synonyms
                existing = merged[hw]
                existing.html_content += f"<hr/><br/>{e.html_content}"
                for s in e.synonyms:
                    if s not in existing.synonyms:
                        existing.synonyms.append(s)
            else:
                merged[hw] = StarDictEntry(
                    headword=hw,
                    html_content=e.html_content,
                    synonyms=list(e.synonyms),
                )

        # 2. Sort entries by stardict_sort_key(headword)
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

        # 4. Build .syn file
        # Each synonym maps to entry index i (0 <= i < len(sorted_entries))
        # Exclude exact match to entry headword
        syn_records_list: List[Tuple[str, int]] = []
        for i, entry in enumerate(sorted_entries):
            seen_syns = set()
            for s in entry.synonyms:
                s_clean = s.strip()
                if not s_clean or s_clean == entry.headword or s_clean in seen_syns:
                    continue
                seen_syns.add(s_clean)
                syn_records_list.append((s_clean, i))

        # Sort synonyms by stardict_sort_key
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


class StarDictReader:
    """Binary search reader for StarDict files to verify exact lookup offsets."""

    def __init__(self, ifo_path: Path):
        self.ifo_path = Path(ifo_path)
        base = self.ifo_path.with_suffix("")
        self.idx_path = base.with_suffix(".idx")
        self.syn_path = base.with_suffix(".syn")
        self.dict_dz_path = base.with_suffix(".dict.dz")
        self.dict_raw_path = base.with_suffix(".dict")

        if not self.idx_path.exists():
            raise FileNotFoundError(f"Missing {self.idx_path}")

        # Parse .ifo metadata
        self.metadata = {}
        for line in self.ifo_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                self.metadata[k.strip()] = v.strip()

        self.wordcount = int(self.metadata.get("wordcount", 0))
        self.synwordcount = int(self.metadata.get("synwordcount", 0))

        # Load .idx entries into an indexed table of offsets
        self.idx_bytes = self.idx_path.read_bytes()
        self.idx_entries: List[Tuple[str, int, int]] = []  # (word, offset, size)
        pos = 0
        while pos < len(self.idx_bytes):
            null_pos = self.idx_bytes.find(b"\x00", pos)
            if null_pos == -1:
                break
            word = self.idx_bytes[pos:null_pos].decode("utf-8")
            offset, size = struct.unpack(">II", self.idx_bytes[null_pos + 1 : null_pos + 9])
            self.idx_entries.append((word, offset, size))
            pos = null_pos + 9

        # Load .syn entries if present
        self.syn_entries: List[Tuple[str, int]] = []
        if self.syn_path.exists():
            syn_bytes = self.syn_path.read_bytes()
            pos = 0
            while pos < len(syn_bytes):
                null_pos = syn_bytes.find(b"\x00", pos)
                if null_pos == -1:
                    break
                syn_word = syn_bytes[pos:null_pos].decode("utf-8")
                entry_idx = struct.unpack(">I", syn_bytes[null_pos + 1 : null_pos + 5])[0]
                self.syn_entries.append((syn_word, entry_idx))
                pos = null_pos + 5

        # Dict reader
        if self.dict_raw_path.exists():
            self._dict_bytes = self.dict_raw_path.read_bytes()
        elif self.dict_dz_path.exists():
            import gzip
            self._dict_bytes = gzip.decompress(self.dict_dz_path.read_bytes())
        else:
            self._dict_bytes = b""

    def binary_search_idx(self, query: str) -> Optional[Tuple[str, int, int]]:
        """Binary search .idx using GLib g_ascii_strcasecmp sort key."""
        target_key = stardict_sort_key(query)
        low = 0
        high = len(self.idx_entries) - 1

        while low <= high:
            mid = (low + high) // 2
            word, offset, size = self.idx_entries[mid]
            mid_key = stardict_sort_key(word)

            if mid_key == target_key:
                return (word, offset, size)
            elif mid_key < target_key:
                low = mid + 1
            else:
                high = mid - 1

        return None

    def binary_search_syn(self, query: str) -> Optional[int]:
        """Binary search .syn using GLib g_ascii_strcasecmp sort key."""
        target_key = stardict_sort_key(query)
        low = 0
        high = len(self.syn_entries) - 1

        while low <= high:
            mid = (low + high) // 2
            syn_word, entry_idx = self.syn_entries[mid]
            mid_key = stardict_sort_key(syn_word)

            if mid_key == target_key:
                return entry_idx
            elif mid_key < target_key:
                low = mid + 1
            else:
                high = mid - 1

        return None

    def lookup(self, query: str) -> Optional[str]:
        """Look up word in .idx, or fall back to .syn, returning HTML definition."""
        # 1. Direct .idx lookup
        res = self.binary_search_idx(query)
        if res:
            _, offset, size = res
            return self._dict_bytes[offset : offset + size].decode("utf-8")

        # 2. .syn lookup
        entry_idx = self.binary_search_syn(query)
        if entry_idx is not None and 0 <= entry_idx < len(self.idx_entries):
            _, offset, size = self.idx_entries[entry_idx]
            return self._dict_bytes[offset : offset + size].decode("utf-8")

        return None
