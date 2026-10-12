"""Trakt (API v2): deutsche Übersetzungen für Filme, Serien und einzelne Episoden.

Braucht nur die Client-ID einer kostenlosen API-App (trakt.tv/oauth/applications) – kein Login.
"""
from . import config, db
from .http import SourceError, request

API = "https://api.trakt.tv"


def enabled():
    return bool(config.get("trakt_client_id"))


def _lang():
    return config.lang_short()


def _get(path, **params):
    r = request("GET", f"{API}{path}", params=params, min_interval=0.35, ok404=True,
                headers={"trakt-api-version": "2", "trakt-api-key": config.get("trakt_client_id"),
                         "Content-Type": "application/json"})
    if r is None:
        return None
    if r.status_code in (401, 403):
        raise SourceError("Trakt: Client-ID ungültig")
    if r.status_code != 200:
        raise SourceError(f"Trakt: HTTP {r.status_code} bei {path}")
    return r.json()


def trakt_id(kind, tmdb_id):
    """kind: movie | show -> Trakt-ID (int) über die TMDB-ID."""
    res = _get(f"/search/tmdb/{int(tmdb_id)}", type=kind) or []
    for r in res:
        if r.get("type") == kind and r.get(kind):
            return r[kind]["ids"]["trakt"]
    return None


def _pick(translations):
    """Deutsche Übersetzung, Land DE bevorzugt (vor AT/CH)."""
    lang = _lang()
    cands = [t for t in translations or [] if t.get("language") == lang]
    cands.sort(key=lambda t: (t.get("country") or "").lower() != lang)
    return cands[0] if cands else {}


def translation(kind, tmdb_id):
    """-> {"title": …, "overview": …, "tagline": …} oder {}"""
    if not enabled() or not tmdb_id:
        return {}
    try:
        tid = trakt_id(kind, tmdb_id)
        if not tid:
            return {}
        t = _pick(_get(f"/{kind}s/{tid}/translations/{_lang()}"))
        return {k: t.get(k) for k in ("title", "overview", "tagline") if t.get(k)}
    except SourceError as e:
        db.log("WARN", str(e))
        return {}


def season_episodes(tid, season):
    """-> {episodennr: {"title": …, "plot": …}} – deutsch, nur Episoden mit Übersetzung."""
    data = _get(f"/shows/{tid}/seasons/{int(season)}", translations=_lang()) or []
    out = {}
    for e in data:
        t = _pick(e.get("translations"))
        if t.get("title") or t.get("overview"):
            out[int(e["number"])] = {"title": t.get("title") or "", "plot": t.get("overview") or ""}
    return out


def test():
    if not enabled():
        return {"ok": False, "msg": "Keine Trakt-Client-ID hinterlegt"}
    try:
        tid = trakt_id("show", 70523)
        eps = season_episodes(tid, 1) if tid else {}
        first = eps.get(1, {}).get("title")
        return {"ok": bool(tid), "msg": f"Trakt OK – Dark S01E01: {first or 'keine dt. Übersetzung'}"
                if tid else "Trakt erreichbar, Testserie nicht gefunden"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}
