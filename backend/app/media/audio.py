"""Media inspection (ffprobe / PyAV) and audio preparation (ffmpeg).

User-supplied names never reach a command line: every subprocess receives an argument list
built from paths we generated ourselves.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..config import get_settings
from ..errors import AppError, Cancelled

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


@dataclass
class MediaInfo:
    duration: float | None
    has_audio: bool
    has_video: bool
    format_name: str | None
    audio_codec: str | None


@lru_cache
def ffmpeg_exe() -> str | None:
    s = get_settings()
    if s.ffmpeg_path:
        return s.ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # bundled static build (pip install imageio-ffmpeg) for machines without ffmpeg
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@lru_cache
def ffprobe_exe() -> str | None:
    s = get_settings()
    return s.ffprobe_path or shutil.which("ffprobe")


def _probe_ffprobe(exe: str, path: Path) -> MediaInfo:
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise AppError("invalid_media") from e
    if out.returncode != 0:
        raise AppError("invalid_media")
    data = json.loads(out.stdout or b"{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1]
    dur = fmt.get("duration") or (audio[0].get("duration") if audio else None)
    return MediaInfo(
        duration=float(dur) if dur not in (None, "N/A") else None,
        has_audio=bool(audio),
        has_video=bool(video),
        format_name=fmt.get("format_name"),
        audio_codec=audio[0].get("codec_name") if audio else None,
    )


def _probe_pyav(path: Path) -> MediaInfo:
    import av

    try:
        with av.open(str(path)) as c:
            audio = list(c.streams.audio)
            video = [v for v in c.streams.video if not (v.disposition & av.stream.Disposition.attached_pic)]
            dur = c.duration / 1_000_000 if c.duration else None
            if dur is None and audio and audio[0].duration and audio[0].time_base:
                dur = float(audio[0].duration * audio[0].time_base)
            return MediaInfo(
                duration=dur,
                has_audio=bool(audio),
                has_video=bool(video),
                format_name=c.format.name,
                audio_codec=audio[0].codec_context.name if audio else None,
            )
    except Exception as e:  # any demuxer error means unreadable media
        raise AppError("invalid_media") from e


def probe(path: Path) -> MediaInfo:
    """Inspect real content (not the extension). Raises AppError for unreadable / audio-free media."""
    if not path.exists() or path.stat().st_size == 0:
        raise AppError("invalid_media")
    exe = ffprobe_exe()
    info = _probe_ffprobe(exe, path) if exe else _probe_pyav(path)
    if not info.format_name:
        raise AppError("invalid_media")
    if not info.has_audio:
        raise AppError("no_audio")
    return info


def to_wav(src: Path, dst: Path, duration_hint: float | None, should_cancel: Callable[[], bool] | None = None) -> None:
    """Decode any input to 16 kHz mono PCM WAV, streaming (constant memory)."""
    exe = ffmpeg_exe()
    if not exe:
        raise AppError("transcription_failed", 500, "FFmpeg is not available on this server.")
    s = get_settings()
    # Decoding runs far faster than real time; the timeout only guards against hangs.
    timeout = max(s.subprocess_timeout_floor_seconds, int((duration_hint or 3600) * 0.5))
    tmp = dst.with_suffix(".part.wav")
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vn", "-sn", "-dn", "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-c:a", "pcm_s16le", "-f", "wav", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        waited = 0.0
        while True:
            try:
                proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                waited += 1.0
                if waited > timeout or (should_cancel and should_cancel()):
                    proc.kill()
                    proc.wait()
                    tmp.unlink(missing_ok=True)
                    if waited > timeout:
                        raise AppError("transcription_failed", 500, "Preparing the audio took too long.") from None
                    raise Cancelled() from None
        err = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
    finally:
        if proc.stderr:
            proc.stderr.close()
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size <= 44:
        log.warning("ffmpeg failed (code %s): %s", proc.returncode, err[-500:])
        tmp.unlink(missing_ok=True)
        raise AppError("no_audio" if "does not contain any stream" in err or "matches no streams" in err else "invalid_media")
    tmp.replace(dst)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def read_wav(path: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        w.setpos(min(int(start * sr), w.getnframes()))
        frames = w.readframes(max(0, int((end - start) * sr)))
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def plan_chunks(path: Path, chunk_seconds: int, search_window: float = 20.0) -> list[tuple[float, float]]:
    """Split audio into ~chunk_seconds pieces, cutting at the quietest point near each boundary
    so words are not split between chunks."""
    total = wav_duration(path)
    if total <= chunk_seconds * 1.25:
        return [(0.0, total)]
    bounds = [0.0]
    pos = 0.0
    while total - pos > chunk_seconds * 1.25:
        target = pos + chunk_seconds
        lo, hi = max(pos + 1, target - search_window), min(total, target + search_window)
        cut = quietest_point(read_wav(path, lo, hi), lo)
        bounds.append(cut)
        pos = cut
    bounds.append(total)
    return list(zip(bounds[:-1], bounds[1:]))


def quietest_point(samples: np.ndarray, offset: float, frame: float = 0.05) -> float:
    n = int(SAMPLE_RATE * frame)
    if len(samples) < n * 3:
        return offset + len(samples) / SAMPLE_RATE / 2
    usable = len(samples) // n * n
    energy = np.sqrt((samples[:usable].reshape(-1, n) ** 2).mean(axis=1))
    # smooth over ~0.5 s so we pick a pause, not a single quiet frame mid-word
    k = 10
    smooth = np.convolve(energy, np.ones(k) / k, mode="same")
    return offset + (int(np.argmin(smooth)) + 0.5) * frame


def iter_chunks(path: Path, plan: list[tuple[float, float]]) -> Iterator[tuple[int, float, float, np.ndarray]]:
    for i, (a, b) in enumerate(plan):
        yield i, a, b, read_wav(path, a, b)


def speech_ratio(path: Path, threshold_db: float = -45.0, frame: float = 0.05, max_seconds: float = 4 * 3600) -> float:
    """Rough fraction of non-silent audio, computed in blocks to keep memory flat."""
    total = min(wav_duration(path), max_seconds)
    loud = frames = 0
    pos = 0.0
    n = int(SAMPLE_RATE * frame)
    thr = 10 ** (threshold_db / 20)
    while pos < total:
        block = read_wav(path, pos, min(total, pos + 300))
        usable = len(block) // n * n
        if usable:
            rms = np.sqrt((block[:usable].reshape(-1, n) ** 2).mean(axis=1))
            loud += int((rms > thr).sum())
            frames += len(rms)
        pos += 300
    return loud / frames if frames else 0.0
