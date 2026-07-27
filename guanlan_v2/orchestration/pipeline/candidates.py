# -*- coding: utf-8 -*-
"""Phase 10 · Task 2 — the deterministic candidate workers.

Three zero-LLM, zero-network DETERMINISTIC workers produce the Task-1
``CandidateSlate@1`` that the 帷幄选股 pipeline screens from:

* ``cand.v4`` — the production v4 ranking artifact's top N;
* ``cand.model`` — the same read against a named model variant;
* ``cand.lane0`` — **the honest typed refusal** (:class:`Lane0LeadersUnavailable`)
  in v1, per ratified D4 (see below).

**Handler ABI (E2).** The executor's deterministic branch is two-stage
(``guanlan_v2/orchestration/worker.py:1730-1735``)::

    handler = handler_factory(worker=worker, resolved=resolved)
    model_result = handler(node=…, input_snapshot=…, contributions=…,
                           data_result_refs=…)

so :func:`cand_v4_handler` / :func:`cand_lane0_handler` / :func:`cand_model_handler`
*bind* their params + ports and return that inner callable, exactly as the
production precedent ``bootstrap.bootstrap_market_factor_handler``
(bootstrap.py:1673) does; :func:`as_handler_factory` is the one place the
``factory(worker=…, resolved=…)`` registration shape is written down (registration
itself is ``TrustedFactoryRegistry.register_handler``, catalog_runtime.py:456 —
Task 11's job). The returned :class:`~guanlan_v2.orchestration.worker.ModelResult`
carries the slate as its ``payload``; the executor validates it against the
worker's ``CandidateSlate@1`` output binding.

**Honesty rules encoded here**

1. *Staleness is badged, never re-dated and never refused.* The v4 ranking is an
   evening artifact consumed by the next session, so a slate produced on a later
   session date carries ``stale_ranking:<artifact-session-date>`` while its own
   ``as_of`` stays the RUN's as-of. Freshness is judged on the Asia/Shanghai
   session date (``session_date_of``), not the UTC date — 22:00Z is already the
   next CN session.
2. *An empty slate is legal but never silent.* Zero candidates ⇒ badge
   ``no_candidates`` **and** a DEGRADED node (the bootstrap all-UNAVAILABLE
   precedent), never a quiet success.
3. *A row outside the Phase 3 symbol grammar is excluded and counted*
   (``unmappable_codes:<n>``), never re-mapped by fiat. This is live today: the
   vendored artifact's 920xxx 北交所 rows derive exchange SZ from their 号段 and
   are refused by ``normalize_symbol`` (an upstream Phase-3 grammar gap, not a
   thing this worker may paper over).
4. *Everything else is Task 1's to enforce.* Rank monotonicity, unique codes,
   ``len(entries) <= top_n`` and the source-kind biconditionals live in
   ``CandidateSlate``'s validators; a duplicate-bearing artifact therefore fails
   loudly instead of being silently deduped here.
5. *Read-only.* The v4 ranking surface is never written (spec §8 v4 信号不动) —
   this module contains no write call at all.

**Ratified D4 (``cand.lane0``).** Neither ``RotationReport``/``MainlineRead`` nor
the ladder factor point carries leader stock codes; the handoff gate pins that
absence (``tests/orchestration/test_phase10_handoff.py``,
``test_d4_rotation_report_carries_no_leader_codes``). So ``cand.lane0`` ships its
full param + provenance surface and refuses with a typed error naming what is
missing — never an invented universe. The day upstream grows codes, the gate
reddens, D4 re-opens, and :func:`build_lane0_slate` is the single seam that flips
to real extraction.

**Ratified D5 (the ranking read surface).** ``guanlan_v2.strategy.ranking``'s
``load_v4_ranking`` + ``ranking_date`` are ONE surface: ``model_id`` ``None``
selects the production artifact and a variant id selects
``models/<id>/v4_ranking.parquet`` (ranking.py:59-65, which is also where
``guanlan_v2.screen.model_registry.variant_ranking_path`` is consulted — binding
that helper a second time here would be a drift-prone parallel path). Both
imports are LAZY: importing this module pulls in no pandas and no screen /
strategy code.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields as dataclass_fields
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
# Deliberate reuse (not a second copy): the reviewed Asia/Shanghai session-date
# rule — fixed +08:00, CN has no DST — lives at market/factors.py:1697 and is
# imported BY OBJECT so the two cannot drift.
from guanlan_v2.orchestration.market.factors import _session_date as session_date_of
from guanlan_v2.orchestration.pipeline.contracts import (
    CandidateEntry,
    CandidateSlate,
    CandidateSourceKind,
)
from guanlan_v2.orchestration.refs import TypedPayloadRef

__all__ = [
    # worker identities
    "CAND_V4_WORKER_ID",
    "CAND_LANE0_WORKER_ID",
    "CAND_MODEL_WORKER_ID",
    # badges
    "STALE_RANKING_BADGE_PREFIX",
    "UNMAPPABLE_CODES_BADGE_PREFIX",
    "EMPTY_SLATE_BADGE",
    # errors
    "CandidateWorkerError",
    "CandidateParamsError",
    "RankingSourceUnavailable",
    "Lane0LeadersUnavailable",
    # the ranking read surface
    "RankingRow",
    "RankingArtifact",
    "RankingReader",
    "build_production_ranking_reader",
    "compute_ranking_artifact_digest",
    "session_date_of",
    "session_date_to_utc",
    # params + ports
    "CandV4Params",
    "CandLane0Params",
    "CandModelParams",
    "CandidatePorts",
    # builders + handlers
    "build_v4_slate",
    "build_model_variant_slate",
    "build_lane0_slate",
    "cand_v4_handler",
    "cand_lane0_handler",
    "cand_model_handler",
    "as_handler_factory",
]

#: the three catalog worker ids Task 11 registers.
CAND_V4_WORKER_ID = "cand.v4"
CAND_LANE0_WORKER_ID = "cand.lane0"
CAND_MODEL_WORKER_ID = "cand.model"

#: ``stale_ranking:<artifact session date>`` — honest staleness, never a refusal.
STALE_RANKING_BADGE_PREFIX = "stale_ranking:"
#: ``unmappable_codes:<n>`` — source rows skipped because their code is outside
#: the Phase 3 grammar (counted while filling the slate, never silently dropped).
UNMAPPABLE_CODES_BADGE_PREFIX = "unmappable_codes:"
#: 无候选=诚实 — a legal empty slate, also surfaced as a DEGRADED node.
EMPTY_SLATE_BADGE = "no_candidates"

#: the reviewed param envelopes (plan §Task 2).
MIN_TOP_N, MAX_TOP_N = 1, 50
MIN_MAINLINE_LIMIT, MAX_MAINLINE_LIMIT = 1, 5

#: the v4 ranking artifact's column contract (ranking.py ``V4_COLUMNS``).
_CODE_COLUMN = "code"
_RANK_COLUMN = "lgb_rank"
_SCORE_COLUMN = "lgb_score"

_CST = timezone(timedelta(hours=8))

#: the upstream field surface D4 was decided against — named in the refusal so a
#: future reader knows exactly what has to appear before extraction is possible.
_D4_UPSTREAM_SURFACE = (
    "RotationReport.mainlines[].MainlineRead"
    "{name, universe_key, stage, strength, persistence, evidence, chain_nodes}"
)


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class CandidateWorkerError(RuntimeError):
    """Base of every typed refusal a candidate worker may raise."""


class CandidateParamsError(CandidateWorkerError):
    """A node/factory param is outside its reviewed envelope (never coerced)."""


class RankingSourceUnavailable(CandidateWorkerError):
    """The ranking read surface is absent or structurally unusable."""


class Lane0LeadersUnavailable(CandidateWorkerError):
    """``cand.lane0``'s ratified-D4 refusal: upstream carries no leader codes.

    Carries the full provenance of the read it *would* have made, so the refusal
    is auditable: the run's ``as_of``, the pinned ``RotationReport@1`` ref (when
    one was injected) and the mainline names that were present.
    """

    def __init__(
        self,
        message: str,
        *,
        as_of: datetime,
        rotation_report_ref: TypedPayloadRef | None,
        mainline_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.as_of = as_of
        self.rotation_report_ref = rotation_report_ref
        self.mainline_names = mainline_names


# =========================================================================== #
# the ranking read surface (internal carriers + port)                          #
# =========================================================================== #
class RankingRow(DigestModel):
    # One row of a ranking artifact as READ (internal carrier; nested, no
    # schema_version). ``code`` keeps the source grammar verbatim — normalization
    # happens at the CandidateEntry boundary, so an unmappable row can be counted
    # rather than silently reshaped. ``score`` is an explicit None when the source
    # has none (never a fabricated 0.0).
    code: NonEmptyStr
    rank: PositiveInt
    score: FiniteFloat | None = None


class RankingArtifact(DigestModel):
    # One ranking read (internal carrier). ``as_of`` is the ARTIFACT's own
    # instant (its Asia/Shanghai session date at CN midnight), never the run's;
    # ``artifact_digest`` identifies exactly the rows that were read.
    as_of: UtcDateTime
    artifact_digest: DigestHex
    rows: tuple[RankingRow, ...] = ()


@runtime_checkable
class RankingReader(Protocol):
    """The injected port every candidate worker reads its ranking through.

    ``variant_id`` is ``None`` for the production v4 artifact and a model-variant
    id otherwise (the ratified-D5 single surface). ``as_of_hint`` is the run's
    as-of; a reader that cannot serve a point-in-time artifact ignores it and
    returns what it has — the caller badges the staleness rather than re-dating.
    """

    def read_ranking(
        self, *, variant_id: str | None, as_of_hint: UtcDateTime
    ) -> RankingArtifact:
        ...


def session_date_to_utc(date_text: str) -> datetime:
    """``"2026-07-24"`` (an Asia/Shanghai session date) → that day's CN midnight.

    The inverse of :func:`session_date_of` for a whole-day artifact stamp:
    ``session_date_of(session_date_to_utc(d)) == d``.
    """
    parts = str(date_text).strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"not an ISO session date: {date_text!r}")
    year, month, day = (int(p) for p in parts)
    return datetime(year, month, day, tzinfo=_CST).astimezone(timezone.utc)


def compute_ranking_artifact_digest(
    rows: tuple[RankingRow, ...], *, model_id: str | None, as_of_date: str
) -> DigestHex:
    """The canonical identity of one ranking READ (source + date + exact rows)."""
    return content_digest(
        {
            "model_id": model_id or "prod",
            "as_of_date": as_of_date,
            "rows": [
                {"code": r.code, "rank": r.rank, "score": r.score} for r in rows
            ],
        }
    )


def _finite_or_none(value: Any) -> float | None:
    # A missing / NaN / unparseable score is an explicit None — never a made-up 0.
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class _ProductionRankingReader:
    """The ratified-D5 reader over ``guanlan_v2.strategy.ranking`` (read-only).

    Both symbols are imported lazily, inside the call: this module must stay
    importable (and cheap) with no pandas, no screen/strategy state and no
    artifact on disk. A missing artifact surfaces as the upstream's own
    ``FileNotFoundError`` — honest passthrough, never a fabricated empty read.
    """

    def read_ranking(
        self, *, variant_id: str | None, as_of_hint: UtcDateTime
    ) -> RankingArtifact:
        from guanlan_v2.strategy.ranking import load_v4_ranking, ranking_date

        model_id = variant_id  # None/"prod" → production v4; else the variant.
        frame = load_v4_ranking(model_id=model_id)
        # NB: a pandas ``Index`` has no truth value — `or ()` would raise.
        raw_columns = getattr(frame, "columns", None)
        columns = () if raw_columns is None else tuple(raw_columns)
        missing = [c for c in (_CODE_COLUMN, _RANK_COLUMN) if c not in columns]
        if missing:
            raise RankingSourceUnavailable(
                f"the ranking artifact for model_id={model_id!r} lacks column(s) "
                f"{missing}; got {list(columns)}"
            )
        date_text = str(ranking_date(model_id=model_id) or "").strip()
        if not date_text:
            raise RankingSourceUnavailable(
                f"the ranking artifact for model_id={model_id!r} carries no "
                "ranking date — refusing rather than stamping it with today's"
            )
        try:
            as_of = session_date_to_utc(date_text)
        except ValueError as exc:
            raise RankingSourceUnavailable(
                f"unreadable ranking date {date_text!r} for model_id={model_id!r}"
            ) from exc

        rows: list[RankingRow] = []
        for index, record in enumerate(frame.to_dict("records")):
            raw_code = record.get(_CODE_COLUMN)
            code = "" if raw_code is None else str(raw_code).strip()
            if not code:
                raise RankingSourceUnavailable(
                    f"ranking row {index} for model_id={model_id!r} carries no code"
                )
            try:
                rank = int(record[_RANK_COLUMN])
            except (KeyError, TypeError, ValueError) as exc:
                raise RankingSourceUnavailable(
                    f"ranking row {index} ({code}) for model_id={model_id!r} "
                    f"carries no usable {_RANK_COLUMN}"
                ) from exc
            rows.append(
                RankingRow(
                    code=code, rank=rank,
                    score=_finite_or_none(record.get(_SCORE_COLUMN)),
                )
            )
        ordered = tuple(sorted(rows, key=lambda r: (r.rank, r.code)))
        return RankingArtifact(
            as_of=as_of,
            artifact_digest=compute_ranking_artifact_digest(
                ordered, model_id=model_id, as_of_date=date_text),
            rows=ordered,
        )


def build_production_ranking_reader() -> RankingReader:
    """The production :class:`RankingReader` bound to the ratified-D5 surface."""
    return _ProductionRankingReader()


# =========================================================================== #
# params (plain frozen dataclasses — invisible to the ContractModel firewall)   #
# =========================================================================== #
def _require_int(value: Any, *, name: str, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CandidateParamsError(
            f"{name} must be an int, got {type(value).__name__} ({value!r})"
        )
    if not low <= value <= high:
        raise CandidateParamsError(f"{name} must be in [{low}, {high}], got {value}")


def _require_text(value: Any, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CandidateParamsError(f"{name} must be a non-blank str, got {value!r}")


@dataclass(frozen=True)
class _CandParams:
    """Shared param plumbing: closed key set, no coercion, node-level override."""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None):
        data = dict(raw or {})
        known = {f.name for f in dataclass_fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise CandidateParamsError(
                f"{cls.__name__} does not accept param(s) {unknown} "
                f"(accepted: {sorted(known)})"
            )
        absent = sorted(
            f.name for f in dataclass_fields(cls)
            if f.name not in data
            and f.default is MISSING and f.default_factory is MISSING
        )
        if absent:
            raise CandidateParamsError(
                f"{cls.__name__} requires param(s) {absent} — no default is invented"
            )
        return cls(**data)

    def as_mapping(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in dataclass_fields(self)}

    def merged_with(self, overrides: Mapping[str, Any] | None):
        """Node params override the factory-bound ones; an unknown/invalid key
        is a typed refusal, never a silently ignored override."""
        if not overrides:
            return self
        return type(self).from_mapping({**self.as_mapping(), **dict(overrides)})


@dataclass(frozen=True)
class CandV4Params(_CandParams):
    """``cand.v4`` params: ``{top_n: 1..50}``."""

    top_n: int

    def __post_init__(self) -> None:
        _require_int(self.top_n, name="top_n", low=MIN_TOP_N, high=MAX_TOP_N)


@dataclass(frozen=True)
class CandLane0Params(_CandParams):
    """``cand.lane0`` params: ``{top_n: 1..50, mainline_limit: 1..5}``.

    Fully validated even though v1 always refuses (invariant 3: the worker ships
    its whole surface so the D4 flip is a one-seam change).
    """

    top_n: int
    mainline_limit: int

    def __post_init__(self) -> None:
        _require_int(self.top_n, name="top_n", low=MIN_TOP_N, high=MAX_TOP_N)
        _require_int(self.mainline_limit, name="mainline_limit",
                     low=MIN_MAINLINE_LIMIT, high=MAX_MAINLINE_LIMIT)


@dataclass(frozen=True)
class CandModelParams(_CandParams):
    """``cand.model`` params: ``{top_n: 1..50, variant_id: str}``."""

    top_n: int
    variant_id: str

    def __post_init__(self) -> None:
        _require_int(self.top_n, name="top_n", low=MIN_TOP_N, high=MAX_TOP_N)
        _require_text(self.variant_id, name="variant_id")


@dataclass(frozen=True)
class CandidatePorts:
    """Everything a candidate worker is allowed to consume.

    ``as_of`` is the run context's as-of (the slate's own stamp). Each worker
    requires only its own ports; an absent one is a typed refusal naming it,
    never a guess. ``rotation_report`` is intentionally duck-typed
    (``RotationReport``-shaped) — the D4 flip must not need a contract change
    here to be testable.
    """

    as_of: datetime
    ranking_reader: RankingReader | None = None
    rotation_report: Any | None = None
    rotation_report_ref: TypedPayloadRef | None = None

    def __post_init__(self) -> None:
        as_of = self.as_of
        if (
            not isinstance(as_of, datetime)
            or as_of.tzinfo is None
            or as_of.utcoffset() is None
        ):
            raise CandidateWorkerError(
                f"CandidatePorts.as_of must be a tz-aware datetime, got {as_of!r}"
            )


# =========================================================================== #
# slate builders                                                               #
# =========================================================================== #
def _read_ranking(ports: CandidatePorts, *, variant_id: str | None) -> RankingArtifact:
    reader = ports.ranking_reader
    if reader is None:
        raise RankingSourceUnavailable(
            "the ranking_reader port is absent — a candidate slate is never "
            "produced without a ranking artifact to read"
        )
    artifact = reader.read_ranking(variant_id=variant_id, as_of_hint=ports.as_of)
    if not isinstance(artifact, RankingArtifact):
        raise RankingSourceUnavailable(
            f"ranking_reader returned {type(artifact).__name__}, "
            "expected a RankingArtifact"
        )
    return artifact


def _slate_from_ranking(
    *,
    source_kind: CandidateSourceKind,   # Task 1's closed vocabulary, not a copy
    variant_id: str | None,
    top_n: int,
    ports: CandidatePorts,
) -> CandidateSlate:
    artifact = _read_ranking(ports, variant_id=variant_id)

    entries: list[CandidateEntry] = []
    unmappable = 0
    # Rank order, ties broken by code so the read is deterministic whatever order
    # the reader hands rows over in.
    for row in sorted(artifact.rows, key=lambda r: (r.rank, r.code)):
        if len(entries) >= top_n:
            break
        try:
            code = normalize_symbol(row.code).code
        except (ValueError, TypeError):
            unmappable += 1  # counted (badge), never re-mapped by fiat
            continue
        entries.append(
            CandidateEntry(
                code=code,
                rank=len(entries) + 1,          # slate rank = position
                score=row.score,
                source_note=f"source_rank={row.rank}",  # the upstream rank kept
            )
        )

    badges: list[str] = []
    artifact_date = session_date_of(artifact.as_of)
    if artifact_date != session_date_of(ports.as_of):
        # Evening artifact consumed by the next session: badge it, keep the run's
        # own as_of, never refuse.
        badges.append(f"{STALE_RANKING_BADGE_PREFIX}{artifact_date}")
    if unmappable:
        badges.append(f"{UNMAPPABLE_CODES_BADGE_PREFIX}{unmappable}")
    if not entries:
        badges.append(EMPTY_SLATE_BADGE)

    # Task 1's validators own rank monotonicity, unique codes, len<=top_n and the
    # source-kind biconditionals — deliberately not re-checked here.
    return CandidateSlate(
        source_kind=source_kind,
        as_of=ports.as_of,
        top_n=top_n,
        entries=tuple(entries),
        source_artifact_digest=artifact.artifact_digest,
        variant_id=variant_id,
        badges=tuple(badges),
    )


def build_v4_slate(*, params: CandV4Params, ports: CandidatePorts) -> CandidateSlate:
    """``cand.v4``: the production v4 ranking's top ``top_n`` (read-only)."""
    return _slate_from_ranking(
        source_kind="v4", variant_id=None, top_n=params.top_n, ports=ports)


