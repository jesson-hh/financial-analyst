# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1/2/3/4/5 + engine-baseline -> Phase 6 handoff gate.

This is a *consumer* gate: it imports the frozen Phase 1 (amended) contract layer,
the sealed Phase 2 runtime kernel, the Phase 3 data/PIT + memory facade, the
Phase 4 Evaluator-Optimizer chain, the Phase 5 Bootstrap Lane-0 chain **and the
untouched fa backtest engine** exactly as the Phase 6 shadow consumer
(``shadow.py`` + ``adapters/luozi.py``) will, exercises the real registries /
builders / stores / digests / engine signatures through their public surfaces,
and asserts the upstream ABI Phase 6 consumes is intact and singular. Every
assertion PASSES against the already-existing upstream code on branch
``report-evidence-pack`` (``pytest tests/orchestration`` collects the whole
suite green). A failure means an upstream Phase 1–5 contract or the engine
baseline drifted from its frozen exit gate and BLOCKS Phase 6 — it must NEVER be
"fixed" by weakening an assertion or editing upstream; fix the owning layer.

It proves the nine points of ``.superpowers/sdd/task-0-brief.md`` Step 1:

1. the Phase 1 amended golden (``schema_manifest_v1.json``, 11 registered schemas
   incl. ``TypedPayloadRef@1`` / ``InputArtifactBinding@1`` /
   ``ContextRuntimeRequirements@1``) and the digest golden vectors reproduce under
   the re-frozen ``registry_digest``;
2. the Amendment-1 surface holds: ``TypedPayloadRef`` is the composite
   ``(schema_ref: SchemaRef, payload_ref: PayloadRef)``; plain ``PayloadRef`` is the
   unchanged bare locator; ``ContextSnapshot.memory_snapshot_ref`` /
   ``.memory_selection_ref`` are ``TypedPayloadRef``;
3. the whole registry + catalog chain resolves by its exact export names and each
   builder verifies its predecessor digest:
   ``PHASE2_BASE_REGISTRY_DIGEST``/``phase2_runtime_registry`` ->
   ``PHASE3_DATA_REGISTRY_DIGEST``/``build_phase3_registry`` ->
   ``PHASE3_FULL_REGISTRY_DIGEST``/``build_phase3_full_registry`` ->
   ``PHASE4_REGISTRY_DIGEST``/``build_phase4_registry`` ->
   ``PHASE5_REGISTRY_DIGEST``/``build_phase5_registry``; catalog chain
   ``PHASE2_STATIC_CATALOG_DIGEST`` -> ``PHASE3_DATA_CATALOG_DIGEST`` ->
   ``PHASE3_FULL_CATALOG_DIGEST`` -> ``PHASE4_CATALOG_DIGEST`` ->
   ``PHASE5_CATALOG_DIGEST``;
4. Phase 1 ``_DECISION_CLASS_SCHEMAS`` still equals ``{"PortfolioDecision",
   "PortfolioTargetProposal", "TargetPortfolioIntent"}`` and
   ``OrchestrationRequest.decision_schedule_ref: ContentRef | None`` still exists —
   the Phase 6 class names + schedule binding key onto these with no ``spec.py`` edit;
5. the Phase-6 deferred-payload guard still lists exactly ``TargetPosition``,
   ``PortfolioTargetProposal``, ``TargetPortfolioIntent``, ``DecisionSchedule``
   (``test_contract_completeness.DEFERRED_PHASE_PAYLOADS``) and no such model
   exists anywhere under ``guanlan_v2/orchestration/`` yet;
6. Phase 2 ``EventStore.append`` / ``PayloadStore.put`` / ``IdempotencyConflict`` /
   ``RuntimeUnitOfWork`` + ``AuthoritativeClock`` resolve; the Phase 3
   ``TradingCalendar`` protocol (``calendar_id`` / ``material_ref`` / ``coverage``)
   and its concrete ``ImmutableTradingCalendar.is_session`` / ``.sessions_between``
   resolve (see NAME CORRECTION N6);
7. the fa backtest engine baseline API is unchanged at the pinned signatures;
8. the engine has **no** take-profit, **no** max-hold and **no** corporate-action
   ledger (grep-style over the resolved backtest package) — the gaps this phase
   closes above ``Broker``;
9. no Phase 6 source/test path overwrites the Phase 1-owned ``schemas.py`` /
   ``spec.py`` / ``schema_registry.py`` / ``catalog.py`` or any upstream golden.

Step 2's reviewed upstream digests are recorded verbatim as module constants and
re-derived from the sealed builders below.

------------------------------------------------------------------------------
NAME CORRECTIONS recorded here (binding on all later Phase 6 tasks, per the plan's
Task-0 correction clauses — resolve against real code, never invent parallel
semantics):

