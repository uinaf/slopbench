from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from slopbench import runner
from slopbench.contracts import (
    AgentReport,
    FailureClassification,
    GateName,
    OutcomeStatus,
    RunManifest,
    TaskContract,
    VerificationEvidence,
)
from slopbench.hashing import ContractError, seal_task, sha256_file, validate_task, write_model
from tests.helpers import (
    SHA_A,
    SHA_B,
    agent_report,
    parse_json,
    report_payload,
    run_manifest,
    run_payload,
    task_contract,
    task_payload,
    verification_evidence,
    verification_payload,
    write_json,
)


def test_process_environment_is_allowlisted_and_resolves_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = run_payload()
    payload["agent"]["credential_env"] = ["CURSOR_API_KEY"]
    manifest = parse_json(RunManifest, payload)
    monkeypatch.setenv("CURSOR_API_KEY", "scoped")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-secret")
    monkeypatch.setenv("UNRELATED", "ambient")

    environment = runner._process_environment(manifest)

    assert environment["CURSOR_API_KEY"] == "scoped"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "UNRELATED" not in environment
    assert environment["NO_COLOR"] == "1"
    assert environment["PYTHONUNBUFFERED"] == "1"


def test_process_environment_reports_missing_credentials() -> None:
    payload = run_payload()
    payload["agent"]["credential_env"] = ["CURSOR_API_KEY"]
    manifest = parse_json(RunManifest, payload)
    os.environ.pop("CURSOR_API_KEY", None)

    with pytest.raises(runner.RunError, match="CURSOR_API_KEY"):
        runner._process_environment(manifest)


def test_default_process_runner_captures_stdout_and_stderr(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"

    code = runner._default_process_runner(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        tmp_path,
        {"PATH": os.environ["PATH"]},
        stdout,
        stderr,
    )

    assert code == 0
    assert stdout.read_text() == "out\n"
    assert stderr.read_text() == "err\n"


def test_harbor_executable_must_neighbor_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner.sys, "executable", str(tmp_path / "python"))

    with pytest.raises(runner.RunError, match="pinned Harbor executable is missing"):
        runner._harbor_executable()


