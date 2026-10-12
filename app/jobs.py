"""Hintergrund-Jobs (immer nur einer gleichzeitig) und automatischer Lauf."""
import threading
import time
from datetime import datetime, timedelta

import json
import os
import shutil

from . import arr, config, db, emby, moviesets, renamer, scanner, scraper, tmdb
from .http import SourceError

_lock = threading.Lock()
state = {"running": False, "name": None, "done": 0, "total": 0, "current": "",
         "started": None, "last_auto": None, "next_auto": None, "cancel": False, "pending": 0}


def _progress(msg):
    state["current"] = msg


def _step():
    state["done"] += 1
    if state["cancel"]:
        raise InterruptedError


def start(name, fn, *args):
    """Startet einen Job in einem Thread. False, wenn schon einer läuft."""
    if not _lock.acquire(blocking=False):
        return False

    def run():
        state.update(running=True, name=name, done=0, total=0, current="", cancel=False,
                     started=datetime.now().isoformat(timespec="seconds"))
        changed = False
        emby.take_touched()          # Reste eines früheren Jobs verwerfen
        try:
            changed = fn(*args)
        except InterruptedError:
            db.log("WARN", f"Job abgebrochen: {name}")
        except (tmdb.TMDBError, SourceError) as e:
            db.log("ERROR", f"Job '{name}' gestoppt: {e}")
        except Exception as e:  # noqa – Job darf den Server nie mitreißen
            db.log("ERROR", f"Job '{name}' Fehler: {type(e).__name__}: {e}")
        finally:
            touched = emby.take_touched()
            if changed and touched:
                emby.notify(touched)
            state.update(running=False, name=None, current="", cancel=False)
            _lock.release()

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------------------------------------------------------------- Job-Funktionen

def job_scan(kind="all"):
    if kind in ("all", "movies"):
        _progress("Filme werden gescannt …")
        scanner.scan_movies(_progress)
    if kind in ("all", "tv"):
        _progress("Serien werden gescannt …")
        scanner.scan_shows(_progress)
    return False


def job_scrape_movies(ids, force=False, auto=False):
    state["total"] = len(ids)
    changed = False
    for mid in ids:
        m = db.one("SELECT guess_title, title FROM movies WHERE id=?", (mid,))
        _progress(f"Film: {(m or {}).get('title') or (m or {}).get('guess_title')}")
        changed |= scraper.scrape_movie(mid, force=force, auto=auto)
        _step()
    if changed:
        _update_sets(ids)
    return changed


def _update_sets(movie_ids):
    """Reihen der gerade gescrapten Filme von TMDB laden und – sofern die Filme schon in Emby sind – dort pflegen."""
    sets = set()
    for mid in movie_ids:
        r = db.one("SELECT meta FROM movies WHERE id=?", (mid,))
        s = ((r or {}).get("meta") or {}).get("set") if isinstance((r or {}).get("meta"), dict) else None
        if s and s.get("tmdb_id"):
            sets.add(int(s["tmdb_id"]))
    if not sets:
        return
    try:
        moviesets.refresh(sets, progress=_progress)
        moviesets.emby_sync(sets, progress=_progress)
    except Exception as e:  # noqa – Kollektionen dürfen den Scrape-Lauf nie scheitern lassen
        db.log("WARN", f"Kollektionen: {e}")


def job_themes(show_ids=None):
    """Titelmelodien für alle (oder ausgewählte) gescrapten Serien nachladen, die noch keine haben."""
    q = "SELECT id, folder, title, tvdb_id FROM shows WHERE status='gescrapt' AND COALESCE(has_theme,0)=0"
    rows = db.query(q)
    if show_ids:
        rows = [r for r in rows if r["id"] in set(show_ids)]
    state["total"] = len(rows)
    got = none = 0
    for r in rows:
        _progress(f"Titelmelodie: {r['title']}")
        res = scraper.download_theme(r["folder"], r["tvdb_id"])
        if res == "ok":
            got += 1
            emby.touch(r["folder"])
        elif res == "keins":
            none += 1
        db.update("shows", r["id"], has_theme=int(scraper.paths.has_theme(r["folder"])))
        _step()
    db.log("INFO", f"Titelmelodien: {got} geladen, {none} Serien ohne verfügbares Theme")
    return got > 0


def job_collections(action, ids=None):
    if action == "refresh":
        _progress("Kollektionen von TMDB laden …")
        moviesets.refresh(ids, force=True, progress=_progress)
    else:
        _progress(f"Kollektionen in {emby.name()} pflegen …")
        moviesets.emby_sync(ids, force_images=bool(ids), progress=_progress)
    return False


def job_scrape_shows(ids, force=False, auto=False):
    state["total"] = len(ids)
    changed = False
    for sid in ids:
        s = db.one("SELECT guess_title, title FROM shows WHERE id=?", (sid,))
        _progress(f"Serie: {(s or {}).get('title') or (s or {}).get('guess_title')}")
        changed |= scraper.scrape_show(sid, force=force, auto=auto, progress=_progress)
        _step()
    return changed


