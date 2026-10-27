"""StarDict binary search reader implementing GLib g_ascii_strcasecmp sorting law.

Provides pure-Python binary search over .idx and .syn files for KOReader compatibility.
"""

from pathlib import Path
import struct
from typing import Dict, List, Optional, Tuple


def stardict_sort_key(word: str) -> Tuple[bytes, bytes]:
    """GLib g_ascii_strcasecmp sorting law for StarDict binary search (.idx & .syn).
    
    KOReader and sdcv sort by GLib's g_ascii_strcasecmp, which ONLY lowercases
    ASCII bytes 0x41..0x5A ('A'-'Z') and treats non-ASCII bytes (UTF-8 >= 0x80,
    such as Danish 'Æ', 'Ø', 'Å') as raw unsigned bytes.
    Python's bytes.lower() implements exact ASCII-only case folding byte-for-byte!
    """
    raw_bytes = word.encode("utf-8")
    return (raw_bytes.lower(), raw_bytes)


class StarDictReader:
    """Binary search reader for StarDict v2.4.2 files to verify exact lookup offsets."""

    def __init__(self, ifo_path: Path | str):
        self.ifo_path = Path(ifo_path)
        base = self.ifo_path.with_suffix("")
        self.idx_path = base.with_suffix(".idx")
        self.syn_path = base.with_suffix(".syn")
        self.dict_dz_path = base.with_suffix(".dict.dz")
        self.dict_raw_path = base.with_suffix(".dict")

        if not self.idx_path.exists():
            raise FileNotFoundError(f"Missing {self.idx_path}")

        # Parse .ifo metadata
        self.metadata: Dict[str, str] = {}
        for line in self.ifo_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                self.metadata[k.strip()] = v.strip()

        self.wordcount = int(self.metadata.get("wordcount", 0))
        self.synwordcount = int(self.metadata.get("synwordcount", 0))

        # Load .idx entries into an indexed table of records: (headword, offset, size)
        self.idx_bytes = self.idx_path.read_bytes()
        self.idx_entries: List[Tuple[str, int, int]] = []
        pos = 0
        total_idx_len = len(self.idx_bytes)
        while pos < total_idx_len:
            null_pos = self.idx_bytes.find(b"\x00", pos)
            if null_pos == -1 or null_pos + 9 > total_idx_len:
                break
            word = self.idx_bytes[pos:null_pos].decode("utf-8")
            offset, size = struct.unpack(">II", self.idx_bytes[null_pos + 1 : null_pos + 9])
            self.idx_entries.append((word, offset, size))
            pos = null_pos + 9

        # Load .syn entries if present: (synonym, entry_index)
        self.syn_entries: List[Tuple[str, int]] = []
        if self.syn_path.exists():
            syn_bytes = self.syn_path.read_bytes()
            pos = 0
            total_syn_len = len(syn_bytes)
            while pos < total_syn_len:
                null_pos = syn_bytes.find(b"\x00", pos)
                if null_pos == -1 or null_pos + 5 > total_syn_len:
                    break
                syn_word = syn_bytes[pos:null_pos].decode("utf-8")
                entry_idx = struct.unpack(">I", syn_bytes[null_pos + 1 : null_pos + 5])[0]
                self.syn_entries.append((syn_word, entry_idx))
                pos = null_pos + 5

        # Load .dict or decompressed .dict.dz payload
        if self.dict_raw_path.exists():
            self._dict_bytes = self.dict_raw_path.read_bytes()
        elif self.dict_dz_path.exists():
            import gzip
            self._dict_bytes = gzip.decompress(self.dict_dz_path.read_bytes())
        else:
            self._dict_bytes = b""

    def binary_search_idx(self, query: str) -> Optional[Tuple[str, int, int]]:
        """Binary search .idx entries using GLib g_ascii_strcasecmp sort key."""
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
        """Binary search .syn entries using GLib g_ascii_strcasecmp sort key."""
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
        q = query.strip()
        if not q:
            return None

        # 1. Direct .idx lookup
        res = self.binary_search_idx(q)
        if res:
            _, offset, size = res
            return self._dict_bytes[offset : offset + size].decode("utf-8")

        # 2. Case-folded .idx lookup (lowercase)
        if q != q.lower():
            res_lower = self.binary_search_idx(q.lower())
            if res_lower:
                _, offset, size = res_lower
                return self._dict_bytes[offset : offset + size].decode("utf-8")

        # 3. Direct .syn lookup
        entry_idx = self.binary_search_syn(q)
        if entry_idx is not None and 0 <= entry_idx < len(self.idx_entries):
            _, offset, size = self.idx_entries[entry_idx]
            return self._dict_bytes[offset : offset + size].decode("utf-8")

        # 4. Case-folded .syn lookup (lowercase)
        if q != q.lower():
            entry_idx_lower = self.binary_search_syn(q.lower())
            if entry_idx_lower is not None and 0 <= entry_idx_lower < len(self.idx_entries):
                _, offset, size = self.idx_entries[entry_idx_lower]
                return self._dict_bytes[offset : offset + size].decode("utf-8")

        return None
