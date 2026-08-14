from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from slopbench.contracts import TaskContract
from slopbench.hashing import (
    ContractError,
    canonical_json_bytes,
    compute_task_inputs,
    compute_worktree_revision,
    digest_files,
    load_model,
    seal_task,
    sha256_bytes,
    sha256_file,
    task_input_paths,
    validate_task,
    write_model,
)
from tests.helpers import task_contract, task_payload, write_json


def test_canonical_json_is_stable_and_newline_terminated() -> None:
    first = canonical_json_bytes({"z": 1, "a": "value"})
    second = canonical_json_bytes({"a": "value", "z": 1})

    assert first == second == b'{"a":"value","z":1}\n'
    assert canonical_json_bytes(task_contract()) == canonical_json_bytes(
        task_contract().model_dump(mode="json")
    )
    assert sha256_bytes(first) == sha256_bytes(second)


def test_load_model_and_write_model_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "task.json"
    contract = task_contract()

    write_model(path, contract)

    assert load_model(path, TaskContract) == contract
    assert json.loads(path.read_text())["schema_version"] == "slopbench.task.v1"


def test_load_model_reports_read_and_validation_context(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema_version":"wrong"}\n')

    with pytest.raises(ContractError, match=f"cannot read {missing}"):
        load_model(missing, TaskContract)
    with pytest.raises(ContractError, match=f"invalid TaskContract at {malformed}"):
        load_model(malformed, TaskContract)


def make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Implement it.\n")
    (task_dir / "tests" / "test.sh").write_text("#!/bin/sh\n")
    write_json(task_dir / "slopbench-task.json", task_payload(sealed=False))
    return task_dir


def test_seal_and_validate_task_are_reproducible(tmp_path: Path) -> None:
    task_dir = make_task_dir(tmp_path)

    first = seal_task(task_dir)
    first_bytes = (task_dir / "slopbench-task.json").read_bytes()
    second = seal_task(task_dir)
    validated, contract_sha, task_digest = validate_task(task_dir)

    assert first == second == validated
    assert first_bytes == (task_dir / "slopbench-task.json").read_bytes()
    assert contract_sha == sha256_file(task_dir / "slopbench-task.json")
    assert len(task_digest) == 64
    assert {item.path for item in first.immutable_inputs} == {
        "instruction.md",
        "tests/test.sh",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("changed", "changed inputs"),
        ("missing", "missing inputs"),
        ("unexpected", "unsealed inputs"),
    ],
)
def test_validate_task_detects_immutable_input_drift(
    tmp_path: Path, mutation: str, message: str
) -> None:
    task_dir = make_task_dir(tmp_path)
    seal_task(task_dir)
    if mutation == "changed":
        (task_dir / "instruction.md").write_text("Changed.\n")
    elif mutation == "missing":
        (task_dir / "instruction.md").unlink()
    else:
        (task_dir / "new.txt").write_text("new\n")

    with pytest.raises(ContractError, match=message):
        validate_task(task_dir)


def test_task_inputs_reject_symlinks(tmp_path: Path) -> None:
    task_dir = make_task_dir(tmp_path)
    (task_dir / "link").symlink_to("instruction.md")

    with pytest.raises(ContractError, match="must not be a symlink"):
        task_input_paths(task_dir)


def test_task_inputs_exclude_only_the_root_contract(tmp_path: Path) -> None:
    task_dir = make_task_dir(tmp_path)
    nested = task_dir / "nested"
    nested.mkdir()
    (nested / "slopbench-task.json").write_text("nested\n")

    paths = [path.relative_to(task_dir).as_posix() for path in task_input_paths(task_dir)]
    inputs = compute_task_inputs(task_dir)

    assert "slopbench-task.json" not in paths
    assert "nested/slopbench-task.json" in paths
    assert [item.path for item in inputs] == paths


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def test_worktree_revision_tracks_content_mode_and_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "source.py"
    source.write_text("value = 1\n")
    (repo / ".gitignore").write_text("slopbench-report.json\n")
    (repo / "link").symlink_to("source.py")
    init_repo(repo)
    original = compute_worktree_revision(repo)

    (repo / "slopbench-report.json").write_text("ignored\n")
    assert compute_worktree_revision(repo) == original
    source.write_text("value = 2\n")
    content_revision = compute_worktree_revision(repo)
    assert content_revision != original
    source.chmod(source.stat().st_mode | 0o100)
    executable_revision = compute_worktree_revision(repo)
    assert executable_revision != content_revision
    (repo / "link").unlink()
    (repo / "link").symlink_to("elsewhere")
    assert compute_worktree_revision(repo) != executable_revision


def test_worktree_revision_requires_a_git_repository(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="cannot enumerate repository files"):
        compute_worktree_revision(tmp_path)


def test_worktree_revision_records_deletions_and_non_file_entries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tracked").write_text("file\n")
    init_repo(repo)
    original = compute_worktree_revision(repo)
    (repo / "tracked").unlink()
    deleted = compute_worktree_revision(repo)
    (repo / "tracked").mkdir()
    non_file = compute_worktree_revision(repo)

    assert deleted != original
    assert non_file != deleted


def test_digest_files_is_sorted_and_skips_directories(tmp_path: Path) -> None:
    first = tmp_path / "b.txt"
    second = tmp_path / "a.txt"
    directory = tmp_path / "directory"
    first.write_text("b")
    second.write_text("a")
    directory.mkdir()

    digests = digest_files(tmp_path, [first, directory, second])

    assert [item.path for item in digests] == ["a.txt", "b.txt"]
    assert digests[0].sha256 == sha256_file(second)


def test_task_input_hashes_are_content_based(tmp_path: Path) -> None:
    path = tmp_path / "value"
    path.write_bytes(b"value")

    assert sha256_file(path) == sha256_bytes(b"value")
    os.utime(path, (1, 1))
    assert sha256_file(path) == sha256_bytes(b"value")
