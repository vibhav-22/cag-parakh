from __future__ import annotations

import copy
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.auth_manifest import (
    ManifestError,
    hash_password,
    isoformat,
    load_private_key,
    parse_timestamp,
    read_envelope,
    sign_authorization,
    utc_now,
    verify_envelope,
    write_envelope,
)


class AuthorizationAdmin:
    """Edits and signs the deployable offline authorization file.

    The private key is supplied for each administrator invocation and is never
    copied into the authorization file or the employee application.
    """

    def __init__(self, authorization_file: Path, private_key_file: Path, password: bytes) -> None:
        self.authorization_file = authorization_file
        self.private_key = load_private_key(private_key_file, password)

    def _load(self) -> dict[str, Any]:
        authorization, _ = verify_envelope(
            read_envelope(self.authorization_file), self.private_key.public_key()
        )
        if not isinstance(authorization.get("users"), list):
            raise ManifestError("The authorization users field is invalid.")
        return copy.deepcopy(authorization)

    def _save(self, authorization: dict[str, Any]) -> dict[str, Any]:
        authorization["issued_at"] = isoformat(utc_now())
        envelope = sign_authorization(authorization, self.private_key)
        write_envelope(self.authorization_file, envelope)
        return envelope

    def initialize(self, expires_at: datetime) -> dict[str, Any]:
        if self.authorization_file.exists():
            raise FileExistsError("Refusing to overwrite an existing authorization file.")
        if expires_at <= utc_now():
            raise ValueError("Authorization expiry must be in the future.")
        return self._save(
            {
                "id": uuid.uuid4().hex,
                "issued_at": isoformat(utc_now()),
                "expires_at": isoformat(expires_at),
                "users": [],
            }
        )

    @staticmethod
    def _find(authorization: dict[str, Any], email: str) -> dict[str, Any]:
        normalized = email.strip().lower()
        for user in authorization["users"]:
            if user.get("email") == normalized:
                return user
        raise KeyError(f"No approved user exists for {normalized}.")

    def list_users(self) -> list[dict[str, Any]]:
        authorization = self._load()
        return [
            {
                "id": user.get("id"),
                "email": user.get("email"),
                "display_name": user.get("display_name"),
                "expires_at": user.get("expires_at"),
                "device_ids": user.get("device_ids", []),
            }
            for user in authorization["users"]
        ]

    def add_user(
        self,
        email: str,
        password: str,
        *,
        display_name: str = "",
        expires_at: datetime | None = None,
        device_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        authorization = self._load()
        normalized = email.strip().lower()
        if "@" not in normalized:
            raise ValueError("Enter a valid email address.")
        if any(user.get("email") == normalized for user in authorization["users"]):
            raise ValueError("That email address already exists.")
        auth_expiry = parse_timestamp(authorization["expires_at"], "expires_at")
        if expires_at is not None and expires_at > auth_expiry:
            raise ValueError("A user expiry cannot be later than the authorization expiry.")
        bindings = self._normalize_devices(device_ids or [])
        authorization["users"].append(
            {
                "id": uuid.uuid4().hex,
                "email": normalized,
                "display_name": display_name.strip() or normalized,
                "password_hash": hash_password(password),
                "expires_at": isoformat(expires_at) if expires_at is not None else None,
                "device_ids": bindings,
            }
        )
        authorization["users"].sort(key=lambda user: user["email"])
        self._save(authorization)
        return self._find(authorization, normalized)

    @staticmethod
    def _normalize_devices(device_ids: list[str]) -> list[str]:
        try:
            normalized = [str(uuid.UUID(value)) for value in device_ids]
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Device bindings must be UUIDs copied from Parakh diagnostics.") from exc
        if len(set(normalized)) != len(normalized):
            raise ValueError("Device bindings must not be duplicated.")
        return normalized

    def _mutate_user(
        self, email: str, change: Callable[[dict[str, Any], dict[str, Any]], None]
    ) -> dict[str, Any]:
        authorization = self._load()
        user = self._find(authorization, email)
        change(user, authorization)
        self._save(authorization)
        return user

    def remove_user(self, email: str) -> None:
        authorization = self._load()
        user = self._find(authorization, email)
        authorization["users"].remove(user)
        self._save(authorization)

    def reset_password(self, email: str, password: str) -> dict[str, Any]:
        return self._mutate_user(
            email, lambda user, _authorization: user.update(password_hash=hash_password(password))
        )

    def set_user_expiry(self, email: str, expires_at: datetime | None) -> dict[str, Any]:
        def change(user: dict[str, Any], authorization: dict[str, Any]) -> None:
            auth_expiry = parse_timestamp(authorization["expires_at"], "expires_at")
            if expires_at is not None and expires_at > auth_expiry:
                raise ValueError("A user expiry cannot be later than the authorization expiry.")
            user["expires_at"] = isoformat(expires_at) if expires_at is not None else None

        return self._mutate_user(email, change)

    def set_user_devices(self, email: str, device_ids: list[str]) -> dict[str, Any]:
        bindings = self._normalize_devices(device_ids)
        return self._mutate_user(
            email, lambda user, _authorization: user.update(device_ids=bindings)
        )

    def set_expiry(self, expires_at: datetime) -> dict[str, Any]:
        if expires_at <= utc_now():
            raise ValueError("Authorization expiry must be in the future.")
        authorization = self._load()
        for user in authorization["users"]:
            user_expiry = user.get("expires_at")
            if user_expiry is not None and parse_timestamp(
                user_expiry, f"users[{user.get('email')}].expires_at"
            ) > expires_at:
                raise ValueError("Authorization expiry cannot precede an existing user expiry.")
        authorization["expires_at"] = isoformat(expires_at)
        return self._save(authorization)

    def resign(self) -> dict[str, Any]:
        return self._save(self._load())
