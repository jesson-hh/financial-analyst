# -*- coding: utf-8 -*-
"""Phase 8 - Task 4b: the K线经典形态词典 P0 registry contract + seed material (AMEND-6a).

Written test-first (RED until ``guanlan_v2/orchestration/pattern_registry.py`` exists).

This is the user's personally-named P0 ("把 k线缺口这个重点记录 一定要完善"). The
delivered surface is (R10 boundary): the registry **contract** (``PatternReplayStats``
/ ``GapFillStats`` / ``PatternDefinition`` / ``PatternDictionary``) plus the reviewed
**seed dictionary material** (self-authored definitions + explicit numeric params). The
~31 deterministic recognizer *handlers* and the historical-replay statistics are a
separately-chartered small task; until it lands every seed entry's ``replay_stats`` is
``None`` ⇒ UNAVAILABLE 显形 (样本不足绝不硬给胜率).

Invariant matrix proven here:
  * stats honesty      -> any ``None`` field ⇒ UNAVAILABLE; a rate without its sample
    count (``n_occurrences``) is rejected (dishonest); bounds enforced;
  * gap-fill honesty   -> the 缺口回补统计 contract shape (fill_rate / median_bars_to_fill
    / n_gaps) obeys the same honesty rule; only the gap family may carry it;
  * definition matrix  -> closed family vocabulary; geometry_inputs are a non-empty,
    sorted, dup-free subset of the closed geometry vocabulary (every entry NAMES its
    inputs so the later recognizer task binds a handler with no @2 bump); rule_params
    are explicit finite floats; frozen;
  * dictionary seal    -> entries sorted by ``pattern_id@definition_version`` + dup-free;
    ``build()`` seals ``content_digest`` and it is re-verified on load;
  * seed census        -> the six AMEND-6a families with the reviewed counts; the 缺口
    (gap) family authored in full with per-type gap-fill slots; multi-bar structures
    are approximate (置信分), every other family is geometrically precise;
  * material digest     -> the physical guardrail material bytes reproduce the reviewed
    ``PatternDictionary`` via ``catalog_material_digest`` (stable, pinned) + round-trip;
  * no-LLM             -> an AST/source scan proves the recognizer contract is pure
    (no engine / LLM / network import anywhere; 识别器 = deterministic predicate).

Run from repo root: ``pytest tests/orchestration/test_pattern_registry.py -v``
"""
from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

from guanlan_v2.orchestration.catalog import (
    ResolvedTextMaterial,
    catalog_material_digest,
)

from guanlan_v2.orchestration.pattern_registry import (
    GEOMETRY_INPUTS,
    PATTERN_DICTIONARY_MATERIAL_ID,
    PATTERN_DICTIONARY_MATERIAL_VERSION,
    PATTERN_FAMILIES,
    STATS_UNAVAILABLE,
    GapFillStats,
    GeometryInput,
    PatternDefinition,
    PatternDictionary,
    PatternFamily,
    PatternReplayStats,
    build_pattern_dictionary_material,
    build_seed_pattern_dictionary,
    gap_fill_stats_label,
    pattern_dictionary_from_material_bytes,
    pattern_dictionary_material_bytes,
    replay_stats_label,
)

# --------------------------------------------------------------------------- #
# Paths + the pinned reviewed material digest (drift guard).                   #
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[2]
MATERIAL_FILE = (
    REPO_ROOT / "config" / "orchestration" / "materials" / "guardrails"
    / "pattern-dictionary.json"
)
PATTERN_REGISTRY_SRC = REPO_ROOT / "guanlan_v2" / "orchestration" / "pattern_registry.py"

#: pinned reviewed material digest (drift guard); sealed from the physical bytes.
EXPECTED_MATERIAL_DIGEST = (
    "e84f13698200f2b3bbda19c85abbf8baec5a3eb54ae0e66540383078ceb23a5a"
)

