"""Shared fail-closed scanners for Telegram-adjacent secret material.

These scanners intentionally do not depend on word or identifier boundaries.
An attacker can prepend or append an ordinary ASCII character without changing
the sensitive bytes embedded in a value, so every ingress, persistence and
projection boundary must detect the shape inside a larger run as well.
"""
from __future__ import annotations

import re


TELEGRAM_TOKEN_SCAN_RE = re.compile(
    r"(?:bot)?([0-9]{5,16}:[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)
PAIRING_CODE_ALPHABET = frozenset("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
PAIRING_CODE_SCAN_RE = re.compile(
    r"([23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8})"
)
PAIRING_CODE_RUN_RE = re.compile(
    r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8,}"
)
PAIRING_CODE_EXACT_RE = re.compile(
    r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$"
)


def telegram_token_matches(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    return tuple(match.group(1) for match in TELEGRAM_TOKEN_SCAN_RE.finditer(value))


def contains_telegram_token(value: object) -> bool:
    return isinstance(value, str) and TELEGRAM_TOKEN_SCAN_RE.search(value) is not None


def redact_telegram_tokens(value: object, replacement: str) -> str:
    """Redact every Token-shaped substring, including adjacent characters."""

    return TELEGRAM_TOKEN_SCAN_RE.sub(replacement, str(value or ""))


def pairing_code_windows(value: object) -> set[str]:
    """Return every overlapping eight-character code window in ``value``."""

    if not isinstance(value, str) or not value:
        return set()
    result: set[str] = set()
    for match in PAIRING_CODE_RUN_RE.finditer(value):
        run = match.group(0)
        result.update(run[index:index + 8] for index in range(len(run) - 7))
    return result


def contains_pairing_code(value: object) -> bool:
    return isinstance(value, str) and PAIRING_CODE_RUN_RE.search(value) is not None


def redact_pairing_code_runs(value: object, replacement: str) -> str:
    return PAIRING_CODE_RUN_RE.sub(replacement, str(value or ""))


__all__ = [
    "PAIRING_CODE_ALPHABET",
    "PAIRING_CODE_EXACT_RE",
    "PAIRING_CODE_RUN_RE",
    "PAIRING_CODE_SCAN_RE",
    "TELEGRAM_TOKEN_SCAN_RE",
    "contains_pairing_code",
    "contains_telegram_token",
    "pairing_code_windows",
    "redact_pairing_code_runs",
    "redact_telegram_tokens",
    "telegram_token_matches",
]
