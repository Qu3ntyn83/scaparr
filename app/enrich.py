"""TMDB-Grunddaten mit den Zusatzquellen anreichern: OFDb, Wikidata, OMDb, fanart.tv, TVDB."""
from . import artwork, config, db, fanart, fernsehserien, ofdb, omdb, tmdb, trakt, tvdb, wikidata
from .http import SourceError


def _fsk_order():
    return [s.strip() for s in config.get("fsk_order").split(",") if s.strip()]


def _pick_fsk(meta, candidates):
    """Erste Quelle in der eingestellten Reihenfolge mit Wert gewinnt."""
    for src in _fsk_order():
        if candidates.get(src):
            meta["fsk"] = candidates[src]
            meta["sources"]["fsk"] = {"tmdb": "TMDB", "ofdb": "OFDb", "wikidata": "Wikidata"}[src]
            return
    meta["fsk"] = None
    meta["sources"]["fsk"] = None


def _ratings(meta):
    r = {}
    if meta.get("rating"):
        r["tmdb"] = {"value": round(float(meta["rating"]), 1), "votes": meta.get("votes") or 0}
    r.update(omdb.ratings(meta.get("imdb_id")))
    meta["ratings"] = r
    if any(k != "tmdb" for k in r):
        meta["sources"]["ratings"] = "TMDB + OMDb"


def _english_plot(meta):
    return not meta.get("plot") or (meta["sources"].get("plot") or "").startswith("TMDB (")


def _trakt_texts(meta, kind):
    """Trakt-Übersetzung, wenn Inhalt noch englisch/leer ist (bzw. Tagline fehlt)."""
    if not trakt.enabled() or not (_english_plot(meta) or (kind == "movie" and not meta.get("tagline"))):
        return
    t = trakt.translation(kind, meta.get("tmdb_id"))
    if t.get("overview") and _english_plot(meta):
        meta["plot"] = t["overview"]
        meta["sources"]["plot"] = "Trakt"
    if kind == "movie" and t.get("tagline") and not meta.get("tagline"):
        meta["tagline"] = t["tagline"]


def movie(meta):
    meta.setdefault("sources", {})
    order = _fsk_order()
    cands = {"tmdb": meta.get("fsk")}
    need_wd = wikidata.enabled() and ("wikidata" in order or ofdb.enabled())
    wd = wikidata.lookup("movie", meta.get("tmdb_id"), meta.get("imdb_id")) if need_wd else {}
    cands["wikidata"] = wd.get("fsk")
    if wd.get("qid"):
        meta["wikidata_id"] = wd["qid"]
    want_plot = config.get("plot_fallback_ofdb") and _english_plot(meta)
    if ofdb.enabled() and wd.get("ofdb_id") and ("ofdb" in order or want_plot):
        meta["ofdb_id"] = wd["ofdb_id"]
        of = ofdb.fetch(wd["ofdb_id"], want_fsk="ofdb" in order, want_plot=want_plot)
        cands["ofdb"] = of.get("fsk")
        if want_plot and of.get("plot"):
            meta["plot"] = of["plot"]
            meta["sources"]["plot"] = "OFDb"
    _pick_fsk(meta, cands)
    _trakt_texts(meta, "movie")
    _ratings(meta)
    art = artwork.merge(meta.get("images"), fanart.movie_art(meta.get("tmdb_id"), meta.get("imdb_id")))
    meta["art"] = artwork.choose_all(art)
    meta.pop("images", None)          # Kandidatenlisten nicht in der DB aufbewahren
    return meta


