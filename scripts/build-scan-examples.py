#!/usr/bin/env python3
"""Pre-render the scanned-page examples the claims desk shows: what the scan reader read, how, and what happened to the claim.

    python scripts/build-scan-examples.py --root var/claims --level heavy

Every claim of the synthetic corpus is turned into page pictures at the given degradation (``claims/scan.py``) and read
with the same code the pipeline uses (``claims/extract.py`` → ``claims/ocr.py``). For each value the record keeps what the
reader said first, what it said after the enlarged second reading and the letter repairs, the confidence, where it stood on
the page, and the truth the generator printed. A handful of claims is then picked by rules written below, not by eye, and
their page pictures and records are written to docs/evaluation/scan-examples/ and scan-examples.json. The public demo has no
OCR engine, so it shows this file; it reads nothing at request time.

The examples illustrate; they are not a measurement. The measurement is docs/evaluation/claims-scan-hardening.json.
Needs Tesseract (kor+eng) and the corpus (``python -m fitwitness.claims.synth var/claims 120``).
"""
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fitwitness.claims import evaluate as E
from fitwitness.claims import ocr
from fitwitness.claims.extract import DECISIVE, LABELS, extract_documents, read_pairs_scored
from fitwitness.claims.scan import scan_pdf

FIELD_LABEL = {"insured_name": "환자 이름", "hospital": "병원", "diagnosis_name": "병명", "diagnosis_code": "질병분류기호", "diagnosis_date": "진단일",
               "admission_date": "입원일", "discharge_date": "퇴원일", "surgery_name": "수술명", "surgery_date": "수술일",
               "surgery_grade": "수술 등급", "total_amount": "합계(영수증)"}
SCAN_FIELDS = set(FIELD_LABEL)
IMAGE_WIDTH = 760


def _text(value) -> str | None:
    return None if value is None else str(value)


def _repair(field: str, raw: str) -> str:
    if field.endswith("_date"):
        return ocr.repair_date(raw)
    if field == "diagnosis_code":
        return ocr.repair_code(raw)
    if field == "total_amount":
        return ocr.repair_amount(raw)
    if field == "surgery_grade":
        return ocr.repair_grade(raw)
    return raw


def read_case(args: tuple) -> dict:
    """One claim read at ``level``: every value with its first reading, final reading, confidence and truth, and the decision."""
    root, case, gold, level = args
    docs = [(d["id"], d["kind"], scan_pdf((root / case["case_id"] / f"{d['id']}.pdf").read_bytes(), level, d["id"])) for d in case["documents"]]
    ex, decision, _ = E.decide(case, docs)
    labels = {**{label: f for label, f in LABELS.items()}}
    # What the reader said before the enlarged second reading: the same pages with that step switched off.
    final_pairs, first_pairs, part_lines = {}, {}, {}
    settle = ocr.settle
    for doc_id, kind, data in docs:
        for label, text, ref, conf, _ in read_pairs_scored(data, doc_id):
            if label in ocr.PART_LABELS:
                part_lines.setdefault(doc_id, []).append(text)
            final_pairs[(doc_id, tuple(ref.bbox))] = (label, text, conf)
        ocr.settle = lambda image, kind_, text, tokens: (text, min(t.conf for t in tokens) if tokens else 0.0)
        try:
            for label, text, ref, conf, _ in read_pairs_scored(data, doc_id):
                first_pairs[(doc_id, tuple(ref.bbox))] = (label, text, conf)
        finally:
            ocr.settle = settle
    kinds = {d["id"]: d["kind"] for d in case["documents"]}
    fields = []
    for field in sorted(SCAN_FIELDS):
        want = gold["truth"].get(field)
        got = getattr(ex, field)
        ref = ex.evidence.get(field)
        if field not in gold["visible_fields"]:
            continue  # not printed on any page of this claim: nothing to read
        item = {"field": field, "label": FIELD_LABEL[field], "decisive": field in DECISIVE, "truth": _text(want), "value": _text(got),
                "printed": True}
        if ref is not None:
            key = (ref.doc_id, tuple(ref.bbox))
            label, text, conf = final_pairs.get(key, (None, None, None))
            f_label, f_text, f_conf = first_pairs.get(key, (None, None, None))
            item.update({"doc_id": ref.doc_id, "doc_kind": kinds.get(ref.doc_id), "bbox": [round(v, 4) for v in ref.bbox], "first_read": f_text,
                         "first_conf": None if f_conf is None else round(f_conf, 3), "read": text, "read_conf": None if conf is None else round(conf, 3)})
            how = []
            if f_text is not None and text is not None and (f_text != text):
                how.append("second_read")
            elif f_conf is not None and conf is not None and conf > f_conf + 1e-9:
                how.append("second_read_confirmed")
            if text is not None and _repair(field, text) != text:
                how.append("repaired")
            if field == "total_amount" and text is not None:
                amount, confirmed = ocr.checked_amount(text, part_lines.get(ref.doc_id, []))
                if confirmed:
                    how.append("sum_checked")
            item["how"] = how
            floor_ok = (not item["decisive"]) or ("sum_checked" in how) or (conf is not None and conf >= 0.7)
            item["flagged"] = not floor_ok
        else:
            item.update({"first_read": None, "read": None, "how": [], "flagged": item["decisive"]})
        item["correct"] = got is not None and E._same(field, got, want, level)
        fields.append(item)
    return {"case_id": case["case_id"], "scenario": case["scenario"], "requested": case["requested"], "level": level,
            "documents": [{"id": d["id"], "kind": d["kind"]} for d in case["documents"]],
            "fields": fields, "confidence": round(ex.confidence, 3),
            "outcome": decision.outcome, "amount": decision.total_amount, "rules": decision.rule_ids(),
            "reasons": [{"rule_id": r.rule_id, "severity": r.severity, "message": r.message} for r in decision.reasons],
            "gold": gold["gold"]["outcome"], "gold_amount": gold["gold"]["total_amount"]}


