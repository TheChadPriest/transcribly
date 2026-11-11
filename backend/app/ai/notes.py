"""AI-written study notes, grounded in the transcript.

1. The transcript is given to the model as numbered lines ("[12] 00:03:15 ...").
2. If it fits the model's context, the model reads all of it and writes the notes in one pass.
   Otherwise it reads the transcript part by part, keeping a running summary of what came
   before, writes a referenced digest of each part, and then writes the final notes from all
   digests - so the final writing step always sees the whole video.
3. Every statement must cite the line numbers it is based on. References are checked against
   the transcript: invalid numbers are removed, weakly matching references are re-linked to the
   passage that best matches, and statements that match nothing are dropped.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Callable

from ..config import get_settings
from ..models import CitedText, Concept, Conclusion, Highlight, KeyPoint, StudyMaterials, Topic, Transcript
from ..transcription.segments import format_timestamp
from .factcheck import fact_check
from .llm import LLM, LLMError, estimate_tokens

log = logging.getLogger(__name__)

Report = Callable[[str, float | None], None]  # (detail, fraction or None)

# --- schemas ---------------------------------------------------------------------

REFS = {"type": "array", "items": {"type": "integer"}}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


NOTES_SCHEMA = _obj({
    "overview": {"type": "string"},
    "summary_paragraphs": {"type": "array", "items": _obj({"text": {"type": "string"}, "refs": REFS})},
    "key_points": {"type": "array", "items": _obj({"heading": {"type": "string"}, "explanation": {"type": "string"},
                                                    "refs": REFS})},
    "concepts": {"type": "array", "items": _obj({"term": {"type": "string"}, "explanation": {"type": "string"},
                                                  "defined_in_video": {"type": "boolean"}, "refs": REFS})},
    "conclusions": {"type": "array", "items": _obj({"text": {"type": "string"}, "refs": REFS})},
    "topics": {"type": "array", "items": _obj({"title": {"type": "string"}, "summary": {"type": "string"},
                                                "first_ref": {"type": "integer"},
                                                "subtopics": {"type": "array", "items": {"type": "string"}},
                                                "refs": REFS})},
    "study_questions": {"type": "array", "items": _obj({"question": {"type": "string"}, "refs": REFS})},
    "uncertainties": {"type": "array", "items": {"type": "string"}},
})

DIGEST_SCHEMA = _obj({
    "part_summary": {"type": "string"},
    "sections": {"type": "array", "items": _obj({"title": {"type": "string"}, "first_ref": {"type": "integer"},
                                                  "summary": {"type": "string"}})},
    "ideas": {"type": "array", "items": _obj({"point": {"type": "string"}, "refs": REFS})},
    "terms": {"type": "array", "items": _obj({"term": {"type": "string"}, "explanation": {"type": "string"},
                                               "defined_in_video": {"type": "boolean"}, "refs": REFS})},
    "claims": {"type": "array", "items": _obj({"text": {"type": "string"}, "refs": REFS})},
    "unclear": {"type": "array", "items": {"type": "string"}},
})

# --- prompts -----------------------------------------------------------------------

SYSTEM = (
    "You are an expert university tutor. You turn transcripts of lectures, documentaries and interviews "
    "into clear, accurate study notes that help a student understand the video without watching it. "
    "You write in plain, well-organized educational prose in your own words, and you never add "
    "information that is not in the transcript."
)

RULES = """Rules that apply to everything you write:
- Use only information from the transcript. Do not add facts, numbers, names, dates, examples or claims that the transcript does not contain. You may explain the speaker's ideas in simpler words.
- Write in your own words, like a good textbook or teacher would. Do not paste transcript sentences; a short exact quote is fine only inside quotation marks.
- Attribute opinions and arguments to the speaker ("The speaker argues…") instead of stating them as facts.
- Every item has "refs": the line numbers ([n] in the transcript) where that point is actually made, most relevant first, usually 1–3 lines. Cite only lines that directly support the point. Use only numbers that appear in the transcript. Never write line numbers or brackets inside the text itself - they belong only in "refs".
- If the transcript is unclear, garbled or incomplete somewhere, do not guess - mention it in "uncertainties".
- Write in the same language as the transcript."""


def _notes_instructions(n_key: int, words: int, short: bool) -> str:
    topics = "1–3 topics" if short else "as many topics as the video really has, in order (do not force a number)"
    return f"""Read the whole transcript first and work out what the video is about, its main argument or purpose, how it is structured, and what it concludes. Then write study notes as JSON:

