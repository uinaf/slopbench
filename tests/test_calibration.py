from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from slopbench import calibration
from slopbench.calibration import (
    AuditKind,
    AuditStatus,
    BoundDocument,
    CommonHarnessDecision,
    CommonHarnessDisposition,
    CrossVersionClaim,
    ExpertCalibrationRun,
    ExternalEvidence,
    GateStatus,
    HeldOutEvidence,
    HeldOutStatus,
    HumanTaskReview,
    ReferenceComparisonRecord,
    RegressionFlag,
    RegressionKind,
    RegressionReport,
    ReleaseAudit,
    ReleaseEvidenceManifest,
    ReleaseGate,
    ReleaseReadinessReport,
    ReleaseStage,
    ReviewDecision,
    TaskEvidenceIdentity,
    audit_release,
    build_regression_report,
)
from slopbench.contracts import (
    FailureClassification,
    FailureReason,
    GateName,
    OutcomeStatus,
)
from slopbench.hashing import (
    ContractError,
    load_model,
    sha256_file,
    validate_task,
    write_model,
)
from slopbench.release import (
    EvaluationManifest,
    EvaluationPurpose,
    EvaluationResult,
    ProfileDefinition,
    RawResultVector,
    RawTrialOutcome,
    ReferenceAttestation,
    ReferenceConfiguration,
    ReferenceVerification,
    ResultOrigin,
    SshSignature,
    TaskSetManifest,
    TaskSetVisibility,
    VersionBinding,
    _aggregate,
    build_attestation_statement,
    build_bridge_report,
    build_held_out_disclosure,
    contract_digest,
    profile_binding,
    task_set_binding,
)
from tests.test_release import (
    digest,
    direct_result,
    materialize_evaluation,
    one_task_set,
    profile,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "release" / "slopbench-swe-v1-dev-evidence.json"

VERSIONED_CALIBRATION_MODELS: list[type[BaseModel]] = [
    ReleaseEvidenceManifest,
    ReleaseReadinessReport,
    RegressionReport,
]


@pytest.mark.parametrize("model", VERSIONED_CALIBRATION_MODELS)
def test_calibration_schema_version_is_required(model: type[BaseModel]) -> None:
    assert model.model_fields["schema_version"].is_required()
    assert "schema_version" in model.model_json_schema()["required"]


def candidate_evidence() -> ReleaseEvidenceManifest:
    return load_model(EVIDENCE_PATH, ReleaseEvidenceManifest)


def write_evidence(tmp_path: Path, evidence: ReleaseEvidenceManifest) -> Path:
    path = tmp_path / "release-evidence.json"
    write_model(path, evidence)
    return path


def gate_status(report: ReleaseReadinessReport, gate: ReleaseGate) -> GateStatus:
    return next(item.status for item in report.gates if item.gate == gate)


def external_evidence(label: str = "evidence") -> ExternalEvidence:
    return ExternalEvidence(url=f"https://example.test/{label}", sha256=digest(label))


def task_identities(task_set: TaskSetManifest) -> list[TaskEvidenceIdentity]:
    return [
        TaskEvidenceIdentity(task_id=entry.task_id, task_digest=entry.task_digest)
        for entry in task_set.tasks
    ]


def test_candidate_release_report_records_machine_proof_and_human_blockers() -> None:
    report = audit_release(EVIDENCE_PATH, ROOT)

    assert report.corpus.task_count == 12
    assert report.corpus.patch_task_count == 10
    assert report.corpus.sequential_patch_task_count == 4
    assert report.corpus.machine_admitted_task_count == 12
    assert report.primary_configuration_id == "cursor-grok-4.6-medium"
    assert report.stable_eligible is False
    assert report.blockers == [
        ReleaseGate.OWNER_APPROVAL,
        ReleaseGate.INDEPENDENT_HUMAN_REVIEW,
        ReleaseGate.EXPERT_CALIBRATION,
        ReleaseGate.REFERENCE_COMPARISONS,
        ReleaseGate.SIGNED_REFERENCES,
        ReleaseGate.RELEASE_AUDITS,
        ReleaseGate.HELD_OUT,
    ]
    assert gate_status(report, ReleaseGate.CORPUS_BALANCE) == GateStatus.PASSED
    assert gate_status(report, ReleaseGate.ADVERSARIAL_COVERAGE) == GateStatus.PASSED
    assert gate_status(report, ReleaseGate.REFERENCE_CONFIGURATIONS) == GateStatus.PASSED
    assert gate_status(report, ReleaseGate.PUBLIC_MATERIALS) == GateStatus.PASSED


def test_release_audit_accepts_bound_independent_reviews_experts_and_audits(
    tmp_path: Path,
) -> None:
    task_set = load_model(ROOT / "datasets/slopbench-swe-v1-dev.json", TaskSetManifest)
    identities = task_identities(task_set)
    reviews = [
        HumanTaskReview(
            task=identity,
            reviewer=f"reviewer-{index}",
            decision=ReviewDecision.ACCEPTED,
            evidence=external_evidence(f"review-{index}"),
        )
        for index, identity in enumerate(identities, start=1)
    ]
    expert = ExpertCalibrationRun(
        expert="domain-expert",
        tasks=identities,
        decision=ReviewDecision.ACCEPTED,
        evidence=external_evidence("expert"),
    )
    audits = [
        ReleaseAudit(
            kind=kind,
            auditor=f"auditor-{kind.value}",
            independent=True,
            status=AuditStatus.PASSED,
            evidence=external_evidence(kind.value),
        )
        for kind in AuditKind
        if kind != AuditKind.HELD_OUT_EXECUTION
    ]
    evidence = candidate_evidence().model_copy(
        update={"human_reviews": reviews, "expert_runs": [expert], "audits": audits}
    )

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, ReleaseGate.INDEPENDENT_HUMAN_REVIEW) == GateStatus.PASSED
    assert gate_status(report, ReleaseGate.EXPERT_CALIBRATION) == GateStatus.PASSED
    assert gate_status(report, ReleaseGate.RELEASE_AUDITS) == GateStatus.PASSED
    assert report.corpus.independently_reviewed_task_count == 12
    assert report.corpus.expert_category_count == 6


