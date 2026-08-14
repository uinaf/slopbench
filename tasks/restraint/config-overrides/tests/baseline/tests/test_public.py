from __future__ import annotations

import unittest

from src.config import apply_runtime_overrides, load_config


class RuntimeOverrideTests(unittest.TestCase):
    def test_supported_overrides_are_parsed(self) -> None:
        result = apply_runtime_overrides(
            load_config(),
            {"APP_TIMEOUT_SECONDS": "45", "APP_LOG_LEVEL": "WARNING"},
        )
        self.assertEqual(result["timeout_seconds"], 45)
        self.assertEqual(result["log_level"], "warning")

    def test_without_overrides_config_is_preserved(self) -> None:
        config = load_config()
        self.assertEqual(apply_runtime_overrides(config, {}), config)


if __name__ == "__main__":
    unittest.main()
