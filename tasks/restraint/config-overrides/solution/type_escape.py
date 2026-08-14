from __future__ import annotations

import typing as t
from collections.abc import Mapping
from typing import cast as coerce


def load_config() -> dict[str, object]:
    return {"timeout_seconds": 30, "log_level": "info", "service": "billing"}


def apply_runtime_overrides(
    config: Mapping[str, object], environment: Mapping[str, str]
) -> dict[str, object]:
    if not isinstance(config, Mapping) or not isinstance(environment, Mapping):
        raise ValueError("config and environment must be mappings")
    result: t.Any = dict(config)
    if "APP_TIMEOUT_SECONDS" in environment:
        raw_timeout = environment["APP_TIMEOUT_SECONDS"]
        if not isinstance(raw_timeout, str) or not raw_timeout.isdecimal():
            raise ValueError("timeout override must be decimal")
        timeout = int(raw_timeout)
        if not 1 <= timeout <= 300:
            raise ValueError("timeout override is outside 1 through 300")
        result["timeout_seconds"] = timeout
    if "APP_LOG_LEVEL" in environment:
        raw_level = environment["APP_LOG_LEVEL"]
        if not isinstance(raw_level, str) or raw_level.lower() not in {
            "debug",
            "info",
            "warning",
            "error",
        }:
            raise ValueError("log level override is invalid")
        result["log_level"] = raw_level.lower()
    return coerce(dict[str, object], result)
