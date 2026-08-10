"""L3 Task 3 — the reviewed lane data grants and their one-to-one prefetch rows.

The semantic change of the L3 plan, pinned against the REAL tree (no fixture
stands in for a sealed artifact anywhere in this module).

**What was ruled** (controller, 2026-08-11, ledger
``docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md`` §9):

* **D-1(0) = option (0-a).** The one-to-one predicate gains a SECOND evaluation
  point at the P8 fold; the new rows join the EXISTING sealed Phase-3 prefetch
  material (no new material, no second descriptor for ``bridge.data_runtime`` —
  fact C stands). The predicate at the new point is deliberately ASYMMETRIC:
  rows->grants is strict over the cumulative worker set (the privilege-escalation
  hole is closed in every scope), while grants->rows is enforced ONLY for workers
  whose ``evidence_policy.tool_calls`` is ``REQUIRED`` — an OPTIONAL worker's
  rowless grant remains the C3-ruled licensed-EMPTY reviewed state and is NOT
  swept this phase.
* **D-1 = Option A.** ``pv.technical`` narrows to ``("verified_snapshot",)`` and
  ``text.news`` to ``("news",)``: ``indicators``/``fundamentals`` are BOTH
  backend-less (ledger §5) and pointer-unsatisfiable (§6), so removing them takes
  away a structurally unexercisable paper privilege rather than a real one.
* **D-1 amendment (this task's measurement).** ``text.policy`` is a THIRD
  ``REQUIRED`` worker holding a rowless ``news`` grant — the ruling's original
  tally misclassified it as OPTIONAL. Its method IS backed and IS satisfiable, so
  Option A's argument does not reach it; it was ruled a THIRD ROW rather than a
  narrowing (narrowing would be real deprivation AND would leave a permanently
  unsupportable seat). Hence **three** new rows, four in the material.
* **D-2 = Option A.** Every new row is ``always_invoke`` +
  ``success_requires_finalized_call=True`` (``row_min_finalized == 1``) — the only
  shape that clears ``tool_calls_required_unmet`` for a REQUIRED reader, and the
  only one under which a data outage cannot be reported as node success.

The sealed ``dec.pm`` row is NOT reshaped by any of this: it stays
``cache_or_invoke`` + ``success_requires_finalized_call=False``, and this module
pins that explicitly so a drive-by "consistency" edit reddens here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.data.catalog as DC
import guanlan_v2.orchestration.data.runtime as RT
from guanlan_v2.orchestration.adapters import chain as p9chain
from guanlan_v2.orchestration.data.catalog import (
    CatalogError,
    DataBridgePrefetchBinding,
    DataPrefetchOperation,
    ParamBinding,
    data_capability_refs,
    phase3_data_surface,
)
from guanlan_v2.orchestration.data.source import RouteEntry
from guanlan_v2.orchestration.enums import ToolCallRequirement
from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot
from guanlan_v2.orchestration.pipeline.chain import phase10_catalog_snapshot
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.spec import validate_plan_draft

UTC = timezone.utc

#: the campaign's subject (五粮液), the same identity the L3 gate projects from.
_AS_OF = datetime(2026, 7, 31, 7, 0, tzinfo=UTC)
_CODE = "000858"

#: the THREE reviewed lane grants this task adds: worker id -> method id.
#: ``text.policy`` is the amendment case (see the module docstring).
_NEW_GRANTS: dict[str, str] = {
    "pv.technical": "verified_snapshot",
    "text.news": "news",
    "text.policy": "news",
}

#: the sealed Phase-3 integration grant, whose row shape must NOT move.
_SEALED_GRANT = ("dec.pm", "verified_snapshot")


# --------------------------------------------------------------------------- #
# fixtures — the real sealed artifacts, built once                             #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def p8():
    return phase8_catalog_snapshot()


@pytest.fixture(scope="module")
def subject_params():
    """The ONE reviewed projection recipe, from a REAL committed ``RunSubject@1``."""
    subject = RunSubject(code=_CODE, as_of=_AS_OF)
    return RT.SubjectParams.project(code=subject.code, as_of=subject.as_of)


@pytest.fixture(scope="module")
def recipe():
    return DW.production_data_recipe()


# --------------------------------------------------------------------------- #
# the support-check environments — the sibling suites' OWN sealed fixtures       #
#                                                                               #
# The two drafts are built by the modules that own them, through their pinned    #
# ``env`` fixtures rather than through a private re-derivation here: a support    #
# flip measured against a draft this module invented would prove nothing about    #
# the drafts the pipeline actually ships. ``__wrapped__`` is how a fixture's own  #
# builder is reached from outside pytest's injection.                            #
# --------------------------------------------------------------------------- #
def _env_of(module):
    return module.env.__wrapped__()


@pytest.fixture(scope="module")
def deep_draft():
    from tests.orchestration import test_pipeline_deep_preset as deep

    return deep._materialize(_env_of(deep)).draft


@pytest.fixture(scope="module")
def deep_support(deep_draft):
    from tests.orchestration import test_pipeline_deep_preset as deep

    env = _env_of(deep)
    runtime = deep._full_phase9_runtime(env["snapshot"])
    view = deep._full_phase9_bridge_view(runtime)
    phase1 = validate_plan_draft(
        deep_draft, request=env["request"], context=env["context"],
        catalog=env["snapshot"], schema_registry=env["registry"])
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    return check_runtime_support(
        deep_draft, phase1_report=phase1, context=env["context"],
        context_requirements=None, catalog=runtime, bridge_view=view,
        schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)


@pytest.fixture(scope="module")
def screening_support():
    """The screening lane's FIRST lane, over the same real full bridge view.

    The screening suite's own support test runs against a bridge-free trimmed
    catalog precisely because the grant gap made the real view unsupportable;
    this fixture is the real view, which is the thing L3 changes.
    """
    from tests.orchestration import test_pipeline_deep_preset as deep
    from tests.orchestration import test_pipeline_screening as screen

    env = _env_of(screen)
    lane = screen._build(env).lanes[0]
    runtime = screen._full_phase9_runtime(env["snapshot"])
    view = deep._full_phase9_bridge_view(runtime)
    phase1 = validate_plan_draft(
        lane.draft, request=lane.request, context=env["context"],
        catalog=env["snapshot"], schema_registry=env["registry"])
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    return check_runtime_support(
        lane.draft, phase1_report=phase1, context=env["context"],
        context_requirements=None, catalog=runtime, bridge_view=view,
        schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)


def _rows_by_worker(surface) -> dict[str, list[DataPrefetchOperation]]:
    out: dict[str, list[DataPrefetchOperation]] = {}
    for op in surface.prefetch_binding.operations:
        out.setdefault(op.worker_id, []).append(op)
    return out


def _data_grants(snapshot) -> set[tuple[str, str, str]]:
    """(worker, capability id, capability version) over a catalog's workers."""
    caps = {(c.id, c.version): c for c in data_capability_refs().values()}
    return {
        (w.id, cap.id, cap.version)
        for w in snapshot.workers
        for cap in w.capability_allowlist
        if caps.get((cap.id, cap.version)) == cap
    }


