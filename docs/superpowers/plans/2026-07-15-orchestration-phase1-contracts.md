# Orchestration Phase 1 · 契约冻结与迁移表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the versioned, typed, digest-stable data contracts for `guanlan_v2/orchestration/` (Phase 1 of the编排框架 spec) plus reversible legacy-schema migration adapters — pure offline models with zero runtime behavior.

**Architecture:** Pydantic v2 models organized by responsibility (`digest`/`enums`/`schema_registry`/`data`/`schemas`/`context`/`events`/`spec`/`trials`/`migration`). Every semantic model carries a `schema_version` and a deterministic `content_digest` computed over canonical JSON that **excludes** volatile audit fields (random ids, wall-clock, sequence numbers). Legacy enums (rating/action/confidence/sentiment/rotation-stage) convert through versioned, round-trip-reversible adapters that preserve raw values. No scheduler, no I/O, no LLM, no change to `workflow/executor.run_graph`.

**Tech Stack:** Python ≥3.11, Pydantic v2 (`pydantic.BaseModel`, `field_validator`, `model_validator`, `ConfigDict`), `hashlib.sha256`, `pytest`. All modules use `from __future__ import annotations`.

## Global Constraints

Copied verbatim from the spec (`docs/superpowers/specs/2026-07-15-orchestration-framework-design.md`). Every task's requirements implicitly include this section.

- Every semantic model has `schema_version: str` (default `"1"` unless spec states otherwise — `PlanDraft`/`Artifact` are `"2"`).
- Digests use versioned canonical JSON: keys sorted, times UTC and timezone-aware, floats finite with fixed repr; **random ids, wall-clock timestamps, and event/journal seq numbers never enter a semantic `content_digest`** — they belong to a separate `audit_digest`.
- Each schema explicitly declares its semantic fields vs audit fields (via `SEMANTIC_EXCLUDE`).
- All datetimes are timezone-aware (UTC). A naive datetime is a validation error.
- Reversible migration: legacy rating (-10..10 / 五档), action (case-variant), confidence (0..100 / enum), sentiment (-1..1 / 0..10), rotation stage convert via versioned reversible adapters that preserve the raw value; **no silent coercion**.
- `DataResult` `OK`/`DEGRADED` must carry typed `data`; all other statuses must not carry consumable data; `DEGRADED` requires `coverage` + `degradation_reason`.
- `RegimeReport` three probability axes each normalize to `1 ± 1e-8` over only their own axis labels.
- `TargetPortfolioIntent` is runtime-only, always `origin=LLM` / `authority=ADVISORY_ONLY` / `execution_scope=SHADOW_ONLY`; v1 A-share long-only requires `abs(sum(target_weight)+cash_weight-1) <= 1e-8`, no silent normalization.
- PIT: multi-row / multi-vintage data items subclass `PitRecord` and each carry `available_at`.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests with `pytest` from repo root `G:\guanlan-v2`.

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/__init__.py` | package marker (empty) |
| `guanlan_v2/orchestration/digest.py` | canonical JSON + `content_digest` / `audit_digest` helpers |
| `guanlan_v2/orchestration/enums.py` | all shared enums |
| `guanlan_v2/orchestration/schema_registry.py` | `SchemaRegistry`: `"Type@version"` → model; validate payloads |
| `guanlan_v2/orchestration/data/__init__.py` | package marker (empty) |
| `guanlan_v2/orchestration/data/symbols.py` | `Symbol` / `InstrumentMeta` / `LimitRule` value objects |
| `guanlan_v2/orchestration/data/result.py` | `SourceAttempt` / `PitAudit` / `PitRecord` / `DataResult[T]` |
| `guanlan_v2/orchestration/schemas.py` | artifact/provenance + business/market/regime/shadow payloads |
| `guanlan_v2/orchestration/context.py` | `ClockSpec` / `DataContext` / `RunBudget` / `BudgetReservation` / `RunContext` / snapshots |
| `guanlan_v2/orchestration/events.py` | `RunEvent` / `EventCursor` / `LayerCommit` / `PlanApproval` |
| `guanlan_v2/orchestration/spec.py` | `OrchestrationRequest` / `WorkerSpec` / `PlanNode` / `Dependency` / `PlanDraft` / `Plan` |
| `guanlan_v2/orchestration/trials.py` | `StudySpec` / `TrialRecord` / holdout / sealed contracts |
| `guanlan_v2/orchestration/migration.py` | reversible legacy-schema adapters |
| `tests/orchestration/` | one test module per source module |

---

## Task 1: Canonical digest utility

**Files:**
- Create: `guanlan_v2/orchestration/__init__.py` (empty)
- Create: `guanlan_v2/orchestration/digest.py`
- Test: `tests/orchestration/__init__.py` (empty), `tests/orchestration/test_digest.py`

**Interfaces:**
- Produces:
  - `canonical_json(data: Any) -> str` — deterministic JSON: keys sorted, UTC ISO-8601 for datetimes, `repr`-stable floats, no whitespace.
  - `content_digest(data: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> str` — sha256 hex over `canonical_json` of `data` minus top-level `exclude` keys.
  - `DigestModel(BaseModel)` — base class: `SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset()`; method `semantic_digest() -> str` = `content_digest(self.model_dump(mode="json"), exclude=self.SEMANTIC_EXCLUDE)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_digest.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import ClassVar
from guanlan_v2.orchestration.digest import canonical_json, content_digest, DigestModel


def test_canonical_json_is_key_order_independent():
    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"a": 2, "nested": {"x": 2, "y": 1}, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_serializes_utc_datetime_stably():
    dt = datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc)
    assert canonical_json({"t": dt}) == '{"t":"2026-07-15T09:30:00+00:00"}'


def test_content_digest_excludes_named_fields():
    base = {"value": 1, "event_id": "x"}
    other = {"value": 1, "event_id": "y"}
    assert content_digest(base, exclude=["event_id"]) == content_digest(other, exclude=["event_id"])
    assert content_digest(base) != content_digest(other)


def test_digest_model_excludes_audit_fields():
    class M(DigestModel):
        value: int
        event_id: str
        SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"event_id"})

    assert M(value=1, event_id="a").semantic_digest() == M(value=1, event_id="b").semantic_digest()
    assert M(value=1, event_id="a").semantic_digest() != M(value=2, event_id="a").semantic_digest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guanlan_v2.orchestration'`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/digest.py
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ValueError(f"naive datetime not allowed in digest: {obj!r}")
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"not digest-serializable: {type(obj).__name__}")


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=_default)


