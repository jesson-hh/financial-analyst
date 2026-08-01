# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — the typed data-source ABI: method specs, requests, raw
fetches, the concrete PIT record/batch/result payload families and the frozen
service-side carriers Task 6 dispatches over.

This module freezes the boundary a data *adapter* lives behind. Nothing here
fetches, opens a store, reads a clock or registers a schema at import time: every
public DTO is a strict, frozen, versioned
:class:`~guanlan_v2.orchestration.digest.DigestModel`; every digest is computed by
a *pure* builder from field values and re-verified on load. The load-bearing
rules (locked by ``tests/orchestration/data/test_source.py``):

* **No absence masquerades as data.** Only ``OK`` / ``DEGRADED`` DataResults carry
  a typed batch (Phase 1 invariant, inherited). A batch is a tuple of *exactly*
  one registered concrete :class:`~guanlan_v2.orchestration.data.result.PitRecord`
  subtype; there is no generic ``RowSet`` / ``list[dict]`` payload.
* **The raw candidate is the only pre-validation envelope.** Task 4's
  :class:`~guanlan_v2.orchestration.data.pit.RawRowCandidate` is reused verbatim; a
  method adapter is the *only* code allowed to turn a PIT-passed candidate into a
  registered record, and its output is registry-validated before a batch/result is
  built.
* **Every identity is a ref, never a path or callable.** Method spec, source
  descriptor, request, raw fetch and the internal carriers carry only
  ``SchemaRef`` / ``ContentRef`` / ``CapabilityRef`` / ``TypedPayloadRef`` values
  and digests. Credentials and physical locators are service-owned and never enter
  a contract.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import AfterValidator, model_validator

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.pit import FreshnessPolicy, RawRowCandidate
from guanlan_v2.orchestration.data.result import DataResult, PitAudit, PitRecord, SourceAttempt
from guanlan_v2.orchestration.data.symbols import LimitRulePolicy, Symbol
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode, DataStatus
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    LogicalId,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_contracts import ExecutionEvidenceOrdinalToken
from guanlan_v2.orchestration.schemas import ToolCallRecord

__all__ = [
    # -- params schemas ---------------------------------------------------- #
    "IsoAwareDateTime",
    "InstrumentUniverseParams",
    "InstrumentSeriesParams",
    # -- concrete PIT records ---------------------------------------------- #
    "InstrumentNameRecord",
    "OHLCVRecord",
    "IndicatorRecord",
    "VerifiedSnapshotRecord",
    "FundamentalRecord",
    "NewsRecord",
    "SignalRecord",
    # -- concrete immutable batches ---------------------------------------- #
    "InstrumentNameRows",
    "OHLCVRows",
    "IndicatorRows",
    "VerifiedSnapshotRows",
    "FundamentalRows",
    "NewsRows",
    "SignalRows",
    # -- named persisted DataResult envelopes ------------------------------ #
    "InstrumentNameDataResult",
    "OHLCVDataResult",
    "IndicatorDataResult",
    "VerifiedSnapshotDataResult",
    "FundamentalDataResult",
    "NewsDataResult",
    "SignalDataResult",
    # -- method spec + request + raw fetch --------------------------------- #
    "DataMethodSpec",
    "DataRequest",
    "RawFetch",
    "build_data_request",
    "build_raw_fetch",
    "build_data_result",
    "DATA_METHOD_SCHEMAS",
    "MethodSchemaBinding",
    # -- service-side protocol + frozen carriers --------------------------- #
    "DataSource",
    "ResolvedMethodRoute",
    "DataSourceDescriptor",
    "DataSourceRegistrySnapshot",
    "DataPolicyResolver",
    "ResolvedDataMethodPolicy",
    "DataInvocationScope",
    "VerifiedDataCacheHit",
    "DataReadOutcome",
    # -- reviewed model partition (Task 5 data-only) ----------------------- #
    "SOURCE_PUBLIC_MODELS",
    "SOURCE_INTERNAL_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64
#: statuses whose DataResult carries a typed batch (Phase 1 data-bearing set).
_DATA_BEARING = frozenset({DataStatus.OK, DataStatus.DEGRADED})


def _iso(dt: datetime | None) -> str:
    """A total, chronological ISO-UTC sort key for an optional aware datetime."""
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat()


def _require_aware_iso(v: str) -> str:
    """Validate an ISO-8601 *aware* datetime string (params stay pure JSON).

    A method's params are strict JSON config (like ``PlanNode.params``), so a
    time boundary is carried as an aware ISO-8601 string, never a datetime
    object. A naive (tz-less) value cannot prove PIT and is rejected.
    """
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 datetime: {v!r}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"naive datetime {v!r} is rejected; a tz-aware value is required")
    return v


# --------------------------------------------------------------------------- #
# Params schemas — validated by a method's exact ``params_schema_ref``          #
# --------------------------------------------------------------------------- #
#: an ISO-8601 aware datetime string — params stay strict JSON config.
IsoAwareDateTime = Annotated[str, AfterValidator(_require_aware_iso)]


class InstrumentUniverseParams(DigestModel):
    """Params for a universe-scoped read (names / snapshot / news).

    ``symbols`` is the exact instrument set; ``as_of`` is an explicit *aware*
    ISO-8601 string (a naive value fails); ``limit`` is a strict non-negative int
    (``bool`` rejected). Extra fields are forbidden by :class:`DigestModel`.
    """

    schema_version: Literal["1"] = "1"
    symbols: tuple[Symbol, ...] = ()
    as_of: IsoAwareDateTime
    limit: NonNegativeInt | None = None


