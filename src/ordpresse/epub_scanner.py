"""In-memory EPUB token extractor with soft-hyphen sanitization and line-break rejoining."""

from collections import Counter
import io
from pathlib import Path
from typing import BinaryIO, List, Set, Union
import zipfile
from bs4 import BeautifulSoup

from ordpresse.orthography import extract_word_tokens, sanitize_text


class EpubScanner:
    """Scans EPUB files, extracting clean Danish word tokens."""

    def __init__(self, count_frequencies: bool = True):
        self.count_frequencies = count_frequencies

    def scan_file(self, epub_source: Union[Path, str, BinaryIO, bytes]) -> List[str]:
        """Scan EPUB and return unique lowercased word tokens."""
        if isinstance(epub_source, (str, Path)):
            zf = zipfile.ZipFile(str(epub_source), "r")
        elif isinstance(epub_source, bytes):
            zf = zipfile.ZipFile(io.BytesIO(epub_source), "r")
        else:
            zf = zipfile.ZipFile(epub_source, "r")

        tokens_counter: Counter = Counter()

        try:
            for item in zf.infolist():
                filename = item.filename.lower()
                # Process HTML, XHTML, XML content files
                if filename.endswith((".xhtml", ".html", ".htm", ".xml")) and not filename.endswith("container.xml"):
                    try:
                        content_bytes = zf.read(item)
                        # Try decoding utf-8
                        try:
                            html_text = content_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            html_text = content_bytes.decode("latin-1", errors="replace")

                        # Strip HTML tags
                        soup = BeautifulSoup(html_text, "html.parser")
                        # Remove script/style tags
                        for tag in soup(["script", "style", "head", "title"]):
                            tag.decompose()
                        raw_text = soup.get_text(separator=" ")

                        # Extract tokens using orthography sanitization rules
                        file_tokens = extract_word_tokens(raw_text)
                        tokens_counter.update(file_tokens)
                    except Exception:
                        continue
        finally:
            zf.close()

        # Sort tokens alphabetically (or by frequency if needed)
        unique_tokens = sorted(tokens_counter.keys())
        return unique_tokens


def scan_epub_tokens(epub_source: Union[Path, str, BinaryIO, bytes]) -> List[str]:
    """Helper function to quickly extract unique tokens from an EPUB."""
    scanner = EpubScanner()
    return scanner.scan_file(epub_source)
