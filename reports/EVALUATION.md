# PestWatch evaluation (simulated)

60 outbreak and 60 outbreak-free seasons of 75 days per variant (~145 farms, 6 villages, 70% app participation). Every variant is recalibrated so that at most 10% of outbreak-free seasons raise a false alert. Classifier confusion matrix: **measured (smoothed, 30% field-shift blend)** (measured = held-out test set of the trained model; assumed = hand-set).

All numbers come from simulation with assumed parameters (`pestwatch/config.py`), not field data. Medians with interquartile ranges.

## Ablation: what each capability adds

| Variant | Days after rust arrives | Lead vs conventional | Recall (warned before visible) | Precision, HIGH | Alerts / farm | Yield loss avoided |
|---|---|---|---|---|---|---|
| photos only | 25.2 d (18.8 d–28.4 d) | 8.2 d (4.9 d–14.3 d) | 84% (77%–91%) | 86% (76%–91%) | 1.5 (1.0–1.8) | 48% (43%–52%) |
| + farmer inspection replies | 25.2 d (18.8 d–28.4 d) | 8.2 d (4.9 d–14.3 d) | 85% (76%–93%) | 82% (71%–91%) | 1.8 (1.4–2.2) | 51% (43%–56%) |
| + targeted scouting requests | 22.5 d (16.0 d–25.2 d) | 12.9 d (8.8 d–17.5 d) | 90% (79%–95%) | 84% (73%–91%) | 1.4 (1.2–1.8) | 70% (62%–74%) |
| + evidence fusion (scan + farmer confirmations) | 20.4 d (16.9 d–25.1 d) | 13.8 d (9.2 d–17.9 d) | 91% (80%–95%) | 78% (66%–88%) | 1.8 (1.3–2.3) | 73% (68%–79%) |
| + wind-aware alert zones | 20.4 d (16.9 d–25.1 d) | 13.8 d (9.2 d–17.9 d) | 94% (87%–96%) | 82% (71%–92%) | 1.7 (1.4–2.4) | 72% (66%–78%) |
| + alert budget & all-clear (full) | 20.4 d (16.9 d–25.1 d) | 13.8 d (9.2 d–17.9 d) | 92% (84%–95%) | 82% (70%–91%) | 1.7 (1.3–2.2) | 71% (66%–78%) |

Fusion model (logistic regression on simulated training seasons): scan_llr +0.46, farmer_confirmations +2.87, trap_anomaly +0.00, bias -1.42.

## Baselines at the same false-alarm budget (photos only)

| Detector | Detected | Days after rust arrives | Lead vs conventional |
|---|---|---|---|
| PestWatch scan (photos only) | 100% | 25.2 d (18.8 d–28.4 d) | 8.2 d (4.9 d–14.3 d) |
| Regional count, no spatial aggregation | 100% | 26.6 d (24.0 d–30.1 d) | 7.0 d (3.1 d–10.6 d) |
| Single-farm rule, no community aggregation | 100% | 25.9 d (23.8 d–29.6 d) | 7.6 d (3.8 d–9.9 d) |

Conventional route = 3 farmers notice damage + 5-day reporting lag. Widespread = 10% of farms visibly damaged (counterfactual).

## Full system: impact and cost

