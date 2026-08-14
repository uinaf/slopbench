from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from slopbench.contracts import GateName
from slopbench.coverage import (
    BenchmarkCoverageManifest,
    CoverageDisposition,
    CoverageEvidence,
    validate_coverage,
)
from slopbench.hashing import ContractError, load_model, write_model
from slopbench.release import TaskSetManifest, task_set_binding

ROOT = Path(__file__).parents[1]
COVERAGE_PATH = ROOT / "coverage" / "slopbench-swe-v1-dev-agent-rules.json"
TASK_SET_PATH = ROOT / "datasets" / "slopbench-swe-v1-dev.json"


def checked_in_contracts() -> tuple[BenchmarkCoverageManifest, TaskSetManifest]:
    task_set = load_model(TASK_SET_PATH, TaskSetManifest)
    return validate_coverage(COVERAGE_PATH, task_set), task_set


def test_checked_in_coverage_is_bound_and_explicit_about_gaps() -> None:
    coverage, task_set = checked_in_contracts()

    assert BenchmarkCoverageManifest.model_fields["schema_version"].is_required()
    assert coverage.task_set == task_set_binding(task_set)
    assert coverage.coverage_id == "slopbench-swe-v1-dev-agent-rules"
    assert Counter(rule.disposition for rule in coverage.rules) == {
        CoverageDisposition.MEASURED: 8,
        CoverageDisposition.PARTIAL: 5,
        CoverageDisposition.OUT_OF_SCOPE: 10,
    }
    assert all(
        rule.limitation is None
        for rule in coverage.rules
        if rule.disposition == CoverageDisposition.MEASURED
    )
    assert all(
        rule.limitation is not None
        for rule in coverage.rules
        if rule.disposition != CoverageDisposition.MEASURED
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate-rule", "coverage rule IDs must be unique"),
        ("duplicate-evidence-task", "coverage evidence task IDs must be unique"),
        ("duplicate-evidence-gate", "coverage evidence gates must be unique"),
        ("duplicate-rule-task", "coverage rule task IDs must be unique"),
        ("measured-without-evidence", "measured coverage requires evidence"),
        ("partial-without-limitation", "partial coverage requires evidence and a limitation"),
        ("out-of-scope-with-evidence", "out-of-scope coverage requires only a limitation"),
    ],
)
def test_coverage_contract_rejects_ambiguous_dispositions(
    mutation: str,
    message: str,
) -> None:
    coverage, _ = checked_in_contracts()
    payload = coverage.model_dump(mode="json")
    if mutation == "duplicate-rule":
        payload["rules"].append(payload["rules"][0])
    elif mutation == "duplicate-evidence-task":
        evidence = payload["rules"][0]["evidence"][0]
        evidence["task_ids"].append(evidence["task_ids"][0])
    elif mutation == "duplicate-evidence-gate":
        evidence = payload["rules"][0]["evidence"][0]
        evidence["gates"].append(evidence["gates"][0])
    elif mutation == "duplicate-rule-task":
        evidence = payload["rules"][0]["evidence"][0]
        payload["rules"][0]["evidence"].append(
            {"task_ids": [evidence["task_ids"][0]], "gates": evidence["gates"]}
        )
    elif mutation == "measured-without-evidence":
        measured = next(rule for rule in payload["rules"] if rule["disposition"] == "measured")
        measured["evidence"] = []
    elif mutation == "partial-without-limitation":
        partial = next(rule for rule in payload["rules"] if rule["disposition"] == "partial")
        partial["limitation"] = None
    else:
        outside = next(rule for rule in payload["rules"] if rule["disposition"] == "out_of_scope")
        outside["evidence"] = payload["rules"][0]["evidence"]

    with pytest.raises(ValidationError, match=message):
        BenchmarkCoverageManifest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("task_id", "gate", "message"),
    [
        ("slopbench/unknown/task", GateName.AUTHORITY, "references unknown task"),
        (
            "slopbench/review/archive-extractor",
            GateName.BUILD_AND_TYPES,
            "maps non-applicable gates",
        ),
    ],
)
def test_coverage_validation_rejects_unknown_or_non_applicable_evidence(
    task_id: str,
    gate: GateName,
    message: str,
    tmp_path: Path,
) -> None:
    coverage, task_set = checked_in_contracts()
    evidence = CoverageEvidence(task_ids=[task_id], gates=[gate])
    rule = coverage.rules[0].model_copy(update={"evidence": [evidence]})
    changed = coverage.model_copy(update={"rules": [rule, *coverage.rules[1:]]})
    path = tmp_path / "coverage.json"
    write_model(path, changed)

    with pytest.raises(ContractError, match=message):
        validate_coverage(path, task_set)


def test_coverage_validation_rejects_task_set_drift(tmp_path: Path) -> None:
    coverage, task_set = checked_in_contracts()
    changed = coverage.model_copy(
        update={"task_set": coverage.task_set.model_copy(update={"sha256": "a" * 64})}
    )
    path = tmp_path / "coverage.json"
    write_model(path, changed)

    with pytest.raises(ContractError, match="task-set binding mismatch"):
        validate_coverage(path, task_set)
