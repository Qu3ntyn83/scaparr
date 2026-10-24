"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  view: "movies", items: [], selected: new Set(), active: null, detail: null,
  sort: { key: "title", asc: true }, wasRunning: false, logMax: 0, bust: Date.now(),
};

async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch { /* leer */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = "toast"), 3500);
}

const SRV = () => S.srv || "Emby";
const img = (path, w = 0) => path ? `/api/image?path=${encodeURIComponent(path)}${w ? "&w=" + w : ""}&v=${S.bust}` : "";
const fskBadge = (f) => f !== null && f !== undefined && f !== "" ? `<span class="fsk fsk-${esc(f)}">${esc(f)}</span>` : `<span class="fsk fsk-none">–</span>`;
const dot = (v) => `<span class="ic ${v ? "y" : ""}"></span>`;
const isMobile = () => window.matchMedia("(max-width: 760px)").matches;

// Mobil: Detailansicht als eigene Seite, Zurück-Knopf und Zurück-Geste des Browsers schließen sie
function openDetailPage() {
  if (!isMobile() || document.body.classList.contains("detail-open")) return;
  document.body.classList.add("detail-open");
  history.pushState({ scraparrDetail: true }, "");
}
function closeDetailPage(fromPop = false) {
  if (!document.body.classList.contains("detail-open")) return;
  document.body.classList.remove("detail-open");
  if (!fromPop && history.state && history.state.scraparrDetail) history.back();
}
window.addEventListener("popstate", () => closeDetailPage(true));

const isLib = () => S.view === "movies" || S.view === "shows";
const kind = () => (S.view === "movies" ? "movie" : "tv");
const base = () => (S.view === "movies" ? "/api/movies" : "/api/shows");

// ------------------------------------------------------------ Navigation
function show(view) {
  closeDetailPage();
  S.view = view;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("#view-library").classList.toggle("hidden", !isLib());
  $("#view-log").classList.toggle("hidden", view !== "log");
  $("#view-settings").classList.toggle("hidden", view !== "settings");
  $("#view-sync").classList.toggle("hidden", view !== "sync");
  $("#view-sets").classList.toggle("hidden", view !== "sets");
  if (isLib()) {
    S.selected.clear(); S.active = null; S.detail = null;
    $(".c-ep").classList.toggle("hidden", view !== "shows");
    $(".c-theme").classList.toggle("hidden", view !== "shows");
    $("#btn-themes").classList.toggle("hidden", view !== "shows");
    $("#btn-rescrape").title = view === "shows" ? "Serie inkl. aller Episoden neu scrapen" : "Ausgewählte komplett neu scrapen, Artwork überschreiben";
    $("#detail").innerHTML = `<div class="placeholder">${view === "movies" ? "Film" : "Serie"} links auswählen</div>`;
    loadList();
  } else if (view === "log") { S.logMax = 0; $("#log").innerHTML = ""; loadLog(); }
  else if (view === "settings") loadSettings();
  else if (view === "sync") loadSync();
  else if (view === "sets") loadSets();
  try { localStorage.setItem("scraparr-view", view); } catch { /* egal */ }
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-goto]");
  if (g) { e.preventDefault(); show(g.dataset.goto); }
});

// ------------------------------------------------------------ Liste
async function loadList() {
  const v = S.view;
  try {
    const items = await api("GET", base());
    if (v !== S.view) return;
    S.items = items;
    renderList();
  } catch (e) { toast(e.message, true); }
}

function filtered() {
  const q = $("#filter-text").value.trim().toLowerCase();
  const st = $("#filter-status").value;
  let list = S.items.filter((i) => {
    if (q && !`${i.title} ${i.guess_title} ${i.original_title} ${i.path || i.folder}`.toLowerCase().includes(q)) return false;
    if (st === "incomplete") return !(i.has_nfo && i.has_poster && i.has_fanart);
    if (st && i.status !== st) return false;
    return true;
  });
  const { key, asc } = S.sort;
  list.sort((a, b) => {
    let x = key === "title" ? (a.title || a.guess_title || "") : a[key];
    let y = key === "title" ? (b.title || b.guess_title || "") : b[key];
    if (key === "fsk") { x = +x || -1; y = +y || -1; }
    const r = typeof x === "string" ? x.localeCompare(y, "de", { sensitivity: "base" }) : (x ?? 0) - (y ?? 0);
    return asc ? r : -r;
  });
  return list;
}

function renderList() {
  const list = filtered();
  const tb = $("#list tbody");
  const shows = S.view === "shows";
  tb.innerHTML = list.map((i) => {
    const title = i.title || i.guess_title;
    const sub = i.status !== "gescrapt" ? `<span class="sub">${esc(i.message || (i.path || i.folder))}</span>` : "";
    const st = i.status !== "gescrapt" ? `<span class="st ${esc(i.status)}">${esc(i.status === "manuell" ? "prüfen" : i.status)}</span>` : "";
    return `<tr data-id="${i.id}" class="${S.active === i.id ? "active" : ""}">
      <td class="c-chk"><input type="checkbox" class="rowchk" ${S.selected.has(i.id) ? "checked" : ""}></td>
      <td class="c-title"><span class="t">${esc(title)}${st}</span>${sub}
        <span class="m-meta">${esc(i.year || i.guess_year || "")} ${fskBadge(i.fsk)} <span class="m-dots" title="NFO · Poster · Fanart · Trailer${shows ? " · Theme" : ""}">${dot(i.has_nfo)}${dot(i.has_poster)}${dot(i.has_fanart)}${dot(i.has_trailer)}${shows ? dot(i.has_theme) : ""}</span></span></td>
      <td class="c-year">${esc(i.year || i.guess_year || "")}</td>
      <td class="c-fsk">${fskBadge(i.fsk)}</td>
      <td class="c-ic">${dot(i.has_nfo)}</td><td class="c-ic">${dot(i.has_poster)}</td>
      <td class="c-ic">${dot(i.has_fanart)}</td><td class="c-ic">${dot(i.has_trailer)}</td>
      ${shows ? `<td class="c-ic">${dot(i.has_theme)}</td>` : ""}
      ${shows ? `<td class="c-ep">${i.ep_ok || 0}/${i.ep_total || 0}${i.ep_err ? ` <span style="color:var(--err)">!${i.ep_err}</span>` : ""}</td>` : ""}
    </tr>`;
  }).join("");
  const empty = $("#list-empty");
  empty.classList.toggle("hidden", list.length > 0);
  if (!list.length) empty.innerHTML = S.items.length ? "Keine Treffer für diesen Filter."
    : `Noch nichts eingelesen.<br><br><button class="btn" onclick="scan()">Bibliothek scannen</button>`;
  $("#cnt-" + S.view).textContent = S.items.length || "";
  updateSel();
  $$("#list th[data-sort]").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === S.sort.key);
    th.classList.toggle("asc", th.dataset.sort === S.sort.key && S.sort.asc);
  });
}

