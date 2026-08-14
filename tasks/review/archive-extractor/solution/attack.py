from __future__ import annotations

import os
import subprocess
from pathlib import Path

from write_review import write_review

ROOT = Path("/app")


def known_false_positive() -> None:
    write_review(Path("/solution/known_false_positive.json"))


def source_mutation() -> None:
    write_review(Path("/solution/oracle.json"))
    source = ROOT / "src" / "archive.py"
    source.write_text(source.read_text().replace("length=64 * 1024", "length=32 * 1024"))


ATTACKS = {
    "known-false-positive": known_false_positive,
    "source-mutation": source_mutation,
}


def main() -> None:
    fixture = os.environ.get("SLOPBENCH_ATTACK_FIXTURE")
    try:
        ATTACKS[fixture]()
    except KeyError as exc:
        raise SystemExit(f"unknown SLOPBENCH_ATTACK_FIXTURE: {fixture}") from exc
    subprocess.run(["python", "tools/write_slopbench_report.py"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
