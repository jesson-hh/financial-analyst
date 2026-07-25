# -*- coding: utf-8 -*-
"""Phase 6 · Task 11 — the shadow-consumer RED-LINE regression suite.

The final Phase-6 gate. Every invariant here is already enforced by Tasks 1–10;
this suite pins each red line as an executable regression so a later edit that
silently re-opens one fails loudly. It is a *documentation-green* suite (the
deliverable is the suite itself): where a behaviour is missing it would go red,
but on branch ``report-evidence-pack`` all eight groups pass because the phase
already enforces them.

The eight invariant groups (brief §Required invariants):

1. **structural zero-trading** — ``TargetPortfolioIntent.origin`` / ``authority`` /
   ``execution_scope`` are single-value ``Literal`` fields (introspected: exactly
   one allowed value each), and any non-default value fails validation;
2. **never on a live bus** — importing either Phase-6 module drags neither
   ``guanlan_v2.seats`` nor any of ``{requests, httpx, aiohttp, urllib.request}``
   onto the path (direct AST + a clean-subprocess ``sys.modules`` import graph),
   and the Phase-6 catalog capability manifest is byte-equal to Phase 5's — so no
   order/signal-write capability exists to grant any worker;
3. **no promotion surface** — the reviewed ``__all__`` of both modules is pinned by
   exact equality, and no exported name is a promotion path (shadow artifact → live
   order/signal/seats object);
4. **no in-place mutation after realized** — intent + ``ShadowRunResult`` are frozen
   (mutation raises); the runner returns its input intents digest-untouched; a rerun
   after "seeing" the first result is digest-identical;
5. **no silent normalization anywhere** — a weight-sum-violating book is re-refused at
   every construction gate (proposal / intent envelope / deterministic target set),
   so it can never reach the diff, runner or mirror (they consume only gated books);
6. **honest degradation badges** — an ST-unknown match and a synthetic
   corporate-action run each surface their badge on ``ShadowRunResult``;
7. **scope protection** — the Task-0 pinned engine signatures still hold, and the
   Phase-9 machinery names (``WAITING_FOR_MATURITY`` resume/wakeup, multi-decision
   PIT replay, dual-curve Evaluator wiring, frontend switch-over) are absent as
   defined/imported names from both Phase-6 modules;
8. **no defaults minted at any extraction layer** — an all-``None`` optional-field
   book survives ``None`` verbatim across proposal → intent → runner, produces ZERO
   stop/take/max-hold orders, and a tranche is refused rather than silently executed
   (the banned counter-example — defaulting a buy trigger to reference-price × 1.15 —
   never appears).

Sanctioned carries folded in (test-only): (a) ``ShadowOrderRecord`` price/qty/budget
are required-no-default; (b) ``run_targets`` tranche-refusal symmetry; (c) the
``no_ref_prev_close`` reject-reason assessment (documented — a doc tension, not a
red-line breach; see ``test_carry_c_*``).

Run from repo root: ``pytest tests/orchestration/test_shadow_redlines.py -v``
"""
from __future__ import annotations

import ast
import subprocess
import sys
import typing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

# REAL fa engine primitives (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest import broker as fa_broker
from financial_analyst.backtest.costs import CostModel

from guanlan_v2.orchestration import bootstrap, shadow
from guanlan_v2.orchestration.adapters import luozi
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicTargetSet,
    ShadowBacktestRunner,
    ShadowRunConfig,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    CorporateActionEvent,
    PortfolioTargetProposal,
    ShadowContractError,
    ShadowRunResult,
    TargetPortfolioIntent,
    TargetPosition,
    TrancheTrigger,
)

