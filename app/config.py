"""Einstellungen: Defaults aus Umgebungsvariablen, Änderungen aus der Web-UI in SQLite."""
import json
import os

from . import db

DEFAULTS = {
    # Zugang
    "tmdb_api_key": os.environ.get("TMDB_API_KEY", ""),   # v3-Key oder v4-Lesezugriffstoken
    "tvdb_api_key": os.environ.get("TVDB_API_KEY", ""),
    "tvdb_pin": os.environ.get("TVDB_PIN", ""),         # nur bei "user-supported" Keys nötig
    "fanart_api_key": os.environ.get("FANART_API_KEY", ""),
    "fanart_client_key": os.environ.get("FANART_CLIENT_KEY", ""),   # optional, persönlicher Key
    "omdb_api_key": os.environ.get("OMDB_API_KEY", ""),
    "trakt_client_id": os.environ.get("TRAKT_CLIENT_ID", ""),
    "animelists_enabled": True,              # Fribb/anime-lists (GitHub), ohne Key
    "fernsehserien_enabled": os.environ.get("FERNSEHSERIEN_ENABLED", "true").lower() == "true",
    "fernsehserien_plots": True,             # auch deutsche Episodeninhalte übernehmen
    "ofdb_enabled": os.environ.get("OFDB_ENABLED", "true").lower() == "true",
    "wikidata_enabled": True,
    # Quellen-Logik
    "fsk_order": "ofdb,tmdb,wikidata",       # erste Quelle mit Treffer gewinnt
    "plot_fallback_ofdb": True,              # deutsche OFDb-Inhaltsangabe statt englischem TMDB-Text
    "rating_default": "imdb",                # imdb | tmdb – welche Wertung Emby als Hauptwertung sieht
    "artwork_order": "tmdb,fanart,tvdb",     # innerhalb gleicher Sprache
    "download_extra_art": True,              # clearart, disc, banner, landscape (fanart.tv)
    "tv_episode_source": "tmdb",             # tmdb | tvdb
    "tv_episode_order": "default",           # default | dvd | absolute (nur TVDB)
    # Bibliotheken (mehrere Pfade mit ; trennen)
    "movie_paths": os.environ.get("MOVIE_PATHS", "/media/filme"),
    "tv_paths": os.environ.get("TV_PATHS", "/media/serien"),
    "tv_ignore_folders": os.environ.get("TV_IGNORE_FOLDERS", ""),   # Ordnernamen, die nie als Serie gelten (kommagetrennt)
    # Sprache
    "language": os.environ.get("LANGUAGE", "de-DE"),
    "fallback_language": os.environ.get("FALLBACK_LANGUAGE", "en-US"),
    "country": os.environ.get("COUNTRY", "DE"),
    "fsk_format": os.environ.get("FSK_FORMAT", "FSK-{age}"),   # z. B. "FSK-{age}", "FSK {age}", "DE-{age}"
    # Artwork
    "poster_size": "original",
    "fanart_size": "original",
    "download_logo": True,
    "download_actor_images": False,
    "download_episode_thumbs": True,
    "download_season_posters": True,
    "download_tv_themes": True,              # Titelmelodie (theme.mp3) für Serien, Quelle: Plex über TVDB-ID
    "overwrite_artwork": False,
    # Trailer
    "trailer_mode": os.environ.get("TRAILER_MODE", "download"),  # off | nfo | download
    "trailer_fallback_en": False,      # wenn kein deutscher Trailer existiert, englischen nehmen
    "trailer_max_height": 1080,
    "trailer_cookies": "",             # Pfad zu cookies.txt (Netscape-Format); leer = /data/cookies.txt, falls vorhanden
    # Automatik
    "auto_enabled": os.environ.get("AUTO_ENABLED", "true").lower() == "true",
    "auto_interval_min": int(os.environ.get("AUTO_INTERVAL_MIN", "60")),
    "auto_scrape_new": True,           # neue, eindeutig erkannte Titel automatisch scrapen
    "auto_rename": False,
    # Umbenennen
    "rename_movie_folder": "{title} ({year})",
    "rename_movie_file": "{title} ({year})",
    "rename_episode_file": "{showtitle} - {eps} - {title}",
    # Kollektionen (Filmreihen)
    "collections_enabled": True,
    "collection_min_movies": 2,              # erst ab so vielen eigenen Filmen eine Kollektion anlegen
    "collections_to_emby": True,             # Kollektionen in Emby anlegen/aktualisieren
    "collection_art_folder": "",             # optional: Bilder zusätzlich als <Ordner>/<Name>/poster.jpg ablegen
    # Emby
    "media_server": os.environ.get("MEDIA_SERVER", "auto"),   # auto | emby | jellyfin
    "emby_url": os.environ.get("EMBY_URL", "") or os.environ.get("JELLYFIN_URL", ""),
    "emby_api_key": os.environ.get("EMBY_API_KEY", "") or os.environ.get("JELLYFIN_API_KEY", ""),
    "emby_refresh_after_job": True,
    "emby_refresh_mode": "item",             # item: nur geänderte Titel neu einlesen | library: ganze Bibliothek
    "emby_path_map": os.environ.get("EMBY_PATH_MAP", ""),     # "Emby-Pfad => lokaler Pfad", eine Zeile je Zuordnung
    # Radarr / Sonarr
    "radarr_url": os.environ.get("RADARR_URL", ""),
    "radarr_api_key": os.environ.get("RADARR_API_KEY", ""),
    "radarr_path_map": os.environ.get("RADARR_PATH_MAP", ""),
    "sonarr_url": os.environ.get("SONARR_URL", ""),
    "sonarr_api_key": os.environ.get("SONARR_API_KEY", ""),
    "sonarr_path_map": os.environ.get("SONARR_PATH_MAP", ""),
    "arr_auto_ids": True,                    # offene/unklare Titel mit Radarr-/Sonarr-ID zuordnen
    "sonarr_episode_numbers": True,          # nicht erkannte Episoden mit Sonarrs Nummerierung versehen
    "webhook_token": os.environ.get("WEBHOOK_TOKEN", ""),
    "webhook_delay_sec": 30,                 # Wartezeit, um Staffelpakete gesammelt zu verarbeiten
}

_BOOL = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
_INT = {k for k, v in DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool)}


def _coerce(key, value):
    if key in _BOOL:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "on", "ja", "yes")
        return bool(value)
    if key in _INT:
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULTS[key]
    return "" if value is None else str(value)


_cache = {"db": None, "data": None}


def get_all() -> dict:
    """Einstellungen; im Speicher gehalten (get() läuft pro Datei im Scan), save() leert den Cache."""
    if _cache["data"] is not None and _cache["db"] == db.DB_PATH:
        return dict(_cache["data"])
    data = dict(DEFAULTS)
    for r in db.query("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            data[r["key"]] = _coerce(r["key"], json.loads(r["value"]))
    _cache.update(db=db.DB_PATH, data=data)
    return dict(data)


def get(key):
    if _cache["data"] is not None and _cache["db"] == db.DB_PATH:
        return _cache["data"][key]
    return get_all()[key]


def save(values: dict):
    _cache["data"] = None
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        v = _coerce(k, v)
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))


def paths(key) -> list[str]:
    return [p.strip() for p in get(key).split(";") if p.strip()]


def lang_short() -> str:
    return get("language").split("-")[0]


def format_fsk(age) -> str:
    if age in (None, ""):
        return ""
    return get("fsk_format").replace("{age}", str(age))
