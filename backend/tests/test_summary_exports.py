import io
import json
import re
import zipfile

import pytest

from app.citations.formatter import format_all
from app.exports.binary import to_docx, to_pdf
from app.exports.document import build_document, to_markdown, to_text
from app.models import CitationMetadata, Results, Segment, StudyMaterials, Transcript
from app.summarization.extractive import ExtractiveSummarizer
from app.summarization.text import keyphrases, split_sentences
from app.transcription.segments import build_paragraphs

from .conftest import MEDIA

LECTURE = json.loads((MEDIA / "expected.json").read_text())["lecture"]


def lecture_transcript(repeat: int = 1) -> Transcript:
    segs = []
    t = 0.0
    for _r in range(repeat):
        for para in LECTURE:
            for sent in re.split(r"(?<=[.!?])\s+", para):
                dur = len(sent.split()) * 0.4
                segs.append(Segment(index=len(segs), start=round(t, 2), end=round(t + dur, 2), text=sent))
                t += dur + 0.3
            t += 1.5
    tr = Transcript(source="speech_recognition", language="en", duration=t, segments=segs)
    tr.paragraphs = build_paragraphs(segs)
    return tr


def all_text(t: Transcript) -> str:
    return " ".join(s.text for s in t.segments)


def test_split_sentences_tracks_segments():
    segs = [Segment(index=0, start=0, end=2, text="First part of a"),
            Segment(index=1, start=2, end=4, text="sentence. Second one starts"),
            Segment(index=2, start=4, end=6, text="and ends here.")]
    sents = split_sentences(segs)
    assert [s.text for s in sents] == ["First part of a sentence.", "Second one starts and ends here."]
    assert sents[0].segment_indices == [0, 1] and sents[1].segment_indices == [1, 2]
    assert sents[1].start == 2 and sents[1].end == 6


def test_split_sentences_unpunctuated_auto_captions():
    words = ("so today we are going to talk about the french revolution and why it changed europe " * 12).split()
    segs = [Segment(index=i, start=i * 2, end=i * 2 + 2, text=" ".join(words[i * 6:(i + 1) * 6])) for i in range(len(words) // 6)]
    sents = split_sentences(segs, max_words=45)
    assert len(sents) > 1
    assert all(len(s.text.split()) <= 50 for s in sents)
    assert " ".join(s.text for s in sents) == " ".join(s.text for s in segs)


def test_keyphrases_do_not_cross_punctuation():
    phr = [p for p, _, _ in keyphrases(["We saw rain, snow and hail.", "Rain, snow, more rain, snow."], min_count=1)]
    assert "rain snow" not in phr


def test_extractive_summary_is_grounded():
    t = lecture_transcript()
    study = ExtractiveSummarizer().summarize(t, "The water cycle")
    corpus = all_text(t)
    # every sentence of the summary, key points and conclusions is verbatim transcript text
    for sent in re.split(r"(?<=[.!?])\s+", study.executive_summary):
        assert sent in corpus
    for k in study.key_points:
        assert k.text in corpus
        assert k.support and k.support.segment_indices
        assert k.timestamp == pytest.approx(t.segments[k.support.segment_indices[0]].start)
    for c in study.conclusions:
        assert c.text in corpus
    assert 50 <= len(study.executive_summary.split()) <= 320
    terms = {c.term.lower(): c for c in study.concepts}
    assert "evaporation" in terms and terms["evaporation"].explanation_kind == "definition"
    assert "precipitation" in terms
    assert any("conclusion" in c.text.lower() for c in study.conclusions)
    assert study.study_questions
    assert study.limitations and "extractive" in study.limitations[0]


def test_long_transcript_gets_multiple_ordered_topics():
    t = lecture_transcript(repeat=6)  # ~13 minutes of varied text
    study = ExtractiveSummarizer().summarize(t)
    assert len(study.topics) >= 2
    starts = [tp.start for tp in study.topics]
    assert starts == sorted(starts)
    assert study.topics[0].start == 0
    assert 3 <= len(study.key_points) <= 12
    assert all(tp.title for tp in study.topics)


def test_short_transcript_falls_back_to_full_text():
    t = Transcript(source="speech_recognition", duration=4, segments=[Segment(index=0, start=0, end=3, text="Hello and welcome.")])
    study = ExtractiveSummarizer().summarize(t)
    assert study.executive_summary == "Hello and welcome."
    assert study.key_points == []
    assert any("too short" in x for x in study.limitations)


def test_auto_caption_limitation_disclosed():
    t = lecture_transcript()
    t.source = "auto_captions"
    study = ExtractiveSummarizer().summarize(t)
    assert any("automatically generated captions" in x for x in study.limitations)


def results_for(t: Transcript, study: StudyMaterials | None = None) -> Results:
    meta = CitationMetadata(creator="Prof. Rivera", title="The Water Cycle", published="2024-02-01", platform="YouTube",
                            url="https://www.youtube.com/watch?v=abcdefghijk", source_type="youtube_video")
    return Results(job_id="x" * 24, metadata=meta, citations=format_all(meta), transcript=t, study=study)


def test_text_and_markdown_exports_complete():
    t = lecture_transcript()
    res = results_for(t, ExtractiveSummarizer().summarize(t))
    blocks = build_document(res, style="mla")
    txt = to_text(blocks)
    md = to_markdown(blocks)
    for heading in ("EXECUTIVE SUMMARY", "KEY TAKEAWAYS", "TOPIC OUTLINE", "FULL TRANSCRIPT", "CITATION (MLA 9TH EDITION)"):
        assert heading in txt
    assert md.startswith("# The Water Cycle")
    assert "## Full transcript" in md
    assert t.segments[-1].text in txt  # nothing truncated
    assert "[00:00:00]" in txt
    assert "*YouTube*" in md
    no_ts = to_text(build_document(res, timestamps=False))
    assert "[00:00:00]" not in no_ts


def test_export_without_summary_still_has_transcript():
    t = lecture_transcript()
    res = results_for(t, None)
    res.summary_error = "Summary unavailable."
    txt = to_text(build_document(res))
    assert "Summary unavailable." in txt and t.segments[-1].text in txt


def test_binary_exports_for_long_transcript():
    # ~5,000 segments (roughly a 4-5 hour recording) must export completely
    segs = [Segment(index=i, start=i * 3.0, end=i * 3.0 + 2.5, text=f"Sentence number {i} about thermodynamics — ünïcødé ok.")
            for i in range(5000)]
    t = Transcript(source="speech_recognition", language="en", duration=15000, segments=segs)
    t.paragraphs = build_paragraphs(segs)
    res = results_for(t, None)
    blocks = build_document(res)
    docx = to_docx(blocks)
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        xml = z.read("word/document.xml").decode()
    assert "Sentence number 4999 about thermodynamics" in xml
    pdf = to_pdf(blocks, "Long")
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page") > 50
