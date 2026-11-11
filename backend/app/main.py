"""FastAPI application: HTTP API, optional static frontend, and in-process workers."""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ai.llm import ai_status
from .citations.formatter import format_all
from .config import get_settings
from .errors import MESSAGES, AppError
from .exports.binary import pdf_unicode_supported, to_docx, to_pdf
from .exports.document import build_document, to_markdown, to_text
from .jobs.service import JobService
from .jobs.worker import Worker
from .logging_config import setup_logging
from .media.audio import ffmpeg_exe, ffprobe_exe
from .models import (
    CitationMetadata,
    CitationSet,
    JobCreated,
    JobOptions,
    JobState,
    Results,
    Transcript,
)
from .sources.resolver import resolve, supported_labels
from .storage.db import Store

log = logging.getLogger("transcribly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.work_dir.mkdir(parents=True, exist_ok=True)
    store = Store(s.db_path)
    worker = Worker(store)
    app.state.store = store
    app.state.service = JobService(s, store, worker.wake)
    app.state.started = time.time()
    if s.run_worker_in_api:
        worker.start()
    log.info("Transcribly %s started (model=%s, concurrency=%d)", __version__, s.whisper_model, s.worker_concurrency)
    yield
    worker.shutdown()


app = FastAPI(title="Transcribly API", version=__version__, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Job-Token", "Idempotency-Key"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": {"code": "invalid_request", "message": "Some of the submitted details are invalid."}},
                        status_code=422)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse({"error": {"code": "internal", "message": MESSAGES["internal"]}}, status_code=500)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def service(request: Request) -> JobService:
    return request.app.state.service


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


Token = Annotated[str | None, Header(alias="X-Job-Token")]
Svc = Annotated[JobService, Depends(service)]


# --- routes -------------------------------------------------------------------

@app.get("/api/health")
def health(request: Request) -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": round(time.time() - request.app.state.started),
        "engine": s.transcription_engine,
        "default_model": s.whisper_model,
        "ffmpeg": bool(ffmpeg_exe()),
        "ffprobe": bool(ffprobe_exe()) or "pyav",
        "ai_notes": ai_status(),
        "worker_in_api": s.run_worker_in_api,
        "pdf_unicode_font": pdf_unicode_supported(),
    }


@app.get("/api/config")
def public_config() -> dict:
    s = get_settings()
    return {
        "max_upload_mb": s.max_upload_mb,
        "max_duration_seconds": s.max_duration_seconds,
        "default_model": s.whisper_model,
        "models": s.allowed_models,
        "supported_sources": supported_labels(),
        "retention_hours": s.result_retention_hours,
        "keep_media": s.keep_media,
        "ai_notes": ai_status(),
    }


@app.post("/api/sources/check")
def check_source(payload: dict) -> dict:
    """Lightweight URL check used by the source-type indicator (no network access)."""
    r = resolve(str(payload.get("url", "")))
    return {"kind": r.kind, "label": r.label, "url": r.url}


