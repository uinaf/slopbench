from __future__ import annotations

from collections.abc import Mapping


def load_config() -> dict[str, object]:
    return {"timeout_seconds": 30, "log_level": "info", "service": "billing"}


def apply_runtime_overrides(
    config: Mapping[str, object], environment: Mapping[str, str]
) -> dict[str, object]:
    result = dict(config)
    for name, value in environment.items():
        if name.startswith("APP_"):
            result[name.removeprefix("APP_").lower()] = value
    return result
