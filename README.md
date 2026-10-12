# Scraparr

**Scraparr** ist ein Scraper für Filme und Serien **komplett auf Deutsch** von TMDB
und legt alles so ab, dass **Emby oder Jellyfin** (und Kodi) es lokal einliest.

## Was er macht

| | Filme | Serien |
|---|---|---|
| NFO (Titel, Handlung, Genres, Tagline, Besetzung, Regie, Studio, Filmreihe) | `<film>.nfo` | `tvshow.nfo` + `<episode>.nfo` |
| FSK-Freigabe (DE) | `<mpaa>FSK-12</mpaa>` | ✔ (auch in jeder Episode) |
| Poster (deutsch > textlos > englisch) | `poster.jpg` bzw. `<film>-poster.jpg` | `poster.jpg`, `season01-poster.jpg` |
| Fanart (textlos bevorzugt) | `fanart.jpg` bzw. `<film>-fanart.jpg` | `fanart.jpg` |
| Clearlogo (deutsch bevorzugt) | `clearlogo.png` | `clearlogo.png` |
| Episodenbild | – | `<episode>-thumb.jpg` |
| Deutscher Trailer (YouTube via yt-dlp) | `<film>-trailer.mp4` | `trailers/trailer.mp4` |

## Quellen

| Quelle | Key | Liefert |
|---|---|---|
| **TMDB** | ja (kostenlos) | Grunddaten, deutsche Texte, Besetzung, Poster/Fanart/Logos, Trailer-Auswahl, FSK |
| **TheTVDB** | ja (v4-Key, ggf. PIN) | deutsche Episodentitel, wo TMDB nur „Folge 3“ hat; DVD-/absolute Reihenfolge; Serien-Artwork |
| **fanart.tv** | ja (Projekt-Key, optional persönlicher) | Logos, Clearart, Disc, Banner, Landscape, Staffelposter |
| **OMDb** | ja (Free: 1.000 Abfragen/Tag) | IMDb-Wertung + Stimmen, Rotten Tomatoes, Metacritic |
| **OFDb** | nein | FSK der deutschen Fassungen (Kino > Blu-ray > DVD), deutsche Inhaltsangaben |
| **Wikidata** | nein | FSK und die OFDb-ID zum Film |

| **Trakt** | ja (Client-ID einer kostenlosen API-App) | deutsche Übersetzungen für Filme, Serien und **einzelne Episoden** |
| **fernsehserien.de** | nein | deutsche Episodentitel und -inhalte, wie sie im deutschen TV/Streaming liefen |
| **anime-lists** (Fribb) | nein | Anime-Zuordnung AniDB/AniList/MAL/Kitsu/aniSearch ↔ TMDB/TVDB inkl. Staffel und Episodenversatz |

Ohne Key ist eine Quelle einfach inaktiv. Die Detailansicht zeigt pro Titel, woher Inhalt, FSK,
Wertungen und jedes Bild stammen.

**Wie die Daten zusammengeführt werden**

* **Inhalt:** TMDB deutsch → (Film) OFDb-Inhaltsangabe / (Serie) TVDB-Übersetzung → TMDB englisch.
* **Episoden:** TMDB ist Hauptquelle; fehlende deutsche Titel/Texte (oder Platzhalter wie „Folge 3“) werden der
  Reihe nach aus **TVDB → Trakt → fernsehserien.de** ergänzt, erst danach Englisch. Die Detailansicht zeigt pro
  Episode, woher der deutsche Text stammt. Alternativ TVDB als
  Hauptquelle mit Reihenfolge *Ausstrahlung*, *DVD* oder *Absolut* (Anime) – Dateien müssen dann nach dieser
  Nummerierung benannt sein.
* **FSK:** Reihenfolge einstellbar, Standard *OFDb → TMDB → Wikidata*. OFDb liest die Kinofassung, sonst
  Blu-ray/DVD; bei mehreren Freigaben gewinnt die häufigste (bei Gleichstand die höhere).
