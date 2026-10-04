# How PestWatch works, end to end

This walks one coffee leaf photo through the whole system: what happens at each step, which file does it, and the actual numbers the code uses.

## 1. The big picture

```
 FARMER'S PHONE (offline)                 SERVER / VILLAGE HUB                       FARMERS & EXTENSION
 ───────────────────────                  ────────────────────                       ───────────────────
 ① Photo of a coffee leaf
 ② On-device model → 5 probabilities
 ③ "Not sure? ask a person" check
 ④ Outbox (store-and-forward) ──sync──▶ ⑤ Reports stored (capture time, sync time)
                                          ⑥ Space-time scan: are weak reports clustering?
                                          ⑦ WATCH ──▶ ⑧ Scouting requests ───────────▶ 12 nearest farmers: "check 10 trees"
                                                                                          │ reply 1 / 2 (SMS, app, keypad)
                                          ⑨ Fusion: P(outbreak) = f(scan, confirmations) ◀─┘
                                          ⑩ ALERT ──▶ ⑪ Per-farmer alerts ────────────▶ app push / SMS / voice, in her language
                                                       ⑫ Extension task list ─────────▶ officer: who to visit, fungicide to stock
                                          ⑬ 7 quiet days ──▶ all-clear ─────────────────▶ "all clear near you"
```

Three kinds of AI are used:
- **Computer vision** on the phone (steps 2–3).
- **Statistical machine learning** across farms (steps 6 and 9).
- **Language understanding** of farmer replies (steps 8 and 11).

Everything else is plumbing that keeps those three working offline, in the farmer's language, with a person making the final decision.

---

## 2. On the phone

### ① Taking the photo
**What:** the farmer, or her daughter at the weekend, photographs a coffee leaf, ideally the underside of an older leaf filling the frame.
**Where:** `web/field.html` and `web/field.js`, an installable web app (PWA).

### ② The on-device model
**What:** the photo is shrunk to 224×224 pixels and normalised, then run through **MobileNetV3-Small**. The output is five probabilities that add up to 1:

```
healthy 0.26 · leaf_rust 0.50 · leaf_miner 0.12 · cercospora 0.11 · phoma 0.01
```

**Where:**
- the model: `models/pest_classifier/model_int8.onnx` (1.65 MB)
- the browser code: `web/model.js`, using onnxruntime-web (WebAssembly in `web/vendor/ort/`)

**Training:**
- **Base:** ImageNet-pretrained MobileNetV3-Small, fine-tuned on three open coffee-leaf datasets: JMuBEN (Kenya), BRACOL (Brazil) and RoCoLe (Ecuador).
- **Calibration:** a "temperature" (T = 0.81) is baked into the model, so a 0.9 means roughly 90% likely.
- **Quantisation:** weights are stored as 8-bit integers, shrinking the file from 6.1 MB to 1.65 MB with the same accuracy.
- **Speed:** about 15–20 ms per photo in the browser.

**How good it is** (real held-out test images, 2,066 of them):
- It finds 93% of rust leaves, and 79% of its rust calls are right.
- It recognises 94% of healthy leaves, and called only 4 of 139 healthy test leaves rust.
- **Weak spots:** early rust, Phoma, Cercospora. And healthy leaves from East Africa aren't in the data.

### ③ The fail-safe: "Not sure — ask a person"
**What:** before trusting the result, the phone checks whether it is clear-cut. It shows **"Not sure — ask a person"** if any of these hold:
- the top class is below **60%**, or
- the top two classes are within **20 points** of each other, or
- rust is between **30% and 60%** (possible but unclear).

The farmer is told *not to spray because of this photo*. A button sends a referral ("please send someone to check my farm") to the lead farmer or extension officer.

**Where:** `web/field.js` (constants `UNSURE_TOP`, `UNSURE_MARGIN`).

This is the brief's pass/fail requirement: when the data isn't enough, the AI points to a person instead of guessing.

