from __future__ import annotations

import os
import re
import stat
from pathlib import Path

EXPECTED = {
    "check_authority.py",
    "check_integrity.py",
    "check_receipt.py",
    "check_requested.py",
    "check_write_boundary.py",
    "revision.py",
    "test.sh",
    "verify.py",
}

task_digest = os.environ.get("SLOPBENCH_TASK_DIGEST", "")
if re.fullmatch(r"[0-9a-f]{64}", task_digest) is None:
    raise SystemExit("SLOPBENCH_TASK_DIGEST is missing or malformed")
if os.environ.get("SLOPBENCH_VERIFIER_ISOLATION") != "separate":
    raise SystemExit("verifier is not running in declared separate isolation")
actual = {path.name for path in Path("/tests").iterdir() if path.is_file()}
if actual != EXPECTED:
    raise SystemExit(f"verifier file set mismatch: {sorted(actual)}")
published_logs = Path("/logs/verifier")
if any(published_logs.iterdir()):
    raise SystemExit("published verifier logs changed during trusted verification")
protected = [
    Path("/app"),
    *Path("/app").rglob("*"),
    Path("/tests"),
    *Path("/tests").rglob("*"),
    Path("/trusted-logs"),
    *Path("/trusted-logs").rglob("*"),
]
for path in protected:
    metadata = path.lstat()
    if path.is_symlink():
        raise SystemExit(f"trusted verifier path must not be a symlink: {path}")
    if metadata.st_uid != 0:
        raise SystemExit(f"trusted verifier path must be owned by root: {path}")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SystemExit(f"trusted verifier path must not be writable by other users: {path}")
