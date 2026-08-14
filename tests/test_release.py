from __future__ import annotations

import base64
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from slopbench import cli
from slopbench.contracts import (
    CapabilityCategory,
    FailureClassification,
    GateName,
    OutcomeStatus,
    ResultBundle,
    RunManifest,
    TaskBinding,
    ToolPin,
)
from slopbench.hashing import (
    ContractError,
    load_model,
    sha256_bytes,
    sha256_file,
    validate_task,
    write_model,
)
from slopbench.release import (
    AggregateMetrics,
    AttestationStatement,
    BridgeReport,
    BudgetStatus,
    ComponentPin,
    CoverageSnapshot,
    EvaluationManifest,
    EvaluationPurpose,
    EvaluationResult,
    EvaluationRunBinding,
    ProfileBudget,
    ProfileDefinition,
    RawResultVector,
    RawTrialOutcome,
    ReferenceAttestation,
    ReferenceConfiguration,
    ResultOrigin,
    RetiredPublication,
    RetirementManifest,
    RetirementReason,
    RetirementRecord,
    SshSignature,
    TaskSetEntry,
    TaskSetManifest,
    TaskSetVisibility,
    _aggregate,
    build_attestation_statement,
    build_bridge_report,
    build_held_out_disclosure,
    compute_evaluation,
    contract_digest,
    profile_binding,
    sign_reference_attestation,
    task_set_binding,
    validate_retirement,
    validate_retirement_models,
    validate_task_set,
    verify_reference_attestation,
)
from tests.helpers import (
    GIT_REVISION,
    REVISION,
    parse_json,
    report_payload,
    result_payload,
    run_payload,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "slopbench-swe-v1-dev.json"


def digest(label: str) -> str:
    return sha256_bytes(label.encode())


def rebuild[ModelT](model: ModelT, **updates: object) -> ModelT:
    model_type = type(model)
    payload = model.model_dump(mode="json")
    payload.update(updates)
    return model_type.model_validate_json(json.dumps(payload))


def profile(name: str = "balanced") -> ProfileDefinition:
    return load_model(ROOT / "profiles" / f"{name}.json", ProfileDefinition)


def one_task_set(
    *,
    entry_index: int = 0,
    visibility: TaskSetVisibility = TaskSetVisibility.PUBLIC,
    version: str = "0.1.0",
    task_set_id: str = "fixture-suite",
) -> TaskSetManifest:
    dataset, _ = validate_task_set(DATASET, ROOT)
    return TaskSetManifest(
        task_set_id=task_set_id,
        version=version,
        visibility=visibility,
        tasks=[dataset.tasks[entry_index]],
    )


def reference_configuration() -> ReferenceConfiguration:
    manifest = parse_json(RunManifest, run_payload())
    return ReferenceConfiguration(
        configuration_id="oracle-reference",
        version="0.1.0",
        harness=ComponentPin(name=manifest.agent.harness, version=manifest.agent.harness_version),
        adapter=ToolPin(name="harbor-oracle", version="0.16.1", settings={}),
        model=manifest.agent.model,
        effort_tier=manifest.agent.effort_tier,
        settings=manifest.agent.settings,
        environment={},
        tools=manifest.agent.tools,
        credential_env=[],
    )


def raw_trial(
    entry: TaskSetEntry,
    pair_index: int,
    configuration: ReferenceConfiguration,
    *,
    fail_requested: bool = False,
    cost_usd: float | None = 0.25,
    duration_seconds: float | None = 2.5,
) -> RawTrialOutcome:
    run_id = f"{entry.task_id.replace('/', '-')}-{pair_index}"
    run = parse_json(RunManifest, run_payload())
    harbor_task_checksum = digest(f"harbor-task-{entry.task_id}")
    contract_path = ROOT / entry.contract_path
    task_binding = TaskBinding(
        contract_path=entry.contract_path,
        contract_sha256=(
            sha256_file(contract_path)
            if contract_path.is_file()
            else digest(f"contract-{entry.task_id}")
        ),
        task_digest=entry.task_digest,
        task_id=entry.task_id,
        task_version=entry.task_version,
        harbor_task_checksum=harbor_task_checksum,
    )
    agent = run.agent.model_copy(
        update={
            "harness": configuration.harness.name,
            "harness_version": configuration.harness.version,
            "adapter": configuration.adapter,
            "model": configuration.model,
            "effort_tier": configuration.effort_tier,
            "settings": configuration.settings,
            "environment": configuration.environment,
            "tools": configuration.tools,
            "credential_env": configuration.credential_env,
        }
    )
    result_data = result_payload(
        classification=("valid_agent_failure" if fail_requested else "valid_pass")
    )
    result_data.update(
        run_id=run_id,
        task_digest=entry.task_digest,
        run_manifest_sha256=digest(f"manifest-{run_id}"),
        attempt=1,
    )
    result_data["receipt"] = {
        "present": True,
        "valid": True,
        "sha256": digest(f"report-{run_id}"),
        "errors": [],
    }
    result_data["usage"] = {
        "input_tokens": 100,
        "cache_tokens": 20,
        "output_tokens": 30,
        "cost_usd": cost_usd,
    }
    result_data["timing"] = {
        "started_at": "2026-08-14T00:00:00Z",
        "finished_at": "2026-08-14T00:00:02Z",
        "duration_seconds": duration_seconds,
    }
    result_data["harbor"] = {
        "version": "0.16.1",
        "task_checksum": harbor_task_checksum,
        "result_sha256": digest(f"harbor-result-{run_id}"),
        "config_sha256": digest(f"harbor-config-{run_id}"),
        "trajectory_sha256": digest(f"trajectory-{run_id}"),
    }
    result_data["artifacts"] = [
        {"path": f"traces/{run_id}.json", "sha256": digest(f"artifact-{run_id}")}
    ]
    for outcome in result_data["outcomes"]:
        outcome["status"] = (
            "not_applicable" if outcome["gate"] == GateName.SAFETY_TYPE_ESCAPES.value else "passed"
        )
        if fail_requested and outcome["gate"] == GateName.REQUESTED_BEHAVIOR.value:
            outcome["status"] = "failed"
    result = parse_json(ResultBundle, result_data)
    outcomes = {outcome.gate: outcome for outcome in result.outcomes}
    ordered_outcomes = [outcomes[gate] for gate in GateName]
    failed = [gate for gate in GateName if outcomes[gate].status == OutcomeStatus.FAILED]
    return RawTrialOutcome(
        task_id=entry.task_id,
        task_digest=entry.task_digest,
        pair_index=pair_index,
        run_id=run_id,
        task=task_binding,
        run_manifest_sha256=result.run_manifest_sha256,
        result_sha256=digest(f"result-{run_id}"),
        classification=result.classification,
        failure_reason=result.failure_reason,
        agent=agent,
        runtime=run.runtime,
        limits=run.limits,
        trial=run.trial.model_copy(update={"id": run_id, "seed": pair_index}),
        outcomes=ordered_outcomes,
        strict_gate_failures=failed,
        uncertainty=[{"code": "held-out", "detail": "Private checks ran after the agent."}],
        report_sha256=result.receipt.sha256,
        receipt=result.receipt,
        usage=result.usage,
        timing=result.timing,
        harbor=result.harbor,
        artifacts=result.artifacts,
    )


def direct_result(
    task_set: TaskSetManifest,
    scoring_profile: ProfileDefinition,
    *,
    purpose: EvaluationPurpose = EvaluationPurpose.COMPARISON,
    origin: ResultOrigin = ResultOrigin.MAINTAINER,
    configuration: ReferenceConfiguration | None = None,
    fail_requested: bool = False,
) -> EvaluationResult:
    configuration = configuration or reference_configuration()
    trial_count = {
        EvaluationPurpose.SMOKE: 1,
        EvaluationPurpose.CALIBRATION: 3,
        EvaluationPurpose.COMPARISON: 5,
    }[purpose]
    trials = [
        raw_trial(entry, pair_index, configuration, fail_requested=fail_requested)
        for entry in sorted(task_set.tasks, key=lambda task: task.task_id)
        for pair_index in range(1, trial_count + 1)
    ]
    vector = RawResultVector(trials=trials)
    return EvaluationResult(
        evaluation_id=f"evaluation-{task_set.version.replace('.', '-')}",
        task_set=task_set_binding(task_set),
        task_set_manifest=task_set,
        profile=profile_binding(scoring_profile),
        profile_definition=scoring_profile,
        evaluation_manifest_sha256=digest(f"evaluation-{task_set.version}"),
        purpose=purpose,
        configuration=configuration,
        result_origin=origin,
        result_vector_sha256=contract_digest("slopbench.result-vector.v1", vector),
        trials=trials,
        metrics=_aggregate(trials, scoring_profile),
    )


def materialize_evaluation(
    tmp_path: Path,
    *,
    scoring_profile: ProfileDefinition | None = None,
    purpose: EvaluationPurpose = EvaluationPurpose.SMOKE,
    origin: ResultOrigin = ResultOrigin.EXTERNAL,
    fail_requested: bool = False,
) -> dict[str, Any]:
    task_set = one_task_set()
    scoring_profile = scoring_profile or profile()
    task_set_path = tmp_path / "task-set.json"
    profile_path = tmp_path / "profile.json"
    write_model(task_set_path, task_set)
    write_model(profile_path, scoring_profile)
    configuration = reference_configuration()
    trial_count = {
        EvaluationPurpose.SMOKE: 1,
        EvaluationPurpose.CALIBRATION: 3,
        EvaluationPurpose.COMPARISON: 5,
    }[purpose]
    bindings: list[EvaluationRunBinding] = []
    entry = task_set.tasks[0]
    contract_path = ROOT / entry.contract_path
    task, contract_sha256, task_digest = validate_task(contract_path.parent)
    assert task_digest == entry.task_digest
    instruction_layers = [
        {
            "name": phase.name,
            "path": (contract_path.parent / phase.instruction_path).relative_to(ROOT).as_posix(),
            "sha256": sha256_file(contract_path.parent / phase.instruction_path),
        }
        for phase in task.phases
    ]
    harbor_task_checksum = digest(f"harbor-task-{entry.task_id}")
    for pair_index in range(1, trial_count + 1):
        run_id = f"fixture-run-{pair_index}"
        run_data = run_payload()
        run_data.update(run_id=run_id)
        run_data["task"] = {
            "contract_path": entry.contract_path,
            "contract_sha256": contract_sha256,
            "task_digest": entry.task_digest,
            "task_id": entry.task_id,
            "task_version": entry.task_version,
            "harbor_task_checksum": harbor_task_checksum,
        }
        run_data["agent"].update(
            {
                "harness": configuration.harness.name,
                "harness_version": configuration.harness.version,
                "adapter": configuration.adapter.model_dump(mode="json"),
                "model": (
                    None
                    if configuration.model is None
                    else configuration.model.model_dump(mode="json")
                ),
                "effort_tier": configuration.effort_tier,
                "settings": configuration.settings,
                "environment": configuration.environment,
                "tools": [tool.model_dump(mode="json") for tool in configuration.tools],
                "instruction_layers": instruction_layers,
                "credential_env": configuration.credential_env,
            }
        )
        run_data["trial"] = {"id": run_id, "attempt": 1, "seed": pair_index}
        run = parse_json(RunManifest, run_data)
        trial_dir = tmp_path / "bundles" / str(pair_index)
        run_path = trial_dir / "run.json"
        report_path = trial_dir / "report.json"
        result_path = trial_dir / "result.json"
        write_model(run_path, run)

        report_data = report_payload(public_passed=not fail_requested)
        report_data.update(
            task_digest=entry.task_digest,
            base_revision=GIT_REVISION,
            final_revision=REVISION,
        )
        write_json(report_path, report_data)
        report_sha = sha256_file(report_path)

        result_data = result_payload(
            classification=("valid_agent_failure" if fail_requested else "valid_pass")
        )
        result_data.update(
            run_id=run_id,
            task_digest=entry.task_digest,
            run_manifest_sha256=sha256_file(run_path),
            attempt=1,
        )
        result_data["receipt"] = {
            "present": True,
            "valid": True,
            "sha256": report_sha,
            "errors": [],
        }
        result_data["usage"] = {
            "input_tokens": 100,
            "cache_tokens": 25,
            "output_tokens": 50,
            "cost_usd": 0.125,
        }
        result_data["timing"] = {
            "started_at": "2026-08-14T00:00:00Z",
            "finished_at": "2026-08-14T00:00:03Z",
            "duration_seconds": 3.0,
        }
        result_data["harbor"] = {
            "version": run.runtime.harbor_version,
            "task_checksum": run.task.harbor_task_checksum,
            "result_sha256": digest(f"harbor-result-{pair_index}"),
            "config_sha256": digest(f"harbor-config-{pair_index}"),
            "trajectory_sha256": digest(f"trajectory-{pair_index}"),
        }
        result_data["artifacts"] = [
            {"path": "harbor/trajectory.json", "sha256": digest(f"trace-{pair_index}")}
        ]
        for outcome in result_data["outcomes"]:
            outcome["status"] = (
                "not_applicable"
                if outcome["gate"] == GateName.SAFETY_TYPE_ESCAPES.value
                else "passed"
            )
            if fail_requested and outcome["gate"] == GateName.REQUESTED_BEHAVIOR.value:
                outcome["status"] = "failed"
        write_json(result_path, result_data)
        bindings.append(
            EvaluationRunBinding(
                task_id=entry.task_id,
                task_digest=entry.task_digest,
                pair_index=pair_index,
                run_manifest_path=run_path.relative_to(tmp_path).as_posix(),
                run_manifest_sha256=sha256_file(run_path),
                result_path=result_path.relative_to(tmp_path).as_posix(),
                result_sha256=sha256_file(result_path),
                report_path=report_path.relative_to(tmp_path).as_posix(),
                report_sha256=report_sha,
            )
        )
    evaluation = EvaluationManifest(
        evaluation_id="fixture-evaluation",
        task_set=task_set_binding(task_set),
        profile=profile_binding(scoring_profile),
        purpose=purpose,
        configuration=configuration,
        runs=bindings,
    )
    evaluation_path = tmp_path / "evaluation.json"
    result_path = tmp_path / "evaluation-result.json"
    write_model(evaluation_path, evaluation)
    result = compute_evaluation(
        evaluation_path,
        task_set_path,
        profile_path,
        ROOT,
        tmp_path,
        result_origin=origin,
    )
    write_model(result_path, result)
    return {
        "task_set": task_set,
        "task_set_path": task_set_path,
        "profile": scoring_profile,
        "profile_path": profile_path,
        "evaluation": evaluation,
        "evaluation_path": evaluation_path,
        "result": result,
        "result_path": result_path,
        "bindings": bindings,
    }


def test_public_dataset_and_named_profiles_are_versioned_and_balanced() -> None:
    task_set, task_set_sha = validate_task_set(DATASET, ROOT)

    assert task_set.task_set_id == "slopbench-swe-v1-dev"
    assert task_set.version.startswith("0.")
    assert task_set.visibility == TaskSetVisibility.PUBLIC
    assert task_set_sha == task_set_binding(task_set).sha256
    assert len(task_set.tasks) == 12
    assert Counter(task.category for task in task_set.tasks) == {
        CapabilityCategory.DIAGNOSIS_REPAIR: 2,
        CapabilityCategory.FEATURE: 2,
        CapabilityCategory.RESTRAINT: 2,
        CapabilityCategory.COMPOSITION_DOMAIN_EVOLUTION: 2,
        CapabilityCategory.STATE_EFFECTS: 2,
        CapabilityCategory.CODE_REVIEW: 2,
    }

    profiles = {
        path.stem: load_model(path, ProfileDefinition)
        for path in sorted((ROOT / "profiles").glob("*.json"))
    }
    assert set(profiles) == {
        "altay",
        "balanced",
        "cost-aware",
        "fast-feedback",
        "reliability-first",
    }
    assert profiles["altay"].subjective is True
    assert profiles["altay"].source_note is not None
    assert all(item.version.startswith("0.") for item in profiles.values())
    assert profiles["cost-aware"].budget is not None
    assert profiles["cost-aware"].budget.max_mean_cost_usd == 1.0
    assert profiles["fast-feedback"].budget is not None
    assert profiles["fast-feedback"].budget.max_mean_duration_seconds == 300.0


@pytest.mark.parametrize(
    ("purpose", "count"),
    [
        (EvaluationPurpose.SMOKE, 1),
        (EvaluationPurpose.CALIBRATION, 3),
        (EvaluationPurpose.COMPARISON, 5),
    ],
)
def test_evaluation_manifest_enforces_trial_policy(purpose: EvaluationPurpose, count: int) -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    runs = [
        EvaluationRunBinding(
            task_id=task_set.tasks[0].task_id,
            task_digest=task_set.tasks[0].task_digest,
            pair_index=index,
            run_manifest_path=f"runs/{index}/run.json",
            run_manifest_sha256=digest(f"run-{index}"),
            result_path=f"runs/{index}/result.json",
            result_sha256=digest(f"result-{index}"),
            report_path=f"runs/{index}/report.json",
            report_sha256=digest(f"report-{index}"),
        )
        for index in range(1, count + 1)
    ]

    manifest = EvaluationManifest(
        evaluation_id="trial-policy",
        task_set=task_set_binding(task_set),
        profile=profile_binding(scoring_profile),
        purpose=purpose,
        configuration=reference_configuration(),
        runs=runs,
    )

    assert len(manifest.runs) == count
    with pytest.raises(ValidationError, match="requires"):
        EvaluationManifest.model_validate_json(
            json.dumps(
                {
                    **manifest.model_dump(mode="json"),
                    "runs": [
                        run.model_dump(mode="json") for run in (runs[:-1] or [*runs, runs[0]])
                    ],
                }
            )
        )


def test_evaluation_bindings_reject_duplicates_and_partial_report_bindings() -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    binding = EvaluationRunBinding(
        task_id=task_set.tasks[0].task_id,
        task_digest=task_set.tasks[0].task_digest,
        pair_index=1,
        run_manifest_path="run.json",
        run_manifest_sha256=digest("run"),
        result_path="result.json",
        result_sha256=digest("result"),
    )
    with pytest.raises(ValidationError, match="declared together"):
        EvaluationRunBinding.model_validate_json(
            json.dumps({**binding.model_dump(mode="json"), "report_path": "report.json"})
        )

    payload = {
        "evaluation_id": "duplicates",
        "task_set": task_set_binding(task_set).model_dump(mode="json"),
        "profile": profile_binding(scoring_profile).model_dump(mode="json"),
        "purpose": "calibration",
        "configuration": reference_configuration().model_dump(mode="json"),
        "runs": [],
    }
    for index in range(1, 4):
        item = binding.model_copy(
            update={
                "pair_index": index,
                "run_manifest_path": f"run-{index}.json",
                "result_path": f"result-{index}.json",
            }
        ).model_dump(mode="json")
        payload["runs"].append(item)
    with pytest.raises(ValidationError, match="digests must be unique"):
        EvaluationManifest.model_validate_json(json.dumps(payload))


def test_compute_evaluation_is_deterministic_and_retains_full_raw_evidence(
    tmp_path: Path,
) -> None:
    fixture = materialize_evaluation(tmp_path)
    first = fixture["result"]
    second = compute_evaluation(
        fixture["evaluation_path"],
        fixture["task_set_path"],
        fixture["profile_path"],
        ROOT,
        tmp_path,
    )

    assert first == second
    assert first.official is False
    assert first.result_origin == ResultOrigin.EXTERNAL
    assert first.metrics.trial_count == 1
    assert first.metrics.quality_bps == 10_000
    assert first.metrics.reliability_bps == 10_000
    trial = first.trials[0]
    assert trial.agent.harness == "oracle"
    assert trial.agent.adapter == fixture["evaluation"].configuration.adapter
    assert trial.agent.credential_env == fixture["evaluation"].configuration.credential_env
    assert trial.task.contract_path == fixture["task_set"].tasks[0].contract_path
    assert len(trial.agent.instruction_layers) == 2
    assert trial.runtime.images[0].reference.endswith("f" * 64)
    assert trial.trial.seed == 1
    assert trial.report_sha256 == trial.receipt.sha256
    assert trial.uncertainty[0].code == "hidden-verifier"
    assert trial.usage.cost_usd == 0.125
    assert trial.timing.duration_seconds == 3.0
    assert trial.harbor.trajectory_sha256 == digest("trajectory-1")
    assert trial.artifacts[0].path == "harbor/trajectory.json"
    assert trial.strict_gate_failures == []
    assert first.result_vector_sha256 == contract_digest(
        "slopbench.result-vector.v1", RawResultVector(trials=first.trials)
    )


def test_evaluate_cli_recomputes_an_immutable_bundle(tmp_path: Path) -> None:
    fixture = materialize_evaluation(tmp_path)
    output = tmp_path / "cli-result.json"

    code = cli.main(
        [
            "evaluate",
            "--manifest",
            str(fixture["evaluation_path"]),
            "--task-set",
            str(fixture["task_set_path"]),
            "--profile",
            str(fixture["profile_path"]),
            "--project-root",
            str(ROOT),
            "--bundle-root",
            str(tmp_path),
            "--origin",
            "external",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert load_model(output, EvaluationResult) == fixture["result"]


def test_profile_version_changes_do_not_rewrite_raw_outcomes(tmp_path: Path) -> None:
    original = profile()
    fixture = materialize_evaluation(tmp_path, scoring_profile=original, fail_requested=True)
    changed_payload = original.model_dump(mode="json")
    changed_payload.update(version="0.2.0", strict_gates=[GateName.AUTHORITY.value])
    changed_payload["gate_weights"][GateName.REQUESTED_BEHAVIOR.value] = 1
    changed_payload["gate_weights"][GateName.REGRESSIONS.value] = 100
    changed = ProfileDefinition.model_validate_json(json.dumps(changed_payload))
    changed_path = tmp_path / "changed-profile.json"
    changed_evaluation_path = tmp_path / "changed-evaluation.json"
    write_model(changed_path, changed)
    changed_evaluation = rebuild(
        fixture["evaluation"], profile=profile_binding(changed).model_dump(mode="json")
    )
    write_model(changed_evaluation_path, changed_evaluation)

    result = compute_evaluation(
        changed_evaluation_path,
        fixture["task_set_path"],
        changed_path,
        ROOT,
        tmp_path,
    )

    assert result.profile.version == "0.2.0"
    assert result.result_vector_sha256 == fixture["result"].result_vector_sha256
    assert result.trials == fixture["result"].trials
    assert result.metrics.quality_bps != fixture["result"].metrics.quality_bps


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("run", "run manifest digest mismatch"),
        ("result", "raw result digest mismatch"),
        ("report", "agent report digest mismatch"),
    ],
)
def test_compute_evaluation_rejects_changed_bundle_files(
    tmp_path: Path, target: str, message: str
) -> None:
    fixture = materialize_evaluation(tmp_path)
    binding = fixture["bindings"][0]
    relative = {
        "run": binding.run_manifest_path,
        "result": binding.result_path,
        "report": binding.report_path,
    }[target]
    assert relative is not None
    path = tmp_path / relative
    path.write_text(path.read_text() + " ")

    with pytest.raises(ContractError, match=message):
        compute_evaluation(
            fixture["evaluation_path"],
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )


@pytest.mark.parametrize(
    ("gate", "status"),
    [
        (GateName.REQUESTED_BEHAVIOR, OutcomeStatus.NOT_APPLICABLE),
        (GateName.SAFETY_TYPE_ESCAPES, OutcomeStatus.PASSED),
    ],
)
def test_compute_evaluation_rejects_gate_applicability_drift(
    tmp_path: Path, gate: GateName, status: OutcomeStatus
) -> None:
    fixture = materialize_evaluation(tmp_path)
    binding = fixture["bindings"][0]
    result_path = tmp_path / binding.result_path
    payload = json.loads(result_path.read_text())
    for outcome in payload["outcomes"]:
        if outcome["gate"] == gate.value:
            outcome["status"] = status.value
    write_json(result_path, payload)
    changed_binding = binding.model_copy(update={"result_sha256": sha256_file(result_path)})
    changed_evaluation = fixture["evaluation"].model_copy(update={"runs": [changed_binding]})
    write_model(fixture["evaluation_path"], changed_evaluation)

    with pytest.raises(ContractError, match="gate applicability mismatch"):
        compute_evaluation(
            fixture["evaluation_path"],
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )


def test_compute_evaluation_rejects_missing_attributable_harbor_checksum(
    tmp_path: Path,
) -> None:
    fixture = materialize_evaluation(tmp_path)
    binding = fixture["bindings"][0]
    result_path = tmp_path / binding.result_path
    payload = json.loads(result_path.read_text())
    payload["harbor"]["task_checksum"] = None
    write_json(result_path, payload)
    changed_binding = binding.model_copy(update={"result_sha256": sha256_file(result_path)})
    changed_evaluation = fixture["evaluation"].model_copy(update={"runs": [changed_binding]})
    write_model(fixture["evaluation_path"], changed_evaluation)

    with pytest.raises(ContractError, match="raw run/result binding mismatch"):
        compute_evaluation(
            fixture["evaluation_path"],
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )


def test_compute_evaluation_rejects_binding_and_configuration_mismatches(
    tmp_path: Path,
) -> None:
    fixture = materialize_evaluation(tmp_path)
    evaluation = fixture["evaluation"]
    bad_profile_binding = evaluation.profile.model_copy(update={"sha256": digest("wrong")})
    write_model(
        tmp_path / "bad-profile-binding.json",
        evaluation.model_copy(update={"profile": bad_profile_binding}),
    )
    with pytest.raises(ContractError, match="profile binding mismatch"):
        compute_evaluation(
            tmp_path / "bad-profile-binding.json",
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )

    changed_configuration = evaluation.configuration.model_copy(
        update={"environment": {"SLOPBENCH_VARIANT": "different"}}
    )
    write_model(
        tmp_path / "bad-configuration.json",
        evaluation.model_copy(update={"configuration": changed_configuration}),
    )
    with pytest.raises(ContractError, match="reference configuration mismatch"):
        compute_evaluation(
            tmp_path / "bad-configuration.json",
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )

    changed_adapter = evaluation.configuration.adapter.model_copy(update={"version": "0.16.2"})
    adapter_configuration = evaluation.configuration.model_copy(update={"adapter": changed_adapter})
    write_model(
        tmp_path / "bad-adapter.json",
        evaluation.model_copy(update={"configuration": adapter_configuration}),
    )
    with pytest.raises(ContractError, match="adapter"):
        compute_evaluation(
            tmp_path / "bad-adapter.json",
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )

    task_set = fixture["task_set"]
    wrong_binding = task_set_binding(task_set).model_copy(update={"sha256": digest("wrong-set")})
    write_model(
        tmp_path / "bad-task-set-binding.json",
        evaluation.model_copy(update={"task_set": wrong_binding}),
    )
    with pytest.raises(ContractError, match="task-set binding mismatch"):
        compute_evaluation(
            tmp_path / "bad-task-set-binding.json",
            fixture["task_set_path"],
            fixture["profile_path"],
            ROOT,
            tmp_path,
        )


def test_raw_result_models_reject_vector_and_identity_tampering() -> None:
    task_set = one_task_set()
    result = direct_result(task_set, profile(), purpose=EvaluationPurpose.SMOKE)
    payload = result.model_dump(mode="json")
    payload["result_vector_sha256"] = digest("tampered")
    with pytest.raises(ValidationError, match="result vector digest mismatch"):
        EvaluationResult.model_validate_json(json.dumps(payload))

    trial_payload = result.trials[0].model_dump(mode="json")
    trial_payload["run_id"] = "different"
    with pytest.raises(ValidationError, match=r"run_id and trial\.id"):
        RawTrialOutcome.model_validate_json(json.dumps(trial_payload))
    trial_payload = result.trials[0].model_dump(mode="json")
    trial_payload["strict_gate_failures"] = [GateName.AUTHORITY.value]
    with pytest.raises(ValidationError, match="strict_gate_failures"):
        RawTrialOutcome.model_validate_json(json.dumps(trial_payload))
    trial_payload = result.trials[0].model_dump(mode="json")
    trial_payload["report_sha256"] = None
    with pytest.raises(ValidationError, match="receipt presence"):
        RawTrialOutcome.model_validate_json(json.dumps(trial_payload))

    metrics_payload = result.model_dump(mode="json")
    metrics_payload["metrics"]["quality_bps"] = 0
    with pytest.raises(ValidationError, match="metrics do not recompute"):
        EvaluationResult.model_validate_json(json.dumps(metrics_payload))

    changed_outcomes = [
        outcome.model_copy(update={"status": OutcomeStatus.NOT_APPLICABLE})
        if outcome.gate == GateName.REQUESTED_BEHAVIOR
        else outcome
        for outcome in result.trials[0].outcomes
    ]
    changed_trial = result.trials[0].model_copy(update={"outcomes": changed_outcomes})
    changed_trials = [changed_trial]
    changed_result = result.model_copy(
        update={
            "trials": changed_trials,
            "result_vector_sha256": contract_digest(
                "slopbench.result-vector.v1", RawResultVector(trials=changed_trials)
            ),
            "metrics": _aggregate(changed_trials, result.profile_definition),
        }
    )
    with pytest.raises(ValidationError, match="gate applicability mismatch"):
        EvaluationResult.model_validate_json(json.dumps(changed_result.model_dump(mode="json")))


def test_profile_budgets_are_separate_eligibility_signals() -> None:
    task_set = one_task_set()
    configuration = reference_configuration()
    entry = task_set.tasks[0]
    expensive = raw_trial(entry, 1, configuration, cost_usd=2.0)
    missing = raw_trial(entry, 1, configuration, cost_usd=None)
    slow = raw_trial(entry, 1, configuration, duration_seconds=400.0)

    balanced = _aggregate([expensive], profile("balanced"))
    cost_failed = _aggregate([expensive], profile("cost-aware"))
    cost_incomplete = _aggregate([missing], profile("cost-aware"))
    time_failed = _aggregate([slow], profile("fast-feedback"))

    assert balanced.budget_status == BudgetStatus.NOT_DECLARED
    assert cost_failed.budget_status == BudgetStatus.FAILED
    assert cost_failed.budget_failures == ["mean_cost_usd"]
    assert cost_incomplete.budget_status == BudgetStatus.INCOMPLETE
    assert cost_incomplete.missing_cost_trials == 1
    assert time_failed.budget_status == BudgetStatus.FAILED
    assert time_failed.budget_failures == ["mean_duration_seconds"]
    assert cost_failed.selection_bps == balanced.selection_bps


def test_active_held_out_disclosure_is_whitelist_only() -> None:
    task_set = one_task_set(
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE, task_set_id="private-suite"
    )
    scoring_profile = profile()
    result = direct_result(task_set, scoring_profile, fail_requested=True)

    disclosure = build_held_out_disclosure(task_set, scoring_profile, result)
    payload = disclosure.model_dump(mode="json")
    rendered = json.dumps(payload, sort_keys=True)

    assert set(payload) == {
        "schema_version",
        "task_set",
        "category_counts",
        "capability_requirements",
        "scoring_contract",
        "aggregate",
    }
    assert disclosure.task_set.sha256 == task_set_binding(task_set).sha256
    assert disclosure.aggregate.failure_counts[FailureClassification.VALID_AGENT_FAILURE] == 5
    for forbidden in (
        "contract_path",
        "task_id",
        "instruction",
        "fixture",
        "patch",
        "trajectory",
        "trace",
        "check_ids",
        "network_allowed_hosts",
        "environment",
    ):
        assert forbidden not in rendered


def test_disclosure_rejects_non_publication_and_tampered_inputs() -> None:
    scoring_profile = profile()
    public = one_task_set()
    held_out = one_task_set(visibility=TaskSetVisibility.HELD_OUT_ACTIVE)
    comparison = direct_result(held_out, scoring_profile)
    smoke = direct_result(held_out, scoring_profile, purpose=EvaluationPurpose.SMOKE)

    with pytest.raises(ContractError, match="active held-out"):
        build_held_out_disclosure(public, scoring_profile, comparison)
    with pytest.raises(ContractError, match="five-trial"):
        build_held_out_disclosure(held_out, scoring_profile, smoke)
    with pytest.raises(ContractError, match="scoring profile"):
        build_held_out_disclosure(held_out, profile("altay"), comparison)
    bad_metrics = comparison.metrics.model_copy(update={"selection_bps": 0})
    with pytest.raises(ContractError, match="do not recompute"):
        build_held_out_disclosure(
            held_out,
            scoring_profile,
            comparison.model_copy(update={"metrics": bad_metrics}),
        )


def retirement_fixture() -> tuple[
    TaskSetManifest,
    TaskSetManifest,
    BridgeReport,
    RetirementManifest,
]:
    dataset, _ = validate_task_set(DATASET, ROOT)
    retired_entry = dataset.tasks[0]
    replacement_entry = dataset.tasks[1]
    before = TaskSetManifest(
        task_set_id="held-out-suite",
        version="0.1.0",
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE,
        tasks=[retired_entry],
    )
    after = TaskSetManifest(
        task_set_id="held-out-suite",
        version="0.2.0",
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE,
        tasks=[replacement_entry],
    )
    scoring_profile = profile()
    configuration = reference_configuration()
    before_result = direct_result(before, scoring_profile, configuration=configuration)
    after_result = direct_result(after, scoring_profile, configuration=configuration)
    bridge = build_bridge_report(
        before,
        after,
        before_result,
        after_result,
        digest("before-result"),
        digest("after-result"),
    )
    record = RetirementRecord(
        retired_task_id=retired_entry.task_id,
        retired_task_digest=retired_entry.task_digest,
        reason=RetirementReason.LEAKAGE,
        replacement_task_id=replacement_entry.task_id,
        replacement_task_digest=replacement_entry.task_digest,
        publication=RetiredPublication(
            task_url="https://example.test/tasks/retired",
            fixtures_url="https://example.test/fixtures/retired",
            reference_runs_url="https://example.test/runs/retired",
            provenance=retired_entry.provenance,
            license=retired_entry.license,
        ),
    )
    retirement = RetirementManifest(
        before_task_set=task_set_binding(before),
        after_task_set=task_set_binding(after),
        bridge_sha256=digest("bridge"),
        records=[record],
    )
    return before, after, bridge, retirement


def test_retirement_requires_replacement_coverage_bridge_and_publication() -> None:
    before, after, bridge, retirement = retirement_fixture()

    validate_retirement_models(retirement, bridge, before, after)
    assert bridge.paired_trials == 5
    assert bridge.coverage_before == bridge.coverage_after
    assert retirement.records[0].publication.license == before.tasks[0].license

    with pytest.raises(ContractError, match="cover every removed task"):
        validate_retirement_models(
            retirement.model_copy(update={"records": []}), bridge, before, after
        )
    carried = after.tasks[0]
    before_with_carried = before.model_copy(update={"tasks": [before.tasks[0], carried]})
    new_entry = carried.model_copy(
        update={
            "task_id": "slopbench/diagnosis/new-replacement",
            "task_digest": digest("new-replacement"),
            "contract_path": "tasks/diagnosis/new-replacement/slopbench-task.json",
        }
    )
    after_with_new = after.model_copy(update={"tasks": [carried, new_entry]})
    configuration = reference_configuration()
    carried_bridge = build_bridge_report(
        before_with_carried,
        after_with_new,
        direct_result(before_with_carried, profile(), configuration=configuration),
        direct_result(after_with_new, profile(), configuration=configuration),
        digest("before-carried"),
        digest("after-carried"),
    )
    carried_replacement = retirement.records[0].model_copy(
        update={
            "replacement_task_id": carried.task_id,
            "replacement_task_digest": carried.task_digest,
        }
    )
    carried_manifest = retirement.model_copy(
        update={
            "before_task_set": task_set_binding(before_with_carried),
            "after_task_set": task_set_binding(after_with_new),
            "records": [carried_replacement],
        }
    )
    with pytest.raises(ContractError, match="replacement task identity is not new"):
        validate_retirement_models(
            carried_manifest, carried_bridge, before_with_carried, after_with_new
        )

    bad_publication = retirement.records[0].publication.model_copy(
        update={"license": {"spdx": "Apache-2.0", "holder": "Other"}}
    )
    bad_record = retirement.records[0].model_copy(update={"publication": bad_publication})
    with pytest.raises(ContractError, match="provenance or license"):
        validate_retirement_models(
            retirement.model_copy(update={"records": [bad_record]}), bridge, before, after
        )


def test_retirement_file_validation_binds_bridge_digest(tmp_path: Path) -> None:
    before, after, _, retirement = retirement_fixture()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_result_path = tmp_path / "before-result.json"
    after_result_path = tmp_path / "after-result.json"
    bridge_path = tmp_path / "bridge.json"
    retirement_path = tmp_path / "retirement.json"
    write_model(before_path, before)
    write_model(after_path, after)
    configuration = reference_configuration()
    before_result = direct_result(before, profile(), configuration=configuration)
    after_result = direct_result(after, profile(), configuration=configuration)
    write_model(before_result_path, before_result)
    write_model(after_result_path, after_result)
    bridge = build_bridge_report(
        before,
        after,
        before_result,
        after_result,
        sha256_file(before_result_path),
        sha256_file(after_result_path),
    )
    write_model(bridge_path, bridge)
    write_model(
        retirement_path,
        retirement.model_copy(update={"bridge_sha256": sha256_file(bridge_path)}),
    )

    validate_retirement(
        retirement_path,
        bridge_path,
        before_path,
        after_path,
        before_result_path,
        after_result_path,
        ROOT,
    )
    bridge_path.write_text(bridge_path.read_text() + " ")
    with pytest.raises(ContractError, match="bridge digest mismatch"):
        validate_retirement(
            retirement_path,
            bridge_path,
            before_path,
            after_path,
            before_result_path,
            after_result_path,
            ROOT,
        )


def test_retirement_rejects_bridge_result_digest_forgery(tmp_path: Path) -> None:
    before, after, forged_bridge, retirement = retirement_fixture()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_result_path = tmp_path / "before-result.json"
    after_result_path = tmp_path / "after-result.json"
    bridge_path = tmp_path / "bridge.json"
    retirement_path = tmp_path / "retirement.json"
    write_model(before_path, before)
    write_model(after_path, after)
    write_model(before_result_path, direct_result(before, profile()))
    write_model(after_result_path, direct_result(after, profile()))
    write_model(bridge_path, forged_bridge)
    write_model(
        retirement_path,
        retirement.model_copy(update={"bridge_sha256": sha256_file(bridge_path)}),
    )

    with pytest.raises(ContractError, match="comparison result digest mismatch"):
        validate_retirement(
            retirement_path,
            bridge_path,
            before_path,
            after_path,
            before_result_path,
            after_result_path,
            ROOT,
        )


def test_bridge_rejects_nonpaired_or_inconsistent_results() -> None:
    before, after, _, _ = retirement_fixture()
    scoring_profile = profile()
    configuration = reference_configuration()
    smoke = direct_result(
        before,
        scoring_profile,
        purpose=EvaluationPurpose.SMOKE,
        configuration=configuration,
    )
    after_result = direct_result(after, scoring_profile, configuration=configuration)
    with pytest.raises(ContractError, match="five-trial"):
        build_bridge_report(before, after, smoke, after_result, digest("before"), digest("after"))

    external_after = after_result.model_copy(update={"result_origin": ResultOrigin.EXTERNAL})
    with pytest.raises(ContractError, match="same result origin"):
        build_bridge_report(
            before,
            after,
            direct_result(before, scoring_profile, configuration=configuration),
            external_after,
            digest("before"),
            digest("after"),
        )


def test_aggregate_excludes_non_agent_evidence_from_quality_and_reliability() -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    configuration = reference_configuration()
    valid = raw_trial(task_set.tasks[0], 1, configuration)
    infrastructure = raw_trial(task_set.tasks[0], 2, configuration).model_copy(
        update={"classification": FailureClassification.INFRASTRUCTURE_FAILURE}
    )

    mixed = _aggregate([valid, infrastructure], scoring_profile)
    unavailable = _aggregate([infrastructure], scoring_profile)

    assert mixed.reliability_trial_count == 1
    assert mixed.excluded_reliability_trials == 1
    assert mixed.reliability_bps == 10_000
    assert mixed.quality_bps == 10_000
    assert mixed.failure_counts[FailureClassification.INFRASTRUCTURE_FAILURE] == 1
    assert unavailable.reliability_trial_count == 0
    assert unavailable.reliability_bps == 0
    assert unavailable.quality_bps == 0


def test_publication_rejects_non_comparable_trials() -> None:
    task_set = one_task_set(
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE, task_set_id="private-suite"
    )
    scoring_profile = profile()
    result = direct_result(task_set, scoring_profile)
    changed_trials = [
        result.trials[0].model_copy(
            update={"classification": FailureClassification.INFRASTRUCTURE_FAILURE}
        ),
        *result.trials[1:],
    ]
    changed = result.model_copy(
        update={"trials": changed_trials, "metrics": _aggregate(changed_trials, scoring_profile)}
    )

    with pytest.raises(ContractError, match="non-comparable trial"):
        build_held_out_disclosure(task_set, scoring_profile, changed)


def test_retirement_allows_a_same_id_digest_change_only_for_a_major_release() -> None:
    before, after, _, retirement = retirement_fixture()
    replacement = after.tasks[0].model_copy(update={"task_id": before.tasks[0].task_id})
    after_same_id = after.model_copy(update={"tasks": [replacement]})
    configuration = reference_configuration()
    bridge = build_bridge_report(
        before,
        after_same_id,
        direct_result(before, profile(), configuration=configuration),
        direct_result(after_same_id, profile(), configuration=configuration),
        digest("same-id-before"),
        digest("same-id-after"),
    )
    record = retirement.records[0].model_copy(
        update={
            "reason": RetirementReason.MAJOR_TASK_SET_RELEASE,
            "replacement_task_id": replacement.task_id,
            "replacement_task_digest": replacement.task_digest,
        }
    )
    manifest = retirement.model_copy(
        update={"after_task_set": task_set_binding(after_same_id), "records": [record]}
    )

    validate_retirement_models(manifest, bridge, before, after_same_id)

    leakage = record.model_copy(update={"reason": RetirementReason.LEAKAGE})
    with pytest.raises(ContractError, match="same-ID replacement requires a major"):
        validate_retirement_models(
            manifest.model_copy(update={"records": [leakage]}),
            bridge,
            before,
            after_same_id,
        )


def test_bridge_rejects_execution_pin_drift_for_an_unchanged_task() -> None:
    dataset, _ = validate_task_set(DATASET, ROOT)
    shared, added = dataset.tasks[:2]
    before = TaskSetManifest(
        task_set_id="held-out-suite",
        version="0.1.0",
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE,
        tasks=[shared],
    )
    after = TaskSetManifest(
        task_set_id="held-out-suite",
        version="0.2.0",
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE,
        tasks=[shared, added],
    )
    scoring_profile = profile()
    configuration = reference_configuration()
    before_result = direct_result(before, scoring_profile, configuration=configuration)
    after_result = direct_result(after, scoring_profile, configuration=configuration)
    payload = after_result.model_dump(mode="json")
    for trial in payload["trials"]:
        if trial["task_id"] == shared.task_id:
            trial["runtime"]["memory_mb"] += 128
    vector = RawResultVector.model_validate_json(json.dumps({"trials": payload["trials"]}))
    payload["result_vector_sha256"] = contract_digest("slopbench.result-vector.v1", vector)
    payload["metrics"] = _aggregate(vector.trials, scoring_profile).model_dump(mode="json")
    changed_after = EvaluationResult.model_validate_json(json.dumps(payload))

    with pytest.raises(ContractError, match="execution pins drift for unchanged task"):
        build_bridge_report(
            before,
            after,
            before_result,
            changed_after,
            digest("before-result"),
            digest("after-result"),
        )


def test_ssh_attestation_promotes_only_trusted_maintainer_references(tmp_path: Path) -> None:
    fixture = materialize_evaluation(tmp_path, origin=ResultOrigin.MAINTAINER)
    key = tmp_path / "maintainer-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    signer = "maintainer@uinaf.dev"
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text(f"{signer} {key.with_suffix('.pub').read_text()}")

    attestation = sign_reference_attestation(
        fixture["evaluation_path"],
        fixture["result_path"],
        key,
        signer,
    )
    attestation_path = tmp_path / "attestation.json"
    write_model(attestation_path, attestation)
    verification = verify_reference_attestation(
        attestation_path,
        allowed_signers,
        fixture["evaluation_path"],
        fixture["result_path"],
    )

    assert verification.status == "official"
    assert verification.signer == signer
    assert verification.attestation_sha256 == sha256_file(attestation_path)
    assert attestation.statement == build_attestation_statement(
        fixture["evaluation_path"], fixture["result_path"]
    )

    untrusted = tmp_path / "untrusted"
    untrusted.write_text(f"someone-else@uinaf.dev {key.with_suffix('.pub').read_text()}")
    with pytest.raises(ContractError, match="not trusted"):
        verify_reference_attestation(
            attestation_path,
            untrusted,
            fixture["evaluation_path"],
            fixture["result_path"],
        )


def test_attestation_rejects_external_bad_and_mismatched_signatures(tmp_path: Path) -> None:
    external = materialize_evaluation(tmp_path / "external")
    with pytest.raises(ContractError, match="only maintainer"):
        build_attestation_statement(external["evaluation_path"], external["result_path"])

    maintainer = materialize_evaluation(tmp_path / "maintainer", origin=ResultOrigin.MAINTAINER)
    statement = build_attestation_statement(
        maintainer["evaluation_path"], maintainer["result_path"]
    )
    allowed = tmp_path / "allowed"
    allowed.write_text("maintainer@uinaf.dev ssh-ed25519 AAAAinvalid\n")
    for encoded, message in [
        ("!" * 20, "valid base64"),
        (base64.b64encode(b"not an ssh signature").decode(), "armored SSH"),
    ]:
        attestation = ReferenceAttestation(
            statement=statement,
            signature=SshSignature(signer="maintainer@uinaf.dev", signature_base64=encoded),
        )
        path = tmp_path / f"bad-{message.split()[0]}.json"
        write_model(path, attestation)
        with pytest.raises(ContractError, match=message):
            verify_reference_attestation(
                path,
                allowed,
                maintainer["evaluation_path"],
                maintainer["result_path"],
            )

    changed_statement = statement.model_copy(update={"evaluation_id": "different"})
    mismatched = ReferenceAttestation(
        statement=changed_statement,
        signature=SshSignature(
            signer="maintainer@uinaf.dev",
            signature_base64=base64.b64encode(b"-----BEGIN SSH SIGNATURE-----\ninvalid\n").decode(),
        ),
    )
    mismatched_path = tmp_path / "mismatched.json"
    write_model(mismatched_path, mismatched)
    with pytest.raises(ContractError, match="subjects do not match"):
        verify_reference_attestation(
            mismatched_path,
            allowed,
            maintainer["evaluation_path"],
            maintainer["result_path"],
        )


def test_attestation_rejects_trials_substituted_beyond_the_evaluation_manifest(
    tmp_path: Path,
) -> None:
    fixture = materialize_evaluation(tmp_path, origin=ResultOrigin.MAINTAINER)
    result = fixture["result"]
    changed_trial = result.trials[0].model_copy(
        update={"run_manifest_sha256": digest("substituted-run")}
    )
    trials = [changed_trial]
    changed = result.model_copy(
        update={
            "trials": trials,
            "result_vector_sha256": contract_digest(
                "slopbench.result-vector.v1", RawResultVector(trials=trials)
            ),
        }
    )
    changed_path = tmp_path / "substituted-result.json"
    write_model(changed_path, changed)

    with pytest.raises(ContractError, match="run bindings"):
        build_attestation_statement(fixture["evaluation_path"], changed_path)


def test_attestation_rejects_non_comparable_reference_trials(tmp_path: Path) -> None:
    fixture = materialize_evaluation(
        tmp_path,
        purpose=EvaluationPurpose.COMPARISON,
        origin=ResultOrigin.MAINTAINER,
    )
    result = fixture["result"]
    changed_trials = [
        result.trials[0].model_copy(
            update={"classification": FailureClassification.INFRASTRUCTURE_FAILURE}
        ),
        *result.trials[1:],
    ]
    changed = result.model_copy(
        update={
            "trials": changed_trials,
            "result_vector_sha256": contract_digest(
                "slopbench.result-vector.v1", RawResultVector(trials=changed_trials)
            ),
            "metrics": _aggregate(changed_trials, result.profile_definition),
        }
    )
    changed_path = tmp_path / "non-comparable-result.json"
    write_model(changed_path, changed)

    with pytest.raises(ContractError, match="non-comparable trial"):
        build_attestation_statement(fixture["evaluation_path"], changed_path)


def test_attestation_signer_reports_process_and_output_failures(tmp_path: Path) -> None:
    fixture = materialize_evaluation(tmp_path, origin=ResultOrigin.MAINTAINER)

    def failed(command: list[str], statement: bytes) -> tuple[int, bytes]:
        assert command[:3] == ["ssh-keygen", "-Y", "sign"]
        assert statement
        return 1, b""

    with pytest.raises(ContractError, match="could not create"):
        sign_reference_attestation(
            fixture["evaluation_path"],
            fixture["result_path"],
            tmp_path / "key",
            "maintainer@uinaf.dev",
            process_runner=failed,
        )

    def invalid(command: list[str], statement: bytes) -> tuple[int, bytes]:
        return 0, b"not armored"

    with pytest.raises(ContractError, match="invalid attestation"):
        sign_reference_attestation(
            fixture["evaluation_path"],
            fixture["result_path"],
            tmp_path / "key",
            "maintainer@uinaf.dev",
            process_runner=invalid,
        )


def test_release_models_reject_secret_configuration_and_invalid_profile() -> None:
    configuration = reference_configuration().model_dump(mode="json")
    configuration["environment"] = {"API_TOKEN": "secret"}
    with pytest.raises(ValidationError, match="looks sensitive"):
        ReferenceConfiguration.model_validate_json(json.dumps(configuration))

    scoring_profile = profile().model_dump(mode="json")
    scoring_profile["quality_weight"] = 90
    with pytest.raises(ValidationError, match="sum to 100"):
        ProfileDefinition.model_validate_json(json.dumps(scoring_profile))
    with pytest.raises(ValidationError, match="declare a cost or duration"):
        ProfileBudget(max_mean_cost_usd=None, max_mean_duration_seconds=None)


def test_aggregate_contract_requires_complete_failure_counters() -> None:
    metrics = direct_result(one_task_set(), profile(), purpose=EvaluationPurpose.SMOKE).metrics
    payload = metrics.model_dump(mode="json")
    del payload["failure_counts"][FailureClassification.INVALID_RUN.value]
    with pytest.raises(ValidationError, match="every classification"):
        AggregateMetrics.model_validate_json(json.dumps(payload))


def test_task_set_models_reject_duplicate_identity_paths_and_gates() -> None:
    task_set = one_task_set()
    entry = task_set.tasks[0]
    entry_payload = entry.model_dump(mode="json")
    entry_payload["applicable_gates"].append(entry_payload["applicable_gates"][0])
    with pytest.raises(ValidationError, match="applicable_gates must be unique"):
        TaskSetEntry.model_validate_json(json.dumps(entry_payload))

    duplicate_id = task_set.model_dump(mode="json")
    duplicate_id["tasks"].append(duplicate_id["tasks"][0])
    with pytest.raises(ValidationError, match="task_ids must be unique"):
        TaskSetManifest.model_validate_json(json.dumps(duplicate_id))

    duplicate_path = task_set.model_dump(mode="json")
    second = dict(duplicate_path["tasks"][0])
    second.update(task_id="slopbench/diagnosis/another", task_digest=digest("another"))
    duplicate_path["tasks"].append(second)
    with pytest.raises(ValidationError, match="contract_paths must be unique"):
        TaskSetManifest.model_validate_json(json.dumps(duplicate_path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-gate", "every stable gate"),
        ("negative", "cannot be negative"),
        ("all-zero", "at least one gate weight"),
        ("duplicate-strict", "strict_gates must be unique"),
        ("zero-strict", "strict gates must carry positive"),
        ("subjective-source", "subjective profiles require"),
        ("objective-source", "objective profiles cannot"),
    ],
)
def test_profile_models_reject_ambiguous_weighting(mutation: str, message: str) -> None:
    payload = profile().model_dump(mode="json")
    if mutation == "missing-gate":
        del payload["gate_weights"][GateName.AUTHORITY.value]
    elif mutation == "negative":
        payload["gate_weights"][GateName.AUTHORITY.value] = -1
    elif mutation == "all-zero":
        payload["gate_weights"] = {gate.value: 0 for gate in GateName}
        payload["strict_gates"] = []
    elif mutation == "duplicate-strict":
        payload["strict_gates"].append(payload["strict_gates"][0])
    elif mutation == "zero-strict":
        payload["gate_weights"][payload["strict_gates"][0]] = 0
    elif mutation == "subjective-source":
        payload.update(subjective=True, source_note=None)
    else:
        payload.update(subjective=False, source_note="not allowed")
    with pytest.raises(ValidationError, match=message):
        ProfileDefinition.model_validate_json(json.dumps(payload))


def test_reference_configuration_rejects_duplicate_tools() -> None:
    payload = reference_configuration().model_dump(mode="json")
    payload["tools"].append(payload["tools"][0])
    with pytest.raises(ValidationError, match="tool names must be unique"):
        ReferenceConfiguration.model_validate_json(json.dumps(payload))

    payload = reference_configuration().model_dump(mode="json")
    payload["credential_env"] = ["CURSOR_API_KEY", "CURSOR_API_KEY"]
    with pytest.raises(ValidationError, match="credential_env values must be unique"):
        ReferenceConfiguration.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("pair-index", "pair_index coverage"),
        ("task-digest", "task digest changes"),
        ("run-path", "run manifest paths"),
        ("result-path", "result paths"),
        ("result-digest", "result digests"),
        ("report-path", "agent report paths"),
    ],
)
def test_evaluation_manifest_rejects_ambiguous_trial_bindings(mutation: str, message: str) -> None:
    fixture = materialize_evaluation_for_model(EvaluationPurpose.CALIBRATION)
    payload = fixture.model_dump(mode="json")
    if mutation == "pair-index":
        payload["runs"][2]["pair_index"] = 2
    elif mutation == "task-digest":
        payload["runs"][2]["task_digest"] = digest("different-task")
    elif mutation == "run-path":
        payload["runs"][2]["run_manifest_path"] = payload["runs"][0]["run_manifest_path"]
    elif mutation == "result-path":
        payload["runs"][2]["result_path"] = payload["runs"][0]["result_path"]
    elif mutation == "result-digest":
        payload["runs"][2]["result_sha256"] = payload["runs"][0]["result_sha256"]
    else:
        payload["runs"][2]["report_path"] = payload["runs"][0]["report_path"]
    with pytest.raises(ValidationError, match=message):
        EvaluationManifest.model_validate_json(json.dumps(payload))