* **Wertungen:** alle in die NFO (`<ratings>`), Hauptwertung wahlweise IMDb oder TMDB, Rotten Tomatoes
  zusätzlich als `<criticrating>` (Emby zeigt sie als Kritikerwertung).
* **Artwork:** Sprache schlägt Quelle – ein deutsches Poster von fanart.tv gewinnt gegen ein englisches von TMDB.
* **Bilder von Hand wählen:** In der Detailansicht unter *Bilder* Poster, Fanart, Clearlogo, Thumb oder Banner antippen –
  Scraparr zeigt alle Kandidaten von TMDB, fanart.tv und TVDB (deutsch zuerst) oder übernimmt eine eigene Bild-URL.
  Die Wahl wird gesperrt (● am Bild) und beim Neu-Scrapen nicht überschrieben; *Automatisch wählen* hebt das auf.
  Bei gleicher Sprache entscheidet die eingestellte Reihenfolge, dann die Bewertung. Hintergründe bevorzugt textlos.

**Anime:** Die Fribb-Liste wird einmal pro Woche von GitHub geladen (~7 MB, lokal in `data/`), pro Titel gibt es keine
Abfrage. Zwei Fälle:

* *Ganze Serie in einem Ordner* (`Attack on Titan/Staffel 1 …`): Anime wird automatisch über die TMDB-/TVDB-ID erkannt,
  die NFO bekommt zusätzlich AniDB-, AniList-, MAL-, Kitsu- und aniSearch-IDs als `<uniqueid>`.
* *AniDB-Stil – jede Staffel ein eigener Ordner* mit Folgen ab 1: ID in den Ordnernamen schreiben, z. B.
  `Shingeki no Kyojin Season 2 [anidb-10944]` (auch `[anilist-…]`, `[mal-…]`, `[kitsu-…]`, `[anisearch-…]`, `[tvdbid-…]`).
  Dann wird der Ordner TMDB-Staffel 2 (inkl. Episodenversatz bei Teil 2/Cour 2) zugeordnet, Titel/Poster der Staffel
  verwendet, die NFOs behalten aber die Nummern aus den Dateinamen – Emby zeigt den Ordner als eigene Serie
  „Attack on Titan – Staffel 2“.

**Zu fernsehserien.de:** keine API – gelesen werden Suche, Episodenführer und Staffelseiten, `robots.txt` wird zur
Laufzeit geprüft, max. 1 Anfrage/Sekunde, pro Serie und Lauf nur einmal. Die Serie wird nur bei exaktem Titel
(deutsch oder original) und passendem Startjahr (±1) übernommen. Achtung: fernsehserien.de zählt Episoden nach
deutscher Ausstrahlung – weicht die Nummerierung von TMDB ab (selten, z. B. zusammengelegte Doppelfolgen), kann ein Titel
verrutschen. Die Nutzungsbedingungen der Seite solltest du selbst prüfen; abschaltbar in den Einstellungen.

**Zu OFDb:** OFDb hat keine API. Gelesen werden nur Film-, Fassungs- und Inhaltsseiten, die laut
`robots.txt` erlaubt sind (wird zur Laufzeit geprüft), höchstens 1 Anfrage/Sekunde. Die OFDb-Suche ist für
Bots gesperrt und wird nicht benutzt – die Zuordnung Film → OFDb-ID kommt von Wikidata. Filme ohne
Wikidata-Eintrag mit OFDb-ID bekommen deshalb keine OFDb-Daten.

**Zu Wikidata:** Die Abfragen laufen über die normale Wikidata-API (1/Sekunde). Bei einer Drosselung (HTTP 429)
pausiert die Quelle 10 Minuten, statt weiter anzufragen.

## Mehrteilige Filme (2 CDs)

