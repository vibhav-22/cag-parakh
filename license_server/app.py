from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .store import LicenseError, LicenseStore


DATABASE_PATH = Path(os.getenv("PARAKH_LICENSE_DB", "license-data/licenses.db"))
SIGNING_SECRET = os.getenv("PARAKH_SIGNING_SECRET", "")
store = LicenseStore(DATABASE_PATH, SIGNING_SECRET)
app = FastAPI(title="Parakh Authorization Service", docs_url=None, redoc_url=None)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)
    device_id: str = Field(min_length=36, max_length=36)
    device_name: str = Field(min_length=1, max_length=120)

    @field_validator("device_id")
    @classmethod
    def valid_device_id(cls, value: str) -> str:
        return str(uuid.UUID(value))


class AttemptLimiter:
    def __init__(self, limit: int = 8, window_seconds: int = 15 * 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self.lock:
            attempts = self.attempts[key]
            while attempts and attempts[0] <= now - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")

    def fail(self, key: str) -> None:
        with self.lock:
            self.attempts[key].append(time.time())

    def clear(self, key: str) -> None:
        with self.lock:
            self.attempts.pop(key, None)


limiter = AttemptLimiter()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/login")
def login(body: LoginRequest, request: Request) -> dict[str, object]:
    host = request.client.host if request.client else "unknown"
    key = f"{host}:{body.email.strip().lower()}"
    limiter.check(key)
    try:
        result = store.login(body.email, body.password, body.device_id, body.device_name)
    except LicenseError as exc:
        limiter.fail(key)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    limiter.clear(key)
    return result


@app.post("/v1/validate")
def validate(authorization: str = Header(default="")) -> dict[str, object]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A bearer token is required.")
    try:
        claims = store.validate(authorization[7:])
    except LicenseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"valid": True, "subject": claims["sub"], "device_id": claims["device_id"]}
