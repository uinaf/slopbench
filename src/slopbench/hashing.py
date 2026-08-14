"""Canonical hashing and immutable task sealing."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from slopbench.contracts import FileDigest, TaskContract

TASK_CONTRACT_FILENAME = "slopbench-task.json"


class ContractError(ValueError):
    """An external contract or immutable input is invalid."""


def canonical_json_bytes(value: BaseModel | dict[str, object]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    try:
        raw = path.read_text()
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    try:
        return model_type.model_validate_json(raw)
    except ValidationError as exc:
        raise ContractError(f"invalid {model_type.__name__} at {path}: {exc}") from exc


def write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def task_input_paths(task_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in task_dir.rglob("*"):
        if path.name == TASK_CONTRACT_FILENAME and path.parent == task_dir:
            continue
        if path.is_symlink():
            raise ContractError(f"task input must not be a symlink: {path}")
        if path.is_file():
            paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(task_dir).as_posix())


def compute_task_inputs(task_dir: Path) -> list[FileDigest]:
    return [
        FileDigest(
            path=path.relative_to(task_dir).as_posix(),
            sha256=sha256_file(path),
        )
        for path in task_input_paths(task_dir)
    ]


def seal_task(task_dir: Path) -> TaskContract:
    task_dir = task_dir.resolve()
    contract_path = task_dir / TASK_CONTRACT_FILENAME
    contract = load_model(contract_path, TaskContract)
    sealed = contract.model_copy(update={"immutable_inputs": compute_task_inputs(task_dir)})
    sealed = TaskContract.model_validate_json(canonical_json_bytes(sealed))
    write_model(contract_path, sealed)
    return sealed


def validate_task(task_dir: Path) -> tuple[TaskContract, str, str]:
    task_dir = task_dir.resolve()
    contract_path = task_dir / TASK_CONTRACT_FILENAME
    contract = load_model(contract_path, TaskContract)
    actual_inputs = compute_task_inputs(task_dir)
    expected = {item.path: item.sha256 for item in contract.immutable_inputs}
    actual = {item.path: item.sha256 for item in actual_inputs}
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed = sorted(
        path for path in expected.keys() & actual.keys() if expected[path] != actual[path]
    )
    problems: list[str] = []
    if missing:
        problems.append(f"missing inputs: {missing}")
    if unexpected:
        problems.append(f"unsealed inputs: {unexpected}")
    if changed:
        problems.append(f"changed inputs: {changed}")
    if problems:
        raise ContractError("task seal mismatch: " + "; ".join(problems))
    contract_sha256 = sha256_file(contract_path)
    task_digest = sha256_bytes(b"slopbench.task.v1\0" + canonical_json_bytes(contract))
    return contract, contract_sha256, task_digest


def _git_paths(repo_dir: Path) -> list[bytes]:
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
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode(errors="replace").strip()
        raise ContractError(f"cannot enumerate repository files: {message}")
    return sorted(path for path in completed.stdout.split(b"\0") if path)


def compute_worktree_revision(
    repo_dir: Path,
    excluded_paths: Iterable[str] = ("slopbench-report.json",),
) -> str:
    repo_dir = repo_dir.resolve()
    excluded = {path.encode() for path in excluded_paths}
    digest = hashlib.sha256(b"slopbench.worktree.v1\0")
    for relative_bytes in _git_paths(repo_dir):
        if relative_bytes in excluded:
            continue
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


def digest_files(root: Path, paths: Iterable[Path]) -> list[FileDigest]:
    return [
        FileDigest(path=path.relative_to(root).as_posix(), sha256=sha256_file(path))
        for path in sorted(paths)
        if path.is_file()
    ]
