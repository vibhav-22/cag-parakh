from __future__ import annotations

import os
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import fitz
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

from .access_control import AccessDenied, SESSION_COOKIE, SESSION_TTL_SECONDS, SessionManager
from .app_paths import ROOT, data_dir, is_packaged
from .models import (
    BatchState,
    BatchSummary,
    DocumentManifest,
    DocumentPage,
    JobState,
    PreflightFile,
    PreflightReport,
    PriorScreening,
    ProjectState,
)
from .photo_identity import batch_photo_duplicates
from .qr_identity import batch_qr_duplicates
from .reporting import batch_csv, batch_xlsx, case_report, render_batch_html, render_case_html
from .sample_case import SAMPLE_ANALYZERS, SAMPLE_FILENAME, build_sample_document
from .service import (
    JobStore,
    content_digest,
    pdf_page_sizes,
    sanitize_analysis_settings,
    submit_job,
)
from .version import APP_VERSION, DETECTORS_VERSION
from .vlm import VLMError, answer_document_question, get_vlm_status, vlm_is_configured
from .vlm_documents import VLMDocumentStore


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_BATCH_FILES = 50
MAX_IMAGE_PIXELS = 50_000_000
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}
DATA_DIR = data_dir()
store = JobStore(DATA_DIR)
vlm_documents = VLMDocumentStore(DATA_DIR / "vlm-documents")
sessions = SessionManager(DATA_DIR)

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").strip() == "1"


def require_access(request: Request) -> None:
    user = sessions.authenticate(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    request.state.user = user


class ReviewRequest(BaseModel):
    decision: Literal["verified", "needs_investigation", "inconclusive", "escalated"]
    notes: str = Field(default="", max_length=4000)
    # Kept for compatibility with existing clients. The authenticated account
    # is always stamped server-side and cannot be replaced by this field.
    reviewer: str = Field(default="", max_length=120)
    assigned_to: str = Field(default="", max_length=120)


class RerunRequest(BaseModel):
    # Empty means "every check that did not complete", which is the case the
    # retry control exists for.
    analyzers: list[str] = Field(default_factory=list, max_length=50)


class UnanalyzableRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)
    unanalyzable: bool = True


class SessionRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class ProjectNameRequest(BaseModel):
    """Shared by create and rename — the only field either one needs."""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_must_contain_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Give the project a name.")
        return cleaned


class DocumentQuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    current_page: int | None = Field(default=None, ge=1)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Enter a question with at least two characters.")
        return cleaned


app = FastAPI(
    title="Document Suspicion System API",
    version=APP_VERSION,
    description="Queues PDF and image screening checks and returns their individual reports.",
    docs_url=None if is_packaged() else "/docs",
    redoc_url=None if is_packaged() else "/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_access)])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/session")
def session_state(request: Request) -> dict[str, object]:
    token = request.cookies.get(SESSION_COOKIE, "")
    user = sessions.authenticate(token)
    return {
        "required": sessions.required,
        "authenticated": user is not None,
        "user": user.public_dict() if user else None,
        # Lets the app warn that the authorization service is unreachable and
        # access will stop when the grace window runs out, rather than failing
        # without notice.
        "connectivity": sessions.connectivity(token),
    }


@app.post("/api/v1/session")
def create_session(body: SessionRequest, response: Response) -> dict[str, object]:
    if sessions.required:
        try:
            token, user = sessions.create(body.email, body.password)
        except AccessDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=COOKIE_SECURE,
            max_age=SESSION_TTL_SECONDS,
            path="/",
        )
    else:
        user = sessions.authenticate("")
    return {
        "required": sessions.required,
        "authenticated": True,
        "user": user.public_dict() if user else None,
    }


@app.delete("/api/v1/session")
def end_session(request: Request, response: Response) -> dict[str, object]:
    sessions.delete(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"required": sessions.required, "authenticated": not sessions.required, "user": None}


@api.get("/analyzers")
def analyzers() -> dict[str, object]:
    return {"analyzers": store.available_analyzers()}


@api.get("/vlm/status")
def vlm_status() -> dict[str, object]:
    return get_vlm_status(ping=True)


def _vlm_document(document_id: str) -> tuple[dict[str, object], Path]:
    document = vlm_documents.get(document_id)
    path = vlm_documents.path_for(document_id)
    if document is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Visual Q&A document not found.")
    return document, path


