"""Medienserver (Emby oder Jellyfin): Bibliothek auslesen, Abgleich mit Scraparr, gezielt einzelne Titel neu
einlesen lassen. Jellyfin ist ein Emby-Fork mit fast gleicher API – Unterschiede: Anmelde-Header, Einzelabfrage
von Items (/Items/{id}?userId= statt /Users/{id}/Items/{id}) und Erkennung über /System/Info/Public."""
import os
import re
import time

from . import config, db, pathmap
from .http import SourceError, request

_items_by_path: dict[str, str] = {}       # lokaler Pfad -> Emby-Item-ID (aus dem letzten Abgleich)
_touched: list[str] = []                  # im laufenden Job geänderte Filme/Serienordner


def moved(old, new):
    """Bekannte Emby-ID auf den neuen Pfad umhängen (Umbenennung durch Radarr/Sonarr)."""
    iid = _items_by_path.pop(os.path.normpath(old), None) if old else None
    if iid and new:
        _items_by_path[os.path.normpath(new)] = iid


def touch(path):
    if path:
        _touched.append(path)


def take_touched():
    out = list(dict.fromkeys(_touched))
    _touched.clear()
    return out


def enabled():
    return bool(config.get("emby_url") and config.get("emby_api_key"))


def _url(path):
    return config.get("emby_url").rstrip("/") + path


_kind = {"url": None, "kind": None, "until": 0}


def kind():
    """'emby' oder 'jellyfin' – fest eingestellt oder über /System/Info/Public erkannt (zwischengespeichert)."""
    setting = (config.get("media_server") or "auto").lower()
    if setting in ("emby", "jellyfin"):
        return setting
    url = config.get("emby_url")
    if _kind["url"] == url and _kind["kind"] and time.time() < _kind["until"]:
        return _kind["kind"]
    found, ok = "emby", False
    if url:
        try:
            r = request("GET", _url("/System/Info/Public"), min_interval=0, retries=1)
            if r.status_code < 400:
                info = r.json()
                ok = True
                if "jellyfin" in (info.get("ProductName") or "").lower() or "jellyfin" in r.headers.get("server", "").lower():
                    found = "jellyfin"
        except (SourceError, ValueError):
            pass
    # erkannt: 1 Tag merken; Server nicht erreichbar: in 5 Minuten erneut versuchen
    _kind.update(url=url, kind=found, until=time.time() + (86400 if ok else 300))
    return found


def name():
    return "Jellyfin" if kind() == "jellyfin" else "Emby"


server_name = name      # für Funktionen mit Parameter "name"


def _auth():
    key = config.get("emby_api_key")
    if kind() == "jellyfin":
        # Jellyfin 10.11+ akzeptiert X-Emby-Token nur noch mit aktivierter Legacy-Anmeldung
        return {"Authorization": f'MediaBrowser Client="Scraparr", Device="Scraparr", DeviceId="scraparr", '
                                 f'Version="1", Token="{key}"'}
    return {"X-Emby-Token": key}


def _req(method, path, headers_extra=None, **kw):
    headers = {**_auth(), **(headers_extra or {})}
    r = request(method, _url(path), headers=headers, min_interval=0.05, **kw)
    if r.status_code in (401, 403):
        raise SourceError(f"{name()}: API-Key ungültig")
    if r.status_code >= 400:
        raise SourceError(f"{name()}: HTTP {r.status_code} bei {path}")
    return r


def _get_item(item_id):
    """Vollständiges Item (für Änderungen). Jellyfin 10.9+: /Items/{id}?userId=, ältere/Emby: /Users/{uid}/Items/{id}."""
    uid = _admin_user()
    if kind() == "jellyfin":
        try:
            return _req("GET", f"/Items/{item_id}", params={"userId": uid}).json()
        except SourceError:
            pass
    return _req("GET", f"/Users/{uid}/Items/{item_id}").json()


def _list_params(params):
    """Jellyfin: Listen im Kontext des Admin-Benutzers abfragen (ältere Versionen verlangen eine userId)."""
    if kind() == "jellyfin":
        try:
            params = {**params, "userId": _admin_user()}
        except SourceError:
            pass
    return params


def items():
    """Alle Filme und Serien mit Pfad, IDs, Altersfreigabe und Bildstatus."""
    out, start = [], 0
    while True:
        r = _req("GET", "/Items", params=_list_params({
            "Recursive": "true", "IncludeItemTypes": "Movie,Series", "StartIndex": start, "Limit": 1000,
            "Fields": "Path,ProviderIds,OfficialRating,ProductionYear,Overview,OriginalTitle",
            "EnableImageTypes": "Primary,Backdrop",
        })).json()
        batch = r.get("Items", [])
        out.extend(batch)
        start += len(batch)
        if not batch or start >= r.get("TotalRecordCount", 0):
            return out


