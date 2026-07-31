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


# ── syntactic, path-safe symbol normalization ──────────────────────────────
#: The three complete external grammars. Each is anchored (``fullmatch``) so a
#: partial or embedded code can never sneak through: only a bare six-digit code
#: (``600519``), a dotted form (``600519.SH``) or an engine form (``SH600519``)
#: is accepted. There is deliberately no lenient/"repair" path.
_BARE_RE = re.compile(r"^(?P<code>[0-9]{6})$")
_DOTTED_RE = re.compile(r"^(?P<code>[0-9]{6})\.(?P<exchange>SH|SZ|BJ)$")
_ENGINE_RE = re.compile(r"^(?P<exchange>SH|SZ|BJ)(?P<code>[0-9]{6})$")


def normalize_symbol(raw: str) -> Symbol:
    """Normalize a complete A-share symbol string into a :class:`Symbol`.

    Purely syntactic and offline: it only reshapes a *complete* bare / dotted /
    engine code and infers ``exchange``/``board`` from the 号段. It never touches
    the network and never infers ST status, listing stage, or the day's
    price-limit — those are metadata, not shape.

    Inference rules (prefix → exchange, board):

    * ``688`` → ``SH``/``star`` (科创板)
    * ``300`` / ``301`` → ``SZ``/``chinext`` (创业板)
    * leading ``8`` or ``4``, or ``920`` → ``BJ``/``bj`` (北交所; leading 8
      covers the 82/83/87/88 号段, leading 4 covers 40/42/43, and ``920`` is
      the exchange's own new-issue 号段 — other ``92x``/``9xx`` codes stay on
      the fall-through)
    * leading ``6`` → ``SH``/``main``
    * otherwise → ``SZ``/``main``

    Anything that is not one of the three complete grammars — an embedded code,
    trailing junk, multiple codes, the wrong number of digits — is rejected with
    a :class:`ValueError` rather than silently repaired. A non-string ``raw``
    (including ``bool``, which subclasses ``int``) raises :class:`TypeError`; no
    coercion is attempted. When the caller supplies an explicit exchange that
    disagrees with the code-derived one, that too is a :class:`ValueError`
    (message mentions ``exchange``) — the conflict is surfaced, never resolved by
    fiat.

    The returned :attr:`Symbol.code` always matches ``^[0-9]{6}$`` and is thus
    safe to use as a cache-key component.
    """
    if type(raw) is not str:
        raise TypeError(f"symbol must be a str, got {type(raw).__name__}")
    s = raw.strip().upper()
    m = _BARE_RE.fullmatch(s) or _DOTTED_RE.fullmatch(s) or _ENGINE_RE.fullmatch(s)
    if m is None:
        raise ValueError(f"unsupported A-share symbol grammar: {raw!r}")
    code = m.group("code")
    explicit_exchange = m.groupdict().get("exchange")
    if code.startswith("688"):
        exchange, board = "SH", "star"
    elif code.startswith(("300", "301")):
        exchange, board = "SZ", "chinext"
    elif code[0] in ("8", "4") or code.startswith("920"):
        exchange, board = "BJ", "bj"
    elif code[0] == "6":
        exchange, board = "SH", "main"
    else:
        exchange, board = "SZ", "main"
    if explicit_exchange is not None and explicit_exchange != exchange:
        raise ValueError(
            f"explicit exchange {explicit_exchange} conflicts with "
            f"code-derived exchange {exchange} for {code}"
        )
    return Symbol(code=code, exchange=exchange, board=board)


# ─────────────────────────────────────────────────────────────────────────────
# Name resolution + versioned, PIT-aware price-limit rules (Phase 3 · Task 3)
# ─────────────────────────────────────────────────────────────────────────────
# Appended (existing content above is byte-untouched). These are pure, offline
# resolvers plus the closed, versioned price-limit policy they consult.
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import ClassVar

from guanlan_v2.orchestration.data.calendar import TradingCalendar
from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    NonNegativeInt,
    content_digest,
)
from guanlan_v2.orchestration.refs import ContentRef

#: The closed set of boards a policy rule table may key on.
_KNOWN_BOARDS: frozenset[str] = frozenset({"main", "star", "chinext", "bj"})


