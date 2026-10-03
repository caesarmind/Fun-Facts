"""Polite HTTP client: per-host rate limit, retries, robots.txt, on-disk cache."""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
import urllib.robotparser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 RecipeLibraryScraper/1.0"
)


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, cache_dir: str | Path | None = "data/cache", delay: float = 1.0,
                 timeout: float = 30, retries: int = 3, user_agent: str = DEFAULT_UA,
                 respect_robots: bool = True, refresh: bool = False, offline: bool = False):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self.refresh = refresh
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ka,en;q=0.8",
        })
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self._dead_hosts: set[str] = set()
        self.stats = {"network": 0, "cache": 0, "errors": 0}

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(key.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.gz"

    def _cache_get(self, key: str) -> str | None:
        p = self._cache_path(key)
        if p and p.exists() and not self.refresh:
            self.stats["cache"] += 1
            return gzip.decompress(p.read_bytes()).decode("utf-8")
        return None

    def _cache_put(self, key: str, text: str) -> None:
        p = self._cache_path(key)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(text.encode("utf-8")))

    # ------------------------------------------------------------- politeness
    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host, 0.0)
        wait = self.delay - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                text = self._request("GET", base + "/robots.txt", check_robots=False, use_cache=True,
                                     allow_404=True) or ""
                rp.parse(text.splitlines())
            except FetchError:
                rp = None  # unreachable robots.txt -> allow
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.session.headers["User-Agent"], url)

    # ---------------------------------------------------------------- request
    def _request(self, method: str, url: str, *, json_body: Any = None, check_robots: bool = True,
                 use_cache: bool = True, allow_404: bool = False, headers: dict | None = None) -> str | None:
        key = method + " " + url + ("" if json_body is None else " " + json.dumps(json_body, sort_keys=True))
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        if check_robots and not self.allowed(url):
            raise FetchError(f"disallowed by robots.txt: {url}")

        resp = self._send(method, url, json_body=json_body, headers=headers, allow_404=allow_404)
        if resp is None:
            return None
        text = _decode(resp)
        if use_cache:
            self._cache_put(key, text)
        return text

    def _send(self, method: str, url: str, *, json_body: Any = None, headers: dict | None = None,
              allow_404: bool = False) -> requests.Response | None:
        """One polite request: per-host rate limit, retries with backoff, dead-host short-circuit."""
        host = urlsplit(url).netloc
        if host in self._dead_hosts:
            raise FetchError(f"{host} is unreachable (connection failed earlier); skipping {url}")
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            with self._host_lock(host):
                self._wait_turn(host)
                try:
                    resp = self.session.request(method, url, json=json_body, timeout=self.timeout,
                                                headers=headers)
                except requests.RequestException as exc:
                    last_exc = exc
                    resp = None
            if resp is not None:
                self.stats["network"] += 1
                if resp.status_code == 404 and allow_404:
                    return None
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                    retry_after = resp.headers.get("Retry-After", "")
                    time.sleep(float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    self.stats["errors"] += 1
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
                return resp
            if attempt < self.retries:
                time.sleep(2 ** attempt)
        self.stats["errors"] += 1
        if isinstance(last_exc, (requests.ConnectionError, requests.Timeout)):
            self._dead_hosts.add(host)  # don't spend the retry budget again on every URL of a dead/blocked host
        raise FetchError(f"giving up on {url}: {last_exc}")

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        """Binary download (images). Not cached here: callers keep the file themselves."""
        if self.offline:
            raise FetchError(f"offline: {url}")
        if not self.allowed(url):
            raise FetchError(f"disallowed by robots.txt: {url}")
        resp = self._send("GET", url)
        return resp.content, resp.headers.get("Content-Type", "").split(";")[0].strip()

    def get(self, url: str, **kw: Any) -> str:
        text = self._request("GET", url, **kw)
        if text is None:
            raise FetchError(f"not found: {url}")
        return text

    def get_optional(self, url: str) -> str | None:
        """Like get() but returns None on 404 / errors (used for pagination probing)."""
        try:
            return self._request("GET", url, allow_404=True)
        except FetchError as exc:
            log.debug("get_optional %s: %s", url, exc)
            return None

    def post_json(self, url: str, body: Any, **kw: Any) -> Any:
        text = self._request("POST", url, json_body=body,
                             headers={"Accept": "application/json", "Content-Type": "application/json"}, **kw)
        return json.loads(text) if text else None

    # -------------------------------------------------------------- rendering
    def render(self, url: str, wait_selector: str | None = None, timeout_ms: int = 20000) -> str:
        """Render a JavaScript page with Playwright (optional dependency)."""
        key = "RENDER " + url
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise FetchError("rendering needs: pip install playwright && playwright install chromium") from exc
        host = urlsplit(url).netloc
        with self._host_lock(host):
            self._wait_turn(host)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=self.session.headers["User-Agent"])
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if wait_selector:
                        try:
                            page.wait_for_selector(wait_selector, timeout=timeout_ms)
                        except Exception:  # noqa: BLE001 - render whatever arrived
                            log.debug("selector %s not seen on %s", wait_selector, url)
                    html = page.content()
                finally:
                    browser.close()
        self.stats["network"] += 1
        self._cache_put(key, html)
        return html


def _decode(resp: requests.Response) -> str:
    ctype = resp.headers.get("Content-Type", "")
    enc = resp.encoding
    if not enc or ("charset" not in ctype.lower() and enc.lower() == "iso-8859-1"):
        enc = "utf-8"  # Georgian sites are UTF-8; requests' latin-1 default would garble them
    return resp.content.decode(enc, errors="replace")
