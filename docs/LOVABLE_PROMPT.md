# Lovable prompt: PestWatch PoC

Paste **Prompt 1** into Lovable to create the project. Then send Prompts 2–5 one at a time; Lovable works best in steps. Each prompt is self-contained.

---

## Prompt 1: project brief and app shell

```
Build "PestWatch", a proof-of-concept web app for the World Bank "Small AI for Development" hackathon (agriculture track).

ONE-LINE PITCH
PestWatch turns farmers' everyday phone photos and quick "did you see rust on your leaves?" replies into a village-level early warning for coffee leaf rust. It asks nearby farmers to check when a signal is weak, and alerts the community in each farmer's language (English, Swahili, French) before damage is visible.

SCOPE
This is a demo PoC, not a production app:
- No auth and no backend. Everything runs client-side.
- All data is simulated in the browser from a fixed random seed, so every reload gives the same season.
- Label simulated numbers as "simulated" in the UI.

TECH
- React + Vite + TypeScript + Tailwind + shadcn/ui.
- Charts: Recharts.
- Map: an HTML canvas in kilometre coordinates. Do not use map tiles; it must work offline.
- State: Zustand.
- Seeded RNG: implement a small mulberry32 PRNG. Do not use Math.random in the simulation.

PAGES (react-router)
1. "/": Dashboard with guided demo (desktop, dark theme)
2. "/field": Farmer field app (mobile-first, light theme, looks like a phone app)
3. "/about": How it works, honest limitations, and trade-offs

DESIGN
Dashboard dark theme:
- Surfaces #121211 (page), #1a1a19 (cards), #232321 (raised), hairlines #2e2e2b.
- Text #ffffff, #c3c2b7, #8b8a82.
- Series colours: "With PestWatch" = #3987e5 (blue), "Without" = #d95926 (orange).
- Status colours, always shown with an icon and a text label, never colour alone: good #0ca30c, warning #fab219, serious #ec835a, critical #d03b3b.

Charts:
- One y-axis per chart. Never dual axes.
- 2px lines, solid hairline gridlines, a legend.
- Direct labels at line ends.
- Hover crosshair tooltip.

Field app: light theme, background #f4f3ef, white cards, rounded-2xl corners, large tap targets.

Typography: system-ui. Header shows the "PestWatch" wordmark with a green ◉.

Create the shell now:
- routes and layout
- a top nav (Dashboard · Field app · About)
- placeholder pages
- the Zustand store
- the seeded RNG utility

Subsequent prompts will add the simulation, detection, UI and languages.
```

---

## Prompt 2: simulation and outbreak detection (the core)

