let LOCALE = "fr-FR";      // langue des dates et nombres (fixee par i18n.js)
// Fonctions communes. Aucune donnee fictive : tout vient du serveur.
const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString(LOCALE);
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opt); } catch (e) { return { ok: false, status: 0, data: { error: "Serveur injoignable" } }; }
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (r.status === 401 && !/\/api\/(login|signup|me|ingest)/.test(path)) location.href = "/login";
  return { ok: r.ok, status: r.status, data };
}
async function requireUser(role) {
  const r = await api("/api/me");
  if (!r.ok) { location.href = "/login"; return null; }
  if (role && r.data.role !== role) { location.href = r.data.role === "steg" ? "/steg" : "/app"; return null; }
  if (window.KW_LANG && r.data.role === "client" && r.data.profile && r.data.profile.lang && r.data.profile.lang !== window.KW_LANG) {      // la langue enregistree dans le profil fait foi
    try { localStorage.setItem("kw-lang", r.data.profile.lang); } catch (e) {} location.reload(); return null;
  }
  return r.data;
}
async function logout() { await api("/api/logout", {}); location.href = "/"; }

const _shown = new WeakMap();
function countUp(el, to, { dur = 700, dec = 0, suffix = "" } = {}) {
  const from = _shown.get(el) ?? 0; _shown.set(el, to);
  const f = (v) => Number(v).toLocaleString(LOCALE, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suffix;
  if (from === to) { el.textContent = f(to); return; }
  const t0 = performance.now();
  const step = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.textContent = f(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

function toast(title, text = "", level = "") {
  let box = $("toasts"); if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  while (box.children.length >= 3) box.firstChild.remove();
  const d = document.createElement("div"); d.className = "toast " + level; d.innerHTML = `<b>${esc(title)}</b><span>${esc(text)}</span>`;
  box.appendChild(d); setTimeout(() => { d.style.transition = "opacity .4s"; d.style.opacity = 0; setTimeout(() => d.remove(), 400); }, 5200);
}

// ---------- Graphiques ----------
// points : [{x (0..1 position), y}] ; labels : [{x, text}]
function lineChart(svgId, points, { color = cssVar("--chart"), cap = null, capLabel = "", labels = [], unit = "W" } = {}) {
  const el = $(svgId), W = Math.max(320, Math.round(el.getBoundingClientRect().width) || 720), H = 210, pad = 18;
  const max = (Math.max(...points.map((p) => p.y), cap || 0, 1)) * 1.15;
  const X = (x) => pad + x * (W - 2 * pad), Y = (v) => H - 26 - v / max * (H - 44);
  let out = `<defs><linearGradient id="${svgId}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
  for (let k = 0; k <= 3; k++) { const gy = Y(max * k / 3.45); out += `<line x1="${pad}" x2="${W - pad}" y1="${gy}" y2="${gy}" stroke="currentColor" stroke-opacity=".12"/><text x="${pad}" y="${gy - 3}">${Math.round(max * k / 3.45)} ${unit}</text>`; }
  labels.forEach((l) => (out += `<text x="${X(l.x)}" y="${H - 7}" text-anchor="middle">${esc(l.text)}</text>`));
  if (cap) out += `<line x1="${pad}" x2="${W - pad}" y1="${Y(cap)}" y2="${Y(cap)}" stroke="#ff5d66" stroke-dasharray="6 5" stroke-width="1.5"/><text x="${W - pad}" y="${Y(cap) - 6}" text-anchor="end" style="fill:#ff8a92">${esc(capLabel)}</text>`;
  if (points.length > 1) {
    const pts = points.map((p) => `${X(p.x)},${Y(p.y)}`).join(" ");
    out += `<polygon points="${X(points[0].x)},${Y(0)} ${pts} ${X(points[points.length - 1].x)},${Y(0)}" fill="url(#${svgId}g)"/><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
  } else if (points.length === 1) out += `<circle cx="${X(points[0].x)}" cy="${Y(points[0].y)}" r="4" fill="${color}"/>`;
  el.style.height = H + "px"; el.setAttribute("viewBox", `0 0 ${W} ${H}`); el.innerHTML = out;
}
function setGauge(id, pct, color) {
  const arc = $(id), r = 100, full = 2 * Math.PI * r, sweep = full * 0.75;
  arc.setAttribute("stroke-dasharray", `${sweep} ${full}`);
  arc.setAttribute("stroke-dashoffset", String(sweep * (1 - Math.max(0.02, Math.min(1, pct)))));
  arc.style.stroke = color; arc.style.color = color;
}
function alertsHTML(list) {
  return list.map((a) => `<div class="alert ${a.level}">${esc(a.text)}</div>`).join("");
}
const timeAgo = (ts, now) => { const s = Math.max(0, now - ts); return s < 90 ? "a l'instant" : s < 5400 ? `il y a ${Math.round(s / 60)} min` : s < 129600 ? `il y a ${Math.round(s / 3600)} h` : `il y a ${Math.round(s / 86400)} j`; };

// ---------- Meteo et climat (sorties des modeles IA) ----------
const GRAN = { day: "journalier", month: "mensuel", year: "annuel" };
function climateChart(svgId, items) {
  const el = $(svgId), W = Math.max(320, Math.round(el.getBoundingClientRect().width) || 720), H = 220, pad = 40, n = items.length;
  const vals = items.flatMap((i) => [i.temp_mean_c, i.temp_min_c ?? i.temp_mean_c, i.temp_max_c ?? i.temp_mean_c]);
  let lo = Math.min(...vals), hi = Math.max(...vals); if (hi - lo < 2) { lo -= 1; hi += 1; }
  lo = Math.floor(lo - 1); hi = Math.ceil(hi + 1);
  const X = (i) => pad + (n === 1 ? 0.5 : i / (n - 1)) * (W - pad - 14), Y = (v) => H - 28 - (v - lo) / (hi - lo) * (H - 46);
  let out = "";
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = Y(v); out += `<line x1="${pad}" x2="${W - 10}" y1="${y}" y2="${y}" stroke="currentColor" stroke-opacity=".14"/><text x="${pad - 6}" y="${y + 4}" text-anchor="end">${v.toFixed(0)}°</text>`; }
  const hasBand = items.every((i) => i.temp_min_c != null && i.temp_max_c != null);
  if (hasBand && n > 1) out += `<polygon points="${items.map((it, i) => `${X(i)},${Y(it.temp_max_c)}`).join(" ")} ${items.map((it, i) => `${X(n - 1 - i)},${Y(items[n - 1 - i].temp_min_c)}`).join(" ")}" fill="rgba(255,138,31,.18)"/>`;
  const step = Math.ceil(n / 8);
  items.forEach((it, i) => { if (i % step === 0 || i === n - 1) out += `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(it.period)}</text>`; });
  out += `<polyline points="${items.map((it, i) => `${X(i)},${Y(it.temp_mean_c)}`).join(" ")}" fill="none" stroke="#ffb42e" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;
  if (n <= 40) items.forEach((it, i) => (out += `<circle cx="${X(i)}" cy="${Y(it.temp_mean_c)}" r="3.5" fill="#ffb42e"><title>${esc(it.period)} : ${it.temp_mean_c} °C</title></circle>`));
  el.style.height = H + "px"; el.setAttribute("viewBox", `0 0 ${W} ${H}`); el.innerHTML = out;
}
function renderClimate(rootId, data, hint = "") {
  const root = $(rootId), fm = (v, d = 1, u = "") => (v == null ? "—" : Number(v).toFixed(d) + u);
  if (!data.groups.length) {
    root.innerHTML = `<div class="glass empty"><div class="ic">🌡️</div><h2>Aucune prevision pour l'instant</h2><p class="sub" style="max-width:540px;margin:6px auto 0">${esc(hint || "Les previsions apparaitront ici des que le modele d'IA aura envoye ses resultats. Rien n'est calcule ni invente par l'application.")}</p></div>`;
    return;
  }
  root.innerHTML = data.groups.map((g, gi) => `<div class="glass" style="margin-top:14px">
    <div class="head"><h2>${esc(g.model)} <span class="chip">${esc(GRAN[g.granularity] || g.granularity)}</span>${g.scenario ? ` <span class="chip">scenario ${esc(g.scenario)}</span>` : ""}${g.version ? ` <span class="chip">v${esc(g.version)}</span>` : ""}</h2>
      <span class="sub">mis a jour ${new Date(g.updated * 1000).toLocaleString(LOCALE)}</span></div>
    <div class="legend"><span><i style="background:#ffb42e"></i>Temperature moyenne (°C)</span><span><i style="background:rgba(255,138,31,.4)"></i>Min – max</span></div>
    <svg class="chart" id="clim_${rootId}_${gi}" style="height:230px"></svg>
    <div style="max-height:220px;overflow:auto;margin-top:8px"><table><tr><th>Periode</th><th>Moyenne</th><th>Min</th><th>Max</th><th>Pluie (mm)</th><th>Humidite</th><th>Confiance</th></tr>
      ${g.items.map((i) => `<tr><td>${esc(i.period)}</td><td><b>${fm(i.temp_mean_c, 1, " °C")}</b></td><td>${fm(i.temp_min_c, 1)}</td><td>${fm(i.temp_max_c, 1)}</td><td>${fm(i.precip_mm, 0)}</td><td>${fm(i.humidity_pct, 0, " %")}</td><td>${i.confidence == null ? "—" : Math.round(i.confidence * 100) + " %"}</td></tr>`).join("")}</table></div>
    <p class="sub" style="margin-top:8px">${g.source === "open-meteo" ? "Source : Open-Meteo, previsions meteorologiques pour le chef-lieu de votre gouvernorat (16 jours). Ce n'est pas une mesure de votre quartier." : "Prevision generee par un modele d'intelligence artificielle : ce n'est pas une mesure."}</p></div>`).join("");
  data.groups.forEach((g, gi) => climateChart(`clim_${rootId}_${gi}`, g.items));
}