* **N3 (chain export owners)** — the reviewed chain lives across these modules:
  - ``runtime_contracts.phase2_runtime_registry(expected_phase1_digest)`` +
    ``runtime_contracts.PHASE2_BASE_REGISTRY_DIGEST`` (== the Phase-1 amended digest);
    a wrong base -> ``runtime_contracts.Phase2RuntimeRegistryError``.
  - ``presets.phase2_static_catalog_snapshot()`` + ``presets.PHASE2_STATIC_CATALOG_DIGEST``.
  - ``data.schema_registry.build_phase3_registry(expected_phase2_runtime_digest)`` +
    ``data.schema_registry.PHASE3_DATA_REGISTRY_DIGEST``; wrong base ->
    ``Phase3DataRegistryError``. Its predecessor is the Phase-2 *runtime* digest
    (``phase2_runtime_registry(...).registry_digest``), NOT the Phase-2 base.
  - ``data.catalog.build_phase3_catalog(phase2_snapshot, ...)`` +
    ``data.catalog.phase3_data_catalog_snapshot()`` + ``data.catalog.PHASE3_DATA_CATALOG_DIGEST``.
  - ``memory.schema_registry.build_phase3_full_registry(expected_phase3_data_digest)`` +
    ``.PHASE3_FULL_BASE_REGISTRY_DIGEST`` (== ``PHASE3_DATA_REGISTRY_DIGEST``) +
    ``.PHASE3_FULL_REGISTRY_DIGEST``; wrong base -> ``Phase3FullRegistryError``.
  - ``memory.catalog.build_phase3_full_catalog(...)`` +
    ``memory.catalog.phase3_full_catalog_snapshot()`` + ``.PHASE3_FULL_CATALOG_DIGEST``.
  - ``trial.build_phase4_registry(expected_phase3_full_digest)`` +
    ``trial.PHASE4_BASE_REGISTRY_DIGEST`` (== ``PHASE3_FULL_REGISTRY_DIGEST``) +
    ``trial.PHASE4_REGISTRY_DIGEST``; wrong base -> ``trial.Phase4RegistryError``.
    Catalog: ``trial.build_phase4_catalog_snapshot(...)`` /
    ``trial.phase4_catalog_snapshot()`` / ``trial.PHASE4_CATALOG_DIGEST`` /
    ``trial.PHASE4_BASE_CATALOG_DIGEST`` (== ``PHASE3_FULL_CATALOG_DIGEST``).
  - ``bootstrap.build_phase5_registry(expected_phase4_digest)`` +
    ``bootstrap.PHASE5_BASE_REGISTRY_DIGEST`` (== ``trial.PHASE4_REGISTRY_DIGEST``) +
    ``bootstrap.PHASE5_REGISTRY_DIGEST``; wrong base -> ``bootstrap.Phase5RegistryError``.
    Catalog: ``bootstrap.build_phase5_catalog_snapshot(...)`` /
    ``bootstrap.phase5_catalog_snapshot()`` / ``bootstrap.PHASE5_CATALOG_DIGEST`` /
    ``bootstrap.PHASE5_BASE_CATALOG_DIGEST`` (== ``trial.PHASE4_CATALOG_DIGEST``).
* **N5 (deferred-payload guard location)** — the brief's ``test_contract_completeness.py:74-78``
  drifted: the Phase-6 deferrals live in ``DEFERRED_PHASE_PAYLOADS`` (a single tuple
  carrying the Phase-5 flipped names, the four Phase-6 names, and Phase-8
  ``DebateMessage``), lines ~129-133. The reviewed firewall mechanism Task 1 must
  adopt: (a) keep the four Phase-6 names in ``DEFERRED_PHASE_PAYLOADS`` for the
  Phase-1 absence half; (b) add a ``PHASE6_MODULES`` tuple
  (``guanlan_v2.orchestration.shadow`` + ``guanlan_v2.orchestration.adapters.luozi``)
  to the discovery-firewall exclusion in ``test_contract_completeness.py`` mirroring
  ``PHASE5_MODULES``; (c) add the presence half + a Phase-6 registry firewall in a
  new ``test_phase6_registry.py`` mirroring ``test_phase5_registry.py`` — never
  remove a name from ``DEFERRED_PHASE_PAYLOADS``.
* **N6 (TradingCalendar shape)** — the brief said the ``TradingCalendar`` protocol
  carries ``is_session(date)`` / ``sessions_between(start, end)``. It does NOT: the
  ``data.calendar.TradingCalendar`` Protocol exposes ``calendar_id`` /
  ``material_ref`` / ``coverage`` (all properties); ``is_session(day)`` and
  ``sessions_between(start, end)`` are concrete methods on
  ``data.calendar.ImmutableTradingCalendar``. Phase 6 code that counts sessions
  must consume the concrete class (or a structural type carrying those methods),
  not the Protocol.
* **N-events (out of Task-0 scope, recorded for the later event task)** — the frozen
  ``EventType`` vocabulary is exactly 23 values (``test_events.py`` +
  ``tests/orchestration/memory``): the shadow event task must EXTEND that reviewed
  23-value set to 25 by adding the two shadow values, never restore the pre-Phase-4
  shape. Task 0 does not consume ``EventType`` (not one of the nine points).

Run from repo root: ``python -m pytest tests/orchestration/test_phase6_handoff.py -v``
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import inspect
import json
import pkgutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# -- Phase 1 (amended) contract layer --------------------------------------- #
import guanlan_v2.orchestration as orch_pkg
from guanlan_v2.orchestration.digest import CJSON_VERSION, ContractModel, DigestModel
from guanlan_v2.orchestration.refs import (
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaRegistry,
    default_registry,
)
from guanlan_v2.orchestration.context import (
    ContextRuntimeRequirements,
    ContextSnapshot,
    InputArtifactBinding,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    _DECISION_CLASS_SCHEMAS,
)

# -- Phase 2 runtime kernel ------------------------------------------------- #
from guanlan_v2.orchestration.budget import IdempotencyConflict
from guanlan_v2.orchestration.eventstore import (
    EventStore,
    PayloadStore,
    RuntimeUnitOfWork,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    Phase2RuntimeRegistryError,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration import presets as P

# -- Phase 3 data/PIT + memory facade --------------------------------------- #
from guanlan_v2.orchestration.data import catalog as data_catalog
from guanlan_v2.orchestration.data import schema_registry as data_schema_registry
from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendar,
)
from guanlan_v2.orchestration.data.schema_registry import (
    Phase3DataRegistryError,
    build_phase3_registry,
)
from guanlan_v2.orchestration.memory import catalog as mem_catalog
from guanlan_v2.orchestration.memory import schema_registry as mem_schema_registry
from guanlan_v2.orchestration.memory.schema_registry import (
    PHASE3_FULL_BASE_REGISTRY_DIGEST,
    Phase3FullRegistryError,
    build_phase3_full_registry,
)

