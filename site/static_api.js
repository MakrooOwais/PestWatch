/* Serverless stand-in for the PestWatch API, used only by the static build (dist/).
   Backed by data/live_demo.js: a snapshot produced by the real server logic
   (scripts/build_static.py). Farmer replies are parsed in the browser with the same
   catalog keywords the server uses. Anything that would change detection (a confirmed
   sighting or a likely-rust photo) switches to the precomputed post-confirmation state. */
(function () {
  "use strict";
  const D = window.LIVE_DEMO, I18N = window.I18N || {};
  if (!D) return;
  const FAW = D.classes.indexOf("leaf_rust");
  const KEY = "pw_static_state";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
  const save = (s) => localStorage.setItem(KEY, JSON.stringify(s));
  const st = Object.assign({ triggered: false, lang: {}, n_live: 0, unsub: {} }, load());

  const parse = (text, lang) => window.PestWatchReplyParser.parse(text, lang);

  // ---- API ----
  const farm = (id) => D.farms.find((f) => f.id === id);
  const profileLang = (id) => st.lang[id] || (farm(id) || {}).language || "en";
  function status(id) {
    const s = st.triggered ? D.after : D.before;
    const per = (s.farms && s.farms[id]) || {};
    return {
      t_live: D.t_live, live_observations: st.n_live, clusters: s.clusters,
      alert: st.unsub[id] ? null : per.alert || null, notice: per.alert ? null : per.notice || null,
      profile: { farm: id, language: profileLang(id), channel: (farm(id) || {}).channel, subscribed: !st.unsub[id] },
      static_demo: true,
    };
  }
  const json = (obj) => Promise.resolve(JSON.parse(JSON.stringify(obj)));

  window.PestWatchStaticAPI = {
    get(path) {
      const u = new URL(path, location.href);
      if (u.pathname.endsWith("/api/farms")) {
        return json({ farms: D.farms.map((f) => ({ ...f, language: profileLang(f.id) })), classes: D.classes });
      }
      if (u.pathname.endsWith("/api/status")) return json(status(+u.searchParams.get("farm")));
      return Promise.reject(new Error("not available in the static demo"));
    },
    post(path, body) {
      if (path.endsWith("api/observations")) {
        const obs = body.observations || [];
        st.n_live += obs.length;
        if (obs.some((o) => o.probs && o.probs[FAW] >= 0.5)) st.triggered = true;
        save(st);
        return json({ accepted: obs.map((o) => o.id), ...status(obs.length ? obs[obs.length - 1].farm : 0) });
      }
      if (path.endsWith("api/inbound")) {
        const p = parse(String(body.text || ""), profileLang(body.farm));
        if (p.intent === "PEST_FOUND") { st.triggered = true; st.n_live++; }
        if (p.intent === "NO_PEST") st.n_live++;
        if (p.intent === "LANGUAGE") st.lang[body.farm] = p.newLang;
        if (p.intent === "STOP") st.unsub[body.farm] = true;
        if (p.intent === "START") delete st.unsub[body.farm];
        save(st);
        const reply = ((I18N[p.lang] || I18N.en).reply || {})[p.intent] || "";
        return json({ farm: body.farm, text: body.text, intent: p.intent, detected_lang: p.detected,
                      reply_lang: p.lang, reply, profile: status(body.farm).profile });
      }
      if (path.endsWith("api/profile")) {
        if (body.language) st.lang[body.farm] = body.language;
        save(st);
        return json(status(body.farm).profile);
      }
      return Promise.reject(new Error("not available in the static demo"));
    },
    reset() { localStorage.removeItem(KEY); },
    parse,
  };
})();
