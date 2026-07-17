# -*- coding: utf-8 -*-
"""Phase 3 · Task 8 — zero-live-I/O replay + audit replay over recorded evidence.

Three replay guarantees:

* a **recorded dispatch** (verified cache hit over snapshot.py's entries) resolves
  the exact typed result identity with ZERO live source invocations and NO
  evidence-counting ToolCallRecord;
* **replay-vs-live equivalence**: the same frozen world re-dispatched yields the
  identical semantic typed identities (content-digest dedup — no duplicate
  payload visibility for the same semantics);
* **audit replay** resolves the persisted PromptAssemblyRecord ref, its ordered
  render ref and the request/result refs through the exact typed schemas, makes
  zero external calls and reconstructs byte-identical canonical render output;
  the no-prompt-record branch resolves the direct refs (including a cache-hit
  request) through their exact typed schemas.

Multi-attempt re-execution here uses DETERMINISTIC nodes: a re-attempted LLM
node re-persists its (identical) prompt record under an attempt-agnostic Phase-2
batch key — a pre-existing executor-side wrinkle outside this task's scope; the
LLM render/prompt path itself is locked by test_runtime_integration.

Run: ``python -m pytest tests/orchestration/data/test_data_replay.py -v``
"""
from __future__ import annotations

from datetime import timezone

import pytest

