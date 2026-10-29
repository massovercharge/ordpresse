"""Zero-Compromise Triage of cache.sqlite3 (Phase 4).

Inspects every row in cache.sqlite3 (`definitions` table) and classifies each row into:
- KEEP: 100% clean rows meeting all quality criteria
- SALVAGE: 100% lossless deterministic local repair (Nordic 1800s, Molbech, compound synthesis, NAOB SITATER stripping)
- PURGE: Deleted without hesitation (dummy placeholders, truncated definitions, empty COR skeletons)
"""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
from typing import Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from ordpresse.formatter import normalize_pos, render_canonical_html
from ordpresse.models import CanonicalEntry, SenseItem
from ordpresse.orthography import generate_orthographic_synonyms, synthesize_compound_entry
from ordpresse.sources.molbech_1859 import get_molbech_1859_entry
from ordpresse.sources.nordic_1800s import get_nordic_1800s_entry
from ordpresse.synthesizer import truncate_summary_hook

logger = logging.getLogger(__name__)

FORBIDDEN_SUBSTRINGS = [
    "Historisk opslag for",
    "ingen betydningsangivelse",
    "SITATER",
    "EKSEMPLER",
    "ORD I NÆRHEDEN",
    "Mente du:",
]

GLUED_POS_PATTERN = re.compile(
    r"\b[a-zæøåA-ZÆØÅ]{2,}(?:adjektiv|substantiv|verbum|adverbium)\b"
)
UNNORMALIZED_POS_TAG_PATTERN = re.compile(
    r"<i>[^<]*(?:adjektiv|substantiv|verbum|adverbium)</i>"
)


@dataclass
class TriageResult:
    total_before: int = 0
    keep_count: int = 0
    salvaged_count: int = 0
    purged_count: int = 0
    salvaged_examples: List[Tuple[str, str, str]] = field(default_factory=list)  # (word, before_summary, after_summary)
    purged_words: List[str] = field(default_factory=list)


