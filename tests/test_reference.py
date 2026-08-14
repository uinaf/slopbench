from __future__ import annotations

from pathlib import Path

import pytest

from slopbench.contracts import AgentReport, ResultBundle, RunManifest
from slopbench.hashing import ContractError, load_model, sha256_file, validate_task, write_model
from slopbench.reference import (
    _image_references,
    build_reference_evaluation,
    build_reference_run,
    write_reference_runs,
)
from slopbench.release import (
    EvaluationPurpose,
    ProfileDefinition,
    ReferenceConfiguration,
    TaskSetManifest,
    compute_evaluation,
)
from slopbench.runner import _validate_run_binding
from tests.helpers import parse_json, report_payload, result_payload

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks" / "diagnosis" / "query-cache-key"


def evaluation_fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    ReferenceConfiguration,
    TaskSetManifest,
    ProfileDefinition,
    list[Path],
]:
    configuration = load_model(
        ROOT / "reference-configurations" / "cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )
    dataset = load_model(ROOT / "datasets" / "slopbench-swe-v1-dev.json", TaskSetManifest)
    entry = next(
        item for item in dataset.tasks if item.task_id == "slopbench/diagnosis/query-cache-key"
    )
    task_set = TaskSetManifest(
        schema_version="slopbench.task-set.v1",
        task_set_id="reference-evaluation-fixture",
        version="0.1.0",
        visibility=dataset.visibility,
        tasks=[entry],
    )
    profile = load_model(ROOT / "profiles" / "balanced.json", ProfileDefinition)
    bundle_root = tmp_path / "reference"
    manifest_dir = bundle_root / "manifests"
    result_dir = bundle_root / "bundles"
    manifests = write_reference_runs(
        [TASK],
        ROOT,
        configuration,
        EvaluationPurpose.COMPARISON,
        manifest_dir,
        environment_provider_version="29.5.2",
    )
    for manifest_path in manifests:
        run = load_model(manifest_path, RunManifest)
        result_bundle = result_dir / run.run_id
        report_data = report_payload()
        report_data["task_digest"] = run.task.task_digest
        report = parse_json(AgentReport, report_data)
        report_relative = f"harbor/{run.run_id}/steps/implement/artifacts/app/slopbench-report.json"
        report_path = result_bundle / report_relative
        write_model(report_path, report)
        report_sha256 = sha256_file(report_path)
        payload = result_payload()
        payload.update(
            {
                "run_id": run.run_id,
                "task_digest": run.task.task_digest,
                "run_manifest_sha256": sha256_file(manifest_path),
                "receipt": {
                    "present": True,
                    "valid": True,
                    "sha256": report_sha256,
                    "errors": [],
                },
                "artifacts": [
                    {
                        "path": report_relative,
                        "sha256": report_sha256,
                    }
                ],
            }
        )
        payload["harbor"].update(
            {
                "version": run.runtime.harbor_version,
                "task_checksum": run.task.harbor_task_checksum,
                "agent": {
                    "name": run.agent.harness,
                    "version": run.agent.harness_version,
                    "model": (
                        None if run.agent.model is None else run.agent.model.model_dump(mode="json")
                    ),
                },
            }
        )
        write_model(result_bundle / "result.json", parse_json(ResultBundle, payload))
    return bundle_root, result_dir, configuration, task_set, profile, manifests


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


def test_reference_evaluation_binds_and_computes_five_trial_result(tmp_path: Path) -> None:
    bundle_root, result_dir, configuration, task_set, profile, manifests = evaluation_fixture(
        tmp_path
    )
    first_run = load_model(manifests[0], RunManifest)
    first_result_path = result_dir / first_run.run_id / "result.json"
    first_result = load_model(first_result_path, ResultBundle)
    final_report = next(
        artifact
        for artifact in first_result.artifacts
        if artifact.sha256 == first_result.receipt.sha256
    )
    duplicate_relative = (
        f"harbor/{first_run.run_id}/steps/prepare/artifacts/app/slopbench-report.json"
    )
    duplicate_path = result_dir / first_run.run_id / duplicate_relative
    write_model(
        duplicate_path,
        load_model(result_dir / first_run.run_id / final_report.path, AgentReport),
    )
    first_result_data = first_result.model_dump(mode="json")
    first_result_data["artifacts"].append(
        {"path": duplicate_relative, "sha256": sha256_file(duplicate_path)}
    )
    write_model(first_result_path, parse_json(ResultBundle, first_result_data))

    evaluation = build_reference_evaluation(
        bundle_root / "manifests",
        result_dir,
        bundle_root,
        configuration,
        task_set,
        profile,
        EvaluationPurpose.COMPARISON,
        "reference-evaluation-fixture-balanced",
    )

    assert [run.pair_index for run in evaluation.runs] == [1, 2, 3, 4, 5]
    assert all(run.report_path is not None for run in evaluation.runs)
    assert all(run.run_manifest_path.startswith("manifests/") for run in evaluation.runs)
    assert evaluation.runs[0].report_path is not None
    assert "/steps/implement/" in evaluation.runs[0].report_path
    evaluation_path = tmp_path / "evaluation.json"
    task_set_path = tmp_path / "task-set.json"
    write_model(evaluation_path, evaluation)
    write_model(task_set_path, task_set)
    result = compute_evaluation(
        evaluation_path,
        task_set_path,
        ROOT / "profiles" / "balanced.json",
        ROOT,
        bundle_root,
    )
    assert result.metrics.trial_count == len(manifests) == 5
    assert result.metrics.reliability_bps == 10_000


def test_reference_evaluation_rejects_missing_task_coverage(tmp_path: Path) -> None:
    bundle_root, result_dir, configuration, _, profile, _ = evaluation_fixture(tmp_path)
    full_task_set = load_model(ROOT / "datasets" / "slopbench-swe-v1-dev.json", TaskSetManifest)

    with pytest.raises(ContractError, match="do not cover task set"):
        build_reference_evaluation(
            bundle_root / "manifests",
            result_dir,
            bundle_root,
            configuration,
            full_task_set,
            profile,
            EvaluationPurpose.COMPARISON,
            "missing-task-coverage",
        )


def test_reference_evaluation_rejects_receipt_artifact_drift(tmp_path: Path) -> None:
    bundle_root, result_dir, configuration, task_set, profile, manifests = evaluation_fixture(
        tmp_path
    )
    run = load_model(manifests[0], RunManifest)
    report_path = (
        result_dir
        / run.run_id
        / f"harbor/{run.run_id}/steps/implement/artifacts/app/slopbench-report.json"
    )
    report_path.write_text("{}\n")

    with pytest.raises(ContractError, match="agent report digest mismatch"):
        build_reference_evaluation(
            bundle_root / "manifests",
            result_dir,
            bundle_root,
            configuration,
            task_set,
            profile,
            EvaluationPurpose.COMPARISON,
            "receipt-artifact-drift",
        )