#: reviewed per-family seed census (AMEND-6a coverage).
EXPECTED_FAMILY_COUNTS = {
    "single_bar": 6,
    "double_bar": 5,
    "triple_bar": 4,
    "multi_bar": 8,
    "gap": 4,
    "astock": 4,
}
EXPECTED_TOTAL = sum(EXPECTED_FAMILY_COUNTS.values())  # 31


def _defn(**over):
    """A minimal valid PatternDefinition for contract-matrix tests."""
    base = dict(
        pattern_id="pv.single.hammer",
        definition_version="1",
        family="single_bar",
        display_name="锤子线",
        predicate="下影线≥实体2倍、上影极短、收于上部,出现在下跌后:做多反转。",
        geometry_inputs=("body", "close_pos", "lower_wick", "prior_trend", "upper_wick"),
        rule_params={"lower_wick_to_body_min": 2.0, "min_close_pos": 0.6},
        approximate=False,
    )
    base.update(over)
    return PatternDefinition(**base)


# =========================================================================== #
# PatternReplayStats — UNAVAILABLE honesty (样本不足绝不硬给胜率).               #
# =========================================================================== #
def test_replay_stats_all_present_is_available():
    s = PatternReplayStats(win_rate=0.55, profit_loss_ratio=1.8, n_occurrences=42)
    assert s.is_available is True
    label = replay_stats_label(s)
    assert STATS_UNAVAILABLE not in label
    assert "0.55" in label and "42" in label


def test_replay_stats_any_none_is_unavailable():
    # not-yet-replayed: all None.
    s0 = PatternReplayStats(win_rate=None, profit_loss_ratio=None, n_occurrences=None)
    assert s0.is_available is False
    # insufficient sample: n_occurrences known but rates withheld (the exact 样本不足 case).
    s1 = PatternReplayStats(win_rate=None, profit_loss_ratio=None, n_occurrences=3)
    assert s1.is_available is False
    for s in (s0, s1):
        assert replay_stats_label(s) == STATS_UNAVAILABLE


def test_replay_stats_none_renders_unavailable_never_zeros():
    assert replay_stats_label(None) == STATS_UNAVAILABLE
    # honesty: an unavailable render must NEVER masquerade as a real zero statistic.
    assert "0" not in replay_stats_label(None)


def test_replay_stats_rate_without_sample_count_is_rejected():
    # a win_rate/pl with no n_occurrences is dishonest (a rate with no sample) -> reject.
    with pytest.raises(ValueError):
        PatternReplayStats(win_rate=0.6, profit_loss_ratio=None, n_occurrences=None)
    with pytest.raises(ValueError):
        PatternReplayStats(win_rate=None, profit_loss_ratio=1.5, n_occurrences=None)


def test_replay_stats_bounds_enforced():
    with pytest.raises(ValueError):
        PatternReplayStats(win_rate=1.4, profit_loss_ratio=1.0, n_occurrences=10)
    with pytest.raises(ValueError):
        PatternReplayStats(win_rate=-0.1, profit_loss_ratio=1.0, n_occurrences=10)
    with pytest.raises(ValueError):
        PatternReplayStats(win_rate=0.5, profit_loss_ratio=-1.0, n_occurrences=10)


# =========================================================================== #
# GapFillStats — the 缺口回补统计 contract shape (same honesty).                #
# =========================================================================== #
def test_gap_fill_stats_available_and_unavailable():
    ok = GapFillStats(fill_rate=0.8, median_bars_to_fill=4.0, n_gaps=25)
    assert ok.is_available is True
    assert gap_fill_stats_label(None) == STATS_UNAVAILABLE
    none = GapFillStats(fill_rate=None, median_bars_to_fill=None, n_gaps=None)
    assert none.is_available is False
    assert gap_fill_stats_label(none) == STATS_UNAVAILABLE
    # insufficient sample: count known, fill withheld.
    part = GapFillStats(fill_rate=None, median_bars_to_fill=None, n_gaps=2)
    assert part.is_available is False and gap_fill_stats_label(part) == STATS_UNAVAILABLE


