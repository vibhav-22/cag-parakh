from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from backend.access_control import (
    LOGIN_FAILURE_LIMIT,
    AccessDenied,
    AuthorizationFileError,
    DpapiSessionProtector,
    SessionManager,
    TestSessionProtector,
    load_device_id,
)
from backend.auth_manifest import (
    generate_key_pair,
    isoformat,
    load_private_key,
    read_envelope,
    sign_authorization,
    utc_now,
    write_envelope,
)
from license_server.store import AuthorizationAdmin


PASSWORD = "a-correct-test-password"
KEY_PASSWORD = b"a-long-test-key-passphrase"


class OfflineAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.private_key = self.root / "external" / "signing-key.pem"
        self.public_key = self.root / "bundle" / "public-key.pem"
        self.authorization = self.root / "authorization" / "authorization.json"
        generate_key_pair(self.private_key, self.public_key, KEY_PASSWORD)
        self.admin = AuthorizationAdmin(self.authorization, self.private_key, KEY_PASSWORD)
        self.admin.initialize(utc_now() + timedelta(days=30))
        self.admin.add_user("analyst@example.com", PASSWORD, display_name="Analyst")
        self.environment = patch.dict(
            os.environ,
            {"PARAKH_PACKAGED": "1", "PARAKH_DEV_AUTH_BYPASS": ""},
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def manager(self) -> SessionManager:
        return SessionManager(
            self.data,
            authorization_file=self.authorization,
            public_key_file=self.public_key,
            protector=TestSessionProtector(),
        )

    def test_signed_local_login_and_session_restart(self) -> None:
        manager = self.manager()
        token, user = manager.create("ANALYST@example.com", PASSWORD)
        self.assertEqual(user.display_name, "Analyst")
        self.assertEqual(manager.authenticate(token), user)
        self.assertEqual(self.manager().authenticate(token), user)

    def test_wrong_password_is_rejected(self) -> None:
        with self.assertRaisesRegex(AccessDenied, "incorrect"):
            self.manager().create("analyst@example.com", "wrong password")

    def test_missing_authorization_file_is_rejected(self) -> None:
        self.authorization.unlink()
        with self.assertRaisesRegex(AuthorizationFileError, "missing"):
            self.manager().create("analyst@example.com", PASSWORD)

    def test_modified_authorization_file_is_rejected(self) -> None:
        raw = read_envelope(self.authorization)
        raw["authorization"]["users"][0]["display_name"] = "Mallory"
        write_envelope(self.authorization, raw)
        with self.assertRaisesRegex(AuthorizationFileError, "signature"):
            self.manager().create("analyst@example.com", PASSWORD)

    def test_wrong_public_key_is_rejected(self) -> None:
        other_private = self.root / "other-private.pem"
        other_public = self.root / "other-public.pem"
        generate_key_pair(other_private, other_public, KEY_PASSWORD)
        manager = SessionManager(
            self.data,
            authorization_file=self.authorization,
            public_key_file=other_public,
            protector=TestSessionProtector(),
        )
        with self.assertRaisesRegex(AuthorizationFileError, "unapproved key"):
            manager.create("analyst@example.com", PASSWORD)

    def test_expired_authorization_file_is_rejected(self) -> None:
        private = load_private_key(self.private_key, KEY_PASSWORD)
        authorization = read_envelope(self.authorization)["authorization"]
        authorization["issued_at"] = isoformat(utc_now() - timedelta(days=2))
        authorization["expires_at"] = isoformat(utc_now() - timedelta(days=1))
        write_envelope(self.authorization, sign_authorization(authorization, private))
        with self.assertRaisesRegex(AuthorizationFileError, "expired"):
            self.manager().create("analyst@example.com", PASSWORD)

    def test_login_lockout_is_persistent_across_restart(self) -> None:
        manager = self.manager()
        for _ in range(LOGIN_FAILURE_LIMIT):
            with self.assertRaises(AccessDenied):
                manager.create("analyst@example.com", "wrong password")
        with self.assertRaises(AccessDenied) as raised:
            self.manager().create("analyst@example.com", PASSWORD)
        self.assertEqual(raised.exception.status_code, 429)

    def test_optional_device_binding_is_enforced(self) -> None:
        manager = self.manager()
        self.admin.set_user_devices("analyst@example.com", [manager.device_id])
        manager.create("analyst@example.com", PASSWORD)
        other = self.root / "other-data"
        unapproved = SessionManager(
            other,
            authorization_file=self.authorization,
            public_key_file=self.public_key,
            protector=TestSessionProtector(),
        )
        with self.assertRaises(AccessDenied):
            unapproved.create("analyst@example.com", PASSWORD)

    def test_new_signed_authorization_invalidates_old_sessions(self) -> None:
        manager = self.manager()
        token, _ = manager.create("analyst@example.com", PASSWORD)
        self.admin.reset_password("analyst@example.com", "a-replacement-password")
        self.assertIsNone(manager.authenticate(token))

    def test_corrupt_protected_state_is_ignored(self) -> None:
        manager = self.manager()
        token, _ = manager.create("analyst@example.com", PASSWORD)
        (self.data / ".auth-state.dat").write_bytes(b"plaintext-tampering")
        self.assertIsNone(self.manager().authenticate(token))


class DeviceIdentityTests(unittest.TestCase):
    def test_device_identity_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(load_device_id(path), load_device_id(path))


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is Windows-only")
class DpapiTests(unittest.TestCase):
    def test_current_user_can_round_trip_protected_state(self) -> None:
        protector = DpapiSessionProtector()
        plaintext = b'{"session":"secret"}'
        protected = protector.protect(plaintext)
        self.assertNotIn(b"secret", protected)
        self.assertEqual(protector.unprotect(protected), plaintext)


class DevelopmentBypassTests(unittest.TestCase):
    def test_source_bypass_must_be_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"PARAKH_PACKAGED": "", "PARAKH_DEV_AUTH_BYPASS": "1"},
            clear=False,
        ):
            manager = SessionManager(Path(directory), protector=TestSessionProtector())
            self.assertFalse(manager.required)
            self.assertEqual(manager.authenticate("").id, "development")

    def test_packaged_marker_disables_development_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"PARAKH_PACKAGED": "", "PARAKH_DEV_AUTH_BYPASS": "1"},
            clear=False,
        ), patch("backend.app_paths.ROOT", Path(directory)):
            (Path(directory) / ".parakh-packaged").touch()
            manager = SessionManager(Path(directory) / "data", protector=TestSessionProtector())
            self.assertTrue(manager.required)
            self.assertFalse(manager.development_bypass)
            self.assertIsNone(manager.authenticate(""))


if __name__ == "__main__":
    unittest.main()