def test_image_validation_requires_digests_and_declared_pins(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    environment = task_dir / "environment"
    environment.mkdir(parents=True)
    dockerfile = environment / "Dockerfile"
    dockerfile.write_text("FROM python:3.13\n")
    runtime = run_manifest().runtime

    with pytest.raises(ContractError, match="un-pinned base image"):
        runner._validate_images(task_dir, runtime)

    reference = f"python:3.13@sha256:{'1' * 64}"
    dockerfile.write_text(f"FROM --platform=linux/amd64 {reference}\n")
    with pytest.raises(ContractError, match="missing image pins"):
        runner._validate_images(task_dir, runtime)

    payload = run_payload()
    payload["runtime"]["images"][0]["reference"] = reference
    runner._validate_images(task_dir, parse_json(RunManifest, payload).runtime)


def write_harbor_task(task_dir: Path, isolation: str = "shared") -> None:
    (task_dir / "environment").mkdir(exist_ok=True)
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text("#!/bin/sh\n")
    (task_dir / "task.toml").write_text(
        f'version = "1.3"\n\n[verifier]\nenvironment_mode = "{isolation}"\n\n[environment]\n'
    )


def test_run_binding_checks_contract_runtime_and_resources(tmp_path: Path) -> None:
    task = task_contract()
    payload = run_payload()
    task_dir = tmp_path / "parent" / "task"
    task_dir.mkdir(parents=True)
    instruction = task_dir / "instruction.md"
    instruction.write_text("instruction\n")
    write_harbor_task(task_dir)
    payload["task"].update(
        contract_path="parent/task/slopbench-task.json",
        contract_sha256=SHA_A,
        task_digest=SHA_B,
        task_id=task.task_id,
        task_version=task.version,
    )
    payload["agent"]["instruction_layers"] = [
        {
            "name": "task",
            "path": "parent/task/instruction.md",
            "sha256": sha256_file(instruction),
        }
    ]
    manifest = parse_json(RunManifest, payload)

    runner._validate_run_binding(manifest, task, SHA_A, SHA_B, task_dir)

    for field, value in [
        ("contract_sha256", "1" * 64),
        ("task_digest", "2" * 64),
        ("task_id", "slopbench/tracer/other"),
        ("task_version", "2.0.0"),
    ]:
        changed = run_payload()
        changed["task"].update(payload["task"])
        changed["task"][field] = value
        with pytest.raises(ContractError, match=field):
            runner._validate_run_binding(
                parse_json(RunManifest, changed), task, SHA_A, SHA_B, task_dir
            )

    changed = run_payload()
    changed["task"].update(payload["task"])
    changed["runtime"]["memory_mb"] = 1024
    with pytest.raises(ContractError, match="task_resources"):
        runner._validate_run_binding(parse_json(RunManifest, changed), task, SHA_A, SHA_B, task_dir)


def test_run_binding_rejects_wrong_contract_path(tmp_path: Path) -> None:
    task = task_contract()
    manifest = run_manifest()
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    with pytest.raises(ContractError, match="does not identify"):
        runner._validate_run_binding(manifest, task, SHA_A, SHA_B, task_dir)


def test_run_binding_rejects_harbor_isolation_drift(tmp_path: Path) -> None:
    payload = task_payload()
    payload["environment"]["verifier_isolation"] = "separate"
    task = parse_json(TaskContract, payload)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    instruction = task_dir / "instruction.md"
    instruction.write_text("instruction\n")
    write_harbor_task(task_dir, isolation="shared")
    manifest_payload = run_payload()
    manifest_payload["task"].update(
        contract_path="task/slopbench-task.json",
        contract_sha256=SHA_A,
        task_digest=SHA_B,
        task_id=task.task_id,
        task_version=task.version,
    )
    manifest_payload["agent"]["instruction_layers"] = [
        {
            "name": "task",
            "path": "task/instruction.md",
            "sha256": sha256_file(instruction),
        }
    ]

    with pytest.raises(ContractError, match="verifier isolation does not match"):
        runner._validate_run_binding(
            parse_json(RunManifest, manifest_payload), task, SHA_A, SHA_B, task_dir
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("digest", "instruction layer digest mismatch"),
        ("missing", "missing task instruction layers"),
        ("duplicate", "instruction layer path is duplicated"),
        ("not-file", "instruction layer is not a regular file"),
    ],
)
def test_run_binding_enforces_instruction_layer_pins(
    tmp_path: Path, mutation: str, message: str
) -> None:
    task = task_contract()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    instruction = task_dir / "instruction.md"
    instruction.write_text("instruction\n")
    payload = run_payload()
    payload["task"].update(
        contract_path="task/slopbench-task.json",
        contract_sha256=SHA_A,
        task_digest=SHA_B,
        task_id=task.task_id,
        task_version=task.version,
    )
    payload["agent"]["instruction_layers"] = [
        {
            "name": "task",
            "path": "task/instruction.md",
            "sha256": sha256_file(instruction),
        }
    ]
    if mutation == "digest":
        payload["agent"]["instruction_layers"][0]["sha256"] = "9" * 64
    elif mutation == "missing":
        payload["agent"]["instruction_layers"] = []
    elif mutation == "duplicate":
        duplicate = payload["agent"]["instruction_layers"][0].copy()
        duplicate["name"] = "duplicate"
        payload["agent"]["instruction_layers"].append(duplicate)
    else:
        payload["agent"]["instruction_layers"][0]["path"] = "task/missing.md"
        payload["agent"]["instruction_layers"][0]["sha256"] = "9" * 64

    with pytest.raises(ContractError, match=message):
        runner._validate_run_binding(parse_json(RunManifest, payload), task, SHA_A, SHA_B, task_dir)


def test_run_binding_rejects_instruction_symlink_escape(tmp_path: Path) -> None:
    task = task_contract()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path.parent / "outside-instruction.md"
    outside.write_text("outside\n")
    (task_dir / "instruction.md").symlink_to(outside)
    payload = run_payload()
    payload["task"].update(
        contract_path="task/slopbench-task.json",
        contract_sha256=SHA_A,
        task_digest=SHA_B,
        task_id=task.task_id,
        task_version=task.version,
    )
    payload["agent"]["instruction_layers"] = [
        {
            "name": "task",
            "path": "task/instruction.md",
            "sha256": sha256_file(outside),
        }
    ]

    with pytest.raises(ContractError, match="escapes project root"):
        runner._validate_run_binding(parse_json(RunManifest, payload), task, SHA_A, SHA_B, task_dir)


