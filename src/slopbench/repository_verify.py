from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FINGERPRINT_VERSION = b"slopbench.repository-verify.v1\0"


class RepositoryVerifyError(RuntimeError):
    """Repository inputs cannot be enumerated or fingerprinted safely."""


class VerifyLane(StrEnum):
    STATIC = "static"
    TESTS = "tests"
    CONTRACTS = "contracts"
    GENERATED = "generated"


@dataclass(frozen=True)
class ContractCounts:
    tasks: int
    runs: int
    profiles: int
    reference_configurations: int


def _is_below(relative: str, *roots: str) -> bool:
    return any(relative == root or relative.startswith(f"{root}/") for root in roots)


def lane_includes(lane: VerifyLane, relative: str) -> bool:
    if relative in {"Makefile", ".python-version", "pyproject.toml", "uv.lock"}:
        return True
    if lane is VerifyLane.STATIC:
        return relative.endswith(".py")
    if lane is VerifyLane.TESTS:
        return relative != "AGENTS.md" and not _is_below(relative, ".github")
    if lane is VerifyLane.CONTRACTS:
        return relative.endswith(".py") or _is_below(
            relative,
            "tasks",
            "runs",
            "profiles",
            "reference-configurations",
        )
    if lane is VerifyLane.GENERATED:
        return relative != "AGENTS.md" and not _is_below(relative, ".github")
    raise AssertionError(f"unhandled verification lane: {lane}")


def _repository_paths(root: Path) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode(errors="replace").strip()
        raise RepositoryVerifyError(f"cannot enumerate repository verification inputs: {message}")
    return sorted(os.fsdecode(path) for path in completed.stdout.split(b"\0") if path)


def lane_fingerprint(root: Path, lane: VerifyLane) -> str:
    root = root.resolve()
    digest = hashlib.sha256(_FINGERPRINT_VERSION)
    digest.update(lane.value.encode())
    digest.update(b"\0")
    digest.update(sys.version.encode())
    digest.update(b"\0")
    for relative in _repository_paths(root):
        if not lane_includes(lane, relative):
            continue
        relative_bytes = os.fsencode(relative)
        path = root / relative
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"m")
            continue
        digest.update(b"x" if metadata.st_mode & stat.S_IXUSR else b"-")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"l")
            content = os.fsencode(os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"f")
            content = path.read_bytes()
        else:
            raise RepositoryVerifyError(f"verification input is not a file or symlink: {relative}")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def write_lane_fingerprint(root: Path, lane: VerifyLane, output: Path) -> bool:
    rendered = lane_fingerprint(root, lane) + "\n"
    try:
        if output.read_text() == rendered:
            return False
    except FileNotFoundError:
        pass
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(rendered)
    os.replace(temporary, output)
    return True


def verify_contracts(root: Path) -> ContractCounts:
    from slopbench.contracts import RunManifest
    from slopbench.hashing import load_model, validate_task
    from slopbench.release import ProfileDefinition, ReferenceConfiguration

    root = root.resolve()
    task_contracts = sorted((root / "tasks").rglob("slopbench-task.json"))
    run_manifests = sorted((root / "runs").rglob("*.json"))
    profiles = sorted((root / "profiles").glob("*.json"))
    reference_configurations = sorted((root / "reference-configurations").glob("*.json"))

    for contract in task_contracts:
        validate_task(contract.parent)
    for manifest in run_manifests:
        load_model(manifest, RunManifest)
    for profile in profiles:
        load_model(profile, ProfileDefinition)
    for configuration in reference_configurations:
        load_model(configuration, ReferenceConfiguration)

    return ContractCounts(
        tasks=len(task_contracts),
        runs=len(run_manifests),
        profiles=len(profiles),
        reference_configurations=len(reference_configurations),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify repository-owned SlopBench contracts")
    parser.add_argument("--root", default=ROOT, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("lane", choices=list(VerifyLane), type=VerifyLane)
    fingerprint.add_argument("output", type=Path)

    commands.add_parser("contracts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fingerprint":
            write_lane_fingerprint(args.root, args.lane, args.output)
        elif args.command == "contracts":
            counts = verify_contracts(args.root)
            print(
                "verified "
                f"{counts.tasks} tasks, "
                f"{counts.runs} runs, "
                f"{counts.profiles} profiles, and "
                f"{counts.reference_configurations} reference configurations"
            )
        else:
            raise AssertionError(f"unhandled command: {args.command}")
    except (RepositoryVerifyError, ValueError, OSError) as exc:
        print(f"repository verification failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