```
Add src/sim/ with a deterministic simulation and the detection algorithm. It runs in the browser in under 2 seconds.

REGION
- 14 × 14 km. 6 villages named Namasoli, Kabula, Sirende, Mukuyu, Lutonyi, Chebukwa.
- Village centres are at least 3.2 km apart.
- Each village has 18–30 farms, Gaussian-scattered (sd 0.8 km) around its centre: about 145 farms total.
- Per farm:
  - id, x, y, village, hectares (0.4–1.6)
  - usesApp (70% true)
  - photoRatePerDay (gamma with mean 0.35, 0 if not an app user)
  - language ("sw" 65%, "en" 35%)
  - channel ("app" if usesApp; otherwise "sms" 75% or "voice" 25%)
- Per village:
  - onlineShare 0.2–0.85: the probability a report syncs immediately
  - offlineDelayHours 6–40: mean delay when offline
  - lookalikeDamage 4–14%: background leaf miner / Cercospora / Phoma damage

TIME
- 60 days in 6-hour steps (240 steps).
- Rust spores arrive on day 10 (after the rains): a Gaussian footprint (sigma 0.9 km) around a random farm.
  - Each farm inside it gets infected with probability 0.55·exp(−d²/2σ²), spread over 3 days.
  - Initial damage share is 0.02.

PEST DYNAMICS, per step dt = 0.25 day
- Within-farm logistic growth at 0.22/day for untreated farms.
- Between-farm spread:
  - force_j = 1.2 · Σ_i K(d_ij) · damage_i · emission_i
  - K(d) = exp(−d / 1.1 km), normalised so the mean row sum is 1
  - P(infect) = 1 − exp(−force · susceptibility · dt)
- Visible-to-farmer threshold: 25% damage. A farmer notices visible damage with probability 0.3/day, then treats 3 days later.
- Treated farms decline at 0.35/day; emission ×0.2, susceptibility ×0.25.

PHOTOS (the "on-device model" output)
- App farmers take Poisson(photoRate·dt) sessions per step, each of 1 + Poisson(1.5) photos.
- Each photo's plant is:
  - leaf rust with probability min(0.95, 3·damage)
  - else a look-alike with probability 2·lookalikeDamage
  - else healthy
- Simulated classifier: produce a probability vector over 5 classes: [healthy, leaf_rust, leaf_miner, cercospora, phoma].
  - Top-1 accuracy:
    - healthy: 0.92
    - rust, early lesions: 0.5, rising linearly to 0.88 at 30% infection
    - look-alikes: 0.72–0.78, with 8–12% mistaken for rust (Cercospora the most)
  - Top-class confidence: Beta with mean 0.8 when right, 0.58 when wrong.
  - Spread the remaining mass, giving the true class the runner-up share.
- Each report stores: farmId, captureTime, syncTime = capture + delay (instant if online, else exponential with the village's mean delay), probs.

NUISANCE EVENTS (prove robustness)
- 2 "extension training days": one village's photo rate ×3 for 3 days.
- 1 "look-alike flare-up": one village's lookalike damage ×2.5 for 10 days.

DETECTION: expectation-based Poisson space-time scan statistic (Neill 2005), run every step on reports with syncTime ≤ now
- Zones: a circle around every farm with radius in {0.6, 1.0, 1.5, 2.2} km × time windows {24, 48, 72} h ending now.
- Signal: C = Σ P(rust) of photos captured in the window (soft counts, so weak detections add up). Cap each farm's contribution at 3.
- Expected: E = Σ over photos in the window of that farm's baseline rate. The baseline comes from history in [now − window − 5 days − 21 days, now − window − 5 days]:
  - farmRate = (farmSignal + regional·60) / (farmPhotos + 60)
  - regional = (allSignal + 0.07·300) / (allPhotos + 300)
- LLR = C·ln(C/E) − (C − E) if C > E, else 0. Require at least 3 distinct farms with a photo of P(rust) ≥ 0.5 in the zone.
- Keep the best window per zone. Return up to 3 non-overlapping zones by LLR.
- WATCH if LLR ≥ 1.2. ALERT if the outbreak probability (below) ≥ 0.99.
  - Expose the thresholds in a settings panel.
  - Also expose a "calibrate" button. It runs 30 outbreak-free seeds and sets the ALERT threshold so at most 10% of them would ever alert.

TARGETED SCOUTING (key differentiator)
When a zone is at WATCH:
- Send a "scouting request" to the 12 nearest farmers, all channels.
- Limits: at most 1 round per zone per 3 days, and at most 2 requests per farmer per 14 days.
- 60% respond within about 0.5 day (exponential). They check 10 plants:
  - P(find rust) = 1 − (1 − min(1, 1.5·damage))^10 if the farm is infected (they check 10 coffee trees)
  - 3% false finds otherwise
- A reply becomes a report with P(rust) 0.95 ("found") or a healthy report ("none found").
- Farmers who find rust act within 1 day (on extension advice).

OUTBREAK PROBABILITY (fusion)
P(outbreak) = sigmoid(−1.92 + 1.05·LLR + 3.01·ln(1 + confirmations_72h) + 0.14·trapAnomaly).
- trapAnomaly is 0 if you skip traps.
- Show P(outbreak) instead of a raw score everywhere.

ALERTS (only in the "With PestWatch" arm)
For each ALERT zone, tier every farm by distance d to the zone centre:
- HIGH if d ≤ radius
- MEDIUM if d ≤ radius + 1.5 km
- LOW if d ≤ radius + 3 km

Rules:
- Re-alert a farm only on escalation, or a HIGH reminder after 72 h.
- Budget: at most 3 alerts per farm per 14 days; HIGH escalations always go through.
- Alerted farmers inspect with 75% compliance after 1/2/4 days by tier.
  - If infested, they find the pest with probability 0.45 + 15·damage, then treat within 1 day.
- 7 quiet days after the last ALERT in a zone → send an "all clear" to that zone's alerted farms.

COUNTERFACTUAL
- Simulate the same seed twice: "With PestWatch" (alerts and scouting) and "Without" (farmers act only when they notice damage).
- Use common random numbers so the two arms are identical until PestWatch first acts. Pre-draw the per-step, per-farm uniform randoms for infection, noticing and scouting.

METRICS (computed from both arms)
- **Lead time vs conventional:** (time of the 3rd farmer noticing + 5 days) − first PestWatch alert.
- **Lead time vs widespread:** (time 10% of farms are visibly damaged, in "Without") − first alert.
- **Recall:** share of infested farms alerted before their damage became visible.
- **Precision of HIGH alerts:** alerted farms infested, or that would have been infested in "Without", within 14 days.
- **Yield loss:**
  - per farm = ha · 2 t/ha · $280/t · min(1, 0.55 · peakDamage)
  - loss avoided = Without − With
- **Running cost:** SMS $0.008, voice $0.04, app push $0.0005, platform $0.30/farm/season, extension visit $5 per alerted village. Benefit-cost ratio = loss avoided ÷ cost.
- **Alerts per farm**, and the number of scouting requests.

Write unit tests (Vitest) for:
- the scan: an injected cluster is found in the right place; a photo-volume surge does NOT alarm; unsynced reports are invisible
- determinism
- the two arms being identical before the first action
```

