from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.launch_token import (
    LAUNCH_TOKEN_HEADER,
    LaunchTokenMiddleware,
    generate_launch_token,
)


TOKEN = "launch-token-for-this-run"


def _client(*, token: str = TOKEN, required: bool = True) -> TestClient:
    """A stand-in app with the same shapes the real one exposes.

    Built here rather than importing backend.app so these tests cover the
    middleware itself and stay independent of the real app's startup, which
    reads a data directory and an authorization service.
    """

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Mirrors the real app, where session routes hang off `app` directly while
    # everything else lives on a router. Both must be protected.
    @app.get("/api/v1/session")
    def session() -> dict[str, bool]:
        return {"authenticated": True}

    @app.get("/api/v1/batches")
    def batches() -> dict[str, list[str]]:
        return {"batches": []}

    app.add_middleware(LaunchTokenMiddleware, token=token, required=required)
    return TestClient(app)


class LaunchTokenGenerationTests(unittest.TestCase):
    def test_every_launch_gets_a_different_token(self) -> None:
        tokens = {generate_launch_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50, "a reused token would survive a restart")

    def test_token_is_long_enough_to_be_unguessable(self) -> None:
        self.assertGreaterEqual(len(generate_launch_token()), 32)


class LaunchTokenEnforcementTests(unittest.TestCase):
    def test_correct_token_is_accepted(self) -> None:
        response = _client().get("/api/v1/batches", headers={LAUNCH_TOKEN_HEADER: TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_missing_token_is_refused(self) -> None:
        response = _client().get("/api/v1/batches")
        self.assertEqual(response.status_code, 403)
        self.assertIn("app window", response.json()["detail"])

    def test_wrong_token_is_refused(self) -> None:
        response = _client().get(
            "/api/v1/batches", headers={LAUNCH_TOKEN_HEADER: "not-the-right-token"}
        )
        self.assertEqual(response.status_code, 403)

    def test_session_routes_are_protected_too(self) -> None:
        # These hang off `app`, not the router. A router dependency would have
        # left the sign-in surface open to a forged local request.
        self.assertEqual(_client().get("/api/v1/session").status_code, 403)
        self.assertEqual(
            _client().get("/api/v1/session", headers={LAUNCH_TOKEN_HEADER: TOKEN}).status_code,
            200,
        )

    def test_health_stays_open_for_the_launcher(self) -> None:
        # The launcher polls this before it has a window to send a token from.
        self.assertEqual(_client().get("/health").status_code, 200)

    def test_preflight_is_not_blocked(self) -> None:
        response = _client().options("/api/v1/batches")
        self.assertNotEqual(response.status_code, 403)

    def test_packaged_build_without_a_token_fails_closed(self) -> None:
        response = _client(token="").get("/api/v1/batches")
        self.assertEqual(response.status_code, 503)
        self.assertIn("without a launch token", response.json()["detail"])

    def test_packaged_build_without_a_token_cannot_be_bypassed(self) -> None:
        # An empty configured token must not mean "any empty header matches".
        response = _client(token="").get("/api/v1/batches", headers={LAUNCH_TOKEN_HEADER: ""})
        self.assertEqual(response.status_code, 503)

    def test_running_from_source_needs_no_token(self) -> None:
        client = _client(required=False)
        self.assertEqual(client.get("/api/v1/batches").status_code, 200)
        self.assertEqual(client.get("/api/v1/session").status_code, 200)


if __name__ == "__main__":
    unittest.main()
