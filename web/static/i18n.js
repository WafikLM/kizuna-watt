// Moteur de langues (francais, anglais, arabe, japonais). Le dictionnaire (window.KW_DICT) est charge avant la page par un petit script de l'en-tete.
// Les textes de l'application sont ecrits en francais : ce moteur remplace, dans la page, chaque texte connu par sa traduction (texte entier, modele {0} {1},
// ou fragment d'un texte plus long), y compris pour ce que les scripts ajoutent apres coup. Un texte inconnu reste en francais.
(function () {
  const L = {
    fr: { name: "Français", dir: "ltr", loc: "fr-FR" }, en: { name: "English", dir: "ltr", loc: "en-GB" },
    ar: { name: "العربية", dir: "rtl", loc: "ar-TN-u-nu-latn" }, ja: { name: "日本語", dir: "ltr", loc: "ja-JP" },
  };
  let lang = "fr"; try { lang = localStorage.getItem("kw-lang") || "fr"; } catch (e) {}
  if (!L[lang]) lang = "fr";
  window.KW_LANG = lang; window.KW_LANGS = L;
  if (typeof LOCALE !== "undefined") LOCALE = L[lang].loc;
  const root = document.documentElement; root.lang = lang; root.dir = L[lang].dir;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const dict = lang === "fr" ? {} : window.KW_DICT || {};
  const exact = new Map(), pats = [], subs = [];
  for (const [k, v] of Object.entries(dict)) {
    if (/\{\d\}/.test(k)) {
      let re = "^"; const idx = [];
      for (const p of k.split(/(\{\d\})/)) { const m = p.match(/^\{(\d)\}$/); if (m) { re += "(.+?)"; idx.push(+m[1]); } else re += esc(p); }
      pats.push({ re: new RegExp(re + "$"), idx, to: v, len: k.length });
    } else {
      exact.set(k, v);
      const t = k.trim();
      if (t.length >= 4) { const A = "A-Za-z\\u00C0-\\u00FF", a = new RegExp("^[" + A + "]").test(t) ? "(?<![" + A + "])" : "", b = new RegExp("[" + A + "]$").test(t) ? "(?![" + A + "])" : ""; subs.push([new RegExp(a + esc(t) + b, "g"), v.trim() || v, t.length]); }
    }
  }
  subs.sort((x, y) => y[2] - x[2]); pats.sort((x, y) => y.len - x.len);
  function T(s) {
    if (lang === "fr" || s == null || typeof s !== "string") return s;
    const t = s.trim(); if (!t || !/[A-Za-zÀ-ÿ]/.test(t)) return s;
    const lead = s.slice(0, s.indexOf(t)), trail = s.slice(s.indexOf(t) + t.length);
    if (exact.has(t)) return lead + exact.get(t) + trail;
    for (const p of pats) { const m = t.match(p.re); if (m) return lead + p.to.replace(/\{(\d)\}/g, (_, n) => T(m[p.idx.indexOf(+n) + 1] || "")) + trail; }
    let out = t, hit = false;
    for (const [re, v] of subs) { if (re.test(out)) { re.lastIndex = 0; out = out.replace(re, v); hit = true; } re.lastIndex = 0; }
    return hit ? lead + out + trail : s;
  }
  window.T = T;
  if (lang === "fr") return;

  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE", "NOSCRIPT"]);
  function txt(n) {
    const p = n.parentNode; if (!p || SKIP.has(p.nodeName) || (p.closest && p.closest("[data-notranslate]"))) return;
    if (n.__tv !== undefined && n.nodeValue === n.__tv) return;          // deja traduit, inchange
    const fr = n.nodeValue, tr = T(fr); n.__tv = tr; if (tr !== fr) n.nodeValue = tr;
  }
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  function attrs(el) {
    if (!el.getAttribute) return;
    for (const a of ATTRS) { const v = el.getAttribute(a); if (!v) continue; el.__at = el.__at || {}; if (el.__at[a] === v) continue; const tr = T(v); el.__at[a] = tr; if (tr !== v) el.setAttribute(a, tr); }
  }
  function walk(node) {
    if (node.nodeType === 3) return txt(node);
    if (node.nodeType !== 1 || SKIP.has(node.nodeName)) return;
    attrs(node);
    const w = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, null);
    let n = w.nextNode(); while (n) { if (n.nodeType === 3) txt(n); else if (!SKIP.has(n.nodeName)) attrs(n); n = w.nextNode(); }
  }
  let queue = [], raf = 0;
  function flush() { raf = 0; const q = queue; queue = []; for (const x of q) { if (x.t === "n") walk(x.n); else if (x.t === "c") txt(x.n); else attrs(x.n); } }
  const obs = new MutationObserver((ms) => {
    for (const m of ms) {
      if (m.type === "childList") m.addedNodes.forEach((n) => queue.push({ t: "n", n }));
      else if (m.type === "characterData") queue.push({ t: "c", n: m.target });
      else if (m.type === "attributes") queue.push({ t: "a", n: m.target });
    }
    if (queue.length && !raf) raf = requestAnimationFrame(flush);
  });
  function start() {
    document.title = T(document.title); walk(document.body);
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();

// Changement de langue : memorise le choix (et l'enregistre dans le profil si le client est connecte), puis recharge la page.
async function kwSetLang(l) {
  try { localStorage.setItem("kw-lang", l); } catch (e) {}
  try { await fetch("/api/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang: l }) }); } catch (e) {}
  location.reload();
}
