from __future__ import annotations

from pathlib import Path

import pytest

from slopbench.hashing import ContractError, load_model, validate_task
from slopbench.reference import _image_references, build_reference_run, write_reference_runs
from slopbench.release import EvaluationPurpose, ReferenceConfiguration
from slopbench.runner import _validate_run_binding

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks" / "diagnosis" / "query-cache-key"


@pytest.mark.parametrize(
    "name",
    [
        "cursor-grok-4.6-medium",
        "codex-terra-medium",
        "claude-code-sonnet-medium",
    ],
)
def test_reference_configuration_builds_an_executable_bound_run(name: str) -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / f"{name}.json", ReferenceConfiguration
    )

    manifest = build_reference_run(
        TASK,
        ROOT,
        configuration,
        1,
        environment_provider_version="29.5.2",
    )
    task, contract_sha256, task_digest = validate_task(TASK)
    _validate_run_binding(manifest, task, contract_sha256, task_digest, TASK)

    assert manifest.agent.harness == configuration.harness.name
    assert manifest.agent.adapter == configuration.adapter
    assert manifest.agent.model == configuration.model
    assert manifest.agent.setup_network_allowed_hosts == configuration.setup_network_allowed_hosts
    assert manifest.agent.network_allowed_hosts == configuration.network_allowed_hosts
    assert manifest.runtime.cpus == task.environment.cpus
    assert manifest.trial.seed == 1


def test_reference_run_writer_uses_matched_five_trial_pairs(tmp_path: Path) -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )

    paths = write_reference_runs(
        [TASK],
        ROOT,
        configuration,
        EvaluationPurpose.COMPARISON,
        tmp_path,
        environment_provider_version="29.5.2",
    )

    assert [path.name for path in paths] == [f"trial-{index}.json" for index in range(1, 6)]
    assert len({path.read_bytes() for path in paths}) == 5


def test_reference_run_rejects_out_of_envelope_model_network() -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )
    changed = configuration.model_copy(update={"network_allowed_hosts": ["example.com"]})

    with pytest.raises(ContractError, match="network exceeds"):
        build_reference_run(
            TASK,
            ROOT,
            changed,
            1,
            environment_provider_version="29.5.2",
        )


def test_reference_run_rejects_task_outside_root_and_invalid_pair() -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )

    with pytest.raises(ContractError, match="below the project root"):
        build_reference_run(
            TASK,
            TASK / "nested-root",
            configuration,
            1,
            environment_provider_version="29.5.2",
        )
    for pair_index in (0, 6):
        with pytest.raises(ContractError, match="between one and five"):
            build_reference_run(
                TASK,
                ROOT,
                configuration,
                pair_index,
                environment_provider_version="29.5.2",
            )


def test_reference_run_rejects_adapter_version_drift() -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )
    changed = configuration.model_copy(
        update={"adapter": configuration.adapter.model_copy(update={"version": "0.15.0"})}
    )

    with pytest.raises(ContractError, match="adapter version"):
        build_reference_run(
            TASK,
            ROOT,
            changed,
            1,
            environment_provider_version="29.5.2",
        )


def test_reference_configuration_rejects_configured_cursor_version() -> None:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )
    payload = configuration.model_dump(mode="json")
    payload["settings"] = {"version": configuration.harness.version}

    with pytest.raises(ValueError, match="must be observed"):
        ReferenceConfiguration.model_validate(payload)


def test_reference_image_discovery_requires_a_pinned_task_image(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="no Docker image pins"):
        _image_references(tmp_path)
