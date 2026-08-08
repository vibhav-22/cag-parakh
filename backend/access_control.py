from __future__ import annotations

import ctypes
import json
import os
import secrets
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .app_paths import is_packaged
from .auth_manifest import (
    ManifestError,
    load_public_key,
    parse_timestamp,
    read_envelope,
    validate_password_hash,
    verify_envelope,
    verify_password,
)


SESSION_COOKIE = "parakh_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60
CLOCK_TOLERANCE_SECONDS = 5 * 60
STATE_FILE_NAME = ".auth-state.dat"
STATE_FILE_VERSION = 2


class AccessDenied(Exception):
    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class AuthorizationFileError(AccessDenied):
    pass


@dataclass(frozen=True)
class AuthorizedUser:
    id: str
    email: str
    display_name: str

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "email": self.email, "display_name": self.display_name}


@dataclass(frozen=True)
class _ApprovedUser:
    user: AuthorizedUser
    password_hash: str
    expires_at: float | None
    device_ids: frozenset[str]


@dataclass(frozen=True)
class _Authorization:
    users: dict[str, _ApprovedUser]
    digest: str
    expires_at: float


@dataclass
class _Session:
    user: AuthorizedUser
    expires_at: float
    authorization_digest: str

    def to_json(self) -> dict[str, Any]:
        return {
            "user": self.user.public_dict(),
            "expires_at": self.expires_at,
            "authorization_digest": self.authorization_digest,
        }

    @classmethod
    def from_json(cls, raw: Any) -> "_Session":
        user = raw["user"]
        return cls(
            user=AuthorizedUser(
                id=str(user["id"]),
                email=str(user["email"]),
                display_name=str(user["display_name"]),
            ),
            expires_at=float(raw["expires_at"]),
            authorization_digest=str(raw["authorization_digest"]),
        )


class SessionProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class DpapiSessionProtector:
    """Protect session and lockout state for the current Windows user."""

    _DESCRIPTION = "Parakh local authentication state"
    _UI_FORBIDDEN = 0x1

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is not available on this operating system.")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(self._Blob),
            wintypes.LPCWSTR,
            ctypes.POINTER(self._Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(self._Blob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(self._Blob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(self._Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(self._Blob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @classmethod
    def _input_blob(cls, value: bytes) -> tuple["DpapiSessionProtector._Blob", Any]:
        buffer = ctypes.create_string_buffer(value)
        blob = cls._Blob(
            len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        return blob, buffer

    def protect(self, value: bytes) -> bytes:
        source, source_buffer = self._input_blob(value)
        output = self._Blob()
        # source_buffer must remain alive across the native call.
        _ = source_buffer
        if not self._crypt32.CryptProtectData(
            ctypes.byref(source),
            self._DESCRIPTION,
            None,
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(), "Windows could not protect the session state.")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def unprotect(self, value: bytes) -> bytes:
        source, source_buffer = self._input_blob(value)
        output = self._Blob()
        description = wintypes.LPWSTR()
        _ = source_buffer
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(), "Windows could not unprotect the session state.")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if description:
                self._kernel32.LocalFree(description)
            self._kernel32.LocalFree(output.pbData)


class TestSessionProtector:
    """Deterministic protector for unit tests; never selected by production code."""

    _PREFIX = b"PARAKH-TEST-STATE\0"

    def protect(self, value: bytes) -> bytes:
        return self._PREFIX + value

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(self._PREFIX):
            raise ValueError("State was not protected by the test protector.")
        return value[len(self._PREFIX) :]


def load_device_id(data_directory: Path) -> str:
    data_directory.mkdir(parents=True, exist_ok=True)
    path = data_directory / ".device-id"
    try:
        return str(uuid.UUID(path.read_text(encoding="ascii").strip()))
    except (OSError, ValueError):
        pass
    device_id = str(uuid.uuid4())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(device_id, encoding="ascii")
    temporary.replace(path)
    return device_id


def _authorization_path(data_directory: Path) -> Path:
    configured = os.getenv("PARAKH_AUTH_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # The installer sets the explicit path. This fallback keeps direct backend
    # use deterministic and still places the file under the mutable app root.
    return data_directory.parent / "authorization" / "authorization.json"


def _public_key_path() -> Path | None:
    configured = os.getenv("PARAKH_AUTH_PUBLIC_KEY_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else None


class SessionManager:
    """Offline authorization, throttling, and Windows-protected sessions."""

    def __init__(
        self,
        data_directory: Path,
        *,
        authorization_file: Path | None = None,
        public_key_file: Path | None = None,
        protector: SessionProtector | None = None,
    ) -> None:
        self.data_directory = data_directory
        self.data_directory.mkdir(parents=True, exist_ok=True)
        packaged = is_packaged()
        requested_bypass = os.getenv("PARAKH_DEV_AUTH_BYPASS", "").strip() == "1"
        self.required = packaged or not requested_bypass
        self.development_bypass = requested_bypass and not packaged
        self.authorization_file = authorization_file or _authorization_path(data_directory)
        self.public_key_file = public_key_file or _public_key_path()
        self.device_id = load_device_id(data_directory)
        self._state_path = data_directory / STATE_FILE_NAME
        self._sessions: dict[str, _Session] = {}
        self._attempts: dict[str, dict[str, Any]] = {}
        self._clock_high_water = 0.0
        self.clock_tampered = False
        self.last_authorization_error: str | None = None
        self._lock = threading.RLock()
        self._protector = protector
        if self.required and self._protector is None and os.name == "nt":
            try:
                self._protector = DpapiSessionProtector()
            except (OSError, RuntimeError):
                self._protector = None
        self.configuration_error = self.required and (
            self.public_key_file is None or self._protector is None
        )
        self._load_state()

    def _load_authorization(self, now: float | None = None) -> _Authorization:
        if self.public_key_file is None:
            raise AuthorizationFileError("The authorization public key is missing.", 503)
        now = time.time() if now is None else now
        try:
            public_key = load_public_key(self.public_key_file)
            authorization, digest = verify_envelope(read_envelope(self.authorization_file), public_key)
            if set(authorization) != {"id", "issued_at", "expires_at", "users"}:
                raise ManifestError("The signed authorization payload has invalid fields.")
            authorization_id = authorization["id"]
            if not isinstance(authorization_id, str) or not authorization_id.strip():
                raise ManifestError("The authorization id is invalid.")
            issued_at = parse_timestamp(authorization["issued_at"], "issued_at").timestamp()
            expires_at = parse_timestamp(authorization["expires_at"], "expires_at").timestamp()
            if expires_at <= issued_at:
                raise ManifestError("The authorization expiry must follow its issue date.")
            if now + CLOCK_TOLERANCE_SECONDS < issued_at:
                raise AuthorizationFileError(
                    "The authorization file is not valid for this device clock.", 403
                )
            if now >= expires_at:
                raise AuthorizationFileError("The authorization file has expired.", 403)
            raw_users = authorization["users"]
            if not isinstance(raw_users, list):
                raise ManifestError("The authorization users field is invalid.")
            users: dict[str, _ApprovedUser] = {}
            ids: set[str] = set()
            for raw in raw_users:
                if not isinstance(raw, dict) or set(raw) != {
                    "id",
                    "email",
                    "display_name",
                    "password_hash",
                    "expires_at",
                    "device_ids",
                }:
                    raise ManifestError("An approved user record has invalid fields.")
                user_id = raw["id"]
                email = raw["email"]
                display_name = raw["display_name"]
                if not isinstance(user_id, str) or not user_id.strip() or user_id in ids:
                    raise ManifestError("An approved user id is invalid or duplicated.")
                if (
                    not isinstance(email, str)
                    or email != email.strip().lower()
                    or "@" not in email
                    or email in users
                ):
                    raise ManifestError("An approved user email is invalid or duplicated.")
                if not isinstance(display_name, str) or not display_name.strip():
                    raise ManifestError("An approved user display name is invalid.")
                validate_password_hash(raw["password_hash"])
                user_expiry = raw["expires_at"]
                parsed_user_expiry = (
                    None
                    if user_expiry is None
                    else parse_timestamp(user_expiry, f"users[{email}].expires_at").timestamp()
                )
                device_ids = raw["device_ids"]
                if not isinstance(device_ids, list):
                    raise ManifestError("An approved user's device bindings are invalid.")
                try:
                    normalized_devices = [str(uuid.UUID(value)) for value in device_ids]
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ManifestError("An approved user's device binding is invalid.") from exc
                if len(set(normalized_devices)) != len(normalized_devices):
                    raise ManifestError("An approved user's device bindings are duplicated.")
                users[email] = _ApprovedUser(
                    user=AuthorizedUser(user_id, email, display_name.strip()),
                    password_hash=raw["password_hash"],
                    expires_at=parsed_user_expiry,
                    device_ids=frozenset(normalized_devices),
                )
                ids.add(user_id)
        except AuthorizationFileError as exc:
            self.last_authorization_error = exc.detail
            raise
        except ManifestError as exc:
            detail = str(exc)
            self.last_authorization_error = detail
            raise AuthorizationFileError(detail, 503) from exc
        self.last_authorization_error = None
        return _Authorization(users=users, digest=digest, expires_at=expires_at)

    def _load_state(self) -> None:
        if not self.required or self._protector is None:
            return
        try:
            decoded = self._protector.unprotect(self._state_path.read_bytes())
            raw = json.loads(decoded.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or raw.get("version") != STATE_FILE_VERSION:
            return
        try:
            high_water = float(raw.get("clock_high_water") or 0.0)
        except (TypeError, ValueError):
            high_water = 0.0
        now = time.time()
        rolled_back = now + CLOCK_TOLERANCE_SECONDS < high_water
        sessions: dict[str, _Session] = {}
        if not rolled_back and isinstance(raw.get("sessions"), dict):
            for token, payload in raw["sessions"].items():
                try:
                    session = _Session.from_json(payload)
                except (KeyError, TypeError, ValueError):
                    continue
                if session.expires_at > now:
                    sessions[str(token)] = session
        attempts = raw.get("attempts")
        with self._lock:
            self._sessions = sessions
            self._attempts = attempts if isinstance(attempts, dict) else {}
            self._clock_high_water = max(high_water, now)
            self.clock_tampered = rolled_back

    def _save_locked(self) -> None:
        if self._protector is None:
            return
        payload = {
            "version": STATE_FILE_VERSION,
            "clock_high_water": self._clock_high_water,
            "sessions": {token: value.to_json() for token, value in self._sessions.items()},
            "attempts": self._attempts,
        }
        try:
            protected = self._protector.protect(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            )
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_bytes(protected)
            temporary.replace(self._state_path)
        except (OSError, ValueError):
            # The in-memory session remains valid, but an unprotected fallback
            # is never written. A restart will require another sign-in.
            return

    def _observe_clock_locked(self, now: float) -> bool:
        if now + CLOCK_TOLERANCE_SECONDS < self._clock_high_water:
            self.clock_tampered = True
            self._sessions.clear()
            self._save_locked()
            return False
        if now > self._clock_high_water:
            self._clock_high_water = now
        return True

    def _check_lockout_locked(self, email: str, now: float) -> None:
        record = self._attempts.get(email)
        if not isinstance(record, dict):
            return
        try:
            locked_until = float(record.get("locked_until") or 0.0)
        except (TypeError, ValueError):
            locked_until = 0.0
        if locked_until > now:
            remaining = max(1, int(locked_until - now + 0.999))
            raise AccessDenied(
                f"Too many sign-in attempts. Try again in {remaining} seconds.", 429
            )

    def _record_failure_locked(self, email: str, now: float) -> None:
        record = self._attempts.get(email)
        failures: list[float] = []
        if isinstance(record, dict) and isinstance(record.get("failures"), list):
            for value in record["failures"]:
                try:
                    timestamp = float(value)
                except (TypeError, ValueError):
                    continue
                if timestamp > now - LOGIN_WINDOW_SECONDS:
                    failures.append(timestamp)
        failures.append(now)
        locked_until = now + LOGIN_LOCKOUT_SECONDS if len(failures) >= LOGIN_FAILURE_LIMIT else 0.0
        self._attempts[email] = {"failures": failures, "locked_until": locked_until}
        self._save_locked()

    @staticmethod
    def _approved_now(approved: _ApprovedUser, now: float, device_id: str) -> bool:
        if approved.expires_at is not None and now >= approved.expires_at:
            return False
        return not approved.device_ids or device_id in approved.device_ids

    def create(self, email: str, password: str) -> tuple[str, AuthorizedUser]:
        if self.development_bypass:
            user = AuthorizedUser("development", "local@parakh", "Local user")
            return secrets.token_urlsafe(32), user
        if self.configuration_error:
            raise AuthorizationFileError(
                "This installation is missing its authorization configuration.", 503
            )
        normalized_email = email.strip().lower()
        now = time.time()
        with self._lock:
            if not self._observe_clock_locked(now):
                raise AccessDenied("The device clock moved backwards. Correct it and try again.", 403)
            self._check_lockout_locked(normalized_email, now)
        authorization = self._load_authorization(now)
        approved = authorization.users.get(normalized_email)
        # A real hash is still computed for unknown users, so account existence
        # cannot be inferred from a much faster response.
        candidate_hash = approved.password_hash if approved else next(
            (user.password_hash for user in authorization.users.values()), ""
        )
        password_ok = bool(candidate_hash) and verify_password(password, candidate_hash)
        approved_now = approved is not None and self._approved_now(approved, now, self.device_id)
        if not password_ok or not approved_now:
            with self._lock:
                self._record_failure_locked(normalized_email, now)
            raise AccessDenied("The email or password is incorrect.")
        assert approved is not None
        expires_at = min(
            now + SESSION_TTL_SECONDS,
            authorization.expires_at,
            approved.expires_at if approved.expires_at is not None else float("inf"),
        )
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._attempts.pop(normalized_email, None)
            self._sessions[token] = _Session(approved.user, expires_at, authorization.digest)
            self.clock_tampered = False
            self._save_locked()
        return token, approved.user

    def authenticate(self, token: str) -> AuthorizedUser | None:
        if self.development_bypass:
            return AuthorizedUser("development", "local@parakh", "Local user")
        if not token or self.configuration_error:
            return None
        now = time.time()
        with self._lock:
            if not self._observe_clock_locked(now):
                return None
            session = self._sessions.get(token)
            if session is None or now >= session.expires_at:
                self._sessions.pop(token, None)
                self._save_locked()
                return None
        try:
            authorization = self._load_authorization(now)
        except AuthorizationFileError:
            with self._lock:
                self._sessions.clear()
                self._save_locked()
            return None
        approved = authorization.users.get(session.user.email)
        if (
            authorization.digest != session.authorization_digest
            or approved is None
            or approved.user.id != session.user.id
            or not self._approved_now(approved, now, self.device_id)
        ):
            with self._lock:
                self._sessions.pop(token, None)
                self._save_locked()
            return None
        return session.user

    def connectivity(self, token: str) -> dict[str, Any]:
        # Retain the existing response shape while replacing its network-grace
        # meaning with the status of the entirely local authorization file.
        return {
            "offline": False,
            "grace_expires_at": None,
            "grace_seconds_remaining": None,
            "grace_total_seconds": 0,
            "warn": bool(self.last_authorization_error),
            "clock_tampered": self.clock_tampered,
            "authorization_error": self.last_authorization_error,
        }

    def delete(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(token, None) is not None:
                self._save_locked()
