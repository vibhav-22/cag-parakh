from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_NAME = "model-pack.json"
MANIFEST_SCHEMA = 1
PACK_ID = "parakh-qwen3-vl-4b-instruct-q4-k-m"
PACK_VERSION = "1.0.0"
MODEL_ALIAS = "Qwen3-VL-4B-Instruct"


@dataclass(frozen=True)
class ModelPack:
    location: Path | None
    manifest_path: Path | None
    manifest: dict[str, Any] | None
    errors: tuple[str, ...]

    @property
    def installed(self) -> bool:
        return self.manifest_path is not None

    @property
    def ready(self) -> bool:
        return self.installed and not self.errors and self.manifest is not None

    def path_for(self, relative: str) -> Path | None:
        if self.location is None or not relative:
            return None
        try:
            candidate = (self.location / relative).resolve()
            candidate.relative_to(self.location.resolve())
        except (OSError, ValueError):
            return None
        return candidate


def _configured_root() -> Path | None:
    configured = os.getenv("PARAKH_VLM_MODEL_PACK", "").strip()
    return Path(configured).expanduser() if configured else None


def candidate_roots() -> list[Path]:
    """Return model-pack roots in operator, machine, then user preference order."""

    candidates: list[Path] = []
    if configured := _configured_root():
        candidates.append(configured)

    for variable in ("PROGRAMDATA", "LOCALAPPDATA"):
        base = os.getenv(variable, "").strip()
        if base:
            candidates.append(Path(base) / "Parakh" / "ModelPacks" / PACK_ID / PACK_VERSION)

    # Useful for source-tree development. This location is gitignored and is
    # never copied into the main installer.
    candidates.append(Path(__file__).resolve().parent / "models" / PACK_ID / PACK_VERSION)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve(strict=False)))
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def _manifest_path(root: Path) -> Path:
    return root if root.name.lower() == MANIFEST_NAME else root / MANIFEST_NAME


def _string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) else ""


