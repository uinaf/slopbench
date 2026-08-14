from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Response:
    status: int
    body: str


def _cache_key(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    without_fragment = url.partition("#")[0]
    parsed = urlsplit(without_fragment)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be absolute HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain user information")
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}{query}"


class ResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, Response] = {}

    def get_or_load(self, url: str, loader: Callable[[str], Response]) -> Response:
        key = _cache_key(url)
        response = self._entries.get(key)
        if response is None:
            response = loader(url)
            self._entries[key] = response
        return response

    def invalidate(self, url: str) -> None:
        self._entries.pop(_cache_key(url), None)
