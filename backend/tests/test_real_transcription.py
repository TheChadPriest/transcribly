"""Real speech recognition with faster-whisper on generated speech with known content.

Skipped unless TRANSCRIBLY_REAL_TESTS=1 (they download a model and take minutes on CPU).
Model: TRANSCRIBLY_REAL_MODEL (default "base").
"""

from __future__ import annotations

import json
import os
import re
import time

import pytest

from .conftest import MEDIA

pytestmark = pytest.mark.skipif(os.environ.get("TRANSCRIBLY_REAL_TESTS") != "1",
                                reason="set TRANSCRIBLY_REAL_TESTS=1 to run real transcription tests")

EXPECTED = json.loads((MEDIA / "expected.json").read_text())


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("%", " percent"))


def word_error_rate(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


@pytest.fixture
def real(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRANSCRIBLY_TRANSCRIPTION_ENGINE", "faster-whisper")
    monkeypatch.setenv("TRANSCRIBLY_WHISPER_MODEL", os.environ.get("TRANSCRIBLY_REAL_MODEL", "base"))
    monkeypatch.setenv("TRANSCRIBLY_DATA_DIR", str(tmp_path / "data"))
    # set TRANSCRIBLY_MODEL_DIR to reuse downloaded models between runs
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


def run(real, name: str, chunk: int | None = None, monkeypatch=None):
    from app.jobs.pipeline import run_job
    from app.jobs.service import JobService
    from app.models import JobOptions
    from app.storage.db import Store

    if chunk:
        real.chunk_seconds = chunk
    real.work_dir.mkdir(parents=True, exist_ok=True)
    store = Store(real.db_path)
    svc = JobService(real, store)
    with open(MEDIA / name, "rb") as f:
        created = svc.create_upload_job(f, name, JobOptions(), "test", None, None)
    t0 = time.perf_counter()
    run_job(store, store.claim_next_job())
    wall = time.perf_counter() - t0
    job = store.get_job(created.job_id)
    assert job["status"] == "completed", (job["error_code"], job["error_message"])
    res = svc.results(job)
    print(f"\n{name}: {wall:.1f}s wall for {res.transcript.duration:.1f}s audio")
    return res


def check_timestamps(t):
    prev_start = -1.0
    for s in t.segments:
        assert 0 <= s.start <= s.end <= t.duration + 0.05
        assert s.start >= prev_start - 0.01
        prev_start = s.start


def test_clean_speech(real):
    res = run(real, "clean.wav")
    hyp = res.transcript.text
    wer = word_error_rate(EXPECTED["clean"], hyp)
    print("WER", round(wer, 3), "|", hyp)
    assert wer < 0.15
    assert "photosynthesis" in hyp.lower() and "chloroplast" in hyp.lower()
    check_timestamps(res.transcript)


def test_long_lecture_chunked_without_duplication(real):
    res = run(real, "lecture.m4a", chunk=40)  # forces 4-5 chunks for a 2 min recording
    ref = " ".join(EXPECTED["lecture"])
    hyp = res.transcript.text
    wer = word_error_rate(ref, hyp)
    print("WER", round(wer, 3))
    assert wer < 0.15
    check_timestamps(res.transcript)
    texts = [s.text.lower() for s in res.transcript.segments]
    assert len(texts) == len(set(texts)), "a segment was duplicated across chunk boundaries"
    assert "welcome" in texts[0]  # beginning not dropped
    assert "keeps changing" in texts[-1]  # end not dropped
    assert res.study and res.study.key_points


def test_multiple_segments_mp3(real):
    res = run(real, "multi_segment.mp3")
    hyp = res.transcript.text.lower()
    # the file holds lecture paragraphs 1-3 (generator interleaves silence between parts)
    for phrase in ("water cycle", "evaporation", "transpiration", "condensation"):
        assert phrase in hyp


def test_silence_gaps_timestamps(real):
    res = run(real, "silence_gaps.wav")
    segs = res.transcript.segments
    # speech starts after 8 s of silence; second copy after a further 12 s gap
    assert 7.0 <= segs[0].start <= 9.5, segs[0]
    assert res.transcript.text.lower().count("photosynthesis") == 2
    big_gap = max(b.start - a.end for a, b in zip(segs, segs[1:], strict=False))
    assert big_gap >= 10
    # nothing invented in the trailing silence
    assert segs[-1].end <= res.transcript.duration - 4


def test_noisy_speech(real):
    res = run(real, "noisy.wav")
    hyp = res.transcript.text.lower()
    wer = word_error_rate(EXPECTED["noisy"], hyp)
    print("WER", round(wer, 3), "|", hyp)
    assert "mitochondria" in hyp or "powerhouse" in hyp
    assert wer < 0.4


def test_video_container(real):
    res = run(real, "with_video.mp4")
    assert "photosynthesis" in res.transcript.text.lower()
