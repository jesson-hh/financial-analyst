import json
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from financial_analyst.dream.outcome_tracker import OutcomeTracker, Outcome, _action_verdict


def test_action_verdict_buy_correct():
    assert _action_verdict("buy", 0.05, False, False) == "correct"
    assert _action_verdict("buy", -0.05, False, False) == "wrong"
    assert _action_verdict("buy", 0.01, False, False) == "partial"
    assert _action_verdict("buy", 0.05, False, True) == "wrong"  # hit_stop overrides


def test_action_verdict_hold():
    assert _action_verdict("hold", 0.01, False, False) == "correct"
    assert _action_verdict("hold", 0.05, False, False) == "partial"


def test_action_verdict_sell_avoid():
    assert _action_verdict("sell", -0.05, False, False) == "correct"
    assert _action_verdict("sell", 0.02, False, False) == "wrong"
    assert _action_verdict("avoid", -0.03, False, False) == "correct"
    assert _action_verdict("avoid", 0.05, False, False) == "partial"


def test_collect_empty_dir(tmp_path):
    loader = MagicMock()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    assert tracker.collect(since_days=30) == []


def test_collect_skips_bad_json(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "SH600519_2026-05-01.json").write_text("not json", encoding="utf-8")
    loader = MagicMock()
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=365, today=pd.Timestamp("2026-05-30"))
    assert outcomes == []


def test_collect_buy_correct_outcome(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": 2, "action": "buy",
           "target_price": 1740.0, "stop_loss": 1500.0, "position_pct": 0.05}
    (out_dir / "SH600519_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")
    loader = MagicMock()
    loader.fetch_quote.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-05-02", periods=20, freq="B"),
        "open": [1700]*20, "high": [1750]*20, "low": [1680]*20,
        "close": [1700 + i*5 for i in range(20)],
        "vol": [1e6]*20, "amount": [1e8]*20,
    })
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=365, today=pd.Timestamp("2026-05-30"))
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.code == "SH600519"
    assert o.return_t5d is not None and o.return_t5d > 0
    assert o.verdict == "correct"


def test_collect_stop_loss_triggers_wrong(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600999", "rating_overall": 1, "action": "buy",
           "target_price": 1100.0, "stop_loss": 950.0, "position_pct": 0.05}
    (out_dir / "SH600999_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")
    loader = MagicMock()
    loader.fetch_quote.return_value = pd.DataFrame({
        "trade_date": pd.date_range("2026-05-02", periods=10, freq="B"),
        "open": [1000]*10, "high": [1020]*10,
        "low": [940] + [990]*9,  # T+1 low triggers stop
        "close": [995]*10, "vol": [1e6]*10, "amount": [1e8]*10,
    })
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=365, today=pd.Timestamp("2026-05-30"))
    assert outcomes[0].hit_stop_within_5d is True
    assert outcomes[0].verdict == "wrong"


def test_collect_pending_when_no_future_data(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": 0, "action": "hold",
           "target_price": 0, "stop_loss": 0, "position_pct": 0}
    (out_dir / "SH600519_2026-05-18.json").write_text(json.dumps(rpt), encoding="utf-8")
    loader = MagicMock()
    loader.fetch_quote.return_value = pd.DataFrame()
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=30, today=pd.Timestamp("2026-05-19"))
    assert outcomes[0].verdict == "pending"


def test_collect_respects_since_days_cutoff(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old_rpt = {"code": "SH600519", "rating_overall": 0, "action": "hold",
               "target_price": 0, "stop_loss": 0, "position_pct": 0}
    (out_dir / "SH600519_2026-01-01.json").write_text(json.dumps(old_rpt), encoding="utf-8")
    loader = MagicMock()
    tracker = OutcomeTracker(loader=loader, out_dir=out_dir)
    outcomes = tracker.collect(since_days=30, today=pd.Timestamp("2026-05-30"))
    assert outcomes == []   # old report filtered out
