"""Bibliotheken durchsuchen und mit der Datenbank abgleichen."""
import json
import os
import re

from . import config, db, parser, paths


def _walk_videos(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not parser.skip_dir(d))
        if "BDMV" in dirnames or "VIDEO_TS" in dirnames:
            db.log("WARN", f"Disc-Struktur übersprungen: {dirpath}")
            dirnames[:] = []
            continue
        vids = sorted(f for f in filenames if parser.is_video(f))
        yield dirpath, [os.path.join(dirpath, f) for f in vids]


def _hints(guess, nfo_ids):
    """Zusätzliche IDs aus Ordner-/Dateinamen und vorhandener NFO (tvdb, anidb, anilist, mal …)."""
    h = {k: v for k, v in guess.items() if k.endswith("_id") and k not in ("tmdb_id", "imdb_id")}
    for k, v in nfo_ids.items():
        if k not in ("tmdb_id", "imdb_id"):
            h.setdefault(k, v)
    return h


def _root_of(path, roots):
    for r in roots:
        if os.path.join(os.path.normpath(path), "").startswith(os.path.join(os.path.normpath(r), "")):
            return r
    return None


def _drop_trailers(vids):
    """Liegen mehrere Videos im Ordner, sind die mit "Trailer"/"Teaser" im Namen Beiwerk, kein eigener Film."""
    if len(vids) < 2:
        return vids
    keep = [v for v in vids if not parser.mentions_trailer(v)]
    return keep or vids


def cleanup_extras():
    """Bereits erfasste Trailer/Zwischendateien wieder aus der Filmliste entfernen."""
    rows = db.query("SELECT id, path, folder FROM movies")
    by_folder = {}
    for r in rows:
        by_folder.setdefault(r["folder"], []).append(r["path"])
    removed = 0
    for r in rows:
        bad = not parser.is_video(r["path"]) or r["path"] not in _drop_trailers(by_folder[r["folder"]])
        if bad:
            db.execute("DELETE FROM movies WHERE id=?", (r["id"],))
            removed += 1
    if removed:
        db.log("INFO", f"{removed} Trailer/Zwischendateien aus der Filmliste entfernt")
    return removed


def _merge_cd_dirs(found, root):
    """{ordner: [videos]} -> {ordner: [videos | ("cd", [teil1, teil2])]}.
    Film/CD1/x.avi + Film/CD2/y.avi werden als ein mehrteiliger Film dem Ordner "Film" zugeschlagen."""
    out, cds = {}, {}
    for d, vids in found.items():
        m = parser.CD_DIR.match(os.path.basename(d))
        if m and len(vids) == 1 and os.path.normpath(d) != os.path.normpath(root):
            cds.setdefault(os.path.dirname(d), {})[int(m.group(1))] = vids[0]
        elif vids:
            out.setdefault(d, []).extend(vids)
    for parent, byn in cds.items():
        if len(byn) >= 2 and not out.get(parent):
            out[parent] = [("cd", [byn[n] for n in sorted(byn)])]
        else:
            for f in byn.values():
                out.setdefault(os.path.dirname(f), []).append(f)
    return out


def _group_parts(items):
    """Dateien, die sich nur in der Teilangabe unterscheiden (Film-cd1/Film-cd2), zu einem Film bündeln."""
    out, byb = [], {}
    files = _drop_trailers([i for i in items if not isinstance(i, tuple)])
    for it in items:
        if isinstance(it, tuple):
            out.append(it[1])
    for f in files:
        base, n = parser.part_key(os.path.splitext(os.path.basename(f))[0])
        if n is None:
            out.append([f])
        else:
            byb.setdefault(base, {}).setdefault(n, f)
    for byn in byb.values():
        if len(byn) >= 2:
            out.append([byn[n] for n in sorted(byn)])
        else:
            out.extend([f] for f in byn.values())
    return sorted(out, key=lambda g: g[0])


def _movie_groups(root):
    """-> [(ordner, [[teil1, teil2], [film], …])] für eine Filmbibliothek."""
    found = {d: vids for d, vids in _walk_videos(root) if vids}
    return [(d, _group_parts(items)) for d, items in sorted(_merge_cd_dirs(found, root).items())]


