"""Retrieve metadata, captions, and audio for a resolved source.

yt-dlp is restricted to the extractor of the matched platform, so it cannot be pointed at
arbitrary hosts. Direct media links are downloaded by our own client, which re-validates
every redirect target against private-network rules.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import get_settings
from ..errors import AppError, Cancelled
from ..models import Segment
from ..security import assert_public_url, url_fingerprint
from .captions import parse_captions
from .resolver import ResolvedSource

log = logging.getLogger(__name__)


@dataclass
class SourceInfo:
    title: str | None
    creator: str | None
    channel: str | None
    published: str | None  # YYYY-MM-DD
    duration: float | None
    url: str
    platform: str | None
    is_live: bool
    language: str | None
    raw: dict[str, Any]


@dataclass
class CaptionResult:
    segments: list[Segment]
    kind: str  # human_captions | auto_captions
    language: str


def _map_ytdlp_error(e: Exception) -> AppError:
    msg = str(e).lower()
    if "429" in msg or "too many requests" in msg or "rate-limit" in msg or "rate limit" in msg:
        return AppError("rate_limited", 429)
    if "live event" in msg or "is live" in msg or "premieres in" in msg:
        return AppError("live_stream")
    if "unsupported url" in msg or "no suitable extractor" in msg:
        return AppError("unsupported_source")
    if any(k in msg for k in ("private", "sign in", "login", "members-only", "unavailable", "removed",
                              "copyright", "not available", "age", "403", "404", "geo", "drm")):
        return AppError("access_restricted", 403)
    return AppError("download_failed", 502)


def _ydl_opts(source: ResolvedSource, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "allowed_extractors": [source.extractor] if source.extractor else ["default"],
        "noprogress": True,
        "cachedir": False,
        "logger": _QuietLogger(),
    }
    opts.update(extra or {})
    return opts


class _QuietLogger:
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)
    def error(self, msg: str) -> None:
        log.info("yt-dlp error: %s", msg[:300])


def _date(s: str | None) -> str | None:
    if s and len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def fetch_info(source: ResolvedSource) -> SourceInfo:
    if source.kind == "direct":
        name = Path(urlsplit(source.url).path).stem or None
        return SourceInfo(name, None, None, None, None, source.url, None, False, None, {})
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ydl_opts(source, {"skip_download": True})) as ydl:
            info = ydl.extract_info(source.url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise _map_ytdlp_error(e) from e
    if not info:
        raise AppError("access_restricted", 403)
    if info.get("_type") == "playlist":
        raise AppError("unsupported_source", message="Playlists aren't supported. Paste the link of a single video.")
    channel = info.get("channel") or info.get("uploader")
    return SourceInfo(
        title=info.get("title"),
        creator=channel,
        channel=info.get("uploader") if info.get("uploader") and info.get("uploader") != channel else None,
        published=_date(info.get("release_date")) or _date(info.get("upload_date")),
        duration=float(info["duration"]) if info.get("duration") else None,
        url=info.get("webpage_url") or source.url,
        platform=source.platform,
        is_live=bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming"),
        language=info.get("language"),
        raw={k: info.get(k) for k in ("subtitles", "automatic_captions", "id", "extractor")},
    )


def _pick(tracks: dict[str, list[dict]], keys: list[str]) -> tuple[str, dict] | None:
    for key in keys:
        for lang, formats in tracks.items():
            if lang == key:
                by_ext = {f.get("ext"): f for f in formats if f.get("url")}
                for ext in ("json3", "vtt", "srt"):
                    if ext in by_ext:
                        return lang, by_ext[ext]
    return None


def fetch_captions(source: ResolvedSource, info: SourceInfo, language: str | None) -> CaptionResult | None:
    """Return platform captions in the video's own language, or None.

    Machine-*translated* auto captions are never used; only captions in the original language."""
    import yt_dlp

    subs: dict = info.raw.get("subtitles") or {}
    auto: dict = info.raw.get("automatic_captions") or {}
    lang = (language or info.language or "").split("-")[0] or None
    human_keys = [k for k in subs if lang and (k == lang or k.startswith(lang + "-"))] if lang else list(subs)[:1]
    picked = _pick({k: v for k, v in subs.items() if k != "live_chat"}, human_keys)
    kind = "human_captions"
    if not picked:
        orig = [k for k in auto if k.endswith("-orig")]
        if lang:
            auto_keys = [f"{lang}-orig"] + ([lang] if info.language and info.language.startswith(lang) else [])
        else:
            auto_keys = orig[:1]
        picked = _pick(auto, auto_keys)
        kind = "auto_captions"
    if not picked:
        return None
    track_lang, fmt = picked
    try:
        assert_public_url(fmt["url"])
        with yt_dlp.YoutubeDL(_ydl_opts(source)) as ydl:
            content = ydl.urlopen(fmt["url"]).read().decode("utf-8", errors="replace")
        segments = parse_captions(content, fmt["ext"])
    except AppError:
        raise
    except Exception as e:
        log.info("caption retrieval failed for %s: %s", url_fingerprint(source.url), type(e).__name__)
        return None
    if len(segments) < 2:
        return None
    return CaptionResult(segments, kind, track_lang.replace("-orig", ""))


def download_audio(source: ResolvedSource, dest_dir: Path, should_cancel: Callable[[], bool],
                   on_bytes: Callable[[int, int | None], None]) -> Path:
    if source.kind == "direct":
        return _download_direct(source.url, dest_dir, should_cancel, on_bytes)
    import yt_dlp

    s = get_settings()

    def hook(d: dict) -> None:
        if should_cancel():
            raise Cancelled()
        if d.get("status") == "downloading":
            on_bytes(int(d.get("downloaded_bytes") or 0), d.get("total_bytes") or d.get("total_bytes_estimate"))

    opts = _ydl_opts(source, {
        # audio only when the platform offers it; otherwise the smallest stream with audio
        "format": "bestaudio/best[height<=360]/best",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "max_filesize": s.max_upload_bytes * 4 if s.max_upload_mb else None,
        "overwrites": True,
        "continuedl": True,
    })
    last: Exception | None = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([source.url])
            files = [p for p in dest_dir.glob("source.*") if not p.name.endswith((".part", ".ytdl"))]
            if not files:
                raise AppError("download_failed", 502)
            return files[0]
        except Cancelled:
            raise
        except yt_dlp.utils.DownloadError as e:
            if isinstance(getattr(e, "exc_info", [None, None])[1], Cancelled):
                raise Cancelled() from e
            mapped = _map_ytdlp_error(e)
            if mapped.code not in ("download_failed", "rate_limited") or attempt == 2:
                raise mapped from e
            last = e
            time.sleep(5 * (attempt + 1))
    raise AppError("download_failed", 502) from last


def _download_direct(url: str, dest_dir: Path, should_cancel: Callable[[], bool],
                     on_bytes: Callable[[int, int | None], None]) -> Path:
    s = get_settings()
    limit = s.max_upload_bytes
    dest = dest_dir / "source.bin"
    current = url
    with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(30.0, read=60.0),
                      headers={"User-Agent": "Transcribly/1.0"}) as client:
        for _ in range(5):  # follow redirects manually so each hop is validated
            assert_public_url(current, s.allow_private_network_urls)
            with client.stream("GET", current) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise AppError("download_failed", 502)
                    nxt = urljoin(current, loc)
                    if urlsplit(nxt).scheme not in ("http", "https"):
                        raise AppError("blocked_url")
                    current = nxt
                    continue
                if r.status_code in (401, 403, 404, 410, 451):
                    raise AppError("access_restricted", 403)
                if r.status_code == 429:
                    raise AppError("rate_limited", 429)
                if r.status_code >= 400:
                    raise AppError("download_failed", 502)
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype.startswith(("text/", "application/json", "application/xml")):
                    raise AppError("unsupported_source", message="That link points to a web page, not a media file.")
                total = int(r.headers["content-length"]) if r.headers.get("content-length", "").isdigit() else None
                if limit and total and total > limit:
                    raise AppError("too_large", 413)
                got = 0
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        if should_cancel():
                            raise Cancelled()
                        got += len(chunk)
                        if limit and got > limit:
                            raise AppError("too_large", 413)
                        f.write(chunk)
                        on_bytes(got, total)
                return dest
    raise AppError("download_failed", 502)
