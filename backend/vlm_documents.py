from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VLMDocumentStore:
    """Persistent documents for the standalone visual Q&A operation."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, filename: str, payload: bytes, owner_user_id: str | None = None) -> dict[str, Any]:
        document_id = uuid.uuid4().hex
        record = {
            "id": document_id,
            "filename": Path(filename).name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "owner_user_id": owner_user_id,
        }
        with self._lock:
            self.path_for(document_id).write_bytes(payload)
            self._record_path(document_id).write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )
        return record

    def get(self, document_id: str) -> dict[str, Any] | None:
        if not document_id.isalnum():
            return None
        record_path = self._record_path(document_id)
        document_path = self.path_for(document_id)
        if not record_path.is_file() or not document_path.is_file():
            return None
        try:
            value = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def get_for_user(self, document_id: str, owner_user_id: str) -> dict[str, Any] | None:
        record = self.get(document_id)
        if record is None:
            return None
        owner = record.get("owner_user_id")
        if owner == owner_user_id or (owner is None and owner_user_id == "development"):
            return record
        return None

    def path_for(self, document_id: str) -> Path:
        return self.data_dir / f"{document_id}.pdf"

    def _record_path(self, document_id: str) -> Path:
        return self.data_dir / f"{document_id}.json"
