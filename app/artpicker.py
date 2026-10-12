"""Artwork von Hand wählen: alle Kandidaten (TMDB, fanart.tv, TVDB) je Bildtyp, Auswahl wird gesperrt,
damit spätere Scrapes sie nicht wieder überschreiben."""
import os
import threading
import time

from . import artwork, config, db, emby, fanart, paths, tmdb, tvdb

KINDS = {"poster": "Poster", "fanart": "Fanart", "logo": "Clearlogo", "landscape": "Thumb", "banner": "Banner"}
_cache = {}          # (typ, tmdb_id) -> (zeit, kandidaten)
CACHE_SEC = 600
MAX_PER_KIND = 80


def _item(typ, iid):
    table = "movies" if typ == "movie" else "shows"
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (iid,))
    if not row:
        raise LookupError("Eintrag nicht gefunden")
    return table, row


def _files(typ, row):
    if typ == "movie":
        return paths.movie_files(row["path"], bool(row["single_folder"]))
    return paths.show_files(row["folder"])


def _preview(c):
    url = c["url"]
    if c["source"] == "tmdb":
        size = "w780" if c.get("kind") in ("fanart", "landscape", "banner") else "w342"
        for s in ("/original/", "/w1280/", "/w780/", "/w500/", "/w342/"):
            if s in url:
                return url.replace(s, f"/{size}/")
    if c["source"] == "fanart" and "/fanart/" in url:
        return url.replace("/fanart/", "/preview/")
    return url


def _collect(typ, row):
    meta = row.get("meta") or {}
    tmdb_id = row.get("tmdb_id") or meta.get("tmdb_id")
    if not tmdb_id:
        raise ValueError("Noch keinem TMDB-Eintrag zugeordnet – erst scrapen oder über Suchen & Zuordnen zuordnen")
    key = (typ, int(tmdb_id))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    if typ == "movie":
        d = tmdb.movie(int(tmdb_id))
        merged = artwork.merge(d.get("images"), fanart.movie_art(int(tmdb_id), d.get("imdb_id") or row.get("imdb_id")))
    else:
        d = tmdb.tv(int(tmdb_id))
        tvdb_id = meta.get("tvdb_id") or d.get("tvdb_id")
        fan = fanart.tv_art(tvdb_id) if tvdb_id else {}
        fan.pop("season_poster", None)
        merged = artwork.merge(d.get("images"), fan, tvdb.series_art(tvdb_id) if tvdb_id else {})
    # Thumb (landscape.jpg): TMDB-Hintergründe mit Titel im Bild eignen sich als Ersatz
    for c in merged.get("fanart", []):
        if c["source"] == "tmdb" and c.get("lang"):
            merged.setdefault("landscape", []).append(dict(c))
    for k in [k for k, v in _cache.items() if time.time() - v[0] >= CACHE_SEC]:
        del _cache[k]                      # abgelaufene Einträge verwerfen
    _cache[key] = (time.time(), merged)
    return merged


def _sorted(cands, kind):
    order = [s.strip() for s in config.get("artwork_order").split(",") if s.strip()]
    rank = {s: i for i, s in enumerate(order)}
    prefs = artwork.lang_prefs(kind)
    lrank = {l: i for i, l in enumerate(prefs)}
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: (lrank.get(c.get("lang"), 9), rank.get(c["source"], 99),
                                          -float(c.get("score") or 0))):
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        out.append(c)
    return out[:MAX_PER_KIND]


def options(typ, iid, kind):
    if kind not in KINDS:
        raise ValueError("Unbekannter Bildtyp")
    _, row = _item(typ, iid)
    cands = _sorted(_collect(typ, row).get(kind, []), kind)
    current = ((row.get("meta") or {}).get("art") or {}).get(kind, {}).get("url")
    lock = (row.get("art_lock") or {}).get(kind)
    files = _files(typ, row)
    return {
        "kind": kind, "label": KINDS[kind], "locked": bool(lock),
        "file": files.get(kind), "has_file": bool(files.get(kind) and os.path.exists(files[kind])),
        "items": [{"url": c["url"], "preview": _preview(dict(c, kind=kind)), "source": c["source"],
                   "lang": c.get("lang"), "w": c.get("w"), "h": c.get("h"),
                   "current": c["url"] in (current, lock)} for c in cands],
    }


def _notify(path):
    threading.Thread(target=emby.notify, args=([path],), daemon=True).start()


def apply(typ, iid, kind, url):
    """Gewähltes Bild herunterladen, Datei ersetzen und für künftige Scrapes sperren."""
    from .scraper import _download        # spät importieren (scraper importiert viel)
    if kind not in KINDS:
        raise ValueError("Unbekannter Bildtyp")
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("Ungültige Bild-URL")
    table, row = _item(typ, iid)
    dest = _files(typ, row).get(kind)
    if not dest:
        raise ValueError(f"{KINDS[kind]} wird für diesen Eintrag nicht gespeichert")
    if not _download(url, dest, force=True):
        raise RuntimeError("Download fehlgeschlagen – Details im Protokoll")
    lock = dict(row.get("art_lock") or {})
    lock[kind] = url
    meta = row.get("meta") or {}
    if meta:
        meta.setdefault("art", {})[kind] = {"url": url, "source": _source_of(url), "lang": None, "manual": True}
    st = paths.movie_status(row["path"], bool(row["single_folder"])) if typ == "movie" else paths.show_status(row["folder"])
    db.update(table, iid, art_lock=lock, **({"meta": meta} if meta else {}), **{k: int(v) for k, v in st.items()})
    db.log("INFO", f"{KINDS[kind]} von Hand gewählt: {row.get('title') or row.get('guess_title')} ({_source_of(url)})")
    _notify(row["path"] if typ == "movie" else row["folder"])
    return {"ok": True, "file": dest}


def unlock(typ, iid, kind):
    """Sperre aufheben – beim nächsten Scrapen wird wieder automatisch gewählt."""
    table, row = _item(typ, iid)
    lock = dict(row.get("art_lock") or {})
    lock.pop(kind, None)
    db.update(table, iid, art_lock=lock or None)
    return {"ok": True}


def _source_of(url):
    if "image.tmdb.org" in url:
        return "tmdb"
    if "fanart.tv" in url:
        return "fanart"
    if "thetvdb.com" in url:
        return "tvdb"
    return "eigene URL"
