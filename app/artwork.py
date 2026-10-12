"""Quellenübergreifende Artwork-Auswahl: erst Sprache, dann Quellen-Reihenfolge, dann Bewertung."""
from . import config

ALL_KINDS = ("poster", "fanart", "logo", "clearart", "discart", "banner", "landscape")
EXTRA_KINDS = ("clearart", "discart", "banner", "landscape")


def lang_prefs(kind):
    de = config.lang_short()
    fb = config.get("fallback_language").split("-")[0]
    if kind == "fanart":
        return [None, de, fb]          # Hintergründe ohne Text
    return [de, None, fb]              # Poster, Logos usw.: deutsch > textlos > englisch


def merge(*sources):
    out = {}
    for src in sources:
        for kind, cands in (src or {}).items():
            out.setdefault(kind, []).extend(cands)
    return out


def choose(cands, kind):
    if not cands:
        return None
    order = [s.strip() for s in config.get("artwork_order").split(",") if s.strip()]
    rank = {s: i for i, s in enumerate(order)}
    prefs = lang_prefs(kind)
    for lang in prefs + ["*"]:
        pool = [c for c in cands if lang == "*" or c.get("lang") == lang]
        if pool:
            pool.sort(key=lambda c: (rank.get(c["source"], 99), -float(c.get("score") or 0)))
            return pool[0]
    return None


def choose_all(cands_by_kind):
    """-> {"poster": {"url":…, "source":…, "lang":…}, …}"""
    out = {}
    for kind in ALL_KINDS:
        c = choose(cands_by_kind.get(kind, []), kind)
        if c:
            out[kind] = {"url": c["url"], "source": c["source"], "lang": c.get("lang")}
    return out
