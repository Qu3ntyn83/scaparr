"""Umbenennen nach Muster – mit Vorschau. Begleitdateien (NFO, Bilder, Untertitel, Trailer) wandern mit."""
import glob
import os
import re

from . import config, db

ILLEGAL = re.compile(r'[<>"/\\|?*\x00-\x1f]')


def clean(name):
    name = name.replace(": ", " - ").replace(":", "-")
    name = ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:200]


def _fmt(pattern, **values):
    class D(dict):
        def __missing__(self, k):
            return ""
    try:
        out = pattern.format_map(D(values))
    except (ValueError, KeyError, IndexError) as e:
        raise ValueError(f"Ungültiges Umbenennungsmuster '{pattern}': {e}")
    out = re.sub(r"\(\s*\)", "", out)          # leere "()" bei fehlendem Jahr
    return clean(out)


def _sidecars(video_path):
    """Alle Dateien mit gleichem Stamm: film.nfo, film-poster.jpg, film.de.srt, film-trailer.mp4 …"""
    base = os.path.splitext(video_path)[0]
    out = []
    for f in glob.glob(glob.escape(base) + "*"):
        rest = f[len(base):]
        if f == video_path or not os.path.isfile(f):
            continue
        name, ext = os.path.splitext(rest)
        ext = ext.lower().lstrip(".")
        if rest.startswith("-"):
            # film-poster.jpg, film-trailer.mkv, film-thumb.jpg … – aber nicht "film-2.mkv"
            if name[1:].lower() in ART_SUFFIX:
                out.append((f, rest))
        elif rest.startswith("."):
            # film.nfo, film.de.srt, film.ger.forced.srt – aber nicht "film.reloaded.mkv"
            parts = rest[1:].split(".")
            ext, middle = parts[-1].lower(), parts[:-1]
            if ext in SIDE_EXT and all(len(x) <= 7 for x in middle) and len(middle) <= 3:
                out.append((f, rest))
    return out


SIDE_EXT = {"nfo", "srt", "ass", "ssa", "sub", "idx", "sup", "vtt", "smi", "jpg", "jpeg", "png", "tbn", "txt"}
ART_SUFFIX = {"poster", "fanart", "thumb", "trailer", "clearlogo", "logo", "landscape", "banner",
              "clearart", "disc", "discart", "backdrop", "keyart"}


EMBY_PART = re.compile(r"^(.*)-cd([1-9])$", re.I)


def stack_ok(m):
    """Erkennt Emby/Jellyfin die Teile als einen Film? Nur bei "<Name>-cd1.ext", "<Name>-cd2.ext" im Filmordner."""
    parts = m.get("part_files") or []
    if len(parts) < 2:
        return True
    bases = set()
    for i, p in enumerate(parts, 1):
        mm = EMBY_PART.match(os.path.splitext(os.path.basename(p))[0])
        if os.path.dirname(p) != m["folder"] or not mm or int(mm.group(2)) != i:
            return False
        bases.add(mm.group(1))
    return len(bases) == 1 and bool(m.get("single_folder"))


def _part_targets(m, new_folder, new_stem):
    return [os.path.join(new_folder, f"{new_stem}-cd{i}{os.path.splitext(p)[1]}")
            for i, p in enumerate(m["part_files"], 1)]


def plan_stack(m):
    """Teile in Emby-taugliche Namen bringen (<Name>-cd1/-cd2 im Filmordner), ohne den Titel zu ändern."""
    from . import parser
    parts = m.get("part_files") or []
    if len(parts) < 2 or stack_ok(m):
        return None
    if m.get("single_folder"):
        base = os.path.basename(m["folder"])          # eigener Ordner: Teile heißen wie der Ordner
        folder = m["folder"]
    else:
        # gemischter Ordner: Emby/Jellyfin bündeln Teile nur im eigenen Filmordner -> Ordner anlegen
        meta = m.get("meta") or {}
        if meta.get("title"):
            base = _fmt(config.get("rename_movie_folder"), title=meta.get("title"), year=meta.get("year") or "",
                        original_title=meta.get("original_title"), fsk=meta.get("fsk") or "",
                        tmdb_id=meta.get("tmdb_id"))
        else:
            base = parser.part_info(os.path.splitext(os.path.basename(parts[0]))[0])[0] or "Film"
            base = clean(base.replace(".", " ") if "." in base and " " not in base else base)
        folder = os.path.join(m["folder"], base)
    targets = _part_targets(m, folder, clean(base))
    return {"id": m["id"], "type": "movie", "old": parts[0], "new": targets[0], "old_folder": m["folder"],
            "new_folder": folder, "parts": list(zip(parts, targets)), "own_folder": folder != m["folder"]}


def plan_movie(m):
    meta = m.get("meta") or {}
    if not meta:
        return plan_stack(m)
    vals = {"title": meta.get("title"), "year": meta.get("year") or "",
            "original_title": meta.get("original_title"), "fsk": meta.get("fsk") or "",
            "tmdb_id": meta.get("tmdb_id")}
    ext = os.path.splitext(m["path"])[1]
    new_stem = _fmt(config.get("rename_movie_file"), **vals)
    folder = m["folder"]
    new_folder = folder
    if m["single_folder"]:
        new_folder = os.path.join(os.path.dirname(folder), _fmt(config.get("rename_movie_folder"), **vals))
    if m.get("part_files") and len(m["part_files"]) > 1 and not m["single_folder"]:
        return plan_stack(m)                       # Mehrteiler im gemischten Ordner -> eigener Filmordner
    if m.get("part_files") and len(m["part_files"]) > 1:
        # Mehrteiler: alle Teile als <Name>-cd1, -cd2 … in den (neuen) Filmordner
        rel = [os.path.join(new_folder, os.path.relpath(p, folder)) for p in m["part_files"]]
        targets = _part_targets(m, new_folder, new_stem)
        if new_folder == folder and rel == targets:
            return None
        return {"id": m["id"], "type": "movie", "old": m["path"], "new": targets[0], "old_folder": folder,
                "new_folder": new_folder, "parts": list(zip(m["part_files"], targets))}
    new_path = os.path.join(new_folder, new_stem + ext)
    if new_path == m["path"]:
        return None
    return {"id": m["id"], "type": "movie", "old": m["path"], "new": new_path,
            "old_folder": folder, "new_folder": new_folder}