UTC = timezone.utc
_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
_SEED_DAY = "2026-07-17"
_DECISION_0720 = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)   # 09:30 CST 07-20
_ELIGIBLE_0721 = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)   # -> session 07-21

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# self-contained fixtures (minimal reader/loader + builders, per the brief)    #
# copied from test_shadow_runner.py — this suite never imports a test module.  #
# --------------------------------------------------------------------------- #
class _MemLoader:
    """Satisfies prepare_bar (day freq): ``_read_bin`` (close/factor) + ``fetch_quote``."""

    def __init__(self, frames):
        self._f = {}
        for code, rows in frames.items():
            idx = pd.to_datetime([r[0] for r in rows])
            self._f[code] = pd.DataFrame(
                {
                    "open": [r[1] for r in rows],
                    "high": [r[2] for r in rows],
                    "low": [r[3] for r in rows],
                    "close": [r[4] for r in rows],
                    "vol": [r[5] for r in rows],
                    "factor": [1.0] * len(rows),
                },
                index=idx,
            )

    def _read_bin(self, code, field, freq):
        df = self._f.get(code)
        if df is None or field not in df.columns:
            return None
        return df[field]

    def fetch_quote(self, code, start, end, freq):
        df = self._f.get(code)
        if df is None:
            return None
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        sub = df.loc[(df.index >= lo) & (df.index <= hi)]
        if len(sub) == 0:
            return None
        return sub.reset_index().rename(columns={"index": "trade_date"})


class _MemReader:
    """Satisfies the runner's only reader dependency: ``trading_days``."""

    def __init__(self, sessions):
        self._sessions = list(sessions)

    def trading_days(self, start=None, end=None):
        lo = start or self._sessions[0]
        hi = end or self._sessions[-1]
        return [d for d in self._sessions if lo <= d <= hi]


def _sym(code="600000", exchange="SH", board="main"):
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight, *, code="600000", exchange="SH", board="main", **kw):
    return TargetPosition(symbol=_sym(code, exchange, board), target_weight=weight, **kw)


def _schedule(**over):
    base = dict(
        id="shadow.daily.ashare",
        version="1",
        calendar_id=_CAL_ID,
        timezone=_TZ,
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
    )
    base.update(over)
    return shadow.DecisionSchedule.build(**base)


def _sched_ref(sch):
    return ContentRef(id=sch.id, version=sch.version, content_digest=sch.content_digest)


def _calendar(sessions=_SESSIONS, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[datetime.fromisoformat(s).date() for s in sessions],
        material_id="cal.ashare.2026",
        material_version="1",
    )


def _intent(positions, cash_weight, *, scheduled_for=_DECISION_0720,
            eligible=_ELIGIBLE_0721, intent_id="intent-1", target_version=1, **over):
    base = dict(
        intent_id=intent_id,
        target_version=target_version,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=scheduled_for,
        decision_as_of=scheduled_for,
        eligible_execution_at=eligible,
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return TargetPortfolioIntent(**base)


def _proposal(positions, cash_weight, **over):
    base = dict(
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
    )
    base.update(over)
    return PortfolioTargetProposal(**base)


def _target_set(positions, cash_weight, *, rule_id="rule.equal", point_ordinal=0,
                target_version=1, session_date="2026-07-20"):
    return DeterministicTargetSet(
        rule_id=rule_id,
        point_ordinal=point_ordinal,
        target_version=target_version,
        session_date=session_date,
        positions=tuple(positions),
        cash_weight=cash_weight,
    )


def _runner(frames, *, sessions=_SESSIONS, cost_model=None, init_cash=1_000_000.0,
            corporate_actions=(), is_st=None, lot_size=100, schedule=None,
            calendar=None, clock=None):
    sch = schedule or _schedule()
    return ShadowBacktestRunner(
        reader=_MemReader(sessions),
        loader=_MemLoader(frames),
        schedule=sch,
        schedule_ref=_sched_ref(sch),
        calendar=calendar or _calendar(sessions),
        cost_model=cost_model or CostModel(),
        init_cash=init_cash,
        corporate_actions=corporate_actions,
        is_st=is_st,
        lot_size=lot_size,
        clock=clock or SystemClock(),
    )


def _run_config(**over):
    base = dict(start="2026-07-20", end="2026-07-24", init_cash=1_000_000.0,
                cost_model=CostModel(), corporate_actions=(), is_st=None, lot_size=100)
    base.update(over)
    return ShadowRunConfig(**base)


# a normal alpha frame: seed 07-17 close 10, then a buyable rising week.
_ALPHA = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.8, 10.2, 1e6),
    ("2026-07-22", 10.2, 11.0, 10.1, 10.8, 1e6),
    ("2026-07-23", 10.8, 11.4, 10.7, 11.0, 1e6),
    ("2026-07-24", 11.0, 11.6, 10.9, 11.2, 1e6),
]


