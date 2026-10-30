"""The scanned official forms through a commercial document AI, scored like the grid reader.

The test split's diagnosis certificates and fee statements are written on the redrawn statutory forms, turned
into page pictures at one damage level (claims/scan.py), and sent to Upstage's information extraction API with
the ten fields as a JSON schema. The same pictures go through this repository's grid reader. Both answers are
scored against the generator's values with the same rules, field by field: correct, missing, wrong. The API
is paid per page, so the run stops at --max-pages and prints what it would cost before it sends anything.

Requires UPSTAGE_API_KEY and FITWITNESS_UPSTAGE_USD_PER_PAGE (the price you agreed to) in the environment."""

import argparse
import base64
import json
import os
import sys
import time
from collections import Counter
from io import BytesIO
from pathlib import Path

sys.path.insert(0, "src")
from fitwitness.claims import ocr  # noqa: E402
from fitwitness.claims.extract import extract_documents, normalise_amount, normalise_date  # noqa: E402

FIELDS = {
    "diagnosis": ["insured_name", "diagnosis_name", "diagnosis_code", "diagnosis_date", "admission_date", "discharge_date", "hospital"],
    "receipt": ["insured_name", "hospital", "total_amount"],
}
# diagnosis_name: the four-page check (Actions 39917721376) used "병명" alone, and the vendor joined the main disease
# with the 부 질병 decoy the fill tool writes next to it (2 of 2). The gold and the grid reader mean the main disease
# only, so the field is defined that way here; that one line was changed after seeing those four test pages.
DESCRIPTIONS = {
    "insured_name": "환자 성명",
    "diagnosis_name": "최종진단의 주 질병 병명 하나 (부 질병ㆍ부상 칸은 제외)",
    "diagnosis_code": "질병분류기호 (KCD code such as K35.8)",
    "diagnosis_date": "진단 연월일, as YYYY-MM-DD",
    "admission_date": "입원 연월일, as YYYY-MM-DD",
    "discharge_date": "퇴원 연월일, as YYYY-MM-DD",
    "hospital": "의료기관 명칭",
    "total_amount": "합계 금액 in won, digits only",
}
ENDPOINT = "https://api.upstage.ai/v1/information-extraction/chat/completions"


def schema(kind: str) -> dict:
    props = {f: {"type": ("integer" if f == "total_amount" else "string"), "description": DESCRIPTIONS[f]} for f in FIELDS[kind]}
    return {"type": "json_schema", "json_schema": {"name": f"{kind}_form", "schema": {"type": "object", "properties": props}}}


def same(field: str, got, want) -> bool:
    if got in (None, "") or want in (None, ""):
        return False
    if field.endswith("_date"):
        return normalise_date(str(got)) == want
    if field == "total_amount":
        return normalise_amount(str(got)) == want
    if field == "diagnosis_code":
        return str(got).strip().upper() == str(want).upper()
    return "".join(str(got).split()) == "".join(str(want).split())


def score(fields: list[str], got: dict, truth: dict, counter: dict) -> None:
    for f in fields:
        want = truth.get(f)
        if want in (None, ""):
            continue
        c = counter.setdefault(f, Counter())
        value = got.get(f)
        c["correct" if same(f, value, want) else "missing" if value in (None, "") else "wrong"] += 1


def page_png(pdf: bytes) -> bytes:
    image = ocr.render(pdf, 0)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def ask_upstage(png: bytes, kind: str, key: str) -> tuple[dict, dict]:
    import httpx

    body = {
        "model": "information-extract",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}],
            }
        ],
        "response_format": schema(kind),
    }
    started = time.perf_counter()
    r = httpx.post(ENDPOINT, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
    latency = (time.perf_counter() - started) * 1000
    if r.status_code >= 400:  # the body says why (a wrong field, a model name, a quota); raise_for_status drops it
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:600]}")
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}
    return parsed, {"latency_ms": round(latency, 1), "usage": data.get("usage"), "raw": content[:2000]}