function updateSel() {
  const n = S.selected.size;
  $("#sel-count").textContent = n ? `${n} ausgewählt` : "Keine Auswahl";
  $("#btn-scrape").textContent = n ? `${n} scrapen` : "Alle offenen scrapen";
  $("#btn-rescrape").disabled = !n;
  $("#btn-rename").disabled = !n;
  $("#btn-merge").classList.toggle("hidden", S.view !== "movies");
  $("#btn-merge").disabled = n < 2;
  const vis = filtered();
  $("#sel-all").checked = vis.length > 0 && vis.every((i) => S.selected.has(i.id));
}

$("#list tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  const id = +tr.dataset.id;
  if (e.target.classList.contains("rowchk")) {
    e.target.checked ? S.selected.add(id) : S.selected.delete(id);
    updateSel(); return;
  }
  openDetail(id);
});
$("#sel-all").addEventListener("change", (e) => {
  filtered().forEach((i) => (e.target.checked ? S.selected.add(i.id) : S.selected.delete(i.id)));
  renderList();
});
$$("#list th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  const k = th.dataset.sort;
  S.sort = { key: k, asc: S.sort.key === k ? !S.sort.asc : k === "title" };
  renderList();
}));
$("#filter-text").addEventListener("input", renderList);
$("#filter-status").addEventListener("change", renderList);
document.addEventListener("keydown", (e) => {
  if (!isLib() || document.querySelector("dialog[open]") || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const list = filtered(); if (!list.length) return;
  e.preventDefault();
  let idx = list.findIndex((i) => i.id === S.active);
  idx = Math.max(0, Math.min(list.length - 1, idx + (e.key === "ArrowDown" ? 1 : -1)));
  openDetail(list[idx].id);
  $(`#list tr[data-id="${list[idx].id}"]`)?.scrollIntoView({ block: "nearest" });
});

// ------------------------------------------------------------ Detail
async function openDetail(id) {
  S.active = id;
  openDetailPage();
  $$("#list tbody tr").forEach((tr) => tr.classList.toggle("active", +tr.dataset.id === id));
  try {
    const d = await api("GET", `${base()}/${id}`);
    if (S.active !== id) return;
    S.detail = d;
    renderDetail(d);
    if (isMobile()) $("#detail").scrollTop = 0;
  } catch (e) { toast(e.message, true); }
}

function renderDetail(d) {
  const m = d.meta || {};
  const movie = S.view === "movies";
  const title = m.title || d.title || d.guess_title;
  const runtime = m.runtime ? `${Math.floor(m.runtime / 60) ? Math.floor(m.runtime / 60) + " h " : ""}${m.runtime % 60} min` : "";
  const files = d.files || {};
  const facts = [
    m.year || d.guess_year ? `<span>${esc(m.year || d.guess_year)}</span>` : "",
    runtime ? `<span>${runtime}</span>` : "",
    m.fsk ? fskBadge(m.fsk) : "",
    ...ratingChips(m),
    !movie && m.status ? `<span>${esc(m.status)}</span>` : "",
    m.tmdb_id ? `<a href="https://www.themoviedb.org/${movie ? "movie" : "tv"}/${m.tmdb_id}?language=de-DE" target="_blank" rel="noopener">TMDB</a>` : "",
    m.imdb_id ? `<a href="https://www.imdb.com/title/${esc(m.imdb_id)}/" target="_blank" rel="noopener">IMDb</a>` : "",
  ].filter(Boolean).join("");
  const msg = d.message ? `<div class="msg ${d.status === "fehler" ? "err" : ""}">${esc(d.message)}</div>` : "";
  const people = [
    m.directors?.length ? `<dt>Regie</dt><dd>${esc(m.directors.join(", "))}</dd>` : "",
    m.writers?.length ? `<dt>Drehbuch</dt><dd>${esc(m.writers.join(", "))}</dd>` : "",
    m.studios?.length ? `<dt>${movie ? "Studio" : "Sender"}</dt><dd>${esc(m.studios.join(", "))}</dd>` : "",
    m.set ? `<dt>Filmreihe</dt><dd>${esc(m.set.name)}</dd>` : "",
    m.trailer ? `<dt>Trailer</dt><dd><a href="${esc(m.trailer.url)}" target="_blank" rel="noopener">${esc(m.trailer.name || "YouTube")}</a> (${esc(m.trailer.lang)})</dd>` : (m.tmdb_id ? `<dt>Trailer</dt><dd>kein deutscher Trailer bei TMDB</dd>` : ""),
    sourcesRow(m),
    `<dt>${movie ? "Datei" : "Ordner"}</dt><dd class="mono">${esc(movie ? d.path : d.folder)}</dd>`,
  ].join("");
  const cast = (m.cast || []).slice(0, 15).map((a) => `<div class="actor"><div class="ph" style="${a.thumb ? `background-image:url('${esc(a.thumb.replace("/h632/", "/w185/"))}')` : ""}"></div><b>${esc(a.name)}</b><span>${esc(a.role)}</span></div>`).join("");

  $("#detail").innerHTML = `
    <button class="back" data-act="back">‹ ${movie ? "Filme" : "Serien"}</button>
    <div class="hero">
      <div class="hero-bg" style="${d.has_fanart ? `background-image:url('${img(files.fanart, 1280)}')` : ""}"></div>
      <div class="hero-in">
        <div class="poster" style="${d.has_poster ? `background-image:url('${img(files.poster, 400)}')` : ""}">${d.has_poster ? "" : "kein Poster"}</div>
        <div class="meta">
          <h1>${esc(title)}</h1>
          ${m.original_title && m.original_title !== title ? `<div class="orig">${esc(m.original_title)}</div>` : `<div class="orig"></div>`}
          <div class="facts">${facts}</div>
          ${m.genres?.length ? `<div class="genres">${m.genres.map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</div>` : ""}
          ${m.tagline ? `<div class="tagline">${esc(m.tagline)}</div>` : ""}
          <div class="plot">${esc(m.plot || (d.status === "gescrapt" ? "Keine Beschreibung verfügbar." : "Noch nicht gescrapt."))}</div>
          ${msg}
          <div class="actions">
            <button class="btn" data-act="search">🔍 Suchen &amp; Zuordnen</button>
            <button class="btn ghost" data-act="rescrape">⟳ Neu scrapen</button>
            <button class="btn ghost" data-act="edit" ${d.meta ? "" : "disabled"}>✎ Bearbeiten</button>
            <button class="btn ghost" data-act="rename" ${d.meta ? "" : "disabled"}>Umbenennen</button>
          </div>
        </div>
      </div>
    </div>
    <div class="section">
      <div class="files">
        <div class="file">${dot(d.has_nfo)} NFO</div><div class="file">${dot(d.has_poster)} Poster</div>
        <div class="file">${dot(d.has_fanart)} Fanart</div><div class="file">${dot(d.has_trailer)} Trailer</div>
        ${movie ? "" : `<div class="file">${dot(d.has_theme)} Titelmelodie</div>`}
      </div>
      ${movie && d.part_files?.length > 1 ? renderParts(d) : ""}
      ${d.meta ? renderArtStrip(d, files) : ""}
      <h4>Details</h4><dl class="kv">${people}</dl>
      ${cast ? `<h4>Besetzung</h4><div class="cast">${cast}</div>` : ""}
      ${movie ? "" : renderEpisodes(d)}
    </div>`;
}

function renderParts(d) {
  const rel = (p) => p.startsWith(d.folder + "/") ? p.slice(d.folder.length + 1) : p;
  const list = d.part_files.map((p, i) => `<li><span class="mono">${esc(rel(p))}</span>${d.stack_plan ? ` <span class="muted">→ ${esc(d.stack_plan[i])}</span>` : ""}</li>`).join("");
  return `<div class="parts ${d.stack_ok ? "ok" : "todo"}">
    <div class="parts-h"><b>Mehrteiliger Film · ${d.part_files.length} Teile</b>
      <span>${d.stack_ok ? `${SRV()} zeigt die Teile als einen Film.` : `So erkennt ${SRV()} die Teile noch nicht als einen Film.`}</span></div>
    <ol>${list}</ol>
    ${d.stack_ok ? "" : `<button class="btn small" data-act="stack">Für ${SRV()} zusammenführen</button>`}
  </div>`;
}

// ------------------------------------------------------------ Artwork-Auswahl
const ART_KINDS = [["poster", "Poster"], ["fanart", "Fanart"], ["logo", "Clearlogo"], ["landscape", "Thumb"], ["banner", "Banner"]];

function renderArtStrip(d, files) {
  const lock = d.art_lock || {};
  return `<h4>Bilder <small class="muted">– antippen zum Ändern</small></h4><div class="art-strip">${ART_KINDS.filter(([k]) => files[k]).map(([k, label]) => `
    <button class="art-tile art-${k}" data-act="art" data-kind="${k}" title="${label} ändern">
      <span class="art-img"><img src="${img(files[k], k === "poster" ? 300 : 480)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('i'),{textContent:'fehlt'}))"></span>
      <span class="art-lbl">${label}${lock[k] ? ' <span class="lock" title="Von Hand gewählt – bleibt beim Neu-Scrapen erhalten">●</span>' : ""}</span>
    </button>`).join("")}</div>`;
}

const AP = { kind: null, data: null };
function openArt(kind) {
  AP.kind = kind;
  $("#ap-tabs").innerHTML = ART_KINDS.filter(([k]) => (S.detail.files || {})[k]).map(([k, l]) => `<button data-kind="${k}" class="${k === kind ? "active" : ""}">${l}</button>`).join("");
  $("#ap-url").value = "";
  $("#dlg-art").showModal();
  loadArt();
}
async function loadArt() {
  const grid = $("#ap-grid");
  $$("#ap-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.kind === AP.kind));
  grid.className = `ap-grid ap-${AP.kind}`;
  grid.innerHTML = `<div class="hint">Lade Bilder von TMDB, fanart.tv und TVDB …</div>`;
  $("#ap-auto").classList.add("hidden");
  try {
    const r = await api("GET", `${base()}/${S.detail.id}/art/${AP.kind}`);
    AP.data = r;
    $("#ap-auto").classList.toggle("hidden", !r.locked);
    const src = { tmdb: "TMDB", fanart: "fanart.tv", tvdb: "TVDB" };
    grid.innerHTML = r.items.length ? r.items.map((c, i) => `
      <button class="ap-item ${c.current ? "cur" : ""}" data-i="${i}" title="${esc(c.url)}">
        <span class="ap-img"><img src="${esc(c.preview)}" alt="" loading="lazy"></span>
        <span class="ap-meta">${src[c.source] || esc(c.source)} · ${c.lang ? esc(c.lang.toUpperCase()) : "ohne Text"}${c.w ? ` · ${c.w}×${c.h}` : ""}${c.current ? " · aktuell" : ""}</span>
      </button>`).join("") : `<div class="hint">Keine ${esc(r.label)}-Bilder gefunden. ${AP.kind === "banner" || AP.kind === "landscape" ? "Banner und Thumbs liefert vor allem fanart.tv – API-Key in den Einstellungen prüfen." : ""} Alternativ unten eine Bild-URL eintragen.</div>`;
  } catch (e) { grid.innerHTML = `<div class="hint err">${esc(e.message)}</div>`; }
}
async function chooseArt(url) {
  const grid = $("#ap-grid");
  grid.classList.add("busy");
  try {
    await api("POST", `${base()}/${S.detail.id}/art/${AP.kind}`, { url });
    toast(`${AP.data?.label || "Bild"} gespeichert`);
    $("#dlg-art").close();
    S.bust = Date.now();
    openDetail(S.detail.id);
    loadList();
  } catch (e) { toast(e.message, true); }
  finally { grid.classList.remove("busy"); }
}
$("#ap-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-kind]");
  if (b) { AP.kind = b.dataset.kind; loadArt(); }
});
$("#ap-grid").addEventListener("click", (e) => {
  const b = e.target.closest(".ap-item");
  if (b && AP.data) chooseArt(AP.data.items[+b.dataset.i].url);
});
$("#ap-url-go").addEventListener("click", () => {
  const u = $("#ap-url").value.trim();
  if (!/^https?:\/\//.test(u)) return toast("Bitte eine http(s)-Bild-URL eintragen", true);
  chooseArt(u);
});
$("#ap-auto").addEventListener("click", async () => {
  try {
    await api("DELETE", `${base()}/${S.detail.id}/art/${AP.kind}`);
    toast("Sperre aufgehoben – beim nächsten Neu-Scrapen wird automatisch gewählt");
    loadArt();
    openDetail(S.detail.id);
  } catch (e) { toast(e.message, true); }
});

function ratingChips(m) {
  const r = m.ratings || (m.rating ? { tmdb: { value: m.rating } } : {});
  const out = [];
  if (r.imdb) out.push(`<span class="rating" title="IMDb (${(r.imdb.votes || 0).toLocaleString("de-DE")} Stimmen)">IMDb ${(+r.imdb.value).toFixed(1)}</span>`);
  if (r.tmdb) out.push(`<span class="rating" title="TMDB">★ ${(+r.tmdb.value).toFixed(1)}</span>`);
  if (r.rottentomatoes) out.push(`<span class="rating rt" title="Rotten Tomatoes">🍅 ${Math.round(r.rottentomatoes.value)} %</span>`);
  if (r.metacritic) out.push(`<span class="rating mc" title="Metacritic">MC ${Math.round(r.metacritic.value)}</span>`);
  return out;
}

function sourcesRow(m) {
  const s = m.sources;
  if (!s) return "";
  const art = m.art ? Object.entries(m.art).map(([k, v]) => `${{ poster: "Poster", fanart: "Fanart", logo: "Logo", clearart: "Clearart", discart: "Disc", banner: "Banner", landscape: "Landscape" }[k] || k}: ${{ tmdb: "TMDB", fanart: "fanart.tv", tvdb: "TVDB" }[v.source] || v.source}${v.lang ? " (" + v.lang + ")" : ""}`).join(", ") : "";
  const parts = [
    `Daten: ${esc(s.meta || "TMDB")}`, `Inhalt: ${esc(s.plot || "–")}`, `FSK: ${esc(s.fsk || "–")}`,
    s.ratings ? `Wertungen: ${esc(s.ratings)}` : "",
    s.anime ? "Anime (anime-lists)" : "",
    s.mapping ? `Zuordnung: ${esc(s.mapping)}` : "",
  ].filter(Boolean).join(" · ");
  const a = m.anime_ids || {};
  const animeLinks = [
    a.anidb ? `<a href="https://anidb.net/anime/${esc(a.anidb)}" target="_blank" rel="noopener">AniDB</a>` : "",
    a.anilist ? `<a href="https://anilist.co/anime/${esc(a.anilist)}" target="_blank" rel="noopener">AniList</a>` : "",
    a.mal ? `<a href="https://myanimelist.net/anime/${esc(a.mal)}" target="_blank" rel="noopener">MAL</a>` : "",
    a.anisearch ? `<a href="https://www.anisearch.de/anime/${esc(a.anisearch)}" target="_blank" rel="noopener">aniSearch</a>` : "",
  ];
  const links = [
    ...animeLinks,
    m.fs_slug ? `<a href="https://www.fernsehserien.de/${esc(m.fs_slug)}/episodenguide" target="_blank" rel="noopener">fernsehserien.de</a>` : "",
    m.ofdb_id ? `<a href="https://www.ofdb.de/film/${esc(m.ofdb_id)}/" target="_blank" rel="noopener">OFDb</a>` : "",
    m.tvdb_id ? `<a href="https://www.thetvdb.com/dereferrer/series/${esc(m.tvdb_id)}" target="_blank" rel="noopener">TVDB</a>` : "",
    m.wikidata_id ? `<a href="https://www.wikidata.org/wiki/${esc(m.wikidata_id)}" target="_blank" rel="noopener">Wikidata</a>` : "",
  ].filter(Boolean).join(" ");
  return `<dt>Quellen</dt><dd class="srcs">${parts}${art ? `<br>Artwork: ${esc(art)}` : ""}${links ? `<br>${links}` : ""}</dd>`;
}

function epSrc(e) {
  if (!Array.isArray(e.meta)) return "";
  const src = [...new Set(e.meta.flatMap((m) => [m.src_title, m.src_plot]).filter(Boolean))];
  return src.length ? ` · deutsch via ${esc(src.join(" + "))}` : "";
}

function fmtSize(n) {
  if (n == null) return "";
  return n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : n >= 1048576 ? `${Math.round(n / 1048576)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function renderEpisodes(d) {
  if (!d.episodes?.length) return `<h4>Episoden</h4><div class="hint">Keine Episoden gefunden.</div>`;
  const by = {};
  d.episodes.forEach((e) => (by[e.season ?? "?"] ||= []).push(e));
  const seasonName = (s) => (d.meta?.seasons || []).find((x) => x.season === +s)?.name || (s === "0" ? "Specials" : s === "?" ? "Nicht erkannt" : `Staffel ${s}`);
  return `<h4>Episoden</h4>` + Object.keys(by).sort((a, b) => (a === "?" ? 1e4 : +a) - (b === "?" ? 1e4 : +b)).map((s) => {
    const eps = by[s].sort((a, b) => (a.episodes?.[0] ?? 0) - (b.episodes?.[0] ?? 0));
    const ok = eps.filter((e) => e.status === "gescrapt").length;
    return `<div class="season"><div class="season-h"><b>${esc(seasonName(s))}</b><span>${ok}/${eps.length} gescrapt</span></div>
      ${eps.map((e) => {
        const no = e.episodes?.length ? `S${String(e.season).padStart(2, "0")}${e.episodes.map((n) => "E" + String(n).padStart(2, "0")).join("")}` : "??";
        const file = e.path.split("/").pop();
        const bad = !e.episodes?.length;
        const info = [fmtSize(e.size), e.mtime ? new Date(e.mtime * 1000).toLocaleDateString("de-DE") : "", e.size == null ? "Datei nicht erreichbar" : ""]
          .filter(Boolean).join(" · ");
        const dir = e.rel && e.rel.includes("/") ? e.rel.slice(0, e.rel.lastIndexOf("/")) : "";
        const dg = e.diagnosis;
        const read = dg && Object.keys(dg.gelesen).length ? Object.entries(dg.gelesen).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ") : "";
        return `<div class="ep ${bad ? "bad" : ""}" title="${esc(e.path)}">
          <div class="th" style="${e.has_thumb ? `background-image:url('${img(e.thumb, 240)}')` : ""}"></div>
          <div class="no">${no}</div>
          <div class="tt"><b>${esc(bad ? file : e.title || "—")}</b>
            ${bad ? "" : `<span>${esc(e.message || file)}${epSrc(e)}</span>`}
            ${bad ? `<span class="why">${esc(e.message || "Staffel/Episode nicht erkannt")}${dg?.fehlt?.length ? " – fehlt: " + esc(dg.fehlt.join(", ")) : ""}</span>
              ${dir ? `<span>Ordner: ${esc(dir)}</span>` : ""}
              ${info ? `<span>${esc(info)}</span>` : ""}
              ${read ? `<span>Erkannt: ${read}</span>` : `<span>Aus dem Dateinamen wurde nichts gelesen</span>`}` : (info ? `<span class="dim">${esc(info)}</span>` : "")}
          </div>
          <div>${dot(e.has_nfo)}</div></div>`;
      }).join("")}</div>`;
  }).join("");
}

$("#detail").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || !S.detail) return;
  const a = b.dataset.act;
  if (a === "back") return closeDetailPage();
  if (a === "search") openSearch();
  if (a === "art") openArt(b.dataset.kind);
  if (a === "stack") runJob("/api/movies/stack", { ids: [S.detail.id] });
  if (a === "edit") openEdit();
  if (a === "rescrape") runJob(`${base()}/scrape`, { ids: [S.detail.id], force: true });
  if (a === "rename") openRename([S.detail.id]);
});

// ------------------------------------------------------------ Aktionen
async function runJob(url, body) {
  try {
    const r = await api("POST", url, body);
    toast(r.duplicate ? `Wartet bereits: ${r.started}`
      : r.queued ? `In Warteschlange (läuft nach „${r.after}“): ${r.started}` : `Gestartet: ${r.started}`);
    pollStatus();
  } catch (e) { toast(e.message, true); }
}
function scan() { runJob(`/api/scan/${S.view === "shows" ? "tv" : S.view === "movies" ? "movies" : "all"}`); }
window.scan = scan;
$("#btn-scan").addEventListener("click", scan);
$("#btn-auto").addEventListener("click", () => runJob("/api/auto/run"));
$("#job-cancel").addEventListener("click", () => api("POST", "/api/job/cancel").then(() => toast("Wird nach dem aktuellen Titel abgebrochen …")));
$("#btn-scrape").addEventListener("click", () => runJob(`${base()}/scrape`, { ids: [...S.selected], force: false }));
$("#btn-rescrape").addEventListener("click", () => runJob(`${base()}/scrape`, { ids: [...S.selected], force: true }));
$("#btn-rename").addEventListener("click", () => openRename([...S.selected]));
$("#btn-merge").addEventListener("click", async () => {
  const ids = [...S.selected];
  try {
    const r = await api("POST", "/api/movies/merge/preview", { ids });
    const name = (p) => p.split("/").pop();
    const ok = confirm(`„${r.title || "Film"}“ aus ${r.parts.length} Dateien zu EINEM Film verschmelzen?\n\n` +
      r.parts.map((p, i) => `Teil ${i + 1}: ${name(p)}\n   → ${name(r.targets[i])}`).join("\n") +
      `\n\nDie Dateien werden umbenannt/verschoben, die übrigen Einträge entfernt. ` +
      `Gedacht für CD1/CD2 – nicht für zwei Fassungen desselben Films.`);
    if (ok) { await runJob("/api/movies/merge", { ids }); S.selected.clear(); }
  } catch (e) { toast(e.message, true); }
});
$("#btn-themes").addEventListener("click", () => runJob("/api/shows/themes", { ids: [...S.selected], force: false }));

// ------------------------------------------------------------ Suche
function openSearch() {
  const d = S.detail;
  $("#s-query").value = d.title || d.guess_title || "";
  $("#s-year").value = d.year || d.guess_year || "";
  $("#s-file").textContent = S.view === "movies" ? d.path : d.folder;
  $("#s-results").innerHTML = "";
  $("#dlg-search").showModal();
  doSearch();
}
async function doSearch() {
  const q = $("#s-query").value.trim();
  if (!q) return;
  const box = $("#s-results");
  box.innerHTML = `<div class="hint">Suche bei TMDB …</div>`;
  try {
    const y = $("#s-year").value;
    const res = await api("GET", `/api/search?type=${kind()}&q=${encodeURIComponent(q)}${y ? "&year=" + y : ""}`);
    box.innerHTML = res.length ? res.map((r) => `
      <div class="res" data-id="${r.tmdb_id}">
        <div class="p" style="${r.poster ? `background-image:url('${esc(r.poster)}')` : ""}"></div>
        <div><b>${esc(r.title)}</b><span class="y">${esc(r.year || "")}</span>
          ${r.original_title && r.original_title !== r.title ? `<div class="o">${esc(r.original_title)}</div>` : ""}
          <p>${esc(r.overview || "Keine deutsche Beschreibung.")}</p></div>
      </div>`).join("") : `<div class="hint">Keine Treffer. Tipp: Originaltitel, Jahr weglassen oder TMDB-/IMDb-ID eingeben.</div>`;
  } catch (e) { box.innerHTML = `<div class="hint err">${esc(e.message)}</div>`; }
}
$("#s-go").addEventListener("click", doSearch);
[$("#s-query"), $("#s-year")].forEach((i) => i.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); }));
$("#s-results").addEventListener("click", (e) => {
  const r = e.target.closest(".res");
  if (!r) return;
  $("#dlg-search").close();
  runJob(`${base()}/${S.detail.id}/match`, { tmdb_id: +r.dataset.id });
});

// ------------------------------------------------------------ Bearbeiten
function openEdit() {
  const m = S.detail.meta || {};
  const f = $("#edit-form");
  f.title.value = m.title || ""; f.original_title.value = m.original_title || "";
  f.sorttitle.value = m.sorttitle || ""; f.year.value = m.year || "";
  f.fsk.value = m.fsk || ""; f.tagline.value = m.tagline || "";
  f.genres.value = (m.genres || []).join(", "); f.plot.value = m.plot || "";
  $("#dlg-edit").showModal();
}
$("#edit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    title: f.title.value.trim(), original_title: f.original_title.value.trim(),
    sorttitle: f.sorttitle.value.trim(), year: f.year.value ? +f.year.value : null,
    fsk: f.fsk.value, tagline: f.tagline.value.trim(), plot: f.plot.value.trim(),
    genres: f.genres.value.split(",").map((g) => g.trim()).filter(Boolean),
  };
  try {
    const d = await api("POST", `${base()}/${S.detail.id}/edit`, body);
    $("#dlg-edit").close();
    S.detail = d; renderDetail(d); loadList();
    toast("Gespeichert, NFO aktualisiert");
  } catch (err) { toast(err.message, true); }
});

// ------------------------------------------------------------ Umbenennen
let rnIds = [];
async function openRename(ids) {
  rnIds = ids;
  const body = S.view === "movies" ? { movie_ids: ids } : { show_ids: ids };
  try {
    const plans = await api("POST", "/api/rename/preview", body);
    $("#rn-list").innerHTML = plans.length ? plans.map((p) => `<div class="rn"><div class="o">${esc(p.old)}</div><div class="n${p.conflict ? " bad" : ""}">${esc(p.new)}${p.parts?.length > 1 ? ` <span class="muted">(+ ${p.parts.length - 1} weitere${p.parts.length > 2 ? "" : "r"} Teil)</span>` : ""}${p.conflict ? ` — ${esc(p.conflict)}, wird übersprungen` : ""}</div></div>`).join("")
      : `<div class="hint">Alles schon korrekt benannt (oder noch nicht gescrapt).</div>`;
    const okN = plans.filter((p) => !p.conflict).length;
    $("#rn-info").textContent = plans.length ? `${okN} von ${plans.length} Datei(en) – NFO, Bilder, Untertitel und Trailer wandern mit.` : "";
    $("#rn-apply").disabled = !okN;
    $("#dlg-rename").showModal();
  } catch (e) { toast(e.message, true); }
}
$("#rn-apply").addEventListener("click", () => {
  $("#dlg-rename").close();
  runJob("/api/rename/apply", S.view === "movies" ? { movie_ids: rnIds } : { show_ids: rnIds });
});

// ------------------------------------------------------------ Status-Polling
async function pollStatus() {
  try {
    const s = await api("GET", "/api/status");
    const j = s.job;
    if ((s.server || "Emby") !== SRV()) { S.srv = s.server || "Emby"; $$(".srv").forEach((e) => (e.textContent = S.srv)); }
    $("#no-key").classList.toggle("hidden", s.api_key);
    $("#job-idle").classList.toggle("hidden", j.running);
    $("#job-run").classList.toggle("hidden", !j.running);
    if (j.running) {
      $("#job-name").textContent = j.name;
      $("#job-current").textContent = (j.current || "") + ((j.queue || []).length ? `  · +${j.queue.length} in Warteschlange` : "");
      const bar = $("#job-bar").parentElement;
      bar.classList.toggle("indet", !j.total);
      $("#job-bar").style.width = j.total ? `${Math.round((j.done / j.total) * 100)}%` : "0";
    } else {
      const open = (s.movies.neu || 0) + (s.movies.manuell || 0) + (s.shows.neu || 0) + (s.shows.manuell || 0);
      const next = j.next_auto ? `Nächster Auto-Lauf ${new Date(j.next_auto).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}` : "Auto-Lauf aus";
      $("#job-next").textContent = `Bereit · ${open ? open + " offen · " : ""}${j.pending ? j.pending + " Webhook-Aufgabe(n) wartend · " : ""}${next}`;
    }
    if (S.wasRunning && !j.running) {          // Job fertig -> alles neu laden
      S.bust = Date.now();
      if (isLib()) { await loadList(); if (S.active) openDetail(S.active); }
      if (S.view === "log") loadLog();
      if (S.view === "sync") loadSync();
      if (S.view === "sets") loadSets();
    } else if (j.running && isLib() && Math.random() < 0.25) loadList();
    S.wasRunning = j.running;
  } catch { /* Server kurz weg */ }
}
setInterval(pollStatus, 2000);

// ------------------------------------------------------------ Protokoll
async function loadLog() {
  try {
    const rows = await api("GET", `/api/log?after=${S.logMax}`);
    if (!rows.length) return;
    S.logMax = Math.max(S.logMax, ...rows.map((r) => r.id));
    const lvl = $("#log-level").value;
    const html = rows.filter((r) => !lvl || r.level === lvl).map((r) =>
      `<div><span class="ts">${esc(r.ts.replace("T", " "))}</span><span class="${r.level}">${r.level}</span><span>${esc(r.msg)}</span></div>`).join("");
    $("#log").insertAdjacentHTML("afterbegin", html);
  } catch (e) { toast(e.message, true); }
}
setInterval(() => { if (S.view === "log") loadLog(); }, 3000);
$("#log-level").addEventListener("change", () => { S.logMax = 0; $("#log").innerHTML = ""; loadLog(); });
$("#log-clear").addEventListener("click", async () => { await api("DELETE", "/api/log"); S.logMax = 0; $("#log").innerHTML = ""; });

// ------------------------------------------------------------ Einstellungen
async function loadSettings() {
  api("GET", "/api/hook/info").then((h) => { $("#hook-radarr").textContent = h.radarr; $("#hook-sonarr").textContent = h.sonarr; }).catch(() => {});
  const s = await api("GET", "/api/settings");
  const f = $("#settings");
  for (const [k, v] of Object.entries(s)) {
    const el = f.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
}
$("#settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, body = {};
  for (const el of f.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  try { await api("POST", "/api/settings", body); toast("Einstellungen gespeichert"); loadSettings(); pollStatus(); }
  catch (err) { toast(err.message, true); }
});
async function testConn(which) {
  const out = $(`#test-${which}-res`);
  out.className = "hint"; out.textContent = "Teste … (vorher speichern!)";
  try {
    const r = await api("POST", `/api/test/${which}`);
    out.className = "hint " + (r.ok ? "ok" : "err"); out.textContent = r.msg;
  } catch (e) { out.className = "hint err"; out.textContent = e.message; }
}
$("#test-tmdb").addEventListener("click", () => testConn("tmdb"));
$$("[data-test]").forEach((b) => b.addEventListener("click", async () => {
  const src = b.dataset.test, out = $(`[data-res="${src}"]`);
  out.className = "hint"; out.textContent = "Teste … (vorher speichern!)";
  try {
    const r = await api("POST", `/api/test/${src}`);
    out.className = "hint " + (r.ok ? "ok" : "err"); out.textContent = r.msg;
  } catch (e) { out.className = "hint err"; out.textContent = e.message; }
}));
$("#test-emby").addEventListener("click", () => testConn("emby"));

// ------------------------------------------------------------ Start
let startView = "movies";
try { startView = localStorage.getItem("scraparr-view") || localStorage.getItem("ms-view") || "movies"; } catch { /* egal */ }
show(startView);
pollStatus();

// ------------------------------------------------------------ Abgleich
const SYS = {
  emby: { get name() { return SRV(); }, get desc() { return `Wie zeigt ${SRV()} deine Titel an? Fehlende, abweichende und nicht eingelesene Einträge.`; },
          get labels() { return { emby_titel: `Titel in ${SRV()}`, abweichungen: "Abweichungen", fehlt_in_emby: `fehlen in ${SRV()}`, unbekannt: "außerhalb der Bibliotheken" }; },
          get action() { return `In ${SRV()} neu einlesen / melden`; } },
  radarr: { name: "Radarr", desc: "TMDB-IDs aus Radarr – keine Raterei bei der Zuordnung.",
            labels: { radarr_filme: "Filme mit Datei", ids_uebernommen: "IDs übernommen", abweichungen: "ID-Abweichungen", fehlt_im_scraper: "fehlen hier", nur_im_scraper: "nur hier" },
            action: "Radarr-ID übernehmen & neu scrapen" },
  sonarr: { name: "Sonarr", desc: "TVDB/TMDB-IDs und Episodennummern aus Sonarr.",
            labels: { sonarr_serien: "Serien", ids_uebernommen: "IDs übernommen", episoden_korrigiert: "Episoden korrigiert", abweichungen: "Abweichungen", fehlt_im_scraper: "fehlen hier", nur_im_scraper: "nur hier" },
            action: "Sonarr-Werte übernehmen & neu scrapen" },
};
const TYPE_LABEL = { abweichung: "Abweichung", fehlt: "fehlt", unbekannt: "unbekannt", offen: "offen", episode: "Episode" };
let SYNC = {};

async function loadSync() {
  try { SYNC = await api("GET", "/api/sync"); } catch (e) { toast(e.message, true); return; }
  const total = Object.values(SYNC).reduce((n, r) => n + (r.issues || []).filter((i) => i.action).length, 0);
  $("#cnt-sync").textContent = total || "";
  $("#sync-cards").innerHTML = Object.entries(SYS).map(([key, def]) => {
    const r = SYNC[key] || {};
    const head = `<div class="sync-head"><h3>${def.name}</h3>
      <span class="hint">${r.ts ? "Letzter Abgleich: " + new Date(r.ts).toLocaleString("de-DE") : def.desc}</span>
      ${r.configured ? `<button class="btn small" data-sync="${key}">⟳ Abgleichen</button>` : `<a href="#" data-goto="settings">In den Einstellungen einrichten →</a>`}</div>`;
    if (!r.ts) return `<div class="sync-card">${head}</div>`;
    const chips = Object.entries(def.labels).map(([k, l]) => r.summary && k in r.summary
      ? `<span class="chip ${["abweichungen", "fehlt_in_emby", "fehlt_im_scraper", "unbekannt"].includes(k) && r.summary[k] ? "warn" : ""}"><b>${r.summary[k]}</b> ${l}</span>` : "").join("");
    const issues = r.issues || [];
    const rows = issues.map((i, idx) => `<tr class="${i.id ? "clickable" : ""}" data-kind="${i.kind}" data-id="${i.id || ""}">
        <td>${i.action ? `<input type="checkbox" class="sync-chk" data-sys="${key}" data-idx="${idx}">` : ""}</td>
        <td><span class="tbadge ${esc(i.type)}">${esc(TYPE_LABEL[i.type] || i.type)}</span></td>
        <td>${esc(i.title || i.arr_title || i.emby_title || "")}${i.arr_title && i.title && i.arr_title !== i.title ? `<br><small class="hint">${def.name}: ${esc(i.arr_title)}</small>` : ""}${i.emby_title && i.title && i.emby_title !== i.title ? `<br><small class="hint">${SRV()}: ${esc(i.emby_title)}</small>` : ""}</td>
        <td>${esc(i.text)}</td><td class="p">${esc(i.path || "")}</td></tr>`).join("");
    const actionable = issues.some((i) => i.action);
    return `<div class="sync-card">${head}<div class="chips">${chips}</div>
      ${issues.length ? `<div class="sync-scroll"><table class="sync-table"><thead><tr><th><input type="checkbox" class="sync-all" data-sys="${key}"></th><th>Art</th><th>Titel</th><th>Hinweis</th><th>Pfad</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${actionable ? `<div class="sync-foot"><span class="hint">Auswählen, dann:</span><button class="btn small" data-apply="${key}">${def.action}</button></div>` : ""}`
      : `<div class="sync-empty">✔ Alles stimmt überein.</div>`}</div>`;
  }).join("");
}

$("#sync-cards").addEventListener("click", async (e) => {
  const run = e.target.closest("[data-sync]");
  if (run) return runJob(`/api/sync/${run.dataset.sync}`);
  const ap = e.target.closest("[data-apply]");
  if (ap) {
    const sys = ap.dataset.apply;
    const idx = $$(`.sync-chk[data-sys="${sys}"]:checked`).map((c) => +c.dataset.idx);
    if (!idx.length) return toast("Nichts ausgewählt", true);
    return runJob(`/api/sync/${sys}/apply`, { indexes: idx }).then(loadSync);
  }
  if (e.target.matches("input")) return;
  const tr = e.target.closest("tr.clickable");
  if (tr && tr.dataset.id) {
    show(tr.dataset.kind === "movie" ? "movies" : "shows");
    setTimeout(() => openDetail(+tr.dataset.id), 400);
  }
});
$("#sync-cards").addEventListener("change", (e) => {
  if (e.target.classList.contains("sync-all"))
    $$(`.sync-chk[data-sys="${e.target.dataset.sys}"]`).forEach((c) => (c.checked = e.target.checked));
});
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  const txt = $("#" + b.dataset.copy).textContent;
  (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => toast("Kopiert"), () => toast(txt));
});