@pytest.mark.parametrize(
    ("kind", "gate"),
    [
        ("review", ReleaseGate.INDEPENDENT_HUMAN_REVIEW),
        ("expert", ReleaseGate.EXPERT_CALIBRATION),
        ("audit-status", ReleaseGate.RELEASE_AUDITS),
        ("audit-independence", ReleaseGate.RELEASE_AUDITS),
    ],
)
def test_release_audit_marks_recorded_objections_as_failed(
    tmp_path: Path,
    kind: str,
    gate: ReleaseGate,
) -> None:
    task_set = load_model(ROOT / "datasets/slopbench-swe-v1-dev.json", TaskSetManifest)
    identity = task_identities(task_set)[0]
    updates: dict[str, object] = {}
    if kind == "review":
        updates["human_reviews"] = [
            HumanTaskReview(
                task=identity,
                reviewer="independent-reviewer",
                decision=ReviewDecision.CHANGES_REQUESTED,
                evidence=external_evidence(),
            )
        ]
    elif kind == "expert":
        updates["expert_runs"] = [
            ExpertCalibrationRun(
                expert="domain-expert",
                tasks=[identity],
                decision=ReviewDecision.CHANGES_REQUESTED,
                evidence=external_evidence(),
            )
        ]
    else:
        updates["audits"] = [
            ReleaseAudit(
                kind=AuditKind.CLASSIFICATION,
                auditor="independent-auditor",
                independent=kind != "audit-independence",
                status=(AuditStatus.FAILED if kind == "audit-status" else AuditStatus.PASSED),
                evidence=external_evidence(),
            )
        ]
    evidence = candidate_evidence().model_copy(update=updates)

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, gate) == GateStatus.FAILED


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("task-digest", "human review binds an unknown task"),
        ("owner-review", "human review is not independent"),
        ("expert-digest", "expert run binds an unknown task"),
        ("primary", "primary reference configuration"),
        ("comparison", "unknown configurations"),
    ],
)
def test_release_audit_rejects_unbound_evidence(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    task_set = load_model(ROOT / "datasets/slopbench-swe-v1-dev.json", TaskSetManifest)
    identity = task_identities(task_set)[0]
    evidence = candidate_evidence()
    if mutation == "task-digest":
        bad_identity = identity.model_copy(update={"task_digest": "a" * 64})
        evidence = evidence.model_copy(
            update={
                "human_reviews": [
                    HumanTaskReview(
                        task=bad_identity,
                        reviewer="reviewer",
                        decision=ReviewDecision.ACCEPTED,
                        evidence=external_evidence(),
                    )
                ]
            }
        )
    elif mutation == "owner-review":
        evidence = evidence.model_copy(
            update={
                "human_reviews": [
                    HumanTaskReview(
                        task=identity,
                        reviewer="uinaf",
                        decision=ReviewDecision.ACCEPTED,
                        evidence=external_evidence(),
                    )
                ]
            }
        )
    elif mutation == "expert-digest":
        bad_identity = identity.model_copy(update={"task_digest": "a" * 64})
        evidence = evidence.model_copy(
            update={
                "expert_runs": [
                    ExpertCalibrationRun(
                        expert="expert",
                        tasks=[bad_identity],
                        decision=ReviewDecision.ACCEPTED,
                        evidence=external_evidence(),
                    )
                ]
            }
        )
    elif mutation == "primary":
        evidence = evidence.model_copy(update={"primary_configuration_id": "missing"})
    else:
        placeholder = BoundDocument(path="missing.json", sha256="a" * 64)
        evidence = evidence.model_copy(
            update={
                "reference_comparisons": [
                    ReferenceComparisonRecord(
                        configuration_id="missing",
                        evaluation=placeholder,
                        result=placeholder.model_copy(update={"path": "missing-result.json"}),
                        attestation=placeholder.model_copy(
                            update={"path": "missing-attestation.json"}
                        ),
                        allowed_signers=placeholder.model_copy(
                            update={"path": "missing-allowed-signers"}
                        ),
                        verification=placeholder.model_copy(
                            update={"path": "missing-verification.json"}
                        ),
                    )
                ]
            }
        )

    with pytest.raises(ContractError, match=message):
        audit_release(write_evidence(tmp_path, evidence), ROOT)


def test_release_audit_rejects_bound_file_drift_and_wrong_tracer(tmp_path: Path) -> None:
    evidence = candidate_evidence()
    bad_digest = evidence.model_copy(
        update={"task_set": evidence.task_set.model_copy(update={"sha256": "a" * 64})}
    )
    with pytest.raises(ContractError, match="digest mismatch"):
        audit_release(write_evidence(tmp_path, bad_digest), ROOT)

    material_drift = evidence.model_copy(
        update={
            "public_materials": [
                bound.model_copy(update={"sha256": "a" * 64})
                if bound.path == "README.md"
                else bound
                for bound in evidence.public_materials
            ]
        }
    )
    with pytest.raises(ContractError, match="digest mismatch"):
        audit_release(write_evidence(tmp_path, material_drift), ROOT)

    wrong_file = evidence.model_copy(
        update={
            "tracer_task": BoundDocument(
                path="Makefile",
                sha256=sha256_file(ROOT / "Makefile"),
            )
        }
    )
    with pytest.raises(ContractError, match="must bind a SlopBench task contract"):
        audit_release(write_evidence(tmp_path, wrong_file), ROOT)

    task_path = "tasks/diagnosis/lease-expiry/slopbench-task.json"
    wrong_category = evidence.model_copy(
        update={
            "tracer_task": BoundDocument(
                path=task_path,
                sha256=sha256_file(ROOT / task_path),
            )
        }
    )
    with pytest.raises(ContractError, match="not the tracer task"):
        audit_release(write_evidence(tmp_path, wrong_category), ROOT)


def test_release_audit_rejects_duplicate_loaded_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_model
    first_profile = original(ROOT / "profiles/altay.json", ProfileDefinition)

    def duplicate_profiles(path: Path, model_type: type[Any]) -> Any:
        if model_type is ProfileDefinition:
            return first_profile
        return original(path, model_type)

    monkeypatch.setattr(calibration, "load_model", duplicate_profiles)
    with pytest.raises(ContractError, match="profile IDs must be unique"):
        audit_release(write_evidence(tmp_path, candidate_evidence()), ROOT)


def test_release_audit_rejects_duplicate_loaded_configuration_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_model
    first = original(
        ROOT / "reference-configurations/cursor-grok-4.6-medium.json",
        ReferenceConfiguration,
    )

    def duplicate_configurations(path: Path, model_type: type[Any]) -> Any:
        if model_type is ReferenceConfiguration:
            return first
        return original(path, model_type)

    monkeypatch.setattr(calibration, "load_model", duplicate_configurations)
    with pytest.raises(ContractError, match="configuration IDs must be unique"):
        audit_release(write_evidence(tmp_path, candidate_evidence()), ROOT)


def test_release_task_loader_rejects_task_set_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_set = one_task_set()
    task_path = ROOT / task_set.tasks[0].contract_path
    task, contract_sha256, _ = validate_task(task_path.parent)
    monkeypatch.setattr(
        calibration,
        "validate_task",
        lambda path: (task, contract_sha256, "a" * 64),
    )

    with pytest.raises(ContractError, match="release task digest mismatch"):
        calibration._task_contracts(task_set, ROOT)


def test_release_audit_requires_cursor_as_primary_and_valid_common_harness(
    tmp_path: Path,
) -> None:
    evidence = candidate_evidence().model_copy(
        update={
            "primary_configuration_id": "codex-terra-medium",
            "common_harness": CommonHarnessDecision(
                disposition=CommonHarnessDisposition.INCLUDED,
                configuration_id="missing-common",
                rationale="The candidate has not been bound.",
            ),
        }
    )

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, ReleaseGate.REFERENCE_CONFIGURATIONS) == GateStatus.FAILED
    assert gate_status(report, ReleaseGate.COMMON_HARNESS_DECISION) == GateStatus.FAILED


