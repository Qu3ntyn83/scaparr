"""Dateinamenskonventionen für NFO/Artwork/Trailer (Emby- und Kodi-kompatibel)."""
import glob
import os


def stem(path):
    return os.path.splitext(path)[0]


def movie_files(path, single_folder):
    folder, base = os.path.dirname(path), stem(path)
    if single_folder:
        return {
            "nfo": base + ".nfo",
            "poster": os.path.join(folder, "poster.jpg"),
            "fanart": os.path.join(folder, "fanart.jpg"),
            "logo": os.path.join(folder, "clearlogo.png"),
            "clearart": os.path.join(folder, "clearart.png"),
            "discart": os.path.join(folder, "disc.png"),
            "banner": os.path.join(folder, "banner.jpg"),
            "landscape": os.path.join(folder, "landscape.jpg"),
            "trailer": base + "-trailer.mp4",
        }
    return {
        "nfo": base + ".nfo",
        "poster": base + "-poster.jpg",
        "fanart": base + "-fanart.jpg",
        "logo": base + "-clearlogo.png",
        "clearart": base + "-clearart.png",
        "discart": base + "-disc.png",
        "banner": base + "-banner.jpg",
        "landscape": base + "-landscape.jpg",
        "trailer": base + "-trailer.mp4",
    }


def show_files(folder):
    return {
        "nfo": os.path.join(folder, "tvshow.nfo"),
        "poster": os.path.join(folder, "poster.jpg"),
        "fanart": os.path.join(folder, "fanart.jpg"),
        "logo": os.path.join(folder, "clearlogo.png"),
        "clearart": os.path.join(folder, "clearart.png"),
        "banner": os.path.join(folder, "banner.jpg"),
        "landscape": os.path.join(folder, "landscape.jpg"),
        "trailer": os.path.join(folder, "trailers", "trailer.mp4"),
    }


def season_poster(folder, season):
    name = "season-specials-poster.jpg" if season == 0 else f"season{season:02d}-poster.jpg"
    return os.path.join(folder, name)


def episode_files(path):
    base = stem(path)
    return {"nfo": base + ".nfo", "thumb": base + "-thumb.jpg"}


def movie_status(path, single_folder):
    f = movie_files(path, single_folder)
    folder, base = os.path.dirname(path), stem(path)
    ex = os.path.exists
    has_trailer = any(glob.glob(glob.escape(base) + "-trailer.*"))
    if single_folder:
        has_trailer = has_trailer or os.path.isdir(os.path.join(folder, "trailers")) \
            and bool(os.listdir(os.path.join(folder, "trailers")))
    return {
        "has_nfo": ex(f["nfo"]) or (single_folder and ex(os.path.join(folder, "movie.nfo"))),
        "has_poster": ex(f["poster"]) or (single_folder and ex(os.path.join(folder, "folder.jpg")))
                      or ex(base + "-poster.jpg"),
        "has_fanart": ex(f["fanart"]) or (single_folder and ex(os.path.join(folder, "backdrop.jpg")))
                      or ex(base + "-fanart.jpg"),
        "has_trailer": has_trailer,
    }


def existing_movie_nfo(path, single_folder):
    f = movie_files(path, single_folder)["nfo"]
    if os.path.exists(f):
        return f
    alt = os.path.join(os.path.dirname(path), "movie.nfo")
    return alt if single_folder and os.path.exists(alt) else None


def theme_file(folder):
    return os.path.join(folder, "theme.mp3")


def has_theme(folder):
    """theme.mp3 (oder andere Endung) im Serienordner bzw. Emby-Ordner theme-music/ mit Inhalt."""
    try:
        if any(f.lower().startswith("theme.") for f in os.listdir(folder)):
            return True
        tm = os.path.join(folder, "theme-music")
        return os.path.isdir(tm) and bool(os.listdir(tm))
    except OSError:
        return False


def show_status(folder):
    f = show_files(folder)
    tdir = os.path.join(folder, "trailers")
    return {
        "has_nfo": os.path.exists(f["nfo"]),
        "has_poster": os.path.exists(f["poster"]) or os.path.exists(os.path.join(folder, "folder.jpg")),
        "has_fanart": os.path.exists(f["fanart"]) or os.path.exists(os.path.join(folder, "backdrop.jpg")),
        "has_trailer": os.path.isdir(tdir) and bool(os.listdir(tdir)),
    }


def episode_status(path):
    f = episode_files(path)
    return {"has_nfo": os.path.exists(f["nfo"]), "has_thumb": os.path.exists(f["thumb"])}
