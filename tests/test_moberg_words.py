"""End-to-end regression suite verifying the 5 Moberg benchmark words in a simulated EPUB."""

from pathlib import Path
import zipfile
import pytest

from ordpresse.pipeline import Pipeline
from ordpresse.stardict_writer import StarDictReader


def create_simulated_moberg_epub(dest_path: Path) -> Path:
    """Create a minimal valid EPUB containing the 5 Moberg benchmark words."""
    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Moberg Test Roman</dc:title>
    <dc:identifier id="pub-id">urn:uuid:12345</dc:identifier>
    <dc:language>da</dc:language>
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>"""

    # Text containing all 5 Moberg benchmark words with soft-hyphens and historical contexts
    chapter1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Kapitel 1</title></head>
<body>
  <h1>Kapitel 1: På den gamle hede</h1>
  <p>
    I sognet vandrede de udstødte <em>lægds&shy;lemmer</em> fra gård til gård for at modtage deres nådsensbrød.
    Præsten slog op i sognets ældgamle <strong>jordebog</strong> for at efterse tiendeafgifterne.
    Langs grusvejen nærmede sig en omrejsende <strong>bissekræmmer</strong> med sin tunge kramkiste på ryggen.
    Hjemme i den fugtige stue sad den <strong>værkbrudne</strong> fader med gigt i alle lemmer,
    og han mindedes dengang, en ko kostede fyrretyve rigsdaler <strong>banco</strong>.
  </p>
</body>
</html>"""

    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/chapter1.xhtml", chapter1_xhtml)

    return dest_path


def test_full_pipeline_moberg_words_regression(tmp_path: Path):
    """End-to-end pipeline test on a simulated EPUB with the 5 Moberg words."""
    epub_file = tmp_path / "moberg_test.epub"
    create_simulated_moberg_epub(epub_file)

    output_dir = tmp_path / "output"
    pipeline = Pipeline(output_dir=output_dir, bookname="Moberg StarDict Test")

    # Run full build pipeline using built-in offline seeds (no external network needed)
    result = pipeline.run(
        epub_files=[epub_file],
        include_seeds=True,
        allow_network=False,
    )

    assert result.entry_count >= 5
    assert result.zip_path.exists()
    assert result.files["ifo"].exists()
    assert result.files["idx"].exists()
    assert result.files["syn"].exists()

    # Open with StarDictReader and test lookup of all 5 surface strings
    reader = StarDictReader(result.files["ifo"])

    # 1. Surface word: "lægdslemmer" (plural of lægdslem)
    def_laegdslemmer = reader.lookup("lægdslemmer")
    assert def_laegdslemmer is not None, "Lookup for 'lægdslemmer' failed!"
    assert any(term in def_laegdslemmer.lower() for term in ["fattiglem", "lægd", "forsørgelse"])

    # 2. Surface word: "jordebog"
    def_jordebog = reader.lookup("jordebog")
    assert def_jordebog is not None, "Lookup for 'jordebog' failed!"
    assert any(term in def_jordebog.lower() for term in ["jordebog", "ejendom", "gods"])

    # 3. Surface word: "bissekræmmer"
    def_bissekraemmer = reader.lookup("bissekræmmer")
    assert def_bissekraemmer is not None, "Lookup for 'bissekræmmer' failed!"
    assert any(term in def_bissekraemmer.lower() for term in ["kræmmer", "handelsmand", "kramkiste"])

    # 4. Surface word: "værkbrudne" (inflection of værkbrudden)
    def_vaerkbrudne = reader.lookup("værkbrudne")
    assert def_vaerkbrudne is not None, "Lookup for 'værkbrudne' failed!"
    assert any(term in def_vaerkbrudne.lower() for term in ["svækket", "lam", "gigt", "værkbrudden"])

    # 5. Surface word: "banco"
    def_banco = reader.lookup("banco")
    assert def_banco is not None, "Lookup for 'banco' failed!"
    assert any(term in def_banco.lower() for term in ["banco", "banko", "møntfod", "kurantbanken"])
