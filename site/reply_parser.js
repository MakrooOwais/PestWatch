/* Farmer-reply parser that runs on the phone (offline): a port of pestwatch/i18n/inbound.py.
   Uses the intent keywords shipped in data/i18n.js. Exposes window.PestWatchReplyParser.parse(text, profileLang). */
(function () {
  "use strict";
  const I18N = window.I18N || {};
  const PRIORITY = ["STOP", "START", "LANGUAGE", "HELP", "REFERRAL", "NO_PEST", "PEST_FOUND"];
  const norm = (t) => t.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .replace(/['’]/g, "").replace(/[^a-z0-9?]+/g, " ").trim();
  function oneEdit(a, b) {
    if (Math.abs(a.length - b.length) > 1) return false;
    let i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a.length > b.length) i++; else if (b.length > a.length) j++; else { i++; j++; }
    }
    return edits + (a.length - i) + (b.length - j) <= 1;
  }
  const index = {};
  Object.entries(I18N).forEach(([code, c]) => Object.entries(c.intents || {}).forEach(([intent, words]) =>
    words.forEach((w) => { const k = norm(w); (index[k] = index[k] || []).push([code, intent]); })));
  const aliases = {};
  Object.entries(I18N).forEach(([code, c]) => (c.meta.aliases || []).forEach((a) => { aliases[norm(a)] = code; }));
  const langWords = new Set(Object.values(I18N).flatMap((c) => (c.intents.LANGUAGE || []).map(norm)));

  function parse(text, profileLang) {
    const n = norm(text), toks = n.split(" ").filter(Boolean);
    const rest = toks.filter((t) => !langWords.has(t));
    if (rest.length === 1 && aliases[rest[0]]) return { intent: "LANGUAGE", lang: aliases[rest[0]], detected: aliases[rest[0]], newLang: aliases[rest[0]] };
    const hits = {}, score = {};
    Object.entries(index).forEach(([kw, owners]) => {
      if (!kw) return;
      const hit = kw.includes(" ") || kw.length < 5
        ? new RegExp(`(^|[^a-z0-9])${kw.replace(/[?]/g, "\\?")}($|[^a-z0-9])`).test(n)
        : toks.some((t) => t === kw || oneEdit(t, kw));
      if (!hit) return;
      owners.forEach(([code, intent]) => { (hits[intent] = hits[intent] || []).push(code); });
      const langs = new Set(owners.map((o) => o[0]));
      if (langs.size === 1 && !/^\d+$/.test(kw)) { const c = owners[0][0]; score[c] = (score[c] || 0) + kw.length; }
    });
    const detected = Object.keys(score).sort((a, b) => score[b] - score[a])[0] || null;
    const intent = PRIORITY.find((i) => i !== "LANGUAGE" && hits[i]) || "UNKNOWN";
    return { intent, lang: detected || profileLang, detected, newLang: null };
  }

  window.PestWatchReplyParser = { parse };
})();
