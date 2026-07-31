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
   ``LimitRulePolicy`` naming ``cn_a_share`` + the committed material ref);
4. calendar honesty (digest-verified committed material; 春节 2026-02-17 absent;
   a normal Tuesday present; tamper raises);
5. cross-process determinism (subprocess digest triple == in-process) + the NEW
   additive golden ``production_data_registry_manifest_v1.json`` (read-only) +
   the existing ``data_source_manifest_v1.json`` byte-identical.

Mutations (Task 1 Step 4, each red -> byte-identical revert): m1 two-entry
route -> group 1 red; m2 rebuilt spec with a different freshness ref -> group 2
red; m3 dropped limit policy -> group 3 red; m4 one-byte calendar-material
edit -> group 4 red.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.data.runtime as RT
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters.data_world import (
    PRODUCTION_CALENDAR_MATERIAL_PATH,
    PRODUCTION_DATA_REGISTRY_VERSION,
    PRODUCTION_ROUTING_AUDIT_ID,
    ProductionDataWorldRecipe,
    _load_calendar_material,
    production_data_recipe,
)
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.errors import SnapshotMismatchError
from guanlan_v2.orchestration.data.registry import SealedRegistryError
from guanlan_v2.orchestration.data.source import RouteEntry
from guanlan_v2.orchestration.data.symbols import (
    InstrumentMeta,
    Symbol,
    resolve_limit_rule,
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
    def test_verified_snapshot_resolves_under_an_online_context(self, recipe):
        """``resolve_method`` succeeds for ``verified_snapshot`` under an
        ONLINE context at a frozen as_of (gate D-B: the elapsed surface policy
        requires the registered ``LimitRulePolicy`` carrying the ``cn_a_share``
        calendar identity + the committed material ref). Mutation m3 (limit
        policy dropped) reddens here with D-B's exact ValueError."""
        surf = phase3_data_surface()
        resolved = recipe.policy_resolver.resolve_method(
            surf.spec_by_method["verified_snapshot"],
            ctx=P.pilot_data_context(as_of=AS_OF))
        assert resolved.freshness_policy.policy_id == \
            "policy.freshness.default-elapsed"
        assert resolved.limit_policy is not None
        assert resolved.calendar_id == "cn_a_share"
        assert resolved.calendar_material_ref == recipe.calendar.material_ref

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