class InstrumentSeriesParams(DigestModel):
    """Params for a per-instrument time-series read (ohlcv / indicator / …).

    ``field_id`` names an optional sub-series and ``min_value`` an optional finite
    numeric filter (``NaN`` / ``Inf`` / ``bool`` rejected by :data:`FiniteFloat`).
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    start: IsoAwareDateTime
    end: IsoAwareDateTime
    field_id: NonEmptyStr | None = None
    min_value: FiniteFloat | None = None

    @model_validator(mode="after")
    def _window(self) -> "InstrumentSeriesParams":
        if datetime.fromisoformat(self.end) < datetime.fromisoformat(self.start):
            raise ValueError("end must not precede start")
        return self


# --------------------------------------------------------------------------- #
# Concrete PIT records — each subclasses Phase-1 PitRecord + adds domain data   #
# --------------------------------------------------------------------------- #
class _DataRow(PitRecord):
    """Private abstract base for concrete data rows (never registered).

    Concrete rows add domain fields and define :meth:`pit_identity` (the duplicate
    key) and :meth:`pit_sort_key` (the canonical order key). The inherited
    ``available_at`` / ``ingested_at`` / ``revision_id`` / ``content_digest`` PIT
    metadata is never replaced by one top-level timestamp.
    """

    def pit_identity(self) -> tuple[str, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    def pit_sort_key(self) -> tuple[str, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "_DataRow":  # pragma: no cover
        raise NotImplementedError


def _row_common(candidate: RawRowCandidate) -> dict[str, Any]:
    return {
        "effective_at": candidate.effective_at,
        "available_at": candidate.available_at,
        "ingested_at": candidate.ingested_at,
        "revision_id": candidate.revision_id,
    }


class InstrumentNameRecord(_DataRow):
    """A verified instrument-name row (symbol → authoritative name)."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    name: NonEmptyStr

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.name)

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.name, self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "InstrumentNameRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), name=p["name"], **_row_common(candidate)
        )


class OHLCVRecord(_DataRow):
    """One instrument's OHLCV bar for a period (``effective_at``)."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat
    volume: NonNegativeInt

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, _iso(self.effective_at))

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, _iso(self.effective_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "OHLCVRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), open=p["open"], high=p["high"],
            low=p["low"], close=p["close"], volume=p["volume"], **_row_common(candidate)
        )


class IndicatorRecord(_DataRow):
    """One computed indicator value for an instrument at a period."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    indicator_id: NonEmptyStr
    value: FiniteFloat

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.indicator_id, _iso(self.effective_at))

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.indicator_id, _iso(self.effective_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "IndicatorRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), indicator_id=p["indicator_id"],
            value=p["value"], **_row_common(candidate)
        )


class VerifiedSnapshotRecord(_DataRow):
    """One verified point-in-time quote snapshot for an instrument."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    last_price: FiniteFloat
    prev_close: FiniteFloat

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, _iso(self.available_at))

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, _iso(self.available_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "VerifiedSnapshotRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), last_price=p["last_price"],
            prev_close=p["prev_close"], **_row_common(candidate)
        )


class FundamentalRecord(_DataRow):
    """One fundamental metric value for an instrument at a reporting period."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    metric_id: NonEmptyStr
    value: FiniteFloat

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.metric_id, _iso(self.effective_at), self.revision_id or "")

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.metric_id, _iso(self.effective_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "FundamentalRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), metric_id=p["metric_id"],
            value=p["value"], **_row_common(candidate)
        )


