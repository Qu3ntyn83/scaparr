"""Wikidata: FSK-Freigabe (P1981) und OFDb-ID (P3138) über TMDB- oder IMDb-ID.

Nutzt die MediaWiki-Action-API (haswbstatement-Suche + wbgetentities) statt des SPARQL-Endpunkts,
der häufig stark gedrosselt ist. Bei 429 pausiert die Quelle 10 Minuten statt weiter anzufragen.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, db
from .http import UA, SourceError, _throttle

API = "https://www.wikidata.org/w/api.php"
P_TMDB = {"movie": "P4947", "tv": "P4983"}
_labels: dict[str, str] = {}          # Cache: Q-ID der FSK-Stufe -> "FSK 12"
_pause = {"until": 0.0}


def enabled():
    return bool(config.get("wikidata_enabled"))


def _get(params):
    if time.time() < _pause["until"]:
        raise SourceError("Wikidata pausiert (Rate-Limit)")
    # Wikimedia blockt httpx-Clients (403) – urllib wird akzeptiert
    _throttle("www.wikidata.org", 1.0)
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "maxlag": 5})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _pause["until"] = time.time() + 600
            raise SourceError("Wikidata: Rate-Limit (429) – pausiere 10 Minuten")
        raise SourceError(f"Wikidata: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SourceError(f"Wikidata: {e}")
    if "error" in data:
        raise SourceError(f"Wikidata: {data['error'].get('info', data['error'])}")
    return data


def _find(prop, value):
    r = _get({"action": "query", "list": "search", "srsearch": f"haswbstatement:{prop}={value}",
              "srlimit": 3, "srprop": ""})
    hits = r.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def _claims(qid):
    e = _get({"action": "wbgetentities", "ids": qid, "props": "claims"})
    return e.get("entities", {}).get(qid, {}).get("claims", {})


def _values(claims, prop):
    out = []
    for c in claims.get(prop, []):
        if c.get("rank") == "deprecated":
            continue
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append(v)
    return out


def _fsk_label(item_ids):
    missing = [i for i in item_ids if i not in _labels]
    if missing:
        e = _get({"action": "wbgetentities", "ids": "|".join(missing), "props": "labels", "languages": "de|en"})
        for k, v in e.get("entities", {}).items():
            lab = v.get("labels", {})
            _labels[k] = (lab.get("de") or lab.get("en") or {}).get("value", "")
    return [_labels.get(i, "") for i in item_ids]


def lookup(kind, tmdb_id=None, imdb_id=None):
    """-> {"fsk": "12" | None, "ofdb_id": "901" | None, "qid": "Q62730"}"""
    if not enabled() or not (tmdb_id or imdb_id):
        return {}
    try:
        qid = _find(P_TMDB[kind], int(tmdb_id)) if tmdb_id else None
        if not qid and imdb_id and re.fullmatch(r"tt\d+", imdb_id):
            qid = _find("P345", imdb_id)
        if not qid:
            return {}
        claims = _claims(qid)
        fsk_items = [v["id"] for v in _values(claims, "P1981") if isinstance(v, dict) and "id" in v]
        ages = set()
        for label in _fsk_label(fsk_items) if fsk_items else []:
            m = re.search(r"\b(0|6|12|16|18)\b", label)
            if m:
                ages.add(int(m.group(1)))
        ofdb = next(iter(_values(claims, "P3138")), None)
        # mehrere Freigaben (z. B. Neuprüfung) -> die höhere, sicher für den Jugendschutz
        return {"fsk": str(max(ages)) if ages else None, "ofdb_id": ofdb, "qid": qid}
    except SourceError as e:
        if "pausiert" not in str(e):
            db.log("WARN", str(e))
        return {}


def test():
    _pause["until"] = 0
    r = lookup("movie", tmdb_id=387)
    if not r:
        return {"ok": False, "msg": "Wikidata nicht erreichbar oder gedrosselt (Protokoll prüfen)"}
    return {"ok": True, "msg": f"Wikidata OK – Das Boot: FSK {r.get('fsk') or '–'}, OFDb-ID {r.get('ofdb_id') or '–'}"}
