"""Thin Harbor invocation and deterministic result finalization."""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath

from harbor.models.environment_type import EnvironmentType
from harbor.models.task.config import VerifierEnvironmentMode
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
    TrialConfig,
    VerifierConfig,
)
from harbor.models.trial.config import (
    TaskConfig as HarborTaskConfig,
)
from harbor.models.trial.result import TrialResult

from slopbench.contracts import (
    RESULT_SCHEMA_VERSION,
    AgentReport,
    ArtifactDigest,
    ClaimStatus,
    FailureClassification,
    GateName,
    GateOutcome,
    HarborEvidence,
    OutcomeStatus,
    ReceiptValidation,
    ResultBundle,
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
    "AgentSetupTimeoutError",
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
            env=manifest.agent.environment,
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
        ),
        verifier=VerifierConfig(
            override_timeout_sec=manifest.limits.verifier_timeout_sec,
            include_logs=["reward.json", "slopbench-verification.json", "test-*.txt"],
            env={
                "SLOPBENCH_TASK_DIGEST": task_digest,
                "SLOPBENCH_VERIFIER_ISOLATION": task.environment.verifier_isolation,
            },
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
    if report.final_revision != verification.final_revision:
        errors.append("final_revision does not match verifier evidence")
    claims = {claim.gate: claim for claim in report.claims}
    for gate in task.applicable_gates:
        claim = claims.get(gate)
        if claim is None:
            errors.append(f"missing claim for applicable gate: {gate.value}")
            continue
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


def _artifact_digests(bundle_dir: Path) -> list[ArtifactDigest]:
    excluded = {bundle_dir / "result.json"}
    return [
        ArtifactDigest(
            path=path.relative_to(bundle_dir).as_posix(),
            sha256=sha256_file(path),
        )
        for path in sorted(bundle_dir.rglob("*"))
        if path.is_file() and path not in excluded
    ]


def _not_applicable_outcomes() -> list[GateOutcome]:
    return [
        GateOutcome(gate=gate, status=OutcomeStatus.NOT_APPLICABLE, check_ids=[])
        for gate in GateName
    ]


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
            if trial_result.exception_info.exception_type in _AGENT_FAILURE_EXCEPTIONS:
                classification = FailureClassification.VALID_AGENT_FAILURE
        else:
            verification_path = trial_dir / "verifier" / "slopbench-verification.json"
            if not verification_path.is_file():
                classification = FailureClassification.BENCHMARK_DEFECT
                receipt = receipt.model_copy(
                    update={"errors": ["trusted verifier evidence is missing"]}
                )
            else:
                try:
                    verification = load_model(verification_path, VerificationEvidence)
                except ContractError as exc:
                    classification = FailureClassification.BENCHMARK_DEFECT
                    receipt = receipt.model_copy(update={"errors": [str(exc)]})
                else:
                    if verification.task_digest != task_digest:
                        classification = FailureClassification.BENCHMARK_DEFECT
                        receipt = receipt.model_copy(
                            update={"errors": ["verifier task digest mismatch"]}
                        )
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
                        reward_errors = _validate_rewards(
                            trusted_outcomes,
                            (
                                trial_result.verifier_result.rewards
                                if trial_result.verifier_result
                                else None
                            ),
                        )
                        if benchmark_errors or reward_errors:
                            classification = FailureClassification.BENCHMARK_DEFECT
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
                        elif all(
                            outcome.status in {OutcomeStatus.PASSED, OutcomeStatus.NOT_APPLICABLE}
                            for outcome in outcomes
                        ):
                            classification = FailureClassification.VALID_PASS
                        else:
                            classification = FailureClassification.VALID_AGENT_FAILURE

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
    result = ResultBundle(
        schema_version=RESULT_SCHEMA_VERSION,
        run_id=manifest.run_id,
        task_digest=task_digest,
        run_manifest_sha256=run_manifest_sha256,
        classification=classification,
        completed=completed,
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
    task, contract_sha256, task_digest = validate_task(task_dir)
    manifest = load_model(manifest_path, RunManifest)
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
    copied_manifest.write_bytes(manifest_path.read_bytes())
    manifest_sha256 = sha256_file(copied_manifest)
    harbor_dir = bundle_dir / "harbor"
    harbor_dir.mkdir()
    harbor_config = _harbor_config(manifest, task, task_dir, harbor_dir, task_digest)
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
        task_dir.parent,
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