---

## Prompt 3: dashboard and guided demo

```
Build the "/" Dashboard on top of the simulation.

HEADER
- Wordmark plus subtitle "Simulated region · 6 villages · N farms · seed S".
- Segmented toggles: [What PestWatch sees | Ground truth] and [With PestWatch | Without].
- A primary "▶ Guided demo" button.

TIMELINE BAR
- Play/pause, speed (1, 2 or 4 days per second), a range slider over 240 steps, and "Day 17.5" with a sub-label.
- Clickable markers under the slider: "rust arrives", "PestWatch alert", "1st farmer notices*", "conventional detection*", "widespread*" (* = without PestWatch).
- Stagger labels that would overlap.

MAP CARD (canvas)
Background: grid every 2 km, a scale bar, and village names drawn on top with a dark halo.

Layer "What PestWatch sees":
- Farms: filled grey dots for app users, hollow dots for SMS/voice users.
- Synced photos from the last 72 h, fading with age: grey if P(rust) < 30%, amber 30–60%, red ≥ 60%.
- A small hollow square next to farms with reports still queued offline.
- WATCH zones: dashed amber circles labelled "WATCH · n reports · f farms · P xx%".
- ALERT zones: red circle plus a faint outer buffer ring.
- Alerted farms: rings coloured by tier.
- Farms asked to scout: dashed aqua ring (#199e70) for about 1 day. Replies: a red triangle for "found", a hollow aqua circle for "none found".

Layer "Ground truth":
- Farms coloured by damage share (dark grey → #e66767).
- A green ring on treated farms.
- The landing footprint.

In the "Without" arm, force Ground truth: no reports or alerts exist.

Interaction:
- Clicking a farm selects it for the phone panel.
- Hovering shows a tooltip: farm name, app/SMS, photos synced, an alert if any, and damage share when in Ground truth.

KPI TILES (6, two rows of three)
Each tile shows "This season" plus a small grey note "simulated".
- lead time vs conventional (days)
- lead time vs widespread
- recall
- crop loss avoided ($ and %)
- benefit-cost ratio
- alerts per farm

PHONE PANEL
- A phone mock-up showing the selected farm's latest message in its preferred language and channel. Above it: "Prefers Kiswahili · SMS".
- Preview buttons [EN SW FR] and [App SMS Voice]. When previewing a non-preferred option, show a "previewing…" label in amber.
- Formats:
  - App push: a red risk badge with icon and label in the language, e.g. "▲ JUU"
  - SMS: a grey bubble, plus a character count
  - Voice: "📞 PestWatch is calling…" plus the script
- Below, the farm's last on-device photo result ("Healthy 82% · P(leaf rust) 6% · ✓ Synced" or "⏳ Queued offline").

EVENT FEED (newest first)
- Weak reports: "Farm 23 · Kabula: possible leaf rust (41%) — too weak to act on alone · synced 18 h late (offline)".
- Watch zones, scouting requests ("Asked 12 farmers near Mukuyu to check 10 plants"), replies, alert batches ("Alerts sent to 34 farms (12 HIGH · 10 MEDIUM · 12 LOW)"), all-clears.
- Nuisance events tagged NOISE: "(not an outbreak)".
- Ground-truth events tagged TRUTH, shown only in the Ground-truth layer.

CHARTS (three small charts in a row; single axis each; revealed up to the current time; cursor line)
1. "Farms with visible damage": With vs Without (%), with a "first alert" marker.
2. "Outbreak probability": 0–100%, with an alert threshold line and a watch line.
3. "Photo reports per day": synced photos, with the P(rust) ≥ 50% subset.

Plus a collapsible data table.

GUIDED DEMO
Build stops from the season's actual events and skip any that didn't happen. At each stop: pause, set the layer, show a caption card over the map bottom with "Next ▸", and auto-advance after 7 s.
1. Day 0.5: "A normal season in six coffee villages. Farmers photograph coffee leaves; an on-device model classifies each photo offline. Look-alike leaf problems cause occasional false flags."
2. Training-day surge: "Photo volume triples in X. A naive counter would panic; PestWatch compares against what's expected for that many photos, so it stays quiet."
3. Rust arrives (Ground truth): "Rust spores arrive near X after rain and infect N farms. Nobody can see it yet."
4. First WATCH: "Scattered low-confidence reports, each insignificant alone, start clustering."
5. First scouting request: "Instead of waiting, PestWatch asks the 12 nearest farmers to check 10 plants — by SMS, voice or app, in their language."
6. First alert: "P(outbreak) crosses the threshold. N farmers get a localized alert in their language. Select a farmer warned before the pest reached them."
7. First farmer notices (Without): "Without PestWatch, the first farmer would only notice now — X days after the alert."
8. Conventional detection: "…and the extension service would hear about it now."
9. Season end (Without arm, Ground truth): "N farms damaged without PestWatch vs M with. Crop loss avoided $X. ~$1 per farm per season. All numbers simulated."

Deep links: support #t=17&layer=truth&arm=cf&farm=12&lang=sw&channel=voice.
```