# -- Phase 4 Evaluator-Optimizer chain -------------------------------------- #
from guanlan_v2.orchestration import trial

# -- Phase 5 Bootstrap Lane-0 chain ----------------------------------------- #
from guanlan_v2.orchestration import bootstrap

# -- fa backtest engine baseline (untouched; imported = it resolves) -------- #
from financial_analyst.backtest import broker as fa_broker
from financial_analyst.backtest import costs as fa_costs
from financial_analyst.backtest import portfolio as fa_portfolio
from financial_analyst.backtest import limits as fa_limits
from financial_analyst.backtest import metrics as fa_metrics
from financial_analyst.backtest import records as fa_records
from financial_analyst.backtest import decision as fa_decision
from financial_analyst.backtest import engine as fa_engine


# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "golden"
ORCH_DIR = Path(orch_pkg.__file__).resolve().parent

SCHEMA_MANIFEST_GOLDEN = GOLDEN_DIR / "schema_manifest_v1.json"
DIGEST_VECTORS_GOLDEN = GOLDEN_DIR / "digest_vectors_v1.json"

UTC = timezone.utc

# =========================================================================== #
# Step 2 — reviewed upstream digests recorded verbatim.                        #
# The exact sealed registry / catalog digests as implemented and reviewed on   #
# branch report-evidence-pack (HEAD db09d66) — not a local path, a guess or a  #
# mutable singleton identity. Each is re-derived from a sealed builder below.  #
# =========================================================================== #
PHASE1_AMENDED_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE2_RUNTIME_REGISTRY_DIGEST = (
    "b11fcacf0efd931dc3a3d11f859d6f5bac86c1c24b78e5cc1d2b44829822d8d5"
)
PHASE3_DATA_REGISTRY_DIGEST_FROZEN = (
    "9119fa179a598b3d2d62e059747c6fd581d4e8b3be5eea0304da47d80ca51612"
)
PHASE3_FULL_REGISTRY_DIGEST_FROZEN = (
    "ff2a56d283499e347dcdbf4c2f5c5977710fd5575afbc94ee34819b2b531e49d"
)
PHASE4_REGISTRY_DIGEST_FROZEN = (
    "5475880847f5e44fae500545d24fd01f227b507d7b74945e0427bec32f361f0d"
)
PHASE5_REGISTRY_DIGEST_FROZEN = (
    "5703d4c4916c7bdbbe209fcc7cf2ee9971dd0c2fb25313eb401f2b271e1eeb4f"
)

PHASE2_STATIC_CATALOG_DIGEST_FROZEN = (
    "b41bf223f0dd0b05c5a4f4f7bbd32960eef90d7176a17300dbe576f6b61bf0d3"
)
PHASE3_DATA_CATALOG_DIGEST_FROZEN = (
    "ba7086929a79de08a8d4002f36549a4e325dc4c344f615d77506adaf74454e0c"
)
PHASE3_FULL_CATALOG_DIGEST_FROZEN = (
    "c13294e5f020de542cc553d92e509d3dfea45673700373ac0c013d9115c3a773"
)
PHASE4_CATALOG_DIGEST_FROZEN = (
    "aefe0cf3b12f875d4d5062db7b6d00e8ae65cf3f5081eb527d8750671b7e651b"
)
PHASE5_CATALOG_DIGEST_FROZEN = (
    "42af246047adb25956df507b56aabb215baec692859772e4b4f98d776c149229"
)

#: The 11 Phase 1 amended registry schema keys (order-independent).
PHASE1_SCHEMA_KEYS = frozenset(
    {
        "ContextRuntimeRequirements@1",
        "ContextSnapshot@1",
        "EmptyMemorySelection@1",
        "EmptyMemorySnapshot@1",
        "InputArtifactBinding@1",
        "InputSnapshot@1",
        "MemoryRecordRef@1",
        "PortfolioDecision@1",
        "ResearchPlan@1",
        "SentimentReport@1",
        "TypedPayloadRef@1",
    }
)

#: The Phase-6 payloads Phase 6 will mint (shadow.py). At Task 0 they must NOT
#: resolve in any chain registry nor be defined anywhere under the package.
PHASE6_DEFERRED_PAYLOADS = (
    "TargetPosition",
    "PortfolioTargetProposal",
    "TargetPortfolioIntent",
    "DecisionSchedule",
)

#: Phase-1-owned source files Phase 6 must never overwrite (shadow). The four
#: the brief names explicitly + the upstream goldens that pin them.
PHASE1_OWNED_FILES = (
    ORCH_DIR / "schemas.py",
    ORCH_DIR / "spec.py",
    ORCH_DIR / "schema_registry.py",
    ORCH_DIR / "catalog.py",
)
UPSTREAM_GOLDEN_FILES = (
    GOLDEN_DIR / "schema_manifest_v1.json",
    GOLDEN_DIR / "digest_vectors_v1.json",
    GOLDEN_DIR / "phase4_schema_manifest_v1.json",
    GOLDEN_DIR / "phase4_catalog_manifest_v1.json",
    GOLDEN_DIR / "phase5_schema_manifest_v1.json",
    GOLDEN_DIR / "phase5_catalog_manifest_v1.json",
)

