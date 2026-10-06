"""Synthetic claim cases: rendered Korean PDFs, a manifest the pipeline reads, and gold
the evaluator reads. Nothing here is a real person, hospital, contract or record.

A case carries the truth (dates, code, grade, amount), the scenario that shaped its
documents (clean, format variance, OCR-like noise, a missing document, a date
conflict, waiting period, exclusion, duplicate, lapsed policy, high amount, missing
grade), and the gold decision, which is the rules engine applied to the truth. So the
evaluation measures extraction + validation + routing against a deterministic oracle.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from fitwitness.claims import vocab
from fitwitness.claims.models import ClaimDecision, ClaimDocument, Extraction, Policy
from fitwitness.claims.policy import PRODUCTS, REQUIRED_DOCS, adjudicate

FONT = Path(__file__).with_name("fonts") / "NotoSansKR-claims.ttf"
FONT_NAME = "NotoSansKR-claims"
DATE_FORMATS = ["%Y-%m-%d", "%Y.%m.%d", "%Y년 %m월 %d일", "%Y/%m/%d"]
SCENARIOS = ["clean", "ocr_noise", "format_variance", "missing_doc", "date_conflict", "waiting_period",
             "exclusion", "duplicate", "lapsed_policy", "high_amount", "grade_missing"]
SCENARIO_LABELS = {
    "clean": "정상 청구", "ocr_noise": "스캔 품질 저하", "format_variance": "표기 형식 다양", "missing_doc": "서류 누락",
    "date_conflict": "서류 간 날짜 불일치", "waiting_period": "면책기간 내 진단", "exclusion": "면책 질병",
    "duplicate": "중복 청구", "lapsed_policy": "실효 계약", "high_amount": "자동승인 한도 초과", "grade_missing": "수술 등급 누락",
}
_ALLOWED = set(vocab.all_text())


@dataclass
class ClaimCase:
    case_id: str
    claim_id: str
    policy: Policy
    insured_name: str
    requested: list[str]
    documents: list[ClaimDocument]
    submitted_at: str
    scenario: str
    truth: Extraction
    gold: ClaimDecision
    prior_paid_keys: list[str] = field(default_factory=list)
    gold_flags: list[str] = field(default_factory=list)
    visible_fields: list[str] = field(default_factory=list)


def _fmt_date(d: date, fmt: str) -> str:
    if "월" in fmt:
        return f"{d.year}년 {d.month}월 {d.day}일"
    return d.strftime(fmt)


def _amount(n: int, style: int) -> str:
    return [f"{n:,}원", f"{n:,} 원", f"금 {n:,}원정", f"{n}원"][style % 4]


def _ocr(text: str, rng: random.Random, rate: float) -> str:
    subs = {"0": "O", "1": "l", ".": ",", "8": "B", "5": "S"}
    return "".join(subs[ch] if ch in subs and rng.random() < rate else ch for ch in text)


def _check_vocab(*parts: str) -> None:
    bad = {ch for p in parts for ch in p} - _ALLOWED
    if bad:
        raise ValueError(f"text uses glyphs outside the embedded font subset: {sorted(bad)}")


def render_document(path: Path, title: str, rows: list[tuple[str, str]], issued: date, hospital: str,
                    doctor: str, doc_no: str, closing: str) -> None:
    """One A4 page: title, numbered form rows, closing line, issuing hospital, disclaimer."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT)))
    _check_vocab(title, closing, hospital, doctor, doc_no, "보험회사 제출용", "문서번호:", "발급일:", "발행기관:", "담당의:", "면허번호:",
                 "연구용 합성 문서 · 실제 환자 정보가 아닙니다", *(k + v for k, v in rows))
    c = canvas.Canvas(str(path), pagesize=(595, 842), invariant=1)
    c.setTitle(title)
    c.setFont(FONT_NAME, 20)
    c.drawCentredString(297, 760, title)
    c.setFont(FONT_NAME, 9)
    c.drawString(60, 790, "문서번호:")
    c.drawString(110, 790, doc_no)
    c.drawRightString(535, 790, "보험회사 제출용")
    c.setLineWidth(0.6)
    c.line(60, 740, 535, 740)
    y = 700
    c.setFont(FONT_NAME, 11)
    for label, value in rows:
        c.drawString(70, y, label)
        c.drawString(190, y, value)
        c.setLineWidth(0.2)
        c.line(60, y - 8, 535, y - 8)
        y -= 32
    y -= 20
    c.setFont(FONT_NAME, 11)
    c.drawString(70, y, closing)
    y -= 50
    c.drawString(70, y, "발급일:")
    c.drawString(190, y, issued.isoformat())
    y -= 32
    c.drawString(70, y, "발행기관:")
    c.drawString(190, y, hospital)
    y -= 32
    c.drawString(70, y, "담당의:")
    c.drawString(190, y, doctor)
    c.drawString(330, y, "면허번호:")
    c.drawString(400, y, f"제 {abs(hash(doctor)) % 90000 + 10000} 호")
    c.setFont(FONT_NAME, 8)
    c.drawString(60, 60, "연구용 합성 문서 · 실제 환자 정보가 아닙니다")
    c.drawString(60, 48, "FitWitness synthetic claim document. Not a real medical record.")
    c.save()


