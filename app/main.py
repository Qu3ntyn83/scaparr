"""Scraparr – Web-API und Frontend."""
import base64
import json
import hashlib
import os
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (animelists, arr, artpicker, config, db, emby, fanart, fernsehserien, jobs, moviesets, nfo, ofdb, omdb, paths, renamer,
               scraper, tmdb, trakt, tvdb, wikidata)

STATIC = os.path.join(os.path.dirname(__file__), "static")
VERSION = "1.5.0"
NO_CACHE = {"Cache-Control": "no-cache"}
CACHE = os.path.join(db.DATA_DIR, "cache")
SECRET_KEYS = ("tmdb_api_key", "emby_api_key", "tvdb_api_key", "tvdb_pin", "fanart_api_key",
               "fanart_client_key", "omdb_api_key", "trakt_client_id",
               "radarr_api_key", "sonarr_api_key")
MASK = "••••••••"

@asynccontextmanager
async def lifespan(_app):
    db.init()
    os.makedirs(CACHE, exist_ok=True)
    if not config.get("webhook_token"):
        config.save({"webhook_token": secrets.token_urlsafe(18)})
    db.log("INFO", f"Scraparr {VERSION} gestartet")
    threading.Thread(target=jobs.scheduler, daemon=True).start()
    yield


app = FastAPI(title="Scraparr", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

WEB_USER, WEB_PASS = os.environ.get("WEB_USER"), os.environ.get("WEB_PASS")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # Webhooks (POST) prüfen ihr eigenes Token; alles andere – auch /api/hook/info mit dem Token – braucht den Login
    is_hook = request.method == "POST" and request.url.path in ("/api/hook/radarr", "/api/hook/sonarr")
    if WEB_USER and WEB_PASS and not is_hook:
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(auth[6:]).decode().partition(":")
                ok = (secrets.compare_digest(u.encode(), WEB_USER.encode())
                      & secrets.compare_digest(p.encode(), WEB_PASS.encode()))
            except (ValueError, UnicodeDecodeError):
                pass
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Scraparr"'})
    return await call_next(request)


# ---------------------------------------------------------------- Status & Einstellungen

@app.get("/api/status")
def status():
    def counts(table):
        return {r["status"]: r["n"] for r in
                db.query(f"SELECT status, COUNT(*) n FROM {table} GROUP BY status")}
    return {"job": {k: v for k, v in jobs.state.items() if k != "cancel"},
            "movies": counts("movies"), "shows": counts("shows"), "episodes": counts("episodes"),
            "api_key": bool(config.get("tmdb_api_key")), "version": VERSION,
            "server": emby.name() if emby.enabled() else None}


@app.get("/api/settings")
def get_settings():
    s = config.get_all()
    for k in SECRET_KEYS:
        s[k] = MASK if s[k] else ""
    return s


@app.post("/api/settings")
def save_settings(values: dict):
    for k in SECRET_KEYS:
        if values.get(k) == MASK:
            values.pop(k)
    for k in ("rename_movie_folder", "rename_movie_file", "rename_episode_file"):
        if k in values:
            try:
                renamer._fmt(values[k], title="T", year=2000, showtitle="S", season=1, episode=1,
                             eps="S01E01")
            except ValueError as e:
                raise HTTPException(400, str(e))
    config.save(values)
    emby._kind.update(until=0)          # Servertyp (Emby/Jellyfin) neu erkennen
    db.log("INFO", "Einstellungen gespeichert")
    return get_settings()


@app.post("/api/test/tmdb")
def test_tmdb():
    try:
        r = tmdb.search_movie("Das Boot", 1981)
        return {"ok": True, "msg": f"TMDB OK – Testtreffer: {r[0]['title']} ({r[0]['year']})" if r
                else "TMDB OK"}
    except tmdb.TMDBError as e:
        return {"ok": False, "msg": str(e)}


@app.post("/api/test/{source}")
def test_source(source: str):
    mods = {"tvdb": tvdb, "fanart": fanart, "omdb": omdb, "ofdb": ofdb, "wikidata": wikidata, "trakt": trakt,
            "fernsehserien": fernsehserien, "animelists": animelists}
    if source == "emby":
        return emby.test()
    if source in ("radarr", "sonarr"):
        return arr.test(source)
    if source not in mods:
        raise HTTPException(404)
    return mods[source].test()


# ---------------------------------------------------------------- Jobs

class Ids(BaseModel):
    ids: list[int] = []
    force: bool = False


def _start(name, fn, *args):
    """Job starten – läuft gerade einer, wird der neue eingereiht und danach automatisch ausgeführt."""
    r = jobs.submit(name, fn, *args)
    return {"started": name, "queued": r == "queued", "duplicate": r == "duplicate",
            "after": jobs.state["name"] if r != "started" else None}


@app.post("/api/scan/{kind}")
def scan(kind: str):
    if kind not in ("all", "movies", "tv"):
        raise HTTPException(400)
    return _start("Bibliotheken scannen" if kind == "all" else
                  "Filme scannen" if kind == "movies" else "Serien scannen", jobs.job_scan, kind)


@app.post("/api/auto/run")
def auto_run():
    return _start("Automatischer Lauf", jobs.job_auto)


@app.post("/api/job/cancel")
def cancel():
    """Laufenden Job abbrechen und die Warteschlange leeren."""
    dropped = jobs.clear_queue()
    jobs.state["cancel"] = True
    return {"ok": True, "dropped": dropped}


@app.post("/api/emby/refresh")
def emby_refresh():
    try:
        emby.library_refresh()
        return {"ok": True}
    except Exception as e:  # noqa
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- Kollektionen

@app.get("/api/collections")
def collections_list():
    return {"items": moviesets.listing(), "min": config.get("collection_min_movies"),
            "emby": emby.enabled() and config.get("collections_to_emby")}


@app.get("/api/collections/{cid}")
def collection_detail(cid: int):
    return moviesets.detail(cid)


class CollAction(BaseModel):
    ids: list[int] = []


@app.post("/api/collections/{action}")
def collections_action(action: str, body: CollAction):
    if action not in ("refresh", "emby"):
        raise HTTPException(404)
    if action == "emby" and not emby.enabled():
        raise HTTPException(400, "Kein Medienserver (Emby/Jellyfin) eingerichtet")
    name = "Kollektionen von TMDB laden" if action == "refresh" else f"Kollektionen nach {emby.name()}"
    return _start(name, jobs.job_collections, action, body.ids or None)


# ---------------------------------------------------------------- Abgleich

SYSTEMS = ("emby", "radarr", "sonarr")


@app.get("/api/sync")
def sync_reports():
    out = {}
    for sysname in SYSTEMS:
        r = db.one("SELECT ts, data FROM sync WHERE system=?", (sysname,))
        out[sysname] = {"configured": emby.enabled() if sysname == "emby" else arr.enabled(sysname),
                        "ts": r["ts"] if r else None, **(json.loads(r["data"]) if r else {})}
    return out


@app.post("/api/sync/{system}")
def sync_run(system: str):
    if system not in SYSTEMS:
        raise HTTPException(404)
    if not (emby.enabled() if system == "emby" else arr.enabled(system)):
        raise HTTPException(400, f"{system.capitalize()} ist nicht eingerichtet")
    return _start(f"Abgleich {system.capitalize()}", jobs.job_sync, system)


class SyncApply(BaseModel):
    indexes: list[int]


@app.post("/api/sync/{system}/apply")
def sync_apply(system: str, body: SyncApply):
    r = db.one("SELECT data FROM sync WHERE system=?", (system,))
    if not r:
        raise HTTPException(400, "Erst abgleichen")
    issues = json.loads(r["data"]).get("issues", [])
    chosen = [issues[i] for i in body.indexes if 0 <= i < len(issues) and issues[i].get("action")]
    if not chosen:
        raise HTTPException(400, "Keine ausführbaren Einträge gewählt")
    res = _start(f"{system.capitalize()}: {len(chosen)} Korrektur(en)", jobs.job_sync_apply, system, chosen)
    # erledigte Einträge aus dem Bericht nehmen
    rep = json.loads(r["data"])
    rep["issues"] = [x for i, x in enumerate(issues) if i not in set(body.indexes)]
    jobs.save_report(system, rep)
    return res


# ---------------------------------------------------------------- Webhooks

@app.get("/api/hook/info")
def hook_info(request: Request):
    base = str(request.base_url).rstrip("/")
    t = config.get("webhook_token")
    return {s: f"{base}/api/hook/{s}?token={t}" for s in ("radarr", "sonarr")}


@app.post("/api/hook/{system}")
async def hook(system: str, request: Request, token: str = ""):
    if system not in ("radarr", "sonarr"):
        raise HTTPException(404)
    expected = config.get("webhook_token")
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Token ungültig")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Kein JSON")
    ev, tasks = arr.parse_webhook(system, payload)
    if ev == "Test":
        db.log("INFO", f"{system.capitalize()}-Webhook: Test empfangen ✔")
    elif tasks:
        jobs.enqueue(tasks)
        db.log("INFO", f"{system.capitalize()}-Webhook: {ev} – {len(tasks)} Aufgabe(n) eingereiht")
    return {"ok": True, "queued": len(tasks)}


