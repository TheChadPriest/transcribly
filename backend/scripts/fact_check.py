"""Run only the fact-check pass on saved AI notes and show what it changed.

Usage: python scripts/fact_check.py results.json   (a Results JSON with AI-written study notes)
"""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.factcheck import FactChecker  # noqa: E402
from app.ai.llm import get_llm  # noqa: E402
from app.ai.notes import Grounder, clean_text  # noqa: E402
from app.models import StudyMaterials, Transcript  # noqa: E402


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    t = Transcript(**data["transcript"])
    before = StudyMaterials(**data["study"])
    llm = get_llm()
    if llm is None:
        sys.exit("No AI model available.")
    notes = copy.deepcopy(before)
    t0 = time.perf_counter()
    checker = FactChecker(llm, t, Grounder(t))
    notes = checker.check(notes, lambda d, f: print("  ..", d, flush=True), None, clean_text)
    print(f"done in {time.perf_counter() - t0:.0f}s: corrected={checker.corrected} removed={checker.removed}\n")

    def show(kind, old, new):
        if old != new:
            print(f"[{kind}] CHANGED\n   before: {old}\n   after:  {new}\n")

    for a, b in zip(before.summary_paragraphs, notes.summary_paragraphs):
        show("summary", a.text, b.text)
    after_kp = {k.heading: k.text for k in notes.key_points}
    for k in before.key_points:
        show("key point " + k.heading, k.text, after_kp.get(k.heading, "(removed)"))
    after_c = {c.term: c.explanation for c in notes.concepts}
    for c in before.concepts:
        show("concept " + c.term, c.explanation, after_c.get(c.term, "(removed)"))
    after_cl = [c.text for c in notes.conclusions]
    for c in before.conclusions:
        if c.text not in after_cl:
            print(f"[conclusion] CHANGED/REMOVED\n   before: {c.text}\n   now: {after_cl}\n")
    for a, b in zip(before.topics, notes.topics):
        show("topic " + a.title, a.summary, b.summary)


if __name__ == "__main__":
    main()