def _aware_utc(v: object) -> datetime:
    """Return ``v`` normalized to UTC iff it is a tz-aware datetime.

    A non-datetime raises :class:`TypeError`; a naive datetime raises
    :class:`ValueError`. Mirrors the Phase 1 ``UtcDateTime`` rule for a plain
    argument that is not a validated model field.
    """
    if not isinstance(v, datetime):
        raise TypeError(f"as_of must be a tz-aware datetime, got {type(v).__name__}")
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("as_of must be tz-aware; a naive datetime is rejected")
    return v.astimezone(timezone.utc)


def resolve_name_to_code(raw: str, name_map: Mapping[str, str]) -> Symbol:
    """Resolve a *complete* symbol or a verified instrument *name* to a Symbol.

    Order of attempts:

    1. the exact A-share symbol grammar via :func:`normalize_symbol` (so a code /
       dotted / engine form passes straight through);
    2. the ``name_map`` — which must be extracted from the digest-verified,
       PIT-filtered ``InstrumentNameRows`` (Task 5); this helper never fetches or
       mutates a map and public/capability APIs never accept a caller-supplied
       current map. A mapped value is itself run through :func:`normalize_symbol`,
       so a bogus map value is rejected, never trusted blindly.

    Anything that is neither a symbol nor a verified name — in particular a CJK
    industry / concept term such as ``"白酒"`` — is rejected with a
    :class:`ValueError`. The resolver never *guesses* a code; the caller must pass
    a six-digit code (or a verified exact name).
    """
    if type(raw) is not str:
        raise TypeError(f"name/code must be a str, got {type(raw).__name__}")
    try:
        return normalize_symbol(raw)
    except (TypeError, ValueError):
        pass
    for key in (raw, raw.strip()):
        if key in name_map:
            value = name_map[key]
            try:
                return normalize_symbol(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"verified name map value {value!r} for {key!r} is not a valid "
                    f"symbol: {exc}"
                ) from exc
    raise ValueError(
        f"cannot resolve {raw!r} to a 6-digit code: it is neither a symbol nor a "
        "verified instrument name (industry / concept names are rejected, never "
        "guessed — pass a 6-digit code)"
    )


class LimitRuleEntry(DigestModel):
    """One as-of-effective slice of a versioned price-limit rule table.

    Closed and immutable. ``board_pct`` maps each board to the ordinary (non-ST)
    daily price-limit fraction; ``st_pct`` is the fraction applied when an
    instrument is ST, regardless of board. ``first_session_window`` is the number
    of initial trading *sessions* after listing during which the ordinary limit
    does not yet apply — a session-based rule that can only be evaluated against
    the exact authoritative trading calendar — and ``first_session_pct`` is the
    fraction that applies inside that window, or ``None`` when the initial
    sessions carry no ordinary price limit at all.
    """

    schema_version: Literal["1"] = "1"
    effective_from: UtcDateTime
    effective_to: UtcDateTime | None = None
    board_pct: dict[str, FiniteFloat]
    st_pct: FiniteFloat = Field(gt=0, le=1)
    first_session_window: NonNegativeInt = 0
    first_session_pct: FiniteFloat | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _check(self) -> "LimitRuleEntry":
        if not self.board_pct:
            raise ValueError("board_pct must be non-empty")
        for board, pct in self.board_pct.items():
            if board not in _KNOWN_BOARDS:
                raise ValueError(f"unknown board {board!r} in board_pct")
            if not (0.0 < pct <= 1.0):
                raise ValueError(f"board_pct[{board!r}] must be a fraction in (0, 1]")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be strictly after effective_from")
        return self

    def covers(self, as_of: datetime) -> bool:
        if as_of < self.effective_from:
            return False
        if self.effective_to is not None and as_of >= self.effective_to:
            return False
        return True


