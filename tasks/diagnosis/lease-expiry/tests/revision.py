from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path


def worktree_revision(repo_dir: Path) -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=True,
        capture_output=True,
    )
    excluded = {b"slopbench-report.json"}
    digest = hashlib.sha256(b"slopbench.worktree.v1\0")
    for relative_bytes in sorted(
        path for path in completed.stdout.split(b"\0") if path and path not in excluded
    ):
        relative = os.fsdecode(relative_bytes)
        path = repo_dir / relative
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"-m")
            digest.update((0).to_bytes(8, "big"))
            continue
        digest.update(b"x" if metadata.st_mode & stat.S_IXUSR else b"-")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"l")
            content = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"f")
            content = path.read_bytes()
        else:
            digest.update(b"o")
            content = b""
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"
