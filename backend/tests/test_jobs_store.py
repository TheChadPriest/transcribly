"""Job store, recovery after restart, resumable chunks and retention cleanup."""

import os
import time

from app.jobs.pipeline import run_job
from app.jobs.worker import Worker, cleanup
from app.models import JobOptions
from app.storage.db import Store

from .conftest import MEDIA


def new_store(settings):
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    return Store(settings.db_path)


def base_job(jid, **kw):
    return {"id": jid, "status": "queued", "source_type": "upload", "options": {}, "metadata": {}, **kw}


def test_claim_is_atomic_and_ordered(settings):
    s = new_store(settings)
    s.create_job(base_job("job-aaaaaaaaaaaaaaaaaaaaaa"))
    time.sleep(0.01)
    s.create_job(base_job("job-bbbbbbbbbbbbbbbbbbbbbb"))
    first = s.claim_next_job()
    second = s.claim_next_job()
    assert first["id"].startswith("job-a") and second["id"].startswith("job-b")
    assert first["status"] == "running" and first["attempts"] == 1
    assert s.claim_next_job() is None


def test_recovery_policy(settings):
    s = new_store(settings)
    s.create_job(base_job("job-cccccccccccccccccccccc", status="running", attempts=1))
    s.create_job(base_job("job-dddddddddddddddddddddd", status="running", attempts=settings.max_attempts))
    Worker(s).recover()
    assert s.get_job("job-cccccccccccccccccccccc")["status"] == "queued"
    failed = s.get_job("job-dddddddddddddddddddddd")
    assert failed["status"] == "failed" and failed["error_code"] == "interrupted"


def test_resume_uses_saved_chunks(settings, monkeypatch):
    """A job interrupted mid-transcription resumes without re-transcribing finished chunks."""
    import shutil

    from app.transcription import engine

    monkeypatch.setenv("TRANSCRIBLY_CHUNK_SECONDS", "40")
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    s = new_store(settings)
    jid = "job-eeeeeeeeeeeeeeeeeeeeee"
    work = settings.work_dir / jid
    work.mkdir(parents=True)
    shutil.copy(MEDIA / "lecture.m4a", work / "source.bin")
    s.create_job(base_job(jid, options=JobOptions().model_dump()))

    calls = []
    original = engine.MockEngine.transcribe

    def counting(self, audio, offset, *a, **k):
        calls.append(offset)
        if len(calls) == 2:
            raise KeyboardInterrupt  # simulate the process dying during the second chunk
        return original(self, audio, offset, *a, **k)

    monkeypatch.setattr(engine.MockEngine, "transcribe", counting)
    job = s.claim_next_job()
    try:
        run_job(s, job)
    except KeyboardInterrupt:
        pass
    assert len(s.load_chunks(jid)) == 1
    assert (work / "audio.wav").exists()

    Worker(s).recover()
    calls.clear()
    monkeypatch.setattr(engine.MockEngine, "transcribe", lambda self, audio, offset, *a, **k: (calls.append(offset), original(self, audio, offset, *a, **k))[1])
    run_job(s, s.claim_next_job())
    final = s.get_job(jid)
    assert final["status"] == "completed", final
    assert calls and calls[0] > 0  # first chunk was not redone
    res = s.load_results(jid)
    starts = [x["start"] for x in res["transcript"]["segments"]]
    assert starts == sorted(starts) and starts[0] < 5
    assert not work.exists()


def test_cleanup_removes_expired_and_orphans(settings):
    s = new_store(settings)
    s.create_job(base_job("job-ffffffffffffffffffffff", status="completed", expires_at=time.time() - 10))
    s.save_results("job-ffffffffffffffffffffff", {"source": "speech_recognition", "segments": []}, None, None)
    s.create_job(base_job("job-gggggggggggggggggggggg", status="completed", expires_at=time.time() + 3600))
    orphan = settings.work_dir / "orphan-dir-hhhhhhhhhhhhhhhh"
    orphan.mkdir()
    (orphan / "source.bin").write_bytes(b"x")
    fresh = settings.work_dir / "upload-in-progress-iiiiiiii"  # no job row yet, but recent
    fresh.mkdir()
    old = time.time() - 7200
    os.utime(orphan, (old, old))
    assert cleanup(s) == 1
    assert fresh.exists()
    assert s.get_job("job-ffffffffffffffffffffff") is None
    assert s.load_results("job-ffffffffffffffffffffff") is None
    assert s.get_job("job-gggggggggggggggggggggg") is not None
    assert not orphan.exists()
