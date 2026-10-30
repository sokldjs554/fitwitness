"""Measure the grid reader (claims/official.py) on the statutory diagnosis certificate and fee statement.

The generator's 120 cases supply the truth. Each diagnosis and receipt is written onto the form with
claims/official_fill.py at growing placement jitter, read back through the same ``extract_documents`` the
pipeline uses, and every field is scored as correct, missing (the reader returned nothing: the claim goes to
a person) or wrong (it returned another value: the risk). A blank form is read as the control; it must give
nothing.

    python scripts/eval-official-forms.py                          # the redrawn layout
    python scripts/eval-official-forms.py --diagnosis-form F5-2.pdf --receipt-form F6.pdf   # real blank forms
    python scripts/eval-official-forms.py --scan medium --split dev --workers 4   # the forms as page pictures (claims/official_scan.py)

With ``--scan`` every form is turned into a page picture at that damage level (``claims/scan.py``) before it is read.
``--split`` takes the even-numbered (dev) or odd-numbered (test) cases of the 120, the split ``claims.evaluate`` uses.

The real forms are published by the ministry and are not in this repository; the file given is only read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fitwitness.claims.extract import extract_documents  # noqa: E402
from fitwitness.claims.official_fill import fill  # noqa: E402

FIELDS = {
    "diagnosis": ["insured_name", "diagnosis_name", "diagnosis_code", "diagnosis_date", "admission_date", "discharge_date", "hospital"],
    "receipt": ["insured_name", "total_amount", "hospital"],
}


def _case(args: tuple) -> list[tuple[str, str, float | None]]:
    """(field, outcome, confidence) for every field of one case, both forms."""
    n, truth, blanks, jitter, scan = args
    out = []
    for kind, fields in FIELDS.items():
        data = fill(kind, truth, blank=blanks[kind], seed=f"{n}-{kind}", jitter=jitter)
        if scan:
            from fitwitness.claims.scan import scan_pdf

            data = scan_pdf(data, scan, f"{n}-{kind}")
        got = extract_documents([(f"{n}-{kind}", kind, data)])
        for f in fields:
            want = truth.get(f)
            if want in (None, ""):
                continue
            have = getattr(got, f)
            same = have == want or (
                scan
                and f in ("insured_name", "diagnosis_name", "hospital")
                and isinstance(have, str)
                and have.replace(" ", "") == str(want).replace(" ", "")
            )  # a scan cannot be asked to keep Korean word spacing
            out.append((f"{kind}.{f}", "correct" if same else "missing" if have in (None, "") else "wrong", got.confidence))
    return out


def score(
    truths: list[dict],
    blanks: dict[str, bytes | None],
    jitter: float,
    scan: str | None = None,
    workers: int = 1,
    first: int = 0,
    step: int = 1,
) -> dict:
    jobs = [(first + step * i, t, blanks, jitter, scan) for i, t in enumerate(truths)]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_case, jobs, chunksize=1))
    else:
        rows = [_case(j) for j in jobs]
    tally: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for field, outcome, _ in row:
            tally[field][outcome] += 1
    return {k: dict(v) for k, v in sorted(tally.items())}


def blank_control(blanks: dict[str, bytes | None], scan: str | None = None) -> dict:
    out = {}
    for kind in FIELDS:
        data = fill(kind, {}, blank=blanks[kind], decoys=False)
        if scan:
            from fitwitness.claims.scan import scan_pdf

            data = scan_pdf(data, scan, f"blank-{kind}")
        got = extract_documents([(f"blank-{kind}", kind, data)])
        out[kind] = sorted(f for f in got.FIELDS if getattr(got, f) not in (None, ""))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="var/claims")
    ap.add_argument("--diagnosis-form")
    ap.add_argument("--receipt-form")
    ap.add_argument("--jitters", default="0,1.5,3,4.5,6")
    ap.add_argument(
        "--scan", choices=["clean", "light", "medium", "heavy"], help="read each form as a scanned page picture at this damage level"
    )
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--limit", type=int, help="only the first N cases of the split")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--output")
    a = ap.parse_args()
    gold = json.loads((Path(a.root) / "gold.json").read_text())
    everything = sorted(gold.items())
    first, step = {"all": (0, 1), "dev": (0, 2), "test": (1, 2)}[a.split]
    chosen = everything[first::step][: a.limit]
    truths = [g["truth"] for _, g in chosen]
    blanks = {
        "diagnosis": Path(a.diagnosis_form).read_bytes() if a.diagnosis_form else None,
        "receipt": Path(a.receipt_form).read_bytes() if a.receipt_form else None,
    }
    result = {
        "cases": len(truths),
        "split": a.split,
        "scan": a.scan,
        "forms": {k: "real" if v else "redrawn" for k, v in blanks.items()},
        "blank_form_reads": blank_control(blanks, a.scan),
        "by_jitter": {},
    }
    for j in [float(x) for x in a.jitters.split(",")]:
        by_field = score(truths, blanks, j, a.scan, a.workers, first, step)
        total = Counter()
        for c in by_field.values():
            total.update(c)
        result["by_jitter"][str(j)] = {"fields": by_field, "total": dict(total)}
        n = sum(total.values())
        print(f"jitter {j:>4}: {total['correct']}/{n} correct, {total['missing']} missing, {total['wrong']} wrong")
    print("blank form reads:", result["blank_form_reads"])
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
