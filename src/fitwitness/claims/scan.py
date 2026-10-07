"""Turn a claim PDF into what a scanner or a phone camera would hand over: a page picture.

There are no real claim documents in this repository, so scan quality is simulated on the
synthetic ones, in named steps that can be reproduced from a seed:

=========  ========  =====  =======  ===========  ========
level      rotation  blur   noise    JPEG         contrast
=========  ========  =====  =======  ===========  ========
clean      0         0      0        95           1.00
light      0.4 deg   0.5    5        85           0.95
medium     0.9 deg   0.9    10       70           0.85
heavy      1.6 deg   1.3    16       50           0.75
=========  ========  =====  =======  ===========  ========

The output is an image-only PDF (no text layer), so the extractor takes its OCR path. What this
measures is how the reader and the routing behave as quality falls; it does not measure real
scans, whose damage (stamps over text, folds, handwriting, skew in three dimensions) is wider
than anything simulated here.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

LEVELS: dict[str, dict] = {
    "clean": {"rotate": 0.0, "blur": 0.0, "noise": 0.0, "jpeg": 95, "contrast": 1.00},
    "light": {"rotate": 0.4, "blur": 0.5, "noise": 5.0, "jpeg": 85, "contrast": 0.95},
    "medium": {"rotate": 0.9, "blur": 0.9, "noise": 10.0, "jpeg": 70, "contrast": 0.85},
    "heavy": {"rotate": 1.6, "blur": 1.3, "noise": 16.0, "jpeg": 50, "contrast": 0.75},
}
DPI = 200


def _seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:4], "big")


def degrade(image: Image.Image, level: str, seed: int) -> Image.Image:
    p = LEVELS[level]
    rng = np.random.default_rng(seed)
    image = image.convert("L")
    if p["rotate"]:
        image = image.rotate(float(rng.choice([-1, 1]) * rng.uniform(0.6, 1.0) * p["rotate"]), resample=Image.BICUBIC, expand=False, fillcolor=255)
    if p["blur"]:
        image = image.filter(ImageFilter.GaussianBlur(p["blur"]))
    if p["contrast"] != 1.0:
        image = ImageEnhance.Contrast(image).enhance(p["contrast"])
    if p["noise"]:
        arr = np.asarray(image, dtype=np.float32) + rng.normal(0.0, p["noise"], size=(image.height, image.width))
        image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=p["jpeg"])
    return Image.open(BytesIO(buf.getvalue())).convert("L")


def scan_pdf(pdf: bytes, level: str, key: str) -> bytes:
    """The PDF as a scan at the given level; ``key`` (a document id) makes the damage reproducible."""
    from fitwitness.claims.ocr import render

    import pypdfium2 as pdfium

    pages = [degrade(render(pdf, i, DPI), level, _seed(key, level, str(i))) for i in range(len(pdfium.PdfDocument(pdf)))]
    out = BytesIO()
    pages[0].save(out, format="PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    return out.getvalue()
