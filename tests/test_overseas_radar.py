"""Smoke tests for v1.9.7 overseas-radar + morning-brief v2 agents.

Covers:
- tencent_global collector parsing
- overseas-market-scanner risk_tone judgment
- sector-rotation-analyzer aggregation
- catalyst-extractor mock LLM
- global-news-aggregator + macro-impact-analyzer mock LLM
"""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from financial_analyst.data.collectors.tencent_global import TencentGlobalCollector


# ─────────── collector ───────────


def test_tencent_global_parser_extracts_change_percent():
    """tencent 国际指数 layout: [3]=price, [4]=prevClose, [5]=open, [31]=change,
    [32]=changePercent, [33]=high, [34]=low."""
    # field layout: [0-9] header + [10-29] zeros (20) + [30]=date + [31]=change
    # + [32]=changePct + [33]=high + [34]=low
    fake_raw = (
        'v_usINX="200~标普500~.INX~7473.47~7445.72~7468.82~2693030895~0~0~7434.78'
        + '~0' * 20
        + '~2026-05-22 16:45:08~27.75~0.37~7506.32~7463.29' + '~USD' + '~0' * 35 + '";'
    )
    out = TencentGlobalCollector._parse(fake_raw, ["usINX"])
    assert "usINX" in out
    assert out["usINX"]["price"] == 7473.47
    assert out["usINX"]["prevClose"] == 7445.72
    assert out["usINX"]["change"] == 27.75
    assert out["usINX"]["changePercent"] == 0.37


# ─────────── overseas-market-scanner ───────────


@pytest.mark.asyncio
async def test_overseas_scanner_risk_on_with_strong_us_and_hk(tmp_path):
    from financial_analyst.agent.market.overseas_market_scanner import OverseasMarketScanner
    fake_collector = MagicMock()
    fake_collector.fetch_default.return_value = {
        "usDJI": {"name": "道琼斯", "price": 50000, "changePercent": 1.0,
                  "change": 500, "high": 50100, "low": 49500, "prevClose": 49500},
        "usIXIC": {"name": "纳指", "price": 26000, "changePercent": 1.5,
                   "change": 380, "high": 26100, "low": 25700, "prevClose": 25620},
        "usINX": {"name": "标普500", "price": 7470, "changePercent": 1.2,
                  "change": 88, "high": 7500, "low": 7400, "prevClose": 7382},
        "usVIX": {"name": "VIX", "price": 15.0, "changePercent": -8.0,
                  "change": -1.3, "high": 16.5, "low": 14.9, "prevClose": 16.3},
        "hkHSI": {"name": "恒生", "price": 25600, "changePercent": 1.5,
                  "change": 379, "high": 25700, "low": 25400, "prevClose": 25221},
        "hkHSTECH": {"name": "恒生科技", "price": 4869, "changePercent": 2.0,
                     "change": 95, "high": 4900, "low": 4800, "prevClose": 4774},
    }
    agent = OverseasMarketScanner(memory_root=tmp_path, collector=fake_collector)
    r = await agent._execute({"asof_date": "2026-05-24"})
    assert r["risk_tone"] == "risk_on"
    assert r["vix_level"] == 15.0
    assert r["n_indices"] == 6


@pytest.mark.asyncio
async def test_overseas_scanner_risk_off_with_high_vix(tmp_path):
    from financial_analyst.agent.market.overseas_market_scanner import OverseasMarketScanner
    fake_collector = MagicMock()
    fake_collector.fetch_default.return_value = {
        "usDJI": {"name": "道指", "price": 50000, "changePercent": -1.5,
                  "change": -762, "high": 50500, "low": 49800, "prevClose": 50762},
        "usVIX": {"name": "VIX", "price": 26.0, "changePercent": 25.0,
                  "change": 5.2, "high": 27, "low": 21, "prevClose": 20.8},
        "hkHSI": {"name": "恒生", "price": 25000, "changePercent": -1.2,
                  "change": -303, "high": 25400, "low": 24900, "prevClose": 25303},
    }
    agent = OverseasMarketScanner(memory_root=tmp_path, collector=fake_collector)
    r = await agent._execute({"asof_date": "2026-05-24"})
    assert r["risk_tone"] == "risk_off"
    assert r["vix_level"] == 26.0


