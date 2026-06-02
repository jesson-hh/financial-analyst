"""Tests for watch/outcome.py — 推荐复盘闭环 (C, self-improving).

C1 scorer: T+1/T+5 forward returns + verdict (reuses backtest.records._action_verdict),
hit-rate aggregation, OutcomeScorer (stub-loader), parquet backfill (idempotent).

Pure functions (score_one / compute_hitrate) use synthetic daily frames — no loader.
OutcomeScorer / backfill use a tiny stub loader mirroring test_watch_signals._StubLoader.
"""
from __future__ import annotations

import pandas as pd

from financial_analyst.watch.outcome import (
    _watch_action_to_canon,
    backfill_outcomes,
    compute_hitrate,
    format_hitrate_context,
    load_outcomes,
    score_one,
    OutcomeScorer,
)


def _daily(closes, highs=None, lows=None, start="2026-05-20"):
    """Build a daily frame: trade_date/open/high/low/close (high/low default ≈ close)."""
    n = len(closes)
    dates = pd.date_range(start, periods=n).strftime("%Y-%m-%d").tolist()
    highs = highs if highs is not None else [c * 1.001 for c in closes]
    lows = lows if lows is not None else [c * 0.999 for c in closes]
    return pd.DataFrame({
        "trade_date": dates,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
    })


# ==========================================================================
# _watch_action_to_canon — map 5 watch actions → 4 canonical verdict actions
# ==========================================================================
def test_action_canon_mapping():
    assert _watch_action_to_canon("buy") == "buy"
    assert _watch_action_to_canon("add") == "buy"
    assert _watch_action_to_canon("hold") == "hold"
    assert _watch_action_to_canon("reduce") == "sell"
    assert _watch_action_to_canon("sell") == "sell"


# ==========================================================================
# score_one — buy that rises ≥2% within 5d → "correct"
# ==========================================================================
def test_score_one_buy_correct():
    # rec on 2026-05-20 (base close 10.0); fwd5 closes climb to 11.0 → +10%.
    closes = [10.0, 10.2, 10.4, 10.6, 10.8, 11.0]            # idx0=base, 1..5 fwd
    df = _daily(closes)
    rec = {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
           "action": "buy", "target_price": 0.0, "stop_loss": 0.0}
    o = score_one(rec, df)
    assert o is not None
    assert o["base_close"] == 10.0
    assert round(o["return_t1"], 4) == round(10.2 / 10.0 - 1, 4)
    assert round(o["return_t5"], 4) == round(11.0 / 10.0 - 1, 4)
    assert o["n_fwd"] == 5
    assert o["verdict"] == "correct"


# ==========================================================================
# score_one — sell that drops → "correct"
# ==========================================================================
def test_score_one_sell_correct():
    closes = [10.0, 9.8, 9.6, 9.4, 9.2, 9.0]                 # falls 10% over 5d
    df = _daily(closes)
    rec = {"ts": "2026-05-20 14:00:00", "code": "SZ002594", "trigger_kind": "vol_regime",
           "action": "sell", "target_price": 0.0, "stop_loss": 0.0}
    o = score_one(rec, df)
    assert o["verdict"] == "correct"
    assert o["return_t5"] < 0


# ==========================================================================
# score_one — buy with stop breached intraday → "wrong"
# ==========================================================================
def test_score_one_buy_hit_stop_wrong():
    closes = [10.0, 9.6, 9.5, 9.7, 9.8, 9.9]
    lows = [9.99, 9.2, 9.4, 9.6, 9.7, 9.8]                   # day1 low 9.2 ≤ stop 9.5
    df = _daily(closes, lows=lows)
    rec = {"ts": "2026-05-20 09:45:00", "code": "SH600000", "trigger_kind": "breakout_high",
           "action": "buy", "target_price": 0.0, "stop_loss": 9.5}
    o = score_one(rec, df)
    assert o["hit_stop"] is True
    assert o["verdict"] == "wrong"


# ==========================================================================
# score_one — buy reaches target high within 5d → hit_target True, correct
# ==========================================================================
def test_score_one_buy_hit_target():
    closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
    highs = [10.05, 10.2, 11.2, 10.4, 10.5, 10.6]           # day2 high 11.2 ≥ target 11.0
    df = _daily(closes, highs=highs)
    rec = {"ts": "2026-05-20 10:00:00", "code": "SH600519", "trigger_kind": "breakout_high",
           "action": "buy", "target_price": 11.0, "stop_loss": 0.0}
    o = score_one(rec, df)
    assert o["hit_target"] is True
    assert o["verdict"] == "correct"


