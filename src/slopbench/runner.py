"""Thin Harbor invocation and deterministic result finalization."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath

from harbor.models.environment_type import EnvironmentType
from harbor.models.task.config import NetworkMode, VerifierEnvironmentMode
from harbor.models.task.task import Task as HarborTask
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.models.trial.config import (
    AgentConfig as HarborAgentConfig,
)
from harbor.models.trial.config import ArtifactConfig as HarborArtifactConfig
from harbor.models.trial.config import (
    EnvironmentConfig as HarborEnvironmentConfig,
)
from harbor.models.trial.config import (
    ResourceMode,
    ServiceVolumeConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.models.trial.config import (
    TaskConfig as HarborTaskConfig,
)
from harbor.models.trial.result import TrialResult
from harbor.trial.network_policy import resolve_trial_network_plan
from pydantic import ValidationError

from slopbench.contracts import (
    RESULT_SCHEMA_VERSION,
    AgentReport,
    ArtifactDigest,
    ClaimStatus,
    FailureClassification,
    FailureReason,
    GateName,
    GateOutcome,
    HarborEvidence,
    OutcomeStatus,
    ReceiptValidation,
    ResultBundle,
    RetryDecision,
    RetryDisposition,
    RunManifest,
    RuntimeConfiguration,
    TaskContract,
    TimingMetrics,
    UsageMetrics,
    VerificationEvidence,
)
from slopbench.hashing import (
    ContractError,
    load_model,
    sha256_bytes,
    sha256_file,
    validate_task,
    write_model,
)

ProcessRunner = Callable[[list[str], Path, Mapping[str, str], Path, Path], int]

_SAFE_ENV_NAMES = {
    "COLIMA_HOME",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "HOME",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_FILE",
    "TMPDIR",
    "USER",
}
_AGENT_FAILURE_EXCEPTIONS = {
    "AgentTimeoutError",
    "ContextLengthExceededError",
    "NonZeroAgentExitCodeError",
    "OutputLengthExceededError",
}
_PROVIDER_FAILURE_EXCEPTIONS = {
    "ApiRateLimitError": FailureReason.PROVIDER_RATE_LIMIT,
    "ApiUsageLimitError": FailureReason.PROVIDER_USAGE_LIMIT,
}
_BENCHMARK_FAILURE_EXCEPTIONS = {
    "AddTestsDirError",
    "DownloadVerifierDirError",
    "RewardFileEmptyError",
    "RewardFileNotFoundError",
    "VerifierOutputParseError",
    "VerifierTimeoutError",
}


class RunError(RuntimeError):
    """The requested run cannot safely start."""


def _default_process_runner(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            check=False,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
    return completed.returncode


def _runtime_version() -> str:
    return importlib.metadata.version("harbor")


def _harbor_executable() -> Path:
    candidate = Path(sys.executable).with_name("harbor")
    if not candidate.is_file():
        raise RunError(f"pinned Harbor executable is missing: {candidate}")
    return candidate


def _process_environment(manifest: RunManifest) -> dict[str, str]:
    child = {key: value for key, value in os.environ.items() if key in _SAFE_ENV_NAMES}
    missing: list[str] = []
    for name in manifest.agent.credential_env:
        value = os.environ.get(name)
        if value is None:
            missing.append(name)
        else:
            child[name] = value
    if missing:
        raise RunError(f"missing required credential environment variables: {missing}")
    child["NO_COLOR"] = "1"
    child["PYTHONUNBUFFERED"] = "1"
    return child


def _validate_images(task_dir: Path, runtime: RuntimeConfiguration) -> None:
    dockerfiles = sorted(path for path in task_dir.rglob("Dockerfile") if path.is_file())
    declared = {image.reference for image in runtime.images}
    discovered: set[str] = set()
    for dockerfile in dockerfiles:
        for line in dockerfile.read_text().splitlines():
            match = re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", line, re.IGNORECASE)
            if not match:
                continue
            reference = match.group(1)
            if "@sha256:" not in reference:
                relative = dockerfile.relative_to(task_dir)
                raise ContractError(f"un-pinned base image in {relative}: {reference}")
            discovered.add(reference)
    missing = discovered - declared
    if missing:
        raise ContractError(f"run manifest is missing image pins: {sorted(missing)}")


def _validate_policy(
    label: str,
    actual_mode: NetworkMode,
    actual_hosts: list[str],
    expected_mode: NetworkMode,
    expected_hosts: list[str],
) -> None:
    if actual_mode != expected_mode or actual_hosts != expected_hosts:
        raise ContractError(
            f"{label} network policy mismatch: expected "
            f"{expected_mode.value} {expected_hosts}, got {actual_mode.value} {actual_hosts}"
        )


def _validate_harbor_boundary(
    harbor_task: HarborTask,
    manifest: RunManifest,
    task: TaskContract,
    task_dir: Path,
) -> None:
    if manifest.runtime.environment_provider != EnvironmentType.DOCKER.value:
        raise ContractError("official v1 runs require the Docker environment provider")
    compose_path = task_dir / "environment" / "docker-compose.yaml"
    if compose_path.exists():
        raise ContractError("task-authored Docker Compose is outside the official v1 boundary")

    config = harbor_task.config
    metadata = config.metadata or {}
    if (
        metadata.get("slopbench_task_id") != task.task_id
        or metadata.get("slopbench_task_version") != task.version
    ):
        raise ContractError("Harbor metadata does not match the sealed task identity")
    if (
        config.environment.cpus != task.environment.cpus
        or config.environment.memory_mb != task.environment.memory_mb
        or config.environment.storage_mb != task.environment.storage_mb
        or config.environment.workdir != "/app"
    ):
        raise ContractError("Harbor environment does not match sealed resources or workdir")
    if config.environment.env or config.environment.mcp_servers or config.artifacts:
        raise ContractError("Harbor task declares unapproved environment inputs or artifacts")
    if config.solution.env or config.verifier.env:
        raise ContractError("Harbor task declares unapproved solution or verifier environment")

    expected_agent_mode = (
        NetworkMode.NO_NETWORK if task.capabilities.network == "none" else NetworkMode.ALLOWLIST
    )
    expected_agent_hosts = list(task.capabilities.network_allowed_hosts)
    trial_agent = HarborAgentConfig()
    trial_environment = HarborEnvironmentConfig()
    steps = list(config.steps or [None])
    for index, step in enumerate(steps):
        plan = resolve_trial_network_plan(
            config,
            trial_agent,
            trial_environment,
            step,
            verifier_mode=VerifierEnvironmentMode.SEPARATE,
        )
        suffix = f"step {index + 1}" if step is not None else "task"
        _validate_policy(
            f"{suffix} agent baseline",
            plan.agent_env_baseline.network_mode,
            plan.agent_env_baseline.allowed_hosts,
            NetworkMode.NO_NETWORK,
            [],
        )
        _validate_policy(
            f"{suffix} agent phase",
            plan.agent_phase.network_mode,
            plan.agent_phase.allowed_hosts,
            expected_agent_mode,
            expected_agent_hosts,
        )
        if plan.verifier_env_baseline is None:
            raise ContractError("official verifier must have a separate environment baseline")
        _validate_policy(
            f"{suffix} verifier baseline",
            plan.verifier_env_baseline.network_mode,
            plan.verifier_env_baseline.allowed_hosts,
            NetworkMode.NO_NETWORK,
            [],
        )
        _validate_policy(
            f"{suffix} verifier phase",
            plan.verifier_phase.network_mode,
            plan.verifier_phase.allowed_hosts,
            NetworkMode.NO_NETWORK,
            [],
        )


def _validate_capability_binding(manifest: RunManifest, task: TaskContract) -> None:
    unexpected_environment = set(manifest.agent.environment) - set(task.capabilities.environment)
    if unexpected_environment:
        raise ContractError(
            f"agent environment exceeds the capability envelope: {sorted(unexpected_environment)}"
        )
    unexpected_tools = {tool.name for tool in manifest.agent.tools} - set(task.capabilities.tools)
    if unexpected_tools:
        raise ContractError(
            f"agent tools exceed the capability envelope: {sorted(unexpected_tools)}"
        )
    if manifest.agent.harness in {"oracle", "nop"} and manifest.agent.credential_env:
        raise ContractError("utility harnesses cannot receive credential environment variables")

    fixtures = {fixture.id: fixture for fixture in task.attack_fixtures}
    if manifest.attack_fixture_id is None:
        return
    if manifest.attack_fixture_id not in fixtures:
        raise ContractError(f"unknown attack fixture: {manifest.attack_fixture_id}")
    if (
        manifest.agent.harness != "oracle"
        or manifest.agent.model is not None
        or manifest.agent.credential_env
        or manifest.limits.max_cost_usd != 0.0
    ):
        raise ContractError("attack fixtures require the zero-cost oracle harness")


def _validate_run_binding(
    manifest: RunManifest,
    task: TaskContract,
    contract_sha256: str,
    task_digest: str,
    task_dir: Path,
) -> None:
    binding = manifest.task
    contract_relative = PurePosixPath(binding.contract_path)
    contract_path = (task_dir / "slopbench-task.json").resolve()
    try:
        project_root = contract_path.parents[len(contract_relative.parts) - 1]
    except IndexError as exc:
        raise ContractError(f"task contract_path does not identify {contract_path}") from exc
    bound_contract = project_root.joinpath(*contract_relative.parts).resolve()
    if bound_contract != contract_path:
        raise ContractError(f"task contract_path does not identify {contract_path}")
    mismatches: list[str] = []
    if binding.contract_sha256 != contract_sha256:
        mismatches.append("contract_sha256")
    if binding.task_digest != task_digest:
        mismatches.append("task_digest")
    if binding.task_id != task.task_id:
        mismatches.append("task_id")
    if binding.task_version != task.version:
        mismatches.append("task_version")
    if manifest.runtime.harbor_version != _runtime_version():
        mismatches.append("harbor_version")
    if (
        manifest.runtime.cpus != task.environment.cpus
        or manifest.runtime.memory_mb != task.environment.memory_mb
        or manifest.runtime.storage_mb != task.environment.storage_mb
    ):
        mismatches.append("task_resources")
    if mismatches:
        raise ContractError(f"run manifest binding mismatch: {mismatches}")
    _validate_images(task_dir, manifest.runtime)
    _validate_instruction_layers(manifest, task, task_dir, project_root)
    _validate_capability_binding(manifest, task)
    try:
        harbor_task = HarborTask(task_dir)
    except Exception as exc:
        raise ContractError(f"invalid Harbor task at {task_dir}: {exc}") from exc
    harbor_isolation = resolve_task_verifier_mode(harbor_task.config)
    expected_isolation = VerifierEnvironmentMode(task.environment.verifier_isolation)
    if harbor_isolation != expected_isolation:
        raise ContractError(
            "Harbor verifier isolation does not match the task contract: "
            f"expected {expected_isolation.value}, got {harbor_isolation.value}"
        )
    if harbor_task.checksum != manifest.task.harbor_task_checksum:
        raise ContractError(
            "Harbor task checksum mismatch: "
            f"expected {manifest.task.harbor_task_checksum}, got {harbor_task.checksum}"
        )
    _validate_harbor_boundary(harbor_task, manifest, task, task_dir)


def _validate_instruction_layers(
    manifest: RunManifest,
    task: TaskContract,
    task_dir: Path,
    project_root: Path,
) -> None:
    resolved_layers: set[Path] = set()
    for layer in manifest.agent.instruction_layers:
        declared_path = project_root.joinpath(*PurePosixPath(layer.path).parts)
        resolved_path = declared_path.resolve()
        if not resolved_path.is_relative_to(project_root):
            raise ContractError(f"instruction layer escapes project root: {layer.path}")
        if declared_path.is_symlink() or not resolved_path.is_file():
            raise ContractError(f"instruction layer is not a regular file: {layer.path}")
        if resolved_path in resolved_layers:
            raise ContractError(f"instruction layer path is duplicated: {layer.path}")
        resolved_layers.add(resolved_path)
        actual_sha256 = sha256_file(resolved_path)
        if actual_sha256 != layer.sha256:
            raise ContractError(
                f"instruction layer digest mismatch for {layer.path}: "
                f"expected {layer.sha256}, got {actual_sha256}"
            )
    required = {(task_dir / phase.instruction_path).resolve() for phase in task.phases}
    missing = sorted(path.relative_to(task_dir).as_posix() for path in required - resolved_layers)
    if missing:
        raise ContractError(f"run manifest is missing task instruction layers: {missing}")


def _harbor_config(
    manifest: RunManifest,
    task: TaskContract,
    task_dir: Path,
    trials_dir: Path,
    task_digest: str,
) -> TrialConfig:
    try:
        environment_type = EnvironmentType(manifest.runtime.environment_provider)
    except ValueError as exc:
        raise RunError(
            f"unsupported Harbor environment provider: {manifest.runtime.environment_provider}"
        ) from exc
    agent_environment = {
        **manifest.agent.environment,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/slopbench-agent-pycache",
        "SLOPBENCH_TASK_DIGEST": task_digest,
    }
    verifier_environment = {
        "SLOPBENCH_TASK_DIGEST": task_digest,
        "SLOPBENCH_VERIFIER_ISOLATION": task.environment.verifier_isolation,
    }
    if manifest.attack_fixture_id is not None:
        agent_environment["SLOPBENCH_ATTACK_FIXTURE"] = manifest.attack_fixture_id
        verifier_environment["SLOPBENCH_ATTACK_FIXTURE"] = manifest.attack_fixture_id
    verifier_log_dir = (trials_dir / manifest.trial.id / "verifier").resolve()
    return TrialConfig(
        task=HarborTaskConfig(path=task_dir),
        trial_name=manifest.trial.id,
        trials_dir=trials_dir,
        agent=HarborAgentConfig(
            name=manifest.agent.harness,
            model_name=(manifest.agent.model.harbor_name if manifest.agent.model else None),
            override_timeout_sec=manifest.limits.agent_timeout_sec,
            override_setup_timeout_sec=manifest.limits.agent_setup_timeout_sec,
            kwargs=manifest.agent.settings,
            env=agent_environment,
            include_logs=["trajectory.json", "*.txt"],
        ),
        environment=HarborEnvironmentConfig(
            type=environment_type,
            delete=True,
            force_build=False,
            cpu_enforcement_policy=ResourceMode.LIMIT,
            memory_enforcement_policy=ResourceMode.LIMIT,
            override_cpus=manifest.runtime.cpus,
            override_memory_mb=manifest.runtime.memory_mb,
            override_storage_mb=manifest.runtime.storage_mb,
            mounts=[
                ServiceVolumeConfig(
                    type="bind",
                    source=verifier_log_dir.as_posix(),
                    target="/logs/verifier",
                    read_only=True,
                )
            ],
        ),
        verifier=VerifierConfig(
            override_timeout_sec=manifest.limits.verifier_timeout_sec,
            include_logs=["reward.json", "slopbench-verification.json", "test-*.txt"],
            env=verifier_environment,
        ),
        artifacts=(
            [HarborArtifactConfig(source="/app", exclude=[".git"])]
            if task.environment.verifier_isolation == "separate"
            else ["/app/slopbench-report.json"]
        ),
    )


def _write_harbor_config(path: Path, config: TrialConfig) -> None:
    rendered = config.model_dump_json(indent=2, exclude_none=True) + "\n"
    path.write_text(rendered)


def _snapshot_task(
    source: Path,
    destination: Path,
    expected_contract_sha256: str,
    expected_task_digest: str,
    expected_harbor_checksum: str,
) -> None:
    shutil.copytree(source, destination, symlinks=True)
    _, contract_sha256, task_digest = validate_task(destination)
    harbor_checksum = HarborTask(destination).checksum
    if (
        contract_sha256 != expected_contract_sha256
        or task_digest != expected_task_digest
        or harbor_checksum != expected_harbor_checksum
    ):
        raise ContractError("task changed while the immutable run snapshot was created")
    for path in sorted(destination.rglob("*"), reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)
    destination.chmod(destination.stat().st_mode & ~0o222)
    if HarborTask(destination).checksum != expected_harbor_checksum:
        raise ContractError("read-only task snapshot changed the Harbor task checksum")


def _duration_seconds(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


def _check_outcomes(
    task: TaskContract,
    verification: VerificationEvidence,
    receipt_valid: bool,
) -> tuple[list[GateOutcome], list[str]]:
    outcomes: list[GateOutcome] = []
    errors: list[str] = []
    for gate in GateName:
        checks = [check for check in verification.checks if check.gate == gate]
        if gate not in task.applicable_gates:
            if checks:
                errors.append(f"non-applicable gate has verifier checks: {gate.value}")
            outcomes.append(
                GateOutcome(gate=gate, status=OutcomeStatus.NOT_APPLICABLE, check_ids=[])
            )
            continue
        if not checks:
            errors.append(f"applicable gate has no verifier check: {gate.value}")
            outcomes.append(GateOutcome(gate=gate, status=OutcomeStatus.FAILED, check_ids=[]))
            continue
        passed = all(check.passed for check in checks)
        if gate == GateName.EVIDENCE_RECEIPT:
            passed = passed and receipt_valid
        outcomes.append(
            GateOutcome(
                gate=gate,
                status=OutcomeStatus.PASSED if passed else OutcomeStatus.FAILED,
                check_ids=[check.id for check in checks],
            )
        )
    return outcomes, errors


def _validate_rewards(
    outcomes: list[GateOutcome], rewards: dict[str, float | int] | None
) -> list[str]:
    if rewards is None:
        return ["Harbor result has no reward vector"]
    errors: list[str] = []
    applicable = [outcome for outcome in outcomes if outcome.status != OutcomeStatus.NOT_APPLICABLE]
    expected_keys = {"reward", *(outcome.gate.value for outcome in applicable)}
    if set(rewards) != expected_keys:
        missing = sorted(expected_keys - set(rewards))
        unexpected = sorted(set(rewards) - expected_keys)
        errors.append(f"Harbor reward keys mismatch: missing={missing}, unexpected={unexpected}")
    for outcome in applicable:
        expected = 1 if outcome.status == OutcomeStatus.PASSED else 0
        actual = rewards.get(outcome.gate.value)
        if actual != expected:
            errors.append(
                f"Harbor reward mismatch for {outcome.gate.value}: "
                f"expected {expected}, got {actual}"
            )
    expected_reward = 1 if all(item.status == OutcomeStatus.PASSED for item in applicable) else 0
    if rewards.get("reward") != expected_reward:
        errors.append(
            "Harbor completion reward mismatch: "
            f"expected {expected_reward}, got {rewards.get('reward')}"
        )
    return errors


def _receipt_path(trial_dir: Path) -> Path:
    return trial_dir / "artifacts" / "app" / "slopbench-report.json"


def _validate_receipt(
    report_path: Path,
    task: TaskContract,
    verification: VerificationEvidence,
) -> tuple[ReceiptValidation, AgentReport | None, bool]:
    if report_path.is_symlink():
        return (
            ReceiptValidation(
                present=True,
                valid=False,
                sha256=None,
                errors=["slopbench-report.json must be a regular file, not a symlink"],
            ),
            None,
            True,
        )
    if not report_path.is_file():
        return (
            ReceiptValidation(
                present=False,
                valid=False,
                sha256=None,
                errors=["slopbench-report.json was not produced"],
            ),
            None,
            False,
        )
    digest = sha256_file(report_path)
    try:
        report = load_model(report_path, AgentReport)
    except ContractError as exc:
        return (
            ReceiptValidation(
                present=True,
                valid=False,
                sha256=digest,
                errors=[str(exc)],
            ),
            None,
            True,
        )
    checks = {check.id: check for check in verification.checks}
    errors: list[str] = []
    if report.task_digest != verification.task_digest:
        errors.append("task_digest does not match verifier evidence")
    if report.base_revision != verification.base_revision:
        errors.append("base_revision does not match verifier evidence")
    if report.final_revision != verification.final_revision:
        errors.append("final_revision does not match verifier evidence")
    claims = {claim.gate: claim for claim in report.claims}
    expected_claims = set(task.applicable_gates)
    if set(claims) != expected_claims:
        missing = sorted(gate.value for gate in expected_claims - set(claims))
        unexpected = sorted(gate.value for gate in set(claims) - expected_claims)
        errors.append(f"receipt gate coverage mismatch: missing={missing}, unexpected={unexpected}")
    for gate in task.applicable_gates:
        claim = claims.get(gate)
        if claim is None:
            continue
        expected_evidence_ids = {check.id for check in verification.checks if check.gate == gate}
        if set(claim.evidence_ids) != expected_evidence_ids:
            errors.append(f"claim evidence coverage mismatch: {gate.value}")
        evidence = [checks.get(evidence_id) for evidence_id in claim.evidence_ids]
        if any(item is None for item in evidence):
            errors.append(f"claim references unknown evidence: {gate.value}")
            continue
        typed_evidence = [item for item in evidence if item is not None]
        if any(item.gate != gate for item in typed_evidence):
            errors.append(f"claim references evidence for a different gate: {gate.value}")
            continue
        observed_pass = all(item.passed for item in typed_evidence)
        if claim.status == ClaimStatus.PASSED and not observed_pass:
            errors.append(f"claim contradicts failing verifier evidence: {gate.value}")
        if claim.status == ClaimStatus.FAILED and observed_pass:
            errors.append(f"claim contradicts passing verifier evidence: {gate.value}")
        if claim.status == ClaimStatus.UNCERTAIN and not report.uncertainty:
            errors.append(f"uncertain claim has no uncertainty record: {gate.value}")
    for command in report.commands:
        check = checks.get(command.id)
        if check is None:
            errors.append(f"command references unknown captured id: {command.id}")
        elif check.command != command.command or check.exit_code != command.exit_code:
            errors.append(f"command does not match captured evidence: {command.id}")
    claimed_evidence = {
        evidence_id for claim in report.claims for evidence_id in claim.evidence_ids
    }
    for command in report.commands:
        if command.id not in claimed_evidence:
            errors.append(f"command is not referenced by a claim: {command.id}")
    return (
        ReceiptValidation(
            present=True,
            valid=not errors,
            sha256=digest,
            errors=errors,
        ),
        report,
        bool(errors),
    )


def _validate_verification_logs(trial_dir: Path, verification: VerificationEvidence) -> list[str]:
    verifier_dir = trial_dir / "verifier"
    errors: list[str] = []
    for check in verification.checks:
        path = verifier_dir.joinpath(*PurePosixPath(check.log_path).parts)
        if path.is_symlink() or not path.is_file():
            errors.append(f"verifier check log is missing: {check.log_path}")
        elif sha256_file(path) != check.log_sha256:
            errors.append(f"verifier check log digest mismatch: {check.log_path}")
    return errors


def _validate_reward_artifact(
    trial_dir: Path,
    task: TaskContract,
    rewards: dict[str, float | int] | None,
) -> list[str]:
    reward_path = trial_dir / "verifier" / PurePosixPath(task.verifier.reward_path).name
    if reward_path.is_symlink() or not reward_path.is_file():
        return ["trusted verifier reward artifact is missing"]
    try:
        raw = json.loads(reward_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"trusted verifier reward artifact is invalid: {exc}"]
    if raw != rewards:
        return ["trusted verifier reward artifact does not match Harbor result"]
    return []


def _artifact_digests(bundle_dir: Path) -> list[ArtifactDigest]:
    excluded = {bundle_dir / "result.json"}
    return [
        ArtifactDigest(
            path=path.relative_to(bundle_dir).as_posix(),
            sha256=(
                sha256_bytes(b"slopbench.symlink.v1\0" + os.fsencode(os.readlink(path)))
                if path.is_symlink()
                else sha256_file(path)
            ),
        )
        for path in sorted(bundle_dir.rglob("*"))
        if (path.is_file() or path.is_symlink()) and path not in excluded
    ]


def _not_applicable_outcomes() -> list[GateOutcome]:
    return [
        GateOutcome(gate=gate, status=OutcomeStatus.NOT_APPLICABLE, check_ids=[])
        for gate in GateName
    ]


def _classify_exception(exception_type: str) -> tuple[FailureClassification, FailureReason]:
    provider_reason = _PROVIDER_FAILURE_EXCEPTIONS.get(exception_type)
    if provider_reason is not None:
        return FailureClassification.INFRASTRUCTURE_FAILURE, provider_reason
    if exception_type in _AGENT_FAILURE_EXCEPTIONS:
        reasons = {"AgentTimeoutError": FailureReason.AGENT_TIMEOUT}
        return (
            FailureClassification.VALID_AGENT_FAILURE,
            reasons.get(exception_type, FailureReason.AGENT_EXIT),
        )
    if exception_type == "AgentSetupTimeoutError":
        return (
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.AGENT_SETUP_TIMEOUT,
        )
    if exception_type in _BENCHMARK_FAILURE_EXCEPTIONS:
        reason = (
            FailureReason.VERIFIER_TIMEOUT
            if exception_type == "VerifierTimeoutError"
            else FailureReason.VERIFIER_EVIDENCE_INVALID
        )
        return FailureClassification.BENCHMARK_DEFECT, reason
    if exception_type == "EnvironmentStartTimeoutError":
        return (
            FailureClassification.INFRASTRUCTURE_FAILURE,
            FailureReason.ENVIRONMENT_START_TIMEOUT,
        )
    return FailureClassification.INFRASTRUCTURE_FAILURE, FailureReason.HARBOR_EXCEPTION


def _retry_disposition(
    manifest: RunManifest,
    classification: FailureClassification,
    failure_reason: FailureReason,
) -> RetryDisposition:
    remaining = manifest.retry_policy.max_attempts - manifest.trial.attempt
    if classification != FailureClassification.INFRASTRUCTURE_FAILURE:
        decision = RetryDecision.CLASSIFICATION_NOT_RETRYABLE
    elif failure_reason.value not in {
        reason.value for reason in manifest.retry_policy.retryable_reasons
    }:
        decision = RetryDecision.REASON_NOT_ALLOWED
    elif remaining == 0:
        decision = RetryDecision.ATTEMPTS_EXHAUSTED
    else:
        decision = RetryDecision.RETRY_ALLOWED
    return RetryDisposition(
        eligible=decision == RetryDecision.RETRY_ALLOWED,
        decision=decision,
        remaining_attempts=remaining,
    )


def _harbor_evidence(trial_dir: Path, result: TrialResult | None) -> HarborEvidence:
    result_path = trial_dir / "result.json"
    config_path = trial_dir / "config.json"
    trajectory_path = trial_dir / "agent" / "trajectory.json"
    return HarborEvidence(
        version=_runtime_version(),
        task_checksum=result.task_checksum if result else None,
        result_sha256=sha256_file(result_path) if result_path.is_file() else None,
        config_sha256=sha256_file(config_path) if config_path.is_file() else None,
        trajectory_sha256=(sha256_file(trajectory_path) if trajectory_path.is_file() else None),
    )


def _load_trial_result(trial_dir: Path) -> TrialResult | None:
    path = trial_dir / "result.json"
    if not path.is_file():
        return None
    try:
        return TrialResult.model_validate_json(path.read_text())
    except Exception as exc:
        raise ContractError(f"invalid Harbor result at {path}: {exc}") from exc


def _finalize(
    bundle_dir: Path,
    manifest: RunManifest,
    task: TaskContract,
    task_digest: str,
    run_manifest_sha256: str,
    process_exit_code: int,
) -> ResultBundle:
    trial_dir = bundle_dir / "harbor" / manifest.trial.id
    trial_result_error: str | None = None
    try:
        trial_result = _load_trial_result(trial_dir)
    except ContractError as exc:
        trial_result = None
        trial_result_error = str(exc)
    harbor = _harbor_evidence(trial_dir, trial_result)
    usage = UsageMetrics()
    timing = TimingMetrics(started_at=None, finished_at=None, duration_seconds=None)
    receipt = ReceiptValidation(
        present=False,
        valid=False,
        sha256=None,
        errors=[trial_result_error or "run did not reach receipt validation"],
    )
    classification = FailureClassification.INFRASTRUCTURE_FAILURE
    failure_reason = (
        FailureReason.HARBOR_RESULT_INVALID
        if trial_result_error
        else FailureReason.HARBOR_PROCESS_FAILURE
    )
    outcomes = _not_applicable_outcomes()

    if trial_result is not None:
        input_tokens, cache_tokens, output_tokens, cost_usd = (
            trial_result.compute_token_cost_totals()
        )
        usage = UsageMetrics(
            input_tokens=input_tokens,
            cache_tokens=cache_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        timing = TimingMetrics(
            started_at=(trial_result.started_at.isoformat() if trial_result.started_at else None),
            finished_at=(
                trial_result.finished_at.isoformat() if trial_result.finished_at else None
            ),
            duration_seconds=_duration_seconds(trial_result.started_at, trial_result.finished_at),
        )
        if trial_result.exception_info is not None:
            classification, failure_reason = _classify_exception(
                trial_result.exception_info.exception_type
            )
        else:
            if trial_result.task_checksum != manifest.task.harbor_task_checksum:
                classification = FailureClassification.BENCHMARK_DEFECT
                failure_reason = FailureReason.HARBOR_TASK_MISMATCH
                receipt = receipt.model_copy(
                    update={"errors": ["Harbor executed task checksum does not match the run"]}
                )
            else:
                verification_path = (
                    trial_dir / "verifier" / PurePosixPath(task.verifier.evidence_path).name
                )
                if verification_path.is_symlink():
                    classification = FailureClassification.BENCHMARK_DEFECT
                    failure_reason = FailureReason.VERIFIER_EVIDENCE_INVALID
                    receipt = receipt.model_copy(
                        update={"errors": ["trusted verifier evidence must not be a symlink"]}
                    )
                elif not verification_path.is_file():
                    classification = FailureClassification.BENCHMARK_DEFECT
                    failure_reason = FailureReason.VERIFIER_EVIDENCE_MISSING
                    receipt = receipt.model_copy(
                        update={"errors": ["trusted verifier evidence is missing"]}
                    )
                else:
                    try:
                        verification = load_model(verification_path, VerificationEvidence)
                    except ContractError as exc:
                        classification = FailureClassification.BENCHMARK_DEFECT
                        failure_reason = FailureReason.VERIFIER_EVIDENCE_INVALID
                        receipt = receipt.model_copy(update={"errors": [str(exc)]})
                    else:
                        contract_errors: list[str] = []
                        if verification.task_digest != task_digest:
                            contract_errors.append("verifier task digest mismatch")
                        if verification.base_revision != task.environment.base_revision:
                            contract_errors.append("verifier base revision mismatch")
                        contract_errors.extend(_validate_verification_logs(trial_dir, verification))
                        if contract_errors:
                            classification = FailureClassification.BENCHMARK_DEFECT
                            failure_reason = FailureReason.VERIFIER_CONTRACT_MISMATCH
                            receipt = receipt.model_copy(update={"errors": contract_errors})
                        else:
                            receipt, _, invalid_receipt = _validate_receipt(
                                _receipt_path(trial_dir), task, verification
                            )
                            trusted_outcomes, benchmark_errors = _check_outcomes(
                                task, verification, True
                            )
                            outcomes, receipt_errors = _check_outcomes(
                                task, verification, receipt.valid
                            )
                            benchmark_errors.extend(receipt_errors)
                            rewards = (
                                trial_result.verifier_result.rewards
                                if trial_result.verifier_result
                                else None
                            )
                            reward_errors = _validate_rewards(trusted_outcomes, rewards)
                            reward_errors.extend(
                                _validate_reward_artifact(trial_dir, task, rewards)
                            )
                            if benchmark_errors or reward_errors:
                                classification = FailureClassification.BENCHMARK_DEFECT
                                failure_reason = (
                                    FailureReason.REWARD_MISMATCH
                                    if reward_errors
                                    else FailureReason.VERIFIER_CONTRACT_MISMATCH
                                )
                                receipt = receipt.model_copy(
                                    update={
                                        "errors": [
                                            *receipt.errors,
                                            *benchmark_errors,
                                            *reward_errors,
                                        ]
                                    }
                                )
                            elif invalid_receipt:
                                classification = FailureClassification.INVALID_RUN
                                failure_reason = FailureReason.RECEIPT_INVALID
                            elif all(
                                outcome.status
                                in {OutcomeStatus.PASSED, OutcomeStatus.NOT_APPLICABLE}
                                for outcome in outcomes
                            ):
                                classification = FailureClassification.VALID_PASS
                                failure_reason = FailureReason.NONE
                            else:
                                classification = FailureClassification.VALID_AGENT_FAILURE
                                failure_reason = (
                                    FailureReason.RECEIPT_MISSING
                                    if not receipt.present
                                    else FailureReason.GATE_FAILURE
                                )

        if process_exit_code != 0 and trial_result.exception_info is None:
            classification = FailureClassification.INFRASTRUCTURE_FAILURE
            failure_reason = FailureReason.HARBOR_PROCESS_FAILURE
            receipt = receipt.model_copy(
                update={
                    "errors": [
                        *receipt.errors,
                        f"Harbor process exited {process_exit_code} despite a completed result",
                    ]
                }
            )

    if process_exit_code != 0 and trial_result is None:
        process_error = f"Harbor process exited {process_exit_code} without a result"
        receipt = receipt.model_copy(
            update={
                "errors": (
                    [*receipt.errors, process_error] if trial_result_error else [process_error]
                )
            }
        )

    completed = classification == FailureClassification.VALID_PASS
    retry = _retry_disposition(manifest, classification, failure_reason)
    result = ResultBundle(
        schema_version=RESULT_SCHEMA_VERSION,
        run_id=manifest.run_id,
        task_digest=task_digest,
        run_manifest_sha256=run_manifest_sha256,
        classification=classification,
        failure_reason=failure_reason,
        completed=completed,
        attempt=manifest.trial.attempt,
        retry=retry,
        outcomes=outcomes,
        receipt=receipt,
        usage=usage,
        timing=timing,
        harbor=harbor,
        artifacts=_artifact_digests(bundle_dir),
    )
    write_model(bundle_dir / "result.json", result)
    return result


def execute_run(
    task_dir: Path,
    manifest_path: Path,
    output_root: Path,
    *,
    process_runner: ProcessRunner = _default_process_runner,
) -> tuple[ResultBundle, Path]:
    task_dir = task_dir.resolve()
    manifest_path = manifest_path.resolve()
    output_root = output_root.resolve()
    if output_root == task_dir or output_root.is_relative_to(task_dir):
        raise RunError("run output must be outside the sealed task directory")
    task, contract_sha256, task_digest = validate_task(task_dir)
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = RunManifest.model_validate_json(manifest_bytes)
    except ValidationError as exc:
        raise ContractError(f"invalid RunManifest at {manifest_path}: {exc}") from exc
    _validate_run_binding(
        manifest,
        task,
        contract_sha256,
        task_digest,
        task_dir,
    )
    bundle_dir = output_root / manifest.run_id
    if bundle_dir.exists():
        raise RunError(f"run output already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True)
    copied_manifest = bundle_dir / "slopbench-run.json"
    copied_manifest.write_bytes(manifest_bytes)
    manifest_sha256 = sha256_file(copied_manifest)
    task_snapshot = bundle_dir / "inputs" / "task"
    _snapshot_task(
        task_dir,
        task_snapshot,
        contract_sha256,
        task_digest,
        manifest.task.harbor_task_checksum,
    )
    harbor_dir = bundle_dir / "harbor"
    harbor_dir.mkdir()
    harbor_config = _harbor_config(manifest, task, task_snapshot, harbor_dir, task_digest)
    harbor_config_path = bundle_dir / "harbor-input.json"
    _write_harbor_config(harbor_config_path, harbor_config)
    command = [
        str(_harbor_executable()),
        "trial",
        "start",
        "--config",
        str(harbor_config_path),
    ]
    process_exit_code = process_runner(
        command,
        task_snapshot.parent,
        _process_environment(manifest),
        bundle_dir / "harbor.stdout.log",
        bundle_dir / "harbor.stderr.log",
    )
    result = _finalize(
        bundle_dir,
        manifest,
        task,
        task_digest,
        manifest_sha256,
        process_exit_code,
    )
    return result, bundle_dir