---

## Prompt 4: languages and farmer messages

```
Add src/i18n/ with one message catalog per language (en, sw, fr). Every catalog has the same keys; add a Vitest test that enforces key and placeholder parity. The Swahili and French text is a draft: show "draft translation" in /about.

Plurals:
- en: one when n = 1
- fr: one when n ≤ 1
- sw: one when n = 1

Number format: French uses a decimal comma ("1,2 km").

KEY STRINGS (use verbatim; the full catalogs are in pestwatch/i18n/catalogs/*.json)
ALERT head
- en: "Coffee leaf rust has been reported in your area."
- sw: "Kutu ya majani ya kahawa imeripotiwa katika eneo lako."
- fr: "Rouille orangée du caféier signalée dans votre zone."
ALERT tip
- en: "Check the underside of older leaves for orange, powdery spots. Nearest report: {km} km."
- sw: "Angalia upande wa chini wa majani ya zamani kama kuna madoa ya unga wa rangi ya chungwa. Ripoti ya karibu: km {km}."
- fr: "Regardez le dessous des feuilles âgées : taches orange poudreuses. Signalement le plus proche : {km} km."
SMS (one 160-char GSM-7 segment; test with worst-case 999/999)
- en: "PestWatch: Coffee leaf rust near you. {r} reports/{f} farms in {h}h. Risk {level}. Check leaves {when}. Reply 1=found 2=none"
- sw: "PestWatch: Kutu ya kahawa karibu nawe. Ripoti {r}, mashamba {f}, saa {h}. Hatari {level}. Kagua majani {when}. Jibu 1=nimeona 2=sijaona"
- fr: "PestWatch: Rouille du caféier près de vous. {r} signal./{f} expl. en {h}h. Risque {level}. Inspectez {when}. Répondez 1=trouvé 2=rien"
VOICE
- en: "Hello, this is PestWatch. {head} {counts} {risk} {action} {tip} If you find rust, press 1. If you find none, press 2. To hear this again, press 9. Again: {action}"
- sw: "Habari, huyu ni PestWatch. {head} {counts} {risk} {action} {tip} Ukiona kutu, bonyeza 1. Usipoona kutu, bonyeza 2. Kusikiliza tena, bonyeza 9. Narudia: {action}"
- fr: "Bonjour, ici PestWatch. {head} {counts} {risk} {action} {tip} Si vous trouvez de la rouille, tapez 1. Sinon, tapez 2. Pour réécouter, tapez 9. Je répète : {action}"
SCOUTING REQUEST (app)
- en: "PestWatch needs your help: there are unusual reports near you. Please check 10 coffee trees today (look under the older leaves), then reply 1 if you find orange rust spots or 2 if none. Thank you for protecting your community."
- sw: "PestWatch inahitaji msaada wako: kuna ripoti zisizo za kawaida karibu nawe. Tafadhali kagua miti 10 ya kahawa leo (angalia chini ya majani ya zamani), kisha jibu 1 ukiona madoa ya kutu au 2 usipoona. Asante kwa kulinda jamii yako."
- fr: "PestWatch a besoin de vous : des signalements inhabituels ont été faits près de chez vous. Vérifiez 10 caféiers aujourd'hui (dessous des feuilles âgées), puis répondez 1 si vous trouvez des taches de rouille ou 2 sinon. Merci de protéger votre communauté."
SCOUTING REQUEST (SMS)
- en: "PestWatch: Unusual reports near you. Please check 10 coffee trees today. Reply 1=rust found 2=none. Thank you!"
- sw: "PestWatch: Ripoti zisizo za kawaida karibu nawe. Kagua miti 10 ya kahawa leo. Jibu 1=nimeona kutu 2=sijaona. Asante!"
- fr: "PestWatch: Signalements inhabituels près de vous. Vérifiez 10 caféiers aujourd'hui. Répondez 1=rouille 2=rien. Merci!"
ALL CLEAR
- en: "All clear: no new coffee leaf rust reports near you for {days} days. Keep checking your trees once a week."
- sw: "Hali ni shwari: hakuna ripoti mpya za kutu ya kahawa karibu nawe kwa siku {days}. Endelea kukagua miti yako mara moja kwa wiki."
- fr: "Fin d'alerte : aucun nouveau signalement de rouille du caféier près de chez vous depuis {days} jours. Continuez à vérifier vos caféiers chaque semaine."
REPLY to a found report
- en: "Thank you. Your report helps protect your neighbours. Ask your extension officer or cooperative about approved fungicide and pruning; don't spray before you have advice."
- sw: "Asante. Ripoti yako inawalinda majirani zako. Muulize afisa wa ugani au chama cha ushirika kuhusu dawa ya kuvu iliyoidhinishwa na kupogoa; usinyunyize kabla ya kupata ushauri."
- fr: "Merci. Votre signalement protège vos voisins. Demandez à l'agent de vulgarisation ou à votre coopérative les fongicides homologués et la taille ; ne traitez pas sans conseil."
REPLY to none found
- en: "Thank you. Please check again in 3 days."
- sw: "Asante. Tafadhali kagua tena baada ya siku 3."
- fr: "Merci. Vérifiez à nouveau dans 3 jours."
REPLY to a referral
- en: "Thank you. Your lead farmer and extension officer have been asked to check your farm. Until then, please don't spray; keep a damaged leaf to show them."
- sw: "Asante. Mkulima kiongozi na afisa wa ugani wameombwa kukagua shamba lako. Hadi hapo, tafadhali usinyunyize dawa; hifadhi jani lililoharibika uwaonyeshe."
- fr: "Merci. Votre agriculteur relais et l'agent de vulgarisation ont été prévenus. En attendant, ne traitez pas ; gardez une feuille abîmée pour la leur montrer."
Risk levels: en HIGH/MEDIUM/LOW; sw JUU/WASTANI/CHINI; fr ÉLEVÉ/MOYEN/FAIBLE. Actions: en "Check your coffee within 24 hours." / "Check your coffee within 48 hours." / "Check your coffee this week."; sw "Kagua kahawa yako ndani ya saa 24." / "Kagua kahawa yako ndani ya saa 48." / "Kagua kahawa yako wiki hii."; fr "Inspectez vos caféiers dans les 24 heures." / "Inspectez vos caféiers dans les 48 heures." / "Surveillez vos caféiers cette semaine.".
Counts sentence: en "{reports} from {farms} in the last 72 hours." ("{n} report(s)", "{n} farm(s)"); sw "{Reports} kutoka {farms} katika saa 72 zilizopita." ("ripoti {n}", "shamba {n}" / "mashamba {n}"); fr "{reports} provenant {farms} au cours des 72 dernières heures." ("{n} signalement(s)", "d'une exploitation" / "de {n} exploitations").

REPLY PARSER (farmer replies in any language)
Normalise: lowercase, strip accents and apostrophes, keep only a–z, 0–9 and ?.
Intent keywords:
- PEST_FOUND: en: 1, yes, found, found it, rust, leaf rust, orange spots, orange powder; sw: 1, ndiyo, ndio, nimeona, nimeiona, kutu, madoa, unga wa chungwa; fr: 1, oui, trouve, jai trouve, rouille, taches orange, poudre orange
- NO_PEST: en: 2, no, none, nothing, not found, no rust, clean, didnt find, did not find; sw: 2, hapana, sijaona, sijaiona, hakuna, sikuona, hakuna kutu, salama; fr: 2, non, rien, aucune, aucun, pas de rouille, pas trouve, rien trouve
- HELP: en: help, menu, ?; sw: msaada, saidia, menyu; fr: aide, menu
- STOP: en: stop, unsubscribe, quit; sw: acha, sitisha, stop; fr: stop, arret, desabonner
- START: en: start, resume, subscribe; sw: anza, endelea, start; fr: reprendre, demarrer, start
- REFERRAL: en: ask a person, check my farm, extension officer, lead farmer; sw: kukagua shamba langu, afisa wa ugani, mkulima kiongozi, mtu aje; fr: quelquun de verifier, verifier mon champ, agent de vulgarisation, agriculteur relais
- LANGUAGE: a single language alias ("kiswahili", "swahili", "sw", "english", "en", "kiingereza", "francais", "fr", "anglais"), optionally preceded by "language", "lugha" or "langue".
Priority: STOP > START > LANGUAGE > HELP > REFERRAL > NO_PEST > PEST_FOUND. Negation wins: "sijaona kutu" = NO_PEST, "pas de rouille" = NO_PEST.
Matching: multi-word or short keywords by word boundary; single words of 5+ letters also match with 1 edit ("rouile", "nimeonaa").
Detect the reply's language from keywords unique to one language. Reply in the detected language, else the farmer's profile language.

Add Vitest cases for all the examples above.
```