# ---------------------------------------------------------------- Filme

LIST_MOVIE = ("id, path, folder, guess_title, guess_year, tmdb_id, title, original_title, year, fsk, "
              "status, message, has_nfo, has_poster, has_fanart, has_trailer, single_folder, added")


@app.get("/api/movies")
def movies():
    return db.query(f"SELECT {LIST_MOVIE} FROM movies ORDER BY COALESCE(title, guess_title) COLLATE NOCASE")


@app.get("/api/movies/{mid}")
def movie(mid: int):
    m = db.one("SELECT * FROM movies WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404)
    m["files"] = paths.movie_files(m["path"], bool(m["single_folder"]))
    if m.get("part_files"):
        m["stack_ok"] = renamer.stack_ok(m)
        p = renamer.plan_stack(m)
        m["stack_plan"] = [os.path.basename(d) for _, d in p["parts"]] if p else None
    return m


@app.post("/api/movies/scrape")
def movies_scrape(body: Ids):
    ids = body.ids or [r["id"] for r in db.query("SELECT id FROM movies WHERE status!='gescrapt'")]
    return _start(f"{len(ids)} Film(e) scrapen", jobs.job_scrape_movies, ids, body.force)


class Match(BaseModel):
    tmdb_id: int


@app.post("/api/movies/{mid}/match")
def movie_match(mid: int, body: Match):
    return _start("Film zuordnen", jobs.job_scrape_one, "movie", mid, body.tmdb_id)


class Edit(BaseModel):
    title: str | None = None
    original_title: str | None = None
    sorttitle: str | None = None
    year: int | None = None
    plot: str | None = None
    tagline: str | None = None
    fsk: str | None = None
    genres: list[str] | None = None


def _apply_edit(meta, body):
    for k, v in body.model_dump(exclude_none=True).items():
        if meta.get(k) != v:
            meta.setdefault("sources", {})[k] = "manuell"
        meta[k] = v
    return meta


@app.post("/api/movies/{mid}/edit")
def movie_edit(mid: int, body: Edit):
    m = db.one("SELECT * FROM movies WHERE id=?", (mid,))
    if not m or not m.get("meta"):
        raise HTTPException(400, "Film muss zuerst gescrapt werden")
    meta = _apply_edit(m["meta"], body)
    nfo.write_movie(paths.movie_files(m["path"], bool(m["single_folder"]))["nfo"], meta)
    db.update("movies", mid, meta=meta, title=meta["title"], year=meta.get("year"), fsk=meta.get("fsk"))
    db.log("INFO", f"Film bearbeitet: {meta['title']}")
    return movie(mid)


# ---------------------------------------------------------------- Serien

@app.get("/api/shows")
def shows():
    return db.query(
        "SELECT s.id, s.folder, s.guess_title, s.guess_year, s.tmdb_id, s.title, s.original_title, s.year, "
        "s.fsk, s.status, s.message, s.has_nfo, s.has_poster, s.has_fanart, s.has_trailer, s.has_theme, s.added, "
        "COUNT(e.id) AS ep_total, SUM(e.status='gescrapt') AS ep_ok, SUM(e.status='fehler') AS ep_err "
        "FROM shows s LEFT JOIN episodes e ON e.show_id=s.id GROUP BY s.id "
        "ORDER BY COALESCE(s.title, s.guess_title) COLLATE NOCASE")


@app.get("/api/shows/{sid}")
def show(sid: int):
    s = db.one("SELECT * FROM shows WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404)
    s["episodes"] = db.query("SELECT id, path, season, episodes, title, meta, status, message, has_nfo, has_thumb "
                             "FROM episodes WHERE show_id=? ORDER BY season, path", (sid,))
    for e in s["episodes"]:
        e["thumb"] = paths.episode_files(e["path"])["thumb"]
    s["files"] = paths.show_files(s["folder"])
    return s


@app.post("/api/shows/scrape")
def shows_scrape(body: Ids):
    ids = body.ids or [r["id"] for r in db.query("SELECT id FROM shows WHERE status!='gescrapt'")]
    return _start(f"{len(ids)} Serie(n) scrapen", jobs.job_scrape_shows, ids, body.force)


@app.post("/api/shows/themes")
def shows_themes(body: Ids):
    return _start("Titelmelodien laden", jobs.job_themes, body.ids or None)


@app.post("/api/shows/{sid}/match")
def show_match(sid: int, body: Match):
    return _start("Serie zuordnen", jobs.job_scrape_one, "tv", sid, body.tmdb_id)


@app.post("/api/shows/{sid}/edit")
def show_edit(sid: int, body: Edit):
    s = db.one("SELECT * FROM shows WHERE id=?", (sid,))
    if not s or not s.get("meta"):
        raise HTTPException(400, "Serie muss zuerst gescrapt werden")
    meta = _apply_edit(s["meta"], body)
    nfo.write_tvshow(paths.show_files(s["folder"])["nfo"], meta)
    db.update("shows", sid, meta=meta, title=meta["title"], year=meta.get("year"), fsk=meta.get("fsk"))
    db.log("INFO", f"Serie bearbeitet: {meta['title']}")
    return show(sid)


# ---------------------------------------------------------------- Suche & Umbenennen

@app.get("/api/search")
def search(type: str, q: str, year: int | None = None):
    try:
        if q.strip().startswith("tt"):
            r = tmdb.find_imdb(q.strip())
            res = r.get("movie_results" if type == "movie" else "tv_results", [])
            return [{"tmdb_id": x["id"], "title": x.get("title") or x.get("name"),
                     "original_title": x.get("original_title") or x.get("original_name"),
                     "year": int((x.get("release_date") or x.get("first_air_date") or "0")[:4] or 0) or None,
                     "overview": x.get("overview"), "poster": tmdb.img(x.get("poster_path"), "w185")}
                    for x in res]
        if q.strip().isdigit():
            d = tmdb.movie(int(q)) if type == "movie" else tmdb.tv(int(q))
            return [{"tmdb_id": d["tmdb_id"], "title": d["title"], "original_title": d["original_title"],
                     "year": d["year"], "overview": d["plot"], "poster": tmdb.img(d["poster"], "w185")}]
        search = tmdb.search_movie if type == "movie" else tmdb.search_tv
        res = search(q, year)
        ids = {r["tmdb_id"] for r in res}
        for v, _, _ in scraper._alt_queries(q):   # Umlaute, Mehrteiler, Haupt-/Untertitel
            more = [r for r in search(v, year) if r["tmdb_id"] not in ids]
            ids.update(r["tmdb_id"] for r in more)
            res += more
        return res
    except tmdb.TMDBError as e:
        raise HTTPException(502, str(e))


class RenameReq(BaseModel):
    movie_ids: list[int] = []
    show_ids: list[int] = []


@app.post("/api/rename/preview")
def rename_preview(body: RenameReq):
    return renamer.preview(body.movie_ids, body.show_ids)


@app.post("/api/movies/stack")
def movies_stack(body: dict):
    ids = [int(i) for i in (body or {}).get("ids", [])]
    rows = [db.one("SELECT * FROM movies WHERE id=?", (i,)) for i in ids]
    todo = [r["id"] for r in rows if r and renamer.plan_stack(r)]
    if not todo:
        return {"started": None, "count": 0}
    res = _start(f"{len(todo)} Mehrteiler zusammenführen", jobs.job_stack, todo)
    return {**res, "started": True, "count": len(todo)}


@app.post("/api/movies/merge/preview")
def movies_merge_preview(body: dict):
    try:
        main, rest, parts = renamer.merge_prepare((body or {}).get("ids", []))
    except ValueError as e:
        raise HTTPException(400, str(e))
    plan = renamer.plan_stack(main)
    return {"title": main.get("title") or main.get("guess_title"), "parts": parts,
            "targets": [d for _, d in plan["parts"]] if plan else parts,
            "new_folder": plan["new_folder"] if plan else main["folder"]}


@app.post("/api/movies/merge")
def movies_merge(body: dict):
    ids = [int(i) for i in (body or {}).get("ids", [])]
    try:
        renamer.merge_prepare(ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _start("Filme zu einem verschmelzen", jobs.job_merge, ids)


@app.post("/api/rename/apply")
def rename_apply(body: RenameReq):
    plans = renamer.preview(body.movie_ids, body.show_ids)   # serverseitig neu berechnen
    if not plans:
        return {"started": None, "count": 0}
    # die Pläne werden erst beim Ausführen berechnet – ein davor laufender Job kann Titel/Pfade geändert haben
    res = _start(f"{len(plans)} Datei(en) umbenennen", jobs.job_rename_ids, body.movie_ids, body.show_ids)
    return {**res, "started": True, "count": len(plans)}


# ---------------------------------------------------------------- Protokoll

@app.get("/api/log")
def log(after: int = 0, limit: int = 300):
    return db.query("SELECT * FROM log WHERE id>? ORDER BY id DESC LIMIT ?", (after, limit))


@app.delete("/api/log")
def log_clear():
    db.execute("DELETE FROM log")
    return {"ok": True}


# ---------------------------------------------------------------- Bilder

def _allowed(path):
    real = os.path.realpath(path)
    roots = config.paths("movie_paths") + config.paths("tv_paths")
    return any(real.startswith(os.path.join(os.path.realpath(r), "")) for r in roots)


# ---------------------------------------------------------------- Artwork-Auswahl
ART_TYPES = {"movies": "movie", "shows": "tv"}


def _art_call(fn, *args):
    try:
        return fn(*args)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, tmdb.TMDBError) as e:
        raise HTTPException(502, str(e))


@app.get("/api/{section}/{iid}/art/{kind}")
def art_options(section: str, iid: int, kind: str):
    if section not in ART_TYPES:
        raise HTTPException(404)
    return _art_call(artpicker.options, ART_TYPES[section], iid, kind)


@app.post("/api/{section}/{iid}/art/{kind}")
def art_apply(section: str, iid: int, kind: str, body: dict):
    if section not in ART_TYPES:
        raise HTTPException(404)
    return _art_call(artpicker.apply, ART_TYPES[section], iid, kind, (body or {}).get("url", "").strip())


@app.delete("/api/{section}/{iid}/art/{kind}")
def art_unlock(section: str, iid: int, kind: str):
    if section not in ART_TYPES:
        raise HTTPException(404)
    return _art_call(artpicker.unlock, ART_TYPES[section], iid, kind)


@app.get("/api/image")
def image(path: str, w: int = 0):
    if not _allowed(path) or not os.path.isfile(path):
        raise HTTPException(404)
    if not w:
        return FileResponse(path)
    w = min(max(w, 50), 1920)
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}|{w}".encode()).hexdigest()
    cached = os.path.join(CACHE, key + ".jpg")
    if not os.path.exists(cached):
        try:
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((w, w * 3))
                im.save(cached, "JPEG", quality=82)
        except Exception:  # noqa – kaputtes Bild: Original ausliefern
            return FileResponse(path)
    return FileResponse(cached, headers={"Cache-Control": "max-age=86400"})


class _Static(StaticFiles):
    """Statische Dateien immer neu validieren lassen, damit nach Updates nichts Altes hängen bleibt."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers.update(NO_CACHE)
        return resp


app.mount("/static", _Static(directory=STATIC), name="static")


@app.get("/")
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("style.css", "app.js"):
        html = html.replace(f"/static/{asset}\"", f"/static/{asset}?v={VERSION}\"")
    html = html.replace("{{VERSION}}", VERSION)
    return HTMLResponse(html, headers=NO_CACHE)
