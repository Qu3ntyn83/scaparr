"""SQLite-Persistenz: Filme, Serien, Episoden, Einstellungen, Protokoll."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "scraparr.db")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,          -- Videodatei
    folder TEXT NOT NULL,
    single_folder INTEGER DEFAULT 1,    -- Film liegt allein im Ordner
    guess_title TEXT, guess_year INTEGER,
    tmdb_id INTEGER, imdb_id TEXT,
    title TEXT, original_title TEXT, year INTEGER, fsk TEXT,
    meta TEXT,                          -- komplette gescrapte Daten (JSON)
    status TEXT DEFAULT 'neu',          -- neu | gescrapt | manuell | fehler
    message TEXT,
    has_nfo INTEGER DEFAULT 0, has_poster INTEGER DEFAULT 0,
    has_fanart INTEGER DEFAULT 0, has_trailer INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    added TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT UNIQUE NOT NULL,
    guess_title TEXT, guess_year INTEGER,
    tmdb_id INTEGER, imdb_id TEXT, tvdb_id INTEGER,
    title TEXT, original_title TEXT, year INTEGER, fsk TEXT,
    meta TEXT,
    status TEXT DEFAULT 'neu', message TEXT,
    has_nfo INTEGER DEFAULT 0, has_poster INTEGER DEFAULT 0,
    has_fanart INTEGER DEFAULT 0, has_trailer INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    added TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    path TEXT UNIQUE NOT NULL,
    season INTEGER, episodes TEXT,      -- JSON-Liste der Episodennummern (Multi-Episode)
    title TEXT, meta TEXT,
    status TEXT DEFAULT 'neu', message TEXT,
    has_nfo INTEGER DEFAULT 0, has_thumb INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    added TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, level TEXT, msg TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_show ON episodes(show_id);
CREATE TABLE IF NOT EXISTS sync (system TEXT PRIMARY KEY, ts TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS collections (
    tmdb_id INTEGER PRIMARY KEY,
    name TEXT, overview TEXT,
    poster TEXT, fanart TEXT,            -- gewählte Bild-URLs
    parts TEXT,                          -- JSON: alle Teile laut TMDB
    emby_id TEXT, emby_synced TEXT,
    updated TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def _migrate_old_name():
    """Umbenennung MediaScraper -> Scraparr: vorhandene Datenbank übernehmen."""
    old = os.path.join(DATA_DIR, "mediascraper.db")
    if os.path.exists(old) and not os.path.exists(DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(old + suffix):
                os.replace(old + suffix, DB_PATH + suffix)


def init():
    os.makedirs(DATA_DIR, exist_ok=True)
    _migrate_old_name()
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # Migrationen für ältere Datenbanken
        for table, col, typ in (("movies", "hints", "TEXT"), ("shows", "hints", "TEXT"),
                                ("shows", "mapping", "TEXT"), ("shows", "has_theme", "INTEGER DEFAULT 0"),
                                ("movies", "art_lock", "TEXT"), ("shows", "art_lock", "TEXT"),
                                ("movies", "part_files", "TEXT")):
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("meta", "episodes", "hints", "mapping", "parts", "art_lock", "part_files"):
        if k in d and d[k]:
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def query(sql, args=()):
    with conn() as c:
        return [row(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    with conn() as c:
        return row(c.execute(sql, args).fetchone())


def execute(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def update(table, rid, **fields):
    if not fields:
        return
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v, ensure_ascii=False)
    fields["updated"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), rid))


def log(level: str, msg: str):
    print(f"[{level}] {msg}", flush=True)
    try:
        execute("INSERT INTO log (ts, level, msg) VALUES (?,?,?)", (now(), level, msg))
        execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 2000 FROM log)")
    except sqlite3.Error:
        pass
