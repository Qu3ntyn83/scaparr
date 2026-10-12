"""Kollektionen (Filmreihen): Daten von TMDB auf Deutsch, Übersicht vorhanden/fehlend, Abgleich mit Emby."""
import json
import os
from datetime import datetime, timedelta

from . import artwork, config, db, emby, renamer, tmdb
from .http import SourceError, request

MAX_AGE = timedelta(days=7)


def enabled():
    return bool(config.get("collections_enabled"))


# ---------------------------------------------------------------- eigene Filme je Reihe

def owned_map():
    """{coll_id: [film, …]} aus den gescrapten Filmen (meta.set)."""
    out = {}
    for r in db.query("SELECT id, path, title, year, tmdb_id, status, meta FROM movies WHERE status='gescrapt'"):
        s = (r.get("meta") or {}).get("set") if isinstance(r.get("meta"), dict) else None
        if s and s.get("tmdb_id"):
            out.setdefault(int(s["tmdb_id"]), []).append(
                {"id": r["id"], "path": r["path"], "title": r["title"], "year": r["year"], "tmdb_id": r["tmdb_id"]})
    return out


# ---------------------------------------------------------------- TMDB

def _stale(row):
    if not row or not row.get("updated"):
        return True
    try:
        return datetime.now() - datetime.fromisoformat(row["updated"]) > MAX_AGE
    except ValueError:
        return True


def refresh(coll_ids=None, force=False, progress=None):
    """Reihen von TMDB holen (deutsch). Ohne IDs: alle, zu denen es eigene Filme gibt."""
    if not enabled():
        return 0
    ids = list(coll_ids) if coll_ids else list(owned_map())
    done = 0
    for cid in ids:
        row = db.one("SELECT * FROM collections WHERE tmdb_id=?", (cid,))
        if not force and not _stale(row):
            continue
        try:
            c = tmdb.collection(cid)
        except tmdb.TMDBError as e:
            db.log("WARN", f"Kollektion {cid}: {e}")
            continue
        poster = artwork.choose(c["images"]["poster"], "poster")
        fanart = artwork.choose(c["images"]["fanart"], "fanart")
        poster_url = poster["url"] if poster else tmdb.img(c.get("poster_path"), config.get("poster_size"))
        fanart_url = fanart["url"] if fanart else tmdb.img(c.get("backdrop_path"), config.get("fanart_size"))
        db.execute(
            "INSERT INTO collections (tmdb_id, name, overview, poster, fanart, parts, updated) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(tmdb_id) DO UPDATE SET name=excluded.name, overview=excluded.overview, "
            "poster=excluded.poster, fanart=excluded.fanart, parts=excluded.parts, updated=excluded.updated",
            (cid, c["name"], c["overview"], poster_url, fanart_url, json.dumps(c["parts"], ensure_ascii=False),
             db.now()))
        _write_art_folder(c["name"], poster_url, fanart_url)
        done += 1
        if progress:
            progress(f"Kollektion: {c['name']}")
    if done:
        db.log("INFO", f"{done} Kollektion(en) von TMDB aktualisiert")
    return done


def _write_art_folder(name, poster, fanart):
    """Optional: Bilder als <Ordner>/<Name>/poster.jpg + fanart.jpg (Kodi/tMM „Movie Set Folder“)."""
    base = (config.get("collection_art_folder") or "").strip()
    if not base:
        return
    from .scraper import _download        # spät importieren, vermeidet Ringimport
    folder = os.path.join(base, renamer.clean(name))
    _download(poster, os.path.join(folder, "poster.jpg"))
    _download(fanart, os.path.join(folder, "fanart.jpg"))


# ---------------------------------------------------------------- Übersicht