def test_gap_fill_stats_rate_without_count_rejected_and_bounds():
    with pytest.raises(ValueError):
        GapFillStats(fill_rate=0.7, median_bars_to_fill=None, n_gaps=None)
    with pytest.raises(ValueError):
        GapFillStats(fill_rate=1.2, median_bars_to_fill=1.0, n_gaps=5)
    with pytest.raises(ValueError):
        GapFillStats(fill_rate=0.5, median_bars_to_fill=-1.0, n_gaps=5)


# =========================================================================== #
# PatternDefinition — recognizer contract matrix.                              #
# =========================================================================== #
def test_family_vocabulary_is_closed():
    assert set(PATTERN_FAMILIES) == set(typing.get_args(PatternFamily))
    with pytest.raises(ValueError):
        _defn(family="candlestick")  # not in the closed vocabulary


def test_geometry_inputs_are_closed_nonempty_sorted_dedup():
    assert set(GEOMETRY_INPUTS) == set(typing.get_args(GeometryInput))
    # empty -> reject (every entry MUST name its geometry inputs).
    with pytest.raises(ValueError):
        _defn(geometry_inputs=())
    # a key outside the closed vocabulary -> reject.
    with pytest.raises(ValueError):
        _defn(geometry_inputs=("body", "not_a_real_input"))
    # unsorted -> reject (canonical order).
    with pytest.raises(ValueError):
        _defn(geometry_inputs=("close_pos", "body"))
    # duplicate -> reject.
    with pytest.raises(ValueError):
        _defn(geometry_inputs=("body", "body"))


def test_geometry_inputs_reference_pa_feature_compatible_keys():
    # the closed vocabulary must include the raw OHLCV bar fields AND the derived
    # compute_pa_features geometry keys (so a recognizer binds the existing pure
    # bar-geometry vocabulary without inventing a parallel one).
    for raw in ("open", "high", "low", "close", "vol", "prev_close"):
        assert raw in GEOMETRY_INPUTS
    for derived in ("body", "upper_wick", "lower_wick", "close_pos", "range_atr",
                    "ema20_rel", "gap", "limit", "vol_ratio", "breakout"):
        assert derived in GEOMETRY_INPUTS


def test_rule_params_must_be_finite_floats():
    with pytest.raises(ValueError):
        _defn(rule_params={"x": float("nan")})
    with pytest.raises(ValueError):
        _defn(rule_params={"x": float("inf")})


def test_definition_is_frozen():
    d = _defn()
    with pytest.raises(Exception):
        d.rule_params = {}  # type: ignore[misc]


def test_replay_stats_defaults_none_meaning_unavailable():
    d = _defn()
    assert d.replay_stats is None
    assert d.replay_stats_available is False
    assert d.render_replay_stats() == STATS_UNAVAILABLE


def test_gap_fill_stats_only_on_gap_family():
    gfs = GapFillStats(fill_rate=None, median_bars_to_fill=None, n_gaps=None)
    # a non-gap entry may NOT carry gap-fill stats (category error).
    with pytest.raises(ValueError):
        _defn(family="single_bar", gap_fill_stats=gfs)
    # a gap entry may carry the (reserved) gap-fill slot.
    g = _defn(
        pattern_id="pv.gap.common",
        family="gap",
        display_name="普通缺口",
        geometry_inputs=("breakout", "high", "low", "prev_close", "vol_ratio"),
        rule_params={"min_gap_frac": 0.005, "max_vol_ratio": 1.5},
        gap_fill_stats=gfs,
    )
    assert g.gap_fill_stats is gfs


