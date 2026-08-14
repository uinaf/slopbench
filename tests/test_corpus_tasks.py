from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from harbor.models.task.task import Task as HarborTask

from slopbench import runner
from slopbench.contracts import (
    AgentReport,
    CapabilityCategory,
    ClaimStatus,
    PhaseMode,
    RunManifest,
)
from slopbench.hashing import compute_worktree_revision, load_model, validate_task

ROOT = Path(__file__).parents[1]
CORPUS_TASKS = [
    ROOT / "tasks" / "diagnosis" / "query-cache-key",
    ROOT / "tasks" / "diagnosis" / "lease-expiry",
    ROOT / "tasks" / "domain" / "pricing-adjustments",
    ROOT / "tasks" / "domain" / "fulfillment-plan",
    ROOT / "tasks" / "feature" / "idempotency-registry",
    ROOT / "tasks" / "feature" / "event-pagination",
    ROOT / "tasks" / "restraint" / "header-lookup",
    ROOT / "tasks" / "restraint" / "config-overrides",
    ROOT / "tasks" / "state" / "sync-command",
    ROOT / "tasks" / "state" / "watch-subscription",
]
FORBIDDEN_PROMPT_TERMS = ("sicp", "domain-driven design", "elm architecture", "decade essay")


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


def run_command(
    command: str, repo: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        shlex.split(command),
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_corpus_shape_and_design_records() -> None:
    tasks = [validate_task(task_dir)[0] for task_dir in CORPUS_TASKS]

    assert len(tasks) == 10
    assert [task.phase_mode for task in tasks].count(PhaseMode.SEQUENTIAL) == 4
    assert [task.design.category for task in tasks].count(CapabilityCategory.DIAGNOSIS_REPAIR) == 2
    assert [task.design.category for task in tasks].count(CapabilityCategory.FEATURE) == 2
    assert [task.design.category for task in tasks].count(CapabilityCategory.RESTRAINT) == 2
    assert [task.design.category for task in tasks].count(
        CapabilityCategory.COMPOSITION_DOMAIN_EVOLUTION
    ) == 2
    assert [task.design.category for task in tasks].count(CapabilityCategory.STATE_EFFECTS) == 2
    for task_dir, task in zip(CORPUS_TASKS, tasks, strict=True):
        assert HarborTask.is_valid_dir(task_dir)
        assert task.design.owner == "uinaf"
        assert task.design.admission.status == "candidate"
        assert task.design.admission.evidence.complete
        assert task.license.spdx == "MIT"
        assert task.provenance.origin == "slopbench-authored"
        assert task.provenance.source_revision == task.environment.base_revision
        assert len(task.design.traps) == 2
        assert len(task.design.valid_alternatives) == 1
        prompt = "\n".join(
            (task_dir / phase.instruction_path).read_text().lower() for phase in task.phases
        )
        assert not any(term in prompt for term in FORBIDDEN_PROMPT_TERMS)


@pytest.mark.parametrize("task_dir", CORPUS_TASKS, ids=lambda path: path.name)
def test_corpus_task_baseline_and_manifests_are_bound(task_dir: Path) -> None:
    task, contract_sha, task_digest = validate_task(task_dir)
    assert fixture_files(task_dir / "environment" / "repo") == fixture_files(
        task_dir / "tests" / "baseline"
    )
    assert (task_dir / "environment" / "repo" / "tools" / "revision.py").read_bytes() == (
        task_dir / "tests" / "revision.py"
    ).read_bytes()
    runs_dir = ROOT / "runs" / task_dir.relative_to(ROOT / "tasks")
    assert {path.name for path in runs_dir.glob("*.json")} == {
        "alternate.json",
        "attack.json",
        "invalid.json",
        "nop.json",
        "oracle.json",
    }
    for manifest_path in sorted(runs_dir.glob("*.json")):
        manifest = load_model(manifest_path, RunManifest)
        runner._validate_run_binding(
            manifest,
            task,
            contract_sha,
            task_digest,
            task_dir,
        )
        if task.phase_mode == PhaseMode.SEQUENTIAL and manifest_path.stem in {
            "attack",
            "invalid",
        }:
            assert manifest.agent.environment["SLOPBENCH_TARGET_PHASE"] == task.phases[-1].name


@pytest.mark.parametrize("task_dir", CORPUS_TASKS, ids=lambda path: path.name)
def test_corpus_base_revision_matches_deterministic_fixture_commit(
    task_dir: Path, tmp_path: Path
) -> None:
    repo = initialize_repo(task_dir, tmp_path)
    task, _, _ = validate_task(task_dir)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert revision == task.environment.base_revision


def test_lease_expiry_requested_contract_rejects_unfixed_baseline(tmp_path: Path) -> None:
    task_dir = ROOT / "tasks" / "diagnosis" / "lease-expiry"
    repo = initialize_repo(task_dir, tmp_path)
    hidden = subprocess.run(
        ["python", str(task_dir / "tests" / "hidden_test.py")],
        cwd=repo,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
            "PYTHONPATH": str(repo),
            "SLOPBENCH_PHASE": "implement",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert hidden.returncode != 0
    assert "test_new_owner_can_acquire_at_exact_expiry" in hidden.stderr


@pytest.mark.parametrize("task_dir", CORPUS_TASKS, ids=lambda path: path.name)
@pytest.mark.parametrize("variant", ["oracle", "alternate", "invalid"])
def test_corpus_solutions_have_expected_behavior(
    task_dir: Path,
    variant: str,
    tmp_path: Path,
) -> None:
    task, _, task_digest = validate_task(task_dir)
    repo = initialize_repo(task_dir, tmp_path)
    meta = json.loads((repo / "tools" / "task_meta.json").read_text())
    allowed_paths = meta["allowed_paths"]
    assert len(allowed_paths) == 1
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "PYTHONPATH": str(repo),
    }
    for phase in task.phases:
        solution_dir = (
            task_dir / "solution"
            if task.phase_mode == PhaseMode.SINGLE
            else task_dir / "steps" / phase.name / "solution"
        )
        shutil.copyfile(solution_dir / f"{variant}.py", repo / allowed_paths[0])
        public = run_command(meta["public_command"], repo, environment)
        assert public.returncode == 0, public.stdout + public.stderr
        hidden = subprocess.run(
            ["python", str(task_dir / "tests" / "hidden_test.py")],
            cwd=repo,
            env={**environment, "SLOPBENCH_PHASE": phase.name},
            check=False,
            capture_output=True,
            text=True,
        )
        expected_hidden = 1 if variant == "invalid" else 0
        assert (hidden.returncode != 0) == bool(expected_hidden), hidden.stdout + hidden.stderr

    if variant == "invalid":
        return
    report = subprocess.run(
        ["python", "tools/write_slopbench_report.py"],
        cwd=repo,
        env={**environment, "SLOPBENCH_TASK_DIGEST": task_digest},
        check=False,
        capture_output=True,
        text=True,
    )
    assert report.returncode == 0, report.stdout + report.stderr
    receipt = load_model(repo / "slopbench-report.json", AgentReport)
    assert receipt.final_revision == compute_worktree_revision(repo)
    assert {claim.gate for claim in receipt.claims} == set(task.applicable_gates)
    authority = next(claim for claim in receipt.claims if claim.gate.value == "authority")
    assert authority.status == ClaimStatus.PASSED
