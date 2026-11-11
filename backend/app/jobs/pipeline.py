"""The processing pipeline for one job. Runs inside a worker thread."""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from ..ai.llm import free_ai_memory
from ..citations.formatter import format_all
from ..config import get_settings
from ..errors import MESSAGES, AppError, Cancelled
from ..media import audio
from ..models import (
    STAGE_LABELS,
    CitationMetadata,
    JobOptions,
    JobStatus,
    Segment,
    Transcript,
)
from ..sources import fetch
from ..sources.resolver import resolve
from ..storage.db import Store
from ..summarization.service import get_summarizer
from ..transcription.engine import get_engine, release_engine
from ..transcription.segments import (
    build_paragraphs,
    dedupe_boundary,
    drop_adjacent_duplicates,
    format_timestamp,
    reindex,
)

log = logging.getLogger(__name__)


class JobContext:
    """Stage bookkeeping + throttled progress writes and cancellation checks."""

    def __init__(self, store: Store, job: dict[str, Any]):
        self.store = store
        self.job = job
        self.id: str = job["id"]
        self.stages: list[dict] = [s for s in job.get("stages") or [] if s.get("finished_at")]
        self._last_write = 0.0
        self._last_cancel_check = 0.0
        self._cancelled = False

    def begin(self, stage: str, detail: str | None = None) -> None:
        now = time.time()
        if self.stages and self.stages[-1].get("finished_at") is None:
            self.stages[-1]["finished_at"] = now
        self.stages.append({"stage": stage, "label": STAGE_LABELS[stage], "started_at": now, "finished_at": None})
        self.store.update_job(self.id, stage=stage, detail=detail, progress=None, stages=self.stages)
        log.info("job %s stage=%s", self.id[:8], stage)

    def finish_stages(self) -> None:
        if self.stages and self.stages[-1].get("finished_at") is None:
            self.stages[-1]["finished_at"] = time.time()

    def detail(self, detail: str | None, progress: float | None = None, force: bool = False) -> None:
        now = time.time()
        if force or now - self._last_write > 1.0:
            self._last_write = now
            self.store.update_job(self.id, detail=detail, progress=progress)

    def should_cancel(self) -> bool:
        now = time.time()
        if not self._cancelled and now - self._last_cancel_check > 1.0:
            self._last_cancel_check = now
            row = self.store.get_job(self.id)
            self._cancelled = bool(row is None or row["cancel_requested"])
        return self._cancelled

    def check_cancel(self) -> None:
        if self.should_cancel():
            raise Cancelled()


def run_job(store: Store, job: dict[str, Any]) -> None:
    s = get_settings()
    ctx = JobContext(store, job)
    work = s.work_dir / job["id"]
    work.mkdir(parents=True, exist_ok=True)
    opts = JobOptions(**{k: v for k, v in (job.get("options") or {}).items() if not k.startswith("_")})
    meta = CitationMetadata(**(job.get("metadata") or {}))
    warnings: list[str] = list(job.get("warnings") or [])
    # "Write AI study notes" on an existing result re-runs only the notes step
    notes_only = bool((job.get("options") or {}).get("_notes_only"))
    try:
        if notes_only:
            saved = store.load_results(ctx.id)
            if saved is None:
                raise AppError("not_found", 404)
            transcript = Transcript(**saved["transcript"])
        else:
            free_ai_memory()  # a local AI model and Whisper may not both fit in RAM
            transcript = _transcribe(ctx, job, work, opts, meta, warnings)
        ctx.check_cancel()

        summarizer = get_summarizer()
        if summarizer.uses_ai:
            release_engine()
            ctx.begin("ai_notes", "Reading the transcript")
        else:
            ctx.begin("summarizing", "Building the summary, key points and topic outline from the transcript")
        study = None
        summary_error = None
        try:
            study = summarizer.summarize(transcript, meta.title, report=lambda d, f: ctx.detail(d, f),
                                         should_cancel=ctx.should_cancel)
        except Cancelled:
            raise
        except Exception:
            log.exception("summary failed for job %s", ctx.id[:8])
            summary_error = MESSAGES_SUMMARY
        breaks = {t.start for t in study.topics[1:]} if study else set()
        transcript.paragraphs = build_paragraphs(transcript.segments, breaks=breaks)

        ctx.begin("citations")
        meta = _final_metadata(store, ctx.id, meta, transcript)
        format_all(meta)  # validates that every style renders

        ctx.begin("finalizing")
        store.save_results(ctx.id, transcript.model_dump(), study.model_dump() if study else None, summary_error)
        ctx.finish_stages()
        now = time.time()
        options = {k: v for k, v in (job.get("options") or {}).items() if k != "_notes_only"}
        store.update_job(ctx.id, status=JobStatus.completed.value, stage=None, detail=None, progress=None,
                         stages=ctx.stages, completed_at=now, metadata=meta.model_dump(), warnings=warnings,
                         duration=transcript.duration, options=options, cancel_requested=0,
                         expires_at=now + s.result_retention_hours * 3600 if s.result_retention_hours else None)
        store.clear_chunks(ctx.id)
        _cleanup(work)
        log.info("job %s completed (%d segments)", ctx.id[:8], len(transcript.segments))
    except Cancelled:
        ctx.finish_stages()
        row = store.get_job(ctx.id)
        if notes_only and row and not (row["expires_at"] and row["expires_at"] <= time.time()):
            # keep the existing result when regenerating notes is cancelled
            options = {k: v for k, v in (row.get("options") or {}).items() if k != "_notes_only"}
            store.update_job(ctx.id, status=JobStatus.completed.value, stage=None, detail=None, progress=None,
                             stages=ctx.stages, options=options, cancel_requested=0,
                             expires_at=time.time() + s.result_retention_hours * 3600 if s.result_retention_hours else None)
            return
        store.clear_chunks(ctx.id)
        _cleanup(work)
        if row and row["expires_at"] and row["expires_at"] <= time.time():  # deleted by the user while running
            store.delete_job(ctx.id)
            shutil.rmtree(work, ignore_errors=True)
            return
        store.update_job(ctx.id, status=JobStatus.cancelled.value, detail=None, progress=None, stages=ctx.stages,
                         expires_at=time.time() + 3600)
        log.info("job %s cancelled", ctx.id[:8])
    except AppError as e:
        _fail(store, ctx, work, e.code, e.message, keep_work=e.retryable)
    except Exception:
        log.exception("job %s failed", ctx.id[:8])
        _fail(store, ctx, work, "transcription_failed", MESSAGES["transcription_failed"], keep_work=True)