#: The Phase-6 module/golden paths the plan creates task-by-task. Task 0 ran from
#: a state with none of them present; the DURABLE point-9 guarantee (learned from
#: the Phase-5 handoff) is that none of them ever *shadows* (equals) a Phase-1-owned
#: source or an upstream golden — never a transient "must not exist" assertion,
#: which Task 1 onward lawfully invalidates.
PHASE6_NEW_PATHS = (
    ORCH_DIR / "shadow.py",
    ORCH_DIR / "adapters",
    ORCH_DIR / "adapters" / "__init__.py",
    ORCH_DIR / "adapters" / "luozi.py",
    GOLDEN_DIR / "phase6_schema_manifest_v1.json",
    GOLDEN_DIR / "phase6_catalog_manifest_v1.json",
)

D64 = "0" * 64


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


def _param_defaults(func) -> dict:
    return {
        name: p.default
        for name, p in inspect.signature(func).parameters.items()
        if p.default is not inspect.Parameter.empty
    }


def _backtest_pkg_dir() -> Path:
    """The resolved fa backtest package dir (never a hard-coded local path)."""
    return Path(fa_engine.__file__).resolve().parent


def _all_registry_names() -> dict[str, set[str]]:
    """The registered schema-name set of every registry in the chain (p1..p5)."""
    p1 = {e.schema_ref.name for e in default_registry().manifest()}
    p2 = {
        e.schema_ref.name
        for e in phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).manifest()
    }
    p3d = {
        e.schema_ref.name
        for e in build_phase3_registry(
            phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest
        ).manifest()
    }
    p3f = {
        e.schema_ref.name
        for e in build_phase3_full_registry(PHASE3_FULL_BASE_REGISTRY_DIGEST).manifest()
    }
    p4 = {
        e.schema_ref.name
        for e in trial.build_phase4_registry(trial.PHASE4_BASE_REGISTRY_DIGEST).manifest()
    }
    p5 = {
        e.schema_ref.name
        for e in bootstrap.build_phase5_registry(
            bootstrap.PHASE5_BASE_REGISTRY_DIGEST
        ).manifest()
    }
    return {"p1": p1, "p2": p2, "p3_data": p3d, "p3_full": p3f, "p4": p4, "p5": p5}


# =========================================================================== #
# Point 1 — Phase 1 amended golden reproduces (11 schemas) + digest vectors     #
# =========================================================================== #
def test_point1_default_registry_is_sealed():
    reg = default_registry()
    assert isinstance(reg, SchemaRegistry)
    assert reg.sealed is True
    with pytest.raises(RegistrySealedError):
        from guanlan_v2.orchestration.schemas import ResearchPlan

        reg.register(ResearchPlan)


def test_point1_amended_schema_manifest_golden_reproduces_11_schemas():
    doc = _load_json(SCHEMA_MANIFEST_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}

    reg = default_registry()
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}

    assert set(golden) == PHASE1_SCHEMA_KEYS
    for landed in ("TypedPayloadRef@1", "InputArtifactBinding@1",
                   "ContextRuntimeRequirements@1"):
        assert landed in golden, f"amendment schema {landed} missing from the golden"
    assert set(manifest) == set(golden), (
        "sealed registry schema-key set drifted from the frozen amended golden"
    )
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"
    assert reg.registry_digest == doc["registry_digest"] == PHASE1_AMENDED_REGISTRY_DIGEST


def test_point1_digest_golden_vectors_are_internally_consistent():
    doc = _load_json(DIGEST_VECTORS_GOLDEN)
    assert doc["algorithm"] == CJSON_VERSION
    assert doc["vectors"], "digest golden must carry vectors"
    for vec in doc["vectors"]:
        recomputed = hashlib.sha256(vec["canonical_json"].encode("utf-8")).hexdigest()
        assert recomputed == vec["digest"], (
            f"golden vector {vec['name']!r} digest != sha256 of its canonical JSON"
        )


# =========================================================================== #
# Point 2 — the Amendment-1 surface (composite TypedPayloadRef / bare PayloadRef) #
# =========================================================================== #
def test_point2_typed_payload_ref_is_the_composite_and_payload_ref_is_the_bare_locator():
    assert TypedPayloadRef.__module__ == "guanlan_v2.orchestration.refs"
    assert set(TypedPayloadRef.model_fields) == {
        "schema_version", "schema_ref", "payload_ref"
    }
    assert TypedPayloadRef.model_fields["schema_ref"].annotation is SchemaRef
    assert TypedPayloadRef.model_fields["payload_ref"].annotation is PayloadRef

    # plain PayloadRef is the unchanged bare locator — no schema_ref field.
    assert set(PayloadRef.model_fields) == {
        "schema_version", "namespace", "object_id", "content_digest"
    }
    assert "schema_ref" not in PayloadRef.model_fields

    # a composite instance actually composes a SchemaRef + a PayloadRef.
    tpr = TypedPayloadRef(
        schema_ref=SchemaRef(name="PortfolioDecision", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="o-1", content_digest="a" * 64),
    )
    assert isinstance(tpr.schema_ref, SchemaRef)
    assert isinstance(tpr.payload_ref, PayloadRef)


def test_point2_amendment_types_resolve_from_phase1_modules():
    assert InputArtifactBinding.__module__ == "guanlan_v2.orchestration.context"
    assert ContextRuntimeRequirements.__module__ == "guanlan_v2.orchestration.context"


def test_point2_context_snapshot_carries_typed_memory_refs():
    assert ContextSnapshot.model_fields["memory_snapshot_ref"].annotation is TypedPayloadRef
    assert ContextSnapshot.model_fields["memory_selection_ref"].annotation is TypedPayloadRef
    assert ContextSnapshot.model_fields["runtime_requirements_ref"].annotation == (
        TypedPayloadRef | None
    )


