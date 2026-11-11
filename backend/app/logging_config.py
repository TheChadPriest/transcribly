"""Plain, single-line logs. Transcript text and full source URLs are never logged."""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_transcribly", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._transcribly = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
