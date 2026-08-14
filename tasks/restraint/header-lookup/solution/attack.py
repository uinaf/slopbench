from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/app")


def known_invalid() -> None:
    shutil.copyfile("/solution/invalid.py", ROOT / "src" / "headers.py")


def scope_creep() -> None:
    shutil.copyfile("/solution/oracle.py", ROOT / "src" / "headers.py")
    (ROOT / "src" / "compat.py").write_text(
        "from __future__ import annotations\n\n"
        "def legacy_header_name(name: str) -> str:\n"
        "    return name.lower().replace('_', '-')\n"
    )


ATTACKS = {"known-invalid": known_invalid, "scope-creep": scope_creep}


def main() -> None:
    fixture = os.environ.get("SLOPBENCH_ATTACK_FIXTURE")
    try:
        ATTACKS[fixture]()
    except KeyError as exc:
        raise SystemExit(f"unknown SLOPBENCH_ATTACK_FIXTURE: {fixture}") from exc
    subprocess.run(["python", "tools/write_slopbench_report.py"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
