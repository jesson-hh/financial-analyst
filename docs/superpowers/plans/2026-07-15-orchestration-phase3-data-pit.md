# Orchestration Phase 3 · 数据 / PIT 层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed, PIT-safe multi-source data interface for `guanlan_v2/orchestration/data/` — error taxonomy, syntactic symbol normalization, `PitGuard` (physically refuses future data), narrow-fallback `SourceRegistry.dispatch`, and the `DataReader` facade + `render_for_prompt`. **Vendor acquisition stays stubbed** — this phase freezes the interface, not the scrapers.

**Architecture:** Mirrors TradingAgents `dataflows/interface.py` — a `{method:{vendor:DataSource}}` dispatch table, a config-resolved vendor *chain*, and **routing by typed exception** (the set of exception types is the whole control surface, so adding a vendor needs zero new `except` clauses). Adds what TA lacks and 帷幄 requires: a first-class `PitGuard` keyed on `available_at` (当时可知时间) that refuses `> as_of` rows in strict replay and never falls through to another vendor. Everything returns a typed `DataResult[RowSet]`; only `render_for_prompt` turns a result into an LLM-facing text block. Built entirely on Phase 1 contracts (`DataResult`, `PitAudit`, `SourceAttempt`, `Symbol`, `DataStatus`, `DataMode`) plus this phase's `errors`.

**Tech Stack:** Python ≥3.11, Pydantic v2, `re`, `pytest`. All modules `from __future__ import annotations`. Depends on Phase 1 (`guanlan_v2/orchestration/data/{symbols,result}.py`, `enums.py`, `digest.py`).