def test_release_audit_requires_every_public_material_binding(tmp_path: Path) -> None:
    evidence = candidate_evidence()
    evidence = evidence.model_copy(
        update={
            "public_materials": [
                bound for bound in evidence.public_materials if bound.path != "README.md"
            ]
        }
    )

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, ReleaseGate.PUBLIC_MATERIALS) == GateStatus.FAILED


def test_release_audit_requires_one_matched_profile_and_pair_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = candidate_evidence()
    placeholder = BoundDocument(path="comparison/file.json", sha256="a" * 64)

    def record(configuration_id: str, index: int) -> ReferenceComparisonRecord:
        return ReferenceComparisonRecord(
            configuration_id=configuration_id,
            evaluation=placeholder.model_copy(update={"path": f"comparison/{index}-eval.json"}),
            result=placeholder.model_copy(update={"path": f"comparison/{index}-result.json"}),
            attestation=placeholder.model_copy(
                update={"path": f"comparison/{index}-attestation.json"}
            ),
            allowed_signers=placeholder.model_copy(
                update={"path": f"comparison/{index}-allowed-signers"}
            ),
            verification=placeholder.model_copy(
                update={"path": f"comparison/{index}-verification.json"}
            ),
        )

    records = [
        record("cursor-grok-4.6-medium", 1),
        record("codex-terra-medium", 2),
    ]
    scoring_profile = profile()
    expected_profile = profile_binding(scoring_profile)
    monkeypatch.setattr(
        calibration,
        "_validate_reference_comparison",
        lambda *args: (expected_profile, [("slopbench/fixture/task", 1, 1)]),
    )
    report = audit_release(
        write_evidence(
            tmp_path, evidence.model_copy(update={"reference_comparisons": records[:1]})
        ),
        ROOT,
    )
    assert gate_status(report, ReleaseGate.REFERENCE_COMPARISONS) == GateStatus.PENDING

    calls = 0

    def mismatched(*args: object) -> tuple[VersionBinding, list[tuple[str, int, int]]]:
        nonlocal calls
        calls += 1
        return expected_profile, [("slopbench/fixture/task", 1, calls)]

    monkeypatch.setattr(calibration, "_validate_reference_comparison", mismatched)
    with pytest.raises(ContractError, match="matched task/trial seeds"):
        audit_release(
            write_evidence(
                tmp_path,
                evidence.model_copy(update={"reference_comparisons": records}),
            ),
            ROOT,
        )