# =========================================================================== #
# Point 3 — the whole registry + catalog chain resolves by exact names,         #
# each builder verifies its predecessor digest, digests reproduce               #
# =========================================================================== #
def test_point3_registry_chain_digests_reproduce_and_link():
    # linkage: each phase's base digest == its predecessor's sealed digest.
    assert PHASE2_BASE_REGISTRY_DIGEST == PHASE1_AMENDED_REGISTRY_DIGEST
    p2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    assert p2.sealed is True
    assert p2.registry_digest == PHASE2_RUNTIME_REGISTRY_DIGEST

    p3_data = build_phase3_registry(p2.registry_digest)
    assert p3_data.sealed is True
    assert p3_data.registry_digest == PHASE3_DATA_REGISTRY_DIGEST_FROZEN
    assert data_schema_registry.PHASE3_DATA_REGISTRY_DIGEST == PHASE3_DATA_REGISTRY_DIGEST_FROZEN

    assert PHASE3_FULL_BASE_REGISTRY_DIGEST == PHASE3_DATA_REGISTRY_DIGEST_FROZEN
    p3_full = build_phase3_full_registry(PHASE3_FULL_BASE_REGISTRY_DIGEST)
    assert p3_full.sealed is True
    assert p3_full.registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert mem_schema_registry.PHASE3_FULL_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN

    assert trial.PHASE4_BASE_REGISTRY_DIGEST == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    p4 = trial.build_phase4_registry(trial.PHASE4_BASE_REGISTRY_DIGEST)
    assert p4.sealed is True
    assert p4.registry_digest == trial.PHASE4_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST_FROZEN

    assert bootstrap.PHASE5_BASE_REGISTRY_DIGEST == PHASE4_REGISTRY_DIGEST_FROZEN
    p5 = bootstrap.build_phase5_registry(bootstrap.PHASE5_BASE_REGISTRY_DIGEST)
    assert p5.sealed is True
    assert p5.registry_digest == bootstrap.PHASE5_REGISTRY_DIGEST == PHASE5_REGISTRY_DIGEST_FROZEN

    # the six chain digests are all distinct — no "latest" alias collapsed them.
    assert len({
        PHASE1_AMENDED_REGISTRY_DIGEST, PHASE2_RUNTIME_REGISTRY_DIGEST,
        PHASE3_DATA_REGISTRY_DIGEST_FROZEN, PHASE3_FULL_REGISTRY_DIGEST_FROZEN,
        PHASE4_REGISTRY_DIGEST_FROZEN, PHASE5_REGISTRY_DIGEST_FROZEN,
    }) == 6


def test_point3_each_registry_builder_verifies_its_predecessor_digest():
    with pytest.raises(Phase2RuntimeRegistryError):
        phase2_runtime_registry(D64)
    with pytest.raises(Phase3DataRegistryError):
        build_phase3_registry(D64)
    with pytest.raises(Phase3FullRegistryError):
        build_phase3_full_registry(D64)
    with pytest.raises(trial.Phase4RegistryError):
        trial.build_phase4_registry(D64)
    with pytest.raises(bootstrap.Phase5RegistryError):
        bootstrap.build_phase5_registry(D64)


def test_point3_catalog_chain_digests_reproduce_and_link():
    assert P.PHASE2_STATIC_CATALOG_DIGEST == PHASE2_STATIC_CATALOG_DIGEST_FROZEN
    assert P.phase2_static_catalog_snapshot().catalog_digest == PHASE2_STATIC_CATALOG_DIGEST_FROZEN

    assert data_catalog.PHASE3_DATA_CATALOG_DIGEST == PHASE3_DATA_CATALOG_DIGEST_FROZEN
    assert data_catalog.phase3_data_catalog_snapshot().catalog_digest == (
        PHASE3_DATA_CATALOG_DIGEST_FROZEN
    )

    assert mem_catalog.PHASE3_FULL_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    assert mem_catalog.phase3_full_catalog_snapshot().catalog_digest == (
        PHASE3_FULL_CATALOG_DIGEST_FROZEN
    )

    assert trial.PHASE4_BASE_CATALOG_DIGEST == PHASE3_FULL_CATALOG_DIGEST_FROZEN
    assert trial.PHASE4_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST_FROZEN
    assert trial.phase4_catalog_snapshot().catalog_digest == PHASE4_CATALOG_DIGEST_FROZEN

    assert bootstrap.PHASE5_BASE_CATALOG_DIGEST == PHASE4_CATALOG_DIGEST_FROZEN
    assert bootstrap.PHASE5_CATALOG_DIGEST == PHASE5_CATALOG_DIGEST_FROZEN
    assert bootstrap.phase5_catalog_snapshot().catalog_digest == PHASE5_CATALOG_DIGEST_FROZEN

    # the five catalog chain digests are all distinct.
    assert len({
        PHASE2_STATIC_CATALOG_DIGEST_FROZEN, PHASE3_DATA_CATALOG_DIGEST_FROZEN,
        PHASE3_FULL_CATALOG_DIGEST_FROZEN, PHASE4_CATALOG_DIGEST_FROZEN,
        PHASE5_CATALOG_DIGEST_FROZEN,
    }) == 5


