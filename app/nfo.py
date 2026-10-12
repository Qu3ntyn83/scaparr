"""Kodi/Emby-kompatible NFO-Dateien schreiben."""
import xml.etree.ElementTree as ET

from . import config

HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' \
         '<!-- erstellt von Scraparr (TMDB, de-DE) -->\n'


def _add(parent, tag, value, **attrs):
    if value in (None, "", []):
        return None
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items() if v is not None})
    el.text = str(value)
    return el


RATING_NAMES = {"tmdb": ("themoviedb", 10), "imdb": ("imdb", 10),
                "rottentomatoes": ("tomatometerallcritics", 100), "metacritic": ("metacritic", 100)}


def _ratings(root, m):
    ratings = dict(m.get("ratings") or {})
    if not ratings and m.get("rating"):
        ratings["tmdb"] = {"value": m["rating"], "votes": m.get("votes") or 0}
    if not ratings:
        return
    default = config.get("rating_default")
    if default not in ratings or RATING_NAMES[default][1] != 10:
        default = "imdb" if "imdb" in ratings else "tmdb" if "tmdb" in ratings else next(iter(ratings))
    rs = ET.SubElement(root, "ratings")
    for key, r in ratings.items():
        name, mx = RATING_NAMES.get(key, (key, 10))
        attrs = {"name": name, "max": str(mx)}
        if key == default:
            attrs["default"] = "true"
        el = ET.SubElement(rs, "rating", attrs)
        _add(el, "value", round(float(r["value"]), 1))
        if r.get("votes"):
            _add(el, "votes", r["votes"])
    _add(root, "rating", round(float(ratings[default]["value"]), 1))
    if "rottentomatoes" in ratings:            # Emby: Kritikerwertung
        _add(root, "criticrating", int(ratings["rottentomatoes"]["value"]))


def _actors(root, cast):
    for a in cast or []:
        el = ET.SubElement(root, "actor")
        _add(el, "name", a["name"])
        _add(el, "role", a.get("role"))
        _add(el, "order", a.get("order"))
        _add(el, "thumb", a.get("thumb"))


def _common(root, m):
    _add(root, "title", m.get("title"))
    _add(root, "originaltitle", m.get("original_title"))
    if m.get("sorttitle"):
        _add(root, "sorttitle", m["sorttitle"])
    _ratings(root, m)
    _add(root, "plot", m.get("plot"))
    _add(root, "outline", (m.get("plot") or "").split(". ")[0][:300] if m.get("plot") else None)
    _add(root, "runtime", m.get("runtime"))
    fsk = config.format_fsk(m.get("fsk"))
    _add(root, "mpaa", fsk)
    _add(root, "certification", fsk)
    _add(root, "premiered", m.get("premiered"))
    _add(root, "year", m.get("year"))
    for g in m.get("genres", []):
        _add(root, "genre", g)
    for s in m.get("studios", []):
        _add(root, "studio", s)
    for c in m.get("countries", []):
        _add(root, "country", c)
    for t in m.get("tags", []):
        _add(root, "tag", t)


def _write(path, root):
    ET.indent(root, space="  ")
    data = HEADER + ET.tostring(root, encoding="unicode") + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


ANIME_ID_TYPES = ("anidb", "anilist", "mal", "kitsu", "anisearch")


def _anime_ids(root, m):
    for k in ANIME_ID_TYPES:
        _add(root, "uniqueid", (m.get("anime_ids") or {}).get(k), type=k)


def write_movie(path, m):
    root = ET.Element("movie")
    _common(root, m)
    _add(root, "tagline", m.get("tagline"))
    _add(root, "uniqueid", m.get("tmdb_id"), type="tmdb", default="true")
    _add(root, "uniqueid", m.get("imdb_id"), type="imdb")
    _anime_ids(root, m)
    _add(root, "tmdbid", m.get("tmdb_id"))
    _add(root, "id", m.get("imdb_id"))
    for d in m.get("directors", []):
        _add(root, "director", d)
    for w in m.get("writers", []):
        _add(root, "credits", w)
    if m.get("set"):
        s = ET.SubElement(root, "set", {"tmdbcolid": str(m["set"]["tmdb_id"])})
        _add(s, "name", m["set"]["name"])
        _add(s, "overview", m["set"].get("overview"))
    if m.get("trailer") and config.get("trailer_mode") != "off":
        _add(root, "trailer", m["trailer"]["url"])
    _add(root, "languages", config.lang_short())
    _actors(root, m.get("cast"))
    _write(path, root)


def write_tvshow(path, m):
    root = ET.Element("tvshow")
    _common(root, m)
    _add(root, "status", m.get("status"))
    _add(root, "uniqueid", m.get("tmdb_id"), type="tmdb", default="true")
    _add(root, "uniqueid", m.get("imdb_id"), type="imdb")
    _add(root, "uniqueid", m.get("tvdb_id"), type="tvdb")
    _anime_ids(root, m)
    _add(root, "tmdbid", m.get("tmdb_id"))
    _add(root, "tvdbid", m.get("tvdb_id"))
    _add(root, "imdb_id", m.get("imdb_id"))
    for s in m.get("seasons", []):
        if s.get("name"):
            _add(root, "namedseason", s["name"], number=s["season"])
    if m.get("trailer") and config.get("trailer_mode") != "off":
        _add(root, "trailer", m["trailer"]["url"])
    _actors(root, m.get("cast"))
    _write(path, root)


def write_episode(path, show, episodes):
    """Mehrere <episodedetails> in einer Datei = Multi-Episode."""
    blocks = []
    for e in episodes:
        root = ET.Element("episodedetails")
        _add(root, "title", e.get("title"))
        _add(root, "showtitle", show.get("title"))
        _add(root, "season", e["season"])
        _add(root, "episode", e["episode"])
        _ratings(root, e)
        _add(root, "plot", e.get("plot"))
        _add(root, "runtime", e.get("runtime"))
        _add(root, "aired", e.get("aired"))
        _add(root, "premiered", e.get("aired"))
        _add(root, "mpaa", config.format_fsk(show.get("fsk")))
        _add(root, "uniqueid", e.get("tmdb_id"), type="tmdb", default="true")
        _add(root, "uniqueid", e.get("tvdb_id"), type="tvdb", default=None if e.get("tmdb_id") else "true")
        for d in e.get("directors", []):
            _add(root, "director", d)
        for w in e.get("writers", []):
            _add(root, "credits", w)
        _actors(root, e.get("guests"))
        ET.indent(root, space="  ")
        blocks.append(ET.tostring(root, encoding="unicode"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n".join(blocks) + "\n")