NOTES = {
    "plain": "지급에 쓰는 값을 모두 맞게 읽었고 신뢰도가 기준(0.7) 이상이라 사람 없이 지급됩니다. 합계는 영수증의 두 줄이 더해져 맞을 때만 확정합니다.",
    "second-read": "처음 읽기는 이 값의 모양이 틀렸습니다(코드는 영문 한 글자로 시작합니다). 그래서 값이 있는 상자만 3배로 키워 그 필드에 가능한 글자만 허용해 한 번 더 읽고, 그 값이 필드의 모양일 때만 받습니다.",
    "repaired": "스캔은 쉼표를 마침표로, 날짜의 '년'·'월'을 숫자나 글자로 읽기도 합니다. 자리가 말해 주는 만큼만 고치고, 합계는 영수증 두 줄의 합이 맞을 때만 확정합니다.",
    "unread": "읽지 못했거나 신뢰도가 낮은 값은 지급에 쓰지 않고 담당자에게 보냅니다. 틀린 채 지급되는 것보다 사람이 한 번 보는 쪽을 택한 동작입니다. 0이 O·D·Q 중 무엇인지처럼 글자 모양이 정하지 못하는 것은 추측하지 않습니다.",
    "scan-denial": "정답은 부지급이지만 스캔에서 읽은 값으로는 부지급을 확정하지 않습니다(R-SCAN-01). 사람이 원본을 봅니다.",
}


def decisive_ok(c: dict) -> bool:
    return all(f["correct"] for f in c["fields"] if f["decisive"] and f["printed"])


def pick(cases: list[dict]) -> list[dict]:
    """Five claims by rule, the first of each kind in case-number order. Each rule is named in the record."""
    def first(test):
        return next((c for c in cases if test(c)), None)

    def has(c, how, correct=True):
        return any(f["field"] in DECISIVE and how in f["how"] and f["correct"] == correct and f["printed"] for f in c["fields"])

    chosen = [
        ("plain", "스캔을 그대로 읽어 자동 지급", "읽은 값이 모두 정답이고 결정도 정답(지급)인 첫 건",
         first(lambda c: c["outcome"] == "APPROVE" and c["gold"] == "APPROVE" and c["amount"] == c["gold_amount"] and decisive_ok(c)
               and not any(f["how"] for f in c["fields"] if f["decisive"]))),
        ("second-read", "첫 읽기가 틀린 코드를 확대해 다시 읽어 바로잡음", "두 번째 읽기가 결정에 쓰는 값을 정답으로 바꾸고, 정답이 자동 지급이며 그대로 지급된 첫 건",
         first(lambda c: has(c, "second_read") and c["outcome"] == "APPROVE" == c["gold"] and c["amount"] == c["gold_amount"])),
        ("repaired", "읽은 글자를 자리에 맞게 보정", "금액·날짜·코드의 글자 보정을 거쳐 정답이 되고(두 번째 읽기 없이), 정답이 자동 지급이며 그대로 지급된 첫 건",
         first(lambda c: has(c, "repaired") and c["outcome"] == "APPROVE" == c["gold"] and c["amount"] == c["gold_amount"] and not has(c, "second_read"))),
        ("unread", "못 읽은 값은 사람에게", "결정에 쓰는 값을 틀리게 읽거나 못 읽었고 결과가 담당자 확인인 첫 건(정답은 지급)",
         first(lambda c: c["outcome"] == "REVIEW" and c["gold"] == "APPROVE" and any((not f["correct"]) and f["decisive"] and f["printed"] for f in c["fields"]))),
        ("scan-denial", "스캔에서 읽은 값만으로는 거절을 확정하지 않음", "정답은 부지급인데 스캔 규칙(R-SCAN-01)으로 담당자 확인이 된 첫 건",
         first(lambda c: c["gold"] == "DENY" and c["outcome"] == "REVIEW" and "R-SCAN-01" in c["rules"])),
    ]
    return [{"id": i, "title": t, "rule": r, "claim": c} for i, t, r, c in chosen if c is not None]


