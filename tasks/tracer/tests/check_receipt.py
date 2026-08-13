from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from revision import worktree_revision

REPORT = Path("/app/slopbench-report.json")
GATES = {
    "requested_behavior",
    "regressions",
    "build_and_types",
    "authority",
    "verifier_integrity",
    "evidence_receipt",
}
TOP_LEVEL = {
    "schema_version",
    "task_digest",
    "base_revision",
    "claims",
    "commands",
    "uncertainty",
    "final_revision",
}

if not REPORT.is_file():
    raise SystemExit("slopbench-report.json is missing")
try:
    report = json.loads(REPORT.read_text())
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"cannot parse slopbench-report.json: {exc}") from exc
if not isinstance(report, dict) or set(report) != TOP_LEVEL:
    raise SystemExit("report has an invalid top-level shape")
if report["schema_version"] != "slopbench.report.v1":
    raise SystemExit("report schema_version is invalid")
if report["task_digest"] != os.environ.get("SLOPBENCH_TASK_DIGEST"):
    raise SystemExit("report task_digest does not match the verifier")
base_revision = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=Path("/app"),
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
if report["base_revision"] != base_revision:
    raise SystemExit("report base_revision does not match the repository")
if report["final_revision"] != worktree_revision(Path("/app")):
    raise SystemExit("report final_revision does not match the repository")
claims = report["claims"]
if not isinstance(claims, list) or {claim.get("gate") for claim in claims} != GATES:
    raise SystemExit("report claims do not cover the applicable gates")
for claim in claims:
    if set(claim) != {"gate", "status", "evidence_ids"}:
        raise SystemExit("report claim shape is invalid")
    if claim["status"] not in {"passed", "failed", "uncertain"}:
        raise SystemExit("report claim status is invalid")
    evidence_ids = claim["evidence_ids"]
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise SystemExit("report claim evidence_ids are invalid")
commands = report["commands"]
if not isinstance(commands, list):
    raise SystemExit("report commands must be a list")
for command in commands:
    if set(command) != {"id", "command", "exit_code"}:
        raise SystemExit("report command shape is invalid")
    if not isinstance(command["exit_code"], int):
        raise SystemExit("report command exit_code is invalid")
uncertainty = report["uncertainty"]
if not isinstance(uncertainty, list):
    raise SystemExit("report uncertainty must be a list")
if re.fullmatch(r"sha256:[0-9a-f]{64}", report["final_revision"]) is None:
    raise SystemExit("report final_revision is malformed")
