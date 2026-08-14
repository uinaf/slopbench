"""Versioned task sets, profiles, publication results, and held-out lifecycle."""

from __future__ import annotations

import base64
import binascii
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from slopbench.contracts import (
    _SENSITIVE_KEY,
    AgentConfiguration,
    AgentReport,
    ArtifactDigest,
    CapabilityCategory,
    CapabilityEnvelope,
    ContractModel,
    EnvName,
    FailureClassification,
    FailureReason,
    GateName,
    GateOutcome,
    HarborEvidence,
    LicenseContract,
    ModelPin,
    OutcomeStatus,
    Provenance,
    ReceiptValidation,
    ResultBundle,
    RunLimits,
    RunManifest,
    RuntimeConfiguration,
    Sha256Hex,
    TaskBinding,
    TaskKind,
    TimingMetrics,
    ToolPin,
    TrialIdentity,
    Uncertainty,
    UsageMetrics,
    Version,
    _reject_sensitive_values,
    validate_network_hosts,
    validate_relative_path,
)
from slopbench.hashing import (
    ContractError,
    canonical_json_bytes,
    load_model,
    sha256_bytes,
    sha256_file,
    validate_instruction_layers,
    validate_task,
)

TASK_SET_SCHEMA_VERSION: Literal["slopbench.task-set.v1"] = "slopbench.task-set.v1"
PROFILE_SCHEMA_VERSION: Literal["slopbench.profile.v1"] = "slopbench.profile.v1"
REFERENCE_CONFIGURATION_SCHEMA_VERSION: Literal["slopbench.reference-configuration.v1"] = (
    "slopbench.reference-configuration.v1"
)
EVALUATION_SCHEMA_VERSION: Literal["slopbench.evaluation.v1"] = "slopbench.evaluation.v1"
EVALUATION_RESULT_SCHEMA_VERSION: Literal["slopbench.evaluation-result.v1"] = (
    "slopbench.evaluation-result.v1"
)
DISCLOSURE_SCHEMA_VERSION: Literal["slopbench.disclosure.v1"] = "slopbench.disclosure.v1"
BRIDGE_SCHEMA_VERSION: Literal["slopbench.bridge.v1"] = "slopbench.bridge.v1"
RETIREMENT_SCHEMA_VERSION: Literal["slopbench.retirement.v1"] = "slopbench.retirement.v1"
ATTESTATION_STATEMENT_SCHEMA_VERSION: Literal["slopbench.attestation-statement.v1"] = (
    "slopbench.attestation-statement.v1"
)
ATTESTATION_SCHEMA_VERSION: Literal["slopbench.attestation.v1"] = "slopbench.attestation.v1"
REFERENCE_VERIFICATION_SCHEMA_VERSION: Literal["slopbench.reference-verification.v1"] = (
    "slopbench.reference-verification.v1"
)

TaskId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$"),
]
Url = Annotated[str, Field(pattern=r"^https://[^\s]+$")]


class TaskSetVisibility(StrEnum):
    PUBLIC = "public"
    HELD_OUT_ACTIVE = "held_out_active"
    RETIRED = "retired"


class EvaluationPurpose(StrEnum):
    SMOKE = "smoke"
    CALIBRATION = "calibration"
    COMPARISON = "comparison"


class ResultOrigin(StrEnum):
    EXTERNAL = "external"
    MAINTAINER = "maintainer"


class BudgetStatus(StrEnum):
    NOT_DECLARED = "not_declared"
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class RetirementReason(StrEnum):
    LEAKAGE = "leakage"
    VERIFIER_WEAKNESS = "verifier_weakness"
    DEPENDENCY_ROT = "dependency_rot"
    MAJOR_TASK_SET_RELEASE = "major_task_set_release"


_AGENT_ATTRIBUTABLE_CLASSIFICATIONS = frozenset(
    {
        FailureClassification.VALID_PASS,
        FailureClassification.VALID_AGENT_FAILURE,
        FailureClassification.INVALID_RUN,
    }
)
_NON_COMPARABLE_CLASSIFICATIONS = frozenset(
    {
        FailureClassification.BENCHMARK_DEFECT,
        FailureClassification.INFRASTRUCTURE_FAILURE,
    }
)