// ------------------------------------------------------------ Kollektionen
let SETS = { items: [] };
async function loadSets() {
  try { SETS = await api("GET", "/api/collections"); } catch (e) { toast(e.message, true); return; }
  $("#cnt-sets").textContent = SETS.items.length || "";
  renderSets();
}
function renderSets() {
  const q = $("#sets-filter").value.trim().toLowerCase(), mode = $("#sets-mode").value;
  const list = SETS.items.filter((c) => {
    if (q && !c.name.toLowerCase().includes(q)) return false;
    if (mode === "incomplete") return c.missing > 0;
    if (mode === "complete") return c.missing === 0;
    if (mode === "noemby") return !c.emby_id && c.owned >= SETS.min;
    return true;
  });
  const total = SETS.items.length, incomplete = SETS.items.filter((c) => c.missing > 0).length;
  $("#sets-info").textContent = total
    ? `${total} ${total === 1 ? "Reihe" : "Reihen"} in deiner Bibliothek · ${incomplete} unvollständig · in ${SRV()} ab ${SETS.min} eigenen Filmen${SETS.emby ? "" : " (Medienserver nicht eingerichtet)"}`
    : "Noch keine Filmreihen – sie erscheinen, sobald Filme einer Reihe gescrapt sind.";
  $("#sets-grid").innerHTML = list.map((c) => {
    const pct = c.total ? Math.round((c.owned / c.total) * 100) : 0;
    const emby = c.emby_id ? `<span class="set-badge ok" title="In ${SRV()} angelegt">${SRV()}</span>`
      : (c.owned >= SETS.min && SETS.emby ? `<span class="set-badge" title="Wird beim nächsten Abgleich angelegt">${SRV()} offen</span>` : "");
    return `<button class="set-card" data-set="${c.tmdb_id}">
      <div class="set-poster" style="${c.poster ? `background-image:url('${esc(c.poster.replace("/original/", "/w342/"))}')` : ""}">${emby}</div>
      <div class="set-name">${esc(c.name)}</div>
      <div class="set-count"><span>${c.owned} von ${c.total}</span>${c.missing ? `<span class="miss">${c.missing} fehlen</span>` : `<span class="okc">komplett</span>`}</div>
      <div class="set-bar"><i style="width:${pct}%"></i></div>
    </button>`;
  }).join("") || (total ? `<div class="hint">Keine Treffer für diesen Filter.</div>` : "");
}
$("#sets-filter").addEventListener("input", renderSets);
$("#sets-mode").addEventListener("change", renderSets);
$("#sets-refresh").addEventListener("click", () => runJob("/api/collections/refresh", { ids: [] }));
$("#sets-emby").addEventListener("click", () => runJob("/api/collections/emby", { ids: [] }));
$("#sets-grid").addEventListener("click", (e) => {
  const b = e.target.closest("[data-set]");
  if (b) openSet(+b.dataset.set);
});

