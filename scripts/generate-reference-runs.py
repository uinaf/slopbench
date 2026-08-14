from __future__ import annotations

import argparse
from pathlib import Path

from slopbench.hashing import load_model
from slopbench.reference import write_reference_runs
from slopbench.release import EvaluationPurpose, ReferenceConfiguration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", required=True, type=Path)
    parser.add_argument(
        "--purpose",
        choices=[purpose.value for purpose in EvaluationPurpose],
        required=True,
    )
    parser.add_argument("--project-root", default=Path("."), type=Path)
    parser.add_argument("--environment-provider-version", required=True)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("task_dir", nargs="+", type=Path)
    args = parser.parse_args()

    configuration = load_model(args.configuration, ReferenceConfiguration)
    paths = write_reference_runs(
        args.task_dir,
        args.project_root,
        configuration,
        EvaluationPurpose(args.purpose),
        args.output_dir,
        environment_provider_version=args.environment_provider_version,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
