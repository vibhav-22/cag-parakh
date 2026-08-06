from __future__ import annotations

import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


# Sent by the app window on every API request. Named rather than reused from
# Authorization so it can never be confused with the account session, which
# answers a different question: this header says "the request came from the
# window this backend launched", not "this person may use the product".
LAUNCH_TOKEN_HEADER = "x-parakh-launch"

PROTECTED_PATH_PREFIX = "/api/"


def generate_launch_token() -> str:
    """Mint a token for one run of the app.

    The launcher calls this once per launch and hands the value to both the
    backend (as PARAKH_LAUNCH_TOKEN) and the window it opens. A fresh value per
    launch means a token captured from a previous run is already worthless.
    """

    return secrets.token_urlsafe(32)


class LaunchTokenMiddleware:
    """Rejects API calls that did not come from this launch's app window.

    On a laptop the backend listens on a local port, and a local port is not a
    private channel: any other process on the machine, and any web page the
    user happens to visit, can send it requests. The account session does not
    close that hole. A page cannot read a cross-origin response, but the
    request still runs, so a forged call could still delete a case or start
    work under the signed-in account.

    Written as pure ASGI rather than BaseHTTPMiddleware on purpose: this app
    returns FileResponse for report and PDF downloads, and BaseHTTPMiddleware
    wraps streaming responses in a way that buffers them.
    """

    def __init__(self, app: ASGIApp, *, token: str, required: bool) -> None:
        self.app = app
        self.token = token
        self.required = required

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.required:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Only the API carries side effects worth forging. Static assets, the
        # health probe the launcher waits on, and the docs pages are untouched.
        if not path.startswith(PROTECTED_PATH_PREFIX):
            await self.app(scope, receive, send)
            return

        # A CORS preflight carries no credentials and performs no work. Letting
        # it through keeps this middleware independent of where it sits in the
        # stack relative to CORSMiddleware.
        if scope.get("method", "") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not self.token:
            # Packaged but never told its token: a broken build, not a broken
            # request. Fail closed and say so, rather than silently accepting
            # everything and shipping a product with no local protection.
            response = JSONResponse(
                {"detail": "This installation was started without a launch token."},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        supplied = ""
        header_name = LAUNCH_TOKEN_HEADER.encode("ascii")
        for name, value in scope.get("headers", []):
            if name == header_name:
                supplied = value.decode("latin-1")
                break

        if not secrets.compare_digest(supplied, self.token):
            response = JSONResponse(
                {"detail": "This request did not come from the Parakh app window."},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