def test_point3_catalog_builders_verify_their_predecessor_snapshot():
    from guanlan_v2.orchestration.catalog import CatalogError
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    # the Phase-5 catalog builder rejects a non-canonical base (the Phase-3 data
    # catalog is not the canonical Phase-4 base) — proving the catalog chain's tail
    # builder verifies its predecessor snapshot; the registry chain proves the same
    # for all five phases in test_point3_each_registry_builder_verifies_*.
    lane0 = bootstrap.load_lane0_catalog()
    miner = bootstrap.factor_miner_placeholder()
    with pytest.raises(CatalogError):
        bootstrap.build_phase5_catalog_snapshot(
            phase3_data_catalog_snapshot(), lane0=lane0, factor_miner=miner
        )
    # the canonical Phase-4 base is accepted and reproduces the frozen digest.
    ok = bootstrap.build_phase5_catalog_snapshot(
        trial.phase4_catalog_snapshot(), lane0=lane0, factor_miner=miner
    )
    assert ok.catalog_digest == PHASE5_CATALOG_DIGEST_FROZEN


# =========================================================================== #
# Point 4 — Phase 1 decision-class + schedule binding surfaces intact           #
# =========================================================================== #
def test_point4_decision_class_schemas_are_the_three_frozen_names():
    assert _DECISION_CLASS_SCHEMAS == frozenset(
        {"PortfolioDecision", "PortfolioTargetProposal", "TargetPortfolioIntent"}
    )


def test_point4_orchestration_request_carries_typed_decision_schedule_ref():
    field = OrchestrationRequest.model_fields["decision_schedule_ref"]
    assert field.annotation == (ContentRef | None)
    # the default is None (a request need not bind a schedule) …
    assert field.default is None
    # … and a ContentRef binds cleanly (the Phase-6 schedule-binding surface).
    req = OrchestrationRequest(
        request_id="req-1", goal="g", workflow="orchestrate_only",
        decision_schedule_ref=ContentRef(id="sched-1", version="1", content_digest="a" * 64),
    )
    assert isinstance(req.decision_schedule_ref, ContentRef)


# =========================================================================== #
# Point 5 — the four Phase-6 payloads stay deferred + undefined everywhere       #
# =========================================================================== #
def test_point5_completeness_gate_defers_the_four_phase6_payloads():
    completeness = importlib.import_module(
        "tests.orchestration.test_contract_completeness"
    )
    deferred = set(completeness.DEFERRED_PHASE_PAYLOADS)
    for name in PHASE6_DEFERRED_PAYLOADS:
        assert name in deferred, (
            f"{name} dropped from DEFERRED_PHASE_PAYLOADS before Phase 6 flips it"
        )


def test_point5_phase6_payloads_absent_from_every_chain_registry():
    by_reg = _all_registry_names()
    for name in PHASE6_DEFERRED_PAYLOADS:
        for label, names in by_reg.items():
            assert name not in names, f"{name} leaked into the {label} registry"


#: the ONLY module allowed to define a Phase-6 payload name (the reviewed Phase-6
#: contract module ``shadow.py``). Adapters/registry live elsewhere but define no
#: Phase-6 *payload* contract; the decision-class names are minted only here.
_PHASE6_PAYLOAD_HOME = "guanlan_v2.orchestration.shadow"


def test_point5_phase6_payloads_defined_only_in_the_shadow_module():
    """FLIPPED (Phase 6 · Task 1) from the transient "defined nowhere" guard to its
    permanent anti-shadow form: a Phase-6 payload name may now be defined ONLY in
    ``guanlan_v2.orchestration.shadow`` (and nowhere else under the package).

    Task 1 lands ``TargetPosition`` / ``PortfolioTargetProposal`` in ``shadow.py``;
    ``TargetPortfolioIntent`` / ``DecisionSchedule`` arrive there in later tasks
    (until then no module defines them, which this assertion also permits). Any
    Phase-6 payload leaking into a non-shadow module fails here. The absence half —
    none of the four resolve in any Phase 1–5 chain registry — stays enforced by
    ``test_point5_phase6_payloads_absent_from_every_chain_registry``."""
    def _onerror(mod_name: str) -> None:  # pragma: no cover - keep import failures loud
        raise AssertionError(f"walk_packages failed to import {mod_name!r}")

    definers: dict[str, set[str]] = {name: set() for name in PHASE6_DEFERRED_PAYLOADS}
    for mod_info in pkgutil.walk_packages(
        orch_pkg.__path__, prefix=orch_pkg.__name__ + ".", onerror=_onerror
    ):
        module = importlib.import_module(mod_info.name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, ContractModel)
                and obj.__module__ == mod_info.name
                and obj.__name__ in definers
            ):
                definers[obj.__name__].add(mod_info.name)
    for name, modules in definers.items():
        assert modules <= {_PHASE6_PAYLOAD_HOME}, (
            f"{name} is defined outside {_PHASE6_PAYLOAD_HOME}: "
            f"{sorted(modules - {_PHASE6_PAYLOAD_HOME})}"
        )


# =========================================================================== #
# Point 6 — Phase 2 store/clock exports + Phase 3 calendar resolve               #
# =========================================================================== #
def test_point6_phase2_store_and_clock_exports_resolve():
    for method in ("append", "journal", "visible"):
        assert callable(getattr(EventStore, method)), f"EventStore.{method} missing"
    assert callable(getattr(PayloadStore, "put"))
    assert callable(getattr(PayloadStore, "get"))
    assert callable(getattr(RuntimeUnitOfWork, "commit"))
    assert IdempotencyConflict.__module__ == "guanlan_v2.orchestration.budget"
    assert issubclass(IdempotencyConflict, Exception)

    # AuthoritativeClock is a runtime_checkable Protocol over now() -> datetime.
    assert AuthoritativeClock.__module__ == "guanlan_v2.orchestration.runtime_clock"
    assert "now" in dir(AuthoritativeClock)

    class _FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 7, 19, tzinfo=UTC)

    assert isinstance(_FixedClock(), AuthoritativeClock)  # structural (runtime_checkable)

    class _NotAClock:
        pass

    assert not isinstance(_NotAClock(), AuthoritativeClock)


