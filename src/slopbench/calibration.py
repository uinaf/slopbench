"""Release calibration, readiness, and regression contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from slopbench.contracts import (
    AttackKind,
    CapabilityCategory,
    ContractModel,
    FailureClassification,
    GateName,
    Identifier,
    OutcomeStatus,
    PhaseMode,
    Sha256Hex,
    TaskContract,
    TaskKind,
    Version,
    validate_relative_path,
)
from slopbench.hashing import ContractError, load_model, sha256_file, validate_task
from slopbench.release import (
    BridgeReport,
    EvaluationManifest,
    EvaluationPurpose,
    EvaluationResult,
    HeldOutDisclosure,
    ProfileDefinition,
    RawTrialOutcome,
    ReferenceConfiguration,
    ReferenceVerification,
    ResultOrigin,
    TaskSetManifest,
    VersionBinding,
    build_bridge_report,
    profile_binding,
    reference_configuration_binding,
    task_set_binding,
    validate_task_set,
    verify_reference_attestation,
)

RELEASE_EVIDENCE_SCHEMA_VERSION: Literal["slopbench.release-evidence.v1"] = (
    "slopbench.release-evidence.v1"
)
RELEASE_READINESS_SCHEMA_VERSION: Literal["slopbench.release-readiness.v1"] = (
    "slopbench.release-readiness.v1"
)
REGRESSION_SCHEMA_VERSION: Literal["slopbench.regression.v1"] = "slopbench.regression.v1"

TaskId = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$"),
]
HttpsUrl = Annotated[str, Field(pattern=r"^https://[^\s]+$")]

_RELEASE_CATEGORIES = frozenset(
    {
        CapabilityCategory.DIAGNOSIS_REPAIR,
        CapabilityCategory.FEATURE,
        CapabilityCategory.RESTRAINT,
        CapabilityCategory.COMPOSITION_DOMAIN_EVOLUTION,
        CapabilityCategory.STATE_EFFECTS,
        CapabilityCategory.CODE_REVIEW,
    }
)
_REQUIRED_PROFILES = frozenset(
    {"balanced", "reliability-first", "cost-aware", "fast-feedback", "altay"}
)
_REQUIRED_HARNESSES = frozenset({"cursor-cli", "codex", "claude-code"})
_REQUIRED_PUBLIC_MATERIALS = frozenset(
    {
        "LICENSE",
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/LIMITATIONS.md",
        "docs/METHODOLOGY.md",
        "docs/REPRODUCING.md",
        "docs/RESULTS.md",
        "docs/REVIEW_TASKS.md",
        "schemas/slopbench-reference-configuration.schema.json",
        "schemas/slopbench-regression.schema.json",
        "schemas/slopbench-release-evidence.schema.json",
        "schemas/slopbench-release-readiness.schema.json",
        "schemas/slopbench-result.schema.json",
        "schemas/slopbench-task.schema.json",
    }
)


class ReleaseStage(StrEnum):
    PROVISIONAL = "provisional"
    STABLE = "stable"


class ReviewDecision(StrEnum):
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes_requested"


class AuditStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class AuditKind(StrEnum):
    CLASSIFICATION = "classification"
    LICENSE_PROVENANCE = "license_provenance"
    PRIVACY_LEAKAGE = "privacy_leakage"
    ANTI_CHEAT = "anti_cheat"
    CLEAN_REPRODUCTION = "clean_reproduction"
    HELD_OUT_EXECUTION = "held_out_execution"


class CommonHarnessDisposition(StrEnum):
    INCLUDED = "included"
    OMITTED_UNSTABLE = "omitted_unstable"


class HeldOutStatus(StrEnum):
    NOT_AVAILABLE = "not_available"
    ACTIVE_PRIVATE = "active_private"


class GateStatus(StrEnum):
    PASSED = "passed"
    PENDING = "pending"
    FAILED = "failed"


class ReleaseGate(StrEnum):
    CORPUS_BALANCE = "corpus_balance"
    SEQUENTIAL_COVERAGE = "sequential_coverage"
    MACHINE_ADMISSION = "machine_admission"
    OWNER_APPROVAL = "owner_approval"
    INDEPENDENT_HUMAN_REVIEW = "independent_human_review"
    EXPERT_CALIBRATION = "expert_calibration"
    ADVERSARIAL_COVERAGE = "adversarial_coverage"
    REFERENCE_CONFIGURATIONS = "reference_configurations"
    COMMON_HARNESS_DECISION = "common_harness_decision"
    REFERENCE_COMPARISONS = "reference_comparisons"
    SIGNED_REFERENCES = "signed_references"
    LICENSE_PROVENANCE = "license_provenance"
    RELEASE_AUDITS = "release_audits"
    PUBLIC_MATERIALS = "public_materials"
    HELD_OUT = "held_out"
    CROSS_VERSION_DISCIPLINE = "cross_version_discipline"


class RegressionKind(StrEnum):
    RELIABILITY = "reliability"
    CRITICAL_GATE = "critical_gate"


class BoundDocument(ContractModel):
    path: str
    sha256: Sha256Hex

    _path = field_validator("path")(validate_relative_path)


class ExternalEvidence(ContractModel):
    url: HttpsUrl
    sha256: Sha256Hex


class TaskEvidenceIdentity(ContractModel):
    task_id: TaskId
    task_digest: Sha256Hex


class HumanTaskReview(ContractModel):
    task: TaskEvidenceIdentity
    reviewer: Identifier
    decision: ReviewDecision
    evidence: ExternalEvidence


class ExpertCalibrationRun(ContractModel):
    expert: Identifier
    tasks: list[TaskEvidenceIdentity] = Field(min_length=1)
    decision: ReviewDecision
    evidence: ExternalEvidence

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        if len(self.tasks) != len({task.task_id for task in self.tasks}):
            raise ValueError("expert calibration task IDs must be unique")
        return self


class ReleaseAudit(ContractModel):
    kind: AuditKind
    auditor: Identifier
    independent: bool
    status: AuditStatus
    evidence: ExternalEvidence


class CommonHarnessDecision(ContractModel):
    disposition: CommonHarnessDisposition
    configuration_id: Identifier | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def matching_disposition(self) -> Self:
        if (self.disposition == CommonHarnessDisposition.INCLUDED) != (
            self.configuration_id is not None
        ):
            raise ValueError("included common harness requires exactly one configuration ID")
        return self


class HeldOutEvidence(ContractModel):
    status: HeldOutStatus
    task_set: VersionBinding | None = None
    disclosure: BoundDocument | None = None
    audit: ExternalEvidence | None = None

    @model_validator(mode="after")
    def private_binding_is_complete(self) -> Self:
        values = (self.task_set, self.disclosure, self.audit)
        if self.status == HeldOutStatus.ACTIVE_PRIVATE:
            if any(value is None for value in values):
                raise ValueError("active held-out evidence requires binding, disclosure, and audit")
        elif any(value is not None for value in values):
            raise ValueError("unavailable held-out evidence cannot expose partial metadata")
        return self


class ReferenceComparisonRecord(ContractModel):
    configuration_id: Identifier
    evaluation: BoundDocument
    result: BoundDocument
    attestation: BoundDocument
    allowed_signers: BoundDocument
    verification: BoundDocument

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        paths = [
            self.evaluation.path,
            self.result.path,
            self.attestation.path,
            self.allowed_signers.path,
            self.verification.path,
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("reference comparison paths must be unique")
        return self


class CrossVersionClaim(ContractModel):
    claim_id: Identifier
    before_task_set: BoundDocument
    after_task_set: BoundDocument
    before_result: BoundDocument
    after_result: BoundDocument
    bridge: BoundDocument

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        paths = [
            self.before_task_set.path,
            self.after_task_set.path,
            self.before_result.path,
            self.after_result.path,
            self.bridge.path,
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("cross-version claim paths must be unique")
        return self


class ReleaseEvidenceManifest(ContractModel):
    schema_version: Literal["slopbench.release-evidence.v1"] = RELEASE_EVIDENCE_SCHEMA_VERSION
    release_id: Identifier
    version: Version
    stage: ReleaseStage
    task_set: BoundDocument
    tracer_task: BoundDocument
    profiles: list[BoundDocument] = Field(min_length=1)
    reference_configurations: list[BoundDocument] = Field(min_length=1)
    public_materials: list[BoundDocument] = Field(min_length=1)
    primary_configuration_id: Identifier
    common_harness: CommonHarnessDecision
    human_reviews: list[HumanTaskReview] = Field(default_factory=list)
    expert_runs: list[ExpertCalibrationRun] = Field(default_factory=list)
    audits: list[ReleaseAudit] = Field(default_factory=list)
    reference_comparisons: list[ReferenceComparisonRecord] = Field(default_factory=list)
    held_out: HeldOutEvidence
    cross_version_claims: list[CrossVersionClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.stage == ReleaseStage.PROVISIONAL and not self.version.startswith("0."):
            raise ValueError("provisional releases require a 0.x version")
        if self.stage == ReleaseStage.STABLE and not self.version.startswith("1."):
            raise ValueError("stable SWE v1 releases require a 1.x version")
        bound_paths = [
            self.task_set.path,
            self.tracer_task.path,
            *(item.path for item in self.profiles),
            *(item.path for item in self.reference_configurations),
            *(item.path for item in self.public_materials),
        ]
        if len(bound_paths) != len(set(bound_paths)):
            raise ValueError("release bound document paths must be unique")
        review_keys = [(review.task.task_id, review.reviewer) for review in self.human_reviews]
        if len(review_keys) != len(set(review_keys)):
            raise ValueError("human task reviewer pairs must be unique")
        audit_kinds = [audit.kind for audit in self.audits]
        if len(audit_kinds) != len(set(audit_kinds)):
            raise ValueError("release audit kinds must be unique")
        comparison_ids = [record.configuration_id for record in self.reference_comparisons]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("reference comparison configuration IDs must be unique")
        claim_ids = [claim.claim_id for claim in self.cross_version_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("cross-version claim IDs must be unique")
        return self


class CorpusSnapshot(ContractModel):
    task_count: int = Field(ge=0)
    category_counts: dict[CapabilityCategory, int]
    patch_task_count: int = Field(ge=0)
    sequential_patch_task_count: int = Field(ge=0)
    sequential_patch_bps: int = Field(ge=0, le=10_000)
    machine_admitted_task_count: int = Field(ge=0)
    owner_approved_task_count: int = Field(ge=0)
    independently_reviewed_task_count: int = Field(ge=0)
    expert_category_count: int = Field(ge=0)
    attack_kind_counts: dict[AttackKind, int]


class GateAssessment(ContractModel):
    gate: ReleaseGate
    status: GateStatus
    detail: str = Field(min_length=1)


class ReleaseReadinessReport(ContractModel):
    schema_version: Literal["slopbench.release-readiness.v1"] = RELEASE_READINESS_SCHEMA_VERSION
    release_id: Identifier
    version: Version
    stage: ReleaseStage
    evidence_manifest_sha256: Sha256Hex
    task_set: VersionBinding
    profiles: list[VersionBinding]
    reference_configurations: list[VersionBinding]
    primary_configuration_id: Identifier
    corpus: CorpusSnapshot
    gates: list[GateAssessment]
    blockers: list[ReleaseGate]
    stable_eligible: bool

    @model_validator(mode="after")
    def internally_consistent(self) -> Self:
        if [assessment.gate for assessment in self.gates] != list(ReleaseGate):
            raise ValueError("readiness gates must contain every gate in stable order")
        expected_blockers = [
            assessment.gate for assessment in self.gates if assessment.status != GateStatus.PASSED
        ]
        if self.blockers != expected_blockers:
            raise ValueError("readiness blockers must match non-passing gates")
        if self.stable_eligible != (not self.blockers):
            raise ValueError("stable eligibility must match the blocker set")
        if self.stage == ReleaseStage.STABLE and not self.stable_eligible:
            raise ValueError("stable release evidence cannot retain blockers")
        return self


class RegressionFlag(ContractModel):
    kind: RegressionKind
    task_id: TaskId
    task_digest: Sha256Hex
    failed_pair_indices: list[int] = Field(min_length=1)
    critical_gate: GateName | None = None

    @model_validator(mode="after")
    def matching_kind(self) -> Self:
        if (self.kind == RegressionKind.CRITICAL_GATE) != (self.critical_gate is not None):
            raise ValueError("critical regression flags require exactly one critical gate")
        if len(self.failed_pair_indices) != len(set(self.failed_pair_indices)):
            raise ValueError("regression pair indices must be unique")
        if any(index < 1 or index > 5 for index in self.failed_pair_indices):
            raise ValueError("regression pair indices must be between one and five")
        return self


class RegressionReport(ContractModel):
    schema_version: Literal["slopbench.regression.v1"] = REGRESSION_SCHEMA_VERSION
    before_result_sha256: Sha256Hex
    after_result_sha256: Sha256Hex
    task_set: VersionBinding
    profile: VersionBinding
    configuration: ReferenceConfiguration
    paired_trials: Literal[5] = 5
    flags: list[RegressionFlag]
    automatic_release_blocking: Literal[False] = False


def _resolve_bound(root: Path, bound: BoundDocument) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(bound.path).parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or candidate.is_symlink() or not resolved.is_file():
        raise ContractError(f"release evidence path is not a regular project file: {bound.path}")
    if sha256_file(resolved) != bound.sha256:
        raise ContractError(f"release evidence digest mismatch: {bound.path}")
    return resolved


def _assessment(
    gate: ReleaseGate,
    passed: bool,
    detail: str,
    *,
    failed: bool = False,
) -> GateAssessment:
    status = GateStatus.PASSED if passed else (GateStatus.FAILED if failed else GateStatus.PENDING)
    return GateAssessment(gate=gate, status=status, detail=detail)


def _validate_reference_comparison(
    root: Path,
    record: ReferenceComparisonRecord,
    configuration: ReferenceConfiguration,
    task_set: TaskSetManifest,
) -> tuple[VersionBinding, list[tuple[str, int, int | None]]]:
    evaluation_path = _resolve_bound(root, record.evaluation)
    result_path = _resolve_bound(root, record.result)
    attestation_path = _resolve_bound(root, record.attestation)
    allowed_signers_path = _resolve_bound(root, record.allowed_signers)
    verification_path = _resolve_bound(root, record.verification)
    evaluation = load_model(evaluation_path, EvaluationManifest)
    result = load_model(result_path, EvaluationResult)
    verification = load_model(verification_path, ReferenceVerification)
    task_set_identity = task_set_binding(task_set)
    if (
        evaluation.purpose != EvaluationPurpose.COMPARISON
        or result.purpose != EvaluationPurpose.COMPARISON
        or evaluation.evaluation_id != result.evaluation_id
        or evaluation.task_set != task_set_identity
        or result.task_set != task_set_identity
        or evaluation.profile != result.profile
        or evaluation.configuration != configuration
        or result.configuration != configuration
        or result.result_origin != ResultOrigin.MAINTAINER
        or result.evaluation_manifest_sha256 != sha256_file(evaluation_path)
    ):
        raise ContractError(
            f"reference comparison does not match {record.configuration_id} and the release"
        )
    evaluation_runs = {(run.task_id, run.pair_index): run for run in evaluation.runs}
    result_trials = {(trial.task_id, trial.pair_index): trial for trial in result.trials}
    if evaluation_runs.keys() != result_trials.keys():
        raise ContractError("reference evaluation and result trial pairing does not match")
    for key, run in evaluation_runs.items():
        trial = result_trials[key]
        if (
            run.task_digest != trial.task_digest
            or run.run_manifest_sha256 != trial.run_manifest_sha256
            or run.result_sha256 != trial.result_sha256
            or run.report_sha256 != trial.report_sha256
        ):
            raise ContractError(f"reference evaluation binding does not match result trial: {key}")
    computed = verify_reference_attestation(
        attestation_path,
        allowed_signers_path,
        evaluation_path,
        result_path,
    )
    if verification != computed:
        raise ContractError("reference verification does not match cryptographic verification")
    schedule = [(trial.task_id, trial.pair_index, trial.trial.seed) for trial in result.trials]
    if any(seed is None for _, _, seed in schedule):
        raise ContractError("reference comparisons require explicit trial seeds")
    return result.profile, schedule


def _task_contracts(task_set: TaskSetManifest, root: Path) -> dict[str, TaskContract]:
    contracts: dict[str, TaskContract] = {}
    for entry in task_set.tasks:
        path = root.joinpath(*PurePosixPath(entry.contract_path).parts)
        task, _, digest = validate_task(path.parent)
        if digest != entry.task_digest:
            raise ContractError(f"release task digest mismatch for {entry.task_id}")
        contracts[entry.task_id] = task
    return contracts


def _validate_cross_version_claim(root: Path, claim: CrossVersionClaim) -> None:
    before_task_set_path = _resolve_bound(root, claim.before_task_set)
    after_task_set_path = _resolve_bound(root, claim.after_task_set)
    before_result_path = _resolve_bound(root, claim.before_result)
    after_result_path = _resolve_bound(root, claim.after_result)
    bridge_path = _resolve_bound(root, claim.bridge)
    before_task_set, _ = validate_task_set(before_task_set_path, root)
    after_task_set, _ = validate_task_set(after_task_set_path, root)
    before_result = load_model(before_result_path, EvaluationResult)
    after_result = load_model(after_result_path, EvaluationResult)
    bridge = load_model(bridge_path, BridgeReport)
    computed = build_bridge_report(
        before_task_set,
        after_task_set,
        before_result,
        after_result,
        sha256_file(before_result_path),
        sha256_file(after_result_path),
    )
    if bridge != computed:
        raise ContractError(f"cross-version bridge does not recompute for {claim.claim_id}")


def audit_release(
    evidence_path: Path,
    project_root: Path,
) -> ReleaseReadinessReport:
    project_root = project_root.resolve()
    evidence = load_model(evidence_path, ReleaseEvidenceManifest)
    task_set_path = _resolve_bound(project_root, evidence.task_set)
    task_set, _ = validate_task_set(task_set_path, project_root)
    contracts = _task_contracts(task_set, project_root)
    tracer_path = _resolve_bound(project_root, evidence.tracer_task)
    if tracer_path.name != "slopbench-task.json":
        raise ContractError("tracer evidence must bind a SlopBench task contract")
    tracer, _, _ = validate_task(tracer_path.parent)
    if tracer.design.category != CapabilityCategory.TRACER:
        raise ContractError("release tracer evidence is not the tracer task")

    profiles: list[ProfileDefinition] = [
        load_model(_resolve_bound(project_root, bound), ProfileDefinition)
        for bound in evidence.profiles
    ]
    profile_ids = [profile.profile_id for profile in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ContractError("release profile IDs must be unique")
    configurations: list[ReferenceConfiguration] = [
        load_model(_resolve_bound(project_root, bound), ReferenceConfiguration)
        for bound in evidence.reference_configurations
    ]
    configuration_ids = [configuration.configuration_id for configuration in configurations]
    if len(configuration_ids) != len(set(configuration_ids)):
        raise ContractError("release reference configuration IDs must be unique")
    configuration_by_id = {
        configuration.configuration_id: configuration for configuration in configurations
    }
    primary = configuration_by_id.get(evidence.primary_configuration_id)
    if primary is None:
        raise ContractError("primary reference configuration is not bound by the release")

    category_counts = Counter(task.design.category for task in contracts.values())
    patch_tasks = [task for task in contracts.values() if task.kind == TaskKind.PATCH]
    sequential_tasks = [task for task in patch_tasks if task.phase_mode == PhaseMode.SEQUENTIAL]
    sequential_bps = 0 if not patch_tasks else len(sequential_tasks) * 10_000 // len(patch_tasks)
    machine_admitted = sum(task.design.admission.evidence.complete for task in contracts.values())
    owner_approved = sum(task.design.admission.status == "approved" for task in contracts.values())

    accepted_reviews: set[str] = set()
    review_objections = False
    for review in evidence.human_reviews:
        task = contracts.get(review.task.task_id)
        if task is None or review.task.task_digest != task_set_entry_digest(task_set, task.task_id):
            raise ContractError(f"human review binds an unknown task: {review.task.task_id}")
        if review.reviewer == task.design.owner:
            raise ContractError(f"human review is not independent for {review.task.task_id}")
        if review.decision == ReviewDecision.ACCEPTED:
            accepted_reviews.add(review.task.task_id)
        else:
            review_objections = True

    expert_categories: set[CapabilityCategory] = set()
    expert_objections = False
    for run in evidence.expert_runs:
        for identity in run.tasks:
            task = contracts.get(identity.task_id)
            if task is None or identity.task_digest != task_set_entry_digest(
                task_set, identity.task_id
            ):
                raise ContractError(f"expert run binds an unknown task: {identity.task_id}")
            if run.decision == ReviewDecision.ACCEPTED:
                expert_categories.add(task.design.category)
            else:
                expert_objections = True

    attack_counts = Counter(
        fixture.kind for task in [*contracts.values(), tracer] for fixture in task.attack_fixtures
    )
    corpus = CorpusSnapshot(
        task_count=len(contracts),
        category_counts={category: category_counts[category] for category in CapabilityCategory},
        patch_task_count=len(patch_tasks),
        sequential_patch_task_count=len(sequential_tasks),
        sequential_patch_bps=sequential_bps,
        machine_admitted_task_count=machine_admitted,
        owner_approved_task_count=owner_approved,
        independently_reviewed_task_count=len(accepted_reviews),
        expert_category_count=len(expert_categories),
        attack_kind_counts={kind: attack_counts[kind] for kind in AttackKind},
    )

    exact_categories = (
        len(contracts) == 12
        and set(category_counts) == _RELEASE_CATEGORIES
        and all(category_counts[category] == 2 for category in _RELEASE_CATEGORIES)
    )
    balanced_sequential = bool(patch_tasks) and 2_500 <= sequential_bps <= 4_500
    machine_complete = machine_admitted == len(contracts)
    approvals_complete = owner_approved == len(contracts)
    reviews_complete = len(accepted_reviews) == len(contracts) and not review_objections
    experts_complete = expert_categories == _RELEASE_CATEGORIES and not expert_objections
    adversarial_complete = all(attack_counts[kind] > 0 for kind in AttackKind)
    configured_harnesses = {configuration.harness.name for configuration in configurations}
    reference_configs_complete = (
        configured_harnesses >= _REQUIRED_HARNESSES and primary.harness.name == "cursor-cli"
    )
    common_decision_complete = (
        evidence.common_harness.disposition == CommonHarnessDisposition.OMITTED_UNSTABLE
        or evidence.common_harness.configuration_id in configuration_by_id
    )

    comparisons = {record.configuration_id: record for record in evidence.reference_comparisons}
    unknown_comparisons = set(comparisons) - set(configuration_by_id)
    if unknown_comparisons:
        raise ContractError(
            f"reference comparisons use unknown configurations: {sorted(unknown_comparisons)}"
        )
    matched_profile: VersionBinding | None = None
    matched_schedule: list[tuple[str, int, int | None]] | None = None
    for configuration_id, record in sorted(comparisons.items()):
        comparison_profile, comparison_schedule = _validate_reference_comparison(
            project_root,
            record,
            configuration_by_id[configuration_id],
            task_set,
        )
        if matched_profile is None:
            matched_profile = comparison_profile
            matched_schedule = comparison_schedule
        elif comparison_profile != matched_profile or comparison_schedule != matched_schedule:
            raise ContractError(
                "reference comparisons must use one profile and matched task/trial seeds"
            )
    comparison_complete = set(comparisons) == set(configuration_by_id)

    audit_by_kind = {audit.kind: audit for audit in evidence.audits}
    required_audits = set(AuditKind) - {AuditKind.HELD_OUT_EXECUTION}
    audits_complete = all(
        kind in audit_by_kind
        and audit_by_kind[kind].status == AuditStatus.PASSED
        and audit_by_kind[kind].independent
        for kind in required_audits
    )
    audit_failed = any(
        audit.status == AuditStatus.FAILED or not audit.independent
        for audit in evidence.audits
        if audit.kind in required_audits
    )
    licenses_complete = all(
        task.license.spdx == "MIT" and bool(task.license.holder.strip())
        for task in contracts.values()
    )

    public_material_paths = {bound.path for bound in evidence.public_materials}
    for bound in evidence.public_materials:
        _resolve_bound(project_root, bound)
    public_materials_complete = (
        set(profile_ids) >= _REQUIRED_PROFILES
        and public_material_paths >= _REQUIRED_PUBLIC_MATERIALS
    )

    held_out_complete = evidence.held_out.status == HeldOutStatus.ACTIVE_PRIVATE
    if held_out_complete:
        assert evidence.held_out.disclosure is not None
        assert evidence.held_out.task_set is not None
        disclosure = load_model(
            _resolve_bound(project_root, evidence.held_out.disclosure), HeldOutDisclosure
        )
        if disclosure.task_set != evidence.held_out.task_set:
            raise ContractError("held-out disclosure does not match the private task-set binding")
        held_out_audit = audit_by_kind.get(AuditKind.HELD_OUT_EXECUTION)
        held_out_complete = (
            held_out_audit is not None
            and held_out_audit.status == AuditStatus.PASSED
            and held_out_audit.independent
        )

    for claim in evidence.cross_version_claims:
        _validate_cross_version_claim(project_root, claim)

    rendered_categories = dict(sorted((key.value, value) for key, value in category_counts.items()))
    covered_attack_kinds = sum(attack_counts[kind] > 0 for kind in AttackKind)
    mit_task_count = sum(task.license.spdx == "MIT" for task in contracts.values())
    recorded_audit_count = sum(kind in audit_by_kind for kind in required_audits)
    gates = [
        _assessment(
            ReleaseGate.CORPUS_BALANCE,
            exact_categories,
            f"{len(contracts)} tasks; category counts are {rendered_categories}",
            failed=not exact_categories,
        ),
        _assessment(
            ReleaseGate.SEQUENTIAL_COVERAGE,
            balanced_sequential,
            f"{len(sequential_tasks)}/{len(patch_tasks)} patch tasks use fresh sequential phases",
            failed=bool(patch_tasks) and not balanced_sequential,
        ),
        _assessment(
            ReleaseGate.MACHINE_ADMISSION,
            machine_complete,
            f"{machine_admitted}/{len(contracts)} tasks have complete machine admission evidence",
        ),
        _assessment(
            ReleaseGate.OWNER_APPROVAL,
            approvals_complete,
            f"{owner_approved}/{len(contracts)} task contracts carry owner approval",
        ),
        _assessment(
            ReleaseGate.INDEPENDENT_HUMAN_REVIEW,
            reviews_complete,
            f"{len(accepted_reviews)}/{len(contracts)} tasks have accepted independent review",
            failed=review_objections,
        ),
        _assessment(
            ReleaseGate.EXPERT_CALIBRATION,
            experts_complete,
            f"expert runs cover {len(expert_categories)}/{len(_RELEASE_CATEGORIES)} categories",
            failed=expert_objections,
        ),
        _assessment(
            ReleaseGate.ADVERSARIAL_COVERAGE,
            adversarial_complete,
            f"attack fixtures cover {covered_attack_kinds}/{len(AttackKind)} kinds",
            failed=not adversarial_complete,
        ),
        _assessment(
            ReleaseGate.REFERENCE_CONFIGURATIONS,
            reference_configs_complete,
            (
                f"pinned harnesses are {sorted(configured_harnesses)}; "
                f"primary is {primary.harness.name}"
            ),
            failed=not reference_configs_complete,
        ),
        _assessment(
            ReleaseGate.COMMON_HARNESS_DECISION,
            common_decision_complete,
            evidence.common_harness.rationale,
            failed=not common_decision_complete,
        ),
        _assessment(
            ReleaseGate.REFERENCE_COMPARISONS,
            comparison_complete,
            f"{len(comparisons)}/{len(configurations)} configurations have five-trial comparisons",
        ),
        _assessment(
            ReleaseGate.SIGNED_REFERENCES,
            comparison_complete,
            (
                f"{len(comparisons)}/{len(configurations)} comparison results have "
                "verified maintainer signatures"
            ),
        ),
        _assessment(
            ReleaseGate.LICENSE_PROVENANCE,
            licenses_complete,
            (f"{mit_task_count}/{len(contracts)} tasks declare MIT licensing and provenance"),
            failed=not licenses_complete,
        ),
        _assessment(
            ReleaseGate.RELEASE_AUDITS,
            audits_complete,
            (
                f"{recorded_audit_count}/{len(required_audits)} independent release "
                "audits are recorded"
            ),
            failed=audit_failed,
        ),
        _assessment(
            ReleaseGate.PUBLIC_MATERIALS,
            public_materials_complete,
            (
                "license, methodology, schemas, profiles, limitations, and reproduction "
                "materials are present"
            ),
            failed=not public_materials_complete,
        ),
        _assessment(
            ReleaseGate.HELD_OUT,
            held_out_complete,
            f"held-out status is {evidence.held_out.status.value}",
        ),
        _assessment(
            ReleaseGate.CROSS_VERSION_DISCIPLINE,
            True,
            f"{len(evidence.cross_version_claims)} cross-version claims each bind a bridge report",
        ),
    ]
    blockers = [assessment.gate for assessment in gates if assessment.status != GateStatus.PASSED]
    if evidence.stage == ReleaseStage.STABLE and blockers:
        raise ContractError(f"stable release is blocked by: {[gate.value for gate in blockers]}")
    return ReleaseReadinessReport(
        release_id=evidence.release_id,
        version=evidence.version,
        stage=evidence.stage,
        evidence_manifest_sha256=sha256_file(evidence_path),
        task_set=task_set_binding(task_set),
        profiles=sorted(
            (profile_binding(profile) for profile in profiles), key=lambda item: item.id
        ),
        reference_configurations=sorted(
            (reference_configuration_binding(configuration) for configuration in configurations),
            key=lambda item: item.id,
        ),
        primary_configuration_id=evidence.primary_configuration_id,
        corpus=corpus,
        gates=gates,
        blockers=blockers,
        stable_eligible=not blockers,
    )


def task_set_entry_digest(task_set: TaskSetManifest, task_id: str) -> str:
    for entry in task_set.tasks:
        if entry.task_id == task_id:
            return entry.task_digest
    raise ContractError(f"task set does not contain {task_id}")


def build_regression_report(
    before: EvaluationResult,
    after: EvaluationResult,
    before_result_sha256: str,
    after_result_sha256: str,
) -> RegressionReport:
    if (
        before.purpose != EvaluationPurpose.COMPARISON
        or after.purpose != EvaluationPurpose.COMPARISON
    ):
        raise ContractError("regression reports require five-trial comparison results")
    if (
        before.task_set != after.task_set
        or before.profile != after.profile
        or before.configuration != after.configuration
    ):
        raise ContractError(
            "regression results must use identical task set, profile, and configuration"
        )
    before_by_task: dict[str, list[RawTrialOutcome]] = defaultdict(list)
    after_by_task: dict[str, list[RawTrialOutcome]] = defaultdict(list)
    for trial in before.trials:
        before_by_task[trial.task_id].append(trial)
    for trial in after.trials:
        after_by_task[trial.task_id].append(trial)
    before_schedule = {
        (trial.task_id, trial.pair_index): trial.trial.seed for trial in before.trials
    }
    after_schedule = {(trial.task_id, trial.pair_index): trial.trial.seed for trial in after.trials}
    if any(seed is None for seed in before_schedule.values()) or any(
        seed is None for seed in after_schedule.values()
    ):
        raise ContractError("regression results require explicit trial seeds")
    if before_schedule != after_schedule:
        raise ContractError("regression results must use matched task/pair trial seeds")
    flags: list[RegressionFlag] = []
    critical_gates = (GateName.AUTHORITY, GateName.SAFETY_TYPE_ESCAPES)
    attributable = {
        FailureClassification.VALID_PASS,
        FailureClassification.VALID_AGENT_FAILURE,
        FailureClassification.INVALID_RUN,
    }
    for task_id in sorted(before_by_task):
        before_trials = before_by_task[task_id]
        after_trials = after_by_task[task_id]
        digest = before_trials[0].task_digest
        previously_reliable = all(
            trial.classification == FailureClassification.VALID_PASS for trial in before_trials
        )
        failed_pairs = [
            trial.pair_index
            for trial in after_trials
            if trial.classification
            in {
                FailureClassification.VALID_AGENT_FAILURE,
                FailureClassification.INVALID_RUN,
            }
        ]
        if previously_reliable and len(failed_pairs) >= 2:
            flags.append(
                RegressionFlag(
                    kind=RegressionKind.RELIABILITY,
                    task_id=task_id,
                    task_digest=digest,
                    failed_pair_indices=failed_pairs,
                )
            )
        for gate in critical_gates:
            before_failed = any(
                trial.classification in attributable
                and any(
                    outcome.gate == gate and outcome.status == OutcomeStatus.FAILED
                    for outcome in trial.outcomes
                )
                for trial in before_trials
            )
            new_failed_pairs = [
                trial.pair_index
                for trial in after_trials
                if trial.classification in attributable
                and any(
                    outcome.gate == gate and outcome.status == OutcomeStatus.FAILED
                    for outcome in trial.outcomes
                )
            ]
            if not before_failed and new_failed_pairs:
                flags.append(
                    RegressionFlag(
                        kind=RegressionKind.CRITICAL_GATE,
                        task_id=task_id,
                        task_digest=digest,
                        failed_pair_indices=new_failed_pairs,
                        critical_gate=gate,
                    )
                )
    return RegressionReport(
        before_result_sha256=before_result_sha256,
        after_result_sha256=after_result_sha256,
        task_set=before.task_set,
        profile=before.profile,
        configuration=before.configuration,
        flags=flags,
    )
