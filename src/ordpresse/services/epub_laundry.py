"""Ordpresse Bogvaskeri & Restaurerings-pipeline til EPUB-filer (v2.0).

Kombinerer AST CSS-vask, tilstandsbaseret typografi, sikker OCR-heling,
format-tro billedoptimering, Calibre ebook-polish og Lexical Pre-Warming.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
import zipfile

from bs4 import BeautifulSoup, CData, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction
import ftfy
from lingua import Language, LanguageDetectorBuilder
from PIL import Image, ImageOps
import tinycss2

from ordpresse.db import Database
from ordpresse.sources.nordic_1800s import get_nordic_1800s_entry

logger = logging.getLogger("ordpresse.laundry")

# Initialiser sprogdetektor ved modul-import
DETECTOR = (
    LanguageDetectorBuilder.from_languages(
        Language.DANISH,
        Language.ENGLISH,
        Language.BOKMAL,
        Language.SWEDISH,
        Language.GERMAN,
    )
    .with_preloaded_language_models()
    .build()
)

# XML-nodetyper der ALDRIG må tekst-transformeres
IGNORED_STRING_TYPES = (
    Comment,
    CData,
    ProcessingInstruction,
    Declaration,
    Doctype,
)


@dataclass
class LaundryOptions:
    """Konfigurationsmuligheder for Ordpresse Bogvaskeri (v2.0)."""
    fix_encoding_and_lang: bool = True
    clean_publisher_css: bool = True
    upgrade_typography: bool = True
    danish_quote_style: Literal["guillemets", "baseline"] = "guillemets"  # »...« vs „...“
    heal_ocr_paragraphs: bool = True
    rebuild_missing_toc: bool = True
    optimize_images: bool = True
    run_calibre_polish: bool = True
    generate_lexical_cache: bool = True


@dataclass
class LaundryResult:
    """Resultat og telemetri fra en gennemført bogvask."""
    epub_bytes: bytes
    book_md5: str
    detected_language: str
    original_size_kb: int
    polished_size_kb: int
    paragraphs_healed: int = 0
    toc_entries_rebuilt: int = 0
    images_optimized: int = 0
    calibre_polished: bool = False
    prewarmed_words_count: int = 0
    processing_ms: int = 0


class DefaultHistoricalDictionaryService:
    """Standard opslagstjeneste til leksikalsk pre-warming."""

    def __init__(self, db: Optional[Database] = None):
        self._db = db

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = Database()
        return self._db

    def lookup_historical_only(self, word: str) -> Optional[dict]:
        w_lower = word.lower().strip()
        # 1. Nordic 1800s opslag (højeste historiske prioritet)
        try:
            entry = get_nordic_1800s_entry(w_lower)
            if entry:
                all_senses = list(getattr(entry, "historical_senses", [])) + list(getattr(entry, "modern_senses", []))
                defs = [s.definition for s in all_senses if getattr(s, "definition", None)]
                def_text = "; ".join(defs) or getattr(entry, "summary_hook", "") or "Historisk opslag"
                return {
                    "headword": entry.headword,
                    "pos": entry.pos or "ukendt",
                    "definition": def_text,
                    "source": "Nordic 1800s",
                }
        except Exception as e:
            logger.debug(f"Nordic 1800s lookup error for {word}: {e}")

        # 2. Opslag i definitionscache
        try:
            cached = self.db.get_cached_definition(w_lower)
            if cached and cached.get("senses"):
                def_text = "; ".join(
                    s.get("definition", "") for s in cached.get("senses", []) if s.get("definition")
                )
                return {
                    "headword": cached.get("headword", w_lower),
                    "pos": cached.get("pos", "ukendt"),
                    "definition": def_text,
                    "source": "Ordpresse Cache",
                }
        except Exception as e:
            logger.debug(f"Cache lookup error for {word}: {e}")

        return None


class EpubLaundryService:
    """Ordpresse Dybdevask & Restaurerings-pipeline til EPUB-filer (v2.0).

    Kombinerer AST CSS-vask, tilstandsbaseret typografi, sikker OCR-heling,
    format-tro billedoptimering, Calibre ebook-polish og Lexical Pre-Warming.
    """

    BLOCKED_CSS_PROPS = {
        "font",
        "font-family",
        "font-size",
        "line-height",
        "letter-spacing",
        "color",
        "background-color",
        "margin-left",
        "margin-right",
        "padding-left",
        "padding-right",
    }

    INLINE_STYLE_BLOCK_RE = re.compile(
        r"\b(font-family|font-size|font|line-height|letter-spacing|color|background-color)\s*:[^;]+;?",
        re.IGNORECASE,
    )

    POETIC_OR_SPECIAL_CLASSES = re.compile(
        r"(verse|poem|poetry|lyric|epigraph|center|right|heading|title|author|dedication|toc)",
        re.IGNORECASE,
    )

    CHAPTER_HEADING_RE = re.compile(
        r"^\s*(?:kapitel|chapter|del|part|bog|book|afsnit)\s+(?:\d+|[IVXLCDM]+)\b",
        re.IGNORECASE,
    )

    DA_ABBREVIATIONS = re.compile(
        r"\b(f\.eks|bl\.a|ca|nr|kap|afd|m\.fl|osv|dvs|pga|jf|inkl|ekskl|hhv|s|kr|dr|hr|frk)\.\s+(?=\w)",
        re.IGNORECASE,
    )
    EN_ABBREVIATIONS = re.compile(
        r"\b(Mr|Mrs|Ms|Dr|Prof|St|Ch|Vol|No|Fig|pp|vs)\.\s+(?=\w)"
    )

    def __init__(self, dictionary_service: Any = None, books_dir: Optional[Path] = None):
        self.dictionary_service = dictionary_service or DefaultHistoricalDictionaryService()
        self.books_dir = books_dir or Path("data/books")
        self.has_calibre = shutil.which("ebook-polish") is not None

    async def process_epub(
        self, raw_epub_bytes: bytes, options: Optional[LaundryOptions] = None
    ) -> LaundryResult:
        """Asynkront entrypoint til FastAPI."""
        opts = options or LaundryOptions()
        return await asyncio.to_thread(self._run_pipeline_sync, raw_epub_bytes, opts)

    def _run_pipeline_sync(
        self, raw_bytes: bytes, opts: LaundryOptions
    ) -> LaundryResult:
        t0 = time.monotonic()
        orig_kb = max(1, len(raw_bytes) // 1024)
        paragraphs_healed = 0
        images_optimized = 0
        toc_rebuilt_count = 0
        word_frequencies: Dict[str, int] = {}
        discovered_chapters: List[Tuple[str, str, str]] = []  # (filename, anchor_id, title)

        with tempfile.TemporaryDirectory(prefix="ordpresse_laundry_") as tmpdir:
            tmp_path = Path(tmpdir)
            stage1_epub = tmp_path / "stage1.epub"
            stage2_epub = tmp_path / "stage2.epub"

            # 1. Detektér sprog ud fra de første 12.000 tegn brødtekst
            detected_lang = self._detect_book_language(raw_bytes)
            logger.info(f"Bogvask v2 startet: Detekteret sprog='{detected_lang}' ({orig_kb} KB)")

            # 2. Gennemløb og transformér indholdet i EPUB ZIP-arkivet
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zin:
                namelist = zin.namelist()
                needs_toc_rebuild = (
                    opts.rebuild_missing_toc and self._check_if_toc_is_sparse(zin)
                )

                with zipfile.ZipFile(
                    stage1_epub, "w", compression=zipfile.ZIP_DEFLATED
                ) as zout:
                    # Krav i EPUB OCF-spec: mimetype SKAL ligge først og være ukomprimeret
                    if "mimetype" in namelist:
                        zout.writestr(
                            "mimetype",
                            zin.read("mimetype"),
                            compress_type=zipfile.ZIP_STORED,
                        )
                    else:
                        zout.writestr(
                            "mimetype",
                            b"application/epub+zip",
                            compress_type=zipfile.ZIP_STORED,
                        )

                    for item in zin.infolist():
                        if item.filename == "mimetype":
                            continue

                        data = zin.read(item.filename)
                        lower_name = item.filename.lower()

                        # A. Opdater OPF-manifest med korrekt ISO 639-1 sprogkode (da / en)
                        if lower_name.endswith(".opf") and opts.fix_encoding_and_lang:
                            data = self._fix_opf_metadata(data, detected_lang)

                        # B. Rekursiv AST-baseret CSS-sanitering
                        elif lower_name.endswith(".css") and opts.clean_publisher_css:
                            data = self._sanitize_css_ast(data)

                        # C. XHTML DOM-reparation, OCR-heling, typografi & kapitel-scanning
                        elif lower_name.endswith((".xhtml", ".html", ".htm")):
                            data, healed_cnt, chap_list = self._process_xhtml_chapter(
                                data,
                                filename=item.filename,
                                lang=detected_lang,
                                opts=opts,
                                scan_chapters=needs_toc_rebuild,
                                word_freq_out=word_frequencies,
                            )
                            paragraphs_healed += healed_cnt
                            discovered_chapters.extend(chap_list)

                        # D. Format-tro billedoptimering (bevarer PNG som PNG og JPEG som JPEG)
                        elif (
                            lower_name.endswith((".jpg", ".jpeg", ".png"))
                            and opts.optimize_images
                        ):
                            new_data, was_opt = self._optimize_image_preserve_format(
                                data,
                                filename=lower_name,
                                is_cover="cover" in lower_name,
                            )
                            if was_opt:
                                images_optimized += 1
                            data = new_data

                        zout.writestr(item.filename, data)

            # 2b. Hvis TOC var tom/defekt og vi fandt kapitler, opdateres toc.ncx
            if needs_toc_rebuild and len(discovered_chapters) >= 2:
                toc_rebuilt_count = self._inject_rebuilt_toc_ncx(
                    stage1_epub, discovered_chapters
                )

            # 3. Kør Calibre `ebook-polish` (EPUB 3 opgradering, font-subsetting, ubrugt CSS)
            calibre_used = False
            if opts.run_calibre_polish and self.has_calibre:
                calibre_used = self._run_calibre_ebook_polish(stage1_epub, stage2_epub)

            active_epub_path = stage2_epub if calibre_used else stage1_epub

            # 4. Generér Lexical Pre-Warming Cache og indlejr i META-INF/ordpresse_lexcache.json
            prewarmed_count = 0
            if (
                opts.generate_lexical_cache
                and detected_lang == "da"
                and self.dictionary_service
            ):
                lexical_cache = self._build_lexical_prewarm_cache(word_frequencies)
                prewarmed_count = len(lexical_cache)
                if prewarmed_count > 0:
                    self._embed_metainf_lexcache(active_epub_path, lexical_cache)

            polished_bytes = active_epub_path.read_bytes()

        book_md5 = hashlib.md5(polished_bytes).hexdigest()
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        return LaundryResult(
            epub_bytes=polished_bytes,
            book_md5=book_md5,
            detected_language=detected_lang,
            original_size_kb=orig_kb,
            polished_size_kb=max(1, len(polished_bytes) // 1024),
            paragraphs_healed=paragraphs_healed,
            toc_entries_rebuilt=toc_rebuilt_count,
            images_optimized=images_optimized,
            calibre_polished=calibre_used,
            prewarmed_words_count=prewarmed_count,
            processing_ms=elapsed_ms,
        )

    def _detect_book_language(self, raw_bytes: bytes) -> str:
        """Udtrækker op til 12.000 tegn brødtekst og bestemmer sproget via Lingua."""
        sample_chunks: List[str] = []
        total_len = 0

        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zin:
                for name in zin.namelist():
                    if name.lower().endswith((".xhtml", ".html", ".htm")):
                        raw_html = zin.read(name).decode("utf-8", errors="ignore")
                        clean_txt = re.sub(r"<[^>]+>", " ", ftfy.fix_text(raw_html))
                        clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
                        if len(clean_txt) > 200:
                            sample_chunks.append(clean_txt)
                            total_len += len(clean_txt)
                        if total_len >= 12_000:
                            break
        except Exception as e:
            logger.warning(f"Fejl under sprogudtræk: {e}")

        sample_text = " ".join(sample_chunks)[:12_000]
        if not sample_text:
            return "da"

        try:
            detected = DETECTOR.detect_language_of(sample_text)
            if detected == Language.ENGLISH:
                return "en"
            if detected in (Language.DANISH, Language.BOKMAL):
                return "da"
        except Exception as e:
            logger.debug(f"Lingua detektion fejlede: {e}")

        return "da" if any(c in sample_text.lower() for c in "æøå") else "en"

    def _fix_opf_metadata(self, opf_bytes: bytes, lang: str) -> bytes:
        """Opdaterer OPF-manifest metadata med korrekt sprog og repareret encoding."""
        opf_str = ftfy.fix_text(opf_bytes.decode("utf-8", errors="ignore"))
        if re.search(r"<dc:language[^>]*>.*?</dc:language>", opf_str, re.I | re.S):
            opf_str = re.sub(
                r"<dc:language[^>]*>.*?</dc:language>",
                f"<dc:language>{lang}</dc:language>",
                opf_str,
                flags=re.IGNORECASE | re.DOTALL,
            )
        else:
            opf_str = opf_str.replace(
                "</metadata>", f"  <dc:language>{lang}</dc:language>\n</metadata>"
            )
        return opf_str.encode("utf-8")

    def _sanitize_css_ast(self, css_bytes: bytes) -> bytes:
        """Parser CSS rekursivt (inkl. @media) med tinycss2 og fjerner låst styling."""
        css_text = css_bytes.decode("utf-8", errors="ignore")
        try:
            rules = tinycss2.parse_stylesheet(css_text, skip_comments=True)
            cleaned_css = self._filter_css_rules_recursive(rules)
        except Exception as exc:
            logger.warning(f"CSS parsing fejlede, fallback til rå rensning: {exc}")
            cleaned_css = css_text

        eink_baseline = (
            "\n/* Ordpresse E-Ink Standard v2.0 */\n"
            "body, p { orphans: 2; widows: 2; hyphens: auto; -webkit-hyphens: auto; }\n"
            "img, svg { max-width: 100%; height: auto; }\n"
        )
        return (cleaned_css + eink_baseline).encode("utf-8")

    def _filter_css_rules_recursive(self, rules: list) -> str:
        out_parts: List[str] = []
        for rule in rules:
            if rule.type == "qualified-rule":
                prelude_str = tinycss2.serialize(rule.prelude).strip()
                declarations = tinycss2.parse_declaration_list(rule.content)
                kept_decls = []
                for decl in declarations:
                    if decl.type == "declaration":
                        prop = decl.lower_name
                        # Tillad relative font-størrelser på overskrifter (h1-h6)
                        if prop == "font-size" and re.search(r"\bh[1-6]\b", prelude_str, re.I):
                            val_str = tinycss2.serialize(decl.value).strip()
                            if any(u in val_str for u in ("%", "em", "rem")):
                                kept_decls.append(decl)
                                continue
                        if prop in self.BLOCKED_CSS_PROPS:
                            continue
                    kept_decls.append(decl)

                if any(d.type == "declaration" for d in kept_decls):
                    out_parts.append(f"{prelude_str} {{ {tinycss2.serialize(kept_decls).strip()} }}")

            elif rule.type == "at-rule" and rule.content is not None:
                at_keyword = rule.lower_at_keyword
                if at_keyword in ("media", "supports"):
                    inner_rules = tinycss2.parse_rule_list(rule.content)
                    cleaned_inner = self._filter_css_rules_recursive(inner_rules)
                    if cleaned_inner.strip():
                        prelude_str = tinycss2.serialize(rule.prelude).strip()
                        out_parts.append(f"@{at_keyword} {prelude_str} {{\n{cleaned_inner}\n}}")
                else:
                    out_parts.append(tinycss2.serialize([rule]).strip())
            elif rule.type != "whitespace":
                ser = tinycss2.serialize([rule]).strip()
                if ser:
                    out_parts.append(ser)

        return "\n".join(out_parts)

    def _process_xhtml_chapter(
        self,
        xhtml_bytes: bytes,
        filename: str,
        lang: str,
        opts: LaundryOptions,
        scan_chapters: bool,
        word_freq_out: Dict[str, int],
    ) -> Tuple[bytes, int, List[Tuple[str, str, str]]]:
        raw_str = xhtml_bytes.decode("utf-8", errors="ignore")
        if opts.fix_encoding_and_lang:
            raw_str = ftfy.fix_text(raw_str)

        try:
            soup = BeautifulSoup(raw_str, "lxml-xml")
            if not soup.find("html") and not soup.find("body"):
                soup = BeautifulSoup(raw_str, "lxml")
        except Exception:
            return xhtml_bytes, 0, []

        # 1. Sæt korrekt sprog-attribut på <html> og <body>
        for root_tag_name in ("html", "body"):
            tag = soup.find(root_tag_name)
            if tag and opts.fix_encoding_and_lang:
                tag["xml:lang"] = lang
                tag["lang"] = lang

        # 2. Rens inline style="..." attributter for låste skrifttyper og farver
        if opts.clean_publisher_css:
            for styled_tag in soup.find_all(style=True):
                cleaned_style = self.INLINE_STYLE_BLOCK_RE.sub("", styled_tag["style"]).strip()
                if cleaned_style:
                    styled_tag["style"] = cleaned_style
                else:
                    del styled_tag["style"]

        # 3. Sikker OCR Afsnits-heling (bevarer id-ankre og skåner digte)
        healed_count = 0
        if opts.heal_ocr_paragraphs:
            healed_count = self._heal_broken_paragraphs_in_dom(soup)

        # 4. Scan efter kapitel-overskrifter hvis TOC skal genopbygges
        chapters_found: List[Tuple[str, str, str]] = []
        if scan_chapters:
            chapters_found = self._extract_chapter_anchors(soup, filename)

        # 5. Tilstandsbaseret Bogtrykker-Typografi & Ordfrekvens-opsamling
        self._upgrade_dom_typography_and_collect_words(
            soup, lang=lang, opts=opts, word_freq_out=word_freq_out
        )

        return str(soup).encode("utf-8"), healed_count, chapters_found

    def _heal_broken_paragraphs_in_dom(self, soup: BeautifulSoup) -> int:
        """Sammensmelter afsnit der er knækket midt i en sætning pga. OCR/PDF.

        Bevarer alle interne id-ankre og skåner digte, citater og centreret tekst.
        """
        healed = 0
        paragraphs = soup.find_all("p")
        i = 0
        while i < len(paragraphs) - 1:
            curr_p = paragraphs[i]
            next_p = paragraphs[i + 1]

            if curr_p.parent != next_p.parent:
                i += 1
                continue

            curr_cls = " ".join(curr_p.get("class", [])) if isinstance(curr_p.get("class"), list) else str(curr_p.get("class", ""))
            next_cls = " ".join(next_p.get("class", [])) if isinstance(next_p.get("class"), list) else str(next_p.get("class", ""))

            # Flet aldrig afsnit med forskellige klasser eller poetiske/centrerede klasser
            if curr_cls != next_cls or self.POETIC_OR_SPECIAL_CLASSES.search(curr_cls):
                i += 1
                continue

            curr_txt = curr_p.get_text().strip()
            next_txt = next_p.get_text().strip()

            if len(curr_txt) > 30 and len(next_txt) > 12:
                last_char = curr_txt[-1]
                first_char = next_txt[0]
                is_broken = (last_char.islower() or last_char in (",", ";", "-")) and first_char.islower()
                if is_broken:
                    # Bevar id-anker fra next_p, så TOC og krydsreferencer aldrig brydes
                    next_id = next_p.get("id")
                    if next_id:
                        anchor_span = soup.new_tag("span", id=next_id)
                        curr_p.append(anchor_span)

                    if last_char == "-":
                        # Fjern bindestreg fra orddelt linjeskift
                        # Find den sidste tekstnode i curr_p og strip det afsluttende bindestregstegn
                        for descendant in reversed(list(curr_p.descendants)):
                            if type(descendant) is NavigableString and descendant.strip().endswith("-"):
                                descendant.replace_with(NavigableString(str(descendant).rstrip().rstrip("-")))
                                break
                    else:
                        curr_p.append(" ")

                    for child in list(next_p.contents):
                        curr_p.append(child)
                    next_p.decompose()
                    paragraphs.pop(i + 1)
                    healed += 1
                    continue
            i += 1
        return healed

    def _upgrade_dom_typography_and_collect_words(
        self,
        soup: BeautifulSoup,
        lang: str,
        opts: LaundryOptions,
        word_freq_out: Dict[str, int],
    ) -> None:
        """Kører tilstandsbaseret anførselstegn-konvertering på blok-niveau.

        Sikrer at citater hen over <em> og <span> parres korrekt uden at ramme kommentarer.
        """
        if lang == "da":
            open_q, close_q = ("»", "«") if opts.danish_quote_style == "guillemets" else ("„", "“")
        else:
            open_q, close_q = ("“", "”")

        candidate_blocks = soup.find_all(["p", "blockquote", "li", "h1", "h2", "h3", "h4", "h5", "h6"])
        block_tags = list(candidate_blocks)
        for d in soup.find_all("div"):
            if not d.find(["p", "blockquote", "li", "div"]):
                block_tags.append(d)

        visited_nodes: Set[int] = set()

        for block in block_tags:
            in_double_quote = False
            for node in list(block.descendants):
                if type(node) is not NavigableString or id(node) in visited_nodes:
                    continue
                if isinstance(node, IGNORED_STRING_TYPES):
                    continue
                if node.parent and node.parent.name in ("script", "style", "pre", "code"):
                    continue

                visited_nodes.add(id(node))
                txt = str(node)
                if not txt:
                    continue

                # Opsaml danske ord til Lexical Pre-Warming
                if lang == "da" and opts.generate_lexical_cache:
                    for word in re.findall(r"\b[a-zæøåA-ZÆØÅ]{5,}\b", txt):
                        w_lower = word.lower()
                        word_freq_out[w_lower] = word_freq_out.get(w_lower, 0) + 1

                if opts.upgrade_typography:
                    upgraded, in_double_quote = self._transform_text_node_stateful(
                        txt,
                        lang=lang,
                        open_q=open_q,
                        close_q=close_q,
                        in_quote_state=in_double_quote,
                    )
                    if upgraded != txt:
                        node.replace_with(NavigableString(upgraded))

    def _transform_text_node_stateful(
        self,
        text: str,
        lang: str,
        open_q: str,
        close_q: str,
        in_quote_state: bool,
    ) -> Tuple[str, bool]:
        """Transformer tekst med typografisk konvertering af anførselstegn, tankestreger og forkortelser."""
        # 1. Udskift lige citationstegn (") tilstandsbaseret
        if '"' in text:
            chars = list(text)
            for idx, ch in enumerate(chars):
                if ch == '"':
                    chars[idx] = close_q if in_quote_state else open_q
                    in_quote_state = not in_quote_state
            text = "".join(chars)

        # 2. Ellipser og tal-intervaller
        text = text.replace("...", "…")
        text = re.sub(r"(?<=\d)-(?=\d)", "–", text)

        # 3. Sprog-specifikke tankestreger og forkortelser
        if lang == "da":
            text = re.sub(r"\s+--?\s+", "\u00a0– ", text)
            text = self.DA_ABBREVIATIONS.sub(r"\1." + "\u00a0", text)
        else:
            text = re.sub(r"\s+--\s+", "—", text)
            text = self.EN_ABBREVIATIONS.sub(r"\1." + "\u00a0", text)

        # 4. Krøllede apostroffer
        text = re.sub(r"(?<=\w)'(?=\w|\s|$)", "’", text)
        return text, in_quote_state

    def _check_if_toc_is_sparse(self, zin: zipfile.ZipFile) -> bool:
        """Returnerer True hvis toc.ncx mangler eller har færre end 2 navPoints."""
        has_ncx = False
        for name in zin.namelist():
            if name.lower().endswith(".ncx"):
                has_ncx = True
                ncx_str = zin.read(name).decode("utf-8", errors="ignore")
                nav_points = re.findall(r"<navPoint\b", ncx_str, re.IGNORECASE)
                if len(nav_points) >= 2:
                    return False
        return True

    def _extract_chapter_anchors(
        self, soup: BeautifulSoup, filename: str
    ) -> List[Tuple[str, str, str]]:
        """Finder kapiteloverskrifter i et kapitel og sikrer at de har et id-anker."""
        found: List[Tuple[str, str, str]] = []
        candidates = soup.find_all(["h1", "h2", "p"])
        for idx, tag in enumerate(candidates):
            txt = re.sub(r"\s+", " ", tag.get_text()).strip()
            if not txt or len(txt) > 90:
                continue

            is_heading_tag = tag.name in ("h1", "h2")
            is_chapter_match = bool(self.CHAPTER_HEADING_RE.match(txt))
            if is_heading_tag or is_chapter_match:
                anchor_id = tag.get("id")
                if not anchor_id:
                    anchor_id = f"ordpresse_chap_{idx + 1}"
                    tag["id"] = anchor_id
                found.append((filename, str(anchor_id), txt))
        return found

    def _inject_rebuilt_toc_ncx(
        self, epub_path: Path, chapters: List[Tuple[str, str, str]]
    ) -> int:
        """Opdaterer eller injicerer toc.ncx med de fundne kapitel-ankre."""
        tmp_out = epub_path.with_suffix(".toc.epub")
        rebuilt_count = 0

        with zipfile.ZipFile(epub_path, "r") as zin:
            ncx_name = next((n for n in zin.namelist() if n.lower().endswith(".ncx")), None)
            opf_name = next((n for n in zin.namelist() if n.lower().endswith(".opf")), None)

            if not ncx_name:
                # Opret standard toc.ncx i samme mappe som OPF-filen
                base_dir = str(Path(opf_name).parent) if opf_name and str(Path(opf_name).parent) != "." else ""
                ncx_name = f"{base_dir}/toc.ncx" if base_dir else "toc.ncx"

            ncx_dir = str(Path(ncx_name).parent)
            with zipfile.ZipFile(tmp_out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                if "mimetype" in zin.namelist():
                    zout.writestr("mimetype", zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)

                ncx_written = False
                for item in zin.infolist():
                    if item.filename == "mimetype":
                        continue
                    data = zin.read(item.filename)
                    if item.filename == ncx_name:
                        ncx_soup = BeautifulSoup(data.decode("utf-8", errors="ignore"), "lxml-xml")
                        nav_map = ncx_soup.find("navMap")
                        if not nav_map:
                            nav_map = ncx_soup.new_tag("navMap")
                            if ncx_soup.find("ncx"):
                                ncx_soup.find("ncx").append(nav_map)
                        if nav_map:
                            nav_map.clear()
                            for idx, (chap_file, anchor_id, title) in enumerate(chapters, start=1):
                                rel_src = chap_file
                                if ncx_dir != "." and chap_file.startswith(ncx_dir + "/"):
                                    rel_src = chap_file[len(ncx_dir) + 1:]
                                nav_pt = ncx_soup.new_tag("navPoint", id=f"navPoint-{idx}", playOrder=str(idx))
                                nav_lbl = ncx_soup.new_tag("navLabel")
                                txt_tag = ncx_soup.new_tag("text")
                                txt_tag.string = title
                                nav_lbl.append(txt_tag)
                                content_tag = ncx_soup.new_tag("content", src=f"{rel_src}#{anchor_id}")
                                nav_pt.append(nav_lbl)
                                nav_pt.append(content_tag)
                                nav_map.append(nav_pt)
                            rebuilt_count = len(chapters)
                            data = str(ncx_soup).encode("utf-8")
                            ncx_written = True
                    zout.writestr(item.filename, data)

                if not ncx_written:
                    # Byg helt ny toc.ncx
                    new_ncx_content = [
                        '<?xml version="1.0" encoding="utf-8"?>',
                        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
                        '<head><meta name="dtb:uid" content="urn:uuid:ordpresse"/></head>',
                        '<docTitle><text>Indholdsfortegnelse</text></docTitle>',
                        '<navMap>',
                    ]
                    for idx, (chap_file, anchor_id, title) in enumerate(chapters, start=1):
                        rel_src = chap_file
                        if ncx_dir != "." and chap_file.startswith(ncx_dir + "/"):
                            rel_src = chap_file[len(ncx_dir) + 1:]
                        new_ncx_content.append(
                            f'  <navPoint id="navPoint-{idx}" playOrder="{idx}">'
                            f'<navLabel><text>{title}</text></navLabel>'
                            f'<content src="{rel_src}#{anchor_id}"/></navPoint>'
                        )
                    new_ncx_content.append('</navMap></ncx>')
                    zout.writestr(ncx_name, "\n".join(new_ncx_content).encode("utf-8"))
                    rebuilt_count = len(chapters)

        tmp_out.replace(epub_path)
        return rebuilt_count

    def _optimize_image_preserve_format(
        self, img_bytes: bytes, filename: str, is_cover: bool = False
    ) -> Tuple[bytes, bool]:
        """Optimerer billeder til E-Ink uden at ændre filformat/MIME-type!

        JPEG forbliver JPEG; PNG forbliver 8-bit/optimeret PNG.
        """
        max_w = 1200 if is_cover else 1000
        size_threshold = 300_000 if is_cover else 200_000

        if len(img_bytes) < size_threshold:
            return img_bytes, False

        is_png = filename.endswith(".png")
        try:
            with Image.open(io.BytesIO(img_bytes)) as img:
                orig_w, orig_h = img.width, img.height
                if orig_w > max_w:
                    ratio = max_w / float(orig_w)
                    img = img.resize(
                        (max_w, int(orig_h * ratio)), Image.Resampling.LANCZOS
                    )

                out = io.BytesIO()
                if is_png:
                    # Bevar PNG-format, kvantiser til gråtoner/256 farver
                    if img.mode not in ("L", "P"):
                        img = img.quantize(colors=64)
                    img.save(out, format="PNG", optimize=True)
                else:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    if is_cover:
                        img = ImageOps.autocontrast(img, cutoff=0.5)
                    img.save(out, format="JPEG", quality=82, optimize=True)

                result_bytes = out.getvalue()
                if len(result_bytes) < len(img_bytes):
                    return result_bytes, True
        except Exception as exc:
            logger.debug(f"Kunne ikke optimere billede {filename}: {exc}")

        return img_bytes, False

    def _run_calibre_ebook_polish(self, in_epub: Path, out_epub: Path) -> bool:
        """Kører Calibres ebook-polish CLI til EPUB 3 opgradering og font-subsetting."""
        if not self.has_calibre:
            return False
        cmd = [
            "ebook-polish",
            "--upgrade-book",
            "--subset-fonts",
            "--remove-unused-css",
            str(in_epub),
            str(out_epub),
        ]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, check=False
            )
            return res.returncode == 0 and out_epub.exists()
        except Exception as exc:
            logger.warning(f"ebook-polish fejlede eller timede ud: {exc}")
            return False

    def _build_lexical_prewarm_cache(
        self, word_frequencies: Dict[str, int], max_entries: int = 400
    ) -> Dict[str, dict]:
        """Finder lavfrekvente/ældre gloser i bogen og slår dem op i Ordpresse-databasen."""
        candidates = [
            w for w, count in word_frequencies.items() if 1 <= count <= 3 and len(w) >= 6
        ]
        cache: Dict[str, dict] = {}
        for word in candidates[:max_entries]:
            if hasattr(self.dictionary_service, "lookup_historical_only"):
                entry = self.dictionary_service.lookup_historical_only(word)
            elif hasattr(self.dictionary_service, "lookup"):
                entry = self.dictionary_service.lookup(word)
            else:
                entry = None
            if entry:
                cache[word] = entry
        return cache

    def _embed_metainf_lexcache(
        self, epub_path: Path, lexical_cache: Dict[str, dict]
    ) -> None:
        """Indlejrer den præ-varmede ordbogscache i `META-INF/ordpresse_lexcache.json`.

        Filer i META-INF/ kræver ikke registrering i content.opf og består epubcheck.
        """
        payload = json.dumps(lexical_cache, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tmp_epub = epub_path.with_suffix(".lex.epub")
        with zipfile.ZipFile(epub_path, "r") as zin, zipfile.ZipFile(
            tmp_epub, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            if "mimetype" in zin.namelist():
                zout.writestr("mimetype", zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)

            for item in zin.infolist():
                if item.filename in ("mimetype", "META-INF/ordpresse_lexcache.json"):
                    continue
                zout.writestr(item.filename, zin.read(item.filename))

            zout.writestr("META-INF/ordpresse_lexcache.json", payload)

        tmp_epub.replace(epub_path)
