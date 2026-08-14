from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from slopbench.contracts import (
    AgentReport,
    ResultBundle,
    RunManifest,
    TaskContract,
    VerificationEvidence,
    validate_relative_path,
)
from tests.helpers import (
    report_payload,
    result_payload,
    run_payload,
    task_payload,
    verification_payload,
)


def validate(model: type[object], payload: dict[str, object]) -> object:
    return model.model_validate_json(json.dumps(payload))  # type: ignore[attr-defined]


def test_task_contract_accepts_single_and_fresh_sequential_phases() -> None:
    single = validate(TaskContract, task_payload())
    sequential = validate(TaskContract, task_payload(sequential=True))

    assert single.phase_mode.value == "single"
    assert sequential.phase_mode.value == "sequential"
    assert len(sequential.phases) == 2


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("", "non-empty POSIX"),
        ("/absolute", "stay within"),
        ("../escape", "stay within"),
        ("a/./b", "canonical"),
        ("a\\b", "POSIX"),
    ],
)
def test_relative_paths_reject_ambiguous_or_escaping_values(path: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_relative_path(path)


def test_relative_path_accepts_canonical_path() -> None:
    assert validate_relative_path("tasks/tracer/instruction.md") == ("tasks/tracer/instruction.md")


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.update(phase_mode="sequential"), "phase_mode"),
        (
            lambda value: value["phases"].append(value["phases"][0].copy()),
            "phase names must be unique",
        ),
        (
            lambda value: value["applicable_gates"].append("regressions"),
            "applicable_gates must be unique",
        ),
        (
            lambda value: value["immutable_inputs"].append(value["immutable_inputs"][0].copy()),
            "immutable input paths must be unique",
        ),
        (
            lambda value: value["immutable_inputs"].pop(0),
            "missing required paths",
        ),
        (
            lambda value: value["capabilities"]["tools"].append("git"),
            "capability values must be unique",
        ),
    ],
)
def test_task_contract_rejects_inconsistent_shapes(mutator: object, message: str) -> None:
    payload = task_payload()
    mutator(payload)  # type: ignore[operator]

    with pytest.raises(ValidationError, match=message):
        validate(TaskContract, payload)


def test_task_contract_allows_an_unsealed_draft() -> None:
    contract = validate(TaskContract, task_payload(sealed=False))

    assert contract.immutable_inputs == []


@pytest.mark.parametrize(
    "field",
    ["evidence_path", "reward_path"],
)
def test_verifier_output_paths_must_be_absolute(field: str) -> None:
    payload = task_payload()
    payload["verifier"][field] = "logs/output.json"

    with pytest.raises(ValidationError, match="canonical file below"):
        validate(TaskContract, payload)


def test_verifier_outputs_must_be_distinct() -> None:
    payload = task_payload()
    payload["verifier"]["reward_path"] = payload["verifier"]["evidence_path"]

    with pytest.raises(ValidationError, match="must be distinct"):
        validate(TaskContract, payload)


@pytest.mark.parametrize(
    ("hosts", "network", "message"),
    [
        (["cursor.com", "cursor.com"], "model-only", "must be unique"),
        (["https://cursor.com"], "model-only", "invalid network host"),
        (["cursor.com."], "model-only", "invalid network host"),
        (["cursor.com"], "none", "must be empty"),
        ([], "model-only", "is required"),
    ],
)
def test_capability_network_policy_is_canonical(
    hosts: list[str], network: str, message: str
) -> None:
    payload = task_payload()
    payload["capabilities"]["network_allowed_hosts"] = hosts
    payload["capabilities"]["network"] = network

    with pytest.raises(ValidationError, match=message):
        validate(TaskContract, payload)


def test_official_task_requires_separate_verifier() -> None:
    payload = task_payload()
    payload["environment"]["verifier_isolation"] = "shared"

    with pytest.raises(ValidationError, match="literal_error"):
        validate(TaskContract, payload)


def attack_task_payload() -> dict[str, object]:
    payload = task_payload()
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
    payload["immutable_inputs"].append({"path": "solution/attack.py", "sha256": "f" * 64})
    return payload


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate-id", "ids must be unique"),
        ("duplicate-gate", "failed_gates must be unique"),
        ("non-applicable", "expects non-applicable gates"),
    ],
)
def test_attack_fixture_contract_rejects_ambiguous_expectations(
    mutation: str, message: str
) -> None:
    payload = attack_task_payload()
    fixtures = payload["attack_fixtures"]
    if mutation == "duplicate-id":
        fixtures.append(fixtures[0].copy())
    elif mutation == "duplicate-gate":
        fixtures[0]["expected"]["failed_gates"] = ["authority", "authority"]
    else:
        fixtures[0]["expected"]["failed_gates"] = ["safety_type_escapes"]

    with pytest.raises(ValidationError, match=message):
        validate(TaskContract, payload)


def test_agent_configuration_rejects_missing_model_for_real_harness() -> None:
    payload = run_payload()
    payload["agent"]["harness"] = "cursor-cli"

    with pytest.raises(ValidationError, match="model is required"):
        validate(RunManifest, payload)


def test_agent_configuration_accepts_a_pinned_model() -> None:
    payload = run_payload()
    payload["agent"]["harness"] = "cursor-cli"
    payload["agent"]["model"] = {"provider": "cursor", "name": "composer-2.5"}

    manifest = validate(RunManifest, payload)

    assert manifest.agent.model.harbor_name == "cursor/composer-2.5"


