from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from src.archive import ArchivePolicyError, extract_archive


class ArchiveTests(unittest.TestCase):
    def test_extracts_regular_files_in_archive_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "fixture.zip"
            destination = root / "output"
            with ZipFile(archive, "w") as handle:
                handle.writestr("a.txt", "alpha")
                handle.writestr("nested/b.txt", "beta")

            extracted = extract_archive(archive, destination)

            self.assertEqual(
                extracted, (destination / "a.txt", destination / "nested/b.txt")
            )
            self.assertEqual((destination / "nested/b.txt").read_text(), "beta")

    def test_rejects_a_member_over_the_member_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "fixture.zip"
            with ZipFile(archive, "w") as handle:
                handle.writestr("large.bin", b"x" * 32)

            with (
                patch("src.archive.MAX_MEMBER_BYTES", 16),
                self.assertRaises(ArchivePolicyError),
            ):
                extract_archive(archive, root / "output")


if __name__ == "__main__":
    unittest.main()
