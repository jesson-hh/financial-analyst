"""A-share instrument value objects: :class:`Symbol`, :class:`InstrumentMeta`,
:class:`LimitRule`.

These are immutable, strict semantic contracts built on
:class:`~guanlan_v2.orchestration.digest.DigestModel`, so they inherit its
frozen + ``strict`` + ``extra="forbid"`` configuration and participate in the
canonical semantic/audit digests. The invariants encoded here are the ones a
downstream deterministic layer must be able to trust without re-checking:

* a ``code`` is exactly six digits (syntactic shape only — never guesses ST /
  board membership from digit prefixes);
* an ``exchange`` and ``board`` are drawn from closed ``Literal`` sets (pydantic
  strict mode rejects raw strings for ``enum.Enum`` fields, so a ``Literal`` is
  used for these closed sets) and must be mutually compatible;
* an unknown ``is_st`` is an explicit ``None`` rather than a silent ``False``;
* a ``LimitRule.pct`` is either a finite fraction in ``(0, 1]`` or an explicit
  ``None`` meaning "rule unknown".
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from guanlan_v2.orchestration.digest import DigestModel, FiniteFloat, UtcDateTime

_CODE_RE = re.compile(r"^[0-9]{6}$")

#: Which boards each exchange may host. SH main/科创板(star); SZ main/创业板
#: (chinext); BJ only its own board.
_EXCHANGE_BOARDS: dict[str, frozenset[str]] = {
    "SH": frozenset({"main", "star"}),
    "SZ": frozenset({"main", "chinext"}),
    "BJ": frozenset({"bj"}),
}


class Symbol(DigestModel):
    """An A-share instrument identity: six-digit code + exchange + board.

    ``dotted`` (``600519.SH``) and ``engine_code`` (``SH600519``) are the two
    external string forms; both are derived, never stored, so they cannot drift
    from the structured fields.
    """

    code: str
    exchange: Literal["SH", "SZ", "BJ"]
    board: Literal["main", "star", "chinext", "bj"]

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError(f"code must be 6 digits, got {v!r}")
        return v

    @model_validator(mode="after")
    def _exchange_board(self) -> "Symbol":
        if self.board not in _EXCHANGE_BOARDS[self.exchange]:
            raise ValueError(
                f"board={self.board} incompatible with exchange={self.exchange}"
            )
        return self

    @property
    def dotted(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def engine_code(self) -> str:
        return f"{self.exchange}{self.code}"


class InstrumentMeta(DigestModel):
    """Slow-moving instrument metadata keyed by a :class:`Symbol`.

    ``is_st`` is deliberately tri-state: ``None`` means "unknown" and must be
    stated explicitly — the code shape alone cannot infer ST status, so we never
    silently default it to ``False``. ``listed_at`` / ``metadata_available_at``
    are tz-aware (UTC-normalized) or an explicit ``None``.
    """

    symbol: Symbol
    is_st: bool | None = None
    listed_at: UtcDateTime | None = None
    metadata_available_at: UtcDateTime | None = None


class LimitRule(DigestModel):
    """The price-limit rule that applies to an instrument on a given day.

    ``pct`` is a finite fraction in ``(0, 1]`` (e.g. ``0.10`` for a 10% limit)
    or an explicit ``None`` meaning the rule is unknown. ``reason`` /
    ``rule_version`` record why the rule was chosen and which rule table produced
    it.
    """

    pct: FiniteFloat | None = Field(default=None, gt=0, le=1)
    reason: str
    rule_version: str