def test_stable_release_evidence_cannot_retain_pending_gates(tmp_path: Path) -> None:
    evidence = candidate_evidence().model_copy(
        update={"stage": ReleaseStage.STABLE, "version": "1.0.0"}
    )

    with pytest.raises(ContractError, match="stable release is blocked"):
        audit_release(write_evidence(tmp_path, evidence), ROOT)


def test_active_held_out_evidence_requires_matching_disclosure_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held_out_set = one_task_set(visibility=TaskSetVisibility.HELD_OUT_ACTIVE)
    result = direct_result(held_out_set, profile())
    disclosure = build_held_out_disclosure(held_out_set, profile(), result)
    disclosure_path = tmp_path / "held-out-disclosure.json"
    write_model(disclosure_path, disclosure)
    relative = "private/held-out-disclosure.json"
    evidence = candidate_evidence().model_copy(
        update={
            "held_out": HeldOutEvidence(
                status=HeldOutStatus.ACTIVE_PRIVATE,
                task_set=task_set_binding(held_out_set),
                disclosure=BoundDocument(path=relative, sha256=sha256_file(disclosure_path)),
                audit=external_evidence("held-out"),
            ),
            "audits": [
                ReleaseAudit(
                    kind=AuditKind.HELD_OUT_EXECUTION,
                    auditor="held-out-auditor",
                    independent=True,
                    status=AuditStatus.PASSED,
                    evidence=external_evidence("held-out-audit"),
                )
            ],
        }
    )
    original = calibration._resolve_bound

    def resolve(root: Path, bound: BoundDocument) -> Path:
        if bound.path == relative:
            return disclosure_path
        return original(root, bound)

    monkeypatch.setattr(calibration, "_resolve_bound", resolve)

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, ReleaseGate.HELD_OUT) == GateStatus.PASSED


def test_active_held_out_evidence_rejects_disclosure_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held_out_set = one_task_set(visibility=TaskSetVisibility.HELD_OUT_ACTIVE)
    other_set = one_task_set(
        visibility=TaskSetVisibility.HELD_OUT_ACTIVE,
        version="0.2.0",
    )
    disclosure = build_held_out_disclosure(
        held_out_set,
        profile(),
        direct_result(held_out_set, profile()),
    )
    disclosure_path = tmp_path / "held-out-disclosure.json"
    write_model(disclosure_path, disclosure)
    relative = "private/held-out-disclosure.json"
    evidence = candidate_evidence().model_copy(
        update={
            "held_out": HeldOutEvidence(
                status=HeldOutStatus.ACTIVE_PRIVATE,
                task_set=task_set_binding(other_set),
                disclosure=BoundDocument(path=relative, sha256=sha256_file(disclosure_path)),
                audit=external_evidence("held-out"),
            )
        }
    )
    original = calibration._resolve_bound
    monkeypatch.setattr(
        calibration,
        "_resolve_bound",
        lambda root, bound: disclosure_path if bound.path == relative else original(root, bound),
    )

    with pytest.raises(ContractError, match="held-out disclosure does not match"):
        audit_release(write_evidence(tmp_path, evidence), ROOT)