// Graphique multi-courbes sur des periodes (mois). series : [{color, dashed, vals: {periode: valeur}}]
function seriesChart(svgId, periods, series) {
  const el = $(svgId), W = Math.max(320, Math.round(el.getBoundingClientRect().width) || 720), H = 250, pad = 48, n = periods.length;
  const all = series.flatMap((s) => Object.values(s.vals));
  if (!all.length) { el.innerHTML = ""; return; }
  let lo = Math.min(...all), hi = Math.max(...all); const m = (hi - lo) * 0.08 || 1; lo -= m; hi += m;
  const X = (i) => pad + (n === 1 ? 0.5 : i / (n - 1)) * (W - pad - 14), Y = (v) => H - 28 - (v - lo) / (hi - lo) * (H - 46);
  let out = "";
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = Y(v); out += `<line x1="${pad}" x2="${W - 10}" y1="${y}" y2="${y}" stroke="currentColor" stroke-opacity=".14"/><text x="${pad - 6}" y="${y + 4}" text-anchor="end">${v.toFixed(0)}</text>`; }
  const step = Math.ceil(n / 8);
  periods.forEach((p, i) => { if (i % step === 0 || i === n - 1) out += `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(p)}</text>`; });
  series.forEach((s) => {
    let seg = [];
    const flush = () => { if (seg.length > 1) out += `<polyline points="${seg.join(" ")}" fill="none" stroke="${s.color}" stroke-width="${s.w || 2.5}" ${s.dashed ? 'stroke-dasharray="7 5"' : ""} stroke-linejoin="round" stroke-linecap="round"/>`; else if (seg.length === 1) out += `<circle cx="${seg[0].split(",")[0]}" cy="${seg[0].split(",")[1]}" r="3.5" fill="${s.color}"/>`; seg = []; };
    periods.forEach((p, i) => { if (s.vals[p] != null) seg.push(`${X(i)},${Y(s.vals[p])}`); else flush(); });
    flush();
  });
  el.style.height = H + "px"; el.setAttribute("viewBox", `0 0 ${W} ${H}`); el.innerHTML = out;
}

