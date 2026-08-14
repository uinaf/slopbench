from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/app")


def write_review(source: Path) -> None:
    payload = json.loads(source.read_text())
    base_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    review = {
        "schema_version": "slopbench.review.v1",
        "task_id": "slopbench/review/webhook-dispatch",
        "task_digest": os.environ["SLOPBENCH_TASK_DIGEST"],
        "base_revision": base_revision,
        "findings": payload["findings"],
    }
    (ROOT / "slopbench-review.json").write_text(json.dumps(review, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    write_review(Path(sys.argv[1]))
