# -*- coding: utf-8 -*-
"""Phase 10 · Task 11 — the ruled rowless-reader discrimination on the memory
bridge (controller ruling C3), BOTH halves.

The catalog build pins the prefetch binding's row set == the Phase-3-derived
memory-reader set one-to-one (``_Phase3MemorySurface.__init__`` row/reader
guard + ``build_phase3_full_catalog`` step (8)), and the analyzer's/provider's
``config_bytes`` are that digest-verified material — so ``rows == 0`` at
runtime unambiguously means "never a reviewed reader" (a later-phase worker,
e.g. Phase-8 ``dec.pm``, declaring the ``memory`` category without a reviewed
query projection). The ruled behaviour, mirrored across both halves:

* analyzer (``memory/catalog.py``): ``rows == 0`` → the honest
  ZERO-CONTRIBUTION summary (``0/0``/no refs) so ``check_runtime_support``
  COMPLETES; ``rows > 1`` (ambiguity) stays a loud ``CatalogError``;
* provider (``memory/runtime.py``): ``rows == 0`` → the honest EMPTY prefetch —
  no memory refs, no query/selection evidence, no rendered memory block for any
  execution kind (least privilege: no reviewed projection = no memory access);
  ``rows > 1`` stays a loud ``MemoryContractError``.

``MemoryBridgePrefetchBinding`` itself refuses duplicate worker rows
(sorted-unique invariant), so the ``rows > 1`` branches are defense in depth
probed past the parse seam; the structural exclusion is pinned here too.
The activation-drift raise stays pinned by
``test_catalog.py::test_analyzer_rejects_a_non_activated_worker``; the kept
analyzer ambiguity raise by
``test_catalog.py::test_analyzer_still_raises_on_ambiguous_multiple_projections``.

Run: ``pytest tests/orchestration/memory/test_rowless_discrimination.py -v``
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.memory.runtime as MR
from guanlan_v2.orchestration.catalog import CatalogError
from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.memory import catalog as MC
from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.worker import (
    BridgeInputContribution,
    BridgeOpenRequest,
    BridgePrepareRequest,
    PreparedBridgeHandle,
)
from tests.orchestration.memory.test_catalog import _memory_reader_worker


@pytest.fixture(scope="module")
def data_snapshot():
    return phase3_data_catalog_snapshot()


@pytest.fixture(scope="module")
def surface():
    return MC.phase3_memory_surface()


@pytest.fixture(scope="module")
def reader(data_snapshot):
    """A real WorkerSpec carrying the ``memory`` read category with NO reviewed
    row in the production (empty) prefetch binding — the dec.pm shape."""
    return _memory_reader_worker(data_snapshot)


def _production_config_bytes(surface):
    return MC.serialize_memory_prefetch_binding(surface.prefetch_binding)


def _one_row_config_bytes(worker_id: str) -> bytes:
    return MC.serialize_memory_prefetch_binding(
        M.MemoryBridgePrefetchBinding.build(
            bridge_id="memory.runtime", bridge_version="1",
            rows=(M.MemoryQueryProjection(
                worker_id=worker_id, query_text_param_pointer="/note", top_k=2),)))


class _ForbiddenEvidenceWriter:
    """A rowless read must never persist evidence — any touch is a failure."""

    def put(self, *a, **k):  # pragma: no cover - failing is the assertion
        raise AssertionError("the rowless empty prefetch must not write evidence")


def _provider(config_bytes: bytes) -> MR.MemoryRuntimeBridgeProvider:
    return MR.MemoryRuntimeBridgeProvider(
        bridge=SimpleNamespace(bridge_id="memory.runtime", priority=200),
        summary=SimpleNamespace(summary_digest="s" * 64),
        stores=None, policy=None,
        renderer_ref=ContentRef(id="memory.render.deterministic", version="1",
                                content_digest="1" * 64),
        config_bytes=config_bytes)


def _prepare_request(worker, *, token=None, writer=None) -> BridgePrepareRequest:
    return BridgePrepareRequest(
        plan=None, node=SimpleNamespace(id="n1", params={}), worker=worker,
        bridge=SimpleNamespace(bridge_id="memory.runtime", priority=200),
        summary=SimpleNamespace(summary_digest="s" * 64),
        token=token if token is not None else SimpleNamespace(name="tok-1"),
        context_snapshot_ref=None,
        evidence_writer=writer if writer is not None else _ForbiddenEvidenceWriter())


def _open_request(worker, handle) -> BridgeOpenRequest:
    return BridgeOpenRequest(
        plan=None, node=SimpleNamespace(id="n1", params={}), worker=worker,
        bridge=SimpleNamespace(bridge_id="memory.runtime", priority=200),
        summary=SimpleNamespace(summary_digest="s" * 64),
        handle=handle, input_snapshot=None, capability_gateway=None,
        evidence_writer=_ForbiddenEvidenceWriter(), reader=None)


# --------------------------------------------------------------------------- #
# analyzer half — the zero path                                                #
# --------------------------------------------------------------------------- #
def test_rowless_reader_gets_the_zero_contribution_summary(surface, reader):
    """The flip's analyzer half: an activated reader with NO reviewed row (the
    production binding is empty) completes with the honest zero summary
    instead of the old ``CatalogError``."""
    node = SimpleNamespace(id="n-zero", params={})
    summary = MC.MemoryBridgeSupportAnalyzer().analyze(
        candidate_plan_digest="c" * 64, node=node, worker=reader,
        descriptor=surface.bridge_descriptor, descriptor_ref=surface.descriptor_ref,
        config_bytes=_production_config_bytes(surface))
    assert summary.min_finalized_tool_calls_on_success == 0
    assert summary.max_capability_invocations == 0
    assert summary.allowed_capability_refs == ()
    # the summary still binds the exact worker/node/bridge identity (it is a
    # REAL completed analysis, not a skip).
    assert summary.worker_id == reader.id
    assert summary.worker_digest == reader.semantic_digest()
    assert summary.node_id == "n-zero"
    assert summary.bridge_id == surface.bridge_descriptor.bridge_id


# --------------------------------------------------------------------------- #
# provider half — stage 1 (prepare_input)                                       #
# --------------------------------------------------------------------------- #
def test_rowless_prepare_returns_the_empty_prefetch_shape(surface, reader):
    """rows == 0 ⇒ status='prepared' with an ALL-EMPTY input contribution and
    zero evidence writes — the same return ABI as a reviewed read (a reviewed
    reader whose query matches nothing already yields ``memory_refs=()``; the
    rowless shape additionally persists no query/selection because no reviewed
    projection exists)."""
    provider = _provider(_production_config_bytes(surface))
    token = SimpleNamespace(name="tok-zero")
    outcome = provider.prepare_input(_prepare_request(reader, token=token))
    assert outcome.status == "prepared"
    handle = outcome.prepared_handle
    assert handle is not None
    assert handle.bridge_id == "memory.runtime"
    assert handle.bridge_priority == 200
    assert handle.summary_digest == "s" * 64
    assert handle.token is token
    assert handle.input_contribution == BridgeInputContribution()
    assert handle.input_contribution.memory_refs == ()
    assert handle.input_contribution.memory_evidence_refs == ()


def test_ambiguous_multi_row_prepare_still_raises(reader, monkeypatch):
    """The KEPT loud half (defense in depth past the parse seam — the binding
    model itself refuses duplicate worker rows)."""
    row = SimpleNamespace(worker_id=reader.id)
    monkeypatch.setattr(
        MR, "parse_memory_prefetch_binding",
        lambda _b: SimpleNamespace(bridge_id="memory.runtime", rows=(row, row)))
    provider = _provider(b"ignored-by-the-stubbed-parse")
    with pytest.raises(M.MemoryContractError, match="exactly one"):
        provider.prepare_input(_prepare_request(reader))


# --------------------------------------------------------------------------- #
# provider half — stage 2 (open_execution + freeze)                             #
# --------------------------------------------------------------------------- #
def _rowless_handle(token=None) -> PreparedBridgeHandle:
    return PreparedBridgeHandle(
        bridge_id="memory.runtime", bridge_priority=200,
        summary_digest="s" * 64,
        token=token if token is not None else SimpleNamespace(name="tok-1"),
        input_contribution=BridgeInputContribution())


@pytest.mark.parametrize("kind", [ExecutionKind.LLM, ExecutionKind.DETERMINISTIC])
def test_rowless_open_and_freeze_yield_an_empty_completed_contribution(
        surface, reader, kind):
    """The rowless session completes with an ALL-EMPTY frozen contribution and
    renders NO memory block for any execution kind — the reader honestly ran
    memory-less."""
    provider = _provider(_production_config_bytes(surface))
    session = provider.open_execution(_open_request(reader, _rowless_handle()))
    outcome = session.freeze_for_execution(kind=kind)
    assert outcome.status == "completed"
    c = outcome.frozen_contribution
    assert c is not None
    assert c.bridge_id == "memory.runtime"
    assert c.bridge_priority == 200
    assert c.summary_digest == "s" * 64
    assert c.tool_call_records == ()
    assert c.data_result_refs == ()
    assert c.direct_evidence_refs == ()
    assert c.untrusted_blocks == ()  # NO RenderedMemoryBlock, llm included


def test_rowless_handle_carrying_evidence_is_refused(surface, reader):
    """A rowless worker whose handle claims memory evidence is prepared-handle
    drift — never silently accepted."""
    provider = _provider(_production_config_bytes(surface))
    dirty = PreparedBridgeHandle(
        bridge_id="memory.runtime", bridge_priority=200, summary_digest="s" * 64,
        token=SimpleNamespace(name="tok-1"),
        input_contribution=BridgeInputContribution(
            memory_evidence_refs=(SimpleNamespace(name="fake-q"),
                                  SimpleNamespace(name="fake-s"))))
    with pytest.raises(M.MemoryContractError, match="drift"):
        provider.open_execution(_open_request(reader, dirty))


def test_reviewed_reader_with_an_empty_handle_still_raises(reader):
    """The empty-prefetch shape is EXCLUSIVE to rowless workers: a reviewed
    reader (one row) whose handle lost its query/selection evidence keeps the
    loud refusal — the ruled discrimination cannot swallow evidence loss."""
    provider = _provider(_one_row_config_bytes(reader.id))
    with pytest.raises(M.MemoryContractError, match="exactly \\(query_ref, selection_ref\\)"):
        provider.open_execution(_open_request(reader, _rowless_handle()))


def test_ambiguous_multi_row_open_still_raises(reader, monkeypatch):
    row = SimpleNamespace(worker_id=reader.id)
    monkeypatch.setattr(
        MR, "parse_memory_prefetch_binding",
        lambda _b: SimpleNamespace(bridge_id="memory.runtime", rows=(row, row)))
    provider = _provider(b"ignored-by-the-stubbed-parse")
    with pytest.raises(M.MemoryContractError, match="exactly one"):
        provider.open_execution(_open_request(reader, _rowless_handle()))


# --------------------------------------------------------------------------- #
# the tripwire — what makes rows == 0 unambiguous                               #
# --------------------------------------------------------------------------- #
def test_catalog_build_still_refuses_row_reader_mismatch(reader):
    """The protection the whole discrimination rests on: the catalog build
    refuses ANY binding whose rows don't cover the derived reader set
    one-to-one (both directions), so a reviewed reader can never reach the
    analyzer/provider rowless — the lost-row shape is structurally excluded."""
    # a reader with no row (lost-row direction)
    with pytest.raises(CatalogError, match="exactly one reviewed query"):
        MC._Phase3MemorySurface(
            base_memory_readers=frozenset({reader.id}), prefetch_rows=())
    # a row without a reader (orphan-grant direction)
    with pytest.raises(CatalogError, match="exactly one reviewed query"):
        MC._Phase3MemorySurface(
            base_memory_readers=frozenset(),
            prefetch_rows=(M.MemoryQueryProjection(
                worker_id=reader.id, query_text_param_pointer="/note", top_k=2),))


def test_duplicate_worker_rows_are_refused_at_the_model(reader):
    """The structural exclusion of ``rows > 1``: the binding model itself
    refuses duplicate worker rows, so the kept loud branches are defense in
    depth behind the parse seam."""
    with pytest.raises(ValueError, match="prefetch rows"):
        M.MemoryBridgePrefetchBinding.build(
            bridge_id="memory.runtime", bridge_version="1",
            rows=(M.MemoryQueryProjection(worker_id=reader.id,
                                          query_text_param_pointer="/a", top_k=1),
                  M.MemoryQueryProjection(worker_id=reader.id,
                                          query_text_param_pointer="/b", top_k=2)))


def test_production_reader_set_and_binding_are_empty(surface):
    """The production facts that make ``rows == 0`` the unreviewed-reader
    signal today: zero Phase-3 memory readers, zero prefetch rows."""
    assert MC.PHASE3_FULL_MEMORY_READERS == frozenset()
    assert surface.prefetch_binding.rows == ()