class LimitRulePolicy(DigestModel):
    """A closed, versioned A-share price-limit policy sealed by its own digest.

    Holds an ordered, non-overlapping table of :class:`LimitRuleEntry` slices
    selected by ``as_of``, plus the exact trading-calendar identity
    (``calendar_id`` + versioned ``calendar_material_ref``) any session-based
    listing-stage rule must be evaluated against. ``policy_digest`` self-seals the
    reviewed material; there is deliberately **no** unversioned module-global
    policy. Build (and seal) it with :func:`build_limit_rule_policy`; Task 5
    registers it and Task 6 refers to it by :class:`ContentRef`.
    """

    schema_version: Literal["1"] = "1"
    policy_id: NonEmptyStr
    policy_version: NonEmptyStr
    calendar_id: NonEmptyStr
    calendar_material_ref: ContentRef
    entries: tuple[LimitRuleEntry, ...]
    policy_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"policy_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "LimitRulePolicy":
        if not self.entries:
            raise ValueError("a LimitRulePolicy must carry at least one entry")
        starts = [e.effective_from for e in self.entries]
        if starts != sorted(starts):
            raise ValueError("entries must be ordered by effective_from")
        for prev, cur in zip(self.entries, self.entries[1:]):
            if prev.effective_to is None or prev.effective_to > cur.effective_from:
                raise ValueError(
                    "policy entries must be closed and non-overlapping in order"
                )
        if self.policy_digest != self.semantic_digest():
            raise ValueError("declared policy_digest does not match canonical digest")
        return self

    @property
    def rule_version(self) -> str:
        return f"{self.policy_id}@{self.policy_version}"

    def entry_for(self, as_of: datetime) -> "LimitRuleEntry | None":
        for entry in self.entries:
            if entry.covers(as_of):
                return entry
        return None


def build_limit_rule_policy(
    *,
    policy_id: str,
    policy_version: str,
    calendar: TradingCalendar,
    entries: tuple[LimitRuleEntry, ...],
) -> LimitRulePolicy:
    """Seal a :class:`LimitRulePolicy`, binding it to ``calendar``'s exact identity.

    The policy's ``calendar_id`` / ``calendar_material_ref`` are copied from the
    supplied calendar so a session-based rule can only be evaluated against the
    exact material named here. ``policy_digest`` is computed from the reviewed
    field values (never a placeholder) and re-verified by the constructor.
    """
    fields = dict(
        policy_id=policy_id,
        policy_version=policy_version,
        calendar_id=calendar.calendar_id,
        calendar_material_ref=calendar.material_ref,
        entries=entries,
    )
    digest = LimitRulePolicy.digest_of_fields(projection="semantic", **fields)
    return LimitRulePolicy(**fields, policy_digest=digest)


