from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from backend.access_control import (
    CLOCK_PERSIST_INTERVAL_SECONDS,
    CLOCK_TOLERANCE_SECONDS,
    OFFLINE_GRACE_SECONDS,
    OFFLINE_RETRY_SECONDS,
    OFFLINE_WARN_SECONDS,
    AccessDenied,
    AuthorizationUnavailable,
    AuthorizedUser,
    AuthorizationClient,
    SessionManager,
    load_device_id,
)
from backend.app_paths import data_dir


class DeviceIdentityTests(unittest.TestCase):
    def test_device_identity_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = load_device_id(path)
            second = load_device_id(path)
            self.assertEqual(first, second)
            self.assertNotIn(first, str(path))

    def test_packaged_data_uses_local_app_data(self) -> None:
        with patch.dict(
            os.environ,
            {"PARAKH_PACKAGED": "1", "LOCALAPPDATA": r"C:\Users\test\AppData\Local"},
            clear=False,
        ):
            with patch.dict(os.environ, {"PARAKH_DATA_DIR": ""}, clear=False):
                self.assertEqual(
                    data_dir(), Path(r"C:\Users\test\AppData\Local") / "Parakh" / "data"
                )


class SessionManagerTests(unittest.TestCase):
    def test_development_mode_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ, {"PARAKH_AUTH_URL": "", "PARAKH_PACKAGED": ""}, clear=False
            ):
                manager = SessionManager(Path(directory))
            user = manager.authenticate("")
            self.assertFalse(manager.required)
            self.assertEqual(user.email, "local@parakh")

    def test_packaged_mode_fails_closed_without_authorization_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ, {"PARAKH_AUTH_URL": "", "PARAKH_PACKAGED": "1"}, clear=False
            ):
                manager = SessionManager(Path(directory))
            self.assertTrue(manager.required)
            self.assertIsNone(manager.authenticate(""))
            with self.assertRaisesRegex(Exception, "authorization service"):
                manager.create("person@example.com", "password")

    def test_remote_http_requires_loopback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must use HTTPS"):
            AuthorizationClient("http://accounts.example.com")

    def test_browser_session_is_opaque_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"PARAKH_AUTH_URL": "https://accounts.example.com", "PARAKH_PACKAGED": "1"},
                clear=False,
            ):
                manager = SessionManager(Path(directory))

            user = AuthorizedUser("user-1", "person@example.com", "Person")
            manager.client.login = lambda *args: ("remote-secret", time.time() + 3600, user)
            token, signed_in = manager.create("person@example.com", "correct-password")
            self.assertEqual(signed_in, user)
            self.assertNotIn("remote-secret", token)
            self.assertEqual(manager.authenticate(token), user)
            manager.delete(token)
            self.assertIsNone(manager.authenticate(token))


USER = AuthorizedUser("user-1", "person@example.com", "Person")


def _manager(directory: str, remote_ttl: float = 3600.0) -> SessionManager:
    with patch.dict(
        os.environ,
        {"PARAKH_AUTH_URL": "https://accounts.example.com", "PARAKH_PACKAGED": "1"},
        clear=False,
    ):
        manager = SessionManager(Path(directory))
    manager.client.login = lambda *args: ("remote-secret", time.time() + remote_ttl, USER)
    return manager


def _force_revalidation(manager: SessionManager, token: str) -> None:
    """Make the next authenticate() call actually hit the service."""

    manager._sessions[token].next_validation_at = 0.0