# ─────────── sector-rotation-analyzer ───────────


@pytest.mark.asyncio
async def test_sector_rotation_groups_by_industry(tmp_path):
    """Mock industry_map + scanner output, verify aggregation by sector."""
    from financial_analyst.agent.market.sector_rotation_analyzer import SectorRotationAnalyzer

    # Create fake parquet root with stock_basic
    pq_root = tmp_path / "parquet"
    pq_root.mkdir()
    import pandas as pd
    df = pd.DataFrame([
        {"ts_code": "600519.SH", "name": "茅台", "industry": "白酒", "area": "贵州", "market": "主板", "list_date": "20010801"},
        {"ts_code": "300750.SZ", "name": "宁德", "industry": "电池", "area": "福建", "market": "创业板", "list_date": "20180611"},
        {"ts_code": "002594.SZ", "name": "比亚迪", "industry": "汽车", "area": "广东", "market": "主板", "list_date": "20110630"},
        {"ts_code": "300308.SZ", "name": "中际旭创", "industry": "半导体", "area": "山东", "market": "创业板", "list_date": "20170418"},
        {"ts_code": "601012.SH", "name": "隆基", "industry": "光伏", "area": "陕西", "market": "主板", "list_date": "20120410"},
    ])
    df.to_parquet(pq_root / "tushare_stock_basic.parquet")

    fake_scanner = {
        "top_gainers": [
            {"code": "SH600519", "name": "茅台", "pct_chg": 3.5},
            {"code": "SZ300308", "name": "中际旭创", "pct_chg": 5.2},
            {"code": "SZ300750", "name": "宁德", "pct_chg": 4.0},
            {"code": "SH601012", "name": "隆基", "pct_chg": 3.8},
        ],
        "top_losers": [
            {"code": "SZ002594", "name": "比亚迪", "pct_chg": -2.5},
        ],
        "volume_anomalies": [],
        "index_snapshot": {"SH000300_pct": 0.8},
    }
    agent = SectorRotationAnalyzer(memory_root=tmp_path, parquet_root=pq_root,
                                   min_movers_per_sector=1)
    r = await agent._execute({"asof_date": "2026-05-24", "market-scanner": fake_scanner})

    # All 5 sectors covered (white/battery/auto/semi/solar each 1 stock)
    assert r["n_sectors_covered"] == 5
    # Top leader should be 半导体 +5.2%
    assert r["today_leaders"][0]["sector"] == "半导体"
    # Laggard 汽车 -2.5%
    laggard_sectors = [s["sector"] for s in r["today_laggards"]]
    assert "汽车" in laggard_sectors


# ─────────── catalyst-extractor (mock LLM) ───────────


@pytest.mark.asyncio
async def test_catalyst_extractor_parses_llm_response(tmp_path):
    from financial_analyst.agent.market.catalyst_extractor import CatalystExtractor

    fake_news_db = MagicMock()
    fake_news_db.query_news.return_value = [
        {"title": "茅台公告 Q3 净利同比 -8%", "ts": "2026-05-24 09:30"},
    ]
    fake_news_db.close = MagicMock()

    fake_resp = {"choices": [{"message": {"content": json.dumps({
        "catalysts": [
            {"code": "SH600519", "name": "茅台", "pct_chg": -2.5,
             "catalyst_type": "earnings", "summary": "Q3 净利 -8% 低于预期",
             "direction": "bearish", "confidence": "high",
             "cited_news_titles": ["茅台公告 Q3 净利同比 -8%"]},
        ]
    })}}]}
    with patch("financial_analyst.agent.market.catalyst_extractor.LLMClient") as mock_llm:
        mock_llm.for_agent.return_value.chat = AsyncMock(return_value=fake_resp)
        agent = CatalystExtractor(memory_root=tmp_path, news_db=fake_news_db)
        r = await agent._execute({
            "asof_date": "2026-05-24",
            "market-scanner": {"top_losers": [{"code": "SH600519", "name": "茅台", "pct_chg": -2.5}]},
        })

    assert len(r["catalysts"]) == 1
    assert r["catalysts"][0]["catalyst_type"] == "earnings"
    assert r["catalysts"][0]["direction"] == "bearish"
    assert r["n_with_catalyst"] == 1


