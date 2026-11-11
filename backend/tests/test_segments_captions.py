import json

import pytest

from app.models import Segment
from app.sources.captions import parse_json3, parse_vtt
from app.transcription.engine import sentence_segments
from app.transcription.segments import (
    build_paragraphs,
    dedupe_boundary,
    drop_adjacent_duplicates,
    format_timestamp,
    parse_timestamp,
)


def seg(i, a, b, t, **kw):
    return Segment(index=i, start=a, end=b, text=t, **kw)


@pytest.mark.parametrize("secs,out", [(0, "00:00:00"), (59.99, "00:00:59"), (61, "00:01:01"),
                                      (3600 * 2 + 5, "02:00:05"), (-3, "00:00:00"), (None, "--:--:--"),
                                      (36000 * 10, "100:00:00")])
def test_format_timestamp(secs, out):
    assert format_timestamp(secs) == out


@pytest.mark.parametrize("raw,secs", [("00:01:02.500", 62.5), ("01:02", 62), ("5", 5), ("00:00:01,250", 1.25),
                                      ("1:00:00", 3600)])
def test_parse_timestamp(raw, secs):
    assert parse_timestamp(raw) == pytest.approx(secs)


def test_parse_timestamp_invalid():
    with pytest.raises(ValueError):
        parse_timestamp("abc")


def test_dedupe_boundary_drops_repeated_segment_and_overlap_words():
    prev = [seg(0, 0, 5, "The light reactions produce oxygen.")]
    incoming = [seg(0, 4.8, 5.2, "The light reactions produce oxygen."),
                seg(0, 5.2, 9, "light reactions produce oxygen and the Calvin cycle builds sugar")]
    out = dedupe_boundary(prev, incoming)
    assert len(out) == 1
    assert out[0].text == "and the Calvin cycle builds sugar"


def test_dedupe_boundary_keeps_distinct_text():
    prev = [seg(0, 0, 5, "First sentence here.")]
    incoming = [seg(0, 5.1, 8, "Second sentence here.")]
    assert dedupe_boundary(prev, incoming) == incoming
    assert dedupe_boundary([], incoming) == incoming


def test_drop_adjacent_duplicates():
    segs = [seg(0, 0, 1, "Hello there."), seg(1, 1, 2, "hello there"), seg(2, 2, 3, "Next."), seg(3, 9, 10, "Next.")]
    out = drop_adjacent_duplicates(segs)
    assert [s.text for s in out] == ["Hello there.", "Next.", "Next."]
    assert out[0].end == 2


def test_paragraphs_preserve_all_segments_and_break_on_pause():
    segs = [seg(i, i * 2, i * 2 + 1.5, f"Sentence {i}.") for i in range(10)]
    segs.append(seg(10, 60, 62, "After a long pause."))
    paras = build_paragraphs(segs, max_words=6)
    flat = [i for p in paras for i in p.segment_indices]
    assert flat == list(range(11))
    assert paras[-1].segment_indices == [10]
    assert all(p.start <= p.end for p in paras)


def test_paragraph_breaks_at_topic_boundary():
    segs = [seg(i, i * 2, i * 2 + 1.9, f"Sentence {i}.") for i in range(6)]
    paras = build_paragraphs(segs, breaks={6.0})
    assert [p.segment_indices for p in paras] == [[0, 1, 2], [3, 4, 5]]


def test_sentence_segments_regroup_words_and_clamp():
    words = [(0.0, 0.4, " Hello", 0.9, False), (0.4, 0.9, " world.", 0.9, False),
             (1.0, 1.4, " Second", 0.3, False), (1.4, 12.0, " part", 0.3, False)]
    out = sentence_segments(words, offset=100, chunk_len=10)
    assert [s.text for s in out] == ["Hello world.", "Second part"]
    assert out[0].start == 100 and out[0].end == pytest.approx(100.9)
    assert out[1].end == 110  # clamped to the chunk
    assert out[1].low_confidence and not out[0].low_confidence


def test_sentence_segments_split_on_pause():
    words = [(0, 0.5, " one", 0.9, False), (3.0, 3.5, " two", 0.9, False)]
    assert [s.text for s in sentence_segments(words, 0, 10)] == ["one", "two"]


ROLLING_VTT = """WEBVTT
Kind: captions

00:00:00.000 --> 00:00:02.000
<c>so</c><00:00:00.500><c> today</c>

00:00:02.000 --> 00:00:02.010
so today

00:00:02.010 --> 00:00:04.000
so today
we talk about cells.

00:00:04.000 --> 00:00:06.000
we talk about cells.
Cells are the unit of life.
"""


def test_parse_vtt_rolling_auto_captions():
    segs = parse_vtt(ROLLING_VTT)
    text = " ".join(s.text for s in segs)
    assert text == "so today we talk about cells. Cells are the unit of life."
    assert segs[0].start == 0
    assert [s.index for s in segs] == list(range(len(segs)))


def test_parse_srt():
    srt = "1\n00:00:01,000 --> 00:00:03,500\nHello &amp; welcome.\n\n2\n00:00:04,000 --> 00:00:06,000\n<i>Second line.</i>\n"
    segs = parse_vtt(srt)
    # short cues are merged into phrase-sized segments; tags and entities are cleaned
    assert " ".join(s.text for s in segs) == "Hello & welcome. Second line."
    assert segs[0].start == 1.0 and segs[-1].end == 6.0


def test_parse_json3():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "Photosynthesis "}, {"utf8": "is a process."}]},
        {"tStartMs": 1500, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2500, "dDurationMs": 3000, "segs": [{"utf8": "Plants use light to make sugar today."}]},
    ]}
    segs = parse_json3(json.dumps(data))
    assert " ".join(s.text for s in segs) == "Photosynthesis is a process. Plants use light to make sugar today."
    assert all(segs[i].end <= segs[i + 1].start for i in range(len(segs) - 1))