Teile eines Films werden als **ein** Film geführt und gemeinsam gescrapt, z. B.
`Film-cd1.avi`/`Film-cd2.avi`, `Film.1999.German.CD1.XviD.avi`, `Film (Disc 1).mkv` oder Unterordner
`Film (1999)/CD1/…` und `CD2/…`. NFO und Bilder landen als `movie.nfo`, `poster.jpg` … im Filmordner,
der Trailer unter `trailers/`.

Emby und Jellyfin fassen Teile nur zusammen, wenn sie `<Name>-cd1.ext`, `<Name>-cd2.ext` heißen und im eigenen
Filmordner liegen. Passt das nicht, zeigt die Detailansicht die geplanten Namen und den Knopf
**Für Emby zusammenführen**: Die Teile werden umbenannt (Untertitel wandern mit), aus `CD1`/`CD2` in den
Filmordner geholt bzw. aus einem Sammelordner in einen eigenen Ordner verschoben, und der Medienserver liest den
Film neu ein. Das normale *Umbenennen* berücksichtigt Mehrteiler ebenfalls.

## Ordnerstruktur Serien

Serien dürfen direkt in der Bibliothek liegen (`serien/Dark/Staffel 1/…`) oder in Kategorie-/Genre-Ordnern
beliebiger Tiefe (`serien/11 - Drama/Dark/…`). Ein Ordner gilt als Serie, wenn er Staffelordner (Season/Staffel/S01,
Specials), eine `tvshow.nfo`, ein Jahr bzw. eine ID im Namen oder direkt Videodateien ohne weitere Unterordner mit Videos
enthält; sonst wird er als Kategorie behandelt und tiefer durchsucht. Lose Dateien direkt in einer Kategorie werden
übersprungen (Hinweis im Protokoll). Sammelordner wie `!Unsortiert!` unter *Einstellungen → Bibliotheken → ignorieren*
eintragen, sonst werden sie als eine Serie behandelt.

## Sonstiges

* Liegt der Film allein in seinem Ordner, heißen die Bilder `poster.jpg`/`fanart.jpg`/`clearart.png`/`disc.png`,
  sonst `<film>-poster.jpg` usw.
* Vorhandene NFOs (z. B. von tMM) werden beim ersten Scan ausgelesen – die TMDB/IMDb-ID wird übernommen,
  es gibt also keine Fehlzuordnungen beim Umstieg.
* IDs im Namen werden erkannt: `Film (2001) [tmdbid-120]`, `tt0120737`.

## Automatischer Lauf

Alle *n* Minuten: Bibliotheken scannen → **neue** Titel scrapen → (optional) umbenennen → Emby/Jellyfin aktualisieren.

Automatisch übernommen werden nur **eindeutige** Treffer (Titel stimmt exakt, Jahr ±1). Alles andere landet
mit Status **„prüfen“** in der Liste – dort per *Suchen & Zuordnen* (Titel, TMDB-ID oder IMDb-ID) festlegen.
Neue Episoden einer bekannten Serie werden automatisch nachgescrapt.

**TV-Produktionen und Mediathek-Mitschnitte:** Sender, Mediathek-Kennungen und Sendedaten am Namensende
(`… NDR.de`, `… ZDF Mediathek 2023-05-14`, `[ARD]`) werden vor der Suche entfernt, ebenso Teilangaben wie
`Teil 2`, `(2/3)`, `(2-2)`. Findet TMDB nichts, sucht Scraparr weiter: mit römischer Ziffer (`Rocky 2` → `Rocky II`),
ohne Teilnummer (`Die Pilgerin 2` → `Die Pilgerin`) und mit Haupt- bzw. Untertitel einzeln
(`Gigant des Nordens - Der Hamburger Hafen`). Treffer über gekürzte Titel werden nur bei TV-Filmen und
Dokumentationen automatisch übernommen, sonst landen sie zur Prüfung in der Liste. Gibt es den Titel bei TMDB nur
als Serie bzw. Miniserie, steht das als Hinweis am Eintrag.

## Installation (Docker)