---

## Prompt 5: field app with the real on-device model

```
Build "/field" as a mobile-first farmer app (max-width 480px, light theme). The whole UI is translated via the i18n catalogs, with a language picker in the header (English / Kiswahili / Français). Support ?lang=sw&farm=72.

The field app talks to the in-browser simulation through a mock API module (src/api/mock.ts) with getFarms(), getStatus(farmId), postObservations(), postReply(farmId, text) and setLanguage(). Write this module so it can later be swapped for real HTTP calls.

Freeze the demo at the last WATCH moment before the first ALERT, so a single confirmation tips the area into ALERT:
- recompute status with the new evidence, using the same scan + fusion
- or, simplest, switch to the precomputed post-confirmation state

Header: "PestWatch Field" (sw: "PestWatch Shambani"), the language picker, and an "● Online / ○ No signal" pill that toggles simulated offline mode.

CARDS
0. "My farm" select. Two option groups: "Near the current signal (demo)" (farms in the WATCH zone) and "All farms". Default to the first suggested farm. Default the language to the farm's registered language.
1. "1 · Take a crop photo". A dashed tap-to-capture box (<input type=file accept=image/* capture=environment>) with image preview. A select "Demo model output" (Healthy leaf / Early, faint rust spots / Obvious orange rust / Leaf miner (look-alike)) and a "No camera? Run the model without a photo (demo)" button.
2. "2 · On-device result (no internet needed)":
   - the top class and its %
   - bars for all 7 classes
   - an explanation: likely (≥60%) / possible (30–60%: "On its own this is too weak to act on; PestWatch combines it with nearby reports") / unlikely
   - a "Save report" button
3. "3 · Reports queue":
   - the last 8 reports with "⏳ queued" / "✓ synced"
   - "N reports waiting for signal"
   - a "Sync now" button
   - the note "Only the model's result (~100 bytes) is uploaded, never the photo."

   Photos, replies and language changes all go into a localStorage outbox, and sync when online or when the pill is toggled back.
4. "4 · My area": shows one of:
   - a red HIGH-risk alert card in the farmer's language
   - an amber "◆ Help check your area" scouting request
   - "◆ WATCH: Emerging signal near X: r reports from f farms in 72 h"
   - a green "● QUIET"
5. "5 · After inspecting":
   - "Did you find rust on your coffee leaves?" [Yes, I found some] [No, none found]: these send "1" / "2"
   - a free-text box "Or send a message in any language" (placeholder: "e.g. found rust / nimeona kutu / j'ai trouvé de la rouille")
   - a chat thread showing the farmer's messages (blue, right) and PestWatch replies (grey, left), with a small "PEST_FOUND · sw" tag

REAL ON-DEVICE MODEL (optional but impressive)
- Add onnxruntime-web, using the wasm backend with numThreads = 1.
- Load /models/pest_classifier/model_int8.onnx, labels.json and preprocess.json. I will upload these files to public/models/pest_classifier/.
- Preprocess: resize to 224×224, RGB / 255, normalise with mean [0.485, 0.456, 0.406] and std [0.229, 0.224, 0.225], NCHW float32. Input name "image", output "probs" (already softmaxed).
- When a photo is chosen, run the real model and show "On-device model: MobileNetV3-Small (real), xx ms". If the model fails to load, fall back to the demo output and say so.
- Add sample photos in public/samples/ (I will upload them).

PWA
- A manifest and a service worker that caches the app shell, the model and the wasm, so the field app works offline after the first load.

/ABOUT PAGE
Explain the loop: photo → on-device AI → offline sync → space-time scan → targeted scouting → outbreak probability → alerts in local languages by app, SMS or voice → all-clear.

Show "Small AI" facts: 1.6 MB model, offline, SMS/voice for feature phones, local languages.

Honest limitations, verbatim:
- "All impact numbers are simulated."
- "The vision model (MobileNetV3-Small, 1.65 MB; leaf rust recall 0.93 / precision 0.79, healthy recall 0.94) was trained on CC-BY-4.0 coffee leaf datasets (JMuBEN, Kenya; BRACOL, Brazil; RoCoLe, Ecuador) and tested only on the same collections; early rust is often missed and East African healthy leaves are not represented."
- "In simulation, wind-shaped alert zones and alert budgets showed no measurable gain; we kept them for trust, not as claimed wins."
- "Swahili and French messages are drafts pending native-speaker review."

Positioning: "Diagnosis apps (Plantix, PlantVillage Nuru) classify one photo; reporting tools (FAO FAMEWS) map reports. PestWatch turns many uncertain photos and two-second farmer replies into a village-level warning before damage is visible." Partners to name: ICIPE, FAO FAMEWS, CABI plant clinics.
```