def build_model_variant_slate(
    *, params: CandModelParams, ports: CandidatePorts
) -> CandidateSlate:
    """``cand.model``: the same read against a named variant (read-only)."""
    return _slate_from_ranking(
        source_kind="model_variant", variant_id=params.variant_id,
        top_n=params.top_n, ports=ports)


def build_lane0_slate(
    *, params: CandLane0Params, ports: CandidatePorts
) -> CandidateSlate:
    """``cand.lane0`` — the ratified-D4 honest refusal (v1 has no other outcome).

    ``params`` is fully validated by its caller before we get here, and the
    provenance of the read that WOULD have happened is attached to the error.
    This is the single seam that flips to real extraction the day
    ``MainlineRead`` (or the ladder factor point) carries leader stock codes —
    at which point the handoff gate's D4 tripwire reddens and the decision is
    re-reviewed rather than quietly bypassed.
    """
    report = ports.rotation_report
    names = tuple(
        str(getattr(m, "name", "")) for m in getattr(report, "mainlines", ()) or ()
    )
    if report is None:
        raise Lane0LeadersUnavailable(
            "cand.lane0 has no rotation_report port to read "
            f"(top_n={params.top_n}, mainline_limit={params.mainline_limit}); "
            "ratified D4: a leader universe is never invented",
            as_of=ports.as_of,
            rotation_report_ref=ports.rotation_report_ref,
            mainline_names=names,
        )
    raise Lane0LeadersUnavailable(
        "cand.lane0 cannot produce candidates: the rotation read carries no "
        f"leader stock codes — its surface is {_D4_UPSTREAM_SURFACE}, and the "
        "ladder factor point is {date, value, aux}. Ratified D4: refuse honestly "
        "rather than invent a universe from mainline names "
        f"({list(names)}; top_n={params.top_n}, "
        f"mainline_limit={params.mainline_limit}). When upstream grows codes the "
        "handoff gate's D4 tripwire reddens and this seam is re-reviewed.",
        as_of=ports.as_of,
        rotation_report_ref=ports.rotation_report_ref,
        mainline_names=names,
    )