MESSAGES_SUMMARY = ("The transcript is available, but automatic summarization could not be completed. "
                    "You can still copy the transcript.")


def _fail(store: Store, ctx: JobContext, work: Path, code: str, message: str, keep_work: bool) -> None:
    s = get_settings()
    ctx.finish_stages()
    store.update_job(ctx.id, status=JobStatus.failed.value, error_code=code, error_message=message,
                     detail=None, progress=None, stages=ctx.stages,
                     expires_at=time.time() + max(1, s.result_retention_hours) * 3600)
    if not keep_work:
        store.clear_chunks(ctx.id)
        _cleanup(work)
    else:  # keep prepared audio so a retry can resume, but never the original download/upload
        for p in work.glob("source.*"):
            if not s.keep_media:
                p.unlink(missing_ok=True)
    log.info("job %s failed: %s", ctx.id[:8], code)


def _cleanup(work: Path) -> None:
    if not get_settings().keep_media:
        shutil.rmtree(work, ignore_errors=True)


def _final_metadata(store: Store, job_id: str, meta: CitationMetadata, t: Transcript) -> CitationMetadata:
    # The user may have edited metadata while the job ran; never overwrite their edits.
    current = store.get_job(job_id)
    stored = CitationMetadata(**(current or {}).get("metadata", {})) if current else meta
    updates: dict[str, Any] = {}
    if not stored.accessed:
        updates["accessed"] = dt.date.today().isoformat()
    if not stored.duration_seconds and t.duration:
        updates["duration_seconds"] = round(t.duration)
    return stored.model_copy(update=updates)


def _check_duration(duration: float | None) -> None:
    s = get_settings()
    if duration and s.max_duration_seconds and duration > s.max_duration_seconds:
        raise AppError("too_long", 413, MESSAGES["too_long"] + f" This server accepts up to "
                       f"{format_timestamp(s.max_duration_seconds)} of media.")


