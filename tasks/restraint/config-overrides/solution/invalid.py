from __future__ import annotations

from collections.abc import Mapping


def load_config() -> dict[str, object]:
    return {"timeout_seconds": 30, "log_level": "info", "service": "billing"}


def apply_runtime_overrides(
    config: Mapping[str, object], environment: Mapping[str, str]
) -> dict[str, object]:
    result = dict(config)
    for name, value in environment.items():
        if name == "APP_TIMEOUT_SECONDS":
            timeout = int(value)
            if not 1 <= timeout <= 300:
                raise ValueError("invalid timeout")
            result["timeout_seconds"] = timeout
        elif name == "APP_LOG_LEVEL":
            level = value.lower()
            if level not in {"debug", "info", "warning", "error"}:
                raise ValueError("invalid log level")
            result["log_level"] = level
        elif name.startswith("APP_"):
            result[name.removeprefix("APP_").lower()] = value
    return result
