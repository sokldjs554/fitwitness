"""Reproducible synthetic CAD corpus. Gold is never loaded by the runtime."""

from __future__ import annotations
import hashlib, json, math, random
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
import cadquery as cq
from reportlab.pdfgen import canvas
import pypdfium2 as pdfium

KINDS = ["bracket", "flange", "shaft", "housing"]
ALIASES = {
    "bracket": "브래킷 지지대 장착 고정 bracket mounting support",
    "flange": "플랜지 연결판 배관 flange pipe connector",
    "shaft": "축 샤프트 회전 shaft rotating spindle",
    "housing": "하우징 케이스 지지 housing case mount",
}


def split_families(family_ids: list[str], seed: int = 1701):
    ids = list(family_ids)
    random.Random(seed).shuffle(ids)
    n = len(ids)
    a = int(n * 0.6)
    b = int(n * 0.8)
    return {"train": ids[:a], "dev": ids[a:b], "test": ids[b:]}


def shape_for(kind, spacing, width, thickness):
    if kind == "flange":
        return (
            cq.Workplane("XY")
            .circle(width / 2)
            .extrude(thickness)
            .faces(">Z")
            .workplane()
            .pushPoints([(-spacing / 2, 0), (spacing / 2, 0)])
            .hole(6)
        )
    if kind == "shaft":
        return (
            cq.Workplane("XY")
            .circle(width * 0.3)
            .extrude(width)
            .faces(">Z")
            .workplane()
            .circle(width / 2)
            .extrude(thickness)
        )
    if kind == "housing":
        return (
            cq.Workplane("XY")
            .box(width, width * 0.7, thickness * 3)
            .faces(">Z")
            .workplane()
            .rect(width * 0.65, width * 0.4)
            .cutBlind(-thickness * 2)
        )
    return (
        cq.Workplane("XY")
        .box(width, 30, thickness)
        .faces(">Z")
        .workplane()
        .pushPoints([(-spacing / 2, 0), (spacing / 2, 0)])
        .hole(6)
    )


