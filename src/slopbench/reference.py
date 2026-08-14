"""Pinned reference-run manifest generation."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from harbor.models.task.task import Task as HarborTask

from slopbench.contracts import (
    RUN_SCHEMA_VERSION,
    AgentConfiguration,
    ImagePin,
    InstructionLayer,
    ResultBundle,
    RetryableReason,
    RetryPolicy,
    RunLimits,
    RunManifest,
    RuntimeConfiguration,
    TaskBinding,
    TrialIdentity,
)
from slopbench.hashing import ContractError, load_model, sha256_file, validate_task, write_model
from slopbench.release import (
    EvaluationManifest,
    EvaluationPurpose,
    EvaluationRunBinding,
    ProfileDefinition,
    ReferenceConfiguration,
    TaskSetManifest,
    profile_binding,
    task_set_binding,
)

_IMAGE_PATTERN = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE)
_TRIAL_COUNTS = {
    EvaluationPurpose.SMOKE: 1,
    EvaluationPurpose.CALIBRATION: 3,
    EvaluationPurpose.COMPARISON: 5,
}


def _image_references(task_dir: Path) -> list[str]:
    references: set[str] = set()
    for dockerfile in task_dir.rglob("Dockerfile"):
        for line in dockerfile.read_text().splitlines():
            match = _IMAGE_PATTERN.match(line)
            if match:
                references.add(match.group(1))
    if not references:
        raise ContractError(f"task has no Docker image pins: {task_dir}")
    return sorted(references)


def build_reference_run(
    task_dir: Path,
    project_root: Path,
    configuration: ReferenceConfiguration,
    pair_index: int,
    *,
    environment_provider_version: str,
) -> RunManifest:
    task_dir = task_dir.resolve()
    project_root = project_root.resolve()
    if not task_dir.is_relative_to(project_root):
        raise ContractError("reference task must be below the project root")
    if pair_index < 1 or pair_index > 5:
        raise ContractError("reference pair_index must be between one and five")
    task, contract_sha256, task_digest = validate_task(task_dir)
    task_path = task_dir.relative_to(project_root).as_posix()
    declared_hosts = set(task.capabilities.network_allowed_hosts)
    unexpected_hosts = set(configuration.network_allowed_hosts) - declared_hosts
    if unexpected_hosts:
        raise ContractError(
            f"reference model network exceeds {task.task_id}: {sorted(unexpected_hosts)}"
        )
    immutable = {item.path: item.sha256 for item in task.immutable_inputs}
    instruction_layers = [
        InstructionLayer(
            name=phase.name,
            path=f"{task_path}/{phase.instruction_path}",
            sha256=immutable[phase.instruction_path],
        )
        for phase in task.phases
    ]
    slug = task.task_id.removeprefix("slopbench/").replace("/", "-")
    run_id = f"{configuration.configuration_id}-{slug}-{pair_index}"
    harbor_version = importlib.metadata.version("harbor")
    if configuration.adapter.version != harbor_version:
        raise ContractError("reference adapter version does not match installed Harbor")
    return RunManifest(
        schema_version=RUN_SCHEMA_VERSION,
        run_id=run_id,
        task=TaskBinding(
            contract_path=f"{task_path}/slopbench-task.json",
            contract_sha256=contract_sha256,
            task_digest=task_digest,
            task_id=task.task_id,
            task_version=task.version,
            harbor_task_checksum=HarborTask(task_dir).checksum,
        ),
        agent=AgentConfiguration(
            harness=configuration.harness.name,
            harness_version=configuration.harness.version,
            adapter=configuration.adapter,
            model=configuration.model,
            effort_tier=configuration.effort_tier,
            settings=configuration.settings,
            environment=configuration.environment,
            setup_network_allowed_hosts=configuration.setup_network_allowed_hosts,
            network_allowed_hosts=configuration.network_allowed_hosts,
            tools=configuration.tools,
            instruction_layers=instruction_layers,
            credential_env=configuration.credential_env,
        ),
        runtime=RuntimeConfiguration(
            harbor_version=harbor_version,
            environment_provider="docker",
            environment_provider_version=environment_provider_version,
            images=[
                ImagePin(role="fixture-base", reference=reference)
                for reference in _image_references(task_dir)
            ],
            cpus=task.environment.cpus,
            memory_mb=task.environment.memory_mb,
            storage_mb=task.environment.storage_mb,
        ),
        limits=RunLimits(
            agent_timeout_sec=900,
            agent_setup_timeout_sec=600,
            verifier_timeout_sec=180,
            max_tokens=None,
            max_cost_usd=None,
        ),
        trial=TrialIdentity(id=run_id, attempt=1, seed=pair_index),
        retry_policy=RetryPolicy(
            max_attempts=2,
            retryable_reasons=[
                RetryableReason.PROVIDER_RATE_LIMIT,
                RetryableReason.ENVIRONMENT_START_TIMEOUT,
            ],
        ),
    )


def write_reference_runs(
    task_dirs: list[Path],
    project_root: Path,
    configuration: ReferenceConfiguration,
    purpose: EvaluationPurpose,
    output_dir: Path,
    *,
    environment_provider_version: str,
) -> list[Path]:
    trial_count = _TRIAL_COUNTS[purpose]
    written: list[Path] = []
    for task_dir in sorted(task_dirs, key=lambda path: path.as_posix()):
        task, _, _ = validate_task(task_dir)
        task_slug = task.task_id.removeprefix("slopbench/").replace("/", "-")
        for pair_index in range(1, trial_count + 1):
            manifest = build_reference_run(
                task_dir,
                project_root,
                configuration,
                pair_index,
                environment_provider_version=environment_provider_version,
            )
            path = output_dir / task_slug / f"trial-{pair_index}.json"
            write_model(path, manifest)
            written.append(path)
    return written


def _relative_regular_file(path: Path, root: Path, label: str) -> str:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ContractError(f"{label} escapes the bundle root: {path}")
    if path.is_symlink() or not resolved.is_file():
        raise ContractError(f"{label} is not a regular file: {path}")
    return resolved.relative_to(resolved_root).as_posix()


def _report_binding(
    result: ResultBundle,
    run: RunManifest,
    result_bundle: Path,
    bundle_root: Path,
) -> tuple[str | None, str | None]:
    if not result.receipt.present:
        return None, None
    if result.receipt.sha256 is None:
        raise ContractError(f"present receipt has no digest for {result.run_id}")
    matches = [
        artifact
        for artifact in result.artifacts
        if artifact.sha256 == result.receipt.sha256
        and Path(artifact.path).name == "slopbench-report.json"
    ]
    if len(matches) > 1:
        final_phase = run.agent.instruction_layers[-1].name
        final_suffix = f"/steps/{final_phase}/artifacts/app/slopbench-report.json"
        matches = [artifact for artifact in matches if artifact.path.endswith(final_suffix)]
    if len(matches) != 1:
        raise ContractError(f"expected one final report artifact for {result.run_id}")
    report_path = result_bundle / matches[0].path
    resolved_bundle = result_bundle.resolve()
    if not report_path.resolve().is_relative_to(resolved_bundle):
        raise ContractError(f"report artifact escapes its result bundle for {result.run_id}")
    relative = _relative_regular_file(report_path, bundle_root, "agent report")
    if sha256_file(report_path) != result.receipt.sha256:
        raise ContractError(f"agent report digest mismatch for {result.run_id}")
    return relative, result.receipt.sha256


def build_reference_evaluation(
    manifest_dir: Path,
    result_dir: Path,
    bundle_root: Path,
    configuration: ReferenceConfiguration,
    task_set: TaskSetManifest,
    profile: ProfileDefinition,
    purpose: EvaluationPurpose,
    evaluation_id: str,
) -> EvaluationManifest:
    expected_tasks = {entry.task_id: entry for entry in task_set.tasks}
    manifest_paths = sorted(manifest_dir.glob("*/trial-*.json"))
    if not manifest_paths:
        raise ContractError(f"reference manifest directory is empty: {manifest_dir}")
    bindings: list[EvaluationRunBinding] = []
    covered_tasks: set[str] = set()
    for manifest_path in manifest_paths:
        run = load_model(manifest_path, RunManifest)
        entry = expected_tasks.get(run.task.task_id)
        if entry is None:
            raise ContractError(f"reference run contains unexpected task: {run.task.task_id}")
        if run.task.task_digest != entry.task_digest:
            raise ContractError(f"reference run task digest mismatch for {run.task.task_id}")
        if run.trial.seed is None:
            raise ContractError(f"reference run has no pair seed: {run.run_id}")
        result_bundle = result_dir / run.run_id
        result_path = result_bundle / "result.json"
        result = load_model(result_path, ResultBundle)
        manifest_sha256 = sha256_file(manifest_path)
        if (
            result.run_id != run.run_id
            or result.task_digest != run.task.task_digest
            or result.run_manifest_sha256 != manifest_sha256
            or result.attempt != run.trial.attempt
        ):
            raise ContractError(f"reference run/result binding mismatch for {run.run_id}")
        report_path, report_sha256 = _report_binding(result, run, result_bundle, bundle_root)
        bindings.append(
            EvaluationRunBinding(
                task_id=run.task.task_id,
                task_digest=run.task.task_digest,
                pair_index=run.trial.seed,
                run_manifest_path=_relative_regular_file(
                    manifest_path, bundle_root, "run manifest"
                ),
                run_manifest_sha256=manifest_sha256,
                result_path=_relative_regular_file(result_path, bundle_root, "raw result"),
                result_sha256=sha256_file(result_path),
                report_path=report_path,
                report_sha256=report_sha256,
            )
        )
        covered_tasks.add(run.task.task_id)
    missing_tasks = sorted(set(expected_tasks) - covered_tasks)
    if missing_tasks:
        raise ContractError(f"reference runs do not cover task set: {missing_tasks}")
    return EvaluationManifest(
        schema_version="slopbench.evaluation.v1",
        evaluation_id=evaluation_id,
        task_set=task_set_binding(task_set),
        profile=profile_binding(profile),
        purpose=purpose,
        configuration=configuration,
        runs=bindings,
    )