// ---------- Editeur d'equipements : circuit du tableau, priorite en cas de pointe, delai de report ----------
const CIRCUITS = ["Eclairage", "Prises", "Cuisine", "Chambres", "Salon", "Climatisation", "Eau chaude", "Autre"];
const PRIO = [["protege", "Protege (jamais coupe)"], ["inutile", "1 · Inutile"], ["tolerable", "2 · Tolerable"], ["decalable", "3 · Decalable"], ["confort", "4 · Confort"]];
// nom, puissance typique (W), circuit, priorite, delai maximal de report (h)
const PRESETS = [["Climatiseur", 1200, "Climatisation", "confort", null], ["Réfrigérateur", 150, "Cuisine", "protege", null], ["Congélateur", 200, "Cuisine", "protege", null],
  ["Chauffe-eau électrique", 1500, "Eau chaude", "decalable", 6], ["Machine à laver", 800, "Cuisine", "decalable", 12], ["Lave-vaisselle", 1200, "Cuisine", "decalable", 12],
  ["Four électrique", 2000, "Cuisine", "decalable", 2], ["Micro-ondes", 1000, "Cuisine", "tolerable", null], ["Bouilloire", 2000, "Cuisine", "tolerable", null],
  ["Télévision", 100, "Salon", "tolerable", null], ["Ordinateur", 150, "Prises", "tolerable", null], ["Chargeurs (téléphones…)", 20, "Prises", "tolerable", null],
  ["Box internet", 15, "Prises", "protege", null], ["Équipement médical vital", 100, "Autre", "protege", null], ["Éclairage (par lampe LED)", 10, "Eclairage", "inutile", null],
  ["Ventilateur", 60, "Chambres", "confort", null], ["Pompe à eau / irrigation", 1100, "Autre", "decalable", 12], ["Fer à repasser", 1200, "Prises", "decalable", 6]];

