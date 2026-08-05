from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_paths import is_packaged


SESSION_COOKIE = "parakh_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
VALIDATION_CACHE_SECONDS = 5 * 60

# How long a signed-in device keeps working while the authorization service
# cannot be reached at all. Losing the network must never look like a
# revocation: the first is a connectivity problem, the second is a decision.
OFFLINE_GRACE_SECONDS = 14 * 24 * 60 * 60
# Remaining grace at which the app starts warning the user to reconnect.
OFFLINE_WARN_SECONDS = 7 * 24 * 60 * 60
# While offline, retry no more often than this. Without it every request would
# attempt its own eight-second round trip and the app would crawl.
OFFLINE_RETRY_SECONDS = 60

# Expiry is measured against a clock the user controls, so winding it backwards
# would extend the offline grace window indefinitely and outlive a revocation.
# We remember the latest time ever seen and refuse to run behind it. The
# tolerance absorbs ordinary NTP and timezone corrections, which move the clock
# by seconds, without absorbing the days an attacker would need.
CLOCK_TOLERANCE_SECONDS = 5 * 60
# How far the high-water mark may advance in memory before it is written down.
# Without this, quitting the app would discard the mark and a restart would
# accept a rolled-back clock.
CLOCK_PERSIST_INTERVAL_SECONDS = 5 * 60

SESSION_FILE_NAME = ".sessions.json"
SESSION_FILE_VERSION = 1


class AccessDenied(Exception):
    """The authorization service answered, and the answer was no."""

    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class AuthorizationUnavailable(AccessDenied):
    """The authorization service could not be asked at all.

    Deliberately a subclass so callers that only care about "sign-in did not
    succeed" (the login path, the API error handler) keep working unchanged,
    while session revalidation can tell "could not ask" apart from "was
    refused" and hold the session open instead of signing the user out.
    """

    def __init__(self, detail: str, status_code: int = 503) -> None:
        super().__init__(detail, status_code)


@dataclass(frozen=True)
class AuthorizedUser:
    id: str
    email: str
    display_name: str

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "email": self.email, "display_name": self.display_name}


@dataclass
class _Session:
    user: AuthorizedUser
    remote_token: str
    # Local session expiry while the service is reachable. Refreshed on every
    # successful revalidation, never past the expiry the service issued.
    expires_at: float
    # Hard ceiling handed down by the service at login. Never extended locally.
    remote_expires_at: float
    # Last time the service actually confirmed this session.
    validated_at: float
    # When the service first became unreachable, or None while it is reachable.
    # This is what the offline grace window is measured from.
    offline_since: float | None = None
    # Earliest next revalidation attempt. Keeps an offline app from firing a
    # round trip per request.
    next_validation_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "user": {
                "id": self.user.id,
                "email": self.user.email,
                "display_name": self.user.display_name,
            },
            "remote_token": self.remote_token,
            "expires_at": self.expires_at,
            "remote_expires_at": self.remote_expires_at,
            "validated_at": self.validated_at,
            "offline_since": self.offline_since,
            "next_validation_at": self.next_validation_at,
        }

    @classmethod
    def from_json(cls, raw: Any) -> "_Session":
        """Rebuild a persisted session, rejecting anything malformed.

        Raises ValueError/TypeError/KeyError on bad input; the caller drops
        that session rather than trusting a half-parsed credential.
        """

        user = raw["user"]
        offline_since = raw.get("offline_since")
        return cls(
            user=AuthorizedUser(
                id=str(user["id"]),
                email=str(user["email"]),
                display_name=str(user["display_name"]),
            ),
            remote_token=str(raw["remote_token"]),
            expires_at=float(raw["expires_at"]),
            remote_expires_at=float(raw["remote_expires_at"]),
            validated_at=float(raw["validated_at"]),
            offline_since=None if offline_since is None else float(offline_since),
            next_validation_at=float(raw.get("next_validation_at") or 0.0),
        )


def _parse_expiry(value: Any) -> float:
    if not isinstance(value, str):
        raise AuthorizationUnavailable("The authorization service returned an invalid session.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationUnavailable(
            "The authorization service returned an invalid session."
        ) from exc
    return parsed.timestamp()


