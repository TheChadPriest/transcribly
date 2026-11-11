"""Parse platform captions (WebVTT, SRT, YouTube json3) into transcript segments."""

from __future__ import annotations

import html
import json
import re

from ..models import Segment
from ..transcription.segments import (
    clean_text,
    drop_adjacent_duplicates,
    parse_timestamp,
    reindex,
)

_TAG = re.compile(r"<[^>]+>")
_CUE_TIME = re.compile(r"(?P<a>[\d:.,]+)\s*-->\s*(?P<b>[\d:.,]+)")


def _strip(text: str) -> str:
    return clean_text(html.unescape(_TAG.sub("", text)))


def parse_vtt(content: str) -> list[Segment]:
    """WebVTT or SRT. Rolling auto-captions (each cue repeating the previous line) are collapsed."""
    segs: list[Segment] = []
    blocks = re.split(r"\r?\n\r?\n+", content.replace("﻿", ""))
    prev_lines: list[str] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        m = _CUE_TIME.search(lines[idx])
        if not m:
            continue
        try:
            start, end = parse_timestamp(m["a"]), parse_timestamp(m["b"])
        except ValueError:
            continue
        text_lines = [_strip(ln) for ln in lines[idx + 1:]]
        text_lines = [t for t in text_lines if t]
        # Rolling captions: drop lines already shown in the previous cue.
        new_lines = [t for t in text_lines if t not in prev_lines]
        prev_lines = text_lines
        text = clean_text(" ".join(new_lines))
        if text and end - start > 0.02:
            segs.append(Segment(index=0, start=start, end=end, text=text))
    return reindex(merge_short(drop_adjacent_duplicates(segs)))


def parse_json3(content: str) -> list[Segment]:
    data = json.loads(content)
    segs: list[Segment] = []
    for ev in data.get("events", []):
        if "segs" not in ev or ev.get("aAppend"):
            continue
        text = _strip("".join(s.get("utf8", "") for s in ev["segs"]))
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000
        end = start + ev.get("dDurationMs", 0) / 1000
        segs.append(Segment(index=0, start=start, end=end, text=text))
    # json3 cue durations overlap; clamp each end to the next start
    for i in range(len(segs) - 1):
        if segs[i].end > segs[i + 1].start:
            segs[i] = segs[i].model_copy(update={"end": max(segs[i].start, segs[i + 1].start)})
    return reindex(merge_short(drop_adjacent_duplicates(segs)))


def merge_short(segs: list[Segment], min_words: int = 8, max_words: int = 30) -> list[Segment]:
    """Caption cues are often 2-5 words; merge into phrase-sized segments, ending at sentence
    punctuation when present."""
    out: list[Segment] = []
    for s in segs:
        if out:
            last = out[-1]
            lw = len(last.text.split())
            ends_sentence = last.text.endswith((".", "?", "!"))
            if lw < min_words or (not ends_sentence and lw + len(s.text.split()) <= max_words and s.start - last.end < 1.5):
                out[-1] = last.model_copy(update={"end": max(last.end, s.end), "text": f"{last.text} {s.text}"})
                continue
        out.append(s)
    return out


def parse_captions(content: str, ext: str) -> list[Segment]:
    ext = ext.lower().lstrip(".")
    if ext == "json3":
        return parse_json3(content)
    if ext in ("vtt", "srt"):
        return parse_vtt(content)
    raise ValueError(f"unsupported caption format {ext}")
