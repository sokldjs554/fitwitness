"""External NIST STEP cross-format geometry evaluation.

AP203 geometry-only files are queries and corresponding AP242 files are the
candidate corpus. Pair IDs come only from public filenames; ranking receives
geometry features, never the expected pair label.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
import zipfile
from hashlib import sha256
from pathlib import Path
from statistics import mean

from fitwitness.ingest.cad import extract_step
from fitwitness.retrieval.geometry import GEOMETRY_WEIGHTS, rank_geometry

NIST_SOURCE_URL = (
    "https://raw.githubusercontent.com/usnistgov/SFA/"
    "72375d17c09cf7072c30d9393b5c08b7aa272285/"
    "Release/NIST-PMI-STEP-Files.zip"
)
NIST_SOURCE_SHA256 = "1fb91bb8ff0fe02032b948fda0775bc74591cd0bebc0988347d32574e5884f90"
CASE_RE = re.compile(r"nist_(ctc_\d+|ftc_\d+)", re.IGNORECASE)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * q
    lo = math.floor(index)
    hi = math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - index) + ordered[hi] * (index - lo)


def _case_id(path: str) -> str | None:
    match = CASE_RE.search(path)
    return match.group(1).lower() if match else None


def _variant(path: str) -> str:
    return "ap203" if "/AP203 geometry only/" in path.replace("\\", "/") else "ap242"


def _canonical_pairs(names: list[str]) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for name in names:
        case = _case_id(name)
        if not case:
            continue
        variant = _variant(name)
        grouped.setdefault(case, {}).setdefault(variant, []).append(name)
    pairs = {}
    for case, variants in grouped.items():
        if "ap203" not in variants or "ap242" not in variants:
            continue
        ap203 = sorted(variants["ap203"])[0]
        preferred = sorted(
            variants["ap242"],
            key=lambda path: ("-tg" in path.lower(), len(path), path),
        )[0]
        pairs[case] = {"ap203": ap203, "ap242": preferred}
    return dict(sorted(pairs.items()))


def _feature_view(features: dict) -> dict:
    return {
        "bbox_mm": features["bbox_mm"],
        "volume_mm3": features["volume_mm3"],
        "surface_mm2": features["surface_mm2"],
        "source_hash": features["source_hash"],
        "feature_hash": features["feature_hash"],
    }


def run(source_zip: Path, output: Path):
    body = source_zip.read_bytes()
    digest = sha256(body).hexdigest()
    if digest != NIST_SOURCE_SHA256:
        raise ValueError(f"unexpected NIST source hash: {digest}")

    with zipfile.ZipFile(source_zip) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".stp", ".step"))
        ]
        pairs = _canonical_pairs(names)
        if len(pairs) != 11:
            raise ValueError(f"expected 11 AP203/AP242 pairs, got {len(pairs)}")

        output.mkdir(parents=True, exist_ok=False)
        code_sha = os.getenv("GITHUB_SHA") or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        protocol = {
            "version": "nist-cad-v1",
            "scope": "external STEP cross-format geometry retrieval; not production industrial validation",
            "source_url": NIST_SOURCE_URL,
            "source_sha256": digest,
            "source_file_count": len(names),
            "paired_case_count": len(pairs),
            "pairing": "NIST CTC/FTC filename ID; AP203 geometry-only query -> canonical AP242 candidate",
            "weights": GEOMETRY_WEIGHTS,
            "ranking_input": ["bbox_mm", "volume_mm3", "surface_mm2"],
            "selection": "all 11 CTC01-05/FTC06-11 pairs in the pinned archive; no post-score filtering",
            "code_sha": code_sha,
        }
        (output / "protocol.json").write_text(
            json.dumps(protocol, ensure_ascii=False, indent=2) + "\n"
        )

        features: dict[str, dict[str, dict]] = {}
        parse_rows = []
        for case, variants in pairs.items():
            features[case] = {}
            for variant, name in variants.items():
                started = time.perf_counter()
                error = None
                values = None
                try:
                    values = extract_step(archive.read(name), include_mesh=False)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                latency_ms = (time.perf_counter() - started) * 1000
                parse_rows.append(
                    {
                        "case_id": case,
                        "variant": variant,
                        "path": name,
                        "status": "error" if error else "ok",
                        "error": error,
                        "latency_ms": latency_ms,
                        "features": _feature_view(values) if values else None,
                    }
                )
                if values:
                    features[case][variant] = values

        failed = [row for row in parse_rows if row["status"] != "ok"]
        (output / "parse.json").write_text(
            json.dumps(parse_rows, ensure_ascii=False, indent=2) + "\n"
        )
        if failed:
            raise RuntimeError(
                f"external STEP parsing failed for {len(failed)} selected files"
            )

        candidates = {case: values["ap242"] for case, values in features.items()}
        rows = []
        for case, values in features.items():
            ranking = rank_geometry(values["ap203"], candidates)
            rank = 1 + next(
                index
                for index, candidate in enumerate(ranking)
                if candidate["candidate_id"] == case
            )
            expected = next(
                candidate
                for candidate in ranking
                if candidate["candidate_id"] == case
            )
            rows.append(
                {
                    "case_id": case,
                    "expected_candidate": case,
                    "rank": rank,
                    "expected_distance": expected,
                    "top5": ranking[:5],
                }
            )

        with (output / "runs.jsonl").open("w") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

        ranks = [row["rank"] for row in rows]
        parse_latencies = [row["latency_ms"] for row in parse_rows]
        pair_distances = [row["expected_distance"]["distance"] for row in rows]
        metrics = {
            "paired_cases": len(rows),
            "selected_files_parsed": len(parse_rows),
            "parse_success_rate": 1.0,
            "top1_accuracy": mean(rank == 1 for rank in ranks),
            "top3_accuracy": mean(rank <= 3 for rank in ranks),
            "mrr": mean(1 / rank for rank in ranks),
            "paired_distance_p50": _percentile(pair_distances, 0.5),
            "paired_distance_p95": _percentile(pair_distances, 0.95),
            "parse_latency_p50_ms": _percentile(parse_latencies, 0.5),
            "parse_latency_p95_ms": _percentile(parse_latencies, 0.95),
        }
        report = {
            "status": "measured",
            "protocol": protocol,
            "metrics": metrics,
            "cases": rows,
            "parse": parse_rows,
            "raw_sha256": sha256((output / "runs.jsonl").read_bytes()).hexdigest(),
            "limitations": [
                "NIST PMI validation test cases are external engineering benchmark models, not production drawings from a factory.",
                "The AP203/AP242 pair represents the same design in different STEP encodings; this measures cross-format geometry extraction and nearest-geometry retrieval, not semantic PMI correctness.",
                "Only 11 paired CTC/FTC cases are scored. STC and duplicate datum-target encodings remain in the pinned source inventory but are not added as extra expected pairs.",
                "The distance uses three auditable global features. Local feature matching, assembly constraints, tolerances and expert manufacturing approval are outside this result.",
                "Weights and pair selection are frozen in protocol.json before parsing/ranking; no score-based tuning is performed.",
            ],
        }
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source_zip, args.output)
