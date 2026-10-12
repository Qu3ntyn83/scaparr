"""Schlanker TMDB-Client (API v3) mit deutscher Sprache und Fallback."""
import re
import time
import threading

import httpx

from . import config

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
_throttle = threading.Lock()
_last = [0.0]


class TMDBError(Exception):
    pass


def _client():
    key = config.get("tmdb_api_key").strip()
    if not key:
        raise TMDBError("Kein TMDB-API-Key hinterlegt (Einstellungen).")
    headers, params = {"Accept": "application/json"}, {}
    if key.startswith("eyJ"):          # v4 Read Access Token (JWT)
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    return httpx.Client(base_url=API, headers=headers, params=params, timeout=20)


def get(path, **params):
    params = {k: v for k, v in params.items() if v not in (None, "")}
    for attempt in range(5):
        with _throttle:                 # max. ~25 Anfragen/s
            wait = 0.04 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with _client() as c:
                r = c.get(path, params=params)
        except httpx.HTTPError as e:
            if attempt == 4:
                raise TMDBError(f"TMDB nicht erreichbar: {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 2)))
            continue
        if r.status_code == 401:
            raise TMDBError("TMDB-API-Key ungültig (401).")
        if r.status_code == 404:
            raise TMDBError(f"TMDB: nicht gefunden ({path}).")
        if r.status_code >= 400:
            raise TMDBError(f"TMDB-Fehler {r.status_code}: {r.text[:200]}")
        return r.json()
    raise TMDBError("TMDB: zu viele Anfragen.")


def img(path, size="original"):
    return f"{IMG}{size}{path}" if path else None


def _langs():
    lang = config.get("language")
    short = lang.split("-")[0]
    fb = config.get("fallback_language").split("-")[0]
    return lang, short, fb


# ---------------------------------------------------------------- Suche

def search_movie(query, year=None):
    lang, *_ = _langs()
    res = get("/search/movie", query=query, year=year, language=lang,
              region=config.get("country"), include_adult="false")["results"]
    if not res and year:   # Jahr manchmal falsch -> ohne Jahr nochmal
        res = get("/search/movie", query=query, language=lang, include_adult="false")["results"]
    return [{
        "tmdb_id": r["id"], "title": r.get("title"), "original_title": r.get("original_title"),
        "year": int(r["release_date"][:4]) if r.get("release_date") else None,
        "overview": r.get("overview"), "poster": img(r.get("poster_path"), "w185"),
        "popularity": r.get("popularity", 0),
    } for r in res]


def search_tv(query, year=None):
    lang, *_ = _langs()
    res = get("/search/tv", query=query, first_air_date_year=year, language=lang)["results"]
    if not res and year:
        res = get("/search/tv", query=query, language=lang)["results"]
    return [{
        "tmdb_id": r["id"], "title": r.get("name"), "original_title": r.get("original_name"),
        "year": int(r["first_air_date"][:4]) if r.get("first_air_date") else None,
        "overview": r.get("overview"), "poster": img(r.get("poster_path"), "w185"),
        "popularity": r.get("popularity", 0),
    } for r in res]


def find_imdb(imdb_id):
    return get(f"/find/{imdb_id}", external_source="imdb_id")


# ---------------------------------------------------------------- Details

def _pick_image(images, prefer):
    """Bestes Bild nach Sprachpriorität, innerhalb einer Sprache nach Bewertung."""
    for lang in prefer:
        cands = [i for i in images if i.get("iso_639_1") == lang]
        if cands:
            cands.sort(key=lambda i: (i.get("vote_average", 0), i.get("vote_count", 0), i.get("width", 0)),
                       reverse=True)
            return cands[0]["file_path"]
    return None


def _cands(images, size="original"):
    """Alle TMDB-Bilder als Kandidaten für die quellenübergreifende Auswahl (artwork.choose)."""
    return [{"url": img(i["file_path"], size), "lang": i.get("iso_639_1"),
             "score": float(i.get("vote_average") or 0) + min(i.get("vote_count", 0), 20) / 100,
             "source": "tmdb", "w": i.get("width"), "h": i.get("height")} for i in images if i.get("file_path")]


def _image_cands(imgs):
    return {"poster": _cands(imgs.get("posters", []), config.get("poster_size")),
            "fanart": _cands(imgs.get("backdrops", []), config.get("fanart_size")),
            "logo": _cands(imgs.get("logos", []))}


def _pick_trailer(videos):
    lang, short, fb = _langs()
    order = [short] + ([fb] if config.get("trailer_fallback_en") else [])
    for l in order:
        vids = [v for v in videos if v.get("site") == "YouTube" and v.get("iso_639_1") == l
                and v.get("type") in ("Trailer", "Teaser")]
        vids.sort(key=lambda v: (v["type"] == "Trailer", v.get("official", False), v.get("size", 0)),
                  reverse=True)
        if vids:
            return {"key": vids[0]["key"], "name": vids[0].get("name"), "lang": l,
                    "url": f"https://www.youtube.com/watch?v={vids[0]['key']}"}
    return None


_FSK_RE = re.compile(r"(\d+)")


def _fsk_age(cert):
    m = _FSK_RE.search(cert or "")
    return m.group(1) if m else None


def _people(credits, limit=30):
    cast = [{"name": c["name"], "role": c.get("character", ""), "order": c.get("order", i),
             "thumb": img(c.get("profile_path"), "h632")}
            for i, c in enumerate(credits.get("cast", [])[:limit])]
    crew = credits.get("crew", [])
    directors = list(dict.fromkeys(c["name"] for c in crew if c.get("job") == "Director"))
    writers = list(dict.fromkeys(c["name"] for c in crew
                                 if c.get("department") == "Writing"))[:6]
    return cast, directors, writers


def _art_prefs():
    _, short, fb = _langs()
    return {
        "poster": [short, None, fb],            # deutsches Poster > textlos > englisch
        "fanart": [None, short, fb],            # Hintergründe bevorzugt textlos
        "logo": [short, fb, None],
    }


def movie(tmdb_id):
    lang, short, fb = _langs()
    d = get(f"/movie/{tmdb_id}", language=lang,
            append_to_response="release_dates,videos,credits,images,external_ids,keywords",
            include_image_language=f"{short},null,{fb}",
            include_video_language=f"{short},{fb}")
    # Fallback für fehlende deutsche Texte
    plot_src = "TMDB"
    if not d.get("overview") or not d.get("title"):
        en = get(f"/movie/{tmdb_id}", language=config.get("fallback_language"))
        if not d.get("overview") and en.get("overview"):
            plot_src = f"TMDB ({fb})"
        d["overview"] = d.get("overview") or en.get("overview")
        d["tagline"] = d.get("tagline") or en.get("tagline")
        d["title"] = d.get("title") or en.get("title")

    fsk = None
    for rel in d.get("release_dates", {}).get("results", []):
        if rel["iso_3166_1"] == config.get("country"):
            certs = [x.get("certification") for x in rel["release_dates"] if x.get("certification")]
            if certs:
                fsk = _fsk_age(certs[0])
    cast, directors, writers = _people(d.get("credits", {}))
    prefs = _art_prefs()
    imgs = d.get("images", {})
    coll = d.get("belongs_to_collection")
    return {
        "type": "movie",
        "tmdb_id": d["id"], "imdb_id": d.get("imdb_id") or d.get("external_ids", {}).get("imdb_id"),
        "title": d.get("title"), "original_title": d.get("original_title"),
        "year": int(d["release_date"][:4]) if d.get("release_date") else None,
        "premiered": d.get("release_date"), "tagline": d.get("tagline") or "",
        "plot": d.get("overview") or "", "runtime": d.get("runtime"),
        "rating": d.get("vote_average"), "votes": d.get("vote_count"),
        "fsk": fsk, "genres": [g["name"] for g in d.get("genres", [])],
        "studios": [s["name"] for s in d.get("production_companies", [])][:5],
        "countries": [c["name"] for c in d.get("production_countries", [])],
        "set": {"name": coll["name"], "tmdb_id": coll["id"]} if coll else None,
        "tags": [k["name"] for k in d.get("keywords", {}).get("keywords", [])][:20],
        "cast": cast, "directors": directors, "writers": writers,
        "poster": _pick_image(imgs.get("posters", []), prefs["poster"]) or d.get("poster_path"),
        "fanart": _pick_image(imgs.get("backdrops", []), prefs["fanart"]) or d.get("backdrop_path"),
        "logo": _pick_image(imgs.get("logos", []), prefs["logo"]),
        "trailer": _pick_trailer(d.get("videos", {}).get("results", [])),
        "images": _image_cands(imgs),
        "sources": {"meta": "TMDB", "plot": plot_src, "fsk": "TMDB" if fsk else None},
    }


def tv(tmdb_id):
    lang, short, fb = _langs()
    d = get(f"/tv/{tmdb_id}", language=lang,
            append_to_response="content_ratings,videos,aggregate_credits,images,external_ids,keywords",
            include_image_language=f"{short},null,{fb}",
            include_video_language=f"{short},{fb}")
    plot_src = "TMDB"
    if not d.get("overview"):
        en = get(f"/tv/{tmdb_id}", language=config.get("fallback_language"))
        d["overview"] = en.get("overview")
        plot_src = f"TMDB ({fb})" if d["overview"] else "TMDB"
    fsk = None
    for r in d.get("content_ratings", {}).get("results", []):
        if r["iso_3166_1"] == config.get("country"):
            fsk = _fsk_age(r.get("rating"))
    agg = d.get("aggregate_credits", {})
    cast = [{"name": c["name"], "order": c.get("order", i),
             "role": (c.get("roles") or [{}])[0].get("character", ""),
             "thumb": img(c.get("profile_path"), "h632")}
            for i, c in enumerate(agg.get("cast", [])[:30])]
    prefs = _art_prefs()
    imgs = d.get("images", {})
    ext = d.get("external_ids", {})
    return {
        "type": "tv",
        "tmdb_id": d["id"], "imdb_id": ext.get("imdb_id"), "tvdb_id": ext.get("tvdb_id"),
        "title": d.get("name"), "original_title": d.get("original_name"),
        "year": int(d["first_air_date"][:4]) if d.get("first_air_date") else None,
        "premiered": d.get("first_air_date"), "plot": d.get("overview") or "",
        "status": d.get("status"), "rating": d.get("vote_average"), "votes": d.get("vote_count"),
        "fsk": fsk, "genres": [g["name"] for g in d.get("genres", [])],
        "studios": [n["name"] for n in d.get("networks", [])],
        "tags": [k["name"] for k in d.get("keywords", {}).get("results", [])][:20],
        "cast": cast,
        "runtime": (d.get("episode_run_time") or [None])[0],
        "seasons": [{"season": s["season_number"], "name": s.get("name"),
                     "poster": s.get("poster_path"), "episodes": s.get("episode_count")}
                    for s in d.get("seasons", [])],
        "poster": _pick_image(imgs.get("posters", []), prefs["poster"]) or d.get("poster_path"),
        "fanart": _pick_image(imgs.get("backdrops", []), prefs["fanart"]) or d.get("backdrop_path"),
        "logo": _pick_image(imgs.get("logos", []), prefs["logo"]),
        "trailer": _pick_trailer(d.get("videos", {}).get("results", [])),
        "images": _image_cands(imgs),
        "sources": {"meta": "TMDB", "plot": plot_src, "fsk": "TMDB" if fsk else None},
    }


_PLACEHOLDER = re.compile(r"^(Episode|Folge|Épisode|Episodio)\s*\d+$", re.I)


def season(tmdb_id, season_no):
    """Episoden einer Staffel – deutsch, englische Texte wo deutsche fehlen."""
    lang, short, fb = _langs()
    d = get(f"/tv/{tmdb_id}/season/{season_no}", language=lang,
            append_to_response="images", include_image_language=f"{short},null,{fb}")
    en = None
    eps = {}
    for e in d.get("episodes", []):
        name, plot = e.get("name") or "", e.get("overview") or ""
        de_name_ok = bool(name) and not _PLACEHOLDER.match(name)
        de_plot_ok = bool(plot)
        if not plot or _PLACEHOLDER.match(name):
            if en is None:
                try:
                    en = {x["episode_number"]: x for x in
                          get(f"/tv/{tmdb_id}/season/{season_no}",
                              language=config.get("fallback_language")).get("episodes", [])}
                except TMDBError:
                    en = {}
            alt = en.get(e["episode_number"], {})
            if _PLACEHOLDER.match(name) and alt.get("name") and not _PLACEHOLDER.match(alt["name"]):
                name = alt["name"]
            plot = plot or alt.get("overview") or ""
        crew = e.get("crew", [])
        eps[e["episode_number"]] = {
            "season": season_no, "episode": e["episode_number"], "tmdb_id": e.get("id"),
            "title": name, "plot": plot, "aired": e.get("air_date"),
            "runtime": e.get("runtime"), "rating": e.get("vote_average"),
            "votes": e.get("vote_count"), "still_url": img(e.get("still_path")),
            "title_fallback": not de_name_ok, "plot_fallback": not de_plot_ok,
            "directors": [c["name"] for c in crew if c.get("job") == "Director"],
            "writers": [c["name"] for c in crew if c.get("department") == "Writing"][:4],
            "guests": [{"name": g["name"], "role": g.get("character", ""),
                        "thumb": img(g.get("profile_path"), "h632")}
                       for g in e.get("guest_stars", [])[:10]],
        }
    cands = _cands(d.get("images", {}).get("posters", []), config.get("poster_size"))
    if not cands and d.get("poster_path"):
        cands = [{"url": img(d["poster_path"], config.get("poster_size")), "lang": None, "score": 0,
                  "source": "tmdb"}]
    return {"season": season_no, "name": d.get("name"), "plot": d.get("overview"),
            "poster_cands": cands, "episodes": eps}


def collection(coll_id):
    """Filmreihe: deutscher Name/Text (Fallback englisch), Poster/Fanart-Kandidaten, alle Teile."""
    lang, short, fb = _langs()
    d = get(f"/collection/{int(coll_id)}", language=lang, append_to_response="images",
            include_image_language=f"{short},null,{fb}")
    overview, name = d.get("overview") or "", d.get("name") or ""
    if not overview or not name:
        en = get(f"/collection/{int(coll_id)}", language=config.get("fallback_language"))
        overview = overview or en.get("overview") or ""
        name = name or en.get("name") or ""
    imgs = d.get("images", {})
    parts = sorted(({"tmdb_id": p["id"], "title": p.get("title") or p.get("original_title"),
                     "year": int(p["release_date"][:4]) if p.get("release_date") else None,
                     "poster": img(p.get("poster_path"), "w185")}
                    for p in d.get("parts", []) if p.get("release_date")),     # unveröffentlichte weglassen
                   key=lambda x: (x["year"] or 9999, x["title"] or ""))
    return {"tmdb_id": d["id"], "name": name, "overview": overview, "parts": parts,
            "images": {"poster": _cands(imgs.get("posters", []), config.get("poster_size")),
                       "fanart": _cands(imgs.get("backdrops", []), config.get("fanart_size"))},
            "poster_path": d.get("poster_path"), "backdrop_path": d.get("backdrop_path")}