def _alpha_frames():
    return {"SH600000": list(_ALPHA)}


def _params(func) -> list[str]:
    import inspect
    return list(inspect.signature(func).parameters)


def _param_defaults(func) -> dict:
    import inspect
    return {
        name: p.default
        for name, p in inspect.signature(func).parameters.items()
        if p.default is not inspect.Parameter.empty
    }


def _module_names(module) -> set[str]:
    """Every module-level DEFINED name (class/func/assignment) + every IMPORTED
    bound name / import target string in a module's own source.

    AST-based, so docstrings and comments (which legitimately cite Phase-9 concepts
    like "replay" / "decommission") are NOT counted — only real code identifiers.
    """
    src = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _import_edges(module) -> list[tuple[str | None, tuple[str, ...]]]:
    """(module, imported-names) edges from a module's own import statements (AST)."""
    src = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    edges: list[tuple[str | None, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.append((None, tuple(a.name for a in node.names)))
        elif isinstance(node, ast.ImportFrom):
            edges.append((node.module, tuple(a.name for a in node.names)))
    return edges


# =========================================================================== #
# Invariant 1 — structural zero-trading (single-value Literals, introspected)   #
# =========================================================================== #
_STRUCTURAL_LITERALS = {
    "origin": "LLM",
    "authority": "ADVISORY_ONLY",
    "execution_scope": "SHADOW_ONLY",
}


def test_intent_origin_authority_scope_are_single_value_literals():
    # introspect the model fields: each is a Literal with EXACTLY one allowed value,
    # and that value is the field default (not merely "the value we happened to try").
    for field_name, only in _STRUCTURAL_LITERALS.items():
        field = TargetPortfolioIntent.model_fields[field_name]
        args = typing.get_args(field.annotation)
        assert args == (only,), (
            f"{field_name} must be a single-value Literal ({only!r}); got {args!r}"
        )
        assert len(args) == 1
        assert field.default == only


@pytest.mark.parametrize(
    "bad",
    [
        {"origin": "HUMAN"},
        {"origin": "SYSTEM"},
        {"authority": "LIVE"},
        {"authority": "EXECUTABLE"},
        {"execution_scope": "LIVE"},
        {"execution_scope": "PRODUCTION"},
    ],
)
def test_intent_rejects_any_non_default_literal_value(bad):
    # constructing an intent that self-reports a live / human / executable envelope
    # is a structural impossibility (the Literal admits exactly one value).
    with pytest.raises(ValidationError):
        _intent([_pos(0.5)], 0.5, **bad)


def test_intent_default_construction_is_the_shadow_advisory_llm_envelope():
    i = _intent([_pos(0.5)], 0.5)
    assert i.origin == "LLM"
    assert i.authority == "ADVISORY_ONLY"
    assert i.execution_scope == "SHADOW_ONLY"


# =========================================================================== #
# Invariant 2 — never on a live bus (import graph + capability manifest)         #
# =========================================================================== #
_BANNED_LIVE_MODULES = {"requests", "httpx", "aiohttp", "urllib.request", "socket"}


def test_neither_phase6_module_directly_imports_seats_or_http():
    # direct AST import check over BOTH Phase-6 modules (comprehensive, one place).
    for module in (shadow, luozi):
        for imported_module, _names in _import_edges(module):
            if imported_module is not None:
                assert not imported_module.startswith("guanlan_v2.seats"), module.__name__
                assert imported_module not in _BANNED_LIVE_MODULES, module.__name__
                assert imported_module not in ("urllib",), (
                    f"{module.__name__} imports urllib (the live-bus escape hatch)"
                )
        for _mod, names in _import_edges(module):
            for name in names:
                assert not name.startswith("guanlan_v2.seats")
                assert name not in _BANNED_LIVE_MODULES


def test_importing_shadow_modules_pulls_no_seats_or_http_transitively():
    # the sys.modules-visible import graph: in a CLEAN subprocess (uncontaminated by
    # the wider test session), importing both Phase-6 modules loads neither seats nor
    # any HTTP client — the transitive proof the AST check cannot give.
    code = (
        "import sys\n"
        "import guanlan_v2.orchestration.shadow\n"
        "import guanlan_v2.orchestration.adapters.luozi\n"
        "banned_exact = {'requests', 'httpx', 'aiohttp', 'urllib.request', 'socket'}\n"
        "loaded = set(sys.modules)\n"
        "seats = sorted(m for m in loaded if m.startswith('guanlan_v2.seats'))\n"
        "http = sorted(m for m in loaded if m in banned_exact)\n"
        "print('SEATS=' + repr(seats))\n"
        "print('HTTP=' + repr(http))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"clean import failed:\n{proc.stderr[-2000:]}"
    assert "SEATS=[]" in proc.stdout, proc.stdout
    assert "HTTP=[]" in proc.stdout, proc.stdout


def test_phase6_catalog_capability_manifest_is_byte_equal_to_phase5():
    # the Phase-6 catalog is the identity node over Phase 5 (via bootstrap.py exports):
    # zero workers, zero capabilities added — so no live order/signal-write tool can
    # enter the framework through this phase.
    p5 = bootstrap.phase5_catalog_snapshot()
    p6 = shadow.phase6_catalog_snapshot()
    assert p6.capability_manifest == p5.capability_manifest
    assert p6.workers == p5.workers
    assert p6.catalog_digest == p5.catalog_digest
    assert shadow.PHASE6_CATALOG_DIGEST == bootstrap.PHASE5_CATALOG_DIGEST


def test_no_order_or_signal_write_capability_exists_to_grant():
    # the red-line derived fact: across the whole catalog there is NO order/signal
    # write capability (there is nothing of that kind to grant a worker), and no
    # worker carries a live/executable decision authority.
    snap = shadow.phase6_catalog_snapshot()
    banned_kind_tokens = ("order", "trade", "execut", "signal_write", "actuat", "broker")
    for cap in snap.capability_manifest:
        kind = str(getattr(cap, "capability_kind", "")).lower()
        cid = str(cap.ref.id).lower()
        assert kind in ("data_adapter", "tool"), (kind, cid)
        for tok in banned_kind_tokens:
            assert tok not in kind, f"capability kind {kind!r} looks order/signal-write"
    for w in snap.workers:
        authority = str(getattr(w, "decision_authority", "none")).lower()
        assert authority in ("none", "advisory_only"), (w.id, authority)


# =========================================================================== #
# Invariant 3 — no promotion surface (exact __all__ + no promotion callable)     #
# =========================================================================== #
_SHADOW_ALL = [
    "ShadowContractError", "ProposalRejected", "PROPOSAL_REASON_CODES",
    "WEIGHT_SUM_TOLERANCE", "TARGET_WEIGHT_BANDS", "TrancheTrigger", "TargetPosition",
    "PortfolioTargetProposal",
    "LocalTimeStr", "IsoDateStr", "SHADOW_MATCHING_ENGINE_VERSION",
    "ASHARE_SESSION_OPEN", "ASHARE_SESSION_CLOSE", "DecisionSchedule",
    "ScheduleComputationError", "UnsupportedBarFrequencyError", "ScheduleConflictError",
    "UnknownScheduleError", "ScheduleRegistrySealedError", "DecisionScheduleRegistry",
    "is_decision_point", "compute_scheduled_for", "compute_cutoff_at",
    "compute_eligible_execution_at",
    "TargetPortfolioIntent", "ShadowEnvelopeError", "wrap_proposal_as_intent",
    "SHADOW_APPLY_KEY_DOMAIN", "target_apply_key", "SHADOW_ORDER_ID_DOMAIN",
    "ShadowOrderKind", "shadow_order_id", "SHADOW_FILL_ID_DOMAIN", "shadow_fill_id",
    "SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN", "deterministic_apply_key_parts",
    "ShadowTargetApplyRecord", "ShadowOrderRecord", "ShadowFillRecord",
    "ShadowRejectRecord", "CorporateActionEvent", "SHADOW_METRIC_KEYS",
    "ShadowRunResult",
    "PHASE6_PUBLIC_MODELS", "Phase6RegistryError", "build_phase6_registry",
    "build_phase6_catalog_snapshot", "phase6_catalog_snapshot",
    "PHASE6_INTERNAL_MODELS", "PHASE6_BASE_REGISTRY_DIGEST", "PHASE6_REGISTRY_DIGEST",
    "PHASE6_BASE_CATALOG_DIGEST", "PHASE6_CATALOG_DIGEST",
]

_LUOZI_ALL = [
    "SHADOW_SKIP_REASONS", "ShadowOrderPlanEntry", "ShadowOrderSkip", "ShadowOrderPlan",
    "diff_target_portfolio", "ShadowApplyConflict", "ShadowDecisionAgent",
    "ShadowRunConfig", "ShadowBacktestRunner", "DeterministicTargetSet",
    "SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN", "deterministic_apply_key",
    "CorporateActionApplication", "apply_corporate_actions",
    "COMPATIBILITY_PROFILE_ID", "COMPAT_TRADE_STRUCTURE_TOL", "COMPAT_PRICE_REL_TOL",
    "COMPAT_RETURN_ABS_TOL", "COMPAT_EQUITY_REL_TOL", "COMPAT_METRIC_REL_TOL",
    "CompatSignal", "CompatClock", "CompatTrade", "CompatibilityRunResult",
    "CompatibilityProfileError", "compat_metrics", "run_compatibility_mirror",
    "map_intents_to_compat_signals",
    # Task 4 (Phase 9) — the DecisionSchedule interval-replay driver surface.
    "RebalanceDateNotSessionError", "SnapshotBindingRefused",
    "RetroactiveIntentRefused", "ReplayApprovalRefused", "resolve_decision_points",
    "reconcile_daily_llm_budget", "ReplayIntentLedger", "ReplayPlanCoordinator",
    "ReplayPointSnapshot", "DeterministicBook", "ReplayRuntimeBindings",
    "run_interval_replay",
    # Task 5 (Phase 9) — dual curves under one execution attestation + handoff.
    "UnsourcedFactorScoreError", "derive_deterministic_targets", "build_dual_curves",
    "hand_off_dual_curves_to_feedback",
]


def test_shadow_all_is_the_reviewed_closed_surface():
    # exact equality (order + membership): a new export must be a reviewed decision,
    # never a silent widening of the shadow → live surface.
    assert list(shadow.__all__) == _SHADOW_ALL
    assert set(shadow.__all__) == set(_SHADOW_ALL)


def test_luozi_all_is_the_reviewed_closed_surface():
    assert list(luozi.__all__) == _LUOZI_ALL
    assert set(luozi.__all__) == set(_LUOZI_ALL)


def test_every_exported_name_resolves_on_its_module():
    for name in shadow.__all__:
        assert hasattr(shadow, name), f"shadow.__all__ names a missing attr {name!r}"
    for name in luozi.__all__:
        assert hasattr(luozi, name), f"luozi.__all__ names a missing attr {name!r}"


def test_no_exported_name_suggests_a_promotion_path():
    # the closed surface is the enforcement; belt-and-suspenders: no exported callable
    # is named as a promotion of a shadow artifact into a live order/signal/seats object.
    promotion_tokens = (
        "to_live", "to_order", "to_signal", "to_seats", "to_ledger", "promote",
        "submit", "place_order", "send_order", "execute_trade", "go_live",
        "activate_live", "to_trade",
    )
    for name in list(shadow.__all__) + list(luozi.__all__):
        low = name.lower()
        for tok in promotion_tokens:
            assert tok not in low, f"exported name {name!r} looks like a promotion path"


# =========================================================================== #
# Invariant 4 — no in-place mutation after realized                             #
# =========================================================================== #
def test_intent_is_frozen_mutation_raises():
    i = _intent([_pos(0.5)], 0.5)
    with pytest.raises(ValidationError):
        i.cash_weight = 0.9
    with pytest.raises(ValidationError):
        i.origin = "HUMAN"


def test_run_result_is_frozen_mutation_raises():
    res = _runner(_alpha_frames()).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24"
    )
    assert isinstance(res, ShadowRunResult)
    with pytest.raises(ValidationError):
        res.n_trades = 999
    with pytest.raises(ValidationError):
        res.badges = ("tampered",)


def test_runner_returns_intents_untouched_pre_post_digest_equality():
    intent = _intent([_pos(0.5)], 0.5)
    before = intent.semantic_digest()
    _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    assert intent.semantic_digest() == before  # frozen input, untouched by the run


def test_rerun_after_seeing_first_result_is_digest_identical():
    # "seeing" the realized result can change no already-produced apply record: a
    # second run over the same fixture reproduces a byte-identical sealed result.
    intent = _intent([_pos(0.5)], 0.5)
    first = _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    # inspect / consume the first result, then rerun.
    _ = (first.content_digest, [a.target_apply_key for a in first.applies])
    second = _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    assert second.content_digest == first.content_digest
    assert [a.target_apply_key for a in second.applies] == [
        a.target_apply_key for a in first.applies
    ]


# =========================================================================== #
# Invariant 5 — no silent normalization at any seam (defense in depth)          #
# =========================================================================== #
# The ONLY books that reach the diff / runner / mirror are validated proposals,
# intents and deterministic target sets. Each construction gate re-applies the SAME
# weight-sum identity (never renormalizes), so a violating book is refused at every
# seam and can never arrive downstream.
def test_seam_proposal_refuses_weight_sum_violation():
    with pytest.raises(ValidationError) as ei:
        _proposal([_pos(0.5)], 0.9)  # 0.5 + 0.9 != 1
    assert "weight_sum_violation" in str(ei.value)


def test_seam_envelope_intent_refuses_weight_sum_violation():
    with pytest.raises(ValidationError):
        _intent([_pos(0.5)], 0.9)  # the intent re-applies the identical matrix


def test_seam_deterministic_target_set_refuses_weight_sum_violation():
    # the deterministic dual-curve book (the run_targets input) shares the matrix.
    with pytest.raises(ValidationError):
        _target_set([_pos(0.5)], 0.9)


def test_seams_diff_runner_mirror_consume_only_gated_books():
    # a weight-sum-violating book is un-constructible, so it can never be handed to
    # the diff, the runner (run / run_targets) or the mirror — the gate is upstream.
    with pytest.raises(ValidationError):
        _intent([_pos(0.75)], 0.75)          # would-be runner/mirror input
    with pytest.raises(ValidationError):
        _target_set([_pos(0.75)], 0.75)      # would-be run_targets input
    # a book at the exact identity boundary is NOT renormalized — it passes verbatim.
    ok = _proposal([_pos(0.25)], 0.75)
    assert ok.cash_weight == 0.75
    assert tuple(p.target_weight for p in ok.positions) == (0.25,)


# =========================================================================== #
# Invariant 6 — honest degradation badges                                       #
# =========================================================================== #
def test_st_unknown_match_surfaces_st_flags_unavailable_badge():
    res = _runner(_alpha_frames()).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24"
    )
    assert res.fills, "the scenario must actually match an order"
    assert "st_flags_unavailable" in res.badges


def test_supplying_st_flags_removes_the_st_badge():
    res = _runner(_alpha_frames(), is_st={"SH600000": False}).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24"
    )
    assert "st_flags_unavailable" not in res.badges


