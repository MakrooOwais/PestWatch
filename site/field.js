/* PestWatch field app: capture -> on-device classification -> offline outbox -> sync -> area alert,
   plus farmer replies in any language. All UI text comes from data/i18n.js (shared catalogs). */
(function () {
  "use strict";
  const CLASSES = ["healthy", "leaf_rust", "leaf_miner", "cercospora", "phoma"];
  // Stand-in for the on-device model. A real build loads a quantised MobileNetV3
  // (TFLite / TF.js) and returns softmax probabilities over CLASSES.
  const DEMO = {
    healthy: [0.9, 0.03, 0.03, 0.02, 0.02],
    early: [0.28, 0.5, 0.04, 0.15, 0.03],
    obvious: [0.04, 0.88, 0.02, 0.04, 0.02],
    lookalike: [0.07, 0.2, 0.65, 0.05, 0.03],
  };
  const I18N = window.I18N || {};
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const store = {
    get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
  };
  const state = {
    outbox: store.get("pw_outbox", []), simOffline: store.get("pw_sim_offline", false),
    farm: store.get("pw_farm", null), lang: store.get("pw_lang", null), probs: null, status: null,
    thread: store.get("pw_thread", []),
  };
  const online = () => navigator.onLine && !state.simOffline;
  // "Not sure — ask a person" when the top class is weak or two classes are close.
  const UNSURE_TOP = 0.6, UNSURE_MARGIN = 0.2;
  // Static build (no server): the same calls go to an in-browser stand-in (static_api.js).
  const STATIC = window.PestWatchStaticAPI || null;
  async function apiGet(path) {
    if (STATIC) return STATIC.get(path);
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

  // ---- i18n ----
  const resolveLang = (l) => { l = (l || "").toLowerCase().split("-")[0]; return I18N[l] ? l : "en"; };
  const plural = (n, lang) => (lang === "fr" ? (n <= 1 ? "one" : "other") : n === 1 ? "one" : "other");
  function tr(key, vars = {}, count) {
    for (const lg of [state.lang, I18N[state.lang] && I18N[state.lang].meta.fallback, "en"]) {
      let v = lg && I18N[lg] && I18N[lg].ui[key];
      if (v == null) continue;
      if (typeof v === "object" && count != null) { v = v[plural(count, lg)] || v.other; vars = { n: count, ...vars }; }
      return String(v).replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
    }
    return key;
  }
  const className = (c) => (I18N[state.lang] && I18N[state.lang].ui.classes[c]) || c;
  function applyLang() {
    document.documentElement.lang = state.lang;
    document.querySelectorAll("[data-i18n]").forEach((e) => { e.textContent = tr(e.dataset.i18n); });
    document.querySelectorAll("[data-i18n-ph]").forEach((e) => { e.placeholder = tr(e.dataset.i18nPh); });
    $("lang").value = state.lang;
    renderNet(); renderQueue(); renderStatus(); renderThread();
    if (state.probs) classify(false);
  }
  function buildLangPicker() {
    const sel = $("lang");
    Object.entries(I18N).forEach(([code, b]) => {
      const o = el("option", null, b.meta.native_name); o.value = code; sel.append(o);
    });
    sel.addEventListener("change", () => {
      state.lang = sel.value; store.set("pw_lang", state.lang);
      // Tell the server so SMS/voice alerts follow the farmer's choice too.
      enqueue({ type: "profile", language: state.lang });
      applyLang();
    });
  }

  function demoModel(kind) {
    const base = DEMO[kind].map((p) => Math.max(0.001, p + (Math.random() - 0.5) * 0.08));
    const s = base.reduce((a, b) => a + b, 0);
    return base.map((p) => p / s);
  }

  // ---- farms ----
  async function loadFarms() {
    let farms = store.get("pw_farms", null);
    try {
      farms = (await apiGet("api/farms")).farms;
      store.set("pw_farms", farms);
    } catch (e) { /* offline: use cached list */ }
    const sel = $("farm");
    sel.replaceChildren();
    if (!farms) { sel.append(el("option", null, tr("connect_once"))); return; }
    const sugg = farms.filter((f) => f.suggested && f.app);
    [[tr("near_signal"), sugg], [tr("all_farms"), farms]].forEach(([label, list]) => {
      if (!list.length) return;
      const g = document.createElement("optgroup"); g.label = label;
      list.forEach((f) => { const o = el("option", null, f.label); o.value = f.id; g.append(o); });
      sel.append(g);
    });
    if (state.farm == null) state.farm = (sugg[0] || farms[0]).id;
    sel.value = String(state.farm);
    // First run: default to the language registered for this farmer, else the phone's language.
    if (!store.get("pw_lang", null)) {
      const f = farms.find((x) => x.id === state.farm);
      state.lang = resolveLang((f && f.language) || navigator.language);
      store.set("pw_lang", state.lang);
    }
  }
  $("farm").addEventListener("change", (e) => {
    state.farm = +e.target.value; store.set("pw_farm", state.farm); refreshStatus();
  });

  // ---- capture & classify ----
  // Real on-device model (web/model.js, onnxruntime-web) when a photo is taken; the labelled demo
  // stub remains for the "no camera" path and as a fallback if the model can't load.
  const REAL = "mnv3s-coffee-v1", STUB = "demo-stub-v0";
  state.model = STUB;
  function setModelLabel(text) { const m = $("modelInfo"); if (m) m.textContent = text; }
  if (window.PestModel) {
    setModelLabel("On-device model: loading MobileNetV3-Small…");
    window.PestModel.load()
      .then(() => setModelLabel("On-device model: MobileNetV3-Small (real, runs offline)"))
      .catch((e) => setModelLabel(`On-device model unavailable (${e.message}); using demo stub`));
  } else {
    setModelLabel("On-device model: demo stub");
  }
  $("photo").addEventListener("change", async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const img = el("img"); img.alt = "Crop photo"; img.src = URL.createObjectURL(f);
    $("captureBox").replaceChildren(img);
    if (window.PestModel) {
      try {
        const r = await window.PestModel.classify(f);
        state.probs = r.probs; state.model = REAL;
        setModelLabel(`On-device model: MobileNetV3-Small (real) · ${r.ms} ms`);
        classify(false);
        return;
      } catch (err) {
        setModelLabel(`Model failed (${err.message}); demo stub result shown`);
      }
    }
    classify(true);
  });
  $("demo").addEventListener("change", () => { if (state.probs && state.model === STUB) classify(true); });
  $("nophoto").addEventListener("click", () => { state.model = STUB; setModelLabel("Demo stub (no photo)"); classify(true); });
  function classify(fresh) {
    if (fresh) { state.probs = demoModel($("demo").value); state.model = STUB; }
    const top = state.probs.indexOf(Math.max(...state.probs)), pf = state.probs[1];
    $("verdict").textContent = `${className(CLASSES[top])} · ${Math.round(state.probs[top] * 100)}%`;
    const bars = $("bars"); bars.replaceChildren();
    CLASSES.forEach((c, i) => {
      const row = el("div", "bar"), track = el("div", "track"), fill = el("i");
      fill.style.width = `${Math.round(state.probs[i] * 100)}%`; track.append(fill);
      row.append(el("span", null, className(c)), track, el("b", null, `${Math.round(state.probs[i] * 100)}%`));
      bars.append(row);
    });
    $("explain").textContent = tr(pf >= 0.6 ? "likely" : pf >= 0.3 ? "possible" : "unlikely");
    // Fail-safe (human in the loop): when the model can't give a clear answer, say so and
    // route to a person instead of guessing.
    const sorted = [...state.probs].sort((a, b) => b - a);
    const unsure = sorted[0] < UNSURE_TOP || sorted[0] - sorted[1] < UNSURE_MARGIN || (pf >= 0.3 && pf < 0.6);
    $("notSure").hidden = !unsure;
    $("askPerson").disabled = false;
    $("resultCard").hidden = false;
  }
  $("save").addEventListener("click", () => {
    if (!state.probs || state.farm == null) return;
    enqueue({ type: "obs", captured_at: new Date().toISOString(), probs: state.probs, model: state.model });
    state.probs = null; $("resultCard").hidden = true;
    $("captureBox").replaceChildren(el("span", null, `📷 ${tr("tap_again")}`));
  });

  $("askPerson").addEventListener("click", () => {
    sendText(tr("referral_msg"));
    $("askPerson").disabled = true;
  });

  // ---- replies (any language) ----
  function sendText(text) {
    text = text.trim();
    if (!text || state.farm == null) return;
    const item = enqueue({ type: "msg", text });
    state.thread.push({ id: item.id, me: true, text, at: item.created });
    // Offline: answer on the phone right away (same keywords as the server); the server's
    // reply replaces this one when the message syncs.
    if (!online() && window.PestWatchReplyParser) {
      const p = window.PestWatchReplyParser.parse(text, state.lang);
      const lg = I18N[p.lang] ? p.lang : state.lang;
      const reply = ((I18N[lg] || I18N.en).reply || {})[p.intent] || "";
      state.thread.push({ forId: item.id, me: false, local: true, intent: p.intent, detected: p.detected,
                          text: `${reply} ${tr("offline_reply")}` });
      if (p.intent === "LANGUAGE" && p.newLang) { state.lang = resolveLang(p.newLang); store.set("pw_lang", state.lang); applyLang(); }
    }
    saveThread();
  }
  $("found").addEventListener("click", () => sendText("1"));
  $("none").addEventListener("click", () => sendText("2"));
  $("send").addEventListener("click", () => { sendText($("msg").value); $("msg").value = ""; });
  $("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") { sendText($("msg").value); $("msg").value = ""; } });
  function saveThread() { state.thread = state.thread.slice(-20); store.set("pw_thread", state.thread); renderThread(); }
  function renderThread() {
    const ul = $("thread"); ul.replaceChildren();
    state.thread.forEach((m) => {
      const li = el("li", m.me ? "me" : "pw", m.text);
      const pending = m.me && state.outbox.some((o) => o.id === m.id && !o.synced);
      if (pending) li.append(el("small", null, `⏳ ${tr("queued")}`));
      if (m.intent) li.append(el("small", null, `${m.intent}${m.detected ? ` · ${m.detected}` : ""}`));
      ul.append(li);
    });
  }

  // ---- outbox & sync (photos, messages and profile changes all wait for signal) ----
  function enqueue(item) {
    const it = { id: uid(), farm: state.farm, created: new Date().toISOString(), synced: false, ...item };
    state.outbox.push(it);
    store.set("pw_outbox", state.outbox);
    renderQueue(); sync();
    return it;
  }
  function renderQueue() {
    const ul = $("queue"); ul.replaceChildren();
    const obs = state.outbox.filter((o) => o.type === "obs");
    obs.slice(-8).reverse().forEach((o) => {
      const li = el("li"), top = o.probs.indexOf(Math.max(...o.probs));
      li.append(el("span", null, `${new Date(o.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${className(CLASSES[top])} ${Math.round(o.probs[top] * 100)}%`),
        el("span", `st ${o.synced ? "s" : "q"}`, o.synced ? `✓ ${tr("synced")}` : `⏳ ${tr("queued")}`));
      ul.append(li);
    });
    const pending = state.outbox.filter((o) => !o.synced).length;
    $("qnote").textContent = !obs.length && !pending ? tr("no_reports")
      : pending ? `${tr("pending", {}, pending)}${online() ? "" : " " + tr("auto_sync")}` : tr("all_synced");
    $("sync").disabled = !pending;
  }
  async function post(path, body) {
    if (STATIC) return STATIC.post(path, body);
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const res = await r.json();
    if (!r.ok) throw new Error(res.error || r.status);
    return res;
  }
  let syncing = false;
  async function sync() {
    if (syncing || !online()) { renderQueue(); return; }
    syncing = true;
    try {
      const obs = state.outbox.filter((o) => !o.synced && o.type === "obs");
      if (obs.length) {
        const res = await post("api/observations", {
          observations: obs.map((o) => ({ id: o.id, farm: o.farm, captured_at: o.captured_at, probs: o.probs, model: o.model })),
        });
        const ok = new Set(res.accepted);
        obs.forEach((o) => { if (ok.has(o.id)) o.synced = true; });
      }
      for (const o of state.outbox.filter((x) => !x.synced && x.type !== "obs")) {
        if (o.type === "profile") {
          await post("api/profile", { farm: o.farm, language: o.language });
        } else {
          const res = await post("api/inbound", { farm: o.farm, text: o.text, channel: "app" });
          const msg = { me: false, text: res.reply, intent: res.intent, detected: res.detected_lang };
          const localIdx = state.thread.findIndex((m) => m.forId === o.id);
          if (localIdx >= 0) state.thread[localIdx] = msg; else state.thread.push(msg);
          // A "change language" message also switches the app.
          if (res.intent === "LANGUAGE" && res.profile) { state.lang = resolveLang(res.profile.language); store.set("pw_lang", state.lang); }
          saveThread();
        }
        o.synced = true;
      }
      store.set("pw_outbox", state.outbox.slice(-200));
      await refreshStatus();
      applyLang();
    } catch (e) {
      $("qnote").textContent = `${tr("sync_failed")} (${e.message})`;
    } finally {
      syncing = false;
    }
    renderQueue();
  }
  $("sync").addEventListener("click", sync);
  window.addEventListener("online", sync);
  window.addEventListener("offline", () => { renderNet(); renderQueue(); renderStatus(); });
  $("net").addEventListener("click", () => {
    state.simOffline = !state.simOffline; store.set("pw_sim_offline", state.simOffline);
    renderNet(); renderQueue(); renderThread(); if (online()) sync();
  });
  function renderNet() {
    const b = $("net");
    b.className = `pill ${online() ? "on" : "off"}`;
    b.textContent = online() ? `● ${tr("online")}` : `○ ${tr("offline")}`;
  }

  // ---- area status ----
  async function refreshStatus() {
    let st = store.get(`pw_status_${state.farm}`, null);
    if (online() || STATIC) {   // the static build's "server" is on the device, so it works offline too
      try {
        st = await apiGet(`api/status?farm=${state.farm}`);
        st._at = Date.now();
        store.set(`pw_status_${state.farm}`, st);
      } catch (e) { /* keep cached */ }
    }
    state.status = st;
    renderStatus();
  }
  function renderStatus() {
    renderStatusInner();
    const st = state.status;
    if (st && !online()) {
      // Offline: say how old the saved status is.
      const when = st._at ? new Date(st._at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "?";
      $("status").append(el("p", "muted", `📴 ${tr("last_updated", { when })}`));
    }
  }
  function renderStatusInner() {
    const st = state.status, box = $("status"), card = $("statusCard");
    box.replaceChildren(); card.classList.remove("alert");
    if (!st) { box.textContent = tr("no_status"); return; }
    if (st.alert) {
      const a = st.alert, lvl = I18N[state.lang] ? I18N[state.lang].levels[a.risk] : a.risk;
      card.classList.add("alert");
      box.append(el("span", `badge ${a.risk}`, `▲ ${tr("risk_badge", { level: lvl })}`));
      box.append(el("p", null, a.msg[state.lang] || a.msg.en));
      return;
    }
    if (st.notice && st.notice.kind === "scout_request") {
      // Targeted scouting: PestWatch asks this farmer to check plants and reply (section 5).
      box.append(el("span", "badge WATCH", `◆ ${tr("scout_title")}`));
      box.append(el("p", null, st.notice.msg[state.lang] || st.notice.msg.en));
      return;
    }
    const c = st.clusters[0];
    if (!c) {
      box.append(el("span", "badge QUIET", `● ${tr("quiet")}`), el("p", null, tr("quiet_text")));
    } else {
      const alert = c.level === "ALERT";
      box.append(el("span", `badge ${alert ? "HIGH" : "WATCH"}`, alert ? tr("alert_elsewhere") : `◆ ${tr("watch")}`));
      box.append(el("p", null, tr(alert ? "outbreak_near" : "emerging", { village: c.village, r: c.n_reports, f: c.n_farms })));
    }
    box.append(el("p", "muted", tr("live_count", { n: st.live_observations })));
  }

  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  if (STATIC) {
    // Make it obvious this is the serverless demo build.
    const note = el("p", "note", "Static demo: no server. Replies and area status are simulated in your browser from a snapshot of the live system.");
    document.querySelector("main").prepend(note);
  }
  buildLangPicker();
  // Demo links: field.html?lang=sw&farm=81
  const q = new URLSearchParams(location.search);
  if (q.get("lang")) store.set("pw_lang", (state.lang = resolveLang(q.get("lang"))));
  if (q.get("farm")) store.set("pw_farm", (state.farm = +q.get("farm") - 1));
  state.lang = resolveLang(state.lang || navigator.language);
  applyLang();
  loadFarms().then(() => { applyLang(); refreshStatus(); sync(); });
})();
