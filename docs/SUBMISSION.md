# Submission pack: Small AI for Development Hackathon, Agriculture

**Deadline: Sat 4 Oct 2026, 09:00 ET (18:30 IST).** Requirements from the concept note (`~/Downloads/file.pdf`), §06–09. Scenario: Annex B, Noor's coffee.

## 1. What must be submitted (§08)

| # | Deliverable | Status | Where |
|---|---|---|---|
| 0 | Application through Hack-Nation (before the weekend) | **⚠ You confirm.** Registration and prior-work rules (we built before the weekend) | Hack-Nation |
| 1 | **Prototype**: the working tool, with the code or a link | ✅ Built. **⚠ Needs hosting** (§4) and a code link (repo or zip) | `dist/` (static demo), whole repo (code) |
| 2 | **Three videos, each ≤ 1 min**: intro, demo, technical walkthrough | **⚠ Record them.** Scripts are in `submission/1_video/` | — |
| 2a | Problem statement in their one-sentence template | ✅ Drafted (§3) | — |
| 2b | AI capabilities, why a simpler tool can't do it, guardrails | ✅ Drafted | — |
| 2c | End-to-end demo of the user journey | ✅ Field app plus dashboard | — |
| 2d | Where the tool sits in the user's day, plus tech stack | ✅ Drafted | — |
| 2e | "Your take": what localizing AI means to you | **⚠ Personalise** the draft | — |
| — | Cite every dataset, its license and size, and what it doesn't cover | ✅ | `docs/DATA.md` |

## 2. Rules compliance (§06)

| Rule | How PestWatch meets it |
|---|---|
| **Runs on a device the user already has** | Noor has a basic phone, so she gets SMS, USSD and voice-call alerts and replies (keypad 1/2). Her daughter's smartphone can run the field app at the weekend for leaf photos. Nothing requires a new device. |
| **Core feature works offline** | The on-device model (≈15–20 ms), the store-and-forward outbox, instant on-phone replies, the cached area status and the PWA cache were all tested with the network cut. SMS, USSD and voice need no mobile data. |
| **Model small enough to side-load or send over a weak link** | **1.65 MB** int8 ONNX model, plus an 11 MB runtime that is cached once. |
| **At least one local-language interaction, by voice or text; name the language** | **Kiswahili** text (app, SMS, USSD) and a voice-call script, plus French and English. **Less-supported languages** (e.g. Kikuyu): add a catalog and keywords with no code change. A new language isn't served until reviewed. Voice could start from Meta MMS. See `docs/DATA.md`. |
| **Human in the loop: a person makes the final call** | Alerts tell the farmer to *check her trees*. Spraying is never automatic. Advice: "ask your extension officer or cooperative about approved fungicide; don't spray before you have advice". |
| **Fail-safe: "not sure — ask a person" (pass/fail)** | **Phone:** a weak or ambiguous leaf result shows "Not sure — ask a person" and a button that sends a referral to the lead farmer or extension officer. **System:** a weak village signal is answered by *asking 12 nearby farmers to check* (watch level), not by alerting. **Text:** an unclear SMS gets "Sorry, we did not understand", never a guess. |
| **Avoid hallucinations** | There's no generative text. Every message comes from a fixed list of answers in the language catalogs. |

## 3. Videos (three, each ≤ 1 min)

Scripts with timings, on-screen actions and the numbers you may quote are in `submission/1_video/`. Together they cover all the brief's required video parts:

| Video | Covers |
|---|---|
| `VIDEO_1_INTRO.md`: you and the problem | the problem-statement sentence (their template), and your take on localizing AI |
| `VIDEO_2_DEMO.md`: Noor's journey | the end-to-end demo (photo → on-device result → offline queue → "not sure — ask a person" → scouting → alert → reply), and where it sits in her day |
| `VIDEO_3_TECHNICAL.md`: how it works | the AI capabilities, why SMS or a spreadsheet can't do it, the guardrails, results, and the tech stack |

## 4. Hosting the demo (GitHub Pages)

The submission repo contains the built static demo in `site/` (dashboard, extension view and field app with the coffee model, about 15 MB, offline-capable). It also has a workflow, `.github/workflows/pages.yml`, that publishes `site/` on every push to `main`.

1. Upload the repo to a public GitHub repository.
2. Go to *Settings → Pages → Source: GitHub Actions*.
3. Run *Actions → Deploy demo to GitHub Pages*, or push any commit.
4. The demo is then at:
   - dashboard: `https://<username>.github.io/<repo>/`
   - field app: `https://<username>.github.io/<repo>/field.html?lang=sw&farm=81`
   - extension view: `https://<username>.github.io/<repo>/extension.html`

Detailed steps, including the web-uploader limits and an optional Lovable landing page, are in `submission/2_demo/HOSTING.md`.

## 6. Judging criteria: where the evidence is (§09)

| Criterion (weight) | Evidence |
|---|---|
| Built solution, Small AI fidelity (25%) | Works end to end offline on the farmer's phone; 1.65 MB model; SMS, USSD and voice; tested with the network cut |
| Development relevance & impact (20%) | Noor's coffee scenario: unexplained yield loss, rare extension visits, local language. About 71% of loss avoided and about $0.87 per farm (simulated) |
| Data grounding (15%) | `docs/DATA.md`: three coffee leaf datasets with licenses and sizes, and **what's not covered** |
| Evidence it works (15%) | `models/pest_classifier/metrics.json`, `reports/EVALUATION.md` (an ablation that adds one capability at a time, sensitivity tests, a no-PestWatch counterfactual), test suite, `docs/END_TO_END.md` |
| Value proposition of AI (15%) | Vision on the phone, aggregation of weak signals, language understanding; why SMS or a spreadsheet can't do this (video part 2) |
| Scalability & what's next (10%) | The pipeline is disease-agnostic (the same scan and alerts work for other crops); a sparse index scales the scan to national level; a new language is one file. Partners: coffee cooperatives, extension services, ICIPE, CABI plant clinics |
| Responsible AI (pass/fail) | Fail-safe, human in the loop, fixed answers, photos stay on the phone, consent, coarsened maps, admin key, data-gap disclosure |

## 7. Honest weak spots to address in Q&A

- **Healthy East African leaves aren't in the training data.** JMuBEN, from Kenya, has no usable healthy images; healthy leaves come from Brazil and Ecuador. False positives on Noor's own healthy leaves are untested. Collecting photos is the first pilot task.
- **Early rust is the model's weakest point.** That's exactly why PestWatch combines many farms' weak signals, asks people to check, and says "not sure" instead of guessing.
- **Impact figures are simulated.** The benefit-cost ratio of about 205× depends on assumed coffee yield and price. The key result for the pitch is about two weeks of extra warning, and that it breaks below about 40% app adoption.
