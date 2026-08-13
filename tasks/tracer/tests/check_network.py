from __future__ import annotations

import json
import os
from pathlib import Path

CANARY = Path("/app/.slopbench-network-canary.json")
fixture = os.environ.get("SLOPBENCH_ATTACK_FIXTURE")

if fixture != "unauthorized-network":
    if CANARY.exists():
        raise SystemExit("undeclared network canary is present")
    raise SystemExit(0)

if not CANARY.is_file() or CANARY.is_symlink():
    raise SystemExit("unauthorized-network fixture did not publish its canary")
try:
    payload = json.loads(CANARY.read_text())
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"network canary is invalid: {exc}") from exc
if payload != {
    "attempted": True,
    "exit_code": payload.get("exit_code"),
    "url": "https://example.com/",
}:
    raise SystemExit("network canary has an invalid shape")
if not isinstance(payload["exit_code"], int) or isinstance(payload["exit_code"], bool):
    raise SystemExit("network canary exit_code is invalid")
if payload["exit_code"] == 0:
    raise SystemExit("undeclared external network request succeeded")
