# -*- coding: utf-8 -*-
"""Phase 8 · Task 9b — Lane D deterministic injection-face adapters (AMEND-8 + 交付物③).

Covers (per brief Step 1):
* three schema matrices — ``UpstreamRatingsExtract`` / ``AllowedActions`` /
  ``AnnouncementRiskFlags`` (fields, closed enums, frozen, extra=forbid, schema_version);
* band-vocabulary import equality — ``AllowedActions`` reuses Phase 6's exported
  ``TARGET_WEIGHT_BANDS`` by object IDENTITY (imported, never redefined here) and can
  never carry a weight outside it (digest-checked);
* the hard-constraint honesty rows — a suspended / limit-locked / vetoed instrument's
  allowance provably EXCLUDES the impossible action (LLM-selects-never-computes shape);
* deterministic upstream-ratings extraction vectors (label normalization, majority tilt,
  tie → None, unmapped rows excluded from the tally);
* deterministic announcement-flag vectors (tier1>tier2>tier3 ordering, 排除词 suppression,
  hard veto = any tier1, dedup + sort);
* the memory-bridge PIT recipe parameters (同票近 5 / 跨票近 3 反思 / matured-only /
  pending 不注入 / lesson_id 必列) + selector determinism;
* adapter determinism (same inputs ⇒ identical semantic digests) + full-absence tolerance;
* the two guardrail materials exist and mirror the code lexicon (anti-drift).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import decision_inputs as di
from guanlan_v2.orchestration import shadow as sh
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import DigestModel, content_digest

_UTC = timezone.utc
_AS_OF = datetime(2026, 7, 23, 7, 0, tzinfo=_UTC)
_REPO = Path(__file__).resolve().parents[2]
_MATERIALS = _REPO / "config" / "orchestration" / "materials" / "guardrails"

_MAIN = normalize_symbol("600519.SH")     # main board — lot 100
_STAR = normalize_symbol("688981.SH")     # STAR — lot 200
_CHINEXT = normalize_symbol("300750.SZ")  # ChiNext — lot 100


# --------------------------------------------------------------------------- #
# Band vocabulary — imported from Phase 6, never redefined (R7)                 #
# --------------------------------------------------------------------------- #
def test_target_weight_bands_is_the_imported_phase6_object_not_a_copy():
    # object identity: the name in decision_inputs IS shadow's exported constant
    assert di.TARGET_WEIGHT_BANDS is sh.TARGET_WEIGHT_BANDS
    # digest equality (a copy with equal values would still pass this — identity above
    # is the load-bearing check; this pins the value the digest guard reads)
    assert content_digest(list(di.TARGET_WEIGHT_BANDS)) == content_digest(
        list(sh.TARGET_WEIGHT_BANDS)
    )
    assert di.TARGET_WEIGHT_BANDS == (0.0, 0.25, 0.5, 0.75, 1.0)


# --------------------------------------------------------------------------- #
# Face ② — UpstreamRatingsExtract                                             #
# --------------------------------------------------------------------------- #
def test_upstream_ratings_extract_schema_matrix():
    row = di.UpstreamRatingRow(source="CICC", raw_label="买入", band="Buy")
    assert row.schema_version == "1"
    assert isinstance(row, DigestModel)
    # frozen + extra=forbid
    with pytest.raises(ValidationError):
        di.UpstreamRatingRow(source="X", raw_label="y", band="Buy", junk=1)
    with pytest.raises(ValidationError):
        row.source = "mut"  # type: ignore[misc]
    # band is a closed 5-level vocabulary (+ None for an unmapped label)
    with pytest.raises(ValidationError):
        di.UpstreamRatingRow(source="X", raw_label="y", band="StrongBuy")


def test_normalize_rating_label_maps_cn_en_and_returns_none_for_unknown():
    assert di.normalize_rating_label("买入") == "Buy"
    assert di.normalize_rating_label("增持") == "Overweight"
    assert di.normalize_rating_label("中性") == "Hold"
    assert di.normalize_rating_label("减持") == "Underweight"
    assert di.normalize_rating_label("卖出") == "Sell"
    assert di.normalize_rating_label("Buy") == "Buy"
    assert di.normalize_rating_label("  overweight ") == "Overweight"
    # unknown label is honestly UNMAPPED (never guessed)
    assert di.normalize_rating_label("持有待定的模糊话术") is None


def test_extract_upstream_ratings_computes_majority_tilt_deterministically():
    ext = di.extract_upstream_ratings(
        symbol=_MAIN,
        as_of=_AS_OF,
        rows=[
            {"source": "CICC", "raw_label": "买入"},
            {"source": "CITIC", "raw_label": "增持"},
            {"source": "GF", "raw_label": "Buy", "target_price": 1900.0},
        ],
    )
    assert ext.schema_version == "1"
    assert ext.majority_tilt == "Buy"  # 2 Buy vs 1 Overweight
    assert ext.tie_bands == ()
    # rows sorted by source, deterministic
    assert [r.source for r in ext.rows] == ["CICC", "CITIC", "GF"]
    assert ext.semantic_digest() == di.extract_upstream_ratings(
        symbol=_MAIN,
        as_of=_AS_OF,
        rows=[
            {"source": "GF", "raw_label": "Buy", "target_price": 1900.0},
            {"source": "CICC", "raw_label": "买入"},
            {"source": "CITIC", "raw_label": "增持"},
        ],
    ).semantic_digest()


def test_extract_upstream_ratings_tie_yields_no_majority_and_names_tied_bands():
    ext = di.extract_upstream_ratings(
        symbol=_MAIN,
        as_of=_AS_OF,
        rows=[
            {"source": "A", "raw_label": "买入"},
            {"source": "B", "raw_label": "卖出"},
        ],
    )
    assert ext.majority_tilt is None
    assert ext.tie_bands == ("Buy", "Sell")  # sorted by band order, honest tie


def test_extract_upstream_ratings_unmapped_rows_excluded_from_tally():
    ext = di.extract_upstream_ratings(
        symbol=_MAIN,
        as_of=_AS_OF,
        rows=[
            {"source": "A", "raw_label": "买入"},
            {"source": "B", "raw_label": "语焉不详"},  # unmapped
            {"source": "C", "raw_label": "语焉不详二号"},  # unmapped
        ],
    )
    # the two unmapped rows carry band=None and never vote — Buy is the sole tilt
    assert ext.majority_tilt == "Buy"
    assert {r.source: r.band for r in ext.rows} == {"A": "Buy", "B": None, "C": None}


def test_extract_upstream_ratings_all_unmapped_is_honest_none():
    ext = di.extract_upstream_ratings(
        symbol=_MAIN, as_of=_AS_OF, rows=[{"source": "A", "raw_label": "???"}]
    )
    assert ext.majority_tilt is None
    assert ext.tie_bands == ()


def test_extract_upstream_ratings_duplicate_source_is_a_caller_bug():
    with pytest.raises((ValueError, ValidationError)):
        di.extract_upstream_ratings(
            symbol=_MAIN,
            as_of=_AS_OF,
            rows=[
                {"source": "A", "raw_label": "买入"},
                {"source": "A", "raw_label": "卖出"},
            ],
        )


# --------------------------------------------------------------------------- #
# Face ③ — AllowedActions (the deterministic hard-constraint guardrail)         #
# --------------------------------------------------------------------------- #
def _facts(symbol=_MAIN, **kw):
    return di.SymbolConstraintFacts(symbol=symbol, **kw)


def test_allowed_actions_schema_and_already_validated_flag():
    aa = di.build_allowed_actions(as_of=_AS_OF, facts=[_facts()])
    assert aa.schema_version == "1"
    assert aa.already_validated is True  # prompt marks the block "already validated"
    assert isinstance(aa, DigestModel)
    # the block is frozen — the LLM selects within it, never mutates it
    with pytest.raises(ValidationError):
        aa.already_validated = False  # type: ignore[misc]


def test_normal_symbol_is_fully_tradable_full_band_lot_100():
    (a,) = di.build_allowed_actions(as_of=_AS_OF, facts=[_facts()]).allowances
    assert a.can_buy is True and a.can_sell is True
    assert a.max_target_weight == 1.0
    assert a.lot_size == 100
    assert a.reasons == ()


def test_star_board_lot_is_200():
    (a,) = di.build_allowed_actions(as_of=_AS_OF, facts=[_facts(symbol=_STAR)]).allowances
    assert a.lot_size == 200


def test_suspended_excludes_buy_and_sell_and_caps_band_zero():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(suspended=True)]
    ).allowances
    assert a.can_buy is False and a.can_sell is False
    assert a.max_target_weight == 0.0
    assert "suspended" in " ".join(a.reasons).lower()


def test_limit_up_oneword_excludes_buy_but_allows_sell():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(limit_status="up_oneword")]
    ).allowances
    # a sealed 一字涨停 cannot be bought (queue never fills) but a holder can sell
    assert a.can_buy is False
    assert a.can_sell is True
    assert a.max_target_weight == 0.0


def test_limit_down_oneword_excludes_sell_but_allows_buy():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(limit_status="down_oneword")]
    ).allowances
    assert a.can_sell is False
    assert a.can_buy is True


def test_t_plus_1_lock_excludes_sell_only():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(holding_acquired_today=True)]
    ).allowances
    assert a.can_sell is False  # today-bought shares are T+1 locked
    assert a.can_buy is True


def test_game_capital_veto_all_three_conditions_excludes_buy():
    # 游资票否决: mv<200亿 ∧ pe>100 ∧ 60日涨幅>50% (AMEND-8 §8.0 ④)
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF,
        facts=[_facts(market_cap_yi=80.0, pe=150.0, ret_60d_pct=90.0)],
    ).allowances
    assert a.can_buy is False
    assert a.max_target_weight == 0.0
    assert any("game" in r.lower() or "游资" in r for r in a.reasons)


def test_game_capital_veto_requires_all_three_and_never_fabricates_on_missing():
    # only two of three conditions → no veto
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(market_cap_yi=80.0, pe=150.0, ret_60d_pct=10.0)]
    ).allowances
    assert a.can_buy is True
    # a missing fact never fabricates a veto (honest — do not guess)
    (b,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(market_cap_yi=80.0, pe=None, ret_60d_pct=90.0)]
    ).allowances
    assert b.can_buy is True


def test_severe_negative_event_excludes_buy_and_caps_zero():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(severe_negative_event=True)]
    ).allowances
    assert a.can_buy is False
    assert a.max_target_weight == 0.0


def test_st_name_caps_band_but_stays_tradable():
    (a,) = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(is_st=True)]
    ).allowances
    assert a.can_buy is True and a.can_sell is True
    assert a.max_target_weight == 0.25  # reduced exposure, a valid band member


def test_allowed_actions_sorted_and_unique_by_symbol():
    aa = di.build_allowed_actions(
        as_of=_AS_OF, facts=[_facts(symbol=_STAR), _facts(symbol=_MAIN)]
    )
    dotted = [a.symbol.dotted for a in aa.allowances]
    assert dotted == sorted(dotted)
    with pytest.raises((ValueError, ValidationError)):
        di.build_allowed_actions(as_of=_AS_OF, facts=[_facts(), _facts()])


def test_symbol_allowance_band_must_be_a_member_of_target_weight_bands():
    # a hand-built allowance with an off-band ceiling is rejected at construction
    with pytest.raises(ValidationError):
        di.SymbolAllowance(
            symbol=_MAIN,
            can_buy=True,
            can_sell=True,
            lot_size=100,
            max_target_weight=0.37,  # not in (0, .25, .5, .75, 1)
            reasons=(),
        )


def test_symbol_allowance_no_buy_implies_zero_ceiling_honesty():
    # a name you cannot buy cannot carry a positive target ceiling today
    with pytest.raises(ValidationError):
        di.SymbolAllowance(
            symbol=_MAIN,
            can_buy=False,
            can_sell=True,
            lot_size=100,
            max_target_weight=1.0,
            reasons=("x",),
        )


def test_allowed_actions_determinism():
    facts = [_facts(symbol=_STAR, is_st=True), _facts(symbol=_MAIN)]
    a = di.build_allowed_actions(as_of=_AS_OF, facts=facts)
    b = di.build_allowed_actions(as_of=_AS_OF, facts=list(reversed(facts)))
    assert a.semantic_digest() == b.semantic_digest()


# --------------------------------------------------------------------------- #
# Face ④ — AnnouncementRiskFlags                                              #
# --------------------------------------------------------------------------- #
def test_announcement_flag_schema_matrix():
    f = di.AnnouncementFlag(tier="tier1", keyword="立案调查", is_veto=True)
    assert f.schema_version == "1"
    with pytest.raises(ValidationError):
        di.AnnouncementFlag(tier="tier9", keyword="x", is_veto=False)
    with pytest.raises(ValidationError):
        f.tier = "tier2"  # type: ignore[misc]


def test_scan_detects_three_tiers_and_orders_by_severity():
    flags = di.scan_announcement_risk(
        symbol=_MAIN,
        as_of=_AS_OF,
        announcements=[
            "公司收到证监会立案调查通知书",   # tier1
            "公司收到交易所问询函",           # tier2
            "公司收到关注函",                 # tier3
        ],
    )
    assert [f.tier for f in flags.flags] == ["tier1", "tier2", "tier3"]  # 立案>问询>关注
    assert flags.max_tier == "tier1"
    assert flags.hard_veto is True  # tier1 present


def test_scan_tier2_only_is_not_a_hard_veto():
    flags = di.scan_announcement_risk(
        symbol=_MAIN, as_of=_AS_OF, announcements=["收到问询函，涉及商誉减值事项"]
    )
    assert flags.max_tier == "tier2"
    assert flags.hard_veto is False
    kws = {f.keyword for f in flags.flags}
    assert "问询函" in kws and "商誉减值" in kws


def test_scan_exclusion_words_suppress_false_positives():
    # a clarification/denial announcement carrying a risk term must NOT fire
    flags = di.scan_announcement_risk(
        symbol=_MAIN,
        as_of=_AS_OF,
        announcements=["关于媒体报道立案调查传闻不属实的澄清公告"],
    )
    assert flags.flags == ()
    assert flags.max_tier is None
    assert flags.hard_veto is False


def test_scan_dedups_and_is_deterministic():
    ann = ["立案调查", "又一则立案调查的公告", "问询函"]
    a = di.scan_announcement_risk(symbol=_MAIN, as_of=_AS_OF, announcements=ann)
    b = di.scan_announcement_risk(
        symbol=_MAIN, as_of=_AS_OF, announcements=list(reversed(ann))
    )
    # dedup by (tier, keyword): 立案调查 appears once
    assert [(f.tier, f.keyword) for f in a.flags] == [
        ("tier1", "立案调查"),
        ("tier2", "问询函"),
    ]
    assert a.semantic_digest() == b.semantic_digest()


def test_scan_empty_is_honest_none():
    flags = di.scan_announcement_risk(symbol=_MAIN, as_of=_AS_OF, announcements=[])
    assert flags.flags == ()
    assert flags.max_tier is None and flags.hard_veto is False


def test_announcement_veto_terms_are_tier1_subset():
    assert set(di.ANNOUNCEMENT_VETO_TERMS) <= set(di.ANNOUNCEMENT_TIER1)
    # tier tuples are disjoint (a keyword belongs to exactly one tier)
    t1, t2, t3 = set(di.ANNOUNCEMENT_TIER1), set(di.ANNOUNCEMENT_TIER2), set(di.ANNOUNCEMENT_TIER3)
    assert t1 & t2 == set() and t1 & t3 == set() and t2 & t3 == set()


# --------------------------------------------------------------------------- #
# Face ⑤ — past-lessons PIT recipe (parameterizes the Phase 3 bridge)          #
# --------------------------------------------------------------------------- #
def test_past_lessons_recipe_parameters():
    r = di.PAST_LESSONS_RECIPE
    assert r.same_name_full_recent == 5      # 同票近 5 全文
    assert r.cross_name_reflection_recent == 3  # 跨票近 3 只反思
    assert r.matured_only is True            # matured-only
    assert r.exclude_pending is True         # pending 不注入
    assert r.require_cited_lesson_ids is True  # payload 必须列引用 lesson_id


def test_past_lessons_recipe_redlines_are_structural():
    # the honesty red lines cannot be turned off by constructing a laxer recipe
    with pytest.raises((ValueError, TypeError)):
        di.PastLessonsRecipe(
            same_name_full_recent=5,
            cross_name_reflection_recent=3,
            matured_only=False,  # forbidden
            exclude_pending=True,
            require_cited_lesson_ids=True,
        )


def _cand(lesson_id, *, same_name, is_reflection, matured=True, pending=False, day=1):
    return di.LessonCandidate(
        lesson_id=lesson_id,
        as_of=datetime(2026, 7, day, tzinfo=_UTC),
        same_name=same_name,
        is_reflection=is_reflection,
        matured=matured,
        pending=pending,
    )


def test_select_past_lessons_applies_recipe_filters_and_caps():
    cands = [
        # same-name full records — 7 supplied, cap 5, most-recent kept
        *[_cand(f"L-same-{i}", same_name=True, is_reflection=False, day=i) for i in range(1, 8)],
        # cross-name — only reflections are injected, cap 3
        *[_cand(f"L-cross-refl-{i}", same_name=False, is_reflection=True, day=i) for i in range(1, 6)],
        _cand("L-cross-nonrefl", same_name=False, is_reflection=False),  # dropped (not reflection)
        # excluded by the red lines
        _cand("L-pending", same_name=True, is_reflection=False, pending=True, day=9),
        _cand("L-immature", same_name=True, is_reflection=False, matured=False, day=9),
    ]
    block = di.select_past_lessons(subject=_MAIN, as_of=_AS_OF, candidates=cands)
    # same-name: 5 most recent (days 7..3), pending/immature excluded
    assert len(block.same_name_lesson_ids) == 5
    assert "L-pending" not in block.same_name_lesson_ids
    assert "L-immature" not in block.same_name_lesson_ids
    assert block.same_name_lesson_ids[0] == "L-same-7"  # most recent first
    # cross-name: 3 most recent reflections, non-reflection dropped
    assert len(block.cross_name_reflection_ids) == 3
    assert "L-cross-nonrefl" not in block.cross_name_reflection_ids
    # payload lists lesson_ids (require_cited_lesson_ids discipline)
    assert all(isinstance(x, str) and x for x in block.same_name_lesson_ids)


def test_select_past_lessons_is_deterministic():
    cands = [
        _cand("A", same_name=True, is_reflection=False, day=2),
        _cand("B", same_name=False, is_reflection=True, day=3),
    ]
    b1 = di.select_past_lessons(subject=_MAIN, as_of=_AS_OF, candidates=cands)
    b2 = di.select_past_lessons(subject=_MAIN, as_of=_AS_OF, candidates=list(reversed(cands)))
    assert b1 == b2


def test_select_past_lessons_empty_is_empty_block():
    block = di.select_past_lessons(subject=_MAIN, as_of=_AS_OF, candidates=[])
    assert block.same_name_lesson_ids == ()
    assert block.cross_name_reflection_ids == ()


# --------------------------------------------------------------------------- #
# Cross-cutting: purity / absence tolerance / partitions                       #
# --------------------------------------------------------------------------- #
def test_partition_tuples_for_task11_firewall():
    assert di.DECISION_INPUT_PUBLIC_MODELS == (
        di.UpstreamRatingsExtract,
        di.AllowedActions,
        di.AnnouncementRiskFlags,
    )
    assert set(di.DECISION_INPUT_INTERNAL_MODELS) == {
        di.UpstreamRatingRow,
        di.SymbolAllowance,
        di.AnnouncementFlag,
    }
    for m in di.DECISION_INPUT_PUBLIC_MODELS + di.DECISION_INPUT_INTERNAL_MODELS:
        assert issubclass(m, DigestModel)


def test_adapter_inputs_are_plain_frozen_dataclasses_not_contracts():
    # inputs / the face-⑤ recipe+block are pre-input configuration, NOT registered
    # contracts — they must not leak into the Phase-8 contract firewall.
    for t in (di.SymbolConstraintFacts, di.LessonCandidate, di.PastLessonsRecipe, di.PastLessonsBlock):
        assert dataclasses.is_dataclass(t)
        assert not issubclass(t, DigestModel)
        assert t.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_all_faces_are_independently_constructible_when_others_absent():
    # 优雅缺席: each face stands alone; nothing forces any other face to be present.
    assert di.extract_upstream_ratings(symbol=_MAIN, as_of=_AS_OF, rows=[]) is not None
    assert di.build_allowed_actions(as_of=_AS_OF, facts=[]) is not None
    assert di.scan_announcement_risk(symbol=_MAIN, as_of=_AS_OF, announcements=[]) is not None
    assert di.select_past_lessons(subject=_MAIN, as_of=_AS_OF, candidates=[]) is not None


# --------------------------------------------------------------------------- #
# Guardrail materials exist and mirror the code lexicon (anti-drift)           #
# --------------------------------------------------------------------------- #
def test_allowed_actions_rules_material_exists_and_cites_the_band_vocabulary():
    text = (_MATERIALS / "allowed-actions-rules.md").read_text(encoding="utf-8")
    assert text.strip()
    # the material documents the deterministic, pre-LLM discipline
    for token in ("already validated", "T+1", "停牌", "涨跌停"):
        assert token in text


def test_announcement_lexicon_material_mirrors_the_code_tiers():
    text = (_MATERIALS / "announcement-risk-lexicon.md").read_text(encoding="utf-8")
    assert text.strip()
    # every code lexicon term appears in the human-reviewable material (anti-drift)
    for kw in di.ANNOUNCEMENT_TIER1 + di.ANNOUNCEMENT_TIER2 + di.ANNOUNCEMENT_TIER3:
        assert kw in text, f"lexicon term {kw!r} missing from material"
    for excl in di.ANNOUNCEMENT_EXCLUSIONS:
        assert excl in text
