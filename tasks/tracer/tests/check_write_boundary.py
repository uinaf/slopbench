from __future__ import annotations

from pathlib import Path

TARGETS = [
    Path("/app/.slopbench-write-canary"),
    Path("/tests/.slopbench-write-canary"),
    Path("/trusted-logs/.slopbench-write-canary"),
]

for target in TARGETS:
    try:
        target.write_text("untrusted verifier write\n")
    except PermissionError:
        continue
    except OSError as exc:
        raise SystemExit(f"write boundary check failed for {target}: {exc}") from exc
    target.unlink(missing_ok=True)
    raise SystemExit(f"untrusted verifier process can write {target.parent}")
