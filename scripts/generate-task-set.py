from __future__ import annotations

import argparse
from pathlib import Path

from slopbench.hashing import validate_task, write_model
from slopbench.release import TaskSetEntry, TaskSetManifest, TaskSetVisibility

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-set-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--visibility",
        choices=[visibility.value for visibility in TaskSetVisibility],
        required=True,
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("task_dir", nargs="+", type=Path)
    args = parser.parse_args()

    entries = []
    for task_dir in sorted((path.resolve() for path in args.task_dir), key=lambda path: str(path)):
        task, _, task_digest = validate_task(task_dir)
        contract_path = task_dir / "slopbench-task.json"
        entries.append(
            TaskSetEntry(
                task_id=task.task_id,
                task_version=task.version,
                task_digest=task_digest,
                contract_path=contract_path.relative_to(ROOT).as_posix(),
                category=task.design.category,
                kind=task.kind,
                capabilities=task.capabilities,
                applicable_gates=task.applicable_gates,
                provenance=task.provenance,
                license=task.license,
            )
        )
    manifest = TaskSetManifest(
        task_set_id=args.task_set_id,
        version=args.version,
        visibility=TaskSetVisibility(args.visibility),
        tasks=entries,
    )
    write_model(args.output, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