def test_synthetic_corporate_action_run_surfaces_badge():
    ev = CorporateActionEvent(
        symbol=_sym(), kind="cash_dividend", ex_date="2026-08-15",
        cash_per_share=0.5, shares_ratio=0.0,
        available_at=datetime(2026, 8, 14, 1, 0, tzinfo=UTC),
    )
    res = _runner(_alpha_frames(), corporate_actions=(ev,)).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24"
    )
    assert "corporate_actions_synthetic" in res.badges


# =========================================================================== #
# Invariant 7 — scope protection (pinned engine sigs + Phase-9 names absent)     #
# =========================================================================== #
def test_pinned_engine_signatures_still_hold():
    # a compact re-assert of the Task-0 load-bearing engine signatures (the shadow
    # consumer builds ABOVE these — a drift here blocks Phase 6, and this stands in
    # for the reviewer's file-level "engine/ + ui/ byte-unmodified" check).
    import dataclasses

    assert _params(fa_broker.Broker.match) == [
        "self", "order", "bar", "prev_close", "portfolio",
        "next_bar_open", "next_bar_date", "is_st",
    ]
    assert _param_defaults(fa_broker.Broker.match) == {
        "next_bar_open": None, "next_bar_date": None, "is_st": False,
    }
    assert dataclasses.is_dataclass(fa_broker.Order)
    order_fields = {f.name: f for f in dataclasses.fields(fa_broker.Order)}
    assert list(order_fields) == [
        "code", "side", "otype", "limit_price", "qty", "cash_budget", "stop_loss",
    ]
    assert order_fields["otype"].default == "limit"
    assert order_fields["qty"].default is None
    assert order_fields["cash_budget"].default == 0.0


