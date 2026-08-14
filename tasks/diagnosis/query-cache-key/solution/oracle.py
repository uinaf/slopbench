from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


@dataclass(frozen=True)
class Response:
    status: int
    body: str


def _parts(url: str) -> SplitResult:
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("url must be absolute HTTP or HTTPS")
    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not contain user information")
    return parts


def _cache_key(url: str) -> str:
    parts = _parts(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


class ResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, Response] = {}

    def get_or_load(self, url: str, loader: Callable[[str], Response]) -> Response:
        key = _cache_key(url)
        if key not in self._entries:
            self._entries[key] = loader(url)
        return self._entries[key]

    def invalidate(self, url: str) -> None:
        self._entries.pop(_cache_key(url), None)
