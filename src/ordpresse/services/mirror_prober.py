import asyncio
import time
from typing import Any
import httpx

LIBGEN_CANDIDATES = [
    "https://libgen.li",
    "https://libgen.is",
    "https://libgen.rs",
    "https://libgen.gs",
]

ANNAS_CANDIDATES = [
    "https://annas-archive.pk",
    "https://annas-archive.gl",
    "https://annas-archive.gd",
    "https://annas-archive.se",
    "https://annas-archive.org",
]

PARKING_MARKERS = (
    "this domain may be for sale",
    "sedoparking",
    "parked-content",
    "buy this domain",
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class MirrorProberService:
    def __init__(self, ttl_seconds: int = 3600, timeout: float = 3.5) -> None:
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, Any] | None = None
        self._last_updated: float = 0.0
        self._lock = asyncio.Lock()
        self._backoff_until: dict[str, float] = {}
        self._fail_counts: dict[str, int] = {}

    def _is_parked(self, body_lower: str) -> bool:
        return any(marker in body_lower for marker in PARKING_MARKERS)

    def _record_failure(self, url: str) -> None:
        count = self._fail_counts.get(url, 0) + 1
        self._fail_counts[url] = count
        backoff_seconds = min(900 * (2 ** (count - 1)), 14400)
        self._backoff_until[url] = time.time() + backoff_seconds

    def _record_success(self, url: str) -> None:
        self._fail_counts.pop(url, None)
        self._backoff_until.pop(url, None)

    async def _probe_libgen(self, client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
        if time.time() < self._backoff_until.get(base_url, 0):
            return None
        start = time.perf_counter()
        try:
            resp = await client.get(f"{base_url}/", follow_redirects=True)
            latency_ms = round((time.perf_counter() - start) * 1000)
            body_lower = resp.text[:8192].lower()
            if resp.status_code == 200 and not self._is_parked(body_lower):
                if "libgen" in body_lower or "index.php" in body_lower or "<form" in body_lower:
                    self._record_success(base_url)
                    return {"url": base_url, "latency_ms": latency_ms, "healthy": True}
        except Exception:
            pass
        self._record_failure(base_url)
        return None

    async def _probe_annas(self, client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
        if time.time() < self._backoff_until.get(base_url, 0):
            return None
        start = time.perf_counter()
        try:
            resp = await client.get(base_url, follow_redirects=False)
            latency_ms = round((time.perf_counter() - start) * 1000)
            body_lower = resp.text[:8192].lower()
            server_hdr = resp.headers.get("server", "").lower()
            is_ddg = "ddos-guard" in server_hdr or any(
                k.startswith("__ddg") for k in resp.cookies.keys()
            )

            if self._is_parked(body_lower):
                self._record_failure(base_url)
                return None

            if resp.status_code == 200 and ("anna" in body_lower or "/search" in body_lower):
                self._record_success(base_url)
                return {
                    "url": base_url,
                    "latency_ms": latency_ms,
                    "healthy": True,
                    "ddos_guard": is_ddg,
                }

            if is_ddg and resp.status_code in (200, 302, 403):
                self._record_success(base_url)
                return {
                    "url": base_url,
                    "latency_ms": latency_ms,
                    "healthy": True,
                    "ddos_guard": True,
                }
        except Exception:
            pass
        self._record_failure(base_url)
        return None

    async def get_mirrors(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cache and (now - self._last_updated) < self.ttl_seconds:
            return self._cache

        async with self._lock:
            if not force_refresh and self._cache and (time.time() - self._last_updated) < self.ttl_seconds:
                return self._cache

            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": BROWSER_UA},
                verify=True,
            ) as client:
                libgen_tasks = [self._probe_libgen(client, u) for u in LIBGEN_CANDIDATES]
                annas_tasks = [self._probe_annas(client, u) for u in ANNAS_CANDIDATES]
                libgen_raw, annas_raw = await asyncio.gather(
                    asyncio.gather(*libgen_tasks),
                    asyncio.gather(*annas_tasks),
                )

            libgen_ok = sorted(
                [r for r in libgen_raw if r is not None],
                key=lambda x: x["latency_ms"],
            )
            annas_ok = sorted(
                [r for r in annas_raw if r is not None],
                key=lambda x: (x.get("ddos_guard", False), x["latency_ms"]),
            )

            if not libgen_ok:
                libgen_ok = [{"url": "https://libgen.li", "latency_ms": 999, "healthy": False}]
            if not annas_ok:
                annas_ok = [{"url": "https://annas-archive.pk", "latency_ms": 999, "healthy": False, "ddos_guard": True}]

            self._last_updated = time.time()
            self._cache = {
                "status": "ok",
                "updated_at": int(self._last_updated),
                "ttl_seconds": self.ttl_seconds,
                "sources": {
                    "libgen": libgen_ok,
                    "annas": annas_ok,
                },
                "flat_mirrors": {
                    "libgen": [m["url"] for m in libgen_ok],
                    "annas": [m["url"] for m in annas_ok],
                },
            }
            return self._cache