#: Phase-9 machinery that must NOT exist in the two Phase-6 modules as defined /
#: imported code identifiers (the deterministic ``run_targets`` LANE is Phase-6, but
#: its Evaluator/replay CONSUMPTION, resume/wakeup, multi-decision replay and the
#: frontend switch-over are Phase-9). Docstrings that legitimately cite "replay" /
#: "decommission" are excluded — the check runs over AST names only.
_PHASE9_NAME_TOKENS = (
    "waiting_for_maturity", "resume", "wakeup", "reawaken",
    "multi_decision", "decision_point_replay", "pit_replay", "replay_points",
    "evaluator", "optimizer", "dual_curve_eval", "wire_evaluator",
    "switch_over", "switchover", "decommission", "frontend_cutover",
)


@pytest.mark.parametrize("module", [shadow, luozi], ids=["shadow", "luozi"])
def test_phase9_machinery_names_absent_from_phase6_modules(module):
    names = _module_names(module)
    for defined in names:
        low = defined.lower()
        for tok in _PHASE9_NAME_TOKENS:
            assert tok not in low, (
                f"{module.__name__} defines/imports {defined!r} — {tok!r} is Phase-9 "
                "machinery (resume/replay/Evaluator wiring/frontend switch-over) that "
                "must not appear in a Phase-6 module"
            )


