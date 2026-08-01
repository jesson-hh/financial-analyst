# -*- coding: utf-8 -*-
"""L2-b Task 1 — the production data-world recipe (the world's frozen half).

Plan: docs/superpowers/plans/2026-07-31-orchestration-L2b-data-runtime.md Task 1,
corrected by the Task-0 gate report (.superpowers/sdd/task-L2b-0-handoff-gate-report.md):
D-B (``LimitRulePolicy`` lives in ``data/symbols.py``, registered via
``DataSourceRegistry.register_limit``) and D-C (frozen clock semantics — nothing in
this file ever touches ``SystemClock``; every as_of is a frozen literal).

The controlling constraint (gate items 2-3): the deep lane's session verifies
(a) ``registry.default_route(m).entries == row.frozen_route`` per sealed row and
(b) ``world.ctx == snapshot.data_context`` by FULL equality, where the snapshot
is committed by Lane 0 in a DIFFERENT process. Every recipe component must
therefore be byte-deterministic across processes — derived only from module
constants, ``phase3_data_surface()`` and the committed calendar material bytes.
That determinism is itself pinned here (subprocess digest-triple test).

Five test groups:
1. registry seals + route equality (all seven method ids, single surface entry,
   cross-resolved through the REAL ``_DataRuntimeBridgeSession._frozen_route_for``);
2. method-spec identity (the surface specs registered UNCHANGED — spec digests
   equal; the test-suite idiom of rebuilding specs is exactly what production
   must NOT do);
3. policy resolution (elapsed surface policy + the ONE registered
   ``LimitRulePolicy`` naming ``cn_a_share`` + the committed material ref;
   plus the RULING ADDENDUM's ADDITIONAL method-scoped SESSION policy for
   ``verified_snapshot`` — ``max_trading_sessions=1`` over the committed
   calendar, the six other methods unchanged on the elapsed default);
4. calendar honesty (digest-verified committed material; 春节 2026-02-17 absent;
   a normal Tuesday present; tamper raises);
5. cross-process determinism (subprocess digest triple == in-process) + the NEW
   additive golden ``production_data_registry_manifest_v1.json`` (read-only) +
   the existing ``data_source_manifest_v1.json`` byte-identical.

Mutations (Task 1 Step 4, each red -> byte-identical revert): m1 two-entry
route -> group 1 red; m2 rebuilt spec with a different freshness ref -> group 2
red; m3 dropped limit policy -> group 3 red; m4 one-byte calendar-material
edit -> group 4 red.

--- Task 2 (appended) — the reviewed source adapter under the sealed identity
+ the ONE production backend (plan Task 2, gate D-A). Five groups (a)-(e):
(a) ``production_data_adapters()`` is exactly the sealed source id ->
facade-default ``LiveClientSource`` carrying the recipe's COMMITTED calendar
(the RULING ADDENDUM's one bound dependency); (b) the echo under the PRODUCTION
identity: a fetch through an injected fake probe, staged with the RECIPE's
registry-frozen route on the real backend, returns a ``RawFetch`` whose
``source_ref`` IS ``phase3_data_surface().source_ref``; (c) unsupported method
refuses loudly through production staging + the supported-set guard (the
chartered closure of Task-1 review Minor #4: every method id granted a sealed
prefetch ROW must be within ``LiveClientSource``'s supported set, so an L3
grant of an unsupported method fails THIS test first, never live); (d) the
interleave pin: two threads stage+invoke on the SHARED backend with different
sources and never cross (mutation: thread-local storage removed -> base-class
single-slot behavior -> red); (e) staging misuse still loud on the calling
thread, and a foreign thread can never consume another thread's staged target.
All fetch-path tests drive the REAL ``LiveClientSource`` class with injected
fake probe functions — never a stub of the class itself; no real network.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.data.runtime as RT
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import live_data
from guanlan_v2.orchestration.adapters.data_world import (
    PRODUCTION_CALENDAR_MATERIAL_PATH,
    PRODUCTION_DATA_REGISTRY_VERSION,
    PRODUCTION_ROUTING_AUDIT_ID,
    PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID,
    ProductionDataWorldRecipe,
    ThreadConfinedDataBackend,
    _load_calendar_material,
    production_data_adapters,
    production_data_backend,
    production_data_recipe,
)
from guanlan_v2.orchestration.adapters.live_data import LiveClientSource
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import (
    RoutingConfigurationError,
    SnapshotMismatchError,
)
from guanlan_v2.orchestration.data.registry import SealedRegistryError
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    ResolvedMethodRoute,
    RouteEntry,
    build_data_request,
)
from guanlan_v2.orchestration.data.symbols import (
    InstrumentMeta,
    Symbol,
    resolve_limit_rule,
)
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    ExecutionEvidenceOrdinalToken,
    phase2_runtime_registry,
)

UTC = timezone.utc
#: 2026-07-16 07:00Z == 15:00 Asia/Shanghai (A-share close) — the frozen as_of
#: (the l2b gate's constant; never a SystemClock reading — gate D-C).
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

#: the seven sealed method ids of the Phase-3 surface (sorted, closed set).
_SEVEN = (
    "fundamentals", "indicators", "instrument_names", "news",
    "ohlcv", "signals", "verified_snapshot",
)

#: byte pin of the PRE-EXISTING Task-6-fixture golden — this plan adds a NEW
#: additive golden and must leave this one byte-identical (Global Constraints).
_EXISTING_SOURCE_MANIFEST_SHA256 = (
    "86af7be7e3d6da02602ded2cb8d4b8f1049c2c30bfacf061b2b55fce76ed7503"
)


@pytest.fixture(scope="module")
def recipe() -> ProductionDataWorldRecipe:
    return production_data_recipe()


# =========================================================================== #
# 1. registry seals + route equality                                           #
# =========================================================================== #
class TestRegistrySealAndRouteEquality:
    def test_frozen_constants(self):
        assert PRODUCTION_DATA_REGISTRY_VERSION == "prod-data-v1"
        assert PRODUCTION_ROUTING_AUDIT_ID == "prod-data-routing-v1"

    def test_registry_is_sealed_and_immutable(self, recipe):
        assert recipe.registry.sealed is True
        surf = phase3_data_surface()
        with pytest.raises(SealedRegistryError):
            recipe.registry.register_method(surf.method_specs[0])
        assert (recipe.registry.snapshot().registry_version
                == PRODUCTION_DATA_REGISTRY_VERSION)

    def test_every_sealed_prefetch_row_route_equality(self, recipe):
        """For every sealed prefetch row the production registry's default
        route entries EQUAL the row's frozen route — the recon-flagged check
        (mutation m1's RED target)."""
        surf = phase3_data_surface()
        assert len(surf.prefetch_binding.operations) >= 1
        for row in surf.prefetch_binding.operations:
            route = recipe.registry.default_route(row.method_ref.id)
            assert tuple(route.entries) == tuple(row.frozen_route)

    def test_all_seven_methods_have_the_single_surface_route(self, recipe):
        """Generalized: a default route exists for ALL seven method ids, each
        the single entry ``(surface.source_ref, spec.capability_ref)`` — so a
        future L3 grant of ``indicators``/``news`` freezes the same route
        without re-opening this module."""
        surf = phase3_data_surface()
        assert tuple(sorted(surf.spec_by_method)) == _SEVEN
        for method_id in _SEVEN:
            spec = surf.spec_by_method[method_id]
            route = recipe.registry.default_route(method_id)
            assert tuple(route.entries) == (
                RouteEntry(source_ref=surf.source_ref,
                           capability_ref=spec.capability_ref),)

    def test_the_real_session_cross_resolves_the_production_registry(self, recipe):
        """The gate's route-equality obligation, closed by THIS registry: the
        REAL ``_DataRuntimeBridgeSession._frozen_route_for`` cross-resolves the
        sealed row over the production registry (spec identity + capability +
        entries all equal — no fake, no rebuilt spec)."""
        surf = phase3_data_surface()
        row = surf.prefetch_binding.operations[0]
        session = RT._DataRuntimeBridgeSession(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64),
            world=SimpleNamespace(source_registry=recipe.registry, request=None),
            request=None)
        spec, route = session._frozen_route_for(row)
        assert spec.method_id == row.method_ref.id
        assert tuple(route.entries) == tuple(row.frozen_route)


