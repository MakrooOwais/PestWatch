/* On-device pest classifier for the field app: MobileNetV3-Small (ONNX) run with onnxruntime-web (wasm).
   Exposes window.PestModel = { load(), classify(imageOrBlob) -> {probs, ms} , info }.
   Everything is served locally (web/vendor/ort, models/) so it works offline once cached. */
(function () {
  "use strict";
  const BASE = "models/pest_classifier/";
  let session = null, pre = null, labels = null, loading = null;

  async function load() {
    if (session) return session;
    if (loading) return loading;
    loading = (async () => {
      if (!window.ort) throw new Error("onnxruntime-web not loaded");
      // Single-threaded wasm works without cross-origin isolation (no SharedArrayBuffer needed).
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.proxy = false;
      ort.env.wasm.wasmPaths = new URL("vendor/ort/", document.baseURI).href;
      const [p, l] = await Promise.all([
        fetch(BASE + "preprocess.json").then((r) => r.json()),
        fetch(BASE + "labels.json").then((r) => r.json()),
      ]);
      pre = p; labels = l;
      session = await ort.InferenceSession.create(BASE + (pre.web_model || "model.onnx"), { executionProviders: ["wasm"] });
      return session;
    })();
    try { return await loading; } catch (e) { loading = null; throw e; }
  }

  function toBitmapSource(src) {
    if (src instanceof Blob) return createImageBitmap(src);
    return Promise.resolve(src); // HTMLImageElement / canvas / ImageBitmap
  }

  async function classify(src) {
    await load();
    const img = await toBitmapSource(src);
    const n = pre.size;
    const cv = document.createElement("canvas");
    cv.width = n; cv.height = n;
    const ctx = cv.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, n, n);  // full image resized to n x n, as in training
    const px = ctx.getImageData(0, 0, n, n).data;
    const data = new Float32Array(3 * n * n);
    for (let i = 0; i < n * n; i++) {
      for (let c = 0; c < 3; c++) data[c * n * n + i] = (px[i * 4 + c] / 255 - pre.mean[c]) / pre.std[c];
    }
    const t0 = performance.now();
    const out = await session.run({ [pre.input]: new ort.Tensor("float32", data, [1, 3, n, n]) });
    const probs = Array.from(out[pre.output].data);
    return { probs, labels, ms: Math.round(performance.now() - t0) };
  }

  window.PestModel = { load, classify, info: "MobileNetV3-Small (coffee leaves, ONNX)" };
})();