def refresh_item(item_id):
    _req("POST", f"/Items/{item_id}/Refresh", params={
        "Recursive": "true", "MetadataRefreshMode": "FullRefresh", "ImageRefreshMode": "FullRefresh",
        "ReplaceAllMetadata": "true", "ReplaceAllImages": "true"})


def media_updated(paths, update_type="Modified"):
    """Server über geänderte Pfade informieren (wie Sonarr/Radarr es tun) – erfasst auch neue Titel."""
    body = {"Updates": [{"Path": pathmap.to_remote("emby", p), "UpdateType": update_type} for p in paths]}
    _req("POST", "/Library/Media/Updated", json=body)


def library_refresh():
    _req("POST", "/Library/Refresh")


def _build_map():
    for it in items():
        if it.get("Path"):
            _items_by_path[os.path.normpath(pathmap.to_local("emby", it["Path"]))] = it["Id"]


def notify(paths):
    """Nach einem Job: bekannte Titel gezielt neu einlesen, unbekannte als neu melden."""
    if not enabled() or not config.get("emby_refresh_after_job") or not paths:
        return False
    try:
        if config.get("emby_refresh_mode") == "library" or len(paths) > 100:
            library_refresh()
            db.log("INFO", f"{name()}: Bibliotheksscan angestoßen")
            return True
        if not _items_by_path:
            _build_map()
        known, unknown = [], []
        for p in dict.fromkeys(paths):
            iid = _items_by_path.get(os.path.normpath(p))
            (known if iid else unknown).append((p, iid))
        for _, iid in known:
            refresh_item(iid)
        if unknown:
            media_updated([p for p, _ in unknown], "Created")
        db.log("INFO", f"{name()}: {len(known)} Titel neu eingelesen, {len(unknown)} als neu gemeldet")
        return True
    except SourceError as e:
        db.log("WARN", str(e))
        return False


# ---------------------------------------------------------------- Abgleich

def _age(rating):
    m = re.search(r"(\d+)", rating or "")
    return m.group(1) if m else None


def sync():
    """-> Bericht {summary, issues[]}. Merkt sich die Emby-IDs für gezielte Refreshes."""
    data = items()
    _items_by_path.clear()
    ours_m = {os.path.normpath(r["path"]): r for r in db.query(
        "SELECT id, path, title, year, fsk, tmdb_id, status FROM movies")}
    ours_s = {os.path.normpath(r["folder"]): r for r in db.query(
        "SELECT id, folder, title, year, fsk, tmdb_id, tvdb_id, status FROM shows")}
    issues, seen_m, seen_s = [], set(), set()
    for it in data:
        local = os.path.normpath(pathmap.to_local("emby", it.get("Path") or ""))
        _items_by_path[local] = it["Id"]
        kind = "movie" if it["Type"] == "Movie" else "show"
        ours = (ours_m if kind == "movie" else ours_s).get(local)
        base = {"kind": kind, "emby_id": it["Id"], "emby_title": it.get("Name"), "path": local}
        if not ours:
            issues.append({**base, "type": "unbekannt",
                           "text": "Liegt nicht in einer Scraparr-Bibliothek – Pfadzuordnung prüfen"})
            continue
        (seen_m if kind == "movie" else seen_s).add(local)
        base.update(id=ours["id"], title=ours["title"])
        if ours["status"] != "gescrapt":
            continue
        pid = it.get("ProviderIds") or {}
        emby_tmdb = pid.get("Tmdb") or pid.get("tmdb")
        problems = []
        if emby_tmdb and ours["tmdb_id"] and str(emby_tmdb) != str(ours["tmdb_id"]):
            problems.append(f"TMDB-ID {emby_tmdb} statt {ours['tmdb_id']}")
        elif not emby_tmdb:
            problems.append("keine TMDB-ID (NFO nicht gelesen?)")
        if (it.get("Name") or "").strip() != (ours["title"] or "").strip():
            problems.append(f"Titel „{it.get('Name')}“")
        if ours["fsk"] and _age(it.get("OfficialRating")) != str(ours["fsk"]):
            problems.append(f"Freigabe „{it.get('OfficialRating') or '–'}“ statt FSK {ours['fsk']}")
        if not (it.get("ImageTags") or {}).get("Primary"):
            problems.append("kein Poster")
        if problems:
            issues.append({**base, "type": "abweichung", "text": "; ".join(problems), "action": "emby_refresh"})
    for table, ours, seen, kind in (("movies", ours_m, seen_m, "movie"), ("shows", ours_s, seen_s, "show")):
        for p, r in ours.items():
            if p not in seen and r["status"] == "gescrapt":
                issues.append({"kind": kind, "id": r["id"], "title": r["title"], "path": p, "type": "fehlt",
                               "text": f"Nicht in {name()}", "action": "emby_add"})
    summary = {
        "emby_titel": len(data),
        "abweichungen": sum(i["type"] == "abweichung" for i in issues),
        "fehlt_in_emby": sum(i["type"] == "fehlt" for i in issues),
        "unbekannt": sum(i["type"] == "unbekannt" for i in issues),
    }
    return {"summary": summary, "issues": issues}