def show(meta):
    meta.setdefault("sources", {})
    order = _fsk_order()
    cands = {"tmdb": meta.get("fsk")}
    if wikidata.enabled() and "wikidata" in order:
        wd = wikidata.lookup("tv", meta.get("tmdb_id"), meta.get("imdb_id"))
        cands["wikidata"] = wd.get("fsk")
        if wd.get("qid"):
            meta["wikidata_id"] = wd["qid"]
    _pick_fsk(meta, cands)               # OFDb führt kaum Serien -> nicht abgefragt
    _ratings(meta)
    tvdb_art = {}
    if tvdb.enabled():
        try:
            if not meta.get("tvdb_id"):
                hits = tvdb.search(meta.get("original_title") or meta["title"], meta.get("year"))
                if hits:
                    meta["tvdb_id"] = hits[0]["tvdb_id"]
            if meta.get("tvdb_id"):
                if _english_plot(meta):
                    tr = tvdb.translation(meta["tvdb_id"])
                    if tr.get("overview"):
                        meta["plot"] = tr["overview"]
                        meta["sources"]["plot"] = "TVDB"
                tvdb_art = tvdb.series_art(meta["tvdb_id"])
        except SourceError as e:
            db.log("WARN", str(e))
    _trakt_texts(meta, "show")
    fan = fanart.tv_art(meta.get("tvdb_id"))
    meta["_season_cands"] = fan.pop("season_poster", [])
    art = artwork.merge(meta.get("images"), fan, tvdb_art)
    meta["art"] = artwork.choose_all(art)
    meta.pop("images", None)
    return meta


def tvdb_episodes(meta, force_default=False):
    """Episodenliste von TVDB (für Ersatz-Titel oder als Hauptquelle)."""
    if not tvdb.enabled() or not meta.get("tvdb_id"):
        return {}
    order = config.get("tv_episode_order") if config.get("tv_episode_source") == "tvdb" else "default"
    if force_default:          # Anime-Zuordnung in TVDB-Zählung -> Standard-Reihenfolge
        order = "default"
    try:
        return tvdb.episodes(meta["tvdb_id"], order)
    except SourceError as e:
        db.log("WARN", str(e))
        return {}


def _missing_title(e):
    t = e.get("title") or ""
    return not t or e.get("title_fallback") or bool(tmdb._PLACEHOLDER.match(t))


def _missing_plot(e):
    return not e.get("plot") or e.get("plot_fallback")


def fill_episodes(meta, season_no, eps, cache):
    """Fehlende/englische Episodentexte auffüllen: Trakt → fernsehserien.de (TVDB lief vorher)."""
    if not eps:
        return
    if trakt.enabled() and any(_missing_title(e) or _missing_plot(e) for e in eps.values()):
        try:
            if "trakt_id" not in cache:
                cache["trakt_id"] = trakt.trakt_id("show", meta["tmdb_id"])
            if cache["trakt_id"]:
                tr = trakt.season_episodes(cache["trakt_id"], season_no)
                for n, e in eps.items():
                    t = tr.get(n)
                    if not t:
                        continue
                    if _missing_title(e) and t["title"] and not tmdb._PLACEHOLDER.match(t["title"]):
                        e.update(title=t["title"], src_title="Trakt", title_fallback=False)
                    if _missing_plot(e) and t["plot"]:
                        e.update(plot=t["plot"], src_plot="Trakt", plot_fallback=False)
        except SourceError as ex:
            db.log("WARN", str(ex))
    if fernsehserien.enabled() and any(_missing_title(e) or _missing_plot(e) for e in eps.values()):
        try:
            if "fs_slug" not in cache:
                cache["fs_slug"] = meta.get("fs_slug") or fernsehserien.find_slug(
                    [meta.get("base_title") or meta.get("title"), meta.get("original_title")], meta.get("year"))
                cache["fs_eps"] = {}
                if cache["fs_slug"]:
                    meta["fs_slug"] = cache["fs_slug"]
            if cache["fs_slug"]:
                if season_no not in cache.setdefault("fs_seasons", set()):
                    cache["fs_eps"].update(fernsehserien.episodes(cache["fs_slug"], [season_no]))
                    cache["fs_seasons"].add(season_no)
                for n, e in eps.items():
                    f = cache["fs_eps"].get((season_no, n))
                    if not f:
                        continue
                    if _missing_title(e) and f["title"] and not tmdb._PLACEHOLDER.match(f["title"]):
                        e.update(title=f["title"], src_title="fernsehserien.de", title_fallback=False)
                    if config.get("fernsehserien_plots") and _missing_plot(e) and f.get("plot"):
                        e.update(plot=f["plot"], src_plot="fernsehserien.de", plot_fallback=False)
        except SourceError as ex:
            db.log("WARN", str(ex))