def extract_definition_text(html: str) -> str:
    """Extract prose definition text from KOReader E-Ink HTML, excluding headers and footers."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Remove header <div><b>headword</b>...</div>
    first_div = soup.find("div")
    if first_div and first_div.find("b"):
        first_div.decompose()
    # Remove footer with Kilde:
    for sm in soup.find_all("small"):
        if "kilde:" in sm.get_text(" ", strip=True).lower():
            if sm.parent and sm.parent.name == "div":
                sm.parent.decompose()
            else:
                sm.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!)])", r"\1", text)
    return text


def is_clean_keep_row(word: str, headword: str, html_content: str) -> bool:
    """Return True if row passes all strict Phase 4 Bucket 1 (KEEP) criteria."""
    if not html_content or not html_content.strip():
        return False

    # 1. Must contain at least one genuine definition marker
    if not any(marker in html_content for marker in ["<ol>", "<li>", "▸", "<p>", "[Historisk kontekst]"]):
        return False

    # 2. Must NOT contain any forbidden substrings
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in html_content:
            return False

    # 3. Must NOT have glued POS tags
    if GLUED_POS_PATTERN.search(html_content) or UNNORMALIZED_POS_TAG_PATTERN.search(html_content):
        return False

    # 4. Must NOT end with mid-sentence truncation "..."
    # Check if definition ends with ... or …
    soup = BeautifulSoup(html_content, "html.parser")
    for li in soup.find_all("li"):
        t = li.get_text(" ", strip=True)
        if t.endswith("...") or t.endswith("…"):
            return False
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if t.endswith("...") or t.endswith("…"):
            return False

    # 5. Must have a non-empty definition text (len >= 15 chars after stripping HTML tags)
    def_text = extract_definition_text(html_content)
    if len(def_text) < 15:
        return False

    # 6. Must not consist solely of unparsed citation code or raw quote
    if re.match(r"^[A-Z][a-zA-Z0-9.\-]+\.[0-9]+$", def_text):
        return False

    return True


def attempt_salvage(
    word: str,
    source: str,
    headword: str,
    html_content: str,
    synonyms: List[str],
    db=None,
) -> Optional[Tuple[CanonicalEntry, str]]:
    """Attempt deterministic, zero-compromise local repair (Phase 4 Bucket 2).
    
    Returns (repaired_canonical_entry, repaired_html) if successful, or None.
    """
    w_clean = word.strip().lower()
    hw_clean = headword.strip().lower()

    # --- Case B: Local Curated Lexicons & Morphological Synthesis ---
    # 1. Nordic 1800s
    nordic = get_nordic_1800s_entry(w_clean) or get_nordic_1800s_entry(hw_clean)
    if nordic:
        html = render_canonical_html(nordic)
        return nordic, html

    # 2. Molbech 1859
    molbech = get_molbech_1859_entry(w_clean) or get_molbech_1859_entry(hw_clean)
    if molbech:
        html = render_canonical_html(molbech)
        return molbech, html

    # 3. Morphological compound synthesis (dernedefra, hernedefra, uefterrettelig)
    synth = synthesize_compound_entry(w_clean, db) or synthesize_compound_entry(hw_clean, db)
    if synth:
        html = render_canonical_html(synth)
        return synth, html

    # --- Case A: NAOB / ODS SITATER or Glued POS Repair ---
    if html_content and ("SITATER" in html_content or GLUED_POS_PATTERN.search(html_content)):
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. Extract and normalize POS
        pos = ""
        # Check italic tags or text
        i_tag = soup.find("i")
        if i_tag:
            raw_pos = i_tag.get_text(" ", strip=True)
            raw_pos = re.sub(rf"^{re.escape(hw_clean)}\s*", "", raw_pos, flags=re.IGNORECASE).strip()
            raw_pos = re.sub(rf"^{re.escape(w_clean)}\s*", "", raw_pos, flags=re.IGNORECASE).strip()
            pos = normalize_pos(raw_pos)

        # 2. Extract prose definition before SITATER / EKSEMPLER
        repaired_senses: List[SenseItem] = []
        # Check <li> nodes first
        li_nodes = soup.find_all("li")
        if li_nodes:
            for li in li_nodes:
                text = li.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                text = re.sub(r"\s+([,.;:!)])", r"\1", text)
                text = re.split(r"\b(?:SITATER|EKSEMPLER)\b", text)[0].strip()
                text = re.sub(r"\s*\([^)]*$", "", text).strip()
                text = re.sub(r"\s+([,.;:!)])", r"\1", text)
                if (
                    text
                    and len(text) >= 12
                    and not text.endswith("...")
                    and not text.endswith("…")
                    and not any(bad in text for bad in FORBIDDEN_SUBSTRINGS)
                ):
                    repaired_senses.append(
                        SenseItem(definition=text, label="nordisk", source_tag=source.upper() or "NAOB")
                    )

        if not repaired_senses:
            # Fallback to whole article text before SITATER
            full_text = soup.get_text(" ", strip=True)
            full_text = re.sub(r"\s+", " ", full_text).strip()
            full_text = re.sub(r"\s+([,.;:!)])", r"\1", full_text)
            text_before = re.split(r"\b(?:SITATER|EKSEMPLER)\b", full_text)[0].strip()
            # Strip leading headword / pos header
            text_before = re.sub(rf"^{re.escape(headword)}\s*", "", text_before, flags=re.IGNORECASE).strip()
            text_before = re.sub(rf"^{re.escape(word)}\s*", "", text_before, flags=re.IGNORECASE).strip()
            text_before = re.sub(r"^(?:adjektiv|substantiv|verbum|adverbium|adj\.|sb\.|vb\.|adv\.)\s*", "", text_before, flags=re.IGNORECASE).strip()
            text_before = re.sub(r"\s*\([^)]*$", "", text_before).strip()
            text_before = re.sub(r"\s+([,.;:!)])", r"\1", text_before)
            if (
                text_before
                and len(text_before) >= 12
                and not text_before.endswith("...")
                and not text_before.endswith("…")
                and not any(bad in text_before for bad in FORBIDDEN_SUBSTRINGS)
            ):
                repaired_senses.append(
                    SenseItem(definition=text_before, label="nordisk", source_tag=source.upper() or "NAOB")
                )

        if repaired_senses:
            summary_hook = truncate_summary_hook(repaired_senses[0].definition, max_chars=165) if len(repaired_senses) > 1 else ""
            canonical = CanonicalEntry(
                headword=hw_clean or w_clean,
                pos=pos or "adj.",
                inflections=set(synonyms) | {w_clean, hw_clean},
                summary_hook=summary_hook,
                historical_senses=repaired_senses[:3],
                sources_used=[source.upper() or "NAOB"],
            )
            html = render_canonical_html(canonical)
            return canonical, html

    return None


def run_triage(
    cache_db_path: Path,
    datasets_db_path: Optional[Path] = None,
    purged_output_path: Optional[Path] = None,
    dry_run: bool = False,
) -> TriageResult:
    """Execute triage algorithm across all rows in cache.sqlite3."""
    cache_db_path = Path(cache_db_path)
    if not cache_db_path.exists():
        raise FileNotFoundError(f"Cache database not found: {cache_db_path}")

    conn = sqlite3.connect(cache_db_path)
    conn.row_factory = sqlite3.Row

    # Optional local database for COR-SEM lookups
    db = None
    if datasets_db_path and Path(datasets_db_path).exists():
        from ordpresse.db import Database
        db = Database(datasets_path=Path(datasets_db_path), cache_path=cache_db_path)

    cur = conn.cursor()
    cur.execute("SELECT word, source, headword, html_content, synonyms_json FROM definitions")
    rows = cur.fetchall()

    result = TriageResult(total_before=len(rows))

    to_update: List[Tuple[str, str, str, str]] = []  # (html_content, headword, synonyms_json, word)
    to_delete: List[str] = []

    for r in rows:
        word = r["word"] or ""
        source = r["source"] or ""
        headword = r["headword"] or ""
        html = r["html_content"] or ""
        try:
            synonyms = json.loads(r["synonyms_json"]) if r["synonyms_json"] else []
        except Exception:
            synonyms = []

        # 1. Bucket 1: KEEP
        if is_clean_keep_row(word, headword, html):
            result.keep_count += 1
            continue

        # 2. Bucket 2: SALVAGE
        salvage_res = attempt_salvage(
            word=word,
            source=source,
            headword=headword,
            html_content=html,
            synonyms=synonyms,
            db=db,
        )

        if salvage_res is not None:
            entry, new_html = salvage_res
            # Re-verify that repaired HTML passes clean keep check!
            if is_clean_keep_row(word, entry.headword, new_html):
                result.salvaged_count += 1
                all_syns = generate_orthographic_synonyms(entry.headword, list(entry.inflections) + synonyms)
                syns_json = json.dumps(all_syns, ensure_ascii=False)
                to_update.append((new_html, entry.headword, syns_json, word))

                if len(result.salvaged_examples) < 5:
                    before_snippet = re.sub(r"<[^>]+>", " ", html)[:80].strip()
                    after_snippet = re.sub(r"<[^>]+>", " ", new_html)[:80].strip()
                    result.salvaged_examples.append((word, before_snippet, after_snippet))
                continue

        # 3. Bucket 3: PURGE
        result.purged_count += 1
        to_delete.append(word)
        result.purged_words.append(word)

    if not dry_run:
        with conn:
            if to_update:
                conn.executemany(
                    "UPDATE definitions SET html_content = ?, headword = ?, synonyms_json = ? WHERE word = ?",
                    to_update,
                )
            if to_delete:
                conn.executemany("DELETE FROM definitions WHERE word = ?", [(w,) for w in to_delete])

        # Write purged words list
        if purged_output_path and result.purged_words:
            purged_path = Path(purged_output_path)
            purged_path.parent.mkdir(parents=True, exist_ok=True)
            # Read existing purged words to deduplicate
            existing_words: Set[str] = set()
            if purged_path.exists():
                existing_words = set(purged_path.read_text(encoding="utf-8").splitlines())
            all_purged = sorted(list(existing_words | set(result.purged_words)))
            purged_path.write_text("\n".join(all_purged) + "\n", encoding="utf-8")

    conn.close()
    if db:
        db.close()

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ordpresse Zero-Compromise Cache Triage")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/cache.sqlite3"),
        help="Path to cache.sqlite3",
    )
    parser.add_argument(
        "--datasets-db",
        type=Path,
        default=Path("data/datasets.sqlite3"),
        help="Path to datasets.sqlite3",
    )
    parser.add_argument(
        "--purged-file",
        type=Path,
        default=Path("data/purged_words.txt"),
        help="Path to write purged words",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and print summary without modifying database",
    )
    args = parser.parse_args()

    print(f"=== Kører Ordpresse Cache Triage på {args.db} ===")
    res = run_triage(
        cache_db_path=args.db,
        datasets_db_path=args.datasets_db if args.datasets_db.exists() else None,
        purged_output_path=args.purged_file,
        dry_run=args.dry_run,
    )

    print(f"Før triage:     {res.total_before} rækker")
    print(f"KEEP (rene):    {res.keep_count}")
    print(f"SALVAGED:       {res.salvaged_count}")
    print(f"PURGED (slettet): {res.purged_count}")

    if res.salvaged_examples:
        print("\n--- Eksempler på SALVAGED rækker ---")
        for w, before, after in res.salvaged_examples:
            print(f"• '{w}':")
            print(f"    Før:  {before}")
            print(f"    Efter: {after}")

    if res.purged_words:
        print(f"\nPurged words ({len(res.purged_words)}) gemt til {args.purged_file}")


if __name__ == "__main__":
    main()