class NewsRecord(_DataRow):
    """One news item (instrument-scoped or macro when ``symbol`` is ``None``)."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol | None = None
    headline: NonEmptyStr
    url: NonEmptyStr | None = None

    def pit_identity(self) -> tuple[str, ...]:
        return (self.content_digest,)

    def pit_sort_key(self) -> tuple[str, ...]:
        return (_iso(self.available_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "NewsRecord":
        p = candidate.raw_payload
        sym = p.get("symbol")
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**sym) if sym is not None else None,
            headline=p["headline"], url=p.get("url"), **_row_common(candidate)
        )


class SignalRecord(_DataRow):
    """One derived signal value for an instrument at a period."""

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    signal_id: NonEmptyStr
    value: FiniteFloat

    def pit_identity(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.signal_id, _iso(self.effective_at))

    def pit_sort_key(self) -> tuple[str, ...]:
        return (self.symbol.dotted, self.signal_id, _iso(self.effective_at), self.content_digest)

    @classmethod
    def from_candidate(cls, candidate: RawRowCandidate) -> "SignalRecord":
        p = candidate.raw_payload
        return cls.build(  # type: ignore[return-value]
            symbol=Symbol(**p["symbol"]), signal_id=p["signal_id"],
            value=p["value"], **_row_common(candidate)
        )


# --------------------------------------------------------------------------- #
# Concrete immutable batches — a tuple of EXACTLY one matching record subtype    #
# --------------------------------------------------------------------------- #
class _PitBatch(DigestModel):
    """Private base for a concrete PIT batch (never registered).

    A concrete batch declares ``rows: tuple[<Record>, ...]`` and seals
    ``batch_content_digest`` via :meth:`build`, which enforces the method-specific
    canonical sort key and duplicate-identity rule. Strict typing on ``rows`` means
    a batch accepts *only* its matching record subtype.
    """

    schema_version: Literal["1"] = "1"
    batch_content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"batch_content_digest"})

    #: concrete subclasses declare ``rows: tuple[<Record>, ...]``; the base only
    #: provides the shared builder/verifier over that field.

    @classmethod
    def build(cls, rows: Any) -> "_PitBatch":
        ordered = tuple(sorted(rows, key=lambda r: r.pit_sort_key()))
        try:
            digest = cls.digest_of_fields(projection="semantic", rows=ordered)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(rows=ordered, batch_content_digest=digest)


def _verify_pit_batch(batch: "_PitBatch") -> "_PitBatch":
    rows = batch.rows
    keys = [r.pit_sort_key() for r in rows]
    if keys != sorted(keys):
        raise ValueError("batch rows must be canonically sorted by the method sort key")
    ids = [r.pit_identity() for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("batch rows must be duplicate-free by their method identity")
    if batch.batch_content_digest != batch.semantic_digest():
        raise ValueError("declared batch_content_digest does not match canonical digest")
    return batch


class InstrumentNameRows(_PitBatch):
    rows: tuple[InstrumentNameRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "InstrumentNameRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class OHLCVRows(_PitBatch):
    rows: tuple[OHLCVRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "OHLCVRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class IndicatorRows(_PitBatch):
    rows: tuple[IndicatorRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "IndicatorRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class VerifiedSnapshotRows(_PitBatch):
    rows: tuple[VerifiedSnapshotRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "VerifiedSnapshotRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class FundamentalRows(_PitBatch):
    rows: tuple[FundamentalRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "FundamentalRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class NewsRows(_PitBatch):
    rows: tuple[NewsRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "NewsRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


class SignalRows(_PitBatch):
    rows: tuple[SignalRecord, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "SignalRows":
        return _verify_pit_batch(self)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Named persisted DataResult envelopes — closed concrete DataResult subclasses  #
# --------------------------------------------------------------------------- #
# Each envelope narrows ``data`` to exactly its matching concrete batch. The
# subclasses deliberately do NOT use the ``DataResult[Rows]`` runtime
# parametrization syntax: pydantic inserts a parametrized generic alias into the
# *defining module's* namespace (for pickle support), which would leak an
# anonymous ``DataResult[...]`` class into the Phase-1 completeness firewall. A
# runtime-only generic annotation is never used as a persisted schema identity —
# only these named classes are registered.
class InstrumentNameDataResult(DataResult):
    data: InstrumentNameRows | None = None


class OHLCVDataResult(DataResult):
    data: OHLCVRows | None = None


class IndicatorDataResult(DataResult):
    data: IndicatorRows | None = None


class VerifiedSnapshotDataResult(DataResult):
    data: VerifiedSnapshotRows | None = None


class FundamentalDataResult(DataResult):
    data: FundamentalRows | None = None


class NewsDataResult(DataResult):
    data: NewsRows | None = None


class SignalDataResult(DataResult):
    data: SignalRows | None = None


# --------------------------------------------------------------------------- #
# The canonical method → schema binding table                                  #
# --------------------------------------------------------------------------- #
class MethodSchemaBinding:
    """The registered schema family + adapter for one canonical data method.

    A pure, import-time-free descriptor (no digest, no I/O). Names the params /
    record / batch / result classes and the one adapter that turns a PIT-passed
    :class:`RawRowCandidate` into the method's registered record subtype.
    """

    __slots__ = ("method_id", "category", "operation", "params_cls", "record_cls",
                 "batch_cls", "result_cls", "optional")

    def __init__(self, *, method_id, category, operation, params_cls, record_cls,
                 batch_cls, result_cls, optional):
        self.method_id = method_id
        self.category = category
        self.operation = operation
        self.params_cls = params_cls
        self.record_cls = record_cls
        self.batch_cls = batch_cls
        self.result_cls = result_cls
        self.optional = optional

    @property
    def params_schema_ref(self) -> SchemaRef:
        return SchemaRef(name=self.params_cls.__name__, version="1")

    @property
    def batch_schema_ref(self) -> SchemaRef:
        return SchemaRef(name=self.batch_cls.__name__, version="1")

    @property
    def result_schema_ref(self) -> SchemaRef:
        return SchemaRef(name=self.result_cls.__name__, version="1")

    def adapt(self, candidate: RawRowCandidate) -> PitRecord:
        """The ONLY path from a PIT-passed candidate to a registered record."""
        return self.record_cls.from_candidate(candidate)


#: reviewed canonical method table (id → schema family). Ordered for stable golden.
DATA_METHOD_SCHEMAS: tuple[MethodSchemaBinding, ...] = (
    MethodSchemaBinding(method_id="instrument_names", category="reference",
                        operation="reference.instrument_names",
                        params_cls=InstrumentUniverseParams, record_cls=InstrumentNameRecord,
                        batch_cls=InstrumentNameRows, result_cls=InstrumentNameDataResult,
                        optional=False),
    MethodSchemaBinding(method_id="ohlcv", category="prices", operation="prices.ohlcv",
                        params_cls=InstrumentSeriesParams, record_cls=OHLCVRecord,
                        batch_cls=OHLCVRows, result_cls=OHLCVDataResult, optional=False),
    MethodSchemaBinding(method_id="indicators", category="prices", operation="prices.indicators",
                        params_cls=InstrumentSeriesParams, record_cls=IndicatorRecord,
                        batch_cls=IndicatorRows, result_cls=IndicatorDataResult, optional=True),
    MethodSchemaBinding(method_id="verified_snapshot", category="quote",
                        operation="quote.verified_snapshot",
                        params_cls=InstrumentUniverseParams, record_cls=VerifiedSnapshotRecord,
                        batch_cls=VerifiedSnapshotRows, result_cls=VerifiedSnapshotDataResult,
                        optional=False),
    MethodSchemaBinding(method_id="fundamentals", category="fundamentals",
                        operation="fundamentals.metrics",
                        params_cls=InstrumentSeriesParams, record_cls=FundamentalRecord,
                        batch_cls=FundamentalRows, result_cls=FundamentalDataResult, optional=True),
    MethodSchemaBinding(method_id="news", category="news", operation="news.items",
                        params_cls=InstrumentUniverseParams, record_cls=NewsRecord,
                        batch_cls=NewsRows, result_cls=NewsDataResult, optional=True),
    MethodSchemaBinding(method_id="signals", category="signals", operation="signals.values",
                        params_cls=InstrumentSeriesParams, record_cls=SignalRecord,
                        batch_cls=SignalRows, result_cls=SignalDataResult, optional=True),
)

_BINDING_BY_METHOD: dict[str, MethodSchemaBinding] = {b.method_id: b for b in DATA_METHOD_SCHEMAS}
_RESULT_BY_NAME: dict[str, type[DataResult]] = {b.result_cls.__name__: b.result_cls for b in DATA_METHOD_SCHEMAS}
_BATCH_BY_NAME: dict[str, type[_PitBatch]] = {b.batch_cls.__name__: b.batch_cls for b in DATA_METHOD_SCHEMAS}


# --------------------------------------------------------------------------- #
# DataMethodSpec — the versioned, self-sealed reviewed method contract          #
# --------------------------------------------------------------------------- #
class DataMethodSpec(DigestModel):
    """A reviewed, versioned data-method contract sealed by ``spec_digest``.

    Binds the stable id/version/category, the concrete params / batch / named
    result :class:`SchemaRef`\\ s, the optional/core ``optional`` policy, the
    supported modes/backends, ``read_only=True``, the exact data-adapter
    :class:`CapabilityRef`, the versioned freshness-policy :class:`ContentRef` and
    the exact ``renderer_ref``. A caller/model can never select the renderer.
    """

    schema_version: Literal["1"] = "1"
    method_id: LogicalId
    method_version: NonEmptyStr
    category: LogicalId
    params_schema_ref: SchemaRef
    batch_schema_ref: SchemaRef
    result_schema_ref: SchemaRef
    optional: bool
    supported_modes: tuple[DataMode, ...]
    supported_backends: tuple[DataBackend, ...]
    read_only: Literal[True] = True
    capability_ref: CapabilityRef
    freshness_policy_ref: ContentRef
    renderer_ref: ContentRef
    spec_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"spec_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataMethodSpec":
        modes = [m.value for m in self.supported_modes]
        if not modes or len(set(modes)) != len(modes) or modes != sorted(modes):
            raise ValueError("supported_modes must be non-empty, unique and canonically sorted")
        backends = [b.value for b in self.supported_backends]
        if not backends or len(set(backends)) != len(backends) or backends != sorted(backends):
            raise ValueError("supported_backends must be non-empty, unique and canonically sorted")
        if self.spec_digest != self.semantic_digest():
            raise ValueError("declared spec_digest does not match canonical digest")
        return self

    @property
    def method_ref(self) -> ContentRef:
        """The self-consistent method-spec :class:`ContentRef` (id@version + digest)."""
        return ContentRef(id=self.method_id, version=self.method_version,
                          content_digest=self.spec_digest)

    @classmethod
    def build(cls, **fields: Any) -> "DataMethodSpec":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, spec_digest=digest)


# --------------------------------------------------------------------------- #
# DataRequest — audit-neutral request identity, produced only by its builder    #
# --------------------------------------------------------------------------- #
class DataRequest(DigestModel):
    """A frozen data request whose ``request_digest`` is a pure function of the
    data universe it was built against.

    ``request_id`` and the ``data_snapshot_id`` locator are audit-only; every
    other field (method spec ref, validated params + params schema ref, as-of /
    mode / backend and the full semantic digest set) is content. There is no
    caller-owned ``strict_pit``, chain or config override — the pure builder
    :func:`build_data_request` derives them from one :class:`DataContext`.
    """

    schema_version: Literal["1"] = "1"
    request_id: NonEmptyStr
    data_snapshot_id: NonEmptyStr
    method_spec_ref: ContentRef
    params_schema_ref: SchemaRef
    params: dict[str, Any]
    as_of: UtcDateTime
    mode: DataMode
    backend: DataBackend
    data_snapshot_content_digest: DigestHex
    vintage_manifest_digest: DigestHex | None = None
    source_config_digest: DigestHex
    source_registry_digest: DigestHex
    routing_snapshot_digest: DigestHex
    request_context_digest: DigestHex
    schema_registry_digest: DigestHex
    resolved_chain_digest: DigestHex
    request_digest: DigestHex

    #: request_id + snapshot locator are audit-only (excluded from request_digest).
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"request_id", "data_snapshot_id"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"request_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataRequest":
        _require_json_shape(self.params, "params")
        if self.request_digest != self.semantic_digest():
            raise ValueError("declared request_digest does not match canonical digest")
        return self


def _require_json_shape(value: Any, field: str) -> None:
    """Reject any non-JSON-native value inside a strict params mapping."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} float must be finite (NaN/Inf rejected)")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{field} dict keys must be strings")
            _require_json_shape(v, field)
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            _require_json_shape(v, field)
        return
    raise ValueError(f"{field} must be strict JSON-shaped config; got {type(value).__name__}")


