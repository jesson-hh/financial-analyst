import json
import pandas as pd
from pathlib import Path
from unittest.mock import patch, MagicMock
from financial_analyst.ask.tools import (
    list_past_reports, read_past_report, search_memory,
    quick_quote, quick_factors, list_dream_proposals,
)


def test_list_past_reports_empty(tmp_path):
    assert list_past_reports(out_dir=tmp_path / "out") == []


def test_list_past_reports_with_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-15.md").write_text("body", encoding="utf-8")
    (out / "SH600519_2026-05-15.json").write_text(json.dumps({
        "rating_overall": 2, "action": "buy", "target_price": 1900,
        "stop_loss": 1500, "position_pct": 0.05,
    }), encoding="utf-8")
    results = list_past_reports(out_dir=out)
    assert len(results) == 1
    assert results[0]["code"] == "SH600519"
    assert results[0]["asof"] == "2026-05-15"
    assert results[0]["rating_overall"] == 2


def test_read_past_report_latest(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    p1 = out / "SH600519_2026-05-15.md"
    p2 = out / "SH600519_2026-05-16.md"
    p1.write_text("first", encoding="utf-8")
    p2.write_text("second", encoding="utf-8")
    # Ensure p2 has a later mtime
    import time
    p2.touch()
    text = read_past_report("SH600519", out_dir=out)
    assert text == "second"


def test_read_past_report_specific_date(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-15.md").write_text("first", encoding="utf-8")
    (out / "SH600519_2026-05-16.md").write_text("second", encoding="utf-8")
    text = read_past_report("SH600519", date_str="2026-05-15", out_dir=out)
    assert text == "first"


def test_search_memory_returns_hits(tmp_path):
    (tmp_path / "bear-advocate").mkdir()
    (tmp_path / "bear-advocate" / "pitfalls.md").write_text("游资 gamecapital risk", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    hits = search_memory("gamecapital", memory_root=tmp_path, cache_dir=cache)
    assert len(hits) >= 1
    assert "bear-advocate" in [h["agent"] for h in hits]


def test_quick_quote_with_loader_mock(tmp_path, monkeypatch):
    fake = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=25, freq="B"),
        "open": [100]*25, "high": [105]*25, "low": [95]*25,
        "close": [100 + i*0.5 for i in range(25)],
        "vol": [1e6]*25, "amount": [1e8]*25,
    })
    fake_db = pd.DataFrame({
        "trade_date": pd.date_range("2026-04-01", periods=25, freq="B"),
        "pe_ttm": [20.0]*25, "pb": [3.0]*25, "total_mv": [80_0000.0]*25,
        "turnover_rate": [3.5]*25,
    })
    class FakeLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            return fake
        def fetch_daily_basic(self, code, start, end):
            return fake_db
    with patch("financial_analyst.data.loader_factory.get_default_loader", return_value=FakeLoader()):
        out = quick_quote("SH600519", asof="2026-05-15")
    assert out["close"] > 100
    assert "pe_ttm" in out
    assert out["pe_ttm"] == 20.0


def test_list_dream_proposals_empty(tmp_path):
    assert list_dream_proposals(memory_root=tmp_path) == []


def test_list_dream_proposals_with_files(tmp_path):
    proposed = tmp_path / "_proposed" / "bear-advocate"
    proposed.mkdir(parents=True)
    (proposed / "2026-05-18_test.md").write_text(
        "---\ntitle: Test Title\nconfidence: med\n---\nbody",
        encoding="utf-8",
    )
    results = list_dream_proposals(memory_root=tmp_path)
    assert len(results) == 1
    assert results[0]["agent"] == "bear-advocate"
    assert results[0]["confidence"] == "med"