def render_png(pdf_path: Path, png_path: Path, scale: float = 1.2) -> None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    pdf[0].render(scale=scale).to_pil().save(png_path)


def build_documents(case_dir: Path, case_id: str, truth: Extraction, requested: list[str], issued: date,
                    rng: random.Random, fmt: str, amt_style: int, department: str, omit: str | None,
                    ocr_rate: float, conflict: bool, doctor: str) -> list[ClaimDocument]:
    need: set[str] = set()
    for k in requested:
        need |= REQUIRED_DOCS[k]
    need.add("receipt")
    hosp = truth.hospital or vocab.HOSPITALS[0]
    name = truth.insured_name or ""

    def d(s: str | None) -> str:
        return _ocr(_fmt_date(date.fromisoformat(s), fmt), rng, ocr_rate) if s else "-"

    docs: list[ClaimDocument] = []

    def emit(kind: str, title: str, rows: list[tuple[str, str]], closing: str) -> None:
        doc_id = f"{case_id}-{kind[:3].upper()}"
        pdf = case_dir / f"{doc_id}.pdf"
        render_document(pdf, title, rows, issued, hosp, doctor, f"{issued.year}-{abs(hash(doc_id)) % 900000 + 100000}", closing)
        render_png(pdf, case_dir / f"{doc_id}.png")
        docs.append(ClaimDocument(id=doc_id, case_id=case_id, kind=kind, issued_at=issued.isoformat()))

    if "diagnosis" in need and omit != "diagnosis":
        emit("diagnosis", "진단서", [
            ("환자성명:", name), ("병명:", truth.diagnosis_name or "-"),
            ("질병분류기호:", _ocr(truth.diagnosis_code or "-", rng, ocr_rate)), ("진단일:", d(truth.diagnosis_date)),
        ], "위와 같이 진단함.")
    if "admission" in need and omit != "admission":
        discharge = truth.discharge_date
        if conflict and truth.admission_date:
            discharge = (date.fromisoformat(truth.admission_date) - timedelta(days=2)).isoformat()  # before admission
        emit("admission", "입퇴원확인서", [
            ("성명:", name), ("입원일:", d(truth.admission_date)), ("퇴원일:", d(discharge)), ("진료과:", department),
        ], "위와 같이 확인함.")
    if "surgery" in need and omit != "surgery":
        rows = [("환자:", name), ("수술명:", truth.surgery_name or "-"), ("수술일:", d(truth.surgery_date))]
        if omit != "grade" and truth.surgery_grade is not None:
            rows.append(("수술분류:", f"{truth.surgery_grade}종"))
        rows.append(("관련진단:", _ocr(truth.diagnosis_code or "-", rng, ocr_rate)))
        emit("surgery", "수술확인서", rows, "위와 같이 확인함.")
    if omit != "receipt":
        total = truth.total_amount or 0
        start, end = truth.admission_date or truth.diagnosis_date, truth.discharge_date or truth.diagnosis_date
        emit("receipt", "진료비 계산서·영수증", [
            ("환자명:", name), ("진료기간:", f"{d(start)} ~ {d(end)}"),
            ("급여본인부담금:", _amount(int(total * 0.6), amt_style)), ("비급여:", _amount(total - int(total * 0.6), amt_style)),
            ("합계:", _amount(total, amt_style)),
        ], "위와 같이 확인함.")
    return docs