def _image_to_pdf(name: str, payload: bytes) -> bytes:
    try:
        with Image.open(BytesIO(payload)) as image:
            image_format = image.format or ""
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=415,
                    detail=f"'{name}' is not a supported JPG, PNG, WebP, or TIFF image.",
                )
            frame_count = int(getattr(image, "n_frames", 1))
            page_dimensions: list[tuple[int, int]] = []
            total_pixels = 0
            for frame_index in range(frame_count):
                image.seek(frame_index)
                page_dimensions.append(image.size)
                total_pixels += image.width * image.height
                if total_pixels > MAX_IMAGE_PIXELS:
                    raise HTTPException(
                        status_code=413,
                        detail=f"'{name}' is too large when decoded (50 megapixels maximum).",
                    )
        with Image.open(BytesIO(payload)) as verification_image:
            verification_image.verify()
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(
            status_code=413,
            detail=f"'{name}' is too large when decoded (50 megapixels maximum).",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"'{name}' is not a valid image file.") from exc

    try:
        if image_format == "WEBP":
            # MuPDF cannot open WebP in all supported builds. Decode each frame
            # with Pillow and embed a lossless PNG representation instead.
            normalized = fitz.open()
            try:
                metadata_parts: list[str] = []
                with Image.open(BytesIO(payload)) as image:
                    software = image.getexif().get(0x0131)
                    if software:
                        metadata_parts.append(str(software))
                    xmp = image.info.get("xmp")
                    if isinstance(xmp, bytes):
                        metadata_parts.append(xmp.decode("utf-8", errors="replace"))
                    elif xmp:
                        metadata_parts.append(str(xmp))
                    for frame_index, (pixel_width, pixel_height) in enumerate(page_dimensions):
                        image.seek(frame_index)
                        frame = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
                        png = BytesIO()
                        frame.save(png, format="PNG")
                        page_width = max(1.0, pixel_width * 72 / 300)
                        page_height = max(1.0, pixel_height * 72 / 300)
                        page = normalized.new_page(width=page_width, height=page_height)
                        page.insert_image(page.rect, stream=png.getvalue())
                if metadata_parts:
                    normalized.set_metadata({
                        "producer": f"Source image metadata: {' '.join(metadata_parts)[:1000]}"
                    })
                converted = normalized.tobytes(garbage=4, deflate=True)
            finally:
                normalized.close()
            return converted

        image_type = "jpeg" if image_format == "JPEG" else image_format.lower()
        with fitz.open(stream=payload, filetype=image_type) as image_document:
            intermediate_bytes = image_document.convert_to_pdf()
        with fitz.open(stream=intermediate_bytes, filetype="pdf") as intermediate:
            normalized = fitz.open()
            try:
                for source_page in intermediate:
                    # At 300 DPI the detector render retains the image's native
                    # resolution without creating an enormous PDF page.
                    pixel_width, pixel_height = page_dimensions[source_page.number]
                    page_width = max(1.0, pixel_width * 72 / 300)
                    page_height = max(1.0, pixel_height * 72 / 300)
                    page = normalized.new_page(width=page_width, height=page_height)
                    page.show_pdf_page(page.rect, intermediate, source_page.number)
                converted = normalized.tobytes(garbage=4, deflate=True)
            finally:
                normalized.close()
    except (IndexError, RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"'{name}' could not be converted for screening.") from exc
    if not converted.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail=f"'{name}' could not be converted for screening.")
    return converted


async def _read_raw_upload(file: UploadFile) -> tuple[str, bytes]:
    """Validate the envelope and return the bytes exactly as uploaded."""

    name = file.filename or ""
    suffix = Path(name).suffix.lower()
    if suffix != ".pdf" and suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"'{name or 'Unnamed file'}' is not a supported PDF or image document.",
        )
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"'{name}' exceeds the 25 MB upload limit.")
    if suffix == ".pdf" and not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail=f"'{name}' is not a valid PDF stream.")
    return name, payload


def _normalize_document(name: str, payload: bytes) -> bytes:
    """Return screenable PDF bytes for an upload, converting images."""

    if Path(name).suffix.lower() == ".pdf":
        return payload
    return _image_to_pdf(name, payload)


