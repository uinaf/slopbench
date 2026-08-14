from __future__ import annotations

from collections.abc import Sequence

Header = tuple[str, str]


def canonicalize_headers(headers: Sequence[Header]) -> tuple[Header, ...]:
    return tuple(headers)


def get_header(headers: Sequence[Header], name: str) -> str | None:
    if type(name) is not str or name == "":
        raise ValueError("invalid header name")
    first: dict[str, str] = {}
    for pair in headers:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("invalid header")
        header_name, value = pair
        if type(header_name) is not str or not header_name or type(value) is not str:
            raise ValueError("invalid header")
        first.setdefault(header_name.lower(), value)
    return first.get(name.lower())