def content_digest(data: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> str:
    ex = set(exclude)
    filtered = {k: v for k, v in data.items() if k not in ex}
    return hashlib.sha256(canonical_json(filtered).encode("utf-8")).hexdigest()


class DigestModel(BaseModel):
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset()

    def semantic_digest(self) -> str:
        return content_digest(self.model_dump(mode="json"), exclude=self.SEMANTIC_EXCLUDE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_digest.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/__init__.py guanlan_v2/orchestration/digest.py tests/orchestration/__init__.py tests/orchestration/test_digest.py
git commit -m "feat(orchestration): canonical JSON + content_digest util (phase1)"
```

---

## Task 2: Shared enums

**Files:**
- Create: `guanlan_v2/orchestration/enums.py`
- Test: `tests/orchestration/test_enums.py`

**Interfaces:**
- Produces (all `str, Enum`): `PortfolioRating`, `SentimentBand`, `Tier`, `Confidence`, `ExecutionKind`, `ToolCallRequirement`, `NodeStatus`, `ExperimentStatus`, `DependencyPolicy`, `PlanSource`, `ApprovalPolicy`, `DataStatus`, `DataMode`, `DataBackend`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_enums.py
from __future__ import annotations
from guanlan_v2.orchestration import enums as e


def test_portfolio_rating_five_values():
    assert [r.value for r in e.PortfolioRating] == ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def test_node_status_has_all_lifecycle_states():
    got = {s.value for s in e.NodeStatus}
    assert got == {"pending", "ready", "running", "completed", "degraded",
                   "incomplete", "failed", "blocked", "skipped", "cancelled"}


def test_data_mode_values():
    assert {m.value for m in e.DataMode} == {"online", "pit_replay"}


def test_enums_are_str_backed():
    assert e.Confidence.LOW == "low"
    assert e.DataStatus.OK == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_enums.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/enums.py
from __future__ import annotations
from enum import Enum


class PortfolioRating(str, Enum):
    BUY = "Buy"; OVERWEIGHT = "Overweight"; HOLD = "Hold"
    UNDERWEIGHT = "Underweight"; SELL = "Sell"


class SentimentBand(str, Enum):
    BULLISH = "Bullish"; MILDLY_BULLISH = "Mildly Bullish"; NEUTRAL = "Neutral"
    MIXED = "Mixed"; MILDLY_BEARISH = "Mildly Bearish"; BEARISH = "Bearish"


class Tier(str, Enum):
    READER = "reader"; CRITIC = "critic"; WRITER = "writer"


class Confidence(str, Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"


class ExecutionKind(str, Enum):
    LLM = "llm"; DETERMINISTIC = "deterministic"


class ToolCallRequirement(str, Enum):
    FORBIDDEN = "forbidden"; OPTIONAL = "optional"; REQUIRED = "required"


class NodeStatus(str, Enum):
    PENDING = "pending"; READY = "ready"; RUNNING = "running"; COMPLETED = "completed"
    DEGRADED = "degraded"; INCOMPLETE = "incomplete"; FAILED = "failed"
    BLOCKED = "blocked"; SKIPPED = "skipped"; CANCELLED = "cancelled"


class ExperimentStatus(str, Enum):
    RUNNING = "running"; WAITING_FOR_MATURITY = "waiting_for_maturity"
    PASSED_VALIDATION = "passed_validation"; SEALED_EVALUATING = "sealed_evaluating"
    COMPLETED = "completed"; FAILED = "failed"


class DependencyPolicy(str, Enum):
    BLOCK = "block"; DEGRADE = "degrade"; SKIP = "skip"


class PlanSource(str, Enum):
    BOOTSTRAP = "bootstrap"; DYNAMIC = "dynamic"; PRESET = "preset"; PRESET_FALLBACK = "preset_fallback"


class ApprovalPolicy(str, Enum):
    REQUIRED = "required"; AUTO = "auto"


class DataStatus(str, Enum):
    OK = "ok"; NO_DATA = "no_data"; STALE = "stale"; UNAVAILABLE = "unavailable"; DEGRADED = "degraded"


class DataMode(str, Enum):
    ONLINE = "online"; PIT_REPLAY = "pit_replay"


class DataBackend(str, Enum):
    LIVE = "live"; PIT_STORE = "pit_store"; CACHE = "cache"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_enums.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/enums.py tests/orchestration/test_enums.py
git commit -m "feat(orchestration): shared enums (phase1)"
```

---

## Task 3: Schema/version registry

**Files:**
- Create: `guanlan_v2/orchestration/schema_registry.py`
- Test: `tests/orchestration/test_schema_registry.py`

**Interfaces:**
- Consumes: `pydantic.BaseModel`.
- Produces: `SchemaRegistry` with `register(name: str, version: str, model: type[BaseModel]) -> None`, `resolve(name: str, version: str) -> type[BaseModel]`, `validate_payload(name: str, version: str, payload: dict) -> BaseModel`, `key(name, version) -> str` returning `"name@version"`. Unknown key raises `KeyError`; double-registration of the same key with a different model raises `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_schema_registry.py
from __future__ import annotations
import pytest
from pydantic import BaseModel
from guanlan_v2.orchestration.schema_registry import SchemaRegistry


class _P(BaseModel):
    x: int


def test_register_and_resolve():
    r = SchemaRegistry()
    r.register("Foo", "1", _P)
    assert r.resolve("Foo", "1") is _P
    assert r.key("Foo", "1") == "Foo@1"


def test_resolve_unknown_raises():
    r = SchemaRegistry()
    with pytest.raises(KeyError):
        r.resolve("Nope", "1")


def test_validate_payload_returns_model():
    r = SchemaRegistry()
    r.register("Foo", "1", _P)
    obj = r.validate_payload("Foo", "1", {"x": 3})
    assert isinstance(obj, _P) and obj.x == 3


def test_conflicting_registration_raises():
    r = SchemaRegistry()
    r.register("Foo", "1", _P)

    class _Q(BaseModel):
        y: int

    with pytest.raises(ValueError):
        r.register("Foo", "1", _Q)
    r.register("Foo", "1", _P)  # idempotent same model is allowed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_schema_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/schema_registry.py
from __future__ import annotations
from pydantic import BaseModel


class SchemaRegistry:
    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    @staticmethod
    def key(name: str, version: str) -> str:
        return f"{name}@{version}"

    def register(self, name: str, version: str, model: type[BaseModel]) -> None:
        k = self.key(name, version)
        existing = self._models.get(k)
        if existing is not None and existing is not model:
            raise ValueError(f"schema {k} already registered to {existing.__name__}")
        self._models[k] = model

    def resolve(self, name: str, version: str) -> type[BaseModel]:
        return self._models[self.key(name, version)]

    def validate_payload(self, name: str, version: str, payload: dict) -> BaseModel:
        return self.resolve(name, version).model_validate(payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_schema_registry.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/schema_registry.py tests/orchestration/test_schema_registry.py
git commit -m "feat(orchestration): schema/version registry (phase1)"
```

---

## Task 4: Symbol value objects

**Files:**
- Create: `guanlan_v2/orchestration/data/__init__.py` (empty), `guanlan_v2/orchestration/data/symbols.py`
- Test: `tests/orchestration/test_symbols.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces:
  - `Symbol` (frozen `BaseModel`, `model_config = ConfigDict(frozen=True)`): `code: str`, `exchange: str`, `board: str`; props `dotted -> str` (`"600519.SH"`), `engine_code -> str` (`"SH600519"`). `code` must match `^[0-9]{6}$` (validator).
  - `InstrumentMeta(BaseModel)`: `symbol: Symbol`, `is_st: bool | None = None`, `listed_at: datetime | None`, `metadata_available_at: datetime | None`.
  - `LimitRule(BaseModel)`: `pct: float | None`, `reason: str`, `rule_version: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_symbols.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.data.symbols import Symbol, InstrumentMeta, LimitRule


def test_symbol_dotted_and_engine_code():
    s = Symbol(code="600519", exchange="SH", board="main")
    assert s.dotted == "600519.SH"
    assert s.engine_code == "SH600519"


def test_symbol_rejects_non_six_digit_code():
    with pytest.raises(ValueError):
        Symbol(code="60051", exchange="SH", board="main")
    with pytest.raises(ValueError):
        Symbol(code="60051X", exchange="SH", board="main")


def test_symbol_is_frozen():
    s = Symbol(code="600519", exchange="SH", board="main")
    with pytest.raises(Exception):
        s.code = "000001"  # frozen


def test_instrument_meta_is_st_unknown_defaults_none():
    m = InstrumentMeta(symbol=Symbol(code="600519", exchange="SH", board="main"))
    assert m.is_st is None  # cannot infer from code; unknown must be explicit


def test_limit_rule_allows_none_pct():
    r = LimitRule(pct=None, reason="rule unknown", rule_version="v0")
    assert r.pct is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_symbols.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/symbols.py
from __future__ import annotations
import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator

_CODE_RE = re.compile(r"^[0-9]{6}$")


class Symbol(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    exchange: str   # SH | SZ | BJ
    board: str      # main | star | chinext | bj (号段由版本化表维护)

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError(f"code must be 6 digits, got {v!r}")
        return v

    @property
    def dotted(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def engine_code(self) -> str:
        return f"{self.exchange}{self.code}"


class InstrumentMeta(BaseModel):
    symbol: Symbol
    is_st: bool | None = None                  # 不能从代码纯语法推断;unknown 显形
    listed_at: datetime | None = None
    metadata_available_at: datetime | None = None


class LimitRule(BaseModel):
    pct: float | None                          # None=该时点无普通涨跌幅限制/规则未知
    reason: str
    rule_version: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_symbols.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/__init__.py guanlan_v2/orchestration/data/symbols.py tests/orchestration/test_symbols.py
git commit -m "feat(orchestration): Symbol/InstrumentMeta/LimitRule value objects (phase1)"
```

---

## Task 5: Typed DataResult + PIT records

**Files:**
- Create: `guanlan_v2/orchestration/data/result.py`
- Test: `tests/orchestration/test_data_result.py`

**Interfaces:**
- Consumes: `enums.DataStatus`, `enums.DataMode`, `digest.DigestModel`.
- Produces:
  - `SourceAttempt(BaseModel)`, `PitAudit(BaseModel)`, `PitRecord(BaseModel)` (base for all multi-row/vintage items; requires `available_at`, `ingested_at`, `content_digest`; optional `effective_at`, `revision_id`).
  - `DataResult(DigestModel, Generic[T])` with the full field set from spec §8 (`status`, `data: T | None`, `coverage`, `resolved_vendor_chain`, `content_digest`, `audit_digest`, `attempts`, `pit_audit`, …). Model-validator invariant: `OK`/`DEGRADED` ⇒ `data is not None`; other statuses ⇒ `data is None`; `DEGRADED` ⇒ `coverage is not None and degradation_reason`.
  - `SEMANTIC_EXCLUDE` on `DataResult` = `{"id","fetched_at","audit_digest","content_digest"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_data_result.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.enums import DataStatus, DataMode
from guanlan_v2.orchestration.data.result import DataResult, SourceAttempt, PitAudit, PitRecord

UTC = timezone.utc


def _audit():
    return PitAudit(mode=DataMode.ONLINE, as_of=datetime(2026, 7, 15, tzinfo=UTC),
                    rows_seen=1, rows_returned=1, future_rows=0, missing_available_at_rows=0,
                    guard_result="passed")


def _attempt():
    return SourceAttempt(vendor="a_stock", configured=True, outcome="success",
                         started_at=datetime(2026, 7, 15, tzinfo=UTC),
                         finished_at=datetime(2026, 7, 15, tzinfo=UTC))


def _base(**kw):
    defaults = dict(id="d1", method="get_ohlcv", request_digest="rq",
                    resolved_vendor_chain=["a_stock"], source_config_digest="cfg",
                    fetched_at=datetime(2026, 7, 15, tzinfo=UTC), content_digest="c",
                    audit_digest="a", attempts=[_attempt()], pit_audit=_audit())
    defaults.update(kw)
    return defaults


def test_ok_requires_data():
    with pytest.raises(ValueError):
        DataResult[dict](**_base(status=DataStatus.OK, data=None))
    ok = DataResult[dict](**_base(status=DataStatus.OK, data={"rows": 1}))
    assert ok.status == DataStatus.OK


def test_no_data_must_not_carry_data():
    with pytest.raises(ValueError):
        DataResult[dict](**_base(status=DataStatus.NO_DATA, data={"rows": 1}))


def test_degraded_requires_coverage_and_reason():
    with pytest.raises(ValueError):
        DataResult[dict](**_base(status=DataStatus.DEGRADED, data={"rows": 1}))
    ok = DataResult[dict](**_base(status=DataStatus.DEGRADED, data={"rows": 1},
                                  coverage=0.5, degradation_reason="partial"))
    assert ok.coverage == 0.5


def test_semantic_digest_ignores_volatile_fields():
    a = DataResult[dict](**_base(status=DataStatus.OK, data={"rows": 1}, id="x", audit_digest="a1"))
    b = DataResult[dict](**_base(status=DataStatus.OK, data={"rows": 1}, id="y", audit_digest="a2"))
    assert a.semantic_digest() == b.semantic_digest()


def test_pit_record_requires_available_at():
    with pytest.raises(ValueError):
        PitRecord(ingested_at=datetime(2026, 7, 15, tzinfo=UTC), content_digest="c")  # missing available_at
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_data_result.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/result.py
from __future__ import annotations
from datetime import datetime
from typing import Any, ClassVar, Generic, Literal, TypeVar
from pydantic import Field, model_validator
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import DataMode, DataStatus

T = TypeVar("T")


class SourceAttempt(DigestModel):
    vendor: str
    subsource: str | None = None
    configured: bool
    outcome: Literal["success", "no_data", "stale", "rate_limited", "not_configured",
                     "future_refused", "missing_availability", "error"]
    fallback_reason: str | None = None
    started_at: datetime
    finished_at: datetime


class PitAudit(DigestModel):
    mode: DataMode
    as_of: datetime
    rows_seen: int
    rows_returned: int
    future_rows: int
    missing_available_at_rows: int
    guard_result: Literal["passed", "filtered", "refused"]
    latest_available_at: datetime | None = None


class PitRecord(DigestModel):
    effective_at: datetime | None = None
    available_at: datetime
    ingested_at: datetime
    revision_id: str | None = None
    content_digest: str


class DataResult(DigestModel, Generic[T]):
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"id", "fetched_at", "audit_digest", "content_digest"})
    schema_version: str = "1"
    id: str
    method: str
    request_digest: str
    status: DataStatus
    data: T | None = None
    coverage: float | None = Field(default=None, ge=0, le=1)
    degradation_reason: str | None = None
    vendor: str | None = None
    subsource: str | None = None
    resolved_vendor_chain: list[str]
    source_config_digest: str
    effective_at: datetime | None = None
    available_at: datetime | None = None
    ingested_at: datetime | None = None
    fetched_at: datetime
    revision_id: str | None = None
    content_digest: str
    audit_digest: str
    row_time_metadata_digest: str | None = None
    attempts: list[SourceAttempt]
    pit_audit: PitAudit
    warnings: list[str] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_data_invariants(self) -> "DataResult[T]":
        has_data = self.data is not None
        if self.status in (DataStatus.OK, DataStatus.DEGRADED):
            if not has_data:
                raise ValueError(f"status={self.status} requires data")
        elif has_data:
            raise ValueError(f"status={self.status} must not carry data")
        if self.status == DataStatus.DEGRADED:
            if self.coverage is None or not self.degradation_reason:
                raise ValueError("DEGRADED requires coverage and degradation_reason")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_data_result.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/result.py tests/orchestration/test_data_result.py
git commit -m "feat(orchestration): typed DataResult + PIT records (phase1)"
```

---

## Task 6: Artifact / provenance / number-anchor / NodeRun

**Files:**
- Create: `guanlan_v2/orchestration/schemas.py`
- Test: `tests/orchestration/test_artifact.py`

**Interfaces:**
- Consumes: `enums.NodeStatus`, `enums.DataMode`, `digest.DigestModel`, `digest.content_digest`.
- Produces: `ArtifactRef`, `ToolCallRecord`, `Provenance`, `NumberAnchor`, `Artifact`, `ArtifactRelation`, `NodeRun` (all from spec §8). Invariants: `NumberAnchor.is_unsourced` property True iff both `source_artifact_id` and `source_data_result_id` are None; `Artifact.SEMANTIC_EXCLUDE = {"id","created_at","content_digest","provenance"}` (provenance is audit).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_artifact.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.schemas import NumberAnchor, Artifact, Provenance
from guanlan_v2.orchestration.enums import DataMode

UTC = timezone.utc


def _prov(**kw):
    d = dict(run_id="r", plan_id="p", plan_digest="pd", node_id="n",
             as_of=datetime(2026, 7, 15, tzinfo=UTC), pit_mode=DataMode.ONLINE, code_version="v1")
    d.update(kw)
    return Provenance(**d)


def test_number_anchor_unsourced_when_both_ids_none():
    a = NumberAnchor(label="市值", value=1.0, payload_path="$.mv")
    assert a.is_unsourced is True
    b = NumberAnchor(label="市值", value=1.0, payload_path="$.mv", source_artifact_id="x")
    assert b.is_unsourced is False


def _artifact(**kw):
    d = dict(id="a1", kind="regime_report", slot="regime_report", output_key="primary",
             producer_node_id="market.regime", run_id="r", payload_type="RegimeReport",
             payload_version="1", payload={"x": 1}, rendered_md="# hi",
             provenance=_prov(model_response_id="resp-a"), created_at=datetime(2026, 7, 15, tzinfo=UTC),
             content_digest="c", rendered_from_payload_digest="rp")
    d.update(kw)
    return Artifact(**d)


def test_artifact_semantic_digest_ignores_provenance_and_id():
    a = _artifact(id="x", provenance=_prov(model_response_id="resp-a"))
    b = _artifact(id="y", provenance=_prov(model_response_id="resp-b"))
    assert a.semantic_digest() == b.semantic_digest()
    c = _artifact(payload={"x": 2})
    assert a.semantic_digest() != c.semantic_digest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_artifact.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/schemas.py
from __future__ import annotations
from datetime import datetime
from typing import Any, ClassVar, Literal
from pydantic import Field
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import DataMode, NodeStatus


class ArtifactRef(DigestModel):
    artifact_id: str
    producer_node_id: str
    slot: str
    output_key: str
    kind: str
    content_digest: str
    relation: Literal["input", "citation", "supports", "refutes"]


class ToolCallRecord(DigestModel):
    tool: str
    request_digest: str
    result_digest: str | None = None
    started_at: datetime
    finished_at: datetime
    status: str


class Provenance(DigestModel):
    run_id: str
    plan_id: str
    plan_digest: str
    node_id: str
    as_of: datetime
    pit_mode: DataMode
    code_version: str
    provider: str | None = None
    model: str | None = None
    model_snapshot: str | None = None
    model_response_id: str | None = None
    model_response_digest: str | None = None
    model_config_digest: str | None = None
    sampling_seed: int | None = None
    prompt_digest: str | None = None
    skill_digests: dict[str, str] = Field(default_factory=dict)
    data_result_ids: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    fallback_events: list[str] = Field(default_factory=list)


class NumberAnchor(DigestModel):
    label: str
    value: float
    unit: str | None = None
    as_of: datetime | None = None
    payload_path: str
    source_artifact_id: str | None = None
    source_data_result_id: str | None = None

    @property
    def is_unsourced(self) -> bool:
        return self.source_artifact_id is None and self.source_data_result_id is None


class Artifact(DigestModel):
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"id", "created_at", "content_digest", "provenance"})
    schema_version: str = "2"
    id: str
    kind: str
    slot: str
    output_key: str
    producer_node_id: str
    run_id: str
    payload_type: str
    payload_version: str
    payload: dict[str, Any]
    rendered_md: str
    input_refs: list[ArtifactRef] = Field(default_factory=list)
    provenance: Provenance
    numbers: list[NumberAnchor] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)
    created_at: datetime
    content_digest: str
    rendered_from_payload_digest: str


class ArtifactRelation(DigestModel):
    event_id: str
    relation: Literal["supersedes", "refutes", "approves", "rejects"]
    from_artifact_id: str
    to_artifact_id: str
    created_at: datetime


class NodeRun(DigestModel):
    schema_version: str = "1"
    node_run_id: str
    run_id: str
    plan_id: str
    plan_digest: str
    node_id: str
    worker_id: str
    status: NodeStatus
    reason_code: str | None = None
    reason: str | None = None
    attempt_id: str
    attempt: int = 1
    input_snapshot_hash: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_type: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_artifact.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/schemas.py tests/orchestration/test_artifact.py
git commit -m "feat(orchestration): artifact/provenance/number-anchor/NodeRun (phase1)"
```

---

## Task 7: Business / market / regime / shadow payloads

**Files:**
- Modify: `guanlan_v2/orchestration/schemas.py` (append payload models)
- Test: `tests/orchestration/test_payloads.py`

**Interfaces:**
- Consumes: `enums.PortfolioRating/SentimentBand/Confidence`, `data.symbols.Symbol`.
- Produces: `ResearchPlan`, `PortfolioDecision`, `SentimentReport`, `MarketFactorValue`, `MarketFactorReport`, `RegimeReport`, `RealizedRegime`, `RotationReport`, `TargetPosition`, `PortfolioTargetProposal`, `TargetPortfolioIntent`, `DecisionSchedule`, `DebateMessage`. Invariants enforced by validators:
  - `RegimeReport`: each of `trend_probabilities`/`risk_probabilities`/`heat_probabilities` sums to `1 ± 1e-8`, keys restricted to that axis's labels, values finite.
  - `TargetPortfolioIntent`: fixed literals `origin="LLM"`, `authority="ADVISORY_ONLY"`, `execution_scope="SHADOW_ONLY"`; no duplicate symbol; `abs(sum(target_weight)+cash_weight-1) <= 1e-8`; `decision_as_of < eligible_execution_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_payloads.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.schemas import (
    RegimeReport, TargetPosition, PortfolioTargetProposal, TargetPortfolioIntent)

UTC = timezone.utc


def _regime(**kw):
    d = dict(trend="bull", risk_state="risk_on", heat_state="normal",
             trend_probabilities={"bull": 0.7, "bear": 0.2, "range": 0.1},
             risk_probabilities={"risk_on": 0.6, "risk_off": 0.3, "neutral": 0.1},
             heat_probabilities={"normal": 0.8, "overheat": 0.2},
             confidence_score=0.7, drivers=["北向"], narrative="…")
    d.update(kw)
    return RegimeReport(**d)


def test_regime_axis_probabilities_must_sum_to_one():
    _regime()  # ok
    with pytest.raises(ValueError):
        _regime(trend_probabilities={"bull": 0.5, "bear": 0.2, "range": 0.1})


def test_regime_axis_rejects_foreign_labels():
    with pytest.raises(ValueError):
        _regime(trend_probabilities={"bull": 0.5, "risk_on": 0.5})


def _intent(**kw):
    d = dict(intent_id="i1", target_version=1, proposal_artifact_id="pa", proposal_digest="pd",
             source_decision_artifact_id="sd", decision_schedule_id="s", decision_schedule_version="1",
             decision_schedule_digest="sd1", scheduled_for=datetime(2026, 7, 15, 9, tzinfo=UTC),
             decision_as_of=datetime(2026, 7, 15, 9, tzinfo=UTC),
             eligible_execution_at=datetime(2026, 7, 16, 1, 30, tzinfo=UTC),
             positions=[TargetPosition(symbol=Symbol(code="600519", exchange="SH", board="main"),
                                       target_weight=0.6)],
             cash_weight=0.4, rationale="…", confidence="high",
             created_at=datetime(2026, 7, 15, 9, tzinfo=UTC))
    d.update(kw)
    return TargetPortfolioIntent(**d)


def test_intent_weights_must_sum_to_one():
    _intent()  # 0.6 + 0.4 == 1
    with pytest.raises(ValueError):
        _intent(cash_weight=0.3)  # 0.6 + 0.3 != 1


def test_intent_is_advisory_shadow_only():
    it = _intent()
    assert it.origin == "LLM" and it.authority == "ADVISORY_ONLY" and it.execution_scope == "SHADOW_ONLY"


def test_intent_rejects_execution_after_before_decision():
    with pytest.raises(ValueError):
        _intent(eligible_execution_at=datetime(2026, 7, 15, 8, tzinfo=UTC))  # before decision_as_of
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_payloads.py -v`
Expected: FAIL with `ImportError: cannot import name 'RegimeReport'`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/schemas.py`)

```python
# ── append to guanlan_v2/orchestration/schemas.py ──
from datetime import time as _time, date as _date
from pydantic import Field as _Field, field_validator, model_validator
from guanlan_v2.orchestration.enums import Confidence, PortfolioRating, SentimentBand
from guanlan_v2.orchestration.data.symbols import Symbol

_AXIS_LABELS = {
    "trend": {"bull", "bear", "range", "unknown"},
    "risk": {"risk_on", "risk_off", "neutral", "unknown"},
    "heat": {"normal", "overheat", "unknown"},
}


def _check_axis(name: str, probs: dict[str, float]) -> dict[str, float]:
    allowed = _AXIS_LABELS[name]
    bad = set(probs) - allowed
    if bad:
        raise ValueError(f"{name}_probabilities has foreign labels {bad}")
    total = sum(probs.values())
    if not all(v == v and abs(v) != float("inf") for v in probs.values()):
        raise ValueError(f"{name}_probabilities must be finite")
    if abs(total - 1.0) > 1e-8:
        raise ValueError(f"{name}_probabilities must sum to 1, got {total}")
    return probs


class ResearchPlan(DigestModel):
    recommendation: PortfolioRating
    rationale: str
    strategic_actions: str


class PortfolioDecision(DigestModel):
    rating: PortfolioRating
    executive_summary: str
    investment_thesis: str
    price_target: float | None = None
    time_horizon: str | None = None


class SentimentReport(DigestModel):
    overall_band: SentimentBand
    overall_score: float = _Field(ge=0, le=10)
    confidence: Confidence
    narrative: str


class MarketFactorValue(DigestModel):
    factor_id: str
    definition_version: str
    value: float | None
    params: dict[str, Any]
    universe: str
    frequency: str
    effective_at: datetime
    available_at: datetime
    coverage: float = _Field(ge=0, le=1)
    status: Literal["ok", "unavailable"]
    missing_policy: str
    content_digest: str


class MarketFactorReport(DigestModel):
    as_of: datetime
    values: list[MarketFactorValue]
    data_snapshot_hash: str
    coverage: float = _Field(ge=0, le=1)
    content_digest: str


class RegimeReport(DigestModel):
    trend: Literal["bull", "bear", "range", "unknown"]
    risk_state: Literal["risk_on", "risk_off", "neutral", "unknown"]
    heat_state: Literal["normal", "overheat", "unknown"]
    trend_probabilities: dict[str, float]
    risk_probabilities: dict[str, float]
    heat_probabilities: dict[str, float]
    confidence_score: float = _Field(ge=0, le=1)
    drivers: list[str]
    narrative: str

    @field_validator("trend_probabilities")
    @classmethod
    def _v_trend(cls, v): return _check_axis("trend", v)

    @field_validator("risk_probabilities")
    @classmethod
    def _v_risk(cls, v): return _check_axis("risk", v)

    @field_validator("heat_probabilities")
    @classmethod
    def _v_heat(cls, v): return _check_axis("heat", v)


class RealizedRegime(DigestModel):
    horizon_trading_days: int = _Field(gt=0)
    forward_return: float
    max_drawdown: float
    realized_volatility: float
    realized_trend: Literal["bull", "bear", "range"]
    available_at: datetime
    data_snapshot_hash: str


class RotationReport(DigestModel):
    mainlines: list[str]
    stage: Literal["启动", "扩散", "分化", "退潮", "unknown"]
    strength: float = _Field(ge=0, le=1)
    persistence_days: int | None = None
    narrative: str


class TargetPosition(DigestModel):
    symbol: Symbol
    target_weight: float = _Field(ge=0, le=1)
    stop_loss_pct: float | None = _Field(default=None, gt=0, le=1)
    take_profit_pct: float | None = _Field(default=None, gt=0)
    max_hold_bars: int | None = _Field(default=None, gt=0)


class PortfolioTargetProposal(DigestModel):
    positions: list[TargetPosition]
    cash_weight: float = _Field(ge=0, le=1)
    rationale: str
    confidence: Confidence


class TargetPortfolioIntent(DigestModel):
    intent_id: str
    target_version: int = _Field(gt=0)
    proposal_artifact_id: str
    proposal_digest: str
    source_decision_artifact_id: str
    decision_schedule_id: str
    decision_schedule_version: str
    decision_schedule_digest: str
    scheduled_for: datetime
    decision_as_of: datetime
    eligible_execution_at: datetime
    valid_until: datetime | None = None
    positions: list[TargetPosition]
    cash_weight: float = _Field(ge=0, le=1)
    origin: Literal["LLM"] = "LLM"
    authority: Literal["ADVISORY_ONLY"] = "ADVISORY_ONLY"
    execution_scope: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"
    rationale: str
    confidence: Confidence
    created_at: datetime

    @model_validator(mode="after")
    def _v_intent(self) -> "TargetPortfolioIntent":
        codes = [p.symbol.code for p in self.positions]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate symbol in positions")
        total = sum(p.target_weight for p in self.positions) + self.cash_weight
        if abs(total - 1.0) > 1e-8:
            raise ValueError(f"weights must sum to 1, got {total}")
        if not self.decision_as_of < self.eligible_execution_at:
            raise ValueError("decision_as_of must be < eligible_execution_at")
        if self.valid_until is not None and not self.eligible_execution_at <= self.valid_until:
            raise ValueError("eligible_execution_at must be <= valid_until")
        return self


class DecisionSchedule(DigestModel):
    id: str
    version: str
    calendar_id: str
    timezone: str
    kind: Literal["daily", "weekly", "rebalance_dates", "manual"]
    decision_local_time: _time
    cutoff_local_time: _time
    bar_frequency: Literal["1d", "60m", "30m", "15m", "5m", "1m"]
    execution_policy: Literal["next_open", "next_bar_close"]
    execution_price_field: Literal["open", "close"]
    matching_engine_version: str
    weekdays: list[int] = _Field(default_factory=list)
    rebalance_dates: list[_date] = _Field(default_factory=list)
    intrabar_exit_priority: Literal["worst_case", "stop_first", "take_profit_first"] = "worst_case"
    content_digest: str

    @model_validator(mode="after")
    def _v_policy_pairing(self) -> "DecisionSchedule":
        pairs = {("next_open", "open"), ("next_bar_close", "close")}
        if (self.execution_policy, self.execution_price_field) not in pairs:
            raise ValueError("execution_policy must pair with price field (next_open↔open, next_bar_close↔close)")
        return self


class DebateMessage(DigestModel):
    debate_id: str
    round: int
    turn: int
    role: str
    artifact_id: str
    created_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_payloads.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/schemas.py tests/orchestration/test_payloads.py
git commit -m "feat(orchestration): business/market/regime/shadow payloads with invariants (phase1)"
```

---

## Task 8: Context, budget, clock

**Files:**
- Create: `guanlan_v2/orchestration/context.py`
- Test: `tests/orchestration/test_context.py`

**Interfaces:**
- Consumes: `enums.DataMode/DataBackend`, `digest.DigestModel`.
- Produces: `ClockSpec`, `DataContext` (+ `model_validator`), `RunBudget`, `BudgetReservation`, `RunContext`, `ContextSnapshot`, `MemoryRecordRef`, `InputSnapshot`. `DataContext` validator: top-level `as_of`/`calendar_id` must equal `clock.as_of`/`clock.calendar_id`; all datetimes tz-aware; if `mode==PIT_REPLAY` then `strict_pit is True` and `data_snapshot_id` and `vintage_manifest_digest` are non-empty. (`ArtifactRef` imported from `schemas`.)

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_context.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.enums import DataMode, DataBackend
from guanlan_v2.orchestration.context import ClockSpec, DataContext

UTC = timezone.utc


def _clock(**kw):
    d = dict(as_of=datetime(2026, 7, 15, tzinfo=UTC), timezone="Asia/Shanghai",
             calendar_id="XSHG", clock_version="1")
    d.update(kw)
    return ClockSpec(**d)


def _ctx(**kw):
    d = dict(as_of=datetime(2026, 7, 15, tzinfo=UTC), clock=_clock(), mode=DataMode.ONLINE,
             backend=DataBackend.LIVE, strict_pit=False, calendar_id="XSHG",
             resolved_vendor_chains={"get_ohlcv": ["a_stock"]}, source_config_digest="cfg",
             data_snapshot_id="snap")
    d.update(kw)
    return DataContext(**d)


def test_context_ok_online():
    assert _ctx().mode == DataMode.ONLINE


def test_as_of_must_match_clock():
    with pytest.raises(ValueError):
        _ctx(as_of=datetime(2026, 7, 14, tzinfo=UTC))  # != clock.as_of


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        _ctx(as_of=datetime(2026, 7, 15), clock=_clock(as_of=datetime(2026, 7, 15)))


def test_pit_replay_requires_strict_and_snapshots():
    with pytest.raises(ValueError):
        _ctx(mode=DataMode.PIT_REPLAY, strict_pit=False, backend=DataBackend.PIT_STORE)
    ok = _ctx(mode=DataMode.PIT_REPLAY, strict_pit=True, backend=DataBackend.PIT_STORE,
              vintage_manifest_digest="vm")
    assert ok.strict_pit is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/context.py
from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.schemas import ArtifactRef


def _require_aware(*values: datetime | None) -> None:
    for v in values:
        if v is not None and v.tzinfo is None:
            raise ValueError(f"datetime must be timezone-aware: {v!r}")


class ClockSpec(DigestModel):
    as_of: datetime
    timezone: str
    calendar_id: str
    clock_version: str


class DataContext(DigestModel):
    schema_version: str = "1"
    as_of: datetime
    clock: ClockSpec
    mode: DataMode
    backend: DataBackend
    strict_pit: bool
    calendar_id: str
    resolved_vendor_chains: dict[str, list[str]]
    source_config_digest: str
    data_snapshot_id: str
    vintage_manifest_digest: str | None = None

    @model_validator(mode="after")
    def _v_ctx(self) -> "DataContext":
        _require_aware(self.as_of, self.clock.as_of)
        if self.as_of != self.clock.as_of:
            raise ValueError("as_of must equal clock.as_of")
        if self.calendar_id != self.clock.calendar_id:
            raise ValueError("calendar_id must equal clock.calendar_id")
        if self.mode == DataMode.PIT_REPLAY:
            if not self.strict_pit:
                raise ValueError("PIT_REPLAY requires strict_pit=True")
            if not self.data_snapshot_id or not self.vintage_manifest_digest:
                raise ValueError("PIT_REPLAY requires data_snapshot_id and vintage_manifest_digest")
        return self


class RunBudget(DigestModel):
    schema_version: str = "1"
    ledger_id: str
    max_tokens: int
    max_llm_invocations: int
    max_concurrency: int
    reserved_tokens: int = 0
    reserved_llm_invocations: int = 0


class BudgetReservation(DigestModel):
    reservation_id: str
    ledger_id: str
    run_id: str
    scope_type: Literal["bootstrap", "planner", "plan", "node", "schema_repair", "retry"]
    scope_id: str
    parent_reservation_id: str | None = None
    reserved_tokens: int
    reserved_llm_invocations: int
    actual_tokens: int = 0
    actual_llm_invocations: int = 0
    status: Literal["reserved", "settled", "released"]
    reserved_at: datetime
    settled_at: datetime | None = None


class MemoryRecordRef(DigestModel):
    record_id: str
    revision_id: str | None = None
    available_at: datetime
    content_digest: str


class ContextSnapshot(DigestModel):
    schema_version: str = "1"
    id: str
    run_id: str
    as_of: datetime
    mode: DataMode
    bootstrap_plan_digest: str
    market_factor_ref: ArtifactRef
    regime_ref: ArtifactRef | None = None
    rotation_ref: ArtifactRef | None = None
    past_context_ref: ArtifactRef | None = None
    data_snapshot_id: str
    data_snapshot_hash: str
    vintage_manifest_digest: str
    memory_snapshot_id: str
    memory_snapshot_hash: str
    past_context_hash: str
    status: Literal["ready", "degraded", "unknown"]
    created_at: datetime
    content_digest: str


class InputSnapshot(DigestModel):
    schema_version: str = "1"
    id: str
    run_id: str
    plan_digest: str
    layer_index: int
    context_snapshot_id: str | None = None
    artifact_refs: list[ArtifactRef]
    data_result_ids: list[str]
    memory_record_refs: list[MemoryRecordRef]
    frozen_at: datetime
    content_digest: str


class RunContext(DigestModel):
    schema_version: str = "1"
    run_id: str
    data: DataContext
    context_snapshot_id: str | None = None
    memory_snapshot_hash: str
    budget: RunBudget
    cancellation_token_id: str
    replays_run_id: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_context.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/context.py tests/orchestration/test_context.py
git commit -m "feat(orchestration): DataContext/RunContext/budget/snapshots (phase1)"
```

---

## Task 9: Run events

**Files:**
- Create: `guanlan_v2/orchestration/events.py`
- Test: `tests/orchestration/test_events.py`

**Interfaces:**
- Consumes: `digest.DigestModel`.
- Produces: `RunEvent`, `EventCursor`, `CommittedArtifactRef`, `LayerCommit`, `PlanApproval`. `RunEvent.SEMANTIC_EXCLUDE` = `{"event_id","journal_seq","visible_seq","occurred_at","content_digest"}` (seq/wall-clock are audit).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_events.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.events import RunEvent

UTC = timezone.utc


def _evt(**kw):
    d = dict(event_id="e1", run_id="r", partition="main", event_type="NodeStateChanged",
             journal_seq=1, idempotency_key="k1", payload_type="NodeRun", payload_version="1",
             payload_ref="ref1", occurred_at=datetime(2026, 7, 15, tzinfo=UTC), content_digest="c")
    d.update(kw)
    return RunEvent(**d)


def test_event_semantic_digest_ignores_seq_and_walltime():
    a = _evt(journal_seq=1, visible_seq=1, occurred_at=datetime(2026, 7, 15, 1, tzinfo=UTC))
    b = _evt(journal_seq=99, visible_seq=42, occurred_at=datetime(2026, 7, 15, 9, tzinfo=UTC))
    assert a.semantic_digest() == b.semantic_digest()
    c = _evt(payload_ref="other")
    assert a.semantic_digest() != c.semantic_digest()


def test_staged_event_has_no_visible_seq():
    staged = _evt(event_type="ArtifactStaged", visible_seq=None)
    assert staged.visible_seq is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/events.py
from __future__ import annotations
from datetime import datetime
from typing import ClassVar, Literal
from pydantic import Field
from guanlan_v2.orchestration.digest import DigestModel

EventType = Literal[
    "RunRequested", "PlanDrafted", "PlanApproved", "PlanRejected", "PlanFrozen",
    "BudgetReserved", "BudgetSettled", "BudgetReleased", "NodeStateChanged",
    "ArtifactStaged", "LayerCommitted", "ContextSnapshotFrozen", "ArtifactRelated",
    "ExperimentStateChanged", "RunCancelled", "RunCompleted", "RunFailed",
    "TrialReserved", "TrialRevealed", "TrialExhausted",
    "CaseCreated", "CaseMatured", "CaseReviewed"]


class RunEvent(DigestModel):
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"event_id", "journal_seq", "visible_seq", "occurred_at", "content_digest"})
    schema_version: str = "1"
    event_id: str
    run_id: str
    partition: str
    plan_digest: str | None = None
    event_type: EventType
    causation_id: str | None = None
    correlation_id: str | None = None
    journal_seq: int
    visible_seq: int | None = None
    idempotency_key: str
    payload_type: str
    payload_version: str
    payload_ref: str
    occurred_at: datetime
    content_digest: str


class EventCursor(DigestModel):
    run_id: str
    partition: str
    visible_seq: int


class CommittedArtifactRef(DigestModel):
    artifact_id: str
    artifact_seq: int


class LayerCommit(DigestModel):
    plan_digest: str
    layer_index: int
    node_run_ids: list[str]
    artifacts: list[CommittedArtifactRef]
    committed_at: datetime


class PlanApproval(DigestModel):
    request_id: str
    plan_digest: str
    decision: Literal["approved", "rejected"]
    actor_id: str
    decided_at: datetime
    reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_events.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/events.py tests/orchestration/test_events.py
git commit -m "feat(orchestration): RunEvent/LayerCommit/PlanApproval contracts (phase1)"
```

---

## Task 10: Worker / Plan contracts

**Files:**
- Create: `guanlan_v2/orchestration/spec.py`
- Test: `tests/orchestration/test_spec.py`

**Interfaces:**
- Consumes: `enums.*`, `data.symbols.Symbol`, `digest.DigestModel`.
- Produces: `OrchestrationRequest`, `ExecutionSpec`, `EvidencePolicy`, `WorkerSpec`, `Dependency`, `PlanNode`, `GateCfg`, `GateResult`, `DebateCfg`, `ReducerCfg`, `PlanDraft`, `Plan`. Invariants:
  - `WorkerSpec`: `supported_modes` non-empty; `outputs` contains `"primary"`.
  - `OrchestrationRequest`: `workflow=="optimize_existing"` requires `existing_candidate_artifact_id`, `existing_candidate_hash`, `existing_context_snapshot_id` all set; other workflows forbid them. `decision_schedule_id/version/digest` all-set-or-all-none.
  - `Dependency` default `policy=BLOCK`, `accept_statuses={COMPLETED}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_spec.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.enums import DataMode, Tier, ExecutionKind
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest, WorkerSpec, ExecutionSpec, Dependency)
from guanlan_v2.orchestration.enums import NodeStatus