```bash
# auf dem Docker-Host
git clone https://github.com/Qu3ntyn83/scraparr.git /opt/scraparr
cd /opt/scraparr
cp docker-compose.example.yml docker-compose.yml
nano docker-compose.yml        # Medienpfade, PUID/PGID, Emby-/Jellyfin-URL anpassen
mkdir -p data
docker compose up -d --build
```

Die eigene `docker-compose.yml` und der Ordner `data/` (Datenbank, Bild-Cache) sind von Git ausgenommen,
Updates überschreiben sie also nicht.

### Update

```bash
cd /opt/scraparr
git pull
docker compose up -d --build
```

yt-dlp veraltet schnell: Schlägt der Trailer-Download fehl, einmal `docker compose build --no-cache` ausführen.

Web-UI: `http://<host>:8585`

1. **Einstellungen** → TMDB-API-Key eintragen (Pflicht); TVDB, fanart.tv und OMDb optional, jeweils mit *Testen* prüfen (themoviedb.org → Konto → Einstellungen → API; v3-Key
   oder das lange v4-„Lesezugriffs-Token“ – beides geht) → Speichern → *Verbindung testen*.
2. Medienserver: Emby- oder Jellyfin-URL + API-Key eintragen, testen (der Servertyp wird erkannt).
3. Oben **Scannen**, dann **Alle offenen scrapen** – oder einfach den Auto-Lauf machen lassen.

### Wichtig: Pfade und Rechte

* Der Container schreibt NFOs und Bilder **neben die Videodateien** – die Freigabe muss also
  schreibbar gemountet sein. `PUID`/`PGID` im Compose auf den User setzen, dem die Dateien gehören
  (bei Synology meist UID 1026, GID 100 – mit `id <user>` auf der DiskStation prüfen).
* Am einfachsten: gleiche Mount-Ziele wie im Emby-Container (`/media/filme`, `/media/serien`).
  Nötig ist das aber nicht – Emby liest die NFOs relativ zur Videodatei.
* Optionaler Login für die Web-UI: `WEB_USER` / `WEB_PASS` im Compose setzen.

## Emby einrichten (einmalig je Bibliothek)

Bibliothek bearbeiten:

* **Bevorzugte Sprache für Metadaten:** Deutsch · **Land:** Deutschland
* **Metadaten-Leser:** `Nfo` aktivieren und ganz nach oben
* **Metadaten-Downloader / Bild-Downloader:** TheMovieDb usw. **deaktivieren** (oder ganz nach unten),
  sonst überschreibt Emby die deutschen Daten teilweise wieder
* **Metadaten-Speicher (Saver) `Nfo`:** **aus** – sonst schreibt Emby in unsere NFOs zurück
* *Bildmaterial in Medienordnern speichern*: egal, die Bilder liegen schon dort

Danach einmal *Alle Metadaten ersetzen* auf die Bibliothek, damit Emby die neuen NFOs/Bilder übernimmt.

## Jellyfin statt Emby