def job_scrape_one(kind, rid, tmdb_id):
    state["total"] = 1
    if kind == "movie":
        r = scraper.scrape_movie(rid, tmdb_id=tmdb_id, force=True)
    else:
        r = scraper.scrape_show(rid, tmdb_id=tmdb_id, force=True, progress=_progress)
    _step()
    return r


def job_stack(movie_ids):
    """Mehrteilige Filme für Emby/Jellyfin zusammenführen: Teile als <Name>-cd1/-cd2 in den Filmordner,
    NFO als movie.nfo neu schreiben, Server informieren."""
    from . import nfo, paths
    state["total"] = len(movie_ids)
    changed = False
    for mid in movie_ids:
        m = db.one("SELECT * FROM movies WHERE id=?", (mid,))
        p = renamer.plan_stack(m) if m else None
        if p:
            p["conflict"] = next((f"Ziel existiert bereits: {os.path.basename(d)}" for s, d in p["parts"]
                                  if os.path.exists(d) and os.path.normcase(s) != os.path.normcase(d)), None)
            done, errors = renamer.apply([p])
            if done:
                m = db.one("SELECT * FROM movies WHERE id=?", (mid,))
                if m.get("meta"):
                    nfo.write_movie(paths.movie_files(m["path"], bool(m["single_folder"]))["nfo"], m["meta"])
                st = paths.movie_status(m["path"], bool(m["single_folder"]))
                db.update("movies", mid, **{k: int(v) for k, v in st.items()})
                emby.touch(m["path"])
                db.log("INFO", f"Mehrteiler zusammengeführt: {os.path.basename(m['folder'])} ({len(m['part_files'])} Teile)")
                changed = True
        _step()
    return changed


def job_rename(plans):
    state["total"] = len(plans)
    done, errors = renamer.apply(plans)
    state["done"] = len(plans)
    for p in plans:
        if not p.get("conflict"):
            emby.touch(p["new"] if p["type"] == "movie" else scanner.show_folder_of(p["new"]))
    return done > 0


def job_auto():
    """Scan → neue Titel scrapen → (optional) umbenennen → Emby aktualisieren."""
    state["last_auto"] = datetime.now().isoformat(timespec="seconds")
    db.log("INFO", "Automatischer Lauf gestartet")
    job_scan("all")
    changed = False
    if config.get("auto_scrape_new"):
        movies = [r["id"] for r in db.query("SELECT id FROM movies WHERE status='neu'")]
        shows = [r["id"] for r in db.query("SELECT id FROM shows WHERE status='neu'")]
        if movies:
            changed |= job_scrape_movies(movies, auto=True)
        if shows:
            state["done"] = 0
            changed |= job_scrape_shows(shows, auto=True)
        if config.get("auto_rename") and changed:
            plans = renamer.preview(movies, shows)
            if plans:
                renamer.apply(plans)
    try:   # Reihen auffrischen (nur veraltete) und Emby-Kollektionen nachziehen – auch für Filme vom letzten Lauf
        moviesets.refresh(progress=_progress)
        moviesets.emby_sync(progress=_progress)
    except Exception as e:  # noqa
        db.log("WARN", f"Kollektionen: {e}")
    db.log("INFO", "Automatischer Lauf beendet")
    return changed


# ---------------------------------------------------------------- Abgleich Emby / Radarr / Sonarr

def save_report(system, report):
    db.execute("INSERT INTO sync (system, ts, data) VALUES (?,?,?) "
               "ON CONFLICT(system) DO UPDATE SET ts=excluded.ts, data=excluded.data",
               (system, db.now(), json.dumps(report, ensure_ascii=False)))


def job_sync(system):
    _progress(f"Abgleich mit {system.capitalize()} …")
    if system == "emby":
        save_report("emby", emby.sync())
        db.log("INFO", f"{emby.name()}-Abgleich fertig")
        return False
    job_scan("movies" if system == "radarr" else "tv")
    _progress(f"Abgleich mit {system.capitalize()} …")
    report, rescrape = arr.sync_radarr() if system == "radarr" else arr.sync_sonarr()
    save_report(system, report)
    db.log("INFO", f"{system.capitalize()}-Abgleich: {report['summary']}")
    if rescrape:
        state["done"] = 0
        return (job_scrape_movies if system == "radarr" else job_scrape_shows)(rescrape)
    return False


def job_sync_apply(system, issues):
    if system == "emby":
        emby.apply(issues)
        return False
    ids = arr.apply(system, issues)
    if not ids:
        return False
    return (job_scrape_movies if system == "radarr" else job_scrape_shows)(ids, True)


# ---------------------------------------------------------------- Webhooks (Radarr/Sonarr)