# =========================================================================== #
# PatternDictionary — sealed, sorted, dup-free.                                #
# =========================================================================== #
def test_dictionary_build_seals_content_digest_and_reverifies():
    a = _defn(pattern_id="pv.single.doji", display_name="十字星",
              geometry_inputs=("body", "lower_wick", "upper_wick"),
              rule_params={"max_body_frac": 0.05, "max_shadow_frac": 0.6})
    b = _defn()  # pv.single.hammer
    d = PatternDictionary.build(entries=(b, a))  # unsorted input; build must not sort silently
    # build seals the digest and the model re-verifies it.
    assert d.content_digest == d.semantic_digest()


def test_build_sorts_entries_but_validator_rejects_unsorted():
    a = _defn(pattern_id="pv.single.aaa", display_name="甲")
    b = _defn(pattern_id="pv.single.bbb", display_name="乙")
    # build() canonically sorts (convenience + determinism): unsorted input is accepted
    # and the sealed dictionary comes out sorted.
    d = PatternDictionary.build(entries=(b, a))
    assert [e.pattern_id for e in d.entries] == ["pv.single.aaa", "pv.single.bbb"]
    # but the raw validator (direct construction / tampered material) rejects unsorted.
    with pytest.raises(ValueError):
        PatternDictionary(entries=(b, a), content_digest="0" * 64)


def test_dictionary_rejects_duplicate_pattern_key():
    a = _defn(pattern_id="pv.single.hammer")
    b = _defn(pattern_id="pv.single.hammer")
    with pytest.raises(ValueError):
        PatternDictionary.build(entries=(a, b))


# =========================================================================== #
# Seed dictionary census — AMEND-6a coverage (reviewed judgment).              #
# =========================================================================== #
def test_seed_dictionary_total_and_family_census():
    d = build_seed_pattern_dictionary()
    assert len(d.entries) == EXPECTED_TOTAL
    counts: dict[str, int] = {}
    for e in d.entries:
        counts[e.family] = counts.get(e.family, 0) + 1
    assert counts == EXPECTED_FAMILY_COUNTS


def test_seed_entries_are_sorted_dedup_and_versioned():
    d = build_seed_pattern_dictionary()
    keys = [f"{e.pattern_id}@{e.definition_version}" for e in d.entries]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert all(e.definition_version == "1" for e in d.entries)
    assert all(e.pattern_id.startswith("pv.") for e in d.entries)


def test_seed_every_entry_names_geometry_inputs_and_params():
    d = build_seed_pattern_dictionary()
    for e in d.entries:
        assert len(e.geometry_inputs) >= 1
        assert set(e.geometry_inputs).issubset(set(GEOMETRY_INPUTS))
        assert tuple(e.geometry_inputs) == tuple(sorted(set(e.geometry_inputs)))
        assert len(e.rule_params) >= 1
        assert e.predicate.strip()


def test_seed_approximation_discipline():
    # multi-bar structures are approximate (宽松几何近似 + 置信分); every other
    # family is geometrically precise.
    d = build_seed_pattern_dictionary()
    for e in d.entries:
        if e.family == "multi_bar":
            assert e.approximate is True, e.pattern_id
        else:
            assert e.approximate is False, e.pattern_id


def test_seed_stats_are_all_unavailable_no_fabrication():
    # R10 boundary: the recognizer/replay task has not landed -> honest UNAVAILABLE.
    d = build_seed_pattern_dictionary()
    for e in d.entries:
        assert e.replay_stats is None
        assert e.render_replay_stats() == STATS_UNAVAILABLE


def test_seed_gap_family_full_coverage_with_fill_slots():
    d = build_seed_pattern_dictionary()
    gap = {e.pattern_id: e for e in d.entries if e.family == "gap"}
    assert set(gap) == {
        "pv.gap.common", "pv.gap.breakaway", "pv.gap.runaway", "pv.gap.exhaustion",
    }
    # every gap type carries the reserved 缺口回补统计 slot, all UNAVAILABLE now.
    for e in gap.values():
        assert gap_fill_stats_label(e.gap_fill_stats) == STATS_UNAVAILABLE
    # non-gap entries never carry a gap-fill slot.
    for e in d.entries:
        if e.family != "gap":
            assert e.gap_fill_stats is None


