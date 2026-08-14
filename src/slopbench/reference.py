"""Pinned reference-run manifest generation."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from harbor.models.task.task import Task as HarborTask

from slopbench.contracts import (
    AgentConfiguration,
    ImagePin,
    InstructionLayer,
    RetryableReason,
    RetryPolicy,
    RunLimits,
    RunManifest,
    RuntimeConfiguration,
    TaskBinding,
    TrialIdentity,
)
from slopbench.hashing import ContractError, validate_task, write_model
from slopbench.release import EvaluationPurpose, ReferenceConfiguration

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
