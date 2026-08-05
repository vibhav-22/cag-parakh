from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def is_packaged() -> bool:
    """Return whether the backend is running as an installed desktop app."""

    return os.getenv("PARAKH_PACKAGED", "").strip().lower() in {"1", "true", "yes"}


def data_dir() -> Path:
    """Resolve mutable app data outside the installed program directory."""

    configured = os.getenv("PARAKH_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    if not is_packaged():
        return ROOT / "backend" / "data"

    app_data = os.getenv("LOCALAPPDATA", "").strip() or os.getenv("APPDATA", "").strip()
    if not app_data:
        raise RuntimeError(
            "PARAKH_DATA_DIR must be set when the packaged app data folder cannot be resolved."
        )
    return Path(app_data) / "Parakh" / "data"