> **Scope note.** The spec §12 phase 3 bundles "data/PIT + **memory facade**". The memory facade wraps the existing metadata-less `AgentMemory`/`memory_ops`/console writer and is a distinct subsystem needing those write-path APIs; it is split into a separate **Phase 3b** plan. This plan is the complete, self-contained data/PIT half.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-07-15-orchestration-framework-design.md`). Every task implicitly includes these.

- **Narrow fallback.** Cross-vendor fallback fires **only** on `RateLimitError` / `NotConfiguredError`. `NoDataError` and `StaleDataError` terminate the current chain with a typed result (no continue). `FutureDataRefused` and any other `DataError` **raise** (a broken primary must be loud, never masked by a fallback's answer).
- **Never fabricate.** No path returns an empty string a model could fill in; missing data is a typed `NO_DATA`/`STALE`/`UNAVAILABLE` `DataResult`, and `render_for_prompt` emits an explicit "do not fabricate" sentinel.
- **PIT is `available_at`, not period-end.** `PitGuard` compares each row's `available_at` (当时可知时间). Strict replay: any `available_at > as_of` → `FutureDataRefused` (never a silent drop, never a fallback). Any row missing `available_at` → `MissingAvailabilityRefused`. Freshness is per-method/category, not a global `MAX_STALE_DAYS`.
- **`normalize_symbol` is purely syntactic** (no network): only normalizes a code; never infers ST, listing stage, or the day's price-limit. The 6-digit result must match `^[0-9]{6}$` before it may be used as a cache key. `resolve_name_to_code` rejects industry/concept names (forces the caller to pass a 6-digit code, never guesses).
- **`DataResult` invariants (Phase 1):** `OK`/`DEGRADED` carry data; `NO_DATA`/`STALE`/`UNAVAILABLE` do not; `DEGRADED` needs `coverage` + `degradation_reason`. Every result keeps the full `attempts` list; `content_digest` excludes wall-clock, `audit_digest` covers it.
- `OPTIONAL_CATEGORIES = {"signal_data", "macro_data", "prediction_markets"}` degrade to `UNAVAILABLE` on chain exhaustion; core categories raise.
- No placeholders, DRY, YAGNI, TDD, frequent commits. Run tests from repo root `G:\guanlan-v2` with `pytest`.

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/data/errors.py` | error taxonomy (the router's whole control surface) |
| `guanlan_v2/orchestration/data/symbols.py` (append) | `normalize_symbol` / `resolve_name_to_code` / `resolve_limit_rule` |
| `guanlan_v2/orchestration/data/pit.py` | `PitGuard` + `FreshnessPolicy` |
| `guanlan_v2/orchestration/data/source.py` | `DataSource` protocol + `DataRequest` / `RawFetch` / `RowSet` |
| `guanlan_v2/orchestration/data/registry.py` | `SourceRegistry.dispatch` (narrow fallback) |
| `guanlan_v2/orchestration/data/reader.py` | `DataReader` facade + `render_for_prompt` |
| `tests/orchestration/data/` | one test module per source module |

---

## Task 1: Error taxonomy

**Files:**
- Create: `guanlan_v2/orchestration/data/errors.py`
- Test: `tests/orchestration/data/__init__.py` (empty), `tests/orchestration/data/test_errors.py`

**Interfaces:**
- Produces: `DataError(Exception)`; `NoDataError(DataError)` with `__init__(self, *, symbol, canonical, detail)`; `StaleDataError(DataError)` with `__init__(self, detail, *, latest_available_at)`; `RateLimitError(DataError)`; `NotConfiguredError(DataError, ValueError)`; `FutureDataRefused(DataError)` with `__init__(self, detail, *, future_rows)`; `MissingAvailabilityRefused(DataError)`; `SourceBrokenError(DataError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_errors.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.data import errors as e


def test_hierarchy():
    assert issubclass(e.NoDataError, e.DataError)
    assert issubclass(e.StaleDataError, e.DataError)
    assert issubclass(e.NotConfiguredError, (e.DataError, ValueError))
    assert issubclass(e.FutureDataRefused, e.DataError)
    assert issubclass(e.MissingAvailabilityRefused, e.DataError)


def test_no_data_carries_symbol_detail():
    err = e.NoDataError(symbol="600519", canonical="600519.SH", detail="delisted")
    assert err.symbol == "600519" and err.canonical == "600519.SH" and err.detail == "delisted"


def test_future_refused_carries_count():
    err = e.FutureDataRefused("leak", future_rows=3)
    assert err.future_rows == 3


def test_not_configured_is_valueerror():
    with pytest.raises(ValueError):
        raise e.NotConfiguredError("no key")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/errors.py
from __future__ import annotations
from datetime import datetime


class DataError(Exception):
    pass


class NoDataError(DataError):
    def __init__(self, *, symbol: str, canonical: str, detail: str = ""):
        self.symbol = symbol
        self.canonical = canonical
        self.detail = detail
        super().__init__(f"no data for {symbol} ({canonical}): {detail}")


class StaleDataError(DataError):
    def __init__(self, detail: str, *, latest_available_at: datetime | None = None):
        self.detail = detail
        self.latest_available_at = latest_available_at
        super().__init__(detail)


class RateLimitError(DataError):
    pass


class NotConfiguredError(DataError, ValueError):
    pass


class FutureDataRefused(DataError):
    def __init__(self, detail: str, *, future_rows: int):
        self.future_rows = future_rows
        super().__init__(detail)


class MissingAvailabilityRefused(DataError):
    pass


class SourceBrokenError(DataError):
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_errors.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/errors.py tests/orchestration/data/__init__.py tests/orchestration/data/test_errors.py
git commit -m "feat(orchestration): data error taxonomy (phase3)"
```

---

## Task 2: `normalize_symbol` (syntactic, path-safe)

**Files:**
- Modify: `guanlan_v2/orchestration/data/symbols.py` (append)
- Test: `tests/orchestration/data/test_normalize_symbol.py`

**Interfaces:**
- Consumes: `Symbol` (Phase 1).
- Produces: `normalize_symbol(raw: str) -> Symbol` — accepts bare (`"600519"`), dotted (`"600519.SH"`), or engine (`"SH600519"`) forms; extracts the 6-digit core; infers `exchange`/`board` from 号段 (688→SH/star, 300|301→SZ/chinext, leading 8|4→BJ/bj, leading 6→SH/main, else SZ/main). Raises `ValueError` if no unambiguous 6-digit code. The returned `Symbol.code` always matches `^[0-9]{6}$` (path-safety guard).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_normalize_symbol.py
from __future__ import annotations
import pytest
from guanlan_v2.orchestration.data.symbols import normalize_symbol


def test_bare_dotted_engine_all_normalize():
    for raw in ["600519", "600519.SH", "SH600519", "sh600519"]:
        s = normalize_symbol(raw)
        assert s.code == "600519" and s.exchange == "SH" and s.board == "main"


def test_star_board():
    s = normalize_symbol("688981")
    assert s.exchange == "SH" and s.board == "star"


def test_chinext_board():
    s = normalize_symbol("300750")
    assert s.exchange == "SZ" and s.board == "chinext"


def test_bj_board():
    s = normalize_symbol("830799")
    assert s.exchange == "BJ" and s.board == "bj"


def test_sz_main():
    assert normalize_symbol("000001").exchange == "SZ"


def test_rejects_no_six_digit_code():
    with pytest.raises(ValueError):
        normalize_symbol("AAPL")
    with pytest.raises(ValueError):
        normalize_symbol("60051")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_normalize_symbol.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_symbol'`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/data/symbols.py`)

```python
# ── append to guanlan_v2/orchestration/data/symbols.py ──
import re as _re

_SIX = _re.compile(r"(?<!\d)(\d{6})(?!\d)")


def normalize_symbol(raw: str) -> Symbol:
    """Purely syntactic (no network). Accepts bare / dotted / engine forms."""
    s = str(raw).strip().upper()
    m = _SIX.search(s)
    if not m:
        raise ValueError(f"no unambiguous 6-digit A-share code in {raw!r}")
    code = m.group(1)
    if code.startswith("688"):
        exchange, board = "SH", "star"
    elif code.startswith(("300", "301")):
        exchange, board = "SZ", "chinext"
    elif code[0] in ("8", "4"):
        exchange, board = "BJ", "bj"
    elif code[0] == "6":
        exchange, board = "SH", "main"
    else:
        exchange, board = "SZ", "main"
    return Symbol(code=code, exchange=exchange, board=board)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_normalize_symbol.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/symbols.py tests/orchestration/data/test_normalize_symbol.py
git commit -m "feat(orchestration): syntactic normalize_symbol (phase3)"
```

---

## Task 3: `resolve_name_to_code` + `resolve_limit_rule`

**Files:**
- Modify: `guanlan_v2/orchestration/data/symbols.py` (append)
- Test: `tests/orchestration/data/test_resolve.py`

**Interfaces:**
- Consumes: `Symbol`, `InstrumentMeta`, `LimitRule` (Phase 1), `normalize_symbol`.
- Produces:
  - `resolve_name_to_code(raw: str, name_map: Mapping[str, str]) -> Symbol` — if `raw` already yields a 6-digit code → `normalize_symbol`; if `raw` contains CJK and is in `name_map` (个股中文名→码) → resolve; if it contains CJK but is not a known stock name (i.e. an industry/concept word) → raise `ValueError` instructing the caller to pass a 6-digit code (never guesses).
  - `resolve_limit_rule(sym: Symbol, as_of: datetime, meta: InstrumentMeta) -> LimitRule` — `is_st is True` → `pct=0.05 reason="ST"`; else by board (`star`/`chinext`→0.20, `bj`→0.30, `main`→0.10); if `meta.is_st is None` → `pct=None reason="ST status unknown"` (never defaults to 10%). `rule_version="a-share-2020"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_resolve.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.data.symbols import (
    Symbol, InstrumentMeta, resolve_name_to_code, resolve_limit_rule)

UTC = timezone.utc
_MAP = {"贵州茅台": "600519"}


def test_name_resolves_to_code():
    assert resolve_name_to_code("贵州茅台", _MAP).code == "600519"


def test_code_passthrough():
    assert resolve_name_to_code("600519.SH", _MAP).code == "600519"


def test_industry_name_rejected():
    with pytest.raises(ValueError):
        resolve_name_to_code("白酒", _MAP)


def test_limit_rule_main_board():
    sym = Symbol(code="600519", exchange="SH", board="main")
    r = resolve_limit_rule(sym, datetime(2026, 7, 15, tzinfo=UTC),
                           InstrumentMeta(symbol=sym, is_st=False))
    assert r.pct == 0.10


def test_limit_rule_star_and_st():
    star = Symbol(code="688981", exchange="SH", board="star")
    assert resolve_limit_rule(star, datetime(2026, 7, 15, tzinfo=UTC),
                              InstrumentMeta(symbol=star, is_st=False)).pct == 0.20
    st = InstrumentMeta(symbol=star, is_st=True)
    assert resolve_limit_rule(star, datetime(2026, 7, 15, tzinfo=UTC), st).pct == 0.05


def test_limit_rule_unknown_st_returns_none():
    sym = Symbol(code="600519", exchange="SH", board="main")
    r = resolve_limit_rule(sym, datetime(2026, 7, 15, tzinfo=UTC),
                           InstrumentMeta(symbol=sym, is_st=None))
    assert r.pct is None and "unknown" in r.reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_resolve.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/data/symbols.py`)

