"""Ausweichsuche, wenn TMDB einen Titel nicht findet (Anime, Dokus, deutsche TV-Produktionen).

Die Quellen liefern hier nur die *Zuordnung*: Sie finden den Titel über ihre eigene Suche und nennen eine ID, mit der
der Titel bei TMDB gefunden wird. Alle Daten (NFO, Poster, Beschreibung) kommen danach wie gewohnt von TMDB & Co.

  TVDB      Serien, Dokus (Suche über den vorhandenen TVDB-Zugang)      -> TMDB über /find (TVDB-ID)
  AniList   Anime, tolerante Suche über Romaji/Englisch/Synonyme        -> TMDB über die Fribb-Anime-Liste
  Wikidata  Dokus und deutsche Produktionen über deutsche Bezeichnung   -> TMDB-/IMDb-ID aus den Aussagen des Eintrags

Ein Treffer gilt nur als sicher, wenn der Titel exakt übereinstimmt (und das Jahr passt, falls bekannt). Alles andere
wird höchstens als unsicherer Vorschlag zurückgegeben und landet in der manuellen Zuordnung.
"""
import re

from . import animelists, config, db, tmdb, tvdb, wikidata
from .http import SourceError, request

ANILIST = "https://graphql.anilist.co"
ANILIST_QUERY = ("query($q:String){Page(perPage:8){media(search:$q,type:ANIME){id format "
                 "startDate{year} title{romaji english native} synonyms}}}")
P_TMDB = {"tv": "P4983", "movie": "P4947"}
P_IMDB, P_DATE = "P345", ("P577", "P580")


def enabled():
    return bool(config.get("fallback_search"))


def _forms(s):
    from .scraper import _forms as f              # spät importieren (scraper importiert dieses Modul)
    return f(s)


def _same(title, names):
    tf = _forms(title)
    return bool(tf) and any(tf & _forms(n) for n in names if n)


def _year_ok(year, found):
    return not year or not found or abs(int(year) - int(found)) <= 1


def _tmdb_from_tvdb(tvdb_id):
    try:
        r = tmdb.get(f"/find/{int(tvdb_id)}", external_source="tvdb_id").get("tv_results", [])
    except Exception:                              # noqa – Ausweichsuche darf den Scrape nie stören
        return None
    return r[0]["id"] if r else None


def _tmdb_from_imdb(imdb_id, kind):
    try:
        r = tmdb.find_imdb(imdb_id).get("tv_results" if kind == "tv" else "movie_results", [])
    except Exception:                              # noqa
        return None
    return r[0]["id"] if r else None


# ---------------------------------------------------------------- TVDB

def via_tvdb(kind, title, year):
    """-> (tmdb_id, sicher, Name) | None. Nur Serien."""
    if kind != "tv" or not tvdb.enabled():
        return None
    try:
        hits = tvdb.search(title, year) or (tvdb.search(title) if year else [])
    except Exception as e:                         # noqa
        db.log("WARN", f"TVDB-Ausweichsuche: {e}")
        return None
    for h in hits:
        if not _same(title, [h.get("title"), h.get("original_title")]):
            continue
        tid = _tmdb_from_tvdb(h["tvdb_id"])
        if tid:
            return tid, _year_ok(year, h.get("year")), h.get("title") or h.get("original_title")
    return None


# ---------------------------------------------------------------- AniList

def _anilist(title):
    r = request("POST", ANILIST, json={"query": ANILIST_QUERY, "variables": {"q": title}},
                min_interval=2.2, retries=2)       # AniList erlaubt zurzeit 30 Anfragen/Minute
    if r.status_code != 200:
        raise SourceError(f"AniList: HTTP {r.status_code}")
    return (((r.json() or {}).get("data") or {}).get("Page") or {}).get("media") or []


