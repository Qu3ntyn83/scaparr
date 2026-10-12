"""Scrapen: TMDB-Daten holen, NFO + Artwork + Trailer schreiben."""
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata

import httpx

from . import parser, animelists, artpicker, artwork, config, db, emby, enrich, moviesets, nfo, paths, tmdb, tvdb


# ---------------------------------------------------------------- Matching

UMLAUT_EXPAND = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _norm(s, expand=True):
    """Vergleichsform. expand=True: ä→ae (passt zu Dateinamen ohne Umlaute), sonst ä→a."""
    s = (s or "").casefold().replace("&", "und")
    s = s.translate(UMLAUT_EXPAND) if expand else s.replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^(der|die|das|the|a|an|ein|eine)\s+", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _forms(s):
    return {_norm(s, True), _norm(s, False)} - {""}


def _same(title_forms, r):
    return bool(title_forms & (_forms(r["title"]) | _forms(r.get("original_title"))))


# ae/oe/ue/ss im Dateinamen können Umlaute sein: "Maedchen" → "Mädchen", "Strasse" → "Straße"
_UML = (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("Ae", "Ä"), ("Oe", "Ö"), ("Ue", "Ü"))


def umlaut_variants(title):
    """Alternative Schreibweisen mit Umlauten, wichtigste zuerst (höchstens 3)."""
    if not title:
        return []
    out = []
    vowels = title
    for a, b in _UML:
        # "que"/"Que" (Queen, Quest), "eue"/"aue" (neue, blaue) nicht umwandeln
        vowels = re.sub(rf"(?<![qQ])(?<![aeiouAEIOU]){a}" if a.lower() == "ue" else a, b, vowels)
    sz = title.replace("ss", "ß")
    for v in (vowels, vowels.replace("ss", "ß"), sz):
        if v != title and v not in out:
            out.append(v)
    return out[:3]


TV_GENRES = {10770, 99}        # TMDB: TV-Film, Dokumentarfilm
_ROMAN = {"2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI"}


def _alt_queries(title):
    """Weitere Suchbegriffe, wichtigste zuerst: (Suchtext, Vergleichstitel, darf sicher sein?)."""
    out = [(v, title, True) for v in umlaut_variants(title)]
    m = re.match(r"^(.*\S)\s+(\d)$", title or "")
    if m:                                       # "Die Pilgerin 2"
        base, n = m.groups()
        if n in _ROMAN:
            out.append((f"{base} {_ROMAN[n]}", f"{base} {_ROMAN[n]}", True))   # Rocky 2 -> Rocky II
        out.append((base, base, "tv"))          # Mehrteiler: nur bei TV-Produktion sicher
    parts = re.split(r"\s+[-–:]\s+", title or "", maxsplit=1)
    if len(parts) == 2 and all(len(p) >= 3 for p in parts):
        out.append((parts[0], parts[0], "tv"))  # "Gigant des Nordens - Der Hamburger Hafen"
        out.append((parts[1], parts[1], "tv"))  # "Tatort - Der Turm"
    seen, uniq = {title}, []
    for q in out:
        if q[0] not in seen:
            seen.add(q[0])
            uniq.append(q)
    return uniq


def _same_as_tv(title, year):
    """Gibt es den Titel bei TMDB nur als Serie (z. B. zweiteiliger TV-Film als Miniserie)?"""
    try:
        res = tmdb.search_tv(title, year) or tmdb.search_tv(re.sub(r"\s+\d$", "", title or ""), year)
    except Exception:          # noqa – reiner Hinweis, darf den Scrape nicht stören
        return None
    tf = _forms(re.sub(r"\s+\d$", "", title or ""))
    return next((r for r in res if _same(tf, r)), None)


def _is_tv(r):
    return bool(TV_GENRES & set(r.get("genre_ids") or []))


