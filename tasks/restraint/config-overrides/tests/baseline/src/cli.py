from __future__ import annotations

from src.config import load_config


def startup_summary() -> str:
    config = load_config()
    return f"{config['service']}:{config['log_level']}"
