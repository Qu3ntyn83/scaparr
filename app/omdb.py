"""OMDb: IMDb-Wertung/-Stimmen, Rotten Tomatoes, Metacritic (Suche über IMDb-ID)."""
import re

from . import config, db
from .http import SourceError, request

API = "https://www.omdbapi.com/"


def enabled():
    return bool(config.get("omdb_api_key"))


def _num(s):
    s = (s or "").replace(",", "")
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m and s != "N/A" else None


def ratings(imdb_id):
    """{"imdb": {"value": 8.7, "votes": 2000000}, "rottentomatoes": {"value": 88}, "metacritic": {...}}"""
    if not enabled() or not imdb_id:
        return {}
    try:
        r = request("GET", API, params={"apikey": config.get("omdb_api_key"), "i": imdb_id}, min_interval=0.2)
    except SourceError as e:
        db.log("WARN", str(e))
        return {}
    if r.status_code == 401:
        db.log("WARN", "OMDb: API-Key ungültig oder Tageslimit erreicht")
        return {}
    try:
        d = r.json()
    except ValueError:
        return {}
    if d.get("Response") != "True":
        if "limit" in (d.get("Error") or "").lower():
            db.log("WARN", f"OMDb: {d['Error']}")
        return {}
    out = {}
    if _num(d.get("imdbRating")):
        out["imdb"] = {"value": _num(d["imdbRating"]), "votes": int(_num(d.get("imdbVotes")) or 0)}
    for r in d.get("Ratings", []):
        v = _num(r.get("Value"))
        if v is None:
            continue
        if r.get("Source") == "Rotten Tomatoes":
            out["rottentomatoes"] = {"value": v}
        elif r.get("Source") == "Metacritic":
            out["metacritic"] = {"value": v}
    if "metacritic" not in out and _num(d.get("Metascore")):
        out["metacritic"] = {"value": _num(d["Metascore"])}
    return out


def test():
    if not enabled():
        return {"ok": False, "msg": "Kein OMDb-Key hinterlegt"}
    r = ratings("tt0133093")
    if not r:
        return {"ok": False, "msg": "OMDb antwortet nicht korrekt (Key prüfen, Freischalt-Mail bestätigt?)"}
    return {"ok": True, "msg": "OMDb OK – Matrix: IMDb " + str(r.get("imdb", {}).get("value", "–")) +
            (f", RT {int(r['rottentomatoes']['value'])} %" if "rottentomatoes" in r else "")}
