from __future__ import annotations

from collections.abc import Sequence

Header = tuple[str, str]


def canonicalize_headers(headers: Sequence[Header]) -> tuple[Header, ...]:
    return tuple(headers)


def get_header(headers: Sequence[Header], name: str) -> str | None:
    if not isinstance(name, str) or not name:
        raise ValueError("invalid name")
    normalized = {header_name.lower(): value.lower() for header_name, value in headers}
    return normalized.get(name.lower())