# =========================================================================== #
# executor handlers                                                            #
# =========================================================================== #
def _render(worker_id: str, slate: CandidateSlate) -> str:
    line = (
        f"{worker_id}: {len(slate.entries)} candidate(s) "
        f"as_of {session_date_of(slate.as_of)} (top_n={slate.top_n})"
    )
    if slate.badges:
        line = f"{line} [{', '.join(slate.badges)}]"
    return line


def _bind_handler(*, worker_id, params, ports, build):
    from guanlan_v2.orchestration.worker import ModelResult

    def handler(*, node, input_snapshot, contributions, data_result_refs):
        # ``input_snapshot`` / ``contributions`` / ``data_result_refs`` are
        # deliberately unused: a candidate worker is a pure function of its
        # params and injected ports (zero LLM, zero network, zero prompt).
        resolved = params.merged_with(getattr(node, "params", None))
        slate = build(params=resolved, ports=ports)
        degraded = not slate.entries
        return ModelResult(
            payload=slate,
            rendered_text=_render(worker_id, slate),
            number_anchors=(),
            input_tokens=0,
            output_tokens=0,
            degraded=degraded,
            degradation_reasons=(EMPTY_SLATE_BADGE,) if degraded else (),
        )

    return handler


def cand_v4_handler(*, params: CandV4Params, ports: CandidatePorts):
    """Bind ``cand.v4``'s params + ports → the executor-shaped handler."""
    return _bind_handler(worker_id=CAND_V4_WORKER_ID, params=params, ports=ports,
                         build=build_v4_slate)