def listing():
    owned = owned_map()
    rows = {r["tmdb_id"]: r for r in db.query("SELECT * FROM collections")}
    out = []
    for cid, films in owned.items():
        r = rows.get(cid) or {}
        parts = r.get("parts") or []
        have = {f["tmdb_id"] for f in films}
        out.append({
            "tmdb_id": cid, "name": r.get("name") or "(noch nicht geladen)", "poster": r.get("poster"),
            "owned": len(films), "total": len(parts) or len(films),
            "missing": len([p for p in parts if p["tmdb_id"] not in have]),
            "emby_id": r.get("emby_id"), "emby_synced": r.get("emby_synced"), "loaded": bool(r),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def detail(cid):
    r = db.one("SELECT * FROM collections WHERE tmdb_id=?", (cid,)) or {"tmdb_id": cid}
    films = owned_map().get(int(cid), [])
    by_tmdb = {f["tmdb_id"]: f for f in films}
    parts = []
    for p in r.get("parts") or []:
        f = by_tmdb.pop(p["tmdb_id"], None)
        parts.append({**p, "owned": bool(f), "movie_id": f["id"] if f else None})
    for f in by_tmdb.values():        # eigene Filme, die TMDB (noch) nicht in der Reihe führt
        parts.append({"tmdb_id": f["tmdb_id"], "title": f["title"], "year": f["year"], "owned": True,
                      "movie_id": f["id"], "poster": None})
    return {**r, "parts": parts, "owned": sum(p["owned"] for p in parts)}


# ---------------------------------------------------------------- Emby

def _fetch_bytes(url):
    if not url:
        return None
    try:
        r = request("GET", url, min_interval=0.1)
        return r.content if r.status_code == 200 else None
    except SourceError:
        return None


def emby_sync(coll_ids=None, force_images=False, progress=None):
    """Kollektionen in Emby anlegen bzw. auf Stand bringen: Mitglieder, deutscher Name/Text, TMDB-ID, Bilder."""
    if not (enabled() and config.get("collections_to_emby") and emby.enabled()):
        return 0
    owned = owned_map()
    min_n = max(1, config.get("collection_min_movies"))
    targets = [cid for cid in (coll_ids or owned) if len(owned.get(cid, [])) >= min_n]
    if not targets:
        return 0
    refresh(targets)                                  # fehlende/alte Reihen nachladen
    emby._build_map()
    boxsets = emby.boxsets()
    by_tmdb = {str((b.get("ProviderIds") or {}).get("Tmdb") or ""): b for b in boxsets}
    by_name = {(b.get("Name") or "").strip().lower(): b for b in boxsets}
    by_id = {b["Id"]: b for b in boxsets}
    done = 0
    for cid in targets:
        row = db.one("SELECT * FROM collections WHERE tmdb_id=?", (cid,))
        if not row:
            continue
        item_ids = [emby._items_by_path.get(os.path.normpath(f["path"])) for f in owned[cid]]
        item_ids = [i for i in item_ids if i]
        if len(item_ids) < min_n:
            continue          # Filme noch nicht in Emby -> beim nächsten Lauf
        box = by_id.get(row.get("emby_id")) or by_tmdb.get(str(cid)) or by_name.get(row["name"].strip().lower())
        try:
            created = False
            if not box:
                bid = emby.create_collection(row["name"], item_ids)
                created = True
            else:
                bid = box["Id"]
                have = set(emby.collection_members(bid))
                add = [i for i in item_ids if i not in have]
                if add:
                    emby.add_to_collection(bid, add)
            emby.set_metadata(bid, row["name"], row.get("overview"), {"Tmdb": str(cid)})
            needs_img = created or force_images or not ((box or {}).get("ImageTags") or {}).get("Primary")
            if needs_img:
                for kind, url in (("Primary", row.get("poster")), ("Backdrop", row.get("fanart"))):
                    data = _fetch_bytes(url)
                    if data:
                        emby.upload_image(bid, kind, data)
            db.execute("UPDATE collections SET emby_id=?, emby_synced=? WHERE tmdb_id=?", (bid, db.now(), cid))
            done += 1
            if progress:
                progress(f"Emby-Kollektion: {row['name']}")
        except SourceError as e:
            db.log("WARN", f"Emby-Kollektion „{row['name']}“: {e}")
    db.log("INFO", f"Emby: {done} Kollektion(en) angelegt/aktualisiert")
    return done
