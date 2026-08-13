from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.task.id import LocalTaskId
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from slopbench import runner
from slopbench.contracts import (
    AgentReport,
    FailureClassification,
    FailureReason,
    GateName,
    OutcomeStatus,
    RetryDecision,
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


def write_harbor_task(task_dir: Path, isolation: str = "separate") -> None:
    (task_dir / "environment").mkdir(exist_ok=True)
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text("#!/bin/sh\n")
    (task_dir / "task.toml").write_text(
        'version = "1.3"\n\n'
        '[metadata]\nslopbench_task_id = "slopbench/tracer/example"\n'
        'slopbench_task_version = "1.0.0"\n\n'
        '[agent]\nnetwork_mode = "allowlist"\nallowed_hosts = ["cursor.com"]\n\n'
        f'[verifier]\nenvironment_mode = "{isolation}"\nnetwork_mode = "no-network"\n\n'
        '[environment]\nnetwork_mode = "no-network"\ncpus = 2\n'
        'memory_mb = 2048\nstorage_mb = 4096\nworkdir = "/app"\n'
    )


def harbor_boundary_fixture(tmp_path: Path) -> tuple[HarborTask, RunManifest, TaskContract, Path]:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("instruction\n")
    write_harbor_task(task_dir)
    return HarborTask(task_dir), run_manifest(), task_contract(), task_dir


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("provider", "require the Docker"),
        ("compose", "Docker Compose"),
        ("metadata", "metadata does not match"),
        ("resources", "resources or workdir"),
        ("environment", "unapproved environment inputs"),
        ("agent-network", "agent phase network policy mismatch"),
        ("verifier-network", "verifier phase network policy mismatch"),
    ],
)
def test_harbor_boundary_rejects_policy_drift(tmp_path: Path, mutation: str, message: str) -> None:
    harbor_task, manifest, task, task_dir = harbor_boundary_fixture(tmp_path)
    payload = manifest.model_dump(mode="json")
    task_toml = task_dir / "task.toml"
    if mutation == "provider":
        payload["runtime"]["environment_provider"] = "modal"
        manifest = RunManifest.model_validate(payload)
    elif mutation == "compose":
        (task_dir / "environment" / "docker-compose.yaml").write_text("services: {}\n")
    elif mutation == "metadata":
        task_toml.write_text(
            task_toml.read_text().replace("slopbench/tracer/example", "slopbench/tracer/other")
        )
        harbor_task = HarborTask(task_dir)
    elif mutation == "resources":
        task_toml.write_text(task_toml.read_text().replace("memory_mb = 2048", "memory_mb = 1024"))
        harbor_task = HarborTask(task_dir)
    elif mutation == "environment":
        task_toml.write_text(task_toml.read_text() + 'env = { FOO = "bar" }\n')
        harbor_task = HarborTask(task_dir)
    elif mutation == "agent-network":
        task_toml.write_text(
            task_toml.read_text().replace(
                'network_mode = "allowlist"\nallowed_hosts = ["cursor.com"]',
                'network_mode = "public"',
            )
        )
        harbor_task = HarborTask(task_dir)
    else:
        task_toml.write_text(
            task_toml.read_text().replace(
                '[verifier]\nenvironment_mode = "separate"\nnetwork_mode = "no-network"',
                '[verifier]\nenvironment_mode = "separate"\nnetwork_mode = "public"',
            )
        )
        harbor_task = HarborTask(task_dir)

    with pytest.raises(ContractError, match=message):
        runner._validate_harbor_boundary(harbor_task, manifest, task, task_dir)


def test_run_binding_checks_contract_runtime_and_resources(tmp_path: Path) -> None:
    task = task_contract()
    payload = run_payload()
    task_dir = tmp_path / "parent" / "task"
    task_dir.mkdir(parents=True)
    instruction = task_dir / "instruction.md"
    instruction.write_text("instruction\n")
    write_harbor_task(task_dir)
    payload["task"]["harbor_task_checksum"] = HarborTask(task_dir).checksum
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


def task_with_attack_fixture() -> TaskContract:
    payload = task_payload(sealed=False)
    payload["attack_fixtures"] = [
        {
            "id": "tamper",
            "kind": "verifier_tampering",
            "entrypoint": "solution/attack.py",
            "expected": {
                "classification": "valid_agent_failure",
                "failed_gates": ["authority"],
            },
        }
    ]
    return parse_json(TaskContract, payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("environment", "environment exceeds"),
        ("tool", "tools exceed"),
        ("utility-credential", "utility harnesses cannot"),
        ("unknown-attack", "unknown attack fixture"),
        ("paid-attack", "zero-cost oracle"),
    ],
)
def test_capability_binding_rejects_manifest_escalation(mutation: str, message: str) -> None:
    payload = run_payload()
    task = task_with_attack_fixture()
    if mutation == "environment":
        payload["agent"]["environment"]["UNDECLARED"] = "value"
    elif mutation == "tool":
        payload["agent"]["tools"].append({"name": "node", "version": "1.0.0", "settings": {}})
    elif mutation == "utility-credential":
        payload["agent"]["credential_env"] = ["CURSOR_API_KEY"]
    elif mutation == "unknown-attack":
        payload["attack_fixture_id"] = "unknown"
    else:
        payload["attack_fixture_id"] = "tamper"
        payload["limits"]["max_cost_usd"] = 1.0

    with pytest.raises(ContractError, match=message):
        runner._validate_capability_binding(parse_json(RunManifest, payload), task)