def _transcribe(ctx: JobContext, job: dict, work: Path, opts: JobOptions, meta: CitationMetadata,
                warnings: list[str]) -> Transcript:
    s = get_settings()
    store = ctx.store
    wav = work / "audio.wav"
    src: Path | None = None
    info_duration: float | None = job.get("duration")

    if job["source_type"] == "upload":
        ctx.begin("validating", "Checking the uploaded file")
        if not wav.exists():
            src = next(iter(work.glob("source.*")), None)
            if src is None:
                raise AppError("invalid_media", message="The uploaded file is no longer available. Please upload it again.")
            mi = audio.probe(src)
            info_duration = mi.duration
            _check_duration(info_duration)
    else:
        ctx.begin("validating", "Checking the link")
        resolved = resolve(job["source_url"])
        ctx.begin("metadata", "Looking up the video's title, creator and date")
        info = fetch.fetch_info(resolved)
        if info.is_live:
            raise AppError("live_stream")
        _check_duration(info.duration)
        info_duration = info.duration
        if not job.get("metadata", {}).get("_filled"):
            meta = meta.model_copy(update={
                "title": meta.title or info.title, "creator": meta.creator or info.creator,
                "channel": meta.channel or info.channel, "published": meta.published or info.published,
                "platform": meta.platform or info.platform, "url": info.url,
                "duration_seconds": round(info.duration) if info.duration else None,
                "source_type": meta.source_type if meta.source_type != "online_video" else
                ("youtube_video" if resolved.kind == "youtube" else "online_video"),
            })
            store.update_job(ctx.id, metadata={**meta.model_dump(), "_filled": True}, duration=info.duration)
        if not info.creator or not info.published:
            warnings.append(MESSAGES_METADATA)

        if opts.prefer_captions and resolved.kind != "direct" and not wav.exists():
            ctx.begin("captions", "Looking for captions published with the video")
            caps = fetch.fetch_captions(resolved, info, opts.language)
            if caps:
                ctx.detail("Using the video's " + ("captions" if caps.kind == "human_captions" else "auto-generated captions"), force=True)
                return Transcript(language=caps.language, source=caps.kind,  # type: ignore[arg-type]
                                  engine=resolved.platform, duration=info.duration,
                                  segments=caps.segments)
        if not wav.exists():
            ctx.begin("downloading", "Downloading the audio track")

            def on_bytes(got: int, total: int | None) -> None:
                mb = got / 1_048_576
                if total:
                    ctx.detail(f"Downloaded {mb:.1f} of {total / 1_048_576:.1f} MB", min(1.0, got / total))
                else:
                    ctx.detail(f"Downloaded {mb:.1f} MB")

            src = fetch.download_audio(resolved, work, ctx.should_cancel, on_bytes)
            ctx.check_cancel()
            mi = audio.probe(src)  # validate real content of what we downloaded
            info_duration = info_duration or mi.duration
            _check_duration(info_duration)

    if not wav.exists():
        assert src is not None
        ctx.begin("preparing", "Converting audio to 16 kHz mono for speech recognition")
        audio.to_wav(src, wav, info_duration, ctx.should_cancel)
        if not s.keep_media:
            src.unlink(missing_ok=True)

    total = audio.wav_duration(wav)
    _check_duration(total)
    return _run_asr(ctx, wav, total, opts)


MESSAGES_METADATA = ("Some source details could not be retrieved. Review the citation fields before using the "
                     "reference in an assignment.")


def _run_asr(ctx: JobContext, wav: Path, total: float, opts: JobOptions) -> Transcript:
    s = get_settings()
    store = ctx.store
    ctx.begin("transcribing", "Loading the speech recognition model")
    model = opts.model or s.whisper_model
    t0 = time.perf_counter()
    engine = get_engine(model)
    load_s = time.perf_counter() - t0
    plan = audio.plan_chunks(wav, s.chunk_seconds)
    saved = {c["idx"]: c for c in store.load_chunks(ctx.id)}
    job = store.get_job(ctx.id) or {}
    language = opts.language or (job.get("options") or {}).get("_detected_language")
    lang_prob: float | None = None
    segments: list[Segment] = []
    t_asr = time.perf_counter()
    for i, (a, b) in enumerate(plan):
        ctx.check_cancel()
        c = saved.get(i)
        if c and abs(c["start"] - a) < 0.01 and abs(c["end"] - b) < 0.01:
            segments.extend(Segment(**d) for d in c["segments"])  # resumed from a previous attempt
            continue
        label = f" (part {i + 1} of {len(plan)})" if len(plan) > 1 else ""
        ctx.detail(f"Transcribed {format_timestamp(a)} of {format_timestamp(total)}{label}", a / total, force=True)

        def on_progress(rel_end: float, a: float = a, label: str = label) -> None:
            pos = min(total, a + rel_end)
            ctx.detail(f"Transcribed {format_timestamp(pos)} of {format_timestamp(total)}{label}", pos / total)

        result = engine.transcribe(audio.read_wav(wav, a, b), a, language, ctx.should_cancel, on_progress)
        if language is None and result.language:
            language = result.language
            lang_prob = result.language_probability
            opts_now = (store.get_job(ctx.id) or {}).get("options") or {}
            store.update_job(ctx.id, options={**opts_now, "_detected_language": language})
        new = dedupe_boundary(segments, result.segments)
        store.save_chunk(ctx.id, i, a, b, [x.model_dump() for x in new])
        segments.extend(new)
    elapsed = time.perf_counter() - t_asr
    log.info("job %s asr: model=%s audio=%.0fs wall=%.1fs (rtf=%.2f) load=%.1fs", ctx.id[:8], model, total,
             elapsed, elapsed / total if total else 0, load_s)
    segments = reindex(drop_adjacent_duplicates(segments))
    if not segments:
        raise AppError("no_speech")
    return Transcript(language=language, language_probability=lang_prob, source="speech_recognition",
                      engine=engine.name, model=engine.model_name, duration=total, segments=segments)

