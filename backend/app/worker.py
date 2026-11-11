"""Standalone worker process: `python -m app.worker` (set TRANSCRIBLY_RUN_WORKER_IN_API=false on the API)."""

from __future__ import annotations

import signal
import threading

from .config import get_settings
from .jobs.worker import Worker
from .logging_config import setup_logging
from .storage.db import Store


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    s.work_dir.mkdir(parents=True, exist_ok=True)
    worker = Worker(Store(s.db_path))
    worker.start()
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    done.wait()
    worker.shutdown()


if __name__ == "__main__":
    main()
