"""Pure helpers for transcript segments: timestamps, boundary de-duplication, paragraphs."""

from __future__ import annotations

import re
from typing import Iterable

from ..models import Paragraph, Segment

_WS = re.compile(r"\s+")
_NORM = re.compile(r"[^\w\s]", re.UNICODE)


def format_timestamp(seconds: float | None) -> str:
    """Always HH:MM:SS (never negative)."""
    if seconds is None:
        return "--:--:--"
    total = max(0, int(round(float(seconds), 3)))  # floor of the second the passage starts in
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_timestamp(value: str) -> float:
    """Accepts HH:MM:SS(.mmm), MM:SS, SS and WebVTT/SRT forms (comma decimal)."""
    value = value.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", value):
        raise ValueError(f"invalid timestamp: {value!r}")
    parts = [float(p) for p in value.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def clean_text(text: str) -> str:
    return _WS.sub(" ", text.replace("​", "")).strip()


def _norm(text: str) -> str:
    return _WS.sub(" ", _NORM.sub("", text.lower())).strip()


def dedupe_boundary(previous: list[Segment], incoming: list[Segment], window: float = 3.0) -> list[Segment]:
    """Remove text repeated across a chunk boundary.

    Drops incoming segments whose text equals one of the last few previous segments when they
    overlap in time, and trims a leading run of words that repeats the tail of the previous text.
    """
    if not previous or not incoming:
        return incoming
    tail = previous[-3:]
    tail_norms = {_norm(s.text) for s in tail}
    last_end = previous[-1].end
    out: list[Segment] = []
    for seg in incoming:
        if seg.start < last_end + window and _norm(seg.text) in tail_norms:
            continue
        out.append(seg)
    if out and out[0].start < last_end + window:
        prev_words = _norm(previous[-1].text).split()
        words = out[0].text.split()
        norm_words = [_norm(w) for w in words]
        for k in range(min(len(prev_words), len(words) - 1, 12), 2, -1):
            if prev_words[-k:] == norm_words[:k]:
                out[0] = out[0].model_copy(update={"text": " ".join(words[k:])})
                break
    return out


def drop_adjacent_duplicates(segments: Iterable[Segment]) -> list[Segment]:
    """Collapse consecutive identical segments (a common caption/ASR artifact)."""
    out: list[Segment] = []
    for s in segments:
        if not s.text:
            continue
        if out and _norm(out[-1].text) == _norm(s.text) and s.start - out[-1].end < 2.0:
            out[-1] = out[-1].model_copy(update={"end": max(out[-1].end, s.end)})
            continue
        out.append(s)
    return out


def reindex(segments: list[Segment]) -> list[Segment]:
    return [s.model_copy(update={"index": i}) for i, s in enumerate(segments)]


def build_paragraphs(segments: list[Segment], max_words: int = 110, gap: float = 2.5,
                     breaks: set[float] | None = None) -> list[Paragraph]:
    """Group segments into readable paragraphs, keeping each segment's own timestamps.

    A paragraph ends at a long pause, at a sentence end once it is long enough, or at an
    explicit break time (e.g. a topic boundary)."""
    breaks = breaks or set()
    paras: list[Paragraph] = []
    cur: list[Segment] = []
    words = 0

    def flush() -> None:
        nonlocal cur, words
        if cur:
            paras.append(Paragraph(index=len(paras), start=cur[0].start, end=cur[-1].end,
                                   segment_indices=[s.index for s in cur]))
        cur, words = [], 0

    for s in segments:
        if cur:
            pause = s.start - cur[-1].end
            sentence_end = cur[-1].text.rstrip().endswith((".", "?", "!", "。", "？", "！", '"', "”"))
            if (
                pause > gap
                or any(cur[-1].end <= b <= s.start + 0.01 for b in breaks)
                or (words >= max_words and sentence_end)
                or words >= max_words * 2
            ):
                flush()
        cur.append(s)
        words += len(s.text.split())
    flush()
    return paras
