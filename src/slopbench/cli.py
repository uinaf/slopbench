"""Command-line interface for SlopBench contracts and runs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from slopbench.contracts import (
    AgentReport,
    FailureClassification,
    ResultBundle,
    ReviewSubmission,
    RunManifest,
    TaskContract,
    VerificationEvidence,
)
from slopbench.hashing import (
    ContractError,
    load_model,
    seal_task,
    sha256_file,
    validate_task,
    write_model,
)
from slopbench.release import (
    AttestationStatement,
    BridgeReport,
    EvaluationManifest,
    EvaluationResult,
    HeldOutDisclosure,
    ProfileDefinition,
    ReferenceAttestation,
    ReferenceVerification,
    RetirementManifest,
    TaskSetManifest,
    build_attestation_statement,
    build_bridge_report,
    build_held_out_disclosure,
    compute_evaluation,
    sign_reference_attestation,
    validate_retirement,
    validate_task_set,
    verify_reference_attestation,
)
from slopbench.runner import RunError, execute_run

_SCHEMAS: dict[str, type[BaseModel]] = {
    "attestation": ReferenceAttestation,
    "attestation-statement": AttestationStatement,
    "bridge": BridgeReport,
    "disclosure": HeldOutDisclosure,
    "evaluation": EvaluationManifest,
    "evaluation-result": EvaluationResult,
    "profile": ProfileDefinition,
    "reference-verification": ReferenceVerification,
    "report": AgentReport,
    "retirement": RetirementManifest,
    "review": ReviewSubmission,
    "result": ResultBundle,
    "run": RunManifest,
    "task": TaskContract,
    "task-set": TaskSetManifest,
    "verification": VerificationEvidence,
}


def _path(value: str) -> Path:
    return Path(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slopbench")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a versioned JSON document")
    validate.add_argument("kind", choices=sorted(_SCHEMAS))
    validate.add_argument("path", type=_path)

    task = commands.add_parser("task", help="seal or verify a task contract")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    for name in ("seal", "check"):
        command = task_commands.add_parser(name)
        command.add_argument("task_dir", type=_path)

    schema = commands.add_parser("schema", help="export JSON schemas")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    export = schema_commands.add_parser("export")
    export.add_argument("output_dir", type=_path)

    run = commands.add_parser("run", help="execute one pinned Harbor trial")
    run.add_argument("--task", dest="task_dir", required=True, type=_path)
    run.add_argument("--manifest", required=True, type=_path)
    run.add_argument("--output", required=True, type=_path)

    task_set = commands.add_parser("task-set", help="verify a versioned task-set manifest")
    task_set.add_argument("manifest", type=_path)
    task_set.add_argument("--root", default=Path("."), type=_path)

    evaluate = commands.add_parser("evaluate", help="compute a deterministic suite result")
    evaluate.add_argument("--manifest", required=True, type=_path)
    evaluate.add_argument("--task-set", required=True, type=_path)
    evaluate.add_argument("--profile", required=True, type=_path)
    evaluate.add_argument("--project-root", default=Path("."), type=_path)
    evaluate.add_argument("--bundle-root", default=Path("."), type=_path)
    evaluate.add_argument("--origin", choices=["external", "maintainer"], default="external")
    evaluate.add_argument("--output", required=True, type=_path)

    disclose = commands.add_parser(
        "disclose", help="write a whitelist-only active held-out disclosure"
    )
    disclose.add_argument("--task-set", required=True, type=_path)
    disclose.add_argument("--profile", required=True, type=_path)
    disclose.add_argument("--result", required=True, type=_path)
    disclose.add_argument("--project-root", default=Path("."), type=_path)
    disclose.add_argument("--output", required=True, type=_path)

    bridge = commands.add_parser("bridge", help="write an old/new five-trial bridge report")
    bridge.add_argument("--before-task-set", required=True, type=_path)
    bridge.add_argument("--after-task-set", required=True, type=_path)
    bridge.add_argument("--before-result", required=True, type=_path)
    bridge.add_argument("--after-result", required=True, type=_path)
    bridge.add_argument("--project-root", default=Path("."), type=_path)
    bridge.add_argument("--output", required=True, type=_path)

    retirement = commands.add_parser("retirement", help="verify retirement and bridge inputs")
    retirement.add_argument("--manifest", required=True, type=_path)
    retirement.add_argument("--bridge", required=True, type=_path)
    retirement.add_argument("--before-task-set", required=True, type=_path)
    retirement.add_argument("--after-task-set", required=True, type=_path)
    retirement.add_argument("--project-root", default=Path("."), type=_path)

    attestation = commands.add_parser(
        "attestation", help="create or verify maintainer reference statements"
    )
    attestation_commands = attestation.add_subparsers(dest="attestation_command", required=True)
    statement = attestation_commands.add_parser("statement")
    statement.add_argument("--evaluation", required=True, type=_path)
    statement.add_argument("--result", required=True, type=_path)
    statement.add_argument("--output", required=True, type=_path)
    sign = attestation_commands.add_parser("sign")
    sign.add_argument("--evaluation", required=True, type=_path)
    sign.add_argument("--result", required=True, type=_path)
    sign.add_argument("--identity", required=True, type=_path)
    sign.add_argument("--signer", required=True)
    sign.add_argument("--output", required=True, type=_path)
    verify = attestation_commands.add_parser("verify")
    verify.add_argument("--attestation", required=True, type=_path)
    verify.add_argument("--allowed-signers", required=True, type=_path)
    verify.add_argument("--evaluation", required=True, type=_path)
    verify.add_argument("--result", required=True, type=_path)
    verify.add_argument("--output", required=True, type=_path)
    return parser


def _validate(kind: str, path: Path) -> None:
    load_model(path, _SCHEMAS[kind])


def _export_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, model in sorted(_SCHEMAS.items()):
        path = output_dir / f"slopbench-{name}.schema.json"
        rendered = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path.write_text(rendered)


def _run(args: argparse.Namespace) -> int:
    result, bundle_dir = execute_run(args.task_dir, args.manifest, args.output)
    print(bundle_dir / "result.json")
    if result.classification == FailureClassification.VALID_PASS:
        return 0
    if result.classification == FailureClassification.VALID_AGENT_FAILURE:
        return 1
    return 2


def _evaluate(args: argparse.Namespace) -> None:
    from slopbench.release import ResultOrigin

    result = compute_evaluation(
        args.manifest,
        args.task_set,
        args.profile,
        args.project_root,
        args.bundle_root,
        result_origin=ResultOrigin(args.origin),
    )
    write_model(args.output, result)


def _disclose(args: argparse.Namespace) -> None:
    task_set, _ = validate_task_set(args.task_set, args.project_root)
    profile = load_model(args.profile, ProfileDefinition)
    result = load_model(args.result, EvaluationResult)
    write_model(args.output, build_held_out_disclosure(task_set, profile, result))


def _bridge(args: argparse.Namespace) -> None:
    before, _ = validate_task_set(args.before_task_set, args.project_root)
    after, _ = validate_task_set(args.after_task_set, args.project_root)
    before_result = load_model(args.before_result, EvaluationResult)
    after_result = load_model(args.after_result, EvaluationResult)
    report = build_bridge_report(
        before,
        after,
        before_result,
        after_result,
        sha256_file(args.before_result),
        sha256_file(args.after_result),
    )
    write_model(args.output, report)


def _attestation(args: argparse.Namespace) -> None:
    if args.attestation_command == "statement":
        write_model(args.output, build_attestation_statement(args.evaluation, args.result))
        return
    if args.attestation_command == "sign":
        attestation = sign_reference_attestation(
            args.evaluation,
            args.result,
            args.identity,
            args.signer,
        )
        write_model(args.output, attestation)
        return
    verification: ReferenceVerification = verify_reference_attestation(
        args.attestation,
        args.allowed_signers,
        args.evaluation,
        args.result,
    )
    write_model(args.output, verification)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            _validate(args.kind, args.path)
        elif args.command == "task":
            if args.task_command == "seal":
                seal_task(args.task_dir)
            else:
                validate_task(args.task_dir)
        elif args.command == "schema":
            _export_schemas(args.output_dir)
        elif args.command == "run":
            return _run(args)
        elif args.command == "task-set":
            validate_task_set(args.manifest, args.root)
        elif args.command == "evaluate":
            _evaluate(args)
        elif args.command == "disclose":
            _disclose(args)
        elif args.command == "bridge":
            _bridge(args)
        elif args.command == "retirement":
            validate_retirement(
                args.manifest,
                args.bridge,
                args.before_task_set,
                args.after_task_set,
                args.project_root,
            )
        elif args.command == "attestation":
            _attestation(args)
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (ContractError, RunError, OSError) as exc:
        print(f"slopbench: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