def test_harbor_config_contains_only_pinned_run_settings(tmp_path: Path) -> None:
    manifest = run_manifest()

    config = runner._harbor_config(
        manifest, task_contract(), tmp_path / "task", tmp_path / "trials", SHA_B
    )

    assert config.trial_name == manifest.trial.id
    assert config.agent.name == "oracle"
    assert config.agent.model_name is None
    assert config.agent.env == {"SLOPBENCH_VARIANT": "oracle"}
    assert config.environment.type.value == "docker"
    assert config.environment.override_cpus == 2
    assert config.environment.override_memory_mb == 2048
    assert config.environment.override_storage_mb == 4096
    assert config.verifier.env == {
        "SLOPBENCH_TASK_DIGEST": SHA_B,
        "SLOPBENCH_VERIFIER_ISOLATION": "shared",
    }
    assert config.artifacts == ["/app/slopbench-report.json"]

    payload = task_payload()
    payload["environment"]["verifier_isolation"] = "separate"
    separate_task = parse_json(TaskContract, payload)
    separate = runner._harbor_config(
        manifest, separate_task, tmp_path / "task", tmp_path / "trials", SHA_B
    )
    assert len(separate.artifacts) == 1
    artifact = separate.artifacts[0]
    assert not isinstance(artifact, str)
    assert artifact.source == "/app"
    assert artifact.exclude == [".git"]


def test_harbor_config_rejects_unknown_environment_provider(tmp_path: Path) -> None:
    payload = run_payload()
    payload["runtime"]["environment_provider"] = "unknown-provider"
    manifest = parse_json(RunManifest, payload)

    with pytest.raises(runner.RunError, match="unsupported Harbor environment provider"):
        runner._harbor_config(
            manifest, task_contract(), tmp_path / "task", tmp_path / "trials", SHA_B
        )


def test_write_harbor_config_and_duration(tmp_path: Path) -> None:
    config = runner._harbor_config(
        run_manifest(), task_contract(), tmp_path / "task", tmp_path / "trials", SHA_B
    )
    path = tmp_path / "config.json"

    runner._write_harbor_config(path, config)

    assert json.loads(path.read_text())["trial_name"] == "tracer-oracle"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert runner._duration_seconds(start, start + timedelta(seconds=3)) == 3
    assert runner._duration_seconds(start, start - timedelta(seconds=3)) == 0
    assert runner._duration_seconds(None, start) is None


def test_outcome_vector_includes_applicable_and_not_applicable_gates() -> None:
    outcomes, errors = runner._check_outcomes(
        task_contract(), verification_evidence(), receipt_valid=True
    )

    assert errors == []
    assert len(outcomes) == len(GateName)
    assert (
        next(item for item in outcomes if item.gate == GateName.SAFETY_TYPE_ESCAPES).status
        == OutcomeStatus.NOT_APPLICABLE
    )
    assert all(
        item.status == OutcomeStatus.PASSED
        for item in outcomes
        if item.gate != GateName.SAFETY_TYPE_ESCAPES
    )


def test_outcome_vector_detects_missing_checks_and_invalid_receipt() -> None:
    payload = verification_payload()
    payload["checks"] = [
        check for check in payload["checks"] if check["gate"] != "requested_behavior"
    ]
    outcomes, errors = runner._check_outcomes(
        task_contract(), parse_json(VerificationEvidence, payload), receipt_valid=False
    )

    assert errors == ["applicable gate has no verifier check: requested_behavior"]
    assert (
        next(item for item in outcomes if item.gate == GateName.REQUESTED_BEHAVIOR).status
        == OutcomeStatus.FAILED
    )
    assert (
        next(item for item in outcomes if item.gate == GateName.EVIDENCE_RECEIPT).status
        == OutcomeStatus.FAILED
    )


def reward_vector(*, requested_passed: bool = True) -> dict[str, int]:
    rewards = {
        gate.value: int(
            requested_passed
            if gate in {GateName.REQUESTED_BEHAVIOR, GateName.REGRESSIONS}
            else True
        )
        for gate in GateName
        if gate != GateName.SAFETY_TYPE_ESCAPES
    }
    rewards["reward"] = int(all(rewards.values()))
    return rewards