def _scan_movie_dir(root, dirpath, groups, seen, progress=None):
    new = 0
    single = len(groups) == 1 and os.path.normpath(dirpath) != os.path.normpath(root)
    for group in groups:
        v = group[0]
        parts = group if len(group) > 1 else None
        seen.update(group)
        status = paths.movie_status(v, single)
        existing = db.one("SELECT id, status, tmdb_id, guess_title, guess_year, part_files FROM movies WHERE path=?", (v,))
        if existing:
            extra = {}
            if existing["status"] in ("neu", "manuell", "fehler") and not existing["tmdb_id"]:
                # noch nicht zugeordnet: Suchtitel mit der aktuellen Erkennung neu bestimmen
                g = parser.guess_movie(v, single)
                if (g["title"], g["year"]) != (existing["guess_title"], existing["guess_year"]):
                    extra = {"guess_title": g["title"], "guess_year": g["year"], "title": g["title"],
                             "year": g["year"], "status": "neu", "message": None}
            if (existing.get("part_files") or None) != parts:
                extra["part_files"] = parts
                if parts:
                    db.log("INFO", f"Mehrteiliger Film erkannt ({len(parts)} Teile): {os.path.basename(v)}")
            db.update("movies", existing["id"], single_folder=int(single), missing=0, folder=dirpath,
                      **{k: int(x) for k, x in status.items()}, **extra)
            continue
        g = parser.guess_movie(v, single)
        nfo = paths.existing_movie_nfo(v, single)
        ids = parser.read_nfo_ids(nfo) if nfo else {}
        tmdb_id = ids.get("tmdb_id") or g.get("tmdb_id")
        imdb_id = ids.get("imdb_id") or g.get("imdb_id")
        db.execute(
            "INSERT INTO movies (path, folder, single_folder, guess_title, guess_year, "
            "tmdb_id, imdb_id, title, year, has_nfo, has_poster, has_fanart, has_trailer, "
            "added, updated, hints, part_files) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v, dirpath, int(single), g["title"], g["year"], tmdb_id, imdb_id,
             g["title"], g["year"], *(int(x) for x in status.values()), db.now(), db.now(),
             json.dumps(_hints(g, ids)), json.dumps(parts) if parts else None))
        new += 1
        if progress:
            progress(f"Neu: {g['title']} ({g['year'] or '?'})")
    return new


def scan_movies(progress=None):
    seen, new = set(), 0
    cleanup_extras()
    for root in config.paths("movie_paths"):
        if not os.path.isdir(root):
            db.log("ERROR", f"Filmpfad nicht gefunden: {root}")
            continue
        for dirpath, groups in _movie_groups(root):
            new += _scan_movie_dir(root, dirpath, groups, seen, progress)
    _mark_missing("movies", "path", seen, config.paths("movie_paths"))
    db.log("INFO", f"Filmscan fertig: {len(seen)} Filme, {new} neu")
    return new


def scan_movie_path(path):
    """Gezielt nur den Ordner einer Videodatei einlesen (Webhook). -> Film-Datensatz oder None"""
    root = _root_of(path, config.paths("movie_paths"))
    if not root:
        db.log("WARN", f"Pfad liegt in keiner Filmbibliothek: {path}")
        return None
    dirpath = os.path.dirname(path)
    if parser.CD_DIR.match(os.path.basename(dirpath)):
        dirpath = os.path.dirname(dirpath)          # Film/CD2/x.avi -> Film
    try:
        found = {dirpath: sorted(os.path.join(dirpath, f) for f in os.listdir(dirpath) if parser.is_video(f))}
        for d in os.listdir(dirpath):
            sub = os.path.join(dirpath, d)
            if parser.CD_DIR.match(d) and os.path.isdir(sub):
                found[sub] = sorted(os.path.join(sub, f) for f in os.listdir(sub) if parser.is_video(f))
    except OSError as e:
        db.log("WARN", f"Ordner nicht lesbar: {dirpath}: {e}")
        return None
    seen = set()
    merged = _merge_cd_dirs(found, root)
    for d, items in merged.items():
        _scan_movie_dir(root, d, _group_parts(items), seen)
    if path in seen and not db.one("SELECT id FROM movies WHERE path=?", (path,)):
        row = db.one("SELECT * FROM movies WHERE part_files LIKE ?", (f'%{json.dumps(path)[1:-1]}%',))
        if row:
            return row
    for r in db.query("SELECT id, path FROM movies WHERE folder=?", (dirpath,)):
        if r["path"] not in seen and not os.path.exists(r["path"]):
            db.execute("DELETE FROM movies WHERE id=?", (r["id"],))
    return db.one("SELECT * FROM movies WHERE path=?", (path,))


