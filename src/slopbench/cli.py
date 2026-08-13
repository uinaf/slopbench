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
    RunManifest,
    TaskContract,
    VerificationEvidence,
)
from slopbench.hashing import ContractError, load_model, seal_task, validate_task
from slopbench.runner import RunError, execute_run

_SCHEMAS: dict[str, type[BaseModel]] = {
    "report": AgentReport,
    "result": ResultBundle,
    "run": RunManifest,
    "task": TaskContract,
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
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (ContractError, RunError, OSError) as exc:
        print(f"slopbench: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