def test_seed_astock_specials_present():
    d = build_seed_pattern_dictionary()
    ids = {e.pattern_id for e in d.entries if e.family == "astock"}
    assert ids == {
        "pv.astock.one_word_board", "pv.astock.t_word_board",
        "pv.astock.limit_up_breakout", "pv.astock.failed_limit_long_upper",
    }


def test_seed_hammer_geometry_is_correct():
    # spot-check geometric correctness (a reviewer verifies a sample): a hammer's
    # lower shadow must be >= 2x its real body and the close must sit high.
    d = build_seed_pattern_dictionary()
    hammer = next(e for e in d.entries if e.pattern_id == "pv.single.hammer")
    assert hammer.rule_params["lower_wick_to_body_min"] >= 2.0
    assert hammer.rule_params["min_close_pos"] >= 0.5
    assert "lower_wick" in hammer.geometry_inputs
    assert "close_pos" in hammer.geometry_inputs


def test_seed_shooting_star_is_bearish_mirror():
    d = build_seed_pattern_dictionary()
    star = next(e for e in d.entries if e.pattern_id == "pv.single.shooting_star")
    assert star.rule_params["upper_wick_to_body_min"] >= 2.0
    assert star.rule_params["max_close_pos"] <= 0.5
    assert "upper_wick" in star.geometry_inputs


# =========================================================================== #
# Catalog guardrail material — digest reproduction + stability + round-trip.    #
# =========================================================================== #
def test_material_id_is_the_reviewed_guardrail_logical_id():
    assert PATTERN_DICTIONARY_MATERIAL_ID == "guardrail.pattern_dictionary"
    assert PATTERN_DICTIONARY_MATERIAL_VERSION == "1"


def test_physical_material_bytes_reproduce_the_reviewed_digest():
    d = build_seed_pattern_dictionary()
    ref, mat = build_pattern_dictionary_material(d)
    assert ref.id == PATTERN_DICTIONARY_MATERIAL_ID
    assert ref.version == "1"
    assert mat.kind == "guardrail"

    assert MATERIAL_FILE.is_file()
    physical = MATERIAL_FILE.read_bytes()
    physical_mat = ResolvedTextMaterial(ref=ref, kind="guardrail", raw_utf8=physical)
    assert catalog_material_digest(physical_mat) == ref.content_digest
    # the physical bytes ARE the canonical material bytes of the reviewed dictionary.
    assert physical == pattern_dictionary_material_bytes(d)


def test_material_digest_is_stable_and_pinned():
    d = build_seed_pattern_dictionary()
    a, _ = build_pattern_dictionary_material(d)
    b, _ = build_pattern_dictionary_material(d)
    assert a.content_digest == b.content_digest  # deterministic
    assert a.content_digest == EXPECTED_MATERIAL_DIGEST  # drift guard


def test_material_bytes_round_trip_back_to_the_reviewed_dictionary():
    d = build_seed_pattern_dictionary()
    raw = pattern_dictionary_material_bytes(d)
    assert pattern_dictionary_from_material_bytes(raw) == d
    # and the physical file round-trips too.
    assert pattern_dictionary_from_material_bytes(MATERIAL_FILE.read_bytes()) == d


# =========================================================================== #
# No-LLM proof (AST + source scan): the recognizer contract is deterministic.   #
# =========================================================================== #
def test_pattern_registry_is_pure_no_llm_no_engine_no_network():
    tree = ast.parse(PATTERN_REGISTRY_SRC.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for mod in imported:
        assert not mod.startswith("financial_analyst"), f"engine import leaked: {mod}"
        assert not mod.startswith("guanlan_v2.datafeed"), f"datafeed import leaked: {mod}"
        for banned in ("httpx", "openai", "requests", "aiohttp", "llm"):
            assert banned not in mod.split("."), f"network/LLM import leaked: {mod}"