def search_with_variants(search, title, year):
    """TMDB-Suche mit Ausweichbegriffen: Umlaute, römische Ziffern, Mehrteiler ohne Teilnummer,
    Haupt-/Untertitel einzeln. Treffer über gekürzte Titel gelten nur bei TV-Filmen/Dokus als sicher."""
    results = search(title, year) or []
    hit, sure = best_match(results, title, year)
    if sure:
        return hit, sure, results
    seen = {r["tmdb_id"] for r in results}
    for query, match_title, may_be_sure in _alt_queries(title):
        found = search(query, year) or []
        more = [r for r in found if r["tmdb_id"] not in seen]
        seen.update(r["tmdb_id"] for r in more)
        results = results + more
        h, s = best_match(found, match_title, year)
        if h and s and may_be_sure == "tv" and not _is_tv(h):
            s = False                           # gekürzter Titel bei Kinofilm: lieber prüfen lassen
        if h and s:
            db.log("INFO", f"Treffer über Suchbegriff „{query}“: „{title}“ → {h['title']} ({h.get('year') or '?'})")
            return h, True, results
        if h and not hit:
            hit = h
    if not hit and results:
        hit, _ = best_match(results, title, year)
    return hit, False, results


def best_match(results, title, year):
    """Gibt (Treffer, sicher?) zurück. Unsichere Treffer gehen in die manuelle Prüfung."""
    if not results:
        return None, False
    tf = _forms(title)

    def score(r):
        s = 0
        if tf and _same(tf, r):
            s += 100
        if year and r["year"]:
            d = abs(year - r["year"])
            s += 50 if d == 0 else 25 if d == 1 else -30
        return s + min(r.get("popularity", 0), 50) / 10

    ranked = sorted(results, key=score, reverse=True)
    top = ranked[0]
    exact = _same(tf, top)
    year_ok = (not year) or (top["year"] and abs(year - top["year"]) <= 1)
    if exact and year_ok:
        if not year:   # ohne Jahr nur sicher, wenn kein zweiter exakter Titel existiert
            others = [r for r in ranked[1:] if _same(tf, r)]
            return top, not others
        return top, True
    if len(results) == 1 and year and top["year"] == year:
        return top, True
    return top, False


# ---------------------------------------------------------------- Downloads

def file_error(e, path):
    """Verständliche Meldung für Dateifehler (meist Rechte auf der NAS-Freigabe)."""
    if isinstance(e, PermissionError) or getattr(e, "errno", None) in (1, 13):
        return f"Keine Schreibrechte: {getattr(e, 'filename', None) or path} – Rechte auf der NAS-Freigabe prüfen"
    if getattr(e, "errno", None) == 30:
        return f"Freigabe ist schreibgeschützt eingehängt: {path}"
    return f"Dateifehler: {e}"


def _download(url, dest, force=False):
    if not url:
        return False
    if os.path.exists(dest) and not (force or config.get("overwrite_artwork")):
        return True
    tmp = None
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
            r.raise_for_status()
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".dl-")
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        os.replace(tmp, dest)
        return True
    except httpx.HTTPError as e:
        db.log("WARN", f"Download fehlgeschlagen {url}: {e}")
        return False
    except OSError as e:
        db.log("WARN", file_error(e, dest))
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


_yt_block = {"until": 0.0}      # YouTube-Sperre: bis dahin keine weiteren Versuche (jeder Versuch verschlimmert sie)
YT_PAUSE_SEC = 1800


def download_trailer(trailer, dest, force=False):
    if not trailer or config.get("trailer_mode") != "download":
        return False
    if os.path.exists(dest) and not force:
        return True
    if time.time() < _yt_block["until"]:
        return False                # Trailer bleibt als Link in der NFO; nächster Versuch nach der Pause
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        db.log("WARN", "yt-dlp nicht installiert – Trailer nur als Link in der NFO.")
        return False
    h = config.get("trailer_max_height")
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except OSError as e:
        db.log("WARN", file_error(e, dest))
        return False
    tmp = dest + ".part.mp4"
    cmd = [ytdlp, "-q", "--no-playlist", "--no-progress", "--js-runtimes", "deno",
           "-f", f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b",
           "--merge-output-format", "mp4", "-o", tmp, "--force-overwrites", trailer["url"]]
    cookies = config.get("trailer_cookies").strip() or os.path.join(db.DATA_DIR, "cookies.txt")
    ctmp = None
    if os.path.isfile(cookies):
        try:    # Kopie nutzen: yt-dlp schreibt die Datei zurück, die Quelle kann schreibgeschützt sein
            fd, ctmp = tempfile.mkstemp(prefix=".cookies-", suffix=".txt")
            os.close(fd)
            shutil.copyfile(cookies, ctmp)
            cmd[1:1] = ["--cookies", ctmp]
        except OSError as e:
            db.log("WARN", f"Cookie-Datei nicht lesbar: {cookies}: {e}")
            ctmp = None
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, dest)
            _clean_partials(dest)
            db.log("INFO", f"Trailer geladen: {os.path.basename(dest)}")
            return True
        errs = [l.strip() for l in p.stderr.splitlines() if l.strip().startswith("ERROR")]
        msg = errs[-1] if errs else p.stderr.strip()[-300:]
        if not shutil.which("deno"):
            msg += " (Deno fehlt – Image mit aktuellem Dockerfile neu bauen)"
        if "not a bot" in msg or "Sign in" in msg:
            _yt_block["until"] = time.time() + YT_PAUSE_SEC
            msg = ("YouTube verlangt eine Anmeldung (Bot-Schutz). Abhilfe: cookies.txt aus dem Browser exportieren "
                   "und als /data/cookies.txt ablegen – siehe README. Weitere Downloads pausieren 30 Minuten") + (
                   " [Cookies wurden verwendet, sind evtl. abgelaufen]" if ctmp else "")
        db.log("WARN", f"Trailer-Download fehlgeschlagen ({trailer['url']}): {msg}")
    except subprocess.TimeoutExpired:
        db.log("WARN", f"Trailer-Download Zeitüberschreitung: {trailer['url']}")
    finally:
        if ctmp and os.path.exists(ctmp):
            os.remove(ctmp)
    _clean_partials(dest)
    return False


