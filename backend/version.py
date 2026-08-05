from __future__ import annotations

# Stamped onto every exported case report and every recorded verdict so a
# decision stays reproducible after the detectors change. APP_VERSION moves
# whenever the API contract does; DETECTORS_VERSION moves whenever a detector's
# scoring, thresholds, or output shape changes in a way that could alter a
# verdict for the same input bytes.
APP_VERSION = "1.4.0"
DETECTORS_VERSION = "2026.07.1"
