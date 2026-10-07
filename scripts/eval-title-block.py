#!/usr/bin/env python3
"""Score the title-block reader on real mechanical drawings (the MechVQA benchmark sample, images only).

    python scripts/eval-title-block.py --images <MechVQA>/benchmark_data/images --output docs/evaluation/title-block-real.json

The images are third-party material and are not in this repository; download them from
https://github.com/xiaofengShi/MechVQA. The answer key below was written by looking at each drawing's
title block before the reader was first run: drawing number, material (None when the block has no
material cell, as on an assembly) and scale. Each page goes through ``extract_pdf``'s own path: wrapped as
an image-only PDF, rendered, read.
"""
import argparse
import glob
import io
import json
import time
from pathlib import Path

from PIL import Image

from fitwitness.ingest import title_block as tb

Image.MAX_IMAGE_PIXELS = None
# file name prefix -> (drawing number, material or None, scale or None)
GOLD = {
    "1e88825c": ("WLJSQ475-00", None, "1:1"), "237c2ddf": ("DJZ-02", "HT200", "1:1"), "2e5727a5": ("QF02-10", "橡胶", "2:1"),
    "3a4e151d": ("ZPT19", None, None), "3d4cebd4": ("PXZSB-03", "40Cr", "1:1"), "499c7e65": ("QF-08", "65Mn", "10:1"),
    "5cf2fc80": ("ZPT04", None, None), "5ea4417d": ("CLYB-02", "ZL107", "1:1"), "6deebccb": ("ZD10-012", "HT200", "1:1"),
    "86f2dc41": ("QF-01", "HT200", "1:1"), "d3597cc7": ("ZPT18", None, None), "d40ad503": ("QF02-02", "LY12", "1:1"),
}
FIELDS = ("drawing_number", "material", "scale")


def norm(value):
    return None if value is None else value.replace(" ", "").replace("：", ":").upper()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    files = {Path(f).name[:8]: f for f in sorted(glob.glob(str(args.images / "*" / "*.png")))}
    missing = sorted(set(GOLD) - set(files))
    if missing:
        raise SystemExit(f"missing images: {missing}")
    if not tb.available() or not tb.languages():
        raise SystemExit("tesseract is not installed")
    rows, correct, expected, wrong, none_but_read = [], dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0), 0, 0
    for key in sorted(GOLD):
        buf = io.BytesIO()
        Image.open(files[key]).convert("RGB").save(buf, format="PDF", resolution=150)
        started = time.perf_counter()
        got = {r.field: r for r in tb.read_page(buf.getvalue(), 0)}
        row = {"image": key, "seconds": round(time.perf_counter() - started, 1)}
        for field, want in zip(FIELDS, GOLD[key]):
            have = got.get(field)
            row[field] = {"expected": want, "read": have.value if have else None, "confidence": round(have.conf, 2) if have else None}
            if want is not None:
                expected[field] += 1
                correct[field] += norm(have.value if have else None) == norm(want)
                wrong += bool(have) and norm(have.value) != norm(want)
            elif have is not None:
                none_but_read += 1
        rows.append(row)
        print(key, {f: (row[f]["read"], row[f]["expected"]) for f in FIELDS}, flush=True)
    summary = {"drawings": len(rows), "correct": correct, "expected": expected, "wrong_values_reported": wrong,
               "readings_where_the_block_has_no_such_cell": none_but_read, "seconds_median": sorted(r["seconds"] for r in rows)[len(rows) // 2],
               "languages": tb.languages()}
    print(json.dumps(summary, ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