UTC = timezone.utc


def _worker(**kw):
    d = dict(id="dec.pm", lane="decision", persona="PM", system_prompt_ref="skills/pm.md",
             tier=Tier.WRITER, execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner_deep"),
             input_model="PMInput", outputs={"primary": "PortfolioDecision@1"},
             supported_modes={DataMode.ONLINE})
    d.update(kw)
    return WorkerSpec(**d)


def test_worker_requires_supported_modes():
    with pytest.raises(ValueError):
        _worker(supported_modes=set())


def test_worker_outputs_must_have_primary():
    with pytest.raises(ValueError):
        _worker(outputs={"secondary": "X@1"})


def test_dependency_defaults_block_completed():
    dep = Dependency(upstream_node_id="a", artifact_slot="s", inject_as="ctx")
    assert dep.policy == "block"
    assert dep.accept_statuses == {NodeStatus.COMPLETED}


def test_optimize_existing_requires_all_three_refs():
    with pytest.raises(ValueError):
        OrchestrationRequest(request_id="q", goal="g", workflow="optimize_existing")
    ok = OrchestrationRequest(request_id="q", goal="g", workflow="optimize_existing",
                              existing_candidate_artifact_id="a", existing_candidate_hash="h",
                              existing_context_snapshot_id="cs")
    assert ok.workflow == "optimize_existing"