def test_capability_binding_accepts_declared_zero_cost_attack() -> None:
    payload = run_payload()
    payload["attack_fixture_id"] = "tamper"

    runner._validate_capability_binding(
        parse_json(RunManifest, payload), task_with_attack_fixture()
    )


def test_harbor_config_contains_only_pinned_run_settings(tmp_path: Path) -> None:
    manifest = run_manifest()

    config = runner._harbor_config(
        manifest, task_contract(), tmp_path / "task", tmp_path / "trials", SHA_B
    )

    assert config.trial_name == manifest.trial.id
    assert config.agent.name == "oracle"
    assert config.agent.model_name is None
    assert config.agent.env == {
        "SLOPBENCH_VARIANT": "oracle",
        "SLOPBENCH_TASK_DIGEST": SHA_B,
    }
    assert config.environment.type.value == "docker"
    assert config.environment.override_cpus == 2
    assert config.environment.override_memory_mb == 2048
    assert config.environment.override_storage_mb == 4096
    assert config.verifier.env == {
        "SLOPBENCH_TASK_DIGEST": SHA_B,
        "SLOPBENCH_VERIFIER_ISOLATION": "separate",
    }
    assert len(config.artifacts) == 1
    artifact = config.artifacts[0]
    assert not isinstance(artifact, str)
    assert artifact.source == "/app"
    assert artifact.exclude == [".git"]


def test_harbor_config_binds_attack_identity_to_both_phases(tmp_path: Path) -> None:
    payload = run_payload()
    payload["attack_fixture_id"] = "tamper"
    manifest = parse_json(RunManifest, payload)

    config = runner._harbor_config(
        manifest, task_with_attack_fixture(), tmp_path / "task", tmp_path / "trials", SHA_B
    )

    assert config.agent.env["SLOPBENCH_ATTACK_FIXTURE"] == "tamper"
    assert config.verifier.env["SLOPBENCH_ATTACK_FIXTURE"] == "tamper"


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


def test_outcome_vector_rejects_checks_for_non_applicable_gates() -> None:
    payload = verification_payload()
    payload["checks"].append(
        {
            "id": "unsafe-cast",
            "gate": "safety_type_escapes",
            "passed": True,
            "command": "true",
            "exit_code": 0,
            "log_path": "test-unsafe-cast.txt",
            "log_sha256": SHA_A,
        }
    )

    _, errors = runner._check_outcomes(
        task_contract(), parse_json(VerificationEvidence, payload), receipt_valid=True
    )

    assert errors == ["non-applicable gate has verifier checks: safety_type_escapes"]


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
    assert len(runner._validate_rewards(outcomes, {"reward": 0})) == 8
    with_extra = {**reward_vector(), "undeclared": 1}
    assert runner._validate_rewards(outcomes, with_extra) == [
        "Harbor reward keys mismatch: missing=[], unexpected=['undeclared']"
    ]


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
        ("missing-claim", "gate coverage mismatch"),
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


def test_receipt_validation_binds_task_base_and_command_coverage(tmp_path: Path) -> None:
    report_payload_value = report_payload()
    report_payload_value["task_digest"] = "9" * 64
    report_payload_value["base_revision"] = "8" * 40
    report_payload_value["commands"].append(
        {"id": "requested-extra", "command": "true", "exit_code": 0}
    )
    verification_payload_value = verification_payload()
    verification_payload_value["checks"].append(
        {
            "id": "requested-extra",
            "gate": "requested_behavior",
            "passed": True,
            "command": "true",
            "exit_code": 0,
            "log_path": "test-requested-extra.txt",
            "log_sha256": SHA_A,
        }
    )
    path = receipt_fixture(tmp_path, report=parse_json(AgentReport, report_payload_value))

    result, _, invalid = runner._validate_receipt(
        path,
        task_contract(),
        parse_json(VerificationEvidence, verification_payload_value),
    )

    assert invalid
    assert any("task_digest" in error for error in result.errors)
    assert any("base_revision" in error for error in result.errors)
    assert any("not referenced by a claim" in error for error in result.errors)