def test_reward_validation_matches_outcomes() -> None:
    outcomes, _ = runner._check_outcomes(task_contract(), verification_evidence(), True)

    assert runner._validate_rewards(outcomes, reward_vector()) == []
    assert runner._validate_rewards(outcomes, None) == ["Harbor result has no reward vector"]
    assert len(runner._validate_rewards(outcomes, {"reward": 0})) == 7


def receipt_fixture(
    tmp_path: Path,
    *,
    report: AgentReport | None = None,
    raw: str | None = None,
) -> Path:
    path = tmp_path / "slopbench-report.json"
    if report is not None:
        write_model(path, report)
    elif raw is not None:
        path.write_text(raw)
    return path


def test_receipt_validation_distinguishes_missing_malformed_and_valid(tmp_path: Path) -> None:
    task = task_contract()
    verification = verification_evidence()
    missing = tmp_path / "missing.json"

    result, report, invalid = runner._validate_receipt(missing, task, verification)
    assert not result.present and not result.valid and report is None and not invalid

    malformed = receipt_fixture(tmp_path, raw="{}\n")
    result, report, invalid = runner._validate_receipt(malformed, task, verification)
    assert result.present and not result.valid and report is None and invalid

    valid = receipt_fixture(tmp_path, report=agent_report())
    result, report, invalid = runner._validate_receipt(valid, task, verification)
    assert result.valid and report is not None and not invalid
    assert result.sha256 == sha256_file(valid)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("revision", "final_revision"),
        ("missing-claim", "missing claim"),
        ("unknown-evidence", "unknown evidence"),
        ("wrong-gate", "different gate"),
        ("passed-contradiction", "contradicts failing"),
        ("failed-contradiction", "contradicts passing"),
        ("uncertain-without-detail", "uncertain claim"),
        ("unknown-command", "command references unknown"),
        ("command-mismatch", "command does not match"),
    ],
)
def test_receipt_validation_reports_claim_mismatches(
    tmp_path: Path, mutation: str, message: str
) -> None:
    task = task_contract()
    verification_payload_value = verification_payload()
    report_payload_value = report_payload()
    if mutation == "revision":
        report_payload_value["final_revision"] = f"sha256:{'9' * 64}"
    elif mutation == "missing-claim":
        report_payload_value["claims"].pop()
    elif mutation == "unknown-evidence":
        report_payload_value["claims"][0]["evidence_ids"] = ["unknown"]
    elif mutation == "wrong-gate":
        report_payload_value["claims"][0]["evidence_ids"] = ["public-regressions"]
    elif mutation == "passed-contradiction":
        verification_payload_value["checks"][0]["passed"] = False
        verification_payload_value["checks"][0]["exit_code"] = 1
    elif mutation == "failed-contradiction":
        report_payload_value["claims"][0]["status"] = "failed"
    elif mutation == "uncertain-without-detail":
        report_payload_value["uncertainty"] = []
    elif mutation == "unknown-command":
        report_payload_value["commands"][0]["id"] = "unknown-command"
    else:
        report_payload_value["commands"][0]["exit_code"] = 9
    report = parse_json(AgentReport, report_payload_value)
    verification = parse_json(VerificationEvidence, verification_payload_value)
    path = receipt_fixture(tmp_path, report=report)

    result, _, invalid = runner._validate_receipt(path, task, verification)

    assert invalid
    assert any(message in error for error in result.errors)


def make_trial_result(
    manifest: RunManifest,
    task_dir: Path,
    trial_dir: Path,
    *,
    rewards: dict[str, int] | None = None,
    exception_type: str | None = None,
) -> TrialResult:
    config = runner._harbor_config(manifest, task_contract(), task_dir, trial_dir.parent, SHA_B)
    exception = None
    if exception_type is not None:
        exception = ExceptionInfo(
            exception_type=exception_type,
            exception_message="failure",
            exception_traceback="traceback",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return TrialResult(
        task_name="task",
        trial_name=manifest.trial.id,
        trial_uri="file:///trial",
        task_id=LocalTaskId(path=task_dir),
        task_checksum="task-checksum",
        config=config,
        agent_info=AgentInfo(name=manifest.agent.harness, version="1.0.0"),
        agent_result=AgentContext(
            n_input_tokens=10,
            n_cache_tokens=2,
            n_output_tokens=3,
            cost_usd=0.25,
        ),
        verifier_result=VerifierResult(rewards=rewards),
        exception_info=exception,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=4),
    )


