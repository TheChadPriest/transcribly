"""SQLite job store.

SQLite in WAL mode is safe for one API process plus worker threads, or an API process plus a
separate worker process on the same host. For multiple hosts, swap this module for PostgreSQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT,
    detail TEXT,
    progress REAL,
    stages TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL,
    source_url TEXT,
    upload_name TEXT,
    options TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    duration REAL,
    error_code TEXT,
    error_message TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cache_key TEXT,
    idempotency_key TEXT,
    client_ip TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key, status);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS chunks (
    job_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (job_id, idx)
);

CREATE TABLE IF NOT EXISTS results (
    job_id TEXT PRIMARY KEY,
    transcript TEXT NOT NULL,
    study TEXT,
    summary_error TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

JSON_FIELDS = {"stages", "options", "metadata", "warnings"}


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
        finally:
            conn.close()

    # --- generic helpers -------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_FIELDS:
            if k in d and d[k] is not None:
                d[k] = json.loads(d[k])
        return d

    def kv_get(self, key: str) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return r["value"] if r else None

    def kv_set_if_absent(self, key: str, value: str) -> str:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO kv(key, value) VALUES (?, ?)", (key, value))
            return c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()["value"]

    # --- jobs -----------------------------------------------------------
    def create_job(self, job: dict[str, Any]) -> None:
        now = time.time()
        job = {**job, "created_at": now, "updated_at": now}
        for k in JSON_FIELDS:
            if k in job:
                job[k] = json.dumps(job[k])
        cols = ",".join(job)
        marks = ",".join("?" for _ in job)
        with self._lock, self._conn() as c:
            c.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", tuple(job.values()))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            return self._row(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self._conn() as c:
            return self._row(c.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone())

    def find_cached(self, cache_key: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT j.* FROM jobs j JOIN results r ON r.job_id = j.id "
                "WHERE j.cache_key=? AND j.status='completed' AND (j.expires_at IS NULL OR j.expires_at > ?) "
                "ORDER BY j.completed_at DESC LIMIT 1",
                (cache_key, time.time()),
            ).fetchone()
            return self._row(row)

    def update_job(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        for k in JSON_FIELDS:
            if k in fields:
                fields[k] = json.dumps(fields[k])
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))

    def claim_next_job(self) -> dict[str, Any] | None:
        """Atomically move the oldest queued job to running."""
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT id FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is None:
                    c.execute("COMMIT")
                    return None
                c.execute(
                    "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                    (time.time(), row["id"]),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return self.get_job(row["id"])

    def count_jobs(self, statuses: tuple[str, ...]) -> int:
        marks = ",".join("?" for _ in statuses)
        with self._conn() as c:
            return c.execute(f"SELECT COUNT(*) FROM jobs WHERE status IN ({marks})", statuses).fetchone()[0]

    def count_recent_by_ip(self, ip: str, since: float) -> int:
        with self._conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM jobs WHERE client_ip=? AND created_at>?", (ip, since)
            ).fetchone()[0]

    def jobs_with_status(self, status: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM jobs WHERE status=?", (status,))]  # type: ignore[misc]

    def expired_job_ids(self, now: float) -> list[str]:
        with self._conn() as c:
            return [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at < ? AND status IN ('completed','failed','cancelled')",
                    (now,),
                )
            ]

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM chunks WHERE job_id=?", (job_id,))
            c.execute("DELETE FROM results WHERE job_id=?", (job_id,))
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    # --- chunks (resumable transcription) ----------------------------------
    def save_chunk(self, job_id: str, idx: int, start: float, end: float, segments: list[dict]) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO chunks(job_id, idx, start, end, data) VALUES (?,?,?,?,?)",
                (job_id, idx, start, end, json.dumps(segments)),
            )

    def load_chunks(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                {"idx": r["idx"], "start": r["start"], "end": r["end"], "segments": json.loads(r["data"])}
                for r in c.execute("SELECT * FROM chunks WHERE job_id=? ORDER BY idx", (job_id,))
            ]

    def clear_chunks(self, job_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM chunks WHERE job_id=?", (job_id,))

    # --- results ------------------------------------------------------------
    def save_results(self, job_id: str, transcript: dict, study: dict | None, summary_error: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO results(job_id, transcript, study, summary_error) VALUES (?,?,?,?)",
                (job_id, json.dumps(transcript), json.dumps(study) if study else None, summary_error),
            )

    def load_results(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM results WHERE job_id=?", (job_id,)).fetchone()
            if r is None:
                return None
            return {
                "transcript": json.loads(r["transcript"]),
                "study": json.loads(r["study"]) if r["study"] else None,
                "summary_error": r["summary_error"],
            }
