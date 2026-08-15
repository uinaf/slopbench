from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

ROOT = Path(os.environ.get("SLOPBENCH_REVIEW_ROOT", "/app"))
ADJUDICATION = Path(os.environ.get("SLOPBENCH_REVIEW_ADJUDICATION", "/tests/adjudication.json"))
CATEGORIES = {
    "api_contract",
    "concurrency",
    "correctness",
    "data_integrity",
    "error_handling",
    "resource_lifecycle",
    "security",
}
SEVERITIES = {"critical", "high", "medium", "low"}
FINDING_KEYS = {"path", "start_line", "line_count", "category", "severity", "explanation"}


def fail(message: str) -> None:
    raise SystemExit(message)


def canonical_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        fail("finding path must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        fail("finding path must stay within the repository")
    candidate = ROOT / value
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        fail("finding path does not identify a file")
    if not resolved.is_relative_to(ROOT.resolve()):
        fail("finding path must stay within the repository")
    if not resolved.is_file():
        fail("finding path does not identify a file")
    return value


def integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        fail(f"{name} must be a positive integer")
    return value


def normalize_finding(value: object, max_span: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != FINDING_KEYS:
        fail("finding has an invalid shape")
    path = canonical_path(value["path"])
    start = integer(value["start_line"], "start_line")
    line_count = integer(value["line_count"], "line_count")
    if line_count > max_span:
        fail("finding range is not tight")
    file_line_count = len((ROOT / path).read_text().splitlines())
    if start + line_count - 1 > file_line_count:
        fail("finding range exceeds the file")
    category = value["category"]
    severity = value["severity"]
    explanation = value["explanation"]
    if category not in CATEGORIES or severity not in SEVERITIES:
        fail("finding taxonomy value is invalid")
    if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 2000:
        fail("finding explanation is invalid")
    return {
        "path": path,
        "start_line": start,
        "line_count": line_count,
        "category": category,
        "severity": severity,
        "explanation": explanation,
    }


def range_distance(finding: dict[str, object], target: dict[str, object]) -> int:
    start = int(finding["start_line"])
    end = start + int(finding["line_count"]) - 1
    target_start = int(target["start_line"])
    target_end = int(target["end_line"])
    if end < target_start:
        return target_start - end
    if target_end < start:
        return start - target_end
    return 0


def range_shape_distance(finding: dict[str, object], target: dict[str, object]) -> int:
    start = int(finding["start_line"])
    end = start + int(finding["line_count"]) - 1
    return abs(start - int(target["start_line"])) + abs(end - int(target["end_line"]))


def match_rank(
    finding: dict[str, object], target: dict[str, object], tolerance: int
) -> tuple[int, int, int, int] | None:
    if finding["path"] != target["path"]:
        return None
    locations = [
        {"start_line": target["start_line"], "end_line": target["end_line"]},
        *target.get("accepted_locations", []),
    ]
    location_ranks = [
        (range_distance(finding, location), range_shape_distance(finding, location), index)
        for index, location in enumerate(locations)
        if range_distance(finding, location) <= tolerance
    ]
    if not location_ranks:
        return None
    classification = (finding["category"], finding["severity"])
    canonical = (target["category"], target["severity"])
    alternatives = {
        (item["category"], item["severity"]) for item in target.get("accepted_classifications", [])
    }
    if classification == canonical:
        classification_rank = 0
    elif classification in alternatives:
        classification_rank = 1
    else:
        return None
    distance, shape_distance, location_rank = min(location_ranks)
    return distance, shape_distance, classification_rank, location_rank


def finding_identity(finding: dict[str, object]) -> tuple[object, ...]:
    return tuple(
        finding[key] for key in ("path", "start_line", "line_count", "category", "severity")
    )


def main() -> int:
    adjudication = json.loads(ADJUDICATION.read_text())
    rules = adjudication["rules"]
    review_path = ROOT / rules["submission_path"]
    if review_path.is_symlink() or not review_path.is_file():
        fail("slopbench-review.json is missing or not a regular file")
    raw = review_path.read_bytes()
    try:
        submission = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"cannot parse slopbench-review.json: {exc}")
    expected_top = {"schema_version", "task_id", "task_digest", "base_revision", "findings"}
    if not isinstance(submission, dict) or set(submission) != expected_top:
        fail("review submission has an invalid top-level shape")
    task_digest = os.environ.get("SLOPBENCH_TASK_DIGEST", "")
    base_revision = os.environ.get("SLOPBENCH_REVIEW_BASE_REVISION", "")
    if submission["schema_version"] != "slopbench.review.v1":
        fail("review schema version is invalid")
    if submission["task_id"] != adjudication["task_id"]:
        fail("review task_id is invalid")
    if (
        re.fullmatch(r"[0-9a-f]{64}", task_digest) is None
        or submission["task_digest"] != task_digest
    ):
        fail("review task digest does not match the verifier")
    if re.fullmatch(r"[0-9a-f]{40,64}", base_revision) is None:
        fail("trusted review base revision is missing or malformed")
    if submission["base_revision"] != base_revision:
        fail("review base revision does not match the repository")
    raw_findings = submission["findings"]
    if not isinstance(raw_findings, list) or len(raw_findings) > 100:
        fail("review findings must be a bounded list")
    findings = [
        normalize_finding(finding, rules["max_location_span_lines"]) for finding in raw_findings
    ]
    indexed = sorted(
        enumerate(findings),
        key=lambda item: (
            item[1]["path"],
            item[1]["start_line"],
            item[1]["line_count"],
            item[1]["category"],
            item[1]["severity"],
            item[1]["explanation"],
            item[0],
        ),
    )
    defects = adjudication["defects"]
    false_positives = adjudication["false_positives"]
    tolerance = rules["location_tolerance_lines"]
    matched: dict[str, int] = {}
    category_mismatches: list[str] = []
    severity_mismatches: list[str] = []
    known_false_positives: list[str] = []
    duplicates: list[int] = []
    novel: list[dict[str, object]] = []
    for index, finding in indexed:
        candidates: list[tuple[tuple[int, int, int, int], str, str, dict[str, object]]] = []
        for kind, targets in (("defect", defects), ("false_positive", false_positives)):
            for target in targets:
                rank = match_rank(finding, target, tolerance)
                if rank is not None:
                    candidates.append((rank, kind, str(target["id"]), target))
        if not candidates:
            novel.append({"submission_index": index, "finding": finding})
            continue
        _, kind, target_id, target = min(
            candidates, key=lambda candidate: (candidate[0], candidate[1], candidate[2])
        )
        if kind == "false_positive":
            known_false_positives.append(target_id)
            continue
        if target_id in matched:
            if finding_identity(finding) == finding_identity(findings[matched[target_id]]):
                duplicates.append(index)
            else:
                novel.append({"submission_index": index, "finding": finding})
            continue
        matched[target_id] = index
        if finding["category"] != target["category"]:
            category_mismatches.append(target_id)
        if finding["severity"] != target["severity"]:
            severity_mismatches.append(target_id)
    true_positives = len(matched)
    false_positive_count = len(known_false_positives) + len(duplicates)
    recall = true_positives / len(defects) if defects else 1.0
    denominator = true_positives + false_positive_count
    precision = true_positives / denominator if denominator else 1.0
    category_matches = true_positives - len(category_mismatches)
    severity_matches = true_positives - len(severity_mismatches)
    exact_classification_matches = true_positives - len(
        set(category_mismatches) | set(severity_mismatches)
    )
    category_calibration = category_matches / true_positives if true_positives else 0.0
    severity_calibration = severity_matches / true_positives if true_positives else 0.0
    exact_classification_calibration = (
        exact_classification_matches / true_positives if true_positives else 0.0
    )
    passed = recall >= rules["recall_threshold"] and precision >= rules["precision_threshold"]
    submission_sha256 = hashlib.sha256(raw).hexdigest()
    score = {
        "schema_version": "slopbench.review-score.v2",
        "task_digest": task_digest,
        "submission_sha256": submission_sha256,
        "true_positives": true_positives,
        "false_positives": false_positive_count,
        "duplicates": len(duplicates),
        "known_false_positives": len(known_false_positives),
        "novel_findings": len(novel),
        "recall": recall,
        "precision": precision,
        "category_calibration": category_calibration,
        "severity_calibration": severity_calibration,
        "exact_classification_calibration": exact_classification_calibration,
        "passed": passed,
        "matched_defect_ids": sorted(matched),
        "category_mismatch_defect_ids": sorted(category_mismatches),
        "severity_mismatch_defect_ids": sorted(severity_mismatches),
        "known_false_positive_ids": sorted(known_false_positives),
        "duplicate_submission_indices": sorted(duplicates),
    }
    queue = {
        "schema_version": "slopbench.review-novel.v1",
        "task_digest": task_digest,
        "submission_sha256": submission_sha256,
        "findings": sorted(novel, key=lambda item: int(item["submission_index"])),
    }
    print(json.dumps({"novel": queue, "score": score}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
