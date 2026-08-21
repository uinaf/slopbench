from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slopbench import repository_verify
from slopbench.repository_verify import (
    ContractCounts,
    RepositoryVerifyError,
    VerifyLane,
    lane_fingerprint,
    lane_includes,
    verify_contracts,
    write_lane_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]


def test_lane_inputs_preserve_verification_boundaries() -> None:
    assert lane_includes(VerifyLane.STATIC, "src/slopbench/contracts.py")
    assert not lane_includes(VerifyLane.STATIC, "README.md")
    assert lane_includes(VerifyLane.CONTRACTS, "tasks/tracer/slopbench-task.json")
    assert not lane_includes(VerifyLane.CONTRACTS, "README.md")
    assert lane_includes(VerifyLane.TESTS, "README.md")
    assert lane_includes(VerifyLane.GENERATED, "docs/METHODOLOGY.md")
    assert all(lane_includes(lane, "Makefile") for lane in VerifyLane)
    assert all(not lane_includes(lane, ".github/workflows/verify.yml") for lane in VerifyLane)


def test_fingerprint_changes_only_when_a_lane_input_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("artifacts/\n")
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    readme = tmp_path / "README.md"
    readme.write_text("first\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    output = tmp_path / "artifacts" / "verify" / "static.sha256"
    assert write_lane_fingerprint(tmp_path, VerifyLane.STATIC, output)
    original = output.read_text()
    original_mtime = output.stat().st_mtime_ns
    assert not write_lane_fingerprint(tmp_path, VerifyLane.STATIC, output)
    assert output.stat().st_mtime_ns == original_mtime

    readme.write_text("second\n")
    assert not write_lane_fingerprint(tmp_path, VerifyLane.STATIC, output)
    source.write_text("value = 2\n")
    assert write_lane_fingerprint(tmp_path, VerifyLane.STATIC, output)
    changed = output.read_text()
    assert changed != original

    source.unlink()
    assert write_lane_fingerprint(tmp_path, VerifyLane.STATIC, output)
    assert output.read_text() not in {original, changed}


def test_batch_contract_verifier_checks_the_repository() -> None:
    counts = verify_contracts(ROOT)
    assert counts.tasks > 0
    assert counts.runs > 0
    assert counts.profiles > 0
    assert counts.reference_configurations > 0


def test_fingerprint_handles_symlinks_and_rejects_non_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    target = tmp_path / "target.py"
    target.write_text("value = 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(target.name)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)

    original = lane_fingerprint(tmp_path, VerifyLane.STATIC)
    link.unlink()
    link.symlink_to("other.py")
    assert lane_fingerprint(tmp_path, VerifyLane.STATIC) != original

    directory = tmp_path / "directory.py"
    directory.mkdir()
    monkeypatch.setattr(repository_verify, "_repository_paths", lambda root: [directory.name])
    with pytest.raises(RepositoryVerifyError, match="not a file or symlink"):
        lane_fingerprint(tmp_path, VerifyLane.STATIC)


def test_fingerprint_rejects_a_non_repository(tmp_path: Path) -> None:
    with pytest.raises(RepositoryVerifyError, match="cannot enumerate"):
        lane_fingerprint(tmp_path, VerifyLane.STATIC)


def test_repository_verify_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    output = tmp_path / "static.sha256"

    assert (
        repository_verify.main(
            ["--root", str(tmp_path), "fingerprint", VerifyLane.STATIC, str(output)]
        )
        == 0
    )
    assert output.is_file()

    monkeypatch.setattr(
        repository_verify,
        "verify_contracts",
        lambda root: ContractCounts(1, 2, 3, 4),
    )
    assert repository_verify.main(["--root", str(tmp_path), "contracts"]) == 0
    assert capsys.readouterr().out == (
        "verified 1 tasks, 2 runs, 3 profiles, and 4 reference configurations\n"
    )

    def fail(*args: object) -> None:
        raise RepositoryVerifyError("broken input")

    monkeypatch.setattr(repository_verify, "write_lane_fingerprint", fail)
    assert (
        repository_verify.main(
            ["--root", str(tmp_path), "fingerprint", VerifyLane.STATIC, str(output)]
        )
        == 2
    )
    assert capsys.readouterr().err == "repository verification failed: broken input\n"
