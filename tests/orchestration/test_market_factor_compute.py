# -*- coding: utf-8 -*-
"""Phase 5 · Task 3 — deterministic PIT market-factor compute core + loaders + shield parity.

Written test-first (RED until the compute half of
``guanlan_v2.orchestration.market.factors`` exists). Covers the pure numeric
helpers, the per-factor v1 formulas (①§3), the ①§2 three-state honesty
(OK / DEGRADED / UNAVAILABLE — never zero-fill), PIT future-row refusal, the
today-tape backfill discipline, the 炸板率/晋级率 口径 pins, unit stamps, the
availability-rule versioning, determinism + truncation invariance, the
import-purity boundary, the interim loader windowing, and the market-temp shield
projection parity (monkeypatched cache reads — no network).

Run from repo root: ``pytest tests/orchestration/test_market_factor_compute.py -v``
"""
from __future__ import annotations

import ast
from datetime import date as _date
from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.data.errors import FutureDataRefused
from guanlan_v2.orchestration.market import factors as F
from guanlan_v2.orchestration.market.factors import (
    DEFAULT_AVAILABILITY_RULE,
    DEFAULT_UNIVERSE_REGISTRY_VERSION,
    TAPE_BACKFILLED_IGNORED_BADGE,
    BoardPoolRow,
    BreakCountRow,
    DailyValueRow,
    MarketFactorInputs,
    PanelAvailabilityRule,
    PanelCloseRow,
    TapePoint,
    UpDownRow,
    build_market_factor_set_v1,
    compute_market_factors,
    load_market_factor_inputs,
    market_factor_handler,
    shield_inputs_from_factor_report,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# tiny builders (available_at stamped like the avail-v1 rule: 07:05Z of date)   #
# --------------------------------------------------------------------------- #
def _stamp(d: str) -> datetime:
    y, m, dd = (int(x) for x in d.split("-"))
    return datetime(y, m, dd, 7, 5, tzinfo=UTC)


def _dates(n: int, start: _date = _date(2025, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _updown(dates: list[str], up: int = 60, down: int = 40, total: int = 100) -> tuple[UpDownRow, ...]:
    return tuple(UpDownRow(date=d, up=up, down=down, total=total, available_at=_stamp(d)) for d in dates)


def _daily(dates: list[str], values) -> tuple[DailyValueRow, ...]:
    if not isinstance(values, (list, tuple)):
        values = [values] * len(dates)
    return tuple(DailyValueRow(date=d, value=float(v), available_at=_stamp(d)) for d, v in zip(dates, values))


def _breaks(dates: list[str], pairs) -> tuple[BreakCountRow, ...]:
    return tuple(
        BreakCountRow(date=d, zt=zt, zb=zb, available_at=_stamp(d)) for d, (zt, zb) in zip(dates, pairs)
    )


def _boards(dates: list[str], streak=4, promo=0.3) -> tuple[BoardPoolRow, ...]:
    return tuple(
        BoardPoolRow(date=d, max_streak=streak, promotion_rate=promo, available_at=_stamp(d)) for d in dates
    )


def _panel(dates: list[str], codes: list[str], closes_by_date) -> tuple[PanelCloseRow, ...]:
    rows: list[PanelCloseRow] = []
    for d in dates:
        for code in codes:
            rows.append(PanelCloseRow(date=d, code=code, close=float(closes_by_date(d, code)), available_at=_stamp(d)))
    return tuple(rows)


def _compute(inputs: MarketFactorInputs, as_of: datetime, **kw):
    spec = build_market_factor_set_v1()
    return compute_market_factors(
        inputs,
        spec=spec,
        as_of=as_of,
        clock_mode=kw.pop("clock_mode", "eod"),
        universe_registry_version=kw.pop("universe_registry_version", DEFAULT_UNIVERSE_REGISTRY_VERSION),
        **kw,
    )


def _val(report, fid: str):
    return next(v for v in report.values if v.factor_id == fid)


# --------------------------------------------------------------------------- #
# numeric helpers — hand-computed                                              #
# --------------------------------------------------------------------------- #
def test_sma_hand():
    out = F._sma([1.0, 2.0, 3.0, 4.0], 2)
    assert out[0] is None
    assert out[1] == pytest.approx(1.5)
    assert out[2] == pytest.approx(2.5)
    assert out[3] == pytest.approx(3.5)


def test_ema_hand():
    # span=3 → alpha=0.5; ema[0]=x0, ema[i]=0.5*x + 0.5*prev
    out = F._ema([2.0, 4.0, 6.0], 3)
    assert out[0] == pytest.approx(2.0)
    assert out[1] == pytest.approx(3.0)
    assert out[2] == pytest.approx(4.5)


def test_slope_hand():
    ma = [None, 1.0, 2.0, 3.0, 4.0]
    out = F._slope(ma, 2)
    assert out[0] is None and out[1] is None and out[2] is None
    assert out[3] == pytest.approx((3.0 - 1.0) / 2)
    assert out[4] == pytest.approx((4.0 - 2.0) / 2)


def test_zscore_trailing_hand():
    xs = [1.0, 2.0, 3.0, 4.0]
    out = F._zscore_trailing(xs, 3)
    assert out[0] is None and out[1] is None
    # window [1,2,3] mean 2 pstdev sqrt(2/3); z of 3 = (3-2)/sqrt(2/3)
    import math

    assert out[2] == pytest.approx((3.0 - 2.0) / math.sqrt(2.0 / 3.0))
    assert out[3] == pytest.approx((4.0 - 3.0) / math.sqrt(2.0 / 3.0))


def test_zscore_zero_variance_is_zero():
    out = F._zscore_trailing([5.0, 5.0, 5.0, 5.0], 3)
    assert out[2] == 0.0 and out[3] == 0.0


def test_percentile_trailing_hand():
    xs = [10.0, 20.0, 30.0]
    out = F._percentile_trailing(xs, 3)
    assert out[0] is None and out[1] is None
    # rank pct of 30 within [10,20,30] = 3/3 = 1.0
    assert out[2] == pytest.approx(1.0)


def test_compounded_return_hand():
    out = F._compounded_return([0.1, 0.1], 2)
    assert out[0] is None
    assert out[1] == pytest.approx(1.1 * 1.1 - 1.0)


# --------------------------------------------------------------------------- #
# breadth.ad_ratio — OK / DEGRADED / short-UNAVAILABLE                          #
# --------------------------------------------------------------------------- #
def test_ad_ratio_ok_full_window():
    dates = _dates(90)
    inp = MarketFactorInputs(updown=_updown(dates))  # ad = (60-40)/100 = 0.2 constant
    rep = _compute(inp, _stamp(dates[-1]))
    v = _val(rep, "breadth.ad_ratio")
    assert v.status == "OK"
    assert v.coverage == 1.0
    assert v.value == pytest.approx(0.2)
    assert v.series[-1].aux["ma5"] == pytest.approx(0.2)
    assert v.series[-1].aux["ma20"] == pytest.approx(0.2)
    assert v.series[-1].aux["slope"] == pytest.approx(0.0)
    assert set(v.series[-1].aux) == {"ma5", "ma20", "slope"}
    assert v.reason is None
    assert len(v.series) == 60  # D1 cap


def test_ad_ratio_degraded_partial_coverage_exact_fraction():
    dates = _dates(40)  # >= min 25, < warmup(24)+60
    inp = MarketFactorInputs(updown=_updown(dates))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.ad_ratio")
    assert v.status == "DEGRADED"
    n_out = 40 - 24
    assert v.coverage == pytest.approx(n_out / 60)
    assert v.reason is not None and "coverage" in v.reason.lower()
    assert v.value == pytest.approx(0.2)


def test_ad_ratio_short_history_unavailable_names_input_and_lengths():
    dates = _dates(20)  # < min 25
    inp = MarketFactorInputs(updown=_updown(dates))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.ad_ratio")
    assert v.status == "UNAVAILABLE"
    assert v.value is None and v.series == () and v.coverage == 0.0
    assert v.reason is not None
    assert "updown" in v.reason and "20" in v.reason and "25" in v.reason


def test_ad_ratio_none_input_unavailable():
    v = _val(_compute(MarketFactorInputs(), _stamp("2025-06-01")), "breadth.ad_ratio")
    assert v.status == "UNAVAILABLE"
    assert "updown" in v.reason


# --------------------------------------------------------------------------- #
# breadth.limit_up_ema / vol.rv / breadth.nhnl                                  #
# --------------------------------------------------------------------------- #
def test_limit_up_ema_ok():
    dates = _dates(80)
    inp = MarketFactorInputs(limit_up_total=_daily(dates, 30.0))  # constant 30 → ema 30
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.limit_up_ema")
    assert v.status == "OK"
    assert v.value == pytest.approx(30.0)
    assert v.series[-1].aux == {"ema3": pytest.approx(30.0)}


def test_vol_rv_ok_and_ratio():
    import math

    dates = _dates(90)
    inp = MarketFactorInputs(closes_index=_daily(dates, 0.01))  # constant daily return 0.01
    v = _val(_compute(inp, _stamp(dates[-1])), "vol.rv")
    assert v.status == "OK"
    rv20 = math.sqrt(20 * 0.01 ** 2) * math.sqrt(250)
    assert v.value == pytest.approx(rv20)
    assert v.series[-1].aux["rv_ratio"] == pytest.approx(0.5)  # RV5/RV20 = sqrt(5/20)


def test_nhnl_ok_all_new_highs():
    dates = _dates(130)
    codes = [f"c{i}" for i in range(6)]
    # strictly increasing per code → every session a new high, no new low
    idx = {d: i for i, d in enumerate(dates)}
    inp = MarketFactorInputs(closes_panel=_panel(dates, codes, lambda d, c: 10.0 + idx[d]))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.nhnl")
    assert v.status == "OK"
    assert v.value == pytest.approx(1.0)
    assert v.series[-1].aux["nhnl20"] == pytest.approx(1.0)
    assert v.series[-1].aux["nhnl60"] == pytest.approx(1.0)


def test_nhnl_short_history_unavailable():
    dates = _dates(40)  # < min 61
    codes = ["c0", "c1"]
    inp = MarketFactorInputs(closes_panel=_panel(dates, codes, lambda d, c: 1.0))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.nhnl")
    assert v.status == "UNAVAILABLE"
    assert "closes_panel" in v.reason


# --------------------------------------------------------------------------- #
# breadth.break_rate — zb/(zt+zb) 口径, 空池 gap, today-tape                     #
# --------------------------------------------------------------------------- #
def test_break_rate_uses_zb_over_zt_plus_zb():
    dates = _dates(10)
    # each session zt=3, zb=1 → break_rate = 1/4 = 0.25
    inp = MarketFactorInputs(break_counts=_breaks(dates, [(3, 1)] * 10))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.break_rate")
    assert v.status in ("OK", "DEGRADED")
    assert v.value == pytest.approx(0.25)


def test_break_rate_empty_pool_is_gap_not_zero():
    dates = _dates(10)
    pairs = [(3, 1)] * 8 + [(0, 0), (3, 1)]  # one 空池 session in the middle-ish
    inp = MarketFactorInputs(break_counts=_breaks(dates, pairs))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.break_rate")
    # 9 valid points (the 空池 session leaves a gap, never 0.0)
    assert v.n_days == 9
    assert all(p.value != 0.0 for p in v.series)
    assert v.value == pytest.approx(0.25)


def test_break_rate_koujing_pin_only_zb_over_zt_plus_zb_moves_it():
    """A fixture supplying zb/(zt+zb), a distinct promotion_rate, and open-board
    numbers proves only the 炸板率 口径 (zb/(zt+zb)) moves breadth.break_rate."""
    dates = _dates(10)
    inp = MarketFactorInputs(
        break_counts=_breaks(dates, [(3, 1)] * 10),  # break_rate = 0.25
        board_pools=_boards(dates, streak=5, promo=0.9),  # promotion_rate = 0.9 (distinct)
    )
    rep = _compute(inp, _stamp(dates[-1]))
    br = _val(rep, "breadth.break_rate")
    pr = _val(rep, "breadth.promotion_rate")
    assert br.value == pytest.approx(0.25)  # 炸板率
    assert pr.value == pytest.approx(0.9)  # 晋级率 — distinct 口径
    assert br.value != pr.value


def test_break_rate_none_input_unavailable():
    v = _val(_compute(MarketFactorInputs(), _stamp("2025-06-01")), "breadth.break_rate")
    assert v.status == "UNAVAILABLE"
    assert "break_counts" in v.reason


# --------------------------------------------------------------------------- #
# breadth.ladder_height / breadth.promotion_rate (standing R4 DEGRADED note)     #
# --------------------------------------------------------------------------- #
def test_ladder_height_passthrough():
    dates = _dates(80)
    inp = MarketFactorInputs(board_pools=_boards(dates, streak=4, promo=0.3))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.ladder_height")
    assert v.status == "OK"
    assert v.value == pytest.approx(4.0)


def test_promotion_rate_is_standing_degraded_with_koujing_reason():
    dates = _dates(80)
    inp = MarketFactorInputs(board_pools=_boards(dates, streak=4, promo=0.3))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.promotion_rate")
    assert v.status == "DEGRADED"  # never OK — standing 口径 note (R4)
    assert v.value == pytest.approx(0.3)
    assert v.reason is not None and "limit_days>=2" in v.reason


def test_promotion_rate_none_promo_session_is_gap():
    dates = _dates(10)
    rows = list(_boards(dates, streak=3, promo=0.3))
    rows[5] = BoardPoolRow(date=dates[5], max_streak=3, promotion_rate=None, available_at=_stamp(dates[5]))
    inp = MarketFactorInputs(board_pools=tuple(rows))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.promotion_rate")
    assert v.n_days == 9  # the None-promotion session leaves a gap


# --------------------------------------------------------------------------- #
# temp.astock — verbatim, OK / DEGRADED accreting                              #
# --------------------------------------------------------------------------- #
def test_temp_astock_ok_verbatim():
    dates = _dates(60)
    vals = [40.0 + i for i in range(60)]
    inp = MarketFactorInputs(astock_temp=_daily(dates, vals))
    v = _val(_compute(inp, _stamp(dates[-1])), "temp.astock")
    assert v.status == "OK"
    assert v.value == pytest.approx(vals[-1])


def test_temp_astock_degraded_when_accreting():
    dates = _dates(40)  # >= min 20 but < 60
    inp = MarketFactorInputs(astock_temp=_daily(dates, 50.0))
    v = _val(_compute(inp, _stamp(dates[-1])), "temp.astock")
    assert v.status == "DEGRADED"
    assert v.coverage == pytest.approx(40 / 60)


# --------------------------------------------------------------------------- #
# rot.* + val.pct — always UNAVAILABLE in v1 (archive/upstream deferred)         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fid",
    ["rot.hhi", "rot.diffusion", "rot.dispersion", "rot.ladder_theme",
     "rot.leader_persist", "rot.flow_streak", "rot.theme_burst", "val.pct"],
)
def test_deferred_factors_unavailable(fid):
    v = _val(_compute(MarketFactorInputs(), _stamp("2025-06-01")), fid)
    assert v.status == "UNAVAILABLE"
    assert v.value is None and v.series == () and v.reason


def test_val_pct_reason_cites_r5():
    v = _val(_compute(MarketFactorInputs(), _stamp("2025-06-01")), "val.pct")
    assert "upstream" in v.reason.lower() or "per-stock" in v.reason.lower()


# --------------------------------------------------------------------------- #
# flow.* — unit stamp (元→亿 / 亿 pass-through recorded in params)               #
# --------------------------------------------------------------------------- #
def test_flow_factors_record_unit_yi_in_params():
    dates = _dates(300)
    inp = MarketFactorInputs(
        north_net=_daily(dates, [float((i % 7) - 3) for i in range(300)]),
        main_net=_daily(dates, [float((i % 5) - 2) for i in range(300)]),
    )
    rep = _compute(inp, _stamp(dates[-1]))
    nb = _val(rep, "flow.northbound")
    mp = _val(rep, "flow.main_pct")
    assert nb.status in ("OK", "DEGRADED") and nb.params.get("unit") == "yi"
    assert mp.status in ("OK", "DEGRADED") and mp.params.get("unit") == "yi"


def test_main_net_yuan_to_yi_helper():
    assert F._yuan_to_yi(3.0e10) == pytest.approx(300.0)  # 元 → 亿


def test_flow_factors_none_input_unavailable():
    rep = _compute(MarketFactorInputs(), _stamp("2025-06-01"))
    assert _val(rep, "flow.northbound").status == "UNAVAILABLE"
    assert _val(rep, "flow.main_pct").status == "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# breadth.divergence — long-series status + determinism                        #
# --------------------------------------------------------------------------- #
def test_divergence_short_history_unavailable():
    dates = _dates(100)  # < min 271
    inp = MarketFactorInputs(updown=_updown(dates), closes_index=_daily(dates, 0.01))
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.divergence")
    assert v.status == "UNAVAILABLE"


def test_divergence_long_series_status_and_finite():
    dates = _dates(350)
    ups = [50 + ((i % 11) - 5) for i in range(350)]
    inp = MarketFactorInputs(
        updown=tuple(UpDownRow(date=d, up=u, down=100 - u, total=100, available_at=_stamp(d)) for d, u in zip(dates, ups)),
        closes_index=_daily(dates, [0.01 * ((i % 7) - 3) for i in range(350)]),
    )
    v = _val(_compute(inp, _stamp(dates[-1])), "breadth.divergence")
    assert v.status in ("OK", "DEGRADED")
    assert v.value is not None
    assert set(v.series[-1].aux) == {"z_index", "z_breadth"}


# --------------------------------------------------------------------------- #
# today_tape backfill discipline                                              #
# --------------------------------------------------------------------------- #
def _break_inputs_with_tape(n: int, *, backfilled: bool, foreign: bool, br: float = 0.5):
    dates = _dates(n + 1)
    hist = dates[:n]
    session = dates[n]
    tape_date = dates[n - 1] if foreign else session
    tp = TapePoint(
        date=tape_date, zt_count=3, zb_count=3, max_streak=4, break_rate=br,
        promotion_rate=0.4, backfilled=backfilled, available_at=_stamp(session),
    )
    inp = MarketFactorInputs(break_counts=_breaks(hist, [(3, 1)] * n), today_tape=tp)
    return inp, _stamp(session), session


def test_today_tape_same_day_point_included():
    inp, as_of, session = _break_inputs_with_tape(6, backfilled=False, foreign=False, br=0.5)
    rep = _compute(inp, as_of)
    v = _val(rep, "breadth.break_rate")
    assert v.series[-1].date == session
    assert v.value == pytest.approx(0.5)  # today's break_rate
    assert TAPE_BACKFILLED_IGNORED_BADGE not in rep.badges


def test_today_tape_backfilled_ignored_with_badge():
    inp, as_of, session = _break_inputs_with_tape(6, backfilled=True, foreign=False)
    rep = _compute(inp, as_of)
    v = _val(rep, "breadth.break_rate")
    assert all(p.date != session for p in v.series)  # today point excluded
    assert TAPE_BACKFILLED_IGNORED_BADGE in rep.badges


def test_today_tape_foreign_date_ignored_with_badge():
    inp, as_of, session = _break_inputs_with_tape(6, backfilled=False, foreign=True)
    rep = _compute(inp, as_of)
    v = _val(rep, "breadth.break_rate")
    assert all(p.date != session for p in v.series)
    assert TAPE_BACKFILLED_IGNORED_BADGE in rep.badges


# --------------------------------------------------------------------------- #
# PIT future-row refusal                                                       #
# --------------------------------------------------------------------------- #
def test_future_row_raises_future_data_refused():
    dates = _dates(30)
    rows = list(_updown(dates))
    # last row leaks from the future relative to as_of
    as_of = _stamp(dates[-2])
    inp = MarketFactorInputs(updown=tuple(rows))
    with pytest.raises(FutureDataRefused):
        _compute(inp, as_of)


def test_future_row_in_today_tape_refused():
    dates = _dates(30)
    session = dates[-1]
    tp = TapePoint(
        date=session, zt_count=1, zb_count=1, max_streak=1, break_rate=0.5,
        promotion_rate=None, backfilled=False,
        available_at=_stamp(session) + timedelta(days=1),  # future
    )
    inp = MarketFactorInputs(break_counts=_breaks(dates, [(3, 1)] * 30), today_tape=tp)
    with pytest.raises(FutureDataRefused):
        _compute(inp, _stamp(session))


# --------------------------------------------------------------------------- #
# invariants: determinism / truncation invariance / removing-history            #
# --------------------------------------------------------------------------- #
def test_determinism_two_runs_bit_identical():
    dates = _dates(90)
    inp = MarketFactorInputs(updown=_updown(dates))
    a = _compute(inp, _stamp(dates[-1]))
    b = _compute(inp, _stamp(dates[-1]))
    assert a.content_digest == b.content_digest


def test_truncation_invariance_window_at_T_equals_shorter_input():
    long_dates = _dates(90)
    T = _stamp(long_dates[59])
    long_inp = MarketFactorInputs(updown=_updown(long_dates))
    windowed = F._window_inputs(long_inp, T)
    short_inp = MarketFactorInputs(updown=_updown(long_dates[:60]))
    ra = _compute(windowed, T)
    rb = _compute(short_inp, T)
    assert ra.content_digest == rb.content_digest


def test_removing_history_flips_to_unavailable_never_zero_fill():
    dates = _dates(90)
    ok = _val(_compute(MarketFactorInputs(updown=_updown(dates)), _stamp(dates[-1])), "breadth.ad_ratio")
    assert ok.status == "OK"
    short = _val(_compute(MarketFactorInputs(updown=_updown(dates[:10])), _stamp(dates[9])), "breadth.ad_ratio")
    assert short.status == "UNAVAILABLE"
    assert short.series == () and short.value is None  # never a zero-filled series


def test_coverage_summary_and_missing_features_consistent():
    dates = _dates(90)
    rep = _compute(MarketFactorInputs(updown=_updown(dates)), _stamp(dates[-1]))
    assert rep.coverage_summary.n_ok >= 1
    assert set(rep.missing_features) == {v.factor_id for v in rep.values if v.status == "UNAVAILABLE"}
    assert set(rep.feature_vector).isdisjoint(rep.missing_features)


# --------------------------------------------------------------------------- #
# availability rule stamped + versioned                                        #
# --------------------------------------------------------------------------- #
def test_availability_rule_stamped_in_every_value_params():
    dates = _dates(90)
    rep = _compute(MarketFactorInputs(updown=_updown(dates)), _stamp(dates[-1]))
    assert all(v.params.get("availability_rule") == "avail-v1" for v in rep.values)


def test_rule_version_change_moves_report_digest():
    dates = _dates(90)
    inp = MarketFactorInputs(updown=_updown(dates))
    r1 = _compute(inp, _stamp(dates[-1]))
    r2 = _compute(inp, _stamp(dates[-1]), rule=PanelAvailabilityRule(rule_version="avail-v2", session_close_utc="07:05"))
    assert r1.content_digest != r2.content_digest
    assert all(v.params.get("availability_rule") == "avail-v2" for v in r2.values)


# --------------------------------------------------------------------------- #
# data_snapshot_hash binds the windowed inputs                                 #
# --------------------------------------------------------------------------- #
def test_data_snapshot_hash_binds_inputs():
    dates = _dates(90)
    inp = MarketFactorInputs(updown=_updown(dates))
    rep = _compute(inp, _stamp(dates[-1]))
    assert rep.data_snapshot_hash == inp.semantic_digest()


# --------------------------------------------------------------------------- #
# handler == compute bit-for-bit                                              #
# --------------------------------------------------------------------------- #
def test_handler_equals_compute_bit_for_bit():
    dates = _dates(90)
    inp = MarketFactorInputs(updown=_updown(dates))
    as_of = _stamp(dates[-1])
    spec = build_market_factor_set_v1()
    h = market_factor_handler(spec=spec, inputs=inp, as_of=as_of)
    c = compute_market_factors(
        inp, spec=spec, as_of=as_of, clock_mode="eod",
        universe_registry_version=DEFAULT_UNIVERSE_REGISTRY_VERSION,
    )
    assert h.content_digest == c.content_digest


# --------------------------------------------------------------------------- #
# compute-core import purity (no screen/datafeed/macro/strategy at module top)  #
# --------------------------------------------------------------------------- #
def test_compute_core_import_purity():
    src = ast.parse(open(F.__file__, encoding="utf-8").read())
    forbidden = ("guanlan_v2.screen", "guanlan_v2.datafeed", "guanlan_v2.macro", "guanlan_v2.strategy")
    for node in src.body:  # module-level statements only
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [node.module]
        for m in mods:
            assert not any(m == f or m.startswith(f + ".") for f in forbidden), \
                f"compute core imports {m} at module level (must be lazy in the loader)"


# --------------------------------------------------------------------------- #
# interim loader — windowing + honest None steady state                        #
# --------------------------------------------------------------------------- #
def test_loader_none_steady_state(monkeypatch):
    monkeypatch.setattr(F, "_load_astock_temp_rows", lambda as_of, rule: None)
    monkeypatch.setattr(F, "_load_closes_index_rows", lambda as_of, rule: None)
    monkeypatch.setattr(F, "_load_today_tape", lambda as_of, rule: None)
    monkeypatch.setattr(F, "_load_panel_rows", lambda provider_uri, end, as_of, rule: (None, None, None))
    inp = load_market_factor_inputs(provider_uri="x", end="2026-07-16", as_of=_stamp("2026-07-16"))
    for name in (
        "updown", "closes_panel", "limit_up_total", "closes_index", "astock_temp",
        "break_counts", "board_pools", "north_net", "main_net", "sector_flows",
        "index_valuation", "concept_membership", "industry_returns",
        "limit_reasons", "sector_leaders", "universe_versions", "today_tape",
    ):
        assert getattr(inp, name) is None


def test_loader_windows_series_to_as_of(monkeypatch):
    dates = _dates(10)
    as_of = _stamp(dates[4])  # keep only first 5
    rows = _daily(dates, 50.0)
    monkeypatch.setattr(F, "_load_astock_temp_rows", lambda a, r: rows)
    monkeypatch.setattr(F, "_load_closes_index_rows", lambda a, r: None)
    monkeypatch.setattr(F, "_load_today_tape", lambda a, r: None)
    monkeypatch.setattr(F, "_load_panel_rows", lambda p, e, a, r: (None, None, None))
    inp = load_market_factor_inputs(provider_uri="x", end=dates[4], as_of=as_of)
    assert inp.astock_temp is not None
    assert all(row.available_at <= as_of for row in inp.astock_temp)
    assert len(inp.astock_temp) == 5


# --------------------------------------------------------------------------- #
# shield projection parity against build_market_temp (monkeypatched, no network) #
# --------------------------------------------------------------------------- #
def test_shield_projection_parity(monkeypatch):
    import guanlan_v2.datafeed.market_tape as mt_mod
    import guanlan_v2.datafeed.sentiment as sent_mod
    import guanlan_v2.fundflow.pulse as ff_mod
    import guanlan_v2.macro.pulse as pulse_mod
    from guanlan_v2.screen.market_temp import build_market_temp

    monkeypatch.setattr(
        mt_mod, "read_tape",
        lambda *a, **k: {"warming": False, "derived": {"break_rate": 0.3125, "zt_count": 2, "zb_count": 1,
                                                       "promotion_rate": None},
                         "freshness": {"overall_age_s": 1}},
    )
    monkeypatch.setattr(pulse_mod, "_read_snapshots", lambda *a, **k: [{"astock_temp": 42.0, "ts": "2026-07-16T15:00:00", "temps": {}}])
    monkeypatch.setattr(ff_mod, "_load_live_cache", lambda *a, **k: None)
    monkeypatch.setattr(ff_mod, "_trigger_live_refresh", lambda *a, **k: None)
    monkeypatch.setattr(sent_mod, "latest_market", lambda *a, **k: {"market_read": None, "market_tilt": None, "as_of": None})

    mt = build_market_temp()
    assert mt["board"]["break_rate"] == 0.3125
    assert mt["global"]["astock_temp"] == 42.0
    assert mt["flow"] is None

    # a report computed from the SAME snapshot: break_rate latest = today-tape 0.3125,
    # temp.astock latest = 42.0
    dates = _dates(26)
    hist, session = dates[:25], dates[25]
    tp = TapePoint(date=session, zt_count=2, zb_count=1, max_streak=3, break_rate=0.3125,
                   promotion_rate=None, backfilled=False, available_at=_stamp(session))
    inp = MarketFactorInputs(
        break_counts=_breaks(hist, [(3, 1)] * 25),
        astock_temp=_daily(dates, [30.0 + i for i in range(25)] + [42.0]),
        today_tape=tp,
    )
    rep = _compute(inp, _stamp(session))
    proj = shield_inputs_from_factor_report(rep)
    assert proj == {"astock_temp": 42.0, "break_rate": 0.3125, "main_net_yi": None}
    # bit-for-bit vs the shield's own board/global block values
    assert proj["break_rate"] == mt["board"]["break_rate"]
    assert proj["astock_temp"] == mt["global"]["astock_temp"]


def test_shield_projection_none_when_factors_unavailable():
    # v1 steady state: no break_counts / no astock history → both UNAVAILABLE → None
    rep = _compute(MarketFactorInputs(), _stamp("2025-06-01"))
    proj = shield_inputs_from_factor_report(rep)
    assert proj == {"astock_temp": None, "break_rate": None, "main_net_yi": None}
