from __future__ import annotations

import unittest

from src.cache import Response, ResponseCache


class HiddenQueryCacheContract(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = ResponseCache()
        self.calls: list[str] = []

    def load(self, url: str) -> Response:
        self.calls.append(url)
        return Response(200, url)

    def test_query_order_and_duplicates_remain_distinct(self) -> None:
        first = "https://example.test/search?tag=a&tag=b"
        second = "https://example.test/search?tag=b&tag=a"
        self.assertNotEqual(
            self.cache.get_or_load(first, self.load),
            self.cache.get_or_load(second, self.load),
        )
        self.assertEqual(self.calls, [first, second])

    def test_fragments_and_authority_case_do_not_split_identity(self) -> None:
        first = self.cache.get_or_load("HTTPS://EXAMPLE.TEST/items?q=x#one", self.load)
        second = self.cache.get_or_load("https://example.test/items?q=x#two", self.load)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)

    def test_invalidation_is_query_specific(self) -> None:
        first = "https://example.test/items?page=1"
        second = "https://example.test/items?page=2"
        self.cache.get_or_load(first, self.load)
        self.cache.get_or_load(second, self.load)
        self.cache.invalidate(first)
        self.cache.get_or_load(first, self.load)
        self.cache.get_or_load(second, self.load)
        self.assertEqual(self.calls, [first, second, first])

    def test_invalid_urls_are_rejected(self) -> None:
        for value in ("/relative", "ftp://example.test/a", "https://u:p@example.test/a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.cache.get_or_load(value, self.load)


if __name__ == "__main__":
    unittest.main()
