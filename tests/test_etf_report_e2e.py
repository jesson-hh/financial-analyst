"""End-to-end test for the etf-deep-dive pipeline with mocked ETFLoader + LLM.

Strategy
--------
1. Inject a _FakeLoader into EtfQuoteFetcher / EtfMetricsFetcher by patching
   ``_get_loader`` directly on the agent classes (they call self._get_loader() at
   runtime, so this is the cleanest injection point).
2. Patch ``LLMClient.for_agent`` in every ETF agent module (plus the central llm.client
   module) so every LLM call returns a canned JSON payload routed by agent NAME.
3. Mock ``overseas-market-scanner._execute`` and ``sector-rotation-analyzer._execute``
   to return minimal valid dicts — they hit live network but the report-writer does NOT
   depend on them, so the pipeline completes regardless.
4. Run ``asyncio.run(run_etf_report_oneshot(...))`` and assert:
   - the .md + .json files are written
   - ``rating_overall == sum(rating_dimensions.values())``  (the key contract)
   - ``action`` is in the allowed set {buy, hold, sell, avoid, accumulate}
"""
from __future__ import annotations

import asyncio
import json
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Canned writer dims — easy to reference in assertions
# ---------------------------------------------------------------------------
_WRITER_DIMS = {"holdings": 1, "technical": 1, "flow": 0, "valuation": 1, "risk": -1}
# sum = 2, so rating_overall = 2, action = "buy", position_pct = 0.05 are consistent


# ---------------------------------------------------------------------------
# Canned payloads per agent NAME
# ---------------------------------------------------------------------------
_PAYLOADS: dict = {
    # Tier-2 analysts
    "etf-holdings-analyst": {
        "holdings_score": 1,
        "bull_points": ["[V4] 市值加权宽基, 成分股流动性优"],
        "bear_points": ["[F4] 前十大集中度约30%"],
        "top_holding_weight": 9.5,
        "sector_concentration_hhi": 0.08,
        "index_methodology_note": "市值加权宽基",
    },
    "etf-technical-analyst": {
        "technical_score": 1,
        "bull_points": ["价格站上MA20"],
        "bear_points": ["RSI近超买区"],
        "ma_state": "bullish",
        "rsi_state": "neutral",
        "breakout_signal": None,
    },
    "etf-flow-analyst": {
        "flow_score": 0,
        "bull_points": ["AUM稳定"],
        "bear_points": ["近期小幅净赎回"],
        "flow_regime": "neutral",
        "aum_trend": "stable",
        "liquidity_note": "流动性充沛",
    },
    "etf-valuation-analyst": {
        "valuation_score": 1,
        "bull_points": ["折价0.1%, 接近面值"],
        "bear_points": ["PE略高于历史均值"],
        "premium_discount_state": "at_par",
        "tracking_error_level": "low",
        "fee_drag_note": "总费率0.20%, 行业中等偏低",
    },
    # Tier-3 debate
    "etf-bull-advocate": {
        "thesis_bullets": [
            "[V1] 沪深300代表性强, 机构配置核心底仓",
            "[V3] 折价提供安全边际",
        ],
        "target_price_high": 5.2,
        "target_price_base": 5.0,
        "disproof_signals": ["流入转持续净赎回"],
    },
    "etf-bear-advocate": {
        "thesis_bullets": [
            "[F1] 大盘股赛道拥挤已充分定价",
            "[F5] 溢价均值回归风险",
        ],
        "target_price_low": 4.5,
        "downside_pct": -8.0,
    },
    "etf-risk-officer": {
        "risk_score": -1,
        "veto_flags": [],
        "position_sizing_advice": "3-5%",
    },
    # Tier-3 writer — rating_overall equal to sum(dims) = 2
    "etf-report-writer": {
        "rating_overall": sum(_WRITER_DIMS.values()),  # 2
        "rating_dimensions": _WRITER_DIMS,
        "action": "buy",
        "target_price": 5.2,
        "stop_loss": 4.6,
        "position_pct": 0.05,
        "markdown_body": "# 报告\n一、综合评级\n测试报告内容",
        "summary_json": {"code": "SH510300"},
    },
    # Tier-4 introspector
    "etf-introspector": {
        "quality_flags": [],
        "proposals": [],
        "summary": "本次报告质量正常，无明显异常",
        "written_to": None,
    },
}