def test_non_optimize_forbids_existing_refs():
    with pytest.raises(ValueError):
        OrchestrationRequest(request_id="q", goal="g", workflow="orchestrate_only",
                             existing_candidate_hash="h")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_spec.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/spec.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import Field, model_validator
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy, DataMode, DependencyPolicy, ExecutionKind, NodeStatus, PlanSource,
    Tier, ToolCallRequirement)


class OrchestrationRequest(DigestModel):
    schema_version: str = "1"
    request_id: str
    goal: str
    workflow: Literal["orchestrate_only", "orchestrate_and_optimize", "optimize_existing"]
    fallback_preset_id: str | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    existing_candidate_artifact_id: str | None = None
    existing_candidate_hash: str | None = None
    existing_context_snapshot_id: str | None = None
    decision_schedule_id: str | None = None
    decision_schedule_version: str | None = None
    decision_schedule_digest: str | None = None

    @model_validator(mode="after")
    def _v_request(self) -> "OrchestrationRequest":
        existing = (self.existing_candidate_artifact_id, self.existing_candidate_hash,
                    self.existing_context_snapshot_id)
        if self.workflow == "optimize_existing":
            if not all(existing):
                raise ValueError("optimize_existing requires all three existing_* refs")
        elif any(existing):
            raise ValueError("existing_* refs only allowed for optimize_existing")
        sched = (self.decision_schedule_id, self.decision_schedule_version, self.decision_schedule_digest)
        if any(sched) and not all(sched):
            raise ValueError("decision_schedule id/version/digest must be all-set or all-none")
        return self


