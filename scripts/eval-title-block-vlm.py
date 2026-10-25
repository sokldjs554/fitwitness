#!/usr/bin/env python3
"""Read the human-checked title blocks of the two MechVQA hold-outs with Claude, beside the Tesseract reader.

    python scripts/eval-title-block-vlm.py --images <MechVQA>/benchmark_data --max-cost-usd 0.6 \\
        --output artifacts/title-block-vlm

Only the scored entries of docs/evaluation/title-block-holdout-gold.json (27) and title-block-holdout2-gold.json
(34) are read, the 61 drawings whose labels a person checked on 2026-10-25. The pages come from MechVQA and are
checked against the committed SHA-256 lists before this script runs. Each page gets two calls to the same model:

  1. ``page``: the whole page, downscaled to 1600 px: find the title block, return its box, read the three cells;
  2. ``crop``: that box cut from the full-resolution page with a 3% margin: read the three cells again
     (when the first call returned no usable box, the crop reading is the page reading).

Both prompts were written before the first call and are in the protocol. Scoring is the hold-out scorer's: a field
is right when its value equals the label after removing spaces and upper-casing; a value read where the label has
one is a wrong value when it differs; a value read where the label has none (the block has no such cell, or it is
empty) is counted apart. The Tesseract numbers beside them are copied from the committed records, not re-run:
hold-out 1 with the reader before the change (the change was developed on those pages), hold-out 2 before and
after. A call is reserved its cap before it starts, so the ceiling cannot be overrun.
"""
import argparse
import glob
import io
import json
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from PIL import Image
from pydantic import Field

from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.providers import create_model
from fitwitness.contracts import Budget, Strict
from fitwitness.ingest.vision import validated_png

Image.MAX_IMAGE_PIXELS = None
FIELDS = ("drawing_number", "material", "scale")
EVAL = Path("docs/evaluation")
SETS = {"holdout1": EVAL / "title-block-holdout-gold.json", "holdout2": EVAL / "title-block-holdout2-gold.json"}
TESSERACT = {"holdout1": {"before the change": EVAL / "title-block-holdout.json"},
             "holdout2": {"before the change": EVAL / "title-block-holdout2-before-the-change.json", "after the change": EVAL / "title-block-holdout2.json"}}
MODEL = "claude-haiku-4-5-20251001"
# One reading: the call and, when its output fails the schema, the client's one repair. The repair is reserved at
# its worst case before it is sent, and $0.02 was not enough for both (run 40208747697: four repairs blocked).
CALL_CAP = Decimal("0.04")
PROMPT_PAGE = (
    "This image is one page with a mechanical drawing, often from a Chinese textbook; title block cells may be labelled "
    "图号, 材料, 比例 or in Korean 도번, 재질, 척도. Find the drawing's title block: the bordered table of cells on the edge of "
    "the drawing frame, usually at the bottom right, that names the part and gives its drawing number, material and scale. "
    "A parts list (明细栏 or 부품표, rows of part names next to or above the title block) is not the title block. "
    "Return title_block_bbox as [left, top, right, bottom] normalized to this image, or null when the page has no title block. "
    "Then read exactly what is printed in the title block's drawing number cell, material cell and scale cell, copying the "
    "characters as printed (for example LJT04.25, HT200, 1:2). Use null for a cell that is absent, empty or unreadable. "
    "Do not take a value from the parts list, the views, the notes or a view label such as 2:1 beside a detail, and do not "
    "guess a value the block does not print.")
PROMPT_CROP = (
    "This image is the title block cut from a mechanical drawing page, often from a Chinese textbook; cells may be labelled "
    "图号, 材料, 比例 or in Korean 도번, 재질, 척도. Read exactly what is printed in its drawing number cell, material cell and "
    "scale cell, copying the characters as printed (for example LJT04.25, HT200, 1:2). Use null for a cell that is absent, "
    "empty or unreadable. Do not take a value from a parts list (明细栏 or 부품표) or from the drawing, and do not guess a value "
    "the block does not print. Return title_block_bbox as null.")


class Cell(Strict):
    value: str | None = Field(default=None, max_length=60)
    visible_text: str = Field(default="", max_length=160)


class TitleBlock(Strict):
    title_block_bbox: tuple[float, float, float, float] | None = None
    drawing_number: Cell
    material: Cell
    scale: Cell


def norm(value):
    return None if value is None else str(value).replace(" ", "").replace("：", ":").upper()


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def box(bbox, margin=0.03):
    """A usable crop box from the model's normalized box, or None."""
    if not bbox:
        return None
    left, top, right, bottom = bbox
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    return (max(0.0, left - margin), max(0.0, top - margin), min(1.0, right + margin), min(1.0, bottom + margin))


def read(image_bytes: bytes, prompt: str):
    # two model calls: the client sends an output that fails the schema back once (run 40207800589 allowed one, and
    # 13 of 118 readings ended at that block instead of the repair)
    tracker = BudgetTracker(Budget(max_model_calls=2, max_tokens=64000, max_cost_usd=CALL_CAP))
    model = create_model("anthropic", MODEL, tracker)
    events = []
    model.emit = lambda kind, data: events.append({"kind": kind, "payload": data})
    started, reading, error = time.perf_counter(), None, None
    try:
        reading, _ = model.read_image(image_bytes, prompt, schema=TitleBlock)
    except Exception as exc:  # noqa: BLE001 - the failure is the finding; it goes in the row
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    usage = tracker.usage
    return reading, {"error": error, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                     "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                     "cost_usd": float(usage.cost_usd or 0), "reserved_left_usd": float(usage.reserved_cost_usd),
                     "image_sha256": sha256(image_bytes).hexdigest(), "events": events,
                     "schema_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")]}