- Lead vs widespread infestation: 34.5 d (29.2 d–39.9 d)
- Farms visibly damaged at season end: 1 (0–2) with PestWatch vs 13 (9–18) without
- Yield loss avoided: 25,492 USD (19,186 USD–29,518 USD) per region-season (71% (66%–78%))
- Running cost: 125 USD (122 USD–131 USD) per region-season (0.87 USD (0.83 USD–0.90 USD) per farm): messaging 4 USD (3 USD–5 USD), platform 43 USD (41 USD–46 USD), lead-farmer stipends 60 USD (60 USD–60 USD), extension visits 20 USD (15 USD–25 USD)
- **Benefit-cost ratio: 205× (156×–231×)** (yield loss avoided ÷ running cost; excludes development, devices and farmers' own control costs)
- Alert load: 1.7 (1.3–2.2) alerts per farm per season (24 (1–68) suppressed by the budget), 342 (312–408) scouting requests, 102 (66–130) all-clear messages
- Anticipatory-finance triggers per outbreak season: 3 (2–4)

## Sensitivity of the full system

16 outbreak + 16 outbreak-free seasons per point, each point recalibrated to the same false-alarm budget (so a change can move the threshold as well as the signal). Differences of a few days are within noise at this sample size; read for direction and breaking points, not decimals.

| Factor | Value | Detected | Lead vs conventional | Recall | Precision HIGH | Loss avoided |
|---|---|---|---|---|---|---|
| App participation | 20% | 94% | -2.2 d (-9.2 d–10.6 d) | 84% (73%–93%) | 77% (60%–95%) | 42% (26%–52%) |
| App participation | 40% | 100% | 12.4 d (6.2 d–16.8 d) | 84% (78%–93%) | 76% (57%–91%) | 63% (51%–72%) |
| App participation | 70% | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| App participation | 90% | 100% | 24.2 d (15.6 d–25.5 d) | 95% (86%–97%) | 69% (53%–79%) | 78% (72%–85%) |
| Classifier degraded toward chance | 0% | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Classifier degraded toward chance | 25% | 100% | 10.8 d (6.2 d–18.6 d) | 89% (75%–95%) | 86% (74%–94%) | 75% (69%–79%) |
| Classifier degraded toward chance | 50% | 100% | 10.9 d (5.4 d–17.4 d) | 82% (72%–92%) | 85% (76%–94%) | 72% (69%–74%) |
| Offline delay (h, min–max) | (3, 20) | 100% | 13.8 d (10.4 d–19.6 d) | 91% (87%–96%) | 82% (72%–89%) | 69% (66%–74%) |
| Offline delay (h, min–max) | (6, 40) | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Offline delay (h, min–max) | (12, 80) | 100% | 14.1 d (10.9 d–21.8 d) | 93% (89%–96%) | 66% (56%–77%) | 66% (64%–72%) |
| Offline delay (h, min–max) | (24, 160) | 100% | 15.4 d (8.1 d–17.6 d) | 92% (84%–96%) | 70% (62%–83%) | 70% (61%–76%) |
| Look-alike damage share | (0.02, 0.07) | 100% | 14.1 d (9.2 d–16.9 d) | 91% (84%–95%) | 83% (69%–89%) | 69% (62%–73%) |
| Look-alike damage share | (0.04, 0.14) | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Look-alike damage share | (0.08, 0.28) | 100% | 16.1 d (12.4 d–17.6 d) | 92% (82%–97%) | 79% (65%–84%) | 72% (65%–76%) |
| Spread rate (beta) | 0.6 | 100% | 12.4 d (8.6 d–14.4 d) | 75% (62%–86%) | 62% (51%–75%) | 64% (57%–76%) |
| Spread rate (beta) | 1.2 | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Spread rate (beta) | 1.8 | 100% | 14.5 d (8.6 d–18.8 d) | 95% (87%–97%) | 88% (83%–97%) | 70% (67%–73%) |
| Spammer farms | 0 | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Spammer farms | 2 | 100% | 14.5 d (12.2 d–18.6 d) | 92% (84%–95%) | 80% (74%–90%) | 72% (68%–78%) |
| Spammer farms | 5 | 100% | 17.1 d (14.1 d–22.3 d) | 88% (76%–96%) | 77% (60%–84%) | 75% (69%–81%) |
| Disease introductions per season | 1 | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Disease introductions per season | 2 | 100% | 14.1 d (9.2 d–17.2 d) | 94% (91%–97%) | 85% (83%–90%) | 72% (69%–77%) |
| Photo incentives (rate ×) | 1.0 | 100% | 14.1 d (11.9 d–17.2 d) | 94% (90%–95%) | 88% (67%–95%) | 68% (62%–76%) |
| Photo incentives (rate ×) | 1.5 | 100% | 18.5 d (14.9 d–21.3 d) | 93% (89%–95%) | 87% (77%–93%) | 77% (74%–83%) |

Unit costs are assumptions (`CostConfig`): SMS $0.008, voice call $0.04, platform $0.30/farm/season, lead-farmer stipend $5/season, extension visit $5 per alerted village.