class OfflineGraceTests(unittest.TestCase):
    """The bug this suite exists for: a network failure is not a revocation."""

    def test_unreachable_service_keeps_the_session_alive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")

            def unreachable(_token: str) -> None:
                raise AuthorizationUnavailable("network down")

            manager.client.validate = unreachable
            _force_revalidation(manager, token)

            # Previously this signed the user out mid-batch.
            self.assertEqual(manager.authenticate(token), USER)
            self.assertTrue(manager.connectivity(token)["offline"])

    def test_refused_service_revokes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")

            def refused(_token: str) -> None:
                raise AccessDenied("This device was deactivated.", 403)

            manager.client.validate = refused
            _force_revalidation(manager, token)

            self.assertIsNone(manager.authenticate(token))

    def test_server_error_is_treated_as_unreachable_not_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")

            def crashing(_token: str) -> None:
                raise AuthorizationUnavailable("bad gateway", 503)

            manager.client.validate = crashing
            _force_revalidation(manager, token)

            self.assertEqual(manager.authenticate(token), USER)

    def test_session_dies_once_the_grace_window_runs_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=OFFLINE_GRACE_SECONDS * 4)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: (_ for _ in ()).throw(
                AuthorizationUnavailable("network down")
            )
            _force_revalidation(manager, token)
            self.assertEqual(manager.authenticate(token), USER)

            # Backdate the outage past the grace window.
            manager._sessions[token].offline_since = time.time() - OFFLINE_GRACE_SECONDS - 1
            self.assertIsNone(manager.authenticate(token))

    def test_offline_retry_is_throttled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            attempts = []

            def unreachable(_token: str) -> None:
                attempts.append(time.time())
                raise AuthorizationUnavailable("network down")

            manager.client.validate = unreachable
            _force_revalidation(manager, token)
            for _ in range(5):
                manager.authenticate(token)

            # One attempt, then backed off. Without this an offline app fires a
            # round trip per request and grinds to a halt.
            self.assertEqual(len(attempts), 1)
            self.assertGreaterEqual(
                manager._sessions[token].next_validation_at, time.time() + OFFLINE_RETRY_SECONDS - 5
            )

    def test_reconnecting_clears_the_offline_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: (_ for _ in ()).throw(
                AuthorizationUnavailable("network down")
            )
            _force_revalidation(manager, token)
            manager.authenticate(token)
            self.assertTrue(manager.connectivity(token)["offline"])

            manager.client.validate = lambda _t: None
            _force_revalidation(manager, token)
            self.assertEqual(manager.authenticate(token), USER)
            self.assertFalse(manager.connectivity(token)["offline"])

    def test_connectivity_warns_near_the_end_of_grace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=OFFLINE_GRACE_SECONDS * 4)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: (_ for _ in ()).throw(
                AuthorizationUnavailable("network down")
            )
            _force_revalidation(manager, token)
            manager.authenticate(token)

            self.assertFalse(manager.connectivity(token)["warn"])
            manager._sessions[token].offline_since = (
                time.time() - OFFLINE_GRACE_SECONDS + OFFLINE_WARN_SECONDS - 60
            )
            state = manager.connectivity(token)
            self.assertTrue(state["warn"])
            self.assertGreater(state["grace_seconds_remaining"], 0)

    def test_remote_expiry_is_never_extended_by_grace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=60.0)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: (_ for _ in ()).throw(
                AuthorizationUnavailable("network down")
            )
            _force_revalidation(manager, token)
            self.assertEqual(manager.authenticate(token), USER)

            # Past the ceiling the service itself issued: grace must not save it.
            manager._sessions[token].remote_expires_at = time.time() - 1
            self.assertIsNone(manager.authenticate(token))


class ClockIntegrityTests(unittest.TestCase):
    """Expiry is measured against a clock the user controls."""

    def test_winding_the_clock_back_revokes_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=OFFLINE_GRACE_SECONDS * 4)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: None
            self.assertEqual(manager.authenticate(token), USER)

            # Same effect as the user setting the system clock back a year.
            manager._clock_high_water = time.time() + 365 * 24 * 60 * 60
            self.assertIsNone(manager.authenticate(token))
            self.assertTrue(manager.connectivity("")["clock_tampered"])

    def test_rollback_cannot_extend_the_offline_grace_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=OFFLINE_GRACE_SECONDS * 4)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: (_ for _ in ()).throw(
                AuthorizationUnavailable("network down")
            )
            _force_revalidation(manager, token)
            manager.authenticate(token)

            # Offline and nearly out of grace, so wind the clock back to reset
            # the countdown. The high-water mark must refuse it.
            manager._sessions[token].offline_since = time.time() - OFFLINE_GRACE_SECONDS + 60
            manager._clock_high_water = time.time() + OFFLINE_GRACE_SECONDS
            self.assertIsNone(manager.authenticate(token))

    def test_ordinary_clock_correction_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: None

            # An NTP or timezone correction moves the clock by seconds.
            manager._clock_high_water = time.time() + (CLOCK_TOLERANCE_SECONDS / 2)
            self.assertEqual(manager.authenticate(token), USER)

    def test_signing_in_again_recovers_from_a_wrong_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            manager._clock_high_water = time.time() + 365 * 24 * 60 * 60
            self.assertIsNone(manager.authenticate(token))
            self.assertTrue(manager.clock_tampered)

            # The service is the authority on time. A successful online
            # sign-in re-anchors it, which is the recovery path for a laptop
            # with a dead CMOS battery.
            recovered_token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: None
            self.assertEqual(manager.authenticate(recovered_token), USER)
            self.assertFalse(manager.clock_tampered)

    def test_the_mark_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=OFFLINE_GRACE_SECONDS * 4)
            token, _ = manager.create("person@example.com", "password")
            future = time.time() + 365 * 24 * 60 * 60
            with manager._lock:
                manager._clock_high_water = future
                manager._save_locked()

            # Quitting the app must not discard the mark, or a restart would
            # happily accept a rolled-back clock.
            restarted = _manager(directory)
            self.assertTrue(restarted.clock_tampered)
            self.assertIsNone(restarted.authenticate(token))

    def test_the_mark_is_persisted_without_an_explicit_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            manager.client.validate = lambda _t: None

            # Advance past the persist interval; authenticate must write it out.
            manager._clock_persisted_at = time.time() - CLOCK_PERSIST_INTERVAL_SECONDS - 1
            manager.authenticate(token)

            raw = json.loads((Path(directory) / ".sessions.json").read_text(encoding="utf-8"))
            self.assertGreater(raw["clock_high_water"], 0)
            self.assertEqual(raw["version"], 1)