class ExecutionSpec(DigestModel):
    kind: ExecutionKind
    handler_ref: str | None = None
    model_tier: Literal["fast", "reasoner", "reasoner_deep"] | None = None
    thinking_budget: int = 0


class EvidencePolicy(DigestModel):
    tool_calls: ToolCallRequirement = ToolCallRequirement.OPTIONAL
    require_input_refs: bool = True
    require_number_anchors: bool = True
    allow_unsourced_numbers: bool = False
    optional_data_may_degrade: bool = True


class WorkerSpec(DigestModel):
    schema_version: str = "1"
    id: str
    lane: Literal["market", "quant", "pv", "text", "decision", "xcut"]
    persona: str
    system_prompt_ref: str
    tier: Tier
    execution: ExecutionSpec
    can_emit_decision: bool = False
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    input_model: str
    input_version: str = "1"
    outputs: dict[str, str]
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    guardrails: list[str] = Field(default_factory=list)
    supported_modes: set[DataMode]
    borrowed_from: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _v_worker(self) -> "WorkerSpec":
        if not self.supported_modes:
            raise ValueError("supported_modes must be non-empty")
        if "primary" not in self.outputs:
            raise ValueError("outputs must contain 'primary'")
        return self


class Dependency(DigestModel):
    upstream_node_id: str
    artifact_slot: str
    upstream_output_key: str = "primary"
    inject_as: str
    policy: DependencyPolicy = DependencyPolicy.BLOCK
    accept_statuses: set[NodeStatus] = Field(default_factory=lambda: {NodeStatus.COMPLETED})