_pending = {"tasks": [], "last": 0.0}
_plock = threading.Lock()


def enqueue(tasks):
    with _plock:
        for t in tasks:
            if t not in _pending["tasks"]:
                _pending["tasks"].append(t)
        _pending["last"] = time.time()
    state["pending"] = len(_pending["tasks"])


def _move_sidecars(old, new):
    """Begleitdateien (NFO, Bilder, Untertitel, Trailer) einer umbenannten/ersetzten Datei mitnehmen."""
    if not old or not new or os.path.normpath(old) == os.path.normpath(new):
        return 0
    new_base = os.path.splitext(new)[0]
    moved = 0
    for f, rest in renamer._sidecars(old):
        target = new_base + rest
        if not os.path.exists(target):
            shutil.move(f, target)
            moved += 1
    emby.moved(old, new)
    for table in ("movies", "episodes"):
        db.execute(f"UPDATE {table} SET path=? WHERE path=?", (new, old))
    db.execute("UPDATE movies SET folder=? WHERE path=?", (os.path.dirname(new), new))
    return moved


def job_pending():
    with _plock:
        tasks, _pending["tasks"] = _pending["tasks"], []
    state["pending"] = 0
    state["total"] = len(tasks)
    changed = False
    shows = {}
    for t in tasks:
        try:
            if t["type"] == "rename":
                n = _move_sidecars(t["old"], t["new"])
                db.log("INFO", f"Webhook: {os.path.basename(t['old'])} → {os.path.basename(t['new'])} "
                               f"({n} Begleitdateien verschoben)")
                emby.touch(t["new"] if scanner._root_of(t["new"], config.paths("movie_paths"))
                           else scanner.show_folder_of(t["new"]))
                changed = True
            elif t["type"] == "movie":
                _progress(f"Webhook: {os.path.basename(t['path'])}")
                row = scanner.scan_movie_path(t["path"])
                if not row:
                    db.log("WARN", f"Webhook: Film nicht gefunden: {t['path']} (Pfadzuordnung prüfen)")
                    continue
                if t.get("imdb_id") and not row.get("imdb_id"):
                    db.update("movies", row["id"], imdb_id=t["imdb_id"])
                changed |= scraper.scrape_movie(row["id"], tmdb_id=t.get("tmdb_id"))
            elif t["type"] == "episode":
                folder = t.get("folder") or scanner.show_folder_of(t["path"])
                sid = scanner.scan_show_folder(folder)
                if not sid:
                    db.log("WARN", f"Webhook: Serienordner nicht gefunden: {folder} (Pfadzuordnung prüfen)")
                    continue
                if t.get("season") is not None and t.get("episodes"):
                    ep = db.one("SELECT id FROM episodes WHERE path=?", (t["path"],))
                    if ep:   # Sonarr kennt die Nummer sicher – Vorrang vor der Dateinamen-Erkennung
                        db.update("episodes", ep["id"], season=t["season"], episodes=t["episodes"],
                                  status="neu", message=None)
                show = db.one("SELECT hints, tmdb_id FROM shows WHERE id=?", (sid,))
                hints = dict(show.get("hints") or {})
                if t.get("tvdb_id"):
                    hints["tvdb_id"] = t["tvdb_id"]
                if t.get("anime"):
                    hints["numbering"] = "tvdb"
                db.update("shows", sid, hints=hints)
                shows[sid] = t.get("tmdb_id") or show.get("tmdb_id")
        except (OSError, SourceError, tmdb.TMDBError) as e:
            db.log("ERROR", f"Webhook-Aufgabe fehlgeschlagen ({t.get('type')}): {e}")
        _step()
    for sid, tmdb_id in shows.items():
        _progress("Webhook: Serie aktualisieren …")
        changed |= scraper.scrape_show(sid, tmdb_id=tmdb_id, progress=_progress)
    return changed


# ---------------------------------------------------------------- Scheduler

def scheduler():
    time.sleep(20)   # Server erst einmal hochfahren lassen
    next_run = datetime.now()
    while True:
        try:
            delay = max(5, config.get("webhook_delay_sec"))
            if _pending["tasks"] and time.time() - _pending["last"] > delay:
                start("Webhook-Verarbeitung", job_pending)
            if config.get("auto_enabled") and config.get("tmdb_api_key"):
                interval = max(5, config.get("auto_interval_min"))
                state["next_auto"] = next_run.isoformat(timespec="seconds")
                if datetime.now() >= next_run:
                    if start("Automatischer Lauf", job_auto):
                        next_run = datetime.now() + timedelta(minutes=interval)
                    else:
                        next_run = datetime.now() + timedelta(minutes=2)   # anderer Job läuft
            else:
                state["next_auto"] = None
                next_run = datetime.now()
        except Exception as e:  # noqa
            db.log("ERROR", f"Scheduler: {e}")
        time.sleep(5)