def render_sheet(path, number, kind, rev, spacing, width, thickness, material):
    c = canvas.Canvas(str(path), pagesize=(720, 510), invariant=1)
    c.setFillColorRGB(0.96, 0.97, 0.96)
    c.rect(0, 0, 720, 510, fill=1, stroke=0)
    c.setStrokeColorRGB(0.18, 0.28, 0.3)
    c.setLineWidth(1.5)
    c.rect(22, 22, 676, 466)
    # Scaled engineering top-view with annotation values derived from the CAD inputs.
    scale = 3
    cx = 335
    cy = 305
    w = width * scale
    if kind == "flange":
        c.circle(cx, cy, w / 2)
    elif kind == "shaft":
        c.circle(cx, cy, w / 2)
        c.circle(cx, cy, w * 0.3)
    elif kind == "housing":
        c.rect(cx - w / 2, cy - w * 0.35, w, w * 0.7)
        c.rect(cx - w * 0.325, cy - w * 0.2, w * 0.65, w * 0.4)
    else:
        c.rect(cx - w / 2, cy - 45, w, 90)
    if kind in ("bracket", "flange"):
        for x in [cx - spacing * scale / 2, cx + spacing * scale / 2]:
            c.circle(x, cy, 9)
            c.line(x, cy - 15, x, cy + 15)
            c.line(x - 15, cy, x + 15, cy)
        c.line(cx - spacing * scale / 2, cy - 75, cx + spacing * scale / 2, cy - 75)
        for x in [cx - spacing * scale / 2, cx + spacing * scale / 2]:
            c.line(x, cy - 55, x, cy - 85)
        c.setFillColorRGB(0.08, 0.18, 0.2)
        c.setFont("Helvetica", 12)
        c.drawCentredString(cx, cy - 94, f"{spacing:.2f} mm")
    c.setFillColorRGB(0.08, 0.18, 0.2)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, 455, number)
    c.setFont("Helvetica", 11)
    c.drawRightString(680, 455, f"REV {rev} | {kind.upper()} | SYNTHETIC")
    lines = [
        f"DRAWING: {number}",
        f"KIND: {kind}",
        f"REVISION: {rev}",
        f"WIDTH: {width:.2f} mm",
        f"THICKNESS: {thickness:.2f} mm",
    ]
    if kind in ("bracket", "flange"):
        lines.append(f"HOLE_SPACING: {spacing:.2f} mm")
    if material:
        lines.append(f"MATERIAL: {material}")
    for i, line in enumerate(lines):
        c.setFont("Helvetica", 11)
        c.drawString(40 + (i // 4) * 335, 142 - (i % 4) * 25, line)
    c.setFont("Helvetica", 8)
    c.drawString(
        40,
        32,
        "FitWitness generated research fixture. Not a certified production drawing.",
    )
    c.save()


def generate_dataset(
    output: Path, *, families: int = 30, variants: int = 6, seed: int = 1701
):
    output = Path(output)
    (output / "input").mkdir(parents=True, exist_ok=True)
    (output / "gold").mkdir(exist_ok=True)
    docs = []
    queries = []
    gold = {}
    for f in range(families):
        family = f"FW-F{f:03d}"
        kind = KINDS[f % 4]
        width = 65 + f * 2
        spacing = 40 + f * 2
        thickness = 4 + f % 5
        for v in range(variants):
            stem = f"{family}-V{v}"
            rev = "B" if v == 5 else "A"
            number = f"FW-{f:03d}-{v if v < 5 else 0}"
            rid = str(uuid5(NAMESPACE_URL, stem))
            docid = str(uuid5(NAMESPACE_URL, number))
            sp = spacing + (2 if v in (1, 5) else 0)
            material = None if v == 3 else "AL6061" if v == 2 else "SUS304"
            wd = width + (10 if v == 4 else 0)
            folder = output / "input" / stem
            folder.mkdir(exist_ok=True)
            obj = shape_for(kind, sp, wd, thickness)
            cq.exporters.export(obj, str(folder / "model.step"))
            render_sheet(
                folder / "drawing.pdf", number, kind, rev, sp, wd, thickness, material
            )
            pdf = pdfium.PdfDocument(str(folder / "drawing.pdf"))
            pdf[0].render(scale=1.3).to_pil().save(folder / "drawing.png")
            pdf.close()
            verts, faces = obj.val().tessellate(0.35)
            mesh = {
                "vertices": [[v.x, v.y, v.z] for v in verts],
                "faces": [list(f) for f in faces],
            }
            (folder / "mesh.json").write_text(json.dumps(mesh, separators=(",", ":")))
            source_hash = hashlib.sha256(
                (folder / "drawing.pdf").read_bytes()
            ).hexdigest()
            prefix = f"input/{stem}"
            entry = {
                "id": rid,
                "document_id": docid,
                "family_id": family,
                "drawing_number": number,
                "revision_label": rev,
                "supersedes": str(uuid5(NAMESPACE_URL, f"{family}-V0"))
                if v == 5
                else None,
                "kind": kind,
                "title": ALIASES[kind],
                "source_hash": source_hash,
                "pdf": prefix + "/drawing.pdf",
                "step": prefix + "/model.step",
                "png": prefix + "/drawing.png",
                "mesh": prefix + "/mesh.json",
                "is_revision_update": v == 5,
            }
            docs.append(entry)
            gold[rid] = {"width": wd, "thickness": thickness, "material": material}
            if kind in ("bracket", "flange"):
                gold[rid]["hole_spacing"] = sp
    from fitwitness.data.queries import build_queries

    queries = build_queries(docs, gold)
    manifest = {
        "version": "synthetic-v1",
        "seed": seed,
        "document_entries": docs,
        "query_entries": queries,
        "family_splits": split_families(sorted({d["family_id"] for d in docs}), seed),
        "provenance": "synthetic CAD, not industrial ground truth",
    }
    (output / "gold/labels.json").write_text(json.dumps(gold, indent=2))
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("var/corpus"))
    a = p.parse_args()
    m = generate_dataset(a.output)
    print(
        f"Generated {len(m['document_entries'])} CAD drawings and {len(m['query_entries'])} query definitions"
    )