# ==========================================================================
# score_one — fewer than 5 forward bars → "pending" (not yet mature)
# ==========================================================================
def test_score_one_pending_when_immature():
    closes = [10.0, 10.1, 10.2]                              # only 2 fwd bars
    df = _daily(closes)
    rec = {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
           "action": "buy", "target_price": 0.0, "stop_loss": 0.0}
    o = score_one(rec, df)
    assert o["verdict"] == "pending"
    assert o["n_fwd"] == 2
    assert o["return_t1"] is not None
    assert o["return_t5"] is None


# ==========================================================================
# score_one — rec date not in daily frame → None
# ==========================================================================
def test_score_one_base_day_missing_returns_none():
    df = _daily([10.0, 10.1, 10.2, 10.3, 10.4, 10.5], start="2026-06-01")
    rec = {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
           "action": "buy", "target_price": 0.0, "stop_loss": 0.0}
    assert score_one(rec, df) is None


# ==========================================================================
# compute_hitrate — overall + per-trigger + per-action breakdown
# ==========================================================================
def test_compute_hitrate_breakdown():
    rows = [
        {"trigger_kind": "breakout_high", "action": "buy", "verdict": "correct",
         "return_t1": 0.01, "return_t5": 0.05},
        {"trigger_kind": "breakout_high", "action": "buy", "verdict": "wrong",
         "return_t1": -0.02, "return_t5": -0.06},
        {"trigger_kind": "vol_regime", "action": "reduce", "verdict": "correct",
         "return_t1": -0.01, "return_t5": -0.04},
        {"trigger_kind": "vol_regime", "action": "reduce", "verdict": "pending",
         "return_t1": None, "return_t5": None},                # excluded from stats
    ]
    h = compute_hitrate(pd.DataFrame(rows))
    assert h["overall"]["n"] == 3                              # pending excluded
    assert h["overall"]["correct"] == 2
    assert h["overall"]["wrong"] == 1
    assert round(h["overall"]["win_rate"], 4) == round(2 / 3, 4)
    assert h["by_trigger"]["breakout_high"]["n"] == 2
    assert h["by_trigger"]["breakout_high"]["correct"] == 1
    assert h["by_trigger"]["vol_regime"]["n"] == 1            # the pending one dropped
    assert h["by_action"]["buy"]["n"] == 2


def test_compute_hitrate_empty_is_safe():
    h = compute_hitrate(pd.DataFrame(columns=["trigger_kind", "action", "verdict",
                                              "return_t1", "return_t5"]))
    assert h["overall"]["n"] == 0
    assert h["overall"]["win_rate"] == 0.0
    assert h["by_trigger"] == {}


# ==========================================================================
# OutcomeScorer.score_recs — stub loader, mixed mature/immature
# ==========================================================================
class _StubLoader:
    """fetch_quote(day)→ per-code OHLC frame. Codes map to canned closes."""

    def __init__(self, frames):
        self._frames = frames                                # {code: DataFrame}

    def fetch_quote(self, code, start, end, freq="day"):
        return self._frames.get(code, pd.DataFrame())


def test_outcome_scorer_scores_each_rec():
    up = _daily([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])
    down = _daily([20.0, 19.6, 19.2, 18.8, 18.4, 18.0])
    loader = _StubLoader({"SH600519": up, "SZ002594": down})
    recs = pd.DataFrame([
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "target_price": 0.0, "stop_loss": 0.0},
        {"ts": "2026-05-20 14:00:00", "code": "SZ002594", "trigger_kind": "vol_regime",
         "action": "sell", "target_price": 0.0, "stop_loss": 0.0},
    ])
    out = OutcomeScorer(loader=loader).score_recs(recs)
    assert len(out) == 2
    by_code = {r["code"]: r for r in out.to_dict("records")}
    assert by_code["SH600519"]["verdict"] == "correct"
    assert by_code["SZ002594"]["verdict"] == "correct"