def test_verifier_log_digests_detect_missing_and_changed_files(tmp_path: Path) -> None:
    payload = verification_payload()
    for check in payload["checks"]:
        path = tmp_path / "verifier" / check["log_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{check['id']}\n")
        check["log_sha256"] = sha256_file(path)
    verification = parse_json(VerificationEvidence, payload)

    assert runner._validate_verification_logs(tmp_path, verification) == []
    first = tmp_path / "verifier" / verification.checks[0].log_path
    first.write_text("changed\n")
    assert "digest mismatch" in runner._validate_verification_logs(tmp_path, verification)[0]
    first.unlink()
    assert "is missing" in runner._validate_verification_logs(tmp_path, verification)[0]


def test_reward_artifact_must_match_harbor_result(tmp_path: Path) -> None:
    rewards = reward_vector()
    path = tmp_path / "verifier" / "reward.json"
    write_json(path, rewards)

    assert runner._validate_reward_artifact(tmp_path, task_contract(), rewards) == []
    path.write_text("not-json\n")
    assert "is invalid" in runner._validate_reward_artifact(tmp_path, task_contract(), rewards)[0]
    write_json(path, {"reward": 0})
    assert (
        "does not match" in runner._validate_reward_artifact(tmp_path, task_contract(), rewards)[0]
    )
    path.unlink()
    assert "is missing" in runner._validate_reward_artifact(tmp_path, task_contract(), rewards)[0]


def test_artifact_digests_hash_symlink_identity_without_following_target(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("secret\n")
    (tmp_path / "link").symlink_to(outside)

    artifacts = runner._artifact_digests(tmp_path)

    assert [artifact.path for artifact in artifacts] == ["link"]
    assert artifacts[0].sha256 != sha256_file(outside)


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
        task_checksum=manifest.task.harbor_task_checksum,
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
        rewards["evidence_receipt"] = 0
        rewards["reward"] = 0
    if reward_mode == "mismatch":
        rewards["reward"] = 1 - rewards["reward"]
    if verification_mode != "missing":
        for check in verification_payload_value["checks"]:
            log_path = trial_dir / "verifier" / check["log_path"]
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"{check['id']}\n")
            check["log_sha256"] = sha256_file(log_path)
        write_json(
            trial_dir / "verifier" / "slopbench-verification.json",
            verification_payload_value,
        )
    write_json(trial_dir / "verifier" / "reward.json", rewards)
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
    ("mutation", "reason"),
    [
        ("task-checksum", FailureReason.HARBOR_TASK_MISMATCH),
        ("invalid-evidence", FailureReason.VERIFIER_EVIDENCE_INVALID),
        ("base-revision", FailureReason.VERIFIER_CONTRACT_MISMATCH),
        ("log-digest", FailureReason.VERIFIER_CONTRACT_MISMATCH),
    ],
)
def test_finalize_rejects_tampered_execution_evidence(
    tmp_path: Path, mutation: str, reason: FailureReason
) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path)
    trial_dir = bundle / "harbor" / manifest.trial.id
    if mutation == "task-checksum":
        path = trial_dir / "result.json"
        payload = json.loads(path.read_text())
        payload["task_checksum"] = "9" * 64
        write_json(path, payload)
    elif mutation == "invalid-evidence":
        (trial_dir / "verifier" / "slopbench-verification.json").write_text("{}\n")
    elif mutation == "base-revision":
        path = trial_dir / "verifier" / "slopbench-verification.json"
        payload = json.loads(path.read_text())
        payload["base_revision"] = "9" * 40
        write_json(path, payload)
    else:
        (trial_dir / "verifier" / "test-requested-contract.txt").write_text("tampered\n")

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 0)

    assert result.classification == FailureClassification.BENCHMARK_DEFECT
    assert result.failure_reason == reason


@pytest.mark.parametrize(
    ("exception_type", "classification", "reason"),
    [
        (
            "AgentTimeoutError",
            FailureClassification.VALID_AGENT_FAILURE,
            FailureReason.AGENT_TIMEOUT,
        ),
        (
            "AgentSetupTimeoutError",
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.AGENT_SETUP_TIMEOUT,
        ),
        (
            "NonZeroAgentExitCodeError",
            FailureClassification.VALID_AGENT_FAILURE,
            FailureReason.AGENT_EXIT,
        ),
        (
            "ApiRateLimitError",
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.PROVIDER_RATE_LIMIT,
        ),
        (
            "ApiUsageLimitError",
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.PROVIDER_USAGE_LIMIT,
        ),
        (
            "VerifierTimeoutError",
            FailureClassification.BENCHMARK_DEFECT,
            FailureReason.VERIFIER_TIMEOUT,
        ),
        (
            "RewardFileNotFoundError",
            FailureClassification.BENCHMARK_DEFECT,
            FailureReason.VERIFIER_EVIDENCE_INVALID,
        ),
        (
            "EnvironmentStartTimeoutError",
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.ENVIRONMENT_START_TIMEOUT,
        ),
        (
            "DockerError",
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.HARBOR_EXCEPTION,
        ),
    ],
)
def test_finalize_classifies_harbor_exceptions(
    tmp_path: Path,
    exception_type: str,
    classification: FailureClassification,
    reason: FailureReason,
) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path, exception_type=exception_type)

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 1)

    assert result.classification == classification
    assert result.failure_reason == reason


