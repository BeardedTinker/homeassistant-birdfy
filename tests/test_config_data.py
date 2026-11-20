"""Tests for Birdfy configuration helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "birdfy"
package = types.ModuleType("birdfy_config_test")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[package.__name__] = package

for module_name in ("const", "config_data"):
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.{module_name}",
        PACKAGE_PATH / f"{module_name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

config_data = sys.modules["birdfy_config_test.config_data"]


class ConfigDataTest(unittest.TestCase):
    """Test safe credential storage and reload detection."""

    def test_password_is_stored_as_digest(self) -> None:
        """Never require storing the account's clear-text password."""
        digest = config_data.password_digest("secret")

        self.assertEqual(len(digest), 32)
        self.assertNotEqual(digest, "secret")
        self.assertEqual(config_data.password_digest(digest), digest)

    def test_token_changes_do_not_require_reload(self) -> None:
        """Exclude renewable authentication state from reload detection."""
        before = {
            "username": "person@example.com",
            "password": "digest",
            "token": "old",
            "refresh_token": "old-refresh",
        }
        after = {
            **before,
            "token": "new",
            "refresh_token": "new-refresh",
        }

        self.assertEqual(
            config_data.runtime_configuration_signature(before),
            config_data.runtime_configuration_signature(after),
        )

    def test_login_or_uuid_change_requires_reload(self) -> None:
        """Include actual integration configuration in the signature."""
        before = {"username": "old", "uuid": "one"}
        after = {"username": "new", "uuid": "two"}

        self.assertNotEqual(
            config_data.runtime_configuration_signature(before),
            config_data.runtime_configuration_signature(after),
        )


if __name__ == "__main__":
    unittest.main()
