"""Run the real pipeline on one file or URL and print the result + timings.

Usage: python scripts/run_pipeline.py <path-or-url> [--model small] [--chunk 600] [--json out.json]
Uses TRANSCRIBLY_DATA_DIR (default ./data). Useful for benchmarking on your own hardware.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--model")
    ap.add_argument("--chunk", type=int)
    ap.add_argument("--language")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.model:
        os.environ["TRANSCRIBLY_WHISPER_MODEL"] = a.model
    if a.chunk:
        os.environ["TRANSCRIBLY_CHUNK_SECONDS"] = str(a.chunk)

    from app.config import get_settings
    from app.jobs.pipeline import run_job
    from app.jobs.service import JobService
    from app.logging_config import setup_logging
    from app.models import JobOptions
    from app.storage.db import Store
    from app.transcription.segments import format_timestamp

    setup_logging("WARNING" if a.quiet else "INFO")
    s = get_settings()
    s.work_dir.mkdir(parents=True, exist_ok=True)
    store = Store(s.db_path)
    svc = JobService(s, store)
    opts = JobOptions(language=a.language, prefer_captions=not a.no_captions)
    if a.source.startswith(("http://", "https://")):
        created = svc.create_url_job(a.source, opts, "cli", None)
    else:
        with open(a.source, "rb") as f:
            created = svc.create_upload_job(f, Path(a.source).name, opts, "cli", None, None)
    job = store.claim_next_job()
    t0 = time.perf_counter()
    if job:
        run_job(store, job)
    wall = time.perf_counter() - t0
    final = store.get_job(created.job_id)
    assert final is not None
    peak = None
    try:
        import psutil  # optional

        peak = psutil.Process().memory_info().peak_wset / 1e6 if hasattr(psutil.Process().memory_info(), "peak_wset") else psutil.Process().memory_info().rss / 1e6
    except Exception:
        pass
    print(f"status={final['status']} error={final['error_code']} wall={wall:.1f}s duration={final['duration']} "
          f"peak_mem_mb={peak and round(peak)} stages={[x['stage'] for x in final['stages']]}")
    if final["status"] != "completed":
        print(final["error_message"])
        return
    r = svc.results(final)
    if a.json:
        Path(a.json).write_text(r.model_dump_json(indent=2), encoding="utf-8")
    if a.quiet:
        return
    print(f"language={r.transcript.language} source={r.transcript.source} segments={len(r.transcript.segments)}")
    for seg in r.transcript.segments:
        flag = " (low confidence)" if seg.low_confidence else ""
        print(f"[{format_timestamp(seg.start)}] {seg.text}{flag}")
    if r.study:
        st = r.study
        print("\nSUMMARY:", st.executive_summary)
        print("\nKEY POINTS:")
        for k in st.key_points:
            print(f"  - [{format_timestamp(k.timestamp)}] {k.heading}: {k.text}")
        print("\nTOPICS:")
        for t in st.topics:
            print(f"  - [{format_timestamp(t.start)}-{format_timestamp(t.end)}] {t.title} | {', '.join(t.subtopics)}")
        print("\nCONCEPTS:")
        for c in st.concepts:
            print(f"  - {c.term} ({c.explanation_kind}): {c.explanation}")
        print("\nCONCLUSIONS:")
        for c in st.conclusions:
            print(f"  - {c.text}")
        print("\nQUESTIONS:")
        for q in st.study_questions:
            print(f"  - {q}")
    print("\nCITATION (APA):", r.citations.citations[0].reference_text)
    print(json.dumps({"warnings": r.citations.warnings[:2]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