def test_point6_trading_calendar_protocol_and_concrete_shape():
    # NAME CORRECTION N6: the Protocol carries calendar_id/material_ref/coverage,
    # NOT is_session/sessions_between (those are on the concrete class).
    assert TradingCalendar.__module__ == "guanlan_v2.orchestration.data.calendar"
    for member in ("calendar_id", "material_ref", "coverage"):
        assert member in dir(TradingCalendar), f"TradingCalendar protocol lost {member}"

    # the concrete calendar carries the session-counting methods with pinned sigs.
    assert callable(ImmutableTradingCalendar.is_session)
    assert callable(ImmutableTradingCalendar.sessions_between)
    assert _params(ImmutableTradingCalendar.is_session) == ["self", "day"]
    assert _params(ImmutableTradingCalendar.sessions_between) == ["self", "start", "end"]
    # a real calendar answers is_session / sessions_between over pure dates.
    from guanlan_v2.orchestration.data.calendar import build_trading_calendar

    cal = build_trading_calendar(
        calendar_id="cn_a_share",
        sessions=[date(2026, 7, 17)],
        material_id="calendar.cn_ashare_test",
        material_version="1",
    )
    assert cal.calendar_id == "cn_a_share"
    assert cal.is_session(date(2026, 7, 17)) is True
    assert cal.is_session(date(2026, 7, 18)) is False
    assert cal.sessions_between(date(2026, 7, 1), date(2026, 7, 31)) == 1


# =========================================================================== #
# Point 7 — the fa backtest engine baseline API is unchanged at pinned sigs      #
# =========================================================================== #
def test_point7_broker_match_and_order_signatures():
    assert _params(fa_broker.Broker.match) == [
        "self", "order", "bar", "prev_close", "portfolio",
        "next_bar_open", "next_bar_date", "is_st",
    ]
    defaults = _param_defaults(fa_broker.Broker.match)
    assert defaults == {"next_bar_open": None, "next_bar_date": None, "is_st": False}

    assert dataclasses.is_dataclass(fa_broker.Order)
    order_fields = {f.name: f for f in dataclasses.fields(fa_broker.Order)}
    assert list(order_fields) == [
        "code", "side", "otype", "limit_price", "qty", "cash_budget", "stop_loss"
    ]
    assert order_fields["otype"].default == "limit"
    assert order_fields["limit_price"].default == 0.0
    assert order_fields["qty"].default is None
    assert order_fields["cash_budget"].default == 0.0
    assert order_fields["stop_loss"].default == 0.0


def test_point7_cost_model_defaults():
    assert dataclasses.is_dataclass(fa_costs.CostModel)
    fields = {f.name: f.default for f in dataclasses.fields(fa_costs.CostModel)}
    assert fields == {
        "commission_rate": 0.00025,
        "min_commission": 5.0,
        "stamp_rate": 0.0005,
        "transfer_rate_sh": 0.0001,
        "transfer_rate_other": 0.0,
        "slippage_bps": 5.0,
    }


def test_point7_virtual_portfolio_and_position_methods():
    for method in ("buy", "sell", "mark_to_market", "check_stop", "snapshot",
                   "seed_initial_nav", "record_nav"):
        assert callable(getattr(fa_portfolio.VirtualPortfolio, method)), method
    assert _params(fa_portfolio.VirtualPortfolio.buy) == [
        "self", "code", "qty", "price", "trade_date", "stop_loss"
    ]
    assert _params(fa_portfolio.VirtualPortfolio.sell) == [
        "self", "code", "qty", "price", "trade_date"
    ]
    assert _params(fa_portfolio.VirtualPortfolio.mark_to_market) == ["self", "prices", "on_date"]
    assert _params(fa_portfolio.VirtualPortfolio.check_stop) == ["self", "lows"]
    assert _params(fa_portfolio.VirtualPortfolio.record_nav) == ["self", "date", "prices"]
    assert _params(fa_portfolio.Position.sellable) == ["self", "today"]


def test_point7_limits_signatures():
    assert _params(fa_limits.limit_pct_for) == ["code", "is_st"]
    assert _param_defaults(fa_limits.limit_pct_for) == {"is_st": False}
    assert _params(fa_limits.compute_ref_prev_close) == ["day_close", "factor"]
    assert _params(fa_limits.is_one_word) == ["bar", "prev_close", "pct", "side"]


def test_point7_engine_prepare_bar_and_legs_to_orders_signatures():
    assert _params(fa_engine.prepare_bar) == ["code", "T", "reader", "loader", "cfg"]
    assert _params(fa_engine.legs_to_orders) == [
        "legs", "portfolio", "reader", "loader", "T", "cfg"
    ]


def test_point7_metrics_and_records_signatures():
    mp = _params(fa_metrics.compute_metrics)
    assert mp == ["nav_history", "init_cash", "turnover", "benchmark_nav",
                  "trade_win_rate", "ppy"]
    assert _param_defaults(fa_metrics.compute_metrics)["ppy"] == 252
    assert _params(fa_records.TradeLog.add_fill) == ["self", "fill"]
    assert _params(fa_records.TradeLog.trade_stats) == ["self"]


