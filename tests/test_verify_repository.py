from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from slopbench import repository_verify
from slopbench.repository_verify import (
    RepositoryVerifyError,
    VerifyLane,
    lane_fingerprint,
    lane_fingerprints,
    lane_includes,
    write_lane_fingerprint,
    write_lane_fingerprints,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("artifacts/\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore"], check=True)
    return tmp_path


def track(root: Path, *paths: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "--", *paths], check=True)


def test_lane_inputs_preserve_verification_boundaries() -> None:
    assert lane_includes(VerifyLane.STATIC, "src/slopbench/contracts.py")
    assert not lane_includes(VerifyLane.STATIC, "README.md")
    assert lane_includes(VerifyLane.CONTRACTS, "tasks/tracer/slopbench-task.json")
    assert not lane_includes(VerifyLane.CONTRACTS, "README.md")
    assert lane_includes(VerifyLane.TESTS, "README.md")
    assert lane_includes(VerifyLane.GENERATED, "docs/METHODOLOGY.md")
    assert all(lane_includes(lane, "Makefile") for lane in VerifyLane)
    assert all(not lane_includes(lane, ".github/workflows/verify.yml") for lane in VerifyLane)


def test_fingerprint_changes_only_when_a_lane_input_changes(git_repository: Path) -> None:
    source = git_repository / "source.py"
    source.write_text("value = 1\n")
    readme = git_repository / "README.md"
    readme.write_text("first\n")
    track(git_repository, "source.py", "README.md")

    output = git_repository / "artifacts" / "verify" / "static.sha256"
    assert write_lane_fingerprint(git_repository, VerifyLane.STATIC, output)
    original = output.read_text()
    original_mtime = output.stat().st_mtime_ns
    assert not write_lane_fingerprint(git_repository, VerifyLane.STATIC, output)
    assert output.stat().st_mtime_ns == original_mtime

    readme.write_text("second\n")
    assert not write_lane_fingerprint(git_repository, VerifyLane.STATIC, output)
    source.write_text("value = 2\n")
    assert write_lane_fingerprint(git_repository, VerifyLane.STATIC, output)
    changed = output.read_text()
    assert changed != original

    source.unlink()
    assert write_lane_fingerprint(git_repository, VerifyLane.STATIC, output)
    assert output.read_text() not in {original, changed}


def test_bulk_fingerprints_match_single_lanes_and_preserve_unchanged_files(
    git_repository: Path,
) -> None:
    source = git_repository / "source.py"
    source.write_text("value = 1\n")
    readme = git_repository / "README.md"
    readme.write_text("first\n")
    track(git_repository, "source.py", "README.md")

    fingerprints = lane_fingerprints(git_repository)
    assert fingerprints == {lane: lane_fingerprint(git_repository, lane) for lane in VerifyLane}

    output_dir = git_repository / "artifacts" / "verify" / "inputs"
    assert write_lane_fingerprints(git_repository, output_dir) == set(VerifyLane)
    mtimes = {lane: (output_dir / f"{lane.value}.sha256").stat().st_mtime_ns for lane in VerifyLane}
    assert write_lane_fingerprints(git_repository, output_dir) == set()
    assert {
        lane: (output_dir / f"{lane.value}.sha256").stat().st_mtime_ns for lane in VerifyLane
    } == mtimes

    readme.write_text("second\n")
    assert write_lane_fingerprints(git_repository, output_dir) == {
        VerifyLane.TESTS,
        VerifyLane.GENERATED,
    }


def test_fingerprint_handles_symlinks_and_rejects_non_files(git_repository: Path) -> None:
    target = git_repository / "target.py"
    target.write_text("value = 1\n")
    link = git_repository / "link.py"
    link.symlink_to(target.name)
    track(git_repository, "target.py", "link.py")

    original = lane_fingerprint(git_repository, VerifyLane.STATIC)
    link.unlink()
    link.symlink_to("other.py")
    assert lane_fingerprint(git_repository, VerifyLane.STATIC) != original

    directory = git_repository / "directory.py"
    directory.write_text("tracked as a file\n")
    track(git_repository, "directory.py")
    directory.unlink()
    directory.mkdir()
    with pytest.raises(RepositoryVerifyError, match="not a file or symlink"):
        lane_fingerprint(git_repository, VerifyLane.STATIC)


def test_fingerprint_rejects_a_non_repository(tmp_path: Path) -> None:
    with pytest.raises(RepositoryVerifyError, match="cannot enumerate"):
        lane_fingerprint(tmp_path, VerifyLane.STATIC)


def test_repository_verify_cli(git_repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = git_repository / "source.py"
    source.write_text("value = 1\n")
    track(git_repository, "source.py")
    output = git_repository / "artifacts" / "verify" / "static.sha256"

    assert (
        repository_verify.main(
            ["--root", str(git_repository), "fingerprint", VerifyLane.STATIC, str(output)]
        )
        == 0
    )
    assert output.is_file()
    assert (
        repository_verify.main(["--root", str(git_repository), "fingerprints", str(output.parent)])
        == 0
    )
    assert all((output.parent / f"{lane.value}.sha256").is_file() for lane in VerifyLane)

    contracts = git_repository / "contracts"
    shutil.copytree(
        ROOT / "tasks" / "diagnosis" / "lease-expiry",
        contracts / "tasks" / "diagnosis" / "lease-expiry",
    )
    for source, destination in (
        (
            ROOT / "runs" / "diagnosis" / "lease-expiry" / "nop.json",
            contracts / "runs" / "nop.json",
        ),
        (ROOT / "profiles" / "balanced.json", contracts / "profiles" / "balanced.json"),
        (
            ROOT / "reference-configurations" / "codex-sol-high.json",
            contracts / "reference-configurations" / "codex-sol-high.json",
        ),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    assert repository_verify.main(["--root", str(contracts), "contracts"]) == 0
    assert capsys.readouterr().out == (
        "verified 1 tasks, 1 runs, 1 profiles, and 1 reference configurations\n"
    )

    missing_root = git_repository / "missing"
    assert (
        repository_verify.main(
            ["--root", str(missing_root), "fingerprint", VerifyLane.STATIC, str(output)]
        )
        == 2
    )
    assert capsys.readouterr().err.startswith(
        "repository verification failed: cannot enumerate repository verification inputs:"
    )
