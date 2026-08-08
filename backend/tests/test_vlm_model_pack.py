from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import dependencies
from backend.vlm import get_vlm_config, get_vlm_status
from backend.vlm_model_pack import (
    MODEL_ALIAS,
    PACK_ID,
    PACK_VERSION,
    discover_model_pack,
    launcher_contract,
    verify_file_hashes,
)


def _write_pack(root: Path, *, model_file: str = "model/model.gguf") -> None:
    files = {
        model_file: b"model",
        "model/mmproj.gguf": b"projector",
        "runtime/cpu/llama-server.exe": b"runtime",
        "runtime/vulkan/llama-server.exe": b"runtime",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {
        "schema_version": 1,
        "pack_id": PACK_ID,
        "pack_version": PACK_VERSION,
        "model": {
            "alias": MODEL_ALIAS,
            "source": "Qwen/Qwen3-VL-4B-Instruct-GGUF",
            "revision": "1cd86afb9a95c410a6038ab3b40d8b578c892266",
            "quantization": "Q4_K_M",
            "file": model_file,
            "mmproj_file": "model/mmproj.gguf",
        },
        "runtime": {
            "name": "llama.cpp",
            "version": "b10298",
            "context_size": 8192,
            "backend_preference": ["vulkan", "cpu"],
            "executables": {
                "vulkan": "runtime/vulkan/llama-server.exe",
                "cpu": "runtime/cpu/llama-server.exe",
            },
        },
        "files": {},
    }
    (root / "model-pack.json").write_text(json.dumps(manifest), encoding="utf-8")


class ModelPackDiscoveryTests(unittest.TestCase):
    def test_absence_is_non_fatal_and_does_not_degrade_screening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("backend.vlm_model_pack.candidate_roots", return_value=[Path(directory)]):
                pack = discover_model_pack()
                with patch("backend.vlm_model_pack.discover_model_pack", return_value=pack):
                    report = dependencies.status()

        self.assertFalse(pack.installed)
        self.assertFalse(report["optional"]["vlm_model_pack"]["installed"])

    def test_valid_pack_is_discovered_and_produces_launcher_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                pack = discover_model_pack()
                contract = launcher_contract(pack)

        self.assertTrue(pack.ready)
        self.assertTrue(contract["ready"])
        self.assertEqual(contract["environment"]["VLM_MODEL"], MODEL_ALIAS)
        self.assertIn("cpu", contract["executables"])
        self.assertNotIn("--host", contract["arguments"])

    def test_manifest_cannot_escape_the_pack_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pack"
            root.mkdir()
            _write_pack(root, model_file="../outside.gguf")
            (root.parent / "outside.gguf").write_bytes(b"outside")
            with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                pack = discover_model_pack()

        self.assertFalse(pack.ready)
        self.assertTrue(any("model file is missing" in error for error in pack.errors))

    def test_hash_verification_reports_modified_file(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            manifest_path = root / "model-pack.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"] = {"model/model.gguf": hashlib.sha256(b"expected").hexdigest()}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                errors = verify_file_hashes(discover_model_pack())

        self.assertEqual(errors, ["Hash mismatch: model/model.gguf"])

    def test_cpu_fallback_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            (root / "runtime/cpu/llama-server.exe").unlink()
            with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                pack = discover_model_pack()

        self.assertFalse(pack.ready)
        self.assertTrue(any("cpu" in error for error in pack.errors))


class ModelPackVLMStatusTests(unittest.TestCase):
    def test_packaged_absence_is_explained_without_breaking_screening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PARAKH_PACKAGED": "1"}, clear=True):
                with patch("backend.vlm_model_pack.candidate_roots", return_value=[Path(directory)]):
                    status = get_vlm_status(ping=True)

        self.assertFalse(status["enabled"])
        self.assertFalse(status["ready"])
        self.assertIn("screening remains available", status["message"])

    def test_pack_enables_vlm_when_no_explicit_flag_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            with patch.dict(os.environ, {}, clear=True):
                with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                    config = get_vlm_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.model, MODEL_ALIAS)

    def test_ready_requires_the_pinned_model_to_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            with patch.dict(os.environ, {}, clear=True):
                with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                    with patch("backend.vlm._request_json", return_value={"data": [{"id": "other"}]}):
                        status = get_vlm_status(ping=True)

        self.assertFalse(status["ready"])
        self.assertEqual(status["source"], "model_pack")
        self.assertIn("pinned model", status["message"])

    def test_configured_does_not_claim_ready_without_a_ping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pack(root)
            with patch.dict(os.environ, {}, clear=True):
                with patch("backend.vlm_model_pack.candidate_roots", return_value=[root]):
                    status = get_vlm_status(ping=False)

        self.assertTrue(status["configured"])
        self.assertFalse(status["ready"])


if __name__ == "__main__":
    unittest.main()
