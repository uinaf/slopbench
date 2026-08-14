from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path("/app")


def output(*args: str) -> set[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(completed.stdout.splitlines())


changed = output("diff", "--name-only", "HEAD")
untracked = output("ls-files", "--others", "--exclude-standard")
if changed - {"src/eventlog.py"}:
    raise SystemExit(f"changes exceed task authority: {sorted(changed)}")
if untracked:
    raise SystemExit(f"untracked files exceed task authority: {sorted(untracked)}")
