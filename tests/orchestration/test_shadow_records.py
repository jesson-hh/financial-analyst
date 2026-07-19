# -*- coding: utf-8 -*-
"""Phase 6 · Task 4 — the shadow run-record contracts + ``CorporateActionEvent``.

Written test-first (RED until ``ShadowTargetApplyRecord`` / ``ShadowOrderRecord`` /
``ShadowFillRecord`` / ``ShadowRejectRecord`` / ``CorporateActionEvent`` /
``ShadowRunResult`` + ``SHADOW_METRIC_KEYS`` exist in
``guanlan_v2.orchestration.shadow``).

These pin, per the Task-4 brief and its 5 required invariants:

* the six record field surfaces + the closed ``schema_version`` Literal;
* invariant 2 — key self-consistency: the apply / order / fill records RECOMPUTE
  their key via the exact Task-3 builders (``target_apply_key`` /
  ``shadow_order_id`` / ``shadow_fill_id``), so tampering any single key component
  fails construction (forged ids are unconstructible);
* invariant 3 — the closed corporate-action kind/field matrix (every illegal
  ``kind`` × ``cash_per_share`` / ``shares_ratio`` combination fails);
* invariant 4 — the ``ShadowRunResult`` causation closure: a dangling fill→order,
  a dangling apply.order_ids→order, and a dangling order→apply each fail;
* the closed 9-name metrics vocabulary + non-finite omission (a NaN/Inf metric is
  OMITTED by ``build()``, never smuggled — a direct construct with a non-finite
  metric is rejected by ``FiniteFloat``); strict-int ``n_trades`` (a float rejects);
* invariant 1 — a ``ShadowRunResult`` references intents ONLY by
  ``intent_content_digests`` (no embedded / mutable intent);
* invariant 5 — every record is frozen and canonically digestible; the run result
  self-seals through ``SELF_DIGEST_FIELDS`` + ``build()``.

Run from repo root: ``pytest tests/orchestration/test_shadow_records.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.refs import ContentRef

UTC = timezone.utc
_SCHED_UTC = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)   # 09:30 CST 07-20
_AVAIL_UTC = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)

_METRIC_VOCAB = frozenset(
    {
        "ann_return", "sharpe", "max_drawdown", "volatility", "turnover",
        "win_rate", "calmar", "trade_win_rate", "profit_factor",
    }
)


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _apply_key(intent_id: str = "intent-1", scheduled_for=_SCHED_UTC,
               target_version: int = 1) -> str:
    """Recompute the apply key via the exact Task-3 function (duck-typed intent)."""
    return shadow.target_apply_key(
        SimpleNamespace(intent_id=intent_id, scheduled_for=scheduled_for,
                        target_version=target_version)
    )


#: the canonical, self-consistent causation chain used across the run-result tests.
_K = _apply_key()
_O = shadow.shadow_order_id(
    apply_key=_K, symbol=_sym(), order_kind="target_buy", trigger_bar="2026-07-21", ordinal=0
)
_F = shadow.shadow_fill_id(order_id=_O, fill_seq=1)


def _apply(**over):
    base = dict(
        target_apply_key=_K,
        intent_content_digest="a" * 64,
        intent_id="intent-1",
        scheduled_for=_SCHED_UTC,
        target_version=1,
        trigger_bar="2026-07-21",
        order_ids=(_O,),
        applied=True,
    )
    base.update(over)
    return shadow.ShadowTargetApplyRecord(**base)


def _order(**over):
    base = dict(
        order_id=_O,
        target_apply_key=_K,
        symbol=_sym(),
        order_kind="target_buy",
        trigger_bar="2026-07-21",
        ordinal=0,
        side="buy",
        otype="limit",
        limit_price=1680.0,
        qty=100,
        cash_budget=None,
    )
    base.update(over)
    return shadow.ShadowOrderRecord(**base)


def _fill(**over):
    base = dict(
        fill_id=_F,
        order_id=_O,
        fill_seq=1,
        symbol=_sym(),
        side="buy",
        qty=100,
        price=1680.0,
        trade_date="2026-07-21",
        gross=168000.0,
        cost=50.4,
        reason="target_buy",
    )
    base.update(over)
    return shadow.ShadowFillRecord(**base)


def _reject(**over):
    base = dict(order_id=_O, symbol=_sym(), trade_date="2026-07-21", reason="suspended")
    base.update(over)
    return shadow.ShadowRejectRecord(**base)


def _cae(**over):
    base = dict(
        symbol=_sym(),
        kind="cash_dividend",
        ex_date="2026-07-21",
        cash_per_share=1.5,
        shares_ratio=0.0,
        available_at=_AVAIL_UTC,
    )
    base.update(over)
    return shadow.CorporateActionEvent(**base)


def _run_fields(**over):
    base = dict(
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
        schedule_ref=ContentRef(id="shadow.daily.ashare", version="1", content_digest="a" * 64),
        start="2026-07-20",
        end="2026-07-31",
        init_cash=1_000_000.0,
        cost_model_digest="c" * 64,
        intent_content_digests=("a" * 64,),
        applies=(_apply(),),
        orders=(_order(),),
        fills=(_fill(),),
        rejects=(),
        nav_history=(("2026-07-20", 1_000_000.0), ("2026-07-21", 1_001_000.0)),
        metrics={},
        n_trades=1,
        warnings=(),
        badges=(),
    )
    base.update(over)
    return base


def _run_result(**over):
    return shadow.ShadowRunResult.build(**_run_fields(**over))


# --------------------------------------------------------------------------- #
# module surface — exports                                                     #
# --------------------------------------------------------------------------- #
def test_new_record_names_exported_and_defined_in_shadow():
    for name in (
        "ShadowTargetApplyRecord", "ShadowOrderRecord", "ShadowFillRecord",
        "ShadowRejectRecord", "CorporateActionEvent", "ShadowRunResult",
        "SHADOW_METRIC_KEYS",
    ):
        assert name in shadow.__all__, f"{name} missing from shadow.__all__"
    for cls in (
        shadow.ShadowTargetApplyRecord, shadow.ShadowOrderRecord,
        shadow.ShadowFillRecord, shadow.ShadowRejectRecord,
        shadow.CorporateActionEvent, shadow.ShadowRunResult,
    ):
        assert cls.__module__ == "guanlan_v2.orchestration.shadow"


def test_metric_vocabulary_is_the_closed_nine_names():
    assert shadow.SHADOW_METRIC_KEYS == _METRIC_VOCAB
    assert len(shadow.SHADOW_METRIC_KEYS) == 9


# --------------------------------------------------------------------------- #
# field surfaces + schema_version                                              #
# --------------------------------------------------------------------------- #
def test_apply_record_field_surface():
    assert set(shadow.ShadowTargetApplyRecord.model_fields) == {
        "schema_version", "target_apply_key", "intent_content_digest", "intent_id",
        "scheduled_for", "target_version", "trigger_bar", "order_ids", "applied",
        "rule_id", "point_ordinal",
    }
    assert shadow.ShadowTargetApplyRecord.model_fields["schema_version"].default == "1"
    # the dual-family components default to None (an intent-lane record carries neither)
    assert shadow.ShadowTargetApplyRecord.model_fields["rule_id"].default is None
    assert shadow.ShadowTargetApplyRecord.model_fields["point_ordinal"].default is None


def test_order_record_field_surface():
    assert set(shadow.ShadowOrderRecord.model_fields) == {
        "schema_version", "order_id", "target_apply_key", "symbol", "order_kind",
        "trigger_bar", "ordinal", "side", "otype", "limit_price", "qty", "cash_budget",
    }


def test_fill_record_field_surface():
    assert set(shadow.ShadowFillRecord.model_fields) == {
        "schema_version", "fill_id", "order_id", "fill_seq", "symbol", "side",
        "qty", "price", "trade_date", "gross", "cost", "reason",
    }


def test_reject_record_field_surface():
    assert set(shadow.ShadowRejectRecord.model_fields) == {
        "schema_version", "order_id", "symbol", "trade_date", "reason",
    }


def test_corporate_action_field_surface():
    assert set(shadow.CorporateActionEvent.model_fields) == {
        "schema_version", "symbol", "kind", "ex_date", "cash_per_share",
        "shares_ratio", "available_at",
    }


def test_run_result_field_surface():
    assert set(shadow.ShadowRunResult.model_fields) == {
        "schema_version", "matching_engine_version", "schedule_ref", "start", "end",
        "init_cash", "cost_model_digest", "intent_content_digests", "applies",
        "orders", "fills", "rejects", "nav_history", "metrics", "n_trades",
        "warnings", "badges", "content_digest",
    }
    assert shadow.ShadowRunResult.SELF_DIGEST_FIELDS == frozenset({"content_digest"})


# --------------------------------------------------------------------------- #
# happy-path constructibility                                                  #
# --------------------------------------------------------------------------- #
def test_all_records_construct_on_the_happy_path():
    assert isinstance(_apply(), shadow.ShadowTargetApplyRecord)
    assert isinstance(_order(), shadow.ShadowOrderRecord)
    assert isinstance(_fill(), shadow.ShadowFillRecord)
    assert isinstance(_reject(), shadow.ShadowRejectRecord)
    assert isinstance(_cae(), shadow.CorporateActionEvent)
    assert isinstance(_run_result(), shadow.ShadowRunResult)


def test_apply_record_applied_is_required_no_default():
    # note 5: `applied: bool` carries no default — it is a required field.
    assert shadow.ShadowTargetApplyRecord.model_fields["applied"].is_required()
    with pytest.raises(ValidationError):
        shadow.ShadowTargetApplyRecord(
            target_apply_key=_K, intent_content_digest="a" * 64, intent_id="intent-1",
            scheduled_for=_SCHED_UTC, target_version=1, trigger_bar="2026-07-21",
            order_ids=(_O,),  # no `applied`
        )


def test_apply_record_honest_non_application():
    # applied=False is an honest non-application (eligible bar never tradable), not
    # an error — it constructs.
    a = _apply(applied=False, order_ids=())
    assert a.applied is False


def test_apply_record_semantic_exclude_is_exactly_intent_id():
    assert shadow.ShadowTargetApplyRecord.SEMANTIC_EXCLUDE == frozenset({"intent_id"})


def test_order_qty_none_is_cash_budget_sized_buy():
    o = _order(qty=None, cash_budget=250000.0)
    assert o.qty is None and o.cash_budget == 250000.0


# --------------------------------------------------------------------------- #
# invariant 2 — apply-key self-consistency (tamper each component)              #
# --------------------------------------------------------------------------- #
def test_apply_record_accepts_a_self_consistent_key():
    assert _apply().target_apply_key == _K


@pytest.mark.parametrize(
    "tamper",
    [
        {"intent_id": "intent-2"},
        {"scheduled_for": datetime(2026, 7, 20, 1, 31, tzinfo=UTC)},
        {"target_version": 2},
    ],
)
def test_apply_key_tampering_a_component_fails(tamper):
    # the declared key is _K (for intent-1 / _SCHED_UTC / v1); changing a component
    # while keeping _K makes the recomputed key disagree → construction fails.
    with pytest.raises(ValidationError):
        _apply(**tamper)  # target_apply_key stays _K


def test_apply_key_forged_digest_fails():
    with pytest.raises(ValidationError):
        _apply(target_apply_key="0" * 64)


# --------------------------------------------------------------------------- #
# FLAG-1 reconciliation — the dual apply-key family (deterministic lane)        #
# --------------------------------------------------------------------------- #
# The deterministic dual-curve lane's apply record carries its OWN domain-tagged
# key family (rule_id / point_ordinal present ⇒ target_apply_key recomputes via
# ``deterministic_apply_key_parts``, NOT the intent-family builder), so a stored
# deterministic key can never collide with an intent key. An intent-lane record
# leaves both components None and self-validates EXACTLY as before.
_DET_RULE = "rule.equal"
_DET_ORD = 0
_DET_VER = 1


def _det_key(rule_id: str = _DET_RULE, point_ordinal: int = _DET_ORD,
             target_version: int = _DET_VER) -> str:
    return shadow.deterministic_apply_key_parts(
        rule_id=rule_id, point_ordinal=point_ordinal, target_version=target_version
    )


def _det_apply(**over):
    base = dict(
        target_apply_key=_det_key(),
        intent_content_digest="c" * 64,
        intent_id=f"{_DET_RULE}#{_DET_ORD}",   # the pinned audit form
        scheduled_for=_SCHED_UTC,
        target_version=_DET_VER,
        trigger_bar="2026-07-21",
        order_ids=(),
        applied=True,
        rule_id=_DET_RULE,
        point_ordinal=_DET_ORD,
    )
    base.update(over)
    return shadow.ShadowTargetApplyRecord(**base)


def test_deterministic_apply_key_parts_domain_and_disjoint_from_intent_family():
    dk = shadow.deterministic_apply_key_parts(
        rule_id="rule.equal", point_ordinal=0, target_version=1
    )
    assert len(dk) == 64
    assert shadow.SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN == "shadow-deterministic-apply-key-v1"
    # identical logical components, distinct domain tag → keys can never collide
    ik = shadow.target_apply_key(
        SimpleNamespace(intent_id="rule.equal#0", scheduled_for=_SCHED_UTC, target_version=1)
    )
    assert dk != ik


def test_deterministic_apply_record_accepts_native_key():
    a = _det_apply()
    assert a.rule_id == _DET_RULE and a.point_ordinal == _DET_ORD
    assert a.target_apply_key == _det_key()


def test_deterministic_apply_record_forged_key_fails():
    with pytest.raises(ValidationError):
        _det_apply(target_apply_key="0" * 64)


@pytest.mark.parametrize(
    "tamper",
    [
        {"rule_id": "rule.other"},        # key stays for rule.equal → recompute disagrees
        {"point_ordinal": 1},             # 1 != 0 → deterministic recompute disagrees
        {"target_version": 2},
    ],
)
def test_deterministic_apply_key_tampering_a_component_fails(tamper):
    with pytest.raises(ValidationError):
        _det_apply(**tamper)  # target_apply_key stays _det_key() for (rule.equal, 0, 1)


def test_deterministic_apply_record_rule_id_without_point_ordinal_fails():
    # a deterministic apply record carries BOTH components or neither.
    with pytest.raises(ValidationError):
        _det_apply(point_ordinal=None)


def test_deterministic_apply_record_intent_id_must_match_pinned_format():
    # a valid deterministic key, but the audit intent_id is NOT "{rule_id}#{point_ordinal}"
    with pytest.raises(ValidationError):
        _det_apply(intent_id="rule.equal#0-tampered")


def test_intent_family_record_with_stray_point_ordinal_fails():
    # rule_id None but point_ordinal set (note: 0 is falsy — the guard is `is not None`).
    with pytest.raises(ValidationError):
        _apply(point_ordinal=0)


def test_intent_family_record_with_a_deterministic_key_fails():
    # cross-family: an intent-lane record (rule_id None) whose key is a deterministic
    # key → the intent-family recompute disagrees → construction fails.
    with pytest.raises(ValidationError):
        _apply(target_apply_key=_det_key())


def test_deterministic_family_record_with_an_intent_key_fails():
    # cross-family (mirror): a deterministic record whose key is the intent-family key.
    with pytest.raises(ValidationError):
        _det_apply(target_apply_key=_K)


def test_intent_family_none_none_record_behaves_as_before():
    a = _apply()  # rule_id / point_ordinal default None
    assert a.rule_id is None and a.point_ordinal is None
    assert a.target_apply_key == _K


def test_deterministic_components_are_semantic_not_excluded():
    # rule_id / point_ordinal are deterministic BUSINESS identity (not runtime-random
    # like intent_id), so they participate in the semantic digest (NOT SEMANTIC_EXCLUDE).
    excl = shadow.ShadowTargetApplyRecord.SEMANTIC_EXCLUDE
    assert "rule_id" not in excl and "point_ordinal" not in excl
    a = _det_apply()
    b = _det_apply(
        rule_id="rule.other",
        intent_id="rule.other#0",
        target_apply_key=_det_key(rule_id="rule.other"),
    )
    assert a.semantic_digest() != b.semantic_digest()


# --------------------------------------------------------------------------- #
# invariant 2 — order-id self-consistency (tamper each component)               #
# --------------------------------------------------------------------------- #
def test_order_record_accepts_a_self_consistent_id():
    assert _order().order_id == _O


@pytest.mark.parametrize(
    "tamper",
    [
        {"target_apply_key": _apply_key(intent_id="intent-2")},
        {"symbol": _sym("000001", exchange="SZ", board="main")},
        {"order_kind": "target_sell"},
        {"trigger_bar": "2026-07-22"},
        {"ordinal": 1},
    ],
)
def test_order_id_tampering_a_component_fails(tamper):
    with pytest.raises(ValidationError):
        _order(**tamper)  # order_id stays _O


def test_order_id_forged_digest_fails():
    with pytest.raises(ValidationError):
        _order(order_id="0" * 64)


def test_order_id_ignores_non_component_fields():
    # side / otype / prices / sizing are NOT key components — changing them keeps _O.
    assert _order(side="sell", otype="market", limit_price=None,
                  qty=None, cash_budget=9.0).order_id == _O


# --------------------------------------------------------------------------- #
# invariant 2 — fill-id self-consistency (tamper each component)                #
# --------------------------------------------------------------------------- #
def test_fill_record_accepts_a_self_consistent_id():
    assert _fill().fill_id == _F


@pytest.mark.parametrize(
    "tamper",
    [
        {"order_id": "b" * 64},
        {"fill_seq": 2},
    ],
)
def test_fill_id_tampering_a_component_fails(tamper):
    with pytest.raises(ValidationError):
        _fill(**tamper)  # fill_id stays _F


def test_fill_id_forged_digest_fails():
    with pytest.raises(ValidationError):
        _fill(fill_id="0" * 64)


# --------------------------------------------------------------------------- #
# invariant 3 — corporate-action kind/field matrix (closed)                     #
# --------------------------------------------------------------------------- #
def test_corporate_action_valid_cash_dividend():
    c = _cae(kind="cash_dividend", cash_per_share=1.25, shares_ratio=0.0)
    assert c.kind == "cash_dividend"


def test_corporate_action_valid_stock_bonus():
    c = _cae(kind="stock_bonus", cash_per_share=0.0, shares_ratio=0.5)
    assert c.kind == "stock_bonus"


def test_corporate_action_valid_split():
    c = _cae(kind="split", cash_per_share=0.0, shares_ratio=2.0)
    assert c.shares_ratio == 2.0


@pytest.mark.parametrize(
    "over",
    [
        # cash_dividend: cash>0 AND ratio==0
        {"kind": "cash_dividend", "cash_per_share": 0.0, "shares_ratio": 0.0},
        {"kind": "cash_dividend", "cash_per_share": 1.5, "shares_ratio": 0.5},
        # stock_bonus: ratio>0 AND cash==0
        {"kind": "stock_bonus", "cash_per_share": 0.0, "shares_ratio": 0.0},
        {"kind": "stock_bonus", "cash_per_share": 1.0, "shares_ratio": 0.5},
        # split: ratio>0 AND ratio!=1 AND cash==0
        {"kind": "split", "cash_per_share": 0.0, "shares_ratio": 0.0},
        {"kind": "split", "cash_per_share": 0.0, "shares_ratio": 1.0},
        {"kind": "split", "cash_per_share": 0.5, "shares_ratio": 2.0},
    ],
)
def test_corporate_action_illegal_matrix_combos_fail(over):
    with pytest.raises(ValidationError):
        _cae(**over)


def test_corporate_action_rejects_negative_amounts():
    with pytest.raises(ValidationError):
        _cae(kind="cash_dividend", cash_per_share=-1.0, shares_ratio=0.0)
    with pytest.raises(ValidationError):
        _cae(kind="stock_bonus", cash_per_share=0.0, shares_ratio=-1.0)


def test_corporate_action_available_at_is_required():
    assert shadow.CorporateActionEvent.model_fields["available_at"].is_required()
    with pytest.raises(ValidationError):
        shadow.CorporateActionEvent(
            symbol=_sym(), kind="cash_dividend", ex_date="2026-07-21",
            cash_per_share=1.5, shares_ratio=0.0,  # no available_at
        )


def test_corporate_action_available_at_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        _cae(available_at=datetime(2026, 7, 20, 8, 0))  # naive


# --------------------------------------------------------------------------- #
# invariant 1 — the run result references intents only by digest                #
# --------------------------------------------------------------------------- #
def test_run_result_references_intents_only_by_digest():
    fields = shadow.ShadowRunResult.model_fields
    assert "intent_content_digests" in fields
    for name, f in fields.items():
        assert "TargetPortfolioIntent" not in str(f.annotation), (
            f"{name} embeds an intent — results must reference intents only by digest"
        )


# --------------------------------------------------------------------------- #
# invariant 4 — run-result causation closure                                    #
# --------------------------------------------------------------------------- #
def test_run_result_build_happy_path_closes_causation_and_self_seals():
    r = _run_result()
    assert r.content_digest == r.semantic_digest()
    assert r.matching_engine_version == shadow.SHADOW_MATCHING_ENGINE_VERSION


def test_causation_dangling_fill_order_fails():
    orphan_fill = _fill(order_id="b" * 64,
                        fill_id=shadow.shadow_fill_id(order_id="b" * 64, fill_seq=1))
    with pytest.raises(ValidationError):
        _run_result(fills=(orphan_fill,))


def test_causation_dangling_apply_order_ids_fails():
    with pytest.raises(ValidationError):
        _run_result(applies=(_apply(order_ids=("b" * 64,)),), fills=())


def test_causation_dangling_order_apply_fails():
    other_key = _apply_key(intent_id="intent-2")
    orphan_order = _order(
        target_apply_key=other_key,
        order_id=shadow.shadow_order_id(apply_key=other_key, symbol=_sym(),
                                        order_kind="target_buy", trigger_bar="2026-07-21",
                                        ordinal=0),
    )
    with pytest.raises(ValidationError):
        _run_result(applies=(), orders=(orphan_order,), fills=())


def test_direct_construct_with_wrong_content_digest_fails_self_seal():
    with pytest.raises(ValidationError):
        shadow.ShadowRunResult(**_run_fields(), content_digest="0" * 64)


# --------------------------------------------------------------------------- #
# metrics vocabulary + non-finite omission + strict-int n_trades                #
# --------------------------------------------------------------------------- #
def test_metrics_all_nine_vocabulary_keys_accepted():
    m = {k: 0.1 for k in _METRIC_VOCAB}
    r = _run_result(metrics=m)
    assert set(r.metrics) == _METRIC_VOCAB


def test_metrics_unknown_key_rejected():
    with pytest.raises(ValidationError):
        _run_result(metrics={"bogus_metric": 0.1})


def test_build_omits_non_finite_metrics():
    r = _run_result(metrics={"sharpe": float("nan"), "ann_return": 0.12,
                             "calmar": float("inf"), "volatility": float("-inf")})
    # the non-finite entries are OMITTED (never smuggled as a sentinel) …
    assert r.metrics == {"ann_return": 0.12}


def test_direct_construct_cannot_smuggle_a_non_finite_metric():
    # bypassing build(): a NaN metric value cannot pass the FiniteFloat field.
    with pytest.raises(ValidationError):
        shadow.ShadowRunResult(**_run_fields(metrics={"sharpe": float("nan")}),
                               content_digest="0" * 64)


def test_metrics_default_is_empty_and_independent_between_instances():
    a, b = _run_result(), _run_result()
    assert a.metrics == {} and b.metrics == {}
    assert a.metrics is not b.metrics  # pydantic deep-copies the mutable default


def test_n_trades_strict_int_rejects_a_float():
    with pytest.raises(ValidationError):
        _run_result(n_trades=5.0)


def test_n_trades_default_is_zero():
    assert shadow.ShadowRunResult.model_fields["n_trades"].default == 0


# --------------------------------------------------------------------------- #
# invariant 5 — frozen + canonically digestible                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("factory", [_apply, _order, _fill, _reject, _cae, _run_result])
def test_records_are_frozen(factory):
    rec = factory()
    with pytest.raises(ValidationError):
        rec.schema_version = "2"


@pytest.mark.parametrize("factory", [_apply, _order, _fill, _reject, _cae, _run_result])
def test_records_are_canonically_digestible(factory):
    d = factory().semantic_digest()
    assert isinstance(d, str) and len(d) == 64
    assert all(c in "0123456789abcdef" for c in d)


@pytest.mark.parametrize("factory", [_apply, _order, _fill, _reject, _cae, _run_result])
def test_records_forbid_extra_fields(factory):
    with pytest.raises(ValidationError):
        factory(not_a_real_field="x")


def test_run_result_survives_json_roundtrip():
    r = _run_result(metrics={"ann_return": 0.12, "sharpe": 1.4})
    reloaded = shadow.ShadowRunResult.model_validate_json(r.model_dump_json())
    assert reloaded.content_digest == r.content_digest
    assert reloaded.semantic_digest() == r.semantic_digest()