def via_anilist(kind, title, year):
    if not animelists.enabled():
        return None
    try:
        media = _anilist(title)
    except (SourceError, ValueError) as e:
        db.log("WARN", f"AniList-Ausweichsuche: {e}")
        return None
    for m in media:
        names = list((m.get("title") or {}).values()) + list(m.get("synonyms") or [])
        if not _same(title, names):
            continue
        mp = animelists.mapping(animelists.by_id("anilist", m["id"]))
        if not mp:
            continue
        movie_like = (m.get("format") or "") in ("MOVIE", "SPECIAL", "OVA", "ONA")
        if kind == "movie":
            tid = mp.get("tmdb_movie")
        else:
            tid = mp.get("tmdb_tv") or (_tmdb_from_tvdb(mp["tvdb"]) if mp.get("tvdb") else None)
        if not tid or (kind == "tv" and movie_like and not mp.get("tmdb_tv")):
            continue
        # Serien: Jahr der ersten Staffel ist nicht das der gefundenen Staffel -> nur bei Filmen prüfen
        ok = _year_ok(year, (m.get("startDate") or {}).get("year")) if kind == "movie" else True
        name = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
        return int(tid), ok, name
    return None


# ---------------------------------------------------------------- Wikidata

def _wd_year(claims):
    for prop in P_DATE:
        for c in claims.get(prop, []):
            t = (c.get("mainsnak", {}).get("datavalue", {}).get("value") or {}).get("time")
            m = re.match(r"[+-]?(\d{4})", t or "")
            if m:
                return int(m.group(1))
    return None


def via_wikidata(kind, title, year):
    if not wikidata.enabled():
        return None
    seen = set()
    try:
        for lang in ("de", "en"):
            d = wikidata._get({"action": "wbsearchentities", "search": title, "language": lang,
                               "uselang": lang, "type": "item", "limit": 6})
            for h in d.get("search", []):
                qid = h.get("id")
                names = [h.get("label")] + list(h.get("aliases") or [])
                if not qid or qid in seen or not _same(title, names):
                    continue
                seen.add(qid)
                claims = wikidata._claims(qid)
                vals = wikidata._values(claims, P_TMDB[kind])
                tid = int(vals[0]) if vals and str(vals[0]).isdigit() else None
                if not tid:
                    imdb = next((v for v in wikidata._values(claims, P_IMDB) if re.fullmatch(r"tt\d+", str(v))), None)
                    tid = _tmdb_from_imdb(imdb, kind) if imdb else None
                if tid:
                    return tid, _year_ok(year, _wd_year(claims)), h.get("label")
    except SourceError as e:
        db.log("WARN", f"Wikidata-Ausweichsuche: {e}")
    return None


# ---------------------------------------------------------------- Zusammenspiel

SOURCES = {"tv": (("TVDB", via_tvdb), ("AniList", via_anilist), ("Wikidata", via_wikidata)),
           "movie": (("AniList", via_anilist), ("Wikidata", via_wikidata))}


def fallback(kind, titles, year=None):
    """Ausweichsuche über mehrere Titelvarianten. -> {"tmdb_id", "sure", "source", "name"} | None.
    Der erste sichere Treffer gewinnt, sonst der erste unsichere (Jahr passt nicht)."""
    if not enabled():
        return None
    first_unsure = None
    for title in dict.fromkeys(t for t in titles if t):
        for label, fn in SOURCES[kind]:
            try:
                hit = fn(kind, title, year)
            except Exception as e:                 # noqa – eine Quelle darf die anderen nicht blockieren
                db.log("WARN", f"{label}-Ausweichsuche: {type(e).__name__}: {e}")
                continue
            if not hit:
                continue
            res = {"tmdb_id": hit[0], "sure": bool(hit[1]), "source": label, "name": hit[2]}
            if res["sure"]:
                db.log("INFO", f"Treffer über {label}: „{title}“ → {res['name']} (TMDB {res['tmdb_id']})")
                return res
            first_unsure = first_unsure or res
    return first_unsure
