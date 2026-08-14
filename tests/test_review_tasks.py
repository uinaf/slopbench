from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from harbor.models.task.task import Task as HarborTask

from slopbench import runner
from slopbench.contracts import (
    CapabilityCategory,
    ReviewSubmission,
    RunManifest,
    TaskKind,
)
from slopbench.hashing import load_model, validate_task

ROOT = Path(__file__).parents[1]
REVIEW_TASKS = [
    ROOT / "tasks" / "review" / "archive-extractor",
    ROOT / "tasks" / "review" / "webhook-dispatch",
]


def fixture_files(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def initialize_repo(task_dir: Path, destination: Path) -> Path:
    repo = destination / task_dir.name
    shutil.copytree(task_dir / "environment" / "repo", repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "slopbench"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "slopbench@invalid"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    match = re.search(r'git commit -m "([^"]+)"', dockerfile)
    assert match is not None
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-qm", match.group(1)],
        cwd=repo,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        },
        check=True,
    )
    return repo


def write_submission(
    task_dir: Path,
    repo: Path,
    task_digest: str,
    findings: list[dict[str, object]],
) -> Path:
    task, _, _ = validate_task(task_dir)
    submission = {
        "schema_version": "slopbench.review.v1",
        "task_id": task.task_id,
        "task_digest": task_digest,
        "base_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "findings": findings,
    }
    path = repo / "slopbench-review.json"
    path.write_text(json.dumps(submission, indent=2, sort_keys=True) + "\n")
    return path


def score(task_dir: Path, repo: Path, task_digest: str) -> tuple[int, dict[str, object] | None]:
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    completed = subprocess.run(
        ["python", str(task_dir / "tests" / "score_review.py")],
        cwd=repo,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(repo.parent / "pycache"),
            "SLOPBENCH_REVIEW_ADJUDICATION": str(task_dir / "tests" / "adjudication.json"),
            "SLOPBENCH_REVIEW_BASE_REVISION": base_revision,
            "SLOPBENCH_REVIEW_ROOT": str(repo),
            "SLOPBENCH_TASK_DIGEST": task_digest,
        },
        check=False,
        capture_output=True,
        text=True,
    )
    envelope = json.loads(completed.stdout) if completed.stdout else None
    return completed.returncode, envelope


def variant_findings(task_dir: Path, variant: str) -> list[dict[str, object]]:
    payload = json.loads((task_dir / "solution" / f"{variant}.json").read_text())
    return payload["findings"]


def test_review_task_shape_and_adjudication_contracts() -> None:
    assert len(REVIEW_TASKS) == 2
    for task_dir in REVIEW_TASKS:
        task, _, _ = validate_task(task_dir)
        assert HarborTask.is_valid_dir(task_dir)
        assert task.kind == TaskKind.REVIEW
        assert task.design.category == CapabilityCategory.CODE_REVIEW
        assert task.capabilities.repository == "read-only"
        assert task.license.spdx == "MIT"
        assert task.provenance.origin == "slopbench-authored"
        assert task.design.admission.status == "candidate"
        assert task.design.admission.evidence.complete
        assert len(task.design.traps) == 2
        assert len(task.design.valid_alternatives) == 1
        assert task.review is not None
        adjudication = json.loads((task_dir / task.review.adjudication_path).read_text())
        assert adjudication["task_id"] == task.task_id
        assert adjudication["rules"] == {
            "submission_path": task.review.submission_path,
            "location_tolerance_lines": task.review.location_tolerance_lines,
            "max_location_span_lines": task.review.max_location_span_lines,
            "recall_threshold": task.review.recall_threshold,
            "precision_threshold": task.review.precision_threshold,
        }
        assert len(adjudication["defects"]) == 3
        assert len(adjudication["false_positives"]) == 2
        assert "without modifying tracked files" in (task_dir / "instruction.md").read_text()


