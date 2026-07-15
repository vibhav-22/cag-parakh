from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import fitz
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .models import DocumentManifest, DocumentPage, JobState
from .service import JobStore, pdf_page_sizes, run_job


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
store = JobStore(ROOT / "backend" / "data")


class ReviewRequest(BaseModel):
    decision: Literal["verified", "needs_investigation", "inconclusive"]
    notes: str = Field(default="", max_length=4000)

app = FastAPI(
    title="Document Suspicion System API",
    version="1.0.0",
    description="Queues PDF screening checks and returns their individual reports.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,https://document-suspicion-system.dacup1-2026-5.chatgpt.site"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/analyzers")
def analyzers() -> dict[str, object]:
    return {"analyzers": store.available_analyzers()}


@app.post("/api/v1/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobState)
async def create_job(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF document to screen")],
    analyzers: str | None = None,
) -> JobState:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only .pdf uploads are supported.")

    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 25 MB upload limit.")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="The uploaded file is not a valid PDF stream.")

    requested = [item.strip() for item in analyzers.split(",")] if analyzers else None
    try:
        job = store.create(file.filename, payload, requested)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(run_job, store, job["id"])
    return JobState.model_validate(job)


@app.get("/api/v1/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobState.model_validate(job)


@app.get("/api/v1/jobs/{job_id}/document", response_class=FileResponse)
def get_document(job_id: str) -> FileResponse:
    job = store.get(job_id)
    path = store.path_for(job_id)
    if job is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    safe_name = Path(str(job["filename"])).name.replace('"', "")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@app.get("/api/v1/jobs/{job_id}/document/manifest", response_model=DocumentManifest)
def get_document_manifest(job_id: str) -> DocumentManifest:
    job = store.get(job_id)
    path = store.path_for(job_id)
    if job is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        sizes = pdf_page_sizes(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Document pages could not be read.") from exc
    pages = [
        DocumentPage(page=page, width=size[0], height=size[1])
        for page, size in sizes.items()
    ]
    return DocumentManifest(page_count=len(pages), pages=pages)


@app.get("/api/v1/jobs/{job_id}/document/pages/{page_number}.png", response_class=Response)
def get_document_page(
    job_id: str,
    page_number: int,
    dpi: Annotated[int, Query(ge=96, le=200)] = 144,
) -> Response:
    job = store.get(job_id)
    path = store.path_for(job_id)
    if job is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        with fitz.open(path) as document:
            if page_number < 1 or page_number > len(document):
                raise HTTPException(status_code=404, detail="Document page not found.")
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            image = pixmap.tobytes("png")
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Document page could not be rendered.") from exc
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/v1/jobs/{job_id}/artifacts/{analyzer}/{filename}", response_class=FileResponse)
def get_artifact(job_id: str, analyzer: str, filename: str) -> FileResponse:
    if store.get(job_id) is None or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    path = store.data_dir / f"{job_id}-{analyzer}-report" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    safe_name = Path(filename).name.replace('"', "")
    return FileResponse(
        path,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@app.post("/api/v1/jobs/{job_id}/review")
def save_review(job_id: str, review: ReviewRequest) -> dict[str, object]:
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    saved = {**review.model_dump(), "reviewed_at": datetime.now(timezone.utc).isoformat()}
    store.update(job_id, review=saved)
    return saved
