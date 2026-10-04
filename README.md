# PestWatch: community early warning for coffee leaf rust

Noor's coffee yields have slipped and she isn't sure why (the hackathon brief, Annex B). PestWatch helps her and her neighbours find out early:

1. A **1.65 MB on-device model** checks a photo of a coffee leaf offline.
2. **Store-and-forward** sends the result when there's signal.
3. A **space-time scan** turns many weak, uncertain reports from nearby farms into a village-level signal.
4. When the signal is weak, PestWatch **asks 12 nearby farmers to check 10 trees**.
5. If it's confirmed, farmers get an alert **in Kiswahili, English or French**, by app, SMS or voice call.

> *Kutu ya majani ya kahawa imeripotiwa katika eneo lako. Ripoti 21 kutoka mashamba 11 katika saa 72 zilizopita. Hatari: JUU. Kagua kahawa yako ndani ya saa 24.*
> ("Coffee leaf rust has been reported in your area. 21 reports from 11 farms in the last 72 hours. Risk: HIGH. Check your coffee within 24 hours.")

**Loop:** leaf photo → on-device detection → offline queue / sync → space-time aggregation → *watch* → targeted scouting requests → farmer confirmations → outbreak probability → localized, wind-aware alerts → response → all-clear.

**Fail-safe:** when the model isn't sure, the phone says **"Not sure — ask a person"** and routes the farmer to the lead farmer or extension officer. A person always makes the final call.

## Quick start

Core: Python 3.9+ with numpy only (use `/usr/bin/python3`). The web UI uses no CDNs and works offline.

```bash
python3 scripts/evaluate.py      # ~15 min: fits fusion, calibrates, ablation + sensitivity -> reports/EVALUATION.md
python3 scripts/run_demo.py      # picks a representative season -> web/data/scenario.js
open web/index.html              # dashboard (works from file://); web/extension.html = extension-officer view
python3 server.py                # live service on :8000: dashboard, field app, API, SMS/USSD/voice webhooks
open "http://localhost:8000/field.html?lang=sw&farm=81"
python3 scripts/walkthrough.py   # narrated end-to-end trace of one season -> docs/END_TO_END.md
python3 scripts/build_static.py  # static site for hosting -> dist/
python3 -m unittest discover -s tests
```

The vision model is already trained, in `models/pest_classifier/`. To retrain:

```bash
python3 -m venv .venv-ml && .venv-ml/bin/pip install -r requirements-ml.txt
.venv-ml/bin/python scripts/train_coffee.py
```

**Submitting?** Start with [`docs/SUBMISSION.md`](docs/SUBMISSION.md) (checklist, video script, hosting) and [`docs/DATA.md`](docs/DATA.md) (data sources and gaps). Then:
- [`docs/END_TO_END.md`](docs/END_TO_END.md): one season traced through every stage
- [`docs/PITCH.md`](docs/PITCH.md): the pitch
- [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md): the model
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): running it for real

## Headline results (simulated; see `reports/EVALUATION.md`)

60 outbreak and 60 outbreak-free seasons per variant, ~145 coffee farms in 6 villages, using the model's measured error rates (blended toward worse for field conditions). Every variant is recalibrated so at most 10% of outbreak-free seasons see a false alert. Each row adds one capability; figures are medians.

| Variant | Warning ahead of conventional route | Recall (warned before damage visible) | Precision of HIGH alerts | Yield loss avoided |
|---|---|---|---|---|
| Photos only (space-time scan) | 8.2 days | 84% | 86% | 48% |
| + farmer inspection replies | 8.2 | 85% | 82% | 51% |
| + **targeted scouting requests** | 12.9 | 90% | 84% | **70%** |
| + evidence fusion (scan + confirmations) | **13.8** | 91% | 78% | 73% |
| + wind-aware zones, alert budget, all-clear (full) | 13.8 | 92% | 82% | 71% |

**Full system:**
- **Lead time:** 34.5 days before rust is widespread.
- **Visible damage at season end:** 1 farm with PestWatch vs. 13 without.
- **Alert load:** 1.7 alerts per farm per season.
- **Running cost:** about **$125 per region-season ($0.87 per farm)**.
- **Benefit-cost ratio:** about 205×. This figure rests on assumed coffee yield (0.6 t/ha) and price ($3,000/t); treat it as indicative.

