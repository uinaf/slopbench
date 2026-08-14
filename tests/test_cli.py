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
        "slopbench-report.schema.json",
        "slopbench-result.schema.json",
        "slopbench-run.schema.json",
        "slopbench-task.schema.json",
        "slopbench-verification.schema.json",
    }
    task_schema = json.loads((output / "slopbench-task.schema.json").read_text())
    assert task_schema["title"] == "TaskContract"


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