from guanlan_v2.orchestration.data import runtime as RT
from guanlan_v2.orchestration.data.errors import CacheIntegrityError
from guanlan_v2.orchestration.data.snapshot import (
    build_data_cache_entry,
    build_data_cache_key,
)
from guanlan_v2.orchestration.data.source import (
    InstrumentSeriesParams,
    VerifiedDataCacheHit,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataStatus, NodeStatus
from guanlan_v2.orchestration.refs import typed_ref_sort_key
from tests.orchestration.data.test_runtime_integration import (
    _BACKUP,
    _PRIMARY,
    NODE_PARAMS,
    OHLCV_RESULT_SR,
    build_data_env,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# an in-memory verified DataCache conformance fake (snapshot.py ABI)            #
# --------------------------------------------------------------------------- #
class InMemoryVerifiedCache:
    """Conformance fake for the ``DataCache`` protocol — full re-verification."""

    def __init__(self):
        self._entries: dict = {}
        self.hits = 0
        self.misses = 0

    def put_verified(self, key, result_ref, *, result, pit_audit):
        entry = build_data_cache_entry(
            key=key, result_ref=result_ref,
            result_content_digest=result.content_digest,
            result_audit_digest=result.audit_digest,
            pit_audit_digest=content_digest(result.pit_audit))
        self._entries[key.key_digest] = entry

    def get_verified(self, key, *, ctx, manifest, registry, payload_store):
        entry = self._entries.get(key.key_digest)
        if entry is None:
            self.misses += 1
            return None
        if entry.key != key:
            raise CacheIntegrityError("cache key digest collision with different semantics")
        if key.data_snapshot_content_digest != ctx.data_snapshot_content_digest:
            raise CacheIntegrityError("cache entry does not bind the frozen snapshot content")
        # resolve the stored typed result THROUGH the payload store (no live call).
        result = payload_store.get(entry.result_ref.payload_ref,
                                   expected_schema_ref=entry.result_ref.schema_ref)
        if result.content_digest != entry.result_content_digest:
            raise CacheIntegrityError("cached result content digest drift")
        if content_digest(result.pit_audit) != entry.pit_audit_digest:
            raise CacheIntegrityError("cached PIT audit digest drift")
        self.hits += 1
        return VerifiedDataCacheHit(result=result, result_ref=entry.result_ref)


def _seed_cache_from_live(env, node_run) -> None:
    """Record the live outcome as a snapshot-bound verified cache entry."""
    result_ref = node_run.data_result_refs[0]
    result = env.stores.payloads.get(result_ref.payload_ref,
                                     expected_schema_ref=OHLCV_RESULT_SR)
    spec = env.data_registry.method_spec("ohlcv")
    route = env.data_registry.default_route("ohlcv")
    params_dict = InstrumentSeriesParams.model_validate(NODE_PARAMS).model_dump(mode="python")
    key = build_data_cache_key(
        method_ref=spec.method_ref, source_ref=route.entries[0].source_ref,
        result_schema_ref=spec.result_schema_ref,
        params_digest=content_digest(params_dict), as_of=env.dc.as_of, mode=env.dc.mode,
        backend=env.dc.backend,
        data_snapshot_content_digest=env.dc.data_snapshot_content_digest,
        vintage_manifest_digest=env.dc.vintage_manifest_digest,
        source_config_digest=env.dc.source_config_digest,
        routing_snapshot_digest=env.dc.routing_snapshot_digest,
        source_registry_digest=env.dc.source_registry_digest,
        schema_registry_digest=env.schema_registry.registry_digest)
    env.cache.put_verified(key, result_ref, result=result, pit_audit=result.pit_audit)


# =========================================================================== #
# 1. recorded dispatch resolves via cache with ZERO live source invocations    #
# =========================================================================== #
def test_recorded_dispatch_resolves_with_zero_live_io():
    env = build_data_env(cache=InMemoryVerifiedCache(), deterministic=True)
    live_run, live_artifact = env.run()
    assert live_run.status is NodeStatus.COMPLETED
    assert env.vendors[_PRIMARY].calls == 1
    _seed_cache_from_live(env, live_run)

    replay_run, replay_artifact = env.run(attempt=2)
    assert replay_run.status is NodeStatus.COMPLETED

    # ZERO further live source invocations — the read resolved from the record.
    assert env.vendors[_PRIMARY].calls == 1
    assert env.vendors[_BACKUP].calls == 0
    assert env.cache.hits == 1

    # a verified cache hit is NOT an evidence-counting tool call.
    assert replay_run.tool_call_records == ()

    # replay-vs-live equivalence: the EXACT typed result identity (object id and
    # all — the hit carries the stored typed ref, never a detached object).
    assert replay_run.data_result_refs == live_run.data_result_refs

    # the cache request evidence is retained directly (no record represents it).
    names = {r.schema_ref.name for r in replay_run.execution_evidence_refs}
    assert "DataRequest" in names
    # provenance still equals the NodeRun tuples exactly.
    prov = replay_artifact.provenance
    assert prov.tool_call_records == replay_run.tool_call_records
    assert prov.data_result_refs == replay_run.data_result_refs
    assert prov.execution_evidence_refs == replay_run.execution_evidence_refs


def test_live_replay_same_semantics_yields_identical_semantic_refs():
    # a second LIVE dispatch of the same frozen request re-invokes the source
    # honestly, but every persisted value deduplicates to the same SEMANTIC
    # typed identity (schema/namespace/content digest).
    env = build_data_env(deterministic=True)
    run1, _ = env.run()
    run2, _ = env.run(attempt=2)
    assert run2.status is NodeStatus.COMPLETED
    assert env.vendors[_PRIMARY].calls == 2  # honest: this is the LIVE path
    assert [typed_ref_sort_key(r) for r in run2.data_result_refs] == \
        [typed_ref_sort_key(r) for r in run1.data_result_refs]
    rec1, rec2 = run1.tool_call_records[0], run2.tool_call_records[0]
    assert typed_ref_sort_key(rec1.request_ref) == typed_ref_sort_key(rec2.request_ref)
    assert typed_ref_sort_key(rec1.result_ref) == typed_ref_sort_key(rec2.result_ref)


# =========================================================================== #
# 2. audit replay — prompt-record branch (LLM)                                 #
# =========================================================================== #
def test_audit_replay_resolves_prompt_render_request_result_with_zero_calls():
    env = build_data_env()
    node_run, artifact = env.run()
    calls_before = (env.vendors[_PRIMARY].calls, env.vendors[_BACKUP].calls)

    replay = RT.audit_replay_data_evidence(
        node_run, payload_reader=env.stores.payloads,
        source_registry=env.data_registry, schema_registry=env.schema_registry)

    # zero external calls: no vendor, no gateway, no renderer material I/O.
    assert (env.vendors[_PRIMARY].calls, env.vendors[_BACKUP].calls) == calls_before

    # the exact persisted PromptAssemblyRecord + its ordered render ref resolve.
    assert replay.prompt_record is not None
    assert len(replay.rendered_blocks) == 1
    block = replay.rendered_blocks[0]
    assert block.trust == "untrusted_data"
    assert block.result_ref == node_run.data_result_refs[0]

    # request/result refs resolve through their exact typed schemas + cross-match.
    assert len(replay.tool_reads) == 1
    read = replay.tool_reads[0]
    assert read.request.method_spec_ref.id == "ohlcv"
    assert read.result.status is DataStatus.OK
    assert read.result.request_digest == read.request.request_digest
    assert len(replay.data_results) == 1
    assert replay.data_results[0].content_digest == read.result.content_digest

    # deterministic re-render reconstructed byte-identical canonical output
    # (audit_replay_data_evidence raises on any drift; assert the invariant too).
    assert block.rendered_from_payload_digest == read.result.content_digest


def test_audit_replay_detects_typed_schema_drift():
    env = build_data_env()
    node_run, _ = env.run()
    # tamper: swap the data ref for the tool record's REQUEST ref (wrong typed
    # schema) — the typed resolution MUST fail loudly rather than reconstructing.
    req_ref = node_run.tool_call_records[0].request_ref
    forged = node_run.model_copy(update={"data_result_refs": (req_ref,)})
    with pytest.raises(Exception):
        RT.audit_replay_data_evidence(
            forged, payload_reader=env.stores.payloads,
            source_registry=env.data_registry, schema_registry=env.schema_registry)


# =========================================================================== #
# 3. audit replay — no-prompt-record branch (deterministic + cache request)     #
# =========================================================================== #
def test_audit_replay_no_prompt_branch_resolves_direct_refs():
    env = build_data_env(cache=InMemoryVerifiedCache(), deterministic=True)
    live_run, _ = env.run()
    _seed_cache_from_live(env, live_run)
    replay_run, _ = env.run(attempt=2)
    calls_before = (env.vendors[_PRIMARY].calls, env.vendors[_BACKUP].calls)

    replay = RT.audit_replay_data_evidence(
        replay_run, payload_reader=env.stores.payloads,
        source_registry=env.data_registry, schema_registry=env.schema_registry)

    assert (env.vendors[_PRIMARY].calls, env.vendors[_BACKUP].calls) == calls_before
    assert replay.prompt_record is None
    assert replay.rendered_blocks == ()
    assert replay.tool_reads == ()  # the cache hit finalized no tool call
    # the consumed named DataResult still resolves through its exact schema.
    assert len(replay.data_results) == 1
    assert replay.data_results[0].status is DataStatus.OK
    # every direct execution-evidence ref (the cache request) resolves typed.
    assert len(replay.direct_payloads) == len(replay_run.execution_evidence_refs)
    assert any(type(p).__name__ == "DataRequest" for p in replay.direct_payloads)
