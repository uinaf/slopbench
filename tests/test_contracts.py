from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from slopbench.contracts import (
    AgentReport,
    ResultBundle,
    RunManifest,
    TaskContract,
    validate_relative_path,
)
from tests.helpers import report_payload, result_payload, run_payload, task_payload


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
            "tools must be unique",
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

    with pytest.raises(ValidationError, match="absolute and canonical"):
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