- "overview": 2–3 sentences a student could read first: what the video is about and why it matters.
- "summary_paragraphs": an executive summary of about {words} words in 2–4 paragraphs: the subject and purpose; the main argument or explanation and how it develops; the principal conclusions; what a student should remember. Each paragraph has its own refs.
- "key_points": about {n_key} key takeaways that together cover every main idea or stage of the video (fewer if the video has fewer real ideas). "heading" is at most 8 words; "explanation" is 1–3 sentences that make sense on their own and teach the idea clearly.
- "concepts": important terms, theories, people or events. Explain each plainly based on how the video uses it. "defined_in_video" is true only if the speaker explicitly defines it.
- "conclusions": what the speaker ultimately argues, demonstrates or concludes.
- "topics": {topics}. "first_ref" is the line where the topic starts; "summary" is 1–2 sentences describing what that part of the video covers (do not repeat a key point word for word); "subtopics" are short labels.
- "study_questions": 3–6 questions that test understanding, application, comparison and critical thinking. Do not answer them; "refs" are the lines where the answer can be found.
- "uncertainties": limitations a student should know about (unclear audio, missing context, likely transcription errors). Empty if none.

{RULES}"""


DIGEST_INSTRUCTIONS = f"""You are reading part {{part}} of {{parts}} of a transcript (lines {{first}}–{{last}}).
{{context}}
Write a digest of THIS part as JSON:
- "part_summary": 3–5 sentences explaining what this part covers and how it connects to what came before.
- "sections": the topic sections in this part, in order, with the line where each starts ("first_ref") and a 1–2 sentence summary.
- "ideas": the important ideas, explanations and findings, each explained in 1–2 sentences.
- "terms": important terms, theories, people or events and what the video says about them.
- "claims": arguments or conclusions the speaker makes in this part.
- "unclear": anything garbled or hard to follow.

