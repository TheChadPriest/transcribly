import pytest

from app.errors import AppError
from app.security import (
    assert_public_url,
    job_token,
    new_job_id,
    normalize_url,
    safe_display_name,
    valid_job_id,
    verify_token,
)
from app.sources.fetch import _map_ytdlp_error
from app.sources.resolver import normalize_youtube, resolve


@pytest.mark.parametrize("raw,expected", [
    ("https://Example.com/a?b=1#frag", "https://example.com/a?b=1"),
    ("example.com/video.mp4", "https://example.com/video.mp4"),
    ("HTTP://example.com", "http://example.com/"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", "ftp://example.com/a.mp3", "file:///etc/passwd", "javascript:alert(1)",
    "https://user:pass@example.com/x.mp4", "https://exa mple.com", "https://" + "a" * 3000 + ".com",
])
def test_normalize_url_rejects(raw):
    with pytest.raises(AppError) as e:
        normalize_url(raw)
    assert e.value.code == "invalid_url"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.mp4", "http://10.0.0.5/a.mp4", "http://192.168.1.1/a.mp4", "http://169.254.169.254/latest",
    "http://[::1]/a.mp4", "http://[::ffff:127.0.0.1]/a.mp4", "http://localhost/a.mp4", "http://foo.internal/a.mp4",
    "http://100.64.0.1/a.mp4", "http://0.0.0.0/a.mp4",
])
def test_private_addresses_blocked(url):
    with pytest.raises(AppError) as e:
        assert_public_url(url)
    assert e.value.code == "blocked_url"


def test_dns_resolving_to_private_is_blocked(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(AppError) as e:
        assert_public_url("https://evil.example.com/a.mp4")
    assert e.value.code == "blocked_url"


def test_tokens():
    jid = new_job_id()
    assert valid_job_id(jid)
    tok = job_token("secret", jid)
    assert verify_token("secret", jid, tok)
    assert not verify_token("secret", jid, tok[:-1] + ("1" if tok[-1] == "0" else "0"))
    assert not verify_token("other", jid, tok)
    assert not verify_token("secret", jid, None)
    assert not valid_job_id("../etc/passwd")


def test_safe_display_name():
    assert safe_display_name("C:\\Users\\me\\lecture 1.mp4") == "lecture 1.mp4"
    assert safe_display_name("../../etc/passwd") == "passwd"
    assert safe_display_name("a\x00b.mp3") == "ab.mp3"
    assert safe_display_name(None) == "upload"


@pytest.mark.parametrize("url,vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&t=10", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_youtube_normalization(url, vid):
    assert normalize_youtube(url) == f"https://www.youtube.com/watch?v={vid}"
    r = resolve(url)
    assert r.kind == "youtube" and r.platform == "YouTube"


def test_youtube_playlist_or_channel_rejected():
    for url in ("https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/@somechannel"):
        with pytest.raises(AppError) as e:
            resolve(url)
        assert e.value.code == "unsupported_source"


def test_resolve_other_sources(settings):
    assert resolve("https://vimeo.com/76979871").kind == "vimeo"
    assert resolve("https://archive.org/details/foo").kind == "archive_org"
    assert resolve("https://cdn.example.org/media/lecture.MP3").kind == "direct"
    with pytest.raises(AppError) as e:
        resolve("https://example.com/article.html")
    assert e.value.code == "unsupported_source"
    with pytest.raises(AppError):
        resolve("notaurl")  # becomes https://notaurl/ -> unsupported


@pytest.mark.parametrize("msg,code", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "access_restricted"),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot", "access_restricted"),
    ("HTTP Error 429: Too Many Requests", "rate_limited"),
    ("ERROR: Unsupported URL: https://x", "unsupported_source"),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours", "live_stream"),
    ("ERROR: Connection reset", "download_failed"),
])
def test_ytdlp_error_mapping(msg, code):
    assert _map_ytdlp_error(Exception(msg)).code == code
