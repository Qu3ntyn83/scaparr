"""Gemeinsamer HTTP-Helfer: User-Agent, Drosselung pro Host, Wiederholungen."""
import threading
import time
from urllib.parse import urlparse

import httpx

UA = "Scraparr/1.4 (+self-hosted; TMDB/TVDB/fanart.tv/OMDb/OFDb/Wikidata)"
_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_glock = threading.Lock()


class SourceError(Exception):
    pass


def _throttle(host, min_interval):
    with _glock:
        lock = _locks.setdefault(host, threading.Lock())
    with lock:
        wait = min_interval - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()


def request(method, url, *, min_interval=0.1, retries=3, ok404=False, **kw):
    """Gibt httpx.Response zurück; 404 -> None wenn ok404."""
    headers = {"User-Agent": UA, **kw.pop("headers", {})}
    host = urlparse(url).netloc
    for attempt in range(retries):
        _throttle(host, min_interval)
        try:
            r = httpx.request(method, url, headers=headers, timeout=25, follow_redirects=True, **kw)
        except httpx.HTTPError as e:
            if attempt == retries - 1:
                raise SourceError(f"{host} nicht erreichbar: {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries - 1:
                raise SourceError(f"{host}: HTTP {r.status_code}")
            time.sleep(int(r.headers.get("Retry-After", 2 ** (attempt + 1))))
            continue
        if r.status_code == 404 and ok404:
            return None
        return r
    raise SourceError(f"{host}: keine Antwort")