```python
# ── append to guanlan_v2/orchestration/data/symbols.py ──
from collections.abc import Mapping as _Mapping
from datetime import datetime as _dt

_CJK = _re.compile(r"[一-鿿]")


def resolve_name_to_code(raw: str, name_map: _Mapping[str, str]) -> Symbol:
    s = str(raw).strip()
    if _SIX.search(s.upper()):
        return normalize_symbol(s)
    if _CJK.search(s):
        if s in name_map:
            return normalize_symbol(name_map[s])
        raise ValueError(
            f"{s!r} is not a known stock name (looks like an industry/concept). "
            "Pass a 6-digit code — never guessed.")
    raise ValueError(f"cannot resolve {raw!r} to a 6-digit code")


def resolve_limit_rule(sym: Symbol, as_of: _dt, meta: InstrumentMeta) -> LimitRule:
    ver = "a-share-2020"
    if meta.is_st is True:
        return LimitRule(pct=0.05, reason="ST", rule_version=ver)
    if meta.is_st is None:
        return LimitRule(pct=None, reason="ST status unknown; cannot assert limit", rule_version=ver)
    board_pct = {"star": 0.20, "chinext": 0.20, "bj": 0.30, "main": 0.10}
    if sym.board not in board_pct:
        return LimitRule(pct=None, reason=f"unknown board {sym.board}", rule_version=ver)
    return LimitRule(pct=board_pct[sym.board], reason=f"{sym.board} board", rule_version=ver)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_resolve.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/symbols.py tests/orchestration/data/test_resolve.py
git commit -m "feat(orchestration): resolve_name_to_code + resolve_limit_rule (phase3)"
```

---

## Task 4: `PitGuard` (refuses future data)

**Files:**
- Create: `guanlan_v2/orchestration/data/pit.py`
- Test: `tests/orchestration/data/test_pit.py`

