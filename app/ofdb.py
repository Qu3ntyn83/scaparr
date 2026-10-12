"""OFDb (ofdb.de): FSK aus den deutschen Fassungen und deutsche Inhaltsangabe.

OFDb hat keine API. Gelesen werden nur Film-, Fassungs- und Inhaltsseiten, die laut robots.txt
erlaubt sind; die (gesperrte) Suche wird nicht benutzt – die OFDb-ID kommt von Wikidata.
Max. 1 Anfrage pro Sekunde.
"""
import html
import re
import time
import urllib.robotparser
from collections import Counter

from . import config, db
from .http import SourceError, request

BASE = "https://www.ofdb.de"
_robots = {"rp": None, "ts": 0}

FASSUNG_RE = re.compile(r'<a href="(https://www\.ofdb\.de/fassung/[^"]+)"[^>]*>'
                        r'(?:<span class="tooltipster" title="[^"]*">)?<b>([^<:]+):</b>\s*([^<]*)')
FSK_RE = re.compile(r"freigabe_(\d+|KJ)\.gif|>FSK (\d+)<")
PLOT_LINK_RE = re.compile(r'href="(https://www\.ofdb\.de/film/\d+,\d+,[^"]*/plot/)"')
PRIORITY = {"Kino": 0, "Blu-ray Disc": 1, "Ultra HD Blu-ray": 1, "4K Ultra HD Blu-ray": 1, "DVD": 2}


def enabled():
    return bool(config.get("ofdb_enabled"))


def _allowed(url):
    if _robots["rp"] is None or time.time() - _robots["ts"] > 86400:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = request("GET", BASE + "/robots.txt", min_interval=1.0)
            rp.parse(r.text.splitlines())
        except SourceError:
            rp.parse([])
        _robots.update(rp=rp, ts=time.time())
    return _robots["rp"].can_fetch("*", url)


def _page(url):
    if not _allowed(url):
        raise SourceError(f"OFDb: laut robots.txt nicht erlaubt: {url}")
    r = request("GET", url, min_interval=1.0, ok404=True)
    if r is None or r.status_code != 200:
        return None
    return r.text


def _german_block(page):
    """Nur der Abschnitt 'Deutschland' der Fassungsliste."""
    m = re.search(r'<p class="bluegrey-dark"><b>Deutschland</b></p>(.*?)(?:<p class="bluegrey-dark"><b>|<h5)',
                  page, re.S)
    return m.group(1) if m else ""


def _fsk_of(url):
    page = _page(url)
    if not page:
        return None
    i = page.find("<b>Freigabe:</b>")
    if i < 0:
        return None
    m = FSK_RE.search(page[i:i + 1500])      # nur der Freigabe-Block, nicht Shop-Angebote darunter
    if not m:
        return None                           # z. B. SPIO/JK oder ungeprüft
    age = m.group(1) or m.group(2)
    return "18" if age == "KJ" else age


def _clean(text):
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch(ofdb_id, want_fsk=True, want_plot=False):
    """-> {"fsk": "12", "plot": "...", "url": "..."}"""
    if not enabled() or not ofdb_id:
        return {}
    out = {}
    try:
        url = f"{BASE}/film/{int(ofdb_id)}/"
        page = _page(url)
        if not page:
            return {}
        out["url"] = url
        if want_fsk:
            rel = [(PRIORITY.get(t.strip(), 9), u, t.strip(), name)
                   for u, t, name in FASSUNG_RE.findall(_german_block(page))]
            rel.sort(key=lambda x: x[0])
            ages = []
            best_prio = None
            for prio, u, _, _ in rel[:6]:
                if best_prio is not None and prio > best_prio:
                    break                   # Kino gefunden -> DVD/Blu-ray nicht mehr nötig
                age = _fsk_of(u)
                if age is not None:
                    ages.append(age)
                    best_prio = prio
            if ages:
                cnt = Counter(ages).most_common()
                top = [a for a, n in cnt if n == cnt[0][1]]
                out["fsk"] = max(top, key=int)
        if want_plot:
            m = PLOT_LINK_RE.search(page)
            if m:
                p = _page(m.group(1))
                if p:
                    pm = re.search(r'<h4 class="mb15">(.*?)</h4>(.*?)<div class="review_meta', p, re.S)
                    if pm:
                        head = _clean(pm.group(1)).rstrip(".… ")
                        body = _clean(pm.group(2)).lstrip(".… ")
                        plot = (head + " " + body).strip() if body else head
                        out["plot"] = re.sub(r"\s+\n", "\n", plot)
    except SourceError as e:
        db.log("WARN", str(e))
    return out


def test():
    if not enabled():
        return {"ok": False, "msg": "OFDb ist deaktiviert"}
    r = fetch(901, want_fsk=True, want_plot=True)
    if not r.get("url"):
        return {"ok": False, "msg": "OFDb nicht erreichbar"}
    return {"ok": True, "msg": f"OFDb OK – Das Boot: FSK {r.get('fsk') or '–'}, "
                               f"Inhaltsangabe {'gefunden' if r.get('plot') else '–'}"}
