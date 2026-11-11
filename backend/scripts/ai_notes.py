"""Write AI study notes for a saved transcript and print them with their references.

Usage:
  python scripts/ai_notes.py results.json            # a Results JSON (scripts/run_pipeline.py --json)
  python scripts/ai_notes.py --lecture               # the test lecture in tests/media/expected.json
Uses the configured AI provider (TRANSCRIBLY_AI_PROVIDER / TRANSCRIBLY_OLLAMA_MODEL ...).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.llm import get_llm  # noqa: E402
from app.ai.notes import AINotesWriter  # noqa: E402
from app.models import Segment, Transcript  # noqa: E402
from app.transcription.segments import format_timestamp  # noqa: E402


def lecture() -> Transcript:
    paras = json.loads((Path(__file__).resolve().parent.parent / "tests/media/expected.json").read_text())["lecture"]
    segs, t = [], 0.0
    for para in paras:
        for sent in re.split(r"(?<=[.!?])\s+", para):
            dur = len(sent.split()) * 0.4
            segs.append(Segment(index=len(segs), start=round(t, 2), end=round(t + dur, 2), text=sent))
            t += dur + 0.3
        t += 1.5
    return Transcript(source="speech_recognition", language="en", duration=t, segments=segs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?")
    ap.add_argument("--lecture", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.lecture:
        t, title = lecture(), "The water cycle"
    else:
        data = json.loads(Path(a.results).read_text(encoding="utf-8"))
        t, title = Transcript(**data["transcript"]), data["metadata"].get("title")
    llm = get_llm()
    if llm is None:
        sys.exit("No AI model available (is Ollama running with the configured model?)")
    print(f"model: {llm.label}; transcript: {len(t.segments)} lines, {len(t.text.split())} words")
    t0 = time.perf_counter()
    notes = AINotesWriter(llm).write(t, title, report=lambda d, f: print(f"  .. {d}", flush=True) if "words written" not in d or d.endswith("0 words written)") else None)
    print(f"done in {time.perf_counter() - t0:.0f}s\n")

    def refs(hl) -> str:
        return " ".join(f"[{format_timestamp(h.start)} #{','.join(map(str, h.segment_indices))}]" for h in hl)

    print("OVERVIEW:", notes.overview, "\n")
    print("EXECUTIVE SUMMARY:")
    for p in notes.summary_paragraphs:
        print(f"  {p.text} {refs(p.refs)}\n")
    print("KEY POINTS:")
    for k in notes.key_points:
        print(f"  - {k.heading}: {k.text} {refs(k.refs)}")
    print("\nCONCEPTS:")
    for c in notes.concepts:
        print(f"  - {c.term} ({c.explanation_kind}): {c.explanation} {refs(c.refs)}")
    print("\nCONCLUSIONS:")
    for c in notes.conclusions:
        print(f"  - {c.text} {refs(c.refs)}")
    print("\nTOPICS:")
    for tp in notes.topics:
        print(f"  - [{format_timestamp(tp.start)}-{format_timestamp(tp.end)}] {tp.title}: {tp.summary}")
    print("\nQUESTIONS:")
    for q in notes.questions:
        print(f"  - {q.text} {refs(q.refs)}")
    print("\nLIMITATIONS:", *notes.limitations, sep="\n  - ")
    if a.json:
        Path(a.json).write_text(notes.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
