"""Radarr / Sonarr: IDs und Episodennummern abgleichen, Webhooks verarbeiten."""
import os

from . import config, db, pathmap
from .http import SourceError, request


def enabled(system):
    return bool(config.get(f"{system}_url") and config.get(f"{system}_api_key"))


def _get(system, path, **params):
    url = config.get(f"{system}_url").rstrip("/") + "/api/v3" + path
    r = request("GET", url, params=params, headers={"X-Api-Key": config.get(f"{system}_api_key")},
                min_interval=0.05)
    if r.status_code in (401, 403):
        raise SourceError(f"{system.capitalize()}: API-Key ungültig")
    if r.status_code != 200:
        raise SourceError(f"{system.capitalize()}: HTTP {r.status_code} bei {path}")
    return r.json()


def test(system):
    if not enabled(system):
        return {"ok": False, "msg": "URL oder API-Key fehlt"}
    try:
        s = _get(system, "/system/status")
        return {"ok": True, "msg": f"Verbunden: {s.get('appName', system)} {s.get('version', '')}"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}


# ---------------------------------------------------------------- Radarr

def sync_radarr():
    """Abgleich Filme. Offene Titel bekommen (optional) automatisch Radarrs TMDB-ID.
    -> (Bericht, IDs der Filme, die neu gescrapt werden sollen)"""
    movies = _get("radarr", "/movie")
    ours = {os.path.normpath(r["path"]): r for r in db.query("SELECT id, path, title, tmdb_id, status FROM movies")}
    issues, rescrape, seen, assigned = [], [], set(), 0
    with_file = [m for m in movies if m.get("hasFile") and (m.get("movieFile") or {}).get("path")]
    for m in with_file:
        local = os.path.normpath(pathmap.to_local("radarr", m["movieFile"]["path"]))
        r = ours.get(local)
        base = {"kind": "movie", "arr_title": f"{m.get('title')} ({m.get('year')})", "path": local,
                "tmdb_id": m.get("tmdbId")}
        if not r:
            issues.append({**base, "type": "fehlt",
                           "text": "Datei nicht in Scraparr – Pfadzuordnung prüfen oder scannen"})
            continue
        seen.add(local)
        base.update(id=r["id"], title=r["title"])
        tid = m.get("tmdbId")
        if not tid:
            continue
        if r["status"] != "gescrapt" or not r["tmdb_id"]:
            if config.get("arr_auto_ids"):
                db.update("movies", r["id"], tmdb_id=tid, imdb_id=m.get("imdbId") or None, status="neu",
                          message="TMDB-ID von Radarr übernommen")
                rescrape.append(r["id"])
                assigned += 1
            else:
                issues.append({**base, "type": "offen", "text": f"Radarr kennt TMDB {tid}", "action": "use_id"})
        elif int(r["tmdb_id"]) != int(tid):
            issues.append({**base, "type": "abweichung",
                           "text": f"Scraparr: TMDB {r['tmdb_id']} · Radarr: TMDB {tid}", "action": "use_id"})
    only_ours = [r for p, r in ours.items() if p not in seen]
    summary = {"radarr_filme": len(with_file), "ids_uebernommen": assigned,
               "abweichungen": sum(i["type"] == "abweichung" for i in issues),
               "fehlt_im_scraper": sum(i["type"] == "fehlt" for i in issues),
               "nur_im_scraper": len(only_ours)}
    return {"summary": summary, "issues": issues}, rescrape


# ---------------------------------------------------------------- Sonarr

def _sonarr_episode_map(series_id):
    """lokaler Dateipfad -> (staffel, [episoden]) laut Sonarr"""
    files = {f["id"]: os.path.normpath(pathmap.to_local("sonarr", f["path"]))
             for f in _get("sonarr", "/episodefile", seriesId=series_id) if f.get("path")}
    out = {}
    for e in _get("sonarr", "/episode", seriesId=series_id):
        fid = e.get("episodeFileId")
        if fid and fid in files:
            s, eps = out.setdefault(files[fid], (e["seasonNumber"], []))
            eps.append(e["episodeNumber"])
    return {p: (s, sorted(eps)) for p, (s, eps) in out.items()}


