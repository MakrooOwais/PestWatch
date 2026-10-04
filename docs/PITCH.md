# PestWatch pitch notes

Lead with numbers the evaluation supports. Every figure here comes from `reports/EVALUATION.md`: simulated seasons, stated assumptions, medians.

## One-liner

PestWatch turns farmers' everyday leaf photos and a two-second "did you see rust?" reply into a village-level early warning for coffee leaf rust. It gives about **two weeks more warning** than today's extension route, for under **$1 per farm per season**.

## Slides

1. **The problem.**
   - Noor's coffee yields are slipping and she doesn't know why.
   - Leaf rust starts as faint spots under the leaves. By the time a farmer notices yellowing and leaf drop, and extension has heard, it has spread to her neighbours.
2. **The insight.** No single uncertain leaf photo is enough to act on, but many uncertain photos close together in space and time are. And when the signal is weak, *ask*: a targeted request to the 12 nearest farmers is cheap and fast.
3. **How it works** (diagram from the README loop):
   - a 1.65 MB on-device model that works offline
   - a space-time scan
   - scouting requests at watch level
   - outbreak probability
   - alerts in the farmer's language by app, SMS or voice
   - an all-clear when the area goes quiet
4. **Demo:** dashboard guided demo, then the phone (live loop, including the "not sure — ask a person" fail-safe).
5. **Evidence** (ablation table):
   - Photos alone: **8.2 days** lead and 48% of loss avoided.
   - With targeted scouting and fusion: **13.8 days** and **71%**.
   - Rust is caught about 34 days before it becomes widespread.
6. **Offline by design.**
   - The model runs on the phone and photos queue in an offline outbox.
   - Replies are answered on the phone.
   - Alerts reach basic phones by SMS and voice, with no data needed.
7. **Inclusion.**
   - Kiswahili, English and French, plus a pipeline for adding local languages.
   - Feature phones and low-literacy users (voice).
   - Honest limits: it needs at least about 40% of farmers using the app, and healthy East African leaves aren't in the training data yet.
8. **Value for money.**
   - About $125 per region-season (~145 farms), against about $25k of coffee yield loss avoided. That ratio uses assumed yield and price, so present it as indicative.
   - For cooperatives and extension: a ranked visit list and litres of fungicide to pre-position.
9. **Path to scale.**
   - Photos from Noor's district; a pilot with a coffee cooperative and extension service.
   - The same pipeline works for other crops and diseases.

## Talk track for the demo (~3 min)

1. "This is a simulated coffee region: six villages, about 150 farms. What you see is what PestWatch sees: leaf-photo results syncing in as phones get signal."
2. "Here a training day triples the photo count in one village. A naive counter would panic. PestWatch compares reports against what's *expected* for that many photos, so it stays quiet."
3. "*Switch to ground truth.* Rust spores have arrived after the rains. No farmer can see anything yet."
4. "Faint reports start to cluster, each one too weak to act on. So PestWatch asks the 12 nearest farmers to check ten coffee trees. Here's the request in Kiswahili, by SMS."
5. "Replies come back. 'None found' is useful too. A farmer confirms rust, and the outbreak probability crosses the threshold."
6. "Alerts go out to farms in the zone, stretched downwind, each in the farmer's own language and channel. This farmer gets a voice call."
7. "Without PestWatch, the first farmer would notice only *now*, and extension would hear about it *now*: about two weeks later."
8. *Phone:* "A faint leaf: the app says *not sure — ask a person*, and sends a referral. A clear rust leaf: 99%. I reply 'nimeona kutu', and the alert fires for the whole area."

## Questions to expect, and honest answers

- **"Is the AI real?"**
  - The vision model is real and trained: MobileNetV3-Small on three CC-BY-4.0 coffee leaf datasets (JMuBEN Kenya, BRACOL Brazil, RoCoLe Ecuador), running on the phone at 1.65 MB.
  - It finds 93% of rust leaves in testing, and calls 4 of 139 healthy test leaves rust.
  - It was tested only on the same collections, early rust is its weakest point, and healthy East African leaves aren't represented.
  - The outbreak detector is a proven statistical method, and the fusion is a small, interpretable model.
- **"Are these results real?"** They come from a simulation with stated assumptions, using the model's measured error rates blended toward worse. Field validation is the next step.
- **"Why not just count reports?"** On photos alone, simple counting is only slightly slower. The value is *where* (which farms to warn) and *asking for evidence* (scouting), which adds about 5 days and 19 points of loss avoided.
- **"Alert fatigue?"** About 1.7 alerts per farm per season. There's a per-farmer budget, all-clear messages, and escalation-only re-alerts.
- **"What if few farmers use the app?"** The lead holds at 40% participation but breaks at 20%. A pilot needs a cooperative or lead-farmer network to reach that.
- **"Data and privacy?"** Only model outputs (~100 bytes) are uploaded, never photos. The public map uses a ~1 km grid and consenting farmers only. Admin endpoints need a key.

## Don't claim

- Field-validated accuracy or lead times.
- That wind-aware zones or the alert budget improved the metrics: the simulation shows no measurable gain.
- Final translations: they're drafts pending native-speaker review.
- The benefit-cost ratio as a firm number: it depends on assumed coffee yield and price.
