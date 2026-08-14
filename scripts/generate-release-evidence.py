from __future__ import annotations

import argparse
from pathlib import Path

from slopbench.calibration import (
    RELEASE_EVIDENCE_SCHEMA_VERSION,
    BoundDocument,
    CommonHarnessDecision,
    CommonHarnessDisposition,
    HeldOutEvidence,
    HeldOutStatus,
    ReleaseEvidenceManifest,
    ReleaseStage,
)
from slopbench.hashing import sha256_file, write_model


def bind(project_root: Path, relative: str) -> BoundDocument:
    return BoundDocument(path=relative, sha256=sha256_file(project_root / relative))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=Path("."), type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    public_material_paths = [
        "LICENSE",
        "README.md",
        *[f"docs/{path.name}" for path in sorted((root / "docs").glob("*.md"))],
        *[f"schemas/{path.name}" for path in sorted((root / "schemas").glob("*.json"))],
    ]

    manifest = ReleaseEvidenceManifest(
        schema_version=RELEASE_EVIDENCE_SCHEMA_VERSION,
        release_id="slopbench-swe-v1-dev",
        version="0.1.1",
        stage=ReleaseStage.PROVISIONAL,
        task_set=bind(root, "datasets/slopbench-swe-v1-dev.json"),
        tracer_task=bind(root, "tasks/tracer/slopbench-task.json"),
        profiles=[
            bind(root, f"profiles/{path.name}")
            for path in sorted((root / "profiles").glob("*.json"))
        ],
        reference_configurations=[
            bind(root, f"reference-configurations/{path.name}")
            for path in sorted((root / "reference-configurations").glob("*.json"))
        ],
        public_materials=[bind(root, path) for path in public_material_paths],
        primary_configuration_id="cursor-grok-4.6-medium",
        common_harness=CommonHarnessDecision(
            disposition=CommonHarnessDisposition.OMITTED_UNSTABLE,
            rationale=(
                "No minimal common or open harness has yet passed the same pinned installation, "
                "evidence, and five-trial reproducibility gates."
            ),
        ),
        human_reviews=[],
        expert_runs=[],
        audits=[],
        reference_comparisons=[],
        held_out=HeldOutEvidence(status=HeldOutStatus.NOT_AVAILABLE),
        cross_version_claims=[],
    )
    write_model(args.output, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