def _clean_partials(dest):
    """Abgebrochene yt-dlp-Zwischendateien (…-trailer.mp4.part*, .temp, .fNNN) entfernen."""
    for f in glob.glob(glob.escape(dest) + ".part*") + glob.glob(glob.escape(os.path.splitext(dest)[0]) + ".f[0-9]*"):
        try:
            os.remove(f)
        except OSError:
            pass


def _artwork(files, meta, force, locks=None):
    art = meta.get("art") or {}
    # von Hand gewählte Bilder (Artwork-Auswahl) haben Vorrang vor der automatischen Wahl
    for kind, url in (locks or {}).items():
        if url and kind in files:
            art[kind] = {"url": url, "source": artpicker._source_of(url), "lang": None, "manual": True}
    meta["art"] = art
    # ältere Datensätze (vor Mehrquellen-Version): nur TMDB-Pfade
    for kind, size in (("poster", config.get("poster_size")), ("fanart", config.get("fanart_size")),
                       ("logo", "original")):
        if kind not in art and meta.get(kind):
            art[kind] = {"url": tmdb.img(meta[kind], size), "source": "tmdb"}
    kinds = ["poster", "fanart"]
    if config.get("download_logo"):
        kinds.append("logo")
    if config.get("download_extra_art"):
        kinds += [k for k in artwork.EXTRA_KINDS if k in files]
    kinds += [k for k in (locks or {}) if k not in kinds]
    for kind in kinds:
        if art.get(kind) and kind in files:
            # gesperrte Bilder nur neu laden, wenn die Datei fehlt
            _download(art[kind]["url"], files[kind], force and kind not in (locks or {}))


THEME_URL = "https://tvthemes.plexapp.com/{tvdb_id}.mp3"