# =========================================================================== #
# 2. method-spec identity — the surface specs registered UNCHANGED              #
# =========================================================================== #
class TestMethodSpecIdentity:
    def test_specs_are_the_surface_specs_unchanged(self, recipe):
        """The registry registers ``phase3_data_surface().method_specs``
        UNCHANGED — spec digests equal per method (mutation m2's RED target).
        The test-suite idiom of re-building specs with a session freshness ref
        is exactly what production must NOT do."""
        surf = phase3_data_surface()
        snap = recipe.registry.snapshot()
        assert snap.method_specs == surf.method_specs
        by_id = {s.method_id: s for s in snap.method_specs}
        for method_id in _SEVEN:
            assert by_id[method_id].spec_digest == \
                surf.spec_by_method[method_id].spec_digest
            assert by_id[method_id].freshness_policy_ref == surf.freshness_ref

    def test_source_descriptor_is_the_surface_descriptor(self, recipe):
        surf = phase3_data_surface()
        snap = recipe.registry.snapshot()
        assert snap.source_descriptors == (surf.source_descriptor,)
        assert snap.source_descriptors[0].descriptor_digest == \
            surf.source_ref.content_digest


# =========================================================================== #
# 3. policy resolution — elapsed policy + the ONE registered limit policy       #
# =========================================================================== #
class TestPolicyResolution:
    def test_verified_snapshot_resolves_the_method_scoped_session_policy(self, recipe):
        """RULING ADDENDUM Part 2: ``verified_snapshot`` resolves the ADDITIONAL
        method-scoped SESSION policy (``max_trading_sessions=1``) bound to the
        committed calendar identity — not the default elapsed policy. The
        method spec itself is UNCHANGED (its ``freshness_policy_ref`` still
        names the surface default; the spec digest is sealed material), so the
        scoping is done by the resolver: a registered policy whose
        ``method_or_category`` equals the method id wins over the spec's
        default ref (``data/source.py::DataPolicyResolver._resolve_freshness``).
        """
        surf = phase3_data_surface()
        spec = surf.spec_by_method["verified_snapshot"]
        # the SPEC is untouched — it still points at the surface default ref.
        assert spec.freshness_policy_ref == surf.freshness_ref
        resolved = recipe.policy_resolver.resolve_method(
            spec, ctx=P.pilot_data_context(as_of=AS_OF))
        assert resolved.freshness_policy.policy_id == \
            PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID
        assert resolved.freshness_policy.is_session_based is True
        assert resolved.freshness_policy.max_trading_sessions == 1
        assert resolved.freshness_policy.method_or_category == "verified_snapshot"
        # bound to the COMMITTED calendar identity, byte-for-byte.
        assert resolved.freshness_policy.calendar_id == "cn_a_share"
        assert resolved.freshness_policy.calendar_material_ref == \
            recipe.calendar.material_ref
        assert resolved.calendar_id == "cn_a_share"
        assert resolved.calendar_material_ref == recipe.calendar.material_ref

    @pytest.mark.parametrize(
        "method_id", [m for m in _SEVEN if m != "verified_snapshot"])
    def test_every_other_method_stays_on_the_default_elapsed_policy(
            self, recipe, method_id):
        """The METHOD-SCOPING pin: the session policy is scoped to
        ``verified_snapshot`` ONLY — the other six keep the default elapsed
        policy (and, per gate D-B, its bound limit policy). Mutation m2
        (register the session policy under a different method scope) reddens
        here and at the verified_snapshot arm above."""
        surf = phase3_data_surface()
        resolved = recipe.policy_resolver.resolve_method(
            surf.spec_by_method[method_id], ctx=P.pilot_data_context(as_of=AS_OF))
        assert resolved.freshness_policy.policy_id == \
            "policy.freshness.default-elapsed"
        assert resolved.freshness_policy.is_session_based is False
        assert resolved.freshness_policy.max_elapsed_seconds == 86400
        # gate D-B: an elapsed method policy still binds the ONE limit policy.
        assert resolved.limit_policy is not None
        assert resolved.calendar_id == "cn_a_share"
        assert resolved.calendar_material_ref == recipe.calendar.material_ref

    def test_both_freshness_policies_are_registered_in_the_sealed_snapshot(self, recipe):
        """Both policies are registry MATERIAL — the session policy is
        ADDITIONAL, the surface default is untouched. ``freshness_policies`` is
        a sealed snapshot field (``data/source.py``), which is exactly why this
        addendum MOVES ``source_registry_digest`` (a conscious re-freeze)."""
        snap = recipe.registry.snapshot()
        by_id = {p.policy_id: p for p in snap.freshness_policies}
        assert set(by_id) == {
            "policy.freshness.default-elapsed",
            PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID,
        }
        assert by_id["policy.freshness.default-elapsed"] == \
            phase3_data_surface().freshness_policy
        session = by_id[PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID]
        assert session.max_trading_sessions == 1
        assert session.max_elapsed_seconds is None
        assert session.calendar_material_ref == recipe.calendar.material_ref

    def test_the_weekly_monday_hole_is_what_the_session_policy_closes(self, recipe):
        """Why the addendum exists, stated as arithmetic over the COMMITTED
        material: Friday 2026-07-17's close (15:00 +08) read at Monday
        2026-07-20's session midnight is 57h old — permanently STALE under the
        86400s default — but exactly ONE trading session old by the calendar."""
        cal = recipe.calendar
        friday, monday = date(2026, 7, 17), date(2026, 7, 20)
        assert cal.is_session(friday) and cal.is_session(monday)
        settled_at = datetime(2026, 7, 17, 7, 0, tzinfo=UTC)   # 15:00 +08
        as_of = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)       # Mon 00:00 +08
        assert (as_of - settled_at).total_seconds() > 86400     # the elapsed hole
        span = cal.sessions_between(friday, monday)
        sessions_since = span - (1 if cal.is_session(friday) else 0)
        assert sessions_since == 1                              # inside the policy

    def test_limit_policy_binds_the_committed_calendar_identity(self, recipe):
        snap = recipe.registry.snapshot()
        assert len(snap.limit_policies) == 1
        lp = snap.limit_policies[0]
        assert lp.calendar_id == "cn_a_share"
        assert lp.calendar_material_ref == recipe.calendar.material_ref
        # the reviewed A-share board table (the limit_rule_policy_v1 shape)
        entry = lp.entry_for(AS_OF)
        assert entry is not None
        assert entry.board_pct == {
            "main": 0.1, "star": 0.2, "chinext": 0.2, "bj": 0.3}
        assert entry.st_pct == 0.05

    def test_limit_policy_validity_starts_at_the_registration_reform(self, recipe):
        """Review minor #2: ``effective_from`` must not overstate the table —
        the WHOLE row (chinext 0.20 + the main-board 5-session window) is only
        true at once from 2023-02-17 全面注册制. A pre-reform as_of is
        therefore honestly OUTSIDE every policy window: this ONLINE-at-2026
        table must never be trusted by a PIT replay over 2020-2023."""
        lp = recipe.registry.snapshot().limit_policies[0]
        entry = lp.entry_for(AS_OF)
        assert entry is not None
        assert entry.effective_from == datetime(2023, 2, 17, tzinfo=UTC)
        # a 2022 as_of falls in no window -> resolve_limit_rule refuses honestly
        assert lp.entry_for(datetime(2022, 6, 1, tzinfo=UTC)) is None