# ---------------------------------------------------------------------------
# Fake ETFLoader (mirrors _FakeLoader from test_etf_data_agents.py)
# ---------------------------------------------------------------------------

class _FakeLoader:
    def fetch_etf_quote(self, *a, **k):
        # 29 rows — enough for ma20, ma60 calcs (which need 20/60; use what we have)
        n = 29
        return pd.DataFrame({
            "trade_date": [f"2026-05-{i:02d}" for i in range(1, n + 1)],
            "open":  [4.9] * n,
            "high":  [5.0] * n,
            "low":   [4.8] * n,
            "close": [4.90 + i * 0.001 for i in range(n)],
            "vol":   [100 + i * 10 for i in range(n)],
            "amount": [49000 + i * 1000 for i in range(n)],
        })

    def fetch_etf_meta(self, c):
        return {
            "name": "沪深300ETF",
            "m_fee": 0.15,
            "c_fee": 0.05,
            "total_fee": 0.20,
            "benchmark": "沪深300",
            "index_code": "000300.SH",
            "fund_type": "ETF",
        }

    def fetch_etf_premium_discount(self, c):
        return {"realtime_premium_discount_pct": -0.1}

    def fetch_etf_nav(self, c, *a, **k):
        return pd.DataFrame({"nav_date": ["2026-05-29"], "unit_nav": [4.91]})

    def fetch_etf_flow(self, c, *a, **k):
        return {
            "latest_share_change": -1260.0,
            "aum_latest": 1.37e7,
            "aum_unit": "wan_yuan",
        }

    def fetch_tracking_error(self, c, *a, **k):
        return {"tracking_error_annualized": 0.0022, "window": 60}

    def fetch_etf_holdings(self, c, *a, **k):
        return {
            "end_date": "20260331",
            "holdings": [{"symbol": "600519.SH", "ratio": 9.0}],
        }


# ---------------------------------------------------------------------------
# Fake LLMClient factory — routes by agent name captured in closure
# ---------------------------------------------------------------------------

def _make_fake_llm_class():
    """Return a fake LLMClient class whose for_agent(name) is name-aware."""

    class _FakeClient:
        def __init__(self, name: str):
            self._name = name

        async def chat(self, messages, **kwargs):
            payload = _PAYLOADS.get(self._name, {})
            return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    class _FakeLLMClient:
        @staticmethod
        def for_agent(name: str, config_path=None):
            return _FakeClient(name)

    return _FakeLLMClient


# ---------------------------------------------------------------------------
# Helper: run the full pipeline and return the orchestrator results dict
# ---------------------------------------------------------------------------