def plan_episode(e, show_meta):
    meta = e.get("meta") or []
    if not meta or not e.get("episodes"):
        return None
    nums = e["episodes"]
    eps = f"S{e['season']:02d}" + "".join(f"E{n:02d}" for n in nums)
    vals = {"showtitle": show_meta.get("title"), "season": e["season"], "episode": nums[0],
            "eps": eps, "title": " & ".join(x["title"] for x in meta if x.get("title")),
            "year": show_meta.get("year") or ""}
    ext = os.path.splitext(e["path"])[1]
    new_path = os.path.join(os.path.dirname(e["path"]), _fmt(config.get("rename_episode_file"), **vals) + ext)
    if new_path == e["path"]:
        return None
    return {"id": e["id"], "type": "episode", "old": e["path"], "new": new_path}


def preview(movie_ids=None, show_ids=None):
    plans = []
    for mid in movie_ids or []:
        m = db.one("SELECT * FROM movies WHERE id=?", (mid,))
        if m and m["status"] == "gescrapt":
            p = plan_movie(m)
            if p:
                plans.append(p)
    for sid in show_ids or []:
        s = db.one("SELECT * FROM shows WHERE id=?", (sid,))
        if not s or not s.get("meta"):
            continue
        for e in db.query("SELECT * FROM episodes WHERE show_id=? AND status='gescrapt'", (sid,)):
            p = plan_episode(e, s["meta"])
            if p:
                plans.append(p)
    # Konflikte markieren: Ziel existiert schon oder zwei Einträge wollen denselben Namen
    targets = {}
    for p in plans:
        targets.setdefault(os.path.normcase(p["new"]), []).append(p)
    for p in plans:
        olds = {os.path.normcase(o) for o, _ in p.get("parts") or []} | {os.path.normcase(p["old"])}
        exists = any(os.path.exists(n) and os.path.normcase(n) not in olds
                     for n in [t for _, t in p.get("parts") or []] or [p["new"]])
        if not p.get("parts"):
            exists = os.path.exists(p["new"]) and os.path.normcase(p["new"]) != os.path.normcase(p["old"])
        p["conflict"] = "Ziel existiert bereits" if exists else \
            "Doppelter Zielname" if len(targets[os.path.normcase(p["new"])]) > 1 else None
    return plans


def apply(plans):
    done, errors = 0, []
    for p in plans:
        if p.get("conflict"):
            errors.append(f"{p['old']}: {p['conflict']}")
            db.log("WARN", f"Übersprungen ({p['conflict']}): {p['old']}")
            continue
        try:
            if p["type"] == "movie":
                _apply_movie(p)
            else:
                _move_with_sidecars(p["old"], p["new"])
                db.update("episodes", p["id"], path=p["new"])
            done += 1
            db.log("INFO", f"Umbenannt: {os.path.basename(p['old'])} → {os.path.basename(p['new'])}")
        except (OSError, ValueError) as e:
            errors.append(f"{p['old']}: {e}")
            db.log("ERROR", f"Umbenennen fehlgeschlagen {p['old']}: {e}")
    return done, errors


def _move_with_sidecars(old, new):
    if os.path.exists(new) and os.path.normcase(old) != os.path.normcase(new):
        raise OSError(f"Ziel existiert bereits: {new}")
    old_base, new_base = os.path.splitext(old)[0], os.path.splitext(new)[0]
    for f, rest in _sidecars(old):
        target = new_base + rest
        if not os.path.exists(target):
            os.rename(f, target)
    os.rename(old, new)
    return old_base, new_base


def _apply_movie(p):
    old, new = p["old"], p["new"]
    folder, new_folder = p["old_folder"], p["new_folder"]
    if p.get("own_folder"):
        # Mehrteiler aus gemischtem Ordner in eigenen Filmordner verschieben
        if os.path.exists(new_folder):
            raise OSError(f"Zielordner existiert bereits: {new_folder}")
        os.makedirs(new_folder)
        done = []
        for src, dst in p["parts"]:
            _move_with_sidecars(src, dst)
            done.append(dst)
        db.update("movies", p["id"], path=done[0], folder=new_folder, part_files=done, single_folder=1)
        return
    if folder != new_folder:
        if os.path.exists(new_folder):
            raise OSError(f"Zielordner existiert bereits: {new_folder}")
        os.rename(folder, new_folder)
        old = os.path.join(new_folder, os.path.relpath(old, folder))
    if p.get("parts"):
        done = []
        for src, dst in p["parts"]:
            src = os.path.join(new_folder, os.path.relpath(src, folder))
            if os.path.normcase(src) != os.path.normcase(dst):
                _move_with_sidecars(src, dst)
            done.append(dst)
            sub = os.path.dirname(src)
            if sub != new_folder and os.path.isdir(sub) and not os.listdir(sub):
                os.rmdir(sub)                          # leeren CD1/CD2-Ordner entfernen
        db.update("movies", p["id"], path=done[0], folder=new_folder, part_files=done)
        return
    if old != new:
        _move_with_sidecars(old, new)
    db.update("movies", p["id"], path=new, folder=new_folder)
