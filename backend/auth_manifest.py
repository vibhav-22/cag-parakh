from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidKey, InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "Ed25519"
PASSWORD_SCHEME = "parakh-argon2id"
ARGON2_MEMORY_KIB = 64 * 1024
ARGON2_ITERATIONS = 3
ARGON2_LANES = 4
ARGON2_LENGTH = 32


class ManifestError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware date is required.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} is invalid.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ManifestError(f"{field} is invalid.") from exc


def canonical_signed_bytes(schema_version: int, authorization: dict[str, Any]) -> bytes:
    return json.dumps(
        {"authorization": authorization, "schema_version": schema_version},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        loaded = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise ManifestError("The authorization public key is missing or invalid.") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise ManifestError("The authorization public key is not an Ed25519 key.")
    return loaded


def load_private_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=password)
    except (OSError, ValueError, TypeError) as exc:
        raise ManifestError("The signing key or its passphrase is invalid.") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ManifestError("The signing key is not an Ed25519 key.")
    return loaded


def generate_key_pair(private_path: Path, public_path: Path, password: bytes) -> None:
    if len(password) < 12:
        raise ValueError("The signing-key passphrase must contain at least 12 characters.")
    if private_path.exists() or public_path.exists():
        raise FileExistsError("Refusing to overwrite an existing signing key.")
    private_key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    _atomic_write(private_path, private_bytes)
    try:
        os.chmod(private_path, 0o600)
    except OSError:
        pass
    _atomic_write(public_path, public_bytes)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Passwords must contain at least 12 characters.")
    salt = salt or os.urandom(16)
    if len(salt) < 16:
        raise ValueError("Password salts must contain at least 16 bytes.")
    derived = Argon2id(
        salt=salt,
        length=ARGON2_LENGTH,
        iterations=ARGON2_ITERATIONS,
        lanes=ARGON2_LANES,
        memory_cost=ARGON2_MEMORY_KIB,
    ).derive(password.encode("utf-8"))
    return (
        f"${PASSWORD_SCHEME}$v=1$"
        f"m={ARGON2_MEMORY_KIB},t={ARGON2_ITERATIONS},p={ARGON2_LANES}$"
        f"{b64encode(salt)}${b64encode(derived)}"
    )


def _password_parts(encoded: Any) -> tuple[int, int, int, bytes, bytes]:
    if not isinstance(encoded, str):
        raise ManifestError("A user password hash is invalid.")
    parts = encoded.split("$")
    if len(parts) != 6 or parts[:3] != ["", PASSWORD_SCHEME, "v=1"]:
        raise ManifestError("A user password hash is not an approved Argon2id value.")
    try:
        parameters = dict(item.split("=", 1) for item in parts[3].split(","))
        memory = int(parameters["m"])
        iterations = int(parameters["t"])
        lanes = int(parameters["p"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError("A user password hash has invalid parameters.") from exc
    if set(parameters) != {"m", "t", "p"}:
        raise ManifestError("A user password hash has invalid parameters.")
    # Never accept a manifest that weakens the pilot's password policy. Upper
    # bounds prevent a signed-but-malformed file from exhausting a laptop.
    if not (ARGON2_MEMORY_KIB <= memory <= 256 * 1024):
        raise ManifestError("A user password hash has an unsafe memory cost.")
    if not (ARGON2_ITERATIONS <= iterations <= 10) or not (1 <= lanes <= 16):
        raise ManifestError("A user password hash has unsafe work parameters.")
    salt = b64decode(parts[4], "password salt")
    expected = b64decode(parts[5], "password digest")
    if len(salt) < 16 or len(expected) != ARGON2_LENGTH:
        raise ManifestError("A user password hash has invalid lengths.")
    return memory, iterations, lanes, salt, expected


def validate_password_hash(encoded: Any) -> None:
    _password_parts(encoded)


def verify_password(password: str, encoded: str) -> bool:
    try:
        memory, iterations, lanes, salt, expected = _password_parts(encoded)
        actual = Argon2id(
            salt=salt,
            length=len(expected),
            iterations=iterations,
            lanes=lanes,
            memory_cost=memory,
        ).derive(password.encode("utf-8"))
    except (ManifestError, InvalidKey):
        return False
    return hmac.compare_digest(actual, expected)


def sign_authorization(
    authorization: dict[str, Any], private_key: Ed25519PrivateKey
) -> dict[str, Any]:
    payload = canonical_signed_bytes(SCHEMA_VERSION, authorization)
    return {
        "schema_version": SCHEMA_VERSION,
        "authorization": authorization,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": public_key_id(private_key.public_key()),
            "value": b64encode(private_key.sign(payload)),
        },
    }


def verify_envelope(envelope: Any, public_key: Ed25519PublicKey) -> tuple[dict[str, Any], str]:
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema_version",
        "authorization",
        "signature",
    }:
        raise ManifestError("The authorization file structure is invalid.")
    if envelope["schema_version"] != SCHEMA_VERSION or not isinstance(
        envelope["authorization"], dict
    ):
        raise ManifestError("The authorization file version is not supported.")
    signature = envelope["signature"]
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "key_id", "value"}:
        raise ManifestError("The authorization signature structure is invalid.")
    if signature["algorithm"] != SIGNATURE_ALGORITHM:
        raise ManifestError("The authorization signature algorithm is not supported.")
    if signature["key_id"] != public_key_id(public_key):
        raise ManifestError("The authorization file was signed by an unapproved key.")
    payload = canonical_signed_bytes(SCHEMA_VERSION, envelope["authorization"])
    try:
        public_key.verify(b64decode(signature["value"], "signature"), payload)
    except InvalidSignature as exc:
        raise ManifestError("The authorization file signature is invalid.") from exc
    return envelope["authorization"], hashlib.sha256(payload).hexdigest()


def read_envelope(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError("The authorization file is missing.") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("The authorization file cannot be read.") from exc
    if not isinstance(raw, dict):
        raise ManifestError("The authorization file structure is invalid.")
    return raw


def write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    encoded = (json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _atomic_write(path, encoded)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