def apply_episode_numbers(show_id, series_id, only_unrecognized=True):
    """Sonarrs Nummerierung für (nicht erkannte) Episoden übernehmen. -> Anzahl, Abweichungen"""
    emap = _sonarr_episode_map(series_id)
    fixed, diffs = 0, []
    for e in db.query("SELECT id, path, season, episodes, status FROM episodes WHERE show_id=?", (show_id,)):
        s = emap.get(os.path.normpath(e["path"]))
        if not s:
            continue
        season, eps = s
        same = e["season"] == season and (e["episodes"] or []) == eps
        if same:
            continue
        unrecognized = e["season"] is None or e["status"] == "fehler"
        if unrecognized or not only_unrecognized:
            db.update("episodes", e["id"], season=season, episodes=eps, status="neu",
                      message="Nummer von Sonarr übernommen")
            fixed += 1
        else:
            diffs.append(f"{os.path.basename(e['path'])}: hier S{e['season']:02d}E{(e['episodes'] or [0])[0]:02d}, "
                         f"Sonarr S{season:02d}E{eps[0]:02d}")
    return fixed, diffs


def sync_sonarr():
    series = _get("sonarr", "/series")
    ours = {os.path.normpath(r["folder"]): r for r in db.query(
        "SELECT id, folder, title, tmdb_id, tvdb_id, status, hints FROM shows")}
    issues, rescrape, seen, assigned, ep_fixed = [], [], set(), 0, 0
    for s in series:
        local = os.path.normpath(pathmap.to_local("sonarr", s.get("path") or ""))
        r = ours.get(local)
        base = {"kind": "show", "arr_title": f"{s.get('title')} ({s.get('year')})", "path": local,
                "tmdb_id": s.get("tmdbId") or None, "tvdb_id": s.get("tvdbId") or None}
        if not r:
            if (s.get("statistics") or {}).get("episodeFileCount", 1):
                issues.append({**base, "type": "fehlt", "text": "Ordner nicht in Scraparr – Pfadzuordnung prüfen"})
            continue
        seen.add(local)
        base.update(id=r["id"], title=r["title"])
        hints = dict(r.get("hints") or {})
        changed_hints = False
        if s.get("tvdbId") and hints.get("tvdb_id") != s["tvdbId"]:
            hints["tvdb_id"] = s["tvdbId"]
            changed_hints = True
        if s.get("seriesType") == "anime" and hints.get("numbering") != "tvdb":
            hints["numbering"] = "tvdb"          # Sonarr zählt Anime nach TVDB
            changed_hints = True
        if changed_hints:
            db.update("shows", r["id"], hints=hints)
        tid = s.get("tmdbId") or None
        if r["status"] != "gescrapt" or not r["tmdb_id"]:
            if config.get("arr_auto_ids") and (tid or s.get("tvdbId")):
                db.update("shows", r["id"], tmdb_id=tid, status="neu",
                          message="ID von Sonarr übernommen")
                rescrape.append(r["id"])
                assigned += 1
            elif tid:
                issues.append({**base, "type": "offen", "text": f"Sonarr kennt TMDB {tid}", "action": "use_id"})
        elif tid and int(r["tmdb_id"]) != int(tid):
            issues.append({**base, "type": "abweichung",
                           "text": f"Scraparr: TMDB {r['tmdb_id']} · Sonarr: TMDB {tid}", "action": "use_id"})
        elif not tid and s.get("tvdbId") and r["tvdb_id"] and int(r["tvdb_id"]) != int(s["tvdbId"]):
            issues.append({**base, "type": "abweichung",
                           "text": f"Scraparr: TVDB {r['tvdb_id']} · Sonarr: TVDB {s['tvdbId']}", "action": "use_id"})
        # Episoden: nur bei Serien mit Problemen nachfragen (spart Anfragen)
        bad = db.one("SELECT COUNT(*) n FROM episodes WHERE show_id=? AND (season IS NULL OR status='fehler')",
                     (r["id"],))["n"]
        if bad and config.get("sonarr_episode_numbers"):
            fixed, diffs = apply_episode_numbers(r["id"], s["id"])
            ep_fixed += fixed
            if fixed and r["id"] not in rescrape:
                db.update("shows", r["id"], status="neu")
                rescrape.append(r["id"])
            for d in diffs[:20]:
                issues.append({**base, "type": "episode", "text": d, "series_id": s["id"], "action": "use_episodes"})
    only_ours = [r for p, r in ours.items() if p not in seen]
    summary = {"sonarr_serien": len(series), "ids_uebernommen": assigned, "episoden_korrigiert": ep_fixed,
               "abweichungen": sum(i["type"] in ("abweichung", "episode") for i in issues),
               "fehlt_im_scraper": sum(i["type"] == "fehlt" for i in issues),
               "nur_im_scraper": len(only_ours)}
    return {"summary": summary, "issues": issues}, rescrape