def prepare_bundle(
    tmp_path: Path,
    *,
    requested_passed: bool = True,
    report_mode: str = "valid",
    verification_mode: str = "valid",
    reward_mode: str = "valid",
    exception_type: str | None = None,
) -> tuple[Path, RunManifest, TaskContract]:
    bundle = tmp_path / "bundle"
    manifest = run_manifest()
    task = task_contract()
    trial_dir = bundle / "harbor" / manifest.trial.id
    trial_dir.mkdir(parents=True)
    verification_payload_value = verification_payload(requested_passed=requested_passed)
    if verification_mode == "wrong-digest":
        verification_payload_value["task_digest"] = "9" * 64
    if verification_mode != "missing":
        write_json(
            trial_dir / "verifier" / "slopbench-verification.json",
            verification_payload_value,
        )
    if report_mode == "valid":
        write_json(
            trial_dir / "artifacts" / "app" / "slopbench-report.json",
            report_payload(public_passed=requested_passed),
        )
    elif report_mode == "malformed":
        path = trial_dir / "artifacts" / "app" / "slopbench-report.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n")
    rewards = reward_vector(requested_passed=requested_passed)
    if report_mode in {"missing", "malformed"}:
        evidence = next(
            item
            for item in verification_payload_value["checks"]
            if item["gate"] == "evidence_receipt"
        )
        evidence["passed"] = False
        evidence["exit_code"] = 1
        write_json(
            trial_dir / "verifier" / "slopbench-verification.json",
            verification_payload_value,
        )
        rewards["evidence_receipt"] = 0
        rewards["reward"] = 0
    if reward_mode == "mismatch":
        rewards["reward"] = 1 - rewards["reward"]
    result = make_trial_result(
        manifest,
        tmp_path / "task",
        trial_dir,
        rewards=rewards,
        exception_type=exception_type,
    )
    (trial_dir / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
    (trial_dir / "config.json").write_text(result.config.model_dump_json(indent=2) + "\n")
    (trial_dir / "agent").mkdir(exist_ok=True)
    (trial_dir / "agent" / "trajectory.json").write_text("{}\n")
    (bundle / "slopbench-run.json").write_text("{}\n")
    return bundle, manifest, task


def test_finalize_valid_pass_captures_evidence_usage_and_timing(tmp_path: Path) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path)

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 0)

    assert result.classification == FailureClassification.VALID_PASS
    assert result.completed
    assert result.receipt.valid
    assert result.usage.input_tokens == 10
    assert result.usage.cache_tokens == 2
    assert result.usage.output_tokens == 3
    assert result.usage.cost_usd == 0.25
    assert result.timing.duration_seconds == 4
    assert result.harbor.result_sha256 is not None
    assert result.harbor.config_sha256 is not None
    assert result.harbor.trajectory_sha256 is not None
    assert (bundle / "result.json").is_file()
    assert "result.json" not in {artifact.path for artifact in result.artifacts}


def test_finalize_classifies_expected_agent_failures(tmp_path: Path) -> None:
    invalid_bundle, manifest, task = prepare_bundle(tmp_path / "invalid", requested_passed=False)
    missing_bundle, missing_manifest, missing_task = prepare_bundle(
        tmp_path / "missing", report_mode="missing"
    )

    invalid = runner._finalize(invalid_bundle, manifest, task, SHA_B, SHA_A, 0)
    missing = runner._finalize(missing_bundle, missing_manifest, missing_task, SHA_B, SHA_A, 0)

    assert invalid.classification == FailureClassification.VALID_AGENT_FAILURE
    assert not invalid.completed and invalid.receipt.valid
    assert missing.classification == FailureClassification.VALID_AGENT_FAILURE
    assert not missing.receipt.present


def test_finalize_classifies_malformed_receipt_as_invalid_run(tmp_path: Path) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path, report_mode="malformed")

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 0)

    assert result.classification == FailureClassification.INVALID_RUN
    assert result.receipt.present and not result.receipt.valid