**Interfaces:**
- Consumes: `data.result.PitAudit` (Phase 1), `enums.DataMode`, `data.errors.*`.
- Produces:
  - `class FreshnessPolicy(BaseModel)`: `max_stale_days: int | None = None`.
  - `class PitGuard` — `__init__(self, *, mode: DataMode, strict: bool)`; `check_rows(self, rows: list[dict], *, as_of: datetime, now: datetime, freshness: FreshnessPolicy | None = None) -> tuple[list[dict], PitAudit]`. Each row must have a tz-aware `available_at`; missing → `MissingAvailabilityRefused`. Rows with `available_at > as_of`: strict → `FutureDataRefused`; non-strict → filtered out (`guard_result="filtered"`). Latest visible `available_at` older than `freshness.max_stale_days` → `StaleDataError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_pit.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest
from guanlan_v2.orchestration.enums import DataMode
from guanlan_v2.orchestration.data.pit import PitGuard, FreshnessPolicy
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused, MissingAvailabilityRefused, StaleDataError)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 15, tzinfo=UTC)
def _row(day, v=1): return {"available_at": datetime(2026, 7, day, tzinfo=UTC), "v": v}


def test_all_visible_rows_pass():
    g = PitGuard(mode=DataMode.ONLINE, strict=False)
    rows, audit = g.check_rows([_row(10), _row(14)], as_of=AS_OF, now=AS_OF)
    assert len(rows) == 2 and audit.guard_result == "passed" and audit.future_rows == 0


def test_strict_replay_refuses_future_rows():
    g = PitGuard(mode=DataMode.PIT_REPLAY, strict=True)
    with pytest.raises(FutureDataRefused) as ei:
        g.check_rows([_row(14), _row(16)], as_of=AS_OF, now=AS_OF)
    assert ei.value.future_rows == 1


def test_soft_mode_filters_future_rows():
    g = PitGuard(mode=DataMode.ONLINE, strict=False)
    rows, audit = g.check_rows([_row(14), _row(16)], as_of=AS_OF, now=AS_OF)
    assert len(rows) == 1 and audit.guard_result == "filtered" and audit.future_rows == 1


def test_missing_available_at_refused():
    g = PitGuard(mode=DataMode.ONLINE, strict=False)
    with pytest.raises(MissingAvailabilityRefused):
        g.check_rows([{"v": 1}], as_of=AS_OF, now=AS_OF)


def test_naive_available_at_refused():
    g = PitGuard(mode=DataMode.ONLINE, strict=False)
    with pytest.raises(MissingAvailabilityRefused):
        g.check_rows([{"available_at": datetime(2026, 7, 10)}], as_of=AS_OF, now=AS_OF)


def test_stale_rows_raise():
    g = PitGuard(mode=DataMode.ONLINE, strict=False)
    with pytest.raises(StaleDataError):
        g.check_rows([_row(1)], as_of=AS_OF, now=AS_OF, freshness=FreshnessPolicy(max_stale_days=5))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_pit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/pit.py
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel
from guanlan_v2.orchestration.data.errors import (
    FutureDataRefused, MissingAvailabilityRefused, StaleDataError)
from guanlan_v2.orchestration.data.result import PitAudit
from guanlan_v2.orchestration.enums import DataMode


class FreshnessPolicy(BaseModel):
    max_stale_days: int | None = None


class PitGuard:
    def __init__(self, *, mode: DataMode, strict: bool):
        self.mode = mode
        self.strict = strict

    def check_rows(self, rows: list[dict], *, as_of: datetime, now: datetime,
                   freshness: FreshnessPolicy | None = None) -> tuple[list[dict], PitAudit]:
        rows_seen = len(rows)
        for r in rows:
            av = r.get("available_at")
            if av is None:
                raise MissingAvailabilityRefused("row missing available_at")
            if av.tzinfo is None:
                raise MissingAvailabilityRefused("available_at must be timezone-aware")
        future = [r for r in rows if r["available_at"] > as_of]
        if future:
            if self.strict:
                raise FutureDataRefused(
                    f"{len(future)} rows available_at > as_of in strict PIT replay",
                    future_rows=len(future))
            visible = [r for r in rows if r["available_at"] <= as_of]
            guard_result = "filtered"
        else:
            visible = list(rows)
            guard_result = "passed"
        latest = max((r["available_at"] for r in visible), default=None)
        if freshness and freshness.max_stale_days is not None and latest is not None:
            if (as_of - latest).days > freshness.max_stale_days:
                raise StaleDataError(
                    f"latest available_at {latest.isoformat()} older than "
                    f"{freshness.max_stale_days}d", latest_available_at=latest)
        audit = PitAudit(mode=self.mode, as_of=as_of, rows_seen=rows_seen,
                         rows_returned=len(visible), future_rows=len(future),
                         missing_available_at_rows=0, guard_result=guard_result,
                         latest_available_at=latest)
        return visible, audit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_pit.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/pit.py tests/orchestration/data/test_pit.py
git commit -m "feat(orchestration): PitGuard refuses future data (phase3)"
```

---

## Task 5: `DataSource` protocol + request/result value objects

**Files:**
- Create: `guanlan_v2/orchestration/data/source.py`
- Test: `tests/orchestration/data/test_source.py`

**Interfaces:**
- Consumes: `digest.content_digest`.
- Produces:
  - `class DataRequest(BaseModel)`: `method: str`, `params: dict[str, Any]`, `category: str`, `as_of: datetime`, `strict_pit: bool`; property `request_digest -> str` (content_digest over method+params+as_of, sorted).
  - `class RawFetch(BaseModel)`: `rows: list[dict]`, `vendor: str`, `subsource: str | None = None`, `fetched_at: datetime`.
  - `class RowSet(BaseModel)`: `rows: list[dict]` (the typed `DataResult` payload for this phase).
  - `class DataSource(Protocol)`: `name: str`; `def capabilities(self) -> set[str]`; `def fetch(self, method: str, req: DataRequest) -> RawFetch` (raises a `DataError` subclass on any non-success).

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_source.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.data.source import DataRequest, RawFetch, RowSet

UTC = timezone.utc


def test_request_digest_stable_and_param_sensitive():
    a = DataRequest(method="get_ohlcv", params={"symbol": "600519", "start": "2026-01-01"},
                    category="core_stock_apis", as_of=datetime(2026, 7, 15, tzinfo=UTC), strict_pit=False)
    b = DataRequest(method="get_ohlcv", params={"start": "2026-01-01", "symbol": "600519"},
                    category="core_stock_apis", as_of=datetime(2026, 7, 15, tzinfo=UTC), strict_pit=False)
    assert a.request_digest == b.request_digest        # key order independent
    c = a.model_copy(update={"params": {"symbol": "000001"}})
    assert a.request_digest != c.request_digest