def summarise(counter: dict) -> dict:
    total = sum(sum(c.values()) for c in counter.values())
    correct = sum(c["correct"] for c in counter.values())
    return {
        "fields": total,
        "correct": correct,
        "missing": sum(c["missing"] for c in counter.values()),
        "wrong": sum(c["wrong"] for c in counter.values()),
        "accuracy": round(correct / total, 4) if total else None,
        "by_field": {f: dict(c) for f, c in sorted(counter.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="var/claims")
    ap.add_argument("--level", choices=["clean", "light", "medium", "heavy"], default="medium")
    ap.add_argument("--split", choices=["dev", "test"], default="test")
    ap.add_argument("--max-pages", type=int, default=120, help="hard cap on pages sent to the API")
    ap.add_argument("--output", default="artifacts/vendor-ocr")
    a = ap.parse_args()
    key, price = os.getenv("UPSTAGE_API_KEY"), os.getenv("FITWITNESS_UPSTAGE_USD_PER_PAGE")
    if not key or not price:
        print("UPSTAGE_API_KEY and FITWITNESS_UPSTAGE_USD_PER_PAGE are required; nothing was sent", file=sys.stderr)
        return 2
    from fitwitness.claims.official_fill import fill
    from fitwitness.claims.scan import scan_pdf

    root = Path(a.root)
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    cases = [c for c in manifest["cases"] if (int(c["case_id"].split("-")[-1]) % 2 == 1) == (a.split == "test")]
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    pages = [(c, kind) for c in cases for kind in FIELDS if kind in {d["kind"] for d in c["documents"]}]
    pages = pages[: a.max_pages]
    estimate = len(pages) * float(price)
    print(f"== plan pages={len(pages)} level={a.level} split={a.split} estimated_usd={estimate:.4f} (at {price} USD/page)", flush=True)
    vendor, ours, rows, errors = {}, {}, [], 0
    for case, kind in pages:
        truth = gold[case["case_id"]]["truth"]
        doc_id = f"{case['case_id']}-{kind}"
        scanned = scan_pdf(fill(kind, truth, blank=None, seed=doc_id), a.level, doc_id)
        png = page_png(scanned)
        try:
            answer, meta = ask_upstage(png, kind, key)
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            answer, meta, errors = {}, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, errors + 1
        mine = extract_documents([(doc_id, kind, scanned)])
        got_ours = {f: getattr(mine, f) for f in FIELDS[kind]}
        score(FIELDS[kind], answer, truth, vendor)
        score(FIELDS[kind], got_ours, truth, ours)
        rows.append(
            {
                "case_id": case["case_id"],
                "kind": kind,
                "vendor": answer,
                "vendor_meta": meta,
                "grid_reader": got_ours,
                "truth": {f: truth.get(f) for f in FIELDS[kind]},
            }
        )
        # The artifact cannot always be fetched from where the log is read, so each page's answer or error is printed.
        print(
            f"== page {len(rows)}/{len(pages)} {doc_id} "
            + json.dumps(
                {
                    "error": meta.get("error"),
                    "vendor": answer,
                    "grid_reader": got_ours,
                    "truth": rows[-1]["truth"],
                    "latency_ms": meta.get("latency_ms"),
                    "usage": meta.get("usage"),
                    "raw": None if answer else meta.get("raw"),
                },
                ensure_ascii=False,
                default=str,
            )[:3000],
            flush=True,
        )
    report = {
        "status": "measured" if rows else "not measured",
        "vendor": "upstage information-extract",
        "endpoint": ENDPOINT,
        "level": a.level,
        "split": a.split,
        "pages": len(rows),
        "vendor_errors": errors,
        "estimated_usd": round(estimate, 4),
        "usd_per_page": price,
        "vendor_summary": summarise(vendor),
        "grid_reader_summary": summarise(ours),
        "rows": rows,
        "limitations": [
            "합성 값을 다시 그린 서식에 채워 스캔처럼 열화시킨 그림입니다. 병원이 낸 실제 서류가 아닙니다.",
            "같은 그림을 두 판독기에 넣었고, 정답은 생성기의 값입니다. 벤더 쪽은 필드 설명이 든 JSON 스키마 한 번 호출입니다.",
            "병명 필드의 설명은 test 4쪽 확인 실행(39917721376)에서 벤더가 주 질병과 부 질병 미끼를 합쳐 답한 것을 본 뒤 '주 질병 하나'로 고쳤습니다. 그 밖의 조정은 없습니다.",
            "비용은 페이지 수 × 환경 변수의 단가로 어림한 값이고, 실제 청구서는 벤더 콘솔에서 확인해야 합니다.",
        ],
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("== vendor", json.dumps(report["vendor_summary"], ensure_ascii=False))
    print("== grid_reader", json.dumps(report["grid_reader_summary"], ensure_ascii=False))
    print("== pages", len(rows), "errors", errors, "estimated_usd", report["estimated_usd"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