def build_data_request(
    ctx: DataContext,
    *,
    method_spec: DataMethodSpec,
    params: dict[str, Any],
    registry: Any,
    request_id: str,
) -> DataRequest:
    """The sole pure builder for a :class:`DataRequest`.

    Validates ``params`` against ``method_spec.params_schema_ref`` through the
    supplied sealed registry, derives every semantic field from ``ctx`` (no caller
    override), computes the resolved-chain digest from ``ctx.resolved_vendor_chains``
    and seals ``request_digest``. Reversed JSON key order is stable; a
    params/context/mode/snapshot/routing/config/registry change alters the digest.
    """
    registry.validate_payload(method_spec.params_schema_ref, params)
    fields = dict(
        request_id=request_id,
        data_snapshot_id=ctx.data_snapshot_id,
        method_spec_ref=method_spec.method_ref,
        params_schema_ref=method_spec.params_schema_ref,
        params=params,
        as_of=ctx.as_of,
        mode=ctx.mode,
        backend=ctx.backend,
        data_snapshot_content_digest=ctx.data_snapshot_content_digest,
        vintage_manifest_digest=ctx.vintage_manifest_digest,
        source_config_digest=ctx.source_config_digest,
        source_registry_digest=ctx.source_registry_digest,
        routing_snapshot_digest=ctx.routing_snapshot_digest,
        request_context_digest=content_digest(ctx),
        schema_registry_digest=registry.registry_digest,
        resolved_chain_digest=content_digest(ctx.resolved_vendor_chains),
    )
    try:
        digest = DataRequest.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return DataRequest(**fields, request_digest=digest)


