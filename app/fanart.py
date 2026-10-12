"""fanart.tv (API v3): Logos, Clearart, Disc, Banner, Landscape, Poster, Hintergründe."""
from . import config, db
from .http import SourceError, request

API = "https://webservice.fanart.tv/v3"

# fanart.tv-Felder -> unsere Artwork-Typen
MOVIE_MAP = {
    "movieposter": "poster", "moviebackground": "fanart", "hdmovielogo": "logo", "movielogo": "logo",
    "hdmovieclearart": "clearart", "movieart": "clearart", "moviedisc": "discart",
    "moviebanner": "banner", "moviethumb": "landscape",
}
TV_MAP = {
    "tvposter": "poster", "showbackground": "fanart", "hdtvlogo": "logo", "clearlogo": "logo",
    "hdclearart": "clearart", "clearart": "clearart", "tvbanner": "banner", "tvthumb": "landscape",
    "seasonposter": "season_poster",
}
HD = {"hdmovielogo", "hdmovieclearart", "hdtvlogo", "hdclearart"}


def enabled():
    return bool(config.get("fanart_api_key"))


def _lang(code):
    if code in (None, "", "00"):
        return None          # textlos
    return code[:2].lower()


def _get(path):
    params = {"api_key": config.get("fanart_api_key")}
    if config.get("fanart_client_key"):
        params["client_key"] = config.get("fanart_client_key")
    r = request("GET", f"{API}{path}", params=params, min_interval=0.2, ok404=True)
    if r is None:
        return {}
    if r.status_code in (401, 403):
        raise SourceError("fanart.tv: API-Key ungültig")
    if r.status_code != 200:
        raise SourceError(f"fanart.tv: HTTP {r.status_code}")
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        return {}
    return data


def _collect(data, mapping):
    out = {}
    for field, kind in mapping.items():
        for i in data.get(field, []) or []:
            try:
                likes = int(i.get("likes") or 0)
            except ValueError:
                likes = 0
            c = {"url": i["url"], "lang": _lang(i.get("lang")), "score": likes + (5 if field in HD else 0),
                 "source": "fanart"}
            if kind == "season_poster":
                s = i.get("season")
                if s in (None, "", "all"):
                    continue
                c["season"] = int(s)
            out.setdefault(kind, []).append(c)
    return out


def movie_art(tmdb_id=None, imdb_id=None):
    if not enabled():
        return {}
    try:
        data = _get(f"/movies/{tmdb_id or imdb_id}")
        return _collect(data, MOVIE_MAP)
    except SourceError as e:
        db.log("WARN", str(e))
        return {}


def tv_art(tvdb_id):
    if not enabled() or not tvdb_id:
        return {}
    try:
        return _collect(_get(f"/tv/{tvdb_id}"), TV_MAP)
    except SourceError as e:
        db.log("WARN", str(e))
        return {}


def test():
    if not enabled():
        return {"ok": False, "msg": "Kein fanart.tv-Key hinterlegt"}
    try:
        art = _collect(_get("/movies/603"), MOVIE_MAP)
        n = sum(len(v) for v in art.values())
        return {"ok": True, "msg": f"fanart.tv OK – Testfilm Matrix: {n} Bilder"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}