def download_theme(folder, tvdb_id):
    """Serien-Titelmelodie von Plex (öffentlich, über die TVDB-ID). Vorhandenes wird nie überschrieben.
    -> "ok" | "vorhanden" | "keins" (für diese Serie gibt es keins) | "fehler" """
    if not tvdb_id:
        return "keins"
    if paths.has_theme(folder):
        return "vorhanden"
    try:
        r = httpx.get(THEME_URL.format(tvdb_id=int(tvdb_id)), timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        db.log("WARN", f"Theme-Download fehlgeschlagen ({folder}): {e}")
        return "fehler"
    if r.status_code == 404:
        return "keins"
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or ("audio" not in ctype and not r.content.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3"))) \
            or len(r.content) < 20_000:
        return "keins"
    dest = paths.theme_file(folder)
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".dl-")
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        os.replace(tmp, dest)
    except OSError as e:
        db.log("WARN", file_error(e, dest))
        return "fehler"
    return "ok"


def _actor_images(base_folder, cast):
    if not config.get("download_actor_images"):
        return
    for a in cast or []:
        if a.get("thumb"):
            fname = re.sub(r"[^\w\-. ]", "_", a["name"]).replace(" ", "_") + ".jpg"
            _download(a["thumb"], os.path.join(base_folder, ".actors", fname))


# ---------------------------------------------------------------- Filme

ANIME_KEYS = ("anidb", "anilist", "mal", "kitsu", "anisearch")


def anime_entry(hints):
    """Anime-Eintrag über eine ID im Ordner-/Dateinamen oder aus einer alten NFO ([anidb-123] …)."""
    for k in ANIME_KEYS:
        if (hints or {}).get(f"{k}_id"):
            e = animelists.by_id(k, hints[f"{k}_id"])
            if e:
                return e
    return None


def _norm_key(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def resolve_movie_id(m):
    if m.get("tmdb_id"):
        return m["tmdb_id"], True, None
    mp = animelists.mapping(anime_entry(m.get("hints")))
    if mp and mp.get("tmdb_movie"):
        return int(mp["tmdb_movie"]), True, None
    if m.get("imdb_id"):
        r = tmdb.find_imdb(m["imdb_id"]).get("movie_results", [])
        if r:
            return r[0]["id"], True, None
    hit, sure, results = search_with_variants(tmdb.search_movie, m["guess_title"], m["guess_year"])
    if not sure:
        # Dateiname taugt nicht? Dann mit dem Ordnernamen (bzw. umgekehrt) versuchen – nur sichere Treffer zählen
        used = {_norm_key(m["guess_title"])}
        for alt in parser.title_candidates(m["path"], bool(m.get("single_folder"))):
            if _norm_key(alt["title"]) in used:
                continue
            used.add(_norm_key(alt["title"]))
            h, s, res = search_with_variants(tmdb.search_movie, alt["title"], alt["year"] or m["guess_year"])
            if h and s:
                db.log("INFO", f"Treffer über Ordner-/Dateinamen „{alt['title']}“: „{m['guess_title']}“ → "
                               f"{h['title']} ({h.get('year') or '?'})")
                return h["tmdb_id"], True, res
            results = results + [r for r in res if r["tmdb_id"] not in {x["tmdb_id"] for x in results}]
            if not hit and h:
                hit = h
    return (hit["tmdb_id"] if hit else None), sure, results


def scrape_movie(movie_id, tmdb_id=None, force=False, auto=False):
    m = db.one("SELECT * FROM movies WHERE id=?", (movie_id,))
    if not m:
        return False
    try:
        if tmdb_id:
            sure = True
        else:
            tmdb_id, sure, _ = resolve_movie_id(m)
        if not tmdb_id:
            msg = "Kein Treffer bei TMDB"
            tv = _same_as_tv(m["guess_title"], m["guess_year"])
            if tv:
                msg = (f"Bei TMDB nur als Serie/Mehrteiler geführt: „{tv['title']}“ ({tv.get('year') or '?'}) – "
                       f"in die Serienbibliothek verschieben oder über Suchen & Zuordnen einen Film wählen")
            db.update("movies", movie_id, status="manuell", message=msg)
            db.log("WARN", f"Kein Treffer: {m['guess_title']} ({m['guess_year']})" + (" – nur als Serie bei TMDB" if tv else ""))
            return False
        if not sure and auto:
            db.update("movies", movie_id, status="manuell",
                      message="Mehrdeutiger Treffer – bitte manuell zuordnen")
            db.log("INFO", f"Manuelle Prüfung nötig: {m['guess_title']} ({m['guess_year']})")
            return False
        changed_id = m.get("tmdb_id") and int(m["tmdb_id"]) != int(tmdb_id)
        meta = enrich.movie(tmdb.movie(tmdb_id))
        if meta.get("set") and moviesets.enabled():
            # Reihe vor dem NFO laden (gecacht), damit <set> deutschen Namen + Beschreibung bekommt
            moviesets.refresh([int(meta["set"]["tmdb_id"])])
            row = db.one("SELECT name, overview FROM collections WHERE tmdb_id=?", (int(meta["set"]["tmdb_id"]),))
            if row:
                meta["set"].update(name=row["name"] or meta["set"]["name"], overview=row["overview"] or None)
        entry = anime_entry(m.get("hints")) or animelists.for_movie(meta["tmdb_id"])
        if entry:
            meta["anime_ids"] = animelists.ids(entry)
        files = paths.movie_files(m["path"], bool(m["single_folder"]))
        nfo.write_movie(files["nfo"], meta)
        locks = None if changed_id else m.get("art_lock")
        if changed_id and m.get("art_lock"):
            db.update("movies", movie_id, art_lock=None)       # anderer Film -> alte Bildwahl verwerfen
        _artwork(files, meta, force or changed_id, locks)
        if m["single_folder"]:
            _actor_images(m["folder"], meta.get("cast"))
        download_trailer(meta.get("trailer"), files["trailer"], force or changed_id)
        st = paths.movie_status(m["path"], bool(m["single_folder"]))
        db.update("movies", movie_id, tmdb_id=meta["tmdb_id"], imdb_id=meta.get("imdb_id"),
                  title=meta["title"], original_title=meta.get("original_title"), year=meta.get("year"),
                  fsk=meta.get("fsk"), meta=meta, status="gescrapt", message=None,
                  **{k: int(v) for k, v in st.items()})
        emby.touch(m["path"])
        db.log("INFO", f"Film gescrapt: {meta['title']} ({meta.get('year')}) "
                       f"FSK {meta.get('fsk') or '–'} ({meta['sources'].get('fsk') or '–'}), "
                       f"Inhalt {meta['sources'].get('plot')}, Trailer {'DE' if meta.get('trailer') else '–'}")
        return True
    except tmdb.TMDBError as e:
        db.update("movies", movie_id, status="fehler", message=str(e))
        db.log("ERROR", f"{m['guess_title']}: {e}")
        if "API-Key" in str(e):
            raise
        return False
    except OSError as e:
        msg = file_error(e, m["folder"])
        db.update("movies", movie_id, status="fehler", message=msg)
        db.log("ERROR", f"{m['guess_title']}: {msg}")
        return False


# ---------------------------------------------------------------- Serien

def resolve_show_id(s):
    if s.get("tmdb_id"):
        return s["tmdb_id"], True
    hints = s.get("hints") or {}
    mp = animelists.mapping(anime_entry(hints))
    if mp and mp.get("tmdb_tv"):
        return int(mp["tmdb_tv"]), True
    if hints.get("tvdb_id"):
        r = tmdb.get(f"/find/{hints['tvdb_id']}", external_source="tvdb_id").get("tv_results", [])
        if r:
            return r[0]["id"], True
    if s.get("imdb_id"):
        r = tmdb.find_imdb(s["imdb_id"]).get("tv_results", [])
        if r:
            return r[0]["id"], True
    hit, sure, _ = search_with_variants(tmdb.search_tv, s["guess_title"], s["guess_year"])
    return (hit["tmdb_id"] if hit else None), sure


def scrape_show(show_id, tmdb_id=None, force=False, auto=False, progress=None):
    s = db.one("SELECT * FROM shows WHERE id=?", (show_id,))
    if not s:
        return False
    try:
        if tmdb_id:
            sure = True
        else:
            tmdb_id, sure = resolve_show_id(s)
        if not tmdb_id:
            db.update("shows", show_id, status="manuell", message="Kein Treffer bei TMDB")
            return False
        if not sure and auto:
            db.update("shows", show_id, status="manuell",
                      message="Mehrdeutiger Treffer – bitte manuell zuordnen")
            db.log("INFO", f"Manuelle Prüfung nötig: Serie {s['guess_title']}")
            return False
        changed_id = s.get("tmdb_id") and int(s["tmdb_id"]) != int(tmdb_id)
        force = force or bool(changed_id)
        meta = enrich.show(tmdb.tv(tmdb_id))
        season_cands = meta.pop("_season_cands", [])
        # Anime: Zuordnung über Fribb-Liste (ID im Ordnernamen) oder automatisch über TMDB-/TVDB-ID
        entry = anime_entry(s.get("hints"))
        mp = animelists.mapping(entry)
        if mp and mp.get("tmdb_tv") and int(mp["tmdb_tv"]) != int(tmdb_id):
            entry, mp = None, None                      # manuell anders zugeordnet -> Liste ignorieren
        if not entry:
            entry = animelists.for_show(tmdb_id, meta.get("tvdb_id"))
            mp = None                                   # automatisch gefunden: Ordner = ganze Serie
        if entry:
            meta["anime_ids"] = animelists.ids(entry)
            meta["sources"]["anime"] = "anime-lists"
        mapping = mp if mp and (mp["season"] not in (None, 1) or mp["offset"]) else None
        if mapping:
            _apply_season_mapping(meta, mapping, season_cands)
        files = paths.show_files(s["folder"])
        nfo.write_tvshow(files["nfo"], meta)
        locks = None if changed_id else s.get("art_lock")
        if changed_id and s.get("art_lock"):
            db.update("shows", show_id, art_lock=None)
        _artwork(files, meta, force, locks)
        _actor_images(s["folder"], meta.get("cast"))
        download_trailer(meta.get("trailer"), files["trailer"], force)
        if config.get("download_tv_themes"):
            if download_theme(s["folder"], meta.get("tvdb_id")) == "ok":
                db.log("INFO", f"Titelmelodie geladen: {meta['title']}")
        db.update("shows", show_id, has_theme=int(paths.has_theme(s["folder"])))
        db.update("shows", show_id, tmdb_id=meta["tmdb_id"], imdb_id=meta.get("imdb_id"),
                  tvdb_id=meta.get("tvdb_id"), title=meta["title"],
                  original_title=meta.get("original_title"), year=meta.get("year"), fsk=meta.get("fsk"),
                  meta=meta, status="gescrapt", message=None)
        db.update("shows", show_id, mapping=mapping)
        ok, failed = _scrape_episodes(show_id, meta, force, progress, season_cands, mapping)
        st = paths.show_status(s["folder"])
        db.update("shows", show_id, meta=meta,      # enthält ggf. neu gefundenen fernsehserien.de-Slug
                  message=(f"{failed} Episode(n) ohne Treffer" if failed else None),
                  **{k: int(v) for k, v in st.items()})
        emby.touch(s["folder"])
        db.log("INFO", f"Serie gescrapt: {meta['title']} – {ok} Episoden, {failed} ohne Treffer")
        return True
    except tmdb.TMDBError as e:
        db.update("shows", show_id, status="fehler", message=str(e))
        db.log("ERROR", f"Serie {s['guess_title']}: {e}")
        if "API-Key" in str(e):
            raise
        return False
    except OSError as e:
        msg = file_error(e, s["folder"])
        db.update("shows", show_id, status="fehler", message=msg)
        db.log("ERROR", f"Serie {s['guess_title']}: {msg}")
        return False


def _apply_season_mapping(meta, mp, season_cands):
    """AniDB-artiger Ordner (eine Staffel = eigene Serie): Titel + Poster der zugeordneten Staffel."""
    n = mp["season"]
    name = f"Staffel {n}" if n else "Specials"
    cands = [c for c in season_cands if c.get("season") == n]
    try:
        sd = tmdb.season(meta["tmdb_id"], n)
        name = sd.get("name") or name
        cands = list(sd.get("poster_cands", [])) + cands
    except tmdb.TMDBError:
        pass
    meta["base_title"] = meta["title"]
    meta["title"] = f"{meta['title']} – {name}" + (f" (ab Folge {mp['offset'] + 1})" if mp["offset"] else "")
    best = artwork.choose(cands, "poster")
    if best:
        meta.setdefault("art", {})["poster"] = {"url": best["url"], "source": best["source"], "lang": best.get("lang")}
    meta["sources"]["mapping"] = f"Staffel {n}, Versatz {mp['offset']} ({mp['numbering'].upper()})"


def _merge_tvdb(season, season_no, tv_eps, primary):
    """TMDB-Staffel mit TVDB-Daten zusammenführen.
    primary=True: TVDB-Nummerierung (DVD/absolut) ist maßgeblich, TMDB füllt Lücken (nur bei gleicher Reihenfolge).
    primary=False: TMDB maßgeblich, TVDB ersetzt englische Ersatztexte durch deutsche."""
    eps = season.setdefault("episodes", {})
    if primary:
        same_order = config.get("tv_episode_order") == "default"
        new = {}
        for (s, n), te in tv_eps.items():
            if s != season_no:
                continue
            base = eps.get(n, {}) if same_order else {}
            merged = {**base, **{k: v for k, v in te.items() if v not in (None, "")}}
            merged.setdefault("title", "")
            merged["src"] = "TVDB"
            new[n] = merged
        season["episodes"] = new
        return
    for n, e in eps.items():
        te = tv_eps.get((season_no, n))
        if not te:
            continue
        if e.get("title_fallback") and te.get("title"):
            e["title"], e["src_title"], e["title_fallback"] = te["title"], "TVDB", False
        if e.get("plot_fallback") and te.get("plot"):
            e["plot"], e["src_plot"], e["plot_fallback"] = te["plot"], "TVDB", False
        e.setdefault("tvdb_id", te.get("tvdb_id"))
        if not e.get("still_url") and te.get("still_url"):
            e["still_url"] = te["still_url"]


def _scrape_episodes(show_id, meta, force, progress, season_cands=(), mapping=None):
    eps = db.query("SELECT * FROM episodes WHERE show_id=? AND season IS NOT NULL ORDER BY season", (show_id,))
    folder = db.one("SELECT folder FROM shows WHERE id=?", (show_id,))["folder"]
    ok = failed = 0
    hints = db.one("SELECT hints FROM shows WHERE id=?", (show_id,)).get("hints") or {}
    tvdb_primary = config.get("tv_episode_source") == "tvdb" or bool(
        mapping and mapping.get("numbering") == "tvdb" and meta.get("tvdb_id")) or bool(
        hints.get("numbering") == "tvdb" and tvdb.enabled() and meta.get("tvdb_id"))   # Sonarr-Anime

    def target(e):
        """Datei-Nummerierung -> Quellen-Nummerierung (Anime-Zuordnung: Staffel + Versatz)."""
        nums = e["episodes"] or []
        if mapping and e["season"] == 1:
            return mapping["season"], [n + mapping["offset"] for n in nums]
        return e["season"], nums

    by_season = {}
    for e in eps:
        by_season.setdefault(target(e)[0], []).append(e)
    tv_eps = None
    cache = {}                                              # Trakt/fernsehserien pro Serie nur einmal
    for season_no, rows in sorted(by_season.items()):
        todo = [e for e in rows if force or e["status"] != "gescrapt" or not e["has_nfo"]]
        if not todo and not force:
            ok += len(rows)
            continue
        try:
            season = tmdb.season(meta["tmdb_id"], season_no)
        except tmdb.TMDBError as ex:
            if not (tvdb_primary and meta.get("tvdb_id")):
                for e in todo:
                    db.update("episodes", e["id"], status="fehler", message=f"Staffel {season_no}: {ex}")
                failed += len(todo)
                continue
            season = {"episodes": {}, "poster_cands": []}     # TVDB übernimmt
        if tv_eps is None:
            tv_eps = enrich.tvdb_episodes(meta, force_default=tvdb_primary and (
                mapping is not None or hints.get("numbering") == "tvdb"))
        if tv_eps:
            _merge_tvdb(season, season_no, tv_eps, tvdb_primary)
        enrich.fill_episodes(meta, season_no, season["episodes"], cache)   # Trakt, fernsehserien.de
        if config.get("download_season_posters") and not mapping:
            cands = list(season.get("poster_cands", [])) + [c for c in season_cands if c.get("season") == season_no]
            best = artwork.choose(cands, "poster")
            if best:
                _download(best["url"], paths.season_poster(folder, season_no), force)
        for e in todo:
            file_nums = e["episodes"] or []
            _, nums = target(e)
            found = []
            for fn, n in zip(file_nums, nums):
                if n in season["episodes"]:
                    # NFO bekommt die Nummer aus dem Dateinamen, Inhalte aus der zugeordneten Episode
                    found.append({**season["episodes"][n], "season": e["season"], "episode": fn})
            if not found:
                src = "TVDB" if tvdb_primary and tv_eps else "TMDB"
                db.update("episodes", e["id"], status="fehler",
                          message=f"S{season_no:02d}E{nums[0]:02d} nicht bei {src}" if nums
                          else "Episodennummer fehlt")
                failed += 1
                continue
            f = paths.episode_files(e["path"])
            try:
                nfo.write_episode(f["nfo"], meta, found)
                if config.get("download_episode_thumbs") and found[0].get("still_url"):
                    _download(found[0]["still_url"], f["thumb"], force)
            except OSError as ex:
                db.update("episodes", e["id"], status="fehler", message=file_error(ex, e["path"]))
                failed += 1
                continue
            title = " / ".join(x.get("title") or "" for x in found)
            db.update("episodes", e["id"], title=title, meta=found, status="gescrapt", message=None,
                      **{k: int(v) for k, v in paths.episode_status(e["path"]).items()})
            ok += 1
            if progress:
                progress(f"{meta['title']} S{e['season']:02d}E{file_nums[0]:02d} – {title}")
        ok += len(rows) - len(todo)
    return ok, failed
