/* Offline shell for every page: precache the site, serve cache-first, never cache /api. */
const CACHE = "pestwatch-v4";
const ASSETS = [
  "index.html", "extension.html", "field.html", "manifest.webmanifest",
  "style.css", "app.js", "extension.js", "shared.js", "field.js", "model.js", "reply_parser.js", "offline.js",
  "data/scenario.js", "data/i18n.js",
  "vendor/ort/ort.wasm.min.js", "vendor/ort/ort-wasm-simd-threaded.mjs", "vendor/ort/ort-wasm-simd-threaded.wasm",
  "models/pest_classifier/model_int8.onnx", "models/pest_classifier/labels.json", "models/pest_classifier/preprocess.json",
  "samples/leaf_rust_1.jpg", "samples/healthy_1.jpg", "samples/leaf_miner_1.jpg",
];
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