def materialize_evaluation_for_model(purpose: EvaluationPurpose) -> EvaluationManifest:
    task_set = one_task_set()
    scoring_profile = profile()
    count = {
        EvaluationPurpose.SMOKE: 1,
        EvaluationPurpose.CALIBRATION: 3,
        EvaluationPurpose.COMPARISON: 5,
    }[purpose]
    entry = task_set.tasks[0]
    return EvaluationManifest(
        evaluation_id="model-validation",
        task_set=task_set_binding(task_set),
        profile=profile_binding(scoring_profile),
        purpose=purpose,
        configuration=reference_configuration(),
        runs=[
            EvaluationRunBinding(
                task_id=entry.task_id,
                task_digest=entry.task_digest,
                pair_index=index,
                run_manifest_path=f"runs/{index}/run.json",
                run_manifest_sha256=digest(f"model-run-{index}"),
                result_path=f"runs/{index}/result.json",
                result_sha256=digest(f"model-result-{index}"),
                report_path=f"runs/{index}/report.json",
                report_sha256=digest(f"model-report-{index}"),
            )
            for index in range(1, count + 1)
        ],
    )


def test_raw_and_evaluation_result_models_reject_internal_drift() -> None:
    result = direct_result(one_task_set(), profile(), purpose=EvaluationPurpose.CALIBRATION)
    raw_payload = result.trials[0].model_dump(mode="json")
    raw_payload["outcomes"] = raw_payload["outcomes"][:-1]
    with pytest.raises(ValidationError, match="exactly one entry per gate"):
        RawTrialOutcome.model_validate_json(json.dumps(raw_payload))
    raw_payload = result.trials[0].model_dump(mode="json")
    raw_payload["receipt"]["sha256"] = digest("wrong-receipt")
    with pytest.raises(ValidationError, match="receipt and report digest"):
        RawTrialOutcome.model_validate_json(json.dumps(raw_payload))
    raw_payload = result.trials[0].model_dump(mode="json")
    raw_payload["outcomes"][0]["status"] = OutcomeStatus.FAILED.value
    raw_payload["strict_gate_failures"] = [GateName.REQUESTED_BEHAVIOR.value]
    with pytest.raises(ValidationError, match="raw valid_pass cannot contain failed gate"):
        RawTrialOutcome.model_validate_json(json.dumps(raw_payload))

    mutations: list[tuple[str, dict[str, Any], str]] = []
    payload = result.model_dump(mode="json")
    payload["trials"][0]["agent"]["harness_version"] = "different"
    mutations.append(("configuration", payload, "configuration mismatch"))
    payload = result.model_dump(mode="json")
    payload["trials"] = payload["trials"][:-1]
    mutations.append(("count", payload, "requires 3 trial"))
    payload = result.model_dump(mode="json")
    payload["trials"][2]["pair_index"] = 2
    mutations.append(("pairs", payload, "pair_index coverage"))
    payload = result.model_dump(mode="json")
    payload["trials"][2]["task_digest"] = digest("changed-task")
    mutations.append(("digest", payload, "raw task binding does not match"))
    payload = result.model_dump(mode="json")
    payload["trials"][1]["runtime"]["memory_mb"] += 128
    mutations.append(("execution-pins", payload, "task execution pins drift"))
    payload = result.model_dump(mode="json")
    payload["trials"] = list(reversed(payload["trials"]))
    mutations.append(("order", payload, "deterministic task and pair order"))
    payload = result.model_dump(mode="json")
    payload["trials"][1]["run_id"] = payload["trials"][0]["run_id"]
    payload["trials"][1]["trial"]["id"] = payload["trials"][0]["run_id"]
    mutations.append(("identity", payload, "run ids must be unique"))
    payload = result.model_dump(mode="json")
    payload["metrics"]["trial_count"] = 2
    payload["metrics"]["reliability_trial_count"] = 2
    payload["metrics"]["failure_counts"][FailureClassification.VALID_PASS.value] = 2
    mutations.append(("metrics", payload, "trial_count does not match"))
    for _, changed, message in mutations:
        with pytest.raises(ValidationError, match=message):
            EvaluationResult.model_validate_json(json.dumps(changed))