class PlanNode(DigestModel):
    id: str
    worker_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)
    writes_slot: str
    gate_ids: list[str] = Field(default_factory=list)
    debate_id: str | None = None
    round_role: str | None = None
    debate_round: int | None = None
    debate_turn: int | None = None
    condition: str | None = None
    timeout_sec: int = 300
    max_attempts: int = 1
    token_reservation: int = 0


class GateCfg(DigestModel):
    id: str
    metric: str
    operator: Literal[">", ">=", "<", "<=", "=="]
    threshold: float | str
    scope: str
    blocking: bool = True
    unavailable_policy: Literal["fail", "degrade", "skip"] = "fail"
    min_samples: int | None = None


class GateResult(DigestModel):
    gate_id: str
    metric_id: str
    status: Literal["passed", "failed", "unavailable"]
    observed: float | str | None = None
    threshold: float | str
    blocking: bool
    reason: str
    metrics_artifact_id: str


class DebateCfg(DigestModel):
    id: str
    seats: list[str]
    turn_order: list[str]
    max_rounds: int
    judge_node_id: str


class ReducerCfg(DigestModel):
    id: str
    slot: str
    reducer_id: str
    producer_node_ids: list[str]
    output_model: str
    output_version: str


class PlanDraft(DigestModel):
    schema_version: str = "2"
    id: str
    run_id: str
    request_id: str
    phase: Literal["bootstrap", "main"]
    source: PlanSource
    goal: str
    as_of: datetime
    mode: DataMode
    context_snapshot_id: str | None = None
    universe: list[Symbol]
    nodes: list[PlanNode]
    sink_node_ids: list[str]
    debates: list[DebateCfg] = Field(default_factory=list)
    gates: list[GateCfg] = Field(default_factory=list)
    reducers: list[ReducerCfg] = Field(default_factory=list)
    catalog_version: str
    catalog_digest: str
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    budget_request_tokens: int = 0
    budget_request_llm_invocations: int = 0
    max_concurrency: int = 4
    stop_condition_ids: list[str] = Field(default_factory=list)