{RULES}"""


# "[4, 5, 3]", "[#12]", "(lines 3-5)" written into prose by the model
_LEAKED_REFS = re.compile(
    r"\s*(\[\s*#?\d+(\s*[,;–-]\s*#?\d+)*\s*\]"
    r"|\(\s*(?:see\s+)?(?:lines?|refs?|references?|sources?)\s*:?\s*#?\d+(?:\s*(?:[,;–-]|and)\s*#?\d+)*\s*\)\.?)",
    re.IGNORECASE)


def clean_text(x: Any) -> str:
    """Normalise model prose: drop line numbers it wrote into the text, tidy whitespace."""
    text = re.sub(r"\(\s*\)", "", _LEAKED_REFS.sub("", str(x or "")))
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", text)).strip()


def number_transcript(t: Transcript, first: int = 0, last: int | None = None) -> str:
    segs = t.segments[first:last]
    return "\n".join(f"[{s.index}] {format_timestamp(s.start)} {s.text}" for s in segs)


# --- grounding -----------------------------------------------------------------------

_STOP = set("""a an the and or but if then than that this these those to of in on at by for with from as is are was
were be been being it its they them their there here he she his her we our you your i me my not no so do does did
can could would should will may might also very more most such into about over under between which who whom whose
what when where why how all any each other some many much one two just only same own too through because
while after before during within without across again around also often""".split())


def _stems(text: str) -> set[str]:
    words = re.findall(r"[^\W\d_]+|\d+", text.lower())
    return {w[:6] for w in words if (len(w) > 2 or w.isdigit()) and w not in _STOP}


class Grounder:
    """Matches written statements to the transcript lines that support them."""

    def __init__(self, t: Transcript):
        self.t = t
        self.by_index = {s.index: s for s in t.segments}
        self.order = [s.index for s in t.segments]
        self.pos = {idx: i for i, idx in enumerate(self.order)}
        self.stems = {s.index: _stems(s.text) for s in t.segments}
        df: Counter[str] = Counter()
        for st in self.stems.values():
            df.update(st)
        n = max(1, len(t.segments))
        self.idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}
        # Words that never occur in the transcript are the strongest sign of unsupported content.
        self.unknown_weight = math.log((n + 1) / 0.5)
        self.relinked = 0
        self.dropped = 0
        self.invalid_refs = 0

    def _window(self, idx: int) -> set[str]:
        i = self.pos[idx]
        out: set[str] = set()
        for j in range(max(0, i - 1), min(len(self.order), i + 2)):
            out |= self.stems[self.order[j]]
        return out

    def score(self, text: str, refs: list[int], window: bool = True) -> float:
        """Share of the statement's (IDF-weighted) content words found in the cited lines
        (and, with window=True, their immediate neighbours - a sentence can straddle two lines)."""
        words = _stems(text)
        if not words or not refs:
            return 0.0
        pool: set[str] = set()
        for r in refs:
            pool |= self._window(r) if window else self.stems[r]
        total = sum(self.idf.get(w, self.unknown_weight) for w in words)
        hit = sum(self.idf.get(w, self.unknown_weight) for w in words if w in pool)
        return hit / total if total else 0.0

    def best(self, text: str, k: int = 2) -> tuple[list[int], float]:
        scored = sorted(((self.score(text, [idx], window=False), idx) for idx in self.order), reverse=True)[:k]
        if not scored or scored[0][0] <= 0:
            return [], 0.0
        top = scored[0][0]
        refs = [idx for sc, idx in scored if sc >= top * 0.8]
        return refs, self.score(text, refs)

    def ground(self, text: str, refs: list[Any], required: bool = True,
               accept: float = 0.25, minimum: float = 0.12) -> list[int] | None:
        """Returns validated refs, re-linked refs, or None (statement should be dropped)."""
        valid = []
        for r in refs:
            if isinstance(r, int) and r in self.by_index and r not in valid:
                valid.append(r)
            else:
                self.invalid_refs += 1
        valid = self._prune(text, valid)
        current = self.score(text, valid)
        if valid and current >= accept:
            return valid
        alt, alt_score = self.best(text)
        if alt and alt_score > current and alt_score >= minimum:
            self.relinked += 1
            return alt
        if valid and current >= minimum:
            return valid
        if required:
            self.dropped += 1
            return None
        return valid

    def _prune(self, text: str, refs: list[int], keep: int = 4) -> list[int]:
        """Models over-cite. Keep the cited lines that individually support the statement best."""
        if len(refs) <= 1:
            return refs
        scored = [(self.score(text, [r], window=False), r) for r in refs]
        top = max(sc for sc, _ in scored)
        good = [r for sc, r in scored if sc >= max(0.08, top * 0.5)]
        good = sorted(good, key=lambda r: -dict((rr, sc) for sc, rr in scored)[r])[:keep]
        return sorted(good, key=lambda r: self.pos[r]) or refs[:1]

    def highlights(self, refs: list[int]) -> list[Highlight]:
        """Group referenced lines into contiguous passages, in transcript order."""
        ordered = sorted(set(refs), key=lambda r: self.pos[r])
        groups: list[list[int]] = []
        for r in ordered:
            if groups and self.pos[r] - self.pos[groups[-1][-1]] == 1:
                groups[-1].append(r)
            else:
                groups.append([r])
        return [Highlight(segment_indices=g, start=self.by_index[g[0]].start, end=self.by_index[g[-1]].end,
                          excerpt=" ".join(self.by_index[i].text for i in g)) for g in groups]


# --- writer ---------------------------------------------------------------------------

class AINotesWriter:
    def __init__(self, llm: LLM, fact_check: bool | None = None):
        self.llm = llm
        if fact_check is None:
            mode = str(get_settings().ai_fact_check).lower()
            fact_check = mode in ("on", "true", "1") or (mode == "auto" and llm.method.startswith("ai:claude"))
        self.fact_check = fact_check

    def write(self, t: Transcript, title: str | None, report: Report | None = None,
              should_cancel: Callable[[], bool] | None = None, base_limitations: list[str] | None = None) -> StudyMaterials:
        report = report or (lambda d, f: None)
        if not t.segments:
            raise LLMError("The transcript is empty.")
        words = sum(len(s.text.split()) for s in t.segments)
        duration = t.duration or t.segments[-1].end
        n_key = int(min(12, max(4, round(words / 300) + 3)))
        target_words = int(min(300, max(80, words * 0.06)))
        short = duration < 120
        header = f"Video title: {title}\n" if title else ""
        instructions = _notes_instructions(n_key, target_words, short)
        full = number_transcript(t)
        budget = self.llm.context_tokens - 4500 - estimate_tokens(instructions)

        def progress(prefix: str, base: float | None):
            def cb(chars: int) -> None:
                report(f"{prefix} (about {max(1, chars // 6)} words written)", base)
            return cb

        if estimate_tokens(full) <= budget:
            report("Reading the full transcript", None)
            prompt = f"{header}TRANSCRIPT ({len(t.segments)} numbered lines):\n{full}\n\n{instructions}"
            raw = self.llm.generate_json(SYSTEM, prompt, NOTES_SCHEMA,
                                         progress("Writing study notes", None), should_cancel)
        else:
            digests = self._read_in_parts(t, header, budget, report, should_cancel)
            report("Writing study notes from the whole video", None)
            prompt = (f"{header}You have read the whole video in {len(digests)} parts. Below are your referenced "
                      f"digests of each part, in order. Line numbers refer to the original transcript "
                      f"(lines {t.segments[0].index}–{t.segments[-1].index}).\n\n"
                      + "\n\n".join(digests) + f"\n\n{instructions}")
            raw = self.llm.generate_json(SYSTEM, prompt, NOTES_SCHEMA,
                                         progress("Writing study notes", None), should_cancel)
        g = Grounder(t)
        notes = self._assemble(t, raw, base_limitations or [], g)
        if self.fact_check:
            notes = fact_check(self.llm, t, g, notes, report, should_cancel, clean_text)
        return notes

    def _read_in_parts(self, t: Transcript, header: str, budget: int, report: Report,
                       should_cancel) -> list[str]:
        part_budget = max(1500, min(budget, self.llm.context_tokens // 2) - 1500)
        parts: list[list] = [[]]
        size = 0
        for s in t.segments:
            cost = estimate_tokens(f"[{s.index}] 00:00:00 {s.text}\n")
            if parts[-1] and size + cost > part_budget:
                parts.append([])
                size = 0
            parts[-1].append(s)
            size += cost
        digests: list[str] = []
        running = ""
        for i, segs in enumerate(parts, 1):
            report(f"Reading part {i} of {len(parts)} of the transcript", (i - 1) / len(parts))
            text = "\n".join(f"[{s.index}] {format_timestamp(s.start)} {s.text}" for s in segs)
            context = f"What came before, in brief: {running}" if running else "This is the start of the video."
            prompt = (f"{header}{DIGEST_INSTRUCTIONS.format(part=i, parts=len(parts), first=segs[0].index, last=segs[-1].index, context=context)}"
                      f"\n\nTRANSCRIPT PART {i}:\n{text}")
            d = self.llm.generate_json(SYSTEM, prompt, DIGEST_SCHEMA, None, should_cancel)
            running = (running + " " + str(d.get("part_summary", ""))).strip()[-2500:]
            digests.append(self._digest_text(i, segs, d))
        return digests

    @staticmethod
    def _digest_text(i: int, segs: list, d: dict[str, Any]) -> str:
        def refs(x: dict) -> str:
            r = [str(v) for v in x.get("refs", []) if isinstance(v, int)]
            return f" [lines {', '.join(r)}]" if r else ""

        lines = [f"PART {i} ({format_timestamp(segs[0].start)}–{format_timestamp(segs[-1].end)}, "
                 f"lines {segs[0].index}–{segs[-1].index})", f"Summary: {d.get('part_summary', '')}"]
        lines += [f"Section starting at line {s.get('first_ref')}: {s.get('title')} - {s.get('summary')}"
                  for s in d.get("sections", [])]
        lines += [f"Idea: {x.get('point')}{refs(x)}" for x in d.get("ideas", [])]
        lines += [f"Term: {x.get('term')} - {x.get('explanation')}"
                  f"{' (defined in the video)' if x.get('defined_in_video') else ''}{refs(x)}" for x in d.get("terms", [])]
        lines += [f"Claim: {x.get('text')}{refs(x)}" for x in d.get("claims", [])]
        lines += [f"Unclear: {x}" for x in d.get("unclear", [])]
        return "\n".join(lines)

    def _assemble(self, t: Transcript, raw: dict[str, Any], base_limitations: list[str], g: Grounder) -> StudyMaterials:
        s = clean_text

        def lst(key: str) -> list[dict]:
            v = raw.get(key)
            return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

        paragraphs = []
        for p in lst("summary_paragraphs"):
            text = s(p.get("text"))
            if text:
                refs = g.ground(text, p.get("refs", []), required=False, accept=0.2, minimum=0.08) or []
                paragraphs.append(CitedText(text=text, refs=g.highlights(refs)))
        if not paragraphs:
            raise LLMError("The AI model did not write a summary.")

        key_points = []
        for kp in lst("key_points"):
            heading, expl = s(kp.get("heading")), s(kp.get("explanation"))
            refs = g.ground(f"{heading} {expl}", kp.get("refs", []))
            if refs is None or not expl:
                continue
            hl = g.highlights(refs)
            key_points.append(KeyPoint(id=f"k{len(key_points) + 1}", heading=heading or expl[:60], text=expl,
                                       timestamp=hl[0].start, support=hl[0], refs=hl))

        concepts = []
        for c in lst("concepts"):
            term, expl = s(c.get("term")), s(c.get("explanation"))
            refs = g.ground(f"{term} {term} {expl}", c.get("refs", []))
            if refs is None or not term:
                continue
            hl = g.highlights(refs)
            mentions = sum(1 for seg in t.segments if term.lower() in seg.text.lower())
            concepts.append(Concept(term=term, explanation=expl,
                                    explanation_kind="definition" if c.get("defined_in_video") else "context",
                                    timestamp=hl[0].start, mentions=mentions, refs=hl))

        conclusions = []
        for c in lst("conclusions"):
            text = s(c.get("text"))
            refs = g.ground(text, c.get("refs", []))
            if refs is None or not text:
                continue
            hl = g.highlights(refs)
            conclusions.append(Conclusion(text=text, timestamp=hl[0].start, refs=hl))

        topics = self._topics(t, g, lst("topics"), s)

        questions = []
        for q in lst("study_questions"):
            text = s(q.get("question"))
            if not text:
                continue
            refs = g.ground(text, q.get("refs", []), required=False, accept=0.15, minimum=0.08) or []
            questions.append(CitedText(text=text, refs=g.highlights(refs)))

        llm = self.llm
        limitations = [f"These notes were written by AI ({llm.label}) after reading the full transcript. AI can "
                       "misread or oversimplify - use the linked passages to check anything important."]
        limitations += [s(u) for u in (raw.get("uncertainties") or []) if s(u)]
        removed = g.dropped
        if removed:
            limitations.append(f"{removed} AI-written statement{'s were' if removed != 1 else ' was'} removed "
                               "because no supporting passage could be found in the transcript.")
        limitations += [x for x in base_limitations if not x.startswith("This summary is extractive")]
        log.info("ai notes grounded: %d key points, %d concepts, relinked=%d dropped=%d invalid_refs=%d",
                 len(key_points), len(concepts), g.relinked, g.dropped, g.invalid_refs)

        return StudyMaterials(
            method=llm.method, generator="ai", generated_by=llm.label,
            executive_summary="\n\n".join(p.text for p in paragraphs),
            summary_paragraphs=paragraphs,
            overview=s(raw.get("overview")) or paragraphs[0].text,
            key_points=key_points, concepts=concepts, conclusions=conclusions, topics=topics,
            study_questions=[q.text for q in questions], questions=questions,
            limitations=limitations,
        )

    @staticmethod
    def _topics(t: Transcript, g: Grounder, items: list[dict], s) -> list[Topic]:
        found = []
        for tp in items:
            title = s(tp.get("title"))
            if not title:
                continue
            first = tp.get("first_ref")
            refs = [r for r in tp.get("refs", []) if isinstance(r, int) and r in g.by_index]
            if not (isinstance(first, int) and first in g.by_index):
                first = min(refs, key=lambda r: g.pos[r]) if refs else None
            if first is None:
                alt, _ = g.best(f"{title} {s(tp.get('summary'))}", k=1)
                first = alt[0] if alt else None
            if first is None:
                continue
            found.append((g.pos[first], title, s(tp.get("summary")), [s(x) for x in tp.get("subtopics", []) if s(x)], refs))
        found.sort(key=lambda x: x[0])
        dedup = []
        for item in found:
            if dedup and item[0] == dedup[-1][0]:
                continue
            dedup.append(item)
        if not dedup:
            return [Topic(id="t1", title="Full recording", summary="", start=t.segments[0].start,
                          end=t.segments[-1].end)]
        topics = []
        for i, (pos, title, summary, subs, refs) in enumerate(dedup):
            start = t.segments[0].start if i == 0 else t.segments[pos].start
            end_pos = dedup[i + 1][0] - 1 if i + 1 < len(dedup) else len(t.segments) - 1
            inside = [r for r in refs if pos <= g.pos[r] <= end_pos] or [g.order[pos]]
            topics.append(Topic(id=f"t{i + 1}", title=title, summary=summary, start=start,
                                end=t.segments[end_pos].end, subtopics=subs[:5], passages=g.highlights(inside)[:4]))
        return topics
