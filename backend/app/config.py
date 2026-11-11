"""Runtime configuration, read from environment variables (prefix TRANSCRIBLY_)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRANSCRIBLY_", env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Path("./data")
    secret_key: str | None = None  # used to derive per-job access tokens; generated if unset

    # Limits (0 = no limit applied by the application)
    max_upload_mb: int = 1024
    max_duration_seconds: int = 6 * 3600
    max_queued_jobs: int = 20
    jobs_per_ip_per_hour: int = 30

    # Workers
    worker_concurrency: int = 1
    run_worker_in_api: bool = True  # False when a separate `python -m app.worker` process is used
    max_attempts: int = 3

    # Retention
    result_retention_hours: int = 72
    keep_media: bool = False
    cache_results: bool = True

    # Transcription
    whisper_model: str = "small"
    whisper_allowed_models: str = "tiny,base,small,medium"
    whisper_device: str = "auto"  # auto | cpu | cuda
    whisper_compute_type: str = "auto"  # auto -> int8 on CPU, float16 on CUDA
    whisper_cpu_threads: int = 0  # 0 = derive from CPU count / concurrency
    whisper_beam_size: int = 5
    chunk_seconds: int = 600
    model_dir: Path | None = None  # where Whisper models are cached; default <data_dir>/models
    transcription_engine: str = "faster-whisper"  # "mock" is available for tests only

    # AI study notes. "auto" uses a local Ollama server when it is reachable and has the model;
    # "claude" sends transcripts to Anthropic's API (explicit opt-in only); "none" = extractive notes.
    ai_provider: str = "auto"  # auto | ollama | claude | none
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_num_ctx: int = 16384  # context window; larger fits longer videos in one pass but needs more RAM
    ollama_timeout_seconds: int = 1800
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"  # low | medium | high | xhigh | max
    # Second pass that checks each AI statement against its cited lines. "auto" = Claude only: in testing,
    # a 7B local model neither caught real errors nor left correct statements alone, and doubled the run time.
    ai_fact_check: str = "auto"  # auto | on | off

    # Sources
    enable_direct_media_urls: bool = True
    allow_private_network_urls: bool = False  # never enable on a public deployment
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    subprocess_timeout_floor_seconds: int = 120

    # HTTP
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    frontend_dist: Path | None = None
    trust_proxy_headers: bool = False

    log_level: str = "INFO"

    test_mode: bool = Field(default=False, description="Enables the mock engine; never use in production")

    @property
    def allowed_models(self) -> list[str]:
        return [m.strip() for m in self.whisper_allowed_models.split(",") if m.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / "transcribly.sqlite3"

    @property
    def models_path(self) -> Path:
        return self.model_dir or self.data_dir / "models"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def cpu_threads(self) -> int:
        if self.whisper_cpu_threads > 0:
            return self.whisper_cpu_threads
        return max(1, (os.cpu_count() or 2) // max(1, self.worker_concurrency))


@lru_cache
def get_settings() -> Settings:
    return Settings()
