"""Localhost-only guard: reject requests whose Host header isn't loopback.

Syrudas binds to 127.0.0.1, but that alone doesn't stop a malicious web page
the user visits from POSTing to http://127.0.0.1:8040 (DNS rebinding / drive-by
CSRF) to spend API credits or drive the agent. Such requests always carry the
attacker's domain in the Host header; a loopback allowlist rejects them while
leaving genuine local access (localhost / 127.0.0.1 / [::1]) untouched.
"""
from __future__ import annotations

import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

_LOCAL_HOSTNAMES = {"localhost"}


def _hostname(host_header: str) -> str:
    """Strip the optional :port (and IPv6 brackets) from a Host header value."""
    host = host_header.strip()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8040
        end = host.find("]")
        return host[1:end] if end != -1 else host
    if host.count(":") == 1:  # host:port (a bare IPv6 has multiple colons)
        host = host.split(":", 1)[0]
    return host


def is_local_host(host_header: str | None) -> bool:
    """True if the Host header names a loopback address or is absent.

    A missing Host is allowed: HTTP/1.1 browsers always send one, so its
    absence means a non-browser local client (curl, a script) - not a
    rebinding attack, which by definition arrives with an external hostname.
    """
    if not host_header:
        return True
    host = _hostname(host_header).lower()
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_local_host(request.headers.get("host")):
            return PlainTextResponse(
                "Forbidden: Syrudas only accepts local (loopback) connections.",
                status_code=403,
            )
        return await call_next(request)