function equipmentEditor(rootId, items) {
  const root = $(rootId); let rows = items.map((x) => ({ circuit: "Autre", priority: "confort", defer_h: null, ...x }));
  const opt = (list, cur, f = (x) => x) => list.map((x) => `<option value="${esc(Array.isArray(x) ? x[0] : x)}" ${(Array.isArray(x) ? x[0] : x) === cur ? "selected" : ""}>${esc(Array.isArray(x) ? x[1] : x)}</option>`).join("");
  const draw = () => {
    root.innerHTML = (rows.length ? `<div class="eq sub"><span>Equipement</span><span>Puissance (W)</span><span>Qte</span><span>Circuit</span><span>Priorite en pointe</span><span>Report max</span><span></span></div>` : `<div class="sub">Aucun equipement pour l'instant.</div>`) +
      rows.map((r, i) => `<div class="eq"><input type="text" value="${esc(r.name)}" data-i="${i}" data-k="name" maxlength="40" data-vtype="label" required><input type="number" min="1" max="20000" value="${r.power_w}" data-i="${i}" data-k="power_w"><input type="number" min="1" max="20" value="${r.quantity}" data-i="${i}" data-k="quantity">
        <select data-i="${i}" data-k="circuit">${opt(CIRCUITS, r.circuit)}</select><select data-i="${i}" data-k="priority">${opt(PRIO, r.priority)}</select>
        ${r.priority === "decalable" ? `<input type="number" min="0" max="48" placeholder="h" value="${r.defer_h ?? ""}" title="Delai maximal de report (heures)" data-i="${i}" data-k="defer_h">` : "<span></span>"}
        <button type="button" class="btn sm danger" data-del="${i}">✕</button></div>`).join("") +
      `<select id="${rootId}Add" style="margin-top:10px;max-width:340px"><option value="">+ Ajouter un equipement…</option>${PRESETS.map((p, i) => `<option value="${i}">${esc(p[0])} (~${p[1]} W)</option>`).join("")}<option value="custom">Autre (saisie libre)</option></select>
       <div class="sub" style="margin-top:6px">Puissances typiques, a corriger selon l'etiquette. <b>Priorite en pointe</b> : « Protege » n'est jamais coupe (ex. refrigerateur) ; 1 a eteindre ; 2 a couper ou differer un peu ; 3 a reporter apres la pointe ; 4 a moduler (ex. climatiseur).</div>`;
    root.querySelectorAll("[data-i]").forEach((el) => { const f = () => { const r = rows[+el.dataset.i], k = el.dataset.k; r[k] = ["name", "circuit", "priority"].includes(k) ? el.value : el.value === "" ? null : +el.value; if (k === "priority") draw(); };
      el.oninput = el.tagName === "SELECT" ? null : f; el.onchange = f; });
    root.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => { rows.splice(+b.dataset.del, 1); draw(); }));
    $(rootId + "Add").onchange = (e) => { const v = e.target.value; if (!v) return; rows.push(v === "custom" ? { name: "", power_w: 100, quantity: 1, circuit: "Autre", priority: "confort", defer_h: null } : { name: PRESETS[+v][0], power_w: PRESETS[+v][1], quantity: 1, circuit: PRESETS[+v][2], priority: PRESETS[+v][3], defer_h: PRESETS[+v][4] }); draw(); };
  };
  draw();
  return { get: () => rows.map((r) => ({ name: String(r.name).trim(), power_w: Math.round(r.power_w), quantity: Math.round(r.quantity), circuit: r.circuit, priority: r.priority, defer_h: r.priority === "decalable" ? r.defer_h : null })), total: () => rows.reduce((s, r) => s + r.power_w * r.quantity, 0) };
}

// ---------- Carte (Leaflet / OpenStreetMap) ----------
const TUNISIA = [33.9, 9.5];
function mapAvailable() { return typeof L !== "undefined"; }
function newMap(elId, center = TUNISIA, zoom = 6) {
  const m = L.map(elId, { zoomControl: true }).setView(center, zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, referrerPolicy: "origin", attribution: "© les contributeurs d'OpenStreetMap" }).addTo(m);      // OpenStreetMap exige un Referer (politique d'utilisation des tuiles)
  return m;
}
// Carte pour poser le pin du domicile. onChange(lat, lng). Retourne {set(lat,lng)}
function pinMap(elId, lat, lng, onChange) {
  if (!mapAvailable()) { $(elId).innerHTML = `<div class="empty sub">La carte n'a pas pu se charger (connexion internet requise). Saisissez la latitude et la longitude a la main.</div>`; return null; }
  const m = newMap(elId, lat ? [lat, lng] : TUNISIA, lat ? 15 : 6); let marker = null;
  const put = (a, b, move) => { if (marker) marker.setLatLng([a, b]); else { marker = L.marker([a, b], { draggable: true }).addTo(m); marker.on("dragend", () => { const p = marker.getLatLng(); onChange(+p.lat.toFixed(6), +p.lng.toFixed(6)); }); } if (move) m.setView([a, b], Math.max(m.getZoom(), 15)); onChange(+a.toFixed(6), +b.toFixed(6)); };
  m.on("click", (e) => put(e.latlng.lat, e.latlng.lng, false));
  if (lat) put(lat, lng, false);
  setTimeout(() => m.invalidateSize(), 250);
  return { set: (a, b) => put(a, b, true), map: m };
}