# =========================================================================== #
# 4. calendar honesty — the committed, digest-verified 2026 session material    #
# =========================================================================== #
class TestCalendarHonesty:
    def test_material_file_is_committed_and_digest_verified(self, recipe):
        """The recipe READS the committed bytes (never re-derives): the file's
        declared content digest equals the re-derived material digest — the
        construction ``ImmutableTradingCalendar`` verifies on load."""
        path = _REPO_ROOT / PRODUCTION_CALENDAR_MATERIAL_PATH
        assert path.is_file(), "the committed calendar material is missing"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["calendar_id"] == "cn_a_share"
        assert doc["content_digest"] == recipe.calendar.material_ref.content_digest
        assert tuple(doc["sessions"]) == recipe.calendar.material.sessions

    def test_coverage_spans_2026(self, recipe):
        cov = recipe.calendar.coverage
        assert cov == (date(2026, 1, 5), date(2026, 12, 31))
        assert recipe.calendar.sessions_between(
            date(2026, 1, 1), date(2026, 12, 31)) == 242

    def test_spot_checks_holiday_absent_trading_day_present(self, recipe):
        """春节 2026-02-17 is NOT a session; a normal Tuesday (2026-07-14) is —
        the reviewed reader's answers, frozen into the committed material."""
        assert recipe.calendar.is_session(date(2026, 2, 17)) is False
        assert recipe.calendar.is_session(date(2026, 1, 1)) is False  # 元旦
        assert recipe.calendar.is_session(date(2026, 7, 14)) is True
        assert recipe.calendar.is_session(date(2026, 7, 16)) is True

    def test_sessions_are_sorted_iso_dates(self, recipe):
        sessions = recipe.calendar.material.sessions
        assert list(sessions) == sorted(sessions)
        assert all(len(s) == 10 for s in sessions)
        assert all(s.startswith("2026-") for s in sessions)

    def test_tampered_material_refuses_loudly(self, tmp_path):
        """One flipped byte in a session date -> ``SnapshotMismatchError`` at
        load (mutation m4's committed-file arm is the same guard on the real
        path; here the tamper is driven on a copy so the committed bytes stay
        untouched)."""
        src = _REPO_ROOT / PRODUCTION_CALENDAR_MATERIAL_PATH
        doc = json.loads(src.read_text(encoding="utf-8"))
        assert doc["sessions"][0] == "2026-01-05"
        # one-byte tamper (05 -> 03): a Saturday NOT in the set, so the tuple
        # stays sorted+unique and the DIGEST guard itself is what fires.
        doc["sessions"][0] = "2026-01-03"
        tampered = tmp_path / "tampered-calendar.json"
        tampered.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(SnapshotMismatchError):
            _load_calendar_material(path=tampered)

    def test_missing_material_file_is_a_typed_refusal(self, tmp_path):
        """Review minor #3: a missing committed material must refuse in the
        module's typed vocabulary (never an untyped FileNotFoundError) — the
        recipe reads committed bytes and never fabricates a calendar."""
        absent = tmp_path / "absent-calendar.json"
        with pytest.raises(RoutingConfigurationError, match="never fabricates"):
            _load_calendar_material(path=absent)

    def test_corrupt_material_file_is_a_typed_refusal(self, tmp_path):
        """Corrupt JSON and a missing required key are both typed refusals
        naming the material path (never an untyped JSONDecodeError/KeyError)."""
        bad = tmp_path / "corrupt-calendar.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(RoutingConfigurationError, match="corrupt-calendar"):
            _load_calendar_material(path=bad)
        keyless = tmp_path / "keyless-calendar.json"
        keyless.write_text(json.dumps({"calendar_id": "cn_a_share"}),
                           encoding="utf-8")
        with pytest.raises(RoutingConfigurationError, match="keyless-calendar"):
            _load_calendar_material(path=keyless)

    def test_a_date_past_coverage_is_uncovered_not_zero(self, recipe):
        """2027 is outside coverage — the honest-refusal contract downstream
        (calendar.py coverage docstring); extending into 2027 is a reviewed
        one-line material bump, never an auto-derivation."""
        cov = recipe.calendar.coverage
        assert cov is not None and cov[1] < date(2027, 1, 1)
        assert recipe.calendar.is_session(date(2027, 1, 4)) is False


