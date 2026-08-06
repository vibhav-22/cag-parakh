from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import dependencies


def _dep(key: str) -> dependencies.ExternalDependency:
    return next(d for d in dependencies.EXTERNAL_DEPENDENCIES if d.key == key)


class ResolutionOrderTests(unittest.TestCase):
    def test_explicit_override_wins(self) -> None:
        dependency = _dep("tesseract")
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "my-tesseract.exe"
            override.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {dependency.env_var: str(override)}, clear=False):
                self.assertEqual(dependencies.resolve(dependency), override)

    def test_override_pointing_at_nothing_is_ignored(self) -> None:
        # A stale override must fall through to a real copy rather than
        # reporting the dependency as present at a path that does not exist.
        dependency = _dep("tesseract")
        with patch.dict(
            os.environ, {dependency.env_var: r"C:\nonexistent\tesseract.exe"}, clear=False
        ):
            resolved = dependencies.resolve(dependency)
        self.assertNotEqual(str(resolved), r"C:\nonexistent\tesseract.exe")

    def test_bundled_copy_is_preferred_over_path(self) -> None:
        # An installed build must use the version it shipped and tested with,
        # not whatever the customer happens to have installed.
        dependency = _dep("pdftoppm")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / dependency.bundled
            bundled.parent.mkdir(parents=True, exist_ok=True)
            bundled.write_text("", encoding="utf-8")
            with patch.object(dependencies, "bundle_root", lambda: root):
                with patch.dict(os.environ, {dependency.env_var: ""}, clear=False):
                    self.assertEqual(dependencies.resolve(dependency), bundled)

    def test_frozen_build_looks_inside_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(dependencies.sys, "_MEIPASS", directory, create=True):
                self.assertEqual(dependencies.bundle_root(), Path(directory))

    def test_running_from_source_uses_the_repo_root(self) -> None:
        if hasattr(dependencies.sys, "_MEIPASS"):
            self.skipTest("only meaningful when not frozen")
        self.assertEqual(dependencies.bundle_root(), dependencies.ROOT)


class StatusReportTests(unittest.TestCase):
    def test_status_names_lost_coverage_not_just_filenames(self) -> None:
        # A reviewer cannot act on "pdftoppm.exe missing". They can act on
        # "the capture consistency check will not run".
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(dependencies, "bundle_root", lambda: Path(directory)):
                with patch.object(dependencies.shutil, "which", lambda _name: None):
                    with patch.dict(
                        os.environ,
                        {d.env_var: "" for d in dependencies.EXTERNAL_DEPENDENCIES}
                        | {"INSIGHTFACE_HOME": str(Path(directory) / "none")},
                        clear=False,
                    ):
                        report = dependencies.status()

        self.assertFalse(report["complete"])
        self.assertTrue(report["degraded_checks"], "missing tooling must name the checks it costs")
        self.assertTrue(
            any("readability" in check for check in report["degraded_checks"]),
            "losing OCR tooling must be reported against the readability check",
        )

    def test_status_is_complete_when_everything_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for dependency in dependencies.EXTERNAL_DEPENDENCIES:
                target = root / dependency.bundled
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("", encoding="utf-8")
            with patch.object(dependencies, "bundle_root", lambda: root):
                with patch.dict(
                    os.environ,
                    {d.env_var: "" for d in dependencies.EXTERNAL_DEPENDENCIES},
                    clear=False,
                ):
                    report = dependencies.status()

        self.assertTrue(report["complete"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["degraded_checks"], [])


class RuntimeConfigurationTests(unittest.TestCase):
    def test_bundled_poppler_is_put_on_path_for_detectors(self) -> None:
        # Several detectors shell out to poppler by bare name, so resolving the
        # path is not enough — the directory has to be reachable.
        dependency = _dep("pdftoppm")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / dependency.bundled
            bundled.parent.mkdir(parents=True, exist_ok=True)
            bundled.write_text("", encoding="utf-8")
            with patch.object(dependencies, "bundle_root", lambda: root):
                with patch.dict(os.environ, {"PDFTOPPM": "", "PATH": ""}, clear=False):
                    dependencies.apply_runtime_configuration()
                    self.assertEqual(os.environ["PDFTOPPM"], str(bundled))
                    self.assertIn(str(bundled.parent), os.environ["PATH"])

    def test_an_operator_override_is_never_overwritten(self) -> None:
        dependency = _dep("pdftoppm")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / dependency.bundled
            bundled.parent.mkdir(parents=True, exist_ok=True)
            bundled.write_text("", encoding="utf-8")
            chosen = root / "operator-pdftoppm.exe"
            chosen.write_text("", encoding="utf-8")
            with patch.object(dependencies, "bundle_root", lambda: root):
                with patch.dict(os.environ, {"PDFTOPPM": str(chosen)}, clear=False):
                    dependencies.apply_runtime_configuration()
                    self.assertEqual(os.environ["PDFTOPPM"], str(chosen))

    def test_configuration_never_raises_when_nothing_is_present(self) -> None:
        # Startup must survive a build that is missing everything; the failure
        # surfaces through status(), not through a crash on launch.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(dependencies, "bundle_root", lambda: Path(directory)):
                with patch.object(dependencies.shutil, "which", lambda _name: None):
                    dependencies.apply_runtime_configuration()


if __name__ == "__main__":
    unittest.main()