**What the evaluation shows:**
- **Asking beats waiting.** Targeted scouting is the biggest single gain (+19 points of loss avoided, +4.7 days of warning).
- **Adoption matters.** The lead holds at 40% app participation (12.4 days) but **breaks at 20%** (−2.2 days). Rust is slow and subtle, so it needs enough eyes.
- **A weaker model still helps.** With the classifier degraded halfway toward chance, the lead is still about 11 days.
- **Some features don't measurably help.** Wind-aware zones and the alert budget show no gain on these metrics. They're kept for trust and operations, not claimed as wins.

All numbers are simulation outputs with stated assumptions (`pestwatch/config.py`), not field results.

## Demo script (~4 min)

1. **Dashboard → ▶ Guided demo.** It pauses at each key moment with a caption:
   1. A normal season, including a photo surge that doesn't alarm.
   2. Rust spores arrive (ground truth; nothing visible yet).
   3. Weak reports start to cluster.
   4. PestWatch sends **scouting requests**, and farmers reply ("none found" ones matter too).
   5. The outbreak probability crosses the threshold, and alerts go out in each farmer's language and channel.
   6. An all-clear when the area goes quiet.
   7. The with/without comparison.
2. **Field app** (`field.html?lang=sw&farm=81`):
   1. Photograph a coffee leaf, or use `samples/leaf_rust_1.jpg` (rust, 99%). The on-device result is shown.
   2. A faint result shows **"Sina uhakika — muulize mtu"** ("Not sure — ask a person").
   3. Toggle **No signal**: reports queue, and replies are answered on the phone.
   4. Reply "nimeona kutu" ("I've seen rust"). The alert appears in Kiswahili.
3. **Extension view** (`extension.html`): villages ranked by urgency, days until damage is visible, hectares at risk, litres of fungicide to pre-position, and scouting replies, with CSV export.

Deep links: `index.html#t=35&layer=truth&arm=cf&farm=12&lang=sw&channel=voice`, `field.html?lang=sw&farm=81`.

## Offline-capable by design

- **On-device model:** about 15–20 ms per leaf. The photo never leaves the phone; only about 100 bytes of model output are uploaded.
- **Outbox:** photos, replies and language changes queue on the phone and sync when signal returns.
- **Replies:** answered instantly on the phone in the farmer's language, using the same keyword grammar as the server.
- **Area status:** the last-known status stays visible with its age.
- **Feature phones:** **SMS, USSD and voice** carry alerts, scouting requests and replies over plain cellular, with no mobile data.
- **All pages:** after one visit, every page is precached by a service worker.

Verified in Chrome with the network cut: every page reloads, the model classifies offline, and a Swahili reply is answered and queued, then syncs on reconnect.

## Hosting the static demo

`python3 scripts/build_static.py` builds `dist/` (about 15 MB). It holds the dashboard, extension view and field app with the on-device model, and works on any static host from any sub-path. Without a server, the field app uses a snapshot of the live demo and parses replies in the browser; a banner says so. In the submission repo the built site is committed as `site/`, and `.github/workflows/pages.yml` publishes it to GitHub Pages on every push to `main`. Enable it under Settings → Pages → Source: GitHub Actions.

## How it works