# =========================================================================== #
# 5. cross-process determinism + goldens                                       #
# =========================================================================== #
class TestCrossProcessDeterminism:
    def test_digest_triple_is_identical_in_a_fresh_process(self, recipe):
        """THE controlling constraint: a subprocess (fresh interpreter, no
        shared module state) builds the recipe and prints the digest triple —
        byte-equal to the in-process values. Lane 0 (producer) and the deep
        lane (verifier) live in different processes; full context equality
        stands on exactly this."""
        code = (
            "import json\n"
            "from guanlan_v2.orchestration.adapters.data_world import "
            "production_data_recipe\n"
            "r = production_data_recipe()\n"
            "print(json.dumps([r.source_registry_digest, "
            "r.routing_snapshot_digest, r.source_config_digest]))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr
        triple = json.loads(proc.stdout.strip().splitlines()[-1])
        assert triple == [
            recipe.source_registry_digest,
            recipe.routing_snapshot_digest,
            recipe.source_config_digest,
        ]

    def test_recipe_is_cached_module_level(self):
        assert production_data_recipe() is production_data_recipe()

    def test_recipe_cache_is_single_flight_under_threads(self, monkeypatch):
        """Review minor #5: concurrent first calls build the recipe exactly
        ONCE (the module cache is lock-guarded, double-checked) — a benign
        race today, closed cheaply before Task 2 makes the cache load-bearing."""
        import concurrent.futures
        import threading

        import guanlan_v2.orchestration.adapters.data_world as DW

        real_build = DW._build_recipe
        calls: list[int] = []

        def counting_build(**kw):
            calls.append(threading.get_ident())
            return real_build(**kw)

        monkeypatch.setattr(DW, "_build_recipe", counting_build)
        monkeypatch.setattr(DW, "_RECIPE", None)  # restored by monkeypatch
        barrier = threading.Barrier(6)

        def hit():
            barrier.wait()
            return DW.production_data_recipe()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = [f.result() for f in [ex.submit(hit) for _ in range(6)]]
        assert len({id(r) for r in results}) == 1
        assert len(calls) == 1, f"recipe built {len(calls)}x under contention"

    def test_derived_digests_are_the_component_digests(self, recipe):
        assert recipe.source_registry_digest == \
            recipe.registry.snapshot().source_registry_digest
        assert recipe.routing_snapshot_digest == recipe.routing.routing_digest
        assert recipe.source_config_digest == \
            recipe.source_config.source_config_digest
        # the routing snapshot binds the exact config + registry digests
        assert recipe.routing.source_registry_digest == \
            recipe.source_registry_digest
        assert recipe.routing.source_config_digest == recipe.source_config_digest
        assert recipe.routing.audit_id == PRODUCTION_ROUTING_AUDIT_ID


class TestLimitRuleClampedLowerBound:
    """The L2-b Task-1 review's empirical cases as fixtures: the production
    recipe's 5-session window + the 2026-only calendar (coverage from
    2026-01-05) must NEVER report a pre-coverage-listed seasoned stock as
    limitless. Pre-fix, 600519 (listed 2001-08-27) at as_of 2026-01-08 came
    back ``pct=None, "within the initial listing sessions"`` — a 25-year-old
    main-board stock reported limitless, recurring at every annual material
    bump. These values seal into digests Task 7 commits into DataContexts."""

    @staticmethod
    def _rule_600519(recipe, as_of):
        sym = Symbol(code="600519", exchange="SH", board="main")
        listed = datetime(2001, 8, 27, tzinfo=UTC)
        meta = InstrumentMeta(
            symbol=sym, is_st=False, listed_at=listed,
            metadata_available_at=listed)
        lp = recipe.registry.snapshot().limit_policies[0]
        return resolve_limit_rule(
            sym, as_of, meta, policy=lp, calendar=recipe.calendar)

    def test_600519_january_is_an_honest_refusal_never_false_no_limit(self, recipe):
        """The review's reproduced January case: 4 in-coverage sessions
        elapsed (2026-01-05..08) < window 5 and the listing predates coverage
        — the elapsed count cannot be established, so the answer is a typed
        unknown, never the false 'no limit'."""
        rule = self._rule_600519(recipe, datetime(2026, 1, 8, 7, 0, tzinfo=UTC))
        assert rule.pct is None
        assert "listed before calendar coverage" in rule.reason
        assert "within the initial listing sessions" not in rule.reason

    def test_600519_july_gets_the_ordinary_main_board_limit(self, recipe):
        """The lower-bound-certain arm: sessions from coverage start >> 5, so
        the listing window has certainly passed — pct 0.1, before AND after
        the fix."""
        rule = self._rule_600519(recipe, AS_OF)
        assert rule.pct == 0.1
        assert "ordinary limit" in rule.reason

    def test_synthetic_2026_ipo_keeps_its_no_limit_window(self, recipe):
        """True IPO fidelity: listed in-coverage 2026-06-01 (a session), 3
        elapsed sessions <= 5 — the exact-count window semantics unchanged."""
        assert recipe.calendar.is_session(date(2026, 6, 1)) is True
        sym = Symbol(code="600519", exchange="SH", board="main")
        listed = datetime(2026, 6, 1, tzinfo=UTC)
        meta = InstrumentMeta(
            symbol=sym, is_st=False, listed_at=listed,
            metadata_available_at=listed)
        lp = recipe.registry.snapshot().limit_policies[0]
        rule = resolve_limit_rule(
            sym, datetime(2026, 6, 3, 7, 0, tzinfo=UTC), meta,
            policy=lp, calendar=recipe.calendar)
        assert rule.pct is None
        assert "within the initial listing sessions" in rule.reason

    def test_as_of_outside_calendar_coverage_refuses(self, recipe):
        """2027 is past coverage: an uncovered date is UNCOVERED, not 'zero
        sessions' — honest refusal, never a counted-across answer."""
        rule = self._rule_600519(recipe, datetime(2027, 1, 4, 7, 0, tzinfo=UTC))
        assert rule.pct is None
        assert "coverage" in rule.reason.lower()


class TestGoldens:
    def test_new_production_registry_manifest_golden(self, recipe):
        """The NEW additive golden, hand-frozen from the first verified build
        and READ-ONLY here (never regenerated by a test)."""
        golden_path = _GOLDEN_DIR / "production_data_registry_manifest_v1.json"
        assert golden_path.is_file(), "the new additive golden is missing"
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        assert recipe.registry.manifest() == golden

    def test_existing_source_manifest_golden_stays_byte_identical(self):
        """The pre-existing Task-6-fixture golden must remain byte-identical —
        this plan adds, never regenerates (Global Constraints)."""
        data = (_GOLDEN_DIR / "data_source_manifest_v1.json").read_bytes()
        assert hashlib.sha256(data).hexdigest() == \
            _EXISTING_SOURCE_MANIFEST_SHA256


# =========================================================================== #
# Task 2 — the reviewed source adapter under the sealed identity + the ONE     #
# production backend (groups (a)-(e); gate D-A)                                #
# =========================================================================== #
@pytest.fixture(scope="module")
def p3_registry():
    """The REAL sealed Phase-3 schema registry (the l2b-gate fixture idiom)."""
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


class _ProbeStub:
    """A fake ``live_client.probe`` returning a controlled envelope (the
    l2b-gate driver idiom) — injected into the REAL ``LiveClientSource`` via
    its existing ``probe_fn`` port; no real vendor, no network."""

    def __init__(self, env) -> None:
        self.env = env
        self.calls: list = []

    def __call__(self, source, code="", date="", limit=20, timeout=90):
        self.calls.append({"source": source, "code": code, "date": date,
                           "limit": limit})
        return self.env


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(
        attempt=1, call_ordinal=1, evidence_ordinal=evidence)


def _ok_envelope(price: float) -> dict:
    return {"ok": True, "status": "ok",
            "items": [{"name": "贵州茅台", "price": price}],
            "n": 1, "error": "", "note": "",
            "pulled_at": "2026-07-16T15:00:00"}


def _scope(route, *, registry_digest: str, node_id: str = "pm") -> DataInvocationScope:
    return DataInvocationScope(
        plan_digest="a" * 64, node_id=node_id, worker_id="dec.pm",
        operation_token=_token(1), attempt_tokens=(_token(2),),
        frozen_route=route, invocation_mode="cache_or_invoke",
        catalog_digest="b" * 64, schema_registry_digest=registry_digest)


def _sealed_request(p3_registry, *, request_id: str):
    surf = phase3_data_surface()
    spec = surf.spec_by_method["verified_snapshot"]
    ctx = P.pilot_data_context(as_of=AS_OF)
    return spec, build_data_request(
        ctx, method_spec=spec,
        params={"symbols": ({"code": "600519", "exchange": "SH",
                             "board": "main"},),
                "as_of": AS_OF.isoformat()},
        registry=p3_registry, request_id=request_id)


class TestProductionDataAdaptersGroupA:
    def test_map_has_exactly_the_sealed_source_id(self):
        """(a) exactly one entry, ``LiveClientSource`` — the key IS the sealed
        surface identity's id (the registration key the backend resolves
        ``source_ref.id`` against — never new bytes)."""
        surf = phase3_data_surface()
        adapters = production_data_adapters()
        assert set(adapters) == {surf.source_ref.id} == {"guanlan.datafeed"}
        assert isinstance(adapters["guanlan.datafeed"], LiveClientSource)

    def test_adapter_is_the_facade_default_construction(self):
        """The production value is the facade-default construction (every
        injectable port unset — the real ``live_client`` facade is bound
        lazily at call time; tests inject fakes, production never does)."""
        src = production_data_adapters()["guanlan.datafeed"]
        assert src._probe_fn is None
        assert src._catalog_fn is None
        assert src._resolve_fn is None
        assert src._known_fn is None

    def test_adapter_binds_the_committed_recipe_calendar(self):
        """RULING ADDENDUM Part 1, bound AT SOURCE: the production adapter
        carries the recipe's COMMITTED calendar — the only thing that can
        attribute a quote row's settled close to a session. It is the SAME
        object the world binds to its ``PitGuard`` (``world_for`` passes
        ``recipe.calendar``), so a session-counted freshness policy and the
        settled stamp are evaluated against one identical material."""
        src = production_data_adapters()["guanlan.datafeed"]
        recipe = production_data_recipe()
        assert src._calendar is recipe.calendar
        assert src._calendar.material_ref == recipe.calendar.material_ref


class TestBackendEchoGroupB:
    def test_fetch_through_the_backend_echoes_the_sealed_identity(
            self, recipe, p3_registry):
        """(b) the echo, now under the PRODUCTION identity: the REAL
        ``LiveClientSource`` (injected fake probe), staged on the real
        thread-confined backend with the RECIPE's registry-frozen route,
        returns a ``RawFetch`` whose ``source_ref`` IS
        ``phase3_data_surface().source_ref`` — and the inherited verification
        chain (source echo + request digest) passes it."""
        surf = phase3_data_surface()
        spec, req = _sealed_request(p3_registry, request_id="req-l2b-t2-echo")
        route = recipe.registry.default_route("verified_snapshot")
        scope = _scope(route, registry_digest=p3_registry.registry_digest)
        stub = _ProbeStub(_ok_envelope(1000.0))
        backend = ThreadConfinedDataBackend(
            {surf.source_ref.id: LiveClientSource(
                probe_fn=stub, resolve_source_fn=lambda s: s)})
        backend.stage(surf.source_ref, scope)
        raw = backend.invoke(capability_ref=spec.capability_ref, request=req)
        assert raw.source_ref == surf.source_ref
        assert raw.source_ref == scope.frozen_route.entries[0].source_ref
        assert raw.capability_ref == spec.capability_ref
        assert raw.request_digest == req.request_digest
        assert [c["source"] for c in stub.calls] == ["tencent_realtime_quote"]
        assert stub.calls[0]["code"] == "SH600519"

    def test_inherited_source_echo_verification_fires_through_the_subclass(
            self, recipe, p3_registry):
        """WRONG-INPUT arm of the inherited chain, driven through the REAL
        adapter: stage a source_ref that differs from the scope's frozen-route
        entry — ``LiveClientSource`` echoes the route entry, and the INHERITED
        ``invoke`` verification refuses the mismatched echo."""
        surf = phase3_data_surface()
        spec, req = _sealed_request(p3_registry, request_id="req-l2b-t2-mism")
        foreign = ContentRef(id="guanlan.datafeed", version="1",
                             content_digest="f" * 64)
        route = recipe.registry.default_route("verified_snapshot")
        scope = _scope(route, registry_digest=p3_registry.registry_digest)
        backend = ThreadConfinedDataBackend(
            {"guanlan.datafeed": LiveClientSource(
                probe_fn=_ProbeStub(_ok_envelope(1.0)),
                resolve_source_fn=lambda s: s)})
        backend.stage(foreign, scope)
        with pytest.raises(RT.SourceBrokenError,
                           match="claiming a different source"):
            backend.invoke(capability_ref=spec.capability_ref, request=req)


class TestSupportedSetGroupC:
    def test_unsupported_method_refuses_through_production_staging(self, recipe):
        """(c) ``indicators`` staged with ITS recipe-frozen route on the real
        backend still refuses loudly in the REAL adapter, before any probe."""
        surf = phase3_data_surface()
        spec = surf.spec_by_method["indicators"]
        route = recipe.registry.default_route("indicators")
        scope = _scope(route, registry_digest="c" * 64)
        backend = ThreadConfinedDataBackend(
            {surf.source_ref.id: LiveClientSource(
                probe_fn=lambda *a, **k: pytest.fail(
                    "probe must never be reached"))})
        backend.stage(surf.source_ref, scope)
        request = SimpleNamespace(method_spec_ref=spec.method_ref)
        with pytest.raises(RoutingConfigurationError,
                           match="is not bound to this source"):
            backend.invoke(capability_ref=spec.capability_ref, request=request)

    def test_every_granted_prefetch_row_is_within_the_supported_set(self):
        """The chartered closure of Task-1 review Minor #4: the recipe's
        ``method_selections`` claims ``guanlan.datafeed`` for all seven method
        ids while ``LiveClientSource`` serves only three — the honest guard is
        HERE: every method id granted a ROW in the sealed prefetch binding
        must be within the adapter's supported set, so a future L3 grant of an
        unsupported method (e.g. ``indicators``) fails THIS test first, and
        forces the adapter to grow BEFORE the grant goes live."""
        surf = phase3_data_surface()
        granted = {row.method_ref.id
                   for row in surf.prefetch_binding.operations}
        assert granted  # at least dec.pm's healed verified_snapshot row
        assert granted <= live_data._SUPPORTED_METHOD_IDS
        # today's exact shape, so growth is a conscious edit here too:
        assert granted == {"verified_snapshot"}
        assert live_data._SUPPORTED_METHOD_IDS == frozenset(
            {"verified_snapshot", "news", "ohlcv"})


class TestInterleavePinGroupD:
    def test_two_threads_staging_on_the_shared_backend_never_cross(
            self, p3_registry):
        """(d) the interleave pin: BOTH threads stage on the ONE shared
        backend before EITHER invokes (barrier-forced), each against its own
        source; both complete and each ``RawFetch`` matches its own thread's
        staged source. The mutation for this guard is exactly the plan's:
        remove the thread-local storage (base-class single-slot behavior) —
        the second ``stage`` then dies "never consumed" / the invokes cross —
        RED here."""
        surf = phase3_data_surface()
        spec, req = _sealed_request(p3_registry, request_id="req-l2b-t2-il")
        ref_a = surf.source_ref
        ref_b = ContentRef(id="l2b.t2.other-source", version="1",
                           content_digest="e" * 64)
        policy_ref = ContentRef(id="policy.route.l2b-t2", version="1",
                                content_digest="d" * 64)

        def route_for(ref):
            return ResolvedMethodRoute(
                method_ref=spec.method_ref,
                entries=(RouteEntry(source_ref=ref,
                                    capability_ref=spec.capability_ref),),
                route_policy_ref=policy_ref)

        stub_a, stub_b = _ProbeStub(_ok_envelope(111.0)), _ProbeStub(_ok_envelope(222.0))
        backend = ThreadConfinedDataBackend({
            ref_a.id: LiveClientSource(probe_fn=stub_a,
                                       resolve_source_fn=lambda s: s),
            ref_b.id: LiveClientSource(probe_fn=stub_b,
                                       resolve_source_fn=lambda s: s),
        })
        barrier = threading.Barrier(2)
        results: dict = {}
        errors: dict = {}

        def work(name, ref):
            try:
                backend.stage(
                    ref, _scope(route_for(ref),
                                registry_digest=p3_registry.registry_digest,
                                node_id=name))
                barrier.wait(timeout=10)  # both staged before either invokes
                results[name] = backend.invoke(
                    capability_ref=spec.capability_ref, request=req)
            except BaseException as exc:  # noqa: BLE001 - recorded + re-raised below
                errors[name] = exc
                barrier.abort()  # never leave the sibling hanging

        threads = [threading.Thread(target=work, args=("a", ref_a)),
                   threading.Thread(target=work, args=("b", ref_b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)
        assert errors == {}
        assert results["a"].source_ref == ref_a
        assert results["b"].source_ref == ref_b
        # each thread's fetch carries its OWN probe's row — never the sibling's
        assert results["a"].candidates[0].raw_payload["price"] == 111.0
        assert results["b"].candidates[0].raw_payload["price"] == 222.0
        assert len(stub_a.calls) == 1 and len(stub_b.calls) == 1


class TestStagingMisuseGroupE:
    def test_invoke_without_stage_raises_on_the_calling_thread(self):
        """(e) staging misuse still loud: invoke without a staged target is
        the inherited typed ``DataRuntimeError`` — on the calling thread."""
        backend = ThreadConfinedDataBackend(production_data_adapters())
        with pytest.raises(RT.DataRuntimeError,
                           match="without a staged frozen-route source"):
            backend.invoke(capability_ref=object(), request=object())

    def test_a_foreign_thread_cannot_consume_anothers_staged_target(
            self, recipe, p3_registry):
        """Thread confinement's sharp edge: a thread that never staged gets
        the loud misuse error EVEN WHILE another thread holds a staged target
        — and the staging thread's own target survives and still serves."""
        surf = phase3_data_surface()
        spec, req = _sealed_request(p3_registry, request_id="req-l2b-t2-fgn")
        route = recipe.registry.default_route("verified_snapshot")
        scope = _scope(route, registry_digest=p3_registry.registry_digest)
        backend = ThreadConfinedDataBackend(
            {surf.source_ref.id: LiveClientSource(
                probe_fn=_ProbeStub(_ok_envelope(9.0)),
                resolve_source_fn=lambda s: s)})
        backend.stage(surf.source_ref, scope)  # the MAIN thread's target
        box: dict = {}

        def foreign():
            try:
                backend.invoke(capability_ref=spec.capability_ref, request=req)
            except RT.DataRuntimeError as exc:
                box["exc"] = exc

        t = threading.Thread(target=foreign)
        t.start()
        t.join(timeout=10)
        assert "without a staged frozen-route source" in str(box["exc"])
        # the main thread's staged target is untouched and still serves:
        raw = backend.invoke(capability_ref=spec.capability_ref, request=req)
        assert raw.source_ref == surf.source_ref


class TestBackendInheritanceAndSingleton:
    def test_verification_chain_is_inherited_never_copied(self):
        """Source-text pin: only the staged-slot STORAGE is overridden; the
        ``stage``/``clear``/``invoke`` methods — including invoke's whole
        verification chain — are the base class's own, never copied."""
        sub = ThreadConfinedDataBackend
        base = RT.DataSourceCapabilityBackend
        assert issubclass(sub, base)
        assert "invoke" not in sub.__dict__
        assert "stage" not in sub.__dict__
        assert "clear" not in sub.__dict__
        assert sub.invoke is base.invoke
        assert sub.stage is base.stage
        assert sub.clear is base.clear
        assert isinstance(sub.__dict__["_staged"], property)

    def test_production_data_backend_is_a_cached_singleton(self):
        """The ONE process-stable backend (the ``_RECIPE_LOCK`` idiom)."""
        b = production_data_backend()
        assert b is production_data_backend()
        assert isinstance(b, ThreadConfinedDataBackend)
        assert set(b._adapters) == {"guanlan.datafeed"}
        assert isinstance(b._adapters["guanlan.datafeed"], LiveClientSource)

    def test_the_d_a_factory_shape_hands_out_the_one_instance(self):
        """Gate D-A: the gateway invokes the registered factory PER capability
        invocation with ``capability_ref=`` — sharing is the factory's job.
        Task 4 registers exactly this lambda; the shape is pinned here."""
        factory = lambda **kw: production_data_backend()  # noqa: E731
        one = production_data_backend()
        assert factory(capability_ref=object()) is one
        assert factory(capability_ref=object()) is one

    def test_backend_cache_is_single_flight_under_threads(self, monkeypatch):
        """Concurrent first calls build exactly ONE backend (the recipe
        cache's contract, mirrored)."""
        import guanlan_v2.orchestration.adapters.data_world as DW
        monkeypatch.setattr(DW, "_BACKEND", None)
        calls: list = []
        real_adapters = DW.production_data_adapters

        def counting_adapters():
            calls.append(1)
            return real_adapters()

        monkeypatch.setattr(DW, "production_data_adapters", counting_adapters)
        built: list = []
        start = threading.Barrier(8)

        def hit():
            start.wait(timeout=10)
            built.append(DW.production_data_backend())

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(built) == 8
        assert len(calls) == 1
        assert all(b is built[0] for b in built)
