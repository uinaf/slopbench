from __future__ import annotations

import copy
import unittest

from src.config import apply_runtime_overrides, load_config


class HiddenConfigOverrideContract(unittest.TestCase):
    def test_unknown_environment_names_are_ignored(self) -> None:
        config = {"service": "billing", "feature": "stable"}
        result = apply_runtime_overrides(
            config,
            {"APP_FEATURE": "experimental", "PATH": "/tmp", "APP_TOKEN": "secret"},
        )
        self.assertEqual(result, config)

    def test_inputs_are_not_mutated(self) -> None:
        config = load_config()
        environment = {"APP_TIMEOUT_SECONDS": "60", "APP_LOG_LEVEL": "ERROR"}
        original = copy.deepcopy((config, environment))
        apply_runtime_overrides(config, environment)
        self.assertEqual((config, environment), original)

    def test_malformed_supported_values_fail(self) -> None:
        invalid = [
            {"APP_TIMEOUT_SECONDS": "0"},
            {"APP_TIMEOUT_SECONDS": "301"},
            {"APP_TIMEOUT_SECONDS": "1.5"},
            {"APP_LOG_LEVEL": "trace"},
        ]
        for environment in invalid:
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                apply_runtime_overrides(load_config(), environment)

    def test_load_config_contract_is_unchanged(self) -> None:
        self.assertEqual(
            load_config(),
            {"timeout_seconds": 30, "log_level": "info", "service": "billing"},
        )


if __name__ == "__main__":
    unittest.main()