# ==========================================================================
# backfill_outcomes — writes parquet, idempotent, upgrades pending→scored
# ==========================================================================
def _write_recs(tmp_path, rows):
    from financial_analyst.watch.store import RECS_COLUMNS
    df = pd.DataFrame(rows)
    for c in RECS_COLUMNS:
        if c not in df.columns:
            df[c] = "" if c in ("reason", "user_action", "user_action_ts") else 0.0
    p = tmp_path / "watch_recommendations.parquet"
    df[RECS_COLUMNS].to_parquet(p, index=False)
    return p


def test_backfill_writes_and_is_idempotent(tmp_path):
    up = _daily([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])
    loader = _StubLoader({"SH600519": up})
    recs_p = _write_recs(tmp_path, [
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "target_price": 0.0, "stop_loss": 0.0, "confidence": 0.8},
    ])
    out_p = tmp_path / "watch_rec_outcomes.parquet"

    df1 = backfill_outcomes(recs_path=recs_p, out_path=out_p, loader=loader)
    assert len(df1) == 1
    assert df1.iloc[0]["verdict"] == "correct"
    assert out_p.exists()

    # re-run: same key, no duplicate row.
    df2 = backfill_outcomes(recs_path=recs_p, out_path=out_p, loader=loader)
    assert len(df2) == 1

    loaded = load_outcomes(out_p)
    assert len(loaded) == 1
    assert loaded.iloc[0]["code"] == "SH600519"


def test_backfill_pending_then_matures(tmp_path):
    # First pass: only 2 fwd bars → pending. Second pass: 5 fwd bars → correct (re-scored).
    recs_p = _write_recs(tmp_path, [
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "target_price": 0.0, "stop_loss": 0.0, "confidence": 0.8},
    ])
    out_p = tmp_path / "watch_rec_outcomes.parquet"

    immature = _StubLoader({"SH600519": _daily([10.0, 10.1, 10.2])})
    df1 = backfill_outcomes(recs_path=recs_p, out_path=out_p, loader=immature)
    assert df1.iloc[0]["verdict"] == "pending"

    mature = _StubLoader({"SH600519": _daily([10.0, 10.2, 10.4, 10.6, 10.8, 11.0])})
    df2 = backfill_outcomes(recs_path=recs_p, out_path=out_p, loader=mature)
    assert len(df2) == 1                                       # still one row
    assert df2.iloc[0]["verdict"] == "correct"                # pending upgraded


def test_backfill_missing_recs_returns_empty(tmp_path):
    out_p = tmp_path / "watch_rec_outcomes.parquet"
    df = backfill_outcomes(recs_path=tmp_path / "nope.parquet", out_path=out_p,
                           loader=_StubLoader({}))
    assert len(df) == 0


# ==========================================================================
# format_hitrate_context (① 回灌 advisor) — compact track-record block for prompt
# ==========================================================================
def _hitrate_dict():
    return {
        "overall": {"n": 12, "correct": 7, "partial": 1, "wrong": 4, "win_rate": 0.5833,
                    "avg_return_t1": 0.004, "avg_return_t5": 0.012},
        "by_trigger": {
            "breakout_high": {"n": 5, "correct": 1, "partial": 0, "wrong": 4, "win_rate": 0.2,
                              "avg_return_t1": -0.006, "avg_return_t5": -0.021},
            "vol_regime": {"n": 7, "correct": 6, "partial": 1, "wrong": 0, "win_rate": 0.857,
                           "avg_return_t1": 0.011, "avg_return_t5": 0.035},
        },
        "by_action": {},
    }


def test_format_hitrate_context_includes_trigger_and_global():
    s = format_hitrate_context(_hitrate_dict(), "breakout_high")
    assert "历史推荐战绩" in s
    assert "本触发 breakout_high" in s
    assert "20%" in s            # breakout win_rate 0.2 → 20%
    assert "全局" in s
    assert "58%" in s            # overall win_rate 0.5833 → 58%


def test_format_hitrate_context_global_only_when_trigger_absent():
    s = format_hitrate_context(_hitrate_dict(), "news")   # not in by_trigger
    assert "本触发" not in s
    assert "全局" in s


def test_format_hitrate_context_empty_returns_blank():
    assert format_hitrate_context({}, "breakout_high") == ""
    assert format_hitrate_context(None, "breakout_high") == ""
    # zero track record (overall n=0) → no injection
    empty = {"overall": {"n": 0, "win_rate": 0.0}, "by_trigger": {}, "by_action": {}}
    assert format_hitrate_context(empty, "breakout_high") == ""
