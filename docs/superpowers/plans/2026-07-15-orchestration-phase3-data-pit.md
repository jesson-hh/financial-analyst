# Orchestration Phase 3 · 数据 / PIT 层 Implementation Plan

> **Execution note:** implement task-by-task with a review checkpoint after the Phase 1/2 handoff gate, the Phase 3 registry snapshot, PIT/cache invariants, and runtime integration. Steps use checkbox (`- [ ]`) syntax for tracking; do not require an environment-specific execution skill.

**Goal:** Build the typed, PIT-safe multi-source data interface for `guanlan_v2/orchestration/data/` — error taxonomy, syntactic symbol normalization, concrete `PitRecord` payloads, immutable routing/snapshot/cache identities, `PitGuard`, narrow-fallback dispatch and deterministic untrusted rendering — then close Phase 3 with a unified PIT-safe read/proposal facade over the existing AgentMemory and console stores. **Vendor acquisition stays stubbed**; no third memory store or accepted-write path is introduced.

**Architecture:** Extends the cumulative sealed Phase 2 runtime registry (which already contains the unchanged Phase 1 public contracts) and the Phase 2 engine-neutral runtime ports. A sealed, versioned `DataSourceRegistry` resolves a catalog-owned ordered vendor chain into an immutable `DataRoutingSnapshot`; a `DataSnapshotManifest` and `DataCacheKey` bind every read to the exact as-of, vintage, schema, source configuration and registry digests. `DataContext` is the sole authority for mode, strictness, backend, resolved chains and snapshot identity. `PitGuard.from_context` rejects future or missing availability before typed conversion; only `RateLimitError` / `NotConfiguredError` may advance the already-frozen chain. Data outcomes and refusal audits persist through Phase 2 ports before workers receive refs or an exception. Task 9 then creates a new immutable cumulative data+memory registry/catalog, freezes PIT-visible memory snapshots from the existing stores, filters before ranking and preserves proposal/human-review writers. Data and memory rendering are deterministic, versioned and explicitly untrusted.

**Tech Stack:** Python ≥3.11, Pydantic v2, `re`, `pytest`. All modules `from __future__ import annotations`. Depends on Phase 1 (`digest.py`, `refs.py`, `schema_registry.py`, `context.py`, `schemas.py`, `data/{symbols,result}.py`), Phase 2 runtime ports (`PayloadStore`, `EventStore`, `CapabilityGateway`, `ArtifactPool`, `AuthoritativeClock`, prompt assembly and replay), and read/proposal adapters over the existing AgentMemory/memory_ops/console modules.

> **Scope note.** The spec §12 phase 3 bundles “data/PIT + memory facade”; this plan now covers both in Task 0–9. Tasks 0–8 freeze and integrate the data half, and Task 9 closes memory PIT/snapshot/proposal semantics in the same reviewed phase. There is no dependency on a nonexistent separate Phase 3b file; Phase 4/5 may start only after all Task 0–9 exit gates pass.

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-07-15-orchestration-framework-design.md`). Every task implicitly includes these.

- **Narrow fallback.** Cross-vendor fallback fires **only** on `RateLimitError` / `NotConfiguredError`. `NoDataError` and `StaleDataError` terminate the current chain with a typed result (no continue). `FutureDataRefused` and any other `DataError` **raise** (a broken primary must be loud, never masked by a fallback's answer).
- **Never fabricate.** No path returns an empty string a model could fill in; missing data is a typed `NO_DATA`/`STALE`/`UNAVAILABLE` `DataResult`, and `render_for_prompt` emits an explicit "do not fabricate" sentinel.
- **PIT is `available_at`, not period-end.** `PitGuard` compares each raw row/vintage's `available_at` (当时可知时间). Any `available_at > as_of` → `FutureDataRefused`; any required row missing it → `MissingAvailabilityRefused`. Neither case may be silently filtered or trigger fallback. `PIT_REPLAY` additionally requires strict mode, a frozen snapshot and matching vintage manifest, and may never fall through to LIVE. Freshness is a versioned per-method/category policy using the frozen trading clock, not a global `MAX_STALE_DAYS`.
- **`normalize_symbol` is purely syntactic** (no network): only normalizes a code; never infers ST, listing stage, or the day's price-limit. The 6-digit result must match `^[0-9]{6}$` before it may be used as a cache key. `resolve_name_to_code` rejects industry/concept names (forces the caller to pass a 6-digit code, never guesses).
- **`DataResult` invariants (Phase 1):** `OK`/`DEGRADED` carry data; `NO_DATA`/`STALE`/`UNAVAILABLE` do not; `DEGRADED` needs `coverage` + `degradation_reason`. Every result keeps the full `attempts` list; `content_digest` excludes wall-clock, `audit_digest` covers it.
- Every public data DTO inherits the Phase 1 `ContractModel`/`DigestModel`, declares a closed `schema_version: Literal["N"]`, rejects unknown/coerced values, and is immutable when it records a fact. All persisted digests are computed or verified by pure builders; tests use real `DigestHex` values, never placeholders.
- Optional/core behavior belongs to the versioned `DataMethodSpec`; it is not a mutable module-level set. Optional chain exhaustion returns `UNAVAILABLE`; core exhaustion raises the first retryable error after its audit is persisted.
- **Memory PIT before relevance.** Freeze/resolve the exact memory snapshot, filter availability/validity/review/role/session in storage/facade space, and only then rank/top-k/fallback. Workers submit proposals only; existing reviewed writers remain the sole accepted-mutation paths.
- Refusal and other raised paths persist an idempotent request/attempt/PIT audit record through the authoritative Phase 2 audit-only `EventRefusalAuditSink` **before** re-raising. Persist failure is loud; it never converts a PIT refusal into a successful or fallback result.
- Run tests from repo root `G:\guanlan-v2` with `pytest`; do not stage unrelated worktree changes.

---

## File Structure (created in this plan)

| File | Responsibility |
|---|---|
| `guanlan_v2/orchestration/data/errors.py` | error taxonomy (the router's whole control surface) |
| `guanlan_v2/orchestration/data/calendar.py` | exact read-only trading-calendar port/resolver; no global “today” calendar |
| `guanlan_v2/orchestration/data/symbols.py` (append) | `normalize_symbol` / `resolve_name_to_code` / `resolve_limit_rule` |
| `guanlan_v2/orchestration/data/pit.py` | `PitGuard.from_context` + versioned `FreshnessPolicy` |
| `guanlan_v2/orchestration/data/source.py` | strict request/raw carriers, concrete `PitRecord` batches and `DataSource` protocol |
| `guanlan_v2/orchestration/data/catalog.py` | reviewed data CapabilityDescriptors + cumulative Phase 3 `WorkerCatalogSnapshot` builder |
| `guanlan_v2/orchestration/data/snapshot.py` | `DataRoutingSnapshot` / `DataSnapshotManifest` / `DataCacheKey` builders |
| `guanlan_v2/orchestration/data/registry.py` | sealed `DataSourceRegistry` + narrow dispatch |
| `guanlan_v2/orchestration/data/render.py` | registered `RenderedDataBlock` contract + deterministic renderer |
| `guanlan_v2/orchestration/data/reader.py` | `DataReader` facade |
| `guanlan_v2/orchestration/data/runtime.py` | Phase 2 payload/event/capability/provenance bridge |
| `guanlan_v2/orchestration/memory/models.py` | strict memory record/snapshot/query/selection/proposal/render contracts |
| `guanlan_v2/orchestration/memory/adapters.py` | read-only adapters over existing AgentMemory and console stores |
| `guanlan_v2/orchestration/memory/store.py` | unified PIT-visible snapshot facade; filter before rank/top-k |
| `guanlan_v2/orchestration/memory/proposals.py` | proposal-only delegation to existing reviewed write paths |
| `guanlan_v2/orchestration/memory/{schema_registry,catalog,runtime}.py` | immutable full-Phase-3 registry/catalog extension and Phase 2 snapshot/replay bridge |
| `tests/orchestration/golden/data_schema_manifest_v1.json` | Phase 3 schema-registry extension golden |
| `tests/orchestration/golden/data_catalog_manifest_v1.json` | reviewed data-capability/catalog extension golden |
| `tests/orchestration/golden/phase3_full_schema_manifest_v1.json` | immutable data+memory cumulative registry golden; does not replace data-only golden |
| `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json` | immutable data+memory catalog extension golden; does not replace data-only golden |
| `tests/orchestration/golden/memory_capture_policy_v1.json` | reviewed conservative legacy-memory capture/ranking policy |
| `tests/orchestration/golden/data_source_manifest_v1.json` | method/source/default-route manifest golden |
| `tests/orchestration/golden/limit_rule_policy_v1.json` | versioned as-of limit-rule policy material/digest |
| `tests/orchestration/data/` | one test module per source module |

---

## Task 0: Phase 1 / Phase 2 handoff gate

**Files:**
- Create: `tests/orchestration/data/test_phase_handoff.py`
- Read only: Phase 1 contract/registry implementation and Phase 2 runtime ports.

**Required handoff:**

- Phase 1 provides strict `ContractModel`/`DigestModel`, canonical semantic/audit digest builders, `SchemaRef`, `PayloadRef`, the sole generic public `PayloadRef`, sealed schema-registry snapshots, `DataResult.build`, `PitRecord`, `PitAudit`, `SourceAttempt`, `DataContext`, `ContextRuntimeRequirements`, `ContextSnapshot`, the complete ready/terminal-partial `InputSnapshot`/`InputArtifactBinding` ABI, `MemoryRecordRef`, canonical `EmptyMemorySnapshot/EmptyMemorySelection`, `Artifact`, `Provenance` and `ToolCallRecord`. The exact memory ABI is already frozen there: record/revision/availability/content identity, typed memory snapshot/selection refs, semantic `memory_snapshot_hash/past_context_hash/memory_session_id`, optional typed runtime requirements and canonical InputSnapshot memory refs. NodeRun and successful Provenance carry exact ToolCallRecord, typed DataResult and remaining execution-evidence tuples; failed/no-Artifact NodeRuns retain all three.
- Phase 2 provides its sealed cumulative runtime registry/golden (Phase 1 public models plus Phase 2 control facts and registered `ExecutionBridgeDescriptor/BridgeStaticSupportSummary/BridgeEvidenceRecorded/PromptUntrustedBlockRef/PromptAssemblyRecord`), `SchemaRegistryResolver`, read-only `CatalogRuntime`, pure descriptor-bound support analyzers, service-owned `ExecutionBridgeResolver` with one global ordinal sequencer, `BridgeEvidenceWriter/Journal` and two-stage `prepare_input → InputSnapshot freeze → RUNNING/open_execution/freeze_for_execution`, the canonical `PHASE2_STATIC_CATALOG_DIGEST/phase2_static_catalog_snapshot()`, and `AuthoritativeClock`; `PayloadStore.put(schema_ref, payload, *, namespace, idempotency_key) -> PayloadRef` plus `get(ref, *, expected_schema_ref)` with digest/schema verification; the same-backend `RuntimeStateCellStore` and staged typed-ref/state-CAS `RuntimeUnitOfWork`; append-only persist-before-publish `EventStore.append(EventAppendRequest) -> RunEvent`; audit-only refusal sink; two-stage `CapabilityGateway.begin/invoke → finalize_success | reject` as the only source-call boundary; `ArtifactPool`; and zero-live-I/O replay. RuntimeSupportReport binds every activated bridge descriptor/config/provider/analyzer ref, embeds each verified per-node tool-call summary and binds ContextRuntimeRequirements before reservation. `LLM` execution merges all provider blocks into one main prompt record and ModelGateway rehashes the exact assembled request before its single send; `DETERMINISTIC` creates none; both retain branch-complete evidence on failed/no-Artifact NodeRuns.
- Low-level PayloadStore/EventStore keep their exact SchemaRef/PayloadRef contracts, while provider writes go only through BridgeEvidenceWriter and `CapabilityGateway.finalize_success` consumes the resulting exact `PayloadRef`s. Phase 3 never passes Python classes, physical paths or free-floating schema/locator pairs into prompt, gateway or generic execution evidence.
- A Phase 1 or Phase 2 exit gate failure blocks this plan. Do not locally redefine a missing upstream type to make an import pass.

- [ ] **Step 1: Write handoff conformance tests**

Required tests inspect real signatures and exercise representative valid objects:

1. every required upstream symbol imports from its owning module;
2. payload round-trip validates the exact `SchemaRef`, `PayloadRef.content_digest` and namespace; `PayloadRef` round-trips through the inherited registry, rejects wrong schema/namespace/content and treats object-ID relocation as audit-only;
3. `PayloadRef.object_id` and snapshot-locator-only relocation change audit identity but not a parent semantic digest;
4. `EventAppendRequest` has no caller-selected cursor, append is authoritative before publish and idempotent on the same key; refusal audit is separate and cannot create a public event;
5. capability invocation cannot bypass `CapabilityGateway`; a pending data invocation yields no evidence-counting ToolCallRecord until `finalize_success`, while `reject` writes only refusal audit;
6. replay resolves a recorded tool/data result without invoking a live handler;
7. `InputSnapshot` and `Artifact.Provenance` can bind immutable data-result refs/digests;
8. the Phase 1 memory/InputSnapshot ABI imports with exact closed fields/projections: the two canonical empty models round-trip as PayloadRefs, typed-object relocation is semantic-invariant, schema/content drift is semantic, selection ref content equals `past_context_hash`, non-empty memory requires ContextRuntimeRequirements, and full Artifact/DataResult/Memory refs enter InputSnapshot without a Phase 3 redefinition;
9. Phase 2's public PromptAssemblyRecord round-trips only in `main`, is persisted before an `LLM` model call, and its typed ref reaches NodeRun plus successful Artifact with all tool/data/generic evidence tuples equal; ModelGateway rejects request/ref/assembler/order drift before send. `DETERMINISTIC` creates no prompt record, while either branch retains complete evidence on failed/no-Artifact paths;
10. the generic bridge descriptor/support summary round-trips from exact catalog material; RuntimeSupport rejects missing/drifted/dynamic provider/analyzer semantics and wrong context requirements before budget, evaluates REQUIRED/FORBIDDEN from verified call bounds, memory-only pre-input refs are frozen into InputSnapshot before execution, RUNNING precedes data/capability I/O, providers write through BridgeEvidenceWriter and cannot call PromptAssembler, and reversed multi-provider completion preserves the generic contribution order plus each Phase 1 evidence-tuple canonicalizer;
11. the generic RuntimeStateCellStore/RuntimeUnitOfWork can atomically put a fake typed payload and CAS head + operation-result cells to its staged ref; same-operation replay returns the original ref after a later head move, and stale/failed commit exposes none.

- [ ] **Step 2: Record the reviewed upstream registry/catalog/runtime digests in the test fixture**

The fixture records the exact Phase 2 registry digest and canonical `PHASE2_STATIC_CATALOG_DIGEST`, not local paths, an earlier pilot catalog or mutable singleton identities.

- [ ] **Step 3: Run the complete upstream suite and the frozen Phase 3 handoff gate**

Run from the pre-Phase-3 implementation state: `pytest tests/orchestration -v`.

Expected: the complete Phase 1 + Phase 2 suite and `tests/orchestration/data/test_phase_handoff.py` PASS **after** the reviewed digests have been recorded. If an upstream contract is absent, incompatible or drifted, stop and fix its owning phase rather than creating a duplicate here.

- [ ] **Step 4: Commit**

```bash
git add tests/orchestration/data/test_phase_handoff.py
git commit -m "test(orchestration): gate phase3 on phase1 and phase2 contracts"
```

---

## Task 1: Error taxonomy

**Files:**
- Create: `guanlan_v2/orchestration/data/errors.py`
- Test: `tests/orchestration/data/__init__.py` (empty), `tests/orchestration/data/test_errors.py`

**Interfaces:**
- Produces: `DataError(Exception)`; `NoDataError(DataError)` with `__init__(self, *, symbol, canonical, detail)`; `StaleDataError(DataError)` with `__init__(self, detail, *, latest_available_at, pit_audit=None)`; `RateLimitError(DataError)`; `NotConfiguredError(DataError, ValueError)`; `FutureDataRefused(DataError)` with `__init__(self, detail, *, future_rows)`; `MissingAvailabilityRefused(DataError)`; non-fallback `DataIntegrityError(DataError)` with `SourceBrokenError`, `RoutingConfigurationError(DataIntegrityError, ValueError)`, `SnapshotMismatchError`, `CacheIntegrityError` and `LiveFallbackRefused` subclasses.

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
    assert issubclass(e.SourceBrokenError, e.DataIntegrityError)
    assert issubclass(e.RoutingConfigurationError, (e.DataIntegrityError, ValueError))
    assert issubclass(e.SnapshotMismatchError, e.DataIntegrityError)
    assert issubclass(e.CacheIntegrityError, e.DataIntegrityError)
    assert issubclass(e.LiveFallbackRefused, e.DataIntegrityError)


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
    def __init__(self, detail: str, *, latest_available_at: datetime | None = None,
                 pit_audit=None):
        self.detail = detail
        self.latest_available_at = latest_available_at
        self.pit_audit = pit_audit
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


class DataIntegrityError(DataError):
    pass


class SourceBrokenError(DataIntegrityError):
    pass


class RoutingConfigurationError(DataIntegrityError, ValueError):
    pass


class SnapshotMismatchError(DataIntegrityError):
    pass


class CacheIntegrityError(DataIntegrityError):
    pass


class LiveFallbackRefused(DataIntegrityError):
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_errors.py -v`
Expected: PASS.

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
- Produces: `normalize_symbol(raw: StrictStr) -> Symbol` — accepts only the complete bare (`"600519"`), dotted (`"600519.SH"`), or engine (`"SH600519"`) grammar; infers `exchange`/`board` from 号段 (688→SH/star, 300|301→SZ/chinext, leading 8|4→BJ/bj, leading 6→SH/main, else SZ/main). Embedded codes, non-strings, multiple codes and an explicit exchange conflicting with the inferred exchange are rejected rather than silently repaired. The returned `Symbol.code` always matches `^[0-9]{6}$` before it may enter a cache key.

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


@pytest.mark.parametrize("raw", ["x600519", "600519-extra", "600519 000001", 600519])
def test_rejects_partial_or_coerced_input(raw):
    with pytest.raises((TypeError, ValueError)):
        normalize_symbol(raw)


