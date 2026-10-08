"""Measure the grid reader (claims/official.py) on the statutory diagnosis certificate and fee statement.

The generator's 120 cases supply the truth. Each diagnosis and receipt is written onto the form with
claims/official_fill.py at growing placement jitter, read back through the same ``extract_documents`` the
pipeline uses, and every field is scored as correct, missing (the reader returned nothing: the claim goes to
a person) or wrong (it returned another value: the risk). A blank form is read as the control; it must give
nothing.

    python scripts/eval-official-forms.py                          # the redrawn layout
    python scripts/eval-official-forms.py --diagnosis-form F5-2.pdf --receipt-form F6.pdf   # real blank forms

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


def score(truths: list[dict], blanks: dict[str, bytes | None], jitter: float) -> dict:
    tally: dict[str, Counter] = defaultdict(Counter)
    for n, truth in enumerate(truths):
        for kind, fields in FIELDS.items():
            data = fill(kind, truth, blank=blanks[kind], seed=f"{n}-{kind}", jitter=jitter)
            got = extract_documents([(f"{n}-{kind}", kind, data)])
            for f in fields:
                want = truth.get(f)
                if want in (None, ""):
                    continue
                have = getattr(got, f)
                outcome = "correct" if have == want else "missing" if have in (None, "") else "wrong"
                tally[f"{kind}.{f}"][outcome] += 1
    return {k: dict(v) for k, v in sorted(tally.items())}


def blank_control(blanks: dict[str, bytes | None]) -> dict:
    out = {}
    for kind in FIELDS:
        got = extract_documents([(f"blank-{kind}", kind, fill(kind, {}, blank=blanks[kind]))])
        out[kind] = sorted(f for f in got.FIELDS if getattr(got, f) not in (None, ""))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="var/claims")
    ap.add_argument("--diagnosis-form")
    ap.add_argument("--receipt-form")
    ap.add_argument("--jitters", default="0,1.5,3,4.5,6")
    ap.add_argument("--output")
    a = ap.parse_args()
    gold = json.loads((Path(a.root) / "gold.json").read_text())
    truths = [g["truth"] for _, g in sorted(gold.items())]
    blanks = {"diagnosis": Path(a.diagnosis_form).read_bytes() if a.diagnosis_form else None,
              "receipt": Path(a.receipt_form).read_bytes() if a.receipt_form else None}
    result = {"cases": len(truths), "forms": {k: "real" if v else "redrawn" for k, v in blanks.items()},
              "blank_form_reads": blank_control(blanks), "by_jitter": {}}
    for j in [float(x) for x in a.jitters.split(",")]:
        by_field = score(truths, blanks, j)
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
