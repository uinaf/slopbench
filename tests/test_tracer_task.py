from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from harbor.models.task.task import Task

from slopbench import runner
from slopbench.contracts import AgentReport, RunManifest
from slopbench.hashing import (
    compute_worktree_revision,
    load_model,
    validate_task,
)

ROOT = Path(__file__).parents[1]
TASK_DIR = ROOT / "tasks" / "tracer"


def test_tracer_is_a_valid_sealed_harbor_task() -> None:
    task, _, _ = validate_task(TASK_DIR)

    assert Task.is_valid_dir(TASK_DIR)
    assert task.task_id == "slopbench/tracer/event-summary"
    assert task.version == "1.0.0"
    assert task.phase_mode.value == "single"
    assert task.environment.verifier_isolation == "separate"
    assert len(task.immutable_inputs) == 34


def test_tracer_manifests_are_bound_to_the_sealed_task() -> None:
    task, contract_sha, task_digest = validate_task(TASK_DIR)

    for path in sorted((ROOT / "runs" / "tracer").glob("*.json")):
        manifest = load_model(path, RunManifest)
        runner._validate_run_binding(
            manifest,
            task,
            contract_sha,
            task_digest,
            TASK_DIR,
        )
        config = runner._harbor_config(
            manifest,
            task,
            TASK_DIR,
            ROOT / "artifacts" / "test-config",
            task_digest,
        )
        assert len(config.artifacts) == 1
        artifact = config.artifacts[0]
        assert not isinstance(artifact, str)
        assert artifact.source == "/app"
        assert artifact.exclude == [".git"]


def test_trusted_and_fixture_revision_algorithms_are_identical() -> None:
    fixture = TASK_DIR / "environment" / "repo" / "tools" / "revision.py"
    verifier = TASK_DIR / "tests" / "revision.py"

    assert fixture.read_bytes() == verifier.read_bytes()


def test_separate_verifier_baseline_matches_agent_fixture() -> None:
    fixture = TASK_DIR / "environment" / "repo"
    baseline = TASK_DIR / "tests" / "baseline"
    fixture_files = {
        path.relative_to(fixture): path.read_bytes()
        for path in fixture.rglob("*")
        if path.is_file()
    }
    baseline_files = {
        path.relative_to(baseline): path.read_bytes()
        for path in baseline.rglob("*")
        if path.is_file()
    }

    assert baseline_files == fixture_files


def initialize_fixture(tmp_path: Path, variant: str) -> Path:
    fixture = tmp_path / variant
    shutil.copytree(TASK_DIR / "environment" / "repo", fixture)
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    subprocess.run(["git", "config", "user.name", "SlopBench"], cwd=fixture, check=True)
    subprocess.run(["git", "config", "user.email", "slopbench@invalid"], cwd=fixture, check=True)
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=fixture, check=True)
    shutil.copyfile(
        TASK_DIR / "solution" / f"implementation_{variant}.py",
        fixture / "src" / "eventlog.py",
    )
    return fixture


@pytest.mark.parametrize(
    ("variant", "expected"),
    [("oracle", 0), ("alternate", 0), ("invalid", 1)],
)
def test_tracer_variants_have_expected_public_and_hidden_results(
    tmp_path: Path, variant: str, expected: int
) -> None:
    fixture = initialize_fixture(tmp_path, variant)
    environment = {**os.environ, "PYTHONPATH": str(fixture)}
    public = subprocess.run(
        ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=fixture,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    hidden = subprocess.run(
        ["python", str(TASK_DIR / "tests" / "check_requested.py")],
        cwd=fixture,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert public.returncode == expected, public.stdout + public.stderr
    assert hidden.returncode == expected, hidden.stdout + hidden.stderr


@pytest.mark.parametrize("variant", ["oracle", "alternate", "invalid"])
def test_tracer_variants_emit_valid_revision_bound_receipts(tmp_path: Path, variant: str) -> None:
    fixture = initialize_fixture(tmp_path, variant)
    completed = subprocess.run(
        ["python", "tools/write_slopbench_report.py"],
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = load_model(fixture / "slopbench-report.json", AgentReport)
    assert report.final_revision == compute_worktree_revision(fixture)


def test_valid_implementations_are_materially_different() -> None:
    oracle = (TASK_DIR / "solution" / "implementation_oracle.py").read_bytes()
    alternate = (TASK_DIR / "solution" / "implementation_alternate.py").read_bytes()

    assert oracle != alternate
    assert json.loads((TASK_DIR / "slopbench-task.json").read_text())["immutable_inputs"]


def test_receipt_helper_marks_untracked_authority_violation(tmp_path: Path) -> None:
    fixture = initialize_fixture(tmp_path, "oracle")
    (fixture / "outside.txt").write_text("outside authority\n")

    completed = subprocess.run(
        ["python", "tools/write_slopbench_report.py"],
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = load_model(fixture / "slopbench-report.json", AgentReport)
    authority = next(claim for claim in report.claims if claim.gate.value == "authority")
    assert authority.status.value == "failed"


def test_receipt_helper_records_deleted_tracked_file(tmp_path: Path) -> None:
    fixture = initialize_fixture(tmp_path, "oracle")
    (fixture / "src" / "eventlog.py").unlink()

    completed = subprocess.run(
        ["python", "tools/write_slopbench_report.py"],
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = load_model(fixture / "slopbench-report.json", AgentReport)
    assert report.final_revision == compute_worktree_revision(fixture)
