from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .app_paths import ROOT


@dataclass(frozen=True)
class ExternalDependency:
    """A program or model file the detectors need but pip does not install."""

    key: str
    label: str
    # Explicit operator override, checked first so a support case can always be
    # unblocked without a rebuild.
    env_var: str
    # Where the installer puts it, relative to the bundle root.
    bundled: str
    # What to look for on PATH when running from source.
    on_path: str
    # Checks that silently lose coverage when this is absent. Named so a
    # missing dependency can be reported as "these checks cannot run" rather
    # than as a filename the reviewer has no way to interpret.
    affects: tuple[str, ...]


# Everything here is a native binary or a model blob. Each one is a thing that
# `pip install -r requirements.txt` does NOT provide, which is exactly why a
# frozen build can pass every unit test and still lose checks on a customer
# machine.
EXTERNAL_DEPENDENCIES: tuple[ExternalDependency, ...] = (
    ExternalDependency(
        key="tesseract",
        label="Tesseract OCR",
        env_var="PARAKH_TESSERACT",
        bundled="vendor/tesseract/tesseract.exe",
        on_path="tesseract",
        affects=("readability: OCR Confidence", "readability: OCR Word Count"),
    ),
    ExternalDependency(
        key="pdftoppm",
        label="Poppler pdftoppm",
        env_var="PDFTOPPM",
        bundled="vendor/poppler/bin/pdftoppm.exe",
        on_path="pdftoppm",
        affects=(
            "readability: OCR + image quality",
            "capture: same-phone consistency",
        ),
    ),
    ExternalDependency(
        key="pdfinfo",
        label="Poppler pdfinfo",
        env_var="PARAKH_PDFINFO",
        bundled="vendor/poppler/bin/pdfinfo.exe",
        on_path="pdfinfo",
        affects=("readability: PDF Stream Integrity",),
    ),
    ExternalDependency(
        key="insightface_model",
        label="InsightFace buffalo_l model",
        env_var="PARAKH_INSIGHTFACE_MODEL",
        bundled="vendor/insightface/models/buffalo_l",
        on_path="",  # a model directory, never on PATH
        affects=("photo: batch face-identity matching",),
    ),
)


def bundle_root() -> Path:
    """Where bundled native files live.

    PyInstaller unpacks to _MEIPASS when frozen; running from source the repo
    root stands in, so the same lookup works in both worlds and nothing needs
    to branch on "am I packaged" at every call site.
    """

    meipass = getattr(sys, "_MEIPASS", "")
    return Path(meipass) if meipass else ROOT


def resolve(dependency: ExternalDependency) -> Path | None:
    """Find a dependency, or None when it is genuinely absent.

    Order is deliberate: an explicit override wins so support can redirect a
    broken install; the bundled copy comes next so an installed build prefers
    the version it shipped and tested against over whatever happens to be on
    the machine; PATH is last, which is the normal case when running from
    source.
    """

    override = os.getenv(dependency.env_var, "").strip()
    if override and Path(override).exists():
        return Path(override)

    bundled = bundle_root() / dependency.bundled
    if bundled.exists():
        return bundled

    if dependency.on_path:
        found = shutil.which(dependency.on_path)
        if found:
            return Path(found)

    # The InsightFace model has a conventional home outside the bundle.
    if dependency.key == "insightface_model":
        home = os.getenv("INSIGHTFACE_HOME", "").strip()
        base = Path(home) if home else Path.home() / ".insightface"
        candidate = base / "models" / "buffalo_l"
        if candidate.exists():
            return candidate

    return None


def missing() -> list[ExternalDependency]:
    """Dependencies that are not present, in declaration order."""

    return [dependency for dependency in EXTERNAL_DEPENDENCIES if resolve(dependency) is None]


def status() -> dict[str, object]:
    """Machine-readable dependency report.

    The clean-VM smoke test (T6) asserts against this: an installed build that
    cannot find its own bundled tooling is a failed build, and this is how that
    gets caught before a customer sees a degraded verdict.
    """

    resolved = {
        dependency.key: (str(path) if (path := resolve(dependency)) else None)
        for dependency in EXTERNAL_DEPENDENCIES
    }
    absent = [dependency for dependency in EXTERNAL_DEPENDENCIES if resolved[dependency.key] is None]
    # The VLM pack is optional: its absence must never make deterministic
    # screening incomplete, but an installed-yet-broken pack must be visible.
    from .vlm_model_pack import model_pack_status

    return {
        "complete": not absent,
        "resolved": resolved,
        "missing": [
            {
                "key": dependency.key,
                "label": dependency.label,
                "affects": list(dependency.affects),
                "env_var": dependency.env_var,
            }
            for dependency in absent
        ],
        # Flattened for the report stamp, which needs to name lost coverage
        # rather than list filenames a reviewer cannot act on.
        "degraded_checks": sorted(
            {check for dependency in absent for check in dependency.affects}
        ),
        "optional": {"vlm_model_pack": model_pack_status()},
    }


def apply_runtime_configuration() -> None:
    """Point the detectors at whatever copies actually exist.

    The detector scripts each grew their own discovery logic — `locate_pdftoppm`
    in the capture tools searches a hard-coded developer cache path, for
    instance. Rather than rewrite every one of them, this resolves each
    dependency once at startup and exports the environment variables they
    already honour, so a frozen build's bundled copies win without any detector
    needing to know it is running inside a bundle.

    Only ever fills in blanks: an operator who set one of these explicitly
    keeps their value.
    """

    for dependency in EXTERNAL_DEPENDENCIES:
        path = resolve(dependency)
        if path is None or os.getenv(dependency.env_var, "").strip():
            continue
        os.environ[dependency.env_var] = str(path)

    pdftoppm = os.getenv("PDFTOPPM", "").strip()
    if pdftoppm:
        # The poppler tools live side by side, and several detectors shell out
        # to them by bare name. Putting that directory on PATH is what makes a
        # bundled poppler reachable from a frozen process.
        bin_dir = str(Path(pdftoppm).parent)
        if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    tesseract = os.getenv("PARAKH_TESSERACT", "").strip()
    if tesseract:
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = tesseract
        except Exception:
            # Missing or broken pytesseract is reported by status() as lost
            # coverage; it must not stop the app from starting.
            pass

    model = os.getenv("PARAKH_INSIGHTFACE_MODEL", "").strip()
    if model and not os.getenv("INSIGHTFACE_HOME", "").strip():
        # InsightFace resolves models under INSIGHTFACE_HOME/models, so point at
        # the grandparent of the model directory itself.
        os.environ["INSIGHTFACE_HOME"] = str(Path(model).parent.parent)