def test_cross_version_claim_requires_a_bound_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_set = one_task_set(version="0.1.0")
    after_set = one_task_set(version="0.2.0")
    before = direct_result(before_set, profile())
    after = direct_result(after_set, profile())
    paths = {
        "private/before-task-set.json": tmp_path / "before-task-set.json",
        "private/after-task-set.json": tmp_path / "after-task-set.json",
        "private/before-result.json": tmp_path / "before-result.json",
        "private/after-result.json": tmp_path / "after-result.json",
        "private/bridge.json": tmp_path / "bridge.json",
    }
    write_model(paths["private/before-task-set.json"], before_set)
    write_model(paths["private/after-task-set.json"], after_set)
    write_model(paths["private/before-result.json"], before)
    write_model(paths["private/after-result.json"], after)
    bridge = build_bridge_report(
        before_set,
        after_set,
        before,
        after,
        sha256_file(paths["private/before-result.json"]),
        sha256_file(paths["private/after-result.json"]),
    )
    write_model(paths["private/bridge.json"], bridge)

    def bound(relative: str) -> BoundDocument:
        return BoundDocument(path=relative, sha256=sha256_file(paths[relative]))

    claim = CrossVersionClaim(
        claim_id="dev-bridge",
        before_task_set=bound("private/before-task-set.json"),
        after_task_set=bound("private/after-task-set.json"),
        before_result=bound("private/before-result.json"),
        after_result=bound("private/after-result.json"),
        bridge=bound("private/bridge.json"),
    )
    evidence = candidate_evidence().model_copy(update={"cross_version_claims": [claim]})
    original = calibration._resolve_bound
    monkeypatch.setattr(
        calibration,
        "_resolve_bound",
        lambda root, item: paths[item.path] if item.path in paths else original(root, item),
    )

    report = audit_release(write_evidence(tmp_path, evidence), ROOT)

    assert gate_status(report, ReleaseGate.CROSS_VERSION_DISCIPLINE) == GateStatus.PASSED

    changed = bridge.model_copy(update={"before_result_sha256": "a" * 64})
    write_model(paths["private/bridge.json"], changed)
    changed_claim = claim.model_copy(update={"bridge": bound("private/bridge.json")})
    with pytest.raises(ContractError, match="bridge does not recompute"):
        audit_release(
            write_evidence(
                tmp_path,
                evidence.model_copy(update={"cross_version_claims": [changed_claim]}),
            ),
            ROOT,
        )


def comparison_fixture(
    tmp_path: Path,
) -> tuple[
    ReferenceComparisonRecord,
    EvaluationManifest,
    EvaluationResult,
    TaskSetManifest,
    ReferenceVerification,
]:
    fixture = materialize_evaluation(
        tmp_path,
        purpose=EvaluationPurpose.COMPARISON,
        origin=ResultOrigin.MAINTAINER,
    )
    evaluation_path = fixture["evaluation_path"]
    result_path = fixture["result_path"]
    statement = build_attestation_statement(evaluation_path, result_path)
    attestation = ReferenceAttestation(
        schema_version="slopbench.attestation.v1",
        statement=statement,
        signature=SshSignature(
            signer="maintainer@example.test",
            signature_base64="c2lnbmF0dXJlLWZpeHR1cmU=",
        ),
    )
    attestation_path = tmp_path / "attestation.json"
    write_model(attestation_path, attestation)
    allowed_signers_path = tmp_path / "allowed_signers"
    allowed_signers_path.write_text("maintainer@example.test ssh-ed25519 AAAAfixture\n")
    verification = ReferenceVerification(
        schema_version="slopbench.reference-verification.v1",
        signer="maintainer@example.test",
        attestation_sha256=sha256_file(attestation_path),
        statement_sha256=digest("statement"),
    )
    verification_path = tmp_path / "verification.json"
    write_model(verification_path, verification)

    def bound(path: Path) -> BoundDocument:
        return BoundDocument(path=path.relative_to(tmp_path).as_posix(), sha256=sha256_file(path))

    evaluation = fixture["evaluation"]
    record = ReferenceComparisonRecord(
        configuration_id=evaluation.configuration.configuration_id,
        evaluation=bound(evaluation_path),
        result=bound(result_path),
        attestation=bound(attestation_path),
        allowed_signers=bound(allowed_signers_path),
        verification=bound(verification_path),
    )
    return record, evaluation, fixture["result"], fixture["task_set"], verification