class VersionBinding(ContractModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    version: Version
    sha256: Sha256Hex


class ComponentPin(ContractModel):
    name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    version: str = Field(min_length=1)


class TaskSetEntry(ContractModel):
    task_id: TaskId
    task_version: Version
    task_digest: Sha256Hex
    contract_path: str
    category: CapabilityCategory
    kind: TaskKind
    capabilities: CapabilityEnvelope
    applicable_gates: list[GateName] = Field(min_length=1)
    provenance: Provenance
    license: LicenseContract

    _contract_path = field_validator("contract_path")(validate_relative_path)

    @field_validator("applicable_gates")
    @classmethod
    def unique_gates(cls, value: list[GateName]) -> list[GateName]:
        if len(value) != len(set(value)):
            raise ValueError("applicable_gates must be unique")
        return value


class TaskSetManifest(ContractModel):
    schema_version: Literal["slopbench.task-set.v1"]
    task_set_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    version: Version
    visibility: TaskSetVisibility
    tasks: list[TaskSetEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        task_ids = [task.task_id for task in self.tasks]
        contract_paths = [task.contract_path for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task set task_ids must be unique")
        if len(contract_paths) != len(set(contract_paths)):
            raise ValueError("task set contract_paths must be unique")
        return self


class ProfileBudget(ContractModel):
    max_mean_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_mean_duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    require_complete_usage: bool = True

    @model_validator(mode="after")
    def declares_limit(self) -> Self:
        if self.max_mean_cost_usd is None and self.max_mean_duration_seconds is None:
            raise ValueError("profile budget must declare a cost or duration limit")
        return self


class ProfileDefinition(ContractModel):
    schema_version: Literal["slopbench.profile.v1"]
    profile_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    version: Version
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    subjective: bool
    source_note: str | None = None
    gate_weights: dict[GateName, int]
    strict_gates: list[GateName]
    quality_weight: int = Field(ge=0, le=100)
    reliability_weight: int = Field(ge=0, le=100)
    budget: ProfileBudget | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if set(self.gate_weights) != set(GateName):
            raise ValueError("gate_weights must contain every stable gate")
        if any(weight < 0 for weight in self.gate_weights.values()):
            raise ValueError("gate_weights cannot be negative")
        if not any(self.gate_weights.values()):
            raise ValueError("at least one gate weight must be positive")
        if len(self.strict_gates) != len(set(self.strict_gates)):
            raise ValueError("strict_gates must be unique")
        if any(self.gate_weights[gate] == 0 for gate in self.strict_gates):
            raise ValueError("strict gates must carry positive weight")
        if self.quality_weight + self.reliability_weight != 100:
            raise ValueError("quality_weight and reliability_weight must sum to 100")
        if self.subjective and not self.source_note:
            raise ValueError("subjective profiles require a source_note")
        if not self.subjective and self.source_note is not None:
            raise ValueError("objective profiles cannot declare a subjective source_note")
        return self


class ReferenceConfiguration(ContractModel):
    schema_version: Literal["slopbench.reference-configuration.v1"] = (
        REFERENCE_CONFIGURATION_SCHEMA_VERSION
    )
    configuration_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    version: Version
    harness: ComponentPin
    adapter: ToolPin
    model: ModelPin | None
    effort_tier: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    environment: dict[EnvName, str] = Field(default_factory=dict)
    setup_network_allowed_hosts: list[str] = Field(default_factory=list)
    network_allowed_hosts: list[str] = Field(default_factory=list)
    tools: list[ToolPin] = Field(default_factory=list)
    credential_env: list[EnvName] = Field(default_factory=list)

    @field_validator("settings")
    @classmethod
    def settings_are_non_secret(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_sensitive_values(value, "configuration.settings")
        return value

    @field_validator("environment")
    @classmethod
    def environment_is_non_secret(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if _SENSITIVE_KEY.search(key):
                raise ValueError(
                    f"configuration.environment.{key} looks sensitive; omit credential values"
                )
        return value

    _network_allowed_hosts = field_validator(
        "setup_network_allowed_hosts", "network_allowed_hosts"
    )(validate_network_hosts)

    @model_validator(mode="after")
    def unique_tools(self) -> Self:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("reference configuration tool names must be unique")
        if len(self.credential_env) != len(set(self.credential_env)):
            raise ValueError("reference configuration credential_env values must be unique")
        configured_version = self.settings.get("version")
        if configured_version is not None and configured_version != self.harness.version:
            raise ValueError("configuration.settings.version must match harness.version")
        if self.harness.name == "cursor-cli" and configured_version is not None:
            raise ValueError("cursor-cli version must be observed instead of configured")
        return self


class EvaluationRunBinding(ContractModel):
    task_id: TaskId
    task_digest: Sha256Hex
    pair_index: int = Field(ge=1, le=5)
    run_manifest_path: str
    run_manifest_sha256: Sha256Hex
    result_path: str
    result_sha256: Sha256Hex
    report_path: str | None = None
    report_sha256: Sha256Hex | None = None

    _run_manifest_path = field_validator("run_manifest_path")(validate_relative_path)
    _result_path = field_validator("result_path")(validate_relative_path)

    @field_validator("report_path")
    @classmethod
    def valid_report_path(cls, value: str | None) -> str | None:
        return None if value is None else validate_relative_path(value)

    @model_validator(mode="after")
    def report_binding_is_complete(self) -> Self:
        if (self.report_path is None) != (self.report_sha256 is None):
            raise ValueError("report_path and report_sha256 must be declared together")
        return self


_TRIAL_COUNTS = {
    EvaluationPurpose.SMOKE: 1,
    EvaluationPurpose.CALIBRATION: 3,
    EvaluationPurpose.COMPARISON: 5,
}


class EvaluationManifest(ContractModel):
    schema_version: Literal["slopbench.evaluation.v1"]
    evaluation_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    task_set: VersionBinding
    profile: VersionBinding
    purpose: EvaluationPurpose
    configuration: ReferenceConfiguration
    runs: list[EvaluationRunBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_runs(self) -> Self:
        expected_count = _TRIAL_COUNTS[self.purpose]
        grouped: dict[str, list[EvaluationRunBinding]] = defaultdict(list)
        for run in self.runs:
            grouped[run.task_id].append(run)
        for task_id, runs in grouped.items():
            if len(runs) != expected_count:
                raise ValueError(
                    f"{self.purpose.value} requires {expected_count} trial(s) for {task_id}"
                )
            if {run.pair_index for run in runs} != set(range(1, expected_count + 1)):
                raise ValueError(f"pair_index coverage is incomplete for {task_id}")
            if len({run.task_digest for run in runs}) != 1:
                raise ValueError(f"task digest changes within paired trials for {task_id}")
        manifest_paths = [run.run_manifest_path for run in self.runs]
        result_paths = [run.result_path for run in self.runs]
        manifest_digests = [run.run_manifest_sha256 for run in self.runs]
        result_digests = [run.result_sha256 for run in self.runs]
        report_paths = [run.report_path for run in self.runs if run.report_path is not None]
        if len(manifest_paths) != len(set(manifest_paths)):
            raise ValueError("run manifest paths must be unique")
        if len(result_paths) != len(set(result_paths)):
            raise ValueError("result paths must be unique")
        if len(manifest_digests) != len(set(manifest_digests)):
            raise ValueError("run manifest digests must be unique")
        if len(result_digests) != len(set(result_digests)):
            raise ValueError("result digests must be unique")
        if len(report_paths) != len(set(report_paths)):
            raise ValueError("agent report paths must be unique")
        return self


class RawTrialOutcome(ContractModel):
    task_id: TaskId
    task_digest: Sha256Hex
    pair_index: int = Field(ge=1, le=5)
    run_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    task: TaskBinding
    run_manifest_sha256: Sha256Hex
    result_sha256: Sha256Hex
    classification: FailureClassification
    failure_reason: FailureReason
    agent: AgentConfiguration
    runtime: RuntimeConfiguration
    limits: RunLimits
    trial: TrialIdentity
    outcomes: list[GateOutcome]
    strict_gate_failures: list[GateName]
    uncertainty: list[Uncertainty]
    report_sha256: Sha256Hex | None
    receipt: ReceiptValidation
    usage: UsageMetrics
    timing: TimingMetrics
    harbor: HarborEvidence
    artifacts: list[ArtifactDigest]

    @model_validator(mode="after")
    def validate_raw_outcome(self) -> Self:
        if self.run_id != self.trial.id:
            raise ValueError("raw run_id and trial.id must match")
        if self.task.task_id != self.task_id or self.task.task_digest != self.task_digest:
            raise ValueError("raw task binding does not match trial identity")
        if self.harbor.version != self.runtime.harbor_version:
            raise ValueError("raw Harbor version does not match runtime configuration")
        if self.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS:
            if self.harbor.task_checksum != self.task.harbor_task_checksum:
                raise ValueError("raw Harbor task checksum is missing or mismatched")
            if self.harbor.agent is None or not self.harbor.agent.matches(self.agent):
                raise ValueError("raw observed agent identity is missing or mismatched")
        elif self.harbor.task_checksum is not None and (
            self.harbor.task_checksum != self.task.harbor_task_checksum
        ):
            raise ValueError("raw Harbor task checksum does not match the run binding")
        gates = [outcome.gate for outcome in self.outcomes]
        if set(gates) != set(GateName) or len(gates) != len(GateName):
            raise ValueError("raw outcomes must contain exactly one entry per gate")
        failed = {
            outcome.gate for outcome in self.outcomes if outcome.status == OutcomeStatus.FAILED
        }
        if self.classification == FailureClassification.VALID_PASS and failed:
            raise ValueError("raw valid_pass cannot contain failed gate outcomes")
        expected_failures = [gate for gate in GateName if gate in failed]
        if self.strict_gate_failures != expected_failures:
            raise ValueError("raw strict_gate_failures must match failed completion gates")
        if self.receipt.present != (self.report_sha256 is not None):
            raise ValueError("raw receipt presence and report digest must match")
        if self.receipt.present and self.receipt.sha256 != self.report_sha256:
            raise ValueError("raw receipt and report digest must match")
        return self


class RawResultVector(ContractModel):
    trials: list[RawTrialOutcome] = Field(min_length=1)


class AggregateMetrics(ContractModel):
    trial_count: int = Field(ge=1)
    reliability_trial_count: int = Field(ge=0)
    excluded_reliability_trials: int = Field(ge=0)
    quality_bps: int = Field(ge=0, le=10_000)
    reliability_bps: int = Field(ge=0, le=10_000)
    selection_bps: int = Field(ge=0, le=10_000)
    failure_counts: dict[FailureClassification, int]
    strict_gate_failure_counts: dict[GateName, int]
    total_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    mean_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    missing_cost_trials: int = Field(ge=0)
    total_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    mean_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    missing_duration_trials: int = Field(ge=0)
    budget_status: BudgetStatus
    budget_failures: list[str]

    @model_validator(mode="after")
    def complete_counters(self) -> Self:
        if set(self.failure_counts) != set(FailureClassification):
            raise ValueError("failure_counts must contain every classification")
        if set(self.strict_gate_failure_counts) != set(GateName):
            raise ValueError("strict_gate_failure_counts must contain every gate")
        if any(count < 0 for count in self.failure_counts.values()) or (
            sum(self.failure_counts.values()) != self.trial_count
        ):
            raise ValueError("failure_counts must be non-negative and sum to trial_count")
        if self.reliability_trial_count + self.excluded_reliability_trials != self.trial_count:
            raise ValueError("reliability trial counts must sum to trial_count")
        return self


class EvaluationResult(ContractModel):
    schema_version: Literal["slopbench.evaluation-result.v1"]
    evaluation_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    task_set: VersionBinding
    task_set_manifest: TaskSetManifest
    profile: VersionBinding
    profile_definition: ProfileDefinition
    evaluation_manifest_sha256: Sha256Hex
    purpose: EvaluationPurpose
    configuration: ReferenceConfiguration
    result_origin: ResultOrigin
    official: Literal[False] = False
    result_vector_sha256: Sha256Hex
    trials: list[RawTrialOutcome] = Field(min_length=1)
    metrics: AggregateMetrics

    @model_validator(mode="after")
    def validate_result_vector(self) -> Self:
        expected_count = _TRIAL_COUNTS[self.purpose]
        if self.task_set != task_set_binding(self.task_set_manifest):
            raise ValueError("result task-set binding does not match its task-set manifest")
        entries = {entry.task_id: entry for entry in self.task_set_manifest.tasks}
        if {trial.task_id for trial in self.trials} != set(entries):
            raise ValueError("raw trials do not cover the result task set exactly")
        grouped: dict[str, list[RawTrialOutcome]] = defaultdict(list)
        for trial in self.trials:
            grouped[trial.task_id].append(trial)
            entry = entries[trial.task_id]
            if trial.task_digest != entry.task_digest:
                raise ValueError(f"raw task digest mismatch for {trial.task_id}")
            if (
                trial.task.task_version != entry.task_version
                or trial.task.contract_path != entry.contract_path
            ):
                raise ValueError(f"raw task contract binding mismatch for {trial.task_id}")
            if trial.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS:
                actual_gates = {
                    outcome.gate
                    for outcome in trial.outcomes
                    if outcome.status != OutcomeStatus.NOT_APPLICABLE
                }
                if actual_gates != set(entry.applicable_gates):
                    raise ValueError(f"raw gate applicability mismatch for {trial.task_id}")
            comparisons = {
                "harness": (self.configuration.harness.name, trial.agent.harness),
                "harness_version": (
                    self.configuration.harness.version,
                    trial.agent.harness_version,
                ),
                "adapter": (self.configuration.adapter, trial.agent.adapter),
                "model": (self.configuration.model, trial.agent.model),
                "effort_tier": (self.configuration.effort_tier, trial.agent.effort_tier),
                "settings": (self.configuration.settings, trial.agent.settings),
                "environment": (self.configuration.environment, trial.agent.environment),
                "setup_network_allowed_hosts": (
                    self.configuration.setup_network_allowed_hosts,
                    trial.agent.setup_network_allowed_hosts,
                ),
                "network_allowed_hosts": (
                    self.configuration.network_allowed_hosts,
                    trial.agent.network_allowed_hosts,
                ),
                "tools": (self.configuration.tools, trial.agent.tools),
                "credential_env": (
                    self.configuration.credential_env,
                    trial.agent.credential_env,
                ),
            }
            mismatches = [
                field for field, (expected, actual) in comparisons.items() if expected != actual
            ]
            if mismatches:
                raise ValueError(
                    f"raw trial configuration mismatch for {trial.task_id}: {mismatches}"
                )
        for task_id, trials in grouped.items():
            if len(trials) != expected_count:
                raise ValueError(
                    f"{self.purpose.value} result requires {expected_count} trial(s) for {task_id}"
                )
            if {trial.pair_index for trial in trials} != set(range(1, expected_count + 1)):
                raise ValueError(f"result pair_index coverage is incomplete for {task_id}")
            if len({trial.task_digest for trial in trials}) != 1:
                raise ValueError(f"task digest changes within result trials for {task_id}")
            baseline = trials[0]
            for trial in trials[1:]:
                if (
                    trial.task != baseline.task
                    or trial.agent.instruction_layers != baseline.agent.instruction_layers
                    or trial.runtime != baseline.runtime
                    or trial.limits != baseline.limits
                ):
                    raise ValueError(
                        f"task execution pins drift within result trials for {task_id}"
                    )
        if self.trials != sorted(self.trials, key=lambda trial: (trial.task_id, trial.pair_index)):
            raise ValueError("raw trials must use deterministic task and pair order")
        for label, values in {
            "run ids": [trial.run_id for trial in self.trials],
            "run manifest digests": [trial.run_manifest_sha256 for trial in self.trials],
            "result digests": [trial.result_sha256 for trial in self.trials],
        }.items():
            if len(values) != len(set(values)):
                raise ValueError(f"raw {label} must be unique")
        vector = RawResultVector(trials=self.trials)
        expected_vector_sha = contract_digest("slopbench.result-vector.v1", vector)
        if self.result_vector_sha256 != expected_vector_sha:
            raise ValueError("result vector digest mismatch")
        if self.profile != profile_binding(self.profile_definition):
            raise ValueError("result profile binding does not match its profile definition")
        if self.metrics.trial_count != len(self.trials):
            raise ValueError("aggregate trial_count does not match raw trials")
        if self.metrics != _aggregate(self.trials, self.profile_definition):
            raise ValueError("aggregate metrics do not recompute from raw trials and profile")
        return self


class PublicCapabilityRequirement(ContractModel):
    repository: Literal["read-write", "read-only"]
    shell: bool
    tests: bool
    tools: list[str]
    network: Literal["none", "model-only", "declared"]
    external_writes: Literal["none"]
    live_credentials: Literal[False]


class PublicScoringContract(ContractModel):
    profile: VersionBinding
    gate_weights: dict[GateName, int]
    strict_gates: list[GateName]
    quality_weight: int
    reliability_weight: int
    budget: ProfileBudget | None


class PublicAggregate(ContractModel):
    trial_count: int
    reliability_trial_count: int
    excluded_reliability_trials: int
    quality_bps: int
    reliability_bps: int
    selection_bps: int
    failure_counts: dict[FailureClassification, int]
    total_cost_usd: float | None
    mean_cost_usd: float | None
    total_duration_seconds: float | None
    mean_duration_seconds: float | None
    budget_status: BudgetStatus


class HeldOutDisclosure(ContractModel):
    schema_version: Literal["slopbench.disclosure.v1"]
    task_set: VersionBinding
    category_counts: dict[CapabilityCategory, int]
    capability_requirements: list[PublicCapabilityRequirement]
    scoring_contract: PublicScoringContract
    aggregate: PublicAggregate


class CoverageSnapshot(ContractModel):
    category_counts: dict[CapabilityCategory, int]

    @field_validator("category_counts")
    @classmethod
    def non_negative_counts(
        cls, value: dict[CapabilityCategory, int]
    ) -> dict[CapabilityCategory, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("coverage counts cannot be negative")
        return value


class BridgeReport(ContractModel):
    schema_version: Literal["slopbench.bridge.v1"]
    before_task_set: VersionBinding
    after_task_set: VersionBinding
    profile: VersionBinding
    configuration: ReferenceConfiguration
    paired_trials: Literal[5] = 5
    before_result_sha256: Sha256Hex
    after_result_sha256: Sha256Hex
    coverage_before: CoverageSnapshot
    coverage_after: CoverageSnapshot

    @model_validator(mode="after")
    def distinct_task_sets(self) -> Self:
        if self.before_task_set == self.after_task_set:
            raise ValueError("bridge task sets must differ")
        return self


class RetiredPublication(ContractModel):
    task_url: Url
    fixtures_url: Url
    reference_runs_url: Url
    provenance: Provenance
    license: LicenseContract


class RetirementRecord(ContractModel):
    retired_task_id: TaskId
    retired_task_digest: Sha256Hex
    reason: RetirementReason
    replacement_task_id: TaskId
    replacement_task_digest: Sha256Hex
    publication: RetiredPublication


class RetirementManifest(ContractModel):
    schema_version: Literal["slopbench.retirement.v1"]
    before_task_set: VersionBinding
    after_task_set: VersionBinding
    bridge_sha256: Sha256Hex
    records: list[RetirementRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_retirements(self) -> Self:
        retired = [record.retired_task_id for record in self.records]
        replacements = [record.replacement_task_id for record in self.records]
        if len(retired) != len(set(retired)):
            raise ValueError("retired tasks must be unique")
        if len(replacements) != len(set(replacements)):
            raise ValueError("replacement tasks must be unique")
        return self


class AttestationSubject(ContractModel):
    name: Literal["evaluation-manifest", "evaluation-result"]
    sha256: Sha256Hex


class AttestationStatement(ContractModel):
    schema_version: Literal["slopbench.attestation-statement.v1"]
    predicate_type: Literal["slopbench.maintainer-reference.v1"] = (
        "slopbench.maintainer-reference.v1"
    )
    evaluation_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")]
    subjects: list[AttestationSubject]

    @model_validator(mode="after")
    def exact_subjects(self) -> Self:
        if {subject.name for subject in self.subjects} != {
            "evaluation-manifest",
            "evaluation-result",
        } or len(self.subjects) != 2:
            raise ValueError("attestation must bind one manifest and one result")
        return self


class SshSignature(ContractModel):
    format: Literal["sshsig"] = "sshsig"
    namespace: Literal["slopbench"] = "slopbench"
    signer: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9@._:+/-]*$")
    signature_base64: str = Field(min_length=16)


class ReferenceAttestation(ContractModel):
    schema_version: Literal["slopbench.attestation.v1"]
    statement: AttestationStatement
    signature: SshSignature


class ReferenceVerification(ContractModel):
    schema_version: Literal["slopbench.reference-verification.v1"]
    status: Literal["official"] = "official"
    signer: str
    attestation_sha256: Sha256Hex
    statement_sha256: Sha256Hex


def contract_digest(domain: str, model: ContractModel) -> str:
    return sha256_bytes(domain.encode() + b"\0" + canonical_json_bytes(model))


def task_set_binding(task_set: TaskSetManifest) -> VersionBinding:
    return VersionBinding(
        id=task_set.task_set_id,
        version=task_set.version,
        sha256=contract_digest(TASK_SET_SCHEMA_VERSION, task_set),
    )


def profile_binding(profile: ProfileDefinition) -> VersionBinding:
    return VersionBinding(
        id=profile.profile_id,
        version=profile.version,
        sha256=contract_digest(PROFILE_SCHEMA_VERSION, profile),
    )


def reference_configuration_binding(configuration: ReferenceConfiguration) -> VersionBinding:
    return VersionBinding(
        id=configuration.configuration_id,
        version=configuration.version,
        sha256=contract_digest(REFERENCE_CONFIGURATION_SCHEMA_VERSION, configuration),
    )


def _resolved_file(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ContractError(f"path escapes declared root: {relative}")
    if candidate.is_symlink() or not resolved.is_file():
        raise ContractError(f"path is not a regular file: {relative}")
    return resolved


def validate_task_set(path: Path, project_root: Path) -> tuple[TaskSetManifest, str]:
    task_set = load_model(path, TaskSetManifest)
    for entry in task_set.tasks:
        contract_path = _resolved_file(project_root, entry.contract_path)
        if contract_path.name != "slopbench-task.json":
            raise ContractError(
                f"task set contract path is not a task contract: {entry.contract_path}"
            )
        task, _, digest = validate_task(contract_path.parent)
        mismatches: list[str] = []
        comparisons = {
            "task_id": (entry.task_id, task.task_id),
            "task_version": (entry.task_version, task.version),
            "task_digest": (entry.task_digest, digest),
            "category": (entry.category, task.design.category),
            "kind": (entry.kind, task.kind),
            "capabilities": (entry.capabilities, task.capabilities),
            "applicable_gates": (entry.applicable_gates, task.applicable_gates),
            "provenance": (entry.provenance, task.provenance),
            "license": (entry.license, task.license),
        }
        for field, (actual, expected) in comparisons.items():
            if actual != expected:
                mismatches.append(field)
        if mismatches:
            raise ContractError(f"task set binding mismatch for {entry.task_id}: {mismatches}")
    return task_set, contract_digest(TASK_SET_SCHEMA_VERSION, task_set)


def _configuration_mismatches(configuration: ReferenceConfiguration, run: RunManifest) -> list[str]:
    mismatches: list[str] = []
    comparisons = {
        "harness": (configuration.harness.name, run.agent.harness),
        "harness_version": (configuration.harness.version, run.agent.harness_version),
        "adapter": (configuration.adapter, run.agent.adapter),
        "model": (configuration.model, run.agent.model),
        "effort_tier": (configuration.effort_tier, run.agent.effort_tier),
        "settings": (configuration.settings, run.agent.settings),
        "environment": (configuration.environment, run.agent.environment),
        "setup_network_allowed_hosts": (
            configuration.setup_network_allowed_hosts,
            run.agent.setup_network_allowed_hosts,
        ),
        "network_allowed_hosts": (
            configuration.network_allowed_hosts,
            run.agent.network_allowed_hosts,
        ),
        "tools": (configuration.tools, run.agent.tools),
        "credential_env": (configuration.credential_env, run.agent.credential_env),
    }
    for field, (expected, actual) in comparisons.items():
        if expected != actual:
            mismatches.append(field)
    return mismatches


def _aggregate(trials: list[RawTrialOutcome], profile: ProfileDefinition) -> AggregateMetrics:
    quality_numerator = 0
    quality_denominator = 0
    failure_counts = Counter(trial.classification for trial in trials)
    strict_counts = Counter(gate for trial in trials for gate in trial.strict_gate_failures)
    reliable_trials = [
        trial for trial in trials if trial.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS
    ]
    for trial in reliable_trials:
        for outcome in trial.outcomes:
            if outcome.status == OutcomeStatus.NOT_APPLICABLE:
                continue
            weight = profile.gate_weights[outcome.gate]
            quality_denominator += weight
            if outcome.status == OutcomeStatus.PASSED:
                quality_numerator += weight
    quality_bps = (
        0 if quality_denominator == 0 else quality_numerator * 10_000 // quality_denominator
    )
    reliability_bps = (
        0
        if not reliable_trials
        else failure_counts[FailureClassification.VALID_PASS] * 10_000 // len(reliable_trials)
    )
    selection_bps = (
        quality_bps * profile.quality_weight + reliability_bps * profile.reliability_weight
    ) // 100

    costs = [trial.usage.cost_usd for trial in trials if trial.usage.cost_usd is not None]
    durations = [
        trial.timing.duration_seconds
        for trial in trials
        if trial.timing.duration_seconds is not None
    ]
    total_cost = None if not costs else round(sum(costs), 6)
    mean_cost = None if total_cost is None else round(total_cost / len(costs), 6)
    total_duration = None if not durations else round(sum(durations), 6)
    mean_duration = None if total_duration is None else round(total_duration / len(durations), 6)
    budget_failures: list[str] = []
    if profile.budget is None:
        budget_status = BudgetStatus.NOT_DECLARED
    else:
        budget = profile.budget
        incomplete = False
        if budget.max_mean_cost_usd is not None:
            if budget.require_complete_usage and len(costs) != len(trials):
                incomplete = True
            elif mean_cost is None or mean_cost > budget.max_mean_cost_usd:
                budget_failures.append("mean_cost_usd")
        if budget.max_mean_duration_seconds is not None:
            if budget.require_complete_usage and len(durations) != len(trials):
                incomplete = True
            elif mean_duration is None or mean_duration > budget.max_mean_duration_seconds:
                budget_failures.append("mean_duration_seconds")
        if incomplete:
            budget_status = BudgetStatus.INCOMPLETE
        elif budget_failures:
            budget_status = BudgetStatus.FAILED
        else:
            budget_status = BudgetStatus.PASSED

    return AggregateMetrics(
        trial_count=len(trials),
        reliability_trial_count=len(reliable_trials),
        excluded_reliability_trials=len(trials) - len(reliable_trials),
        quality_bps=quality_bps,
        reliability_bps=reliability_bps,
        selection_bps=selection_bps,
        failure_counts={kind: failure_counts[kind] for kind in FailureClassification},
        strict_gate_failure_counts={gate: strict_counts[gate] for gate in GateName},
        total_cost_usd=total_cost,
        mean_cost_usd=mean_cost,
        missing_cost_trials=len(trials) - len(costs),
        total_duration_seconds=total_duration,
        mean_duration_seconds=mean_duration,
        missing_duration_trials=len(trials) - len(durations),
        budget_status=budget_status,
        budget_failures=budget_failures,
    )


def compute_evaluation(
    evaluation_path: Path,
    task_set_path: Path,
    profile_path: Path,
    project_root: Path,
    bundle_root: Path,
    *,
    result_origin: ResultOrigin = ResultOrigin.EXTERNAL,
) -> EvaluationResult:
    evaluation = load_model(evaluation_path, EvaluationManifest)
    task_set, task_set_sha = validate_task_set(task_set_path, project_root)
    profile = load_model(profile_path, ProfileDefinition)
    expected_task_set = task_set_binding(task_set)
    expected_profile = profile_binding(profile)
    if evaluation.task_set != expected_task_set or task_set_sha != expected_task_set.sha256:
        raise ContractError("evaluation task-set binding mismatch")
    if evaluation.profile != expected_profile:
        raise ContractError("evaluation profile binding mismatch")
    entries = {entry.task_id: entry for entry in task_set.tasks}
    if {run.task_id for run in evaluation.runs} != set(entries):
        raise ContractError("evaluation runs do not cover the task set exactly")

    trials: list[RawTrialOutcome] = []
    for binding in sorted(evaluation.runs, key=lambda run: (run.task_id, run.pair_index)):
        entry = entries[binding.task_id]
        if binding.task_digest != entry.task_digest:
            raise ContractError(f"evaluation task digest mismatch for {binding.task_id}")
        run_path = _resolved_file(bundle_root, binding.run_manifest_path)
        result_path = _resolved_file(bundle_root, binding.result_path)
        if sha256_file(run_path) != binding.run_manifest_sha256:
            raise ContractError(f"run manifest digest mismatch: {binding.run_manifest_path}")
        if sha256_file(result_path) != binding.result_sha256:
            raise ContractError(f"raw result digest mismatch: {binding.result_path}")
        run = load_model(run_path, RunManifest)
        result = load_model(result_path, ResultBundle)
        contract_path = _resolved_file(project_root, entry.contract_path)
        task, _, _ = validate_task(contract_path.parent)
        validate_instruction_layers(
            run.agent.instruction_layers,
            task,
            contract_path.parent,
            project_root,
        )
        if (
            run.task.task_id != binding.task_id
            or run.task.task_version != entry.task_version
            or run.task.task_digest != binding.task_digest
            or run.task.contract_path != entry.contract_path
            or run.task.contract_sha256 != sha256_file(contract_path)
            or result.task_digest != binding.task_digest
            or result.run_manifest_sha256 != binding.run_manifest_sha256
            or result.run_id != run.run_id
            or result.attempt != run.trial.attempt
            or result.harbor.version != run.runtime.harbor_version
            or (
                result.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS
                and result.harbor.task_checksum != run.task.harbor_task_checksum
            )
            or (
                result.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS
                and (result.harbor.agent is None or not result.harbor.agent.matches(run.agent))
            )
            or (
                result.classification not in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS
                and result.harbor.task_checksum is not None
                and result.harbor.task_checksum != run.task.harbor_task_checksum
            )
        ):
            raise ContractError(f"raw run/result binding mismatch for {binding.task_id}")
        mismatches = _configuration_mismatches(evaluation.configuration, run)
        if mismatches:
            raise ContractError(
                f"reference configuration mismatch for {binding.task_id}: {mismatches}"
            )

        uncertainty: list[Uncertainty] = []
        if binding.report_path is not None and binding.report_sha256 is not None:
            report_path = _resolved_file(bundle_root, binding.report_path)
            if sha256_file(report_path) != binding.report_sha256:
                raise ContractError(f"agent report digest mismatch: {binding.report_path}")
            report = load_model(report_path, AgentReport)
            if report.task_digest != binding.task_digest:
                raise ContractError(f"agent report task mismatch for {binding.task_id}")
            if result.receipt.sha256 != binding.report_sha256:
                raise ContractError(f"result receipt mismatch for {binding.task_id}")
            uncertainty = report.uncertainty
        elif result.receipt.present:
            raise ContractError(f"present receipt lacks report binding for {binding.task_id}")

        outcomes_by_gate = {outcome.gate: outcome for outcome in result.outcomes}
        outcomes = [outcomes_by_gate[gate] for gate in GateName]
        if result.classification in _AGENT_ATTRIBUTABLE_CLASSIFICATIONS:
            actual_gates = {
                outcome.gate
                for outcome in outcomes
                if outcome.status != OutcomeStatus.NOT_APPLICABLE
            }
            if actual_gates != set(entry.applicable_gates):
                raise ContractError(f"raw gate applicability mismatch for {binding.task_id}")
        failed = {outcome.gate for outcome in outcomes if outcome.status == OutcomeStatus.FAILED}
        strict_failures = [gate for gate in GateName if gate in failed]
        trials.append(
            RawTrialOutcome(
                task_id=binding.task_id,
                task_digest=binding.task_digest,
                pair_index=binding.pair_index,
                run_id=result.run_id,
                task=run.task,
                run_manifest_sha256=binding.run_manifest_sha256,
                result_sha256=binding.result_sha256,
                classification=result.classification,
                failure_reason=result.failure_reason,
                agent=run.agent,
                runtime=run.runtime,
                limits=run.limits,
                trial=run.trial,
                outcomes=outcomes,
                strict_gate_failures=strict_failures,
                uncertainty=uncertainty,
                report_sha256=binding.report_sha256,
                receipt=result.receipt,
                usage=result.usage,
                timing=result.timing,
                harbor=result.harbor,
                artifacts=result.artifacts,
            )
        )

    vector = RawResultVector(trials=trials)
    return EvaluationResult(
        schema_version=EVALUATION_RESULT_SCHEMA_VERSION,
        evaluation_id=evaluation.evaluation_id,
        task_set=expected_task_set,
        task_set_manifest=task_set,
        profile=expected_profile,
        profile_definition=profile,
        evaluation_manifest_sha256=sha256_file(evaluation_path),
        purpose=evaluation.purpose,
        configuration=evaluation.configuration,
        result_origin=result_origin,
        result_vector_sha256=contract_digest("slopbench.result-vector.v1", vector),
        trials=trials,
        metrics=_aggregate(trials, profile),
    )


def build_held_out_disclosure(
    task_set: TaskSetManifest,
    profile: ProfileDefinition,
    result: EvaluationResult,
) -> HeldOutDisclosure:
    if task_set.visibility != TaskSetVisibility.HELD_OUT_ACTIVE:
        raise ContractError("held-out disclosure requires an active held-out task set")
    if result.purpose != EvaluationPurpose.COMPARISON:
        raise ContractError("published held-out disclosures require five-trial comparison results")
    if result.task_set != task_set_binding(task_set):
        raise ContractError("disclosure result does not bind the held-out task set")
    if result.profile != profile_binding(profile):
        raise ContractError("disclosure result does not bind the scoring profile")
    _validate_result_task_set(result, task_set)
    if result.metrics != _aggregate(result.trials, profile):
        raise ContractError("disclosure result metrics do not recompute under the profile")
    category_counts = Counter(task.category for task in task_set.tasks)
    capabilities: dict[bytes, PublicCapabilityRequirement] = {}
    for task in task_set.tasks:
        requirement = PublicCapabilityRequirement(
            repository=task.capabilities.repository,
            shell=task.capabilities.shell,
            tests=task.capabilities.tests,
            tools=list(task.capabilities.tools),
            network=task.capabilities.network,
            external_writes=task.capabilities.external_writes,
            live_credentials=task.capabilities.live_credentials,
        )
        capabilities[canonical_json_bytes(requirement)] = requirement
    metrics = result.metrics
    return HeldOutDisclosure(
        schema_version=DISCLOSURE_SCHEMA_VERSION,
        task_set=result.task_set,
        category_counts={category: category_counts[category] for category in category_counts},
        capability_requirements=[capabilities[key] for key in sorted(capabilities)],
        scoring_contract=PublicScoringContract(
            profile=result.profile,
            gate_weights=profile.gate_weights,
            strict_gates=profile.strict_gates,
            quality_weight=profile.quality_weight,
            reliability_weight=profile.reliability_weight,
            budget=profile.budget,
        ),
        aggregate=PublicAggregate(
            trial_count=metrics.trial_count,
            reliability_trial_count=metrics.reliability_trial_count,
            excluded_reliability_trials=metrics.excluded_reliability_trials,
            quality_bps=metrics.quality_bps,
            reliability_bps=metrics.reliability_bps,
            selection_bps=metrics.selection_bps,
            failure_counts=metrics.failure_counts,
            total_cost_usd=metrics.total_cost_usd,
            mean_cost_usd=metrics.mean_cost_usd,
            total_duration_seconds=metrics.total_duration_seconds,
            mean_duration_seconds=metrics.mean_duration_seconds,
            budget_status=metrics.budget_status,
        ),
    )


def coverage_snapshot(task_set: TaskSetManifest) -> CoverageSnapshot:
    counts = Counter(task.category for task in task_set.tasks)
    return CoverageSnapshot(category_counts={category: counts[category] for category in counts})


def _validate_result_task_set(result: EvaluationResult, task_set: TaskSetManifest) -> None:
    if result.task_set_manifest != task_set:
        raise ContractError("result does not retain the bound task-set manifest")
    entries = {entry.task_id: entry for entry in task_set.tasks}
    if {trial.task_id for trial in result.trials} != set(entries):
        raise ContractError("result trials do not cover the bound task set exactly")
    for trial in result.trials:
        if trial.task_digest != entries[trial.task_id].task_digest:
            raise ContractError(f"result task digest mismatch for {trial.task_id}")
    _validate_comparable_trials(result)


def _validate_comparable_trials(result: EvaluationResult) -> None:
    for trial in result.trials:
        if trial.classification in _NON_COMPARABLE_CLASSIFICATIONS:
            raise ContractError(f"result contains non-comparable trial: {trial.run_id}")


def build_bridge_report(
    before_task_set: TaskSetManifest,
    after_task_set: TaskSetManifest,
    before_result: EvaluationResult,
    after_result: EvaluationResult,
    before_result_sha256: str,
    after_result_sha256: str,
) -> BridgeReport:
    if before_result.purpose != EvaluationPurpose.COMPARISON or (
        after_result.purpose != EvaluationPurpose.COMPARISON
    ):
        raise ContractError("bridge reports require five-trial comparison results")
    if before_result.profile != after_result.profile:
        raise ContractError("bridge results must use the same profile")
    if before_result.configuration != after_result.configuration:
        raise ContractError("bridge results must use the same reference configuration")
    if before_result.result_origin != after_result.result_origin:
        raise ContractError("bridge results must use the same result origin")
    if before_result.task_set != task_set_binding(before_task_set) or (
        after_result.task_set != task_set_binding(after_task_set)
    ):
        raise ContractError("bridge result task-set binding mismatch")
    _validate_result_task_set(before_result, before_task_set)
    _validate_result_task_set(after_result, after_task_set)
    before_pins = {
        (trial.task_id, trial.task_digest): trial
        for trial in before_result.trials
        if trial.pair_index == 1
    }
    after_pins = {
        (trial.task_id, trial.task_digest): trial
        for trial in after_result.trials
        if trial.pair_index == 1
    }
    for identity in before_pins.keys() & after_pins.keys():
        before_trial = before_pins[identity]
        after_trial = after_pins[identity]
        if (
            before_trial.task != after_trial.task
            or before_trial.agent.instruction_layers != after_trial.agent.instruction_layers
            or before_trial.runtime != after_trial.runtime
            or before_trial.limits != after_trial.limits
        ):
            raise ContractError(
                f"bridge task execution pins drift for unchanged task: {identity[0]}"
            )
    return BridgeReport(
        schema_version=BRIDGE_SCHEMA_VERSION,
        before_task_set=before_result.task_set,
        after_task_set=after_result.task_set,
        profile=before_result.profile,
        configuration=before_result.configuration,
        before_result_sha256=before_result_sha256,
        after_result_sha256=after_result_sha256,
        coverage_before=coverage_snapshot(before_task_set),
        coverage_after=coverage_snapshot(after_task_set),
    )


def validate_retirement_models(
    retirement: RetirementManifest,
    bridge: BridgeReport,
    before: TaskSetManifest,
    after: TaskSetManifest,
) -> None:
    if retirement.before_task_set != task_set_binding(before) or (
        retirement.after_task_set != task_set_binding(after)
    ):
        raise ContractError("retirement task-set binding mismatch")
    if bridge.before_task_set != retirement.before_task_set or (
        bridge.after_task_set != retirement.after_task_set
    ):
        raise ContractError("retirement bridge task-set binding mismatch")
    before_coverage = coverage_snapshot(before)
    after_coverage = coverage_snapshot(after)
    if bridge.coverage_before != before_coverage or bridge.coverage_after != after_coverage:
        raise ContractError("retirement bridge coverage mismatch")
    categories = set(before_coverage.category_counts) | set(after_coverage.category_counts)
    for category in categories:
        if after_coverage.category_counts.get(category, 0) < before_coverage.category_counts.get(
            category, 0
        ):
            raise ContractError(f"retirement reduces {category.value} coverage")
    before_entries = {task.task_id: task for task in before.tasks}
    after_entries = {task.task_id: task for task in after.tasks}
    removed_task_ids = {
        task_id
        for task_id, entry in before_entries.items()
        if task_id not in after_entries or after_entries[task_id].task_digest != entry.task_digest
    }
    recorded_task_ids = {record.retired_task_id for record in retirement.records}
    if recorded_task_ids != removed_task_ids:
        raise ContractError("retirement records must cover every removed task exactly")
    for record in retirement.records:
        retired = before_entries.get(record.retired_task_id)
        replacement = after_entries.get(record.replacement_task_id)
        if retired is None or retired.task_digest != record.retired_task_digest:
            raise ContractError(f"unknown retired task: {record.retired_task_id}")
        if replacement is None or replacement.task_digest != record.replacement_task_digest:
            raise ContractError(f"unknown replacement task: {record.replacement_task_id}")
        previous_replacement = before_entries.get(record.replacement_task_id)
        if previous_replacement is not None and (
            previous_replacement.task_digest == record.replacement_task_digest
        ):
            raise ContractError(
                f"replacement task identity is not new: {record.replacement_task_id}"
            )
        if (
            record.replacement_task_id == record.retired_task_id
            and record.reason != RetirementReason.MAJOR_TASK_SET_RELEASE
        ):
            raise ContractError("same-ID replacement requires a major task-set release")
        if replacement.category != retired.category:
            raise ContractError("replacement task does not preserve category coverage")
        if record.publication.provenance != retired.provenance or (
            record.publication.license != retired.license
        ):
            raise ContractError("retired publication provenance or license mismatch")


def validate_retirement(
    retirement_path: Path,
    bridge_path: Path,
    before_task_set_path: Path,
    after_task_set_path: Path,
    before_result_path: Path,
    after_result_path: Path,
    project_root: Path,
) -> None:
    retirement = load_model(retirement_path, RetirementManifest)
    bridge = load_model(bridge_path, BridgeReport)
    before, _ = validate_task_set(before_task_set_path, project_root)
    after, _ = validate_task_set(after_task_set_path, project_root)
    if sha256_file(bridge_path) != retirement.bridge_sha256:
        raise ContractError("retirement bridge digest mismatch")
    before_result = load_model(before_result_path, EvaluationResult)
    after_result = load_model(after_result_path, EvaluationResult)
    before_result_sha = sha256_file(before_result_path)
    after_result_sha = sha256_file(after_result_path)
    if bridge.before_result_sha256 != before_result_sha or (
        bridge.after_result_sha256 != after_result_sha
    ):
        raise ContractError("retirement comparison result digest mismatch")
    expected_bridge = build_bridge_report(
        before,
        after,
        before_result,
        after_result,
        before_result_sha,
        after_result_sha,
    )
    if bridge != expected_bridge:
        raise ContractError("retirement bridge does not match its comparison results")
    validate_retirement_models(retirement, bridge, before, after)


def build_attestation_statement(evaluation_path: Path, result_path: Path) -> AttestationStatement:
    evaluation = load_model(evaluation_path, EvaluationManifest)
    result = load_model(result_path, EvaluationResult)
    if evaluation.evaluation_id != result.evaluation_id:
        raise ContractError("attestation evaluation identity mismatch")
    if result.result_origin != ResultOrigin.MAINTAINER:
        raise ContractError("only maintainer reference results may be attested as official")
    if result.evaluation_manifest_sha256 != sha256_file(evaluation_path):
        raise ContractError("attestation result does not bind the evaluation manifest")
    if (
        evaluation.task_set != result.task_set
        or evaluation.profile != result.profile
        or evaluation.purpose != result.purpose
        or evaluation.configuration != result.configuration
    ):
        raise ContractError("attestation result does not match the evaluation contract")
    expected_runs = sorted(
        (
            run.task_id,
            run.task_digest,
            run.pair_index,
            run.run_manifest_sha256,
            run.result_sha256,
            run.report_sha256,
        )
        for run in evaluation.runs
    )
    actual_runs = sorted(
        (
            trial.task_id,
            trial.task_digest,
            trial.pair_index,
            trial.run_manifest_sha256,
            trial.result_sha256,
            trial.report_sha256,
        )
        for trial in result.trials
    )
    if actual_runs != expected_runs:
        raise ContractError("attestation result trials do not match evaluation run bindings")
    _validate_comparable_trials(result)
    return AttestationStatement(
        schema_version=ATTESTATION_STATEMENT_SCHEMA_VERSION,
        evaluation_id=evaluation.evaluation_id,
        subjects=[
            AttestationSubject(name="evaluation-manifest", sha256=sha256_file(evaluation_path)),
            AttestationSubject(name="evaluation-result", sha256=sha256_file(result_path)),
        ],
    )


AttestationRunner = Callable[[list[str], bytes], int]
AttestationSignRunner = Callable[[list[str], bytes], tuple[int, bytes]]


def _run_ssh_verify(command: list[str], statement: bytes) -> int:
    completed = subprocess.run(
        command,
        input=statement,
        check=False,
        capture_output=True,
    )
    return completed.returncode


def _run_ssh_sign(command: list[str], statement: bytes) -> tuple[int, bytes]:
    completed = subprocess.run(
        command,
        input=statement,
        check=False,
        capture_output=True,
    )
    return completed.returncode, completed.stdout


def sign_reference_attestation(
    evaluation_path: Path,
    result_path: Path,
    identity_path: Path,
    signer: str,
    *,
    process_runner: AttestationSignRunner = _run_ssh_sign,
) -> ReferenceAttestation:
    statement = build_attestation_statement(evaluation_path, result_path)
    command = [
        "ssh-keygen",
        "-Y",
        "sign",
        "-f",
        str(identity_path.resolve()),
        "-n",
        "slopbench",
    ]
    returncode, signature = process_runner(command, canonical_json_bytes(statement))
    if returncode != 0:
        raise ContractError("could not create maintainer reference attestation signature")
    if not signature.startswith(b"-----BEGIN SSH SIGNATURE-----"):
        raise ContractError("ssh-keygen returned an invalid attestation signature")
    return ReferenceAttestation(
        schema_version=ATTESTATION_SCHEMA_VERSION,
        statement=statement,
        signature=SshSignature(
            signer=signer,
            signature_base64=base64.b64encode(signature).decode("ascii"),
        ),
    )


def verify_reference_attestation(
    attestation_path: Path,
    allowed_signers_path: Path,
    evaluation_path: Path,
    result_path: Path,
    *,
    process_runner: AttestationRunner = _run_ssh_verify,
) -> ReferenceVerification:
    attestation = load_model(attestation_path, ReferenceAttestation)
    expected = build_attestation_statement(evaluation_path, result_path)
    if attestation.statement != expected:
        raise ContractError("attestation subjects do not match the supplied reference bundle")
    try:
        signature = base64.b64decode(attestation.signature.signature_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ContractError("attestation signature is not valid base64") from exc
    if not signature.startswith(b"-----BEGIN SSH SIGNATURE-----"):
        raise ContractError("attestation signature is not an armored SSH signature")
    with tempfile.TemporaryDirectory(prefix="slopbench-attestation-") as temporary:
        signature_path = Path(temporary) / "reference.sig"
        signature_path.write_bytes(signature)
        command = [
            "ssh-keygen",
            "-Y",
            "verify",
            "-f",
            str(allowed_signers_path.resolve()),
            "-I",
            attestation.signature.signer,
            "-n",
            attestation.signature.namespace,
            "-s",
            str(signature_path),
        ]
        if process_runner(command, canonical_json_bytes(attestation.statement)) != 0:
            raise ContractError("maintainer reference attestation signature is not trusted")
    return ReferenceVerification(
        schema_version=REFERENCE_VERIFICATION_SCHEMA_VERSION,
        signer=attestation.signature.signer,
        attestation_sha256=sha256_file(attestation_path),
        statement_sha256=contract_digest(
            ATTESTATION_STATEMENT_SCHEMA_VERSION, attestation.statement
        ),
    )
