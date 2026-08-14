from __future__ import annotations

from collections.abc import Sequence

Header = tuple[str, str]


def canonicalize_headers(headers: Sequence[Header]) -> tuple[Header, ...]:
    return tuple(headers)


def get_header(headers: Sequence[Header], name: str) -> str | None:
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    for header_name, value in headers:
        if (
            not isinstance(header_name, str)
            or not header_name
            or not isinstance(value, str)
        ):
            raise ValueError(
                "headers must contain non-empty string names and string values"
            )
        if header_name == name:
            return value
    return None
