from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

from revision import worktree_revision

ROOT = Path("/app")
LOGS = Path("/trusted-logs")
PUBLISHED_LOGS = Path("/logs/verifier")
META = json.loads((Path("/baseline") / "tools" / "task_meta.json").read_text())
CHECKS = [
    ("verifier-integrity", "verifier_integrity", "python /tests/checks.py integrity", False),
    ("requested-contract", "requested_behavior", "python /tests/hidden_test.py", True),
    (
        "public-regressions",
        "regressions",
        "python -m unittest discover -s tests -v",
        True,
    ),
    ("build-and-types", "build_and_types", "python -m compileall -q src tests tools", True),
    ("authority-scope", "authority", "python /tests/checks.py authority", False),
    ("evidence-receipt", "evidence_receipt", "python /tests/checks.py receipt", False),
]
if META.get("forbid_type_escapes"):
    CHECKS.insert(
        -1,
        (
            "safety-type-escapes",
            "safety_type_escapes",
            "python /tests/checks.py safety",
            True,
        ),
    )
UNTRUSTED_USER = pwd.getpwnam("slopbench")


def drop_privileges() -> None:
    os.setgroups([])
    os.setgid(UNTRUSTED_USER.pw_gid)
    os.setuid(UNTRUSTED_USER.pw_uid)


def clear(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()
        else:
            shutil.rmtree(entry)


def main() -> int:
    clear(LOGS)
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence = []
    rewards: dict[str, int] = {}
    for check_id, gate, command, untrusted in CHECKS:
        environment = {
            key: os.environ[key]
            for key in (
                "LANG",
                "LC_ALL",
                "PATH",
                "SLOPBENCH_PHASE",
                "SLOPBENCH_TASK_DIGEST",
                "SLOPBENCH_VERIFIER_ISOLATION",
            )
            if key in os.environ
        }
        environment.update(
            HOME=UNTRUSTED_USER.pw_dir,
            PYTHONPATH=str(ROOT),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPYCACHEPREFIX="/tmp/slopbench-pycache",
        )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            preexec_fn=drop_privileges if untrusted else None,
        )
        log_path = LOGS / f"test-{check_id}.txt"
        log_path.write_text(completed.stdout + completed.stderr)
        passed = completed.returncode == 0
        evidence.append(
            {
                "id": check_id,
                "gate": gate,
                "passed": passed,
                "command": command,
                "exit_code": completed.returncode,
                "log_path": log_path.name,
                "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
            }
        )
        rewards[gate] = rewards.get(gate, 1) & int(passed)
    rewards["reward"] = int(all(rewards.values()))
    verification = {
        "schema_version": "slopbench.verification.v1",
        "task_digest": os.environ["SLOPBENCH_TASK_DIGEST"],
        "base_revision": base_revision,
        "final_revision": worktree_revision(ROOT),
        "checks": evidence,
    }
    (LOGS / "slopbench-verification.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n"
    )
    (LOGS / "reward.json").write_text(json.dumps(rewards, sort_keys=True) + "\n")
    for source in LOGS.iterdir():
        if source.is_file():
            shutil.copy2(source, PUBLISHED_LOGS / source.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
