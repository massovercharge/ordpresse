"""Tests for cache persistence, queue de-duplication, and aa-å orthographic normalization.

Ensures that words already in cache.sqlite3 or not_found_words never leak into the scrape queue
upon repeated builds of the same books.
"""

import pytest
from unittest.mock import MagicMock
from ordpresse.sources.ods_scraper import is_valid_definition, parse_ods_html
from ordpresse.triage import is_clean_keep_row


def test_is_valid_definition_allows_short_concise_senses_and_quote_ellipses():
    """Ensure concise definitions and literary quotes with '..' are not rejected as citations."""
    # Concise definitions must be accepted
    assert is_valid_definition("biskoppelig.")
    assert is_valid_definition("fagudtryk; faglig betegnelse.")
    assert is_valid_definition("veksle valuta.")
    assert is_valid_definition("foretage checkclearing.")
    assert is_valid_definition("2 leggvarmere")
    assert is_valid_definition("hang til stærke drikke; drikfældighed.")

    # Quotes with '..' (ellipses) inside must be accepted
    assert is_valid_definition(
        "(egl.: bekendtgøre prisen paa noget, jf. »at krympe sig ved at modtage Gæstfrihed.. viser, at man anslaar den i Penge.« (Georg Brandes)"
    )

    # Pure author signatures or bibliographic citations must still be rejected
    assert not is_valid_definition("Anna Larssen Bjørner")
    assert not is_valid_definition("H.F. Feilberg")
    assert not is_valid_definition("Goldschm.. Aakj..")
    assert not is_valid_definition("L. Holberg Ep..")
    assert not is_valid_definition("Romneya Coulteri.")


def test_is_clean_keep_row_accepts_valid_entries():
    """Ensure entries with valid definitions are not purged by triage."""
    html_anslaa = (
        '<div><b>anslaa</b> <i>vb.</i> <small>(anslå; ældre: -slog;)</small></div>'
        '<div><small><b>HISTORISK BETYDNING (ODS / 1700–1950)</b></small><ol>'
        '<li><i>(hist.)</i> slaa paa ell. imod noget.</li>'
        '<li><i>(hist.)</i> egl.: aabne ved slag.</li>'
        '<li><i>(hist.)</i> egl.: slaa fast (paa noget); fæste ved slag (ell. paa lign. maade).</li>'
        '<li><i>(hist.)</i> (egl.: bekendtgøre prisen paa noget, jf.<br>'
        '<small><i>»at krympe sig ved at modtage Gæstfrihed.. viser, at man anslaar den i Penge.«</i> (Georg Brandes)</small></li>'
        '</ol></div><div><small style="color:#666;">Kilde: ODS</small></div>'
    )
    assert is_clean_keep_row("anslå", "anslaa", html_anslaa)

    html_episkopal = (
        '<div><b>episkopal</b> <i>adj.</i></div>'
        '<div><small><b>MODERNE BETYDNING (DDO)</b></small><ol>'
        '<li>biskoppelig.</li>'
        '</ol></div><div><small style="color:#666;">Kilde: DDO</small></div>'
    )
    assert is_clean_keep_row("episkopal", "episkopal", html_episkopal)


def test_parse_ods_html_preserves_articles_despite_mente_du_in_sidebar():
    """Ensure ODS parser does not discard real articles because ordnet.dk has 'Mente du:' or 'ORD I NÆRHEDEN' in sidebar."""
    raw_html = '''
    <html>
      <body>
        <div class="header">
          <a href="//ordnet.dk/ddo">Den Danske Ordbog</a>
          <a href="//ordnet.dk/ods">Ordbog over det danske Sprog</a>
        </div>
        <div class="main">
          <div class="artikel">
            <span class="match">anslaa</span>
            <span class="allow-glossing">vb.</span>
            <div class="definition">
              <span class="def">slaa paa ell. imod noget.</span>
            </div>
          </div>
        </div>
        <div class="sidebar">
          <p>Mente du: anslag, anslåning</p>
          <p>ORD I NÆRHEDEN: slaa, ramme</p>
        </div>
      </body>
    </html>
    '''
    res = parse_ods_html("anslaa", raw_html)
    assert res is not None
    assert res["headword"] == "anslaa"
    assert "slaa paa ell. imod noget" in res["html_content"]


@pytest.mark.anyio
async def test_submit_cache_handles_ods_and_expands_negative_cache(tmp_path, monkeypatch):
    """Test that submitting an ODS HTML result caches it properly, and negative-cache stores aa-å variants."""
    import ordpresse.web.app
    from ordpresse.db import Database
    from ordpresse.web.app import submit_cache, CacheSubmitPayload

    cache_db = tmp_path / "cache.sqlite3"
    datasets_db = tmp_path / "datasets.sqlite3"

    db = Database(datasets_path=datasets_db, cache_path=cache_db)
    monkeypatch.setattr(ordpresse.web.app, "Database", lambda: db)

    # 1. Submit valid ODS HTML
    raw_html = '''
    <div class="artikel">
      <span class="match">forvildelse</span>
      <span class="allow-glossing">sb.</span>
      <div class="definition"><span class="def">handling, hvorved man forvilder sig.</span></div>
    </div>
    '''
    payload = CacheSubmitPayload(word="forvildelse", html=raw_html, build_id="test_build")
    res = await submit_cache(payload)
    assert res["status"] == "ok"

    cached = db.get_cached("forvildelse")
    assert cached is not None
    assert cached["source"] == "ods"
    assert cached["headword"] == "forvildelse"

    # 2. Submit non-existent page -> triggers negative cache with aa <-> å expansion
    empty_html = '<html><body><h1>Intet resultat</h1></body></html>'
    neg_payload = CacheSubmitPayload(word="anslaa", html=empty_html, build_id="test_build")
    res_neg = await submit_cache(neg_payload)
    assert res_neg["status"] == "ok"

    nf = db.get_not_found_words()
    assert "anslaa" in nf
    assert "anslå" in nf  # aa <-> å variant auto-inserted


def test_pipeline_aa_ao_and_cached_words_not_requeued(tmp_path):
    """Test that words already in cache or matching aa-å variants are not put in missing words queue."""
    from ordpresse.db import Database
    from ordpresse.pipeline import Pipeline
    from ordpresse.web.app import coordinator

    cache_db = tmp_path / "cache.sqlite3"
    datasets_db = tmp_path / "datasets.sqlite3"

    db = Database(datasets_path=datasets_db, cache_path=cache_db)

    # 1. Put an entry in cache under 'anslå'
    db.set_cached(
        word="anslå",
        source="ods",
        headword="anslaa",
        html_content="<div><b>anslaa</b> <i>vb.</i><ol><li>slaa paa noget.</li></ol></div>",
        synonyms=["anslog", "anslaa"],
    )

    # 2. Put a word in not_found_words under 'ukendtord'
    db.set_not_found("ukendtord")

    # Run pipeline with custom words that include 'anslaa' (cached as anslå) and 'ukendtord' (in not_found)
    build_id = "test_queue_b1"
    pipeline = Pipeline(db=db, output_dir=tmp_path / "output")

    # Custom tokens
    pipeline.run(
        custom_words=["anslaa", "ukendtord"],
        full_dictionary=False,
        allow_network=False,
        build_id=build_id,
    )

    # Ensure neither anslå nor ukendtord are pending in coordinator queue
    pending = coordinator.get_pending_words(build_id)
    assert "anslaa" not in pending
    assert "anslå" not in pending
    assert "ukendtord" not in pending
