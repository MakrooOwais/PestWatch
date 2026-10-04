/* PestWatch extension-officer view: which villages need attention today, and what to do. */
(function () {
  "use strict";
  const S = window.SCENARIO;
  if (!S || !window.PW) {
    document.body.textContent = "No scenario found. Run: python3 scripts/run_demo.py";
    return;
  }
  const { C, RISK_COLOR, RISK_ICON, SRC, el, hhmm, fmtDay, pColor, sizeCanvas, haloText, drawTrap, downloadCsv } = window.PW;
  const X = window.PW.build(S);
  const { M, N, stepOf, farmLabel, alertsByFarm, reportsByFarm, noticesByFarm, latest } = X;
  const LANGS = S.languages || [];
  const langName = (c) => (LANGS.find((l) => l.code === c) || { name: c }).name;
  const profileOf = (f) => (S.profiles ? S.profiles[f] : { language: "en", channel: "app" });
  const pct = (v) => `${Math.round(v * 100)}%`;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const ACTIVE = 7;   // days an alert keeps a village active
  const villageFarms = S.villages.map((_, v) => S.farms.map((f, k) => (f.village === v ? k : -1)).filter((k) => k >= 0));
  const isReply = (r) => r.src === SRC.SCOUT || r.src === SRC.INSPECT;
  // Cluster id -> centre farm (for finance triggers).
  const clusterCentre = {};
  S.clusters.forEach((evs) => evs.forEach((e) => { if (clusterCentre[e.id] == null) clusterCentre[e.id] = e.c; }));

  const state = { t: 0, step: 0, playing: false, speed: 2, selected: null };

  function villageClusters(v, step) {
    const vc = X.villageCentre[v], fs = new Set(villageFarms[v]);
    return (S.clusters[step] || []).filter((e) => fs.has(e.c) || X.dist(e.c, vc) <= e.r + 1.0);
  }
  function villageP(v, step) {
    // Strongest of: clusters touching the village, and the village's own fixed zone (scored every 12 h).
    const ps = villageClusters(v, step).map((e) => e.p).filter((p) => p != null);
    const vz = S.village_probability && S.village_probability[step] ? S.village_probability[step][v] : 0;
    return Math.max(vz || 0, ...ps, 0);
  }

  function stats(v, t) {
    const step = stepOf(t), fs = villageFarms[v], fset = new Set(fs);
    const cl = villageClusters(v, step);
    const lvl = cl.some((e) => e.lvl === "ALERT") ? "ALERT" : cl.length ? "WATCH" : null;
    const recentAlerts = fs.map((f) => latest(alertsByFarm[f], t)).filter((a) => a && t - a.t <= ACTIVE);
    const status = lvl || (recentAlerts.length ? "ALERT" : "QUIET");
    const statusNote = cl.some((e) => e.trap) ? "trap" : recentAlerts.length && lvl !== "ALERT" ? "active" : "";
    const p = villageP(v, step), p24 = villageP(v, Math.max(0, step - 4));
    const reps = [], conf = [], none = [];
    fs.forEach((f) => reportsByFarm[f].forEach((r) => {
      if (r.ts > t || r.tc <= t - 3) return;
      if (isReply(r)) (r.p >= 0.5 ? conf : none).push(r);
      else if (r.p >= M.positive_p) reps.push(r);   // the system cannot tell spam apart; the scan caps it
    }));
    const trapIdx = X.traps.map((tp, k) => (tp.village === v ? k : -1)).filter((k) => k >= 0);
    const trap = trapIdx.map((k) => X.trapNow(k, t)).sort((a, b) => b.z - a.z)[0] || null;
    const dtvs = cl.map((e) => e.dtv).filter((d) => d != null);
    const dtv = dtvs.length ? Math.min(...dtvs) : null;
    const atRisk = recentAlerts.filter((a) => a.risk === "HIGH" || a.risk === "MEDIUM");
    const ha = atRisk.reduce((s, a) => s + S.farms[a.farm].ha, 0);
    const litres = ha * (M.dose_l_per_ha || 0.5);
    // Scouting requests sent / answered / positive.
    const reqs = fs.flatMap((f) => noticesByFarm[f].filter((n) => n.kind === "scout_request" && n.t <= t));
    let answered = 0, positive = 0;
    reqs.forEach((n) => {
      const r = reportsByFarm[n.f].find((x) => x.src === SRC.SCOUT && x.tc >= n.t && x.ts <= t && x.tc <= n.t + 3);
      if (r) { answered++; if (r.p >= 0.5) positive++; }
    });
    const sent = fs.flatMap((f) => alertsByFarm[f].filter((a) => a.t <= t));
    const byChan = {}, byLang = {};
    sent.forEach((a) => {
      const d = a.delivery || {};
      byChan[d.channel || "?"] = (byChan[d.channel || "?"] || 0) + 1;
      byLang[d.language || "?"] = (byLang[d.language || "?"] || 0) + 1;
    });
    const trig = (S.triggers || []).filter((tr) => tr.t <= t && fset.has(clusterCentre[tr.cluster_id]));
    const cleared = fs.some((f) => { const n = latest(noticesByFarm[f], t); return n && n.kind === "all_clear" && t - n.t <= ACTIVE; });
    // Silent: asked within 3 days, no reply yet.
    const silent = fs.filter((f) => {
      const n = latest(noticesByFarm[f].filter((x) => x.kind === "scout_request"), t);
      return n && t - n.t <= 3 && !reportsByFarm[f].some((r) => r.src === SRC.SCOUT && r.tc >= n.t && r.ts <= t);
    });
    let action;
    if (status === "ALERT" || recentAlerts.length || conf.length) {
      action = `Visit within 24 h${litres >= 0.5 ? `; pre-position ${Math.ceil(litres)} L fungicide` : ""}${silent.length ? `; follow up ${silent.length} silent farmers` : ""}`;
    } else if (status === "WATCH") {
      action = `Ask lead farmers to check 10 coffee trees today${silent.length ? `; ${silent.length} requests unanswered` : ""}`;
    } else if (cleared) {
      action = "All-clear sent; resume weekly checks";
    } else {
      action = "Routine: weekly lead-farmer round";
    }
    // Live signal outranks an older alert that is merely still active.
    const base = lvl === "ALERT" ? 30 : lvl === "WATCH" ? 20 : status === "ALERT" ? 12 : 0;
    const score = base + p * 15 + 2 * conf.length + (dtv != null ? Math.max(0, 10 - dtv) / 2 : 0) + (trap ? Math.max(0, trap.z) / 5 : 0);
    return {
      v, name: S.villages[v].name, status, statusNote, p, p24, reps: reps.length, repFarms: new Set(reps.map((r) => r.f)).size,
      conf: conf.length, none: none.length, trap, dtv, atRisk: atRisk.length, ha, litres, reqs: reqs.length, answered, positive,
      byChan, byLang, trig, action, score, silent, cl,
    };
  }

  // ---------- task list ----------
  const table = document.getElementById("tasks");
  let rows = [];
  function renderTasks() {
    rows = S.villages.map((_, v) => stats(v, state.t)).sort((a, b) => b.score - a.score);
    if (state.selected == null) state.selected = rows[0].v;
    table.replaceChildren();
    const hr = el("tr");
    ["Village", "Status", "P(outbreak)", "Evidence, 72 h", "Trap", "Visible in", "At risk", "Outreach", "Finance"]
      .forEach((h) => hr.append(el("th", null, h)));
    table.append(hr);
    rows.forEach((r) => {
      const tr = el("tr", `row${r.v === state.selected ? " sel" : ""}`);
      tr.tabIndex = 0;
      tr.addEventListener("click", () => { state.selected = r.v; render(); });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { state.selected = r.v; render(); } });
      const td = (...kids) => { const c = el("td"); kids.forEach((k) => c.append(typeof k === "string" ? document.createTextNode(k) : k)); tr.append(c); return c; };
      td(el("b", null, r.name));
      const icon = r.status === "ALERT" ? "▲" : r.status === "WATCH" ? "◆" : "●";
      td(el("span", `status ${r.status}`, `${icon} ${r.status}`), r.statusNote ? el("div", "sub2", r.statusNote === "trap" ? "trap-led" : "alert active") : "");
      const trend = r.p - r.p24;
      td(el("b", null, pct(r.p)), el("span", trend > 0.02 ? "up" : trend < -0.02 ? "down" : "sub2", trend > 0.02 ? " ▲" : trend < -0.02 ? " ▼" : ""), el("div", "sub2", `24 h ago ${pct(r.p24)}`));
      td(el("b", null, `${plural(r.reps, "report")} · ${plural(r.repFarms, "farm")}`), el("div", "sub2", `${r.conf} confirmed · ${r.none} none found`));
      td(r.trap && r.trap.last ? el("b", null, `${r.trap.last.n} caught${r.trap.z >= 3 ? " ▲" : ""}`) : "—",
        r.trap ? el("div", "sub2", `usual ~${r.trap.baseline.toFixed(1)}/day`) : "");
      td(r.dtv != null ? el("b", null, `~${Math.round(r.dtv)} d`) : "—", r.dtv != null ? el("div", "sub2", "degree-day forecast") : "");
      td(el("b", null, `${plural(r.atRisk, "farm")} · ${r.ha.toFixed(1)} ha`), el("div", "sub2", `${r.litres.toFixed(1)} L fungicide`));
      const ch = Object.entries(r.byChan).map(([k, n]) => `${k} ${n}`).join(" · ");
      const lg = Object.entries(r.byLang).map(([k, n]) => `${k} ${n}`).join(" · ");
      td(el("b", null, `${r.reqs} asked · ${r.answered} replied · ${r.positive} found`),
        el("div", "sub2", ch ? `alerts: ${ch} (${lg})` : "no alerts sent"));
      td(r.trig.length ? el("b", null, `$${Math.round(r.trig.reduce((s, x) => s + x.payout_usd, 0)).toLocaleString()}`) : "—",
        r.trig.length ? el("div", "sub2", `triggered ${hhmm(r.trig[0].t)}`) : "");
      table.append(tr);
      // Suggested action on its own full-width line under the row.
      const ar = el("tr", `actrow${r.v === state.selected ? " sel" : ""}`), ac = el("td");
      ac.colSpan = 9;
      ac.append(el("span", "sub2", "→ "), el("span", "action", r.action));
      ar.append(ac);
      ar.addEventListener("click", () => { state.selected = r.v; render(); });
      table.append(ar);
    });
    const s = document.getElementById("summary");
    s.replaceChildren();
    const nA = rows.filter((r) => r.status === "ALERT").length, nW = rows.filter((r) => r.status === "WATCH").length;
    const litres = rows.reduce((a, r) => a + r.litres, 0), ha = rows.reduce((a, r) => a + r.ha, 0);
    [[`${nA}`, "villages on alert"], [`${nW}`, "on watch"], [`${ha.toFixed(0)} ha`, "at HIGH/MEDIUM risk"], [`${Math.ceil(litres)} L`, "fungicide to pre-position"]]
      .forEach(([v, l]) => { const d = el("span"); d.append(el("b", null, v), document.createTextNode(` ${l}`)); s.append(d); });
  }

  // ---------- detail ----------
  const cv = document.getElementById("vmap");
  let vproj = null;
  function renderDetail() {
    const r = rows.find((x) => x.v === state.selected) || rows[0];
    if (!r) return;
    document.getElementById("dtitle").textContent = `${r.name} · ${r.status}${r.p ? ` · P ${pct(r.p)}` : ""}`;
    const { w, h } = sizeCanvas(cv), ctx = cv.getContext("2d"), t = state.t;
    const fs = villageFarms[r.v];
    const xs = fs.map((f) => S.farms[f].x), ys = fs.map((f) => S.farms[f].y);
    const x0 = Math.min(...xs) - 1, x1 = Math.max(...xs) + 1, y0 = Math.min(...ys) - 1, y1 = Math.max(...ys) + 1;
    const s = Math.min((w - 20) / (x1 - x0), (h - 20) / (y1 - y0));
    const ox = (w - s * (x1 - x0)) / 2, oy = (h - s * (y1 - y0)) / 2;
    const P = { x: (x) => ox + (x - x0) * s, y: (y) => oy + (y1 - y) * s, s };
    vproj = P;
    ctx.clearRect(0, 0, w, h);
    r.cl.forEach((e) => {
      const f = S.farms[e.c];
      ctx.beginPath(); ctx.arc(P.x(f.x), P.y(f.y), e.r * s, 0, 7);
      if (e.lvl === "ALERT") { ctx.fillStyle = "rgba(208,59,59,.12)"; ctx.fill(); ctx.strokeStyle = C.critical; ctx.lineWidth = 2; ctx.stroke(); }
      else { ctx.setLineDash([5, 4]); ctx.strokeStyle = C.warning; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]); }
    });
    S.farms.forEach((f, k) => {
      if (f.x < x0 || f.x > x1 || f.y < y0 || f.y > y1) return;
      const x = P.x(f.x), y = P.y(f.y), mine = f.village === r.v;
      const rep = latest(reportsByFarm[k].filter((q) => q.ts <= t && q.tc > t - 3 && !isReply(q)), t, "tc");
      ctx.globalAlpha = mine ? 1 : 0.35;
      ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fillStyle = rep ? pColor(rep.p) : C.farmOff; ctx.fill();
      const a = latest(alertsByFarm[k], t);
      if (a && t - a.t <= ACTIVE) { ctx.beginPath(); ctx.arc(x, y, 8.5, 0, 7); ctx.strokeStyle = RISK_COLOR[a.risk]; ctx.lineWidth = 2; ctx.stroke(); }
      const rr = latest(reportsByFarm[k].filter((q) => isReply(q) && q.ts <= t && q.tc > t - 3), t, "tc");
      if (rr && rr.p >= 0.5) {
        ctx.beginPath(); ctx.moveTo(x, y - 13); ctx.lineTo(x + 4.5, y - 5.5); ctx.lineTo(x - 4.5, y - 5.5); ctx.closePath();
        ctx.fillStyle = "#e66767"; ctx.fill();
      }
      ctx.globalAlpha = 1;
    });
    X.traps.forEach((tp, k) => {
      if (tp.x < x0 || tp.x > x1 || tp.y < y0 || tp.y > y1) return;
      const now = X.trapNow(k, t);
      drawTrap(ctx, P.x(tp.x), P.y(tp.y), now.z >= 3);
      if (now.last) { ctx.textAlign = "left"; haloText(ctx, `${now.last.n} caught`, P.x(tp.x) + 9, P.y(tp.y) + 4, now.z >= 3 ? C.warning : C.muted, "600 10px system-ui"); }
    });
    ctx.fillStyle = C.muted; ctx.font = "11px system-ui"; ctx.textAlign = "left";
    ctx.fillRect(10, h - 12, s, 2); ctx.fillText("1 km", 10, h - 16);

    const lg = document.getElementById("dlegend");
    lg.replaceChildren();
    [["dot", { background: "#e66767" }, "likely rust"], ["dot", { background: "#c98500" }, "possible"], ["tri", {}, "found"],
      ["ring", { borderColor: C.critical }, "HIGH"]].concat(X.traps.length ? [["diamond", {}, "trap"]] : []).forEach(([c, st, l]) => {
      const k = el("span", "key"), i = el("i", c); Object.assign(i.style, st); k.append(i, document.createTextNode(l)); lg.append(k);
    });

    // Farms needing a visit.
    const ul = document.getElementById("visits");
    ul.replaceChildren();
    const items = [];
    fs.forEach((f) => {
      const a = latest(alertsByFarm[f], t);
      if (a && a.risk === "HIGH" && t - a.t <= ACTIVE) items.push({ f, why: "HIGH", text: `alerted ${hhmm(a.t)}${a.basis === "traps" ? " (forecast)" : ""}`, rank: 2 });
      const found = reportsByFarm[f].find((q) => isReply(q) && q.p >= 0.5 && q.ts <= t && t - q.tc <= ACTIVE);
      if (found) items.push({ f, why: "FOUND", text: `farmer found rust ${hhmm(found.tc)}`, rank: 3 });
    });
    r.silent.forEach((f) => items.push({ f, why: "SILENT", text: "asked to scout, no reply yet", rank: 1 }));
    const seen = new Set();
    items.sort((a, b) => b.rank - a.rank).forEach((it) => {
      const k = `${it.f}:${it.why}`;
      if (seen.has(k)) return;
      seen.add(k);
      const p = profileOf(it.f), li = el("li"), left = el("span");
      left.append(el("b", null, farmLabel(it.f)), document.createTextNode(` · ${it.text} · ${langName(p.language)}, ${p.channel}`));
      li.append(left, el("span", `why ${it.why}`, it.why === "SILENT" ? "NO REPLY" : it.why));
      ul.append(li);
    });
    if (!items.length) ul.append(el("li", null, "No farm needs a visit right now."));
  }
  const maptip = document.getElementById("maptip");
  cv.addEventListener("pointermove", (ev) => {
    if (!vproj) return;
    const rc = cv.getBoundingClientRect(), x = ev.clientX - rc.left, y = ev.clientY - rc.top;
    let best = -1, bd = 14 * 14;
    S.farms.forEach((f, k) => { const d = (vproj.x(f.x) - x) ** 2 + (vproj.y(f.y) - y) ** 2; if (d < bd) { bd = d; best = k; } });
    if (best < 0) { maptip.hidden = true; return; }
    const a = latest(alertsByFarm[best], state.t), p = profileOf(best);
    maptip.replaceChildren(el("div", "tt-h", farmLabel(best)));
    [[`${S.farms[best].ha} ha`, `${langName(p.language)} · ${p.channel}`], a ? [`${RISK_ICON[a.risk]} ${a.risk}`, `alert ${hhmm(a.t)}`] : null]
      .filter(Boolean).forEach(([v, l]) => { const row = el("div", "row"); row.append(el("b", null, v), el("span", null, l)); maptip.append(row); });
    maptip.hidden = false;
    maptip.style.left = `${Math.min(x + 14, rc.width - 200)}px`; maptip.style.top = `${y + 40}px`;
  });
  cv.addEventListener("pointerleave", () => { maptip.hidden = true; });
  cv.parentElement.style.position = "relative";

  // ---------- export / print ----------
  document.getElementById("csv").addEventListener("click", () => {
    const head = ["day", "village", "status", "p_outbreak", "p_24h_ago", "reports_72h", "report_farms", "confirmed", "none_found",
      "trap_last", "trap_usual", "visible_in_days", "farms_at_risk", "ha_at_risk", "fungicide_l", "scout_sent", "scout_answered",
      "scout_found", "finance_usd", "action"];
    const body = rows.map((r) => [state.t.toFixed(2), r.name, r.status, r.p.toFixed(3), r.p24.toFixed(3), r.reps, r.repFarms, r.conf, r.none,
      r.trap && r.trap.last ? r.trap.last.n : "", r.trap ? r.trap.baseline.toFixed(1) : "", r.dtv != null ? r.dtv.toFixed(1) : "",
      r.atRisk, r.ha.toFixed(2), r.litres.toFixed(1), r.reqs, r.answered, r.positive,
      Math.round(r.trig.reduce((s, x) => s + x.payout_usd, 0)), r.action]);
    downloadCsv(`pestwatch_tasks_day${state.t.toFixed(1)}.csv`, [head, ...body]);
  });
  document.getElementById("print").addEventListener("click", () => window.print());

  // ---------- time ----------
  const slider = document.getElementById("slider");
  slider.max = N - 1;
  const playBtn = document.getElementById("play");
  function updatePlay() { playBtn.textContent = state.playing ? "❚❚" : "▶"; }
  function seek(t) { state.t = Math.max(0, Math.min(M.days, t)); state.step = stepOf(state.t); render(); }
  slider.addEventListener("input", () => { state.playing = false; updatePlay(); seek(S.times[+slider.value]); });
  playBtn.addEventListener("click", () => { if (state.t >= M.days) seek(0); state.playing = !state.playing; updatePlay(); });
  document.getElementById("speed").addEventListener("change", (e) => { state.speed = +e.target.value; });
  let lastRender = 0;
  function render() {
    slider.value = state.step;
    document.getElementById("day").textContent = fmtDay(state.t);
    const w = X.windAt(state.t);
    document.getElementById("clocksub").textContent = w ? `wind ${w.speed.toFixed(1)} m/s · ${w.temp.toFixed(0)}°C · rust weather risk ${w.migration.toFixed(2)}` : "";
    renderTasks(); renderDetail();
  }
  let last = performance.now();
  function tick(now) {
    const sec = Math.min(0.1, (now - last) / 1000); last = now;
    if (state.playing) {
      state.t = Math.min(M.days, state.t + sec * state.speed);
      if (state.t >= M.days) { state.playing = false; updatePlay(); }
      state.step = stepOf(state.t);
      if (now - lastRender > 120) { lastRender = now; render(); }   // the task list is heavier than the map
    }
    requestAnimationFrame(tick);
  }
  document.getElementById("subtitle").textContent =
    `Simulated region · ${S.villages.length} villages · ${S.farms.length} farms · ${X.traps.length ? `${X.traps.length} traps · ` : ""}season seed ${M.seed}`;
  window.addEventListener("resize", render);
  const hp = new URLSearchParams(location.hash.slice(1));
  const t0 = hp.get("t") != null ? +hp.get("t") : ((S.metrics.t_first_alert ?? 10) + 1);
  if (hp.get("village")) state.selected = Math.max(0, S.villages.findIndex((v) => v.name.toLowerCase() === hp.get("village").toLowerCase()));
  seek(t0);
  requestAnimationFrame(tick);
})();
