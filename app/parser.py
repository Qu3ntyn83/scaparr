"""Dateinamen-Erkennung (guessit) und Auslesen vorhandener NFOs."""
import os
import re
import xml.etree.ElementTree as ET

from guessit import guessit

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg",
             ".webm", ".iso", ".divx", ".flv", ".vob", ".ogm", ".3gp"}
EXTRA_DIRS = {"extras", "featurettes", "behind the scenes", "deleted scenes", "interviews",
              "scenes", "shorts", "trailers", "samples", "sample", "bonus", "specials-extras",
              ".actors", ".deletedbyttm", "@eadir", "#recycle", ".recycle.bin"}
EXTRA_SUFFIX = re.compile(r"[-_. ](trailer|sample|featurette|behindthescenes|deleted|"
                          r"interview|scene|short|extra|teaser)\d*$", re.I)
# Zwischendateien von yt-dlp/Downloads: X.mp4.part, X.mp4.part.f137.mp4, X.part.temp.mp4, X.temp.mp4
TEMP_FILE = re.compile(r"\.(mp4|mkv|webm|m4a)\.(part|temp|ytdl)|\.part\.(f\d+|temp)|\.(part|temp|ytdl)$|\.f\d{2,4}$")
# Anhängsel, die nach "-trailer" noch kommen können: "-trailer (1)", "-trailer-2", "-trailer.f137"
EXTRA_TAIL = re.compile(r"([-_. ]?\(\d{1,2}\)|[-_. ]\d{1,2}|[-_. ](german|deutsch|english|de|en|1080p|720p|2160p|4k))$", re.I)
TRAILER_END = re.compile(r"[-_. ](trailer|teaser|sample)s?$", re.I)
EXTRA_PREFIX = re.compile(r"^(trailer|teaser|sample)s?([-_.\d]|$)", re.I)
TRAILER_TOKEN = re.compile(r"(?:^|[-_. \[(])(trailer|teaser)s?(?=$|[-_. \])\d])", re.I)
PART_RE = re.compile(r"[-_. ](cd|dvd|part|pt|disc|disk)[-_. ]?([2-9])$", re.I)
YEAR_IN_NAME = re.compile(r"[\(\[ .](19\d{2}|20\d{2})[\)\] .]?")
IMDB_RE = re.compile(r"(tt\d{7,8})")
TMDB_TAG = re.compile(r"\[?tmdb(?:id)?[-=](\d+)\]?", re.I)
# [anidb-9541] {anilist=16498} [mal-16498] [tvdbid-267440] [kitsu-7442] [anisearch-8219]
ID_TAG = re.compile(r"[\[\{]?\b(tmdb|tvdb|anidb|anilist|mal|kitsu|anisearch)(?:id)?[-=](\d+)[\]\}]?", re.I)


def id_hints(text):
    return {f"{k.lower()}_id": int(v) for k, v in ID_TAG.findall(text or "")}


def strip_ids(text):
    return ID_TAG.sub("", IMDB_RE.sub("", text or ""))


def is_video(path: str) -> bool:
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in VIDEO_EXT or name.startswith("."):
        return False
    if TEMP_FILE.search(stem) or PART_RE.search(stem) or is_extra_stem(stem):
        return False
    return True


def is_extra_stem(stem: str) -> bool:
    """Trailer/Sample/Featurette am Anfang oder Ende des Namens (auch mit Zähler/Sprache dahinter)."""
    core = stem
    for _ in range(3):
        core = EXTRA_TAIL.sub("", core)
    return bool(EXTRA_SUFFIX.search(stem) or TRAILER_END.search(core) or EXTRA_PREFIX.match(core))


def mentions_trailer(path: str) -> bool:
    """"Trailer"/"Teaser" irgendwo im Dateinamen, z. B. Film.2024.German.Trailer.1080p.mkv"""
    return bool(TRAILER_TOKEN.search(os.path.splitext(os.path.basename(path))[0]))


def skip_dir(name: str) -> bool:
    return name.lower() in EXTRA_DIRS or name.startswith(".") or name.startswith("@")


def _clean_title(t):
    return re.sub(r"\s+", " ", str(t)).strip() if t else None