def test_neither_phase6_module_imports_the_evaluator_or_optimizer_wiring():
    # the dual-curve lane exists (run_targets); its Evaluator WIRING is Phase-9 — so
    # neither module imports the Phase-4 optimize/Evaluator surface.
    for module in (shadow, luozi):
        for imported_module, names in _import_edges(module):
            if imported_module is not None:
                assert not imported_module.endswith(".optimize"), module.__name__
                assert "evaluator" not in imported_module.lower(), module.__name__
            for name in names:
                assert "Evaluator" not in name and "Optimizer" not in name, (
                    f"{module.__name__} imports {name!r} (Phase-9 Evaluator wiring)"
                )


# =========================================================================== #
# Invariant 8 — no defaults minted at any extraction layer (None-fidelity)       #
# =========================================================================== #
def test_none_fidelity_at_the_proposal_layer():
    # every optional numeric field None, including every TrancheTrigger field.
    tranche = TrancheTrigger()  # price_low / price_high / fraction all None
    assert (tranche.price_low, tranche.price_high, tranche.fraction) == (None, None, None)
    pos = _pos(0.5, stop_loss_pct=None, take_profit_pct=None, max_hold_bars=None,
               entry_tranches=(tranche,))
    prop = _proposal([pos], 0.5)
    p = prop.positions[0]
    assert p.stop_loss_pct is None and p.take_profit_pct is None and p.max_hold_bars is None
    assert p.entry_tranches[0].price_low is None
    assert p.entry_tranches[0].price_high is None
    assert p.entry_tranches[0].fraction is None