@pytest.mark.parametrize(
    "name",
    [
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPYCACHEPREFIX",
        "SLOPBENCH_ATTACK_FIXTURE",
        "SLOPBENCH_TASK_DIGEST",
    ],
)
def test_agent_configuration_rejects_runner_reserved_environment(name: str) -> None:
    payload = run_payload()
    payload["agent"]["environment"] = {name: "forged"}

    with pytest.raises(ValidationError, match="runner-reserved"):
        validate(RunManifest, payload)


@pytest.mark.parametrize(
    "location",
    ["settings", "environment", "tool"],
)
def test_run_manifest_rejects_secret_material(location: str) -> None:
    payload = run_payload()
    if location == "settings":
        payload["agent"]["settings"] = {"nested": {"api_key": "secret"}}
    elif location == "environment":
        payload["agent"]["environment"] = {"ACCESS_TOKEN": "secret"}
    else:
        payload["agent"]["tools"][0]["settings"] = {"password": "secret"}

    with pytest.raises(ValidationError, match="looks sensitive"):
        validate(RunManifest, payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("credential_env", ["CURSOR_API_KEY", "CURSOR_API_KEY"], "must be unique"),
        (
            "tools",
            [
                {"name": "python", "version": "1", "settings": {}},
                {"name": "python", "version": "2", "settings": {}},
            ],
            "tool names must be unique",
        ),
        (
            "instruction_layers",
            [
                {"name": "task", "path": "a", "sha256": "a" * 64},
                {"name": "task", "path": "b", "sha256": "b" * 64},
            ],
            "instruction layer names must be unique",
        ),
    ],
)
def test_agent_configuration_rejects_duplicate_pins(
    field: str, value: object, message: str
) -> None:
    payload = run_payload()
    payload["agent"][field] = value

    with pytest.raises(ValidationError, match=message):
        validate(RunManifest, payload)


def test_run_and_trial_identity_must_match() -> None:
    payload = run_payload()
    payload["trial"]["id"] = "different"

    with pytest.raises(ValidationError, match="must match"):
        validate(RunManifest, payload)


def test_retry_policy_bounds_attempts_and_reasons() -> None:
    exhausted = run_payload()
    exhausted["trial"]["attempt"] = 2
    duplicate = run_payload()
    duplicate["retry_policy"] = {
        "max_attempts": 2,
        "retryable_reasons": ["provider_rate_limit", "provider_rate_limit"],
    }

    with pytest.raises(ValidationError, match="exceeds retry_policy"):
        validate(RunManifest, exhausted)
    with pytest.raises(ValidationError, match="retryable_reasons must be unique"):
        validate(RunManifest, duplicate)


def test_image_references_must_be_digest_pinned() -> None:
    payload = run_payload()
    payload["runtime"]["images"][0]["reference"] = "python:3.13"

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        validate(RunManifest, payload)


def test_agent_report_rejects_duplicate_claims_and_commands() -> None:
    claims = report_payload()
    claims["claims"].append(claims["claims"][0].copy())
    commands = report_payload()
    commands["commands"].append(commands["commands"][0].copy())

    with pytest.raises(ValidationError, match="one entry per gate"):
        validate(AgentReport, claims)
    with pytest.raises(ValidationError, match="command ids must be unique"):
        validate(AgentReport, commands)


def test_agent_report_rejects_duplicate_claim_evidence() -> None:
    payload = report_payload()
    payload["claims"][0]["evidence_ids"].append("requested-contract")

    with pytest.raises(ValidationError, match="claim evidence_ids must be unique"):
        validate(AgentReport, payload)


def test_verification_rejects_duplicate_log_paths() -> None:
    payload = {
        "schema_version": "slopbench.verification.v1",
        "task_digest": "b" * 64,
        "base_revision": "d" * 40,
        "final_revision": f"sha256:{'c' * 64}",
        "checks": [
            {
                "id": identifier,
                "gate": gate,
                "passed": True,
                "command": "true",
                "exit_code": 0,
                "log_path": "same.txt",
                "log_sha256": "a" * 64,
            }
            for identifier, gate in (
                ("first", "requested_behavior"),
                ("second", "regressions"),
            )
        ],
    }

    with pytest.raises(ValidationError, match="log paths must be unique"):
        validate(VerificationEvidence, payload)


def test_verification_rejects_nested_log_paths() -> None:
    payload = verification_payload()
    payload["checks"][0]["log_path"] = "nested/check.txt"

    with pytest.raises(ValidationError, match="direct file"):
        validate(VerificationEvidence, payload)


def test_result_requires_every_gate_once() -> None:
    missing = result_payload()
    missing["outcomes"].pop()
    duplicate = result_payload()
    duplicate["outcomes"][-1] = duplicate["outcomes"][0]

    for payload in (missing, duplicate):
        with pytest.raises(ValidationError, match="exactly one entry per gate"):
            validate(ResultBundle, payload)


def test_result_completion_must_match_classification() -> None:
    payload = result_payload()
    payload["completed"] = False

    with pytest.raises(ValidationError, match="completed must be true"):
        validate(ResultBundle, payload)


def test_result_rejects_inconsistent_failure_and_retry_state() -> None:
    reason = result_payload()
    reason["failure_reason"] = "gate_failure"
    retry = result_payload(classification="valid_agent_failure")
    retry["retry"] = {
        "eligible": True,
        "decision": "retry_allowed",
        "remaining_attempts": 1,
    }

    with pytest.raises(ValidationError, match="failure_reason must be none"):
        validate(ResultBundle, reason)
    with pytest.raises(ValidationError, match="retry eligibility is inconsistent"):
        validate(ResultBundle, retry)
