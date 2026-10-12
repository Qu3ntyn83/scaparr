"""TheTVDB API v4: deutsche Episodentitel/-beschreibungen, DVD-/absolute Reihenfolge, Serien-Artwork."""
import time

from . import config, db
from .http import SourceError, request

API = "https://api4.thetvdb.com/v4"
_token = {"value": None, "ts": 0, "key": None}
_types: dict[int, tuple[str, str]] = {}      # Artwork-Typ-ID -> (recordType, slug)
LANG3 = {"deu": "de", "ger": "de", "eng": "en", "fra": "fr", "spa": "es", "ita": "it", "jpn": "ja",
         "nld": "nl", "por": "pt", "pol": "pl", "swe": "sv", "dan": "da", "nor": "no", "fin": "fi"}
ART_SLUGS = {"poster": "poster", "background": "fanart", "banner": "banner",
             "clearlogo": "logo", "clearart": "clearart"}


def enabled():
    return bool(config.get("tvdb_api_key"))


def lang3():
    return {"de": "deu", "en": "eng"}.get(config.lang_short(), "deu")


def _login():
    key = config.get("tvdb_api_key")
    if _token["value"] and _token["key"] == key and time.time() - _token["ts"] < 20 * 86400:
        return _token["value"]
    body = {"apikey": key}
    if config.get("tvdb_pin"):
        body["pin"] = config.get("tvdb_pin")
    r = request("POST", f"{API}/login", json=body, min_interval=0.3)
    if r.status_code != 200:
        raise SourceError(f"TVDB-Login fehlgeschlagen (HTTP {r.status_code}) – Key/PIN prüfen")
    _token.update(value=r.json()["data"]["token"], ts=time.time(), key=key)
    return _token["value"]


def _get(path, **params):
    for attempt in (1, 2):
        r = request("GET", f"{API}{path}", params={k: v for k, v in params.items() if v not in (None, "")},
                    headers={"Authorization": f"Bearer {_login()}"}, min_interval=0.15, ok404=True)
        if r is None:
            return None
        if r.status_code == 401 and attempt == 1:
            _token["value"] = None          # Token abgelaufen -> neu anmelden
            continue
        if r.status_code != 200:
            raise SourceError(f"TVDB: HTTP {r.status_code} bei {path}")
        return r.json()
    return None


def img(path):
    if not path:
        return None
    return path if path.startswith("http") else "https://artworks.thetvdb.com" + path


def _lang2(code):
    if not code:
        return None
    return LANG3.get(code, code[:2])


# ---------------------------------------------------------------- Suche / Serie

def search(title, year=None):
    d = _get("/search", query=title, type="series", year=year, limit=10) or {}
    out = []
    for r in d.get("data", []):
        tid = r.get("tvdb_id") or str(r.get("id", "")).replace("series-", "")
        trans = r.get("translations") or {}
        out.append({"tvdb_id": int(tid), "title": trans.get(lang3()) or r.get("name"),
                    "original_title": r.get("name"), "year": int(r["year"]) if r.get("year") else None,
                    "overview": (r.get("overviews") or {}).get(lang3()) or r.get("overview"),
                    "poster": r.get("image_url") or r.get("thumbnail")})
    return out


def translation(tvdb_id):
    d = _get(f"/series/{tvdb_id}/translations/{lang3()}")
    return (d or {}).get("data") or {}


def episodes(tvdb_id, order="default"):
    """{(staffel, nr): {...}} – alle Seiten, deutsche Texte."""
    out, page = {}, 0
    while page < 50:
        d = _get(f"/series/{tvdb_id}/episodes/{order}/{lang3()}", page=page)
        if not d:
            break
        for e in (d.get("data") or {}).get("episodes", []):
            if e.get("seasonNumber") is None or e.get("number") is None:
                continue
            out[(int(e["seasonNumber"]), int(e["number"]))] = {
                "season": int(e["seasonNumber"]), "episode": int(e["number"]), "tvdb_id": e.get("id"),
                "title": e.get("name") or "", "plot": e.get("overview") or "", "aired": e.get("aired"),
                "runtime": e.get("runtime"), "still_url": img(e.get("image")),
                "absolute": e.get("absoluteNumber"),
            }
        if not (d.get("links") or {}).get("next"):
            break
        page += 1
    return out


def _artwork_types():
    if not _types:
        for t in (_get("/artwork/types") or {}).get("data", []):
            _types[t["id"]] = ((t.get("recordType") or "").lower(), (t.get("slug") or "").lower())
    return _types


def series_art(tvdb_id):
    if not enabled() or not tvdb_id:
        return {}
    try:
        types = _artwork_types()
        d = _get(f"/series/{tvdb_id}/artworks") or {}
        out = {}
        for a in (d.get("data") or {}).get("artworks", []):
            rec, slug = types.get(a.get("type"), ("", ""))
            kind = ART_SLUGS.get(slug) if rec == "series" else None
            if not kind or not a.get("image"):
                continue
            out.setdefault(kind, []).append({"url": img(a["image"]), "lang": _lang2(a.get("language")),
                                             "score": float(a.get("score") or 0) / 100000, "source": "tvdb"})
        return out
    except SourceError as e:
        db.log("WARN", str(e))
        return {}


def test():
    if not enabled():
        return {"ok": False, "msg": "Kein TVDB-Key hinterlegt"}
    try:
        _token["value"] = None
        r = search("Dark", 2017)
        return {"ok": True, "msg": f"TVDB OK – Testtreffer: {r[0]['title']} ({r[0]['year']})" if r else "TVDB OK"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}