async def _read_document_upload(file: UploadFile) -> tuple[str, bytes]:
    name, payload = await _read_raw_upload(file)
    return name, _normalize_document(name, payload)


async def _read_screening_upload(file: UploadFile) -> tuple[str, bytes, str]:
    """As `_read_document_upload`, plus the digest of the file as uploaded.

    The digest must come from the ORIGINAL bytes, not the normalized PDF: image
    conversion is not byte-deterministic (MuPDF stamps ids and dates), so hashing
    the converted output would give the same photo a new digest every time and
    duplicate detection would never fire for images.
    """

    name, payload = await _read_raw_upload(file)
    return name, _normalize_document(name, payload), content_digest(payload)


@api.post("/vlm/documents", status_code=status.HTTP_201_CREATED)
async def create_vlm_document(
    file: Annotated[UploadFile, File(description="PDF or image document to ask questions about")],
) -> dict[str, object]:
    if not vlm_is_configured():
        raise HTTPException(status_code=503, detail=str(get_vlm_status(ping=False)["message"]))
    name, payload = await _read_document_upload(file)
    record = vlm_documents.create(name, payload)
    try:
        sizes = pdf_page_sizes(vlm_documents.path_for(str(record["id"])))
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Document pages could not be read.") from exc
    return {**record, "page_count": len(sizes)}


@api.post("/vlm/documents/{document_id}/questions")
def ask_vlm_document_question(document_id: str, body: DocumentQuestionRequest) -> dict[str, object]:
    _, path = _vlm_document(document_id)
    if not vlm_is_configured():
        raise HTTPException(status_code=503, detail=str(get_vlm_status(ping=False)["message"]))
    try:
        return answer_document_question(
            path,
            body.question,
            {},
            current_page=body.current_page,
            max_pages=4,
            dpi=110,
        )
    except VLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="The document could not be prepared for visual question answering.",
        ) from exc