def apply(issues):
    """Aktionen aus dem Bericht ausführen."""
    done = 0
    refresh = [i for i in issues if i.get("action") == "emby_refresh" and i.get("emby_id")]
    add = [i["path"] for i in issues if i.get("action") == "emby_add"]
    for i in refresh:
        refresh_item(i["emby_id"])
        done += 1
    if add:
        media_updated(add, "Created")
        done += len(add)
    db.log("INFO", f"{name()}-Abgleich: {len(refresh)} Titel neu eingelesen, {len(add)} gemeldet")
    return done


def test():
    if not enabled():
        return {"ok": False, "msg": "Server-URL oder API-Key fehlt"}
    try:
        _kind.update(url=None, kind=None, until=0)   # Servertyp neu erkennen (URL evtl. geändert)
        i = _req("GET", "/System/Info").json()
        auto = " – automatisch erkannt" if (config.get("media_server") or "auto") == "auto" else ""
        return {"ok": True, "msg": f"Verbunden: {i.get('ServerName')} ({name()} {i.get('Version')}{auto})"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}


# ---------------------------------------------------------------- Kollektionen (BoxSets)

_admin = {"id": None}


def _admin_user():
    """Emby/Jellyfin brauchen für Einzelabfragen/Änderungen von Items einen Benutzerkontext – erster Admin."""
    if _admin["id"]:
        return _admin["id"]
    users = []
    for path in ("/Users/Query", "/Users"):
        try:
            data = _req("GET", path).json()
            users = data.get("Items", []) if isinstance(data, dict) else data
            if users:
                break
        except SourceError:
            continue
    admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), users[0] if users else None)
    if not admin:
        raise SourceError(f"{name()}: kein Benutzer gefunden")
    _admin["id"] = admin["Id"]
    return _admin["id"]


def boxsets():
    r = _req("GET", "/Items", params=_list_params({"Recursive": "true", "IncludeItemTypes": "BoxSet",
                                                   "Fields": "ProviderIds,Overview", "EnableImageTypes": "Primary"})).json()
    return r.get("Items", [])


def collection_members(box_id):
    r = _req("GET", "/Items", params=_list_params({"ParentId": box_id, "Fields": "Path"})).json()
    return [i["Id"] for i in r.get("Items", [])]


def create_collection(name, item_ids):
    r = _req("POST", "/Collections", params={"Name": name, "Ids": ",".join(item_ids)})
    data = r.json() if r.content else {}
    if not data.get("Id"):
        raise SourceError(f"{server_name()}: Kollektion „{name}“ wurde nicht angelegt")
    db.log("INFO", f"{server_name()}: Kollektion „{name}“ angelegt ({len(item_ids)} Filme)")
    return data["Id"]


def add_to_collection(box_id, item_ids):
    _req("POST", f"/Collections/{box_id}/Items", params={"Ids": ",".join(item_ids)})


def set_metadata(item_id, name, overview, provider_ids):
    """Deutschen Namen/Text + Provider-IDs setzen und sperren, damit der Server sie nicht überschreibt."""
    dto = _get_item(item_id)
    locked = set(dto.get("LockedFields") or [])
    dto["Name"] = name
    if overview:
        dto["Overview"] = overview
        locked.add("Overview")
    dto["ProviderIds"] = {**(dto.get("ProviderIds") or {}), **provider_ids}
    locked.add("Name")
    dto["LockedFields"] = sorted(locked)
    dto["LockData"] = dto.get("LockData", False)
    _req("POST", f"/Items/{item_id}", json=dto)


def upload_image(item_id, image_type, data, mime="image/jpeg"):
    """Emby und Jellyfin erwarten das Bild base64-kodiert im Body."""
    import base64
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    _req("POST", f"/Items/{item_id}/Images/{image_type}", content=base64.b64encode(data),
         headers_extra={"Content-Type": mime})