def test_reference_comparison_binds_signed_result_and_pair_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record, evaluation, result, task_set, verification = comparison_fixture(tmp_path)
    monkeypatch.setattr(calibration, "verify_reference_attestation", lambda *args: verification)

    bound_profile, schedule = calibration._validate_reference_comparison(
        tmp_path,
        record,
        evaluation.configuration,
        task_set,
    )

    assert bound_profile == result.profile
    assert schedule == [(result.trials[0].task_id, index, index) for index in range(1, 6)]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("identity", "does not match"),
        ("pairing", "trial pairing"),
        ("binding", "binding does not match"),
        ("seed", "explicit trial seeds"),
        ("verification", "cryptographic verification"),
    ],
)
def test_reference_comparison_rejects_cross_document_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    record, evaluation, result, task_set, verification = comparison_fixture(tmp_path)
    returned_verification = verification
    if mutation == "identity":
        changed = evaluation.model_copy(update={"evaluation_id": "different-evaluation"})
        write_model(tmp_path / record.evaluation.path, changed)
        record = record.model_copy(
            update={
                "evaluation": record.evaluation.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.evaluation.path)}
                )
            }
        )
    elif mutation == "pairing":
        runs = [
            run.model_copy(update={"task_id": "slopbench/fixture/different"})
            for run in evaluation.runs
        ]
        changed = evaluation.model_copy(update={"runs": runs})
        write_model(tmp_path / record.evaluation.path, changed)
        changed_result = result.model_copy(
            update={"evaluation_manifest_sha256": sha256_file(tmp_path / record.evaluation.path)}
        )
        write_model(tmp_path / record.result.path, changed_result)
        record = record.model_copy(
            update={
                "evaluation": record.evaluation.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.evaluation.path)}
                ),
                "result": record.result.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.result.path)}
                ),
            }
        )
    elif mutation == "binding":
        runs = list(evaluation.runs)
        runs[0] = runs[0].model_copy(update={"result_sha256": "a" * 64})
        changed = evaluation.model_copy(update={"runs": runs})
        write_model(tmp_path / record.evaluation.path, changed)
        changed_result = result.model_copy(
            update={"evaluation_manifest_sha256": sha256_file(tmp_path / record.evaluation.path)}
        )
        write_model(tmp_path / record.result.path, changed_result)
        record = record.model_copy(
            update={
                "evaluation": record.evaluation.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.evaluation.path)}
                ),
                "result": record.result.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.result.path)}
                ),
            }
        )
    elif mutation == "seed":
        trials = list(result.trials)
        trials[0] = trials[0].model_copy(
            update={"trial": trials[0].trial.model_copy(update={"seed": None})}
        )
        changed_result = replace_result_trials(result, trials)
        write_model(tmp_path / record.result.path, changed_result)
        record = record.model_copy(
            update={
                "result": record.result.model_copy(
                    update={"sha256": sha256_file(tmp_path / record.result.path)}
                )
            }
        )
    else:
        returned_verification = verification.model_copy(update={"signer": "someone-else"})
    monkeypatch.setattr(
        calibration,
        "verify_reference_attestation",
        lambda *args: returned_verification,
    )

    with pytest.raises(ContractError, match=message):
        calibration._validate_reference_comparison(
            tmp_path,
            record,
            evaluation.configuration,
            task_set,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("provisional-version", "provisional releases require"),
        ("stable-version", "stable SWE v1 releases require"),
        ("paths", "document paths must be unique"),
        ("reviews", "reviewer pairs must be unique"),
        ("audits", "audit kinds must be unique"),
        ("comparisons", "configuration IDs must be unique"),
        ("claims", "claim IDs must be unique"),
    ],
)
def test_release_evidence_contract_rejects_ambiguous_records(
    mutation: str,
    message: str,
) -> None:
    payload = candidate_evidence().model_dump(mode="json")
    identity = {
        "task_id": "slopbench/fixture/task",
        "task_digest": "a" * 64,
    }
    review = {
        "task": identity,
        "reviewer": "reviewer",
        "decision": "accepted",
        "evidence": external_evidence().model_dump(mode="json"),
    }
    placeholder = {"path": "comparison/file.json", "sha256": "a" * 64}
    comparison = {
        "configuration_id": "configuration",
        "evaluation": placeholder,
        "result": {**placeholder, "path": "comparison/result.json"},
        "attestation": {**placeholder, "path": "comparison/attestation.json"},
        "allowed_signers": {**placeholder, "path": "comparison/allowed_signers"},
        "verification": {**placeholder, "path": "comparison/verification.json"},
    }
    if mutation == "provisional-version":
        payload["version"] = "1.0.0"
    elif mutation == "stable-version":
        payload.update(stage="stable", version="0.1.0")
    elif mutation == "paths":
        payload["profiles"] = [payload["profiles"][0], payload["profiles"][0]]
    elif mutation == "reviews":
        payload["human_reviews"] = [review, review]
    elif mutation == "audits":
        audit = {
            "kind": "classification",
            "auditor": "auditor",
            "independent": True,
            "status": "passed",
            "evidence": external_evidence().model_dump(mode="json"),
        }
        payload["audits"] = [audit, audit]
    elif mutation == "comparisons":
        payload["reference_comparisons"] = [comparison, comparison]
    else:
        claim = {
            "claim_id": "claim",
            "before_task_set": {**placeholder, "path": "claim/before-task-set.json"},
            "after_task_set": {**placeholder, "path": "claim/after-task-set.json"},
            "before_result": {**placeholder, "path": "claim/before-result.json"},
            "after_result": {**placeholder, "path": "claim/after-result.json"},
            "bridge": {**placeholder, "path": "claim/bridge.json"},
        }
        payload["cross_version_claims"] = [claim, claim]

    with pytest.raises(ValidationError, match=message):
        ReleaseEvidenceManifest.model_validate_json(json.dumps(payload))


