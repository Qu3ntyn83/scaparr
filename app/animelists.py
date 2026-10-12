"""Fribb/anime-lists: Zuordnung AniDB/AniList/MAL/Kitsu/aniSearch ↔ TVDB/TMDB inkl. Staffel und Episodenversatz.

Die Liste (~7 MB) wird einmal pro Woche von GitHub geladen und lokal zwischengespeichert –
pro Titel gibt es keine Online-Abfrage.
"""
import json
import os
import threading
import time

from . import config, db
from .http import SourceError, request

URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
CACHE = os.path.join(db.DATA_DIR, "anime-list-full.json")
MAX_AGE = 7 * 86400
ID_KEYS = ("anidb_id", "anilist_id", "mal_id", "kitsu_id", "anisearch_id")
_lock = threading.Lock()
_idx = {"loaded": 0.0}


def enabled():
    return bool(config.get("animelists_enabled"))


def _download():
    r = request("GET", URL, min_interval=1.0, retries=2)
    if r.status_code != 200:
        raise SourceError(f"anime-lists: HTTP {r.status_code}")
    data = r.json()
    if not isinstance(data, list) or len(data) < 1000:
        raise SourceError("anime-lists: unerwartetes Format")
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE)
    db.log("INFO", f"Anime-Zuordnungsliste aktualisiert ({len(data)} Einträge)")
    return data


def _load():
    with _lock:
        fresh = os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < MAX_AGE
        if _idx["loaded"] and (fresh or time.time() - _idx["loaded"] < 3600):
            return _idx
        data = None
        if not fresh:
            try:
                data = _download()
            except (SourceError, ValueError) as e:
                db.log("WARN", f"{e} – nutze vorhandene Liste")
        if data is None and os.path.exists(CACHE):
            with open(CACHE, encoding="utf-8") as f:
                data = json.load(f)
        data = data or []
        idx = {k: {} for k in ID_KEYS}
        idx["tmdb_tv"], idx["tmdb_movie"], idx["tvdb"] = {}, {}, {}
        for e in data:
            for k in ID_KEYS:
                if e.get(k) is not None:
                    idx[k][str(e[k])] = e
            tm = e.get("themoviedb_id") or {}
            if tm.get("tv"):
                idx["tmdb_tv"].setdefault(int(tm["tv"]), []).append(e)
            for mid in tm.get("movie") or []:
                idx["tmdb_movie"].setdefault(int(mid), e)
            if e.get("tvdb_id"):
                idx["tvdb"].setdefault(int(e["tvdb_id"]), []).append(e)
        _idx.clear()
        _idx.update(idx, loaded=time.time(), count=len(data))
        return _idx


def by_id(kind, value):
    """kind: anidb | anilist | mal | kitsu | anisearch"""
    if not enabled() or value in (None, ""):
        return None
    return _load().get(f"{kind}_id", {}).get(str(value))


def ids(entry):
    if not entry:
        return {}
    return {k[:-3]: entry[k] for k in ID_KEYS if entry.get(k) is not None}


def mapping(entry):
    """-> {"tmdb_tv": 1429, "tmdb_movie": None, "tvdb": 267440, "season": 2, "offset": 0} – TMDB-Zählung bevorzugt"""
    if not entry:
        return None
    tm = entry.get("themoviedb_id") or {}
    season = entry.get("season") or {}
    off = entry.get("episode_offset") or {}
    has_tmdb = season.get("tmdb") is not None
    return {
        "tmdb_tv": tm.get("tv"), "tmdb_movie": (tm.get("movie") or [None])[0], "tvdb": entry.get("tvdb_id"),
        "season": season.get("tmdb") if has_tmdb else season.get("tvdb"),
        "offset": int((off.get("tmdb") if has_tmdb else off.get("tvdb")) or 0),
        "numbering": "tmdb" if has_tmdb else "tvdb",
        "type": entry.get("type"),
    }


def for_show(tmdb_tv=None, tvdb=None, season=1):
    """Anime-Eintrag zur (ersten) Staffel einer Serie – None, wenn kein Anime."""
    if not enabled():
        return None
    idx = _load()
    cands = idx["tmdb_tv"].get(int(tmdb_tv), []) if tmdb_tv else []
    key = "tmdb"
    if not cands and tvdb:
        cands, key = idx["tvdb"].get(int(tvdb), []), "tvdb"
    if not cands:
        return None
    exact = [e for e in cands if (e.get("season") or {}).get(key) == season and e.get("type") == "TV"
             and not (e.get("episode_offset") or {}).get(key)]
    return (exact or [e for e in cands if (e.get("season") or {}).get(key) == season] or cands)[0]


def for_movie(tmdb_movie):
    if not enabled() or not tmdb_movie:
        return None
    return _load()["tmdb_movie"].get(int(tmdb_movie))


def test():
    if not enabled():
        return {"ok": False, "msg": "Anime-Zuordnung ist deaktiviert"}
    try:
        idx = _load()
        e = for_show(tmdb_tv=1429)
        return {"ok": bool(idx.get("count")),
                "msg": f"anime-lists OK – {idx.get('count', 0)} Einträge; Attack on Titan → AniDB {e.get('anidb_id') if e else '–'}"}
    except Exception as e:  # noqa
        return {"ok": False, "msg": f"anime-lists: {e}"}