def cand_model_handler(*, params: CandModelParams, ports: CandidatePorts):
    """Bind ``cand.model``'s params + ports → the executor-shaped handler."""
    return _bind_handler(worker_id=CAND_MODEL_WORKER_ID, params=params, ports=ports,
                         build=build_model_variant_slate)


def cand_lane0_handler(*, params: CandLane0Params, ports: CandidatePorts):
    """Bind ``cand.lane0``'s params + ports → the executor-shaped handler.

    Invoking it raises :class:`Lane0LeadersUnavailable`, which the executor turns
    into an honest FAILED node (``reason_code="handler_error"``,
    ``error_type="Lane0LeadersUnavailable"``) — never a fabricated slate.
    """
    return _bind_handler(worker_id=CAND_LANE0_WORKER_ID, params=params, ports=ports,
                         build=build_lane0_slate)


def as_handler_factory(handler):
    """Adapt a bound handler to ``TrustedFactoryRegistry.register_handler``.

    The registry calls ``factory(worker=…, resolved=…)`` (worker.py:1730-1731);
    a candidate handler needs neither, so the factory ignores its kwargs — the
    same idiom as ``bootstrap.py:1891``'s ``lambda **_kw: handler``, written
    once here so the ABI lives in one place.
    """

    def factory(**_kw):
        return handler

    return factory