class Plan(PlanDraft):
    budget_reservation_id: str
    frozen_at: datetime
    plan_digest: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_spec.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/spec.py tests/orchestration/test_spec.py
git commit -m "feat(orchestration): worker/plan contracts with validators (phase1)"
```

---

## Task 11: Study / trial / holdout contracts

**Files:**
- Create: `guanlan_v2/orchestration/trials.py`
- Test: `tests/orchestration/test_trials.py`

**Interfaces:**
- Consumes: `digest.DigestModel`.
- Produces: `StudySpec`, `StudyFamily`, `HoldoutWindow`, `TrialRecord`, `OptimizeRunState`, `OptimizeResult`, `HoldoutReceipt`, `HoldoutLease`, `SealedEvaluationRecord`, `SealedCapability` (models only; ledger/gateway behavior is Phase 4). Invariant: `TrialRecord` with `stage=="sealed_holdout"` must have `validation_result_artifact_id is None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_trials.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.trials import TrialRecord, HoldoutReceipt

UTC = timezone.utc


def _trial(**kw):
    d = dict(trial_id="t1", family_id="f1", candidate_hash="ch", data_snapshot_hash="ds",
             split_spec_hash="ss", code_prompt_model_hash="cp", metrics_revealed=["sharpe"],
             stage="validation", status="reserved", idempotency_key="k",
             created_at=datetime(2026, 7, 15, tzinfo=UTC))
    d.update(kw)
    return TrialRecord(**d)


def test_validation_trial_may_carry_result():
    t = _trial(stage="validation", validation_result_artifact_id="a1")
    assert t.validation_result_artifact_id == "a1"


def test_sealed_holdout_trial_must_not_carry_validation_result():
    with pytest.raises(ValueError):
        _trial(stage="sealed_holdout", validation_result_artifact_id="a1")
    ok = _trial(stage="sealed_holdout", validation_result_artifact_id=None)
    assert ok.stage == "sealed_holdout"


