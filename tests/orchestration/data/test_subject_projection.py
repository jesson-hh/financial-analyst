# -*- coding: utf-8 -*-
"""L1 Task 1 — the closed subject->data param projection (``SubjectParams``).

The frozen L1 plan (docs/superpowers/plans/2026-07-31-orchestration-L1-subject-
projection.md, D-0 option (i)) carries the run's committed ``RunSubject@1`` into
the ``data.runtime`` bridge's param assembly OUT-OF-BAND: ``spec.py``'s
``params_not_allowed`` guard and the sealed preset's code-free rule are both
untouched; the projection serves ``_assemble_params`` as a second, CLOSED source
for ``node_param`` pointers.

Binding investigation (Task 1 Step 1, performed 2026-07-31 BEFORE these tests
were written — outcomes recorded here per the plan):

* The ONE reviewed code->Symbol constructor is
  ``guanlan_v2.orchestration.data.symbols.normalize_symbol``
  (``data/symbols.py:114-165``): three anchored grammars (bare ``600519`` /
  dotted ``600519.SH`` / engine ``SH600519``), exchange+board derived inside the
  reviewed constructor — board rules are NEVER re-derived here (the BJ-920
  lesson: a hand-rolled prefix table is a known defect class). The
  watcher-canonical code format is the BARE six-digit form: it is exactly what
  ``RunSubject.code`` stores after validation (``pipeline/contracts.py:91-96``,
  ``_normalized_code`` == ``normalize_symbol(raw).code``), i.e. grammar 1
  (``_BARE_RE``, ``data/symbols.py:109``).
* The sealed row's params class is ``InstrumentUniverseParams``
  (``data/source.py:148-159``): ``symbols: tuple[Symbol, ...]``,
  ``as_of: IsoAwareDateTime`` (``data/source.py:125-145``). Probed empirically
  2026-07-31 under DigestModel strict mode: ``model_validate`` accepts
  ``symbols`` as a TUPLE of ``Symbol`` instances (or of Symbol-shaped
  mappings); a LIST is rejected (``tuple_type``). So ``code_value`` is the
  ratified singleton instrument set ``(normalize_symbol(code),)`` and
  ``asof_value`` is the aware UTC ISO-8601 string.
* Neither Task-1 STOP trigger fired: the reviewed constructor exists, and
  ``InstrumentUniverseParams.model_validate`` accepts the honest projection of
  one code + one aware datetime.

L1 Task 3 adds the ``WorldlessDataBridgeProvider`` shape tests (R3): the
dead-row provider is retired per its own tombstone; its successor NEVER
completes empty over a resolvable row — three LOUD shapes, exercised over the
REAL reduced support report + the real resolver bundle (the
``test_pm_two_bridges`` fixture idiom), with POISONED gateway/writer objects
proving zero begins / zero writes for shapes 2/3. Shape 2's message marker
``params resolved from the run subject projection`` is a FROZEN CONTRACT
within the L1 plan (Task 6's live verification greps for it).

Run: ``pytest tests/orchestration/data/test_subject_projection.py -v``
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.data import runtime as RT
from guanlan_v2.orchestration.data.catalog import ParamBinding, phase3_data_surface
from guanlan_v2.orchestration.data.source import InstrumentUniverseParams
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.enums import ApprovalPolicy
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_catalog_runtime,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    REDUCED_DEEP_DECIDE_PRESET_ID,
    materialize_deep_decide_draft,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanNode,
    validate_plan_draft,
)

UTC = timezone.utc

#: the run's session as-of used throughout — aware, non-UTC offsets also probed.
_AS_OF = datetime(2026, 7, 31, 7, 0, tzinfo=UTC)

#: shape 2's FROZEN message marker (L1 plan Task 3/Task 6 contract).
FROZEN_MARKER = "params resolved from the run subject projection"


@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def real_row(surface):
    """The single REAL sealed prefetch row the reviewed grant produces."""
    rows = surface.prefetch_binding.operations
    assert [(r.worker_id, r.method_ref.id) for r in rows] == [
        ("dec.pm", "verified_snapshot")]
    return rows[0]


def _pm_node(params: dict | None = None) -> PlanNode:
    """A legally-shaped ``dec.pm`` node (params-less unless a test says otherwise)."""
    return PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot-pm", params=params or {})


def _subject() -> "RT.SubjectParams":
    return RT.SubjectParams.project(code="600519", as_of=_AS_OF)


# --------------------------------------------------------------------------- #
# SubjectParams.project — the ONE reviewed projection recipe                    #
# --------------------------------------------------------------------------- #
class TestSubjectParamsProject:
    def test_happy_path_projects_the_singleton_instrument_set(self):
        sp = _subject()
        # the ratified singleton-universe semantic: /code -> (Symbol,) via the
        # EXISTING reviewed constructor, never a hand-rolled board table.
        assert sp.code_value == (normalize_symbol("600519"),)
        assert sp.asof_value == _AS_OF.astimezone(UTC).isoformat()
        assert datetime.fromisoformat(sp.asof_value) == _AS_OF

    def test_board_rules_come_from_the_reviewed_constructor(self):
        # BJ / chinext / star codes: the projection must equal the constructor's
        # own output bit-for-bit — this test carries NO prefix table of its own.
        for code in ("830799", "300750", "688111"):
            sp = RT.SubjectParams.project(code=code, as_of=_AS_OF)
            assert sp.code_value == (normalize_symbol(code),)

    def test_a_non_utc_aware_instant_projects_deterministically(self):
        from datetime import timedelta, timezone as tz

        cst = tz(timedelta(hours=8))
        sp = RT.SubjectParams.project(
            code="600519", as_of=datetime(2026, 7, 31, 15, 0, tzinfo=cst))
        assert sp.asof_value == datetime(2026, 7, 31, 7, 0, tzinfo=UTC).isoformat()

    def test_document_is_the_closed_two_key_shape(self):
        sp = _subject()
        doc = sp.as_document()
        assert doc == {"asof_date": sp.asof_value, "code": sp.code_value}
        assert sorted(doc) == ["asof_date", "code"]  # exactly two keys, closed

    def test_the_carrier_is_frozen(self):
        sp = _subject()
        with pytest.raises(dataclasses.FrozenInstanceError):
            sp.asof_value = "2020-01-01T00:00:00+00:00"  # type: ignore[misc]

    def test_refuses_an_empty_code(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="", as_of=_AS_OF)

    @pytest.mark.parametrize("bad", ["600519.SH", "SH600519", "白酒", "60051", "6005190"])
    def test_refuses_a_non_canonical_code(self, bad):
        """Only the watcher-canonical BARE six-digit form is accepted.

        A dotted / engine spelling parses under the constructor's wider grammar,
        but the committed ``RunSubject.code`` is always the bare form — accepting
        an alternate spelling here would open a second input grammar for the ONE
        recipe, so it is refused, never repaired.
        """
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code=bad, as_of=_AS_OF)

    def test_refuses_a_naive_as_of(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="600519", as_of=datetime(2026, 7, 31, 7, 0))

    def test_refuses_a_non_datetime_as_of(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="600519", as_of="2026-07-31T07:00:00+00:00")

    def test_the_pointer_set_is_closed(self):
        assert RT.SUBJECT_PARAM_POINTERS == frozenset({"/asof_date", "/code"})


# --------------------------------------------------------------------------- #
# the real-row round trip — the load-bearing test of the whole L1 plan          #
# --------------------------------------------------------------------------- #
class TestRealRowRoundTrip:
    def test_the_real_sealed_row_resolves_and_validates_from_the_subject(
            self, real_row):
        """The REAL sealed row + a legally-shaped params-less ``dec.pm`` node
        resolve through the REAL ``_assemble_params`` under the projection, and
        the assembled document validates as a real ``InstrumentUniverseParams``
        whose ``symbols[0]`` round-trips the subject code. No fixture row, no
        fabricated binding."""
        sp = _subject()
        doc = RT._assemble_params(real_row, _pm_node(), subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}
        params_cls = RT._BINDING_BY_METHOD["verified_snapshot"].params_cls
        assert params_cls is InstrumentUniverseParams
        params = params_cls.model_validate(doc)
        assert [s.dotted for s in params.symbols] == ["600519.SH"]
        assert params.symbols[0].code == "600519"
        assert datetime.fromisoformat(params.as_of) == _AS_OF

    def test_without_the_subject_the_same_real_row_still_refuses(self, real_row):
        """The refusal survives — but its cause is now the RUNNER SEAM, not an
        unbuilt projection (the projection exists as of this task)."""
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(real_row, _pm_node())
        msg = str(exc.value)
        assert "'/asof_date'" in msg
        assert "does not resolve" in msg
        assert "params_not_allowed" in msg
        assert "not bound at the runner seam" in msg
        assert "is built" not in msg  # the pre-L1 'until ... is built' story is gone

    def test_a_node_may_never_contradict_the_run_subject(self, real_row):
        """Conflict guard: node params carrying a DIFFERENT ``/code`` value than
        the subject document -> loud typed refusal, never a silent winner."""
        sp = _subject()
        node = _pm_node({"code": "000001"})
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(real_row, node, subject_params=sp)
        assert "contradict" in str(exc.value)
        assert "'/code'" in str(exc.value)

    def test_a_node_carrying_the_same_value_resolves(self, real_row):
        """Same-value node params are NOT a conflict (both arms).

        ``/asof_date`` arm rides a real PlanNode (an ISO string is JSON-shaped);
        the ``/code`` arm needs a stub node because ``PlanNode.params`` is
        JSON-shaped and cannot carry the projected ``(Symbol,)`` tuple — the
        guard compares exact document values, and ``_assemble_params``'s node
        contract is duck-typed (``.id`` + ``.params``)."""
        sp = _subject()
        # arm 1: real PlanNode supplies /asof_date equal to the subject's value.
        doc = RT._assemble_params(
            real_row, _pm_node({"asof_date": sp.asof_value}), subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}
        # arm 2: stub node supplies BOTH pointers with the exact subject values.
        stub = SimpleNamespace(
            id="pm", params={"asof_date": sp.asof_value, "code": sp.code_value})
        doc = RT._assemble_params(real_row, stub, subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}

    def test_a_pointer_outside_the_closed_set_is_still_refused(self, real_row):
        """Closure: a ``node_param`` pointer outside ``SUBJECT_PARAM_POINTERS``
        is never served by the subject document, even when a subject is bound.
        The refusal names the closure so a widened set (mutation m1) reddens
        this test."""
        binding = ParamBinding(
            target_pointer="/limit", source_kind="node_param",
            source_pointer="/limit")
        row = real_row.model_copy(update={"param_bindings": (binding,)})
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(row, _pm_node(), subject_params=_subject())
        msg = str(exc.value)
        assert "'/limit'" in msg
        assert "closed pointer set" in msg


# --------------------------------------------------------------------------- #
# SubjectParams stays a plain service object — never a registered model         #
# --------------------------------------------------------------------------- #
class TestNothingIsRegistered:
    def test_runtime_registers_no_models(self):
        assert RT.RUNTIME_PUBLIC_MODELS == ()
        assert RT.RUNTIME_INTERNAL_MODELS == {}

    def test_subject_params_is_a_plain_dataclass_not_a_model(self):
        assert dataclasses.is_dataclass(RT.SubjectParams)
        assert not issubclass(RT.SubjectParams, BaseModel)


# =========================================================================== #
# L1 Task 3 — the worldless provider's three LOUD shapes (R3)                   #
# the REAL sealed catalog through the REAL production assembly (the             #
# test_pm_two_bridges fixture idiom); zero network, zero LLM, zero var/ writes  #
# =========================================================================== #
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _PoisonedGateway:
    """Shapes 2/3 promise ZERO gateway begins — ANY attribute access is the
    failure (least privilege, proven not asserted)."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"gateway.{name} touched by the worldless provider")