# =========================================================================== #
# 1. the rows exist, in the ruled shape                                         #
# =========================================================================== #
class TestTheThreeRowsExist:
    def test_the_binding_carries_exactly_the_four_reviewed_rows(self, surface):
        got = {(op.worker_id, op.method_ref.id)
               for op in surface.prefetch_binding.operations}
        assert got == {_SEALED_GRANT} | {
            (wid, mid) for wid, mid in _NEW_GRANTS.items()}

    def test_each_new_row_carries_the_method_s_own_capability(self, surface):
        by_worker = _rows_by_worker(surface)
        for wid, mid in _NEW_GRANTS.items():
            (row,) = by_worker[wid]
            spec = surface.spec_by_method[mid]
            assert row.method_ref == spec.method_ref
            assert row.capability_ref == spec.capability_ref

    def test_the_grant_lands_in_the_existing_sealed_material(self, surface):
        """Fact C: ONE bridge, ONE prefetch material, ONE descriptor.

        The rows join the sealed ``bridge.data_runtime.prefetch@1`` guardrail —
        there is no second binding, no second descriptor and no new material id.
        """
        assert surface.config_ref.id == "bridge.data_runtime.prefetch"
        assert surface.descriptor_ref.id == "bridge.data_runtime.descriptor"
        assert surface.bridge_descriptor.config_ref == surface.config_ref
        parsed = DC.parse_prefetch_binding(surface.config_material.raw_utf8)
        assert parsed == surface.prefetch_binding
        ids = [m.ref.id for m in surface.text_materials()]
        assert ids.count("bridge.data_runtime.prefetch") == 1
        assert ids.count("bridge.data_runtime.descriptor") == 1


