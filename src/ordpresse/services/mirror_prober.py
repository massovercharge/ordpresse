import asyncio
import re
import time
import urllib.parse
from typing import Any
from bs4 import BeautifulSoup
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
        self._search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

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

    async def search_books(self, query: str) -> dict[str, Any]:
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return {"status": "ok", "query": "", "count": 0, "results": []}

        now = time.time()
        cached = self._search_cache.get(cleaned_query.lower())
        if cached and (now - cached[0]) < self.ttl_seconds:
            return {"status": "ok", "query": cleaned_query, "count": len(cached[1]), "results": cached[1]}

        mirrors = await self.get_mirrors()
        libgen_mirrors = mirrors.get("flat_mirrors", {}).get("libgen", ["https://libgen.li"])

        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": BROWSER_UA},
            verify=False,
        ) as client:
            for base_url in libgen_mirrors:
                encoded_q = urllib.parse.quote_plus(cleaned_query)
                # Prioritize fiction (topics[]=f) and books (topics[]=l).
                # Never query without topics or with topics[]=a to avoid polluting results with scientific journal papers.
                search_urls = [
                    f"{base_url}/index.php?req={encoded_q}&columns[]=t&columns[]=a&topics[]=f&res=50",
                    f"{base_url}/index.php?req={encoded_q}&columns[]=t&columns[]=a&topics[]=l&res=50",
                    f"{base_url}/index.php?req={encoded_q}&topics[]=f&res=50",
                    f"{base_url}/index.php?req={encoded_q}&topics[]=l&res=50",
                ]
                for s_url in search_urls:
                    try:
                        resp = await client.get(s_url, follow_redirects=True)
                        if resp.status_code == 200 and "<tr" in resp.text:
                            soup = BeautifulSoup(resp.text, "html.parser")
                            table = soup.find("table", {"id": "tablelibgen"}) or soup.find("table")
                            if table:
                                for tr in table.find_all("tr"):
                                    tds = tr.find_all("td")
                                    if len(tds) >= 8:
                                        md5_match = re.search(r"md5=([0-9a-fA-F]{32})", str(tr))
                                        if md5_match:
                                            md5 = md5_match.group(1).lower()
                                            if any(r["md5"] == md5 for r in results):
                                                continue
                                            title_a = tds[0].find("a", href=re.compile(r"edition\.php"))
                                            title = title_a.get_text(strip=True) if title_a else tds[0].get_text(strip=True)
                                            title = re.sub(r"\s+", " ", title).strip()

                                            author = tds[1].get_text(strip=True) if len(tds) > 1 else "Unknown Author"
                                            author = re.sub(r"\s*\((?:Author|author|auth\.)\)", "", author).strip()
                                            author = re.sub(r"\s+", " ", author).strip()

                                            publisher = tds[2].get_text(strip=True) if len(tds) > 2 else ""
                                            year = tds[3].get_text(strip=True) if len(tds) > 3 else ""
                                            lang = tds[4].get_text(strip=True) if len(tds) > 4 else ""
                                            pages = tds[5].get_text(strip=True) if len(tds) > 5 else ""
                                            size = tds[6].get_text(strip=True) if len(tds) > 6 else ""
                                            ext = tds[7].get_text(strip=True).lower() if len(tds) > 7 else "epub"

                                            results.append({
                                                "title": title or "Unknown Title",
                                                "author": author or "Unknown Author",
                                                "year": year,
                                                "format": ext,
                                                "extension": ext,
                                                "size": size,
                                                "language": lang,
                                                "pages": pages,
                                                "publisher": publisher,
                                                "md5": md5,
                                                "source": "libgen",
                                                "download_page": f"{base_url}/ads.php?md5={md5}",
                                            })
                        if len(results) >= 20:
                            break
                    except Exception:
                        continue
                if results:
                    break

        format_priority = {"epub": 1, "azw3": 2, "mobi": 3, "pdf": 4}
        results.sort(key=lambda r: format_priority.get(r.get("format", "").lower(), 10))

        self._search_cache[cleaned_query.lower()] = (now, results)
        return {"status": "ok", "query": cleaned_query, "count": len(results), "results": results}

    async def resolve_download_ticket(self, md5: str) -> dict[str, Any]:
        cleaned_md5 = (md5 or "").strip().lower()
        if not re.match(r"^[0-9a-fA-F]{32}$", cleaned_md5):
            return {"status": "error", "error": "Invalid MD5"}

        mirrors = await self.get_mirrors()
        libgen_mirrors = mirrors.get("flat_mirrors", {}).get("libgen", ["https://libgen.li"])

        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": BROWSER_UA},
            verify=False,
        ) as client:
            for base_url in libgen_mirrors:
                ads_url = f"{base_url}/ads.php?md5={cleaned_md5}"
                try:
                    resp = await client.get(ads_url, follow_redirects=True)
                    if resp.status_code == 200:
                        m = re.search(r'href=[\'"]([^\'"]*get\.php[^\'"]*)[\'"]', resp.text)
                        if m:
                            get_path = m.group(1).lstrip("/")
                            download_url = f"{base_url}/{get_path}"
                            return {
                                "status": "ok",
                                "md5": cleaned_md5,
                                "download_url": download_url,
                            }
                except Exception:
                    continue

        return {"status": "error", "error": "Could not resolve download ticket"}

