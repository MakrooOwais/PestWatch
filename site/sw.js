/* Offline shell for every page: precache the site, serve cache-first, never cache /api. */
const CACHE = "pestwatch-static-v4";
const ASSETS = ["app.js", "data/i18n.js", "data/live_demo.js", "data/scenario.js", "extension.html", "extension.js", "field.html", "field.js", "index.html", "manifest.webmanifest", "model.js", "models/pest_classifier/labels.json", "models/pest_classifier/metrics.json", "models/pest_classifier/model_int8.onnx", "models/pest_classifier/preprocess.json", "offline.js", "reply_parser.js", "samples/healthy_1.jpg", "samples/healthy_2.jpg", "samples/leaf_miner_1.jpg", "samples/leaf_rust_1.jpg", "samples/leaf_rust_2.jpg", "samples/phoma_1.jpg", "samples/samples.json", "shared.js", "static_api.js", "style.css", "vendor/ort/NOTICE.txt", "vendor/ort/ort-wasm-simd-threaded.mjs", "vendor/ort/ort-wasm-simd-threaded.wasm", "vendor/ort/ort.wasm.min.js"];
self.addEventListener("install", (e) => e.waitUntil(
  caches.open(CACHE).then((c) => Promise.all(ASSETS.map((a) => c.add(a).catch(() => null)))).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.includes("/api/") || url.pathname.includes("/gateway/") || e.request.method !== "GET") return;
  // ignoreSearch: field.html?lang=sw&farm=72 is served from the cached field.html.
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return res;
  })));
});