class AuthorizationClient:
    """HTTPS client for the central account and device approval service."""

    def __init__(self, base_url: str, timeout_seconds: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        parsed = urllib.parse.urlparse(self.base_url)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not is_loopback:
            raise RuntimeError("PARAKH_AUTH_URL must use HTTPS except for localhost development.")

    def _request(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str = "",
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8"))
                detail = str(error.get("detail") or "Sign-in was not accepted.")
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = "Sign-in was not accepted."
            # A 5xx, a timeout status, or rate limiting means the service could
            # not give us an answer. Only a deliberate 4xx refusal (401, 403,
            # 404, 400) is the service saying no to this account or device.
            if exc.code >= 500 or exc.code in {408, 429}:
                raise AuthorizationUnavailable(
                    "The authorization service is temporarily unavailable. "
                    "Check your connection and try again.",
                    503,
                ) from exc
            raise AccessDenied(detail, exc.code) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise AuthorizationUnavailable(
                "The authorization service is unavailable. Check your connection and try again."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AuthorizationUnavailable(
                "The authorization service returned an invalid response."
            ) from exc
        if not isinstance(result, dict):
            raise AuthorizationUnavailable("The authorization service returned an invalid response.")
        return result

    def login(
        self, email: str, password: str, device_id: str, device_name: str
    ) -> tuple[str, float, AuthorizedUser]:
        result = self._request(
            "/v1/login",
            body={
                "email": email,
                "password": password,
                "device_id": device_id,
                "device_name": device_name,
            },
        )
        token = result.get("access_token")
        raw_user = result.get("user")
        if not isinstance(token, str) or not token or not isinstance(raw_user, dict):
            raise AccessDenied("The authorization service returned an invalid session.", 503)
        try:
            user = AuthorizedUser(
                id=str(raw_user["id"]),
                email=str(raw_user["email"]),
                display_name=str(raw_user.get("display_name") or raw_user["email"]),
            )
        except KeyError as exc:
            raise AccessDenied("The authorization service returned an invalid user.", 503) from exc
        return token, _parse_expiry(result.get("expires_at")), user

    def validate(self, token: str) -> None:
        self._request("/v1/validate", token=token)


def load_device_id(data_directory: Path) -> str:
    """Load or create the random identifier used for device approval."""

    data_directory.mkdir(parents=True, exist_ok=True)
    path = data_directory / ".device-id"
    try:
        existing = path.read_text(encoding="ascii").strip()
        return str(uuid.UUID(existing))
    except (OSError, ValueError):
        pass
    device_id = str(uuid.uuid4())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(device_id, encoding="ascii")
    temporary.replace(path)
    return device_id


class SessionManager:
    """Keeps opaque browser sessions separate from central access tokens.

    Sessions survive a restart, and a network outage holds them open for
    OFFLINE_GRACE_SECONDS instead of signing the user out. Only the service
    explicitly refusing a session revokes it immediately.
    """

    def __init__(self, data_directory: Path) -> None:
        auth_url = os.getenv("PARAKH_AUTH_URL", "").strip()
        self.required = bool(auth_url) or is_packaged()
        self.configuration_error = self.required and not auth_url
        self.client = AuthorizationClient(auth_url) if auth_url else None
        self.device_id = load_device_id(data_directory)
        self.device_name = os.getenv("COMPUTERNAME", "").strip() or "Windows device"
        self._session_path = data_directory / SESSION_FILE_NAME
        self._sessions: dict[str, _Session] = {}
        self._clock_high_water = 0.0
        self._clock_persisted_at = 0.0
        self.clock_tampered = False
        self._lock = threading.Lock()
        self._load()

    # ----- persistence -------------------------------------------------

    def _load(self) -> None:
        """Restore sessions and the clock mark so a restart is not a reset."""

        # An installation with no working authorization config must not honour
        # anything left on disk from when it did have one.
        if self.configuration_error or not self.required:
            return
        try:
            raw = json.loads(self._session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(raw, dict) or raw.get("version") != SESSION_FILE_VERSION:
            return
        try:
            high_water = float(raw.get("clock_high_water") or 0.0)
        except (TypeError, ValueError):
            high_water = 0.0
        stored = raw.get("sessions")
        if not isinstance(stored, dict):
            stored = {}

        now = time.time()
        restored: dict[str, _Session] = {}
        # A clock behind the stored mark means time moved backwards while the
        # app was closed. Drop every session rather than honour the grace
        # window against a clock we no longer trust.
        rolled_back = now + CLOCK_TOLERANCE_SECONDS < high_water
        if not rolled_back:
            for token, payload in stored.items():
                try:
                    session = _Session.from_json(payload)
                except (KeyError, TypeError, ValueError):
                    continue  # a malformed entry is dropped, never half-trusted
                if self._is_alive(session, now):
                    restored[str(token)] = session

        with self._lock:
            self._sessions = restored
            self._clock_high_water = max(high_water, now)
            self._clock_persisted_at = high_water
            self.clock_tampered = rolled_back

    def _save_locked(self) -> None:
        """Write the session store atomically. Caller must hold the lock."""

        payload = {
            "version": SESSION_FILE_VERSION,
            "clock_high_water": self._clock_high_water,
            "sessions": {token: session.to_json() for token, session in self._sessions.items()},
        }
        temporary = self._session_path.with_suffix(".tmp")
        try:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass  # best effort; Windows ACLs do not map cleanly to modes
            temporary.replace(self._session_path)
        except OSError:
            # Losing persistence costs the user a re-login after restart. It
            # must never take down the running session.
            return
        self._clock_persisted_at = self._clock_high_water

    # ----- clock integrity ----------------------------------------------

    def _observe_clock_locked(self, now: float) -> bool:
        """Track the highest wall-clock time seen. False means it moved back."""

        if now + CLOCK_TOLERANCE_SECONDS < self._clock_high_water:
            return False
        if now > self._clock_high_water:
            self._clock_high_water = now
            if now - self._clock_persisted_at >= CLOCK_PERSIST_INTERVAL_SECONDS:
                self._save_locked()
        return True

    # ----- lifetime rules ----------------------------------------------

    @staticmethod
    def _is_alive(session: _Session, now: float) -> bool:
        if now >= session.remote_expires_at:
            return False  # the service's own ceiling is never extended locally
        if session.offline_since is not None:
            # While the service is unreachable the grace window governs, not
            # the short online TTL that we have had no chance to refresh.
            return now - session.offline_since <= OFFLINE_GRACE_SECONDS
        return now < session.expires_at

    def _revalidate_locked(self, session: _Session, now: float) -> bool:
        """Re-confirm a session with the service. False means revoked.

        Caller must hold the lock. Unreachable is not revoked: it starts (or
        continues) the offline grace window and schedules a later retry.
        """

        if self.client is None:
            return True
        try:
            self.client.validate(session.remote_token)
        except AuthorizationUnavailable:
            if session.offline_since is None:
                session.offline_since = now
            session.next_validation_at = now + OFFLINE_RETRY_SECONDS
            return True
        except AccessDenied:
            return False  # the service answered, and the answer was no
        session.validated_at = now
        session.offline_since = None
        session.next_validation_at = now + VALIDATION_CACHE_SECONDS
        session.expires_at = min(session.remote_expires_at, now + SESSION_TTL_SECONDS)
        return True

    # ----- public API ---------------------------------------------------

    def create(self, email: str, password: str) -> tuple[str, AuthorizedUser]:
        if self.configuration_error or self.client is None:
            raise AccessDenied(
                "This installation has not been connected to its authorization service.", 503
            )
        remote_token, remote_expiry, user = self.client.login(
            email.strip().lower(), password, self.device_id, self.device_name
        )
        now = time.time()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = _Session(
                user=user,
                remote_token=remote_token,
                expires_at=min(remote_expiry, now + SESSION_TTL_SECONDS),
                remote_expires_at=remote_expiry,
                validated_at=now,
                next_validation_at=now + VALIDATION_CACHE_SECONDS,
            )
            # A successful online sign-in re-anchors the clock: the service is
            # the authority on time, and it just accepted this session. Without
            # this, a user whose clock was wrong could never recover by signing
            # in again, which is the recovery path a wrong clock needs.
            self._clock_high_water = now
            self.clock_tampered = False
            self._save_locked()
        return token, user

    def authenticate(self, token: str) -> AuthorizedUser | None:
        if not self.required:
            return AuthorizedUser(id="development", email="local@parakh", display_name="Local user")
        if not token:
            return None
        now = time.time()
        with self._lock:
            if not self._observe_clock_locked(now):
                # Time moved backwards. Expiry and the grace window are both
                # measured against this clock, so nothing on disk can be
                # trusted until the service confirms the user again.
                self.clock_tampered = True
                self._sessions.clear()
                self._save_locked()
                return None
            session = self._sessions.get(token)
            if session is None:
                return None
            changed = False
            if now >= session.next_validation_at:
                if not self._revalidate_locked(session, now):
                    self._sessions.pop(token, None)
                    self._save_locked()
                    return None
                changed = True
            if not self._is_alive(session, now):
                self._sessions.pop(token, None)
                self._save_locked()
                return None
            if changed:
                self._save_locked()
            return session.user

    def connectivity(self, token: str) -> dict[str, Any]:
        """Report offline/grace state so the app can warn before access stops."""

        offline_state: dict[str, Any] = {
            "offline": False,
            "grace_expires_at": None,
            "grace_seconds_remaining": None,
            "grace_total_seconds": OFFLINE_GRACE_SECONDS,
            "warn": False,
            "clock_tampered": False,
        }
        if not self.required:
            return offline_state
        with self._lock:
            offline_state["clock_tampered"] = self.clock_tampered
            session = self._sessions.get(token) if token else None
            if session is None or session.offline_since is None:
                return offline_state
            grace_expires_at = session.offline_since + OFFLINE_GRACE_SECONDS
            remaining = max(0.0, grace_expires_at - time.time())
            clock_tampered = self.clock_tampered
        return {
            "offline": True,
            "grace_expires_at": grace_expires_at,
            "grace_seconds_remaining": remaining,
            "grace_total_seconds": OFFLINE_GRACE_SECONDS,
            "warn": remaining <= OFFLINE_WARN_SECONDS,
            "clock_tampered": clock_tampered,
        }

    def delete(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(token, None) is not None:
                self._save_locked()