# =========================================================================== #
# 2. D-2 arithmetic — exact, and the sealed row untouched                        #
# =========================================================================== #
class TestD2RowShape:
    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_new_row_is_always_invoke_and_requires_a_finalized_call(
            self, surface, worker_id):
        (row,) = _rows_by_worker(surface)[worker_id]
        assert row.invocation_mode == "always_invoke"
        assert row.success_requires_finalized_call is True
        assert row.row_min_finalized == 1
        assert row.row_max_invocations == 1 == len(row.frozen_route)

    def test_the_sealed_dec_pm_row_shape_is_untouched(self, surface):
        (row,) = _rows_by_worker(surface)["dec.pm"]
        assert row.invocation_mode == "cache_or_invoke"
        assert row.success_requires_finalized_call is False
        assert row.row_min_finalized == 0

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_granted_worker_declares_required_tool_calls(self, p8, worker_id):
        """The D-2 premise: each row's owner is a REQUIRED reader, so
        ``row_min_finalized == 1`` is what clears ``tool_calls_required_unmet``."""
        worker = next(w for w in p8.workers if w.id == worker_id)
        assert worker.evidence_policy.tool_calls is ToolCallRequirement.REQUIRED


# =========================================================================== #
# 3. param bindings — the closed pointer set, the sealed shape, the round trip  #
# =========================================================================== #
class TestParamBindings:
    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_binding_kinds_equal_the_healed_dec_pm_row(
            self, surface, worker_id):
        sealed = _rows_by_worker(surface)["dec.pm"][0]
        (row,) = _rows_by_worker(surface)[worker_id]
        shape = lambda r: tuple(  # noqa: E731 — a local projection, not a policy
            (b.target_pointer, b.source_kind, b.source_pointer)
            for b in r.param_bindings)
        assert shape(row) == shape(sealed) == (
            ("/as_of", "node_param", "/asof_date"),
            ("/symbols", "node_param", "/code"),
        )

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_every_source_pointer_is_inside_the_closed_subject_set(
            self, surface, worker_id):
        """A pointer outside ``SUBJECT_PARAM_POINTERS`` has NO serving source —
        that is defect H exactly, and it is refused here rather than at runtime."""
        (row,) = _rows_by_worker(surface)[worker_id]
        pointers = {b.source_pointer for b in row.param_bindings}
        assert pointers <= RT.SUBJECT_PARAM_POINTERS
        assert RT.SUBJECT_PARAM_POINTERS == frozenset({"/asof_date", "/code"})

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_row_round_trips_from_a_projected_subject(
            self, surface, subject_params, worker_id):
        """Task-1 item 5's satisfiability idiom, per row: REAL
        ``_assemble_params`` over the REAL projection, then the method's own
        ``params_cls.model_validate``. This is the executable guard against
        re-creating a DECLARED-but-NOT-RUNNABLE row."""
        (row,) = _rows_by_worker(surface)[worker_id]
        node = SimpleNamespace(id=f"n.{worker_id}", params={})
        payload = RT._assemble_params(row, node, subject_params=subject_params)
        params_cls = RT._BINDING_BY_METHOD[row.method_ref.id].params_cls
        params = params_cls.model_validate(payload)
        assert [s.code for s in params.symbols] == [_CODE]
        assert params.as_of == subject_params.as_document()["asof_date"]

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_row_refuses_without_the_projection(self, surface, worker_id):
        """The other half of the same guarantee: unbound, the row refuses
        loudly by cause rather than reading with a guessed subject."""
        (row,) = _rows_by_worker(surface)[worker_id]
        node = SimpleNamespace(id=f"n.{worker_id}", params={})
        with pytest.raises(RT.DataRuntimeError, match="does not resolve"):
            RT._assemble_params(row, node, subject_params=None)


