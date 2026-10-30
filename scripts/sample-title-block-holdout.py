#!/usr/bin/env python3
"""Draw a fresh set of MechVQA drawings for the title-block hold-out, from images no earlier set used.

    python scripts/sample-title-block-holdout.py --index <MechVQA>/benchmark_data/vqa_benchmark/mechvqa_benchmark.jsonl \
        --seed 20261025 --n 60 --output docs/evaluation/title-block-holdout2-sample.txt

The index lists every image the benchmark's questions use (``images/xx/<sha1>.png``; the JPEG ones are left out, as the first hold-out did). Images already in an
earlier set are left out: the twelve of ``scripts/eval-title-block.py`` and every key of the first hold-out's
gold file. The rest are sorted and sampled with ``random.Random(seed)``, so the same seed gives the same list.
Nothing is read from the images here and no labels are made; a page that turns out to have no title block is
left out of the score later and counted, as in the first hold-out.
"""

import argparse
import importlib.util
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def used_keys() -> set[str]:
    spec = importlib.util.spec_from_file_location("evaltb", ROOT / "scripts" / "eval-title-block.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    keys = set(mod.GOLD)
    for earlier in sorted((ROOT / "docs" / "evaluation").glob("title-block-holdout*-gold.json")):
        keys |= set(json.loads(earlier.read_text(encoding="utf-8")))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True, type=Path)
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    rows = [json.loads(line) for line in args.index.read_text(encoding="utf-8").splitlines() if line.strip()]
    images = sorted(
        {i for r in rows for i in r["images"] if i.endswith(".png")}
    )  # the evaluation scripts read PNG pages, as the first hold-out did
    used = used_keys()
    pool = [i for i in images if Path(i).name[:8] not in used]
    picked = sorted(random.Random(args.seed).sample(pool, args.n))
    print(
        f"{len(images)} images in the index, {len(images) - len(pool)} already used, {len(pool)} left; drew {len(picked)} with seed {args.seed}"
    )
    if args.output:
        args.output.write_text("\n".join(picked) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