@pytest.mark.parametrize(
    ("verification_mode", "reward_mode"),
    [("missing", "valid"), ("wrong-digest", "valid"), ("valid", "mismatch")],
)
def test_finalize_classifies_benchmark_defects(
    tmp_path: Path, verification_mode: str, reward_mode: str
) -> None:
    bundle, manifest, task = prepare_bundle(
        tmp_path,
        verification_mode=verification_mode,
        reward_mode=reward_mode,
    )

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 0)

    assert result.classification == FailureClassification.BENCHMARK_DEFECT
    assert result.receipt.errors


@pytest.mark.parametrize(
    ("exception_type", "classification"),
    [
        ("AgentTimeoutError", FailureClassification.VALID_AGENT_FAILURE),
        ("AgentSetupTimeoutError", FailureClassification.VALID_AGENT_FAILURE),
        ("DockerError", FailureClassification.INFRASTRUCTURE_FAILURE),
    ],
)
def test_finalize_classifies_harbor_exceptions(
    tmp_path: Path,
    exception_type: str,
    classification: FailureClassification,
) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path, exception_type=exception_type)

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 1)

    assert result.classification == classification


def test_finalize_without_harbor_result_is_infrastructure_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    result = runner._finalize(bundle, run_manifest(), task_contract(), SHA_B, SHA_A, 7)

    assert result.classification == FailureClassification.INFRASTRUCTURE_FAILURE
    assert result.receipt.errors == ["Harbor process exited 7 without a result"]
    assert result.harbor.result_sha256 is None


def test_load_trial_result_rejects_invalid_harbor_json(tmp_path: Path) -> None:
    assert runner._load_trial_result(tmp_path) is None
    (tmp_path / "result.json").write_text("{}\n")

    with pytest.raises(ContractError, match="invalid Harbor result"):
        runner._load_trial_result(tmp_path)


def test_finalize_emits_bundle_for_invalid_harbor_result(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    trial_dir = bundle / "harbor" / "tracer-oracle"
    trial_dir.mkdir(parents=True)
    raw_result = trial_dir / "result.json"
    raw_result.write_text("{}\n")

    result = runner._finalize(bundle, run_manifest(), task_contract(), SHA_B, SHA_A, 0)

    assert result.classification == FailureClassification.INFRASTRUCTURE_FAILURE
    assert result.harbor.result_sha256 == sha256_file(raw_result)
    assert any("invalid Harbor result" in error for error in result.receipt.errors)
    assert (bundle / "result.json").is_file()


def make_bound_task(tmp_path: Path) -> tuple[Path, str, str, TaskContract]:
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Do the work.\n")
    (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
    write_harbor_task(task_dir)
    write_json(task_dir / "slopbench-task.json", task_payload(sealed=False))
    seal_task(task_dir)
    task, contract_sha, digest = validate_task(task_dir)
    return task_dir, contract_sha, digest, task


def test_execute_run_builds_bundle_and_finalizes_process_failure(tmp_path: Path) -> None:
    task_dir, contract_sha, task_digest, task = make_bound_task(tmp_path)
    payload = run_payload()
    payload["task"].update(
        contract_path="task/slopbench-task.json",
        contract_sha256=contract_sha,
        task_digest=task_digest,
        task_id=task.task_id,
        task_version=task.version,
    )
    payload["agent"]["instruction_layers"] = [
        {
            "name": "task",
            "path": "task/instruction.md",
            "sha256": sha256_file(task_dir / "instruction.md"),
        }
    ]
    manifest_path = tmp_path / "run.json"
    write_json(manifest_path, payload)

    def failed_process(
        command: list[str],
        cwd: Path,
        environment: object,
        stdout: Path,
        stderr: Path,
    ) -> int:
        assert command[1:3] == ["trial", "start"]
        assert cwd == task_dir.parent
        stdout.write_text("stdout\n")
        stderr.write_text("stderr\n")
        return 9

    result, bundle = runner.execute_run(
        task_dir, manifest_path, tmp_path / "output", process_runner=failed_process
    )

    assert result.classification == FailureClassification.INFRASTRUCTURE_FAILURE
    assert (bundle / "slopbench-run.json").read_bytes() == manifest_path.read_bytes()
    assert (bundle / "harbor-input.json").is_file()
    assert (bundle / "harbor.stdout.log").read_text() == "stdout\n"
    assert (bundle / "result.json").is_file()

    with pytest.raises(runner.RunError, match="already exists"):
        runner.execute_run(
            task_dir, manifest_path, tmp_path / "output", process_runner=failed_process
        )