# =========================================================================== #
# 4. routes cross-resolve to the L2-b production adapters, by source id          #
# =========================================================================== #
class TestRoutesCrossResolve:
    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_route_source_is_a_registered_l2b_adapter(
            self, surface, worker_id):
        adapters = DW.production_data_adapters()
        (row,) = _rows_by_worker(surface)[worker_id]
        for entry in row.frozen_route:
            assert entry.source_ref.id in adapters
            assert entry.source_ref == surface.source_ref
            assert entry.capability_ref == row.capability_ref

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_frozen_route_equals_the_production_default_route(
            self, surface, recipe, worker_id):
        (row,) = _rows_by_worker(surface)[worker_id]
        route = recipe.registry.default_route(row.method_ref.id)
        assert tuple(route.entries) == tuple(row.frozen_route)

    @pytest.mark.parametrize("worker_id", sorted(_NEW_GRANTS))
    def test_the_granted_method_is_inside_the_adapter_supported_set(
            self, surface, worker_id):
        """L2-b's first interception: a grant of a method the real adapter
        cannot serve must never reach production. Both ruled methods are inside
        it — that is exactly WHY D-1 chose them."""
        from guanlan_v2.orchestration.adapters import live_data

        (row,) = _rows_by_worker(surface)[worker_id]
        assert row.method_ref.id in live_data._SUPPORTED_METHOD_IDS


# =========================================================================== #
# 5. D-1 Option A — the two narrowings, measured on the real Phase-8 catalog     #
# =========================================================================== #
class TestAllowlistNarrowing:
    def test_pv_technical_holds_only_verified_snapshot(self, p8):
        worker = next(w for w in p8.workers if w.id == "pv.technical")
        assert [c.id for c in worker.capability_allowlist] == [
            "cap.data.verified_snapshot"]

    def test_text_news_holds_only_news(self, p8):
        worker = next(w for w in p8.workers if w.id == "text.news")
        assert [c.id for c in worker.capability_allowlist] == ["cap.data.news"]

    def test_text_policy_keeps_its_news_capability(self, p8):
        """The amendment's whole point: `text.policy` is NOT narrowed — its
        method is backed and satisfiable, so it got a row instead."""
        worker = next(w for w in p8.workers if w.id == "text.policy")
        assert [c.id for c in worker.capability_allowlist] == ["cap.data.news"]

    def test_no_required_worker_is_left_with_a_rowless_data_grant(self, p8):
        """The invariant the amendment closes, stated positively over the REAL
        catalog: after this task there is no REQUIRED reader holding a data
        capability without a reviewed row."""
        rows = {(op.worker_id, op.capability_ref.id, op.capability_ref.version)
                for op in phase3_data_surface().prefetch_binding.operations}
        by_id = {w.id: w for w in p8.workers}
        rowless_required = {
            g for g in _data_grants(p8) - rows
            if by_id[g[0]].evidence_policy.tool_calls
            is ToolCallRequirement.REQUIRED
        }
        assert rowless_required == set()


