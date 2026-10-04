"""Real vision model checks (coffee leaf classifier). Skipped unless onnxruntime, Pillow and the trained model
are present (`pip install -r requirements-ml.txt` and `python scripts/train_coffee.py`)."""
import json
import os
import sys
import unittest

import numpy as np

COFFEE_CLASSES = ["healthy", "leaf_rust", "leaf_miner", "cercospora", "phoma"]

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
MODEL_DIR = os.path.join(ROOT, "models", "pest_classifier")
SAMPLES = os.path.join(ROOT, "web", "samples")

try:
    import onnxruntime  # noqa: F401
    import PIL  # noqa: F401
    HAVE_RT = True
except ImportError:
    HAVE_RT = False
HAVE_MODEL = os.path.exists(os.path.join(MODEL_DIR, "model.onnx"))


@unittest.skipUnless(HAVE_RT and HAVE_MODEL, "onnxruntime/Pillow or trained model not available")
class VisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pestwatch.vision import OnnxPestClassifier
        cls.clf = OnnxPestClassifier()
        cls.samples = json.load(open(os.path.join(SAMPLES, "samples.json")))

    def test_contract(self):
        from pestwatch.config import CLASSES
        self.assertEqual(self.clf.labels, CLASSES)
        self.assertEqual(json.load(open(os.path.join(MODEL_DIR, "labels.json"))), COFFEE_CLASSES)
        pre = json.load(open(os.path.join(MODEL_DIR, "preprocess.json")))
        self.assertEqual(pre["web_model"], "model_int8.onnx")
        p = self.clf.predict(os.path.join(SAMPLES, self.samples[0]["file"]))
        self.assertEqual(p.shape, (len(CLASSES),))
        self.assertAlmostEqual(float(p.sum()), 1.0, places=4)
        self.assertTrue((p >= 0).all())

    def test_held_out_samples(self):
        from pestwatch.config import CLASSES
        ok = sum(CLASSES[int(np.argmax(self.clf.predict(os.path.join(SAMPLES, s["file"]))))] == s["label"]
                 for s in self.samples)
        self.assertGreaterEqual(ok, len(self.samples) - 1)  # samples are correctly classified held-out test images

    def test_samples_cover_rust_and_healthy(self):
        labels = [s["label"] for s in self.samples]
        self.assertGreaterEqual(labels.count("leaf_rust"), 2)
        self.assertGreaterEqual(labels.count("healthy"), 2)
        for s in self.samples:
            self.assertIn(s["label"], COFFEE_CLASSES)
            self.assertTrue(os.path.exists(os.path.join(SAMPLES, s["file"])))

    def test_metrics_file_contract(self):
        m = json.load(open(os.path.join(MODEL_DIR, "metrics.json")))
        for key in ("fp32", "int8"):
            for field in ("accuracy", "macro_f1", "ece_15bin", "per_class"):
                self.assertIn(field, m[key])
            self.assertEqual(sorted(m[key]["per_class"]), sorted(COFFEE_CLASSES))

    def test_int8_matches_fp32(self):
        int8 = os.path.join(MODEL_DIR, "model_int8.onnx")
        if not os.path.exists(int8):
            self.skipTest("no int8 model")
        from pestwatch.vision import OnnxPestClassifier
        q = OnnxPestClassifier(int8)
        for s in self.samples:
            path = os.path.join(SAMPLES, s["file"])
            self.assertLess(np.abs(q.predict(path) - self.clf.predict(path)).max(), 0.2)

    def test_confusion_file_contract(self):
        from pestwatch.config import CLASSES
        c = json.load(open(os.path.join(MODEL_DIR, "confusion.json")))
        self.assertEqual(c["classes"], CLASSES)
        m = np.array(c["matrix"])
        self.assertEqual(m.shape, (len(CLASSES), len(CLASSES)))
        for row, cls in zip(m, CLASSES):
            if c["per_class_n"][cls]:
                self.assertAlmostEqual(row.sum(), 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
