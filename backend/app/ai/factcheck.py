"""Second pass over AI-written notes: check every statement against the lines it cites.

Reference grounding (notes.Grounder) catches citations that point to the wrong place, but not a
sentence that cites the right passage and still misstates it - e.g. crediting a person the video
says did *not* do something. Here the model re-reads each statement next to its cited lines (plus
a little surrounding context, where such qualifications usually are) and either confirms it,
rewrites it to say only what the lines support, or marks it unsupported so it is removed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..models import CitedText, Highlight, StudyMaterials, Transcript
from ..transcription.segments import format_timestamp
from .llm import LLM, LLMError, estimate_tokens

log = logging.getLogger(__name__)

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["supported", "partly", "unsupported"]},
            "evidence": {"type": "string"},
            "corrected": {"type": "string"},
        },
        "required": ["id", "verdict", "evidence", "corrected"],
        "additionalProperties": False,
    }}},
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM = ("You are a careful fact-checker for student study notes. You compare each statement with the transcript "
          "lines it is based on and correct anything those lines do not support.")

INSTRUCTIONS = """Fact-check each numbered statement below against the transcript lines given with it.
Check every fact in the statement - names, numbers, dates, who did or said what, what came first, causes and results - against ITS lines only.
- "supported": every fact is stated in, or directly follows from, the lines. "evidence" is "" and "corrected" repeats the statement unchanged. When in doubt, choose "supported".
- "partly": a specific fact in the statement is contradicted by the lines (wrong person, wrong date or period, wrong order, or a claim the lines say is not true). "evidence" copies, word for word, the words from the lines that show the problem. "corrected" is the statement with only that fact fixed, keeping everything else and the same length and style. Watch for lines that qualify a claim (for example "although X is given credit, it was actually Y").
- "unsupported": the lines clearly say something different from the whole statement. "evidence" copies the words that show this; "corrected" is "".
Only report a problem you can prove with an exact quote from the lines. Do not add information that is not in the lines. Return one result for every statement, using its id."""


@dataclass
class _Item:
    kind: str
    target: Any  # the model object holding the text
    text: str
    lines: list[int]  # segment indices shown to the checker
    cited: list[int]  # the lines the statement itself cites


def _ids(refs: list[Highlight]) -> list[int]:
    return [i for h in refs for i in h.segment_indices]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


class FactChecker:
    def __init__(self, llm: LLM, transcript: Transcript, grounder):
        self.llm = llm
        self.t = transcript
        self.g = grounder
        self.corrected = 0
        self.removed = 0

    # -- which lines each statement is checked against --------------------------------
    def _context(self, cited: list[int], extra: list[int] | None = None, around: int = 3, cap: int = 30) -> list[int]:
        pos = self.g.pos
        order = self.g.order
        picked: set[int] = set()
        for r in list(cited) + list(extra or []):
            if r not in pos:
                continue
            i = pos[r]
            picked.update(order[max(0, i - around): min(len(order), i + around + 1)])
        lines = sorted(picked, key=lambda r: pos[r])
        if len(lines) > cap and around > 1:
            return self._context(cited, extra, around - 1, cap)
        return lines[:cap]

    def _for_prose(self, text: str, refs: list[Highlight]) -> list[int]:
        cited = [i for h in refs for i in h.segment_indices]
        extra = [r for sent in _sentences(text) for r in self.g.best(sent, k=2)[0]]
        return self._context(cited, extra, around=2, cap=40)

    def _items(self, notes: StudyMaterials) -> list[_Item]:
        items: list[_Item] = []
        for p in notes.summary_paragraphs:
            items.append(_Item("paragraph", p, p.text, self._for_prose(p.text, p.refs), _ids(p.refs)))
        if notes.overview:
            items.append(_Item("overview", notes, notes.overview, self._for_prose(notes.overview, []), []))
        for k in notes.key_points:
            items.append(_Item("key_point", k, k.text, self._context(_ids(k.refs)), _ids(k.refs)))
        for c in notes.concepts:
            items.append(_Item("concept", c, f"{c.term}: {c.explanation}", self._context(_ids(c.refs)), _ids(c.refs)))
        for c in notes.conclusions:
            items.append(_Item("conclusion", c, c.text, self._context(_ids(c.refs)), _ids(c.refs)))
        for tp in notes.topics:
            if tp.summary:
                inside = [s.index for s in self.t.segments if tp.start - 0.01 <= s.start <= tp.end][:4]
                items.append(_Item("topic", tp, tp.summary,
                                   self._context(_ids(tp.passages), inside, 2), _ids(tp.passages)))
        return [i for i in items if i.lines]

    # -- running the checks ---------------------------------------------------------------
    def _render(self, batch: list[_Item]) -> str:
        blocks = []
        for n, item in enumerate(batch, 1):
            lines = "\n".join(f"[{i}] {format_timestamp(self.g.by_index[i].start)} {self.g.by_index[i].text}"
                              for i in item.lines)
            blocks.append(f"STATEMENT {n}: {item.text}\nLINES FOR STATEMENT {n}:\n{lines}")
        return INSTRUCTIONS + "\n\n" + "\n\n".join(blocks)

    def _batches(self, items: list[_Item], max_items: int = 5, max_tokens: int = 3000) -> list[list[_Item]]:
        batches: list[list[_Item]] = [[]]
        for item in items:
            trial = batches[-1] + [item]
            if batches[-1] and (len(trial) > max_items or estimate_tokens(self._render(trial)) > max_tokens):
                batches.append([item])
            else:
                batches[-1] = trial
        return [b for b in batches if b]

    def check(self, notes: StudyMaterials, report: Callable[[str, float | None], None],
              should_cancel: Callable[[], bool] | None, clean: Callable[[str], str]) -> StudyMaterials:
        items = self._items(notes)
        batches = self._batches(items)
        verdicts: dict[int, tuple[str, str]] = {}
        done = 0
        for batch in batches:
            report(f"Fact-checking the notes against the transcript ({done} of {len(items)} statements checked)",
                   done / max(1, len(items)))
            raw = self.llm.generate_json(SYSTEM, self._render(batch), VERIFY_SCHEMA, None, should_cancel)
            for r in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
                if not isinstance(r, dict) or not isinstance(r.get("id"), int) or not 1 <= r["id"] <= len(batch):
                    continue
                verdicts[id(batch[r["id"] - 1])] = (str(r.get("verdict", "")), str(r.get("evidence", "")),
                                                     clean(str(r.get("corrected", ""))))
            done += len(batch)
        for item in items:
            verdict, evidence, corrected = verdicts.get(id(item), ("supported", "", item.text))
            if verdict == "supported" or not self._proven(item, evidence):
                continue  # small models over-flag; only act on problems backed by a verifiable quote
            if verdict == "partly" and self._acceptable(item, corrected):
                self._apply(item, corrected)
                self.corrected += 1
            elif verdict == "unsupported" and self.g.score(item.text, item.cited or item.lines, window=False) < 0.35:
                self._remove(notes, item)
        self._finish(notes)
        log.info("fact-check: %d statements, corrected=%d removed=%d", len(items), self.corrected, self.removed)
        return notes

    def _proven(self, item: _Item, evidence: str) -> bool:
        """The quoted evidence must really be in the lines shown for this statement."""
        def norm(x: str) -> str:
            return " ".join(re.findall(r"[\w']+", x.lower()))

        quote = norm(evidence)
        if len(quote.split()) < 3:
            return False
        context = norm(" ".join(self.g.by_index[i].text for i in item.lines))
        if quote in context:
            return True
        words = quote.split()
        windows = (" ".join(words[i:i + 4]) for i in range(0, max(1, len(words) - 3)))
        hits = sum(1 for w in windows if w in context)
        return hits >= max(1, (len(words) - 3) * 0.7)

    def _acceptable(self, item: _Item, corrected: str) -> bool:
        """A correction must stay close in size and be at least as well supported by the lines."""
        if not corrected or corrected == item.text or len(corrected) > len(item.text) * 1.6 + 40:
            return False
        before = self.g.score(item.text, item.lines, window=False)
        after = self.g.score(corrected, item.lines, window=False)
        return after >= max(0.3, before - 0.05)

    def _regrounded(self, text: str, old: list[Highlight]) -> list[Highlight]:
        cited = [i for h in old for i in h.segment_indices]
        refs = self.g.ground(text, cited, required=False, accept=0.2, minimum=0.08) or cited
        return self.g.highlights(refs)

    def _apply(self, item: _Item, text: str) -> None:
        obj = item.target
        if item.kind == "paragraph":
            obj.text = text
            obj.refs = self._regrounded(text, obj.refs)
        elif item.kind == "overview":
            obj.overview = text
        elif item.kind == "key_point":
            obj.text = text
            obj.refs = self._regrounded(text, obj.refs)
        elif item.kind == "concept":
            term, sep, rest = text.partition(":")
            obj.explanation = rest.strip() if sep and term.strip().lower() == obj.term.lower() else text
            obj.refs = self._regrounded(obj.explanation, obj.refs)
        elif item.kind == "conclusion":
            obj.text = text
            obj.refs = self._regrounded(text, obj.refs)
        elif item.kind == "topic":
            obj.summary = text

    def _remove(self, notes: StudyMaterials, item: _Item) -> None:
        obj = item.target
        if item.kind == "paragraph" and len(notes.summary_paragraphs) > 1:
            notes.summary_paragraphs = [p for p in notes.summary_paragraphs if p is not obj]
        elif item.kind == "key_point":
            notes.key_points = [k for k in notes.key_points if k is not obj]
        elif item.kind == "concept":
            notes.concepts = [c for c in notes.concepts if c is not obj]
        elif item.kind == "conclusion":
            notes.conclusions = [c for c in notes.conclusions if c is not obj]
        elif item.kind == "topic":
            obj.summary = ""
        else:
            return  # the overview and a lone summary paragraph are kept (they are checked again by the reader)
        self.removed += 1

    def _finish(self, notes: StudyMaterials) -> None:
        notes.executive_summary = "\n\n".join(p.text for p in notes.summary_paragraphs)
        for n, k in enumerate(notes.key_points, 1):
            k.id = f"k{n}"
            if k.refs:
                k.support, k.timestamp = k.refs[0], k.refs[0].start
        for c in notes.concepts + notes.conclusions:
            if c.refs:
                c.timestamp = c.refs[0].start
        if self.corrected or self.removed:
            parts = []
            if self.corrected:
                parts.append(f"corrected {self.corrected} statement{'s' if self.corrected != 1 else ''}")
            if self.removed:
                parts.append(f"removed {self.removed} that the transcript didn't support")
            notes.limitations.insert(1, "A fact-check pass compared each statement with its transcript passage and "
                                        + " and ".join(parts) + ".")
        else:
            notes.limitations.insert(1, "A fact-check pass compared each statement with its transcript passage and "
                                        "found no problems.")


def fact_check(llm: LLM, t: Transcript, grounder, notes: StudyMaterials, report, should_cancel, clean) -> StudyMaterials:
    """Run the fact-check; if the model fails, keep the notes and say they weren't checked."""
    try:
        return FactChecker(llm, t, grounder).check(notes, report, should_cancel, clean)
    except LLMError as e:
        log.warning("fact-check skipped: %s", e)
        notes.limitations.insert(1, f"The fact-check step couldn't run ({e}), so these notes were not double-checked.")
        return notes


__all__ = ["FactChecker", "fact_check", "VERIFY_SCHEMA", "CitedText"]
