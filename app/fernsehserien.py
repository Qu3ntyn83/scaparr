"""fernsehserien.de: deutsche Episodentitel und -inhalte, wie sie im deutschen TV/Streaming liefen.

Keine API – gelesen werden Suche, Episodenführer und Staffelseiten, robots.txt wird zur Laufzeit geprüft,
max. 1 Anfrage pro Sekunde. Ergebnisse werden pro Serie nur einmal je Scrape-Lauf geladen.
"""
import html
import re
import time
import unicodedata
import urllib.parse
import urllib.robotparser

from . import config, db
from .http import SourceError, request

BASE = "https://www.fernsehserien.de"
_robots = {"rp": None, "ts": 0}

RESULT_RE = re.compile(r'<li class="[^"]*suchergebnisse-sendung[^"]*"><a[^>]*title="([^"]*)" href="(/[^"/]+)"'
                       r'.*?<dd>(.*?)</dd>', re.S)
SEASON_LINK_RE = re.compile(r'href="(?:/[^/"]+/)?(?:/?)(episodenguide/staffel-(\d+)/\d+)"')
EP_HREF_RE = re.compile(r'(?:href|content)="/[^/"]+/folgen/(\d+)x(\d+)-[^"]*"')
NAME_RE = re.compile(r'itemprop="?name"?>([^<]+)<')


def enabled():
    return bool(config.get("fernsehserien_enabled"))


def _allowed(url):
    if _robots["rp"] is None or time.time() - _robots["ts"] > 86400:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(request("GET", BASE + "/robots.txt", min_interval=1.0).text.splitlines())
        except SourceError:
            rp.parse([])
        _robots.update(rp=rp, ts=time.time())
    return _robots["rp"].can_fetch("Scraparr", url)


def _page(path):
    url = path if path.startswith("http") else BASE + path
    if not _allowed(url):
        raise SourceError(f"fernsehserien.de: laut robots.txt nicht erlaubt: {url}")
    r = request("GET", url, min_interval=1.0, ok404=True)
    if r is None or r.status_code != 200:
        return None
    return r.text


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").casefold()).replace("ß", "ss").replace("&", "und")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def find_slug(titles, year=None):
    """Serien-Slug über die Seitensuche; nur exakte Titeltreffer (deutsch oder original), Jahr ±1."""
    wanted = {_norm(t) for t in titles if t}
    for t in [t for t in titles if t][:2]:
        page = _page("/suche/" + urllib.parse.quote(t))
        if not page:
            continue
        for title, href, dd in RESULT_RE.findall(page):
            if _norm(html.unescape(title)) not in wanted:
                continue
            m = re.search(r"(\d{4})", dd)
            if year and m and abs(int(m.group(1)) - int(year)) > 1:
                continue
            return href.strip("/")
    return None


def _clean(text):
    text = re.sub(r"(?s)<ins\b.*?</ins>", "", text)
    text = re.sub(r"(?s)<script\b.*?</script>", "", text)
    text = re.sub(r'<span class=text-quelle>.*?</span>', "", text)
    text = re.sub(r"<span class=br></span>|<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _parse_episodes(page):
    """Alle Episoden einer Seite: {(staffel, nr): {"title", "plot"}}"""
    out = {}
    starts = [m.start() for m in re.finditer(r'itemprop="episode"', page)] + [len(page)]
    for a, b in zip(starts, starts[1:]):
        block = page[a:b]
        m = EP_HREF_RE.search(block) or EP_HREF_RE.search(page[max(0, a - 400):a])
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if key in out:
            continue
        name = NAME_RE.search(page[a:b])
        plot = ""
        i = block.find("episode-output-inhalt-inner>")
        if i >= 0:
            rest = block[i + len("episode-output-inhalt-inner>"):]
            rest = re.sub(r"(?s)<ins\b.*?</ins>", "", rest)
            plot = _clean(rest.split("</div>")[0])
        out[key] = {"title": html.unescape(name.group(1)).strip() if name else "", "plot": plot}
    return out


def episodes(slug, seasons):
    """Episoden der gewünschten Staffeln. Erst Episodenführer, dann (bei Bedarf) die Staffelseiten."""
    guide = _page(f"/{slug}/episodenguide")
    if not guide:
        return {}
    eps = _parse_episodes(guide)
    links = {int(n): path for path, n in SEASON_LINK_RE.findall(guide)}
    want_plots = config.get("fernsehserien_plots")
    for s in sorted(set(seasons)):
        have = [k for k in eps if k[0] == s]
        need_page = not have or (want_plots and not any(eps[k]["plot"] for k in have))
        if need_page and s in links:
            page = _page(f"/{slug}/{links[s]}")
            if page:
                for k, v in _parse_episodes(page).items():
                    if k not in eps or (v["plot"] and not eps[k]["plot"]):
                        eps[k] = {**eps.get(k, {}), **{x: y for x, y in v.items() if y}}
    return eps


def test():
    if not enabled():
        return {"ok": False, "msg": "fernsehserien.de ist deaktiviert"}
    try:
        slug = find_slug(["Dark"], 2017)
        if not slug:
            return {"ok": False, "msg": "Testserie nicht gefunden (Seitenaufbau geändert?)"}
        eps = episodes(slug, [1])
        e = eps.get((1, 3), {})
        return {"ok": bool(e), "msg": f"fernsehserien.de OK – Dark S01E03: {e.get('title', '–')}, "
                                      f"Inhalt {'gefunden' if e.get('plot') else '–'}"}
    except SourceError as e:
        return {"ok": False, "msg": str(e)}
