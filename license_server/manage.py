from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from .store import LicenseStore


def _store() -> LicenseStore:
    return LicenseStore(
        Path(os.getenv("PARAKH_LICENSE_DB", "license-data/licenses.db")),
        os.getenv("PARAKH_SIGNING_SECRET", ""),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage approved Parakh users and devices.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add-user")
    add.add_argument("email")
    add.add_argument("--name", default="")
    add.add_argument("--max-devices", type=int, default=1)

    subparsers.add_parser("list-users")
    for command in ("disable-user", "enable-user", "list-devices"):
        action = subparsers.add_parser(command)
        action.add_argument("email")
    device = subparsers.add_parser("disable-device")
    device.add_argument("email")
    device.add_argument("device_id")

    args = parser.parse_args()
    store = _store()
    if args.command == "add-user":
        password = getpass.getpass("Password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords do not match.")
        result = store.create_user(args.email, password, args.name, args.max_devices)
    elif args.command == "list-users":
        result = store.list_users()
    elif args.command == "disable-user":
        result = {"updated": store.set_user_active(args.email, False)}
    elif args.command == "enable-user":
        result = {"updated": store.set_user_active(args.email, True)}
    elif args.command == "list-devices":
        result = store.list_devices(args.email)
    else:
        result = {"updated": store.set_device_active(args.email, args.device_id, False)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