| Stage | Implementation |
|---|---|
| On-device detection | `models/pest_classifier/`: MobileNetV3-Small fine-tuned on **JMuBEN (Kenya), BRACOL (Brazil) and RoCoLe (Ecuador)** coffee leaves, all CC-BY-4.0. Classes: healthy, leaf rust, leaf miner, Cercospora, Phoma. Temperature-scaled, ONNX, 1.65 MB int8, run in the browser (`web/model.js`). Leaf rust recall 0.93, precision 0.79; healthy recall 0.94. |
| Fail-safe | Weak or ambiguous results trigger **"Not sure — ask a person"** plus a referral to the lead farmer or extension officer. A weak village signal triggers scouting requests, not alerts. Unclear SMS gets "not understood" rather than a guess. |
| Offline-first sync | Capture time and sync time are kept separately; the detector only sees what has synced. |
| Space-time scan | `pestwatch/scan.py`: prospective expectation-based Poisson scan (Neill 2005; Kulldorff 2001).<br>- Soft counts, so weak detections add up.<br>- Expected counts conditioned on the photos actually taken, with empirical-Bayes per-farm baselines.<br>- Needs at least 3 farms; per-farm caps against spammers.<br>- A sparse index for national scale. |
| Targeted scouting | At watch level, the nearest 12 farmers are asked to check 10 trees by app, SMS or voice, throttled to 2 requests per farmer per fortnight. Replies become strong evidence either way. |
| Fusion | `pestwatch/fusion.py`: logistic regression over scan score and farmer confirmations gives P(outbreak). The threshold is calibrated to the false-alarm budget. |
| Weather | `pestwatch/environment.py`: wind carries spores (dispersal and alert-zone shape); temperature sets rust development and the "visible in ~N days" forecast. Weather is simulated; NASA POWER / CHIRPS are the next step. |
| Alerting | `pestwatch/alerts.py`, `messaging.py`:<br>- risk tiers in wind-aware zones<br>- a budget of 3 alerts per fortnight<br>- all-clear after 7 quiet days<br>- every message in the farmer's language and channel; every SMS fits one GSM-7 segment |
| Operations | Extension view. `server.py` + `pestwatch/service/`:<br>- SQLite and an admin key<br>- rate limiting<br>- Africa's Talking-style SMS/USSD/voice webhooks<br>- a privacy-preserving public risk map<br>- exports |
| Simulation | `pestwatch/simulation.py`:<br>- rust introductions, temperature-driven growth, wind-driven spread<br>- farmer behaviour, lead farmers, spammers, nuisance events<br>- responses and costs<br>The PestWatch and counterfactual arms share random numbers. |

## Models used

| Component | What it is | Notes |
|---|---|---|
| Coffee leaf classifier | **Real, trained.** MobileNetV3-Small on JMuBEN, BRACOL and RoCoLe; on-device via ONNX. | Tested only on the same collections; healthy leaves come from Brazil and Ecuador; early rust is often missed. The simulation blends the measured error rates 30% toward a pessimistic matrix. |
| Outbreak detection | **Real, statistical.** Space-time scan statistic. | Calibrated on outbreak-free seasons. |
| Evidence fusion | **Real**, a small logistic model. | Fitted on simulated seasons; refit on real labelled seasons. |
| Disease spread, weather, behaviour | **Simulated**, for evaluation only. | Validate against cooperative and extension records. |
| Language: alerts, replies, UI, USSD, voice | **Real, deterministic.** Fixed list of answers; no generative text, so no hallucinated advice. | Native-speaker review pending. |

## Languages

Every farmer-facing string comes from one catalog per language (`pestwatch/i18n/catalogs/{en,sw,fr}.json`). The server, dashboard, field app, USSD menus and voice scripts all use the same catalogs.

- **Outputs:** each farmer has a preferred language and channel. SMS are checked to fit one GSM-7 segment; voice scripts are written for text-to-speech; plurals and number formats are correct.
- **Inputs:** replies are understood in any supported language, with negation ("sijaona kutu" = none found) and typos handled.
- **Less-supported languages:** add one with `python3 scripts/new_language.py <code>` (e.g. Kikuyu, Luhya). It isn't served until reviewed.

The Swahili and French text are **drafts awaiting native-speaker review**.

## Still to do outside the code

- Native-speaker review and recorded voice prompts.
- Photos from the target district: healthy leaves and early rust.
- Real weather (NASA POWER / CHIRPS).
- A cooperative and extension partner for a pilot.
- An Africa's Talking sandbox check.
- Consent and data-governance agreements.

## Layout

```
pestwatch/          config, region, environment, classifier, vision, scan, fusion, alerts, messaging,
                    farmers, simulation, metrics, baselines, costs
pestwatch/i18n/     catalogs/*.json, lookup/plurals, sms.py (GSM-7), inbound.py (reply parsing)
pestwatch/service/  store (SQLite), auth, ratelimit, gateways, dispatch, ussd, voice, privacy
models/             pest_classifier/ (coffee leaf ONNX model, labels, confusion matrix, metrics)
scripts/            evaluate.py, run_demo.py, walkthrough.py, build_static.py, train_coffee.py, new_language.py
server.py           HTTP service: static web/, farmer API, gateway webhooks, admin & public endpoints
web/                index.html (dashboard), extension.html (officers), field.html (offline field app), model.js
reports/            EVALUATION.md, evaluation.json
docs/               SUBMISSION.md, DATA.md, END_TO_END.md, PITCH.md, MODEL_CARD.md, DEPLOYMENT.md, LOVABLE_PROMPT.md
tests/              scan, classifier, alerts, simulation, environment, fusion, i18n, service, vision
```