def test_retry_decision_requires_classified_allowed_infrastructure_and_budget() -> None:
    payload = run_payload()
    payload["retry_policy"] = {
        "max_attempts": 2,
        "retryable_reasons": ["provider_rate_limit"],
    }
    manifest = parse_json(RunManifest, payload)

    allowed = runner._retry_disposition(
        manifest,
        FailureClassification.INFRASTRUCTURE_FAILURE,
        FailureReason.PROVIDER_RATE_LIMIT,
    )
    disallowed = runner._retry_disposition(
        manifest,
        FailureClassification.INFRASTRUCTURE_FAILURE,
        FailureReason.PROVIDER_USAGE_LIMIT,
    )
    agent = runner._retry_disposition(
        manifest,
        FailureClassification.VALID_AGENT_FAILURE,
        FailureReason.AGENT_TIMEOUT,
    )
    exhausted_payload = payload.copy()
    exhausted_payload["trial"] = {**payload["trial"], "attempt": 2}
    exhausted = runner._retry_disposition(
        parse_json(RunManifest, exhausted_payload),
        FailureClassification.INFRASTRUCTURE_FAILURE,
        FailureReason.PROVIDER_RATE_LIMIT,
    )

    assert allowed.eligible and allowed.decision == RetryDecision.RETRY_ALLOWED
    assert disallowed.decision == RetryDecision.REASON_NOT_ALLOWED
    assert agent.decision == RetryDecision.CLASSIFICATION_NOT_RETRYABLE
    assert exhausted.decision == RetryDecision.ATTEMPTS_EXHAUSTED


def test_finalize_without_harbor_result_is_infrastructure_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    result = runner._finalize(bundle, run_manifest(), task_contract(), SHA_B, SHA_A, 7)

    assert result.classification == FailureClassification.INFRASTRUCTURE_FAILURE
    assert result.receipt.errors == ["Harbor process exited 7 without a result"]
    assert result.harbor.result_sha256 is None


def test_finalize_rejects_nonzero_harbor_process_with_completed_result(tmp_path: Path) -> None:
    bundle, manifest, task = prepare_bundle(tmp_path)

    result = runner._finalize(bundle, manifest, task, SHA_B, SHA_A, 9)

    assert result.classification == FailureClassification.INFRASTRUCTURE_FAILURE
    assert result.failure_reason == FailureReason.HARBOR_PROCESS_FAILURE
    assert result.receipt.valid
    assert result.receipt.errors == ["Harbor process exited 9 despite a completed result"]


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


def test_task_snapshot_is_revalidated_and_made_read_only(tmp_path: Path) -> None:
    task_dir, contract_sha, task_digest, _ = make_bound_task(tmp_path)
    harbor_checksum = HarborTask(task_dir).checksum
    snapshot = tmp_path / "snapshot"

    runner._snapshot_task(task_dir, snapshot, contract_sha, task_digest, harbor_checksum)

    assert validate_task(snapshot)[1:] == (contract_sha, task_digest)
    assert HarborTask(snapshot).checksum == harbor_checksum
    assert not (snapshot / "instruction.md").stat().st_mode & 0o222
    with pytest.raises(ContractError, match="task changed while"):
        runner._snapshot_task(
            task_dir,
            tmp_path / "wrong-snapshot",
            contract_sha,
            task_digest,
            "9" * 64,
        )


def test_execute_run_rejects_in_task_output_and_invalid_manifest(tmp_path: Path) -> None:
    task_dir, _, _, _ = make_bound_task(tmp_path)
    with pytest.raises(runner.RunError, match="outside the sealed task"):
        runner.execute_run(task_dir, tmp_path / "missing.json", task_dir / "artifacts")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}\n")
    with pytest.raises(ContractError, match="invalid RunManifest"):
        runner.execute_run(task_dir, invalid, tmp_path / "output")


def test_execute_run_builds_bundle_and_finalizes_process_failure(tmp_path: Path) -> None:
    task_dir, contract_sha, task_digest, task = make_bound_task(tmp_path)
    payload = run_payload()
    payload["task"].update(
        contract_path="task/slopbench-task.json",
        contract_sha256=contract_sha,
        task_digest=task_digest,
        task_id=task.task_id,
        task_version=task.version,
        harbor_task_checksum=HarborTask(task_dir).checksum,
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
        assert cwd == tmp_path / "output" / "tracer-oracle" / "inputs"
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