# --------------------------------------------------------------------------- #
# RawFetch — the untrusted adapter output (candidate tuple + audit timing)      #
# --------------------------------------------------------------------------- #
class RawFetch(DigestModel):
    """One source adapter's raw output for a request (never consumable data).

    Binds the exact ``request_digest``, the resolved ``source_ref`` /
    ``capability_ref``, the immutable candidate tuple and the ``subsource``
    identity, plus audit-only ``fetched_at`` / ``provider_request_id`` timing. The
    ``fetch_content_digest`` (semantic) and ``fetch_audit_digest`` (audit) are
    builder-computed; source / capability / request / candidate content and order
    are semantic, while only the declared timing/provider ids are audit-only.
    """

    schema_version: Literal["1"] = "1"
    request_digest: DigestHex
    source_ref: ContentRef
    capability_ref: CapabilityRef
    candidates: tuple[RawRowCandidate, ...] = ()
    subsource: NonEmptyStr | None = None
    fetched_at: UtcDateTime
    provider_request_id: NonEmptyStr | None = None
    fetch_content_digest: DigestHex
    fetch_audit_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"fetched_at", "provider_request_id"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"fetch_content_digest", "fetch_audit_digest"}
    )

    @model_validator(mode="after")
    def _verify(self) -> "RawFetch":
        if self.fetch_content_digest != self.semantic_digest():
            raise ValueError("declared fetch_content_digest does not match canonical digest")
        if self.fetch_audit_digest != self.audit_digest_value():
            raise ValueError("declared fetch_audit_digest does not match canonical audit digest")
        return self