@app.post("/api/jobs", response_model=JobCreated, status_code=201)
async def create_job(
    request: Request,
    svc: Svc,
    url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    language: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    prefer_captions: Annotated[bool, Form()] = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobCreated:
    if idempotency_key and not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", idempotency_key):
        idempotency_key = None
    opts = JobOptions(language=(language or None) if language != "auto" else None, model=model or None,
                      prefer_captions=prefer_captions)
    ip = client_ip(request)
    if file is not None and file.filename:
        size = int(request.headers.get("content-length") or 0) or None
        try:
            return await run_in_threadpool(svc.create_upload_job, file.file, file.filename, opts, ip,
                                           idempotency_key, size)
        finally:
            await file.close()
    if url and url.strip():
        return await run_in_threadpool(svc.create_url_job, url, opts, ip, idempotency_key)
    raise AppError("missing_input")


@app.get("/api/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str, svc: Svc, x_job_token: Token = None) -> JobState:
    return svc.state(svc.authorize(job_id, x_job_token))


@app.get("/api/jobs/{job_id}/results", response_model=Results)
def get_results(job_id: str, svc: Svc, x_job_token: Token = None) -> Results:
    return svc.results(svc.authorize(job_id, x_job_token))


@app.get("/api/jobs/{job_id}/transcript", response_model=Transcript)
def get_transcript(job_id: str, svc: Svc, x_job_token: Token = None) -> Transcript:
    return svc.results(svc.authorize(job_id, x_job_token)).transcript


@app.patch("/api/jobs/{job_id}/metadata", response_model=CitationSet)
def patch_metadata(job_id: str, meta: CitationMetadata, svc: Svc, x_job_token: Token = None) -> CitationSet:
    job = svc.authorize(job_id, x_job_token)
    return format_all(svc.update_metadata(job, meta))


@app.post("/api/citations", response_model=CitationSet)
def format_citations(meta: CitationMetadata) -> CitationSet:
    return format_all(meta)


EXPORTS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": ("application/pdf", "pdf"),
}


def _filename(title: str | None, ext: str) -> str:
    base = re.sub(r"[^\w\- ]+", "", title or "transcript").strip()[:80] or "transcript"
    return f"{base.replace(' ', '_')}_Transcribly.{ext}"


@app.get("/api/jobs/{job_id}/export/{fmt}")
def export(job_id: str, fmt: str, svc: Svc, x_job_token: Token = None, style: str = "apa",
           timestamps: bool = True, sections: str | None = None) -> Response:
    if fmt not in EXPORTS:
        raise AppError("invalid_option", 404, "Export format not supported. Choose TXT, Markdown, PDF or DOCX.")
    res = svc.results(svc.authorize(job_id, x_job_token))
    wanted = set(sections.split(",")) if sections else None
    blocks = build_document(res, style=style, timestamps=timestamps, sections=wanted)
    try:
        if fmt == "txt":
            body: bytes = to_text(blocks).encode("utf-8")
        elif fmt == "md":
            body = to_markdown(blocks).encode("utf-8")
        elif fmt == "docx":
            body = to_docx(blocks)
        else:
            body = to_pdf(blocks, res.metadata.title or "Transcript")
    except Exception as e:
        log.exception("export %s failed", fmt)
        raise AppError("export_failed", 500) from e
    mime, ext = EXPORTS[fmt]
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{_filename(res.metadata.title, ext)}"'})


@app.post("/api/jobs/{job_id}/cancel", response_model=JobState)
def cancel_job(job_id: str, svc: Svc, x_job_token: Token = None) -> JobState:
    job = svc.authorize(job_id, x_job_token)
    svc.cancel(job)
    return svc.state(svc.authorize(job_id, x_job_token))


@app.post("/api/jobs/{job_id}/retry", response_model=JobState)
def retry_job(job_id: str, svc: Svc, x_job_token: Token = None) -> JobState:
    job = svc.authorize(job_id, x_job_token)
    svc.retry(job)
    return svc.state(svc.authorize(job_id, x_job_token))


@app.post("/api/jobs/{job_id}/notes", response_model=JobState)
def regenerate_notes(job_id: str, svc: Svc, x_job_token: Token = None) -> JobState:
    """Rewrite the study notes of a finished job (e.g. after enabling an AI model)."""
    svc.regenerate_notes(svc.authorize(job_id, x_job_token))
    return svc.state(svc.authorize(job_id, x_job_token))


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, svc: Svc, x_job_token: Token = None) -> Response:
    svc.delete(svc.authorize(job_id, x_job_token))
    return Response(status_code=204)


# --- static frontend (single-container deployments) -----------------------------
if _settings.frontend_dist and _settings.frontend_dist.exists():
    dist = _settings.frontend_dist.resolve()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(dist / "index.html")

    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