# =========================================================================== #
# 6. the SECOND evaluation point (D-1(0)) — the asymmetric predicate            #
# =========================================================================== #
class TestSecondEvaluationPoint:
    def test_the_real_phase8_fold_evaluates_the_coverage(self, p8, surface):
        """The seam itself: the shipped Phase-8 catalog passes the cumulative
        check for the sealed binding (and this is the function the fold calls)."""
        DC.verify_cumulative_prefetch_coverage(
            p8, surface.prefetch_binding)

    def test_a_row_without_a_grant_is_refused_in_every_scope(self, p8, surface):
        """rows->grants is STRICT: privilege escalation by adding a row for a
        worker that was never granted the capability is closed at this scope
        too, exactly as it is at Phase 3."""
        spec = surface.spec_by_method["ohlcv"]
        rogue = DataPrefetchOperation(
            worker_id="dec.trader",  # holds no data capability at all
            method_ref=spec.method_ref, capability_ref=spec.capability_ref,
            frozen_route=(RouteEntry(source_ref=surface.source_ref,
                                     capability_ref=spec.capability_ref),),
            invocation_mode="cache_or_invoke")
        binding = DataBridgePrefetchBinding.build(
            bridge_id=DC.DATA_BRIDGE_ID, bridge_version="1",
            operations=tuple(surface.prefetch_binding.operations) + (rogue,))
        with pytest.raises(CatalogError, match="one-to-one") as exc:
            DC.verify_cumulative_prefetch_coverage(p8, binding)
        assert "dec.trader" in str(exc.value)

    def test_a_required_worker_s_rowless_grant_is_refused(self, p8, surface):
        """grants->rows for REQUIRED workers: drop `text.policy`'s row and the
        fold refuses — the structurally dead seat is caught at build time."""
        kept = tuple(op for op in surface.prefetch_binding.operations
                     if op.worker_id != "text.policy")
        binding = DataBridgePrefetchBinding.build(
            bridge_id=DC.DATA_BRIDGE_ID, bridge_version="1", operations=kept)
        with pytest.raises(CatalogError, match="one-to-one") as exc:
            DC.verify_cumulative_prefetch_coverage(p8, binding)
        assert "text.policy" in str(exc.value)

    def test_optional_paper_grants_stay_licensed_empty(self, p8, surface):
        """The ruled ASYMMETRY, pinned as a positive fact rather than as an
        absence: eleven OPTIONAL (worker, capability) grants carry no row, and
        the fold accepts the catalog anyway — C3's licensed-EMPTY reviewed state
        is NOT swept this phase."""
        rows = {(op.worker_id, op.capability_ref.id, op.capability_ref.version)
                for op in surface.prefetch_binding.operations}
        by_id = {w.id: w for w in p8.workers}
        rowless_optional = {
            g for g in _data_grants(p8) - rows
            if by_id[g[0]].evidence_policy.tool_calls
            is ToolCallRequirement.OPTIONAL
        }
        assert len(rowless_optional) == 11
        DC.verify_cumulative_prefetch_coverage(p8, surface.prefetch_binding)

    def test_both_evaluation_points_speak_one_vocabulary(self, p8, surface):
        """One invariant, two evaluation points, ONE refusal sentence — so an
        operator who has learned to read the Phase-3 failure reads this one too.
        Pinned by comparing the two live messages, not by copying a string."""
        import inspect

        kept = tuple(op for op in surface.prefetch_binding.operations
                     if op.worker_id != "text.policy")
        binding = DataBridgePrefetchBinding.build(
            bridge_id=DC.DATA_BRIDGE_ID, bridge_version="1", operations=kept)
        with pytest.raises(CatalogError) as exc:
            DC.verify_cumulative_prefetch_coverage(p8, binding)
        sentence = ("worker data-capability grants and prefetch rows must be "
                    "one-to-one; grants without a row: ")
        assert str(exc.value).startswith(sentence)
        # …and the Phase-3 point still raises that exact sentence, in source.
        assert sentence.split(";")[0] in inspect.getsource(DC.build_phase3_catalog)

    def test_the_binding_checked_is_the_one_the_catalog_carries(
            self, p8, surface):
        """The check is not evaluating a binding of its own imagination: the
        catalog under test must carry the exact prefetch material the binding
        serializes to, and a foreign binding is refused as drift."""
        assert any(e.ref == surface.config_ref for e in p8.content_manifest)
        # a binding that PASSES coverage and is still not the catalog's: an extra
        # row for `pv.microstructure`, which already holds the grant (OPTIONAL,
        # so its rowless state is licensed but its rowed state is covered too).
        spec = surface.spec_by_method["verified_snapshot"]
        extra = DataPrefetchOperation(
            worker_id="pv.microstructure",
            method_ref=spec.method_ref, capability_ref=spec.capability_ref,
            frozen_route=(RouteEntry(source_ref=surface.source_ref,
                                     capability_ref=spec.capability_ref),),
            invocation_mode="cache_or_invoke")
        foreign = DataBridgePrefetchBinding.build(
            bridge_id=DC.DATA_BRIDGE_ID, bridge_version="1",
            operations=tuple(surface.prefetch_binding.operations) + (extra,))
        with pytest.raises(CatalogError, match="does not match the catalog"):
            DC.verify_cumulative_prefetch_coverage(p8, foreign)


