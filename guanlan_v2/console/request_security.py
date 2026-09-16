"""Small, shared browser-request guards for local Console capabilities.

The Console intentionally remains usable from origin-less local CLI/TestClient
callers.  Browser requests, however, must prove that they originate from the
same local Guanlan origin.  Channel controls add a ``Sec-Fetch-Site`` check so
they cannot be turned into a cross-site secret-submission surface.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request


def trusted_local_request(request: Request) -> bool:
    """Allow origin-less local callers, reject cross-origin browser requests."""
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    try:
        src = urlsplit(origin)
        dst_host = (request.url.hostname or "").lower()
        src_host = (src.hostname or "").lower()
        src_port = src.port or (443 if src.scheme == "https" else 80)
        dst_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        src.scheme == request.url.scheme
        and src_host == dst_host
        and src_port == dst_port
        and src_host in {"127.0.0.1", "localhost", "::1"}
    )


def trusted_channel_request(request: Request) -> bool:
    """Apply the stricter local browser boundary used by Channel writes/reads."""
    if not trusted_local_request(request):
        return False
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    return not fetch_site or fetch_site in {"same-origin", "none"}


__all__ = ["trusted_channel_request", "trusted_local_request"]
