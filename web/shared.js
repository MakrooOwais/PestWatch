/* Helpers shared by the dashboard (app.js) and the extension-officer view (extension.js). */
(function () {
  "use strict";
  const C = {
    surface: "#1a1a19", grid: "#2a2a27", axis: "#3a3a37", text: "#c3c2b7", muted: "#8b8a82", ink: "#ffffff",
    s1: "#3987e5", s2: "#d95926", s3: "#199e70",
    good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b",
    farm: "#6b6a64", farmOff: "#4a4a46",
  };
  const RISK_COLOR = { HIGH: C.critical, MEDIUM: C.serious, LOW: C.warning };
  const RISK_ICON = { HIGH: "▲", MEDIUM: "◆", LOW: "●" };
  const SRC = { PHOTO: 0, LEAD: 1, SCOUT: 2, INSPECT: 3, SPAM: 4 };

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const hhmm = (t) => {
    const h = Math.round((t % 1) * 24);
    return `d${Math.floor(t)} ${String(h).padStart(2, "0")}h`;
  };
  const fmtDay = (t) => `Day ${t.toFixed(1)}`;
  const mix = (a, b, u) => {
    const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
    const ch = (s) => Math.round(((pa >> s) & 255) * (1 - u) + ((pb >> s) & 255) * u);
    return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
  };
  const pColor = (p) => (p >= 0.6 ? "#e66767" : p >= 0.3 ? "#c98500" : "#6b6a64");

  /** Indexes over a scenario that both views need. */
  function build(S) {
    const M = S.meta, N = S.times.length, F = S.farms.length, dt = M.dt;
    const stepOf = (t) => Math.max(0, Math.min(N - 1, Math.round(t / dt)));
    const villageOf = (f) => S.villages[S.farms[f].village].name;
    const farmLabel = (f) => `Farm ${f + 1} · ${villageOf(f)}`;
    const byFarm = (arr, key) => {
      const out = Array.from({ length: F }, () => []);
      arr.forEach((x) => out[x[key]].push(x));
      return out;
    };
    S.reports.forEach((r, i) => { r.i = i; });
    const alertsByFarm = byFarm(S.alerts, "farm");
    const reportsByFarm = byFarm(S.reports, "f");
    const notices = S.notices || [];
    const noticesByFarm = byFarm(notices, "f");
    const latest = (list, t, key = "t") => {
      let best = null;
      for (const x of list) { if (x[key] <= t) best = x; else break; }
      return best;
    };

    // Traps: readings by trap, baseline from the first week, z-score of the last 3 days synced.
    const traps = S.traps || [];
    const readings = (S.trap_readings || []).slice().sort((a, b) => a.t - b.t);
    const byTrap = traps.map(() => []);
    readings.forEach((r) => byTrap[r.k].push(r));
    const baseline = byTrap.map((rs) => {
      const early = rs.filter((r) => r.t < 7);
      return Math.max(0.2, early.length ? early.reduce((s, r) => s + r.n, 0) / early.length : 0.4);
    });
    function trapNow(k, t) {
      const seen = byTrap[k].filter((r) => r.ts <= t);
      const last = seen[seen.length - 1] || null;
      const recent = seen.filter((r) => r.t > t - 3);
      const mu = baseline[k] * Math.max(1, recent.length);
      const c = recent.reduce((s, r) => s + r.n, 0);
      return { last, z: recent.length ? (c - mu) / Math.sqrt(mu) : 0, baseline: baseline[k] };
    }
    // Daily highest catch (by read day) for the chart, expanded to steps.
    const days = Math.ceil(M.days) + 1;
    const dayMax = new Array(days).fill(0);
    readings.forEach((r) => { const d = Math.floor(r.t + 1e-9); if (d < days) dayMax[d] = Math.max(dayMax[d], r.n); });
    const trapSeries = S.times.map((t) => dayMax[Math.min(days - 1, Math.floor(t + 1e-9))]);
    // Weather-risk index per step (daily forecast index; drives the third chart).
    const mig = (S.env && S.env.migration) || [];
    const weatherSeries = S.times.map((t) => mig[Math.min(mig.length - 1, Math.floor(t + 1e-9))] || 0);

    const decision = S.probability || null;
    const decisionThr = decision ? M.alert_p : M.threshold;

    function windAt(t) {
      const e = S.env;
      if (!e) return null;
      const d = Math.max(0, Math.min(e.wind_dir.length - 1, Math.floor(t)));
      return { dir: e.wind_dir[d], speed: e.wind_speed[d], temp: e.temp[d], migration: e.migration[d] };
    }

    // Village centre farm (closest to the village centroid) for zone matching.
    const villageCentre = S.villages.map((v) => {
      let best = 0, bd = Infinity;
      S.farms.forEach((f, k) => { const d = (f.x - v.x) ** 2 + (f.y - v.y) ** 2; if (d < bd) { bd = d; best = k; } });
      return best;
    });
    const dist = (a, b) => Math.hypot(S.farms[a].x - S.farms[b].x, S.farms[a].y - S.farms[b].y);

    return {
      M, N, F, dt, stepOf, villageOf, farmLabel, alertsByFarm, reportsByFarm, noticesByFarm, notices, latest,
      traps, byTrap, baseline, trapNow, trapSeries, weatherSeries, decision, decisionThr, windAt, villageCentre, dist,
    };
  }

  function sizeCanvas(cv) {
    const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, r.width * dpr); cv.height = Math.max(1, r.height * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: r.width, h: r.height };
  }
  function haloText(ctx, text, x, y, color, font) {
    if (font) ctx.font = font;
    ctx.lineJoin = "round"; ctx.strokeStyle = C.surface; ctx.lineWidth = 4; ctx.strokeText(text, x, y);
    ctx.fillStyle = color; ctx.fillText(text, x, y);
  }
  function drawTrap(ctx, x, y, anomalous) {
    ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x + 7, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 7, y); ctx.closePath();
    ctx.fillStyle = C.surface; ctx.fill();
    ctx.strokeStyle = anomalous ? C.warning : C.text; ctx.lineWidth = anomalous ? 2.5 : 1.5; ctx.stroke();
  }
  function downloadCsv(name, rows) {
    const esc = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const blob = new Blob([rows.map((r) => r.map(esc).join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  window.PW = { C, RISK_COLOR, RISK_ICON, SRC, el, hhmm, fmtDay, mix, pColor, build, sizeCanvas, haloText, drawTrap, downloadCsv };
})();
