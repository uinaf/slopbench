"""Versioned SlopBench boundary models."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

TASK_SCHEMA_VERSION: Literal["slopbench.task.v1"] = "slopbench.task.v1"
RUN_SCHEMA_VERSION: Literal["slopbench.run.v1"] = "slopbench.run.v1"
REPORT_SCHEMA_VERSION: Literal["slopbench.report.v1"] = "slopbench.report.v1"
VERIFICATION_SCHEMA_VERSION: Literal["slopbench.verification.v1"] = "slopbench.verification.v1"
RESULT_SCHEMA_VERSION: Literal["slopbench.result.v1"] = "slopbench.result.v1"

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
GitRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
Version = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")]
EnvName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(api[_-]?key|secret|password|passwd|token|credential|private[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)


class ContractModel(BaseModel):
    """Reject undeclared or coerced external input."""

    model_config = ConfigDict(extra="forbid", strict=True)


def validate_relative_path(value: str) -> str:
    """Accept canonical repository-relative POSIX paths only."""

    if not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("path must stay within the task directory")
    if candidate.as_posix() != value:
        raise ValueError("path must be canonical")
    return value


def _reject_sensitive_values(value: JsonValue, location: str = "settings") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY.search(key):
                raise ValueError(
                    f"{location}.{key} looks sensitive; use credential_env references instead"
                )
            _reject_sensitive_values(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_values(child, f"{location}[{index}]")


class GateName(StrEnum):
    REQUESTED_BEHAVIOR = "requested_behavior"
    REGRESSIONS = "regressions"
    BUILD_AND_TYPES = "build_and_types"
    AUTHORITY = "authority"
    VERIFIER_INTEGRITY = "verifier_integrity"
    SAFETY_TYPE_ESCAPES = "safety_type_escapes"
    EVIDENCE_RECEIPT = "evidence_receipt"


class FailureClassification(StrEnum):
    VALID_PASS = "valid_pass"
    VALID_AGENT_FAILURE = "valid_agent_failure"
    BENCHMARK_DEFECT = "benchmark_defect"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    INVALID_RUN = "invalid_run"


class FailureReason(StrEnum):
    NONE = "none"
    GATE_FAILURE = "gate_failure"
    RECEIPT_MISSING = "receipt_missing"
    RECEIPT_INVALID = "receipt_invalid"
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_SETUP_TIMEOUT = "agent_setup_timeout"
    AGENT_EXIT = "agent_exit"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_USAGE_LIMIT = "provider_usage_limit"
    ENVIRONMENT_START_TIMEOUT = "environment_start_timeout"
    VERIFIER_TIMEOUT = "verifier_timeout"
    VERIFIER_EVIDENCE_MISSING = "verifier_evidence_missing"
    VERIFIER_EVIDENCE_INVALID = "verifier_evidence_invalid"
    VERIFIER_CONTRACT_MISMATCH = "verifier_contract_mismatch"
    HARBOR_TASK_MISMATCH = "harbor_task_mismatch"
    REWARD_MISMATCH = "reward_mismatch"
    HARBOR_PROCESS_FAILURE = "harbor_process_failure"
    HARBOR_RESULT_INVALID = "harbor_result_invalid"
    HARBOR_EXCEPTION = "harbor_exception"


class RetryableReason(StrEnum):
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    ENVIRONMENT_START_TIMEOUT = "environment_start_timeout"


class RetryDecision(StrEnum):
    RETRY_ALLOWED = "retry_allowed"
    CLASSIFICATION_NOT_RETRYABLE = "classification_not_retryable"
    REASON_NOT_ALLOWED = "reason_not_allowed"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class AttackKind(StrEnum):
    VERIFIER_TAMPERING = "verifier_tampering"
    HIDDEN_MATERIAL_ACCESS = "hidden_material_access"
    PROTECTED_DEPENDENCY_CHANGE = "protected_dependency_change"
    HARDCODED_FIXTURE_OUTPUT = "hardcoded_fixture_output"
    BEHAVIOR_BYPASS = "behavior_bypass"
    FABRICATED_RECEIPT = "fabricated_receipt"
    UNAUTHORIZED_NETWORK = "unauthorized_network"
    GRADER_EXPLOITATION = "grader_exploitation"


class TaskKind(StrEnum):
    PATCH = "patch"
    REVIEW = "review"


class PhaseMode(StrEnum):
    SINGLE = "single"
    SEQUENTIAL = "sequential"


class FileDigest(ContractModel):
    path: str
    sha256: Sha256Hex

    _path = field_validator("path")(validate_relative_path)


class PhaseContract(ContractModel):
    name: Identifier
    instruction_path: str
    context: Literal["fresh"] = "fresh"

    _instruction_path = field_validator("instruction_path")(validate_relative_path)


class CapabilityEnvelope(ContractModel):
    repository: Literal["read-write", "read-only"]
    shell: bool
    tests: bool
    tools: list[Identifier] = Field(default_factory=list)
    network: Literal["none", "model-only", "declared"]
    network_allowed_hosts: list[str] = Field(default_factory=list)
    environment: list[EnvName] = Field(default_factory=list)
    external_writes: Literal["none"] = "none"
    live_credentials: Literal[False] = False

    @field_validator("tools", "environment")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("capability values must be unique")
        return value

    @field_validator("network_allowed_hosts")
    @classmethod
    def valid_network_hosts(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("network_allowed_hosts must be unique")
        for host in value:
            candidate = host[2:] if host.startswith("*.") else host
            if (
                not candidate
                or candidate != candidate.lower().rstrip(".")
                or "://" in candidate
                or "/" in candidate
                or ":" in candidate
                or "*" in candidate
                or any(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                    for label in candidate.split(".")
                )
            ):
                raise ValueError(f"invalid network host pattern: {host}")
        return value

    @model_validator(mode="after")
    def validate_network(self) -> Self:
        if self.network == "none" and self.network_allowed_hosts:
            raise ValueError("network_allowed_hosts must be empty when network is none")
        if self.network != "none" and not self.network_allowed_hosts:
            raise ValueError("network_allowed_hosts is required for network access")
        return self


class EnvironmentContract(ContractModel):
    harbor_task_path: str = "."
    verifier_isolation: Literal["separate"]
    base_revision: GitRevision
    cpus: int = Field(ge=1)
    memory_mb: int = Field(ge=128)
    storage_mb: int = Field(ge=256)

    @field_validator("harbor_task_path")
    @classmethod
    def valid_task_path(cls, value: str) -> str:
        if value == ".":
            return value
        return validate_relative_path(value)


class VerifierContract(ContractModel):
    kind: Literal["deterministic"] = "deterministic"
    entrypoint: str
    evidence_path: str
    reward_path: str

    _entrypoint = field_validator("entrypoint")(validate_relative_path)

    @field_validator("evidence_path", "reward_path")
    @classmethod
    def absolute_container_path(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            not candidate.is_absolute()
            or candidate.as_posix() != value
            or ".." in candidate.parts
            or candidate.parent != PurePosixPath("/logs/verifier")
        ):
            raise ValueError("container output path must be a canonical file below /logs/verifier")
        return value

    @model_validator(mode="after")
    def distinct_outputs(self) -> Self:
        if self.evidence_path == self.reward_path:
            raise ValueError("verifier evidence and reward paths must be distinct")
        return self


class Provenance(ContractModel):
    origin: Literal["slopbench-authored", "public-upstream", "transformed-private"]
    source_url: str | None = None
    source_revision: str | None = None
    transformed: bool = False
    ai_assistance: str | None = None


class LicenseContract(ContractModel):
    spdx: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")]
    holder: str


class AttackExpectation(ContractModel):
    classification: Literal["valid_agent_failure", "invalid_run"]
    failed_gates: list[GateName] = Field(min_length=1)

    @field_validator("failed_gates")
    @classmethod
    def unique_failed_gates(cls, value: list[GateName]) -> list[GateName]:
        if len(value) != len(set(value)):
            raise ValueError("failed_gates must be unique")
        return value


class AttackFixture(ContractModel):
    id: Identifier
    kind: AttackKind
    entrypoint: str
    expected: AttackExpectation

    _entrypoint = field_validator("entrypoint")(validate_relative_path)


class TaskContract(ContractModel):
    schema_version: Literal["slopbench.task.v1"]
    task_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$")]
    version: Version
    kind: TaskKind
    phase_mode: PhaseMode
    phases: list[PhaseContract] = Field(min_length=1)
    environment: EnvironmentContract
    verifier: VerifierContract
    capabilities: CapabilityEnvelope
    applicable_gates: list[GateName] = Field(min_length=1)
    provenance: Provenance
    license: LicenseContract
    attack_fixtures: list[AttackFixture] = Field(default_factory=list)
    immutable_inputs: list[FileDigest] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        phase_names = [phase.name for phase in self.phases]
        if len(phase_names) != len(set(phase_names)):
            raise ValueError("phase names must be unique")
        expected_mode = PhaseMode.SINGLE if len(self.phases) == 1 else PhaseMode.SEQUENTIAL
        if self.phase_mode != expected_mode:
            raise ValueError(
                f"phase_mode must be {expected_mode.value!r} for {len(self.phases)} phase(s)"
            )
        if len(self.applicable_gates) != len(set(self.applicable_gates)):
            raise ValueError("applicable_gates must be unique")
        input_paths = [item.path for item in self.immutable_inputs]
        if len(input_paths) != len(set(input_paths)):
            raise ValueError("immutable input paths must be unique")
        declared_paths = set(input_paths)
        required_paths = {
            self.verifier.entrypoint,
            *(phase.instruction_path for phase in self.phases),
            *(fixture.entrypoint for fixture in self.attack_fixtures),
        }
        missing = required_paths - declared_paths
        if self.immutable_inputs and missing:
            raise ValueError(f"immutable_inputs is missing required paths: {sorted(missing)}")
        fixture_ids = [fixture.id for fixture in self.attack_fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("attack fixture ids must be unique")
        for fixture in self.attack_fixtures:
            unexpected = set(fixture.expected.failed_gates) - set(self.applicable_gates)
            if unexpected:
                raise ValueError(
                    f"attack fixture {fixture.id} expects non-applicable gates: "
                    f"{sorted(gate.value for gate in unexpected)}"
                )
        return self


class TaskBinding(ContractModel):
    contract_path: str
    contract_sha256: Sha256Hex
    task_digest: Sha256Hex
    task_id: str
    task_version: Version
    harbor_task_checksum: Sha256Hex

    _contract_path = field_validator("contract_path")(validate_relative_path)


class ModelPin(ContractModel):
    provider: Identifier
    name: Identifier

    @property
    def harbor_name(self) -> str:
        return f"{self.provider}/{self.name}"


class ToolPin(ContractModel):
    name: Identifier
    version: str
    settings: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def settings_are_non_secret(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_sensitive_values(value, "tool.settings")
        return value


class InstructionLayer(ContractModel):
    name: Identifier
    path: str
    sha256: Sha256Hex

    _path = field_validator("path")(validate_relative_path)


class AgentConfiguration(ContractModel):
    harness: Identifier
    harness_version: str
    model: ModelPin | None
    effort_tier: Identifier
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    environment: dict[EnvName, str] = Field(default_factory=dict)
    tools: list[ToolPin] = Field(default_factory=list)
    instruction_layers: list[InstructionLayer] = Field(default_factory=list)
    credential_env: list[EnvName] = Field(default_factory=list)

    @field_validator("settings")
    @classmethod
    def settings_are_non_secret(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_sensitive_values(value, "agent.settings")
        return value

    @field_validator("environment")
    @classmethod
    def environment_is_non_secret(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if _SENSITIVE_KEY.search(key):
                raise ValueError(
                    f"agent.environment.{key} looks sensitive; use credential_env instead"
                )
        return value

    @model_validator(mode="after")
    def validate_agent(self) -> Self:
        if self.harness not in {"oracle", "nop"} and self.model is None:
            raise ValueError("model is required for non-utility harnesses")
        if len(self.credential_env) != len(set(self.credential_env)):
            raise ValueError("credential_env values must be unique")
        reserved = {"SLOPBENCH_TASK_DIGEST", "SLOPBENCH_ATTACK_FIXTURE"}
        if reserved & self.environment.keys():
            raise ValueError("agent.environment contains runner-reserved values")
        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("tool names must be unique")
        layer_names = [layer.name for layer in self.instruction_layers]
        if len(layer_names) != len(set(layer_names)):
            raise ValueError("instruction layer names must be unique")
        return self


class ImagePin(ContractModel):
    role: Identifier
    reference: Annotated[str, Field(pattern=r"^.+@sha256:[0-9a-f]{64}$")]


class RuntimeConfiguration(ContractModel):
    harbor_version: Version
    environment_provider: Identifier
    environment_provider_version: str
    images: list[ImagePin] = Field(min_length=1)
    cpus: int = Field(ge=1)
    memory_mb: int = Field(ge=128)
    storage_mb: int = Field(ge=256)


class RunLimits(ContractModel):
    agent_timeout_sec: int = Field(ge=1)
    agent_setup_timeout_sec: int = Field(ge=1)
    verifier_timeout_sec: int = Field(ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TrialIdentity(ContractModel):
    id: Identifier
    attempt: int = Field(ge=1)
    seed: int | None = None


class RetryPolicy(ContractModel):
    max_attempts: int = Field(ge=1, le=3)
    retryable_reasons: list[RetryableReason] = Field(default_factory=list)

    @field_validator("retryable_reasons")
    @classmethod
    def unique_retryable_reasons(cls, value: list[RetryableReason]) -> list[RetryableReason]:
        if len(value) != len(set(value)):
            raise ValueError("retryable_reasons must be unique")
        return value


class RunManifest(ContractModel):
    schema_version: Literal["slopbench.run.v1"]
    run_id: Identifier
    task: TaskBinding
    agent: AgentConfiguration
    runtime: RuntimeConfiguration
    limits: RunLimits
    trial: TrialIdentity
    retry_policy: RetryPolicy
    attack_fixture_id: Identifier | None = None

    @model_validator(mode="after")
    def matching_identity(self) -> Self:
        if self.run_id != self.trial.id:
            raise ValueError("run_id and trial.id must match")
        if self.trial.attempt > self.retry_policy.max_attempts:
            raise ValueError("trial.attempt exceeds retry_policy.max_attempts")
        return self


class ClaimStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class Claim(ContractModel):
    gate: GateName
    status: ClaimStatus
    evidence_ids: list[Identifier] = Field(min_length=1)


class CommandClaim(ContractModel):
    id: Identifier
    command: str = Field(min_length=1)
    exit_code: int


class Uncertainty(ContractModel):
    code: Identifier
    detail: str = Field(min_length=1)


class AgentReport(ContractModel):
    schema_version: Literal["slopbench.report.v1"]
    task_digest: Sha256Hex
    base_revision: GitRevision
    claims: list[Claim]
    commands: list[CommandClaim]
    uncertainty: list[Uncertainty]
    final_revision: Revision

    @model_validator(mode="after")
    def unique_entries(self) -> Self:
        gates = [claim.gate for claim in self.claims]
        if len(gates) != len(set(gates)):
            raise ValueError("claims must contain at most one entry per gate")
        command_ids = [command.id for command in self.commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command ids must be unique")
        for claim in self.claims:
            if len(claim.evidence_ids) != len(set(claim.evidence_ids)):
                raise ValueError("claim evidence_ids must be unique")
        return self


class CheckEvidence(ContractModel):
    id: Identifier
    gate: GateName
    passed: bool
    command: str
    exit_code: int
    log_path: str
    log_sha256: Sha256Hex

    _log_path = field_validator("log_path")(validate_relative_path)


class VerificationEvidence(ContractModel):
    schema_version: Literal["slopbench.verification.v1"]
    task_digest: Sha256Hex
    base_revision: GitRevision
    final_revision: Revision
    checks: list[CheckEvidence]

    @model_validator(mode="after")
    def unique_checks(self) -> Self:
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("verification check ids must be unique")
        log_paths = [check.log_path for check in self.checks]
        if len(log_paths) != len(set(log_paths)):
            raise ValueError("verification check log paths must be unique")
        return self


class OutcomeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class GateOutcome(ContractModel):
    gate: GateName
    status: OutcomeStatus
    check_ids: list[Identifier]


class ReceiptValidation(ContractModel):
    present: bool
    valid: bool
    sha256: Sha256Hex | None
    errors: list[str]


class UsageMetrics(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TimingMetrics(ContractModel):
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ArtifactDigest(ContractModel):
    path: str
    sha256: Sha256Hex

    _path = field_validator("path")(validate_relative_path)


class HarborEvidence(ContractModel):
    version: Version
    task_checksum: str | None
    result_sha256: Sha256Hex | None
    config_sha256: Sha256Hex | None
    trajectory_sha256: Sha256Hex | None


class RetryDisposition(ContractModel):
    eligible: bool
    decision: RetryDecision
    remaining_attempts: int = Field(ge=0)


class ResultBundle(ContractModel):
    schema_version: Literal["slopbench.result.v1"]
    run_id: Identifier
    task_digest: Sha256Hex
    run_manifest_sha256: Sha256Hex
    classification: FailureClassification
    failure_reason: FailureReason
    completed: bool
    attempt: int = Field(ge=1)
    retry: RetryDisposition
    outcomes: list[GateOutcome]
    receipt: ReceiptValidation
    usage: UsageMetrics
    timing: TimingMetrics
    harbor: HarborEvidence
    artifacts: list[ArtifactDigest]

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        gates = [outcome.gate for outcome in self.outcomes]
        if set(gates) != set(GateName) or len(gates) != len(GateName):
            raise ValueError("outcomes must contain exactly one entry per gate")
        if self.completed != (self.classification == FailureClassification.VALID_PASS):
            raise ValueError("completed must be true exactly for valid_pass")
        if (self.failure_reason == FailureReason.NONE) != (
            self.classification == FailureClassification.VALID_PASS
        ):
            raise ValueError("failure_reason must be none exactly for valid_pass")
        if self.retry.eligible != (
            self.classification == FailureClassification.INFRASTRUCTURE_FAILURE
            and self.retry.decision == RetryDecision.RETRY_ALLOWED
        ):
            raise ValueError("retry eligibility is inconsistent with classification and decision")
        return self