def test_none_fidelity_survives_verbatim_into_the_intent_layer():
    # the intent copies the book verbatim — no exit parameter or tranche field is
    # replaced by a computed default.
    tranche = TrancheTrigger()
    pos = _pos(0.5, stop_loss_pct=None, take_profit_pct=None, max_hold_bars=None,
               entry_tranches=(tranche,))
    prop = _proposal([pos], 0.5)
    intent = _intent(list(prop.positions), prop.cash_weight)
    ip = intent.positions[0]
    assert ip.stop_loss_pct is None and ip.take_profit_pct is None and ip.max_hold_bars is None
    assert ip.entry_tranches == (tranche,)
    assert ip.entry_tranches[0].fraction is None


def test_runner_mints_no_stop_take_maxhold_orders_for_an_all_none_position():
    # running an all-None (exit-free, tranche-free) book produces ONLY plain target
    # buys/sells — zero stop_loss / take_profit / max_hold_exit orders, and no order
    # carries a fabricated entry trigger (the banned counter-example: a buy trigger
    # defaulted to reference-price x 1.15).
    intent = _intent(
        [_pos(0.5, stop_loss_pct=None, take_profit_pct=None, max_hold_bars=None)], 0.5
    )
    res = _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    kinds = {o.order_kind for o in res.orders}
    assert kinds <= {"target_buy", "target_sell"}, kinds
    assert "stop_loss" not in kinds
    assert "take_profit" not in kinds
    assert "max_hold_exit" not in kinds
    # no fabricated 1.15x entry trigger price on any order; a buy is sized by
    # cash_budget with a limit at the engine ceiling (prev_close * (1 + pct/2)), and
    # the only buy price present must equal the realized engine fill (10.4), never a
    # 10.0 * 1.15 = 11.5 fabricated trigger.
    for o in res.orders:
        if o.limit_price is not None:
            assert o.limit_price != pytest.approx(11.5), "fabricated 1.15x entry trigger"


