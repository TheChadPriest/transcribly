"""Integration tests through the HTTP API. Media handling (probe, ffmpeg conversion, chunking) is
real; the speech engine is the deterministic mock so these run in seconds."""

import time

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.models import Segment

from .conftest import upload, wait_for


def auth(token):
    return {"X-Job-Token": token}


def make_client(monkeypatch, tmp_path, **env):
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("TRANSCRIBLY_DATA_DIR", str(tmp_path / "data"))
    for k in ("max_upload_mb", "max_duration_seconds", "max_queued_jobs", "run_worker_in_api"):
        monkeypatch.delenv(f"TRANSCRIBLY_{k.upper()}", raising=False)
    monkeypatch.setenv("TRANSCRIBLY_JOBS_PER_IP_PER_HOUR", "0")
    for k, v in env.items():
        monkeypatch.setenv(f"TRANSCRIBLY_{k.upper()}", str(v))
    get_settings.cache_clear()
    return TestClient(app)


def test_health_and_config(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["ffmpeg"] is True
    c = client.get("/api/config").json()
    assert "YouTube" in c["supported_sources"] and c["max_upload_mb"] > 0
    assert client.get("/api/openapi.json").status_code == 200


def test_upload_job_end_to_end(client, settings):
    code, body = upload(client, "clean.wav", "Photosynthesis basics.wav")
    assert code == 201, body
    job = wait_for(client, body["job_id"], body["token"])
    assert job["status"] == "completed", job
    stages = [s["stage"] for s in job["stages"]]
    assert stages == ["validating", "preparing", "transcribing", "summarizing", "citations", "finalizing"]
    assert all(s["finished_at"] for s in job["stages"])
    r = client.get(f"/api/jobs/{body['job_id']}/results", headers=auth(body["token"])).json()
    t = r["transcript"]
    assert t["source"] == "speech_recognition" and t["segments"]
    starts = [s["start"] for s in t["segments"]]
    assert starts == sorted(starts)
    assert t["segments"][-1]["end"] <= t["duration"] + 0.01
    flat = [i for p in t["paragraphs"] for i in p["segment_indices"]]
    assert flat == [s["index"] for s in t["segments"]]
    assert r["metadata"]["title"] == "Photosynthesis basics"
    assert r["metadata"]["source_type"] == "uploaded_file"
    assert len(r["citations"]["citations"]) == 5
    # temporary media is removed after success
    assert not (settings.work_dir / body["job_id"]).exists()
    tr = client.get(f"/api/jobs/{body['job_id']}/transcript", headers=auth(body["token"]))
    assert tr.status_code == 200


def test_video_container_upload(client):
    code, body = upload(client, "with_video.mp4")
    assert code == 201
    assert wait_for(client, body["job_id"], body["token"])["status"] == "completed"


@pytest.mark.parametrize("name,code", [("corrupt.mp4", "invalid_media"), ("no_audio.mp4", "no_audio")])
def test_bad_media_rejected(client, settings, name, code):
    status, body = upload(client, name)
    assert status == 400
    assert body["error"]["code"] == code
    assert "\\" not in body["error"]["message"] and "/" not in body["error"]["message"]
    assert not any(settings.work_dir.iterdir())  # nothing left behind


def test_fake_extension_is_validated_by_content(client):
    status, body = upload(client, "corrupt.mp4", "lecture.mp3")
    assert status == 400 and body["error"]["code"] == "invalid_media"


def test_missing_input(client):
    r = client.post("/api/jobs", data={"url": "  "})
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_input"


@pytest.mark.parametrize("url,code", [
    ("not a url", "invalid_url"),
    ("ftp://example.com/a.mp3", "invalid_url"),
    ("https://example.com/blog/post", "unsupported_source"),
    ("http://127.0.0.1:8000/secret.mp4", "blocked_url"),
    ("http://169.254.169.254/latest/meta-data.mp4", "blocked_url"),
])
def test_url_validation(client, url, code):
    r = client.post("/api/jobs", data={"url": url})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == code


def test_access_control(client):
    _, body = upload(client, "clean.wav")
    jid = body["job_id"]
    assert client.get(f"/api/jobs/{jid}").status_code == 403
    assert client.get(f"/api/jobs/{jid}", headers=auth("0" * 64)).status_code == 403
    assert client.get(f"/api/jobs/{jid}/results", headers=auth("0" * 64)).status_code == 403
    assert client.delete(f"/api/jobs/{jid}", headers=auth("bad")).status_code == 403
    assert client.get("/api/jobs/" + "A" * 24, headers=auth(body["token"])).status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc", headers=auth(body["token"])).status_code == 404


def test_results_not_ready_and_idempotency(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path, run_worker_in_api="false") as c:
        with open(__import__("tests.conftest", fromlist=["MEDIA"]).MEDIA / "clean.wav", "rb") as f:
            data = f.read()
        r1 = c.post("/api/jobs", files={"file": ("a.wav", data)}, headers={"Idempotency-Key": "key-123456789"})
        r2 = c.post("/api/jobs", files={"file": ("a.wav", data)}, headers={"Idempotency-Key": "key-123456789"})
        assert r1.status_code == r2.status_code == 201
        assert r1.json()["job_id"] == r2.json()["job_id"]
        jid, tok = r1.json()["job_id"], r1.json()["token"]
        assert c.get(f"/api/jobs/{jid}/results", headers=auth(tok)).status_code == 409
        # cancel while queued, then retry
        st = c.post(f"/api/jobs/{jid}/cancel", headers=auth(tok)).json()
        assert st["status"] == "cancelled"
        r = c.post(f"/api/jobs/{jid}/retry", headers=auth(tok))
        assert r.status_code == 409  # the uploaded media was discarded on cancel
        assert "upload it again" in r.json()["error"]["message"]


def test_cancel_running_job(monkeypatch, tmp_path):
    from app.transcription import engine

    def slow(self, audio, offset, language, should_cancel, on_progress):
        for _ in range(200):
            if should_cancel():
                raise engine.Cancelled()
            time.sleep(0.05)
        return engine.ChunkResult([], "en", 1.0)

    monkeypatch.setattr(engine.MockEngine, "transcribe", slow)
    with make_client(monkeypatch, tmp_path) as c:
        _, body = upload(c, "clean.wav")
        wait_for(c, body["job_id"], body["token"], until=("running",))
        deadline = time.time() + 10
        while time.time() < deadline:
            st = c.get(f"/api/jobs/{body['job_id']}", headers=auth(body["token"])).json()
            if st["stage"] == "transcribing":
                break
            time.sleep(0.1)
        c.post(f"/api/jobs/{body['job_id']}/cancel", headers=auth(body["token"]))
        final = wait_for(c, body["job_id"], body["token"], timeout=15)
        assert final["status"] == "cancelled"


def test_exports_and_metadata(client):
    _, body = upload(client, "clean.wav")
    jid, tok = body["job_id"], body["token"]
    wait_for(client, jid, tok)
    meta = client.get(f"/api/jobs/{jid}/results", headers=auth(tok)).json()["metadata"]
    meta.update(creator="Dr. Ada Lovelace", creator_is_person=True, published="2023-05-02")
    cs = client.patch(f"/api/jobs/{jid}/metadata", json=meta, headers=auth(tok)).json()
    apa = next(c for c in cs["citations"] if c["style"] == "apa")
    assert apa["reference_text"].startswith("Lovelace, D. A. (2023, May 2).") or apa["reference_text"].startswith("Lovelace, A. (2023, May 2).")
    expected = {"txt": "text/plain", "md": "text/markdown", "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    for fmt, mime in expected.items():
        r = client.get(f"/api/jobs/{jid}/export/{fmt}?style=apa", headers=auth(tok))
        assert r.status_code == 200, (fmt, r.text)
        assert r.headers["content-type"].startswith(mime)
        assert "attachment" in r.headers["content-disposition"]
        assert len(r.content) > 500
    txt = client.get(f"/api/jobs/{jid}/export/txt", headers=auth(tok)).text
    assert "Lovelace" in txt and "FULL TRANSCRIPT" in txt
    assert client.get(f"/api/jobs/{jid}/export/exe", headers=auth(tok)).status_code == 404


def test_delete(client):
    _, body = upload(client, "clean.wav")
    wait_for(client, body["job_id"], body["token"])
    assert client.delete(f"/api/jobs/{body['job_id']}", headers=auth(body["token"])).status_code == 204
    assert client.get(f"/api/jobs/{body['job_id']}", headers=auth(body["token"])).status_code == 404


def _fake_info(url="https://www.youtube.com/watch?v=abcdefghijk", creator="Science Channel", published="2021-04-05"):
    from app.sources.fetch import SourceInfo

    return SourceInfo(title="How Cells Divide", creator=creator, channel=None, published=published, duration=30.0,
                      url=url, platform="YouTube", is_live=False, language="en", raw={})


def test_url_job_with_captions_and_cache(client, monkeypatch):
    from app.sources import fetch

    calls = {"info": 0}

    def info(src):
        calls["info"] += 1
        return _fake_info()

    segs = [Segment(index=i, start=i * 3.0, end=i * 3.0 + 2.8, text=t) for i, t in enumerate(
        ["Mitosis is the process of cell division.", "It produces two identical daughter cells.",
         "First the chromosomes condense during prophase.", "In conclusion, mitosis keeps genetic information stable."])]
    monkeypatch.setattr(fetch, "fetch_info", info)
    monkeypatch.setattr(fetch, "fetch_captions", lambda s, i, lang: fetch.CaptionResult(segs, "auto_captions", "en"))
    monkeypatch.setattr(fetch, "download_audio", lambda *a, **k: pytest.fail("audio must not be downloaded"))

    r = client.post("/api/jobs", data={"url": "https://youtu.be/abcdefghijk?si=x"}).json()
    job = wait_for(client, r["job_id"], r["token"])
    assert job["status"] == "completed"
    assert [s["stage"] for s in job["stages"]][:3] == ["validating", "metadata", "captions"]
    res = client.get(f"/api/jobs/{r['job_id']}/results", headers=auth(r["token"])).json()
    assert res["transcript"]["source"] == "auto_captions"
    assert res["metadata"]["creator"] == "Science Channel"
    assert res["metadata"]["published"] == "2021-04-05"
    assert any("automatically generated captions" in x for x in res["study"]["limitations"])

    # same normalized URL + settings -> served from the cache, as a separate job with its own token
    r2 = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcdefghijk"}).json()
    assert r2["status"] == "completed" and r2["job_id"] != r["job_id"]
    j2 = client.get(f"/api/jobs/{r2['job_id']}", headers=auth(r2["token"])).json()
    assert [s["stage"] for s in j2["stages"]] == ["cache"]
    assert calls["info"] == 1
    assert client.get(f"/api/jobs/{r['job_id']}", headers=auth(r2["token"])).status_code == 403


def test_url_job_missing_metadata_warns(client, monkeypatch):
    from app.sources import fetch

    monkeypatch.setattr(fetch, "fetch_info", lambda s: _fake_info(creator=None, published=None))
    segs = [Segment(index=0, start=0, end=3, text="A short clip about rivers and lakes and oceans.")]
    monkeypatch.setattr(fetch, "fetch_captions", lambda s, i, lang: fetch.CaptionResult(segs, "human_captions", "en"))
    r = client.post("/api/jobs", data={"url": "https://vimeo.com/123456"}).json()
    job = wait_for(client, r["job_id"], r["token"])
    assert job["status"] == "completed"
    assert any("citation fields" in w for w in job["warnings"])


def test_url_job_access_restricted(client, monkeypatch):
    from app.sources import fetch

    def denied(src):
        raise AppError("access_restricted", 403)

    monkeypatch.setattr(fetch, "fetch_info", denied)
    r = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=privateVid1"}).json()
    job = wait_for(client, r["job_id"], r["token"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "access_restricted"
    assert "private" in job["error"]["message"]


def test_url_job_falls_back_to_audio(client, monkeypatch, settings):
    import shutil

    from app.sources import fetch

    from .conftest import MEDIA

    monkeypatch.setattr(fetch, "fetch_info", lambda s: _fake_info())
    monkeypatch.setattr(fetch, "fetch_captions", lambda s, i, lang: None)

    def download(src, dest, should_cancel, on_bytes):
        p = dest / "source.m4a"
        shutil.copy(MEDIA / "lecture.m4a", p)
        on_bytes(p.stat().st_size, p.stat().st_size)
        return p

    monkeypatch.setattr(fetch, "download_audio", download)
    r = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcdefghijk", "prefer_captions": "true"}).json()
    job = wait_for(client, r["job_id"], r["token"])
    assert job["status"] == "completed", job
    assert [s["stage"] for s in job["stages"]][:6] == ["validating", "metadata", "captions", "downloading", "preparing", "transcribing"]


def test_summary_failure_keeps_transcript(client, monkeypatch):
    from app.jobs import pipeline

    class Broken:
        name = "broken"
        uses_ai = False

        def summarize(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "get_summarizer", lambda: Broken())
    _, body = upload(client, "clean.wav")
    job = wait_for(client, body["job_id"], body["token"])
    assert job["status"] == "completed"
    res = client.get(f"/api/jobs/{body['job_id']}/results", headers=auth(body["token"])).json()
    assert res["study"] is None
    assert "still copy the transcript" in res["summary_error"]
    assert res["transcript"]["segments"]
    assert client.get(f"/api/jobs/{body['job_id']}/export/pdf", headers=auth(body["token"])).status_code == 200


def test_limits(monkeypatch, tmp_path):
    with make_client(monkeypatch, tmp_path, max_upload_mb=1) as c:
        status, body = upload(c, "silence_gaps.wav")  # 1.8 MB
        assert status == 413 and body["error"]["code"] == "too_large"
    with make_client(monkeypatch, tmp_path / "b", max_duration_seconds=5) as c:
        status, body = upload(c, "clean.wav")  # 16 s
        assert status == 413 and body["error"]["code"] == "too_long"
    with make_client(monkeypatch, tmp_path / "c", jobs_per_ip_per_hour=1, run_worker_in_api="false") as c:
        assert upload(c, "clean.wav")[0] == 201
        status, body = upload(c, "clean.wav")
        assert status == 429 and body["error"]["code"] == "too_many_requests"
    with make_client(monkeypatch, tmp_path / "d", max_queued_jobs=1, jobs_per_ip_per_hour=0, run_worker_in_api="false") as c:
        assert upload(c, "clean.wav")[0] == 201
        status, body = upload(c, "clean.wav")
        assert status == 503 and body["error"]["code"] == "server_busy"


def test_invalid_model_option(client):
    status, body = upload(client, "clean.wav", model="gigantic")
    assert status == 400 and body["error"]["code"] == "invalid_option"


def test_multiple_jobs_queue_and_all_complete(client):
    jobs = [upload(client, name)[1] for name in ("clean.wav", "noisy.wav", "multi_segment.mp3")]
    states = [client.get(f"/api/jobs/{j['job_id']}", headers=auth(j["token"])).json()["status"] for j in jobs]
    assert states.count("running") <= 1  # worker_concurrency=1
    finals = [wait_for(client, j["job_id"], j["token"]) for j in jobs]
    assert [f["status"] for f in finals] == ["completed"] * 3
    done = [f["stages"][-1]["finished_at"] for f in finals]
    assert done == sorted(done)  # processed in submission order


def test_retry_starts_a_clean_run(client, monkeypatch):
    from app.transcription import engine

    calls = {"n": 0}
    original = engine.MockEngine.transcribe

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary failure")
        return original(self, *a, **k)

    monkeypatch.setattr(engine.MockEngine, "transcribe", flaky)
    _, body = upload(client, "clean.wav")
    first = wait_for(client, body["job_id"], body["token"])
    assert first["status"] == "failed" and first["error"]["code"] == "transcription_failed"
    client.post(f"/api/jobs/{body['job_id']}/retry", headers=auth(body["token"]))
    done = wait_for(client, body["job_id"], body["token"])
    assert done["status"] == "completed"
    stages = [s["stage"] for s in done["stages"]]
    assert len(stages) == len(set(stages))  # no leftovers from the failed attempt