def _validate_manifest(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        errors.append(f"Unsupported model-pack schema; expected {MANIFEST_SCHEMA}.")
    if _string(manifest, "pack_id") != PACK_ID:
        errors.append(f"Unexpected model-pack id; expected {PACK_ID}.")
    if _string(manifest, "pack_version") != PACK_VERSION:
        errors.append(f"Unsupported model-pack version; expected {PACK_VERSION}.")

    model = manifest.get("model")
    runtime = manifest.get("runtime")
    if not isinstance(model, dict):
        errors.append("The model-pack manifest has no model section.")
        model = {}
    if not isinstance(runtime, dict):
        errors.append("The model-pack manifest has no runtime section.")
        runtime = {}

    if _string(model, "alias") != MODEL_ALIAS:
        errors.append(f"Unexpected model alias; expected {MODEL_ALIAS}.")
    for key, label in (("file", "model"), ("mmproj_file", "vision projector")):
        relative = _string(model, key)
        candidate = _safe_child(root, relative)
        if candidate is None or not candidate.is_file():
            errors.append(f"The pinned {label} file is missing.")

    executables = runtime.get("executables")
    if not isinstance(executables, dict):
        errors.append("The model-pack manifest has no runtime executables.")
    else:
        preference = runtime.get("backend_preference")
        required_backends = preference if isinstance(preference, list) else ["cpu"]
        if "cpu" not in required_backends:
            required_backends = [*required_backends, "cpu"]
        for backend in required_backends:
            relative = executables.get(backend) if isinstance(backend, str) else None
            candidate = _safe_child(root, relative) if isinstance(relative, str) else None
            if candidate is None or not candidate.is_file():
                errors.append(f"The packaged {backend} llama-server runtime is missing.")
    return errors


def _safe_child(root: Path, relative: str) -> Path | None:
    if not relative:
        return None
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def discover_model_pack() -> ModelPack:
    """Find and validate the optional pack without making its absence fatal."""

    for root in candidate_roots():
        manifest_path = _manifest_path(root)
        if not manifest_path.is_file():
            continue
        location = manifest_path.parent
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return ModelPack(location, manifest_path, None, ("The model-pack manifest is unreadable.",))
        if not isinstance(payload, dict):
            return ModelPack(location, manifest_path, None, ("The model-pack manifest must be a JSON object.",))
        return ModelPack(location, manifest_path, payload, tuple(_validate_manifest(location, payload)))
    return ModelPack(None, None, None, ())


def verify_file_hashes(pack: ModelPack) -> list[str]:
    """Perform the expensive full integrity check used by installers and QA."""

    if not pack.ready or pack.manifest is None:
        return list(pack.errors) or ["The optional model pack is not installed."]
    files = pack.manifest.get("files")
    if not isinstance(files, dict) or not files:
        return ["The model-pack manifest contains no file hashes."]
    errors: list[str] = []
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            errors.append("The model-pack hash table is malformed.")
            continue
        path = pack.path_for(relative)
        if path is None or not path.is_file():
            errors.append(f"Missing hashed file: {relative}")
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest().lower() != expected.lower():
            errors.append(f"Hash mismatch: {relative}")
    return errors


def model_pack_status(pack: ModelPack | None = None) -> dict[str, Any]:
    pack = pack or discover_model_pack()
    manifest = pack.manifest or {}
    model = manifest.get("model") if isinstance(manifest.get("model"), dict) else {}
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    selected_backend = os.getenv("PARAKH_VLM_RUNTIME_BACKEND", "").strip() or None
    selected_executable = os.getenv("PARAKH_VLM_RUNTIME_PATH", "").strip() or None
    device = os.getenv("PARAKH_VLM_DEVICE", "").strip() or None
    return {
        "installed": pack.installed,
        "ready": pack.ready,
        "pack_id": manifest.get("pack_id"),
        "pack_version": manifest.get("pack_version"),
        "location": str(pack.location) if pack.location else None,
        "manifest_path": str(pack.manifest_path) if pack.manifest_path else None,
        "model": model.get("alias"),
        "model_source": model.get("source"),
        "model_revision": model.get("revision"),
        "quantization": model.get("quantization"),
        "model_file": str(pack.path_for(str(model.get("file", "")))) if pack.ready else None,
        "model_sha256": model.get("sha256"),
        "mmproj_file": str(pack.path_for(str(model.get("mmproj_file", "")))) if pack.ready else None,
        "mmproj_quantization": model.get("mmproj_quantization"),
        "mmproj_sha256": model.get("mmproj_sha256"),
        "runtime": runtime.get("name"),
        "runtime_version": runtime.get("version"),
        "runtime_backend": selected_backend,
        "runtime_path": selected_executable,
        "device": device,
        "errors": list(pack.errors),
    }


def launcher_contract(pack: ModelPack | None = None) -> dict[str, Any]:
    """Return paths and fixed arguments the Electron launcher needs.

    Host and port are intentionally omitted: the launcher allocates a loopback
    port at runtime and must append ``--host 127.0.0.1 --port <port>``.
    """

    pack = pack or discover_model_pack()
    if not pack.ready or pack.manifest is None:
        return {"ready": False, "errors": list(pack.errors)}
    model = pack.manifest["model"]
    runtime = pack.manifest["runtime"]
    executables = runtime.get("executables", {})
    resolved = {
        backend: str(path)
        for backend, relative in executables.items()
        if isinstance(backend, str)
        and isinstance(relative, str)
        and (path := pack.path_for(relative)) is not None
        and path.is_file()
    }
    arguments = [
        "--model", str(pack.path_for(model["file"])),
        "--mmproj", str(pack.path_for(model["mmproj_file"])),
        "--alias", model["alias"],
        "--ctx-size", str(runtime.get("context_size", 8192)),
        "--parallel", "1",
        "--jinja",
        "--no-webui",
    ]
    return {
        "ready": True,
        "executables": resolved,
        "preference": runtime.get("backend_preference", ["vulkan", "cpu"]),
        "arguments": arguments,
        "environment": {
            "VLM_ENABLED": "1",
            "VLM_MODEL": model["alias"],
            "PARAKH_VLM_MODEL_PACK": str(pack.location),
        },
    }