def resolve_limit_rule(
    sym: Symbol,
    as_of: datetime,
    meta: InstrumentMeta,
    *,
    policy: LimitRulePolicy,
    calendar: TradingCalendar | None,
) -> LimitRule:
    """Deterministically resolve the price-limit rule for ``sym`` at ``as_of``.

    PIT-aware and calendar-aware. Before consulting the rule table it verifies
    ``meta.symbol == sym`` (mismatch raises) and that ``as_of`` is tz-aware
    (naive raises). It then requires the metadata to be *knowable* and *not in the
    future* at ``as_of``: missing/future ``metadata_available_at``, a
    not-yet-listed instrument, an unknown ST flag, an ``as_of`` outside every
    policy window, or an unavailable / mismatched trading calendar for a
    session-based listing-stage rule each return an explicit ``pct=None``
    :class:`LimitRule` with a stated reason — never a guessed percentage. The
    injected digest-frozen ``policy`` (selected by ``as_of``) owns the ST / board /
    listing-stage rules.

    **Clamped lower-bound semantics for the session window** (the calendar
    coverage contract, ``data/calendar.py``: an uncovered date is *uncovered*,
    not "zero sessions" — counting across it silently undercounts). The
    session-based listing-stage rule is evaluated in three arms:

    1. ``as_of`` outside the calendar's covered span → the elapsed-session
       count is not computable at all → typed unknown refusal.
    2. ``listed_at`` inside coverage → the exact
       ``sessions_between(listed_at, as_of)`` count governs, unchanged — true
       IPO fidelity (a genuine new listing keeps its real no-limit window).
    3. ``listed_at`` *before* coverage → the exact count is unknowable, but
       ``lower_bound = sessions_between(coverage_start, as_of)`` is a certain
       lower bound. If ``lower_bound >= first_session_window`` the window has
       CERTAINLY passed: the listing day itself was a real trading session
       that predates coverage and is therefore *not* in ``lower_bound``, so
       the true count is at least ``lower_bound + 1 > window`` — the ordinary
       limit applies. Otherwise the window may or may not have passed and the
       answer is a typed unknown refusal ("listed before calendar coverage"),
       **never** the false "no ordinary price limit applies" that a raw
       undercount produced for every pre-coverage-listed instrument in the
       material's first ``window`` sessions.

    Rejected alternatives, for the record: ``first_session_window=0`` trades
    one falsehood for another (a genuine in-coverage IPO would wrongly get the
    ordinary pct inside its true no-limit window); a bare coverage assertion
    on ``listed_at`` turns EVERY pre-coverage-listed instrument into unknown
    forever.
    """
    as_of = _aware_utc(as_of)  # raises on naive / non-datetime

    if meta.symbol != sym:
        raise ValueError(
            f"metadata symbol {meta.symbol.dotted} does not match the requested "
            f"symbol {sym.dotted}"
        )

    def _unknown(reason: str) -> LimitRule:
        return LimitRule(pct=None, reason=reason, rule_version=policy.rule_version)

    entry = policy.entry_for(as_of)
    if entry is None:
        return _unknown(
            f"no limit-rule policy window covers as_of {as_of.isoformat()}"
        )

    if meta.metadata_available_at is None or meta.metadata_available_at > as_of:
        return _unknown("instrument metadata is not yet available at as_of")

    if meta.listed_at is None or meta.listed_at > as_of:
        return _unknown("instrument is not yet listed at as_of")

    if meta.is_st is None:
        return _unknown("ST status is unknown; the price limit cannot be determined")

    # Session-based listing-stage rule: requires the exact authoritative calendar.
    # Even a clearly seasoned instrument must be confirmed past the initial window
    # by counting real sessions — never by guessing from calendar-day arithmetic.
    if entry.first_session_window > 0:
        if (
            calendar is None
            or calendar.calendar_id != policy.calendar_id
            or calendar.material_ref != policy.calendar_material_ref
        ):
            return _unknown(
                "the exact trading calendar is unavailable or does not match the "
                "policy; the listing-stage price limit cannot be determined"
            )
        as_of_day = as_of.date()
        listed_day = meta.listed_at.date()
        coverage = calendar.coverage
        if coverage is None or not (coverage[0] <= as_of_day <= coverage[1]):
            return _unknown(
                f"as_of {as_of_day.isoformat()} is outside the trading "
                "calendar's coverage; an uncovered date is uncovered, not "
                "zero sessions, so the listing-stage rule cannot be evaluated"
            )
        if listed_day >= coverage[0]:
            # Arm 2 — exact count (both endpoints inside coverage): true IPO
            # fidelity, semantics unchanged.
            sessions_since_listing = calendar.sessions_between(listed_day, as_of_day)
            if sessions_since_listing <= entry.first_session_window:
                if entry.first_session_pct is None:
                    return LimitRule(
                        pct=None,
                        reason=(
                            "within the initial listing sessions: no ordinary "
                            "price limit applies"
                        ),
                        rule_version=policy.rule_version,
                    )
                return LimitRule(
                    pct=entry.first_session_pct,
                    reason="initial listing-session price limit",
                    rule_version=policy.rule_version,
                )
        else:
            # Arm 3 — clamped lower bound: the listing predates coverage, so
            # the exact count is unknowable. Counting from coverage_start is a
            # certain lower bound; the pre-coverage listing session itself is
            # a real session NOT in it, so lower_bound >= window certifies the
            # window has passed (true count >= lower_bound + 1 > window).
            lower_bound = calendar.sessions_between(coverage[0], as_of_day)
            if lower_bound < entry.first_session_window:
                return _unknown(
                    "listed before calendar coverage; elapsed sessions cannot "
                    "be established, so whether the initial listing window has "
                    "passed is indeterminate"
                )
            # else: certainly past the window — fall through to ST/board.

    if meta.is_st:
        return LimitRule(
            pct=entry.st_pct,
            reason=f"ST instrument on board {sym.board}",
            rule_version=policy.rule_version,
        )

    board_pct = entry.board_pct.get(sym.board)
    if board_pct is None:
        return _unknown(f"policy has no ordinary limit for board {sym.board!r}")
    return LimitRule(
        pct=board_pct,
        reason=f"ordinary limit for board {sym.board}",
        rule_version=policy.rule_version,
    )