# =========================================================================== #
# 6b. the CALL SITE — the wiring is the enforcement, so the wiring is pinned    #
# =========================================================================== #
class TestTheFoldIsWired:
    """The one thing every other test in this module takes for granted.

    Everything above calls :func:`verify_cumulative_prefetch_coverage` directly,
    and every mutation of it hits its internals — so deleting the single call at
    the end of ``lane_catalog.build_phase8_catalog_snapshot`` would leave this
    module, and the whole suite, green while the second evaluation point stopped
    running. That is not a hypothetical: it was measured during this task's
    review.

    **This wiring is load-bearing for ruling D-1(0) item 2.** Phase 3 does not
    thread its skipped rows anywhere; the deferral is real only because the P8
    fold re-reads the same sealed binding and claims them. Unwire the call and
    Phase 3's "skip" silently becomes "accept" — the privilege hole the invariant
    exists to close, reopened with no test noticing. Hence a pin on the CALL, not
    on the callee.
    """

    def test_the_name_the_fold_calls_IS_the_function_under_test(self):
        """The one-line identity that closes the last gap in this class
        (L3 Task 5, a campaign carry).

        Every mutation test here monkeypatches ``lane_catalog``'s NAME, and
        every direct test calls ``data.catalog``'s FUNCTION. If those two ever
        stopped being the same object — a re-export replaced by a local wrapper,
        a shim added for a signature change — the mutations would prove things
        about a function the fold no longer runs, and nothing would say so.
        Identity, not equality: a wrapper delegating perfectly still breaks the
        argument these tests make.
        """
        import guanlan_v2.orchestration.lane_catalog as lc

        assert (lc.verify_cumulative_prefetch_coverage
                is DC.verify_cumulative_prefetch_coverage)

    def test_the_phase8_fold_actually_calls_the_coverage_check(self, monkeypatch):
        import guanlan_v2.orchestration.lane_catalog as lc

        class _Sentinel(Exception):
            pass

        def _boom(snapshot, prefetch_binding):
            raise _Sentinel("the fold reached the coverage check")

        monkeypatch.setattr(lc, "verify_cumulative_prefetch_coverage", _boom)
        with pytest.raises(_Sentinel):
            lc.phase8_catalog_snapshot()

    def test_the_check_receives_the_sealed_catalog_and_binding(self, monkeypatch):
        """…and it is called with the REAL arguments, not with placeholders: the
        snapshot it judges is the Phase-8 one being sealed, and the binding is
        the sealed Phase-3 material."""
        import guanlan_v2.orchestration.lane_catalog as lc

        seen: dict = {}

        def _record(snapshot, prefetch_binding):
            seen["snapshot"] = snapshot
            seen["binding"] = prefetch_binding

        monkeypatch.setattr(lc, "verify_cumulative_prefetch_coverage", _record)
        built = lc.phase8_catalog_snapshot()
        assert seen["snapshot"] is built or (
            seen["snapshot"].catalog_digest == built.catalog_digest)
        assert seen["binding"] is phase3_data_surface().prefetch_binding


