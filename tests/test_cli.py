from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopbench import cli
from slopbench.contracts import FailureClassification
from slopbench.release import TaskSetVisibility
from slopbench.runner import RunError
from tests.helpers import result_bundle, task_payload, write_json
from tests.test_release import direct_result, one_task_set, profile


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
        "slopbench-coverage.schema.json",
        "slopbench-disclosure.schema.json",
        "slopbench-evaluation-result.schema.json",
        "slopbench-evaluation.schema.json",
        "slopbench-profile.schema.json",
        "slopbench-reference-configuration.schema.json",
        "slopbench-reference-verification.schema.json",
        "slopbench-regression.schema.json",
        "slopbench-release-evidence.schema.json",
        "slopbench-release-readiness.schema.json",
        "slopbench-report.schema.json",
        "slopbench-retirement.schema.json",
        "slopbench-review.schema.json",
        "slopbench-review-score.schema.json",
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


@pytest.mark.parametrize(
    ("argv", "first_input"),
    [
        (["task-set", "missing-task-set.json"], "missing-task-set.json"),
        (
            [
                "evaluate",
                "--manifest",
                "missing-evaluation.json",
                "--task-set",
                "task-set.json",
                "--profile",
                "profile.json",
                "--output",
                "result.json",
            ],
            "missing-evaluation.json",
        ),
        (
            [
                "disclose",
                "--task-set",
                "missing-disclosure-task-set.json",
                "--profile",
                "profile.json",
                "--result",
                "result.json",
                "--output",
                "disclosure.json",
            ],
            "missing-disclosure-task-set.json",
        ),
        (
            [
                "bridge",
                "--before-task-set",
                "missing-before-task-set.json",
                "--after-task-set",
                "after-task-set.json",
                "--before-result",
                "before-result.json",
                "--after-result",
                "after-result.json",
                "--output",
                "bridge.json",
            ],
            "missing-before-task-set.json",
        ),
        (
            [
                "retirement",
                "--manifest",
                "missing-retirement.json",
                "--bridge",
                "bridge.json",
                "--before-task-set",
                "before-task-set.json",
                "--after-task-set",
                "after-task-set.json",
                "--before-result",
                "before-result.json",
                "--after-result",
                "after-result.json",
            ],
            "missing-retirement.json",
        ),
        (
            [
                "attestation",
                "statement",
                "--evaluation",
                "missing-statement-evaluation.json",
                "--result",
                "result.json",
                "--output",
                "statement.json",
            ],
            "missing-statement-evaluation.json",
        ),
        (
            [
                "attestation",
                "sign",
                "--evaluation",
                "missing-sign-evaluation.json",
                "--result",
                "result.json",
                "--identity",
                "key",
                "--signer",
                "maintainer@example.test",
                "--output",
                "attestation.json",
            ],
            "missing-sign-evaluation.json",
        ),
        (
            [
                "attestation",
                "verify",
                "--attestation",
                "missing-attestation.json",
                "--allowed-signers",
                "allowed-signers",
                "--evaluation",
                "evaluation.json",
                "--result",
                "result.json",
                "--output",
                "verification.json",
            ],
            "missing-attestation.json",
        ),
        (
            [
                "release",
                "audit",
                "--manifest",
                "missing-release-evidence.json",
                "--output",
                "readiness.json",
            ],
            "missing-release-evidence.json",
        ),
        (
            [
                "regression",
                "--before",
                "missing-before-result.json",
                "--after",
                "after-result.json",
                "--output",
                "regression.json",
            ],
            "missing-before-result.json",
        ),
    ],
)
def test_contract_commands_report_their_missing_first_input(
    argv: list[str],
    first_input: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(argv) == 2
    assert first_input in capsys.readouterr().err


def test_regression_command_writes_the_comparison_report(tmp_path: Path) -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    output_path = tmp_path / "regression.json"
    write_json(before_path, direct_result(task_set, scoring_profile).model_dump(mode="json"))
    write_json(
        after_path,
        direct_result(task_set, scoring_profile, fail_requested=True).model_dump(mode="json"),
    )

    assert (
        cli.main(
            [
                "regression",
                "--before",
                str(before_path),
                "--after",
                str(after_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    report = json.loads(output_path.read_text())
    assert report["schema_version"] == "slopbench.regression.v1"
    assert report["flags"] == [
        {
            "critical_gate": None,
            "failed_pair_indices": [1, 2, 3, 4, 5],
            "kind": "reliability",
            "task_digest": task_set.tasks[0].task_digest,
            "task_id": task_set.tasks[0].task_id,
        }
    ]


def test_disclose_command_writes_the_held_out_summary(tmp_path: Path) -> None:
    task_set = one_task_set(visibility=TaskSetVisibility.HELD_OUT_ACTIVE)
    scoring_profile = profile()
    task_set_path = tmp_path / "task-set.json"
    profile_path = tmp_path / "profile.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "disclosure.json"
    write_json(task_set_path, task_set.model_dump(mode="json"))
    write_json(profile_path, scoring_profile.model_dump(mode="json"))
    write_json(
        result_path,
        direct_result(task_set, scoring_profile).model_dump(mode="json"),
    )

    assert (
        cli.main(
            [
                "disclose",
                "--task-set",
                str(task_set_path),
                "--profile",
                str(profile_path),
                "--result",
                str(result_path),
                "--project-root",
                str(Path(__file__).resolve().parents[1]),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    disclosure = json.loads(output_path.read_text())
    assert disclosure["schema_version"] == "slopbench.disclosure.v1"
    assert disclosure["task_set"]["id"] == task_set.task_set_id
    assert disclosure["category_counts"] == {"diagnosis_repair": 1}
    assert disclosure["aggregate"]["trial_count"] == 5