def values(reading):
    return {f: (getattr(reading, f).value if reading else None) for f in FIELDS}


def score(rows, arm):
    correct, expected = dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0)
    wrong, none_but_read = 0, 0
    for r in rows:
        for f in FIELDS:
            want, have = r["expected"][f], r[arm][f]
            if want is not None:
                expected[f] += 1
                correct[f] += norm(have) == norm(want)
                wrong += have is not None and norm(have) != norm(want)
            elif have is not None:
                none_but_read += 1
    return {"correct": correct, "expected": expected, "wrong_values_reported": wrong,
            "readings_where_the_block_has_no_such_cell": none_but_read}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, type=Path, help="a directory holding images/<xx>/<sha1>.png")
    ap.add_argument("--max-cost-usd", type=float, required=True)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--only", nargs="*", default=None, help="read only these 8-character image keys (a rerun of pages a run could not read)")
    args = ap.parse_args()
    assert 0 < args.max_cost_usd <= 1.0
    ceiling = Decimal(str(args.max_cost_usd))
    args.output.mkdir(parents=True, exist_ok=False)
    files = {Path(f).name[:8]: f for f in sorted(glob.glob(str(args.images / "images" / "*" / "*.png")))}
    todo = []
    for name, path in SETS.items():
        gold = json.loads(path.read_text(encoding="utf-8"))
        for key in sorted(gold):
            g = gold[key]
            if g.get("unclear") or g.get("title_block") == "no" or g.get("duplicate_of"):
                continue
            assert g.get("human_check"), (name, key)
            if args.only is not None and key not in args.only:
                continue
            todo.append((name, key, {f: g.get(f) for f in FIELDS}))
    missing = sorted(k for _, k, _ in todo if k not in files)
    if missing:
        raise SystemExit(f"missing images: {missing}")
    protocol = {"model": MODEL, "pages": len(todo), "only": args.only, "sets": {n: sum(1 for s, _, _ in todo if s == n) for n in SETS},
                "prompt_page": PROMPT_PAGE, "prompt_crop": PROMPT_CROP, "schema": TitleBlock.model_json_schema(),
                "page_long_edge_px": 1600, "crop_margin": 0.03, "call_cap_usd": str(CALL_CAP), "ceiling_usd": str(ceiling),
                "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "started_at": datetime.now(timezone.utc).isoformat()}
    (args.output / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    spent, rows, skipped = Decimal(0), [], []
    for name, key, expected in todo:
        if spent + 2 * CALL_CAP > ceiling:
            skipped.append({"set": name, "image": key, "reason": f"ceiling: spent {spent} + two calls of {CALL_CAP} > {ceiling}"})
            continue
        page = Image.open(files[key]).convert("RGB")
        small = page.copy()
        small.thumbnail((1600, 1600))
        first, first_meta = read(validated_png(png(small)), PROMPT_PAGE)
        spent += Decimal(str(first_meta["cost_usd"])) + Decimal(str(first_meta["reserved_left_usd"]))
        crop_box = box(first.title_block_bbox if first else None)
        if crop_box:
            w, h = page.size
            cut = page.crop((int(crop_box[0] * w), int(crop_box[1] * h), int(crop_box[2] * w), int(crop_box[3] * h)))
            cut.thumbnail((1600, 1600))  # the model gets at most 1600 px either way; a whole-page box would exceed the pixel limit
            second, second_meta = read(validated_png(png(cut)), PROMPT_CROP)
            spent += Decimal(str(second_meta["cost_usd"])) + Decimal(str(second_meta["reserved_left_usd"]))
        else:
            second, second_meta = first, None
        row = {"set": name, "image": key, "page_px": list(page.size), "expected": expected,
               "bbox": list(first.title_block_bbox) if first and first.title_block_bbox else None, "crop_box": list(crop_box) if crop_box else None,
               "page": values(first), "crop": values(second),
               "visible_text": {"page": {f: getattr(first, f).visible_text for f in FIELDS} if first else None,
                                "crop": {f: getattr(second, f).visible_text for f in FIELDS} if second else None},
               "calls": [m for m in (first_meta, second_meta) if m]}
        rows.append(row)
        with (args.output / "rows.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(name, key, {f: (row["crop"][f], expected[f]) for f in FIELDS}, "spent", spent, flush=True)
    calls = [c for r in rows for c in r["calls"]]
    summary = {n: {arm: score([r for r in rows if r["set"] == n], arm) for arm in ("page", "crop")} | {"drawings": sum(r["set"] == n for r in rows)}
               for n in SETS}
    summary["all"] = {arm: score(rows, arm) for arm in ("page", "crop")} | {"drawings": len(rows)}
    tesseract = {n: {k: json.loads(p.read_text())["summary"] for k, p in v.items()} for n, v in TESSERACT.items()}
    report = {"status": "measured" if rows else "not measured", "protocol": protocol, "summary": summary,
              "tesseract_records": tesseract, "spent_usd": float(spent), "skipped": skipped,
              "calls": len(calls), "call_errors": sum(bool(c["error"]) for c in calls),
              "latency_ms_median": sorted(c["latency_ms"] for c in calls)[len(calls) // 2] if calls else None,
              "rows": [{k: v for k, v in r.items() if k != "calls"} | {"calls": [{k: v for k, v in c.items() if k != "events"} for c in r["calls"]]} for r in rows]}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"summary": summary, "spent_usd": float(spent), "skipped": len(skipped), "call_errors": report["call_errors"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