def test_rawfetch_and_rowset_roundtrip():
    rf = RawFetch(rows=[{"v": 1}], vendor="a_stock", fetched_at=datetime(2026, 7, 15, tzinfo=UTC))
    assert RowSet(rows=rf.rows).rows == [{"v": 1}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_source.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/source.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol
from pydantic import BaseModel
from guanlan_v2.orchestration.digest import content_digest


class DataRequest(BaseModel):
    method: str
    params: dict[str, Any]
    category: str
    as_of: datetime
    strict_pit: bool

    @property
    def request_digest(self) -> str:
        return content_digest({"method": self.method, "params": self.params,
                               "as_of": self.as_of.isoformat()})


class RawFetch(BaseModel):
    rows: list[dict]
    vendor: str
    subsource: str | None = None
    fetched_at: datetime


class RowSet(BaseModel):
    rows: list[dict]


class DataSource(Protocol):
    name: str

    def capabilities(self) -> set[str]: ...

    def fetch(self, method: str, req: DataRequest) -> RawFetch: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_source.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/source.py tests/orchestration/data/test_source.py
git commit -m "feat(orchestration): DataSource protocol + request/result value objects (phase3)"
```

---

## Task 6: `SourceRegistry.dispatch` (narrow fallback router)

**Files:**
- Create: `guanlan_v2/orchestration/data/registry.py`
- Test: `tests/orchestration/data/test_registry.py`

**Interfaces:**
- Consumes: `data.source.DataSource/DataRequest/RawFetch/RowSet`, `data.pit.PitGuard/FreshnessPolicy`, `data.errors.*`, `data.result.DataResult/SourceAttempt`, `enums.DataStatus`, `digest.content_digest`.
- Produces: module constant `OPTIONAL_CATEGORIES: set[str]`; `class SourceRegistry`:
  - `register(self, method: str, vendor: str, source: DataSource) -> None`.
  - `resolve_chain(self, method: str, cfg: Mapping[str, str]) -> list[str]` — `cfg[method]` (or category default) is a comma-separated ordered chain; `"default"`/absent ⇒ all registered vendors for the method; empty resolution ⇒ raise `NotConfiguredError`.
  - `dispatch(self, method: str, req: DataRequest, *, guard: PitGuard, now: datetime, cfg: Mapping[str, str] | None = None, source_config_digest: str = "", freshness: FreshnessPolicy | None = None) -> DataResult[RowSet]` — routes by typed exception exactly as the Global Constraints specify.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_registry.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.enums import DataMode, DataStatus
from guanlan_v2.orchestration.data.source import DataRequest, RawFetch
from guanlan_v2.orchestration.data.pit import PitGuard
from guanlan_v2.orchestration.data.errors import (
    RateLimitError, NotConfiguredError, NoDataError, SourceBrokenError, FutureDataRefused)
from guanlan_v2.orchestration.data.registry import SourceRegistry

UTC = timezone.utc
AS_OF = datetime(2026, 7, 15, tzinfo=UTC)
def _now(): return datetime(2026, 7, 15, 1, tzinfo=UTC)
def _row(day=10): return {"available_at": datetime(2026, 7, day, tzinfo=UTC), "v": 1}


class Stub:
    def __init__(self, name, behavior):
        self.name = name
        self._b = behavior
    def capabilities(self): return {"get_ohlcv"}
    def fetch(self, method, req):
        b = self._b
        if isinstance(b, Exception):
            raise b
        return RawFetch(rows=b, vendor=self.name, fetched_at=_now())


def _req(category="core_stock_apis"):
    return DataRequest(method="get_ohlcv", params={"s": "600519"}, category=category,
                       as_of=AS_OF, strict_pit=False)


def _reg(*sources):
    r = SourceRegistry()
    for s in sources:
        r.register("get_ohlcv", s.name, s)
    return r


def _guard(): return PitGuard(mode=DataMode.ONLINE, strict=False)


def test_first_vendor_success():
    reg = _reg(Stub("a_stock", [_row()]))
    res = reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now())
    assert res.status == DataStatus.OK and res.vendor == "a_stock" and res.data.rows


def test_rate_limit_falls_through():
    reg = _reg(Stub("a_stock", RateLimitError("429")), Stub("tushare", [_row()]))
    res = reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now(),
                       cfg={"get_ohlcv": "a_stock,tushare"})
    assert res.status == DataStatus.OK and res.vendor == "tushare"
    assert [a.vendor for a in res.attempts] == ["a_stock", "tushare"]


def test_no_data_stops_chain():
    reg = _reg(Stub("a_stock", NoDataError(symbol="600519", canonical="600519.SH", detail="delisted")),
               Stub("tushare", [_row()]))
    res = reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now(),
                       cfg={"get_ohlcv": "a_stock,tushare"})
    assert res.status == DataStatus.NO_DATA and res.data is None
    assert [a.vendor for a in res.attempts] == ["a_stock"]   # did NOT continue


def test_future_refused_raises_not_falls_through():
    reg = _reg(Stub("a_stock", [{"available_at": datetime(2026, 7, 20, tzinfo=UTC), "v": 1}]),
               Stub("tushare", [_row()]))
    with pytest.raises(FutureDataRefused):
        reg.dispatch("get_ohlcv", _req(), guard=PitGuard(mode=DataMode.PIT_REPLAY, strict=True),
                     now=_now(), cfg={"get_ohlcv": "a_stock,tushare"})


def test_core_broken_primary_raises():
    reg = _reg(Stub("a_stock", SourceBrokenError("parse failed")))
    with pytest.raises(SourceBrokenError):
        reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now())


def test_optional_category_degrades_to_unavailable():
    reg = _reg(Stub("a_stock", RateLimitError("429")))
    res = reg.dispatch("get_ohlcv", _req(category="signal_data"), guard=_guard(), now=_now())
    assert res.status == DataStatus.UNAVAILABLE and res.data is None and "DEGRADED_SOURCE" in res.badges


def test_core_exhausted_raises_first_retriable():
    reg = _reg(Stub("a_stock", RateLimitError("429")))
    with pytest.raises(RateLimitError):
        reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now())


def test_empty_chain_raises_not_configured():
    reg = SourceRegistry()   # nothing registered
    with pytest.raises(NotConfiguredError):
        reg.dispatch("get_ohlcv", _req(), guard=_guard(), now=_now())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/registry.py
from __future__ import annotations
import uuid
from collections.abc import Mapping
from datetime import datetime
from guanlan_v2.orchestration.data.errors import (
    DataError, FutureDataRefused, NoDataError, NotConfiguredError, RateLimitError, StaleDataError)
from guanlan_v2.orchestration.data.pit import FreshnessPolicy, PitGuard
from guanlan_v2.orchestration.data.result import DataResult, PitAudit, SourceAttempt
from guanlan_v2.orchestration.data.source import DataRequest, DataSource, RowSet
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataStatus

OPTIONAL_CATEGORIES = {"signal_data", "macro_data", "prediction_markets"}


class SourceRegistry:
    def __init__(self) -> None:
        self._methods: dict[str, dict[str, DataSource]] = {}

    def register(self, method: str, vendor: str, source: DataSource) -> None:
        self._methods.setdefault(method, {})[vendor] = source

    def resolve_chain(self, method: str, cfg: Mapping[str, str]) -> list[str]:
        available = list(self._methods.get(method, {}).keys())
        raw = (cfg or {}).get(method, "default")
        if raw and raw != "default":
            chain = [v.strip() for v in raw.split(",") if v.strip() and v.strip() in self._methods.get(method, {})]
        else:
            chain = available
        if not chain:
            raise NotConfiguredError(f"no configured vendor for method {method}")
        return chain

    def _result(self, *, status, method, req, chain, source_config_digest, attempts, pit_audit,
                now, data=None, vendor=None, badges=(), coverage=None, degradation_reason=None):
        semantic = {"method": method, "request_digest": req.request_digest, "status": status.value,
                    "chain": chain, "rows": (data.rows if data is not None else None)}
        return DataResult[RowSet](
            id=uuid.uuid4().hex, method=method, request_digest=req.request_digest, status=status,
            data=data, coverage=coverage, degradation_reason=degradation_reason, vendor=vendor,
            resolved_vendor_chain=chain, source_config_digest=source_config_digest,
            fetched_at=now, content_digest=content_digest(semantic),
            audit_digest=content_digest({"attempts": [a.model_dump(mode="json") for a in attempts]}),
            attempts=attempts, pit_audit=pit_audit, badges=list(badges))

    def dispatch(self, method: str, req: DataRequest, *, guard: PitGuard, now: datetime,
                 cfg: Mapping[str, str] | None = None, source_config_digest: str = "",
                 freshness: FreshnessPolicy | None = None) -> DataResult[RowSet]:
        chain = self.resolve_chain(method, cfg or {})
        attempts: list[SourceAttempt] = []
        first_error: DataError | None = None

        for vendor in chain:
            src = self._methods[method][vendor]
            started = now
            try:
                raw = src.fetch(method, req)
                rows, audit = guard.check_rows(raw.rows, as_of=req.as_of, now=now, freshness=freshness)
                attempts.append(SourceAttempt(vendor=vendor, subsource=raw.subsource, configured=True,
                                              outcome="success", started_at=started, finished_at=now))
                return self._result(status=DataStatus.OK, method=method, req=req, chain=chain,
                                    source_config_digest=source_config_digest, attempts=attempts,
                                    pit_audit=audit, now=now, data=RowSet(rows=rows), vendor=vendor)
            except RateLimitError:
                attempts.append(SourceAttempt(vendor=vendor, configured=True, outcome="rate_limited",
                                              fallback_reason="rate limited", started_at=started, finished_at=now))
                if first_error is None:
                    first_error = RateLimitError(f"{vendor} rate limited")
                continue
            except NotConfiguredError as e:
                attempts.append(SourceAttempt(vendor=vendor, configured=False, outcome="not_configured",
                                              fallback_reason=str(e), started_at=started, finished_at=now))
                if first_error is None:
                    first_error = e
                continue
            except NoDataError:
                attempts.append(SourceAttempt(vendor=vendor, configured=True, outcome="no_data",
                                              started_at=started, finished_at=now))
                audit = PitAudit(mode=guard.mode, as_of=req.as_of, rows_seen=0, rows_returned=0,
                                 future_rows=0, missing_available_at_rows=0, guard_result="passed")
                return self._result(status=DataStatus.NO_DATA, method=method, req=req, chain=chain,
                                    source_config_digest=source_config_digest, attempts=attempts,
                                    pit_audit=audit, now=now, vendor=vendor)
            except StaleDataError:
                attempts.append(SourceAttempt(vendor=vendor, configured=True, outcome="stale",
                                              started_at=started, finished_at=now))
                audit = PitAudit(mode=guard.mode, as_of=req.as_of, rows_seen=0, rows_returned=0,
                                 future_rows=0, missing_available_at_rows=0, guard_result="passed")
                return self._result(status=DataStatus.STALE, method=method, req=req, chain=chain,
                                    source_config_digest=source_config_digest, attempts=attempts,
                                    pit_audit=audit, now=now, vendor=vendor)
            except FutureDataRefused:
                attempts.append(SourceAttempt(vendor=vendor, configured=True, outcome="future_refused",
                                              started_at=started, finished_at=now))
                raise
            except DataError:
                attempts.append(SourceAttempt(vendor=vendor, configured=True, outcome="error",
                                              started_at=started, finished_at=now))
                raise

        # chain exhausted only via RateLimit / NotConfigured
        audit = PitAudit(mode=guard.mode, as_of=req.as_of, rows_seen=0, rows_returned=0,
                         future_rows=0, missing_available_at_rows=0, guard_result="passed")
        if req.category in OPTIONAL_CATEGORIES:
            return self._result(status=DataStatus.UNAVAILABLE, method=method, req=req, chain=chain,
                                source_config_digest=source_config_digest, attempts=attempts,
                                pit_audit=audit, now=now, badges=["DEGRADED_SOURCE"])
        assert first_error is not None
        raise first_error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_registry.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/registry.py tests/orchestration/data/test_registry.py
git commit -m "feat(orchestration): SourceRegistry narrow-fallback dispatch (phase3)"
```

---

## Task 7: `DataReader` facade + `render_for_prompt`

**Files:**
- Create: `guanlan_v2/orchestration/data/reader.py`
- Test: `tests/orchestration/data/test_reader.py`

**Interfaces:**
- Consumes: `data.registry.SourceRegistry`, `data.source.DataRequest/RowSet`, `data.pit.PitGuard/FreshnessPolicy`, `data.result.DataResult`, `context.DataContext`, `enums.DataStatus`.
- Produces:
  - `class DataReader` — `__init__(self, registry, ctx: DataContext, guard: PitGuard, *, now, cfg=None, freshness_by_category=None)`; methods `get_ohlcv(sym, start, end)`, `get_indicators(sym, indicator, curr_date, look_back_days=30)`, `get_fundamentals(ticker, curr_date)`, `get_news(ticker, start, end)`, `get_signal(method, sym, curr_date)` — each builds a `DataRequest` (category set per method), calls `registry.dispatch`, returns `DataResult[RowSet]`.
  - `render_for_prompt(result: DataResult) -> str` — an **unforgeable** data block: a provenance header (`status/as_of/vendor/coverage/badges`), and on `OK` the rows; on `NO_DATA`/`STALE`/`UNAVAILABLE` the explicit sentinel `"⚠ 数据不可用(status)…不得编造数值"`. Never returns an empty string.

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestration/data/test_reader.py
from __future__ import annotations
from datetime import datetime, timezone
from guanlan_v2.orchestration.enums import DataMode, DataBackend, DataStatus
from guanlan_v2.orchestration.context import DataContext, ClockSpec
from guanlan_v2.orchestration.data.source import RawFetch
from guanlan_v2.orchestration.data.pit import PitGuard
from guanlan_v2.orchestration.data.registry import SourceRegistry
from guanlan_v2.orchestration.data.reader import DataReader, render_for_prompt

UTC = timezone.utc
AS_OF = datetime(2026, 7, 15, tzinfo=UTC)
def _now(): return datetime(2026, 7, 15, 1, tzinfo=UTC)


class Stub:
    name = "a_stock"
    def __init__(self, rows): self.rows = rows
    def capabilities(self): return {"get_ohlcv"}
    def fetch(self, method, req):
        return RawFetch(rows=self.rows, vendor="a_stock", fetched_at=_now())


def _ctx():
    clock = ClockSpec(as_of=AS_OF, timezone="UTC", calendar_id="XSHG", clock_version="1")
    return DataContext(as_of=AS_OF, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
                       strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
                       source_config_digest="c", data_snapshot_id="s")


def _reader(rows):
    reg = SourceRegistry(); reg.register("get_ohlcv", "a_stock", Stub(rows))
    return DataReader(reg, _ctx(), PitGuard(mode=DataMode.ONLINE, strict=False), now=_now)


def test_get_ohlcv_ok():
    r = _reader([{"available_at": datetime(2026, 7, 10, tzinfo=UTC), "close": 1700}])
    res = r.get_ohlcv("600519", "2026-01-01", "2026-07-15")
    assert res.status == DataStatus.OK and res.data.rows[0]["close"] == 1700


def test_render_ok_has_provenance_header_and_rows():
    r = _reader([{"available_at": datetime(2026, 7, 10, tzinfo=UTC), "close": 1700}])
    text = render_for_prompt(r.get_ohlcv("600519", "2026-01-01", "2026-07-15"))
    assert "vendor: a_stock" in text and "1700" in text


def test_render_no_data_is_sentinel_never_empty():
    reg = SourceRegistry()
    reg.register("get_signal", "a_stock", _NoData())
    reader = DataReader(reg, _ctx(), PitGuard(mode=DataMode.ONLINE, strict=False), now=_now)
    text = render_for_prompt(reader.get_signal("get_signal", "600519", "2026-07-15"))
    assert text.strip() != "" and "不得编造" in text


class _NoData:
    name = "a_stock"
    def capabilities(self): return {"get_signal"}
    def fetch(self, method, req):
        from guanlan_v2.orchestration.data.errors import NoDataError
        raise NoDataError(symbol="600519", canonical="600519.SH", detail="no coverage")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_reader.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# guanlan_v2/orchestration/data/reader.py
from __future__ import annotations
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Callable
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.pit import FreshnessPolicy, PitGuard
from guanlan_v2.orchestration.data.registry import SourceRegistry
from guanlan_v2.orchestration.data.result import DataResult
from guanlan_v2.orchestration.data.source import DataRequest, RowSet
from guanlan_v2.orchestration.enums import DataStatus

_CATEGORY = {
    "get_ohlcv": "core_stock_apis", "get_indicators": "technical_indicators",
    "get_verified_snapshot": "core_stock_apis", "get_fundamentals": "fundamental_data",
    "get_news": "news_data",
}


class DataReader:
    def __init__(self, registry: SourceRegistry, ctx: DataContext, guard: PitGuard, *,
                 now: Callable[[], datetime], cfg: Mapping[str, str] | None = None,
                 freshness_by_category: Mapping[str, FreshnessPolicy] | None = None):
        self._reg = registry
        self._ctx = ctx
        self._guard = guard
        self._now = now
        self._cfg = dict(cfg or {})
        self._fresh = dict(freshness_by_category or {})

    def _call(self, method: str, params: dict, *, category: str) -> DataResult[RowSet]:
        req = DataRequest(method=method, params=params, category=category,
                          as_of=self._ctx.as_of, strict_pit=self._ctx.strict_pit)
        return self._reg.dispatch(method, req, guard=self._guard, now=self._now(), cfg=self._cfg,
                                  source_config_digest=self._ctx.source_config_digest,
                                  freshness=self._fresh.get(category))

    def get_ohlcv(self, sym: str, start: str, end: str) -> DataResult[RowSet]:
        return self._call("get_ohlcv", {"symbol": sym, "start": start, "end": end},
                          category=_CATEGORY["get_ohlcv"])

    def get_indicators(self, sym: str, indicator: str, curr_date: str,
                       look_back_days: int = 30) -> DataResult[RowSet]:
        return self._call("get_indicators", {"symbol": sym, "indicator": indicator,
                                             "curr_date": curr_date, "look_back_days": look_back_days},
                          category=_CATEGORY["get_indicators"])

    def get_fundamentals(self, ticker: str, curr_date: str) -> DataResult[RowSet]:
        return self._call("get_fundamentals", {"ticker": ticker, "curr_date": curr_date},
                          category=_CATEGORY["get_fundamentals"])

    def get_news(self, ticker: str, start: str, end: str) -> DataResult[RowSet]:
        return self._call("get_news", {"ticker": ticker, "start": start, "end": end},
                          category=_CATEGORY["get_news"])

    def get_signal(self, method: str, sym: str, curr_date: str) -> DataResult[RowSet]:
        return self._call(method, {"symbol": sym, "curr_date": curr_date}, category="signal_data")


def render_for_prompt(result: DataResult) -> str:
    head = (f"# Data · {result.method}\n"
            f"# status: {result.status.value} · as_of: {result.pit_audit.as_of.isoformat()} "
            f"· vendor: {result.vendor or 'none'} · coverage: {result.coverage} "
            f"· badges: {','.join(result.badges) or '-'}")
    if result.status in (DataStatus.OK, DataStatus.DEGRADED) and result.data is not None:
        body = json.dumps(getattr(result.data, "rows", result.data.model_dump()),
                          ensure_ascii=False, default=str)
        return f"{head}\n{body}"
    return (f"{head}\n⚠ 数据不可用(status={result.status.value})。该字段无可用数据,"
            "不得编造数值;如需请改用可得口径或显式标注缺失。")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_reader.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole data suite + commit**

Run: `pytest tests/orchestration/data/ -v`
Expected: PASS (all phase-3 data/PIT tests green)

```bash
git add guanlan_v2/orchestration/data/reader.py tests/orchestration/data/test_reader.py
git commit -m "feat(orchestration): DataReader facade + render_for_prompt (phase3)"
```

---

## Self-Review (completed by plan author)

**Spec coverage (§4 + §12 phase 3 data half):** error taxonomy incl. `FutureDataRefused`/`MissingAvailabilityRefused`/`SourceBrokenError` ✓(T1) · `normalize_symbol` syntactic + path-safe ✓(T2) · `resolve_name_to_code` rejects concept names + `resolve_limit_rule` unknown-ST honesty ✓(T3) · `PitGuard` `available_at` cutoff + strict `FutureDataRefused` + freshness ✓(T4) · `DataSource`/`DataRequest`/`RawFetch`/`RowSet` ✓(T5) · `SourceRegistry.dispatch` narrow fallback (RateLimit/NotConfigured→next, NoData/Stale→typed stop, Future/other→raise, optional→UNAVAILABLE, core→raise) ✓(T6) · `DataReader` + `render_for_prompt` never-empty sentinel ✓(T7). **Deferred (correctly out of scope):** the **memory facade** → Phase 3b (needs `memory_ops`/console/curator write-path); real vendor adapters (akshare/tushare/mootdx/东财) + intra-adapter locale redundancy + `_em_get` throttle (acquisition, later); `DEGRADED` partial-coverage path (needs real coverage computation); `resolve_vendor_chain` freeze into `DataContext` at run start (wired in Phase 2/5 runtime); `PitReader`/`pit_store` backend adapter + `news_coverage_floor` (Phase 5 Bootstrap/落子 adapter).

**Placeholder scan:** none — every code step is complete and runnable.

**Type consistency:** `DataRequest`/`RawFetch`/`RowSet` identical across T5/T6/T7; `PitGuard(mode=, strict=)` + `check_rows(rows, *, as_of, now, freshness)` identical in T4/T6/T7; `DataResult[RowSet]` returned by `dispatch` and all `DataReader` methods; `OPTIONAL_CATEGORIES` used in T6 dispatch and referenced by `get_signal` category `"signal_data"` in T7; `NoDataError(symbol=, canonical=, detail=)` keyword form identical in T1/T6/T7.

---

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
