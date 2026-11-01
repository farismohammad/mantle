"""Origin binding tests. No network requests or real credential stores are used."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / "mantle_bridge.py"
spec = importlib.util.spec_from_file_location("mantle_registry_origin_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


class RegistryOriginTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix="mantle-origin-test-")).resolve()
        self.paths = bridge.MantlePaths(self.fixture / "home")

    def request(self, **overrides):
        values = {"url": "https://registry.example", "insecure": False,
                  "registryToken": "fixture-only-token",
                  "registryTokenOrigin": "https://registry.example:443"}
        values.update(overrides)
        return values

    def test_same_origin_accepts_token_and_disables_redirects(self):
        client = bridge.registry_client(self.paths, self.request())
        try:
            self.assertEqual(client.client.headers["Authorization"], "Bearer fixture-only-token")
            self.assertFalse(client.client.follow_redirects)
        finally:
            client.client.close()

    def test_missing_origin_refuses_token(self):
        with self.assertRaisesRegex(bridge.MantleError, "Re-enter"):
            bridge.registry_client(self.paths, self.request(registryTokenOrigin=""))

    def test_different_host_or_port_refuses_token(self):
        for origin in ["https://other.example:443", "https://registry.example:8443"]:
            with self.subTest(origin=origin), self.assertRaisesRegex(bridge.MantleError, "different address"):
                bridge.registry_client(self.paths, self.request(registryTokenOrigin=origin))

    def test_environment_override_cannot_take_bound_token(self):
        with patch.dict(os.environ, {"MANTLE_CLOUD_URL": "https://different.example"}):
            with self.assertRaisesRegex(bridge.MantleError, "different address"):
                bridge.registry_client(self.paths, self.request(url=None))
            # Core supplies the target shown in the UI explicitly, overriding env.
            client = bridge.registry_client(self.paths, self.request())
            try:
                self.assertEqual(client.base_url, "https://registry.example")
                self.assertEqual(client.client.headers["Authorization"], "Bearer fixture-only-token")
            finally:
                client.client.close()

    def test_ipv6_origin_matches_explicit_default_port(self):
        client = bridge.registry_client(self.paths, self.request(
            url="https://[2001:db8::1]/registry", registryTokenOrigin="https://[2001:db8::1]:443"))
        try:
            self.assertEqual(client.client.headers["Authorization"], "Bearer fixture-only-token")
            self.assertFalse(client.client.follow_redirects)
        finally:
            client.client.close()


if __name__ == "__main__":
    unittest.main()
