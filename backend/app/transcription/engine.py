"""Speech-recognition engines behind a small interface so they can be swapped."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from ..config import get_settings
from ..errors import AppError, Cancelled
from ..models import Segment
from .segments import clean_text

log = logging.getLogger(__name__)

# Whisper's own heuristics: segments that are very likely silence/hallucination.
NO_SPEECH_DROP = 0.6
LOGPROB_DROP = -1.0
LOW_CONFIDENCE_LOGPROB = -0.9
COMPRESSION_FLAG = 2.4
LOW_CONFIDENCE_PROB = 0.55  # mean word probability below this marks a segment as uncertain


@dataclass
class ChunkResult:
    segments: list[Segment]
    language: str | None = None
    language_probability: float | None = None
    stats: dict = field(default_factory=dict)


class Engine(Protocol):
    name: str
    model_name: str

    def transcribe(self, audio: np.ndarray, offset: float, language: str | None,
                   should_cancel: Callable[[], bool], on_progress: Callable[[float], None]) -> ChunkResult: ...


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model_name: str):
        from faster_whisper import WhisperModel

        s = get_settings()
        device = s.whisper_device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = s.whisper_compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        t0 = time.perf_counter()
        self.model = WhisperModel(model_name, device=device, compute_type=compute,
                                  cpu_threads=s.cpu_threads, num_workers=max(1, s.worker_concurrency),
                                  download_root=str(s.models_path))
        self.model_name = model_name
        self.device = device
        self.compute_type = compute
        self.load_seconds = time.perf_counter() - t0
        log.info("loaded whisper model=%s device=%s compute=%s in %.1fs", model_name, device, compute, self.load_seconds)

    def transcribe(self, audio, offset, language, should_cancel, on_progress) -> ChunkResult:
        s = get_settings()
        segments_iter, info = self.model.transcribe(
            audio,
            language=language or None,
            beam_size=s.whisper_beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,  # avoids repetition loops on long audio
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            word_timestamps=True,  # accurate boundaries + per-word confidence
        )
        chunk_len = len(audio) / 16000
        words: list[tuple[float, float, str, float, bool]] = []
        dropped = 0
        for seg in segments_iter:  # generator: decoding happens lazily here
            if should_cancel():
                raise Cancelled()
            on_progress(min(seg.end, chunk_len))
            if not clean_text(seg.text):
                continue
            if seg.no_speech_prob > NO_SPEECH_DROP and seg.avg_logprob < LOGPROB_DROP:
                dropped += 1
                continue
            suspicious = seg.avg_logprob < LOW_CONFIDENCE_LOGPROB or seg.compression_ratio > COMPRESSION_FLAG
            if seg.words:
                words += [(w.start, w.end, w.word, w.probability, suspicious) for w in seg.words]
            else:
                words.append((seg.start, seg.end, " " + seg.text.strip(), math.exp(seg.avg_logprob), suspicious))
        out = sentence_segments(words, offset, chunk_len)
        return ChunkResult(out, info.language, float(info.language_probability),
                           {"dropped_as_silence": dropped, "speech_seconds": float(getattr(info, "duration_after_vad", 0) or 0)})


def sentence_segments(words: list[tuple[float, float, str, float, bool]], offset: float, chunk_len: float,
                      max_words: int = 40, pause: float = 1.2) -> list[Segment]:
    """Regroup word timings into sentence-aligned segments, so a highlighted passage or a
    timestamp link starts where the sentence starts. Times are clamped to the chunk."""
    out: list[Segment] = []
    cur: list[tuple[float, float, str, float, bool]] = []

    def flush() -> None:
        if not cur:
            return
        text = clean_text("".join(w[2] for w in cur))
        if text:
            probs = [w[3] for w in cur]
            conf = sum(probs) / len(probs)
            start = max(0.0, min(cur[0][0], chunk_len))
            end = max(start, min(cur[-1][1], chunk_len))
            out.append(Segment(index=0, start=round(offset + start, 3), end=round(offset + end, 3), text=text,
                               confidence=round(conf, 3),
                               low_confidence=any(w[4] for w in cur) or conf < LOW_CONFIDENCE_PROB))
        cur.clear()

    for i, w in enumerate(words):
        if cur and (w[0] - cur[-1][1] > pause or len(cur) >= max_words):
            flush()
        cur.append(w)
        nxt = words[i + 1][2] if i + 1 < len(words) else ""
        if w[2].rstrip().endswith((".", "?", "!", "。", "？", "！")) and (not nxt or nxt[:1] == " "):
            flush()
    flush()
    return out


class MockEngine:
    """Deterministic engine for automated tests only (enabled with TRANSCRIBLY_TEST_MODE)."""

    name = "mock"
    model_name = "mock"

    def transcribe(self, audio, offset, language, should_cancel, on_progress) -> ChunkResult:
        dur = len(audio) / 16000
        segs = []
        t = 0.0
        i = 0
        while t + 1 <= dur:
            if should_cancel():
                raise Cancelled()
            end = min(dur, t + 5)
            segs.append(Segment(index=0, start=offset + t, end=offset + end,
                                text=f"Mock sentence number {i} about photosynthesis and energy.", confidence=0.9))
            on_progress(end)
            t = end
            i += 1
        return ChunkResult(segs, language or "en", 1.0)


_engine_lock = threading.Lock()
_engine_cache: dict[str, Engine] = {}


def release_engine() -> None:
    """Free the loaded speech model (e.g. so a local AI model has room to run)."""
    import gc

    with _engine_lock:
        _engine_cache.clear()
    gc.collect()


def get_engine(model_name: str) -> Engine:
    """Load a model once and reuse it. Keeps only one model in memory at a time."""
    s = get_settings()
    if s.transcription_engine == "mock":
        if not s.test_mode:
            raise AppError("internal", 500)
        return MockEngine()
    if model_name not in s.allowed_models:
        raise AppError("invalid_option")
    with _engine_lock:
        if model_name not in _engine_cache:
            _engine_cache.clear()
            last: Exception | None = None
            for attempt in range(3):  # first use downloads the model; networks fail transiently
                try:
                    _engine_cache[model_name] = FasterWhisperEngine(model_name)
                    break
                except Exception as e:
                    last = e
                    log.warning("loading model %s failed (attempt %d): %s", model_name, attempt + 1, type(e).__name__)
                    time.sleep(3 * (attempt + 1))
            else:
                raise AppError("model_unavailable", 503) from last
        return _engine_cache[model_name]