def test_holdout_receipt_has_no_dereferenceable_metrics():
    r = HoldoutReceipt(trial_id="t", family_id="f", holdout_window_id="w", status="revealed",
                       result_digest="d", revealed_at=datetime(2026, 7, 15, tzinfo=UTC))
    assert set(r.model_dump()) == {"trial_id", "family_id", "holdout_window_id", "status",
                                   "result_digest", "revealed_at"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_trials.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/trials.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import Field, model_validator
from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import ExperimentStatus


class StudySpec(DigestModel):
    objective: str
    objective_digest: str
    label_definition: str
    label_digest: str
    universe_digest: str
    frequency: str
    split_policy_digest: str
    parent_family_id: str | None = None
    change_reason: str | None = None


class StudyFamily(DigestModel):
    family_id: str
    identity_digest: str
    objective_digest: str
    label_digest: str
    universe_digest: str
    frequency: str
    split_policy_digest: str
    parent_family_id: str | None = None
    change_reason: str | None = None
    governor_attestation: str


class HoldoutWindow(DigestModel):
    holdout_window_id: str
    family_identity_digest: str
    start_at: datetime
    end_at: datetime
    matured_at: datetime
    data_snapshot_id: str
    vintage_manifest_digest: str
    prior_window_ids: list[str] = Field(default_factory=list)
    non_overlap_attestation: str


class TrialRecord(DigestModel):
    schema_version: str = "1"
    trial_id: str
    family_id: str
    candidate_hash: str
    parent_trial_id: str | None = None
    data_snapshot_hash: str
    split_spec_hash: str
    code_prompt_model_hash: str
    metrics_revealed: list[str]
    stage: Literal["validation", "sealed_holdout"]
    status: Literal["reserved", "revealed", "failed", "timed_out", "inconclusive"]
    validation_result_artifact_id: str | None = None
    result_digest: str | None = None
    holdout_window_id: str | None = None
    holdout_lease_id: str | None = None
    lease_state: Literal["none", "reserved", "consumed", "exhausted"] = "none"
    revealed_at: datetime | None = None
    idempotency_key: str
    reused_from_trial_id: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _v_sealed_no_result(self) -> "TrialRecord":
        if self.stage == "sealed_holdout" and self.validation_result_artifact_id is not None:
            raise ValueError("sealed_holdout trial must not carry validation_result_artifact_id")
        return self


class OptimizeRunState(DigestModel):
    experiment_id: str
    family_id: str
    status: ExperimentStatus
    candidate_hash: str | None = None
    resume_after: datetime | None = None
    wakeup_key: str | None = None
    updated_at: datetime


class OptimizeResult(DigestModel):
    state: OptimizeRunState
    best_candidate_artifact_id: str | None = None
    validation_trial_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = None


class HoldoutReceipt(DigestModel):
    trial_id: str
    family_id: str
    holdout_window_id: str
    status: Literal["revealed", "failed", "timed_out", "inconclusive"]
    result_digest: str | None = None
    revealed_at: datetime | None = None


class HoldoutLease(DigestModel):
    lease_id: str
    trial_id: str
    candidate_hash: str
    holdout_window_id: str
    issued_at: datetime
    expires_at: datetime
    nonce: str
    signature: str


class SealedEvaluationRecord(DigestModel):
    trial_id: str
    result_artifact_id: str
    result_digest: str
    metrics_payload: dict[str, Any]
    curve_ref: str | None = None
    created_at: datetime


class SealedCapability(DigestModel):
    token_id: str
    scope: Literal["final_report", "human_review"]
    principal_id: str
    expires_at: datetime
    signature: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_trials.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/trials.py tests/orchestration/test_trials.py
git commit -m "feat(orchestration): study/trial/holdout contracts (phase1)"
```

---

## Task 12: Reversible legacy-schema migration adapters

**Files:**
- Create: `guanlan_v2/orchestration/migration.py`
- Test: `tests/orchestration/test_migration.py`

**Interfaces:**
- Consumes: `enums.PortfolioRating/SentimentBand/Confidence`.
- Produces reversible adapters, each with `to_new(raw) -> (enum, RawKept)` and `to_legacy(enum, raw_kept) -> original`, preserving the raw value so round-trip is exact:
  - `migrate_rating(raw: int) -> PortfolioRating` for legacy -10..10 buckets, `rating_to_legacy_bucket(PortfolioRating) -> tuple[int,int]` (returns the inclusive legacy range); plus `MigratedRating(BaseModel){new: PortfolioRating, raw: int}`.
  - `migrate_action(raw: str) -> str` normalizing case-variant Buy/Hold/Sell, `MigratedAction{new, raw}`.
  - `migrate_confidence(raw: float | str) -> Confidence` for 0..100 or enum, `MigratedConfidence{new, raw}`.
  - `migrate_sentiment(raw: float) -> SentimentBand` for -1..1 or 0..10 (scale disambiguated by `scale` arg), `MigratedSentiment{new, raw, scale}`.
- Rule: an out-of-range or unrecognized `raw` raises `ValueError` — never silently coerced.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_migration.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.enums import PortfolioRating, Confidence, SentimentBand
from guanlan_v2.orchestration import migration as m


def test_rating_buckets_and_reverse_preserve_raw():
    mr = m.migrate_rating(9)
    assert mr.new == PortfolioRating.BUY and mr.raw == 9
    lo, hi = m.rating_to_legacy_bucket(PortfolioRating.BUY)
    assert lo <= mr.raw <= hi
    assert m.migrate_rating(0).new == PortfolioRating.HOLD
    assert m.migrate_rating(-9).new == PortfolioRating.SELL


def test_rating_out_of_range_raises():
    with pytest.raises(ValueError):
        m.migrate_rating(11)


def test_action_case_normalized_and_reversible():
    ma = m.migrate_action("BUY")
    assert ma.new == "Buy" and ma.raw == "BUY"
    assert m.migrate_action("sell").new == "Sell"


def test_action_unknown_raises():
    with pytest.raises(ValueError):
        m.migrate_action("accumulate")


def test_confidence_from_0_100_and_enum():
    assert m.migrate_confidence(90).new == Confidence.HIGH
    assert m.migrate_confidence(50).new == Confidence.MEDIUM
    assert m.migrate_confidence("low").new == Confidence.LOW
    with pytest.raises(ValueError):
        m.migrate_confidence(140)


def test_sentiment_scale_disambiguated_and_reversible():
    s = m.migrate_sentiment(0.9, scale="pm1")     # -1..1
    assert s.new == SentimentBand.BULLISH and s.raw == 0.9 and s.scale == "pm1"
    assert m.migrate_sentiment(9.0, scale="zero_ten").new == SentimentBand.BULLISH
    with pytest.raises(ValueError):
        m.migrate_sentiment(9.0, scale="pm1")     # 9 out of -1..1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_migration.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/migration.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from guanlan_v2.orchestration.enums import Confidence, PortfolioRating, SentimentBand

# ── rating: legacy -10..10 → 5 buckets ──
_RATING_BUCKETS: list[tuple[int, int, PortfolioRating]] = [
    (6, 10, PortfolioRating.BUY),
    (2, 5, PortfolioRating.OVERWEIGHT),
    (-1, 1, PortfolioRating.HOLD),
    (-5, -2, PortfolioRating.UNDERWEIGHT),
    (-10, -6, PortfolioRating.SELL),
]


class MigratedRating(BaseModel):
    new: PortfolioRating
    raw: int


def migrate_rating(raw: int) -> MigratedRating:
    for lo, hi, r in _RATING_BUCKETS:
        if lo <= raw <= hi:
            return MigratedRating(new=r, raw=raw)
    raise ValueError(f"legacy rating out of range [-10,10]: {raw}")


def rating_to_legacy_bucket(r: PortfolioRating) -> tuple[int, int]:
    for lo, hi, rr in _RATING_BUCKETS:
        if rr == r:
            return (lo, hi)
    raise ValueError(f"no legacy bucket for {r}")


# ── action: case-variant → canonical ──
_ACTIONS = {"buy": "Buy", "hold": "Hold", "sell": "Sell"}


class MigratedAction(BaseModel):
    new: str
    raw: str


def migrate_action(raw: str) -> MigratedAction:
    key = raw.strip().lower()
    if key not in _ACTIONS:
        raise ValueError(f"unknown legacy action: {raw!r}")
    return MigratedAction(new=_ACTIONS[key], raw=raw)


# ── confidence: 0..100 or enum ──
class MigratedConfidence(BaseModel):
    new: Confidence
    raw: float | str


def migrate_confidence(raw: float | str) -> MigratedConfidence:
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key not in {c.value for c in Confidence}:
            raise ValueError(f"unknown legacy confidence: {raw!r}")
        return MigratedConfidence(new=Confidence(key), raw=raw)
    if not 0 <= raw <= 100:
        raise ValueError(f"legacy confidence out of range [0,100]: {raw}")
    new = Confidence.HIGH if raw >= 67 else (Confidence.MEDIUM if raw >= 34 else Confidence.LOW)
    return MigratedConfidence(new=new, raw=raw)


# ── sentiment: -1..1 (pm1) or 0..10 (zero_ten) → band ──
class MigratedSentiment(BaseModel):
    new: SentimentBand
    raw: float
    scale: Literal["pm1", "zero_ten"]


def migrate_sentiment(raw: float, *, scale: Literal["pm1", "zero_ten"]) -> MigratedSentiment:
    if scale == "pm1":
        if not -1 <= raw <= 1:
            raise ValueError(f"pm1 sentiment out of range [-1,1]: {raw}")
        norm = (raw + 1) * 5  # → 0..10
    else:
        if not 0 <= raw <= 10:
            raise ValueError(f"zero_ten sentiment out of range [0,10]: {raw}")
        norm = raw
    if norm >= 8.5:
        band = SentimentBand.BULLISH
    elif norm >= 6.5:
        band = SentimentBand.MILDLY_BULLISH
    elif norm >= 5.5:
        band = SentimentBand.NEUTRAL
    elif norm >= 4.5:
        band = SentimentBand.MIXED
    elif norm >= 2.5:
        band = SentimentBand.MILDLY_BEARISH
    else:
        band = SentimentBand.BEARISH
    return MigratedSentiment(new=band, raw=raw, scale=scale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_migration.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/migration.py tests/orchestration/test_migration.py
git commit -m "feat(orchestration): reversible legacy-schema migration adapters (phase1)"
```

---

## Task 13: Populate schema registry + phase-1 integration test

**Files:**
- Modify: `guanlan_v2/orchestration/schema_registry.py` (add `default_registry()` factory)
- Test: `tests/orchestration/test_registry_population.py`

**Interfaces:**
- Consumes: all payload models from Tasks 5–11.
- Produces: `default_registry() -> SchemaRegistry` pre-registered with every business/data payload keyed `"<ClassName>@<schema_version-or-1>"`. Guarantees every registered payload validates a round-trip of its own `model_dump()` and its digest is stable across two constructions.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/test_registry_population.py
from __future__ import annotations
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import RegimeReport


def test_default_registry_has_core_payloads():
    r = default_registry()
    for name, ver in [("RegimeReport", "1"), ("PortfolioDecision", "1"),
                      ("PortfolioTargetProposal", "1"), ("MarketFactorReport", "1"),
                      ("SentimentReport", "1"), ("ResearchPlan", "1")]:
        assert r.resolve(name, ver) is not None


def test_registered_model_roundtrips_via_registry():
    r = default_registry()
    rr = RegimeReport(trend="range", risk_state="neutral", heat_state="normal",
                      trend_probabilities={"bull": 0.3, "bear": 0.3, "range": 0.4},
                      risk_probabilities={"risk_on": 0.3, "risk_off": 0.3, "neutral": 0.4},
                      heat_probabilities={"normal": 0.9, "overheat": 0.1},
                      confidence_score=0.5, drivers=[], narrative="")
    back = r.validate_payload("RegimeReport", "1", rr.model_dump(mode="json"))
    assert back == rr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/test_registry_population.py -v`
Expected: FAIL with `ImportError: cannot import name 'default_registry'`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/schema_registry.py`)

```python
# ── append to guanlan_v2/orchestration/schema_registry.py ──
def default_registry() -> "SchemaRegistry":
    from guanlan_v2.orchestration import schemas as s
    reg = SchemaRegistry()
    payloads = [
        s.ResearchPlan, s.PortfolioDecision, s.SentimentReport, s.MarketFactorValue,
        s.MarketFactorReport, s.RegimeReport, s.RealizedRegime, s.RotationReport,
        s.TargetPosition, s.PortfolioTargetProposal, s.TargetPortfolioIntent,
        s.DecisionSchedule, s.DebateMessage,
    ]
    for model in payloads:
        version = getattr(model.model_fields.get("schema_version"), "default", "1") or "1"
        reg.register(model.__name__, version, model)
    return reg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/test_registry_population.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the whole phase-1 suite + commit**

Run: `pytest tests/orchestration/ -v`
Expected: PASS (all phase-1 tests green)

```bash
git add guanlan_v2/orchestration/schema_registry.py tests/orchestration/test_registry_population.py
git commit -m "feat(orchestration): populate schema registry + phase1 integration test"
```

---

## Self-Review (completed by plan author)

**Spec coverage (§8 契约 + §12 phase 1):** OrchestrationRequest ✓(T10) · WorkerSpec ✓(T10) · PlanDraft+Plan ✓(T10) · PlanNode/Dependency ✓(T10) · RunBudget+Reservation ✓(T8) · NodeRun ✓(T6) · RunEvent ✓(T9) · Artifact/Provenance/NumberAnchor ✓(T6) · DataResult+PIT ✓(T5) · Context+InputSnapshot+ContextSnapshot ✓(T8) · Symbol/InstrumentMeta/LimitRule ✓(T4) · business/market/regime/shadow payloads ✓(T7) · trials/holdout/sealed ✓(T11) · schema registry/version ✓(T3,T13) · digest invariants ✓(T1) · reversible migration ✓(T12). **Deferred to later phases (correctly out of scope for Phase 1 — behavior, not contracts):** BudgetLedger/TrialLedger/SealedEvaluatorGateway/ArtifactPool/SourceRegistry/DataReader/PitGuard runtime, `normalize_symbol`/`resolve_*` (need reader), catalog/dag/orchestrator/optimizer/evaluator/governor/memory-facade/market-factors/adapters.

**Placeholder scan:** none — every step has runnable test + implementation code.

**Type consistency:** `DigestModel.semantic_digest()` used uniformly; `Symbol` fields (`code/exchange/board`) consistent across T4/T7/T10; `NodeStatus`/`DataMode`/`DataStatus` imported from `enums` everywhere; `Dependency.accept_statuses` default `{NodeStatus.COMPLETED}` matches T10 test; `TargetPortfolioIntent` literal fields match Global Constraints.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
