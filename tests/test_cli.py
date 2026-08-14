from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopbench import cli
from slopbench.contracts import FailureClassification
from slopbench.runner import RunError
from tests.helpers import result_bundle, task_payload, write_json


def test_validate_command_accepts_valid_document(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    write_json(path, task_payload())

    assert cli.main(["validate", "task", str(path)]) == 0


def test_validate_command_reports_contextual_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":"wrong"}\n')

    assert cli.main(["validate", "task", str(path)]) == 2
    assert f"invalid TaskContract at {path}" in capsys.readouterr().err


def test_task_seal_and_check_commands(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "solution").mkdir()
    (task_dir / "instruction.md").write_text("Do the work.\n")
    (task_dir / "solution" / "alternate.py").write_text("pass\n")
    (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
    write_json(task_dir / "slopbench-task.json", task_payload(sealed=False))

    assert cli.main(["task", "seal", str(task_dir)]) == 0
    assert cli.main(["task", "check", str(task_dir)]) == 0


def test_schema_export_writes_every_versioned_boundary(tmp_path: Path) -> None:
    output = tmp_path / "schemas"

    assert cli.main(["schema", "export", str(output)]) == 0

    assert {path.name for path in output.iterdir()} == {
        "slopbench-attestation-statement.schema.json",
        "slopbench-attestation.schema.json",
        "slopbench-bridge.schema.json",
        "slopbench-disclosure.schema.json",
        "slopbench-evaluation-result.schema.json",
        "slopbench-evaluation.schema.json",
        "slopbench-profile.schema.json",
        "slopbench-reference-verification.schema.json",
        "slopbench-report.schema.json",
        "slopbench-retirement.schema.json",
        "slopbench-review.schema.json",
        "slopbench-result.schema.json",
        "slopbench-run.schema.json",
        "slopbench-task.schema.json",
        "slopbench-task-set.schema.json",
        "slopbench-verification.schema.json",
    }
    task_schema = json.loads((output / "slopbench-task.schema.json").read_text())
    assert task_schema["title"] == "TaskContract"
    review_schema = json.loads((output / "slopbench-review.schema.json").read_text())
    finding = review_schema["$defs"]["ReviewFinding"]["properties"]
    assert finding["line_count"]["minimum"] == 1
    assert finding["line_count"]["maximum"] == 10
    assert finding["path"]["pattern"]


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        (FailureClassification.VALID_PASS, 0),
        (FailureClassification.VALID_AGENT_FAILURE, 1),
        (FailureClassification.INVALID_RUN, 2),
    ],
)
def test_run_command_maps_classification_to_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    classification: FailureClassification,
    expected: int,
) -> None:
    result = result_bundle(classification=classification.value)
    bundle = tmp_path / "bundle"

    def fake_execute(*args: object) -> tuple[object, Path]:
        return result, bundle

    monkeypatch.setattr(cli, "execute_run", fake_execute)

    code = cli.main(
        [
            "run",
            "--task",
            "task",
            "--manifest",
            "run.json",
            "--output",
            "output",
        ]
    )

    assert code == expected
    assert capsys.readouterr().out.strip() == str(bundle / "result.json")


def test_run_command_reports_run_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object) -> None:
        raise RunError("unsafe run")

    monkeypatch.setattr(cli, "execute_run", fail)

    code = cli.main(
        [
            "run",
            "--task",
            "task",
            "--manifest",
            "run.json",
            "--output",
            "output",
        ]
    )

    assert code == 2
    assert "slopbench: unsafe run" in capsys.readouterr().err


def test_release_commands_dispatch_to_contract_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_validate_task_set(path: Path, root: Path) -> tuple[object, str]:
        calls.append(("task-set", (path, root)))
        return f"task-set:{path}", "a" * 64

    def fake_load(path: Path, model_type: type[object]) -> object:
        return f"{model_type.__name__}:{path}"

    def fake_write(path: Path, model: object) -> None:
        calls.append(("write", (path, model)))

    monkeypatch.setattr(cli, "validate_task_set", fake_validate_task_set)
    monkeypatch.setattr(cli, "load_model", fake_load)
    monkeypatch.setattr(cli, "write_model", fake_write)
    monkeypatch.setattr(cli, "sha256_file", lambda path: "b" * 64)
    monkeypatch.setattr(cli, "compute_evaluation", lambda *args, **kwargs: "evaluation")
    monkeypatch.setattr(cli, "build_held_out_disclosure", lambda *args: "disclosure")
    monkeypatch.setattr(cli, "build_bridge_report", lambda *args: "bridge")
    monkeypatch.setattr(
        cli,
        "validate_retirement",
        lambda *args: calls.append(("retirement", args)),
    )
    monkeypatch.setattr(cli, "build_attestation_statement", lambda *args: "statement")
    monkeypatch.setattr(cli, "sign_reference_attestation", lambda *args: "attestation")
    monkeypatch.setattr(cli, "verify_reference_attestation", lambda *args: "official")

    assert cli.main(["task-set", "suite.json", "--root", str(tmp_path)]) == 0
    assert (
        cli.main(
            [
                "evaluate",
                "--manifest",
                "evaluation.json",
                "--task-set",
                "suite.json",
                "--profile",
                "profile.json",
                "--project-root",
                str(tmp_path),
                "--bundle-root",
                str(tmp_path),
                "--origin",
                "maintainer",
                "--output",
                "result.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "disclose",
                "--task-set",
                "suite.json",
                "--profile",
                "profile.json",
                "--result",
                "result.json",
                "--output",
                "public.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "bridge",
                "--before-task-set",
                "before.json",
                "--after-task-set",
                "after.json",
                "--before-result",
                "before-result.json",
                "--after-result",
                "after-result.json",
                "--output",
                "bridge.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "retirement",
                "--manifest",
                "retirement.json",
                "--bridge",
                "bridge.json",
                "--before-task-set",
                "before.json",
                "--after-task-set",
                "after.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "attestation",
                "statement",
                "--evaluation",
                "evaluation.json",
                "--result",
                "result.json",
                "--output",
                "statement.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "attestation",
                "sign",
                "--evaluation",
                "evaluation.json",
                "--result",
                "result.json",
                "--identity",
                "key",
                "--signer",
                "maintainer@uinaf.dev",
                "--output",
                "attestation.json",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "attestation",
                "verify",
                "--attestation",
                "attestation.json",
                "--allowed-signers",
                "allowed_signers",
                "--evaluation",
                "evaluation.json",
                "--result",
                "result.json",
                "--output",
                "verification.json",
            ]
        )
        == 0
    )
    assert any(name == "retirement" for name, _ in calls)
    assert [name for name, _ in calls].count("write") == 6