// ---------- Theme clair / sombre ----------
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#1f9d8a";
const TT_HTML = '<span class="knob"><svg class="sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg><svg class="moon" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></span>';
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("kw-theme", t); } catch (e) {}
  const m = document.querySelector('meta[name="theme-color"]'); if (m) m.content = t === "light" ? "#eaf4f3" : "#06171a";
  document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
}
function makeToggle() {
  const b = document.createElement("button"); b.className = "tt"; b.type = "button"; b.innerHTML = TT_HTML;
  b.title = "Mode clair / sombre"; b.setAttribute("aria-label", "Basculer entre le mode clair et le mode sombre");
  b.onclick = () => setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  return b;
}
(function initTheme() {
  const go = () => {
    const top = document.querySelector(".topnav .grow");
    if (top) top.after(makeToggle());
    const foot = document.querySelector(".side .foot");
    if (foot) { const row = document.createElement("div"); row.className = "themerow"; row.innerHTML = "<span>Apparence</span>"; row.appendChild(makeToggle()); foot.prepend(row); }
    // reflet lumineux qui suit la souris sur les cartes
    document.addEventListener("pointermove", (e) => {
      const t = e.target.closest && e.target.closest(".glass, .kpi"); if (!t) return;
      const r = t.getBoundingClientRect(); t.style.setProperty("--mx", e.clientX - r.left + "px"); t.style.setProperty("--my", e.clientY - r.top + "px");
    }, { passive: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
})();


// ---------- Donut : parts = [{label, value, color}] ----------
const _donutSeen = new Set();   // l'animation d'entree ne joue qu'une fois par graphique (la page se rafraichit toutes les 3 s)
function donut(parts, { size = 210, thick = 30, center = "", sub = "", fmtv = (v) => fmt(Math.round(v)), unit = "", key = "" } = {}) {
  const still = key && _donutSeen.has(key); if (key) _donutSeen.add(key);
  const live = parts.filter((p) => p.value > 0), tot = live.reduce((s, p) => s + p.value, 0) || 1;
  const r = (size - thick) / 2 - 6, c = 2 * Math.PI * r, gap = live.length > 1 ? 3.5 : 0;
  let off = 0, segs = `<circle class="base" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${thick}"/>`;
  live.forEach((p) => {
    const len = p.value / tot * c;
    segs += `<circle class="seg" ${still ? 'style="animation:none"' : ""} cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="${p.color}" stroke-width="${thick}" stroke-dasharray="${Math.max(0.5, len - gap)} ${c}" stroke-dashoffset="${-off}"><title>${esc(p.label)} : ${fmtv(p.value)}${unit} (${Math.round(100 * p.value / tot)} %)</title></circle>`;
    off += len;
  });
  const leg = live.map((p) => `<div><span><i style="background:${p.color}"></i>${esc(p.label)}</span><b>${fmtv(p.value)}${unit} · ${Math.round(100 * p.value / tot)} %</b></div>`).join("");
  return `<div class="donutbox"><div class="donut"><svg viewBox="0 0 ${size} ${size}">${segs}</svg><div class="dc"><b>${center}</b><span>${sub}</span></div></div><div class="dleg">${leg}</div></div>`;
}


// ---------- Coque : barre du haut (pilules, profil, cloche) ----------
const SVGI = {
  spark: '<path d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  list: '<path d="M4 6h16M4 12h11M4 18h6"/><path d="M19 15v6M16 18h6"/>', gift: '<rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 13h18M12 8c-2-4-6-3-5 0 .5 1.3 3 1.5 5 0zM12 8c2-4 6-3 5 0-.5 1.3-3 1.5-5 0z"/>',
  map: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>', bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>', gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>', pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>', trend: '<path d="m3 17 6-6 4 4 8-8M15 7h6v6"/>', gauge: '<path d="M12 14l4-5"/><path d="M4.9 19a9 9 0 1 1 14.2 0"/>',
  doc: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>', line: '<path d="M3 17l5-6 4 3 8-9"/>', bars: '<path d="M5 20V10M12 20V4M19 20v-7"/>', home: '<path d="M3 11l9-8 9 8M5 10v10h14V10"/>'
};
const svgi = (k, extra = "") => `<svg viewBox="0 0 24 24" ${extra}>${SVGI[k] || ""}</svg>`;
function setBell(n) { const b = document.querySelector("#tbBell .bdg"); if (!b) return; b.textContent = n > 9 ? "9+" : n; b.classList.toggle("hide", !n); }
function initShell() {
  const main = document.querySelector(".main"); if (!main || main.querySelector(".topbar")) return;
  document.querySelectorAll(".nav a").forEach((a) => { const t = [...a.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim(); if (t) a.dataset.label = t; });
  const pills = (window.KW_PILLS || []).map((p, i) => `<button class="tb-pill" data-i="${i}" type="button">${svgi(p.icon)}<span>${esc(p.label)}</span></button>`).join("");
  const bar = document.createElement("div"); bar.className = "topbar";
  bar.innerHTML = `<a class="tb-brand" href="/">Kizuna <b>Watt</b><small>STEG · طاقة · 絆</small></a>${pills}<span class="tb-grow"></span>
    <div class="tb-user"><div class="av" id="tbAv">··</div><div class="who"><b id="tbName"> </b><small id="tbRole"> </small></div></div>
    ${langSelect("tbLang")}
    <button class="tb-ic" id="tbBell" type="button" title="Notifications">${svgi("bell")}<i class="bdg hide">0</i></button>
    ${window.KW_GEAR ? `<button class="tb-ic" id="tbGear" type="button" title="Mon profil">${svgi("gear")}</button>` : ""}`;
  main.prepend(bar);
  bar.querySelectorAll(".tb-pill").forEach((b) => (b.onclick = () => window.KW_PILLS[+b.dataset.i].go()));
  $("tbBell").onclick = () => window.KW_BELL && window.KW_BELL();
  bindLang("tbLang");
  if ($("tbGear")) $("tbGear").onclick = () => window.KW_GEAR();
  api("/api/me").then((r) => { if (!r.ok) return; const n = r.data.profile.full_name || r.data.email || ""; $("tbName").textContent = n;
    $("tbRole").textContent = r.data.role === "steg" ? "Gestionnaire STEG" : "Client · " + (r.data.profile.governorate || "");
    $("tbAv").textContent = n.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "KW"; });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(initShell, 0)); else setTimeout(initShell, 0);

// ---------- Mini-graphiques (sans texte : peuvent s'etirer) ----------
function _smooth(pts) { if (pts.length < 3) return "M" + pts.map((p) => p.join(",")).join("L"); let d = `M${pts[0][0]},${pts[0][1]}`; for (let i = 1; i < pts.length; i++) { const p0 = pts[i - 1], p1 = pts[i], mx = (p0[0] + p1[0]) / 2; d += `C${mx},${p0[1]} ${mx},${p1[1]} ${p1[0]},${p1[1]}`; } return d; }
function miniLine(vals, { stroke = "#fff", fill = "rgba(255,255,255,.18)", dot = "last", dotColor = null, W = 300, H = 90, w = 2.4, dots = false } = {}) {
  if (!vals.length) return ""; const max = Math.max(...vals, 1e-9) * 1.1, n = vals.length, pts = vals.map((v, i) => [n === 1 ? W / 2 : 4 + i / (n - 1) * (W - 8), H - 6 - v / max * (H - 16)]);
  const idx = dot === "max" ? vals.indexOf(Math.max(...vals)) : n - 1, d = _smooth(pts), id = "ml" + Math.random().toString(36).slice(2, 7);
  return `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${fill}"/><stop offset="1" stop-color="${fill}" stop-opacity="0"/></linearGradient></defs>
    <path d="${d}L${pts[n - 1][0]},${H}L${pts[0][0]},${H}Z" fill="url(#${id})"/><path d="${d}" fill="none" stroke="${stroke}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" ${dots ? 'stroke-dasharray="1 6"' : ""}/>
    ${dot ? `<line x1="${pts[idx][0]}" x2="${pts[idx][0]}" y1="${pts[idx][1]}" y2="${H}" stroke="${dotColor || stroke}" stroke-opacity=".45" stroke-dasharray="3 4"/><circle cx="${pts[idx][0]}" cy="${pts[idx][1]}" r="5.5" fill="#fff" stroke="${dotColor || stroke}" stroke-width="3"/>` : ""}`;
}
function miniBars(vals, { color = "#1f9d8a", W = 300, H = 90, hi = null } = {}) {
  if (!vals.length) return ""; const max = Math.max(...vals, 1e-9), n = vals.length, bw = (W - 4) / n; let out = "";
  vals.forEach((v, i) => { const h = Math.max(2, v / max * (H - 6)); out += `<rect x="${2 + i * bw + bw * .18}" y="${H - h}" width="${bw * .64}" height="${h}" rx="${Math.min(2, bw * .3)}" fill="${hi != null && i === hi ? "#f29a4a" : color}" opacity="${hi != null && i === hi ? 1 : .85}"/>`; });
  return out;
}
// Demi-jauge a segments arrondis. parts : [{label, value, color}]
const _sgSeen = new Set();
function semiGauge(parts, { center = "", sub = "", key = "" } = {}) {
  const live = parts.filter((p) => p.value > 0), tot = live.reduce((s, p) => s + p.value, 0) || 1, R = 100, L = Math.PI * R, gap = live.length > 1 ? 16 : 0, still = key && _sgSeen.has(key); if (key) _sgSeen.add(key);
  let off = 0, segs = `<path d="M20 120A100 100 0 0 1 220 120" fill="none" stroke="currentColor" stroke-opacity=".08" stroke-width="22" stroke-linecap="round"/>`;
  live.forEach((p) => { const len = p.value / tot * L; segs += `<path class="seg" ${still ? 'style="animation:none"' : ""} d="M20 120A100 100 0 0 1 220 120" stroke="${p.color}" stroke-width="22" stroke-dasharray="${Math.max(2, len - gap)} ${L * 2}" stroke-dashoffset="${-(off + gap / 2)}"><title>${esc(p.label)} : ${Math.round(100 * p.value / tot)} %</title></path>`; off += len; });
  return `<div class="sg"><svg viewBox="0 0 240 134" style="color:var(--ink)">${segs}</svg><div class="gc"><span>${sub}</span><b>${center}</b></div></div>`;
}


// ---------- Controle de saisie (toutes les zones de texte) ----------
// Regle par champ : attributs HTML (required, maxlength, min, max, step, pattern, type) + data-vtype ou table VID ci-dessous.
// Retour immediat sous le champ, normalisation des espaces a la sortie du champ, blocage de l'envoi si un champ est invalide.
// Le serveur revalide TOUT : ce controle sert a guider l'utilisateur, pas a se proteger.
const _CTRL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u202A-\u202E\u2066-\u2069]/;
const VID = { full_name: "name", p_full_name: "name", mmName: "name", phone: "phone", p_phone: "phone", mmEmail: "mail", postal_code: "postal", p_postal_code: "postal",
  steg_ref: "ref", p_steg_ref: "ref", meter_number: "meter", p_meter_number: "meter", address: "address", p_address: "address", delegation: "place", p_delegation: "place", l_del: "place",
  devName: "label", modelName: "label", l_name: "label", l_zone: "label", pkNote: "note", rwRef: "ref2", rwCodes: "codes", bhA: "money", rwAmount: "money", lat: "lat", lng: "lng" };
const _tr = (m) => (window.T ? window.T(m) : m);
function vCheck(el) {
  if (!el || el.disabled || el.readOnly || ["hidden", "checkbox", "radio", "file", "range", "button", "submit"].includes(el.type) || el.dataset.novalidate !== undefined) return "";
  const raw = el.value == null ? "" : String(el.value), secret = el.type === "password", v = secret || el.tagName === "TEXTAREA" ? raw : raw.trim(), kind = el.dataset.vtype || VID[el.id] || "";
  if (v === "") return el.required || el.dataset.vrequired !== undefined ? "Ce champ est obligatoire." : "";
  if (_CTRL.test(raw)) return "Caracteres non autorises.";
  if (el.tagName === "SELECT") return "";
  const textual = ["text", "search", "email", "tel", "password", "url"].includes(el.type) || el.tagName === "TEXTAREA";
  const max = +el.maxLength > 0 ? +el.maxLength : el.tagName === "TEXTAREA" ? +el.dataset.vmax || 2000 : secret ? 128 : 160;
  if (textual && raw.length > max) return `Maximum ${max} caracteres.`;
  if (textual && !secret && !["codes", "csv"].includes(kind) && /[<>]/.test(v)) return "Les caracteres < et > ne sont pas autorises.";
  const dg = v.replace(/[\s.\-]/g, "").replace(/^(\+216|00216)/, "");
  switch (kind) {
    case "name": if (!/^[\p{L}\p{M}][\p{L}\p{M} '’.\-()]{2,79}$/u.test(v)) return "Nom invalide : lettres, espaces, ' - . ( ) (3 a 80 caracteres)."; break;
    case "phone": if (!/^[2-9]\d{7}$/.test(dg)) return "Numero tunisien a 8 chiffres (ex. 22 123 456)."; break;
    case "intphone": { const x = v.replace(/[\s().\-]/g, "").replace(/^00/, "+"); if (!(/^\+[1-9]\d{7,14}$/.test(x) || /^\d{8}$/.test(x))) return "Numero : 8 chiffres (Tunisie) ou format international (+81 90 1234 5678)."; break; }
    case "mail": if (v && !/^[^@\s<>"',;]+@[^@\s<>"',;]+\.[A-Za-z]{2,}$/.test(v)) return "Adresse e-mail invalide."; break;
    case "postal": if (!/^\d{4}$/.test(v)) return "Code postal : 4 chiffres."; break;
    case "ref": if (!/^[A-Za-z0-9\-/ ]{4,24}$/.test(v)) return "Reference : 4 a 24 caracteres (lettres, chiffres, - /)."; break;
    case "meter": if (!/^[A-Za-z0-9\-]{4,24}$/.test(v)) return "Numero de compteur : 4 a 24 caracteres (lettres, chiffres, -)."; break;
    case "ref2": if (!/^[\p{L}\p{N} \-/._#]{1,40}$/u.test(v)) return "Reference : 40 caracteres maximum (lettres, chiffres, - / . _ #)."; break;
    case "address": if (v.length < 5 || !/^[\p{L}\p{N}\p{M} ,.'’°#\-/()]+$/u.test(v)) return "Adresse : au moins 5 caracteres (lettres, chiffres, , . ' - /)."; break;
    case "place": if (!/^[\p{L}\p{M}][\p{L}\p{M}\p{N} '’.\-()]{1,59}$/u.test(v)) return "Texte invalide : 2 a 60 caracteres (lettres, chiffres, espaces)."; break;
    case "label": if (!/^[\p{L}\p{N}\p{M}][\p{L}\p{N}\p{M} '’.,&+()\-_/]{1,59}$/u.test(v)) return "Nom invalide : 2 a 60 caracteres (lettres, chiffres, espaces, . , & + ( ) - _ /)."; break;
    case "codes": { const L = v.split(/\n/).map((s) => s.trim()).filter(Boolean); if (!L.length || L.length > 10 || L.some((s) => !/^\d{16}$/.test(s.replace(/[\s\-]/g, "")))) return "Un code par ligne : 16 chiffres chacun (10 codes au maximum)."; break; }
  }
  if (el.type === "email" || kind === "email") { if (v.length > 120 || !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v)) return "Adresse e-mail invalide."; }
  if (el.type === "password" && (el.autocomplete === "new-password" || kind === "newpass")) {
    if (raw.length < 8 || !/\d/.test(raw) || !/[A-Za-z]/.test(raw)) return "8 caracteres minimum, avec au moins une lettre et un chiffre.";
    if (el.id === "password2" && document.getElementById("password") && raw !== document.getElementById("password").value) return "Les mots de passe ne correspondent pas.";
  }
  if (el.type === "number" || kind === "money" || kind === "lat" || kind === "lng") {
    const n = Number(String(v).replace(",", ".")); if (!Number.isFinite(n)) return "Nombre invalide.";
    const lo = el.min !== "" && el.min != null ? +el.min : kind === "money" ? 0 : null, hi = el.max !== "" && el.max != null ? +el.max : kind === "money" ? 100000 : null;
    if (kind === "lat" && (n < 30 || n > 37.6)) return "Latitude hors de la Tunisie (30 a 37,6).";
    if (kind === "lng" && (n < 7.4 || n > 11.8)) return "Longitude hors de la Tunisie (7,4 a 11,8).";
    if (lo != null && n < lo) return `Valeur minimale : ${lo}.`; if (hi != null && n > hi) return `Valeur maximale : ${hi}.`;
    if (kind === "money" && n <= 0) return "Le montant doit etre superieur a 0.";
    const st = el.step && el.step !== "any" ? +el.step : null; if (st) { const q = (n - (lo || 0)) / st; if (Math.abs(q - Math.round(q)) > 1e-6) return `Pas de ${st} : ${(Math.round(q) * st + (lo || 0)).toFixed(3).replace(/\.?0+$/, "")} par exemple.`; }
    if (kind === "money" && /[.,]\d{4,}/.test(v)) return "3 decimales au maximum.";
  }
  if (el.type === "date" || el.type === "datetime-local") {
    const d = new Date(v); if (isNaN(d) || d.getFullYear() < 2000 || d.getFullYear() > 2100) return "Date invalide.";
    if (el.dataset.vfuture === "no" || el.id === "bhD") { if (d.getTime() > Date.now() + 864e5) return "La date ne peut pas etre dans le futur."; }
  }
  if (el.dataset.k === "start" || el.dataset.k === "end") { if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(v) && !(el.dataset.k === "end" && v === "24:00")) return "Heure au format HH:MM."; }
  if (el.pattern) { try { if (!new RegExp("^(?:" + el.pattern + ")$").test(v)) return el.title || "Format invalide."; } catch (e) {} }
  return "";
}
function showErr(el, msg) {
  if (!el || !el.insertAdjacentElement) return;
  let box = el._vm; if (!box && msg) { box = el._vm = document.createElement("div"); box.className = "err vmsg"; box.setAttribute("role", "alert"); el.insertAdjacentElement("afterend", box); }
  if (box) box.textContent = msg ? _tr(msg) : "";
  el.classList.toggle("bad", !!msg); if (msg) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
}
function _vroots(root) {
  if (typeof root === "string") return [...document.querySelectorAll(root)];
  return root ? [root] : [];
}
function validateGroup(root) {
  const els = [];
  for (const r of _vroots(root)) { if (r.matches && r.matches("input, textarea, select")) els.push(r); else if (r.querySelectorAll) els.push(...r.querySelectorAll("input, textarea, select")); }
  let first = null; const seen = new Set();
  for (const el of els) { if (seen.has(el)) continue; seen.add(el); const m = vCheck(el); showErr(el, m); if (m && !first) first = el; }
  const s = document.getElementById("pkStart"), e = document.getElementById("pkEnd");           // pointe : fin apres debut, 12 h au maximum
  if (!first && s && e && seen.has(e) && s.value && e.value) {
    const d = (new Date(e.value) - new Date(s.value)) / 3600000;
    const m = d <= 0 ? "La fin doit etre apres le debut." : d > 12 ? "Une pointe dure 12 h au maximum." : ""; if (m) { showErr(e, m); first = e; }
  }
  if (first) { try { first.scrollIntoView({ behavior: "smooth", block: "center" }); first.focus({ preventScroll: true }); } catch (x) {} }
  return !first;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest("[data-validate]"); if (!b) return;
  let sel = b.dataset.validate; if (sel.startsWith("fn:")) sel = (window[sel.slice(3)] || (() => ""))();
  if (!validateGroup(sel || b.closest("form"))) { e.stopImmediatePropagation(); e.preventDefault(); }
}, true);
document.addEventListener("submit", (e) => { if (!validateGroup(e.target)) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
document.addEventListener("focusout", (e) => {
  const el = e.target; if (!el || !el.matches || !el.matches("input, textarea, select")) return;
  if (["text", "search", "email", "tel"].includes(el.type) && el.value !== el.value.replace(/\s+/g, " ").trim()) el.value = el.value.replace(/\s+/g, " ").trim();     // espaces normalises
  showErr(el, vCheck(el));
}, true);
document.addEventListener("input", (e) => { const el = e.target; if (el && el._vm && el._vm.textContent) showErr(el, vCheck(el)); });
function _autoMax(root) { (root || document).querySelectorAll("input[type=text], input[type=search], input[type=email], input[type=tel]").forEach((el) => { if (!el.hasAttribute("maxlength") && el.dataset.novalidate === undefined) el.maxLength = 160; }); (root || document).querySelectorAll("input[type=password]").forEach((el) => { if (!el.hasAttribute("maxlength")) el.maxLength = 128; }); }
document.addEventListener("DOMContentLoaded", () => { _autoMax(); new MutationObserver((m) => m.forEach((x) => x.addedNodes.forEach((n) => n.querySelectorAll && _autoMax(n)))).observe(document.body, { childList: true, subtree: true }); });


// ---------- Langues : libelles de periodes, choix de langue ----------
function periodLabel(ps) {
  const y = +ps.slice(0, 4), m = +ps.slice(5, 7), f = (mm, yy) => new Intl.DateTimeFormat(LOCALE, { month: "long" }).format(new Date(yy, mm - 1, 15));
  return `${f(m, y)} - ${f(m % 12 + 1, y)} ${m === 12 ? y + 1 : y}`;
}
function langSelect(id) {
  const L = window.KW_LANGS; if (!L) return "";
  return `<select class="langsel" id="${id}" aria-label="Langue" title="Langue" data-novalidate>${Object.entries(L).map(([k, v]) => `<option value="${k}" ${k === window.KW_LANG ? "selected" : ""}>${v.name}</option>`).join("")}</select>`;
}
function bindLang(id) { const e = document.getElementById(id); if (e) e.onchange = () => window.kwSetLang && window.kwSetLang(e.value); }
document.addEventListener("DOMContentLoaded", () => setTimeout(() => {
  const g = document.querySelector(".topnav .grow"); if (g && window.KW_LANGS && !document.getElementById("pubLang")) { g.insertAdjacentHTML("afterend", langSelect("pubLang")); bindLang("pubLang"); }
}, 0));
