"""Global token bucket rate limiter and fair-share queue coordinator for ODS scraping."""

import asyncio
from collections import deque
import logging
import os
import time
from typing import Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ScrapeCoordinator:
    def __init__(self) -> None:
        self.max_per_hour: int = int(os.getenv("ORDPRESSE_MAX_HOURLY_REQUESTS", "200"))
        self.burst_capacity: int = int(os.getenv("ORDPRESSE_BURST_TOKENS", "10"))
        self.refill_interval: float = 3600.0 / max(1, self.max_per_hour)  # 18.0s at 200/hr
        self.tokens: float = float(self.burst_capacity)
        self.last_refill: float = time.monotonic()
        self.__lock: Optional[asyncio.Lock] = None

        # Active builds tracking: build_id -> list of remaining missing words
        self.missing_by_build: Dict[str, List[str]] = {}
        self.resolved_words: Dict[str, Set[str]] = {}
        self.skip_remaining_flags: Set[str] = set()
        self.word_resolved_events: Dict[str, asyncio.Event] = {}

        # Round-robin queue of build_ids waiting for a server scrape token
        self._rr_queue: deque[str] = deque()

    @property
    def _lock(self) -> asyncio.Lock:
        if self.__lock is None:
            self.__lock = asyncio.Lock()
        return self.__lock

    @_lock.setter
    def _lock(self, val: Optional[asyncio.Lock]) -> None:
        self.__lock = val

    def register_build(self, build_id: str, missing_words: List[str]) -> None:
        """Register a build with its list of missing words requiring ODS resolution."""
        unique_words = list(dict.fromkeys(w.strip() for w in missing_words if w.strip()))
        self.missing_by_build[build_id] = unique_words
        self.resolved_words[build_id] = set()
        self.word_resolved_events[build_id] = asyncio.Event()
        if build_id not in self._rr_queue:
            self._rr_queue.append(build_id)

    def unregister_build(self, build_id: str) -> None:
        """Clean up all tracking structures for a completed/cancelled build."""
        self.missing_by_build.pop(build_id, None)
        self.resolved_words.pop(build_id, None)
        self.skip_remaining_flags.discard(build_id)
        self.word_resolved_events.pop(build_id, None)
        self._rr_queue = deque(b for b in self._rr_queue if b != build_id)

    def get_pending_words(self, build_id: str) -> List[str]:
        """Return the list of words still awaiting resolution for a specific build."""
        all_missing = self.missing_by_build.get(build_id, [])
        done = self.resolved_words.get(build_id, set())
        done_lower = {w.lower() for w in done}
        return [w for w in all_missing if w not in done and w.lower() not in done_lower]

    def mark_word_resolved(self, word: str, build_id: Optional[str] = None) -> None:
        """Mark a word as resolved across builds (or for a specific build) and wake waiting loops."""
        w_low = word.strip().lower()
        target_bids = (
            [build_id]
            if build_id and build_id in self.missing_by_build
            else list(self.missing_by_build.keys())
        )
        for bid in target_bids:
            words = self.missing_by_build.get(bid, [])
            matched = False
            for candidate in words:
                if candidate.lower() == w_low:
                    self.resolved_words.setdefault(bid, set()).add(candidate)
                    matched = True
            if matched or bid == build_id:
                self.resolved_words.setdefault(bid, set()).add(word.strip())
            if bid in self.word_resolved_events:
                self.word_resolved_events[bid].set()

        if build_id and build_id not in target_bids:
            self.resolved_words.setdefault(build_id, set()).add(word.strip())
            if build_id in self.word_resolved_events:
                self.word_resolved_events[build_id].set()

    def trigger_skip_remaining(self, build_id: str) -> None:
        """Signal that a build should skip any remaining scrape queue and proceed directly to compilation."""
        self.skip_remaining_flags.add(build_id)
        if build_id in self.word_resolved_events:
            self.word_resolved_events[build_id].set()

    def should_skip_remaining(self, build_id: str) -> bool:
        """Check if user requested to skip the remaining queue for this build."""
        return build_id in self.skip_remaining_flags

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        added = elapsed / self.refill_interval
        if added > 0:
            self.tokens = min(float(self.burst_capacity), self.tokens + added)
            self.last_refill = now

    async def acquire_token_or_peer_resolve(
        self,
        build_id: str,
        word: str,
        on_wait_callback: Optional[Callable[[float, int], Awaitable[None]]] = None,
    ) -> bool:
        """
        Returns True if the server should scrape `word` now.
        Returns False if `word` was already resolved by the peer bookmarklet OR the user clicked skip.
        """
        w_low = word.strip().lower()
        while True:
            if self.should_skip_remaining(build_id):
                return False

            resolved = self.resolved_words.get(build_id, set())
            if word in resolved or any(r.lower() == w_low for r in resolved):
                return False

            wait_seconds = 0.0
            can_scrape = False

            async with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    # Fair-share check: if multiple builds waiting, respect order
                    if not self._rr_queue or self._rr_queue[0] == build_id:
                        if self._rr_queue and self._rr_queue[0] == build_id:
                            self._rr_queue.popleft()
                        self.tokens -= 1.0
                        can_scrape = True
                    else:
                        wait_seconds = min(0.2, self.refill_interval)
                else:
                    if build_id not in self._rr_queue:
                        self._rr_queue.append(build_id)
                    wait_seconds = (1.0 - self.tokens) * self.refill_interval

            if can_scrape:
                return True

            pending_count = len(self.get_pending_words(build_id))
            if on_wait_callback:
                try:
                    res = on_wait_callback(wait_seconds, pending_count)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as cb_err:
                    logger.debug(f"on_wait_callback exception: {cb_err}")

            # Wait up to min(wait_seconds, 2.0s) OR wake up immediately if peer bookmarklet submits a word!
            evt = self.word_resolved_events.get(build_id)
            if evt:
                evt.clear()
                try:
                    await asyncio.wait_for(evt.wait(), timeout=min(wait_seconds, 2.0))
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(min(wait_seconds, 2.0))


coordinator = ScrapeCoordinator()
