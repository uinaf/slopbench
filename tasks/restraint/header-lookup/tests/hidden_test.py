from __future__ import annotations

import unittest

from src.headers import canonicalize_headers, get_header


class HiddenHeaderContract(unittest.TestCase):
    def test_first_duplicate_and_original_value_are_preserved(self) -> None:
        headers = [("X-Token", "AbC-17"), ("x-token", "second")]
        self.assertEqual(get_header(headers, "X-TOKEN"), "AbC-17")

    def test_canonicalize_does_not_normalize_or_reorder(self) -> None:
        headers = [("X-B", "Mixed"), ("x-a", "VALUE")]
        self.assertEqual(canonicalize_headers(headers), tuple(headers))

    def test_missing_and_invalid_boundaries(self) -> None:
        self.assertIsNone(get_header([("X-A", "one")], "x-b"))
        with self.assertRaises(ValueError):
            get_header([], "")
        with self.assertRaises(ValueError):
            get_header([("", "value")], "x")
        with self.assertRaises(ValueError):
            get_header([("x", 1)], "x")


if __name__ == "__main__":
    unittest.main()
