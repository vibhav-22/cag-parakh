from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from license_server.store import LicenseError, LicenseStore


class LicenseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = LicenseStore(
            Path(self.directory.name) / "licenses.db",
            "test-signing-secret-that-is-longer-than-thirty-two-bytes",
        )
        self.store.create_user("analyst@example.com", "long-test-password", "Analyst", 1)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_login_registers_device_and_token_validates(self) -> None:
        device_id = str(uuid.uuid4())
        result = self.store.login(
            "ANALYST@example.com", "long-test-password", device_id, "Review laptop"
        )
        claims = self.store.validate(result["access_token"])
        self.assertEqual(claims["email"], "analyst@example.com")
        self.assertEqual(claims["device_id"], device_id)
        self.assertEqual(self.store.list_devices("analyst@example.com")[0]["device_name"], "Review laptop")

    def test_wrong_password_is_rejected(self) -> None:
        with self.assertRaises(LicenseError):
            self.store.login(
                "analyst@example.com", "wrong-password", str(uuid.uuid4()), "Review laptop"
            )

    def test_device_limit_and_revocation_are_enforced(self) -> None:
        first_device = str(uuid.uuid4())
        token = self.store.login(
            "analyst@example.com", "long-test-password", first_device, "First laptop"
        )["access_token"]
        with self.assertRaisesRegex(LicenseError, "device limit"):
            self.store.login(
                "analyst@example.com", "long-test-password", str(uuid.uuid4()), "Second laptop"
            )

        self.assertTrue(self.store.set_device_active("analyst@example.com", first_device, False))
        with self.assertRaisesRegex(LicenseError, "no longer approved"):
            self.store.validate(token)

    def test_disabling_user_invalidates_existing_token(self) -> None:
        result = self.store.login(
            "analyst@example.com", "long-test-password", str(uuid.uuid4()), "Review laptop"
        )
        self.assertTrue(self.store.set_user_active("analyst@example.com", False))
        with self.assertRaisesRegex(LicenseError, "no longer approved"):
            self.store.validate(result["access_token"])