def build_raw_fetch(**fields: Any) -> RawFetch:
    """Seal a :class:`RawFetch`: compute its semantic + audit digests from fields."""
    try:
        content = RawFetch.digest_of_fields(projection="semantic", **fields)
        audit = RawFetch.digest_of_fields(projection="audit", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        content = audit = _DIGEST_PLACEHOLDER
    return RawFetch(**fields, fetch_content_digest=content, fetch_audit_digest=audit)


# --------------------------------------------------------------------------- #
# build_data_result — the registry-mediated envelope builder                    #
# --------------------------------------------------------------------------- #
def build_data_result(
    method_spec: DataMethodSpec,
    *,
    registry: Any,
    id: str,
    request_digest: str,
    status: DataStatus,
    data: Any = None,
    resolved_vendor_chain: tuple[str, ...],
    source_config_digest: str,
    fetched_at: Any,
    attempts: tuple[SourceAttempt, ...],
    pit_audit: PitAudit,
    **extra: Any,
) -> DataResult:
    """Build the exact named DataResult envelope for ``method_spec`` (registry-checked).

    Resolves ``result_schema_ref`` and ``batch_schema_ref`` from the sealed
    registry, rejects a generic runtime specialization / generic mapping / wrong
    batch type, and calls the envelope's :meth:`DataResult.build`. A data-bearing
    status must carry a batch whose type equals the resolved ``batch_schema_ref``.
    """
    result_cls = registry.resolve(method_spec.result_schema_ref)
    if result_cls not in _RESULT_BY_NAME.values():
        raise ValueError("result_schema_ref does not resolve a registered named DataResult envelope")
    batch_cls = registry.resolve(method_spec.batch_schema_ref)
    if status in _DATA_BEARING:
        if not isinstance(data, batch_cls):
            raise ValueError(
                f"a {status.value} result requires a {batch_cls.__name__} batch; "
                f"got {type(data).__name__}"
            )
    elif data is not None:
        raise ValueError(f"a {status.value} result must not carry a batch")
    return result_cls.build(
        id=id, method=method_spec.method_id, request_digest=request_digest, status=status,
        data=data, resolved_vendor_chain=resolved_vendor_chain,
        source_config_digest=source_config_digest, fetched_at=fetched_at,
        attempts=attempts, pit_audit=pit_audit, **extra,
    )


# --------------------------------------------------------------------------- #
# DataSource — the service-side adapter protocol                                #
# --------------------------------------------------------------------------- #
@runtime_checkable
class DataSource(Protocol):
    """One adapter handler that lives *behind* the Phase 2 ``CapabilityGateway``.

    It exposes no mutable capability set; dispatch never calls it directly (Task 6
    routes every call through the gateway). It receives a validated request + the
    frozen invocation scope and returns an untrusted :class:`RawFetch` — never a
    consumable :class:`DataResult`.
    """

    def fetch(self, request: DataRequest, *, scope: "DataInvocationScope") -> RawFetch:
        ...


# --------------------------------------------------------------------------- #
# ResolvedMethodRoute — an explicit ordered source/capability fallback route     #
# --------------------------------------------------------------------------- #
class ResolvedMethodRoute(DigestModel):
    """A method's resolved, explicitly ordered fallback route + route-policy ref.

    ``entries`` is a non-empty ordered tuple of ``(source_ref, capability_ref)``
    pairs; ``route_policy_ref`` is the versioned reviewed route policy. Registry
    insertion order is never a routing policy — the order here is explicit.
    """

    schema_version: Literal["1"] = "1"
    method_ref: ContentRef
    entries: tuple["RouteEntry", ...]
    route_policy_ref: ContentRef

    @model_validator(mode="after")
    def _verify(self) -> "ResolvedMethodRoute":
        if not self.entries:
            raise ValueError("a resolved method route must have at least one entry")
        return self


class RouteEntry(DigestModel):
    """One ordered ``(source_ref, capability_ref)`` entry of a fallback route."""

    schema_version: Literal["1"] = "1"
    source_ref: ContentRef
    capability_ref: CapabilityRef


ResolvedMethodRoute.model_rebuild()


# --------------------------------------------------------------------------- #
# DataSourceDescriptor + registry snapshot + policy resolver                    #
# --------------------------------------------------------------------------- #
class DataSourceDescriptor(DigestModel):
    """A reviewed source's supported methods, capabilities and trusted handler.

    ``handler_ref`` resolves from the reviewed catalog/material runtime and is a
    trusted ``ContentRef`` — never a caller callable/path. Credentials and physical
    paths remain service-owned and never enter this contract.
    """

    schema_version: Literal["1"] = "1"
    source_id: LogicalId
    source_version: NonEmptyStr
    method_refs: tuple[ContentRef, ...]
    method_capability_refs: tuple[CapabilityRef, ...]
    supported_modes: tuple[DataMode, ...]
    supported_backends: tuple[DataBackend, ...]
    handler_ref: ContentRef
    source_config_schema_ref: SchemaRef
    descriptor_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"descriptor_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataSourceDescriptor":
        modes = [m.value for m in self.supported_modes]
        if not modes or len(set(modes)) != len(modes) or modes != sorted(modes):
            raise ValueError("supported_modes must be non-empty, unique and canonically sorted")
        backends = [b.value for b in self.supported_backends]
        if not backends or len(set(backends)) != len(backends) or backends != sorted(backends):
            raise ValueError("supported_backends must be non-empty, unique and canonically sorted")
        if self.descriptor_digest != self.semantic_digest():
            raise ValueError("declared descriptor_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "DataSourceDescriptor":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, descriptor_digest=digest)


class DataSourceRegistrySnapshot(DigestModel):
    """The immutable, sealed data-source registry snapshot.

    Holds the reviewed method specs, source descriptors, default route policies and
    the exact ``LimitRulePolicy`` / ``FreshnessPolicy`` materials keyed by their
    :class:`ContentRef`. It contains policy *values / digests*, never physical
    files. ``source_registry_digest`` self-seals the whole snapshot.
    """

    schema_version: Literal["1"] = "1"
    registry_version: NonEmptyStr
    method_specs: tuple[DataMethodSpec, ...]
    source_descriptors: tuple[DataSourceDescriptor, ...]
    default_routes: tuple[ResolvedMethodRoute, ...]
    freshness_policies: tuple[FreshnessPolicy, ...] = ()
    limit_policies: tuple[LimitRulePolicy, ...] = ()
    source_registry_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"source_registry_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataSourceRegistrySnapshot":
        m_keys = [(s.method_id, s.method_version) for s in self.method_specs]
        if len(set(m_keys)) != len(m_keys):
            raise ValueError("method_specs must be unique by (method_id, method_version)")
        if m_keys != sorted(m_keys):
            raise ValueError("method_specs must be canonically sorted")
        s_keys = [(d.source_id, d.source_version) for d in self.source_descriptors]
        if len(set(s_keys)) != len(s_keys):
            raise ValueError("source_descriptors must be unique by (source_id, source_version)")
        if s_keys != sorted(s_keys):
            raise ValueError("source_descriptors must be canonically sorted")
        r_keys = [(r.method_ref.id, r.method_ref.version) for r in self.default_routes]
        if len(set(r_keys)) != len(r_keys):
            raise ValueError("default_routes must be unique by method ref")
        if r_keys != sorted(r_keys):
            raise ValueError("default_routes must be canonically sorted")
        if self.source_registry_digest != self.semantic_digest():
            raise ValueError("declared source_registry_digest does not match canonical digest")
        return self

    def method_spec(self, method_id: str) -> DataMethodSpec:
        for s in self.method_specs:
            if s.method_id == method_id:
                return s
        raise KeyError(f"no method spec for {method_id!r}")

    @classmethod
    def build(cls, **fields: Any) -> "DataSourceRegistrySnapshot":
        specs = tuple(sorted(fields.pop("method_specs", ()),
                             key=lambda s: (s.method_id, s.method_version)))
        descs = tuple(sorted(fields.pop("source_descriptors", ()),
                             key=lambda d: (d.source_id, d.source_version)))
        routes = tuple(sorted(fields.pop("default_routes", ()),
                              key=lambda r: (r.method_ref.id, r.method_ref.version)))
        body = dict(method_specs=specs, source_descriptors=descs, default_routes=routes, **fields)
        try:
            digest = cls.digest_of_fields(projection="semantic", **body)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**body, source_registry_digest=digest)


class ResolvedDataMethodPolicy(DigestModel):
    """The single bound policy bundle a :class:`DataPolicyResolver` returns.

    Frozen internal carrier: binds the exact method ref, the versioned
    :class:`FreshnessPolicy`, the optional :class:`LimitRulePolicy`, the resolved
    session-calendar identity (id + material ref, verified through Task 3's
    :class:`~guanlan_v2.orchestration.data.calendar.TradingCalendarResolver`) and a
    self-sealed ``policy_bundle_digest``. Intentionally unregistered.
    """

    schema_version: Literal["1"] = "1"
    method_ref: ContentRef
    freshness_policy: FreshnessPolicy
    limit_policy: LimitRulePolicy | None = None
    calendar_id: NonEmptyStr
    calendar_material_ref: ContentRef
    policy_bundle_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"policy_bundle_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ResolvedDataMethodPolicy":
        if self.policy_bundle_digest != self.semantic_digest():
            raise ValueError("declared policy_bundle_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ResolvedDataMethodPolicy":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, policy_bundle_digest=digest)


class DataPolicyResolver:
    """A read-only resolver over one sealed :class:`DataSourceRegistrySnapshot`.

    :meth:`resolve_method` binds a method spec's exact freshness/limit policy refs
    (verifying material digest + type), resolves the context-matching session
    calendar through the Task 3 :class:`TradingCalendarResolver`, and returns the
    single bound :class:`ResolvedDataMethodPolicy`. Missing, extra, wrong-type or
    drifted material fails here rather than falling back to a module global.

    **Method scoping.** :class:`FreshnessPolicy` carries a ``method_or_category``
    scope, and the model's own contract is that "a policy is selected per
    method/category" (``data/pit.py``). A registered policy whose
    ``method_or_category`` equals the resolved method's id is that method's
    policy and wins over the spec's default ``freshness_policy_ref``; every
    other method keeps the spec's ref. This is what lets ONE method carry a
    stricter/different rule (e.g. session-counted freshness for
    ``verified_snapshot``) **without touching the method spec** — a spec edit
    would move ``spec_digest`` and therefore ``method_ref``, and the sealed
    catalog's prefetch rows cross-resolve on exactly that identity
    (``data/runtime.py::_frozen_route_for``). Category scoping is deliberately
    NOT implemented: only an exact method-id match scopes, so a policy scoped to
    a category name (or to ``"default"``) never silently captures a method.
    """

    __slots__ = ("_snapshot", "_freshness_by_ref", "_freshness_by_scope",
                 "_limit_by_ref", "_calendars")

    def __init__(self, snapshot: DataSourceRegistrySnapshot, *, calendar_resolver: Any) -> None:
        self._snapshot = snapshot
        self._calendars = calendar_resolver
        self._freshness_by_ref: dict[tuple[str, str, str], FreshnessPolicy] = {}
        self._freshness_by_scope: dict[str, FreshnessPolicy] = {}
        for fp in snapshot.freshness_policies:
            key = (fp.policy_id, fp.policy_version, fp.policy_digest)
            if key in self._freshness_by_ref:
                raise ValueError(f"duplicate freshness policy {fp.rule_version}")
            self._freshness_by_ref[key] = fp
            prior_scope = self._freshness_by_scope.get(fp.method_or_category)
            if prior_scope is not None and prior_scope.policy_digest != fp.policy_digest:
                raise ValueError(
                    f"two freshness policies claim the scope {fp.method_or_category!r} "
                    f"({prior_scope.rule_version} and {fp.rule_version}); a scope "
                    "selects exactly one policy and is never resolved by order"
                )
            self._freshness_by_scope[fp.method_or_category] = fp
        self._limit_by_ref: dict[tuple[str, str, str], LimitRulePolicy] = {}
        for lp in snapshot.limit_policies:
            key = (lp.policy_id, lp.policy_version, lp.policy_digest)
            if key in self._limit_by_ref:
                raise ValueError(f"duplicate limit policy {lp.rule_version}")
            self._limit_by_ref[key] = lp

    def resolve_method(self, method_spec: DataMethodSpec, *, ctx: DataContext) -> ResolvedDataMethodPolicy:
        fp = self._resolve_freshness(
            method_spec.freshness_policy_ref, method_id=method_spec.method_id)
        limit = None
        cal_id = ctx.calendar_id
        cal_material_ref: ContentRef
        if fp.is_session_based:
            if fp.calendar_id != ctx.calendar_id:
                raise ValueError(
                    f"freshness policy calendar {fp.calendar_id!r} does not match the "
                    f"context calendar {ctx.calendar_id!r}"
                )
            cal = self._calendars.resolve(fp.calendar_material_ref, calendar_id=fp.calendar_id)
            cal_id = cal.calendar_id
            cal_material_ref = cal.material_ref
        else:
            # elapsed-duration policy: bind the first available limit policy's calendar
            # identity for the bundle (still verified through the resolver).
            if self._limit_by_ref:
                lp = next(iter(self._limit_by_ref.values()))
                cal = self._calendars.resolve(lp.calendar_material_ref, calendar_id=lp.calendar_id)
                cal_id, cal_material_ref, limit = cal.calendar_id, cal.material_ref, lp
            else:
                raise ValueError(
                    "an elapsed-duration method policy still requires a bound session "
                    "calendar; none is registered in the source registry snapshot"
                )
        return ResolvedDataMethodPolicy.build(
            method_ref=method_spec.method_ref, freshness_policy=fp, limit_policy=limit,
            calendar_id=cal_id, calendar_material_ref=cal_material_ref,
        )

    def _resolve_freshness(
        self, ref: ContentRef, *, method_id: str | None = None
    ) -> FreshnessPolicy:
        """The method's bound policy: its own METHOD-SCOPED one, else ``ref``.

        A registered policy whose ``method_or_category`` equals ``method_id`` is
        that method's policy (see the class docstring's method-scoping clause);
        it is registry material like any other, so it is still exact, versioned
        and digest-sealed — there is no unversioned override path. With no
        method-scoped policy the spec's own versioned ``ref`` is resolved, and a
        digest drift or a missing registration is loud.
        """
        if method_id is not None:
            scoped = self._freshness_by_scope.get(method_id)
            if scoped is not None:
                return scoped
        for fp in self._snapshot.freshness_policies:
            if (fp.policy_id, fp.policy_version) == (ref.id, ref.version):
                if fp.policy_digest != ref.content_digest:
                    raise ValueError(
                        f"freshness policy digest drift for {ref.id}@{ref.version}"
                    )
                return fp
        raise ValueError(f"no freshness policy registered for {ref.id}@{ref.version}")


# --------------------------------------------------------------------------- #
# Frozen dispatch carriers (Task 6 consumes; never a second public schema)      #
# --------------------------------------------------------------------------- #
class DataInvocationScope(DigestModel):
    """The frozen authorization/policy scope one data read executes within.

    Preserves the plan/node/worker identity, the Phase-2 pre-issued operation token
    plus its distinct per-route attempt tokens, the frozen route, the invocation
    mode and the catalog/schema-registry digests. Phase 3 may validate/echo but
    never mints, reuses or renumbers a token. Intentionally unregistered.
    """

    schema_version: Literal["1"] = "1"
    plan_digest: DigestHex
    node_id: LogicalId
    worker_id: LogicalId
    operation_token: ExecutionEvidenceOrdinalToken
    attempt_tokens: tuple[ExecutionEvidenceOrdinalToken, ...]
    frozen_route: ResolvedMethodRoute
    invocation_mode: Literal["cache_or_invoke", "always_invoke"]
    catalog_digest: DigestHex
    schema_registry_digest: DigestHex

    @model_validator(mode="after")
    def _verify(self) -> "DataInvocationScope":
        # one distinct attempt token per possible route entry.
        if len(self.attempt_tokens) != len(self.frozen_route.entries):
            raise ValueError(
                "attempt_tokens must have exactly one distinct token per route entry"
            )
        keys = [(t.attempt, t.call_ordinal, t.evidence_ordinal) for t in self.attempt_tokens]
        if len(set(keys)) != len(keys):
            raise ValueError("attempt_tokens must be distinct")
        return self


class VerifiedDataCacheHit(DigestModel):
    """A cache hit that preserves the exact typed result identity for provenance.

    Carries the resolved :class:`DataResult` and its main ``result_ref``
    (:class:`TypedPayloadRef`) so a hit returns the same typed identity a fresh read
    would, not a detached object. Intentionally unregistered.
    """

    schema_version: Literal["1"] = "1"
    result: Any
    result_ref: TypedPayloadRef

    @model_validator(mode="after")
    def _verify(self) -> "VerifiedDataCacheHit":
        if not isinstance(self.result, DataResult):
            raise ValueError("VerifiedDataCacheHit.result must be a DataResult")
        if self.result_ref.payload_ref.namespace != "main":
            raise ValueError("VerifiedDataCacheHit.result_ref must be main-namespace")
        return self


class DataReadOutcome(DigestModel):
    """The terminal outcome of one data read (cache hit or a finalized call).

    Binds the consumed operation/result tokens, the typed result + its main
    ``request_ref`` / ``result_ref`` and the zero-or-one ``tool_call_record`` per
    read. Intentionally unregistered.
    """

    schema_version: Literal["1"] = "1"
    operation_token: ExecutionEvidenceOrdinalToken
    result_token: ExecutionEvidenceOrdinalToken
    result: Any
    request_ref: TypedPayloadRef
    result_ref: TypedPayloadRef
    tool_call_record: ToolCallRecord | None = None

    @model_validator(mode="after")
    def _verify(self) -> "DataReadOutcome":
        if not isinstance(self.result, DataResult):
            raise ValueError("DataReadOutcome.result must be a DataResult")
        if self.request_ref.payload_ref.namespace != "main":
            raise ValueError("DataReadOutcome.request_ref must be main-namespace")
        if self.result_ref.payload_ref.namespace != "main":
            raise ValueError("DataReadOutcome.result_ref must be main-namespace")
        return self


# --------------------------------------------------------------------------- #
# Reviewed Task-5 data-only model partition (for the Phase-3 completeness gate)  #
# --------------------------------------------------------------------------- #
#: registered payload/ABI models this module contributes to the data registry.
SOURCE_PUBLIC_MODELS: tuple[type, ...] = (
    InstrumentUniverseParams, InstrumentSeriesParams,
    InstrumentNameRecord, OHLCVRecord, IndicatorRecord, VerifiedSnapshotRecord,
    FundamentalRecord, NewsRecord, SignalRecord,
    InstrumentNameRows, OHLCVRows, IndicatorRows, VerifiedSnapshotRows,
    FundamentalRows, NewsRows, SignalRows,
    InstrumentNameDataResult, OHLCVDataResult, IndicatorDataResult,
    VerifiedSnapshotDataResult, FundamentalDataResult, NewsDataResult, SignalDataResult,
    DataMethodSpec, DataRequest, RawFetch,
    ResolvedMethodRoute, RouteEntry, DataSourceDescriptor, DataSourceRegistrySnapshot,
)

_R_INTERNAL_CARRIER = (
    "intentionally unregistered frozen dispatch-carrier value consumed by the Task 6 "
    "registry; it preserves service authorization/policy without a second public schema"
)

#: intentionally-unregistered models this module contributes, each with a reason.
SOURCE_INTERNAL_MODELS: dict[type, str] = {
    ResolvedDataMethodPolicy: _R_INTERNAL_CARRIER,
    DataInvocationScope: _R_INTERNAL_CARRIER,
    VerifiedDataCacheHit: _R_INTERNAL_CARRIER,
    DataReadOutcome: _R_INTERNAL_CARRIER,
}
