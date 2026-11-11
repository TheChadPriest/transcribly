from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MEDIA = ROOT / "tests" / "media"

os.environ.setdefault("TRANSCRIBLY_TEST_MODE", "true")
os.environ.setdefault("TRANSCRIBLY_TRANSCRIPTION_ENGINE", "mock")
os.environ.setdefault("TRANSCRIBLY_JOBS_PER_IP_PER_HOUR", "0")
os.environ.setdefault("TRANSCRIBLY_AI_PROVIDER", "none")  # tests opt in to (fake) AI models explicitly


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRANSCRIBLY_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def wait_for(client, job_id: str, token: str, timeout: float = 60, until=("completed", "failed", "cancelled")) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}", headers={"X-Job-Token": token})
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in until:
            return body
        time.sleep(0.2)
    raise AssertionError(f"job did not finish: {body}")


def upload(client, name: str, filename: str | None = None, **data) -> tuple[int, dict]:
    with open(MEDIA / name, "rb") as f:
        r = client.post("/api/jobs", files={"file": (filename or name, f)}, data=data)
    return r.status_code, r.json()


@pytest.fixture
def media_copy(tmp_path):
    def _copy(name: str) -> Path:
        dst = tmp_path / name
        shutil.copy(MEDIA / name, dst)
        return dst

    return _copy
