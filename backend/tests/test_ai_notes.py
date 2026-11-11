"""AI study notes: grounding of references, multi-part reading, fallbacks and the model clients.
Models are faked here so the tests are deterministic; real-model runs are in docs/TEST_REPORT.md."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.ai.factcheck import VERIFY_SCHEMA
from app.ai.llm import ClaudeLLM, LLMError, OllamaLLM
from app.ai.notes import DIGEST_SCHEMA, NOTES_SCHEMA, AINotesWriter, Grounder
from app.models import Segment, Transcript
from app.summarization.service import StudyNotesService

from .test_summary_exports import lecture_transcript

# line numbers in lecture_transcript(): 0 welcome, 1 today we will look at..., 2 first stage evaporation,
# 3 evaporation is the process..., 4 most evaporation over oceans, 5 transpiration, 6 second stage condensation,
# 7 condensation is when..., 8 droplets dust clouds, 9 warm air rises, 10 third stage precipitation,
# 11 precipitation refers to..., 12 droplets combine heavy, 13 fourth stage collection, 14 collects oceans lakes,
# 15 groundwater thousands of years, 16 human activity, 17 warmer climate, 18 cutting forests,
# 19 in conclusion..., 20 key point to remember


def fact_check_reply(prompt, verdicts):
    """Answer a fact-check batch: 'supported' unless a statement matches a configured fragment."""
    import re as _re

    results = []
    for n, text in _re.findall(r"^STATEMENT (\d+): (.*)$", prompt, flags=_re.M):
        verdict, evidence, corrected = "supported", "", text
        block = prompt.split(f"LINES FOR STATEMENT {n}:\n", 1)[1].split("\n\nSTATEMENT", 1)[0]
        first_line = _re.sub(r"^\[\d+\] \S+ ", "", block.splitlines()[0])
        for frag, (v, e, c) in verdicts.items():
            if frag in text:
                verdict, evidence, corrected = v, (first_line if e == "@lines" else e), c
        results.append({"id": int(n), "verdict": verdict, "evidence": evidence, "corrected": corrected})
    return {"results": results}


class FakeLLM:
    label = "Fake model (test)"
    method = "ai:fake:test"

    def __init__(self, responses, context_tokens=100_000):
        self.responses = list(responses)
        self.context_tokens = context_tokens
        self.calls = []

    verdicts: dict = {}  # statement text fragment -> (verdict, corrected) for the fact-check pass

    def generate_json(self, system, prompt, schema, on_progress=None, should_cancel=None):
        self.calls.append((prompt, schema))
        if schema is VERIFY_SCHEMA:
            return fact_check_reply(prompt, self.verdicts)
        if on_progress:
            on_progress(120)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def good_notes(**override):
    notes = {
        "overview": "The lecture explains how water moves around the Earth in a continuous cycle.",
        "summary_paragraphs": [
            {"text": "The lecture explains the water cycle: evaporation, condensation, precipitation and collection.",
             "refs": [19]},
            {"text": "Human activity is changing the cycle; a warmer climate increases evaporation, bringing heavier rain "
                     "in some regions and droughts in others. [17, 18]", "refs": [17, 99]},
        ],
        "key_points": [
            {"heading": "Evaporation turns water into vapor",
             "explanation": "Heat from the sun turns liquid water into water vapor, mostly over the oceans.",
             "refs": [3, 4, 0, 20]},
            {"heading": "Clouds form by condensation",
             "explanation": "As water vapor cools it condenses into tiny droplets around dust particles, forming clouds.",
             "refs": [7, 8]},
            # wrong reference: the statement is about precipitation but cites the welcome line
            {"heading": "Precipitation",
             "explanation": "Precipitation is water falling from clouds as rain, snow, sleet or hail.",
             "refs": [0]},
            # nothing in the transcript supports this one
            {"heading": "Quantum tunnelling", "explanation": "Electrons tunnel through barriers in semiconductors.",
             "refs": [5]},
        ],
        "concepts": [{"term": "Transpiration", "explanation": "Water vapor released by plants through their leaves.",
                      "defined_in_video": True, "refs": [5]}],
        "conclusions": [{"text": "The speaker concludes that the total amount of water on Earth stays almost the same "
                                 "while where it is stored keeps changing.", "refs": [20]}],
        "topics": [
            {"title": "Human impact", "summary": "How people change the cycle.", "first_ref": 16, "subtopics": [], "refs": [17]},
            {"title": "The four stages", "summary": "Evaporation to collection.", "first_ref": 2,
             "subtopics": ["Evaporation", "Condensation"], "refs": [3, 7]},
        ],
        "study_questions": [{"question": "Why does a warmer climate change rainfall patterns?", "refs": [17]}],
        "uncertainties": [],
    }
    notes.update(override)
    return notes


def test_schemas_are_strict_objects():
    for schema in (NOTES_SCHEMA, DIGEST_SCHEMA):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_ai_notes_are_grounded_and_cleaned():
    t = lecture_transcript()
    llm = FakeLLM([good_notes()])
    notes = AINotesWriter(llm).write(t, "The water cycle")
    assert notes.generator == "ai" and notes.method == "ai:fake:test"
    assert notes.generated_by == "Fake model (test)"
    # single pass: the model saw every numbered line of the transcript
    prompt = llm.calls[0][0]
    assert "[0] 00:00:00 Welcome" in prompt and f"[{t.segments[-1].index}]" in prompt
    # leaked "[17, 18]" removed from prose; invalid ref 99 dropped
    p2 = notes.summary_paragraphs[1]
    assert "[17" not in p2.text and p2.text.endswith("others.")
    assert all(i in range(len(t.segments)) for h in p2.refs for i in h.segment_indices)
    headings = [k.heading for k in notes.key_points]
    assert "Quantum tunnelling" not in headings  # unsupported -> removed
    assert any("removed" in x for x in notes.limitations)
    # over-cited refs pruned to the lines that actually support the point
    evap = notes.key_points[0]
    cited = {i for h in evap.refs for i in h.segment_indices}
    assert cited & {3, 4} and not cited & {0, 20}
    # wrong reference re-linked to the precipitation lines
    precip = next(k for k in notes.key_points if k.heading == "Precipitation")
    assert {i for h in precip.refs for i in h.segment_indices} & {10, 11}
    assert precip.timestamp == pytest.approx(t.segments[min(i for h in precip.refs for i in h.segment_indices)].start)
    # excerpts are verbatim transcript text
    for k in notes.key_points:
        for h in k.refs:
            assert h.excerpt == " ".join(t.segments[i].text for i in h.segment_indices)
    # topics re-ordered by position, first starts at the beginning, contiguous
    assert [tp.title for tp in notes.topics] == ["The four stages", "Human impact"]
    assert notes.topics[0].start == t.segments[0].start
    assert notes.topics[1].start == t.segments[16].start
    assert notes.concepts[0].explanation_kind == "definition"
    assert notes.questions[0].refs
    assert notes.executive_summary.startswith("The lecture explains")


def test_long_transcripts_are_read_in_parts_then_written_whole():
    t = lecture_transcript(repeat=4)
    digest = {"part_summary": "Covers the stages of the water cycle.",
              "sections": [{"title": "Stages", "first_ref": 2, "summary": "The four stages."}],
              "ideas": [{"point": "Evaporation turns water to vapor.", "refs": [3]}],
              "terms": [], "claims": [], "unclear": []}
    class BySchema(FakeLLM):
        def generate_json(self, system, prompt, schema, on_progress=None, should_cancel=None):
            self.calls.append((prompt, schema))
            return digest if schema is DIGEST_SCHEMA else good_notes()

    llm = BySchema([], context_tokens=6000)
    reports = []
    notes = AINotesWriter(llm).write(t, None, report=lambda d, f: reports.append((d, f)))
    digest_calls = [c for c in llm.calls if c[1] is DIGEST_SCHEMA]
    assert len(digest_calls) >= 2
    notes_calls = [c for c in llm.calls if c[1] is NOTES_SCHEMA]
    assert len(notes_calls) == 1 and llm.calls.index(notes_calls[0]) > llm.calls.index(digest_calls[-1])
    final_prompt = notes_calls[0][0]
    assert final_prompt.count("PART ") == len(digest_calls)
    assert "What came before, in brief" in digest_calls[1][0]
    assert any(d.startswith("Reading part 1 of") for d, _ in reports)
    assert notes.generator == "ai"
    # every part together covers the whole transcript exactly once
    seen = []
    for prompt, _ in digest_calls:
        part = prompt.split("TRANSCRIPT PART", 1)[1]
        seen += [int(x) for x in __import__("re").findall(r"^\[(\d+)\]", part, flags=8)]
    assert seen == [s.index for s in t.segments]


def test_service_falls_back_to_extractive_with_notice():
    t = lecture_transcript()
    notes = StudyNotesService(llm=FakeLLM([LLMError("The local AI model (Ollama) isn't running.")])).summarize(t)
    assert notes.generator == "extractive"
    assert "isn't running" in notes.notice
    notes2 = StudyNotesService(llm=FakeLLM([good_notes(summary_paragraphs=[])])).summarize(t)
    assert notes2.generator == "extractive" and "did not write a summary" in notes2.notice
    assert StudyNotesService(use_ai=False).summarize(t).notice is None


def test_grounder_scores():
    texts = ["Mitochondria produce energy for the cell.", "Today we start with a short review.",
             "Next we look at a few slides.", "Let us take a short break now.",
             "The nucleus stores genetic information.", "That is all for today."]
    t = Transcript(source="speech_recognition",
                   segments=[Segment(index=i, start=i * 3, end=i * 3 + 2, text=x) for i, x in enumerate(texts)])
    g = Grounder(t)
    assert g.score("Mitochondria make the cell's energy", [0]) > g.score("Mitochondria make the cell's energy", [4])
    assert g.ground("The nucleus holds genetic information", [0]) == [4]  # re-linked
    assert g.ground("Volcanoes erupt lava", [0]) is None


def ollama_transport(lines, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        body = json.loads(request.content)
        assert body["format"]["type"] == "object" and body["stream"] is True
        return httpx.Response(status, content="\n".join(json.dumps(x) for x in lines).encode())
    return httpx.MockTransport(handler)


def test_ollama_client_streams_and_parses():
    lines = [{"message": {"content": '{"a": '}}, {"message": {"content": "1}"}},
             {"done": True, "done_reason": "stop", "eval_count": 5}]
    llm = OllamaLLM("http://x", "qwen2.5:7b", 8192, 30, transport=ollama_transport(lines))
    assert llm.available()
    progress = []
    assert llm.generate_json("s", "p", {"type": "object"}, progress.append) == {"a": 1}
    assert progress[-1] == len('{"a": 1}')
    truncated = OllamaLLM("http://x", "qwen2.5:7b", 8192, 30, transport=ollama_transport(
        [{"message": {"content": '{"a"'}}, {"done": True, "done_reason": "length"}]))
    with pytest.raises(LLMError, match="ran out of room"):
        truncated.generate_json("s", "p", {"type": "object"})
    missing = OllamaLLM("http://x", "other:1b", 8192, 30, transport=ollama_transport([]))
    assert not missing.available()


class _FakeStream:
    def __init__(self, text, stop_reason="end_turn"):
        self.text_stream = iter([text[: len(text) // 2], text[len(text) // 2:]])
        self.msg = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_claude_client_request_shape_and_errors():
    captured = {}

    def stream(**kwargs):
        captured.update(kwargs)
        return _FakeStream('{"ok": true}')

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    llm = ClaudeLLM("claude-opus-5-5", "medium", client=client)
    assert llm.generate_json("sys", "prompt", {"type": "object"}) == {"ok": True}
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["effort"] == "medium"
    assert captured["fallbacks"] == "default" and captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in captured and "temperature" not in captured

    client.beta.messages.stream = lambda **k: _FakeStream("", stop_reason="refusal")
    with pytest.raises(LLMError, match="declined"):
        llm.generate_json("s", "p", {})


def test_regenerate_notes_endpoint(client, monkeypatch):
    from app.summarization import service

    from .conftest import upload, wait_for

    _, body = upload(client, "clean.wav")
    jid, tok = body["job_id"], body["token"]
    wait_for(client, jid, tok)
    first = client.get(f"/api/jobs/{jid}/results", headers={"X-Job-Token": tok}).json()
    assert first["study"]["generator"] == "extractive"

    segs = first["transcript"]["segments"]
    fake = FakeLLM([{
        "overview": "A short recording about photosynthesis and energy.",
        "summary_paragraphs": [{"text": "The recording repeats sentences about photosynthesis and energy.", "refs": [0, 1]}],
        "key_points": [{"heading": "Photosynthesis and energy", "explanation": "Mock sentence number 0 is about photosynthesis and energy.",
                        "refs": [segs[0]["index"]]}],
        "concepts": [], "conclusions": [], "topics": [], "study_questions": [], "uncertainties": []}])
    monkeypatch.setattr(service, "get_llm", lambda: fake)
    r = client.post(f"/api/jobs/{jid}/notes", headers={"X-Job-Token": tok})
    assert r.status_code == 200 and r.json()["status"] in ("queued", "running")  # the worker may already have it
    done = wait_for(client, jid, tok)
    assert done["status"] == "completed"
    assert [s["stage"] for s in done["stages"]] == ["ai_notes", "citations", "finalizing"]
    res = client.get(f"/api/jobs/{jid}/results", headers={"X-Job-Token": tok}).json()
    assert res["study"]["generator"] == "ai"
    assert res["study"]["key_points"][0]["refs"][0]["segment_indices"] == [segs[0]["index"]]
    assert res["transcript"]["segments"] == segs  # transcript untouched
    txt = client.get(f"/api/jobs/{jid}/export/txt", headers={"X-Job-Token": tok}).text
    assert "written by AI (Fake model (test))" in txt
    # only finished jobs can be rewritten
    assert client.post("/api/jobs/" + "A" * 24 + "/notes", headers={"X-Job-Token": tok}).status_code == 404


def test_leaked_reference_formats_are_removed():
    from app.ai.notes import _LEAKED_REFS

    cases = {
        "Most evaporation occurs over the oceans. (Refs: 2, 3, 4, 20).": "Most evaporation occurs over the oceans.",
        "Clouds form [7, 8] from droplets.": "Clouds form from droplets.",
        "As shown (see lines 3-5), it rains.": "As shown, it rains.",
        "In 2024 [sic] it rose 71% (and more).": "In 2024 [sic] it rose 71% (and more).",
    }
    for raw, want in cases.items():
        got = _LEAKED_REFS.sub("", raw)
        assert __import__("re").sub(r"\s+([.,;:])", r"\1", got).strip() == want


def test_fact_check_corrects_and_removes_misstatements():
    t = lecture_transcript()
    notes = good_notes()
    notes["key_points"].append({"heading": "Groundwater", "explanation":
                                "Groundwater stays underground for millions of years before reaching the sea.",
                                "refs": [15]})
    notes["conclusions"].append({"text": "The speaker concludes that volcanoes cause rain only over oceans.",
                                 "refs": [8]})
    notes["concepts"].append({"term": "Collection", "explanation": "Water gathers in oceans, lakes and rivers.",
                              "defined_in_video": False, "refs": [14]})
    llm = FakeLLM([notes])
    llm.verdicts = {
        "millions of years": ("partly", "Groundwater can stay underground for thousands of years",
                              "Groundwater can stay underground for thousands of years before returning to the surface."),
        "volcanoes": ("unsupported", "@lines", ""),
        # a flag without a real quote from the lines must be ignored
        "lakes and rivers": ("partly", "water is made of hydrogen and oxygen atoms", "Water is a chemical compound."),
    }
    out = AINotesWriter(llm, fact_check=True).write(t, "The water cycle")
    gw = next(k for k in out.key_points if k.heading == "Groundwater")
    assert gw.text.startswith("Groundwater can stay underground for thousands of years")
    assert {i for h in gw.refs for i in h.segment_indices} & {15}
    assert not any("volcanoes" in c.text for c in out.conclusions)
    assert next(c for c in out.concepts if c.term == "Collection").explanation == "Water gathers in oceans, lakes and rivers."
    assert any("fact-check pass" in x and "corrected 1" in x and "removed 1" in x for x in out.limitations)
    checks = [p for p, sch in llm.calls if sch is VERIFY_SCHEMA]
    assert checks, "the fact-check pass ran"
    # each statement is shown with its cited line and surrounding context lines
    assert "[15] " in checks[0] + "".join(checks) and "[14] " in "".join(checks)
    assert out.key_points[0].id == "k1" and out.executive_summary


def test_fact_check_failure_keeps_notes_and_says_so():
    t = lecture_transcript()

    class Failing(FakeLLM):
        def generate_json(self, system, prompt, schema, on_progress=None, should_cancel=None):
            if schema is VERIFY_SCHEMA:
                raise LLMError("The local AI model stopped responding.")
            return super().generate_json(system, prompt, schema, on_progress, should_cancel)

    out = AINotesWriter(Failing([good_notes()]), fact_check=True).write(t, None)
    assert out.generator == "ai" and out.key_points
    assert any("couldn't run" in x for x in out.limitations)


def test_fact_check_defaults_to_claude_only():
    assert AINotesWriter(FakeLLM([])).fact_check is False  # local / other models
    claude = FakeLLM([])
    claude.method = "ai:claude:claude-opus-5-5"
    assert AINotesWriter(claude).fact_check is True
