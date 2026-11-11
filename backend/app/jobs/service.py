"""Job creation, access control, and result retrieval. Used by the HTTP routes."""

from __future__ import annotations

import hashlib
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, BinaryIO

from ..citations.formatter import format_all
from ..config import Settings
from ..errors import AppError
from ..media.audio import probe
from ..models import (
    STAGE_LABELS,
    TERMINAL,
    ApiErrorBody,
    CitationMetadata,
    JobCreated,
    JobOptions,
    JobState,
    JobStatus,
    Results,
    StageEvent,
    StudyMaterials,
    Transcript,
)
from ..security import (
    job_token,
    new_job_id,
    safe_display_name,
    valid_job_id,
    verify_token,
)
from ..sources.resolver import resolve
from ..storage.db import Store


class JobService:
    def __init__(self, settings: Settings, store: Store, wake: Any | None = None):
        self.s = settings
        self.store = store
        self.wake = wake
        self.secret = settings.secret_key or store.kv_set_if_absent("secret_key", secrets.token_hex(32))

    # --- creation -----------------------------------------------------------
    def _check_capacity(self, ip_hash: str) -> None:
        if self.s.max_queued_jobs and self.store.count_jobs(("queued", "running")) >= self.s.max_queued_jobs:
            raise AppError("server_busy", 503)
        if self.s.jobs_per_ip_per_hour and self.store.count_recent_by_ip(ip_hash, time.time() - 3600) >= self.s.jobs_per_ip_per_hour:
            raise AppError("too_many_requests", 429)

    def _validate_options(self, opts: JobOptions) -> None:
        if opts.model and opts.model not in self.s.allowed_models:
            raise AppError("invalid_option")
        if opts.language and (len(opts.language) > 8 or not opts.language.replace("-", "").isalpha()):
            raise AppError("invalid_option")

    def ip_hash(self, ip: str) -> str:
        return hashlib.sha256(f"{self.secret}:{ip}".encode()).hexdigest()[:24]

    def _existing(self, idem: str | None) -> JobCreated | None:
        if not idem:
            return None
        job = self.store.find_by_idempotency_key(idem)
        if job:
            return JobCreated(job_id=job["id"], token=job_token(self.secret, job["id"]), status=JobStatus(job["status"]))
        return None

    def create_url_job(self, url: str, opts: JobOptions, ip: str, idem: str | None) -> JobCreated:
        existing = self._existing(idem)
        if existing:
            return existing
        self._validate_options(opts)
        resolved = resolve(url)  # raises a friendly error for invalid/unsupported URLs
        if resolved.kind == "direct":
            from ..security import assert_public_url

            assert_public_url(resolved.url, self.s.allow_private_network_urls)
        ip_h = self.ip_hash(ip)
        self._check_capacity(ip_h)
        job_id = new_job_id()
        model = opts.model or self.s.whisper_model
        cache_key = hashlib.sha256(
            f"{resolved.url}|{model}|{opts.language or ''}|{int(opts.prefer_captions)}".encode()).hexdigest()
        base = {
            "id": job_id, "source_type": resolved.kind, "source_url": resolved.url, "options": opts.model_dump(),
            "metadata": CitationMetadata(url=resolved.url, platform=resolved.platform,
                                         source_type="youtube_video" if resolved.kind == "youtube" else "online_video").model_dump(),
            "cache_key": cache_key, "idempotency_key": idem, "client_ip": ip_h,
        }
        cached = self.store.find_cached(cache_key) if self.s.cache_results else None
        if cached:
            res = self.store.load_results(cached["id"])
            if res:
                now = time.time()
                self.store.create_job({
                    **base, "status": JobStatus.completed.value, "metadata": cached["metadata"],
                    "duration": cached["duration"], "warnings": cached["warnings"], "completed_at": now,
                    "stages": [{"stage": "cache", "label": STAGE_LABELS["cache"], "started_at": now, "finished_at": now}],
                    "expires_at": now + self.s.result_retention_hours * 3600 if self.s.result_retention_hours else None,
                })
                self.store.save_results(job_id, res["transcript"], res["study"], res["summary_error"])
                return JobCreated(job_id=job_id, token=job_token(self.secret, job_id), status=JobStatus.completed)
        self.store.create_job({**base, "status": JobStatus.queued.value})
        self._wake()
        return JobCreated(job_id=job_id, token=job_token(self.secret, job_id), status=JobStatus.queued)

    def create_upload_job(self, stream: BinaryIO, filename: str | None, opts: JobOptions, ip: str,
                          idem: str | None, declared_size: int | None) -> JobCreated:
        existing = self._existing(idem)
        if existing:
            return existing
        self._validate_options(opts)
        limit = self.s.max_upload_bytes
        if limit and declared_size and declared_size > limit:
            raise AppError("too_large", 413)
        ip_h = self.ip_hash(ip)
        self._check_capacity(ip_h)
        job_id = new_job_id()
        work = self.s.work_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        dest = work / "source.bin"  # never derived from the user's filename
        try:
            size = 0
            with dest.open("wb") as f:
                while chunk := stream.read(1 << 20):
                    size += len(chunk)
                    if limit and size > limit:
                        raise AppError("too_large", 413)
                    f.write(chunk)
            info = probe(dest)
            if info.duration and self.s.max_duration_seconds and info.duration > self.s.max_duration_seconds:
                raise AppError("too_long", 413)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise
        display = safe_display_name(filename)
        title = Path(display).stem or None
        self.store.create_job({
            "id": job_id, "status": JobStatus.queued.value, "source_type": "upload", "upload_name": display,
            "options": opts.model_dump(), "duration": info.duration, "idempotency_key": idem, "client_ip": ip_h,
            "metadata": CitationMetadata(title=title, source_type="uploaded_file").model_dump(),
        })
        self._wake()
        return JobCreated(job_id=job_id, token=job_token(self.secret, job_id), status=JobStatus.queued)

    def _wake(self) -> None:
        if self.wake is not None:
            self.wake.set()

    # --- access -------------------------------------------------------------
    def authorize(self, job_id: str, token: str | None) -> dict[str, Any]:
        if not valid_job_id(job_id):
            raise AppError("not_found", 404)
        job = self.store.get_job(job_id)
        if job is None:
            raise AppError("not_found", 404)
        if not verify_token(self.secret, job_id, token):
            raise AppError("forbidden", 403)
        return job

    def state(self, job: dict[str, Any]) -> JobState:
        status = JobStatus(job["status"])
        stage = job["stage"] if status == JobStatus.running else None
        return JobState(
            job_id=job["id"], status=status, stage=stage,
            stage_label=STAGE_LABELS.get(stage) if stage else ("Waiting in queue" if status == JobStatus.queued else None),
            detail=job["detail"] if status in (JobStatus.running, JobStatus.queued) else None,
            progress=job["progress"] if status == JobStatus.running else None,
            stages=[StageEvent(**e) for e in job["stages"] or []],
            source_type=job["source_type"],
            title=(job["metadata"] or {}).get("title") or job.get("upload_name"),
            duration=job["duration"],
            error=ApiErrorBody(code=job["error_code"], message=job["error_message"]) if job["error_code"] and status == JobStatus.failed else None,
            warnings=job["warnings"] or [],
            created_at=job["created_at"], updated_at=job["updated_at"], expires_at=job["expires_at"],
        )

    def results(self, job: dict[str, Any]) -> Results:
        if job["status"] != JobStatus.completed.value:
            raise AppError("not_ready", 409)
        res = self.store.load_results(job["id"])
        if res is None:
            raise AppError("not_found", 404)
        meta = CitationMetadata(**job["metadata"])
        return Results(
            job_id=job["id"], metadata=meta, citations=format_all(meta),
            transcript=Transcript(**res["transcript"]),
            study=StudyMaterials(**res["study"]) if res["study"] else None,
            summary_error=res["summary_error"], warnings=job["warnings"] or [],
        )

    def update_metadata(self, job: dict[str, Any], meta: CitationMetadata) -> CitationMetadata:
        current = job["metadata"] or {}
        merged = {**current, **meta.model_dump()}
        self.store.update_job(job["id"], metadata=merged)
        return CitationMetadata(**merged)

    def cancel(self, job: dict[str, Any]) -> None:
        status = JobStatus(job["status"])
        if status in TERMINAL:
            return
        if status == JobStatus.queued:
            self.store.update_job(job["id"], status=JobStatus.cancelled.value, cancel_requested=1,
                                  expires_at=time.time() + 3600)
            shutil.rmtree(self.s.work_dir / job["id"], ignore_errors=True)
        else:
            self.store.update_job(job["id"], cancel_requested=1)

    def retry(self, job: dict[str, Any]) -> None:
        if job["status"] not in (JobStatus.failed.value, JobStatus.cancelled.value):
            raise AppError("invalid_option", 409, "Only failed or cancelled jobs can be retried.")
        if job["source_type"] == "upload" and not any((self.s.work_dir / job["id"]).glob("*")):
            raise AppError("invalid_media", 409, "The uploaded file has already been deleted. Please upload it again.")
        self.store.update_job(job["id"], status=JobStatus.queued.value, error_code=None, error_message=None,
                              cancel_requested=0, attempts=0, expires_at=None, stage=None, detail=None, stages=[])
        self._wake()

    def regenerate_notes(self, job: dict[str, Any]) -> None:
        """Queue a notes-only run (keeps the transcript; rewrites summary, key points, topics...)."""
        if job["status"] != JobStatus.completed.value or self.store.load_results(job["id"]) is None:
            raise AppError("not_ready", 409)
        options = {**(job.get("options") or {}), "_notes_only": True}
        self.store.update_job(job["id"], status=JobStatus.queued.value, options=options, stages=[], stage=None,
                              detail=None, progress=None, cancel_requested=0, error_code=None,
                              error_message=None, attempts=0, expires_at=None)
        self._wake()

    def delete(self, job: dict[str, Any]) -> None:
        if job["status"] == JobStatus.running.value:
            self.store.update_job(job["id"], cancel_requested=1)
            # the worker notices within ~1s; the row is removed after it stops
            self.store.update_job(job["id"], expires_at=time.time())
            return
        self.store.delete_job(job["id"])
        shutil.rmtree(self.s.work_dir / job["id"], ignore_errors=True)
