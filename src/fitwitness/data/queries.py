"""Independent synthetic gold construction. This module is not imported by runtime."""

from decimal import Decimal


def build_queries(docs, gold):
    queries = []
    families = sorted({d["family_id"] for d in docs})
    for family in families:
        items = [d for d in docs if d["family_id"] == family]
        base = items[0]
        truth = gold[base["id"]]
        kind = base["kind"]
        width = truth["width"]
        spacing = truth.get("hole_spacing")
        alias = base["title"].split()[0]
        for i, category in enumerate(
            [
                "exact",
                "alias",
                "image",
                "hard_negative",
                "no_answer",
                "missing_fact",
                "unit",
                "revision",
            ]
        ):
            requirements = [
                {"field": "kind", "value": kind},
                {"field": "material", "value": "SUS304"},
                {
                    "field": "width",
                    "value": {"low": str(width), "high": str(width)},
                    "unit": "mm",
                },
            ]
            if spacing is not None:
                requirements.append(
                    {
                        "field": "hole_spacing",
                        "value": {"low": str(spacing), "high": str(spacing)},
                        "unit": "mm",
                    }
                )
            text = {
                "exact": base["drawing_number"],
                "alias": f"{alias} {base['title'].split()[1]} 너비 {width}mm",
                "image": "이 도면처럼 생긴 부품",
                "hard_negative": f"{alias} 너비 {width}mm 소재 SUS304 구멍 간격 {spacing}mm",
                "no_answer": f"{alias} 너비 9999mm 소재 SUS304",
                "missing_fact": items[3]["drawing_number"] + " 소재 확인",
                "unit": f"{alias} 너비 {width / 10}cm 단위 변환",
                "revision": base["drawing_number"] + " 최신 개정판 B",
            }[category]
            if category == "no_answer":
                requirements[2]["value"] = {"low": "9999", "high": "9999"}
            if category == "unit":
                requirements[2]["value"] = {
                    "low": str(Decimal(width) / 10),
                    "high": str(Decimal(width) / 10),
                }
                requirements[2]["unit"] = "cm"
            use_updates = category == "revision"
            active = [
                d
                for d in docs
                if not d["is_revision_update"]
                and not (use_updates and any(x["supersedes"] == d["id"] for x in docs))
                or use_updates
                and d["is_revision_update"]
            ]
            verdicts = {}
            for d in active:
                values = {**gold[d["id"]], "kind": d["kind"]}
                checks = []
                for r in requirements:
                    actual = values.get(r["field"])
                    if actual is None:
                        checks.append("unknown")
                    elif isinstance(r["value"], dict):
                        scale = 10 if r["unit"] == "cm" else 1
                        checks.append(
                            "match"
                            if Decimal(r["value"]["low"]) * scale
                            <= Decimal(actual)
                            <= Decimal(r["value"]["high"]) * scale
                            else "mismatch"
                        )
                    else:
                        checks.append("match" if actual == r["value"] else "mismatch")
                verdicts[d["id"]] = (
                    "mismatch"
                    if "mismatch" in checks
                    else "unknown"
                    if "unknown" in checks
                    else "match"
                )
            queries.append(
                {
                    "id": f"{family}-Q{i}",
                    "family_id": family,
                    "category": category,
                    "text": text,
                    "image_id": base["id"] if category == "image" else None,
                    "requirements": requirements,
                    "apply_updates": use_updates,
                    "expected_matches": [
                        rid for rid, v in verdicts.items() if v == "match"
                    ],
                    "expected_verdicts": verdicts,
                }
            )
    return queries
