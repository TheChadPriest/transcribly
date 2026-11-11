"""Identify and normalize source URLs. Add a new platform by adding a SourceSpec."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from ..config import get_settings
from ..errors import AppError
from ..security import normalize_url

MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".mp3", ".wav", ".m4a", ".aac",
    ".flac", ".ogg", ".oga", ".opus", ".wma", ".mpeg", ".mpg",
}


@dataclass(frozen=True)
class SourceSpec:
    kind: str  # identifier stored with the job
    label: str  # shown in the UI
    platform: str  # used in citations
    domains: tuple[str, ...]
    extractor: str | None = None  # yt-dlp extractor name pattern


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("youtube", "YouTube", "YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com"), r"youtube.*"),
    SourceSpec("vimeo", "Vimeo", "Vimeo", ("vimeo.com",), r"vimeo.*"),
    SourceSpec("dailymotion", "Dailymotion", "Dailymotion", ("dailymotion.com", "dai.ly"), r"dailymotion.*"),
    SourceSpec("archive_org", "Internet Archive", "Internet Archive", ("archive.org",), r"archive\.org.*"),
    SourceSpec("ted", "TED", "TED", ("ted.com",), r"ted.*"),
)


@dataclass(frozen=True)
class ResolvedSource:
    kind: str
    label: str
    platform: str | None
    url: str
    extractor: str | None


def _domain_match(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_youtube(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    vid = None
    if host.endswith("youtu.be"):
        vid = parts.path.strip("/").split("/")[0]
    else:
        q = parse_qs(parts.query)
        if "v" in q:
            vid = q["v"][0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", parts.path)
            if m:
                vid = m.group(1)
    if not vid or not _YT_ID.match(vid):
        raise AppError("unsupported_source", message="This YouTube link doesn't point to a single video. Paste the link of the video itself (playlists and channels aren't supported).")
    return f"https://www.youtube.com/watch?v={vid}"


def resolve(raw_url: str) -> ResolvedSource:
    url = normalize_url(raw_url)
    host = (urlsplit(url).hostname or "").lower()
    for spec in SOURCES:
        if _domain_match(host, spec.domains):
            if spec.kind == "youtube":
                url = normalize_youtube(url)
            return ResolvedSource(spec.kind, spec.label, spec.platform, url, spec.extractor)
    path = urlsplit(url).path.lower()
    if get_settings().enable_direct_media_urls and any(path.endswith(ext) for ext in MEDIA_EXTENSIONS):
        return ResolvedSource("direct", "Direct media link", None, url, None)
    raise AppError("unsupported_source")


def supported_labels() -> list[str]:
    labels = [s.label for s in SOURCES]
    if get_settings().enable_direct_media_urls:
        labels.append("direct links to media files")
    return labels