async function openSet(cid) {
  let c;
  try { c = await api("GET", `/api/collections/${cid}`); } catch (e) { return toast(e.message, true); }
  $("#set-title").textContent = c.name || "Kollektion";
  const parts = c.parts || [];
  $("#set-body").innerHTML = `
    <div class="set-hero" style="${c.fanart ? `background-image:url('${esc(c.fanart.replace("/original/", "/w1280/"))}')` : ""}">
      <div class="set-hero-in">
        <div class="poster" style="${c.poster ? `background-image:url('${esc(c.poster.replace("/original/", "/w342/"))}')` : ""}"></div>
        <div>
          <div class="facts"><span>${c.owned} von ${parts.length} Filmen vorhanden</span>
            <a href="https://www.themoviedb.org/collection/${c.tmdb_id}?language=de-DE" target="_blank" rel="noopener">TMDB</a>
            ${c.emby_synced ? `<span>${SRV()}: ${new Date(c.emby_synced).toLocaleString("de-DE")}</span>` : ""}</div>
          <div class="plot">${esc(c.overview || "Keine Beschreibung.")}</div>
          <div class="actions">
            <button class="btn ghost" data-setact="refresh" data-id="${c.tmdb_id}">⟳ Neu von TMDB</button>
            ${SETS.emby ? `<button class="btn" data-setact="emby" data-id="${c.tmdb_id}">Nach ${SRV()} übertragen</button>` : ""}
          </div>
        </div>
      </div>
    </div>
    <div class="set-parts">${parts.map((p) => `
      <div class="set-part ${p.owned ? "own" : "miss"}" ${p.movie_id ? `data-movie="${p.movie_id}"` : ""}>
        <div class="pp" style="${p.poster ? `background-image:url('${esc(p.poster)}')` : ""}"></div>
        <div class="pt"><b>${esc(p.title)}</b><span>${esc(p.year || "")}</span></div>
        <span class="tbadge ${p.owned ? "ok" : "fehlt"}">${p.owned ? "vorhanden" : "fehlt"}</span>
      </div>`).join("")}</div>`;
  $("#dlg-set").showModal();
}
$("#set-body").addEventListener("click", (e) => {
  const a = e.target.closest("[data-setact]");
  if (a) { $("#dlg-set").close(); return runJob(`/api/collections/${a.dataset.setact}`, { ids: [+a.dataset.id] }); }
  const m = e.target.closest("[data-movie]");
  if (m) { $("#dlg-set").close(); show("movies"); setTimeout(() => openDetail(+m.dataset.movie), 400); }
});