def test_nested_release_evidence_contracts_reject_partial_or_duplicate_state() -> None:
    identity = TaskEvidenceIdentity(
        task_id="slopbench/fixture/task",
        task_digest="a" * 64,
    )
    with pytest.raises(ValidationError, match="task IDs must be unique"):
        ExpertCalibrationRun(
            expert="expert",
            tasks=[identity, identity],
            decision=ReviewDecision.ACCEPTED,
            evidence=external_evidence(),
        )
    with pytest.raises(ValidationError, match="requires exactly one configuration"):
        CommonHarnessDecision(
            disposition=CommonHarnessDisposition.INCLUDED,
            rationale="Missing binding.",
        )
    with pytest.raises(ValidationError, match="requires exactly one configuration"):
        CommonHarnessDecision(
            disposition=CommonHarnessDisposition.OMITTED_UNSTABLE,
            configuration_id="unexpected",
            rationale="Contradictory binding.",
        )
    with pytest.raises(ValidationError, match="requires binding, disclosure, and audit"):
        HeldOutEvidence(status=HeldOutStatus.ACTIVE_PRIVATE)
    with pytest.raises(ValidationError, match="cannot expose partial metadata"):
        HeldOutEvidence(
            status=HeldOutStatus.NOT_AVAILABLE,
            audit=external_evidence(),
        )
    duplicate = BoundDocument(path="duplicate.json", sha256="a" * 64)
    with pytest.raises(ValidationError, match="comparison paths must be unique"):
        ReferenceComparisonRecord(
            configuration_id="configuration",
            evaluation=duplicate,
            result=duplicate,
            attestation=duplicate,
            allowed_signers=duplicate,
            verification=duplicate,
        )
    with pytest.raises(ValidationError, match="claim paths must be unique"):
        CrossVersionClaim(
            claim_id="claim",
            before_task_set=duplicate,
            after_task_set=duplicate,
            before_result=duplicate,
            after_result=duplicate,
            bridge=duplicate,
        )


