import json
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from financial_analyst.agent.market.intraday_reviewer import (
    IntradayReviewer, _load_past_report, _build_stock_context,
)


def test_load_past_report_finds_latest(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-15.json").write_text(
        json.dumps({"rating_overall": 2, "action": "buy",
                    "target_price": 1900, "stop_loss": 1500, "position_pct": 0.05}),
        encoding="utf-8",
    )
    data = _load_past_report("SH600519", out)
    assert data is not None
    assert data["action"] == "buy"
    assert data["_asof"] == "2026-05-15"


def test_load_past_report_missing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert _load_past_report("SZ999999", out) is None


def test_build_stock_context_no_past(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    fake_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-05-13", "2026-05-14", "2026-05-15"]),
        "open": [100, 101, 102], "high": [105]*3, "low": [95]*3,
        "close": [102, 103, 104], "vol": [1e6]*3, "amount": [1e8]*3,
    })
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_df
    ctx = _build_stock_context("SH600519", "2026-05-15", loader, out)
    assert ctx["code"] == "SH600519"
    assert ctx["current_close"] == 104.0
    assert ctx["prev_action"] == "?"   # no past report


def test_build_stock_context_with_past(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-13.json").write_text(
        json.dumps({"rating_overall": 1, "action": "buy",
                    "target_price": 1900, "stop_loss": 95, "position_pct": 0.05}),
        encoding="utf-8",
    )
    fake_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-05-13", "2026-05-14", "2026-05-15"]),
        "open": [100, 101, 102], "high": [105]*3, "low": [95]*3,
        "close": [100, 103, 104], "vol": [1e6]*3, "amount": [1e8]*3,
    })
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_df
    ctx = _build_stock_context("SH600519", "2026-05-15", loader, out)
    assert ctx["prev_action"] == "buy"
    assert ctx["prev_asof"] == "2026-05-13"
    assert ctx["current_close"] == 104.0
    # base = 2026-05-13 close = 100 → ret = (104/100 - 1)*100 = 4%
    assert abs(ctx["pct_change_since_asof"] - 4.0) < 0.01


@pytest.mark.asyncio
async def test_intraday_reviewer_runs(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-13.json").write_text(
        json.dumps({"action": "buy", "target_price": 1900, "stop_loss": 95, "position_pct": 0.05}),
        encoding="utf-8",
    )
    fake_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-05-13", "2026-05-14", "2026-05-15"]),
        "open": [100, 101, 102], "high": [105]*3, "low": [95]*3,
        "close": [100, 103, 104], "vol": [1e6]*3, "amount": [1e8]*3,
    })
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_df

    fake_response_content = json.dumps({
        "verdicts": [{
            "code": "SH600519", "verdict": "OK",
            "reason": "上午 +4% 方向正确",
            "afternoon_action": "继续持有, 关注 14:30 是否守住 103",
            "prev_asof": "2026-05-13", "prev_action": "buy",
            "current_close": 104.0,
        }],
        "summary": "1 OK / 0 警惕 / 0 撤离",
        "markdown_body": "# Intraday Review 2026-05-15\nSH600519 OK",
        "summary_json": {"OK": 1},
    })
    fake = {"choices": [{"message": {"content": fake_response_content}}]}
    agent = IntradayReviewer(memory_root=tmp_path, loader=loader)
    with patch("financial_analyst.agent.market.intraday_reviewer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        result = await agent.run({"codes": "SH600519", "asof_date": "2026-05-15", "out_dir": str(out)})
    assert result.ok is True
    assert result.output.n_stocks == 1
    assert len(result.output.verdicts) == 1
    assert result.output.verdicts[0].verdict == "OK"
    md = out / "intraday_review_2026-05-15.md"
    assert md.exists()


@pytest.mark.asyncio
async def test_intraday_reviewer_auto_detects_codes(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "SH600519_2026-05-13.json").write_text(
        json.dumps({"action": "buy", "target_price": 1900, "stop_loss": 95, "position_pct": 0.05}),
        encoding="utf-8",
    )
    fake_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-05-13", "2026-05-14"]),
        "open": [100, 101], "high": [105]*2, "low": [95]*2,
        "close": [100, 103], "vol": [1e6]*2, "amount": [1e8]*2,
    })
    loader = MagicMock()
    loader.fetch_quote.return_value = fake_df

    fake_response_content = json.dumps({
        "verdicts": [{"code": "SH600519", "verdict": "OK", "reason": "x", "afternoon_action": "y"}],
        "summary": "auto-detected",
        "markdown_body": "# Intraday",
        "summary_json": {},
    })
    fake = {"choices": [{"message": {"content": fake_response_content}}]}
    agent = IntradayReviewer(memory_root=tmp_path, loader=loader)
    with patch("financial_analyst.agent.market.intraday_reviewer.LLMClient.for_agent") as mock_for:
        client = AsyncMock(); client.chat = AsyncMock(return_value=fake)
        mock_for.return_value = client
        # No codes given — should auto-detect SH600519 from out/
        result = await agent.run({"codes": "", "asof_date": "2026-05-14", "out_dir": str(out)})
    assert result.ok is True
    assert result.output.n_stocks == 1


@pytest.mark.asyncio
async def test_intraday_reviewer_empty_input_raises(tmp_path):
    """No codes + empty out/ → raises ValueError."""
    out = tmp_path / "out"
    out.mkdir()   # empty
    agent = IntradayReviewer(memory_root=tmp_path, loader=MagicMock())
    result = await agent.run({"codes": "", "asof_date": "2026-05-15", "out_dir": str(out)})
    assert result.ok is False
    assert "no codes" in result.error.lower() or "no past reports" in result.error.lower()