def _scan_show_folder(folder, seen_eps, progress=None):
    """-> (show_id, neu?)"""
    st = paths.show_status(folder)
    show = db.one("SELECT id FROM shows WHERE folder=?", (folder,))
    isnew = False
    if show:
        sid = show["id"]
        db.update("shows", sid, missing=0, **{k: int(x) for k, x in st.items()})
    else:
        g = parser.guess_show_folder(folder)
        ids = parser.read_nfo_ids(os.path.join(folder, "tvshow.nfo"))
        sid = db.execute(
            "INSERT INTO shows (folder, guess_title, guess_year, tmdb_id, imdb_id, title, year, "
            "has_nfo, has_poster, has_fanart, has_trailer, added, updated, hints) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (folder, g["title"], g["year"], ids.get("tmdb_id") or g.get("tmdb_id"),
             ids.get("imdb_id") or g.get("imdb_id"), g["title"], g["year"],
             *(int(x) for x in st.values()), db.now(), db.now(), json.dumps(_hints(g, ids))))
        isnew = True
        if progress:
            progress(f"Neue Serie: {g['title']}")
    db.update("shows", sid, has_theme=int(paths.has_theme(folder)))
    for _, vids in _walk_videos(folder):
        for v in vids:
            seen_eps.add(v)
            est = paths.episode_status(v)
            ep = db.one("SELECT id, status FROM episodes WHERE path=?", (v,))
            if ep:
                db.update("episodes", ep["id"], missing=0, show_id=sid,
                          **{k: int(x) for k, x in est.items()})
                continue
            g = parser.guess_episode(v, folder)
            if g:
                db.execute(
                    "INSERT INTO episodes (show_id, path, season, episodes, has_nfo, has_thumb, "
                    "added, updated) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, v, g["season"], json.dumps(g["episodes"]),
                     *(int(x) for x in est.values()), db.now(), db.now()))
                # neue Episode -> Serie muss nachgescrapt werden
                db.execute("UPDATE shows SET status='neu' WHERE id=? AND status='gescrapt'", (sid,))
            else:
                db.execute(
                    "INSERT INTO episodes (show_id, path, status, message, added, updated) "
                    "VALUES (?,?,?,?,?,?)",
                    (sid, v, "fehler", "Staffel/Episode nicht erkannt", db.now(), db.now()))
    return sid, isnew


# ---------------------------------------------------------------- Serienordner erkennen
# Unterstützt flache Bibliotheken (serien/Dark/…) und Kategorie-/Genre-Ordner (serien/09 - Action/Dark/…)
# in beliebiger Tiefe. Ein Ordner ist eine Serie, wenn er Staffelordner, eine tvshow.nfo, eine ID/ein Jahr im
# Namen oder direkt Videodateien (ohne weitere Unterordner mit Videos) enthält. Sonst = Kategorie -> tiefer suchen.

MAX_DEPTH = 4
YEAR_TAG = re.compile(r"\((?:19|20)\d{2}\)|\[(?:tmdb|tvdb|anidb|anilist|mal|imdb)", re.I)


def _ignored(name):
    ign = {x.strip().lower() for x in (config.get("tv_ignore_folders") or "").split(",") if x.strip()}
    return name.lower() in ign or parser.skip_dir(name)


def _listdir(folder):
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError:
        return [], []
    dirs = sorted(e.name for e in entries if e.is_dir(follow_symlinks=True) and not _ignored(e.name))
    vids = [e.name for e in entries if e.is_file() and parser.is_video(e.name)]
    return dirs, vids


def _has_videos(folder, depth=0):
    """Enthält der Ordner (oder seine Staffelordner) Videos? Flach geprüft, damit es auf NAS-Freigaben schnell bleibt."""
    dirs, vids = _listdir(folder)
    if vids:
        return True
    return depth < 1 and any(_has_videos(os.path.join(folder, d), depth + 1) for d in dirs)


