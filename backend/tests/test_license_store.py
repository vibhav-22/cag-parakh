from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from backend.auth_manifest import (
    generate_key_pair,
    load_public_key,
    read_envelope,
    utc_now,
    verify_envelope,
    verify_password,
)
from license_server.store import AuthorizationAdmin


KEY_PASSWORD = b"a-long-test-key-passphrase"


class AuthorizationAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.private_key = root / "private" / "signing-key.pem"
        self.public_key = root / "public-key.pem"
        self.authorization = root / "authorization.json"
        generate_key_pair(self.private_key, self.public_key, KEY_PASSWORD)
        self.admin = AuthorizationAdmin(self.authorization, self.private_key, KEY_PASSWORD)
        self.admin.initialize(utc_now() + timedelta(days=90))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def payload(self):
        envelope = read_envelope(self.authorization)
        payload, _ = verify_envelope(envelope, load_public_key(self.public_key))
        return payload

    def test_generated_private_key_is_encrypted_and_never_enters_manifest(self) -> None:
        self.assertIn(b"ENCRYPTED PRIVATE KEY", self.private_key.read_bytes())
        serialized = self.authorization.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE KEY", serialized)
        self.assertNotIn(KEY_PASSWORD.decode("ascii"), serialized)

    def test_add_users_uses_argon2id_with_unique_salts(self) -> None:
        self.admin.add_user("one@example.com", "first-long-password")
        self.admin.add_user("two@example.com", "second-long-password")
        users = self.payload()["users"]
        first_hash = users[0]["password_hash"]
        second_hash = users[1]["password_hash"]
        self.assertTrue(first_hash.startswith("$parakh-argon2id$"))
        self.assertNotEqual(first_hash.split("$")[-2], second_hash.split("$")[-2])
        self.assertTrue(verify_password("first-long-password", first_hash))

    def test_reset_password_replaces_hash_and_signature(self) -> None:
        self.admin.add_user("person@example.com", "original-password")
        before = self.payload()["users"][0]["password_hash"]
        self.admin.reset_password("person@example.com", "replacement-password")
        after = self.payload()["users"][0]["password_hash"]
        self.assertNotEqual(before, after)
        self.assertFalse(verify_password("original-password", after))
        self.assertTrue(verify_password("replacement-password", after))

    def test_remove_user_generates_new_valid_file(self) -> None:
        self.admin.add_user("person@example.com", "original-password")
        self.admin.remove_user("person@example.com")
        self.assertEqual(self.payload()["users"], [])

    def test_user_and_manifest_expiry_can_be_updated(self) -> None:
        user_expiry = utc_now() + timedelta(days=10)
        manifest_expiry = utc_now() + timedelta(days=30)
        self.admin.add_user("person@example.com", "original-password")
        self.admin.set_user_expiry("person@example.com", user_expiry)
        self.admin.set_expiry(manifest_expiry)
        payload = self.payload()
        self.assertIsNotNone(payload["users"][0]["expires_at"])
        self.assertIsNotNone(payload["expires_at"])

    def test_list_users_does_not_disclose_password_hashes(self) -> None:
        self.admin.add_user("person@example.com", "original-password")
        listed = self.admin.list_users()
        self.assertNotIn("password_hash", listed[0])


if __name__ == "__main__":
    unittest.main()
