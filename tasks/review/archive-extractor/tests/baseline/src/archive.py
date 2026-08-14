from __future__ import annotations

import shutil
from pathlib import Path
from zipfile import ZipFile

MAX_MEMBER_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


class ArchivePolicyError(ValueError):
    pass


def extract_archive(archive: Path, destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with ZipFile(archive) as handle:
        for member in handle.infolist():
            if member.is_dir():
                continue
            if member.file_size > MAX_MEMBER_BYTES:
                raise ArchivePolicyError("archive member exceeds the size limit")
            if member.file_size > MAX_TOTAL_BYTES:
                raise ArchivePolicyError("archive exceeds the total size limit")
            target = destination / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)
            extracted.append(target)
    return tuple(extracted)