@api.get("/vlm/documents/{document_id}/manifest", response_model=DocumentManifest)
def get_vlm_document_manifest(document_id: str) -> DocumentManifest:
    _, path = _vlm_document(document_id)
    try:
        sizes = pdf_page_sizes(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Document pages could not be read.") from exc
    pages = [DocumentPage(page=page, width=size[0], height=size[1]) for page, size in sizes.items()]
    return DocumentManifest(page_count=len(pages), pages=pages)


@api.get("/vlm/documents/{document_id}/pages/{page_number}.png", response_class=Response)
def get_vlm_document_page(
    document_id: str,
    page_number: int,
    dpi: Annotated[int, Query(ge=96, le=200)] = 144,
) -> Response:
    _, path = _vlm_document(document_id)
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


def _requested_analyzers(analyzers: str | None) -> list[str] | None:
    return [item.strip() for item in analyzers.split(",")] if analyzers else None


def _requested_settings(settings: str | None) -> dict[str, dict[str, object]]:
    if not settings:
        return sanitize_analysis_settings(None)
    try:
        payload = json.loads(settings)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Advanced settings are not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Advanced settings must be an object.")
    return sanitize_analysis_settings(payload)


def _requested_profile(profile: str | None) -> dict[str, object] | None:
    """Sanitize the profile stamp a run was started under.

    Identity and intent only. Check selection and thresholds already ride on the
    job's own `analyzers`/`settings`, and what earns a tick is fixed per detector
    in `backend.checks` — the stamp is the human-readable record of the test, not
    a second source of execution config, so nothing here can change what runs or
    how a result is graded.
    """

    if not profile:
        return None
    try:
        payload = json.loads(profile)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Screening profile is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Screening profile must be an object.")
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    return {
        "id": str(payload.get("id") or "")[:120] or None,
        "name": name[:80],
        "goal": str(payload.get("goal") or "").strip()[:400],
        "guidance": str(payload.get("guidance") or "").strip()[:2000],
    }


@api.post("/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobState)
async def create_job(
    file: Annotated[UploadFile, File(description="PDF or image document to screen")],
    analyzers: str | None = None,
    settings: Annotated[str | None, Form()] = None,
    profile: Annotated[str | None, Form()] = None,
) -> JobState:
    name, payload, digest = await _read_screening_upload(file)
    try:
        job = store.create(
            name, payload, _requested_analyzers(analyzers), _requested_settings(settings),
            digest, _requested_profile(profile),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    submit_job(store, job["id"])
    return JobState.model_validate(job)


def _project_or_404(project_id: str) -> dict[str, object]:
    project = next((item for item in store.list_projects() if item["id"] == project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@api.post("/projects", status_code=status.HTTP_201_CREATED, response_model=ProjectState)
def create_project(body: ProjectNameRequest) -> ProjectState:
    project = store.create_project(body.name)
    return ProjectState.model_validate(_project_or_404(project["id"]))


@api.get("/projects", response_model=list[ProjectState])
def list_projects() -> list[ProjectState]:
    return [ProjectState.model_validate(item) for item in store.list_projects()]


@api.get("/projects/{project_id}", response_model=ProjectState)
def get_project(project_id: str) -> ProjectState:
    return ProjectState.model_validate(_project_or_404(project_id))


@api.post("/projects/{project_id}/rename", response_model=ProjectState)
def rename_project(project_id: str, body: ProjectNameRequest) -> ProjectState:
    try:
        renamed = store.rename_project(project_id, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if renamed is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return ProjectState.model_validate(_project_or_404(project_id))


@api.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str) -> Response:
    if not store.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api.get("/projects/{project_id}/batches", response_model=list[BatchSummary])
def list_project_batches(project_id: str) -> list[BatchSummary]:
    _project_or_404(project_id)
    return [BatchSummary.model_validate(item) for item in store.list_project_batches(project_id)]


@api.post("/batches", status_code=status.HTTP_202_ACCEPTED, response_model=BatchState)
async def create_batch(
    files: Annotated[list[UploadFile], File(description="PDF or image documents to screen together")],
    analyzers: str | None = None,
    settings: Annotated[str | None, Form()] = None,
    profile: Annotated[str | None, Form()] = None,
    batch_name: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
) -> BatchState:
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one PDF or image document.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=413, detail=f"Upload up to {MAX_BATCH_FILES} documents per batch."
        )
    uploads = [await _read_screening_upload(file) for file in files]
    try:
        batch = store.create_batch(
            uploads, _requested_analyzers(analyzers),
            _requested_settings(settings), _requested_profile(profile),
            str(batch_name or "").strip()[:80] or None,
            str(project_id or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for job_id in batch["job_ids"]:
        submit_job(store, job_id)
    return BatchState.model_validate(store.batch_state(batch["id"]))


@api.get("/batches", response_model=list[BatchSummary])
def list_batches() -> list[BatchSummary]:
    return [BatchSummary.model_validate(item) for item in store.list_batches()]


@api.get("/batches/{batch_id}", response_model=BatchState)
def get_batch(batch_id: str) -> BatchState:
    state = store.batch_state(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return BatchState.model_validate(state)


@api.get("/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobState.model_validate(job)


@api.get("/jobs/{job_id}/document", response_class=FileResponse)
def get_document(job_id: str) -> FileResponse:
    job = store.get(job_id)
    path = store.path_for(job_id)
    if job is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    safe_name = Path(str(job["filename"])).name.replace('"', "")
    if Path(safe_name).suffix.lower() != ".pdf":
        safe_name = f"{Path(safe_name).stem}.pdf"
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@api.get("/jobs/{job_id}/document/manifest", response_model=DocumentManifest)
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


@api.get("/jobs/{job_id}/document/pages/{page_number}.png", response_class=Response)
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


@api.get("/jobs/{job_id}/artifacts/{analyzer}/{filename:path}", response_class=FileResponse)
def get_artifact(job_id: str, analyzer: str, filename: str) -> FileResponse:
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    artifact_root = (store.data_dir / f"{job_id}-{analyzer}-report").resolve()
    path = (artifact_root / filename).resolve()
    if path != artifact_root and artifact_root not in path.parents:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    safe_name = path.name.replace('"', "")
    return FileResponse(
        path,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@api.post("/jobs/{job_id}/review")
def save_review(job_id: str, review: ReviewRequest, request: Request) -> dict[str, object]:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    # Stamp the versions and the exact settings the decision was made against.
    # Without them a verdict cannot be reproduced once a detector changes.
    saved = {
        **review.model_dump(),
        "reviewer": request.state.user.display_name,
        "reviewer_id": request.state.user.id,
        "reviewer_email": request.state.user.email,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "detectors_version": DETECTORS_VERSION,
        "analysis_settings": job.get("settings", {}),
    }
    store.update(job_id, review=saved)
    return saved


@api.get("/jobs/{job_id}/duplicates", response_model=list[PriorScreening])
def job_duplicates(job_id: str) -> list[PriorScreening]:
    """Earlier screenings of byte-identical content, for a job already stored."""

    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    digest = job.get("sha256")
    if not digest:
        return []
    return [
        PriorScreening.model_validate(item)
        for item in store.prior_screenings(str(digest), exclude_job_id=job_id)
    ]


@api.post("/jobs/{job_id}/rerun", response_model=JobState, status_code=status.HTTP_202_ACCEPTED)
def rerun_job(job_id: str, body: RerunRequest) -> JobState:
    """Re-run checks that did not complete, keeping the results that did."""

    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not store.path_for(job_id).is_file():
        raise HTTPException(
            status_code=409,
            detail="The stored document is no longer on this device, so these checks cannot be re-run.",
        )
    requested = body.analyzers or [
        analyzer
        for analyzer, run in job.get("analyzer_runs", {}).items()
        if run.get("status") == "failed"
    ]
    unknown = sorted(set(requested) - set(job.get("analyzers", [])))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"This document did not run: {', '.join(unknown)}"
        )
    reset = store.reset_analyzers(job_id, requested)
    if not reset:
        raise HTTPException(
            status_code=409, detail="There are no finished checks to re-run on this document."
        )
    submit_job(store, job_id, reset)
    return JobState.model_validate(store.get(job_id))


@api.post("/jobs/{job_id}/unanalyzable", response_model=JobState)
def set_unanalyzable(job_id: str, body: UnanalyzableRequest) -> JobState:
    """Record that a document cannot be screened at all.

    A file that will never analyze has to leave the flagged queue, or it sits
    there forever looking like unfinished review work.
    """

    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    store.update(
        job_id,
        unanalyzable=body.unanalyzable,
        unanalyzable_reason=body.reason.strip() or None if body.unanalyzable else None,
    )
    return JobState.model_validate(store.get(job_id))


@api.post("/jobs/{job_id}/questions")
def ask_job_question(job_id: str, body: DocumentQuestionRequest) -> dict[str, object]:
    """Ask the VLM about a screened document, with its detector results in hand.

    Document-scoped on purpose: the model answers about this page set, and the
    analyzer outcomes steer which pages it is shown.
    """

    job = store.get(job_id)
    path = store.path_for(job_id)
    if job is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")
    if not vlm_is_configured():
        raise HTTPException(status_code=503, detail=str(get_vlm_status(ping=False)["message"]))
    try:
        return answer_document_question(
            path,
            body.question,
            job.get("results", {}),
            current_page=body.current_page,
            max_pages=4,
            dpi=110,
        )
    except VLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="The document could not be prepared for visual question answering.",
        ) from exc


def _report_filename(stem: str, suffix: str) -> str:
    safe = "".join(character for character in stem if character.isalnum() or character in "-_") or "case"
    return f"parakh-{safe}.{suffix}"


@api.get("/jobs/{job_id}/report.json")
def job_report_json(job_id: str) -> dict[str, object]:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return case_report(job, store.batch_id_for_job(job_id))


@api.get("/jobs/{job_id}/report.html", response_class=Response)
def job_report_html(job_id: str) -> Response:
    """A printable case report. Print to PDF is the export path."""

    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    html = render_case_html(case_report(job, store.batch_id_for_job(job_id)))
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{_report_filename(job_id[:8], "html")}"'},
    )


@api.get("/batches/{batch_id}/report.csv", response_class=Response)
def batch_report_csv(batch_id: str) -> Response:
    state = store.batch_state(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return Response(
        content=batch_csv(state),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_report_filename(batch_id[:8], "csv")}"'
        },
    )


@api.get("/batches/{batch_id}/report.xlsx", response_class=Response)
def batch_report_xlsx(batch_id: str) -> Response:
    state = store.batch_state(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    photo_duplicates = batch_photo_duplicates(state["jobs"], store.data_dir)
    qr_duplicates = batch_qr_duplicates(state["jobs"])
    return Response(
        content=batch_xlsx(state, photo_duplicates, qr_duplicates),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{_report_filename(batch_id[:8], "xlsx")}"'
        },
    )


@api.get("/batches/{batch_id}/report.html", response_class=Response)
def batch_report_html(batch_id: str) -> Response:
    state = store.batch_state(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return Response(
        content=render_batch_html(state),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{_report_filename(batch_id[:8], "html")}"'
        },
    )


def _pdf_shape(payload: bytes) -> tuple[int, bool]:
    """Return (page_count, has_text_layer) for a PDF already known to open."""

    with fitz.open(stream=payload, filetype="pdf") as document:
        page_count = len(document)
        has_text = any(page.get_text().strip() for page in document)
    return page_count, has_text


async def _preflight_file(file: UploadFile) -> PreflightFile:
    """Inspect one upload candidate without creating a job for it.

    Every branch returns a row. A file that cannot be screened must be reported,
    not raised, or one bad file in a batch of forty hides the other thirty-nine.
    """

    name = file.filename or "Unnamed file"
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    size = len(payload)
    suffix = Path(name).suffix.lower()

    if size > MAX_UPLOAD_BYTES:
        return PreflightFile(filename=name, size_bytes=size, readable=False,
                             blocker="Larger than the 25 MB upload limit.")
    if suffix != ".pdf" and suffix not in SUPPORTED_IMAGE_SUFFIXES:
        return PreflightFile(filename=name, size_bytes=size, readable=False,
                             blocker="Not a supported PDF or image document.")

    if suffix == ".pdf":
        if not payload.startswith(b"%PDF-"):
            return PreflightFile(filename=name, size_bytes=size, readable=False,
                                 blocker="Not a valid PDF stream — the file may be corrupt.")
        stored = payload
    else:
        try:
            stored = _image_to_pdf(name, payload)
        except HTTPException as exc:
            return PreflightFile(filename=name, size_bytes=size, readable=False,
                                 blocker=str(exc.detail))

    try:
        page_count, has_text = _pdf_shape(stored)
    except (OSError, RuntimeError, ValueError):
        return PreflightFile(filename=name, size_bytes=size, readable=False,
                             blocker="The document could not be opened for screening.")
    if page_count == 0:
        return PreflightFile(filename=name, size_bytes=size, readable=False,
                             blocker="The document has no pages.")

    # Digest the file as uploaded, matching what intake records. Hashing the
    # normalized PDF instead would break duplicate detection for images, whose
    # conversion is not byte-stable.
    digest = content_digest(payload)
    warning = None
    if not has_text and suffix == ".pdf":
        warning = ("No text layer — metadata, font, and readability checks have little to read on a "
                   "pure scan.")
    return PreflightFile(
        filename=name,
        size_bytes=size,
        sha256=digest,
        readable=True,
        page_count=page_count,
        warning=warning,
        prior_screenings=[
            PriorScreening.model_validate(item) for item in store.prior_screenings(digest)
        ],
    )


@api.post("/documents/preflight", response_model=PreflightReport)
async def preflight_documents(
    files: Annotated[list[UploadFile], File(description="Documents to check before screening")],
) -> PreflightReport:
    """Check candidate uploads for duplicates and unreadable files.

    Catching a resubmitted file here is worth more than any single detector, and
    it costs nothing: the digest is already the join key.
    """

    if not files:
        raise HTTPException(status_code=422, detail="Select at least one PDF or image document.")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=413, detail=f"Upload up to {MAX_BATCH_FILES} documents per batch."
        )
    rows = [await _preflight_file(file) for file in files]
    return PreflightReport(
        files=rows,
        duplicate_count=sum(bool(row.prior_screenings) for row in rows),
        blocked_count=sum(not row.readable for row in rows),
    )


@api.post("/samples/case", status_code=status.HTTP_202_ACCEPTED, response_model=BatchState)
def create_sample_case() -> BatchState:
    """Screen a generated sample document so first run has something to read."""

    try:
        payload = build_sample_document()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="The sample document could not be generated.") from exc
    batch = store.create_batch(
        [(SAMPLE_FILENAME, payload)], SAMPLE_ANALYZERS, sanitize_analysis_settings(None),
        name="Sample case",
    )
    for job_id in batch["job_ids"]:
        submit_job(store, job_id)
    return BatchState.model_validate(store.batch_state(batch["id"]))


app.include_router(api)