@pytest.mark.parametrize("raw", ["600519.SZ", "SZ600519"])
def test_rejects_explicit_exchange_conflict(raw):
    with pytest.raises(ValueError, match="exchange"):
        normalize_symbol(raw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_normalize_symbol.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_symbol'`

- [ ] **Step 3: Write minimal implementation** (append to `guanlan_v2/orchestration/data/symbols.py`)

```python
# ── append to guanlan_v2/orchestration/data/symbols.py ──
import re as _re

_BARE = _re.compile(r"^(?P<code>[0-9]{6})$")
_DOTTED = _re.compile(r"^(?P<code>[0-9]{6})\.(?P<exchange>SH|SZ|BJ)$")
_ENGINE = _re.compile(r"^(?P<exchange>SH|SZ|BJ)(?P<code>[0-9]{6})$")


def normalize_symbol(raw: str) -> Symbol:
    """Purely syntactic (no network); never coerces or partially matches."""
    if type(raw) is not str:
        raise TypeError("symbol must be a string")
    s = raw.strip().upper()
    m = _BARE.fullmatch(s) or _DOTTED.fullmatch(s) or _ENGINE.fullmatch(s)
    if not m:
        raise ValueError(f"unsupported A-share symbol grammar: {raw!r}")
    code = m.group("code")
    explicit_exchange = m.groupdict().get("exchange")
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
    if explicit_exchange is not None and explicit_exchange != exchange:
        raise ValueError(
            f"explicit exchange {explicit_exchange} conflicts with code-derived {exchange}")
    return Symbol(code=code, exchange=exchange, board=board)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_normalize_symbol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/symbols.py tests/orchestration/data/test_normalize_symbol.py
git commit -m "feat(orchestration): syntactic normalize_symbol (phase3)"
```

---

## Task 3: `resolve_name_to_code` + `resolve_limit_rule`

**Files:**
- Create: `guanlan_v2/orchestration/data/calendar.py`
- Modify: `guanlan_v2/orchestration/data/symbols.py` (append)
- Create: `tests/orchestration/data/calendar_fixtures.py`
- Test: `tests/orchestration/data/test_calendar.py`
- Test: `tests/orchestration/data/test_resolve.py`
- Create: `tests/orchestration/golden/limit_rule_policy_v1.json`

**Interfaces:**
- Consumes: `Symbol`, `InstrumentMeta`, `LimitRule`, `ContentRef` (Phase 1), `normalize_symbol` and the exact `calendar_id` frozen in `DataContext/ClockSpec`.
- Produces:
  - `TradingCalendar(Protocol)`: read-only `calendar_id`, exact versioned `material_ref: ContentRef`, `is_session(date)` and deterministic `sessions_between(start, end)` over one immutable calendar material. It never reads wall clock or mutable global holidays.
  - `TradingCalendarResolver`: service-owned mapping from the full calendar `ContentRef` to a trusted implementation. Resolution verifies material digest and exact `calendar_id`; missing/drifted material is loud. Tests use an immutable fake behind this same port.
  - `LimitRulePolicy(DigestModel)`: closed versioned rule table plus exact trading-calendar material ref and verified policy digest; Task 5 registers it. There is no unversioned module-global policy.
  - internal pure helper `resolve_name_to_code(raw: StrictStr, name_map: Mapping[str, StrictStr]) -> Symbol`. `name_map` may only be extracted from the digest-verified, PIT-filtered `InstrumentNameRows` registered in Task 5; capability/public APIs never accept a caller-supplied current map. A direct symbol uses `normalize_symbol`; an unknown CJK industry/concept term is rejected rather than guessed.
  - `resolve_limit_rule(sym: Symbol, as_of: UtcDateTime, meta: InstrumentMeta, *, policy: LimitRulePolicy, calendar: TradingCalendar | None) -> LimitRule`. It verifies `meta.symbol == sym`, `listed_at <= as_of`, and `metadata_available_at <= as_of` before using `is_st`. Missing/future metadata or an uncovered listing-policy window returns `pct=None` with an explicit reason. The injected digest-frozen policy selected by `as_of` owns ST/board/listing-stage rules; any session-based branch requires the exact matching calendar, and unavailable/mismatched calendar returns explicit unknown rather than guessing.

- [ ] **Step 1: Write the failing test**

The test module constructs one verified `LimitRulePolicy` and immutable fake `TradingCalendar`, and passes them explicitly to every `resolve_limit_rule` call shown below; no example may rely on a module-global/current calendar. `test_calendar.py` separately covers material/calendar-ID mismatch, deterministic session counts, holidays and missing material.

```python
# tests/orchestration/data/test_resolve.py
from __future__ import annotations
from datetime import datetime, timezone
import pytest
from guanlan_v2.orchestration.data.symbols import (
    Symbol, InstrumentMeta, resolve_name_to_code, resolve_limit_rule)
from tests.orchestration.data.calendar_fixtures import CALENDAR, LIMIT_POLICY

UTC = timezone.utc
_MAP = {"贵州茅台": "600519"}  # extracted from a verified InstrumentNameRows fixture
META_AT = datetime(2026, 7, 14, tzinfo=UTC)
LISTED_AT = datetime(2001, 8, 27, tzinfo=UTC)


def _limit(sym, as_of, meta):
    return resolve_limit_rule(
        sym, as_of, meta, policy=LIMIT_POLICY, calendar=CALENDAR)


def _meta(sym, *, is_st=False, metadata_available_at=META_AT):
    return InstrumentMeta(symbol=sym, is_st=is_st, listed_at=LISTED_AT,
                          metadata_available_at=metadata_available_at)


def test_name_resolves_to_code():
    assert resolve_name_to_code("贵州茅台", _MAP).code == "600519"


def test_code_passthrough():
    assert resolve_name_to_code("600519.SH", _MAP).code == "600519"


def test_industry_name_rejected():
    with pytest.raises(ValueError):
        resolve_name_to_code("白酒", _MAP)


def test_limit_rule_main_board():
    sym = Symbol(code="600519", exchange="SH", board="main")
    r = _limit(sym, datetime(2026, 7, 15, tzinfo=UTC), _meta(sym))
    assert r.pct == 0.10


def test_limit_rule_star_and_st():
    star = Symbol(code="688981", exchange="SH", board="star")
    assert _limit(star, datetime(2026, 7, 15, tzinfo=UTC),
                  _meta(star)).pct == 0.20
    st = _meta(star, is_st=True)
    assert _limit(star, datetime(2026, 7, 15, tzinfo=UTC), st).pct == 0.05


def test_limit_rule_unknown_st_returns_none():
    sym = Symbol(code="600519", exchange="SH", board="main")
    r = _limit(sym, datetime(2026, 7, 15, tzinfo=UTC),
               _meta(sym, is_st=None))
    assert r.pct is None and "unknown" in r.reason.lower()


def test_limit_rule_rejects_symbol_mismatch():
    sym = Symbol(code="600519", exchange="SH", board="main")
    other = Symbol(code="000001", exchange="SZ", board="main")
    with pytest.raises(ValueError, match="symbol"):
        _limit(sym, datetime(2026, 7, 15, tzinfo=UTC), _meta(other))


def test_future_metadata_is_explicit_unknown_not_current_rule():
    sym = Symbol(code="600519", exchange="SH", board="main")
    future = datetime(2026, 7, 16, tzinfo=UTC)
    rule = _limit(sym, datetime(2026, 7, 15, tzinfo=UTC),
                  _meta(sym, metadata_available_at=future))
    assert rule.pct is None and "available" in rule.reason.lower()


def test_not_yet_listed_is_explicit_unknown():
    sym = Symbol(code="600519", exchange="SH", board="main")
    meta = InstrumentMeta(
        symbol=sym, is_st=False,
        listed_at=datetime(2026, 7, 16, tzinfo=UTC), metadata_available_at=META_AT)
    rule = _limit(sym, datetime(2026, 7, 15, tzinfo=UTC), meta)
    assert rule.pct is None and "listed" in rule.reason.lower()


def test_limit_rule_rejects_naive_as_of():
    sym = Symbol(code="600519", exchange="SH", board="main")
    with pytest.raises((TypeError, ValueError)):
        _limit(sym, datetime(2026, 7, 15), _meta(sym))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_calendar.py tests/orchestration/data/test_resolve.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the PIT-aware pure resolvers** (append to `guanlan_v2/orchestration/data/symbols.py`)

Implementation requirements:

- never call `str(raw)` or partially match a code;
- attempt the exact symbol grammar first, then the verified name map;
- reject a name-map value that is not itself an exact valid symbol;
- verify symbol identity and all metadata timestamps before consulting the rule table;
- select one immutable `LimitRulePolicy` entry by `as_of`; its canonical material/digest matches `limit_rule_policy_v1.json` and Task 6 refers to it by `ContentRef` rather than treating policy bytes as a schema-registry entry;
- use the authoritative trading calendar for any listing-session rule; if it is unavailable, return an explicit unknown `LimitRule`, not a guessed percentage;
- preserve the Phase 1 strict/frozen/digest invariants of returned `Symbol` and `LimitRule`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_calendar.py tests/orchestration/data/test_resolve.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/calendar.py guanlan_v2/orchestration/data/symbols.py tests/orchestration/data/calendar_fixtures.py tests/orchestration/data/test_calendar.py tests/orchestration/data/test_resolve.py tests/orchestration/golden/limit_rule_policy_v1.json
git commit -m "feat(orchestration): resolve_name_to_code + resolve_limit_rule (phase3)"
```

---

## Task 4: `PitGuard` (refuses future data)

**Files:**
- Create: `guanlan_v2/orchestration/data/pit.py`
- Test: `tests/orchestration/data/test_pit.py`

**Interfaces:**
- Consumes: Phase 1 `DataContext`, `PitAudit`, `ContentRef`, canonical digest/shared strict types; Task 1 errors; Task 3 `TradingCalendar`; Phase 2 `AuthoritativeClock`/`NamedEvidenceDigest` shapes and an internal refusal-recorder protocol. It does not construct a store or require the Task 5 registry.
- Produces:
  - `RawRowCandidate(DigestModel)`: the minimal immutable pre-validation envelope—canonical raw JSON payload, effective time, optional `available_at` so missing metadata remains classifiable, trusted ingestion time, revision ID and verified raw content digest. It is not consumable data and is registered by Task 5.
  - `FreshnessPolicy(DigestModel)`: closed `schema_version: Literal["1"]`, stable policy ID/version, exactly one explicit elapsed-duration or trading-session threshold, exact calendar `ContentRef` when session-based, and its verified policy digest. Counts are strict non-negative integers; bool is rejected.
  - `DataFetchRefusalDetails(DigestModel)`: closed `schema_version: Literal["1"]` audit-detail payload containing reason code/stage plus request/context/method/routing/snapshot-content/vintage identities and optional source/capability/candidate-metadata/PIT-audit identities according to how far execution reached. Its validator enforces the stage/optional-field matrix (for example, pre-invocation manifest mismatch forbids candidate/PIT facts; future-row refusal requires both). It contains no raw candidate bytes, credentials or physical paths and is registered by Task 5.
  - `PitGuard.from_context(ctx: DataContext, *, clock: AuthoritativeClock, calendar: TradingCalendar, refusal_recorder) -> PitGuard`. Calendar ID/material must match the context and selected freshness policy; there is no session arithmetic fallback. The recorder protocol accepts `DataFetchRefusalDetails` plus ordered evidence and returns only after recording succeeds; neither the guard nor recorder callers construct `EventRefusalRecord`. Task 4 tests it with an in-memory spy because the cumulative Phase 3 registry does not exist until Task 5; Task 6 binds it to the current gateway pending invocation's `reject` adapter or a standalone pre-invocation `EventRefusalAuditSink.record` adapter. The constructor copies no caller override: mode, backend, strictness, as-of, snapshot locator/content digest, vintage digest and source-config identity come only from `ctx`.
  - `check_raw(candidates: tuple[RawRowCandidate, ...], *, request_digest: DigestHex, request_context_digest: DigestHex, source_ref: ContentRef, freshness=None) -> tuple[tuple[RawRowCandidate, ...], PitAudit]`. It verifies the supplied context digest, timezone metadata and frozen cutoff before Task 5 converts payloads to a registered `PitRecord` subtype. Task 5's DataRequest builder supplies these values; Task 4 has no forward dependency.

**PIT/refusal invariants:**

- `PitGuard` validates only intrinsic `DataContext` facts: `PIT_REPLAY` requires `strict_pit=True`, a non-empty snapshot locator, `data_snapshot_content_digest`, `vintage_manifest_digest` and a non-LIVE backend. Exact context↔manifest/routing/source-registry equality is a Task 6 pre-invocation check because Task 4 deliberately does not receive those objects.
- missing/naive `available_at` always raises `MissingAvailabilityRefused`; `available_at > ctx.as_of` always raises `FutureDataRefused`. There is no soft `filtered` success path.
- the guard makes exactly one recorder call with typed `DataFetchRefusalDetails` and ordered `NamedEvidenceDigest`s before raising. Task 4 proves ordering and failure propagation with a spy; Task 6 proves the registered production binding. In production, an active source call delegates to `CapabilityGateway.reject`, which alone calls `EventRefusalAuditSink`; before an invocation the adapter delegates directly to `EventRefusalAuditSink.record`. The sink is the sole owner of detail persistence and `EventRefusalRecord` creation. Dispatch must not write a second record, and refusal is never forged as a public `RunEvent`.
- a refused candidate never enters a cache, `PayloadStore`, `DataResult`, `InputSnapshot`, `Artifact` or fallback vendor.
- freshness is evaluated against `ctx.as_of` with the frozen clock/calendar policy; wall-clock collection time remains audit-only.

- [ ] **Step 1: Write the failing tests**

Keep the reusable visible/future/missing/naive/stale cases, but construct valid Phase 1 `DataContext` fixtures and `RawRowCandidate` objects through their builders. Add this rejection matrix:

1. visible ordered candidates return an immutable tuple and coherent `PitAudit(passed)`;
2. both ONLINE and PIT_REPLAY refuse a future candidate rather than filtering it;
3. missing and naive availability produce distinct refusal reasons;
4. PIT_REPLAY with non-strict context, missing snapshot locator/content/vintage identity or LIVE backend is rejected before a source call; exact manifest/context mismatch moves to Task 6 tests;
5. a request-context digest different from the guard's DataContext digest is rejected;
6. the recorder spy observes exactly one typed detail before the exception is observed, and the same guard idempotency key is reused deterministically;
7. recorder failure remains loud and cannot return data; registered audit-sink idempotency/namespace behavior is tested after Task 5 in Task 6;
8. refused candidates cause zero cache/payload/snapshot writes;
9. negative/bool thresholds, calendar mismatch and mutable/extra policy fields are rejected;
10. elapsed-duration and trading-session freshness boundaries use the frozen clock and exact calendar material; missing/drifted calendars fail rather than using wall-clock/global session arithmetic.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_pit.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement strict policy and context-bound guard**

Use Phase 1 shared validators and digest projections. Build `PitAudit` through its verified builder. The refusal helper calls the supplied recorder once with `DataFetchRefusalDetails` and waits for success before raising. Keep Task 4 free of registry/store construction; Task 6 supplies the production gateway/sink adapter after Task 5 registers the detail schema. Raw rejected data is never passed to the recorder. Do not add a public event, duplicate the recorder call, or catch a refusal in order to return data.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_pit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/pit.py tests/orchestration/data/test_pit.py
git commit -m "feat(orchestration): PitGuard refuses future data (phase3)"
```

---

## Task 5: `DataSource` protocol + request/result value objects

**Files:**
- Create: `guanlan_v2/orchestration/data/source.py`
- Create: `guanlan_v2/orchestration/data/catalog.py`
- Create: `guanlan_v2/orchestration/data/snapshot.py`
- Create: `guanlan_v2/orchestration/data/render.py`
- Create: `guanlan_v2/orchestration/data/schema_registry.py`
- Test: `tests/orchestration/data/test_source.py`
- Test: `tests/orchestration/data/test_snapshot.py`
- Test: `tests/orchestration/data/test_data_schema_registry.py`
- Test: `tests/orchestration/data/test_data_catalog.py`
- Create: `tests/orchestration/golden/data_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/data_catalog_manifest_v1.json`

**Interfaces:**
- Consumes: Phase 1 strict/digest types, `SchemaRef`/`ContentRef`/`CapabilityRef`/`PayloadRef`, sealed `SchemaRegistry`, `WorkerCatalogSnapshot`/`WorkerSpec`/`CapabilityDescriptor` and their material-aware builder, `PitRecord`, `DataResult`, `DataContext`, `Symbol`, `DataMode` and `DataBackend`; plus Phase 2 `PHASE2_PUBLIC_MODELS`, cumulative registry digest, canonical `PHASE2_STATIC_CATALOG_DIGEST/phase2_static_catalog_snapshot()` and read-only `CatalogRuntime` contract.
- Produces:
  - `DataMethodSpec(DigestModel)`: closed `schema_version: Literal["1"]`, stable ID/version/category, `params_schema_ref`, concrete `batch_schema_ref`, named concrete `result_schema_ref`, optional/core policy, supported modes/backends, `read_only: Literal[True]`, exact data-adapter `CapabilityRef`, versioned freshness-policy `ContentRef` and exact `renderer_ref: ContentRef`. Caller/model input cannot select a renderer.
  - `DataRequest(DigestModel)`: closed version, audit-only request ID and snapshot locator, method-spec `ContentRef`, validated params plus params `SchemaRef`, and values derived by its pure builder from one `DataContext`/routing/registry snapshot: as-of, mode, backend, semantic `data_snapshot_content_digest`, vintage-manifest/source-config/source-registry/routing/context/schema-registry digests and resolved-chain digest. There is no caller-owned `strict_pit`, chain or config override. `request_digest` is verified and excludes the audit-only request ID/snapshot locator.
  - Task 4 `RawRowCandidate` is reused as the only untrusted pre-validation carrier; Task 5 does not redefine it.
  - `RawFetch(DigestModel)`: exact request digest, source/capability refs, immutable candidate tuple, subsource identity and audit-only fetch/provider timing; declared semantic/audit digests are builder-computed.
  - intentionally unregistered, frozen internal carriers in `source.py`: `DataInvocationScope(plan_digest, node_id, worker_id, operation_token: ExecutionEvidenceOrdinalToken, attempt_tokens: tuple[ExecutionEvidenceOrdinalToken, ...], frozen_route, invocation_mode: cache_or_invoke | always_invoke, catalog_digest, schema_registry_digest)`, `ResolvedDataMethodPolicy(method_ref, freshness_policy, limit_policy, calendar, policy_bundle_digest)`, `VerifiedDataCacheHit(result, result_ref: PayloadRef)` and `DataReadOutcome(operation_token, result_token, result, request_ref: PayloadRef, result_ref: PayloadRef, tool_call_record)`. In canonical operation/route order the Phase 2 sequencer pre-issues one operation token plus one distinct attempt token for each possible route entry; cache/no-call evidence uses the operation token, while every actual `CapabilityGateway.begin` consumes its matching attempt token and a successful ToolCallRecord/result use that same attempt token. Phase 3 may validate/echo but never mint, reuse or renumber tokens. These carriers preserve service authorization/policy, issuance order and already-persisted typed refs without creating a second public schema; `tool_call_record` is zero-or-one per read. Task 6 consumes them rather than defining later registry models.
  - service-side `DataSource(Protocol)`: one adapter handler behind the Phase 2 `CapabilityGateway`; it does not expose a mutable capability set and dispatch never calls the handler directly.

**Concrete registered payloads:**

- `InstrumentNameRecord`, `OHLCVRecord`, `IndicatorRecord`, `VerifiedSnapshotRecord`, `FundamentalRecord`, `NewsRecord` and `SignalRecord` each subclass Phase 1 `PitRecord` and add field-level strict domain data. Their required `available_at`, `ingested_at`, revision and row content digest are never replaced by one top-level timestamp.
- Corresponding immutable batches `InstrumentNameRows`, `OHLCVRows`, `IndicatorRows`, `VerifiedSnapshotRows`, `FundamentalRows`, `NewsRows` and `SignalRows` subclass `DigestModel` and contain tuples of exactly the matching record type. Builders enforce method-specific canonical sort keys, duplicate identity rules and batch content digest.
- Named persisted envelopes `InstrumentNameDataResult`, `OHLCVDataResult`, `IndicatorDataResult`, `VerifiedSnapshotDataResult`, `FundamentalDataResult`, `NewsDataResult` and `SignalDataResult` are closed concrete subclasses/specializations of Phase 1 `DataResult[MatchingRows]`. Each has its own stable registered `SchemaRef`; `DataMethodSpec.result_schema_ref` must name the matching envelope. A runtime-only generic annotation is never used as a persisted schema identity.
- No public generic `RowSet`, `list[dict]` result payload or anonymous schema is allowed. `RawRowCandidate.raw_payload` is the only pre-validation JSON envelope; the method adapter validates it to the method spec's concrete registered record before `DataResult.build` can consume it.
- Task 5 also freezes the strict DTOs consumed by later runtime tasks:
  - `DataSourceDescriptor(DigestModel)`: closed version, stable source ID/version, supported method refs, per-method capability refs, supported modes/backends, trusted `handler_ref: ContentRef`, source-config `SchemaRef` and descriptor digest. `handler_ref` resolves from the reviewed catalog/material runtime and may not be a caller callable/path; credentials and physical paths remain service-owned;
  - `DataSourceRegistrySnapshot(DigestModel)`: closed version, immutable method specs/source descriptors/default route policies, exact `LimitRulePolicy`/`FreshnessPolicy` materials keyed by their `ContentRef`, and verified source-registry digest. It contains policy values/digests, not physical files;
  - read-only `DataPolicyResolver` over that sealed snapshot: `resolve_method(method_spec, *, ctx) -> ResolvedDataMethodPolicy` resolves exact policy refs, verifies material digest/type, resolves the context-matching session calendar through Task 3 `TradingCalendarResolver`, and returns the single bound bundle. Missing, extra, wrong-type or drifted policy/calendar material fails at seal/startup rather than falling back to a module global;
  - `RenderedDataBlock(DigestModel)`: closed version, renderer `ContentRef`, exact main `result_ref: PayloadRef`, source result/content/PIT-audit digests, status/provenance fields, `trust=Literal["untrusted_data"]`, deterministic media type/text, `rendered_from_payload_digest` and verified block digest. Its builder derives/verifies the result SchemaRef from the typed ref against the DataMethodSpec and loaded named result; no detached schema/ref pair is accepted.
  - `DataPrefetchOperation@1` and `DataBridgePrefetchBinding@1`: strict registered catalog-material contracts that bind one exact execution-bridge ID to canonically ordered worker/method mappings, the complete ordered non-empty fallback route of `(source_ref, capability_ref)` entries, `invocation_mode: cache_or_invoke | always_invoke`, `success_requires_finalized_call: bool`, and a closed projection from already validated `PlanNode.params`/named InputSnapshot values into each method's params schema. `always_invoke` bypasses a cache hit only when explicitly reviewed and still permits replay only through frozen evidence; it is never inferred from `tool_calls=REQUIRED`. `success_requires_finalized_call=True` is legal only when node success is impossible on cache/optional exhaustion and at least one route call must finalize. Sources are exact JSON-pointer/value bindings only—no expression, callable, model-generated method/params, clock, global config or dynamic late-call escape. The binding digest is verified and all referenced workers/methods/route sources/capabilities/schemas cross-resolve; runtime requires the resolved `DataRoutingSnapshot` route to equal this exact catalog route. The pure bridge support analyzer derives its min/max bounds from these rows rather than trusting declared summary numbers.
  Tasks 6/7 implement registry/renderer behavior over these already-registered contracts; they do not introduce a late unregistered model.

**Routing / snapshot / cache ABI (`snapshot.py`):**

- `DataSourceConfigSnapshot(DigestModel)`: normalized non-secret method/source selections and option digests. Credential values never enter this model; the gateway owns them. Its declared source-config digest is builder-verified.
- `ResolvedMethodRoute(DigestModel)`: method-spec ref plus an explicit ordered tuple of source/capability refs and the versioned route-policy ref.
- `DataRoutingSnapshot(DigestModel)`: audit ID, source-registry/schema-registry/source-config digests and canonically keyed method routes. Its builder is the sole producer of `DataContext.resolved_vendor_chains`; registry insertion order is never a routing policy.
- `DataSnapshotEntry(DigestModel)`: dataset/method/source/revision identities, payload `SchemaRef`, content digest and maximum availability. It never embeds a cache-key digest; cache keys bind the completed manifest in one direction, avoiding a digest cycle.
- `DataSnapshotManifest(DigestModel)`: audit-only `data_snapshot_id`, `manifest_kind: Literal["pit_frozen","online_capture_root"]`, as-of/mode, routing and schema-registry digests, immutable sorted entries, vintage-manifest digest and verified `content_digest`. The locator is excluded from the semantic projection; relocating byte-identical manifest content changes audit/dereference identity only. The vintage digest is computed from the sorted vintage-entry projection and excludes itself; the overall content digest is then computed without either declared digest self-referencing. A `pit_frozen` manifest is complete and immutable for replay. An `online_capture_root` freezes the run-start boundary/routing but does not pretend a live vendor is snapshot-isolated; subsequently recorded DataResults append under that root without mutating it.
- `build_data_context(clock: AuthoritativeClock, *, mode, backend, source_config, source_registry, routing, manifest) -> DataContext` is the only Phase 3 runtime constructor. It fills the exact Phase 1 fields `resolved_vendor_chains`, `source_config_digest`, `source_registry_digest`, `routing_snapshot_digest`, `data_snapshot_id`, `data_snapshot_content_digest` and `vintage_manifest_digest`, and verifies exact digest/as-of/calendar consistency. PIT_REPLAY accepts only a complete `pit_frozen` manifest; ONLINE uses an `online_capture_root` and per-result persisted evidence.
- `DataCacheKey(DigestModel)`: method/source refs, canonical params digest, as-of, mode/backend, semantic snapshot-content/vintage/schema/source-config/routing/source-registry/schema-registry digests. It never uses `data_snapshot_id` as a semantic key dimension. `DataCacheEntry(DigestModel)` binds that key to an exact main `result_ref: PayloadRef`, result semantic/audit digests and `PitAudit` digest. Result SchemaRef/content/PIT identity is semantic; result audit digest, snapshot locator and nested `PayloadRef.object_id` remain audit/dereference identity and cannot perturb semantic cache equivalence.
- `DataCache(Protocol)`: `get_verified(key, *, ctx, manifest, registry, payload_store) -> VerifiedDataCacheHit | None` and `put_verified(key, result_ref: PayloadRef, *, result, pit_audit)`. The protocol carries no physical root; an in-memory conformance fake proves the ABI while production PIT/cache adapters remain later work. A cache hit therefore keeps the exact typed result identity needed by provenance instead of returning a detached Python object or separately supplied schema.
- Cache reads verify every bound digest and rerun the context/PIT compatibility check. Rejected data is never written. In PIT_REPLAY, a manifest mismatch is terminal; a miss may continue only to an explicitly frozen, snapshot-bound PIT_STORE adapter. `backend=CACHE` has no such continuation, and no replay path may invoke LIVE.

**Phase 3 schema-registry extension:**

- `PHASE3_PUBLIC_MODELS` is one reviewed tuple containing the Task 3 `LimitRulePolicy` and every public model introduced by Tasks 4–5, including `DataFetchRefusalDetails`, `DataPrefetchOperation`, `DataBridgePrefetchBinding` and all seven named concrete DataResult envelopes; `PHASE3_INTERNAL_MODELS` maps `DataInvocationScope`, `ResolvedDataMethodPolicy`, `VerifiedDataCacheHit`, `DataReadOutcome` and any other intentionally unregistered helper to a reviewed reason.
- `PHASE3_DATA_REGISTRY_DIGEST` and `build_phase3_registry(expected_phase2_runtime_digest) -> sealed SchemaRegistry` construct/identify a fresh cumulative **data-only** registry from Phase 2's exported `PHASE2_PUBLIC_MODELS` (which already includes the exact Phase 1 public model set) plus `PHASE3_PUBLIC_MODELS`, and first verify the expected Phase 2 runtime manifest/digest plus its exported `PHASE2_BASE_REGISTRY_DIGEST`. It never mutates or unseals either upstream sealed registry. Task 9 may extend this immutable snapshot but may not rewrite its tuple, digest or golden.
- Every new model's JSON Schema has a constant closed version; manifest ordering and digest are registration-order independent and frozen in `data_schema_manifest_v1.json`.
- old Plans continue resolving their exact Phase 1 or Phase 2 registry digest; Plans using these data payloads bind the Phase 3 cumulative registry digest. No global mutable "latest registry" may reinterpret an old Plan.

**Phase 3 catalog/capability extension (`catalog.py`):**

- `PHASE3_DATA_CAPABILITIES` is a reviewed tuple of the unchanged closed Phase 1 `CapabilityDescriptor@1`. Each entry contains only `id/version/capability_kind/transport/operation/input_schema_ref/output_schema_ref`; for a data method those SchemaRefs are the registered `DataRequest` and that method's named concrete DataResult. `RawFetch` is an unpublished gateway/adapter intermediate, never the public capability output SchemaRef. Phase 3 must not add mode/backend/side-effect/handler fields to this v1 descriptor.
- Data semantics remain in their owning contracts: allowed modes/backends and `read_only=True` live in `DataMethodSpec`; trusted handler material lives in `DataSourceDescriptor.handler_ref` and resolves through the catalog's `ContentManifestEntry(kind="handler")` plus Phase 2 `CatalogRuntime`. The method spec, source route, capability descriptor and handler material must cross-resolve to one reviewed identity set.
- Phase 3 data adds one canonical Phase 2 `ExecutionBridgeDescriptor@1` material for `DataRuntimeBridge`. Its activation predicates are the exact Phase 3 data capability refs, `pre_input_kind="none"`, `lifecycle="static_prefetch_v1"`; its config ref/schema identify the exact `DataBridgePrefetchBinding@1`, its provider-handler ref identifies the trusted implementation, its support-analyzer ref identifies a pure reviewed analyzer, and its priority is globally reviewed for later data+memory composition. The analyzer verifies every mapped operation/allowlist/cache mode/ordered route and emits exact per-node aggregate bounds by summing rows: each row has `max_capability_invocations=len(frozen_route)`; `cache_or_invoke` has minimum zero; `always_invoke` has minimum one only when `success_requires_finalized_call=True`, otherwise zero. Multi-route fallback therefore never claims max one, and optional/cache success never claims a false minimum. Descriptor/config are `kind="guardrail"`; provider/analyzer are distinct `kind="handler"` materials. A worker with any activated data capability therefore requires this exact provider/analyzer before reservation.
- `PHASE3_BASE_CATALOG_DIGEST` equals the canonical end-of-Phase-2 `PHASE2_STATIC_CATALOG_DIGEST`; the Task 0 fixture verifies it. `PHASE3_DATA_CATALOG_DIGEST` identifies the immutable data-only result. `build_phase3_catalog(phase2_snapshot, *, reviewed_worker_updates, reviewed_source_descriptors, reviewed_method_specs, data_bridge_descriptor, data_bridge_prefetch_binding, resolved_materials) -> WorkerCatalogSnapshot` first rejects every other base digest, then uses the Phase 1 catalog builder. It preserves unchanged worker/material identities, applies only reviewed capability-allowlist updates, and proves every source handler, method renderer and execution-bridge descriptor/config/provider/analyzer ref has exactly one correctly typed material and no extra material. Every worker granted a data capability has an exact prefetch row for that capability, and every row is granted—coverage is one-to-one. It validates all model/content/capability/support-bound digests and emits the one Phase 3 data catalog digest/golden. Missing/drifted bridge/analyzer material, false call bounds, a dynamic/late-call config or incomplete/extra worker-capability mapping fails. It never mutates the Phase 2 snapshot; Task 9 extends it by a new digest/golden rather than overwriting it.
- No capability is granted globally. The initial integration explicitly reviews which existing pilot/final worker may call each data method; at minimum the Phase 3 integration fixture grants only the methods exercised by that worker. `compat.*` remains `static_legacy_only` and gains no data capability unless the legacy mapping/material evidence explicitly requires it.
- old Plans keep the Phase 1/2 catalog digest. A Plan using a Phase 3 data capability binds both the Phase 3 cumulative registry digest and Phase 3 catalog digest; changing either requires validation, support analysis, reservation and approval again.

- [ ] **Step 1: Write failing contract, snapshot and registry tests**

Preserve the useful request-digest intent: reversed JSON key order is stable, while a params/context/mode/snapshot/routing/source-config/schema-registry change alters the digest. Construct requests only with the pure builder. Add:

1. params are validated by the method's exact `params_schema_ref`; extra fields, bool-as-number, naive datetime and non-finite values fail;
2. RawFetch semantic digest ignores only declared audit timing/provider IDs; source, capability, request, candidate content/order/outcome changes are semantic;
3. missing availability can exist only in `RawRowCandidate` long enough for `PitGuard` to refuse it;
4. every concrete batch accepts only its matching `PitRecord` subtype, is immutable and canonicalizes ordering deterministically;
5. `DataResult.build` accepts each registered concrete batch through its matching named DataResult envelope and rejects generic runtime specializations, generic mappings, wrong result/batch schema refs and declared digest mismatches;
6. `RowSet` is absent from the public module/registry;
7. source-config/routing/snapshot/context builders reject secrets, duplicate/unknown methods/sources, missing/extra/drifted policy material, mismatched policy/calendar refs, registry/config/context/as-of/calendar digests and unordered declarations;
8. adding a future revision does not change an older PIT manifest/result digest; appending an online captured result does not mutate its capture-root digest;
9. all semantic cache-key dimensions are sensitive; snapshot-ID/object-ID relocation alone is invariant, while a cache hit with a mismatched snapshot-content/result/PIT/schema digest is rejected;
10. PIT_REPLAY cache miss can reach only an explicitly frozen matching PIT_STORE fake; CACHE-only and every LIVE continuation are rejected;
11. Phase 1 and Phase 2 registries remain sealed and unchanged after building the Phase 3 snapshot, and the Phase 2 control-model subset remains byte/schema-identical;
12. every public ContractModel introduced by Tasks 3–5 under `orchestration.data` is in the data-only `PHASE3_PUBLIC_MODELS` or reviewed data internal map; Task 9 memory modules are deliberately checked by their separate full-registry completeness test;
13. reversed Phase 3 registration order has the same manifest/digest and matches the golden file.
14. the Phase 3 catalog builder rejects a non-canonical Phase 2 base digest, unknown/schema-mismatched capabilities, an attempted extra field on closed `CapabilityDescriptor@1`, unreviewed worker grants, missing/extra provider/support-analyzer material and drift; the exact data bridge descriptor/config/provider/analyzer cross-resolve and activate for every granted data worker. Tests reject dynamic/model-selected/late-call mappings, route mismatch, duplicate/reused attempt tokens and false min/max bounds (including multi-route fallback and optional/cache success), and prove any mapping/material/priority drift alters the catalog digest. Unchanged worker/material entries remain digest-identical, and the resulting manifest matches the single `data_catalog_manifest_v1.json`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestration/data/test_source.py tests/orchestration/data/test_snapshot.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement strict models, pure builders and the sealed Phase 3 registry/catalog extensions**

All public DTOs use `ContractModel`/`DigestModel`, immutable tuples and shared strict validators. Registry and cache lookup take an explicit snapshot/digest; imports perform no registration or I/O. Method-specific adapters are the only code allowed to turn a raw candidate into a concrete record, and their output is immediately registry-validated before batch/result construction.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/orchestration/data/test_source.py tests/orchestration/data/test_snapshot.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/source.py guanlan_v2/orchestration/data/catalog.py guanlan_v2/orchestration/data/snapshot.py guanlan_v2/orchestration/data/render.py guanlan_v2/orchestration/data/schema_registry.py tests/orchestration/data/test_source.py tests/orchestration/data/test_snapshot.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py tests/orchestration/golden/data_schema_manifest_v1.json tests/orchestration/golden/data_catalog_manifest_v1.json
git commit -m "feat(orchestration): freeze typed data payload and snapshot ABI"
```

---

## Task 6: sealed `DataSourceRegistry.dispatch` (narrow fallback router)

**Files:**
- Create: `guanlan_v2/orchestration/data/registry.py`
- Test: `tests/orchestration/data/test_registry.py`
- Create: `tests/orchestration/golden/data_source_manifest_v1.json`

**Interfaces:**

- Consumes: Task 4 guard/policy/refusal detail, Task 5 method/source/request/named concrete result/routing/snapshot/cache contracts, sealed `DataPolicyResolver` and cumulative Phase 3 registry, Phase 1 `DataResult.build`/`SourceAttempt`/refs/registry, and Phase 2 `CapabilityGateway`, read-only `PayloadStore`, `BridgeEvidenceWriter`, `AuthoritativeClock` and audit-only `EventRefusalAuditSink`.
- Produces:
  - `DataSourceRegistry.register_descriptor(...)`, `register_method(...)`, `seal()`, `manifest()`, `snapshot()` and `build_routing_snapshot(...)`. Same declaration is idempotent; conflicts, unknown refs and every mutation after seal fail.
  - dispatch behavior over Task 5's internal carriers. `DataInvocationScope` is created only by Task 8 from an admitted Plan and selected WorkerSpec, verified against the gateway/runtime before every `begin`, and cannot be manufactured from application/model input. `DataReadOutcome` carries the named result plus exact persisted refs and an optional finalized-success Phase 1 `ToolCallRecord`.
  - `dispatch(req: DataRequest, *, invocation_scope: DataInvocationScope, ctx: DataContext, routing: DataRoutingSnapshot, manifest: DataSnapshotManifest, schema_registry, resolved_policy: ResolvedDataMethodPolicy, gateway: CapabilityGateway, evidence_writer: BridgeEvidenceWriter, payload_reader, refusal_audit_sink: EventRefusalAuditSink, cache, clock: AuthoritativeClock) -> DataReadOutcome`.

**Routing and authorization invariants:**

- the registry is sealed before `DataRoutingSnapshot`/`DataContext` construction. A configured unknown source/method is a freeze-time error; it is never filtered out. A known descriptor whose runtime capability/credentials are absent may raise `NotConfiguredError`, creating a real attempt before advancing the frozen chain;
- before dispatch, `DataReader` asks the sealed `DataPolicyResolver` for one `ResolvedDataMethodPolicy`. Dispatch verifies its method/context/bundle digest and gives its sole calendar to PitGuard; there is no second `calendar`/policy parameter. Drift or an unbound caller bundle fails before cache/source access;
- the default chain is an explicit ordered tuple in a versioned route policy. Registration order, later registration and process hash seed cannot alter it;
- before cache lookup or `CapabilityGateway.begin`, dispatch verifies that request, context, route, manifest, source/schema registry and source-config digests are one frozen set, including `ctx.data_snapshot_content_digest == manifest.content_digest`, `ctx.data_snapshot_id == manifest.data_snapshot_id` as audit locator, and exact vintage-manifest equality. A mismatch records one registered `DataFetchRefusalDetails` directly through `EventRefusalAuditSink.record` and stops before any source call. Dispatch accepts no `cfg`, chain, mode, strictness, snapshot or blank digest argument;
- every adapter invocation goes through the two-stage `CapabilityGateway` with current service-owned worker/catalog authorization. Dispatch first requires its resolved route to equal the complete catalog-frozen route and `len(attempt_tokens)` to equal its length. The exact normal sequence is: persist/reuse the request PayloadRef via `BridgeEvidenceWriter.put(operation_token,"request",...)`; for route entry `i`, call `begin(... ordinal_token=attempt_tokens[i], capability_ref=frozen_route[i].capability_ref, ...)`; invoke only the trusted resolved source; keep `RawFetch` unpublished while PIT/output validation builds the named concrete DataResult; persist a successful result exactly once through `evidence_writer.put(attempt_tokens[i],"result", DataMethodSpec.result_schema_ref, ...)`; then call only `finalize_success(pending, request_ref=request_typed, result_ref=result_typed, request_digest=req.request_digest, result_digest=result.content_digest)`. The gateway re-verifies the existing main typed refs/digests and creates the Phase 1 ToolCallRecord at that distinct attempt-token ordinal; failed fallback attempts consume their own token and can never reuse the successful one. `DataReadOutcome.result_token` equals the successful attempt token. Dispatch never calls a descriptor handler or registered Python object directly;
- scope re-verification checks the selected Worker's exact capability allowlist, catalog-bound invocation mode/route/success semantics and the support-summary allowance for every attempt token. Under `cache_or_invoke`, dispatch validates/persists the current DataRequest once through the writer before `get_verified`; for `cache_hit: VerifiedDataCacheHit`, it calls `record_existing(operation_token,"result", cache_hit.result_ref)` and returns `DataReadOutcome(operation_token=operation_token, result_token=operation_token, result=cache_hit.result, request_ref=current_request_typed_ref, result_ref=cache_hit.result_ref, tool_call_record=None)` after registry/digest/PIT verification. It preserves recorded attempts/audit/badges and cannot fabricate a source ToolCallRecord. Under reviewed `always_invoke`, lookup cannot terminate the operation; dispatch begins/finalizes an exact frozen-route call. A successful node may claim analyzer minimum one only when `success_requires_finalized_call=True`; optional exhaustion cannot satisfy it. In PIT_REPLAY, either mode may invoke only an explicitly frozen matching PIT_STORE source; CACHE-only miss, snapshot mismatch or any route pointing to LIVE fails before that invocation.

**Outcome matrix:**

- only `RateLimitError` and `NotConfiguredError` advance to the next already-frozen source. Each failed pending invocation is first terminalized exactly once through `gateway.reject(... DataFetchRefusalDetails ...)`; its audit-sink failure is loud and prevents fallback;
- success after an advance remains `OK` and adds `FALLBACK_USED`; fallback count alone never means `DEGRADED`;
- `DEGRADED` is allowed only for a method-policy-approved partial concrete batch meeting its coverage floor, with coverage and reason;
- `NO_DATA` and `STALE` are derived only after `PitGuard` evaluates the returned candidates, then become named concrete DataResults persisted and finalized as successful capability outcomes. An adapter-raised `NoDataError`/`StaleDataError` before it returns candidates/audit is a broken-source error and is rejected, never converted into a fabricated passed audit;
- `FutureDataRefused` / `MissingAvailabilityRefused` are already transitioned/persisted by the guard's pending reject recorder and are re-raised unchanged; the catch does not reject a second time, continue, `finalize_success` or write cache/data payloads;
- any other `DataError` checks whether the pending invocation is still open and, if so, calls `gateway.reject` exactly once before raising. Optional exhaustion builds/persists a named `UNAVAILABLE` result at the operation token with no fabricated success ToolCallRecord; its DataReadOutcome uses that operation token and is legal only when the catalog row does not require a finalized call. Core exhaustion has already persisted every failed-attempt refusal and raises the first retryable error;
- every returned result is created only by Phase 1 `DataResult.build`, wrapped as the method's registered named concrete result, registry-validated and persisted by the fixed-main writer ABI `BridgeEvidenceWriter.put(token, role, schema_ref, payload, *, idempotency_key)`; provider code cannot select a namespace. `DataReadOutcome` carries the operation/result tokens, exact PayloadRefs and optional record to the invocation-scoped collector. A raw PayloadStore write, manual semantic/audit digest dictionary or second persistence path is forbidden.

- [ ] **Step 1: Write failing registry/router tests**

Preserve the original source-routing scenarios as contract tests, rebuilt with strict fixtures, a sealed registry, a real `DataContext` builder and a fake `CapabilityGateway`:

1. `test_first_source_success`: first source succeeds and returns `DataReadOutcome` with one persisted request ref, one named concrete DataResult ref and one verified ToolCallRecord; request/result each have exactly one write;
2. `test_rate_limit_falls_through`: first attempt is rate-limited, second succeeds, each `begin` consumes its distinct pre-issued route token, successful result/ToolCallRecord use the second token, attempt order is preserved and `FALLBACK_USED` is present;
3. `test_not_configured_falls_through`: a known but unavailable capability advances with its own explicit attempt token; token reuse, missing/extra tokens and completion-order renumbering fail;
4. `test_no_data_stops_chain` and `test_stale_stops_chain`: guard-derived terminal named results, no second invocation and no consumable data; premature adapter-raised claims are rejected as broken source;
5. `test_future_refused_raises_not_falls_through` and the matching missing-availability case: pending invocation is rejected, the already validated request may remain by its idempotent ref, but no second invocation/cache/raw/result main-payload write or success ToolCallRecord exists; one audit-only refusal exists before the raised exception;
6. `test_core_broken_primary_raises`, with its failure audit retained;
7. `test_optional_category_exhaustion_is_unavailable`: policy comes from `DataMethodSpec`, not a module set;
8. `test_core_exhausted_raises_first_retriable`, after persisting the complete ordered attempts;
9. empty explicit route, unknown configured source/method and catalog-route/runtime-route/registry/context/manifest snapshot-content or vintage mismatch fail instead of silently defaulting; a manifest mismatch writes one audit detail before `begin` and invokes no source;
10. reverse descriptor registration order gives the same registry/routing digests and explicit default route, matching `data_source_manifest_v1.json`;
11. conflicting registration and mutation after seal fail;
12. direct handler invocation/bypassed gateway is impossible; pending invocation cannot count as evidence, every attempted invocation reaches exactly one terminal state, and double finalize/reject or finalize-after-reject fails;
13. params/output schema mismatch, wrong source/capability ref and wrong DataResult declared digest fail;
14. `cache_or_invoke` permits a verified hit with analyzer minimum zero, records the existing typed result at the operation token and creates no ToolCallRecord; `always_invoke` bypasses the hit and may satisfy minimum one only when success requires finalization. Analyzer max equals frozen route length; multi-route fallback, optional exhaustion and false min/max claims are covered. A bad cache entry is rejected; replay miss reaches only a frozen matching PIT_STORE source, while CACHE-only miss/LIVE route is terminal;
15. each new evidence write is one UoW containing evidence payload + `BridgeEvidenceRecorded` control payload + journal RunEvent: failure before commit exposes none; crash after UoW commit but before provider return exposes all three and recovery drains by token/role; payload-only, control-only or event-only visibility is impossible. Same-key/same semantics returns the exact PayloadRef, same-key/different semantics conflicts, and a provider cannot obtain a write-capable PayloadStore;
16. same idempotency key/same data fetch is stable; same key/different semantics conflicts;
17. fallback success is `OK`, while partial coverage alone exercises the complete `DEGRADED` matrix.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/data/test_registry.py -v`

Expected: FAIL until the sealed registry/router exists.

- [ ] **Step 3: Implement the sealed registry, frozen router and result builder path**

Keep source descriptors separate from service-owned handlers. Resolve the descriptor's reviewed handler ref only inside the Phase 2 trusted runtime/gateway, keeping raw output unpublished while Task 4/5 perform PIT and schema validation. Persist the validated request and named result once, then pass their existing refs to `finalize_success`. The guard owns PIT rejection; its catch only re-raises. Other still-pending failures call `reject` once before fallback/raise. Both gateway rejection and pre-invocation refusal delegate record/detail creation to the one `EventRefusalAuditSink`; no caller constructs `EventRefusalRecord`, and no refusal detail enters the main namespace or public event journal.

- [ ] **Step 4: Run tests**

Run: `pytest tests/orchestration/data/test_registry.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/registry.py tests/orchestration/data/test_registry.py tests/orchestration/golden/data_source_manifest_v1.json
git commit -m "feat(orchestration): add sealed narrow-fallback data router"
```

---

## Task 7: `DataReader` facade + deterministic untrusted rendering

**Files:**
- Create: `guanlan_v2/orchestration/data/reader.py`
- Modify: `guanlan_v2/orchestration/data/render.py`
- Test: `tests/orchestration/data/test_reader.py`
- Test: `tests/orchestration/data/test_render.py`

**Interfaces:**

- Consumes: sealed `DataSourceRegistry`, `DataRoutingSnapshot`, `DataSnapshotManifest`, Task 6 `DataReadOutcome`, Task 5 `RenderedDataBlock`, Phase 3 schema registry, Phase 1 `DataContext` and Phase 2 capability/payload/audit/cache/`AuthoritativeClock` plus executor-owned ordinal-token ports.
- Produces:
  - internal invocation-scoped `DataEvidenceCollector`: accepts Phase 2-issued ordinal tokens before dispatch, never creates an ordinal, accepts each complete token-matching `DataReadOutcome` and rejects foreign/duplicate/conflicting tokens/refs. It first normalizes contributions by the generic `(call_ordinal, bridge_priority, bridge_id, within_call_role)` key, then emits each WorkerExecutionResult class through its Phase 1 canonicalizer: ToolCallRecords by call ordinal, DataResult PayloadRefs by typed semantic projection, and only remaining direct evidence by the generic merge key. Completion order therefore changes none of the three tuples. It is not a second payload/evidence schema.
  - `DataReader` constructed from that exact frozen evidence plus the Task 8-created `DataInvocationScope`, collector, sealed `DataPolicyResolver` and `AuthoritativeClock`. For each method it resolves one policy/calendar bundle, and Task 6 constructs `PitGuard` only after `begin` using that bundle and the current pending invocation's reject recorder; invocation-precheck refusals use the standalone sink adapter. Callers cannot supply plan/node/worker identity, a second guard, mutable config, source chain, freshness map, mode, strict flag, wall clock or alternate calendar.
  - typed methods:
    - `get_ohlcv(...) -> OHLCVDataResult`;
    - `get_indicators(...) -> IndicatorDataResult`;
    - `get_verified_snapshot(...) -> VerifiedSnapshotDataResult`;
    - `get_fundamentals(...) -> FundamentalDataResult`;
    - `get_news(...) -> NewsDataResult`;
    - `get_signal(method_ref, ...) -> SignalDataResult`, where the ref must resolve to an approved signal method spec rather than an arbitrary method string.
  - pure `render_for_prompt(result, *, result_schema_ref, result_ref, method_spec: DataMethodSpec, schema_registry, catalog_runtime) -> RenderedDataBlock`. It resolves only `method_spec.renderer_ref` from the exact catalog; ordinary callers cannot pass/replace a renderer. Task 8 owns registry-validated persistence and prompt assembly; the renderer itself performs no I/O.

**Reader/render invariants:**

- constructor validation proves context, route, snapshot, source/schema registry and source-config digests form one frozen set. Every call selects its `DataMethodSpec`, validates a concrete params model, builds `DataRequest` from the context, delegates to Task 6, records the complete `DataReadOutcome`, then returns only its named concrete result to application code;
- DataReader never reads today's date, global vendor config, mutable registry state or a physical cache path;
- rendering verifies the result/method/result-SchemaRef tuple, resolves the catalog-bound renderer and uses the canonical registry serializer—never `default=str`, ad-hoc markdown parsing or an anonymous dict. OK/DEGRADED embeds the concrete payload as length-delimited canonical JSON; missing statuses embed an explicit no-fabrication sentinel;
- the block is not described as cryptographically “unforgeable.” Task 8 persists it only through `BridgeEvidenceWriter` and returns one exact Phase 1 `PayloadRef` (plus media type and bounded length) in the provider's untrusted-block DTO; Phase 2's executor alone merges that DTO into its generic PromptAssembler and untrusted data/tool-input channel. Strings inside rows cannot become system/skill/guardrail instructions or close the outer boundary;
- changing only `PayloadRef.object_id` changes audit/dereference identity but not rendered semantic content; changing namespace, schema, content, PIT audit, status or renderer material changes the block digest;
- downstream workers consume typed payloads when available. `RenderedDataBlock` is only for an LLM-facing view and records `rendered_from_payload_digest`.

- [ ] **Step 1: Write failing reader/render tests**

Preserve the original useful scenarios with contract-correct fixtures:

1. `test_get_ohlcv_ok` returns the registered `OHLCVDataResult` whose rows are `OHLCVRecord`, while the collector retains the matching request/result refs and ToolCallRecord;
2. every facade method chooses the exact approved params and result SchemaRefs, including `get_verified_snapshot`;
3. unknown/arbitrary signal method ref is rejected;
4. a constructor with mismatched context/routing/manifest/registry/config digests fails;
5. there is no caller `cfg`, guard, strict, mode, `now` or freshness override;
6. OK and DEGRADED rendering includes schema key, result/content/PIT audit digests, source/coverage/badges and canonical data;
7. NO_DATA/STALE/UNAVAILABLE rendering is deterministic, non-empty and contains the no-fabrication sentinel without consumable data;
8. embedded fake headers, delimiters and “ignore previous instructions” text remain JSON data inside the untrusted block;
9. renderer ref comes only from DataMethodSpec/exact catalog; caller override is absent, and renderer material drift or result schema/content/status/PIT changes alter the digest while object-ID-only change does not;
10. naive/non-finite/unregistered payload rendering fails instead of string-coercing;
11. the pure renderer returns a registry-valid `RenderedDataBlock` without touching PayloadStore or PromptAssembler;
12. foreign/duplicate/conflicting executor-issued ordinal tokens/refs fail and the generic merge order is deterministic under reversed completion.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/orchestration/data/test_reader.py tests/orchestration/data/test_render.py -v`

Expected: FAIL until the frozen reader and renderer exist.

- [ ] **Step 3: Implement the facade and deterministic renderer**

Resolve every method, policy and renderer by stable refs/digests. Return only the named envelopes built through Phase 1 `DataResult.build`, while the invocation collector retains the already-persisted refs/evidence for Phase 2. The renderer returns a strict block for Task 8 to persist and route through the generic prompt assembler; row content never owns trust boundaries. Imports and rendering perform no external I/O.

- [ ] **Step 4: Run tests**

Run: `pytest tests/orchestration/data/test_reader.py tests/orchestration/data/test_render.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/reader.py guanlan_v2/orchestration/data/render.py tests/orchestration/data/test_reader.py tests/orchestration/data/test_render.py
git commit -m "feat(orchestration): add frozen data reader and untrusted renderer"
```

---

## Task 8: Phase 2 runtime, provenance and replay integration

**Files:**
- Create: `guanlan_v2/orchestration/data/runtime.py`
- Test: `tests/orchestration/data/test_runtime_integration.py`
- Test: `tests/orchestration/data/test_data_replay.py`

**Interfaces:**

- `DataRuntimeBridge` implements Phase 2's generic `ExecutionBridgeProvider/Session`; it is constructed only by the service-owned `ExecutionBridgeResolver` from the exact registered data bridge descriptor/config/provider-handler material after the separate support-analyzer material is verified and its embedded `BridgeStaticSupportSummary` revalidates, never passed by a worker/model. `prepare_input` validates/binds the admitted Plan/node/WorkerSpec/config and returns an empty InputSnapshot addition plus frozen handle—data is never prefetched into the current node's InputSnapshot and this phase performs no capability/live/source I/O. After RUNNING, `open_execution` binds the exact ContextSnapshot/InputSnapshot, catalog/schema/source/routing/snapshot/policy/calendar-material digests, Phase 2 `CapabilityGateway`, read-only `PayloadStore` view, `BridgeEvidenceWriter`, `EventRefusalAuditSink`, continued shared evidence sequencer and `AuthoritativeClock`; the sealed `DataPolicyResolver` supplies the sole exact policy/calendar bundle for each method. A provider never receives a raw write-capable PayloadStore.
- the bridge resolves the exact sealed Phase 3 schema snapshot through Phase 2 `SchemaRegistryResolver` using the admitted Plan's registry digest and loads the exact catalog digest; neither worker/model input nor a global “latest” registry can supply a replacement. A data method is callable only when `DataMethodSpec`, `DataSourceDescriptor`, closed Phase 1 `CapabilityDescriptor@1`, handler material and the selected Worker's allowlist form the same reviewed identity set.
- from that authority the bridge executes only the catalog-bound `DataBridgePrefetchBinding` mappings. In canonical operation then frozen-route order it asks the Phase 2 sequencer to pre-issue one operation token and distinct attempt tokens for every possible begin, each bound to this node/bridge's embedded support-summary digest, then creates `DataInvocationScope`, `DataEvidenceCollector` and `DataReader`. Cache/no-call evidence uses the operation token; each fallback call consumes only its matching attempt token, and the successful result/ToolCallRecord use that attempt token. Phase 3 may validate/echo but never mint, reuse or renumber tokens. Phase 2 Gateway rejects a capability outside the summary or an invocation beyond its frozen-route maximum and checks the per-summary minimum independently before successful terminal status. `freeze_for_execution(kind)` returns its immutable typed contribution for the resolver to merge; no later/model-selected call may mutate prompt inputs. It classifies complete/recovered outcomes into existing WorkerExecutionResult fields, then applies Phase 1 ordering separately—ToolCallRecords by ordinal, `data_result_refs` by typed semantic projection and remaining direct evidence by generic merge key. Phase 3 does not create a parallel public evidence or tool schema.
- a normal read follows Task 6's one-owner state machine: request and named concrete result are each registry-validated and persisted exactly once through `BridgeEvidenceWriter`; each new value atomically commits evidence payload + `BridgeEvidenceRecorded` control payload + journal RunEvent. Raw output remains unpublished, and `finalize_success` receives only the existing PayloadRefs plus request/result semantic digests. PIT audit/attempts are already bound inside the result digest and are not extra gateway parameters. A verified cache hit uses `record_existing` and produces no ToolCallRecord; provider code cannot call a raw payload put.
- for an LLM view, the bridge calls the pure Task 7 renderer, validates `RenderedDataBlock@1`, persists it once in `main` through `BridgeEvidenceWriter`, and returns an ordered untrusted-block DTO containing its exact `PayloadRef`, media type and bounded length. The provider never holds/calls PromptAssembler. Phase 2 merges all provider block DTOs, persists one registered `PromptAssemblyRecord@1` before its single model invocation and appends that record ref to execution evidence; the data block ref remains transitively ordered only inside that record. Journal recovery classifies refs without duplication: successful finalized calls enter ToolCallRecords, consumed named results enter `data_result_refs`, and only refs not already represented there or transitively by a valid PromptAssemblyRecord (for example a rejected/orphan request or a pre-assembly render) enter direct `execution_evidence_refs`. Thus failure after any write retains evidence without placing the same ref in two tuples.
- for deterministic execution, the bridge returns only typed data values/refs and ToolCallRecords actually consumed; it does not render or return an untrusted block. A required data provider that cannot resolve is a pre-execution failure in either branch; static v1 has no optional activated provider and no fake empty payload.
- the Phase 1 Artifact builder receives the exact pre-node `InputSnapshot` digest plus all three immutable evidence tuples: ToolCallRecords, typed DataResult refs and remaining execution-evidence refs. Its reproducibility digest therefore changes with data/schema/routing/capability/request/result/render/prompt-assembly changes, while provider/wall-clock/object locators remain audit-only. `NodeRun` freezes the same three tuples for every terminal status; successful Artifact Provenance equals them exactly, and a failed/no-Artifact path retains every journaled ref even when the provider never returned normally.
- Phase 2's builder freezes the exact Phase 1 ABI: main `context_snapshot_ref: PayloadRef`, WorkerSpec-ordered `InputArtifactBinding`s, canonical main typed pre-node `data_result_refs`, canonical `memory_record_refs` and `readiness="ready"` for execution. A data call made during a node is **not** retroactively inserted into that snapshot; it enters the three NodeRun/Provenance evidence tuples and, after the layer barrier commits an Artifact, may influence a later layer's newly frozen snapshot. `terminal_partial` is retained only for non-executable terminal nodes.
- normal request/result/render payloads use namespace `main`. Namespace/capability checks occur on write and read; `sealed`, `review` and `audit` payloads cannot enter public ArtifactPool, ContextSnapshot, InputSnapshot or ordinary worker replay.
- audit replay handles both branches. With a valid PromptAssemblyRecord it resolves that typed ref and its transitive render refs plus ToolCallRecord/request and typed DataResult refs; without a prompt record (deterministic or pre-assembly failure) it resolves the direct execution-evidence refs, including any orphan request/render, through the exact typed schemas. Both reconstruct the recorded named DataResult/render evidence with zero source, renderer or model-dependent live calls. Re-execution creates a new run identity and may call sources only under the new frozen context/policy.
- future/missing availability makes the guard call the pending invocation's sink-backed reject recorder exactly once, producing an `audit`-namespace detail and Phase 2 `EventRefusalRecord` before the error; dispatch only re-raises. The already committed main request PayloadRef, its BridgeEvidenceRecorded control payload and journal-only recovery RunEvent remain and are classified as direct failed-NodeRun evidence. There is no raw/result/render main payload, success or public-visible domain RunEvent, cache entry, evidence-counting ToolCallRecord, Artifact or layer commit. The owning node terminates with the reviewed PIT refusal reason; it does not degrade or fall back.

- [ ] **Step 1: Write failing end-to-end integration tests**

Required tests:

1. one authorized source read follows atomic request evidence write once → pending/invoke → PIT/schema → atomic named-result evidence write once → exact four-argument `finalize_success` over the existing PayloadRefs/digests, yielding one complete `DataReadOutcome`, one Phase 1 ToolCallRecord and exact typed evidence in NodeRun/Artifact Provenance;
2. capability absent from WorkerSpec/descriptor is denied before handler invocation;
3. a Phase 2 catalog-bound Plan cannot use a newly registered Phase 3 capability, and catalog/registry digest drift requires a new candidate/approval;
4. request/result/render payload cannot be staged under the wrong schema/namespace or with a mismatched registry/plan/context digest;
5. object-ID and snapshot-ID-only relocation leaves reproducibility/semantic digests stable; data/snapshot-content/schema/routing/capability/renderer content changes them;
6. canonical operation/route pre-issuance makes completion order irrelevant; a two-source fallback uses distinct attempt tokens, successful result/ToolCallRecord use the successful token, unused/cache operation tokens cannot be recycled, and merged data-ref/ToolCallRecord ordering is deterministic;
7. same idempotency key/replay does not duplicate payload/event/artifact visibility; same key/different semantics conflicts;
8. audit replay resolves the exact persisted PromptAssemblyRecord PayloadRef and its ordered render ref plus request/result refs, makes zero external calls and reconstructs identical canonical data/render output;
9. a future or missing-availability candidate transitions pending → rejected, persists one refusal audit, calls no second source, and leaves zero public raw/result/cache/success-record/artifact/commit records;
10. the next layer sees only barrier-committed refs; same-layer data/artifacts remain invisible;
11. the catalog-bound resolver—not worker input—constructs the DataRuntimeBridge; missing required/extra/forged descriptor/config/provider/analyzer material or a false static call bound fails before reservation. `prepare_input` is I/O-free/empty, RUNNING precedes every source call, and the real data provider plus a Phase 2 generic fake second bridge share one sequencer; reversed completion preserves generic contribution order and the distinct Phase 1 evidence-tuple canonicalizers. The real data+memory composition is tested only after Task 9 exists;
12. the DataRuntimeBridge returns the persisted RenderedDataBlock PayloadRef only as an untrusted-block DTO; Phase 2's executor alone merges all providers and persists one PromptAssemblyRecord before model execution, binding that record ref into NodeRun plus successful Artifact. Malicious row text never enters system/skill/guardrail materials;
13. deterministic execution receives typed data/direct evidence but creates neither RenderedDataBlock nor PromptAssemblyRecord; a late/dynamic/model-selected provider call is rejected. Timeout/failure after request/result/block or LLM prompt persistence still leaves all exact typed refs in NodeRun, while wrong namespace/schema/content or any ToolCallRecord/DataResult/execution-evidence drift between NodeRun and successful Artifact fails;
14. crash after the evidence payload/control/event UoW commit but before provider return is recovered from `BridgeEvidenceRecorded` without a duplicate put; failure before commit exposes none, same-key semantic drift conflicts and a raw provider PayloadStore write is impossible;
15. every begun invocation has exactly one terminal success/reject transition across success, retryable fallback, PIT refusal and broken-source paths;
16. at runtime an attempt token is charged to its exact embedded data summary: wrong capability and `max+1` fail before source I/O, successful terminal status fails below that summary's minimum, and a fake second bridge cannot contribute a ToolCallRecord to satisfy the data summary;
17. Phase 1 and Phase 2 suites remain green with old Phase 1/2 registry/catalog-bound fixtures and new Phase 3 cumulative fixtures.

- [ ] **Step 2: Run integration tests to verify failure**

Run: `pytest tests/orchestration/data/test_runtime_integration.py tests/orchestration/data/test_data_replay.py -v`

Expected: FAIL until the bridge is implemented.

- [ ] **Step 3: Implement the thin runtime bridge**

Do not duplicate storage, event, capability, snapshot, Artifact or provenance builders. The bridge resolves and composes the Phase 1/2 owners, and carries Phase 3 typed refs/digests across them.

- [ ] **Step 4: Run Phase 1–3 regression suites**

Run: `pytest tests/orchestration/ -v`

Expected: PASS, including the Phase 2 static-runtime integration/equivalence tests.

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/orchestration/data/runtime.py tests/orchestration/data/test_runtime_integration.py tests/orchestration/data/test_data_replay.py
git commit -m "feat(orchestration): integrate PIT data with runtime provenance and replay"
```

---

## Task 9: Unified PIT-safe memory facade + proposal-only mutation boundary

**Files:**
- Create: `guanlan_v2/orchestration/memory/__init__.py`
- Create: `guanlan_v2/orchestration/memory/models.py`
- Create: `guanlan_v2/orchestration/memory/adapters.py`
- Create: `guanlan_v2/orchestration/memory/store.py`
- Create: `guanlan_v2/orchestration/memory/proposals.py`
- Create: `guanlan_v2/orchestration/memory/schema_registry.py`
- Create: `guanlan_v2/orchestration/memory/catalog.py`
- Create: `guanlan_v2/orchestration/memory/runtime.py`
- Create: `engine/memory_coordination.py` (owner-neutral canonical-root lease/factory; imports no orchestration package)
- Modify: `engine/financial_analyst/dream/proposal_writer.py` (backward-compatible safe/idempotent proposal API; legacy entry point preserved)
- Modify: `engine/financial_analyst/memory_ops.py` (shared owner coordinator for decision-backed exact apply/update/reject and legacy CLI/MCP accept/reject/revert; legacy results are not post-cutover approval evidence)
- Modify: `guanlan_v2/console/store.py` (idempotent structured proposal/decision events under the shared root coordinator)
- Modify: `guanlan_v2/console/tools.py` (human-approved idempotent proposal application through the shared root/file coordinator)
- Modify: `guanlan_v2/console/api.py` (fail-closed verifier-injected approve/reject endpoints for exact structured proposals)
- Modify: `guanlan_v2/console/curator.py` (marker-aware consolidation/archive under the shared root coordinator)
- Test: `tests/test_memory_ops.py`
- Test: `tests/test_memory_coordination.py`
- Test: `tests/test_console_store.py`
- Test: `tests/test_console_tools.py`
- Test: `tests/test_console_api.py`
- Test: `tests/test_curator.py`
- Test: `tests/orchestration/memory/test_handoff.py`
- Test: `tests/orchestration/memory/test_models.py`
- Test: `tests/orchestration/memory/test_schema_registry.py`
- Test: `tests/orchestration/memory/test_catalog.py`
- Test: `tests/orchestration/memory/test_adapters.py`
- Test: `tests/orchestration/memory/test_visibility.py`
- Test: `tests/orchestration/memory/test_snapshot.py`
- Test: `tests/orchestration/memory/test_proposals.py`
- Test: `tests/orchestration/memory/test_runtime_integration.py`
- Create: `tests/orchestration/golden/phase3_full_schema_manifest_v1.json`
- Create: `tests/orchestration/golden/phase3_full_catalog_manifest_v1.json`
- Create: `tests/orchestration/golden/memory_capture_policy_v1.json`

### Handoff and ownership gate

- Phase 1 remains the sole owner of `MemoryRecordRef`, `ContextRuntimeRequirements`, `ContextSnapshot`, `InputSnapshot`, `ToolCallRecord`, `NodeRun` and their builders/projections. Task 9 imports the Task 0-verified ABI; it never adds fields or locally redefines those types. A Phase 3 context supplies typed memory snapshot/selection refs plus a runtime-requirements ref, and every node freezes the complete Phase 1 ready/terminal-partial InputSnapshot rather than a local memory-only substitute.
- Phase 2 supplies `SchemaRegistryResolver`, read-only `PayloadStore` access, `BridgeEvidenceWriter`, `CapabilityGateway`, `PromptAssembler`, `BridgeSupportAnalyzer`, `AuthoritativeClock`, admitted Plan/Worker authorization and replay, plus the same-backend `RuntimeStateCellStore`/`StateCellCompareAndSwapCommand` used by exactly-once memory repositories. Task 9 defines one canonical lexicographically ordered startup union `PHASE3_MEMORY_STATE_CELL_NAMESPACES = ("memory.cutover_preparation.v1", "memory.proposal_preparation.v1", "memory.snapshot_head.v1", "memory.snapshot_operation.v1", "memory.snapshot_preparation.v1", "memory.source_head.v1", "memory.source_operation.v1")`; the service constructs/seals the Phase 2 store once with exactly this union before any repository is created. A reviewed owner map assigns each namespace to exactly one cutover/proposal/source/snapshot repository; each repository validates only its owned subset and can neither reseal nor extend the global store. Missing/extra namespaces, duplicate ownership or construction after first use fail startup. Frozen memory evidence may be stored through the Phase 2 evidence/storage owner, but Task 9 never gains a raw provider write path, independent transaction manager or second support/admission mechanism.
- The authoritative accepted facts remain the existing memory-root Markdown and `var/console/{memory.md,memory.archive.md,sessions/*/notes.md}` files. Existing entry points are `engine/financial_analyst/agent/memory.py`, `agent/memory_index.py`, `memory_paths.py`, `memory_ops.py`, `dream/proposal_writer.py` and `guanlan_v2/console/{store,tools,api,curator}.py`. Task 9 adds a read/proposal facade and narrow backward-compatible idempotency/review hooks to those owners, not a third authoritative memory root, SQLite DB, JSONL log or accepted-write path.
- `tests/orchestration/memory/test_handoff.py` inspects those real read/proposal/accept/reject/revert/archive/session entry points. Read adapters require explicit service-owned roots and must not call a default-root helper that seeds/mutates the filesystem during capture. If repository behavior differs, update this plan from evidence before implementation; do not hide it behind a second writer.

### Strict public contracts (`memory/models.py`)

- `MemoryKind = semantic | episodic | procedural`; v1 `MemoryReviewState = pending | approved`; `StoreDigestPrecondition = DigestHex | Literal["absent"]`. Rejected proposals remain proposal decisions and never become MemoryRecords; an accepted file later removed/reverted is omitted from the next snapshot while old snapshots retain its historical approved revision. V1 does not invent a revoked tombstone state.
- `MemoryCapturePolicy@1`: reviewed logical source IDs; source→kind/scope/importance/mandatory mappings; one-global-cutover requirement; revision identity/state-evidence rule; archive window; canonical context-scope, worker-own/shared, borrower→borrowed-owner, kind and session grant matrices; strict `top_k_non_mandatory`, maximum mandatory count, per-record normalized/rendered byte limit and total rendered byte limit; deterministic rank/tie-break policy; exact policy digest. All set-like grants are canonically sorted/unique. Context selection is `MemoryContextAuthority ∩ policy`; worker selection is the already frozen context/session/snapshot boundary `∩ policy ∩ exact WorkerSpec`, with borrowed scope requiring both policy and `borrowed_from`. One side alone cannot widen either path. Mandatory records do not consume `top_k_non_mandatory`; exceeding any mandatory/count/byte bound fails closed rather than truncating or silently dropping a mandatory record. Physical roots are runtime configuration/audit only. A structurally valid caller policy has no authority; runtime accepts only the exact catalog-bound policy material.
- `MemoryCutoverSourcePayload@1` freezes one normalized baseline source body plus global `cutover_operation_id`, logical source/locator identity, store-content digest and a source revision token deterministically derived from `(cutover_operation_id, logical source identity, store-content digest)`. Its PayloadStore idempotency key is derived from the same tuple. `MemoryCutoverManifest@1` binds that operation ID and contains exact main PayloadRefs to those payloads and their store digests; it deliberately contains no final MemoryRecord revision/content digest, review-evidence digest, availability time or attestation digest. `MemoryCutoverAttestation@1` binds the manifest's main PayloadRef, exact **cutover-policy**/root-state digests, authenticated admin actor and authoritative cutover time. A later reviewed current policy may reclassify only these frozen baseline bodies without a second cutover; snapshot binds both cutover attestation and current policy, and any changed derived record is a new capture-timed revision/head.
- Registered `MemoryCutoverPreparation@1` is a strict immutable `DigestModel` and `main` recovery-control payload. It binds the authenticated admin-operation key, one service-derived operation ID and the complete canonically ordered normalized logical source tuple/store digests/revision-token and source-put-idempotency map; it is not accepted memory. The non-cyclic, crash-recoverable construction order is fixed: derive the stable admin-operation key → first load/verify the `memory.cutover_preparation.v1` result cell and exact typed preparation; only when absent stable-scan raw sources and call `MemoryCutoverRepository.prepare_once(...)`, which uses one Phase 2 UoW to put the registered preparation and CAS that result cell from absent to its staged PayloadRef → idempotently persist every source payload **from that preparation** → persist manifest → acquire the global cutover lease and every reviewed owner-root coordinator in canonical logical-root order → perform the next stable scan used **only for equality validation** → require its complete logical locator set, normalized bytes and store digests to equal the frozen preparation/manifest exactly → `initialize_once(manifest_ref, authenticated_admin, validated_root_state_digest)` persists/returns one attestation → initialize the policy-independent source-continuity genesis from that same validated manifest/root-state digest → release leases → build legacy evidence from attestation + frozen source ref + genesis continuity epoch → build final capture-timed MemoryRecord/revision → persist snapshot. A preparation pre-commit failure exposes neither payload nor cell; an identical retry after commit returns the same preparation without a new initial scan, while same-key semantic drift conflicts. Missing, extra or drifted content at the equality scan fails closed and must never refresh the preparation, update the manifest or cut a replacement baseline. Root leases remain held through attestation and genesis initialization; crash recovery reacquires them, repeats the equality-only scan and reuses the same operation/token/put identities. A crash after attestation but before genesis may resume only if the current root-state digest still equals the attested/frozen digest; otherwise ONLINE remains blocked. Competing preparation/initialization conflicts. The v1 repository is a narrow adapter over the injected Phase 2 `RuntimeStateCellStore`/`RuntimeUnitOfWork` and payload/event backend, claims only `memory.cutover_preparation.v1` from the already sealed global namespace union, and owns no independent transaction manager/backend. Recovery is supported only when that backend survives the service object/process; the Phase 2 in-memory backend makes no cross-process durability claim. Lost/corrupt repository, preparation or payload state blocks ONLINE until reviewed evidence is restored or a later durable backend is installed—rescanning a new baseline is forbidden.
- Owner-neutral `engine.memory_coordination.ProcessSharedRootLease` and `RootLeaseFactory` are the one concrete lock implementation for cutover, AgentMemory and console roots. That module imports neither `guanlan_v2` nor financial-analyst/console domain models; orchestration coordinators, `memory_ops.py`, ConsoleStore/writers and legacy entry-point factories all receive/import this same primitive rather than defining local locks. It resolves and validates the canonical absolute root first, derives an OS/process-shared advisory/exclusive lock identity from that root, records owner/operation metadata for diagnosis, and fails closed when another process or independently constructed coordinator owns the lease; an in-process mutex alone is never sufficient. The global order is `global-cutover lease → canonical root leases in logical-root order → optional target-file locks`, with reverse acquisition rejected. Lease/coordination files live in a reserved non-memory namespace, are excluded from adapter scans/cutover manifests/capture digests, and can never be interpreted as accepted memory or proposal evidence. Subprocess/two-instance tests must prove injected and legacy-factory paths share exclusion across cutover-vs-legacy and exact-vs-legacy races.
- `MemorySourceStateEntry@1` records one logical source/locator as `present | absent`, optional exact store digest, optional validated `apply_marker_id` and a service-owned `continuity_epoch`; present requires a digest, absent forbids digest/marker. `MemorySourceStateSnapshot@1` is a registered, policy/session-independent stable-scan fact binding root-state identity, capture time, canonically ordered entries, exact main `previous_source_state_ref: PayloadRef | None`, matching predecessor hash and verified content digest. Its genesis form has no predecessor and must bind the exact cutover manifest PayloadRef plus attestation PayloadRef; later forms bind neither replacement genesis nor caller epoch. Each content/marker change, missing or reappearing locator advances its epoch—even `A → B → A` or `A → absent → A`; unchanged continuously present bytes/marker retain it. A service-owned `MemorySourceStateHeadRepository` keyed only by logical root-state binding exposes `advance_once(stable_scan_operation_key, candidate)`: one Phase 2 RuntimeUnitOfWork puts the typed state and CASes both the reviewed source-head cell and stable-scan operation-result cell to that staged PayloadRef, so retry returns the same ref and replay proves continuity without live filesystem I/O.
- The owner boundary uses two different facts and never conflates them. Internal owner-owned `OwnerApplyResult` variants are durable recovery primitives persisted under the Agent/console root lease by the existing owner modules; they bind operation identity, logical target, marker, expected/intended values, observed before/after target/container store digests and owner result state, but import neither `guanlan_v2` nor Phase 2 and are not public orchestration schemas. After the owner lock is released, the orchestration adapter resolves the exact Proposal/Receipt/Decision, verifies the recovered owner result byte-for-byte, and idempotently projects `MemoryOwnerApplySemanticReceipt@1` into `main`. This registered receipt contains those semantic bindings plus the exact Decision ref. Physical root/path, audit ID/time, Git staged/error and raw owner log fields live only in an optional audit projection/ref and never affect MemoryRecord semantics. A crash after the owner write/result but before the public receipt leaves content pending; retry recovers the same owner result and completes the projection. `memory_ops.py` never writes the public receipt, and `AgentAppliedEvidence` binds only its typed ref—not a raw owner/audit digest.
- `MemoryReviewEvidence@1` is a concrete registered `DigestModel` envelope whose discriminated `evidence` field contains exactly `LegacyCutoverEvidence | AgentAppliedEvidence | ConsoleAppliedEvidence`. Legacy evidence binds exact attestation/source payload/source-state genesis epoch and is valid only while that source continuity remains unbroken. Post-cutover variants bind the current source-state entry/continuity epoch and transitively bind exact persisted proposal, pending receipt, authenticated Decision and `MemoryOwnerApplySemanticReceipt` refs; console evidence also binds the exact `console_memory_applied (journal_session_id,event_id)`. The closed target CAS matrix distinguishes intent from observation: create has `expected_before_target_store_digest=actual_before_target_store_digest="absent"`; update has equal digest values; both require `actual_after_target_store_digest == intended_after_target_store_digest`. Console additionally requires actual-before container == expected-before container and actual-after container == intended-after container. Proposal/Receipt/Decision never claim an `actual_*` value. Every semantic value/marker/epoch must cross-match; `None`, blank, physical-path and MemoryRecord digests are invalid. A caller string such as legacy `source="mcp"` is not evidence.
- `MemoryRecord@1`: exact Phase 1 ref identity (`record_id`, required `revision_id`, aware `available_at`, `content_digest`) plus logical source/locator, required `source_continuity_epoch`, owner/scope, kind, immutable normalized text, aware `created_at/valid_from/valid_to`, review state, optional `review_basis: legacy_cutover | memory_ops_approval | console_proposal_approval | None`, optional exact review-evidence `PayloadRef`, finite importance and mandatory flag. `pending` requires basis/evidence absent; `approved` requires matching positive evidence and exact store/text/continuity identity. `valid_to` is exclusive. Its builder verifies the epoch against the bound `MemorySourceStateEntry`, computes the final record revision/content only after evidence exists, then emits/verifies the matching Phase 1 `MemoryRecordRef`; raw store digest, record semantic digest and PayloadRef content digest are named/tested separately and never substituted.
- `MemorySnapshotEntry@1`: exact `MemoryRecordRef` plus the registered `MemoryRecord@1` main `PayloadRef`. It verifies schema, namespace, record/revision/availability/content equality; the nested `PayloadRef.object_id` remains locator-only.
- `MemorySnapshot@1`: as-of, aware capture-completed time, exact `memory_session_id: LogicalId | None`, service-derived policy-independent `scope_binding_digest`, exact main `cutover_attestation_ref: PayloadRef`, exact main `source_state_ref: PayloadRef`, `previous_snapshot_hash: DigestHex | None`, exact main `previous_snapshot_ref: PayloadRef | None`, current policy digest, canonically sorted entries and verified content digest. The cutover/source-state/predecessor refs must resolve their exact schemas and every record locator/continuity epoch must match the bound source state. Previous hash/ref are both absent only for a scope's first snapshot; policy changes use the same scope head and therefore retain predecessor lineage. The snapshot digest includes scope/cutover/source-state/previous/current-policy typed content identities and excludes physical roots, capture wall-clock and payload object IDs. The current snapshot has no self-locator field; after persistence the complete `PayloadRef` becomes `ContextSnapshot.memory_snapshot_ref`, and its nested payload content digest becomes `memory_snapshot_hash`.
- Registered `MemorySnapshotCapturePreparation@1` is a strict immutable `DigestModel` and a `main` recovery-control payload, not approved memory. It binds the stable capture-operation key/ID, authoritative capture time, scope/session/policy digest, exact source-state PayloadRef/hash and exact predecessor PayloadRef/hash (or the closed initial form). The internal `MemorySnapshotCaptureRepository` makes ONLINE capture exactly-once. Source-state and snapshot repositories are narrow adapters over the injected Phase 2 `RuntimeStateCellStore`/`RuntimeUnitOfWork`; from the already sealed global union they claim only `memory.source_head.v1`/`memory.source_operation.v1` and `memory.snapshot_head.v1`/`memory.snapshot_operation.v1`/`memory.snapshot_preparation.v1`, respectively, and own no independent lock/backend. The service derives the key from authenticated request/data-context/authority identity. `prepare_once` first loads the preparation-result cell and, when present, resolves and verifies that exact typed preparation **before** consulting clock or a newer head. When absent, it reads the authoritative clock/head once, builds the preparation, then uses one UoW to put the registered preparation in `main` and CAS the preparation-result cell from absent to its staged PayloadRef; a pre-commit failure exposes neither, and an identical retry returns the committed ref while same-key semantic drift conflicts. `capture_once(preparation_ref, snapshot_payload)` resolves that exact preparation and uses one UoW to put the typed snapshot and CAS both the scope-head cell and capture-operation-result cell to that staged PayloadRef. Failure anywhere before commit exposes none of those three; after commit all are replayable. A crash after preparation commit but before capture, or after capture commit but before query/selection/ContextSnapshot construction, resumes from the same preparation/snapshot refs without a new clock/head read. Repeating the same completed operation returns its original ref even after later operations advance the head; a changed payload/predecessor under the same key or a stale distinct operation conflicts, so retry never silently chooses a newer predecessor.
- `MemoryQuery@1`: query text plus service-derived reader role (`context | worker`), allowed kinds/scopes, optional exact session ID, strict positive bounded `top_k` (bool rejected) and policy digest. Two authority-specific builders exist—there is no generic caller-filled builder:
  - `build_context_memory_query(query_text, top_k, *, authority: MemoryContextAuthority, policy: ResolvedMemoryPolicy) -> MemoryQuery` is used only before ContextSnapshot construction; `authority` is created by the authenticated orchestration entry point and supplies the exact session/context scopes;
  - `build_worker_memory_query(query_text, top_k, *, worker: WorkerSpec, context: ContextSnapshot, snapshot: MemorySnapshot, policy: ResolvedMemoryPolicy) -> MemoryQuery` requires `"memory" in worker.read_categories`, verifies `snapshot.memory_session_id == context.memory_session_id`, derives own scope from `worker.id`, permits borrowed scopes only when both policy and `worker.borrowed_from` grant them, and can only retain or narrow the ContextSnapshot session scope.
  Caller/model values may provide query text and a policy-bounded `top_k`; they can never widen role, scope, kind, session or the policy maximum. In particular, pre-admission code does not fabricate a WorkerSpec or use a not-yet-created RunContext.
- `MemorySelection@1`: exact snapshot digest plus persisted `MemoryQuery@1` `PayloadRef(namespace="main")`, whose payload content equals `query_digest`, and selected entries with explicit rank in deterministic ranking order and a verified order-sensitive content digest. Query object-ID relocation is audit-only; query semantic drift changes selection digest. Ties use `(record_id, revision_id)`; the selection digest is `past_context_hash`. When refs enter Phase 1 InputSnapshot they are separately canonicalized by its ABI, while prompt rendering follows persisted selection ranks.
- `MemoryProposalRequest@1` is the worker-safe registered capability input envelope. Its discriminated `target` contains agent topic/content fields or console `scope/key/text`, but never proposer/run/session/journal/expected-digest/actor/path authority. `MemoryProposalGrant@1` is a canonical catalog-bound least-privilege row from exact worker ID to allowed agent owners and separately allowed console `session | global` scopes; no wildcard exists, borrowed/read access grants nothing, and self-only must be explicit. Registered `MemoryProposalPreparation@1` is a strict immutable `DigestModel` and `main` recovery-control payload that binds the exact Request PayloadRef/semantic identity, stable preparation key, service-derived proposer/run/context/session authority, normalizer version, proposal ID/time/effective date, logical target, marker identity and all payload/target/container CAS digests; it is not a proposal decision or accepted memory. `MemoryProposal@1` is a separate service-owned registered envelope with explicit `proposal_id`, aware `proposed_at`, `effective_date`, normalizer version, logical target, marker identity, intended user-payload digest, expected-before target/container preconditions and intended-after complete target/container digests: `build_memory_proposal(request, *, preparation: MemoryProposalPreparation, worker, plan, run_id, context: ContextSnapshot, facade) -> MemoryProposal` verifies the exact capability/catalog and matching grant **before any pending side effect**, resolves/verifies the typed preparation and copies only its frozen authority/time/CAS values; journal origin/target session derives only from `context.memory_session_id`. Cross-agent, borrowed-owner, session→global and ungranted target requests fail; a session/global console target from a context with no session also fails. No caller-supplied foreign session/journal value exists.
- Both proposal envelopes are concrete `DigestModel`s whose `target` field contains explicitly registered discriminator variants, not an unregistrable top-level `Annotated[Union]` alias. Agent target fields mirror the existing writer (`target_agent, topic_slug, title, confidence, supporting_cases, reasoning, lesson_md`); console keys reject separators, traversal and anything changed/collided by the exact normalizer. Registered `AgentPendingLocator@1` contains only target agent, proposal ID and reviewed relative logical locator; it rejects absolute roots, drive/UNC prefixes, separators outside the closed form and `..`. Registered `ConsolePendingEventLocator@1` contains only `(journal_session_id,event_id)`.
- Proposal digest domains are acyclic and closed. `intended_target_payload_digest` hashes only the exact normalizer-versioned user-memory payload and excludes marker/layout/container bytes. The marker builder computes `marker_hex = sha256(canonical_json(["guanlan.memory.apply-marker.v1", request_ref typed semantic projection, stable proposal_id, logical target, intended_target_payload_digest])).lower_hex` and returns `apply_marker_id = "apply." + marker_hex`, which is exactly one Phase 1 `LogicalId`; no raw digest is used as an ID. `expected_before_target_store_digest` binds the complete pre-write target unit (`"absent"` for create); `intended_after_target_store_digest` hashes the complete normalized target unit including its one reserved marker in the required position. Console additionally freezes expected-before and intended-after complete container digests. `MemoryProposalPreparation.prepare_once` freezes the normalizer version, proposal ID/time/effective date, logical target, payload digest, marker ID and all target/container expected/intended digests before Proposal persistence. Proposal, Receipt and Decision copy and cross-match these values exactly; none derives a marker or intended digest from its own or a later payload ref. `MemoryProposalReceipt@1` additionally binds Proposal ref, pending-store content digest and exactly one logical pending locator, with `status="pending_external_review"`. Physical root/path is owner runtime/audit state and two roots with identical logical content produce the same semantic receipt.
- admin-only `MemoryProposalDecision@1` binds exact proposal/receipt PayloadRefs, the same expected-before/intended-after target/container digests, `approved|rejected`, authenticated human actor, reason and authoritative decision time; it contains no `actual_*` field. The actor comes only from a fail-closed injected `AdminReviewVerifier`, never a body field. It is not accepted by `memory.propose` or any WorkerSpec. A service-owned `MemoryDecisionRepository.decide_once(...)` atomically keys the terminal decision by proposal content identity, not caller idempotency key: the same proposal + identical semantics returns the one stored Decision ref even under a different retry key; any changed actor/reason/digest or `approved`↔`rejected` race conflicts. Decision persistence precedes every owner side effect, and retry recovers the unique ref. Approval adapters may delegate to existing writers only from this persisted record; there is no approved/direct-write request in the worker-facing schema.
- `MemoryBridgePrefetchBinding@1` is a strict registered catalog material that binds the memory bridge ID to exact worker/query-text/`top_k` projections from admitted node params plus the already bound snapshot/context. It cannot select a new snapshot, scope, policy, renderer, clock or live store, and model-generated/late query expansion is absent.
- `MemoryFacadeDescriptor@1` is a self-contained registered body with stable descriptor ID/version, exact proposal `CapabilityRef`, trusted proposal-handler `ContentRef`, one combined capture/visibility/ranking `policy_ref: ContentRef`, renderer `ContentRef`, exact bridge-prefetch binding `ContentRef`, supported proposal targets and canonical `proposal_grants`; it contains no self-referential ContentRef. Builder-owned `PHASE3_MEMORY_FACADE_DESCRIPTOR_REF: ContentRef` hashes the complete canonical JSON bytes of that body as an exact catalog `ContentManifestEntry(kind="guardrail")`; policy/prefetch canonical JSON are separate exact `kind="guardrail"` materials. Thus supported target/grant or handler/policy/prefetch/renderer drift changes the external descriptor ref and catalog digest without a hash cycle. The Phase 1 CapabilityDescriptor stays closed; runtime resolves only this externally bound descriptor material.
- `RenderedMemoryBlock@1`: exact selection/snapshot identities, renderer `ContentRef`, `trust="untrusted_data"`, deterministic text/media type, per-record and total encoded-byte counts and verified digest. `render_memory(...)` resolves only the bound descriptor renderer. It must fail before prompt assembly on any policy byte/count overflow; truncation, partial mandatory rendering and caller renderer override are absent. Raw memory text can never become prompt/skill/guardrail material.
- `MemoryContextAuthority`, `AuthenticatedAdminPrincipal`, `AdminReviewVerifier`, global `MemoryCutoverRepository`, service-owned `MemoryCutoverCoordinator`, policy-independent `MemorySourceStateHeadRepository`, scoped `MemorySnapshotHeadRepository`, `MemorySnapshotCaptureRepository`, `MemoryProposalSubmissionRepository`, `MemoryDecisionRepository`, `MemoryReplayBinding`, `ReviewEvidenceResolver`, `ResolvedMemoryPolicy`, `MemoryReadOutcome(ordinal_token: ExecutionEvidenceOrdinalToken, query_ref: PayloadRef, selection_ref: PayloadRef, rendered_block_ref: PayloadRef | None, selected_memory_refs)` and raw adapter rows/source scan/head state are frozen internal carriers/ports in `PHASE3_MEMORY_INTERNAL_MODELS`. The registered `MemoryCutoverPreparation@1`, `MemorySnapshotCapturePreparation@1` and `MemoryProposalPreparation@1` are deliberately excluded from that tuple and included in the public registry as recovery controls. The concrete neutral root lease/factory, owner-module `OwnerApplyResult` and console `curation_started/curation_committed` journal intents are explicitly outside the Phase 3 schema registry because they belong to owner coordination/recovery modules; only verified semantic projections enter main. The orchestration `ReviewEvidenceResolver` resolves/validates Phase 2 refs and builds a primitive immutable exact mutation command plus the semantic owner-receipt projection; `financial_analyst.memory_ops` receives only that command's strings/digests/paths and imports only the neutral lease module, never `guanlan_v2` or Phase 2 models. `MemoryCutoverCoordinator` owns the one global process-shared cutover lease, acquires reviewed root leases in canonical order and exposes no mutation/refresh operation. The cutover repository exposes `load_preparation/prepare_once/load/initialize_once`; `initialize_once` requires the equality-validated root-state digest and cannot attest a different preparation/manifest. Source-state heads expose `load/advance_once/load_result`; snapshot capture exposes `prepare_once/capture_once/load_result`; proposal/decision repositories expose their single-writer prepare/terminal transitions. Stale/missing conflicts fail the whole operation.

### Preserve data-only replay while extending the registry/catalog

- `PHASE3_MEMORY_PUBLIC_MODELS` explicitly contains `MemoryCapturePolicy`, cutover source/manifest/attestation, `MemorySourceStateEntry/Snapshot`, all three recovery controls (`MemoryCutoverPreparation`, `MemorySnapshotCapturePreparation`, `MemoryProposalPreparation`), `MemoryOwnerApplySemanticReceipt`, every concrete review-evidence/proposal-target/pending-locator variant plus their registered envelopes, record/snapshot/query/selection, proposal grant/request/proposal/receipt/decision, `MemoryBridgePrefetchBinding`, `MemoryFacadeDescriptor` and rendered-block contracts. Together with `PHASE3_FULL_BASE_REGISTRY_DIGEST = PHASE3_DATA_REGISTRY_DIGEST`, `PHASE3_FULL_REGISTRY_DIGEST` and `build_phase3_full_registry(expected_phase3_data_digest)`, it creates a **new** sealed cumulative registry from the immutable Task 5 data snapshot plus those models. Completeness/round-trip tests reject an unregistered nested variant, any recovery preparation, owner receipt, source-state or cutover source schema. The data-only builder/tuple/digest/golden remain byte-for-byte rebuildable for Plans admitted by Tasks 5–8.
- `phase3_full_schema_manifest_v1.json` is a separate reviewed golden. Tests prove the inherited Phase 1/2/data JSON Schemas are byte-identical, order-independent and simultaneously resolvable by `SchemaRegistryResolver` under their original digests.
- `PHASE3_FULL_BASE_CATALOG_DIGEST = PHASE3_DATA_CATALOG_DIGEST`, `PHASE3_FULL_CATALOG_DIGEST` and `build_phase3_full_catalog(phase3_data_snapshot: WorkerCatalogSnapshot, *, facade_descriptor_ref: ContentRef, facade_descriptor: MemoryFacadeDescriptor, memory_bridge_descriptor: ExecutionBridgeDescriptor, memory_bridge_prefetch_binding: MemoryBridgePrefetchBinding, memory_bridge_support_analyzer_ref: ContentRef, resolved_materials: tuple[ResolvedMaterial, ...]) -> WorkerCatalogSnapshot` first reject every non-canonical data base and require `facade_descriptor_ref == PHASE3_MEMORY_FACADE_DESCRIPTOR_REF`, then extend it with the exact `memory.propose` capability (`input_schema_ref=MemoryProposalRequest@1`, `output_schema_ref=MemoryProposalReceipt@1`), facade/policy/prefetch materials and one generic memory execution-bridge descriptor. That descriptor activates exactly on `read_categories` containing `memory`, uses `pre_input_kind="memory_refs_v1"`/`lifecycle="static_prefetch_v1"`, binds the registered prefetch config, trusted MemoryRuntimeBridge provider and the exact reviewed support-analyzer ref, and has a globally reviewed priority distinct from data. Descriptor/facade/policy/prefetch are strict-NFC canonical JSON `kind="guardrail"`; proposal/provider/renderer/support-analyzer implementations use distinct reviewed `kind="handler"` materials. Builder parses/round-trips every complete registered body, verifies semantic and external material digests separately and requires one-to-one coverage. Missing/extra/drifted provider or analyzer material, grant/priority/config mismatch, false bounds or competing refs fails. This full golden performs no WorkerSpec update, emits `phase3_full_catalog_manifest_v1.json` and never rewrites `data_catalog_manifest_v1.json`.
- The memory `BridgeSupportAnalyzer` is pure and receives only the candidate digest, Phase 1-validated PlanDraft node/WorkerSpec plus exact descriptor/prefetch bytes. It verifies every activated reader has exactly one closed query projection and reports `min_finalized_tool_calls_on_success=0` and `max_capability_invocations=0`: memory retrieval is frozen pre-input evidence, not a CapabilityGateway call. It cannot access a clock, store, filesystem, gateway or provider. Before budget reservation, Phase 2 `RuntimeSupportReport` must embed its exact analyzer ref and `BridgeStaticSupportSummary`; provider/analyzer drift or any nonzero/dynamic bound is unsupported.
- The reviewed full-Phase-3 golden grants `memory.propose` to **no existing worker**, contains no production proposal-grant row and preserves every inherited `read_categories` tuple exactly. `PHASE3_FULL_MEMORY_READERS` is a derived/read-only set of inherited workers that already had `"memory"`; it grants nothing but does activate the exact memory bridge descriptor where non-empty, and the frozen prefetch binding must contain one reviewed query projection for every such worker. Canonical runtime tests assert exact inherited allow/deny/provider/config coverage. If no suitable final worker exists, node-read and proposal success conformance tests use a separately reviewed derived test snapshot whose WorkerSpec, prefetch row and descriptor `proposal_grants` agree; a future production reader/proposer/target grant requires a new catalog digest, RuntimeSupportReport and approval. `compat.*` remains ungranted for proposals. There is no `memory.accept`, generic path write, skill write or code write capability.
- `MemoryContextPreparationService` also builds and persists the Phase 1 `ContextRuntimeRequirements@1` for every Phase 3 memory binding, even when its selected record tuple is empty. The fact binds the exact Phase 1 context-subject projection (DataContext + typed snapshot/selection semantics + session, excluding the requirements ref itself), `PHASE3_FULL_REGISTRY_DIGEST`, `PHASE3_FULL_CATALOG_DIGEST`, the complete facade/policy/prefetch/descriptor/provider/support-analyzer/renderer material closure, the exact memory bridge ID and any actually required capability refs. ONLINE and PIT_REPLAY return its main `PayloadRef` with the snapshot/selection refs; Phase 2 resolves and enforces it before reservation. Only the canonical Phase 1 EmptyMemorySnapshot/EmptyMemorySelection pair may omit requirements.

### Existing-store adapters and conservative legacy capture

- `AgentMemoryAdapter` preserves current own/shared/borrowed and `always_include.txt` coverage; excludes `_proposed`, `_pending_introspections`, `_buddy` and other staging/private paths. Static v1 does **not** call existing `MemoryIndex.search` because its API applies `ORDER BY/LIMIT` before any visibility allowlist; Task 9 ranks the already frozen/visible MemoryRecord tuple deterministically in memory. The live search→top-k→fallback-to-`load_all` path is forbidden for orchestration reads.
- `ConsoleMemoryAdapter` reads current global `memory.md`, the same reviewed archive-tail window used by existing console behavior (the policy freezes its size), and only the matching session's `sessions/<sid>/notes.md`; it parses the existing dated/keyed line form, preserves keyed importance, ignores headings and reserved exact-apply metadata markers as user memory, and never crosses session IDs. It separately validates any marker for review evidence. Indexing the full archive would be a future reviewed policy change, not an implicit expansion here.
- Logical record IDs never contain an absolute root or raw Unicode/path text: builders hash the canonical logical identity into `mem.<lowercase-sha256>` so it satisfies Phase 1 `LogicalId`. Agent records derive identity from reviewed owner/source/relative locator; `always_include` targets must remain under the explicit root and reject absolute/`..` paths. Console keyed records derive identity from scope/session/key, and unkeyed records from scope/session/canonical content plus deterministic occurrence index so identical repeated lines are not collapsed. Moving the same logical record into the reviewed archive window preserves identity. Same locator with changed content is a new required revision.
- Legacy adoption is an explicit one-time administrative operation, not “the first capture for this session”. It stable-scans reviewed roots, persists cutover source payloads/manifest/attestation and initializes the exact `MemorySourceStateSnapshot` genesis. Only a source whose current entry remains continuously on that genesis epoch may use `review_basis=legacy_cutover`; all sessions/scopes reuse the same proof. Crash after attestation resumes from frozen refs, not current files. Missing attestation/source payload/source-state genesis blocks ONLINE preparation, and restart/head loss never recreates a baseline.
- After cutover, a new/changed agent record is `approved` only when an exact Decision-backed `MemoryOwnerApplySemanticReceipt` resolves, matches expected/intended/actual target digests and matches a canonical proposal/apply identity marker atomically embedded by the exact owner in the accepted file. Legacy CLI/MCP `accept_proposal(source=...)` remains operational for old callers but cannot create that marker/evidence. A console record requires Decision, exact apply-start marker and `console_memory_applied` semantic receipt/event. Approval alone, visible text written without the marker or a raw audit receipt remains pending. Unmatched direct writes remain `pending`/invisible, so legacy writers remain compatible without bypassing the Worker proposal boundary.
- Existing accepted files do not contain sufficient PIT metadata. Never infer `created_at`, `valid_from` or `available_at` from filesystem mtime, Git timestamp or a date embedded in prose. `MemoryContextPreparationService` receives the already frozen `DataContext.as_of` as the run cutoff and captures stable sources afterwards. A baseline revision uses `created_at = valid_from = available_at = cutover capture_completed_at` and `valid_to=None`; every later newly observed **record revision** uses its own capture-completed instant. It is therefore not visible in that current run and can appear only in a later run, preventing backdating of data observed after the cutoff.
- An ONLINE capture first stable-scans and calls source-state `advance_once` with its stable operation key, atomically persisting/reusing the policy-independent state/head/result for the logical root binding. It then calls `MemorySnapshotCaptureRepository.prepare_once` with the authenticated stable capture-operation key; only that preparation may read/freeze the predecessor from the service-owned head keyed by policy-independent `(scope_binding_digest, memory_session_id)`, never a request parameter, so a policy change cannot reset lineage. After building records from the frozen source-state/predecessor/time, `capture_once` atomically persists/reuses the snapshot, advances the scope head by CAS and records the operation result. Stale distinct operations, concurrent source change, missing/wrong predecessor/source-state typed ref/hash or non-initial absent head fail closed; retry of the same completed/partially completed operation returns the original snapshot PayloadRef even when a later operation has advanced the head. Session heads cannot initialize another legacy baseline. The Phase 2 in-memory backend proves same-process semantics but does not claim cross-process durability before a later durable backend.
- Revision reuse requires the immediately preceding source-state entry to be continuously present with the same epoch **and** the complete immutable tuple to match: normalized content, review state, review basis and exact review-evidence digest. Any changed/missing→reappearing source receives a new epoch and capture-timed revision; old legacy/agent/console evidence cannot approve it, even when bytes return exactly (`A→B→A`, `A→absent→A`) or policy changes in between. New exact applied evidence may approve only its matching epoch. If continuously present content is unchanged but valid applied evidence arrives later, capture emits a new approved revision with `available_at=capture_completed_at`; it never mutates/backdates the pending revision. Rejected proposals never create MemoryRecords. Deleted/reverted accepted content is omitted from the new snapshot while old snapshots remain immutable.
- `MemorySnapshot.as_of == DataContext.as_of` is mandatory, and the Phase 1 ContextSnapshot must bind that exact DataContext content digest and the same `memory_session_id`. PIT_REPLAY verifies those identities plus exact typed snapshot/query/selection/requirements refs and predecessor lineage. ONLINE may contain future-available captured entries, but the selection filter excludes them until a later run.
- The preparation API has a closed mode matrix. `prepare_online(data_context, authority, ...)` may capture/reuse and create a new memory binding. `prepare_pit_replay(data_context, authority: MemoryContextAuthority, *, prior_context_ref: PayloadRef, ...) -> MemoryReplayBinding` first resolves a previously persisted Phase 1 `ContextSnapshot` through the exact registry/PayloadStore, then verifies its DataContext digest/as-of, registry/catalog/policy identity, session and context scopes against authenticated authority. Merely possessing/guessing a ref grants nothing; `None↔session`, leaked foreign session and scope mismatch fail. It projects the exact existing snapshot/source-state/selection refs and hashes without capture, query rewrite or clock/live-store access. Wrong schema/namespace/content or any data-context/as-of/policy mismatch also fails; object-ID relocation remains audit-only when content identity is unchanged. Frozen payload copies are run evidence, never a write-back or third authoritative memory store.

### Visibility-before-ranking and snapshot binding

The retrieval order is closed:

1. resolve and verify the exact frozen snapshot/registry;
2. create the visible set using `available_at <= as_of`, `valid_from <= as_of < valid_to` (or no `valid_to`), `review_state=approved`, role/own/shared/borrowed grants and exact session scope;
3. only then include all visible mandatory records in deterministic ID order, rank the remaining visible records by the frozen role/recency/importance/relevance policy, and take `top_k` non-mandatory records;
4. zero-hit fallback may inspect only that same visible set—never live `AgentMemory.load_all()` or an unfiltered FTS result.

Missing/naive metadata is a loud refusal. A future/pending/expired highly relevant record cannot consume a rank slot. Source enumeration and FTS completion order cannot affect selection; ties use `(record_id, revision_id)`.

- pre-admission `MemoryContextPreparationService` runs after the frozen DataContext/request cutoff but before Plan validation, runtime support, budget and approval. In `ONLINE`, authenticated `MemoryContextAuthority` exists before a new ContextSnapshot: the service resolves the catalog-bound policy, captures/reuses the exact typed `MemorySnapshot`, service-builds/persists the context `MemoryQuery`, then builds/persists the typed `MemorySelection` and the subject-matching `ContextRuntimeRequirements`. In `PIT_REPLAY`, authenticated authority plus an exact **prior persisted** ContextSnapshot PayloadRef are required; the ref alone is never authority and is not the not-yet-built current ContextSnapshot. `prepare_pit_replay` resolves and verifies the prior typed snapshot/selection/requirements closure and supplies an unchanged `MemoryReplayBinding`; it cannot weaken or synthesize requirements. Both paths feed the Phase 1 builder with `memory_snapshot_ref`, `memory_snapshot_hash=memory_snapshot_ref.payload_ref.content_digest`, exact `memory_session_id`, `memory_selection_ref`, `past_context_hash=memory_selection_ref.payload_ref.content_digest` and `runtime_requirements_ref`. Neither fabricates a RunContext or WorkerSpec. Phase 2 must satisfy that exact registry/catalog/material/capability/bridge closure before the first reservation.
- Node-specific selections come only from that same snapshot through Phase 2's pre-input bridge stage. `MemoryRuntimeBridge.prepare_input(..., sequencer, evidence_writer)` consumes one executor-issued token, builds/persists the exact query and selection in `main` through `BridgeEvidenceWriter`, returns their direct typed evidence refs plus canonical Phase 1 `MemoryRecordRef`s and freezes all of them in its prepared handle. Before freeze, the runner computes `expected_memory_record_refs = canonical_union(base_authorized_memory_refs, every completed PreparedBridgeSet provider addition)` with the Phase 1 canonicalizer; it freezes the complete ready `InputSnapshot@1` from that tuple and immediately re-resolves it to require exact equality. Missing, extra, foreign, duplicate-ambiguous, future or post-freeze refs fail; live/proposal/accepted-after-freeze content cannot be added retroactively. This stage uses only frozen registry/read-only PayloadStore facts plus the evidence writer—no legacy/FTS/filesystem, clock, CapabilityGateway, PromptAssembler or model I/O.
- after child reservation and RUNNING, `MemoryRuntimeBridge.open_execution(handle, input_snapshot, sequencer, evidence_writer)` verifies its prepared selection/ref tuple, while the Phase 2 resolver re-verifies the **whole** PreparedBridgeSet-derived `expected_memory_record_refs` tuple against InputSnapshot rather than a provider-local subset. Any drift or late selection fails. Direct query/selection refs retain the generic `(call_ordinal, bridge_priority, bridge_id, within_call_role)` order. On `LLM`, the session renders/persists `RenderedMemoryBlock` through the evidence writer and returns its PayloadRef only as an untrusted-block DTO; it never holds/calls PromptAssembler. Phase 2 merges all providers and appends its one PromptAssemblyRecord ref. On `DETERMINISTIC`, it does not render or return a prompt block. NodeRun and successful Artifact retain identical ToolCallRecord/DataResult/execution-evidence tuples; preparation/execution failure and no-Artifact paths retain every journaled typed ref. Context replay uses typed snapshot/selection/requirements refs; node replay resolves the direct tuple and optional prompt record with zero legacy-store I/O.

### Proposal-only mutation boundary

- `submit_proposal` is idempotent, always returns a pending receipt and never makes content readable in the current snapshot. Same idempotency key + same semantic request returns the already persisted enriched proposal/receipt; same key + different content is a conflict. After Request persistence, the service first verifies exact WorkerSpec capability plus descriptor `proposal_grants`; only then may `MemoryProposalSubmissionRepository.prepare_once(request_ref, authority, store_view, clock, idempotency_key)` act. It first loads and verifies the exact typed preparation from the sealed `memory.proposal_preparation.v1` result cell before consulting clock/store. When absent, it freezes normalizer version, `proposal_id`, aware `proposed_at`, effective date, journal/session authority, logical target, `intended_target_payload_digest`, marker ID, `expected_before_target_store_digest`, `intended_after_target_store_digest` and the console expected-before/intended-after container digests, then uses one Phase 2 UoW to put registered `MemoryProposalPreparation@1` in `main` and CAS the result cell from absent to its staged PayloadRef. The repository is a narrow adapter over the injected Phase 2 state store/UoW, claims only `memory.proposal_preparation.v1` from the already sealed global union, and owns no independent backend. A pre-commit failure exposes neither payload nor cell; retry after commit returns the same preparation before clock/store, so a crash after Request, during preparation, after preparation-before-Proposal put or across midnight cannot change identity/path/date/marker/CAS. Same-key semantic drift conflicts; unauthorized target rejection creates no preparation or pending-store side effect.
- extend the **existing** agent `proposal_writer.py` with a backward-compatible safe pending-proposal API that receives service-assigned proposal ID/effective date, validates target agent/slug/root containment, creates one deterministic `_proposed` path, uses create-if-absent/atomic replace and returns a logical `AgentPendingLocator` plus pending content digest; absolute physical path remains audit/runtime only. The legacy writer remains. A crash after file creation but before Receipt persistence is recovered by the same ID/digest; no second file/date is created. Worker submission never calls accept/reject/revert.
- extend the **existing owner** `engine/financial_analyst/memory_ops.py` with an `AgentMemoryFileCoordinator` keyed by canonical owner root and guarded by `ProcessSharedRootLease`. Primitive exact apply/update/reject **and** existing legacy CLI/MCP accept/reject/revert must all enter this same coordinator with the sole lock order `owner-root lease → target`; no path may perform an unlocked check-then-move/unlink. While holding the locks, the coordinator rereads pending/accepted/intent state, enforces root containment and target CAS, persists/reuses the deterministic apply intent, performs create-if-absent or atomic temp+fsync+replace/move, verifies the reserved marker and actual digest after the write, then durably commits/recovers the owner-owned `OwnerApplyResult` before releasing. It never imports Phase 2/`guanlan_v2` and never constructs or writes `MemoryOwnerApplySemanticReceipt`. An unresolved exact intent or marker-bound target makes a competing legacy overwrite/move/delete fail closed; exact-vs-legacy concurrency permits at most one semantic winner and cannot lose a marker. The orchestration adapter resolves proposal/receipt/Decision refs first, passes an immutable primitive command, verifies the returned/recovered result, and idempotently projects the public main receipt afterward. Same command retry returns the same owner result and completes a missing public projection; stale/conflicting/manual same-text-without-marker fails. Legacy entry points and return shapes remain backward-compatible but cannot create exact markers or `AgentAppliedEvidence` after cutover.
- The Agent accepted-file marker has one closed ASCII grammar, for example `<!-- guanlan-memory-apply-v1 marker_id=<LogicalId> request_digest=<DigestHex> payload_digest=<DigestHex> -->`. After an optional UTF-8 BOM it is exactly the first line of the complete target unit, occurs exactly once and is immediately followed by the normalized payload whose digest it declares. Capture ignores the marker as user text but validates grammar, position, request/payload/target binding and uniqueness. Duplicate, orphaned, relocated or mismatched markers fail closed; a matching unmarked manual file is never exact-apply evidence.
- add a structured console lifecycle to the **existing** `ConsoleStore` journal because today's `review_proposal` is notification-only. One `ConsoleRootCoordinator`, keyed by canonical console root, owns the process-shared writer lease/root lock and is injected into ConsoleStore, `ConsoleMemoryFileCoordinator`, legacy memory writers and curator; independently constructed module/instance locks cannot guard these paths. The only lock order is `console-root lease → optional target-file`, and reverse acquisition is forbidden. `append_event_once` atomically keys lifecycle events by proposal/Decision/idempotency plus expected/intended target/container digests and returns the stored event on identical retry. For `console_memory_proposed`, `console_memory_apply_started` and `console_memory_applied`, event append and `provenance_pinned=true` commit in one coordinator transaction. `delete_session` enters the same root transaction, folds the complete journal (not the 2,000-event tail), rejects a pin and only then deletes; append/apply/curate cannot interleave. Recovery rebuilds pins from the journal without any deletable window. A second process/service instance for the same canonical root fails closed. Receipt/event identity is `(journal_session_id,event_id)`.
- `build_console_router(..., memory_review_verifier: AdminReviewVerifier | None = None)` remains backward-compatible for existing routes. Memory approve/reject routes call the injected verifier; when it is absent they are disabled/fail closed, anonymous/non-admin requests are denied, and a body-supplied actor is ignored/rejected. The endpoint resolves exact persisted proposal/receipt refs and calls `MemoryDecisionRepository.decide_once` to persist/recover the proposal's unique terminal `MemoryProposalDecision` in `main` **before** any apply/reject side effect, then passes only that ref to the owner adapter. A concurrent approve-vs-reject or semantically different second decision fails before an owner write. Workers cannot call this admin port. The default `guanlan_v2/server.py` wiring does not silently invent authentication.
- add one pure `normalize_console_memory_write(...)` shared by request, Proposal, Decision, exact writer, capture and tests. It rejects text over 280 characters, CR/LF, empty text, invalid scope/session and lossy/colliding keys. `ConsoleMemoryFileCoordinator` is subordinate to the injected `ConsoleRootCoordinator`; it owns only target-file selection and atomic temp+fsync+replace. The exact writer enters one root transaction and, without releasing it, (1) persists/recovers `console_memory_apply_started` plus the provenance pin, binding Decision and all preparation-frozen payload/marker/target/container digests, (2) acquires the target-file lock in the sole allowed order and re-verifies target/container CAS, (3) atomically writes and post-verifies the container/marker/digests, then (4) appends `console_memory_applied`, commits its pin and durably records/reuses the console owner-result primitive before releasing file then root locks. The orchestration adapter subsequently verifies that primitive and idempotently persists the registered semantic receipt; owner code does not write PayloadStore main. Internal locked store methods never reacquire locks. Any legacy write enters the same root→file path, refuses while an apply intent is unresolved and preserves existing reserved markers; direct/manual same visible text lacks the marker and cannot be recovered as exact apply. Existing `memory_write_impl` preserves public normalization/return behavior while using these coordinators. Exact service paths use explicit Store/root/session, never `CTX_SID` or module-global `_MEMORY_PATH`.
- the reserved console marker has one closed ASCII grammar, for example `<!-- guanlan-memory-apply-v1 marker_id=<LogicalId> request_digest=<DigestHex> payload_digest=<DigestHex> -->`, and must immediately precede exactly one normalized target record. It never embeds a Proposal/target-store digest that transitively contains the marker. It is non-user-memory metadata: adapters ignore its text but validate its binding. `curator.py` parses marker+target as one indivisible unit under `ConsoleRootCoordinator`; it may neither classify a marker as an unkeyed memory line nor archive/drop/reorder it independently. Key consolidation retains the winning keyed record with its matching marker; when policy allows an unkeyed record to move to archive, the matching marker moves in the same atomic unit. Orphan, duplicate, non-adjacent or digest-mismatched markers fail closed before any rewrite. Console capture requires the exact Decision + apply-start marker + applied owner result/public semantic receipt, not approval or matching visible bytes alone. Retry after exact file-write-before-applied-event may finalize only when the in-container marker and intended target/container digests match the frozen intent; crash before exact write followed by manual identical text remains distinguishable and pending. Accepted agent/console content becomes visible only in a later ONLINE snapshot because approval changes create a new capture-timed revision.
- Cross-file curation is recoverable, not merely lock-protected. Under the console root lease the curator appends an owner-journal `curation_started` intent and pins it atomically; that intent binds a stable operation ID, exact main/archive before and intended-after container digests, and the ordered digests of every moved marker+record unit. It then replaces/archive-fsyncs the archive, replaces/fsyncs main, and appends `curation_committed` plus the final pin state before releasing the lease. Recovery accepts only: both files at before (perform both writes), archive at after with main at before (finish main), or both at after (append the missing commit). Every other combination or digest drift fails closed. Stage-by-stage crash tests prove no marker unit is duplicated, lost or detached.
- all Task 9 source-state/snapshot/query/selection/render, the three registered recovery preparations, proposal Request/enriched Proposal/Receipt/Decision, semantic owner receipt, review evidence and cutover payloads use namespace `main`; optional raw owner audit projections use `audit` and never enter record semantics. `main` means replayable typed run/control evidence, **not** approved memory. Wrong namespace fails. For an authorized future worker the exact proposal sequence is: persist the validated Request once through BridgeEvidenceWriter → verify target grant → atomically persist/recover the typed preparation once from that Request ref → service-build/persist enriched Proposal once → perform/recover pending-store side effect → persist Receipt once through BridgeEvidenceWriter binding Proposal ref → `finalize_success(request_ref=RequestTypedRef, result_ref=ReceiptTypedRef, ...)`. Retry reuses the Request/Preparation/Proposal/Receipt refs; gateway request_ref is never the enriched Proposal or preparation ref. Runtime evidence persistence is not an accepted-memory write.

- [ ] **Step 1: Write failing handoff, contract and immutable-extension tests**

Write every listed `tests/orchestration/memory/` module plus the neutral/process coordination and decision-backed owner regressions in `tests/test_memory_coordination.py`, `tests/test_memory_ops.py`, `tests/test_console_store.py`, `tests/test_console_tools.py`, `tests/test_console_api.py` and `tests/test_curator.py` before implementation. They cover exact upstream imports/projections, real legacy entry points, all strict model/state matrices, cutover/source-state/head/capture/visibility, all three registered preparation round trips and same-backend atomic recovery, proposal/application/crash recovery, runtime evidence, process-shared lock ordering/marker preservation, separate data-only/full registry and catalog goldens, inherited-schema byte identity, non-canonical base rejection, closed CapabilityDescriptor fields and no implicit worker grants. State-store tests require the startup namespace tuple to equal the exact seven-name canonical union, every name to have exactly one repository owner and every repository subset to be contained in that union; missing/extra names, duplicate ownership, local reseal or runtime extension fail. Exact tests prove supported-target/proposal-grant drift changes the facade ref; policy/prefetch/renderer/proposal-handler/provider-handler/support-analyzer/bridge-priority drift changes the correct material ref and full catalog digest; facade/bridge descriptor/policy/prefetch use `kind="guardrail"`, handlers/renderers/analyzer use `kind="handler"`; the analyzer proves exact memory `0/0` capability bounds, and activated readers plus their ContextRuntimeRequirements are bound into RuntimeSupportReport before reservation.

- [ ] **Step 2: Run the initial failing suite**

Run: `pytest tests/orchestration/memory tests/test_memory_coordination.py tests/test_memory_ops.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py -v`

Expected: FAIL until the memory contracts and immutable extensions exist.

- [ ] **Step 3: Implement contracts and immutable full registry/catalog snapshots**

Implement `models.py`, `schema_registry.py` and `catalog.py` first, including the cutover/review-evidence state matrices, registered `MemoryCutoverPreparation@1`/`MemorySnapshotCapturePreparation@1`/`MemoryProposalPreparation@1`, typed query/snapshot/predecessor projections, acyclic proposal digest domains, memory bridge descriptor/provider/support-analyzer and exact `0/0` summary rules. Manually review and freeze `memory_capture_policy_v1.json`, `phase3_full_schema_manifest_v1.json` and `phase3_full_catalog_manifest_v1.json`; tests never regenerate or overwrite a golden. Prove data-only digests still rebuild exactly.

Run: `pytest tests/orchestration/memory/test_models.py tests/orchestration/memory/test_schema_registry.py tests/orchestration/memory/test_catalog.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py -v`

Expected: PASS before filesystem adapters or writers are changed.

- [ ] **Step 4: Implement adapters, capture, visibility and pre-admission preparation**

Required behavioral tests:

1. Agent adapter matches own/shared/borrowed/always-include coverage, enforces root containment and excludes staging/buddy content without calling the seeding default-root helper;
2. console global/reviewed-archive-tail/session/keyed parsing, duplicate occurrence identity, archive identity stability and session isolation;
3. cutover preparation/source payloads/manifest/attestation/source-state genesis initialize exactly once with no digest cycle; the registered preparation put + preparation-result-cell CAS is atomic, and empty baseline, competing preparation/attestation/genesis, crash before/after preparation staging/commit, before/among source puts, before manifest, after attestation-before-genesis and after genesis are covered with surviving-backend recovery and lost/corrupt-backend fail-closed. After preparation commit, retry loads its original PayloadRef before any new initial scan even if roots later move; same-key semantic drift conflicts. At both prepare→attestation and attestation→genesis windows, parameterized add/change/delete races must make the equality-only validation scan reject missing/extra/drifted logical locators/bytes/digests without changing the frozen preparation or baseline. Tests prove the global-cutover→canonical-root→optional-file lock order, leases held through initialization, exact root-state digest binding, same-state retry and drifted-state fail-closed. Subprocess/two-independent-coordinator tests prove cutover-vs-Agent/console legacy writers and exact-vs-legacy writers cannot both enter; coordination files are excluded from scans/capture. Retry reuses exact operation/token/put identities; restart/new session cannot reclassify a post-cutover direct edit as legacy-approved;
4. policy-independent source-state `advance_once` atomically binds typed state + head CAS + stable-scan operation result, followed by exactly-once `(scope_binding_digest, memory_session_id)` snapshot capture: `prepare_once` first atomically binds the registered preparation payload + preparation-result-cell CAS, then `capture_once` binds snapshot payload + head CAS + operation result in one transaction. Inject crashes before preparation commit, after preparation payload staging, after preparation commit before capture, before capture commit, after snapshot payload staging, after committed head but before selection/context construction, and retry after a later operation advances either head; the original operation always returns its original preparation and snapshot PayloadRefs without consulting a new clock/head. Two-session/policy-change concurrency, competing operation keys, same-key semantic drift, stale distinct heads, missing/wrong source-state/predecessor typed ref/hash and old-lineage replay all fail or recover by the closed matrix;
5. conservative unchanged/new/changed/deleted/reappeared behavior; mtime, physical-root and payload-object relocation cannot backdate/change semantic identity. `A→B→A` and `A→absent→A`, including an intervening policy change, advance continuity epoch and cannot reuse legacy/applied evidence; continuously present policy reclassification creates a linked capture-timed revision/head without re-cutting raw baseline;
6. the pending/approved basis/evidence matrix, including `write → pending capture → exact marker + semantic applied receipt → new approved capture revision → later-run visibility`, stale/forged/raw-audit-only evidence, approval-without-application, proposal rejection producing no MemoryRecord and accepted-file removal disappearing only from later snapshots;
7. filter-before-rank for future/pending/expired/wrong-role/wrong-session records, mandatory/top-k/byte budgets, mandatory overflow fail-closed and zero-hit fallback;
8. canonical query/selection/snapshot digests independent of filesystem/completion order; query/snapshot/selection object-ID relocation is invariant but wrong schema/content digest fails;
9. every record ref resolves exact schema/revision/availability/content/continuity epoch and mismatch fails; same text with changed review evidence or epoch is a new immutable revision;
10. a future addition cannot alter an old snapshot/selection hash; ONLINE and PIT_REPLAY obey separate input/state matrices, and replay resolves prior ContextSnapshot, typed snapshot/selection/requirements refs, source-state, query, records and predecessor lineage with zero legacy-store/clock calls. Missing/leaked foreign context ref, absent/drifted ContextRuntimeRequirements, wrong schema/namespace/session/authority scope/data-context/as-of/policy/catalog/material/capability/bridge and attempted query rewrite fail before reservation, while object relocation alone is invariant.

Run: `pytest tests/orchestration/memory/test_adapters.py tests/orchestration/memory/test_visibility.py tests/orchestration/memory/test_snapshot.py -v`

Expected: PASS before proposal/runtime integration begins.

- [ ] **Step 5: Implement idempotent proposal owners and post-admission runtime bridge**

Test pre-admission authority→typed query/selection refs→typed snapshot ref→ContextRuntimeRequirements→ContextSnapshot binding and Phase 2 pre-input memory selection→exact MemoryRecordRefs→complete ready InputSnapshot→prepared-handle verification. The InputSnapshot tuple must equal the Phase 1-canonical union of base-authorized refs and **all** completed PreparedBridgeSet additions; missing/extra/foreign/duplicate-ambiguous/future/late refs fail, selection and requirements precede support/freeze/reservation, and the whole expected tuple is reverified after RUNNING. The generic resolver alone constructs the catalog-bound MemoryRuntimeBridge; missing/forged/extra provider/analyzer or nonzero call bounds fail before reservation, while data+memory contributions retain canonical shared-token order under reversed completion.

Cover the four registered payload proposal sequence (Request → Preparation → Proposal → Receipt), target grants (cross-agent/session→global/borrowed-owner denied before preparation), logical pending locators, two-root semantic invariance, single persistence, pending invisibility, service-derived session/journal authority and the closed payload-marker-target-container digest domains. Tests prove preparation put + preparation-result-cell CAS is atomic; crash after Request, after preparation staging, after preparation commit-before-Proposal and before every later put reuses the original PayloadRef before clock/store, including across midnight or later target-state movement, while same-key semantic drift conflicts. Marker derivation is independent of Proposal/Receipt/Decision refs, uses the exact domain/canonical projection and always yields `apply.<64 lowercase hex>` satisfying LogicalId; every public fact cross-matches preparation-frozen values; Agent marker grammar/first-line/exactly-once and console adjacency are enforced. Cover direct-edit/stale-decision conflicts. Concurrent approve-vs-reject, identical decision under another key, semantic drift and crash after Decision-before-apply prove one terminal Decision.

Agent exact application binds marker + durable owner result; exact apply/update/reject raced against each legacy accept/reject/revert under the process-shared `AgentMemoryFileCoordinator` permits at most one winner, cannot lose a marker, and leaves a post-verified digest/intent/result state. Inject owner-write/result-before-main-receipt crash and prove the adapter recovers the same OwnerApplyResult then emits one public semantic receipt; raw owner results never approve a MemoryRecord. Physical root/Git/audit-time drift is semantic-invariant while tampered Decision/store fails. Console tests cover fail-closed admin/session inputs, one process-shared root coordinator/lease, enforced root→file lock order, full-journal/pin UoW, event-before-pin, apply/curate/append-vs-delete races, normalization/root/concurrency cases, apply-start intent, exact-write-before-applied/receipt recovery, and the contrasting Decision→manual-identical-before-writer case which remains pending without the marker. Curator tests inject crash before start, after `curation_started`, after archive replace, after main replace and before commit; recovery proves marker+record units are neither duplicated nor lost, while orphan/duplicate/non-adjacent/mismatched markers or unexpected container drift leave files byte-identical and fail closed. `LLM` keeps query/selection direct and render only inside the one executor-owned PromptAssemblyRecord; `DETERMINISTIC` creates neither render nor prompt. Spies prove no worker path reaches owner/admin writes or raw PayloadStore writes.

Run: `pytest tests/orchestration/memory/test_proposals.py tests/orchestration/memory/test_runtime_integration.py tests/test_memory_coordination.py tests/test_memory_ops.py tests/test_console_store.py tests/test_console_tools.py tests/test_console_api.py tests/test_curator.py -v`

Expected: PASS.

- [ ] **Step 6: Run focused Phase 3 memory + cumulative snapshot suites**

Run: `pytest tests/orchestration/memory tests/test_memory_coordination.py tests/test_memory_ops.py tests/orchestration/data/test_data_schema_registry.py tests/orchestration/data/test_data_catalog.py -v`

Expected: PASS with both data-only and full-Phase-3 digests resolvable.

- [ ] **Step 7: Run existing memory/console and orchestration regressions**

Run: `pytest tests/test_bug_fixes_124.py tests/test_playbook_placement.py tests/test_memory_coordination.py tests/test_memory_ops.py tests/test_console_store.py tests/test_console_tools.py tests/test_console_api.py tests/test_curator.py -v`

Then run: `pytest tests/orchestration -v`.

Assert no new authoritative memory root/database/log and no dual accepted-write path were created.

- [ ] **Step 8: Commit**

```bash
git add guanlan_v2/orchestration/memory engine/memory_coordination.py engine/financial_analyst/dream/proposal_writer.py engine/financial_analyst/memory_ops.py guanlan_v2/console/store.py guanlan_v2/console/tools.py guanlan_v2/console/api.py guanlan_v2/console/curator.py tests/test_memory_coordination.py tests/test_memory_ops.py tests/test_console_store.py tests/test_console_tools.py tests/test_console_api.py tests/test_curator.py tests/orchestration/memory tests/orchestration/golden/phase3_full_schema_manifest_v1.json tests/orchestration/golden/phase3_full_catalog_manifest_v1.json tests/orchestration/golden/memory_capture_policy_v1.json
git commit -m "feat(orchestration): add PIT-safe unified memory facade"
```

---

## Phase 3 Exit Gates

The previous “self-review completed” assertion is removed. Phase 3 is complete only when implementation evidence satisfies every applicable data **and memory** gate below for Tasks 0–9.

### Upstream handoff and strict contracts

- [ ] Phase 1 and revised Phase 2 exit suites pass before Phase 3 tests;
- [ ] Task 0 proves exact imports/signatures and registry/payload/event/capability/snapshot extension points; no upstream type is locally redefined;
- [ ] every public Phase 3 DTO is a closed-version strict `ContractModel`/`DigestModel`, immutable when factual, and rejects extra/coerced/naive/non-finite input;
- [ ] every persisted digest is computed/verified with the Phase 1 canonical projection; no short placeholder digest exists;
- [ ] no public generic `RowSet`, consumable `list[dict]`, caller-selected schema or anonymous payload remains;
- [ ] every successful multi-row result contains a registered concrete `PitRecord` subtype, is built only through `DataResult.build`, and persists under its named concrete `*DataResult@1` SchemaRef rather than a runtime generic.

### Registry, routing, snapshot and cache identity

- [ ] the Phase 3 data-only schema snapshot extends rather than mutates the sealed cumulative Phase 2 registry, preserves exact Phase 1/2 subsets, matches `data_schema_manifest_v1.json` and remains rebuildable after Task 9;
- [ ] Task 9 creates a separate immutable full data+memory registry/golden extending the exact data digest; `SchemaRegistryResolver` simultaneously resolves old Phase 1/2, data-only Phase 3 and full Phase 3 digests without a “latest” alias;
- [ ] old Plans resolve their original registry/catalog digests; new data Plans bind the data-only Phase 3 pair and memory-enabled Plans bind the full Phase 3 pair;
- [ ] data-only and full Phase 3 catalog snapshots each extend one canonical base, preserve unchanged entries, use only the closed Phase 1 `CapabilityDescriptor@1` fields, match separate goldens and are loaded by exact digest;
- [ ] a worker/Plan without the exact Phase 3 capability/catalog/registry binding is denied before any source invocation;
- [ ] `DataSourceRegistry` is sealed; descriptor conflicts, mutation after seal and unknown configured method/source are rejected;
- [ ] every default/fallback chain is an explicit versioned order frozen into `DataRoutingSnapshot` and `DataContext`;
- [ ] `DataContext` is the only authority for as-of, mode, backend, strictness, chain, source config, data snapshot and vintage manifest; no dispatch/reader override exists;
- [ ] `DataSnapshotManifest` binds all dataset/source/revision/schema/content identities and a future revision cannot alter an old as-of digest;
- [ ] every semantic `DataCacheKey` dimension is covered; snapshot locator relocation is invariant, cache hits retain the exact result `PayloadRef`, are journaled through `record_existing` without a ToolCallRecord, and refused data is never cached;
- [ ] PIT_REPLAY manifest mismatch, missing immutable snapshot, CACHE-only miss or LIVE route is a hard stop; a miss can reach only an explicitly frozen matching PIT_STORE source.

### PIT, freshness and fallback behavior

- [ ] symbol parsing is exact/full-match, rejects coercion and exchange conflict, and all emitted cache keys use canonical symbols;
- [ ] name resolution uses only a PIT-filtered, digest-verified name snapshot;
- [ ] limit rules reject symbol mismatch and current/future metadata; policy and trading-calendar selection use exact sealed refs/material, and missing/drifted calendar or policy never falls back to a global calendar;
- [ ] missing/naive/future availability is refused in ONLINE and PIT_REPLAY with no silent filter, fallback, result, cache or artifact;
- [ ] refusal audit is persisted once to `EventRefusalAuditSink` before the exception; it never masquerades as a valid public `RunEvent`;
- [ ] only RateLimit/NotConfigured advances the frozen chain; NoData/Stale stops; every other DataError is loud;
- [ ] fallback success is OK + `FALLBACK_USED`; DEGRADED requires policy-approved partial data, coverage and reason;
- [ ] freshness uses the frozen method policy, `AuthoritativeClock` and exact `TradingCalendar`, and its elapsed/session boundary tests pass;
- [ ] every result/raised path retains truthful ordered attempts and PIT audit—no unexecuted guard is labeled passed.

### Phase 2 runtime, evidence and replay

- [ ] every external source call traverses the service-owned two-stage `CapabilityGateway`, exact WorkerSpec allowlist and token-bound data support summary; route attempts use distinct pre-issued tokens, wrong capability/`max+1` fail before I/O and each successful node independently meets that summary's minimum. Pending/rejected invocations cannot satisfy finalized evidence policy;
- [ ] normal request and named result persist exactly once as main `PayloadRef`s through `BridgeEvidenceWriter`; evidence payload + `BridgeEvidenceRecorded` control payload + journal RunEvent are one UoW visibility boundary, provider failure is journal-recoverable, and `finalize_success` only verifies the existing typed refs/digests and never writes again;
- [ ] Phase 1 `ToolCallRecord` binds request/result/DataResult refs and digests; no parallel tool record exists;
- [ ] every begun capability invocation reaches exactly one terminal `finalize_success` or `reject`; retryable fallback rejects the failed pending call once, while PIT-refusal catch paths never reject twice;
- [ ] NodeRun retains exact ToolCallRecord, typed DataResult and remaining execution-evidence tuples for every terminal status; successful Artifact Provenance equals all three and binds Plan/context/catalog/registry/routing/InputSnapshot/data/capability identities with correct semantic/audit separation;
- [ ] a during-node read is not retroactively inserted into the frozen pre-node InputSnapshot; later visibility occurs only through the layer barrier;
- [ ] the support-report-bound generic resolver constructs only exact catalog descriptor/config/provider handlers after the separate support analyzer/material and `BridgeStaticSupportSummary` are verified before reservation; ContextRuntimeRequirements are fully satisfied, memory pre-input additions precede InputSnapshot, RUNNING/timeout precede data/provider I/O, and one sequencer/generic contribution key spans both stages/providers before Phase 1 canonicalizes each evidence tuple. Late/dynamic calls fail;
- [ ] audit replay performs zero external I/O and reconstructs identical recorded named DataResult plus any LLM-persisted `RenderedDataBlock` by exact refs; deterministic runs have no render/prompt ref;
- [ ] idempotent retry/replay cannot duplicate payload, event or artifact visibility, while same-key semantic conflict fails;
- [ ] PIT refusal yields an audit-only refusal and reviewed failed-node reason, with no successful ToolCallRecord, public event, artifact or commit.

### Rendering and scope protection

- [ ] `RenderedDataBlock` is deterministic, versioned and bound to renderer/result/PIT identities;
- [ ] each data renderer comes only from `DataMethodSpec.renderer_ref` and exact catalog material; caller renderer selection is impossible;
- [ ] Task 8 providers persist each LLM-facing block once and return only ordered untrusted-block DTOs; Phase 2's executor alone merges all providers and passes exact `PayloadRef`s to one PromptAssembler/PromptAssemblyRecord, while deterministic execution creates no render-only block or prompt record;
- [ ] embedded instructions/delimiters stay untrusted data and cannot escape the Phase 2 prompt boundary;
- [ ] missing data renders a non-empty no-fabrication sentinel; canonical serialization never uses `default=str`;
- [ ] no real vendor scraper, credential, physical cache root or silent `PitReader` wrapper is smuggled into this interface phase;
- [ ] existing `financial_analyst` runtime and `workflow/executor.run_graph` remain unchanged except through reviewed adapters;
- [ ] unrelated worktree changes are not staged.

### Unified memory facade, PIT and mutation boundary

- [ ] one Task 9 facade covers existing AgentMemory own/shared/borrowed/always-include plus console global/session/keyed/archive semantics without a third authoritative store or accepted-write path;
- [ ] one authenticated global cutover preparation/manifest/attestation plus persisted source-state genesis fixes the only `legacy_cutover` baseline. Registered main preparation put + `memory.cutover_preparation.v1` result-cell CAS commits atomically through the Phase 2 UoW; retry resolves that typed ref before a new initial scan, clock or mutable root read, and same-key drift fails. Before attestation/genesis, a global-cutover→canonical-root lease set protects an equality-only stable re-scan whose complete locators/normalized bytes/store digests must equal the frozen preparation/manifest; missing/extra/drift cannot refresh or re-cut it. Crashes before/among source puts or between attestation/genesis reuse the frozen operation/token map and revalidate the same root-state digest, while restart, missing head, direct edit or a new session/scope cannot initialize another baseline;
- [ ] policy-independent source-state continuity proves each present/absent epoch; `A→B→A` and `A→absent→A` cannot reuse old legacy/applied evidence, policy changes do not reset scope lineage, and PIT replay resolves the proof with zero legacy-store/FTS/filesystem I/O;
- [ ] conservative incremental capture never backdates from mtime/Git/prose dates; a new content, continuity epoch or review-evidence revision takes capture-completed availability;
- [ ] every pending/approved basis/evidence combination follows the closed matrix; rejected proposals create no MemoryRecord and removed/reverted accepted files disappear only from later snapshots. Post-cutover approval requires exact Decision + in-store apply marker + semantic owner receipt whose actual digests equal expected/intended values, never a raw audit receipt, legacy source string, approval-only event or matching visible manual write;
- [ ] availability/validity/review/role/session filtering precedes mandatory/relevance/ranking/top-k/fallback; future/pending/expired records cannot displace visible results, and mandatory/count/byte overflow fails closed without truncation;
- [ ] every `MemoryRecordRef` matches one exact stored record revision/availability/content/continuity epoch; authenticated context/replay authority binds exact session/scopes, typed snapshot/selection refs and hashes plus a subject-matching main ContextRuntimeRequirements ref into ContextSnapshot. Its full registry/catalog/material/capability/bridge requirements are rejected before reservation when absent or drifted;
- [ ] one startup-only `PHASE3_MEMORY_STATE_CELL_NAMESPACES` union contains exactly the seven reviewed cutover/proposal/source/snapshot namespaces in canonical order, with one owner per name and no repository-local reseal/extension. The source-state head advances through `advance_once` (typed state put + head-cell CAS + stable-scan result-cell CAS), while each policy-independent scope head advances through an exactly-once snapshot-capture operation using that same injected Phase 2 RuntimeStateCellStore/UoW backend: registered main preparation freezes time/source-state/policy/predecessor; one UoW binds preparation put + preparation-result-cell CAS before any replay clock/head read, and a second UoW binds snapshot put + head-cell CAS + operation-result-cell CAS. Every non-initial state/snapshot carries a digest-matching main predecessor PayloadRef; crash/retry returns the original preparation/snapshot refs even after a later head advance. Old source/query/selection/record/lineage refs remain reproducible after policy/future memory changes;
- [ ] payload object-ID relocation is semantic-invariant, but query/record/review/snapshot/selection content changes are semantic; wrong SchemaRef/content/namespace or missing predecessor evidence fails closed;
- [ ] memory `prepare_input` persists query/selection before InputSnapshot freeze; its final memory tuple equals the Phase 1-canonical union of base-authorized refs plus every completed PreparedBridgeSet addition and rejects missing/extra/foreign/duplicate-ambiguous/future/late refs. The resolver reverifies that whole tuple after RUNNING. Query/selection PayloadRefs remain direct evidence; on `LLM`, render remains only inside the single executor-owned PromptAssemblyRecord, while `DETERMINISTIC` creates neither render nor prompt;
- [ ] workers can only submit pending proposals through both exact `memory.propose` capability and catalog-bound proposer→target/scope grant; registered main preparation freezes clock/expected/intended CAS metadata and commits with the `memory.proposal_preparation.v1` result-cell CAS through one Phase 2 UoW before Proposal persistence. Retry resolves the original typed preparation before clock/store, same-key drift fails, logical locators exclude physical paths, one repository-enforced terminal Decision exists per proposal, and no worker path reaches owner/admin/generic writes;
- [ ] `engine.memory_coordination` is the sole concrete canonical-root lease/factory imported by orchestration, Agent and console legacy/exact paths; it imports no orchestration/domain model, subprocess competitors fail closed, and reserved coordination files are absent from every memory scan/digest;
- [ ] exact agent mutation remains owned by decision-backed `memory_ops`: under the process-shared root→target lock it durably emits/reuses only an owner primitive result and never imports orchestration or writes main. The adapter verifies that result and idempotently projects the path/Git/time-invariant public semantic receipt afterward; crash between the two recovers without a second write. Exact and legacy accept/reject/revert paths share the coordinator and closed marker/intent/result rules, so concurrency permits at most one winner. Legacy APIs stay compatible but cannot grant orchestration approval;
- [ ] console review rejects missing/forged admin authority and lossy normalization, uses unique `(journal_session_id,event_id)` evidence and one injected process-shared `ConsoleRootCoordinator` lease across journal, pins, root→file writes, legacy writer, curator and delete. Exact apply holds the root transaction from apply-start+pin through CAS/replace/postverify/applied owner result; the adapter then projects one main receipt. Delete folds/checks pins under that same lease, a second process fails closed, and exact-write crash recovery remains distinguishable from manual identical visible text;
- [ ] Agent and console markers use closed acyclic grammars bound to request/payload identities and are inseparable from their targets. Curator persists a pinned `curation_started` intent, replaces archive then main, commits, and recovers only the three expected before/after combinations; it preserves/moves a matching marker+record unit exactly once while orphan/duplicate/non-adjacent/mismatched markers or container drift fail before rewrite. Accepted agent/console content becomes visible only in a later ONLINE capture;
- [ ] data-only Phase 3 registry/catalog digests remain rebuildable while the separate full data+memory snapshots pass their goldens; the full catalog binds the exact memory support analyzer and its verified `0/0` call bounds, and any later memory-capable phase extends the exact `PHASE3_FULL_REGISTRY_DIGEST/PHASE3_FULL_CATALOG_DIGEST` pair rather than the data-only pair or a “latest” alias.

---

## Execution Handoff

Implement in task order. Mandatory review checkpoints:

1. after Task 0 — Phase 1/2 ABI and runtime-port conformance;
2. after Task 3 — exact symbol and as-of metadata semantics;
3. after Task 5 — concrete `PitRecord` payloads, Phase 3 registry, routing/snapshot/cache identity;
4. after Task 6 — sealed source registry, narrow fallback and refusal audit;
5. after Task 7 — typed reader and untrusted deterministic rendering;
6. after Task 8 — Phase 2 provenance/barrier/replay integration;
7. after Task 9 — memory PIT, snapshot, untrusted rendering and proposal-only closure.

Do not begin Phase 4/5 until every Task 0–9 Phase 3 exit gate has test evidence.
