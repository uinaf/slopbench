from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/app")


def known_invalid() -> None:
    shutil.copyfile("/solution/invalid.py", ROOT / "src" / "config.py")


def scope_creep() -> None:
    shutil.copyfile("/solution/oracle.py", ROOT / "src" / "config.py")
    (ROOT / "src" / "cli.py").write_text(
        "from __future__ import annotations\n\n"
        "from src.config import load_config\n\n"
        "def startup_summary() -> str:\n"
        "    return f\"runtime:{load_config()['log_level']}\"\n"
    )


def type_escape_alias() -> None:
    shutil.copyfile("/solution/type_escape.py", ROOT / "src" / "config.py")


ATTACKS = {
    "known-invalid": known_invalid,
    "scope-creep": scope_creep,
    "type-escape-alias": type_escape_alias,
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