def classify(folder):
    """-> "show" | "category" | "empty" """
    name = os.path.basename(folder.rstrip(os.sep))
    dirs, vids = _listdir(folder)
    if os.path.exists(os.path.join(folder, "tvshow.nfo")) or YEAR_TAG.search(name):
        return "show"
    if any(parser.is_season_dir(d) for d in dirs):
        return "show"
    sub_with_videos = [d for d in dirs if _has_videos(os.path.join(folder, d))]
    if vids and not sub_with_videos:
        return "show"
    if sub_with_videos:
        if vids:
            db.log("WARN", f"Lose Videodateien in Kategorieordner ignoriert: {folder} ({len(vids)} Dateien)")
        return "category"
    return "empty"


def find_show_folders(root):
    out = []

    def walk(folder, depth):
        dirs, _ = _listdir(folder)
        for d in dirs:
            path = os.path.join(folder, d)
            kind = classify(path)
            if kind == "show":
                out.append(path)
            elif kind == "category" and depth < MAX_DEPTH:
                walk(path, depth + 1)

    walk(root, 0)
    return out


def scan_shows(progress=None):
    seen_shows, seen_eps, new = set(), set(), 0
    for root in config.paths("tv_paths"):
        if not os.path.isdir(root):
            db.log("ERROR", f"Serienpfad nicht gefunden: {root}")
            continue
        if progress:
            progress(f"Suche Serienordner in {root} …")
        for folder in find_show_folders(root):
            seen_shows.add(folder)
            _, isnew = _scan_show_folder(folder, seen_eps, progress)
            new += int(isnew)
    roots = config.paths("tv_paths")
    # Serien: nicht mehr erkannte Ordner entfernen, auch wenn sie noch existieren (z. B. früher fälschlich
    # als Serie erkannte Kategorie-Ordner). Ihre Episoden sind zu diesem Zeitpunkt schon neu zugeordnet.
    _mark_missing("shows", "folder", seen_shows, roots, require_gone=False)
    _mark_missing("episodes", "path", seen_eps, roots)
    db.log("INFO", f"Serienscan fertig: {len(seen_shows)} Serien, {len(seen_eps)} Episoden, {new} neue Serien")
    return new


def show_folder_of(path):
    """Serienordner zu einem Datei- oder Ordnerpfad – auch unterhalb von Kategorie-Ordnern."""
    root = _root_of(path, config.paths("tv_paths"))
    if not root:
        return None
    root = os.path.normpath(root)
    cur = os.path.normpath(path if os.path.isdir(path) else os.path.dirname(path))
    # Staffel-/Extras-Ordner überspringen
    while cur != root and (parser.is_season_dir(os.path.basename(cur)) or parser.skip_dir(os.path.basename(cur))):
        cur = os.path.dirname(cur)
    if cur == root:
        return None
    # bekannter Serienordner? dann diesen nehmen
    probe = cur
    while probe != root:
        if db.one("SELECT id FROM shows WHERE folder=?", (probe,)):
            return probe
        probe = os.path.dirname(probe)
    return cur


def scan_show_folder(folder):
    """Gezielt eine Serie einlesen (Webhook). -> show_id oder None"""
    if not folder or not os.path.isdir(folder):
        return None
    seen = set()
    sid, _ = _scan_show_folder(folder, seen)
    for r in db.query("SELECT id, path FROM episodes WHERE show_id=?", (sid,)):
        if r["path"] not in seen and not os.path.exists(r["path"]):
            db.execute("DELETE FROM episodes WHERE id=?", (r["id"],))
    return sid


def _reachable(root):
    """Leerer oder fehlender Pfad = vermutlich nicht gemountetes NAS -> nichts löschen."""
    try:
        return os.path.isdir(root) and bool(os.listdir(root))
    except OSError:
        return False


def _mark_missing(table, col, seen, roots, require_gone=True):
    roots = [os.path.join(os.path.normpath(r), "") for r in roots if _reachable(r)]
    for r in db.query(f"SELECT id, {col} FROM {table}"):
        if r[col] in seen or not any(r[col].startswith(x) for x in roots):
            continue
        if not require_gone or not os.path.exists(r[col]):
            db.execute(f"DELETE FROM {table} WHERE id=?", (r["id"],))
