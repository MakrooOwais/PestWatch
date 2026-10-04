/* PestWatch dashboard: replays a simulated season exported by scripts/run_demo.py. */
(function () {
  "use strict";
  const S = window.SCENARIO;
  if (!S || !window.PW) {
    document.body.textContent = "No scenario found. Run: python3 scripts/run_demo.py";
    return;
  }
  const { C, RISK_COLOR, RISK_ICON, SRC, el, hhmm, fmtDay, mix, pColor, sizeCanvas, haloText, drawTrap } = window.PW;
  const X = window.PW.build(S);
  const { M, N, F, stepOf, villageOf, farmLabel, alertsByFarm, reportsByFarm, noticesByFarm, latest } = X;

  const CLASS_NAME = (window.I18N && window.I18N.en && window.I18N.en.ui.classes) || {};
  const damageColor = (d) => (d <= 0 ? C.farmOff : mix("#7a5a4a", "#e66767", Math.min(1, Math.sqrt(d / 0.5))));
  const pct = (v) => `${Math.round(v * 100)}%`;

  // ---------- derived data ----------
  const m = S.metrics;
  const key = {
    intro: m.t_intro, alert: m.t_first_alert, notice: m.t_first_farmer_notice,
    conventional: m.t_conventional, widespread: m.t_widespread,
  };
  const visShare = (arr) => arr.map((row) => row.filter((d) => d >= M.visible_threshold).length / F);
  const infShare = (arr) => arr.map((row) => row.filter((d) => d > 0).length / F);
  const series = {
    visPw: visShare(S.damage_pw), visCf: visShare(S.damage_cf),
    infPw: infShare(S.damage_pw), infCf: infShare(S.damage_cf),
  };
  const batches = {};
  S.alerts.forEach((a) => { (batches[a.t] = batches[a.t] || []).push(a); });
  const batchTimes = Object.keys(batches).map(Number).sort((a, b) => a - b);
  const firstAlertBatch = batchTimes.find((t) => t >= (key.intro ?? -1));
  const firstReportBatch = batchTimes.find((t) => t >= (key.intro ?? -1) && batches[t].some((a) => a.basis !== "traps"));
  let firstWatch = null, firstAlertEvent = null;
  S.clusters.forEach((evs, i) => evs.forEach((e) => {
    if (S.times[i] < (key.intro ?? 0)) return;
    if (!firstWatch) firstWatch = { t: S.times[i], ...e };
    if (e.lvl === "ALERT" && !firstAlertEvent) firstAlertEvent = { t: S.times[i], ...e };
  }));

  // Default phone: first HIGH-risk farm warned before the pest reached it.
  const firstHigh = S.alerts.filter((a) => a.risk === "HIGH");
  const warnedEarly = firstHigh.find((a) => S.farms[a.farm].infected_pw == null || S.farms[a.farm].infected_pw > a.t);
  const I18N = window.I18N || {};
  const LANGS = S.languages || [{ code: "en", name: "English" }];
  const langName = (c) => (LANGS.find((l) => l.code === c) || { name: c }).name;
  const CHAN_NAME = { app: "app notification", sms: "SMS", voice: "voice call" };
  const profileOf = (f) => (S.profiles ? S.profiles[f] : { language: "en", channel: S.farms[f].app ? "app" : "sms" });
  const lead = new Set(S.lead_farmers || []), spam = new Set(S.spammers || []);
  const state = {
    step: 0, t: 0, playing: false, speed: 2, layer: "system", arm: "pw", lang: null, channel: null,
    selected: (warnedEarly || firstHigh[0] || { farm: 0 }).farm, story: null, hover: null,
  };

  // ---------- events (feed) ----------
  const events = [];
  const isReply = (r) => r.src === SRC.SCOUT || r.src === SRC.INSPECT;
  S.reports.forEach((r) => {
    const late = (r.ts - r.tc) * 24;
    const sync = late > 6 ? ` · synced ${Math.round(late)} h late (offline)` : "";
    if (isReply(r)) {
      const found = r.p >= 0.5;
      const asked = latest(noticesByFarm[r.f].filter((n) => n.kind === "scout_request"), r.tc);
      const ctx = r.src === SRC.SCOUT
        ? `scouting reply${asked ? `, ${Math.round((r.tc - asked.t) * 24)} h after the request` : ""}`
        : "inspection after alert";
      events.push({ t: r.ts, cls: found ? "alert" : "", tag: found ? "FOUND" : "NONE",
        text: `${farmLabel(r.f)}: ${found ? "found rust" : "checked 10 trees, none found"} (${ctx})${sync}` });
      return;
    }
    if (r.p < 0.35) return;
    const weak = r.p < 0.6 ? " — too weak to act on alone" : "";
    const who = r.src === SRC.LEAD ? " (lead-farmer round)" : "";
    events.push({ t: r.ts, cls: "", text: `${farmLabel(r.f)}: possible leaf rust (${pct(r.p)})${who}${weak}${sync}` });
  });
  const seenCluster = {};
  S.clusters.forEach((evs, i) => evs.forEach((e) => {
    const k = `${e.id}:${e.lvl}:${e.trap}`;
    if (seenCluster[k]) return;
    seenCluster[k] = true;
    const where = `${e.r} km around ${villageOf(e.c)}`;
    const prob = e.p != null ? ` · P(outbreak) ${pct(e.p)}` : "";
    if (e.trap) {
      events.push({ t: S.times[i], cls: "", tag: "WATCH", text: `Trap catch spike near ${villageOf(e.c)} (z ${e.tz.toFixed(1)}): watch zone opened, no photo cluster yet` });
    } else if (e.lvl === "ALERT") {
      events.push({ t: S.times[i], cls: "alert", tag: "ALERT", text: `Outbreak cluster ${where}: ${e.n} reports from ${e.nf} farms in 72 h, ${e.nconf || 0} farmer confirmations${prob}` });
    } else {
      events.push({ t: S.times[i], cls: "", tag: "WATCH", text: `Emerging signal ${where}: ${e.n} reports / ${e.nf} farms${prob} — below alert threshold` });
    }
  }));
  batchTimes.forEach((t) => {
    const as = batches[t], c = { HIGH: 0, MEDIUM: 0, LOW: 0 };
    as.forEach((a) => c[a.risk]++);
    const parts = Object.entries(c).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`);
    const traps = as.every((a) => a.basis === "traps");
    events.push({ t, cls: "alert", tag: traps ? "FORECAST" : null,
      text: traps ? `Forecast alerts (trap-based) sent to ${as.length} farms (${parts.join(" · ")})`
        : `Alerts sent to ${as.length} farms (${parts.join(" · ")})` });
  });
  (S.scout_requests || []).forEach((r) => {
    const trapLed = (S.clusters[stepOf(r.t)] || []).some((e) => e.id === r.cid && e.trap);
    events.push({ t: r.t, cls: "", tag: "SCOUT", text: `Asked ${r.farms.length} farmers near ${villageOf(r.c)} to check 10 plants${trapLed ? " (trap catch spike)" : " (weak photo signal)"}` });
  });
  const clears = {};
  X.notices.filter((n) => n.kind === "all_clear").forEach((n) => { (clears[n.t] = clears[n.t] || []).push(n); });
  Object.entries(clears).forEach(([t, ns]) => events.push({ t: +t, cls: "", tag: "CLEAR", text: `All-clear sent to ${ns.length} farms: no new reports for ${ns[0].days} days` }));
  (S.triggers || []).forEach((tr) => events.push({ t: tr.t, cls: "alert", tag: "FUND",
    text: `Anticipatory-finance trigger: P(outbreak) ${pct(tr.probability)}, ${tr.farms_at_risk} farms / ${tr.ha_at_risk.toFixed(0)} ha at risk → $${Math.round(tr.payout_usd).toLocaleString()} released for control inputs` }));
  // Trap spikes (once per trap per 3 days).
  const lastSpike = {};
  (S.trap_readings || []).slice().sort((a, b) => a.ts - b.ts).forEach((r) => {
    const z = (r.n - X.baseline[r.k]) / Math.sqrt(X.baseline[r.k]);
    if (z < 4 || (lastSpike[r.k] != null && r.ts - lastSpike[r.k] < 3)) return;
    lastSpike[r.k] = r.ts;
    events.push({ t: r.ts, cls: "", tag: "TRAP", text: `Trap at ${S.villages[S.traps[r.k].village].name}: ${r.n} caught (usual ~${X.baseline[r.k].toFixed(1)}/day)` });
  });
  // Migration forecast rising.
  if (S.env) {
    let high = false;
    S.env.migration.forEach((v, d) => {
      if (v >= 0.5 && !high) events.push({ t: d, cls: "", tag: "FORECAST", text: `Rust weather risk high (index ${v.toFixed(2)}): warm, wet conditions favour infection` });
      high = v >= 0.5;
    });
  }
  S.nuisance.forEach((n) => {
    const v = S.villages[n.village].name;
    events.push({
      t: n.start, cls: "", tag: "NOISE",
      text: n.kind === "engagement_surge"
        ? `Extension training day in ${v}: photo volume ×${n.multiplier} for ${Math.round(n.end - n.start)} days (not an outbreak)`
        : `Grasshopper / leaf beetle flare-up in ${v}: look-alike damage ×${n.multiplier} for ${Math.round(n.end - n.start)} days (not an outbreak)`,
    });
  });
  (S.landings || []).forEach((L) => {
    const near = L.farms.length ? villageOf(L.farms[0]) : "the region";
    events.push({ t: L.t, truth: true, tag: "TRUTH", cls: "", text: `Rust spores arrive near ${near} after rain: ${L.farms.length} farms get first infections. Nobody can see it yet.` });
  });
  if (key.notice != null) events.push({ t: key.notice, truth: true, tag: "TRUTH", cls: "", text: "Without PestWatch: first farmer notices damage on their own" });
  events.sort((a, b) => a.t - b.t);

  // ---------- map ----------
  const map = document.getElementById("map"), mctx = map.getContext("2d");
  let proj = null;
  function layoutMap() {
    const { w, h } = sizeCanvas(map), pad = 26;
    const s = Math.min((w - 2 * pad) / M.size_km, (h - 2 * pad) / M.size_km);
    const ox = (w - s * M.size_km) / 2, oy = (h - s * M.size_km) / 2;
    proj = { s, w, h, ox, oy, x: (x) => ox + x * s, y: (y) => oy + (M.size_km - y) * s };
  }
  function jitter(i) {
    const a = ((i * 2654435761) % 1000) / 1000 * Math.PI * 2, r = 3 + ((i * 40503) % 7);
    return [Math.cos(a) * r, Math.sin(a) * r];
  }
  function drawWind(ctx, P, t) {
    const w = X.windAt(t);
    if (!w) return;
    // Top-right of the canvas, outside the (square) map when there is room.
    const cx = Math.min(Math.max(P.ox + P.s * M.size_km + 40, P.w - 44), P.w - 70), cy = 44, len = 18;
    ctx.beginPath(); ctx.arc(cx, cy, 24, 0, 7); ctx.fillStyle = "rgba(18,18,17,.85)"; ctx.fill();
    ctx.strokeStyle = C.axis; ctx.lineWidth = 1; ctx.stroke();
    const dx = Math.cos(w.dir), dy = -Math.sin(w.dir);
    ctx.strokeStyle = C.text; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx - dx * len, cy - dy * len); ctx.lineTo(cx + dx * len, cy + dy * len); ctx.stroke();
    const hx = cx + dx * len, hy = cy + dy * len, a = Math.atan2(dy, dx);
    ctx.beginPath(); ctx.moveTo(hx, hy);
    ctx.lineTo(hx - 8 * Math.cos(a - 0.45), hy - 8 * Math.sin(a - 0.45));
    ctx.lineTo(hx - 8 * Math.cos(a + 0.45), hy - 8 * Math.sin(a + 0.45));
    ctx.closePath(); ctx.fillStyle = C.text; ctx.fill();
    ctx.textAlign = "center"; ctx.font = "11px system-ui"; ctx.fillStyle = C.muted;
    ctx.fillText(`wind ${w.speed.toFixed(1)} m/s · ${w.temp.toFixed(0)}°C`, cx, cy + 38);
  }
  function drawMap() {
    const ctx = mctx, P = proj, t = state.t, i = state.step;
    const sys = state.layer === "system" && state.arm === "pw";
    const dmg = state.arm === "pw" ? S.damage_pw[i] : S.damage_cf[i];
    ctx.clearRect(0, 0, P.w, P.h);
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    for (let k = 0; k <= M.size_km; k += 2) {
      ctx.beginPath(); ctx.moveTo(P.x(k), P.y(0)); ctx.lineTo(P.x(k), P.y(M.size_km)); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(P.x(0), P.y(k)); ctx.lineTo(P.x(M.size_km), P.y(k)); ctx.stroke();
    }
    ctx.fillStyle = C.muted; ctx.font = "11px system-ui"; ctx.textAlign = "left";
    ctx.fillRect(P.x(0.3), P.y(0.35), 2 * P.s, 2);
    ctx.fillText("2 km", P.x(0.3), P.y(0.35) - 5);

    // Ground truth: landing areas.
    if (!sys) {
      (S.landings || []).forEach((L) => {
        if (L.t > t) return;
        ctx.beginPath(); ctx.arc(P.x(L.x), P.y(L.y), 1.4 * P.s, 0, 7);
        ctx.setLineDash([3, 4]); ctx.strokeStyle = C.muted; ctx.lineWidth = 1.2; ctx.stroke(); ctx.setLineDash([]);
        ctx.textAlign = "center";
        haloText(ctx, `rust arrives · day ${L.t.toFixed(0)}`, P.x(L.x), P.y(L.y) + 1.4 * P.s + 14, C.muted, "600 11px system-ui");
      });
    }

    // Clusters (behind farms).
    if (sys) {
      S.clusters[i].forEach((e) => {
        const f = S.farms[e.c], cx = P.x(f.x), cy = P.y(f.y), r = e.r * P.s;
        if (e.lvl === "ALERT") {
          ctx.beginPath(); ctx.arc(cx, cy, (e.r + M.buffer_km) * P.s, 0, 7);
          ctx.strokeStyle = "rgba(208,59,59,.35)"; ctx.lineWidth = 1; ctx.stroke();
          ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7);
          ctx.fillStyle = "rgba(208,59,59,.13)"; ctx.fill();
          ctx.strokeStyle = C.critical; ctx.lineWidth = 2; ctx.stroke();
        } else {
          ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7);
          ctx.setLineDash([5, 4]); ctx.strokeStyle = C.warning; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]);
        }
        const basisTraps = e.lvl === "ALERT" && S.alerts.some((a) => a.cluster_id === e.id && a.basis === "traps" && Math.abs(a.t - S.times[i]) < 1e-6);
        const prob = e.p != null ? ` · P ${pct(e.p)}` : "";
        const label = e.trap ? "WATCH · trap catch spike"
          : basisTraps ? `ALERT · forecast${prob}`
            : `${e.lvl} · ${e.n} reports · ${e.nf} farms${prob}`;
        ctx.textAlign = "center";
        haloText(ctx, label, cx, cy + r + 14, e.lvl === "ALERT" ? "#ff8a8a" : C.warning, "600 11px system-ui");
      });
    }

    // Farms.
    S.farms.forEach((f, k) => {
      const x = P.x(f.x), y = P.y(f.y);
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 7);
      if (sys) {
        if (f.app) { ctx.fillStyle = C.farm; ctx.fill(); }
        else { ctx.strokeStyle = C.farm; ctx.lineWidth = 1.2; ctx.stroke(); }
      } else {
        ctx.fillStyle = damageColor(dmg[k]); ctx.fill();
        const tr = state.arm === "pw" ? f.treated_pw : f.treated_cf;
        if (tr != null && tr <= t) {
          ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.strokeStyle = C.good; ctx.lineWidth = 1.5; ctx.stroke();
        }
      }
    });

    if (sys) {
      // Synced reports from the last 72 h, and offline queue per farm.
      const queued = new Array(F).fill(0);
      for (const r of S.reports) {
        if (r.tc > t) break;
        if (r.ts > t) { queued[r.f]++; continue; }
        if (r.tc < t - 3) continue;
        const f = S.farms[r.f], [jx, jy] = jitter(r.i), age = (t - r.tc) / 3;
        const x = P.x(f.x) + jx, y = P.y(f.y) + jy;
        ctx.globalAlpha = 1 - 0.6 * age;
        if (isReply(r)) {
          if (r.p >= 0.5) {   // found rust: filled triangle
            ctx.beginPath(); ctx.moveTo(x, y - 5); ctx.lineTo(x + 4.5, y + 3.5); ctx.lineTo(x - 4.5, y + 3.5); ctx.closePath();
            ctx.fillStyle = "#e66767"; ctx.fill(); ctx.strokeStyle = C.surface; ctx.lineWidth = 1; ctx.stroke();
          } else {            // checked, none found: hollow aqua circle
            ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 7); ctx.strokeStyle = C.s3; ctx.lineWidth = 1.5; ctx.stroke();
          }
        } else {
          ctx.beginPath(); ctx.arc(x, y, r.p >= 0.3 ? 3.2 : 1.8, 0, 7);
          ctx.fillStyle = pColor(r.p); ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = C.text; ctx.lineWidth = 1.2;
      queued.forEach((q, k) => {
        if (!q) return;
        const f = S.farms[k];
        ctx.strokeRect(P.x(f.x) + 6, P.y(f.y) - 11, 5, 5);
      });
      // Scouting requests in the last day: dashed aqua ring.
      X.notices.forEach((n) => {
        if (n.kind !== "scout_request" || n.t > t || t - n.t > 1) return;
        const f = S.farms[n.f];
        ctx.beginPath(); ctx.arc(P.x(f.x), P.y(f.y), 9.5, 0, 7);
        ctx.setLineDash([2, 2]); ctx.strokeStyle = C.s3; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]);
      });
      // Farms alerted in the last 3 days, ringed by risk tier.
      S.farms.forEach((f, k) => {
        const a = latest(alertsByFarm[k], t);
        if (!a || t - a.t > 3) return;
        ctx.beginPath(); ctx.arc(P.x(f.x), P.y(f.y), 8, 0, 7);
        ctx.strokeStyle = RISK_COLOR[a.risk]; ctx.lineWidth = 2; ctx.stroke();
      });
    } else {
      // Ground truth: lead farmers and spammers, labelled.
      ctx.textAlign = "left";
      S.farms.forEach((f, k) => {
        if (lead.has(k)) haloText(ctx, "L", P.x(f.x) + 6, P.y(f.y) + 4, C.s3, "700 10px system-ui");
        if (spam.has(k)) haloText(ctx, "spam", P.x(f.x) + 6, P.y(f.y) + 4, "#ff8a8a", "700 10px system-ui");
      });
    }

    // Pheromone traps (both layers): diamond + latest catch.
    X.traps.forEach((tp, k) => {
      const now = X.trapNow(k, t), anomalous = now.z >= 3;
      const x = P.x(tp.x), y = P.y(tp.y);
      drawTrap(ctx, x, y, anomalous);
      if (now.last && (now.last.n > 0 || anomalous)) {
        ctx.textAlign = "left";
        haloText(ctx, `${anomalous ? "▲ " : ""}${now.last.n} caught`, x + 9, y + 4, anomalous ? C.warning : C.muted, "600 10px system-ui");
      }
    });

    // Village labels on top, with a surface halo.
    ctx.textAlign = "center";
    S.villages.forEach((v) => haloText(ctx, v.name.toUpperCase(), P.x(v.x), P.y(v.y) - 1.6 * P.s, C.text, "600 12px system-ui"));

    drawWind(ctx, P, t);
    ctx.textAlign = "left";
    const sf = S.farms[state.selected];
    ctx.beginPath(); ctx.arc(P.x(sf.x), P.y(sf.y), 11, 0, 7);
    ctx.strokeStyle = C.ink; ctx.lineWidth = 1.5; ctx.stroke();
  }
  function nearestFarm(px, py) {
    let best = -1, bd = 14 * 14;
    S.farms.forEach((f, k) => {
      const d = (proj.x(f.x) - px) ** 2 + (proj.y(f.y) - py) ** 2;
      if (d < bd) { bd = d; best = k; }
    });
    return best;
  }
  map.addEventListener("click", (ev) => {
    const r = map.getBoundingClientRect(), k = nearestFarm(ev.clientX - r.left, ev.clientY - r.top);
    if (k >= 0) { state.selected = k; state.lang = null; state.channel = null; render(); }
  });
  const maptip = document.getElementById("maptip");
  map.addEventListener("pointermove", (ev) => {
    const r = map.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top, k = nearestFarm(x, y);
    if (k < 0) { maptip.hidden = true; return; }
    const f = S.farms[k], d = (state.arm === "pw" ? S.damage_pw : S.damage_cf)[state.step][k];
    const prof = profileOf(k);
    maptip.replaceChildren(el("div", "tt-h", farmLabel(k)));
    const rows = [
      [f.app ? "uses app" : "feature phone", `${f.ha} ha · ${langName(prof.language)} · ${prof.channel}`],
      [`${reportsByFarm[k].filter((q) => q.ts <= state.t).length}`, "reports synced"],
    ];
    if (lead.has(k)) rows.push(["lead farmer", "weekly 10-tree round"]);
    if (state.layer === "truth" || state.arm === "cf") {
      rows.push([pct(d), "plants damaged (truth)"]);
      if (spam.has(k)) rows.push(["spammer", "sends bogus reports (truth)"]);
    }
    const a = state.arm === "pw" ? latest(alertsByFarm[k], state.t) : null;
    if (a) rows.push([a.risk, `alert ${hhmm(a.t)}${a.basis === "traps" ? " (forecast)" : ""}`]);
    rows.forEach(([v, l]) => { const row = el("div", "row"); row.append(el("b", null, v), el("span", null, l)); maptip.append(row); });
    maptip.hidden = false;
    maptip.style.left = `${Math.min(x + 14, r.width - 220)}px`; maptip.style.top = `${y + 14}px`;
  });
  map.addEventListener("pointerleave", () => { maptip.hidden = true; });

  function renderLegend() {
    const lg = document.getElementById("legend");
    lg.replaceChildren();
    const add = (iCls, style, label) => {
      const k = el("span", "key"), i = el("i", iCls);
      Object.assign(i.style, style);
      k.append(i, document.createTextNode(label)); lg.append(k);
    };
    if (state.layer === "system" && state.arm === "pw") {
      document.getElementById("maptitle").textContent = "What PestWatch sees";
      add("dot", { background: "#6b6a64" }, "photo: healthy/other");
      add("dot", { background: "#c98500" }, "possible rust");
      add("dot", { background: "#e66767" }, "likely rust");
      add("tri", {}, "scout: found");
      add("ring", { borderColor: C.s3 }, "scout: none / asked");
      if (X.traps.length) add("diamond", {}, "trap");
      add("sq", {}, "queued offline");
      add("ring", { borderColor: C.critical }, "alerted HIGH");
      add("ring", { borderColor: C.serious }, "MEDIUM");
      add("ring", { borderColor: C.warning }, "LOW");
    } else {
      document.getElementById("maptitle").textContent =
        `Ground truth: crop damage ${state.arm === "pw" ? "with" : "without"} PestWatch`;
      add("ramp", {}, "share of plants damaged");
      add("dot", { background: C.farmOff }, "not infested");
      add("ring", { borderColor: C.good }, "treated");
      if (X.traps.length) add("diamond", {}, "trap");
      add("txt", {}, "L = lead farmer");
    }
  }

  // ---------- phone ----------
  const phone = document.getElementById("phone");
  function renderPhone() {
    const f = state.selected, farm = S.farms[f], t = state.t;
    phone.replaceChildren();
    const head = el("div", "apphead");
    head.append(el("span", null, "PestWatch"), el("span", null, `${farmLabel(f)} · ${hhmm(t)}`));
    phone.append(head);
    const prof = profileOf(f);
    const lang = state.lang || prof.language, chan = state.channel || prof.channel;
    const pref = document.getElementById("pref");
    pref.replaceChildren(document.createTextNode("Prefers "), el("b", null, `${langName(prof.language)} · ${CHAN_NAME[prof.channel]}`));
    if (lang !== prof.language || chan !== prof.channel) {
      pref.append(el("span", "preview", ` — previewing ${langName(lang)} · ${CHAN_NAME[chan]}`));
    }
    document.querySelectorAll("[data-lang]").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
    document.querySelectorAll("[data-chan]").forEach((b) => b.classList.toggle("on", b.dataset.chan === chan));
    const pw = state.arm === "pw";
    const a = pw ? latest(alertsByFarm[f], t) : null;
    const n = pw ? latest(noticesByFarm[f], t) : null;
    if (n && (!a || n.t > a.t)) {
      renderNotice(n, lang, chan);
    } else if (a) {
      renderAlert(a, lang, chan);
    } else {
      phone.append(el("p", "muted", pw ? "No pest alerts for your area." :
        "Without PestWatch there is no early warning: farmers find out when damage becomes visible."));
    }
    if (!farm.app) {
      phone.append(el("div", "det", `Feature-phone farmer: messages arrive by ${CHAN_NAME[prof.channel]} in ${langName(prof.language)}.`));
      return;
    }
    const last = latest(reportsByFarm[f].filter((r) => r.src === SRC.PHOTO || r.src === SRC.LEAD), t, "tc");
    if (!last || !pw) return;
    const d = el("div", "det");
    const top = M.classes[last.top];
    d.append(el("div", null, `Last photo ${hhmm(last.tc)} · on-device model`));
    d.append(el("b", null, `${CLASS_NAME[top] || top} ${pct(last.conf)}`));
    const bar = el("div", "bar"), fill = el("i");
    fill.style.width = pct(last.p); bar.append(fill);
    d.append(el("div", null, `P(leaf rust) ${pct(last.p)}`), bar);
    d.append(el("div", "muted", last.ts > t ? "⏳ Queued offline — will sync when connected" : "✓ Synced"));
    phone.append(d);
  }
  function renderAlert(a, lang, chan) {
    const asDelivered = a.delivery && a.delivery.language === lang && a.delivery.channel === chan;
    if (chan === "sms") {
      const txt = asDelivered ? a.delivery.text : a.sms[lang];
      phone.append(el("div", "sms", txt));
      phone.append(el("div", "meta", `${txt.length} chars${asDelivered ? ` · ${a.delivery.encoding} · ${a.delivery.segments} SMS` : ""}`));
    } else if (chan === "voice") {
      const v = el("div", "voice");
      v.append(el("div", "call", "📞 PestWatch is calling…"), el("p", null, a.voice[lang]));
      phone.append(v, el("div", "meta", "Text-to-speech script · keypad replies 1 / 2 / 9"));
    } else {
      const n = el("div", "notif"), b = el("span", `badge ${a.risk}`);
      const lvl = (I18N[lang] && I18N[lang].levels && I18N[lang].levels[a.risk]) || a.risk;
      b.textContent = `${RISK_ICON[a.risk]} ${lvl}`;
      n.append(b, el("p", null, asDelivered ? a.delivery.text : a.msg[lang]));
      phone.append(n);
    }
    phone.append(el("p", "muted", `Received ${hhmm(a.t)}${a.basis === "traps" ? " · forecast-based" : ""}`));
  }
  function renderNotice(n, lang, chan) {
    const asDelivered = n.delivery && n.delivery.language === lang && n.delivery.channel === chan;
    const txt = asDelivered ? n.delivery.text
      : ((S.notice_text && S.notice_text[n.kind] && S.notice_text[n.kind][lang] && S.notice_text[n.kind][lang][chan]) || "");
    const isClear = n.kind === "all_clear";
    if (chan === "sms") {
      phone.append(el("div", "sms", txt), el("div", "meta", `${txt.length} chars`));
    } else if (chan === "voice") {
      const v = el("div", "voice");
      v.append(el("div", "call", "📞 PestWatch is calling…"), el("p", null, txt));
      phone.append(v);
    } else {
      const box = el("div", "notif"), b = el("span", `badge ${isClear ? "CLEAR" : "SCOUT"}`, isClear ? "✓ ALL CLEAR" : "🔎 HELP CHECK");
      box.append(b, el("p", null, txt));
      phone.append(box);
    }
    phone.append(el("p", "muted", `${isClear ? "All-clear" : "Scouting request"} received ${hhmm(n.t)}`));
  }

  // ---------- KPIs ----------
  function renderKpis() {
    const ev = S.evaluation, box = document.getElementById("kpis");
    const abl = ev && ev.ablation ? Object.values(ev.ablation) : null;
    const full = abl && abl.length ? abl[abl.length - 1].summary : null;
    const med = (k, legacy) => {
      let o = full && full[k];
      if (!o && legacy && ev) { o = ev; for (const p of legacy) o = o && o[p]; }
      return o && o.median != null ? o.median : null;
    };
    const nSeasons = ev && ev.settings ? ev.settings.n_outbreak : null;
    const k = [
      [m.lead_vs_conventional_days, (v) => `${v.toFixed(1)} days`, "earlier than the conventional extension route",
        med("lead_vs_conventional_days", ["detection", "scan", "lead_vs_conventional_days"]), (v) => `${v.toFixed(1)} d`],
      [m.lead_vs_widespread_days, (v) => `${v.toFixed(0)} days`, "before infestation is widespread",
        med("lead_vs_widespread_days", ["detection", "scan", "lead_vs_widespread_days"]), (v) => `${v.toFixed(0)} d`],
      [m.recall, (v) => pct(v), "of infested farms warned before damage was visible",
        med("recall", ["farm_level", "recall"]), pct],
      [m.loss_avoided_usd, (v) => `$${(v / 1000).toFixed(1)}k`, `crop loss avoided (${pct(m.loss_avoided_pct)} of yield loss)`,
        med("loss_avoided_usd", ["impact", "loss_avoided_usd"]), (v) => `$${(v / 1000).toFixed(1)}k`],
      [m.benefit_cost_ratio, (v) => `${v.toFixed(0)}×`, `benefit-cost ratio (running cost $${m.cost ? Math.round(m.cost.total) : "—"}/season)`,
        med("benefit_cost_ratio", ["impact", "benefit_cost_ratio"]), (v) => `${v.toFixed(0)}×`],
      [m.alerts_per_farm_mean, (v) => v.toFixed(1), `alerts per farm this season (${m.scout_messages ?? 0} scouting requests, ${m.all_clear_messages ?? 0} all-clears)`,
        med("alerts_per_farm_mean"), (v) => v.toFixed(1)],
    ];
    box.replaceChildren();
    k.forEach(([v, f, l, e, fe]) => {
      const d = el("div", "kpi");
      d.append(el("div", "v", v == null ? "—" : f(v)), el("div", "l", l));
      if (e != null) d.append(el("div", "e", `This season · median over ${nSeasons ?? "?"} simulated seasons: ${fe(e)}`));
      box.append(d);
    });
  }

  // ---------- feed ----------
  const feed = document.getElementById("feed");
  let lastFeedKey = "";
  function renderFeed() {
    const showTruth = state.layer === "truth" || state.arm === "cf";
    // Without PestWatch there are no reports or alerts, only what happens on the ground.
    const vis = events.filter((e) => e.t <= state.t && (e.truth ? showTruth : state.arm === "pw")).slice(-100).reverse();
    const keyStr = `${vis.length}:${showTruth}:${state.arm}`;
    if (keyStr === lastFeedKey) return;
    lastFeedKey = keyStr;
    feed.replaceChildren();
    vis.forEach((e) => {
      const li = el("li", e.cls), body = el("span");
      if (e.tag) body.append(el("span", `tag ${e.tag}`, e.tag));
      body.append(document.createTextNode(e.text));
      li.append(el("span", "t", hhmm(e.t)), body);
      feed.append(li);
    });
  }

  // ---------- charts ----------
  const probSeries = X.decision || S.scores;
  const charts = [
    { cv: document.getElementById("chartDamage"), fmt: pct, ymax: null, lines: [
      { name: "With PestWatch", short: "With", color: C.s1, data: series.visPw },
      { name: "Without", short: "Without", color: C.s2, data: series.visCf }], marks: true },
    { cv: document.getElementById("chartProb"), fmt: X.decision ? pct : (v) => String(+v.toFixed(1)), ymax: X.decision ? 1 : null,
      lines: [{ name: X.decision ? "P(outbreak)" : "Scan score", color: C.s1, data: probSeries }],
      refs: X.decision ? [{ v: M.alert_p, label: "alert", color: C.critical }, { v: M.watch_p, label: "watch", color: C.warning }]
        : [{ v: M.threshold, label: "alert", color: C.critical }] },
    { cv: document.getElementById("chartTrap"), fmt: (v) => v.toFixed(2), ymax: 1,
      lines: [{ name: "Rust weather risk", color: C.s1, data: X.weatherSeries }] },
  ];
  function niceMax(v, isPct) {
    const raw = (isPct ? Math.max(v, 0.04) : Math.max(v, 1)) / 4;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    return [1, 2, 2.5, 5, 10].map((k) => k * p).find((k) => k >= raw) * 4;
  }
  charts.forEach((ch) => {
    if (ch.ymax == null) {
      const all = ch.lines.flatMap((l) => l.data).concat((ch.refs || []).map((r) => r.v * 1.2));
      ch.ymax = niceMax(Math.max(...all), ch.fmt === pct);
    }
  });
  function drawChart(ch) {
    const { w, h } = sizeCanvas(ch.cv), ctx = ch.cv.getContext("2d");
    const L = 40, R = ch.lines.length > 1 ? 86 : 46, T = 10, B = 22;
    const Xp = (t) => L + (t / M.days) * (w - L - R), Y = (v) => T + (1 - v / ch.ymax) * (h - T - B);
    ch.X = Xp; ch.L = L; ch.R = R; ch.w = w;
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px system-ui"; ctx.lineWidth = 1;
    for (let k = 0; k <= 4; k++) {
      const v = (ch.ymax * k) / 4, y = Y(v);
      ctx.strokeStyle = k === 0 ? C.axis : C.grid;
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(w - R, y); ctx.stroke();
      ctx.fillStyle = C.muted; ctx.textAlign = "right";
      ctx.fillText(ch.fmt(v), L - 6, y + 4);
    }
    ctx.textAlign = "center";
    for (let d = 0; d <= M.days; d += (w < 420 ? 20 : 10)) ctx.fillText(`day ${d}`, Xp(d), h - 6);
    const marks = [[key.alert, "first alert", C.s1]];
    if (ch.marks) marks.push([key.conventional, "conventional", C.text]);
    marks.forEach(([t, label, col], mi) => {
      if (t == null || t > state.t) return;
      ctx.strokeStyle = col; ctx.globalAlpha = 0.6;
      ctx.beginPath(); ctx.moveTo(Xp(t), T); ctx.lineTo(Xp(t), h - B); ctx.stroke(); ctx.globalAlpha = 1;
      // Label only on the damage chart; elsewhere the timeline names the moment and refs own the top edge.
      if (ch.marks) { ctx.fillStyle = col; ctx.textAlign = "left"; ctx.fillText(label, Xp(t) + 4, T + 10 + mi * 14); }
    });
    (ch.refs || []).forEach((r) => {
      ctx.strokeStyle = r.color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(L, Y(r.v)); ctx.lineTo(w - R, Y(r.v)); ctx.stroke();
      ctx.fillStyle = C.text; ctx.textAlign = "left"; ctx.fillText(r.label, w - R + 6, Y(r.v) + 4);
    });
    const upto = state.step;
    ch.lines.forEach((ln) => {
      ctx.strokeStyle = ln.color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
      for (let i = 0; i <= upto; i++) { const x = Xp(S.times[i]), y = Y(ln.data[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.stroke();
    });
    if (ch.lines.length > 1) {
      const labs = ch.lines.map((ln) => ({ ln, v: ln.data[upto], y: Y(ln.data[upto]) + 4 })).sort((a, b) => a.y - b.y);
      labs.forEach((l, k) => { l.y = Math.min(h - B - (labs.length - 1 - k) * 14, k ? Math.max(l.y, labs[k - 1].y + 14) : l.y); });
      ctx.fillStyle = C.text; ctx.textAlign = "left";
      labs.forEach((l) => ctx.fillText(`${l.ln.short || l.ln.name} ${ch.fmt(l.v)}`, Xp(S.times[upto]) + 6, Math.max(T + 8, l.y)));
    }
    ctx.strokeStyle = "#5a5a55"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Xp(state.t), T); ctx.lineTo(Xp(state.t), h - B); ctx.stroke();
    if (state.hover && state.hover.ch === ch) {
      const x = Xp(S.times[state.hover.i]);
      ctx.strokeStyle = C.text; ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, h - B); ctx.stroke();
      ch.lines.forEach((ln) => {
        if (state.hover.i > upto) return;
        ctx.beginPath(); ctx.arc(x, Y(ln.data[state.hover.i]), 4, 0, 7);
        ctx.fillStyle = ln.color; ctx.fill(); ctx.strokeStyle = C.surface; ctx.lineWidth = 2; ctx.stroke();
      });
    }
  }
  const charttip = document.getElementById("charttip");
  charts.forEach((ch) => {
    const idx = (ev) => {
      const r = ch.cv.getBoundingClientRect(), x = ev.clientX - r.left;
      const t = ((x - ch.L) / (ch.w - ch.L - ch.R)) * M.days;
      return stepOf(Math.max(0, Math.min(M.days, t)));
    };
    ch.cv.addEventListener("pointermove", (ev) => {
      const i = idx(ev);
      state.hover = { ch, i };
      const host = ch.cv.closest(".charts").getBoundingClientRect(), r = ch.cv.getBoundingClientRect();
      charttip.replaceChildren(el("div", "tt-h", `${fmtDay(S.times[i])}${i > state.step ? " (not yet reached)" : ""}`));
      if (i <= state.step) {
        ch.lines.forEach((ln) => {
          const row = el("div", "row"), k = el("i");
          k.style.background = ln.color;
          row.append(k, el("b", null, ch.fmt(ln.data[i])), el("span", null, ln.name));
          charttip.append(row);
        });
        (ch.refs || []).forEach((rf) => charttip.append(el("div", "tt-h", `${rf.label} threshold ${ch.fmt(rf.v)}`)));
      }
      charttip.hidden = false;
      charttip.style.left = `${Math.min(r.left - host.left + ch.X(S.times[i]) + 12, host.width - 170)}px`;
      charttip.style.top = `${r.top - host.top + 20}px`;
      drawChart(ch);
    });
    ch.cv.addEventListener("pointerleave", () => { state.hover = null; charttip.hidden = true; drawChart(ch); });
    ch.cv.addEventListener("click", (ev) => seek(S.times[idx(ev)]));
  });

  function renderTable() {
    const tbl = el("table"), hr = el("tr");
    ["Day", "Visible dmg (with)", "Visible dmg (without)", "Infested (with)", "Infested (without)",
      X.decision ? "P(outbreak)" : "Scan score", "Rust weather risk", "Scouting requests", "Alerts sent"]
      .forEach((h) => hr.append(el("th", null, h)));
    tbl.append(hr);
    for (let d = 0; d <= M.days; d++) {
      const i = stepOf(d), tr = el("tr");
      const sent = S.alerts.filter((a) => a.t > d - 1 && a.t <= d).length;
      const asked = X.notices.filter((n) => n.kind === "scout_request" && n.t > d - 1 && n.t <= d).length;
      [d, pct(series.visPw[i]), pct(series.visCf[i]), pct(series.infPw[i]), pct(series.infCf[i]),
        charts[1].fmt(probSeries[i]), X.weatherSeries[i].toFixed(2), asked, sent]
        .forEach((v) => tr.append(el("td", null, String(v))));
      tbl.append(tr);
    }
    document.getElementById("table").replaceChildren(tbl);
  }

  // ---------- timeline ----------
  const slider = document.getElementById("slider");
  slider.max = N - 1;
  function renderMarkers() {
    const box = document.getElementById("markers");
    box.replaceChildren();
    const items = [
      [key.intro, "rust arrives", "truth"], [key.alert, "PestWatch alert", "alert"],
      [key.notice, "1st farmer notices*", ""], [key.conventional, "conventional detection*", ""],
      [key.widespread, "widespread*", ""],
    ];
    let prevT = -Infinity, row = 0;
    items.filter(([t]) => t != null).sort((a, b) => a[0] - b[0]).forEach(([t, label, cls]) => {
      row = (t - prevT) / M.days < 0.12 ? 1 - row : 0;
      prevT = t;
      const mk = el("span", `marker ${cls}`, label);
      mk.style.left = `${(t / M.days) * 100}%`;
      mk.style.top = `${row * 15}px`;
      mk.title = `${label} — ${fmtDay(t)}${label.endsWith("*") ? " (without PestWatch)" : ""}`;
      mk.addEventListener("click", () => seek(t));
      box.append(mk);
    });
  }
  function seek(t) {
    state.t = Math.max(0, Math.min(M.days, t));
    state.step = stepOf(state.t);
    render();
  }
  slider.addEventListener("input", () => { state.playing = false; updatePlay(); seek(S.times[+slider.value]); });
  const playBtn = document.getElementById("play");
  function updatePlay() { playBtn.textContent = state.playing ? "❚❚" : "▶"; playBtn.setAttribute("aria-label", state.playing ? "Pause" : "Play"); }
  playBtn.addEventListener("click", () => {
    if (state.t >= M.days) seek(0);
    state.playing = !state.playing; updatePlay();
  });
  document.getElementById("speed").addEventListener("change", (e) => { state.speed = +e.target.value; });

  function setSeg(attr, value) {
    document.querySelectorAll(`[data-${attr}]`).forEach((b) => {
      const on = b.dataset[attr] === value;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    });
  }
  function setLayer(v) { state.layer = v; setSeg("layer", v); lastFeedKey = ""; render(); }
  function setArm(v) { state.arm = v; setSeg("arm", v); lastFeedKey = ""; render(); }
  document.querySelectorAll("[data-layer]").forEach((b) => b.addEventListener("click", () => setLayer(b.dataset.layer)));
  document.querySelectorAll("[data-arm]").forEach((b) => b.addEventListener("click", () => setArm(b.dataset.arm)));
  const langSeg = document.getElementById("langSeg");
  LANGS.forEach((l) => {
    const b = el("button", null, l.code.toUpperCase());
    b.dataset.lang = l.code; b.title = l.name;
    b.addEventListener("click", () => { state.lang = l.code; renderPhone(); });
    langSeg.append(b);
  });
  document.querySelectorAll("[data-chan]").forEach((b) => b.addEventListener("click", () => {
    state.channel = b.dataset.chan; renderPhone();
  }));

  // ---------- guided story ----------
  const caption = document.getElementById("caption");
  function buildStory() {
    const ev = S.evaluation, t0 = key.intro ?? 0;
    const stops = [{ t: 0.5, layer: "system", arm: "pw", text: "A normal season in six coffee villages. Farmers photograph coffee leaves; an <b>on-device model</b> classifies each photo offline. Look-alike leaf problems (leaf miner, Cercospora) cause occasional false flags." }];
    const surge = S.nuisance.find((n) => n.kind === "engagement_surge" && n.start < t0);
    if (surge) stops.push({ t: surge.start + 0.75, layer: "system", text: `Extension training day in ${S.villages[surge.village].name}: photo volume triples. PestWatch compares reports against what is <b>expected given how many photos were taken</b>, so it stays quiet.` });
    if (S.env) {
      const d = S.env.migration.findIndex((v, k) => v >= 0.5 && k >= t0 - 5);
      if (d >= 0 && d <= t0 + 1) stops.push({ t: d + 0.1, layer: "system", text: `The <b>rust weather risk</b> rises to ${S.env.migration[d].toFixed(2)}: warm, wet weather favours new infections.` });
    }
    if (key.intro != null) {
      const L = S.landings && S.landings[0];
      stops.push({ t: t0, ord: -1, layer: "truth", text: `<b>Ground truth:</b> rust spores arrive near ${villageOf(M.landing_farms[0])} after rain and infect ${L ? L.farms.length : M.landing_farms.length} farms. The first lesions are too faint to notice.` });
    }
    const spike = (S.trap_readings || []).filter((r) => r.ts >= t0 && (r.n - X.baseline[r.k]) / Math.sqrt(X.baseline[r.k]) >= 3).sort((a, b) => a.ts - b.ts)[0];
    if (spike) stops.push({ t: spike.ts, layer: "system", text: `The trap at ${S.villages[S.traps[spike.k].village].name} catches <b>${spike.n}</b> (usual ~${X.baseline[spike.k].toFixed(1)}/day).` });
    // The scouting round closest to where the disease actually arrived (others may be noise elsewhere).
    const L0 = S.landings && S.landings[0];
    const cand = (S.scout_requests || []).filter((r) => r.t >= t0 - 0.01 && r.t <= t0 + 6);
    const dl = (r) => (L0 ? Math.hypot(S.farms[r.c].x - L0.x, S.farms[r.c].y - L0.y) : 0);
    const req = cand.slice().sort((a, b) => dl(a) - dl(b) || a.t - b.t)[0] || (S.scout_requests || []).find((r) => r.t >= t0);
    if (req) stops.push({ t: req.t, layer: "system", select: { farm: req.farms[0] }, text: `PestWatch sends <b>targeted scouting requests</b> to ${req.farms.length} farmers near ${villageOf(req.c)}: “please check 10 coffee trees today”, each in their own language and channel.` });
    const reply = S.reports.filter((r) => r.src === SRC.SCOUT && r.p >= 0.5 && r.ts >= t0).sort((a, b) => a.ts - b.ts)[0];
    if (reply) stops.push({ t: reply.ts, layer: "system", select: { farm: reply.f }, text: `Farmers reply. ${farmLabel(reply.f)} <b>found rust</b>: a farmer confirmation is the strongest single piece of evidence PestWatch has.` });
    if (X.decision) {
      const i = X.decision.findIndex((p, k) => p >= M.alert_p && S.times[k] >= t0);
      if (i >= 0 && (firstAlertBatch == null || Math.abs(S.times[i] - firstAlertBatch) > 0.01)) stops.push({ t: S.times[i], layer: "system", text: `The fused <b>P(outbreak)</b> (photos + farmer confirmations) reaches ${pct(X.decision[i])}, above the ${pct(M.alert_p)} alert threshold.` });
    }
    if (firstAlertBatch != null) {
      const as = batches[firstAlertBatch], traps = as.every((a) => a.basis === "traps");
      const langs = [...new Set(as.map((a) => (a.delivery || {}).language).filter(Boolean))].map(langName).join(", ");
      stops.push({ t: firstAlertBatch, layer: "system", select: as.find((a) => a.risk === "HIGH") || as[0],
        text: traps ? `<b>Forecast alert:</b> ${as.length} farmers are warned in ${langs} <b>before visible damage exists</b>.`
          : `<b>Outbreak alert:</b> ${as.length} farmers are warned in ${langs}, before most have visible damage.` });
    }
    if (firstReportBatch != null && firstReportBatch !== firstAlertBatch) {
      const as = batches[firstReportBatch], a0 = as[0];
      stops.push({ t: firstReportBatch, layer: "system", select: as.find((a) => a.risk === "HIGH") || a0, text: `As lesions spread, photo reports and confirmations build a <b>report-based alert</b>: ${a0.n_reports} reports from ${a0.n_farms} farms in 72 h; ${as.length} more farmers alerted.` });
    }
    const clear = X.notices.find((n) => n.kind === "all_clear");
    if (clear) stops.push({ t: clear.t, layer: "system", select: { farm: clear.f }, text: `Where reports stop for ${clear.days} days, farmers get an <b>all-clear</b>, so alerts stay credible and nobody keeps spraying needlessly.` });
    if (key.notice != null && key.alert != null) stops.push({ t: key.notice, text: `Without PestWatch, the <b>first farmer would only notice damage now</b>, ${(key.notice - key.alert).toFixed(1)} days after the first alert.` });
    if (key.conventional != null && m.lead_vs_conventional_days != null) stops.push({ t: key.conventional, text: `…and the extension service would learn of it about now: <b>${m.lead_vs_conventional_days.toFixed(1)} days</b> after PestWatch.` });
    const abl = ev && ev.ablation ? Object.values(ev.ablation) : null;
    const full = abl && abl.length ? abl[abl.length - 1].summary : null;
    const medLead = full && full.lead_vs_conventional_days && full.lead_vs_conventional_days.median;
    stops.push({ t: M.days, layer: "truth", arm: "cf", text: `<b>Season end, without PestWatch:</b> ${m.farms_visible_end_cf} farms with visible damage (with PestWatch: ${m.farms_visible_end_pw}). Crop loss avoided: <b>$${Math.round(m.loss_avoided_usd).toLocaleString()}</b> (${pct(m.loss_avoided_pct)}) for a running cost of $${m.cost ? Math.round(m.cost.total) : "—"}: <b>${m.benefit_cost_ratio ? m.benefit_cost_ratio.toFixed(0) : "—"}× benefit-cost</b>.` + (medLead != null ? ` Median over ${ev.settings.n_outbreak} simulated seasons: ${medLead.toFixed(1)} days earlier warning.` : "") });
    return stops.sort((a, b) => a.t - b.t || (a.ord || 0) - (b.ord || 0));
  }
  function showStop(stop) {
    state.playing = false; updatePlay();
    if (stop.layer) setLayer(stop.layer);
    if (stop.arm) setArm(stop.arm);
    if (stop.select) { state.selected = stop.select.farm; state.lang = null; state.channel = null; }
    seek(stop.t);
    caption.replaceChildren();
    const p = el("div"); p.innerHTML = stop.text;  // story text is authored here; data values are numbers/names from the exporter
    const next = el("button", "primary", state.story.idx < state.story.stops.length - 1 ? "Next ▸" : "Close");
    next.style.marginTop = "8px";
    next.addEventListener("click", advanceStory);
    caption.append(p, next);
    caption.hidden = false;
  }
  function advanceStory() {
    if (!state.story) return;
    state.story.idx++;
    if (state.story.idx >= state.story.stops.length) { endStory(); return; }
    caption.hidden = true;
    if (state.arm === "cf") setArm("pw");
    state.playing = true; updatePlay();
  }
  function endStory() { state.story = null; caption.hidden = true; document.getElementById("story").textContent = "▶ Guided demo"; }
  function startStory(idx = 0) {
    state.story = { stops: buildStory(), idx: 0 };
    state.story.idx = Math.max(0, Math.min(state.story.stops.length - 1, idx));
    document.getElementById("story").textContent = "■ Stop demo";
    setArm("pw"); seek(0); state.speed = 2; document.getElementById("speed").value = "2";
    showStop(state.story.stops[state.story.idx]);
  }
  document.getElementById("story").addEventListener("click", () => { if (state.story) endStory(); else startStory(); });

  // ---------- main loop ----------
  function render() {
    slider.value = state.step;
    document.getElementById("day").textContent = fmtDay(state.t);
    const w = X.windAt(state.t);
    document.getElementById("clocksub").textContent = key.intro != null && state.t >= key.intro && (state.layer === "truth" || state.arm === "cf")
      ? `rust arrived ${(state.t - key.intro).toFixed(1)} days ago`
      : `${S.reports.filter((r) => r.ts <= state.t).length} reports synced${w ? ` · rust weather risk ${w.migration.toFixed(2)}` : ""}`;
    renderLegend(); drawMap(); renderPhone(); renderFeed(); charts.forEach(drawChart);
  }
  let last = performance.now();
  function tick(now) {
    const sec = Math.min(0.1, (now - last) / 1000); last = now;
    if (state.playing) {
      const before = state.t;
      state.t = Math.min(M.days, state.t + sec * state.speed);
      if (state.story) {
        const stop = state.story.stops[state.story.idx];
        if (stop && before < stop.t && state.t >= stop.t) { state.t = stop.t; state.step = stepOf(state.t); showStop(stop); }
      }
      if (state.t >= M.days) { state.playing = false; updatePlay(); }
      state.step = stepOf(state.t);
      render();
    }
    requestAnimationFrame(tick);
  }

  document.getElementById("subtitle").textContent =
    `Simulated region · ${S.villages.length} villages · ${F} farms · ${S.farms.filter((f) => f.app).length} using the app · ${X.traps.length ? `${X.traps.length} traps · ` : ""}season seed ${M.seed}`;
  if (S.evaluation && S.evaluation.settings) {
    document.getElementById("evalnote").textContent =
      ` Evaluation: ${S.evaluation.settings.n_outbreak} outbreak + ${S.evaluation.settings.n_null} outbreak-free simulated seasons per variant (reports/EVALUATION.md). Classifier: ${M.classifier || "assumed"}. * = without PestWatch.`;
  }
  window.addEventListener("resize", () => { layoutMap(); render(); });
  // Deep links for rehearsed demos, e.g. index.html#t=22&layer=truth&arm=cf&farm=12&lang=sw
  const hp = new URLSearchParams(location.hash.slice(1));
  if (hp.get("layer")) setSeg("layer", (state.layer = hp.get("layer")));
  if (hp.get("arm")) setSeg("arm", (state.arm = hp.get("arm")));
  if (hp.get("farm")) state.selected = Math.max(0, Math.min(F - 1, +hp.get("farm") - 1));
  if (hp.get("lang")) state.lang = hp.get("lang");
  if (hp.get("channel")) state.channel = hp.get("channel");
  state.t = Math.max(0, Math.min(M.days, +(hp.get("t") || 0))); state.step = stepOf(state.t);
  layoutMap(); renderMarkers(); renderKpis(); renderTable(); render();
  // Rehearsal: index.html#story=4 opens the guided demo at stop 4.
  if (hp.get("story") != null) startStory(+hp.get("story") || 0);
  window.PW_STORY_STOPS = () => buildStory().map((x) => ({ t: x.t, text: x.text.replace(/<[^>]+>/g, "") }));
  // Scripted control, for recording demo videos and rehearsals.
  window.PWDemo = {
    seek, layer: setLayer, arm: setArm,
    select(f) { state.selected = f; state.lang = null; state.channel = null; render(); },
    lang(l) { state.lang = l; renderPhone(); },
    channel(c) { state.channel = c; renderPhone(); },
  };
  requestAnimationFrame(tick);
})();