# ---------------------------------------------------------------- Aktionen aus dem Bericht

def apply(system, issues):
    """-> IDs (Filme bzw. Serien), die neu gescrapt werden müssen"""
    todo = []
    for i in issues:
        if not i.get("id"):
            continue
        if i.get("action") == "use_id":
            if i["kind"] == "movie":
                db.update("movies", i["id"], tmdb_id=i["tmdb_id"], status="neu", message="ID von Radarr übernommen")
            else:
                fields = {"status": "neu", "message": "ID von Sonarr übernommen"}
                if i.get("tmdb_id"):
                    fields["tmdb_id"] = i["tmdb_id"]
                else:
                    fields["tmdb_id"] = None
                    show = db.one("SELECT hints FROM shows WHERE id=?", (i["id"],))
                    fields["hints"] = {**(show.get("hints") or {}), "tvdb_id": i["tvdb_id"]}
                db.update("shows", i["id"], **fields)
            todo.append(i["id"])
        elif i.get("action") == "use_episodes" and i.get("series_id"):
            apply_episode_numbers(i["id"], i["series_id"], only_unrecognized=False)
            db.update("shows", i["id"], status="neu")
            todo.append(i["id"])
    return list(dict.fromkeys(todo))


# ---------------------------------------------------------------- Webhooks

def parse_webhook(system, payload):
    """Radarr-/Sonarr-Webhook -> Liste von Aufgaben für die Warteschlange."""
    ev = payload.get("eventType")
    tasks = []
    if system == "radarr":
        movie = payload.get("movie") or {}
        if ev == "Download" and (payload.get("movieFile") or {}).get("path"):
            new = pathmap.to_local("radarr", payload["movieFile"]["path"])
            for old in payload.get("deletedFiles") or []:       # Upgrade: Begleitdateien umhängen
                if old.get("path"):
                    tasks.append({"type": "rename", "old": pathmap.to_local("radarr", old["path"]), "new": new})
            tasks.append({"type": "movie", "path": new, "tmdb_id": movie.get("tmdbId") or None,
                          "imdb_id": movie.get("imdbId") or None})
        elif ev == "Rename":
            for f in payload.get("renamedMovieFiles") or []:
                tasks.append({"type": "rename", "old": pathmap.to_local("radarr", f.get("previousPath")),
                              "new": pathmap.to_local("radarr", f.get("path"))})
    else:
        series = payload.get("series") or {}
        folder = pathmap.to_local("sonarr", series.get("path")) if series.get("path") else None
        if ev == "Download" and (payload.get("episodeFile") or {}).get("path"):
            new = pathmap.to_local("sonarr", payload["episodeFile"]["path"])
            for old in payload.get("deletedFiles") or []:
                if old.get("path"):
                    tasks.append({"type": "rename", "old": pathmap.to_local("sonarr", old["path"]), "new": new})
            eps = payload.get("episodes") or []
            tasks.append({"type": "episode", "path": new, "folder": folder,
                          "season": eps[0]["seasonNumber"] if eps else None,
                          "episodes": sorted(e["episodeNumber"] for e in eps),
                          "tmdb_id": series.get("tmdbId") or None, "tvdb_id": series.get("tvdbId") or None,
                          "anime": series.get("type") == "anime"})
        elif ev == "Rename":
            for f in payload.get("renamedEpisodeFiles") or []:
                tasks.append({"type": "rename", "old": pathmap.to_local("sonarr", f.get("previousPath")),
                              "new": pathmap.to_local("sonarr", f.get("path"))})
    return ev, tasks