@pytest.mark.parametrize("mutation", ["gate-order", "blockers", "eligibility", "stable"])
def test_readiness_contract_rejects_internal_drift(mutation: str) -> None:
    payload = load_model(
        ROOT / "release/slopbench-swe-v1-dev-readiness.json",
        ReleaseReadinessReport,
    ).model_dump(mode="json")
    if mutation == "gate-order":
        payload["gates"] = list(reversed(payload["gates"]))
    elif mutation == "blockers":
        payload["blockers"] = []
    elif mutation == "eligibility":
        payload["stable_eligible"] = True
    else:
        payload.update(stage="stable", version="1.0.0")

    with pytest.raises(ValidationError):
        ReleaseReadinessReport.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "kind": "reliability",
                "task_id": "slopbench/fixture/task",
                "task_digest": "a" * 64,
                "failed_pair_indices": [1],
                "critical_gate": "authority",
            },
            "critical regression flags require",
        ),
        (
            {
                "kind": "critical_gate",
                "task_id": "slopbench/fixture/task",
                "task_digest": "a" * 64,
                "failed_pair_indices": [1, 1],
                "critical_gate": "authority",
            },
            "pair indices must be unique",
        ),
        (
            {
                "kind": "reliability",
                "task_id": "slopbench/fixture/task",
                "task_digest": "a" * 64,
                "failed_pair_indices": [6],
                "critical_gate": None,
            },
            "between one and five",
        ),
    ],
)
def test_regression_flag_rejects_inconsistent_kind(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RegressionFlag.model_validate_json(json.dumps(payload))


def result_with_authority_failure(result: EvaluationResult) -> EvaluationResult:
    trials = list(result.trials)
    first = trials[0]
    outcomes = [
        outcome.model_copy(
            update={"status": OutcomeStatus.FAILED} if outcome.gate == GateName.AUTHORITY else {}
        )
        for outcome in first.outcomes
    ]
    trials[0] = first.model_copy(
        update={
            "classification": FailureClassification.VALID_AGENT_FAILURE,
            "failure_reason": FailureReason.GATE_FAILURE,
            "outcomes": outcomes,
            "strict_gate_failures": [GateName.AUTHORITY],
        }
    )
    vector = RawResultVector(trials=trials)
    return result.model_copy(
        update={
            "trials": trials,
            "result_vector_sha256": contract_digest("slopbench.result-vector.v1", vector),
            "metrics": _aggregate(trials, result.profile_definition),
        }
    )


def replace_result_trials(
    result: EvaluationResult,
    trials: list[RawTrialOutcome],
) -> EvaluationResult:
    vector = RawResultVector(trials=trials)
    return EvaluationResult.model_validate_json(
        result.model_copy(
            update={
                "trials": trials,
                "result_vector_sha256": contract_digest("slopbench.result-vector.v1", vector),
                "metrics": _aggregate(trials, result.profile_definition),
            }
        ).model_dump_json()
    )


def test_regression_report_flags_reliability_and_first_critical_failure() -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    before = direct_result(task_set, scoring_profile)
    failed = direct_result(task_set, scoring_profile, fail_requested=True)
    authority = result_with_authority_failure(before)

    reliability = build_regression_report(before, failed, digest("before"), digest("after"))
    critical = build_regression_report(before, authority, digest("before"), digest("authority"))

    assert reliability.flags == [
        RegressionFlag(
            kind=RegressionKind.RELIABILITY,
            task_id=before.trials[0].task_id,
            task_digest=before.trials[0].task_digest,
            failed_pair_indices=[1, 2, 3, 4, 5],
        )
    ]
    assert critical.flags == [
        RegressionFlag(
            kind=RegressionKind.CRITICAL_GATE,
            task_id=before.trials[0].task_id,
            task_digest=before.trials[0].task_digest,
            failed_pair_indices=[1],
            critical_gate=GateName.AUTHORITY,
        )
    ]
    assert reliability.automatic_release_blocking is False


def test_regression_report_ignores_existing_failures_and_rejects_unmatched_results() -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    failing = direct_result(task_set, scoring_profile, fail_requested=True)
    clean = build_regression_report(failing, failing, digest("before"), digest("after"))

    assert clean.flags == []
    smoke = direct_result(task_set, scoring_profile, purpose=EvaluationPurpose.SMOKE)
    with pytest.raises(ContractError, match="five-trial comparison"):
        build_regression_report(smoke, smoke, digest("before"), digest("after"))
    different_profile = direct_result(task_set, profile("altay"))
    with pytest.raises(ContractError, match="identical task set, profile, and configuration"):
        build_regression_report(failing, different_profile, digest("before"), digest("after"))

    changed_trials = list(failing.trials)
    changed_trials[0] = changed_trials[0].model_copy(
        update={"trial": changed_trials[0].trial.model_copy(update={"seed": 99})}
    )
    changed_seed = replace_result_trials(failing, changed_trials)
    with pytest.raises(ContractError, match="matched task/pair trial seeds"):
        build_regression_report(failing, changed_seed, digest("before"), digest("after"))

    unseeded_trials = [
        trial.model_copy(update={"trial": trial.trial.model_copy(update={"seed": None})})
        for trial in failing.trials
    ]
    unseeded = replace_result_trials(failing, unseeded_trials)
    with pytest.raises(ContractError, match="explicit trial seeds"):
        build_regression_report(unseeded, unseeded, digest("before"), digest("after"))


def test_regression_report_ignores_non_attributable_critical_gate_failures() -> None:
    task_set = one_task_set()
    scoring_profile = profile()
    before = direct_result(task_set, scoring_profile)
    authority = result_with_authority_failure(before)
    trials = list(authority.trials)
    trials[0] = trials[0].model_copy(
        update={
            "classification": FailureClassification.BENCHMARK_DEFECT,
            "failure_reason": FailureReason.HARBOR_RESULT_INVALID,
        }
    )
    benchmark_defect = replace_result_trials(authority, trials)

    report = build_regression_report(
        before,
        benchmark_defect,
        digest("before"),
        digest("benchmark-defect"),
    )

    assert report.flags == []


def test_bound_release_file_rejects_digest_drift_and_symlinks(tmp_path: Path) -> None:
    regular = tmp_path / "regular.json"
    regular.write_text("{}\n")
    bound = BoundDocument(path="regular.json", sha256=sha256_file(regular))
    assert calibration._resolve_bound(tmp_path, bound) == regular

    with pytest.raises(ContractError, match="digest mismatch"):
        calibration._resolve_bound(
            tmp_path,
            bound.model_copy(update={"sha256": "a" * 64}),
        )
    link = tmp_path / "link.json"
    link.symlink_to(regular)
    with pytest.raises(ContractError, match="not a regular project file"):
        calibration._resolve_bound(
            tmp_path,
            BoundDocument(path="link.json", sha256=sha256_file(regular)),
        )


def test_task_set_entry_digest_rejects_unknown_task() -> None:
    with pytest.raises(ContractError, match="does not contain"):
        calibration.task_set_entry_digest(one_task_set(), "slopbench/missing/task")
