"""Real on-device pest classifier (MobileNetV3-Small, ONNX) for Python callers.

Same contract as the simulated classifier: image -> probabilities over CLASSES.
onnxruntime and Pillow are imported lazily, so the core package needs only numpy.
Install them with `pip install -r requirements-ml.txt` (or just onnxruntime + pillow).
"""
import json
import os

import numpy as np

from .config import CLASSES

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "pest_classifier")


class OnnxPestClassifier:
    def __init__(self, path: str = None):
        import onnxruntime as ort  # lazy
        d = path if path and os.path.isdir(path) else DEFAULT_DIR
        model = path if path and path.endswith(".onnx") else os.path.join(d, "model.onnx")
        meta_dir = os.path.dirname(model)
        with open(os.path.join(meta_dir, "preprocess.json")) as f:
            self.pre = json.load(f)
        with open(os.path.join(meta_dir, "labels.json")) as f:
            self.labels = json.load(f)
        if self.labels != CLASSES:
            raise ValueError(f"model labels {self.labels} != CLASSES {CLASSES}")
        self.session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])

    def preprocess(self, image) -> np.ndarray:
        from PIL import Image  # lazy
        img = image if isinstance(image, Image.Image) else Image.open(image)
        n = self.pre["size"]
        x = np.asarray(img.convert("RGB").resize((n, n), Image.BILINEAR), dtype=np.float32) / 255.0
        x = (x - np.array(self.pre["mean"], np.float32)) / np.array(self.pre["std"], np.float32)
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)

    def predict(self, image) -> np.ndarray:
        """Calibrated probabilities over CLASSES (temperature scaling is inside the model)."""
        return self.session.run(None, {self.pre["input"]: self.preprocess(image)})[0][0]
