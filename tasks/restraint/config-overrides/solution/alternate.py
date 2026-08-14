from __future__ import annotations

from collections.abc import Callable, Mapping


def load_config() -> dict[str, object]:
    return {"timeout_seconds": 30, "log_level": "info", "service": "billing"}


def _timeout(value: str) -> int:
    if type(value) is not str or not value.isdecimal() or not 1 <= int(value) <= 300:
        raise ValueError("invalid timeout")
    return int(value)


def _level(value: str) -> str:
    if type(value) is not str or value.casefold() not in ("debug", "info", "warning", "error"):
        raise ValueError("invalid log level")
    return value.casefold()


PARSERS: dict[str, tuple[str, Callable[[str], object]]] = {
    "APP_TIMEOUT_SECONDS": ("timeout_seconds", _timeout),
    "APP_LOG_LEVEL": ("log_level", _level),
}


def apply_runtime_overrides(
    config: Mapping[str, object], environment: Mapping[str, str]
) -> dict[str, object]:
    if not isinstance(config, Mapping) or not isinstance(environment, Mapping):
        raise ValueError("invalid mappings")
    result = dict(config)
    for variable, (setting, parse) in PARSERS.items():
        if variable in environment:
            result[setting] = parse(environment[variable])
    return result
