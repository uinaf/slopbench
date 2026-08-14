from __future__ import annotations

import unittest

from src.headers import canonicalize_headers, get_header


class HeaderLookupTests(unittest.TestCase):
    def test_lookup_is_case_insensitive(self) -> None:
        headers = [("Content-Type", "application/json")]
        self.assertEqual(get_header(headers, "content-type"), "application/json")

    def test_canonicalize_preserves_input_pairs(self) -> None:
        headers = [("X-Trace", "A-17")]
        self.assertEqual(canonicalize_headers(headers), tuple(headers))


if __name__ == "__main__":
    unittest.main()