def generate_cases(root: Path, n: int = 120, seed: int = 7) -> list[ClaimCase]:
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    cases: list[ClaimCase] = []
    for i in range(n):
        scenario = SCENARIOS[i % len(SCENARIOS)] if i < len(SCENARIOS) * 3 else \
            rng.choices(SCENARIOS, weights=[30, 12, 12, 8, 6, 6, 6, 6, 5, 5, 4])[0]
        product_id = "CANCER-B" if (scenario == "waiting_period" or rng.random() < 0.25) else "HLTH-A"
        name = rng.choice(vocab.SURNAMES) + rng.choice(vocab.GIVEN)
        eff_from = date(2025, 1, 1) + timedelta(days=rng.randint(0, 200))
        policy = Policy(policy_id=f"POL-{i:05d}", insured_id=f"INS-{i:04d}", product_id=product_id,
                        effective_from=eff_from.isoformat(), effective_to=(eff_from + timedelta(days=365 * 5)).isoformat(),
                        status="lapsed" if scenario == "lapsed_policy" else "active")
        pool = [dx for dx in vocab.DIAGNOSES if not dx[0].startswith(("C44", "Q", "Z41"))]
        if scenario == "exclusion":
            pool = [dx for dx in vocab.DIAGNOSES if dx[0].startswith(("C44", "Q", "Z41"))]
        if scenario == "waiting_period":
            pool = [dx for dx in vocab.DIAGNOSES if dx[0].startswith("C") and not dx[0].startswith("C44")]
        if scenario == "high_amount":
            pool = [dx for dx in vocab.DIAGNOSES if dx[0].startswith(("C16", "C50", "I21", "I63"))]
        if scenario == "clean":
            # The reference scenario stays below the auto-approve limit: no lump-sum diagnoses.
            pool = [dx for dx in pool if not dx[0].startswith(("C", "D0", "I21", "I6"))]
        code, dx_name = rng.choice(pool)
        dx_date = eff_from + timedelta(days=rng.randint(1, 80) if scenario == "waiting_period" else rng.randint(100, 700))
        requested: list[str] = []
        if code in vocab.SURGERIES and rng.random() < 0.8:
            requested.append("surgery")
        if rng.random() < 0.8 or not requested:
            requested.append("hospitalization_daily")
        if code.startswith(("C", "D0", "I21", "I6")) and rng.random() < 0.9:
            requested.append("diagnosis")
        if scenario == "waiting_period":
            requested = list(dict.fromkeys(requested + ["diagnosis"]))  # the waiting period applies to the lump sum
        if scenario == "exclusion":
            requested = list(dict.fromkeys(requested + (["diagnosis"] if code.startswith("C44") else ["surgery"])))
        if product_id == "CANCER-B":
            requested = [r for r in requested if r in PRODUCTS[product_id].coverages] or ["hospitalization_daily"]
        if scenario == "date_conflict" and "hospitalization_daily" not in requested:
            requested.append("hospitalization_daily")
        if scenario == "high_amount":
            requested = [r for r in dict.fromkeys(requested + ["diagnosis", "hospitalization_daily"]) if r in PRODUCTS[product_id].coverages]
        nights = rng.randint(1, 12) if scenario != "high_amount" else rng.randint(30, 60)
        adm = dx_date - timedelta(days=rng.randint(0, 1))
        dis = adm + timedelta(days=nights)
        surg = adm + timedelta(days=rng.randint(0, min(2, nights))) if "surgery" in requested else None
        s_name, s_grade = vocab.SURGERIES.get(code, (None, None))
        total = nights * rng.randint(150_000, 400_000) + (1_500_000 if surg else 0)
        truth = Extraction(insured_name=name, hospital=rng.choice(vocab.HOSPITALS), diagnosis_name=dx_name, diagnosis_code=code,
                           diagnosis_date=dx_date.isoformat(), admission_date=adm.isoformat(), discharge_date=dis.isoformat(),
                           surgery_name=s_name if surg else None, surgery_date=surg.isoformat() if surg else None,
                           surgery_grade=s_grade if surg else None, total_amount=total, confidence=1.0)
        fmt = DATE_FORMATS[0] if scenario == "clean" else rng.choice(DATE_FORMATS)
        amt_style = 0 if scenario == "clean" else rng.randint(0, 3)
        omit = None
        if scenario == "missing_doc":
            omit = rng.choice([k for k in ("admission", "surgery", "diagnosis") if any(k in REQUIRED_DOCS[r] for r in requested)])
        if scenario == "grade_missing" and "surgery" in requested:
            omit = "grade"
        case_id = f"CLM-{i:05d}"
        case_dir = root / case_id
        case_dir.mkdir(exist_ok=True)
        department = "외과" if surg else ("혈액종양내과" if code.startswith("C") else "내과")
        docs = build_documents(case_dir, case_id, truth, requested, dis + timedelta(days=3), rng, fmt, amt_style, department,
                               omit, 0.35 if scenario == "ocr_noise" else 0.0, scenario == "date_conflict", rng.choice(vocab.DOCTORS))
        prior: list[str] = []
        if scenario == "duplicate":
            prior.append(f"{policy.policy_id}|hosp|{adm.isoformat()}")
            if surg:
                prior.append(f"{policy.policy_id}|surg|{surg.isoformat()}")
            if "diagnosis" in requested:
                prior.append(f"{policy.policy_id}|diag|{code[:3]}")
        present = {d.kind for d in docs}
        visible = {"insured_name", "hospital", "total_amount"} if "receipt" in present else set()
        if "diagnosis" in present:
            visible |= {"insured_name", "hospital", "diagnosis_name", "diagnosis_code", "diagnosis_date"}
        if "admission" in present:
            visible |= {"insured_name", "admission_date", "discharge_date"}
        if "surgery" in present:
            visible |= {"insured_name", "surgery_name", "surgery_date", "diagnosis_code"} | ({"surgery_grade"} if omit != "grade" else set())
        truth_for_gold = truth.model_copy()
        gold_flags: list[str] = []
        if omit == "grade":
            truth_for_gold.surgery_grade = None
        if scenario == "date_conflict":
            gold_flags.append("discharge_before_admission")
            truth_for_gold.discharge_date = (adm - timedelta(days=2)).isoformat()
        gold = adjudicate(policy, PRODUCTS[product_id], requested, truth_for_gold, present, set(prior), gold_flags)
        cases.append(ClaimCase(case_id=case_id, claim_id=case_id, policy=policy, insured_name=name, requested=requested,
                               documents=docs, submitted_at=(dis + timedelta(days=5)).isoformat(), scenario=scenario, truth=truth,
                               gold=gold, prior_paid_keys=prior, gold_flags=gold_flags, visible_fields=sorted(visible)))
    manifest = {"version": "claims-v1", "seed": seed, "cases": [
        {"case_id": c.case_id, "claim_id": c.claim_id, "policy": c.policy.model_dump(), "insured_name": c.insured_name,
         "requested": c.requested, "documents": [d.model_dump() for d in c.documents], "submitted_at": c.submitted_at,
         "scenario": c.scenario, "prior_paid_keys": c.prior_paid_keys} for c in cases]}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    gold = {c.case_id: {"truth": c.truth.model_dump(), "gold": c.gold.model_dump(), "gold_flags": c.gold_flags,
                        "visible_fields": c.visible_fields} for c in cases}
    (root / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1))
    return cases


def load_manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("var/claims")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    out = generate_cases(target, n)
    print(f"generated {len(out)} claim cases under {target}")
