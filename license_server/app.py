"""Compatibility tombstone for the retired network authorization service.

Parakh authorization is intentionally offline. This module remains only so an
old service command fails explicitly instead of exposing stale login routes.
Use ``python -m license_server.manage --help`` to create a signed local file.
"""

from fastapi import FastAPI, HTTPException


app = FastAPI(title="Parakh Authorization Service (retired)", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "disabled", "mode": "signed-local-authorization"}


@app.post("/{path:path}")
def disabled(path: str) -> None:
    raise HTTPException(
        status_code=410,
        detail="Network authorization is disabled. Deploy a signed local authorization file.",
    )