def guess_movie(path: str, single_folder: bool):
    """Titel/Jahr aus Ordnername (bevorzugt, wenn er ein Jahr enthält) oder Dateiname."""
    folder = os.path.basename(os.path.dirname(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    hints = {}
    for src in (folder if single_folder else "", stem):
        hints.update(id_hints(src))
        m = IMDB_RE.search(src)
        if m:
            hints["imdb_id"] = m.group(1)
    candidates = []
    if single_folder and YEAR_IN_NAME.search(folder + " "):
        candidates.append(folder)
    candidates.append(stem)
    if single_folder:
        candidates.append(folder)
    for c in candidates:
        c = strip_ids(c)
        g = guessit(c, {"type": "movie"})
        title = _clean_title(g.get("title"))
        alt = g.get("alternative_title")
        if title and alt:   # "Herr der Ringe - Die Gefährten"
            title = f"{title} - {_clean_title(alt if isinstance(alt, str) else alt[0])}"
        if title:
            year = g.get("year")
            return {"title": title, "year": int(year) if year else None, **hints}
    return {"title": stem, "year": None, **hints}


def guess_show_folder(folder: str):
    name = os.path.basename(folder.rstrip("/"))
    hints = id_hints(name)
    m = IMDB_RE.search(name)
    if m:
        hints["imdb_id"] = m.group(1)
    name = strip_ids(name)
    m = re.search(r"\((\d{4})\)", name)
    year = int(m.group(1)) if m else None
    title = re.sub(r"\(\d{4}\)", "", name)
    t = title.strip()
    abbrev = re.fullmatch(r"(?:[A-Za-z0-9]\.){2,}[A-Za-z0-9]?", t)   # S.W.A.T. / M.A.S.H bleiben
    if " " not in t and not abbrev:      # Szene-Namen wie "Breaking.Bad" auflösen
        title = re.sub(r"[._]", " ", title)
    return {"title": _clean_title(title), "year": year, **hints}


SEASON_DIR = re.compile(r"^(?:season|staffel|series|s)[ ._-]*(\d{1,3})(?:\b|$)", re.I)
SPECIAL_DIRS = {"specials", "special", "season 0", "staffel 0"}


def is_season_dir(name):
    n = name.strip()
    return bool(SEASON_DIR.match(n)) or n.lower() in SPECIAL_DIRS
SXXEYY = re.compile(r"[Ss](\d{1,3})[ ._-]?[Ee](\d{1,4})((?:[ ._-]?[Ee-]\d{1,4})*)")
NXNN = re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)")


def guess_episode(path: str, show_root: str):
    stem = os.path.splitext(os.path.basename(path))[0]
    season = None
    rel = os.path.relpath(os.path.dirname(path), show_root)
    for part in rel.split(os.sep):
        m = SEASON_DIR.match(part.strip())
        if m:
            season = int(m.group(1))
        elif part.lower() in ("specials", "special", "extras"):
            season = 0
    m = SXXEYY.search(stem)
    if m:
        first = int(m.group(2))
        extra = [int(x) for x in re.findall(r"(\d{1,4})", m.group(3) or "")]
        extra = [x for x in extra if first < x <= first + 10]      # "-1080p" o. ä. ignorieren
        eps = [first] + extra
        if len(eps) == 2 and "-" in (m.group(3) or ""):
            eps = list(range(eps[0], eps[1] + 1))       # S01E01-E03
        return {"season": int(m.group(1)), "episodes": eps}
    m = NXNN.search(stem)
    if m:
        return {"season": int(m.group(1)), "episodes": [int(m.group(2))]}
    g = guessit(stem, {"type": "episode"})
    ep = g.get("episode")
    if ep is not None:
        eps = ep if isinstance(ep, list) else [ep]
        s = g.get("season", season if season is not None else 1)
        if isinstance(s, list):
            s = s[0]
        return {"season": int(s), "episodes": [int(e) for e in eps]}
    return None


def read_nfo_ids(nfo_path: str) -> dict:
    """IDs aus vorhandener NFO lesen (tMM/Kodi/Emby) – übernimmt bestehende Zuordnungen."""
    ids = {}
    try:
        with open(nfo_path, "rb") as f:
            raw = f.read()
    except OSError:
        return ids
    text = raw.decode("utf-8", "ignore")
    try:
        root = ET.fromstring(raw)
        for u in root.iter("uniqueid"):
            t = (u.get("type") or "").lower()
            if u.text and t in ("tmdb", "imdb", "tvdb", "anidb", "anilist", "mal", "kitsu", "anisearch"):
                ids[f"{t}_id"] = u.text.strip()
        for tag in ("tmdbid", "imdbid", "tvdbid"):
            el = root.find(tag)
            if el is not None and el.text:
                ids.setdefault(f"{tag[:-2]}_id", el.text.strip())
        el = root.find("id")
        if el is not None and el.text and IMDB_RE.match(el.text.strip()):
            ids.setdefault("imdb_id", el.text.strip())
    except ET.ParseError:
        # Manche NFOs enthalten nur eine URL
        m = re.search(r"themoviedb\.org/(?:movie|tv)/(\d+)", text)
        if m:
            ids["tmdb_id"] = m.group(1)
        m = IMDB_RE.search(text)
        if m:
            ids["imdb_id"] = m.group(1)
    for k in list(ids):
        if k != "imdb_id":
            try:
                ids[k] = int(ids[k])
            except ValueError:
                ids.pop(k)
    return ids