Scraparr spricht beide Server über dieselbe Einstellung *Einstellungen → Medienserver*. Der Typ wird automatisch
erkannt (oder fest auf *Emby*/*Jellyfin* gestellt); Abgleich, gezieltes Neu-Einlesen, Kollektionen und Bild-Upload
funktionieren gleich. API-Schlüssel in Jellyfin: *Dashboard → Erweitert → API-Schlüssel → +*.

Jellyfin-Bibliothek (*Dashboard → Bibliotheken → Bibliothek verwalten*):

* **Bevorzugte Sprache:** Deutsch · **Land:** Deutschland
* **Metadaten-Leser:** `Nfo` aktiv und ganz oben
* **Metadaten-Downloader / Bildabrufer:** TheMovieDb, TheTVDB, OMDb usw. **deaktivieren**
* **Metadaten-Speicher `Nfo`:** **aus**
* Unter *Bibliothek → Filme* „Filme automatisch zu Sammlungen hinzufügen“ ausschalten, wenn Scraparr die
  Kollektionen pflegen soll

Danach *Bibliothek scannen → Alle Metadaten ersetzen* (Bilder ersetzen anhaken).

**FSK:** Standardformat ist `FSK-12`. Zeigt Emby die Freigabe nicht korrekt an, in den Einstellungen
auf `DE-{age}` umstellen und die betroffenen Titel neu scrapen.

## Titelmelodien für Serien

Beim Scrapen einer Serie lädt Scraparr die Titelmelodie als `theme.mp3` in den Serienordner (Quelle: Plex,
über die TVDB-ID). Emby spielt sie beim Öffnen der Serie ab, sofern im Emby-Client „Titelmusik abspielen“ an ist.
Vorhandene `theme.*`-Dateien oder ein `theme-music/`-Ordner werden nie überschrieben. Für bereits gescrapte Serien:
Reiter **Serien → ♪ Themes** (ausgewählte oder alle ohne Theme). Nicht jede Serie hat ein Theme bei Plex – vor allem
deutsche Produktionen und Anime fehlen oft. Abschaltbar unter *Einstellungen → Artwork*.

## Kollektionen (Filmreihen)

Reiter **Kollektionen**: alle Filmreihen, zu denen du Filme hast, mit deutschem Namen, Beschreibung und Poster von
TMDB, „2 von 4 vorhanden“ und der Liste der fehlenden Teile (unveröffentlichte Teile werden ignoriert).

* **NFO:** Jeder Film bekommt `<set>` mit deutschem Namen, Beschreibung und TMDB-Collection-ID.
* **Emby:** Ab der eingestellten Mindestanzahl eigener Filme (Standard 2) legt Scraparr die Kollektion in Emby an
  bzw. ergänzt fehlende Filme, setzt deutschen Namen, Beschreibung und TMDB-ID (gesperrt, damit Emby sie nicht
  überschreibt) und lädt Poster und Hintergrund hoch. Bestehende Emby-Kollektionen werden über die TMDB-ID oder den
  Namen wiedergefunden, es entstehen keine Dubletten.
* **Automatisch:** nach jedem Film-Scrape für die betroffenen Reihen und am Ende jedes Auto-Laufs. Ganz neue Filme
  landen beim nächsten Lauf in der Kollektion, sobald Emby sie eingelesen hat.
* **Optional:** Bilder zusätzlich als „Movie Set“-Ordner ablegen (`<Ordner>/<Name>/poster.jpg`, für Kodi/tMM).

Tipp: In Emby unter *Bibliothek → Filme* die eingebaute Option „Filme automatisch zu Sammlungen gruppieren“
ausschalten, sonst legt Emby parallel eigene (englische) Sammlungen an.

## Abgleich mit Emby/Jellyfin, Radarr und Sonarr

Neuer Reiter **Abgleich**. Einrichtung unter *Einstellungen → Medienserver* bzw. *Radarr & Sonarr* (URL + API-Key).

**Pfadzuordnung:** Sehen Emby/Radarr/Sonarr die Dateien unter anderen Pfaden als Scraparr (typisch bei
mehreren Containern), je Dienst eine Zeile `Pfad dort => Pfad hier` eintragen, z. B. `/movies => /media/filme`.
Einträge, die „außerhalb der Bibliotheken“ oder „fehlen hier“ landen, deuten fast immer auf eine fehlende Zuordnung hin.

| Dienst | Was abgeglichen wird | Automatisch | Per Auswahl |
|---|---|---|---|
| **Radarr** | TMDB-ID je Filmdatei | Offene/unklare Titel bekommen Radarrs TMDB-ID und werden gescrapt | Abweichende IDs übernehmen |
| **Sonarr** | TMDB-/TVDB-ID je Serie, Episodennummern | IDs für offene Serien; nicht erkannte Episoden bekommen Sonarrs Nummer; Anime-Serien zählen nach TVDB | Abweichende IDs/Episodennummern übernehmen |
| **Emby** | Anzeige in Emby: Titel, TMDB-ID, Freigabe, Poster; fehlende Titel | – | Titel in Emby gezielt neu einlesen, fehlende melden |

**Emby aktualisieren:** Nach jedem Job werden nur die geänderten Titel in Emby neu eingelesen (Metadaten + Bilder
ersetzen), neue Titel werden Emby gemeldet – kein kompletter Bibliotheksscan mehr. Umschaltbar auf „ganze Bibliothek“.

**Webhooks (empfohlen):** Die Adressen stehen in den Einstellungen (mit Token). In Radarr und Sonarr unter
*Settings → Connect → + → Webhook* eintragen, Ereignisse **On File Import**, **On File Upgrade** und **On Rename**.
Dann gilt:

* *Import:* nur der betroffene Ordner wird eingelesen und sofort mit der ID aus Radarr/Sonarr gescrapt –
  Episodennummern kommen direkt von Sonarr. Mehrere Webhooks innerhalb der Sammelzeit (Standard 30 s) werden
  zusammen verarbeitet, ein Staffelpaket ist also ein Durchgang.
* *Upgrade:* NFO, Bilder, Untertitel und Trailer der alten Datei werden auf die neue umgehängt.
* *Rename:* Begleitdateien werden mit umbenannt, Emby bekommt den neuen Pfad.

**Wichtig – Metadaten in Radarr/Sonarr ausschalten:** *Settings → Metadata → Kodi (XBMC)/Emby (Legacy)* deaktivieren,
sonst überschreiben Radarr/Sonarr beim Import die deutschen NFOs und Bilder wieder. Eine zusätzliche
„Emby“-Verbindung unter *Connect* ist nicht mehr nötig – Scraparr informiert Emby selbst, nachdem die NFOs fertig sind.

## Umbenennen

Optional, mit Vorschau. Muster in den Einstellungen, Standard:

* Ordner/Datei: `{title} ({year})` → `Der Herr der Ringe - Die Gefährten (2001)/Der Herr der Ringe - Die Gefährten (2001).mkv`
* Episoden: `{showtitle} - {eps} - {title}` → `Dark - S01E01 - Geheimnisse.mkv`

NFO, Bilder, Untertitel (`.de.srt`, `.ger.forced.srt` …) und Trailer werden mitverschoben. Konflikte
(Ziel existiert) werden in der Vorschau rot markiert und übersprungen.

## Grenzen

* Disc-Strukturen (`BDMV`, `VIDEO_TS`) werden übersprungen.
* Anime mit absoluter Nummerierung (ohne SxxEyy) wird nicht erkannt.
* YouTube blockt yt-dlp gelegentlich – dann bleibt der Trailer als Link in der NFO (Emby zeigt ihn als
  Online-Trailer). `docker compose build --pull` aktualisiert yt-dlp.

## Dateien

```
app/main.py      Web-API (FastAPI) + Bild-Thumbnails
app/tmdb.py      TMDB-Client: Sprache, Fallback, FSK, Bild-/Trailer-Auswahl
app/scanner.py   Bibliotheken einlesen, vorhandene NFOs übernehmen
app/parser.py    Dateinamen-Erkennung (guessit + eigene Regeln)
app/scraper.py   Matching, NFO/Artwork/Trailer schreiben
app/nfo.py       Kodi/Emby-NFO-Format
app/renamer.py   Umbenennen mit Begleitdateien
app/jobs.py      Hintergrundjobs, Auto-Lauf, Emby-Refresh
app/static/      Webfrontend (ohne Build-Schritt)
data/            SQLite-DB + Thumbnail-Cache (Volume)
```

## Lizenz

MIT – siehe [LICENSE](LICENSE). Daten von TMDB, TheTVDB, fanart.tv, OMDb, OFDb, Wikidata, Trakt und
fernsehserien.de unterliegen den Nutzungsbedingungen der jeweiligen Anbieter; für TMDB, TVDB, fanart.tv, OMDb
und Trakt wird ein eigener API-Key benötigt.
