from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.access_control import DpapiSessionProtector
from backend.auth_manifest import generate_key_pair, parse_timestamp
from license_server.store import AuthorizationAdmin


def _date(value: str) -> datetime:
    try:
        return parse_timestamp(value, "expiry")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _require_windows_administrator() -> None:
    if os.name != "nt":
        return
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        elevated = False
    if not elevated:
        raise PermissionError("Run the Parakh authorization utility as a Windows administrator.")


def _confirmed_secret(prompt: str, confirmation: str, minimum: int = 12) -> str:
    value = getpass.getpass(prompt)
    repeated = getpass.getpass(confirmation)
    if value != repeated:
        raise ValueError("The entered values do not match.")
    if len(value) < minimum:
        raise ValueError(f"The value must contain at least {minimum} characters.")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and maintain signed offline Parakh authorization files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    keys = subparsers.add_parser("generate-key", help="Generate an external signing key pair.")
    keys.add_argument("--private-key", type=Path, required=True)
    keys.add_argument("--public-key", type=Path, required=True)
    keys.add_argument(
        "--protected-passphrase-file",
        type=Path,
        help="Optionally store the passphrase encrypted for the current Windows user with DPAPI.",
    )

    def signed_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--file", type=Path, required=True)
        command.add_argument("--private-key", type=Path, required=True)
        command.add_argument(
            "--protected-passphrase-file",
            type=Path,
            help="Read the signing-key passphrase from a current-user DPAPI-protected file.",
        )
        return command

    initialize = signed_command("init", "Create a new empty signed authorization file.")
    initialize.add_argument("--expires", type=_date, required=True)

    add = signed_command("add-user", "Add an approved employee.")
    add.add_argument("email")
    add.add_argument("--name", default="")
    add.add_argument("--expires", type=_date)
    add.add_argument("--device-id", action="append", default=[])

    remove = signed_command("remove-user", "Remove an approved employee.")
    remove.add_argument("email")

    reset = signed_command("reset-password", "Replace an employee password hash.")
    reset.add_argument("email")

    user_expiry = signed_command("set-user-expiry", "Set or clear an employee expiry.")
    user_expiry.add_argument("email")
    expiry_group = user_expiry.add_mutually_exclusive_group(required=True)
    expiry_group.add_argument("--expires", type=_date)
    expiry_group.add_argument("--clear", action="store_true")

    devices = signed_command("set-user-devices", "Set optional installation bindings.")
    devices.add_argument("email")
    devices.add_argument("--device-id", action="append", default=[])

    expiry = signed_command("set-expiry", "Set the whole authorization-file expiry.")
    expiry.add_argument("--expires", type=_date, required=True)

    signed_command("list-users", "List approved employees without password hashes.")
    signed_command("sign", "Verify and issue a fresh signature for the file.")
    return parser


def _protected_passphrase(path: Path) -> bytes:
    try:
        passphrase = DpapiSessionProtector().unprotect(path.read_bytes())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "The protected passphrase cannot be read by the current Windows user."
        ) from exc
    if len(passphrase) < 12:
        raise ValueError("The protected signing-key passphrase is invalid.")
    return passphrase


def _write_protected_passphrase(path: Path, passphrase: bytes) -> None:
    if path.exists():
        raise FileExistsError("Refusing to overwrite a protected passphrase file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = DpapiSessionProtector().protect(passphrase)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(protected)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _admin(args: argparse.Namespace) -> AuthorizationAdmin:
    protected_file = getattr(args, "protected_passphrase_file", None)
    passphrase = (
        _protected_passphrase(protected_file)
        if protected_file
        else getpass.getpass("Signing-key passphrase: ").encode("utf-8")
    )
    return AuthorizationAdmin(args.file, args.private_key, passphrase)


def run(args: argparse.Namespace) -> Any:
    _require_windows_administrator()
    if args.command == "generate-key":
        passphrase = _confirmed_secret(
            "New signing-key passphrase (12+ characters): ", "Confirm passphrase: "
        ).encode("utf-8")
        if args.protected_passphrase_file and args.protected_passphrase_file.exists():
            raise FileExistsError("Refusing to overwrite a protected passphrase file.")
        generate_key_pair(args.private_key, args.public_key, passphrase)
        result = {
            "private_key": str(args.private_key.resolve()),
            "public_key": str(args.public_key.resolve()),
        }
        if args.protected_passphrase_file:
            _write_protected_passphrase(args.protected_passphrase_file, passphrase)
            result["protected_passphrase_file"] = str(
                args.protected_passphrase_file.resolve()
            )
        return result

    admin = _admin(args)
    if args.command == "init":
        return admin.initialize(args.expires)
    if args.command == "add-user":
        password = _confirmed_secret("Employee password (12+ characters): ", "Confirm password: ")
        return admin.add_user(
            args.email,
            password,
            display_name=args.name,
            expires_at=args.expires,
            device_ids=args.device_id,
        )
    if args.command == "remove-user":
        admin.remove_user(args.email)
        return {"removed": args.email.strip().lower()}
    if args.command == "reset-password":
        password = _confirmed_secret("New employee password (12+ characters): ", "Confirm password: ")
        return admin.reset_password(args.email, password)
    if args.command == "set-user-expiry":
        return admin.set_user_expiry(args.email, None if args.clear else args.expires)
    if args.command == "set-user-devices":
        return admin.set_user_devices(args.email, args.device_id)
    if args.command == "set-expiry":
        return admin.set_expiry(args.expires)
    if args.command == "list-users":
        return admin.list_users()
    return admin.resign()


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        result = run(args)
    except (FileExistsError, KeyError, PermissionError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