class ResponseClassificationTests(unittest.TestCase):
    """The fix itself: which HTTP outcomes mean "refused" vs "could not ask"."""

    def _validate_raising(self, error: Exception) -> None:
        client = AuthorizationClient("https://accounts.example.com")
        with patch("backend.access_control.urllib.request.urlopen", side_effect=error):
            client.validate("remote-secret")

    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://accounts.example.com/v1/validate",
            code,
            "error",
            {},  # type: ignore[arg-type]
            io.BytesIO(b'{"detail":"nope"}'),
        )

    def test_deliberate_refusals_are_access_denied(self) -> None:
        for code in (400, 401, 403, 404):
            with self.subTest(code=code):
                with self.assertRaises(AccessDenied) as caught:
                    self._validate_raising(self._http_error(code))
                self.assertNotIsInstance(caught.exception, AuthorizationUnavailable)

    def test_server_side_failures_are_unavailable(self) -> None:
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                with self.assertRaises(AuthorizationUnavailable):
                    self._validate_raising(self._http_error(code))

    def test_transport_failures_are_unavailable(self) -> None:
        for error in (
            urllib.error.URLError("dns failure"),
            TimeoutError("timed out"),
            OSError("network unreachable"),
        ):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(AuthorizationUnavailable):
                    self._validate_raising(error)

    def test_unavailable_is_still_an_access_denied_for_the_login_path(self) -> None:
        # app.py maps AccessDenied to an HTTP response; the subclass must not
        # slip past that handler.
        self.assertTrue(issubclass(AuthorizationUnavailable, AccessDenied))


class SessionPersistenceTests(unittest.TestCase):
    def test_session_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")

            restarted = _manager(directory)
            restarted.client.validate = lambda _t: None
            self.assertEqual(restarted.authenticate(token), USER)

    def test_signing_out_does_not_survive_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")
            manager.delete(token)

            restarted = _manager(directory)
            self.assertIsNone(restarted.authenticate(token))

    def test_expired_sessions_are_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory, remote_ttl=1.0)
            token, _ = manager.create("person@example.com", "password")
            manager._sessions[token].remote_expires_at = time.time() - 1
            with manager._lock:
                manager._save_locked()

            restarted = _manager(directory)
            self.assertIsNone(restarted.authenticate(token))

    def test_corrupt_session_file_is_ignored_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".sessions.json").write_text("{not json", encoding="utf-8")
            manager = _manager(directory)
            self.assertIsNone(manager.authenticate("anything"))

    def test_sessions_are_not_restored_without_authorization_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            token, _ = manager.create("person@example.com", "password")

            # Same data folder, but the installation is no longer connected to
            # its service. Stale credentials on disk must not be honoured.
            with patch.dict(
                os.environ, {"PARAKH_AUTH_URL": "", "PARAKH_PACKAGED": "1"}, clear=False
            ):
                unconfigured = SessionManager(Path(directory))
            self.assertIsNone(unconfigured.authenticate(token))
