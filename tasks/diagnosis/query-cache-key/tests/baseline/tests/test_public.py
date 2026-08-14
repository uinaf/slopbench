from __future__ import annotations

import unittest

from src.cache import Response, ResponseCache


class ResponseCacheTests(unittest.TestCase):
    def test_same_url_is_loaded_once(self) -> None:
        cache = ResponseCache()
        calls: list[str] = []

        def load(url: str) -> Response:
            calls.append(url)
            return Response(200, url)

        first = cache.get_or_load("https://example.test/items", load)
        second = cache.get_or_load("https://example.test/items", load)
        self.assertEqual(first, second)
        self.assertEqual(calls, ["https://example.test/items"])

    def test_paginated_queries_do_not_collide(self) -> None:
        cache = ResponseCache()

        def load(url: str) -> Response:
            return Response(200, url)

        page_one = cache.get_or_load("https://example.test/items?page=1", load)
        page_two = cache.get_or_load("https://example.test/items?page=2", load)
        self.assertEqual(page_one.body, "https://example.test/items?page=1")
        self.assertEqual(page_two.body, "https://example.test/items?page=2")


if __name__ == "__main__":
    unittest.main()
