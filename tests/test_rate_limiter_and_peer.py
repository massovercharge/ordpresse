"""Unit & integration tests for rate limiter, peer bookmarklet submit, and queue skipping."""

import asyncio
import time
import pytest
from fastapi.testclient import TestClient

from ordpresse.db import Database
from ordpresse.sources.rate_limiter import ScrapeCoordinator, coordinator
from ordpresse.web.app import app


@pytest.mark.anyio
async def test_token_bucket_burst_and_rate_limit():
    """Verify ScrapeCoordinator allows immediate burst up to capacity, then enforces waiting."""
    coord = ScrapeCoordinator()
    coord.burst_capacity = 3
    coord.tokens = 3.0
    coord.refill_interval = 100.0  # Long interval to test depletion

    build_id = "test_burst_b"
    words = ["word1", "word2", "word3", "word4"]
    coord.register_build(build_id, words)

    # 3 tokens available -> first 3 must succeed immediately
    assert await coord.acquire_token_or_peer_resolve(build_id, "word1") is True
    assert await coord.acquire_token_or_peer_resolve(build_id, "word2") is True
    assert await coord.acquire_token_or_peer_resolve(build_id, "word3") is True

    # 4th acquisition: tokens depleted (< 1.0)
    assert coord.tokens < 1.0

    waited = False

    def on_wait(wait_s: float, pending: int):
        nonlocal waited
        waited = True
        assert wait_s > 0
        assert pending >= 1
        # Refill token so test finishes promptly
        coord.tokens = 1.0

    success = await coord.acquire_token_or_peer_resolve(build_id, "word4", on_wait_callback=on_wait)
    assert waited is True
    assert success is True
    coord.unregister_build(build_id)


@pytest.mark.anyio
async def test_peer_cache_submit_wakes_waiting_build():
    """Verify POST /api/cache/submit stores cleaned HTML, updates missing words, and wakes waiting build."""
    client = TestClient(app)
    build_id = "test_peer_b"
    word = "uefterrettelig"

    coordinator.register_build(build_id, [word])
    coordinator.tokens = 0.0
    coordinator.last_refill = time.monotonic()
    coordinator.refill_interval = 1000.0

    sample_ods_html = """
    <div class="artikel">
        <span class="match">uefterrettelig</span>
        <span class="tekstmedium">adj.</span>
        <div class="betydning">
            upålidelig, utilregnelig; som man ikke kan stole paa.
        </div>
    </div>
    """

    start_time = time.monotonic()

    # Start waiting task
    task = asyncio.create_task(
        coordinator.acquire_token_or_peer_resolve(build_id, word)
    )

    # Allow task to enter wait loop
    await asyncio.sleep(0.05)

    # Concurrently submit via API
    resp = client.post(
        "/api/cache/submit",
        json={"word": word, "html": sample_ods_html, "build_id": build_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["word"] == word
    assert data["remaining"] == 0

    # Wait for task to wake up
    result = await asyncio.wait_for(task, timeout=0.5)
    elapsed = time.monotonic() - start_time

    # a) Verify definition stored in cache.sqlite3
    db = Database()
    cached = db.get_cached_definition(word)
    assert cached is not None
    assert "upålidelig" in cached["html_content"]

    # b) Verify missing-words drops word
    get_res = client.get(f"/api/build/{build_id}/missing-words")
    assert get_res.status_code == 200
    missing_data = get_res.json()
    assert missing_data["missing_count"] == 0
    assert word not in missing_data["words"]

    # Also test job alias
    job_alias_res = client.get(f"/api/jobs/{build_id}/missing-words")
    assert job_alias_res.status_code == 200
    assert job_alias_res.json()["missing_count"] == 0

    # c) Task woke up quickly and returned False (0 server tokens spent)
    assert elapsed < 0.3
    assert result is False

    coordinator.unregister_build(build_id)


@pytest.mark.anyio
async def test_skip_queue_endpoint():
    """Verify POST /api/build/{build_id}/skip-queue sets flag and immediately releases waiting task."""
    client = TestClient(app)
    build_id = "test_skip_b"
    word = "sjaeldentord"

    coordinator.register_build(build_id, [word])
    coordinator.tokens = 0.0
    coordinator.last_refill = time.monotonic()
    coordinator.refill_interval = 1000.0

    task = asyncio.create_task(
        coordinator.acquire_token_or_peer_resolve(build_id, word)
    )

    await asyncio.sleep(0.05)

    resp = client.post(f"/api/build/{build_id}/skip-queue")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipping"

    result = await asyncio.wait_for(task, timeout=0.5)
    assert result is False
    assert coordinator.should_skip_remaining(build_id) is True

    coordinator.unregister_build(build_id)


def test_cors_preflight_and_headers():
    """Verify CORS headers are present for ordnet.dk cross-origin requests."""
    client = TestClient(app)
    resp = client.options(
        "/api/cache/submit",
        headers={
            "Origin": "https://ordnet.dk",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


@pytest.mark.anyio
async def test_bookmarklet_mode_does_not_consume_tokens():
    """Verify that in bookmarklet mode the server does not consume scrape tokens and waits for peer/mode change."""
    coord = ScrapeCoordinator()
    coord.burst_capacity = 5
    coord.tokens = 5.0
    build_id = "test_bmark_mode_b"
    word = "pauseord"

    coord.register_build(build_id, [word], initial_mode="bookmarklet")

    task = asyncio.create_task(
        coord.acquire_token_or_peer_resolve(build_id, word)
    )

    # Let task run into bookmarklet wait loop
    await asyncio.sleep(0.05)

    # Tokens must remain completely untouched!
    assert coord.tokens == 5.0

    # Switch mode to server
    coord.set_build_mode(build_id, "server")

    # Now task should acquire 1 token and return True
    result = await asyncio.wait_for(task, timeout=0.5)
    assert result is True
    assert coord.tokens == 4.0

    coord.unregister_build(build_id)


def test_start_server_scrape_endpoint():
    """Verify POST /api/build/{build_id}/start-server-scrape switches mode to server."""
    client = TestClient(app)
    build_id = "test_switch_b"
    coordinator.register_build(build_id, ["testord"], initial_mode="bookmarklet")

    resp = client.post(f"/api/build/{build_id}/start-server-scrape")
    assert resp.status_code == 200
    assert resp.json()["status"] == "server_scraping"
    assert coordinator.get_build_mode(build_id) == "server"

    coordinator.unregister_build(build_id)