def test_point7_decision_dataclasses_and_agent_n_calls():
    for name in ("DecisionLeg", "Decision", "DecisionInput"):
        model = getattr(fa_decision, name)
        assert dataclasses.is_dataclass(model), f"{name} must be a dataclass"
    leg_fields = [f.name for f in dataclasses.fields(fa_decision.DecisionLeg)]
    assert leg_fields == ["code", "action", "target_price", "stop_loss", "weight_pct", "reason"]
    decision_fields = [f.name for f in dataclasses.fields(fa_decision.Decision)]
    assert decision_fields == ["market_view", "decisions", "warnings", "raw"]

    # DecisionAgent.n_calls is a property read by BacktestRunner.
    assert isinstance(inspect.getattr_static(fa_decision.DecisionAgent, "n_calls"), property)
    runner_src = inspect.getsource(fa_engine.BacktestRunner)
    assert "agent.n_calls" in runner_src, "BacktestRunner must read agent.n_calls"


# =========================================================================== #
# Point 8 — engine has NO take-profit / max-hold / corporate-action ledger       #
# =========================================================================== #
def test_point8_engine_has_no_take_profit_max_hold_or_corporate_action_ledger():
    pkg_dir = _backtest_pkg_dir()
    sources = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(pkg_dir.glob("*.py"))
    }
    assert sources, "backtest package must contain source modules"
    joined = "\n".join(sources.values())

    # no take-profit as a code identifier (the only 'take-profit' is the
    # disclaiming comment in engine.py — the positive grounding checked below).
    assert "take_profit" not in joined, "engine gained a take_profit identifier"
    assert "profit_target" not in joined
    # positive grounding: the engine explicitly disclaims take-profit.
    assert "take-profit" in sources["engine.py"], (
        "engine.py must retain the explicit 'no take-profit' grounding comment"
    )

    # no max-hold / holding-period cap.
    for token in ("max_hold", "max_days", "holding_period", "max_hold_days"):
        assert token not in joined, f"engine gained a {token} identifier"

    # no corporate-action ledger (the adjust-factor ref-close is NOT a ledger).
    for token in ("corporate_action", "CorporateAction", "corp_action",
                  "dividend_ledger", "apply_corporate_action", "rights_issue"):
        assert token not in joined, f"engine gained a corporate-action token {token!r}"


# =========================================================================== #
# Point 9 — no Phase 6 path overwrites Phase-1-owned files or upstream goldens    #
# =========================================================================== #
def test_point9_phase1_owned_files_and_upstream_goldens_exist_singular():
    for path in PHASE1_OWNED_FILES + UPSTREAM_GOLDEN_FILES:
        assert path.is_file() and path.stat().st_size > 0, f"missing upstream file: {path}"
    # this handoff gate is an additive Phase 6 test; it must not shadow upstream.
    assert Path(__file__).name == "test_phase6_handoff.py"


def test_point9_phase6_paths_never_shadow_phase1_owned_or_golden_files():
    """Point 9 (durable form, learned from the Phase-5 handoff): a Phase-6
    module/golden path is always a NEW path, never an overwrite of the
    Phase-1-owned schemas.py/spec.py/schema_registry.py/catalog.py or an upstream
    golden. Checked structurally rather than by a transient "does not exist yet"
    assertion (which Task 1 onward lawfully invalidates)."""
    owned = set(PHASE1_OWNED_FILES) | set(UPSTREAM_GOLDEN_FILES)
    for path in PHASE6_NEW_PATHS:
        assert path not in owned, (
            f"Phase 6 path {path} shadows a Phase-1-owned/golden file — Phase 6 is "
            "additive and may never overwrite an upstream source or golden"
        )
    # the two Phase-6 goldens (once created) carry the phase6 prefix, never a
    # Phase-1 golden filename.
    for golden in (GOLDEN_DIR / "phase6_schema_manifest_v1.json",
                   GOLDEN_DIR / "phase6_catalog_manifest_v1.json"):
        assert golden.name.startswith("phase6_")


# =========================================================================== #
# Step 2 — the reviewed upstream digests are the frozen ones (recorded evidence) #
# =========================================================================== #
def test_step2_records_the_exact_registry_chain_digests():
    assert default_registry().registry_digest == PHASE1_AMENDED_REGISTRY_DIGEST
    assert phase2_runtime_registry(
        PHASE2_BASE_REGISTRY_DIGEST
    ).registry_digest == PHASE2_RUNTIME_REGISTRY_DIGEST
    assert build_phase3_registry(
        phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST).registry_digest
    ).registry_digest == PHASE3_DATA_REGISTRY_DIGEST_FROZEN
    assert build_phase3_full_registry(
        PHASE3_FULL_BASE_REGISTRY_DIGEST
    ).registry_digest == PHASE3_FULL_REGISTRY_DIGEST_FROZEN
    assert trial.build_phase4_registry(
        trial.PHASE4_BASE_REGISTRY_DIGEST
    ).registry_digest == PHASE4_REGISTRY_DIGEST_FROZEN
    assert bootstrap.build_phase5_registry(
        bootstrap.PHASE5_BASE_REGISTRY_DIGEST
    ).registry_digest == PHASE5_REGISTRY_DIGEST_FROZEN


def test_step2_records_the_exact_catalog_chain_digests():
    assert P.phase2_static_catalog_snapshot().catalog_digest == PHASE2_STATIC_CATALOG_DIGEST_FROZEN
    assert data_catalog.phase3_data_catalog_snapshot().catalog_digest == (
        PHASE3_DATA_CATALOG_DIGEST_FROZEN
    )
    assert mem_catalog.phase3_full_catalog_snapshot().catalog_digest == (
        PHASE3_FULL_CATALOG_DIGEST_FROZEN
    )
    assert trial.phase4_catalog_snapshot().catalog_digest == PHASE4_CATALOG_DIGEST_FROZEN
    assert bootstrap.phase5_catalog_snapshot().catalog_digest == PHASE5_CATALOG_DIGEST_FROZEN
