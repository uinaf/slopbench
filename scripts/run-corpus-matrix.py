from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from slopbench.contracts import (
    FailureClassification,
    OutcomeStatus,
    ResultBundle,
    RunManifest,
)
from slopbench.hashing import load_model, validate_task, write_model
from slopbench.runner import execute_run

ROOT = Path(__file__).resolve().parents[1]


def output_path(value: str | None) -> Path:
    if value is not None:
        return Path(value).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "artifacts" / f"corpus-{stamp}"


def artifact_shas(result_path: Path, suffix: str) -> tuple[str, ...]:
    result = load_model(result_path, ResultBundle)
    matches = tuple(
        artifact.sha256 for artifact in result.artifacts if artifact.path.endswith(suffix)
    )
    if not matches:
        raise SystemExit(f"missing {suffix} in {result_path}")
    return matches


def run_expected(
    task_dir: Path,
    manifest_path: Path,
    output: Path,
    label: str,
    expected: FailureClassification,
) -> tuple[Path, ResultBundle]:
    result, bundle = execute_run(task_dir, manifest_path, output / label)
    if result.classification != expected:
        raise SystemExit(
            f"{task_dir.name}/{label}: expected {expected.value}, "
            f"got {result.classification.value}; see {bundle / 'result.json'}"
        )
    print(f"{task_dir.name}/{label}: {result.classification.value}")
    return bundle, result


def run_task(task_dir: Path, output: Path, temporary: Path) -> None:
    task, _, _ = validate_task(task_dir)
    runs_dir = ROOT / "runs" / task_dir.relative_to(ROOT / "tasks")
    oracle_a, _ = run_expected(
        task_dir,
        runs_dir / "oracle.json",
        output,
        f"{task_dir.name}-oracle-a",
        FailureClassification.VALID_PASS,
    )
    oracle_b, _ = run_expected(
        task_dir,
        runs_dir / "oracle.json",
        output,
        f"{task_dir.name}-oracle-b",
        FailureClassification.VALID_PASS,
    )
    for suffix in (
        "slopbench-report.json",
        "slopbench-verification.json",
        "reward.json",
    ):
        first = artifact_shas(oracle_a / "result.json", suffix)
        second = artifact_shas(oracle_b / "result.json", suffix)
        if first != second:
            raise SystemExit(f"{task_dir.name}: repeated oracle mismatch for {suffix}")
    run_expected(
        task_dir,
        runs_dir / "alternate.json",
        output,
        f"{task_dir.name}-alternate",
        FailureClassification.VALID_PASS,
    )
    run_expected(
        task_dir,
        runs_dir / "invalid.json",
        output,
        f"{task_dir.name}-invalid",
        FailureClassification.VALID_AGENT_FAILURE,
    )
    run_expected(
        task_dir,
        runs_dir / "nop.json",
        output,
        f"{task_dir.name}-nop",
        FailureClassification.VALID_AGENT_FAILURE,
    )

    attack_template = load_model(runs_dir / "attack.json", RunManifest)
    for fixture in task.attack_fixtures:
        run_id = f"{task_dir.name}-attack-{fixture.id}"
        payload = attack_template.model_dump(mode="json")
        payload["attack_fixture_id"] = fixture.id
        payload["run_id"] = run_id
        payload["trial"] = {**payload["trial"], "id": run_id}
        manifest_path = temporary / f"{task_dir.name}-{fixture.id}.json"
        write_model(manifest_path, RunManifest.model_validate(payload))
        bundle, result = run_expected(
            task_dir,
            manifest_path,
            output,
            run_id,
            FailureClassification(fixture.expected.classification),
        )
        failed_gates = {
            outcome.gate for outcome in result.outcomes if outcome.status == OutcomeStatus.FAILED
        }
        if failed_gates != set(fixture.expected.failed_gates):
            raise SystemExit(
                f"{task_dir.name}/{fixture.id}: expected failed gates "
                f"{sorted(gate.value for gate in fixture.expected.failed_gates)}, got "
                f"{sorted(gate.value for gate in failed_gates)}; see {bundle / 'result.json'}"
            )
        if result.retry.eligible:
            raise SystemExit(f"{task_dir.name}/{fixture.id}: attack was marked retryable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append", type=Path, dest="tasks")
    parser.add_argument("output", nargs="?")
    args = parser.parse_args()
    tasks = args.tasks or sorted(
        contract.parent for contract in (ROOT / "tasks").glob("*/*/slopbench-task.json")
    )
    destination = output_path(args.output)
    if destination.exists():
        raise SystemExit(f"output already exists: {destination}")
    with tempfile.TemporaryDirectory(prefix="slopbench-corpus-") as temporary:
        for task_dir in tasks:
            run_task(task_dir.resolve(), destination, Path(temporary))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
