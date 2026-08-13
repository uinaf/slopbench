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
    external_writes: Literal["none"] = "none"
    live_credentials: Literal[False] = False

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tools must be unique")
        return value


class EnvironmentContract(ContractModel):
    harbor_task_path: str = "."
    verifier_isolation: Literal["shared", "separate"]
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
        if not value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("container output path must be absolute and canonical")
        return value


class Provenance(ContractModel):
    origin: Literal["slopbench-authored", "public-upstream", "transformed-private"]
    source_url: str | None = None
    source_revision: str | None = None
    transformed: bool = False
    ai_assistance: str | None = None


class LicenseContract(ContractModel):
    spdx: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")]
    holder: str


class TaskContract(ContractModel):
    schema_version: Literal["slopbench.task.v1"] = TASK_SCHEMA_VERSION
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
        }
        missing = required_paths - declared_paths
        if self.immutable_inputs and missing:
            raise ValueError(f"immutable_inputs is missing required paths: {sorted(missing)}")
        return self


class TaskBinding(ContractModel):
    contract_path: str
    contract_sha256: Sha256Hex
    task_digest: Sha256Hex
    task_id: str
    task_version: Version

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


class RunManifest(ContractModel):
    schema_version: Literal["slopbench.run.v1"] = RUN_SCHEMA_VERSION
    run_id: Identifier
    task: TaskBinding
    agent: AgentConfiguration
    runtime: RuntimeConfiguration
    limits: RunLimits
    trial: TrialIdentity

    @model_validator(mode="after")
    def matching_identity(self) -> Self:
        if self.run_id != self.trial.id:
            raise ValueError("run_id and trial.id must match")
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
    schema_version: Literal["slopbench.report.v1"] = REPORT_SCHEMA_VERSION
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
        return self


class CheckEvidence(ContractModel):
    id: Identifier
    gate: GateName
    passed: bool
    command: str
    exit_code: int


class VerificationEvidence(ContractModel):
    schema_version: Literal["slopbench.verification.v1"] = VERIFICATION_SCHEMA_VERSION
    task_digest: Sha256Hex
    base_revision: GitRevision
    final_revision: Revision
    checks: list[CheckEvidence]

    @model_validator(mode="after")
    def unique_checks(self) -> Self:
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("verification check ids must be unique")
        return self


class OutcomeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class GateOutcome(ContractModel):
    gate: GateName
    status: OutcomeStatus
    check_ids: list[Identifier]


class FailureClassification(StrEnum):
    VALID_PASS = "valid_pass"
    VALID_AGENT_FAILURE = "valid_agent_failure"
    BENCHMARK_DEFECT = "benchmark_defect"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    INVALID_RUN = "invalid_run"


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


class ResultBundle(ContractModel):
    schema_version: Literal["slopbench.result.v1"] = RESULT_SCHEMA_VERSION
    run_id: Identifier
    task_digest: Sha256Hex
    run_manifest_sha256: Sha256Hex
    classification: FailureClassification
    completed: bool
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
        return self