### ④ Store-and-forward outbox
**What:** the photo itself **never leaves the phone**. What gets queued is only the result, about 140 bytes:

```json
{"id": "…", "farm": 80, "captured_at": "2026-10-04T08:12", "probs": [0.257, 0.498, 0.125, 0.106, 0.014], "model": "mnv3s-coffee-v1"}
```

**Behaviour:**
- Photos, text replies and language changes all go into one **outbox** on the phone (browser storage).
- When signal returns (the browser's `online` event, or the Online/No-signal toggle), the outbox syncs automatically.
- If the farmer replies while offline, the phone **answers immediately** using the same keyword rules as the server, marked "answered on this phone". The server's reply replaces it after sync.
- The last-known area status stays on screen with its age ("last updated…, you're offline").
- A **service worker** (`web/sw.js`) caches the app, the model and the WebAssembly runtime, so the app reopens with no internet at all.

---

## 3. On the server, or a village hub

The server is `server.py` plus `pestwatch/service/`: Python and numpy only, with SQLite storage. It can run on a laptop or Raspberry Pi with no internet.

### ⑤ Storing reports
Each report keeps **two times**:
- the **capture time**, when the photo was taken, which is what the analysis uses;
- the **sync time**, when it arrived.

The detector only ever sees reports that have synced, so a phone that was offline for a day still counts, at the right time.

### ⑥ Space-time scan: are weak signals clustering?
**The question:** "Around any farm, in the last 1–3 days, is there *more rust signal than normal*, given how many photos were taken?"

**How**, in `pestwatch/scan.py`, an "expectation-based Poisson scan statistic" (Neill 2005):

1. **Zones.** For every farm, draw circles of **0.6, 1.0, 1.5 and 2.2 km**, combined with time windows of **24, 48 and 72 h** ending now. These overlapping "cylinders" are the candidate zones.
2. **Observed signal C.** Add up P(rust) over all photos in the zone. These are *soft counts*: five photos at 40% count about as much as two photos at 100%. That's how weak reports add up.
3. **Expected signal E.** For each photo, use that farm's own usual rust probability, learned from a 21-day history that ends 5 days before the window, so a growing outbreak doesn't hide itself. It's shrunk toward the regional average (weight of 60 photos) so new farms aren't erratic. Because E scales with the number of photos, a training day where everyone photographs a lot does **not** look like an outbreak.
4. **Score.** If C > E, the score is LLR = C·ln(C/E) − (C − E), which is high when C is much bigger than E. Two extra rules:
   - At least **3 different farms** must have a ≥50% rust photo in the zone.
   - Each farm's contribution is **capped at 3**, so one farm, or a spammer, can't make a cluster on its own.
5. Keep the best window per zone, and return up to 3 non-overlapping zones.

**Why it beats simple counting:** a raw count of rust reports can't tell "more photos" from "more rust", and can't say *where*. The scan does both.

### ⑦ Watch level
A zone reaches **WATCH** when its scan score is **≥ 1.2** or its outbreak probability (step 9) is **≥ 40%**. Watch means "something might be happening here, but we're not sure". So the system asks rather than alerts.

### ⑧ Targeted scouting requests
**What:** the **12 nearest farmers** to the watch zone get a message: *"PestWatch inahitaji msaada wako… kagua miti 10 ya kahawa leo…"* ("PestWatch needs your help… please check 10 coffee trees today"). It goes by app, SMS or voice call, in each farmer's own language.

**Limits:**
- one round per zone every 3 days;
- at most **2 requests per farmer per fortnight**, so nobody gets nagged.

**Replies:** farmers answer `1` (found rust) or `2` (none found). Free text works too, in any of the three languages. The parser (`pestwatch/i18n/inbound.py`, and `web/reply_parser.js` on the phone):
- lower-cases the text, strips accents, and tolerates one-letter typos;
- matches keywords per language, with negation winning ("**sijaona** kutu" means *no* rust, even though "kutu" means rust);
- detects which language the farmer wrote in and replies in that language.

**Effect:** a "found" reply becomes a report with P(rust) = 0.95; a "none found" reply becomes a healthy report. Both are strong evidence, because a person looked at 10 trees.

**Measured value:** in the evaluation, asking farmers was the **biggest single improvement**: +4.7 days of warning and +19 points of loss avoided.

### ⑨ Fusion: one outbreak probability
**What:** combine the scan score with how many farmers confirmed rust in the zone (last 72 h):

```
P(outbreak) = sigmoid( −1.42 + 0.46 × scan_score + 2.87 × ln(1 + confirmations) )
```

**Where:**
- the model: `pestwatch/fusion.py`
- the fitted weights: `data/fusion.json`, learned from ~108k simulated zone snapshots; replace them with a fit on real seasons later

**Alert threshold:** P ≥ **0.951**. It's calibrated so that at most **10% of outbreak-free seasons** would ever raise a false alert. Worked example of what it takes to alert:

| Farmer confirmations in the zone | Scan score needed |
|---|---|
| 0 | ≥ 9.5 (photos alone must be very strong) |
| 1 | ≥ 5.2 |
| 2 | ≥ 2.7 |
| 3 | ≥ 0.9 |

So a couple of human confirmations matter more than lots of uncertain photos. That's the "human in the loop" built into the maths.

### ⑩–⑪ Alerts, per farmer
When a zone reaches **ALERT**, every farmer near it is assigned a risk tier by distance from the zone centre:

| Distance | Tier | Action in the message |
|---|---|---|
| inside the zone | **HIGH** | "Check your coffee within 24 hours" |
| up to +1.5 km | HIGH if the signal is very strong, else MEDIUM | "…within 48 hours" |
| up to +3 km | MEDIUM or LOW | "Check your coffee this week" |

**Refinements:**
- **Wind:** distances are stretched downwind and shrunk upwind, since spores travel with the wind. The zone keeps the same area as a circle; it's only reshaped.
- **No fatigue:** a farmer is re-alerted only if her risk goes up, or with a HIGH reminder after 72 h, and at most **3 alerts per fortnight**. HIGH escalations always go through.
- **Language and channel:** each farmer has a preferred language (Kiswahili, English or French) and channel. One alert becomes three message formats (`pestwatch/messaging.py`):
  - **App:** the full text.
  - **SMS:** a short version guaranteed to fit **one 160-character GSM-7 message**. Characters that would force expensive Unicode SMS are transliterated.
  - **Voice:** a text-to-speech script with keypad replies (1 = found, 2 = none, 9 = repeat). The text is checked by tests for every language at worst-case numbers.
- **Fixed list of answers:** every word comes from `pestwatch/i18n/catalogs/{en,sw,fr}.json`. Nothing is generated, so the system can't invent advice. The advice always ends with "ask your extension officer or cooperative about approved fungicide; don't spray before you have advice".

Example HIGH alert in Kiswahili:
> *Kutu ya majani ya kahawa imeripotiwa katika eneo lako. Ripoti 21 kutoka mashamba 11 katika saa 72 zilizopita. Hatari: JUU. Kagua kahawa yako ndani ya saa 24.*
> ("Coffee leaf rust has been reported in your area. 21 reports from 11 farms in the last 72 hours. Risk: HIGH. Check your coffee within 24 hours.")

### ⑫ Extension and cooperative view
`web/extension.html` ranks villages by urgency. For each it shows:
- outbreak probability and trend;
- reports and confirmations;
- **days until damage becomes visible**, estimated from temperature;
- farms and hectares at risk, and **litres of fungicide to pre-position**;
- scouting replies, and alerts sent by channel and language.

It also lists which farms to visit, and exports CSV.

### ⑬ All-clear
If an alerted zone stays quiet for **7 days**, its farmers get *"Hali ni shwari…"* ("all clear"). This keeps alerts credible and stops unnecessary spraying.

### Feature phones: no data needed
- **USSD menu:** 1 found, 2 none, 3 area status, 4 language, 0 help.
- **SMS** replies and **voice calls** with keypad input.

All three work over the basic cellular network (`/gateway/ussd`, `/gateway/sms` and `/gateway/voice`, in Africa's Talking format). Noor never needs a smartphone or mobile data to receive warnings or reply.

### Privacy and safety
- Photos stay on the phone; only probabilities are uploaded.
- The public risk map (`/api/public/risk`) uses a ~1 km grid, includes only farmers who consented, and hides cells with fewer than 3 farms.
- Admin actions need an API key, and requests are rate-limited.

---

## 4. How the numbers were produced (the evaluation)

There's no field data yet, so the system was tested in a **simulation** (`pestwatch/simulation.py`, `scripts/evaluate.py`):

1. **A synthetic region:** 6 villages, ~145 coffee farms, 70% of farmers using the app, different connectivity per village.
2. **Rust dynamics:**
   - spores arrive after the rains and infect a few farms;
   - infection grows within each farm (faster near 22 °C);
   - it spreads to neighbours, more strongly downwind;
   - farmers notice it themselves only once about 25% of leaves are affected.
3. **The camera model:** the real coffee model's **measured** error rates from its test set, blended 30% toward worse, because field photos are harder than test photos.
4. **Nuisance events** to try to fool the system: training days with triple the photo volume, and flare-ups of look-alike diseases.
5. **Two copies of every season** with the same random draws: one *with* PestWatch and one *without* (farmers act only once they see damage). The difference is the impact.
6. **Calibration:** every setup's alert threshold is set on 60 outbreak-free seasons, so false alarms stay at or below 10%.
7. **Ablation:** capabilities are added one at a time (photos → replies → scouting → fusion → wind → budget) to see what each one adds.

**Results (medians over 60 seasons):**

| | Photos only | Full system |
|---|---|---|
| Warning ahead of the conventional route* | 8.2 days | **13.8 days** |
| Yield loss avoided | 48% | **71%** |
| Farms visibly damaged at season end | — | 1 (vs. 13 without PestWatch) |
| Running cost | — | about $0.87 per farm per season |

\*The conventional route is 3 farmers noticing damage, plus 5 days for the extension service to hear.

**Limits we found:**
- It needs **at least about 40% of farmers** taking photos; at 20% it fails.
- Wind-shaped zones and the alert budget didn't measurably help.
- All of this is simulated and needs a field pilot.

---

## 5. Where everything lives

| Step | File |
|---|---|
| Field app (photo, outbox, fail-safe, replies) | `web/field.html`, `web/field.js`, `web/model.js`, `web/reply_parser.js`, `web/sw.js` |
| Vision model and training | `models/pest_classifier/`, `scripts/train_coffee.py`, `docs/MODEL_CARD.md` |
| Space-time scan | `pestwatch/scan.py` |
| Fusion | `pestwatch/fusion.py`, `data/fusion.json`, `data/calibration.json` |
| Scouting, alerts and simulation loop | `pestwatch/simulation.py`, `pestwatch/alerts.py`, `pestwatch/messaging.py` |
| Languages | `pestwatch/i18n/catalogs/*.json`, `pestwatch/i18n/inbound.py`, `pestwatch/i18n/sms.py` |
| Server, SMS/USSD/voice, privacy | `server.py`, `pestwatch/service/` |
| Dashboards | `web/index.html`, `web/app.js`, `web/extension.html`, `web/extension.js` |
| Evaluation and demo data | `scripts/evaluate.py`, `scripts/run_demo.py`, `reports/EVALUATION.md` |
| Static hosting build | `scripts/build_static.py` → `site/` |
| Settings and assumptions | `pestwatch/config.py` |
