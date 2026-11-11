"""Job identifiers, access tokens, and network-address safety checks."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
from urllib.parse import urlsplit, urlunsplit

from .errors import AppError

JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")


def new_job_id() -> str:
    return secrets.token_urlsafe(18)  # 144 bits; unguessable


def job_token(secret: str, job_id: str) -> str:
    """Per-job bearer token. Derived with HMAC so it never needs to be stored."""
    return hmac.new(secret.encode(), job_id.encode(), hashlib.sha256).hexdigest()


def verify_token(secret: str, job_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(job_token(secret, job_id), token or "")


def valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.match(job_id))


# --- URLs -------------------------------------------------------------------

MAX_URL_LENGTH = 2048


def normalize_url(raw: str) -> str:
    """Basic syntactic validation + normalization. Raises AppError('invalid_url')."""
    raw = (raw or "").strip()
    if not raw or len(raw) > MAX_URL_LENGTH or any(c.isspace() for c in raw):
        raise AppError("invalid_url")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as e:
        raise AppError("invalid_url") from e
    if parts.scheme.lower() not in ("http", "https"):
        raise AppError("invalid_url")
    if not parts.hostname or parts.username or parts.password:
        raise AppError("invalid_url")
    host = parts.hostname.lower().rstrip(".")
    try:
        host.encode("idna")
    except UnicodeError as e:
        raise AppError("invalid_url") from e
    netloc = host if port is None else f"{host}:{port}"
    if ":" in host and not host.startswith("["):  # IPv6 literal
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def resolve_public_host(host: str, allow_private: bool = False) -> list[str]:
    """Resolve host and refuse if any address is non-public (prevents SSRF / DNS tricks)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise AppError("access_restricted") from e
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise AppError("access_restricted")
    if not allow_private:
        for a in addrs:
            if not _ip_is_public(ipaddress.ip_address(a.split("%")[0])):
                raise AppError("blocked_url")
    return addrs


def assert_public_url(url: str, allow_private: bool = False) -> None:
    host = urlsplit(url).hostname or ""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        if not allow_private and not _ip_is_public(ip):
            raise AppError("blocked_url")
        return
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        if not allow_private:
            raise AppError("blocked_url")
    resolve_public_host(host, allow_private)


def safe_display_name(name: str | None, limit: int = 120) -> str:
    """A filename safe to show and store (never used as a path)."""
    name = (name or "upload").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip() or "upload"
    return name[:limit]


def url_fingerprint(url: str) -> str:
    """Logs contain this instead of full URLs."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]