def page_image(root: Path, case: dict, doc_id: str, level: str, out: Path) -> tuple[int, int]:
    data = (root / case["case_id"] / f"{doc_id}.pdf").read_bytes()
    img = ocr.render(scan_pdf(data, level, doc_id), 0)
    img = img.resize((IMAGE_WIDTH, round(img.height * IMAGE_WIDTH / img.width)))
    img.save(out, "JPEG", quality=58, optimize=True)
    return img.size


def story_doc(claim: dict) -> str:
    """The page whose picture is shown: the one holding the most of the values that decided or failed the claim."""
    score: dict[str, int] = {}
    for f in claim["fields"]:
        if f.get("doc_id") and f["decisive"]:
            score[f["doc_id"]] = score.get(f["doc_id"], 0) + 1 + (3 if (f["how"] or not f["correct"]) else 0)
    return max(score, key=score.get) if score else claim["documents"][0]["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("var/claims"))
    ap.add_argument("--level", default="heavy", choices=["clean", "light", "medium", "heavy"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("docs/evaluation"))
    ap.add_argument("--dump", type=Path, help="also write every claim's record here (not committed)")
    ap.add_argument("--from-dump", type=Path, help="pick from a record written by --dump instead of reading the corpus again")
    args = ap.parse_args()
    manifest = json.loads((args.root / "manifest.json").read_text())
    gold = json.loads((args.root / "gold.json").read_text())
    if args.from_dump:
        cases = [{**c, "fields": [f for f in c["fields"] if f.get("printed", True)]} for c in json.loads(args.from_dump.read_text())]
    else:
        jobs = [(args.root, c, gold[c["case_id"]], args.level) for c in manifest["cases"]]
        with ProcessPoolExecutor(args.workers) as pool:
            cases = list(pool.map(read_case, jobs, chunksize=2))
    if args.dump:
        args.dump.write_text(json.dumps(cases, ensure_ascii=False))
    picked = pick(cases)
    folder = args.out / "scan-examples"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.jpg"):
        old.unlink()
    by_id = {c["case_id"]: c for c in manifest["cases"]}
    examples = []
    for p in picked:
        claim = p["claim"]
        doc_id = story_doc(claim)
        name = f"{p['id']}.jpg"
        width, height = page_image(args.root, by_id[claim["case_id"]], doc_id, args.level, folder / name)
        examples.append({"id": p["id"], "title": p["title"], "picked_by": p["rule"], "note": NOTES[p["id"]], "case_id": claim["case_id"], "scenario": claim["scenario"],
                         "level": claim["level"], "page": {"doc_id": doc_id, "doc_kind": next(d["kind"] for d in claim["documents"] if d["id"] == doc_id),
                                                            "image": f"scan-examples/{name}", "width": width, "height": height},
                         "fields": claim["fields"], "confidence": claim["confidence"],
                         "outcome": claim["outcome"], "amount": claim["amount"], "rules": claim["rules"], "reasons": claim["reasons"],
                         "gold": claim["gold"], "gold_amount": claim["gold_amount"]})
    record = {"status": "measured", "what": "스캔한 청구 서류 페이지를 읽은 예시. 합성 서류를 시뮬레이션 스캔(clean·light·medium·heavy 중 %s)으로 만들어 Tesseract로 읽은 결과를 미리 계산해 둔 것입니다." % args.level,
              "generated_at": datetime.now(timezone.utc).isoformat(), "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "corpus": {"claims": len(cases), "level": args.level},
              "caveats": ["서류는 모두 합성이고 스캔은 시뮬레이션입니다. 실제 스캐너·손글씨·도장은 읽어 본 적이 없습니다.",
                          "이름·병명은 띄어쓰기를 무시하고 비교합니다(스캔은 띄어쓰기를 보존하라고 요구할 수 없는 유일한 것입니다). 금액·날짜·코드는 글자 그대로 비교합니다.",
                          "영수증 합계는 실제 지출액입니다. 입원일당·수술비·진단비 같은 정액 담보의 지급액과는 별개이고, 이 시스템은 합계로 지급액을 정하지 않습니다. 합계는 읽은 값이 맞는지(영수증 두 줄의 합) 확인하고 신뢰도를 보는 데만 씁니다.",
                          "추출 신뢰도 0.7 이상은 지급의 필요조건일 뿐입니다. 계약·면책·중복·서류 대조 규칙을 모두 통과해야 지급되고, 지급에 쓰는 값은 하나씩 따로 보아 가장 낮은 신뢰도가 기준입니다.",
                          "예시는 규칙으로 고른 것입니다(각 예시의 picked_by). 측정이 아니며, 측정은 claims-scan-hardening.json에 있습니다.",
                          "공개 데모에는 OCR 엔진이 없어 이 화면은 미리 계산한 기록을 보여 주고, 요청 때 아무것도 읽지 않습니다."],
              "examples": examples}
    (args.out / "scan-examples.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"claims_read": len(cases), "picked": [(e["id"], e["case_id"], e["page"]["doc_kind"]) for e in examples]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