@pytest.mark.parametrize("task_dir", REVIEW_TASKS, ids=lambda path: path.name)
def test_review_task_baseline_revision_and_manifests_are_bound(
    task_dir: Path, tmp_path: Path
) -> None:
    task, contract_sha, task_digest = validate_task(task_dir)
    assert fixture_files(task_dir / "environment" / "repo") == fixture_files(
        task_dir / "tests" / "baseline"
    )
    repo = initialize_repo(task_dir, tmp_path)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert revision == task.environment.base_revision
    runs_dir = ROOT / "runs" / task_dir.relative_to(ROOT / "tasks")
    assert {path.name for path in runs_dir.glob("*.json")} == {
        "alternate.json",
        "attack.json",
        "invalid.json",
        "nop.json",
        "oracle.json",
    }
    for manifest_path in runs_dir.glob("*.json"):
        runner._validate_run_binding(
            load_model(manifest_path, RunManifest),
            task,
            contract_sha,
            task_digest,
            task_dir,
        )


@pytest.mark.parametrize("task_dir", REVIEW_TASKS, ids=lambda path: path.name)
@pytest.mark.parametrize(
    ("variant", "expected_exit", "recall", "precision"),
    [
        ("oracle", 0, 1.0, 1.0),
        ("alternate", 0, 1.0, 1.0),
        ("invalid", 1, 0.0, 1.0),
        ("known_false_positive", 1, 1.0, 0.75),
    ],
)
def test_review_scorer_has_stable_recall_and_precision(
    task_dir: Path,
    variant: str,
    expected_exit: int,
    recall: float,
    precision: float,
    tmp_path: Path,
) -> None:
    _, _, task_digest = validate_task(task_dir)
    repo = initialize_repo(task_dir, tmp_path)
    submission_path = write_submission(
        task_dir, repo, task_digest, variant_findings(task_dir, variant)
    )
    load_model(submission_path, ReviewSubmission)

    first_exit, first = score(task_dir, repo, task_digest)
    second_exit, second = score(task_dir, repo, task_digest)

    assert first_exit == second_exit == expected_exit
    assert first == second
    assert first is not None
    assert first["score"]["recall"] == recall
    assert first["score"]["precision"] == precision
    assert first["score"]["passed"] == (expected_exit == 0)


@pytest.mark.parametrize("task_dir", REVIEW_TASKS, ids=lambda path: path.name)
def test_novel_findings_are_queued_without_changing_official_score(
    task_dir: Path, tmp_path: Path
) -> None:
    _, _, task_digest = validate_task(task_dir)
    repo = initialize_repo(task_dir, tmp_path)
    findings = variant_findings(task_dir, "oracle")
    findings.append(
        {
            "path": "README.md",
            "start_line": 1,
            "end_line": 1,
            "category": "correctness",
            "severity": "low",
            "explanation": "A previously unadjudicated concern for the human review queue.",
        }
    )
    write_submission(task_dir, repo, task_digest, findings)

    exit_code, envelope = score(task_dir, repo, task_digest)

    assert exit_code == 0
    assert envelope is not None
    assert envelope["score"]["recall"] == 1.0
    assert envelope["score"]["precision"] == 1.0
    assert envelope["score"]["novel_findings"] == 1
    assert len(envelope["novel"]["findings"]) == 1


@pytest.mark.parametrize("task_dir", REVIEW_TASKS, ids=lambda path: path.name)
def test_duplicate_findings_are_false_positives(task_dir: Path, tmp_path: Path) -> None:
    _, _, task_digest = validate_task(task_dir)
    repo = initialize_repo(task_dir, tmp_path)
    findings = variant_findings(task_dir, "oracle")
    duplicate = {**findings[0], "explanation": "The same defect reported a second time."}
    findings.append(duplicate)
    write_submission(task_dir, repo, task_digest, findings)

    exit_code, envelope = score(task_dir, repo, task_digest)

    assert exit_code == 1
    assert envelope is not None
    assert envelope["score"]["duplicates"] == 1
    assert envelope["score"]["precision"] == 0.75
