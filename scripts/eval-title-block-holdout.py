#!/usr/bin/env python3
"""Score the unchanged title-block reader on MechVQA drawings that were never used to tune it.

    python scripts/eval-title-block-holdout.py --images <MechVQA>/benchmark_data/images \
        --gold docs/evaluation/title-block-holdout-gold.json --output docs/evaluation/title-block-holdout.json

The gold file maps an 8-character image prefix to what is printed in the drawing's title block (written by
looking at the drawing before the reader was run on it). Entries marked "unclear" are left out of the score
and counted. Each page goes through the same path as scripts/eval-title-block.py.
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
FIELDS = ("drawing_number", "material", "scale")


def norm(value):
    return None if value is None else str(value).replace(" ", "").replace("：", ":").upper()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    files = {Path(f).name[:8]: f for f in sorted(glob.glob(str(args.images / "*" / "*.png")))}
    missing = sorted(set(gold) - set(files))
    if missing:
        raise SystemExit(f"missing images: {missing}")
    if not tb.available() or not tb.languages():
        raise SystemExit("tesseract is not installed")
    rows, correct, expected, wrong, none_but_read, skipped = [], dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0), 0, 0, []
    for key in sorted(gold):
        g = gold[key]
        if g.get("unclear") or g.get("title_block") == "no":
            skipped.append({"image": key, "why": "unclear" if g.get("unclear") else "no title block", "note": g.get("note", "")})
            continue
        buf = io.BytesIO()
        Image.open(files[key]).convert("RGB").save(buf, format="PDF", resolution=150)
        started = time.perf_counter()
        got = {r.field: r for r in tb.read_page(buf.getvalue(), 0)}
        row = {"image": key, "seconds": round(time.perf_counter() - started, 1)}
        for field in FIELDS:
            want, have = g.get(field), got.get(field)
            row[field] = {"expected": want, "read": have.value if have else None, "confidence": round(have.conf, 2) if have else None}
            if want is not None:
                expected[field] += 1
                correct[field] += norm(have.value if have else None) == norm(want)
                wrong += bool(have) and norm(have.value) != norm(want)
            elif have is not None:
                none_but_read += 1
        rows.append(row)
        print(key, {f: (row[f]["read"], row[f]["expected"]) for f in FIELDS}, flush=True)
    summary = {"drawings": len(rows), "skipped": len(skipped), "correct": correct, "expected": expected,
               "wrong_values_reported": wrong, "readings_where_the_block_has_no_such_cell": none_but_read,
               "seconds_median": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else None, "languages": tb.languages()}
    print(json.dumps(summary, ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps({"summary": summary, "skipped": skipped, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
