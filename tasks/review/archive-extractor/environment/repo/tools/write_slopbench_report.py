from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from revision import worktree_revision

ROOT = Path(__file__).resolve().parents[1]
META = json.loads((ROOT / "tools" / "task_meta.json").read_text())


def git_paths(*args: str) -> set[str]:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return set(completed.stdout.splitlines())


def main() -> int:
    task_digest = os.environ.get("SLOPBENCH_TASK_DIGEST", "")
    if re.fullmatch(r"[0-9a-f]{64}", task_digest) is None:
        raise SystemExit("SLOPBENCH_TASK_DIGEST is missing or malformed")
    review_path = ROOT / META["submission_path"]
    if review_path.is_symlink() or not review_path.is_file():
        raise SystemExit("slopbench-review.json is missing or not a regular file")
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    changed = git_paths("diff", "--name-only", "HEAD")
    untracked = git_paths("ls-files", "--others") - {"slopbench-report.json"}
    authority_passed = not changed and untracked <= set(META["allowed_untracked"])
    report = {
        "schema_version": "slopbench.report.v1",
        "task_digest": task_digest,
        "base_revision": base_revision,
        "claims": [
            {
                "gate": "requested_behavior",
                "status": "uncertain",
                "evidence_ids": ["review-score"],
            },
            {
                "gate": "authority",
                "status": "passed" if authority_passed else "failed",
                "evidence_ids": ["authority-scope"],
            },
            {
                "gate": "verifier_integrity",
                "status": "uncertain",
                "evidence_ids": ["verifier-integrity"],
            },
            {
                "gate": "evidence_receipt",
                "status": "passed",
                "evidence_ids": ["evidence-receipt"],
            },
        ],
        "commands": [],
        "uncertainty": [
            {
                "code": "hidden-adjudication",
                "detail": "Recall, precision, and verifier integrity are checked after review submission.",
            }
        ],
        "final_revision": worktree_revision(ROOT),
    }
    (ROOT / "slopbench-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