---

## Files to upload to Lovable

These come from this repo:
- `models/pest_classifier/model_int8.onnx`, `labels.json`, `preprocess.json` → `public/models/pest_classifier/`
- `web/samples/*.jpg` → `public/samples/`. Use `leaf_rust_1.jpg` for the demo (rust, 99%).
- Optional, for exact wording: `pestwatch/i18n/catalogs/{en,sw,fr}.json` → `src/i18n/`

## Tips

- If Lovable gets the scan statistic or the counterfactual wrong, paste the relevant function from `pestwatch/scan.py` or `pestwatch/simulation.py`. They translate almost line by line to TypeScript.
- Check determinism first: the same seed must give the same season.
- Lovable's numbers won't match the Python evaluation exactly. For the pitch, quote `reports/EVALUATION.md` (60 seasons), not a single in-browser season.

---

## Prompt 6: offline capability (required by the updated problem statement)

```
The app must be fully offline-capable. Implement and verify the following.

1. PWA PRECACHE
- Use vite-plugin-pwa (Workbox) with injectManifest or generateSW.
- Precache the whole built app: all routes, JS/CSS, the i18n catalogs, onnxruntime-web's .wasm and .mjs files, public/models/pest_classifier/*, and public/samples/*.
- Raise maximumFileSizeToCacheInBytes to about 20 MB, because the wasm is 11 MB.
- Navigation fallback: index.html for SPA routes. Ignore query strings when matching, so /field?lang=sw&farm=72 works offline.
- Register the service worker on every route, not just /field. A user who only opened the dashboard must still be able to open /field offline.
- Cache-first for assets. Never cache /api/*.

2. ON-DEVICE EVERYTHING FOR THE FARMER
- Photo classification runs locally (onnxruntime-web wasm, numThreads = 1). Never send the photo anywhere.
- Persist the outbox (photos' model outputs, replies, language changes) in IndexedDB (use idb-keyval). Sync automatically on the "online" event and on the Online/No-signal toggle.
- Offline replies: when the farmer sends "1", "2" or free text while offline, run the same reply parser on the phone (Prompt 4). Immediately show the reply in their language, followed by "(answered on this phone; it will be sent when you have signal)".
  - sw: "(imejibiwa kwenye simu hii; itatumwa ukipata mtandao)"
  - fr: "(réponse donnée sur ce téléphone ; elle sera envoyée dès que vous aurez du réseau)"

  When the message later syncs, replace that local reply with the server's.
- Offline status: keep the last area status with a timestamp. When offline, show "📴 Last updated {when}. You are offline: showing the saved status." (translated).
- Language switches made offline apply to the UI immediately.

3. DASHBOARD OFFLINE
- No CDN fonts, scripts or map tiles anywhere.
- Show a small fixed badge "📴 Offline: running from this device's cache" when navigator.onLine is false.

4. FEATURE PHONES NEED NO DATA
On /about, state that alerts, scouting requests and replies use SMS, USSD and voice over the cellular network, so they work without mobile data. A village hub (a laptop or Raspberry Pi running the backend on a local hotspot) can operate with no internet and sync upstream when connected.

5. VERIFY
Write a Playwright test:
1. Load / online and wait for the service worker to be active.
2. context.setOffline(true).
3. Reload /, /field?lang=sw&farm=72 and /about. All must render with no console errors.
4. Classify public/samples/leaf_rust_1.jpg and expect leaf_rust with P(rust) ≥ 0.8.
5. Send "sijaona kutu" and expect an instant Swahili NO_PEST reply plus the offline note.
6. setOffline(false) and expect the outbox to sync.
```
