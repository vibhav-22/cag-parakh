from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LicenseError(Exception):
    def __init__(self, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


class LicenseStore:
    def __init__(self, database_path: Path, signing_secret: str, token_ttl: int = 8 * 60 * 60) -> None:
        if len(signing_secret.encode("utf-8")) < 32:
            raise RuntimeError("PARAKH_SIGNING_SECRET must contain at least 32 bytes.")
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.signing_secret = signing_secret.encode("utf-8")
        self.token_ttl = token_ttl
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    max_devices INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, device_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def create_user(
        self, email: str, password: str, display_name: str = "", max_devices: int = 1
    ) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise ValueError("Enter a valid email address.")
        if len(password) < 12:
            raise ValueError("Passwords must contain at least 12 characters.")
        if not 1 <= max_devices <= 20:
            raise ValueError("max_devices must be between 1 and 20.")
        salt = secrets.token_bytes(16)
        user_id = uuid.uuid4().hex
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, email, display_name, password_salt, password_hash,
                        active, max_devices, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_email,
                        display_name.strip() or normalized_email,
                        _b64(salt),
                        _b64(_password_hash(password, salt)),
                        max_devices,
                        _now_iso(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That email address already exists.") from exc
        return self.get_user(normalized_email) or {}

    def get_user(self, email: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, email, display_name, active, max_devices, created_at FROM users WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT u.id, u.email, u.display_name, u.active, u.max_devices,
                       u.created_at, COUNT(d.device_id) AS device_count
                FROM users u
                LEFT JOIN devices d ON d.user_id = u.id AND d.active = 1
                GROUP BY u.id
                ORDER BY u.email
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_user_active(self, email: str, active: bool) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET active = ? WHERE email = ?",
                (int(active), email.strip().lower()),
            )
        return cursor.rowcount > 0

    def list_devices(self, email: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT d.device_id, d.device_name, d.active, d.first_seen_at, d.last_seen_at
                FROM devices d JOIN users u ON u.id = d.user_id
                WHERE u.email = ? ORDER BY d.last_seen_at DESC
                """,
                (email.strip().lower(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_device_active(self, email: str, device_id: str, active: bool) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE devices SET active = ?
                WHERE user_id = (SELECT id FROM users WHERE email = ?) AND device_id = ?
                """,
                (int(active), email.strip().lower(), device_id),
            )
        return cursor.rowcount > 0

    def login(
        self, email: str, password: str, device_id: str, device_name: str
    ) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        with self._connection() as connection:
            user = connection.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
            if user is None:
                _password_hash(password, b"parakh-login-dummy")
                raise LicenseError("The email or password is incorrect.")
            supplied_hash = _password_hash(password, _unb64(user["password_salt"]))
            if not hmac.compare_digest(supplied_hash, _unb64(user["password_hash"])):
                raise LicenseError("The email or password is incorrect.")
            if not user["active"]:
                raise LicenseError("This account has been disabled.", 403)

            device = connection.execute(
                "SELECT * FROM devices WHERE user_id = ? AND device_id = ?",
                (user["id"], device_id),
            ).fetchone()
            if device is not None and not device["active"]:
                raise LicenseError("This device has been removed from the account.", 403)
            if device is None:
                active_devices = connection.execute(
                    "SELECT COUNT(*) FROM devices WHERE user_id = ? AND active = 1", (user["id"],)
                ).fetchone()[0]
                if active_devices >= user["max_devices"]:
                    raise LicenseError("This account has reached its approved device limit.", 403)
                now = _now_iso()
                connection.execute(
                    """
                    INSERT INTO devices (
                        user_id, device_id, device_name, active, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (user["id"], device_id, device_name[:120], now, now),
                )
            else:
                connection.execute(
                    "UPDATE devices SET device_name = ?, last_seen_at = ? WHERE user_id = ? AND device_id = ?",
                    (device_name[:120], _now_iso(), user["id"], device_id),
                )

        now = int(time.time())
        expires = now + self.token_ttl
        claims = {
            "sub": user["id"],
            "email": user["email"],
            "device_id": device_id,
            "iat": now,
            "exp": expires,
            "jti": uuid.uuid4().hex,
        }
        return {
            "access_token": self._sign(claims),
            "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user["display_name"],
            },
        }

    def _sign(self, claims: dict[str, Any]) -> str:
        payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _b64(hmac.new(self.signing_secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def validate(self, token: str) -> dict[str, Any]:
        try:
            payload, signature = token.split(".", 1)
            expected = hmac.new(self.signing_secret, payload.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(signature)):
                raise ValueError
            claims = json.loads(_unb64(payload).decode("utf-8"))
            if not isinstance(claims, dict) or int(claims["exp"]) <= int(time.time()):
                raise ValueError
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LicenseError("The session is invalid or has expired.") from exc

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT u.active AS user_active, d.active AS device_active
                FROM users u JOIN devices d ON d.user_id = u.id
                WHERE u.id = ? AND d.device_id = ?
                """,
                (claims["sub"], claims["device_id"]),
            ).fetchone()
        if row is None or not row["user_active"] or not row["device_active"]:
            raise LicenseError("This account or device is no longer approved.", 403)
        return claims