# =========================================================================== #
# 7. the coverage assumption behind choosing the P8 fold (controller-ruled pin) #
# =========================================================================== #
class TestP8CoverageAssumption:
    """The evaluation point sits at the P8 fold because P8 is the FIRST fold at
    which the deferred rows can be claimed at all. That choice is only safe
    while no later fold introduces a data-capability holder. This pin makes the
    assumption executable: when a future phase grants data to a worker it adds
    after Phase 8, this reddens and forces that phase to extend the evaluation
    point rather than silently lose coverage.
    """

    def test_no_post_phase8_fold_adds_a_data_capability_holder(self, p8):
        p9 = p9chain.phase9_catalog_snapshot()
        p10 = phase10_catalog_snapshot()
        assert _data_grants(p9) == _data_grants(p8)
        assert _data_grants(p10) == _data_grants(p8)
        p8_ids = {w.id for w in p8.workers}
        late_ids = ({w.id for w in p9.workers} | {w.id for w in p10.workers}
                    ) - p8_ids
        assert late_ids, "the assumption is vacuous if no later fold adds workers"
        assert {g[0] for g in _data_grants(p10)} & late_ids == set()

    def test_the_granted_workers_first_appear_at_the_phase8_fold(self, p8):
        """…and the converse half: the three granted lane workers are added BY
        the Phase-8 fold, which is why no earlier fold could claim their rows."""
        from guanlan_v2.orchestration import phase7_registry as p7

        earlier = {w.id for w in p7.phase7_catalog_snapshot().workers}
        added = {w.id for w in p8.workers} - earlier
        assert set(_NEW_GRANTS) <= added


# =========================================================================== #
# 8. THE SEMANTIC PAYOFF — both drafts are runtime-supported for real           #
# =========================================================================== #
class TestSupportFlips:
    def test_the_ten_node_deep_draft_is_supported(self, deep_support):
        """The whole point of L3: the FULL ten-node deep-decide preset stops
        support-refusing. Before this task it was honestly UNSUPPORTED with
        ``tool_calls_required_unmet`` on ``pv.technical`` and ``text.news``."""
        report = deep_support
        assert report.supported is True, [
            (i.code, i.node_id) for i in report.issues]

    def test_the_screening_draft_is_supported(self, screening_support):
        """The screening lane also schedules ``text.news`` — it flips with the
        same grant, and pinning it here is what proves the flip is a property of
        the grant rather than of one preset."""
        report = screening_support
        assert report.supported is True, [
            (i.code, i.node_id) for i in report.issues]

    def test_no_tool_calls_required_unmet_remains_in_either_draft(
            self, deep_support, screening_support):
        for report in (deep_support, screening_support):
            assert [i for i in report.issues
                    if i.code == "tool_calls_required_unmet"] == []

    def test_both_readers_carry_a_min_one_data_bridge_summary(
            self, deep_support, deep_draft):
        """…and the flip is caused by the ROWS, not by a relaxed checker: the
        two readers' data-bridge summaries now claim a guaranteed finalized
        call each."""
        node_worker = {n.id: n.worker_id for n in deep_draft.nodes}
        seen = {}
        for s in deep_support.bridge_support_summaries:
            if s.bridge_id != "data.runtime":
                continue
            seen[node_worker[s.node_id]] = s
        for wid in ("pv.technical", "text.news"):
            assert seen[wid].min_finalized_tool_calls_on_success == 1
            assert seen[wid].max_capability_invocations == 1