class _PoisonedWriter:
    """The worldless provider must never persist evidence — ANY attribute
    access is the failure."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"writer.{name} touched by the worldless provider")


@pytest.fixture(scope="module")
def deep_env():
    registry = chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)
    snapshot = chain.phase9_catalog_snapshot()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW)
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id="ctx-l1t3-1",
        content_digest=context.content_digest)
    request = OrchestrationRequest(
        request_id="req-l1t3-1", goal="观澜 · L1 无世界数据桥三形状",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref, "request": request,
    }


@pytest.fixture(scope="module")
def deep_bundle(deep_env):
    return build_production_catalog_runtime(deep_env["snapshot"])


@pytest.fixture(scope="module")
def deep_view(deep_bundle):
    return production_bridge_view(deep_bundle.runtime)


@pytest.fixture(scope="module")
def deep_reduced(deep_env, deep_bundle, deep_view):
    """The materialized reduced draft + its REAL support report (the live shape)."""
    subject = RunSubject(code="833509", as_of=NOW)
    subject_ref = TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id="subject-833509",
            content_digest=subject.semantic_digest()),
        schema_ref=SchemaRef(name="RunSubject", version="1"))
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    materialized = materialize_deep_decide_draft(
        request=deep_env["request"], preset_registry=presets,
        preset_id=REDUCED_DEEP_DECIDE_PRESET_ID,
        context_snapshot_ref=deep_env["ctx_ref"], subject_ref=subject_ref,
        clock=_FixedClock(), context=deep_env["context"],
        catalog=deep_env["snapshot"], schema_registry=deep_env["registry"],
        draft_id="draft-l1t3-1", run_id="run-l1t3-1")
    draft = materialized.draft
    phase1 = validate_plan_draft(
        draft, request=deep_env["request"], context=deep_env["context"],
        catalog=deep_env["snapshot"], schema_registry=deep_env["registry"])
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    report = check_runtime_support(
        draft, phase1_report=phase1, context=deep_env["context"],
        context_requirements=None, catalog=deep_bundle.runtime,
        bridge_view=deep_view, schema_registry=deep_env["registry"],
        profile=STATIC_RUNTIME_PROFILE_V2)
    assert report.supported is True, [(i.code, i.node_id) for i in report.issues]
    return SimpleNamespace(draft=draft, report=report)


def _summary_for(report, node_id: str, bridge_id: str):
    mine = [s for s in report.bridge_support_summaries
            if s.bridge_id == bridge_id and s.node_id == node_id]
    assert len(mine) == 1
    return mine[0]


class TestWorldlessDataBridgeProviderShapes:
    """R3's three shapes over the REAL sealed row — all loud, none empty."""

    @pytest.fixture()
    def parts(self, deep_env, deep_view, deep_reduced):
        worker = {w.id: w for w in deep_env["snapshot"].workers}["dec.pm"]
        rb = deep_view.resolve("data.runtime")
        summary = _summary_for(deep_reduced.report, "pm", "data.runtime")
        node = next(n for n in deep_reduced.draft.nodes if n.id == "pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)
        return SimpleNamespace(
            worker=worker, rb=rb, summary=summary, node=node,
            sequencer=sequencer, token=token,
            env=deep_env, view=deep_view)

    def _provider(self, parts, subject_params=None):
        return RT.worldless_data_provider_factory(subject_params)(
            bridge=parts.rb, summary=parts.summary)

    def _empty_handle(self, parts):
        return W.PreparedBridgeHandle(
            bridge_id="data.runtime", bridge_priority=parts.rb.priority,
            summary_digest=parts.summary.summary_digest, token=parts.token,
            input_contribution=W.BridgeInputContribution())

    def _open_request(self, parts, *, worker=None, bridge=None, handle=None):
        return SimpleNamespace(
            plan=None, node=parts.node,
            worker=worker if worker is not None else parts.worker,
            bridge=bridge if bridge is not None else parts.rb,
            summary=parts.summary,
            handle=handle if handle is not None else self._empty_handle(parts),
            input_snapshot=None, capability_gateway=_PoisonedGateway(),
            evidence_writer=_PoisonedWriter(), reader=None,
            sequencer=parts.sequencer)

    # -- shape 1: rowless allowlisted worker — the UNCHANGED pv-aux text ------ #
    def test_shape1_rowless_worker_keeps_the_unchanged_loud_text(self, parts):
        """The pv aux nodes' refusal is byte-identical to the retired
        provider's (their ``bridge_execution_error`` + degrade behavior did
        not move), bound or unbound alike."""
        expected = (
            "worker 'pv.price_action' activates the data.runtime bridge through "
            "its capability allowlist but the sealed prefetch binding carries "
            "no reviewed row for it, and this lane binds no production "
            "DataRuntimeWorld (the chartered L2-b gap) -- honest refusal, "
            "never a fabricated read")
        pv = {w.id: w for w in parts.env["snapshot"].workers}["pv.price_action"]
        for subject_params in (None, _subject()):
            provider = self._provider(parts, subject_params)
            with pytest.raises(RT.DataRuntimeError) as exc:
                provider.open_execution(self._open_request(parts, worker=pv))
            assert str(exc.value) == expected

    # -- shape 2: rows + subject bound — proven resolvable, then refused ------ #
    def test_shape2_bound_proves_the_row_then_refuses_naming_the_subject(
            self, parts):
        """The NEW refusal: the frozen marker, the subject's code and as-of,
        the row's method id and the L2-b naming — raised only AFTER the real
        assembly+validation proof, with the POISONED gateway/writer proving
        zero begins and zero writes."""
        sp = _subject()
        provider = self._provider(parts, sp)
        with pytest.raises(RT.DataRuntimeError) as exc:
            provider.open_execution(self._open_request(parts))
        msg = str(exc.value)
        assert FROZEN_MARKER in msg
        assert "600519" in msg
        assert sp.asof_value in msg
        assert "verified_snapshot" in msg
        assert "no production DataRuntimeWorld is bound (the chartered L2-b gap)" in msg
        assert "faking a data read" in msg

    def test_shape2_actually_runs_the_real_param_assembly(
            self, parts, real_row, monkeypatch):
        """The resolvability-proof pin (mutation m2's target): shape 2 must
        RUN ``_assemble_params`` over the REAL sealed row with the bound
        subject — a fabricated message that skips the proof reddens this."""
        sp = _subject()
        calls: list = []
        real = RT._assemble_params

        def spy(row, node, **kwargs):
            calls.append((row, kwargs.get("subject_params")))
            return real(row, node, **kwargs)

        monkeypatch.setattr(RT, "_assemble_params", spy)
        provider = self._provider(parts, sp)
        with pytest.raises(RT.DataRuntimeError, match="run subject projection"):
            provider.open_execution(self._open_request(parts))
        assert len(calls) == 1
        called_row, called_sp = calls[0]
        assert called_sp is sp
        # the row the proof ran over IS the sealed row (parsed from the
        # resolved bridge's own config bytes — model equality, not identity).
        assert called_row == real_row

    # -- shape 3: rows + subject unbound — the wiring defect ------------------ #
    def test_shape3_unbound_names_the_runner_seam_never_the_marker(self, parts):
        """Mutation m3's discrimination: the unbound shape names the runner
        seam and must NOT carry shape 2's frozen marker (that would claim a
        resolution that never happened)."""
        provider = self._provider(parts, None)
        with pytest.raises(RT.DataRuntimeError) as exc:
            provider.open_execution(self._open_request(parts))
        msg = str(exc.value)
        assert "subject projection not bound at the runner seam" in msg
        assert "wiring defect" in msg
        assert FROZEN_MARKER not in msg

    # -- the registration recipe threads the subject (half-wired-kwarg pin) --- #
    def test_the_registration_recipe_passes_the_subject_through(
            self, parts, deep_bundle):
        """``register_worldless_data_provider(subject_params=…)`` must hand
        the BOUND factory to the sealed provider ref — an accepted-but-dropped
        kwarg (the campaign's half-wired defect class) reddens the echo."""
        sp = _subject()
        factories = TrustedFactoryRegistry(deep_bundle.runtime)
        RT.register_worldless_data_provider(
            factories=factories, subject_params=sp)
        provider = factories.handler_factory(
            phase3_data_surface().provider_ref)(
            bridge=parts.rb, summary=parts.summary)
        assert isinstance(provider, RT.WorldlessDataBridgeProvider)
        with pytest.raises(RT.DataRuntimeError) as exc:
            provider.open_execution(self._open_request(parts))
        assert FROZEN_MARKER in str(exc.value)
        assert "600519" in str(exc.value)

    def test_the_registration_recipe_defaults_to_the_unbound_process_level(
            self, parts, deep_bundle):
        """Process-level registration stays UNBOUND (subject_params=None →
        shape 3); the per-run bound view is L1 Task 4's seam."""
        factories = TrustedFactoryRegistry(deep_bundle.runtime)
        RT.register_worldless_data_provider(factories=factories)
        provider = factories.handler_factory(
            phase3_data_surface().provider_ref)(
            bridge=parts.rb, summary=parts.summary)
        with pytest.raises(RT.DataRuntimeError,
                           match="not bound at the runner seam"):
            provider.open_execution(self._open_request(parts))


# --------------------------------------------------------------------------- #
# the tombstone is honored — the dead-row names survive nowhere outside docs    #
# --------------------------------------------------------------------------- #
class TestTheTombstoneIsHonored:
    def test_the_dead_row_provider_names_survive_nowhere_outside_docs(self):
        """Repo-wide grep pin (L1 plan Task 3): the retired provider's names
        (class, fact helper, predicate, factory, recipe, session) exist in NO
        ``.py`` file under ``guanlan_v2/`` or ``tests/`` — docs/reports keep
        the history. The needles are concatenation-built so this pin never
        matches itself."""
        root = Path(__file__).resolve().parents[3]
        needles = ("Structurally" + "Dead", "structurally" + "_dead")
        offenders: list[str] = []
        for base in ("guanlan_v2", "tests"):
            for p in sorted((root / base).rglob("*.py")):
                text = p.read_text(encoding="utf-8", errors="ignore")
                for needle in needles:
                    if needle in text:
                        offenders.append(f"{p.relative_to(root)}: {needle}")
        assert offenders == []