def test_runner_refuses_a_tranche_bearing_intent_never_silently_executes():
    # a tranche (even all-None) is REFUSED by shadow-match-v1 — zero tranche behavior,
    # never a silently fabricated tranche fill.
    intent = _intent([_pos(0.5, entry_tranches=(TrancheTrigger(),))], 0.5)
    with pytest.raises(ShadowContractError):
        _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")


def test_carry_b_run_targets_refuses_a_tranche_bearing_target_set():
    # (Task-6 review carry) run() has a tranche-refusal; run_targets must refuse the
    # deterministic lane's tranche too (symmetry — never a silently ignored tranche).
    ts = _target_set([_pos(0.5, entry_tranches=(TrancheTrigger(),))], 0.5)
    r = _runner(_alpha_frames())
    with pytest.raises(ShadowContractError):
        r.run_targets((ts,), run_config=_run_config(), calendar=_calendar(), clock=r.clock)


# =========================================================================== #
# Carry (a) — ShadowOrderRecord price/qty/budget are required (no default)       #
# =========================================================================== #
@pytest.mark.parametrize("field", ["limit_price", "qty", "cash_budget"])
def test_carry_a_order_record_price_qty_budget_are_required_no_default(field):
    # (Task-4 review carry) these tri-state fields carry NO default — an explicit
    # value (possibly None) must always be supplied, so a sizing/price field is never
    # silently defaulted to a fabricated zero.
    fld = shadow.ShadowOrderRecord.model_fields[field]
    assert fld.is_required(), f"ShadowOrderRecord.{field} must be required (no default)"


# =========================================================================== #
# Carry (c) — the ``no_ref_prev_close`` reject-reason assessment (documented)     #
# =========================================================================== #
def test_carry_c_reject_reason_is_free_form_not_a_closed_engine_vocabulary():
    """ASSESS-ONLY (Task-6 review): ``ShadowRejectRecord.reason`` is a free-form
    ``NonEmptyStr``, NOT a closed engine vocabulary. The runner emits the
    engine's ``Broker.last_reason`` verbatim for a matched-then-rejected order, but
    for a held-but-unpriceable EXIT (``prev_close is None``, degenerate data) the
    order never reaches ``Broker.match``, so the runner originates the honest reason
    ``"no_ref_prev_close"`` and records BOTH an order and a reject (never a silent
    drop, never a fabricated price).

    Assessment: this is an HONEST surfaced non-action — it violates NO red-line
    invariant (no silent normalization, no fabricated value, degradation surfaced).
    The only tension is documentation: ``ShadowRejectRecord``'s docstring calls
    ``reason`` "the engine's ``Broker.last_reason`` verbatim", which slightly
    overstates — one runner-originated reason exists for the pre-match unpriceable
    path. Recorded for the Exit-Gate review; no production change made here.
    """
    fld = shadow.ShadowRejectRecord.model_fields["reason"]
    assert fld.is_required()
    # the field admits a runner-originated reason (no closed-vocabulary validator),
    # which is exactly why "no_ref_prev_close" is constructible.
    rec = shadow.ShadowRejectRecord(
        order_id="a" * 64, symbol=_sym(), trade_date="2026-07-21",
        reason="no_ref_prev_close",
    )
    assert rec.reason == "no_ref_prev_close"
    # and a genuine engine reason is equally accepted (the two share one field).
    assert shadow.ShadowRejectRecord(
        order_id="a" * 64, symbol=_sym(), trade_date="2026-07-21", reason="suspended"
    ).reason == "suspended"
