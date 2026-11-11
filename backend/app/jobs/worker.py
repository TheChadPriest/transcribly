"""Background workers: claim queued jobs from the store, recover interrupted ones, clean up.

Run inside the API process (default) or standalone with `python -m app.worker`.
Use exactly one worker process per data directory.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time

from ..config import get_settings
from ..errors import MESSAGES
from ..models import JobStatus
from ..storage.db import Store
from .pipeline import run_job

log = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 3600


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def recover(self) -> None:
        """Jobs that were running when the process stopped are re-queued (and resume from saved
        chunks) until they hit max_attempts; then they fail with a clear message."""
        s = get_settings()
        for job in self.store.jobs_with_status(JobStatus.running.value):
            if job["attempts"] >= s.max_attempts:
                self.store.update_job(job["id"], status=JobStatus.failed.value, error_code="interrupted",
                                      error_message=MESSAGES["interrupted"], expires_at=time.time() + 86400)
                log.warning("job %s failed after %d interrupted attempts", job["id"][:8], job["attempts"])
            else:
                self.store.update_job(job["id"], status=JobStatus.queued.value, detail="Resuming after a server restart")
                log.info("job %s re-queued after restart", job["id"][:8])

    def start(self) -> None:
        s = get_settings()
        self.recover()
        for i in range(max(1, s.worker_concurrency)):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        t = threading.Thread(target=self._janitor, name="janitor", daemon=True)
        t.start()
        self.threads.append(t)

    def shutdown(self) -> None:
        self.stop.set()
        self.wake.set()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                job = self.store.claim_next_job()
            except Exception:
                log.exception("claim failed")
                job = None
            if job is None:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            run_job(self.store, job)

    def _janitor(self) -> None:
        while not self.stop.is_set():
            try:
                cleanup(self.store)
            except Exception:
                log.exception("cleanup failed")
            self.stop.wait(600)


def cleanup(store: Store) -> int:
    """Delete expired jobs and any orphaned working directories."""
    s = get_settings()
    removed = 0
    for job_id in store.expired_job_ids(time.time()):
        store.delete_job(job_id)
        shutil.rmtree(s.work_dir / job_id, ignore_errors=True)
        removed += 1
    if s.work_dir.exists():
        for d in s.work_dir.iterdir():
            job = store.get_job(d.name)
            # An upload writes its directory before the job row exists, so only remove
            # directories without a job once they are clearly abandoned.
            orphan = job is None and time.time() - d.stat().st_mtime > ORPHAN_GRACE_SECONDS
            finished = job is not None and job["status"] in ("completed", "cancelled") and not s.keep_media
            if orphan or finished:
                shutil.rmtree(d, ignore_errors=True)
    if removed:
        log.info("cleanup removed %d expired jobs", removed)
    return removed
