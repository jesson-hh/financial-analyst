"""Shared, fail-closed policy primitives for explicit Telegram media sends."""
from __future__ import annotations

import hashlib
import re


MEDIA_ID_RE = re.compile(r"^mi_[0-9a-f]{32}$")
MEDIA_PERMIT_ID_RE = re.compile(r"^pub_[0-9a-f]{32}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# A caption is presentation text, never a transport for a local or remote
# resource locator.  Keep the patterns narrow enough for ordinary analytical
# prose (including ``20/60/120``) while rejecting the common path grammars at
# every trust boundary.
_URI_SCHEME_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://"
)
# URI references do not need ``//`` (for example ``https:example`` or
# ``file:C``).  Numeric metric notation is the one intentional exception:
# analytical prose commonly contains values such as ``Alpha:0.3``.  A
# non-numeric scheme body remains fail-closed, including custom schemes.
_SCHEME_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:"
    r"(?!(?:[+-]?(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?:$|[\s\u00b7,，;；%\)\]\}]))"
    r"(?=\S)[^\s\r\n]+"
)
_HIGH_RISK_SCHEME_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])"
    r"(?:https?|wss?|file|data|mailto|javascript|blob|ftp|sftp|ssh|tel|sms):"
    r"(?=\S)"
)
_WINDOWS_DRIVE_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
_UNC_RE = re.compile(r"(?<![A-Za-z0-9])(?:\\\\|//)[^\s\\/]+[\\/][^\s\\/]+")
_POSIX_ABSOLUTE_RE = re.compile(
    r"(?<![A-Za-z0-9\u3400-\u9fff])/(?![/\s])[^\s\r\n]+"
)
_RELATIVE_FILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?:\.{1,2}[\\/])?"
    r"(?:[^\s\\/:]+[\\/])+[^\s\\/:]+\.[A-Za-z][A-Za-z0-9]{0,9}"
    r"(?![A-Za-z0-9])"
)
_RELATIVE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(?:\.{1,2}[\\/])?"
    r"(?=[A-Za-z0-9_.~-]*[A-Za-z_.~-])"
    r"[A-Za-z0-9_.~-]+[\\/]"
    r"(?:[A-Za-z0-9_.~-]+[\\/])*[A-Za-z0-9_.~-]+"
    r"(?![A-Za-z0-9_.~-])"
)
_BARE_FILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])[^\s\\/:]+\."
    r"(?:png|jpe?g|webp|gif|svg|pdf|csv|json|html?|xlsx?)"
    r"(?![A-Za-z0-9])"
)


def caption_contains_location(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return any(
        pattern.search(value) is not None
        for pattern in (
            _URI_SCHEME_RE,
            _SCHEME_PATH_RE,
            _HIGH_RISK_SCHEME_RE,
            _WINDOWS_DRIVE_RE,
            _UNC_RE,
            _POSIX_ABSOLUTE_RE,
            _RELATIVE_FILE_RE,
            _RELATIVE_PATH_RE,
            _BARE_FILE_RE,
        )
    )


def caption_sha256(caption: str) -> str:
    if not isinstance(caption, str):
        raise TypeError("caption must be text")
    return hashlib.sha256(caption.encode("utf-8")).hexdigest()


def media_call_key(media_id: str, caption: str) -> str:
    """In-memory lookup key; unlike the authority subject it omits generation."""
    if not isinstance(media_id, str) or not isinstance(caption, str):
        raise TypeError("media call key requires text")
    return hashlib.sha256(
        media_id.encode("ascii") + b"\x00" + caption.encode("utf-8")
    ).hexdigest()


def media_confirmation_subject(
    media_id: str,
    caption: str,
    binding_id: str,
    binding_paired_at: str,
) -> str:
    if not all(
        isinstance(value, str)
        for value in (media_id, caption, binding_id, binding_paired_at)
    ):
        raise TypeError("media publication subject requires text")
    return hashlib.sha256(
        b"\x00".join(
            (
                media_id.encode("ascii"),
                caption.encode("utf-8"),
                binding_id.encode("ascii"),
                binding_paired_at.encode("ascii"),
            )
        )
    ).hexdigest()


__all__ = [
    "MEDIA_ID_RE",
    "MEDIA_PERMIT_ID_RE",
    "SHA256_HEX_RE",
    "caption_contains_location",
    "caption_sha256",
    "media_call_key",
    "media_confirmation_subject",
]
