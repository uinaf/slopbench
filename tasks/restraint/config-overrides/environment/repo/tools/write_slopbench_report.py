from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from revision import worktree_revision

ROOT = Path(__file__).resolve().parents[1]
META = json.loads((ROOT / "tools" / "task_meta.json").read_text())


def run(command: str) -> int:
    return subprocess.run(command, cwd=ROOT, shell=True, check=False).returncode


def status(passed: bool) -> str:
    return "passed" if passed else "failed"


def git_paths(*args: str) -> set[str]:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return set(completed.stdout.splitlines())


def main() -> int:
    task_digest = os.environ.get("SLOPBENCH_TASK_DIGEST", "")
    if re.fullmatch(r"[0-9a-f]{64}", task_digest) is None:
        raise SystemExit("SLOPBENCH_TASK_DIGEST is missing or malformed")
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    public_command = META["public_command"]
    build_command = META["build_command"]
    public_exit = run(public_command)
    build_exit = run(build_command)
    changed = git_paths("diff", "--name-only", "HEAD")
    untracked = git_paths("ls-files", "--others") - {"slopbench-report.json"}
    authority_passed = changed <= set(META["allowed_paths"]) and not untracked
    claims = [
        {
            "gate": "requested_behavior",
            "status": "uncertain",
            "evidence_ids": ["requested-contract"],
        },
        {
            "gate": "regressions",
            "status": status(public_exit == 0),
            "evidence_ids": ["public-regressions"],
        },
        {
            "gate": "build_and_types",
            "status": status(build_exit == 0),
            "evidence_ids": ["build-and-types"],
        },
        {
            "gate": "authority",
            "status": status(authority_passed),
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
    ]
    if META.get("forbid_type_escapes"):
        claims.insert(
            -1,
            {
                "gate": "safety_type_escapes",
                "status": "uncertain",
                "evidence_ids": ["safety-type-escapes"],
            },
        )
    report = {
        "schema_version": "slopbench.report.v1",
        "task_digest": task_digest,
        "base_revision": base_revision,
        "claims": claims,
        "commands": [
            {
                "id": "public-regressions",
                "command": public_command,
                "exit_code": public_exit,
            },
            {
                "id": "build-and-types",
                "command": build_command,
                "exit_code": build_exit,
            },
        ],
        "uncertainty": [
            {
                "code": "hidden-verifier",
                "detail": "Requested behavior and verifier integrity are checked after the agent phase.",
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
