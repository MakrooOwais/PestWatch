"""Build a self-contained static site (dist/) for GitHub Pages or any static host.

- Copies web/ (dashboard, extension view, field app, on-device model, sample photos),
  resolving the models/ symlink and leaving out files the pages don't use.
- Snapshots the live demo from the real server logic into dist/data/live_demo.js so the
  field app works without a server (static_api.js stands in for the API).
- Works from any sub-path (all URLs are relative).

Usage: python3 scripts/build_static.py [--out dist]
Nothing is uploaded; deploy dist/ yourself when ready.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pestwatch  # noqa: E402,F401  (pins BLAS threads before numpy loads)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WEB = os.path.join(ROOT, "web")
SKIP = {"data/scenario.json", "model_test.html", "audio/README.md", "samples/README.md"}
MODEL_FILES = ["model_int8.onnx", "labels.json", "preprocess.json", "metrics.json"]


def copy_web(out):
    for dirpath, dirnames, files in os.walk(WEB):
        rel_dir = os.path.relpath(dirpath, WEB)
        dirnames[:] = [d for d in dirnames if d != "models"]  # copied explicitly below
        for f in files:
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if rel in SKIP or f.startswith(".") or f.endswith(".pyc"):
                continue
            os.makedirs(os.path.join(out, rel_dir), exist_ok=True)
            shutil.copy2(os.path.join(dirpath, f), os.path.join(out, rel))
    mdir = os.path.join(out, "models", "pest_classifier")
    os.makedirs(mdir, exist_ok=True)
    for f in MODEL_FILES:
        shutil.copy2(os.path.join(ROOT, "models", "pest_classifier", f), mdir)


def slim_alert(a):
    return None if not a else {k: a[k] for k in ("risk", "basis", "n_reports", "n_farms", "msg") if k in a}


def snapshot():
    """Status of every farm at the frozen demo moment, before and after one confirmation."""
    import server
    from pestwatch.service.gateways import ConsoleGateway
    tmp = tempfile.mkdtemp()
    st = server.LiveState(db_path=os.path.join(tmp, "static.db"), admin_key="static-build",
                          gateway=ConsoleGateway(stream=open(os.devnull, "w")))
    farms = st.farms()

    def capture():
        out = {"clusters": None, "farms": {}}
        for f in farms:
            s = st.status(f["id"])
            out["clusters"] = s["clusters"]
            entry = {}
            if s.get("alert"):
                entry["alert"] = slim_alert(s["alert"])
            if s.get("notice"):
                entry["notice"] = {"kind": s["notice"]["kind"], "msg": s["notice"]["msg"]}
            if entry:
                out["farms"][f["id"]] = entry
        return out

    before = capture()
    trigger = next((f["id"] for f in farms if f["suggested"] and f["app"]), farms[0]["id"])
    st.inbound(trigger, "1", channel="app")
    after = capture()
    from pestwatch.config import CLASSES
    return {"t_live": st.t_live, "classes": CLASSES, "farms": farms, "trigger_farm": trigger,
            "before": before, "after": after}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    copy_web(out)

    snap = snapshot()
    with open(os.path.join(out, "data", "live_demo.js"), "w", encoding="utf-8") as f:
        f.write("window.LIVE_DEMO=")
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    # Field app: load the snapshot and the serverless API before field.js.
    fp = os.path.join(out, "field.html")
    html = open(fp, encoding="utf-8").read()
    tag = '<script src="field.js"></script>'
    assert tag in html, "field.html layout changed"
    html = html.replace(tag, '<script src="data/live_demo.js"></script>\n<script src="static_api.js"></script>\n' + tag)
    open(fp, "w", encoding="utf-8").write(html)
    # Cache the static-only files for offline use.
    sw = os.path.join(out, "sw.js")
    js = open(sw, encoding="utf-8").read()
    # Precache every file of the static site, so any page works offline after one visit.
    import re
    files = sorted(os.path.relpath(os.path.join(dp, f), out).replace(os.sep, "/")
                   for dp, _, fs in os.walk(out) for f in fs
                   if not f.startswith(".") and f not in ("sw.js", "README.md"))
    js = re.sub(r"const ASSETS = \[.*?\];", "const ASSETS = " + json.dumps(files) + ";", js, count=1, flags=re.S)
    js = js.replace('const CACHE = "pestwatch-v4"', 'const CACHE = "pestwatch-static-v4"', 1)
    open(sw, "w", encoding="utf-8").write(js)
    # No favicon request (avoids a 404 in the console).
    for page in ("index.html", "extension.html", "field.html"):
        pp = os.path.join(out, page)
        h = open(pp, encoding="utf-8").read()
        if 'rel="icon"' not in h:
            h = h.replace("<head>", '<head>\n<link rel="icon" href="data:,">', 1)
            open(pp, "w", encoding="utf-8").write(h)
    open(os.path.join(out, ".nojekyll"), "w").close()   # GitHub Pages: serve files as-is
    with open(os.path.join(out, "README.md"), "w") as f:
        f.write("# PestWatch static demo\n\nGenerated by `scripts/build_static.py`. Pages:\n\n"
                "- `index.html`: dashboard and guided demo\n- `extension.html`: extension-officer view\n"
                "- `field.html`: farmer field app (on-device model; replies simulated in the browser)\n\n"
                "All numbers are simulation outputs. The live service (SMS/USSD/voice, persistence) needs `server.py`.\n")

    total, biggest = 0, []
    for dp, _, fs in os.walk(out):
        for f in fs:
            p = os.path.join(dp, f)
            n = os.path.getsize(p)
            total += n
            biggest.append((n, os.path.relpath(p, out)))
    biggest.sort(reverse=True)
    print(f"Built {out}: {total / 1e6:.1f} MB in {len(biggest)} files; largest: "
          + ", ".join(f"{p} {n / 1e6:.1f} MB" for n, p in biggest[:4]))
    print(f"Static demo frozen at day {snap['t_live']}; {len(snap['before']['farms'])} farms with a notice/alert before, "
          f"{len(snap['after']['farms'])} after farm {snap['trigger_farm'] + 1} confirms.")


if __name__ == "__main__":
    main()