# ─────────── global-news-aggregator (mock LLM) ───────────


@pytest.mark.asyncio
async def test_global_news_aggregator_writes_narrative(tmp_path):
    from financial_analyst.agent.market.global_news_aggregator import GlobalNewsAggregator

    fake_resp = {"choices": [{"message": {"content": json.dumps({
        "overall_narrative": "美股隔夜小涨 +0.3%, 港股早盘 +1%, VIX 21.7 偏中性. 主要 channel 是美股 TMT 跟随效应.",
        "impacts": [
            {"channel": "us_equity", "summary": "纳指 +0.2% → 半导体板块预期跟随",
             "direction_for_a_shares": "bullish", "affected_sectors": ["半导体", "CPO"],
             "importance": "high"},
        ],
        "key_channels": ["us_equity"],
    })}}]}
    with patch("financial_analyst.agent.market.global_news_aggregator.LLMClient") as mock_llm:
        mock_llm.for_agent.return_value.chat = AsyncMock(return_value=fake_resp)
        agent = GlobalNewsAggregator(memory_root=tmp_path)
        r = await agent._execute({
            "asof_date": "2026-05-24",
            "overseas-market-scanner": {
                "risk_tone": "mixed",
                "vix_level": 21.7,
                "us_overnight": {},
                "hk_market": {},
            },
        })

    assert "美股" in r["overall_narrative"]
    assert r["key_channels"] == ["us_equity"]
    assert len(r["impacts"]) == 1
    assert r["impacts"][0]["channel"] == "us_equity"


# ─────────── macro-impact-analyzer (mock LLM + 落盘) ───────────


@pytest.mark.asyncio
async def test_macro_impact_writes_markdown(tmp_path):
    from financial_analyst.agent.market.macro_impact_analyzer import MacroImpactAnalyzer

    fake_resp = {"choices": [{"message": {"content": json.dumps({
        "headline": "美股 +0.5% A 股仅 +0.1% → follow-through 偏弱",
        "follow_through_judgment": "海外 risk_on 但 A 股内生韧性不足, 板块切换中.",
        "actionable_signals": [
            {"signal": "明日大盘高开 0.2-0.5%, 关注半导体跟随",
             "confidence": "medium", "affected_codes_or_sectors": ["半导体"]},
            {"signal": "防御板块持有, 不追高",
             "confidence": "high", "affected_codes_or_sectors": ["红利", "电力"]},
        ],
    })}}]}
    with patch("financial_analyst.agent.market.macro_impact_analyzer.LLMClient") as mock_llm:
        mock_llm.for_agent.return_value.chat = AsyncMock(return_value=fake_resp)
        agent = MacroImpactAnalyzer(memory_root=tmp_path)
        out_dir = tmp_path / "out"
        r = await agent._execute({
            "asof_date": "2026-05-24",
            "overseas-market-scanner": {"risk_tone": "risk_on", "vix_level": 18,
                                         "us_overnight": {}, "hk_market": {}},
            "global-news-aggregator": {"overall_narrative": "...", "key_channels": ["us_equity"], "impacts": []},
            "market-scanner": {},
            "out_dir": str(out_dir),
        })

    md_path = Path(r["output_md_path"])
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "海外格局雷达" in content
    assert "follow-through 偏弱" in content
    assert "🟡" in content or "🟢" in content   # signal emoji
    assert len(r["actionable_signals"]) == 2