def test_lifecycle_models_reject_invalid_counts_and_duplicate_records() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        CoverageSnapshot(category_counts={CapabilityCategory.DIAGNOSIS_REPAIR: -1})

    before, _, bridge, retirement = retirement_fixture()
    bridge_payload = bridge.model_dump(mode="json")
    bridge_payload["after_task_set"] = bridge_payload["before_task_set"]
    with pytest.raises(ValidationError, match="task sets must differ"):
        BridgeReport.model_validate_json(json.dumps(bridge_payload))

    duplicate = retirement.model_dump(mode="json")
    duplicate["records"].append(duplicate["records"][0])
    with pytest.raises(ValidationError, match="retired tasks must be unique"):
        RetirementManifest.model_validate_json(json.dumps(duplicate))
    duplicate_replacement = retirement.model_dump(mode="json")
    second = dict(duplicate_replacement["records"][0])
    second.update(
        retired_task_id="slopbench/diagnosis/another-retired",
        retired_task_digest=digest("another-retired"),
    )
    duplicate_replacement["records"].append(second)
    with pytest.raises(ValidationError, match="replacement tasks must be unique"):
        RetirementManifest.model_validate_json(json.dumps(duplicate_replacement))

    statement = {
        "evaluation_id": "invalid-statement",
        "subjects": [{"name": "evaluation-manifest", "sha256": digest("manifest")}],
    }
    with pytest.raises(ValidationError, match="one manifest and one result"):
        AttestationStatement.model_validate_json(json.dumps(statement))

    metrics = direct_result(
        before, profile(), purpose=EvaluationPurpose.COMPARISON
    ).metrics.model_dump(mode="json")
    del metrics["strict_gate_failure_counts"][GateName.AUTHORITY.value]
    with pytest.raises(ValidationError, match="every gate"):
        AggregateMetrics.model_validate_json(json.dumps(metrics))
