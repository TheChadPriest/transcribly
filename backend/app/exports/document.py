"""One study-document model, rendered to TXT, Markdown, DOCX and PDF so all formats match."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..models import CitationRun, Results
from ..transcription.segments import format_timestamp

SOURCE_LABELS = {
    "human_captions": "Creator-provided captions (retrieved from the platform)",
    "auto_captions": "Platform auto-generated captions (not verified)",
    "speech_recognition": "Automatic speech recognition",
}


@dataclass
class Block:
    kind: Literal["h1", "h2", "h3", "p", "bullet", "meta", "transcript"]
    runs: list[CitationRun] = field(default_factory=list)
    timestamp: str | None = None

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


def _p(kind, text: str, timestamp: str | None = None) -> Block:
    return Block(kind, [CitationRun(text=text)], timestamp)


def build_document(res: Results, style: str = "apa", timestamps: bool = True,
                   sections: set[str] | None = None) -> list[Block]:
    want = sections or {"overview", "key_points", "concepts", "conclusions", "topics", "questions",
                        "transcript", "citation", "limitations"}
    m = res.metadata
    t = res.transcript
    s = res.study
    out: list[Block] = [_p("h1", m.title or "Untitled recording")]

    info = []
    if m.creator:
        info.append(f"Creator: {m.creator}")
    if m.channel and m.channel != m.creator:
        info.append(f"Channel: {m.channel}")
    if m.published:
        info.append(f"Published: {m.published}")
    if m.platform:
        info.append(f"Platform: {m.platform}")
    if m.url and m.source_type != "uploaded_file":
        info.append(f"URL: {m.url}")
    if t.duration:
        info.append(f"Duration: {format_timestamp(t.duration)}")
    info.append(f"Transcript source: {SOURCE_LABELS.get(t.source, t.source)}")
    if t.language:
        info.append(f"Language: {t.language}")
    out += [_p("meta", line) for line in info]

    def refs(hl) -> str:
        return f" [{', '.join(format_timestamp(h.start) for h in hl)}]" if timestamps and hl else ""

    if s and s.generator == "ai" and s.generated_by:
        out.append(_p("meta", f"Study notes: written by AI ({s.generated_by}) from the transcript. "
                              "Timestamps in brackets point to the passages each statement is based on."))

    if s and "overview" in want:
        out.append(_p("h2", "Executive summary"))
        if s.summary_paragraphs:
            out += [_p("p", p.text + refs(p.refs)) for p in s.summary_paragraphs]
        else:
            out.append(_p("p", s.executive_summary))
    elif not s and res.summary_error:
        out.append(_p("p", res.summary_error))

    if s and "key_points" in want and s.key_points:
        out.append(_p("h2", "Key takeaways"))
        for k in s.key_points:
            ts = refs(k.refs) or (f" [{format_timestamp(k.timestamp)}]" if timestamps and k.timestamp is not None else "")
            out.append(_p("bullet", f"{k.heading}: {k.text}{ts}"))

    if s and "concepts" in want and s.concepts:
        out.append(_p("h2", "Key concepts"))
        for c in s.concepts:
            label = "defined in the video" if c.explanation_kind == "definition" else (
                "explained from the video" if s.generator == "ai" else "in context")
            ts = refs(c.refs) or (f" [{format_timestamp(c.timestamp)}]" if timestamps and c.timestamp is not None else "")
            out.append(_p("bullet", f"{c.term} ({label}): {c.explanation}{ts}"))

    if s and "conclusions" in want and s.conclusions:
        out.append(_p("h2", "Main conclusions (as stated in the video)"))
        for c in s.conclusions:
            ts = refs(c.refs) or (f" [{format_timestamp(c.timestamp)}]" if timestamps and c.timestamp is not None else "")
            out.append(_p("bullet", f"{c.text}{ts}"))

    if s and "topics" in want and s.topics:
        out.append(_p("h2", "Topic outline"))
        for i, tp in enumerate(s.topics, 1):
            rng = f" ({format_timestamp(tp.start)}–{format_timestamp(tp.end)})" if timestamps else ""
            out.append(_p("h3", f"{i}. {tp.title}{rng}"))
            out.append(_p("p", tp.summary))
            if tp.subtopics:
                out.append(_p("p", "Also covers: " + ", ".join(tp.subtopics)))

    if s and "questions" in want and s.study_questions:
        out.append(_p("h2", "Study questions"))
        if s.questions:
            out += [_p("bullet", q.text + (f" (where to look:{refs(q.refs)[1:]})" if refs(q.refs) else "")) for q in s.questions]
        else:
            out += [_p("bullet", q) for q in s.study_questions]

    if "transcript" in want:
        out.append(_p("h2", "Full transcript"))
        by_index = {seg.index: seg for seg in t.segments}
        paras = t.paragraphs or []
        if not paras:
            out += [_p("transcript", seg.text, format_timestamp(seg.start) if timestamps else None) for seg in t.segments]
        for para in paras:
            text = " ".join(by_index[i].text for i in para.segment_indices if i in by_index)
            out.append(_p("transcript", text, format_timestamp(para.start) if timestamps else None))

    if "citation" in want:
        cit = next((c for c in res.citations.citations if c.style == style), res.citations.citations[0])
        out.append(_p("h2", f"Citation ({cit.label})"))
        out.append(Block("p", list(cit.reference)))
        if cit.note:
            out.append(_p("p", f"{cit.note_label}: {cit.note}"))
        if cit.in_text:
            out.append(_p("p", f"In-text: {cit.in_text}"))
        out.append(_p("p", "Generated reference: verify against your institution's style guide."))

    if s and "limitations" in want and s.limitations:
        out.append(_p("h2", "Notes on accuracy"))
        out += [_p("bullet", x) for x in s.limitations]
    return out


def to_text(blocks: list[Block]) -> str:
    lines: list[str] = []
    for b in blocks:
        if b.kind == "h1":
            lines += [b.text, "=" * min(len(b.text), 80), ""]
        elif b.kind == "h2":
            lines += ["", b.text.upper(), ""]
        elif b.kind == "h3":
            lines += ["", b.text]
        elif b.kind == "meta":
            lines.append(b.text)
        elif b.kind == "bullet":
            lines.append(f"- {b.text}")
        elif b.kind == "transcript":
            lines += [f"[{b.timestamp}] {b.text}" if b.timestamp else b.text, ""]
        else:
            lines += [b.text, ""]
    return "\n".join(lines).strip() + "\n"


def _md_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("#", "\\#") \
        .replace("[", "\\[").replace("]", "\\]")


def _md_runs(b: Block) -> str:
    return "".join(f"*{_md_escape(r.text.strip())}*" + (" " if r.text.endswith(" ") else "")
                   if r.italic else _md_escape(r.text) for r in b.runs)


def to_markdown(blocks: list[Block]) -> str:
    lines: list[str] = []
    for b in blocks:
        t = _md_runs(b)
        if b.kind == "h1":
            lines += [f"# {t}", ""]
        elif b.kind == "h2":
            lines += ["", f"## {t}", ""]
        elif b.kind == "h3":
            lines += ["", f"### {t}", ""]
        elif b.kind == "meta":
            lines.append(f"- {t}")
        elif b.kind == "bullet":
            lines.append(f"- {t}")
        elif b.kind == "transcript":
            lines += [f"**\\[{b.timestamp}\\]** {t}" if b.timestamp else t, ""]
        else:
            lines += [t, ""]
    return "\n".join(lines).strip() + "\n"
