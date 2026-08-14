from __future__ import annotations

import argparse
from pathlib import Path

from slopbench.hashing import load_model, write_model
from slopbench.reference import build_reference_evaluation
from slopbench.release import (
    EvaluationPurpose,
    ProfileDefinition,
    ReferenceConfiguration,
    TaskSetManifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", required=True, type=Path)
    parser.add_argument("--task-set", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--purpose",
        choices=[purpose.value for purpose in EvaluationPurpose],
        required=True,
    )
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--manifest-dir", required=True, type=Path)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    evaluation = build_reference_evaluation(
        args.manifest_dir,
        args.result_dir,
        args.bundle_root,
        load_model(args.configuration, ReferenceConfiguration),
        load_model(args.task_set, TaskSetManifest),
        load_model(args.profile, ProfileDefinition),
        EvaluationPurpose(args.purpose),
        args.evaluation_id,
    )
    write_model(args.output, evaluation)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