async def _run_pipeline(tmp_path, monkeypatch):
    """Internal coroutine: wire all mocks then drive the ETF pipeline.

    Returns the ``results`` dict from Orchestrator.run so the test can inspect
    individual SubAgentResult objects.
    """
    # 1. Register all sub-agents (idempotent)
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()

    # 2. Fake loader — patch _get_loader() directly on agent classes
    fake_loader = _FakeLoader()

    import financial_analyst.agent.etf.quote_fetcher as qf_mod
    import financial_analyst.agent.etf.metrics_fetcher as mf_mod

    monkeypatch.setattr(
        qf_mod.EtfQuoteFetcher, "_get_loader", lambda self: fake_loader, raising=False
    )
    monkeypatch.setattr(
        mf_mod.EtfMetricsFetcher, "_get_loader", lambda self: fake_loader, raising=False
    )

    # 3. Fake LLMClient — patch in the central module + each ETF agent module
    import financial_analyst.llm.client as llm_mod
    FakeLLMClient = _make_fake_llm_class()
    monkeypatch.setattr(llm_mod, "LLMClient", FakeLLMClient, raising=False)

    import importlib
    _etf_llm_mods = [
        "financial_analyst.agent.etf.holdings_analyst",
        "financial_analyst.agent.etf.technical_analyst",
        "financial_analyst.agent.etf.flow_analyst",
        "financial_analyst.agent.etf.valuation_analyst",
        "financial_analyst.agent.etf.bull_advocate",
        "financial_analyst.agent.etf.bear_advocate",
        "financial_analyst.agent.etf.risk_officer",
        "financial_analyst.agent.etf.report_writer",
        "financial_analyst.agent.etf.introspector",
    ]
    for mod_path in _etf_llm_mods:
        try:
            mod = importlib.import_module(mod_path)
            monkeypatch.setattr(mod, "LLMClient", FakeLLMClient, raising=False)
        except (ImportError, AttributeError):
            pass

    # 4. Mock context agents that hit live network
    import financial_analyst.agent.market.overseas_market_scanner as oms_mod
    import financial_analyst.agent.market.sector_rotation_analyzer as sra_mod

    async def _fake_overseas(self, inputs):
        return {
            "as_of": "2026-05-29",
            "us_overnight": {},
            "hk_market": {},
            "risk_tone": "mixed",
            "risk_tone_detail": "stub",
            "vix_level": None,
            "n_indices": 0,
        }

    async def _fake_sector(self, inputs):
        return {
            "as_of": "2026-05-29",
            "today_leaders": [],
            "today_laggards": [],
            "rotation_signal": "stub",
            "n_sectors_covered": 0,
        }

    monkeypatch.setattr(oms_mod.OverseasMarketScanner, "_execute", _fake_overseas)
    monkeypatch.setattr(sra_mod.SectorRotationAnalyzer, "_execute", _fake_sector)

    # 5. Build the DAG and run the orchestrator directly so we get results back
    from financial_analyst.agent.memory_index import MemoryIndex
    from financial_analyst.agent.orchestrator import Orchestrator
    from financial_analyst.memory_paths import default_memory_root
    from financial_analyst.settings import Settings
    from financial_analyst.swarm import load_preset

    settings = Settings()
    from pathlib import Path
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    mem_index = MemoryIndex(
        memory_root=default_memory_root(),
        db_path=cache_dir / "memory.fts5.db",
    )
    mem_index.update_changed()

    nodes = load_preset("etf-deep-dive", memory_root=tmp_path, memory_index=mem_index)
    orch = Orchestrator(nodes)
    results = await orch.run({
        "code": "SH510300",
        "asof_date": "2026-05-29",
        "out_dir": str(tmp_path),
    })
    return results


# ---------------------------------------------------------------------------
# Main E2E test
# ---------------------------------------------------------------------------

def test_etf_report_e2e(monkeypatch, tmp_path):
    """Full etf-deep-dive pipeline: mocked loader + LLM, report written, sums correct."""
    results = asyncio.run(_run_pipeline(tmp_path, monkeypatch))

    # ----- A. Files must be written -----
    md_path = tmp_path / "SH510300_2026-05-29.md"
    json_path = tmp_path / "SH510300_2026-05-29.json"

    assert md_path.exists(), (
        f".md not written; contents of {tmp_path}: {sorted(tmp_path.iterdir())}\n"
        f"writer result: {results.get('etf-report-writer')}"
    )
    assert json_path.exists(), (
        f".json not written; writer result: {results.get('etf-report-writer')}"
    )

    # ----- B. Report writer SubAgentResult must be ok -----
    writer_result = results.get("etf-report-writer")
    assert writer_result is not None, "etf-report-writer not in results"
    assert writer_result.ok, f"etf-report-writer failed: {writer_result.error}"

    # ----- C. rating_overall == sum(rating_dimensions.values()) -----
    out = writer_result.output
    rating_overall = out.rating_overall
    dims = out.rating_dimensions
    dims_sum = sum(dims.values())
    assert rating_overall == dims_sum, (
        f"rating_overall={rating_overall} != sum(dims)={dims_sum}  dims={dims}"
    )

    # ----- D. action is in the allowed set -----
    allowed_actions = {"buy", "hold", "sell", "avoid", "accumulate"}
    assert out.action in allowed_actions, (
        f"action={out.action!r} not in {allowed_actions}"
    )

    # ----- E. MD content sanity -----
    md_text = md_path.read_text(encoding="utf-8")
    assert any(kw in md_text for kw in ("报告", "SH510300", "综合评级", "ETF")), (
        f"Unexpected md content (first 300 chars): {md_text[:300]}"
    )
