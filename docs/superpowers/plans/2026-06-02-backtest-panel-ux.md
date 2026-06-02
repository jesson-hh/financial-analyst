# Backtest Panel UX (P0+P1+P2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent 回测 panel 从"我都不知道在回测什么"升级到"banner 说清模式 + 横条点开池子来源 + 交易点开看 LLM 全文 + KPI 公式 tooltip + 控件能调池子/持有期/止盈止损"

**Architecture:** 后端 `BacktestRunReq` 加 4 字段 + `CandidateConfig` 新增 `pool` 模式 (语义切换, 不破坏 WatchLoop 老调用) + `_MockAgent` 接 `hold_days/take_profit/stop_loss`. 前端 `BacktestMode` 重构成 5 个新 sub-component: BacktestStrategyBanner / BacktestSummaryChips / TradeReasonModal / KpiTooltip / AdvancedControls. Playwright 真浏览器烟测验收.

**Tech Stack:** Python 3.13 (FastAPI/pydantic 2/pytest) + JSX (React 18 inline babel) + Playwright (browser_eval / browser_click)

**Spec:** [docs/superpowers/specs/2026-06-02-backtest-panel-ux-design.md](../specs/2026-06-02-backtest-panel-ux-design.md)

---

## File Structure

**后端**:
- Modify: `src/financial_analyst/buddy/server.py` (BacktestRunReq @ L203-209, endpoint validation @ L2221-2247)
- Modify: `src/financial_analyst/backtest/candidate.py` (CandidateConfig + select_candidates 加 pool 分支)
- Modify: `src/financial_analyst/buddy/backtest_run.py` (`_MOCK_HOLD_DAYS` 改 param, _MockAgent take/stop 决策, run_backtest 串联)

**前端**:
- Modify: `src/financial_analyst/ui/quant.jsx` (BacktestMode @ L1750-1896 全重构, 加 5 sub-components)
- Modify: `src/financial_analyst/ui/quant.html` (`?v=` bump 防浏览器缓存)

**测试**:
- Create: `tests/test_backtest_run_req_v2.py` (5 字段 pydantic + endpoint validation)
- Create: `tests/test_candidate_pool_mode.py` (pool=csi300 → 池子模式; pool=None → 老 watchlist 语义保留)
- Create: `tests/test_mock_agent_v2.py` (hold_days/take_profit/stop_loss 决策优先级)
- Create: `tests/test_backtest_run_end_to_end_v2.py` (req → run_backtest mock 5 日 csi_fast 完整链路)
- Create: `tests/test_backtest_panel_ux_e2e.py` (Playwright)

---

## Task 1: 后端字段扩展 + 池子模式 + Mock Agent 决策优先级

**Files:**
- Modify: `src/financial_analyst/buddy/server.py:203-209` (BacktestRunReq)
- Modify: `src/financial_analyst/buddy/server.py:2221-2247` (endpoint validation)
- Modify: `src/financial_analyst/backtest/candidate.py` (CandidateConfig + select_candidates)
- Modify: `src/financial_analyst/buddy/backtest_run.py` (_MockAgent + run_backtest)
- Create: `tests/test_backtest_run_req_v2.py`
- Create: `tests/test_candidate_pool_mode.py`
- Create: `tests/test_mock_agent_v2.py`
- Create: `tests/test_backtest_run_end_to_end_v2.py`

### 1.1 写 `BacktestRunReq` 字段失败测试

- [ ] **Step 1: 写失败测试**

Create `tests/test_backtest_run_req_v2.py`:
```python
"""BacktestRunReq P2 扩字段: pool/hold_days/factor_name/stop_loss_pct/take_profit_pct"""
import pytest
from pydantic import ValidationError
from financial_analyst.buddy.server import BacktestRunReq


class TestBacktestRunReqExtended:
    def test_default_pool_is_csi300(self):
        req = BacktestRunReq()
        assert req.pool == "csi300"

    def test_default_hold_days_is_3(self):
        req = BacktestRunReq()
        assert req.hold_days == 3

    def test_default_factor_name_is_rev_20(self):
        req = BacktestRunReq()
        assert req.factor_name == "rev_20"

    def test_default_stop_take_are_none(self):
        req = BacktestRunReq()
        assert req.stop_loss_pct is None
        assert req.take_profit_pct is None

    def test_pool_accepts_whitelist(self):
        for pool in ("csi300", "csi_fast", "csi500", "csi800"):
            req = BacktestRunReq(pool=pool)
            assert req.pool == pool

    def test_hold_days_range(self):
        BacktestRunReq(hold_days=1)
        BacktestRunReq(hold_days=60)
        with pytest.raises(ValidationError):
            BacktestRunReq(hold_days=0)
        with pytest.raises(ValidationError):
            BacktestRunReq(hold_days=61)

    def test_stop_loss_range(self):
        BacktestRunReq(stop_loss_pct=0.05)
        BacktestRunReq(stop_loss_pct=0.5)
        with pytest.raises(ValidationError):
            BacktestRunReq(stop_loss_pct=0.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(stop_loss_pct=0.6)

    def test_take_profit_range(self):
        BacktestRunReq(take_profit_pct=0.1)
        BacktestRunReq(take_profit_pct=2.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(take_profit_pct=0.0)
        with pytest.raises(ValidationError):
            BacktestRunReq(take_profit_pct=2.1)
```

- [ ] **Step 2: 跑测试验失败**

Run: `cd G:/financial-analyst && pytest tests/test_backtest_run_req_v2.py -v --tb=short`
Expected: 8 fails (AttributeError or ValidationError mismatch, 字段还没加)

- [ ] **Step 3: 加字段到 BacktestRunReq**

Edit `src/financial_analyst/buddy/server.py:203-209`:
```python
class BacktestRunReq(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    init_cash: float = 1_000_000.0
    candidate_topn: int = 20
    mode: str = "mock"
    match_freq: str = "day"
    # P2 扩字段 ↓
    pool: str = Field(default="csi300", description="csi300|csi_fast|csi500|csi800")
    hold_days: int = Field(default=3, ge=1, le=60, description="mock 持有期 (1-60)")
    factor_name: str = Field(default="rev_20", description="候选排序因子, 第一版只 rev_20")
    stop_loss_pct: Optional[float] = Field(default=None, gt=0, le=0.5,
                                            description="持仓亏损止损阈, None=不触发")
    take_profit_pct: Optional[float] = Field(default=None, gt=0, le=2.0,
                                              description="持仓盈利止盈阈, None=不触发")
```

`from pydantic import BaseModel, Field` (确认 import 已有 Field).

- [ ] **Step 4: 跑测试验通过**

Run: `pytest tests/test_backtest_run_req_v2.py -v --tb=short`
Expected: 8 pass

### 1.2 写 endpoint validation 失败测试 + 实现

- [ ] **Step 5: 加 endpoint validation 测试到同文件**

Append to `tests/test_backtest_run_req_v2.py`:
```python
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


class TestEndpointValidation:
    def setup_method(self):
        app = build_app()
        self.client = TestClient(app)

    def test_rejects_pool_all(self):
        r = self.client.post("/backtest/run", json={"pool": "all", "mode": "mock"})
        assert r.status_code == 400
        assert "csi800" in r.json()["error"]

    def test_rejects_non_whitelist_factor(self):
        r = self.client.post("/backtest/run", json={"factor_name": "mom_20", "mode": "mock"})
        assert r.status_code == 400
        assert "rev_20" in r.json()["error"]

    def test_accepts_full_p2_payload(self):
        r = self.client.post("/backtest/run", json={
            "pool": "csi_fast", "hold_days": 5,
            "stop_loss_pct": 0.05, "take_profit_pct": 0.1,
            "mode": "mock"
        })
        assert r.status_code == 200
        assert r.json()["status"] == "running"
```

- [ ] **Step 6: 跑测试验失败**

Run: `pytest tests/test_backtest_run_req_v2.py::TestEndpointValidation -v`
Expected: 2 fails (pool='all' 当前接受, factor_name='mom_20' 当前接受)

- [ ] **Step 7: 加 endpoint validation**

Edit `src/financial_analyst/buddy/server.py` `/backtest/run` 校验区 (L2223 附近, mode 校验后):
```python
if req.pool not in ("csi300", "csi_fast", "csi500", "csi800"):
    return JSONResponse(status_code=400, content={
        "error": f"pool 不支持 '{req.pool}', 可选 csi300|csi_fast|csi500|csi800 (全市场池请用 csi800 替代)",
        "status": "bad_request"})
if req.factor_name != "rev_20":
    return JSONResponse(status_code=400, content={
        "error": f"factor_name 第一版只支持 'rev_20', 收到 '{req.factor_name}' (其它因子下轮接 /factor/list)",
        "status": "bad_request"})
```

- [ ] **Step 8: 跑测试验通过**

Run: `pytest tests/test_backtest_run_req_v2.py -v`
Expected: 11 pass

### 1.3 写 CandidateConfig.pool 失败测试

- [ ] **Step 9: 写测试**

Create `tests/test_candidate_pool_mode.py`:
```python
"""CandidateConfig.pool 语义切换: None=旧 watchlist 路径, 非空=池子模式"""
import pytest
from unittest.mock import MagicMock, patch
from financial_analyst.backtest.candidate import CandidateConfig, select_candidates


class TestCandidateConfigPool:
    def test_default_pool_is_none_backward_compat(self):
        cfg = CandidateConfig()
        assert cfg.pool is None

    def test_accepts_pool_arg(self):
        cfg = CandidateConfig(pool="csi300")
        assert cfg.pool == "csi300"

    @patch("financial_analyst.data.universe.resolve_universe_codes")
    def test_pool_mode_uses_resolved_codes(self, mock_resolve):
        mock_resolve.return_value = ["SH600000", "SH600001", "SH600002"]
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        # 让 fetch_quote_leq_prev 返一段够长的 close 序列
        import pandas as pd
        reader.fetch_quote_leq_prev.return_value = pd.DataFrame({
            "trade_date": pd.date_range("2026-04-01", periods=25, freq="D").astype(str),
            "close": [10.0 - i*0.1 for i in range(25)],
        })
        cfg = CandidateConfig(pool="csi300", topn=2)
        result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
        assert mock_resolve.called
        assert mock_resolve.call_args[0][0] == "csi300"
        # base 来自 pool, 不是 watchlist
        assert all(c in ("SH600000", "SH600001", "SH600002") for c in result.codes)

    @patch("financial_analyst.backtest.candidate._load_watchlist_codes")
    def test_pool_none_keeps_old_watchlist_path(self, mock_watch):
        mock_watch.return_value = ["SH000001", "SH000002"]
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        reader.fetch_quote_leq_prev.return_value = None
        cfg = CandidateConfig(pool=None)
        result = select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
        # 老路径仍调 watchlist
        assert mock_watch.called

    @patch("financial_analyst.data.universe.resolve_universe_codes")
    def test_pool_unresolvable_raises(self, mock_resolve):
        mock_resolve.return_value = []
        reader = MagicMock()
        reader.prev_trade_date.return_value = "2026-05-30"
        cfg = CandidateConfig(pool="bad_pool_name")
        with pytest.raises(ValueError, match="resolved to 0 codes"):
            select_candidates("2026-05-31", holdings=[], reader=reader, cfg=cfg)
```

- [ ] **Step 10: 跑测试验失败**

Run: `pytest tests/test_candidate_pool_mode.py -v`
Expected: 5 fails (TypeError: __init__ got unexpected keyword 'pool')

- [ ] **Step 11: 改 CandidateConfig + select_candidates**

Edit `src/financial_analyst/backtest/candidate.py`:

`CandidateConfig` 加 pool 字段 (L23-31 区域):
```python
@dataclass
class CandidateConfig:
    topn: int = 20
    pool: Optional[str] = None     # P2 新增 — None=旧 watchlist 路径, 非空=池子模式
    rev20_lookback_tradedays: int = 30
    rev20_pick: str = "low"
    include_holdings: bool = True
    include_watchlist: bool = True   # pool 非空时此字段被忽略
    watchlist_path: Optional[Path] = None
    sentinel_codes: tuple = ("SH999999",)
```

`select_candidates` 加分支 (替换 L74-75 的 base 计算):
```python
def select_candidates(date: str, holdings: List[str], reader,
                      cfg: CandidateConfig = CandidateConfig()) -> CandidateResult:
    """Build the candidate pool for ``date`` using only ≤T-1 data.

    Two modes (cfg.pool):
    * None  → 旧 watchlist 路径: base = holdings ∪ watchlist (WatchLoop 实盘盯盘场景)
    * 非空  → 池子模式: base = holdings ∪ resolve_universe_codes(pool), watchlist 不参与
              (BacktestRunner 回测场景, 在固定池子内 rev_20 选股)
    """
    date = str(date)
    prev = reader.prev_trade_date(date)

    holdings = list(dict.fromkeys(holdings)) if cfg.include_holdings else []
    sentinels = set(cfg.sentinel_codes)

    if cfg.pool:
        # 池子模式
        from financial_analyst.data.universe import resolve_universe_codes
        pool_codes = [c for c in resolve_universe_codes(cfg.pool) if c not in sentinels]
        if not pool_codes:
            raise ValueError(
                f"pool '{cfg.pool}' resolved to 0 codes "
                f"(缺 index_constituents.parquet? 跑 `fa data bootstrap`)")
        base: List[str] = list(dict.fromkeys([*holdings, *pool_codes]))
        watch = []   # 池子模式下 watchlist 不参与
    else:
        # 旧 watchlist 路径
        watch = [c for c in _load_watchlist_codes(cfg) if c not in sentinels]
        base = list(dict.fromkeys([*holdings, *watch]))

    raw_rev20: Dict[str, float] = {}
    for code in base:
        df = reader.fetch_quote_leq_prev(
            code, n_days_back=cfg.rev20_lookback_tradedays,
            freq="day", as_of_date=date)
        if df is None or len(df) == 0 or "close" not in df.columns:
            continue
        df = df.sort_values("trade_date")
        close = df["close"].dropna()
        if len(close) >= 21:
            raw_rev20[code] = float(close.iloc[-1] / close.iloc[-21] - 1.0)

    rev20_rank: Dict[str, float] = {}
    rev20_top: List[str] = []
    if raw_rev20:
        s = pd.Series(raw_rev20)
        rev20_rank = {k: float(v) for k, v in s.rank(pct=True).to_dict().items()}
        picked = (s.nsmallest(cfg.topn) if cfg.rev20_pick == "low"
                  else s.nlargest(cfg.topn))
        rev20_top = list(picked.index)

    # union, holdings first, then rev20_top, then remaining watchlist (老路径才填)
    ordered: List[str] = []
    source: Dict[str, str] = {}
    for c in holdings:
        if c not in source:
            ordered.append(c); source[c] = "holding"
    for c in rev20_top:
        if c not in source:
            ordered.append(c); source[c] = "rev20_top"
    if not cfg.pool:
        for c in watch:
            if c not in source:
                ordered.append(c); source[c] = "watchlist"

    return CandidateResult(
        codes=ordered, rev20_rank=rev20_rank, universe_source=source,
        asof_prev=prev if prev is not None else "",
    )
```

- [ ] **Step 12: 跑测试验通过**

Run: `pytest tests/test_candidate_pool_mode.py -v`
Expected: 5 pass

### 1.4 写 _MockAgent v2 失败测试

- [ ] **Step 13: 写测试**

Create `tests/test_mock_agent_v2.py`:
```python
"""_MockAgent P2: 可配 hold_days + take_profit + stop_loss + 决策优先级"""
import pytest
import asyncio
from financial_analyst.buddy.backtest_run import _MockAgent


class _StubInp:
    """模拟 DecisionInput: candidates / holdings / rev20_rank / unrealized_pct"""
    def __init__(self, candidates=(), holdings=None, rev20=None, unrealized=None):
        self.candidates = list(candidates)
        self.holdings = holdings or {}
        self.rev20_rank = rev20 or {}
        self.unrealized_pct = unrealized or {}   # code -> 浮动盈亏 pct
        self.date = "2026-05-23"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class TestMockAgentHoldDays:
    def test_default_hold_days_is_3(self):
        ag = _MockAgent()
        assert ag._hold_days == 3

    def test_custom_hold_days(self):
        ag = _MockAgent(hold_days=5)
        assert ag._hold_days == 5

    def test_holds_until_hold_days_then_sells(self):
        ag = _MockAgent(hold_days=4)
        # day 1: 买入 SH600000
        d1 = _run(ag.decide(_StubInp(candidates=["SH600000"], rev20={"SH600000": 0.1})))
        assert d1.decisions[0].action == "buy"
        # day 2-4: 持有 (3 个日子, _held_since 从 1 涨到 4)
        for day in range(3):
            d = _run(ag.decide(_StubInp(candidates=[], holdings={"SH600000": 1000})))
            assert all(leg.action != "sell" for leg in d.decisions)
        # day 5: _held_since 涨到 4 == hold_days → sell
        d5 = _run(ag.decide(_StubInp(candidates=[], holdings={"SH600000": 1000})))
        assert any(leg.action == "sell" and leg.code == "SH600000" for leg in d5.decisions)


class TestMockAgentTakeProfit:
    def test_take_profit_triggers_early_sell(self):
        ag = _MockAgent(hold_days=10, take_profit_pct=0.05)
        # day 1: 买入
        _run(ag.decide(_StubInp(candidates=["SH600000"], rev20={"SH600000": 0.1})))
        # day 2: 持仓收益 6% > 5% → 触发止盈 sell (不等 hold_days)
        d2 = _run(ag.decide(_StubInp(candidates=[],
                                      holdings={"SH600000": 1000},
                                      unrealized={"SH600000": 0.06})))
        sells = [l for l in d2.decisions if l.action == "sell"]
        assert len(sells) == 1
        assert sells[0].code == "SH600000"
        assert "止盈" in sells[0].reason


class TestMockAgentStopLoss:
    def test_stop_loss_triggers_early_sell(self):
        ag = _MockAgent(hold_days=10, stop_loss_pct=0.05)
        _run(ag.decide(_StubInp(candidates=["SH600000"], rev20={"SH600000": 0.1})))
        d2 = _run(ag.decide(_StubInp(candidates=[],
                                      holdings={"SH600000": 1000},
                                      unrealized={"SH600000": -0.06})))
        sells = [l for l in d2.decisions if l.action == "sell"]
        assert len(sells) == 1
        assert "止损" in sells[0].reason


class TestMockAgentDecisionPriority:
    def test_take_profit_beats_hold_days(self):
        ag = _MockAgent(hold_days=10, take_profit_pct=0.05, stop_loss_pct=0.05)
        _run(ag.decide(_StubInp(candidates=["SH600000"], rev20={"SH600000": 0.1})))
        d2 = _run(ag.decide(_StubInp(candidates=[],
                                      holdings={"SH600000": 1000},
                                      unrealized={"SH600000": 0.10})))
        assert any("止盈" in l.reason for l in d2.decisions if l.action == "sell")
```

- [ ] **Step 14: 跑测试验失败**

Run: `pytest tests/test_mock_agent_v2.py -v`
Expected: 5+ fails (TypeError: __init__ got unexpected keyword 'hold_days', AttributeError _hold_days...)

- [ ] **Step 15: 重写 _MockAgent**

Edit `src/financial_analyst/buddy/backtest_run.py`:

替换 `_MOCK_HOLD_DAYS` 常量 + `_MockAgent` 类 (L23-68):
```python
# (删除 _MOCK_HOLD_DAYS = 3 常量行)


class _MockAgent:
    """确定性盘前决策 (0 次 LLM, 不依赖 DASHSCOPE key):

    决策优先级 (高 → 低):
      1. 持仓收益 ≥ take_profit_pct → sell (止盈)
      2. 持仓收益 ≤ -stop_loss_pct  → sell (止损)
      3. 持有 ≥ hold_days           → sell (定时了结)
      4. 空仓 + 有候选              → 对 rev20 分位最低 (跌最多) 的 1 只 buy 50%
      5. 否则                       → hold

    raw 写成结构化 dict (含 decisions 列表), 以便前端理由回退能取到。
    """
    def __init__(self, hold_days: int = 3,
                 take_profit_pct: Optional[float] = None,
                 stop_loss_pct: Optional[float] = None):
        self._n = 0
        self._hold_days = hold_days
        self._take_profit_pct = take_profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._held_since: dict = {}   # code -> 已持有的决策日计数

    @property
    def n_calls(self):
        return self._n

    async def decide(self, inp) -> Decision:
        legs: List[DecisionLeg] = []
        holdings = inp.holdings or {}
        unrealized = getattr(inp, "unrealized_pct", {}) or {}

        # 推进持有计数
        for code in list(self._held_since):
            if code in holdings:
                self._held_since[code] += 1
            else:
                self._held_since.pop(code, None)

        # 优先级 1+2+3: 检查所有持仓的止盈/止损/到期
        for code in list(holdings):
            pct = unrealized.get(code, 0.0)
            if self._take_profit_pct is not None and pct >= self._take_profit_pct:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 止盈 (收益 {pct:+.1%} ≥ {self._take_profit_pct:.1%})"))
                continue
            if self._stop_loss_pct is not None and pct <= -self._stop_loss_pct:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 止损 (收益 {pct:+.1%} ≤ -{self._stop_loss_pct:.1%})"))
                continue
            n = self._held_since.get(code, 0)
            if n >= self._hold_days:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 持有满 {n} 日 (阈值 {self._hold_days}), 了结"))

        # 优先级 4: 空仓 + 有候选 + 本轮没产 sell → buy 最低 rev20
        if not holdings and inp.candidates and not legs:
            top = sorted(inp.candidates,
                         key=lambda c: inp.rev20_rank.get(c, 1.0))[0]
            legs = [DecisionLeg(code=top, action="buy", weight_pct=50.0,
                                stop_loss=0.0,
                                reason=f"mock: rev20 分位最低({top}), 反转介入")]
            self._held_since[top] = 0

        raw = {"market_view": "mock 决策(确定性)",
               "decisions": [asdict(l) for l in legs], "warnings": []}
        return Decision(market_view=raw["market_view"], decisions=legs,
                        warnings=[], raw=raw)
```

Top imports 确认有 `from typing import List, Optional`. 删除 `_MOCK_HOLD_DAYS = 3` 常量行.

- [ ] **Step 16: 跑测试验通过**

Run: `pytest tests/test_mock_agent_v2.py -v`
Expected: 5+ pass

### 1.5 写 run_backtest end-to-end 失败测试

- [ ] **Step 17: 写测试**

Create `tests/test_backtest_run_end_to_end_v2.py`:
```python
"""buddy.backtest_run.run_backtest 接住 P2 新字段: pool/hold_days/take/stop"""
import pytest
import asyncio
from financial_analyst.buddy.server import BacktestRunReq
from financial_analyst.buddy.backtest_run import run_backtest


# 标 slow + 需要真 data layer (csi_fast 成分股), 在缺数据环境会 skip
pytestmark = pytest.mark.slow


@pytest.mark.asyncio
async def test_run_backtest_threads_pool_to_candidate_config():
    """req.pool='csi_fast' → CandidateConfig.pool='csi_fast' → 池子模式跑"""
    try:
        from financial_analyst.data.universe import resolve_universe_codes
        if not resolve_universe_codes("csi_fast"):
            pytest.skip("csi_fast 池子未解析 (缺 universes/csi_fast.txt 或 index_constituents.parquet)")
    except Exception as e:
        pytest.skip(f"data layer 缺: {e}")
    req = BacktestRunReq(
        start="2026-05-23", end="2026-05-30",
        pool="csi_fast", hold_days=3, mode="mock", candidate_topn=5,
    )
    result = await run_backtest(req)
    trades = result.get("trades", [])
    actions = [t["action"] for t in trades]
    assert "buy" in actions, f"窗口内应有 buy, trades={trades}"


@pytest.mark.asyncio
async def test_run_backtest_threads_hold_days_to_mock_agent():
    """req.hold_days=5 → _MockAgent(hold_days=5) — 不报错就 OK"""
    try:
        from financial_analyst.data.universe import resolve_universe_codes
        if not resolve_universe_codes("csi_fast"):
            pytest.skip("csi_fast 缺")
    except Exception as e:
        pytest.skip(f"data layer 缺: {e}")
    req = BacktestRunReq(
        start="2026-05-19", end="2026-05-30",
        pool="csi_fast", hold_days=5, mode="mock", candidate_topn=3,
    )
    result = await run_backtest(req)
    assert result.get("status") != "error", result.get("error")
```

- [ ] **Step 18: 跑测试验失败**

Run: `pytest tests/test_backtest_run_end_to_end_v2.py -v -m "slow"`
Expected: 2 fails OR skips (run_backtest 没把 req.pool 串到 CandidateConfig)

- [ ] **Step 19: 改 run_backtest 串联新字段**

Edit `src/financial_analyst/buddy/backtest_run.py:run_backtest()`:

找到 `CandidateConfig(topn=req.candidate_topn)` 那行 (L119 附近), 改成:
```python
candidate=CandidateConfig(
    topn=req.candidate_topn,
    pool=req.pool,        # P2: 池子模式
),
```

找到 mock 模式的 `_MockAgent()` 实例化, 改成:
```python
if req.mode == "mock":
    agent = _MockAgent(
        hold_days=req.hold_days,
        take_profit_pct=req.take_profit_pct,
        stop_loss_pct=req.stop_loss_pct,
    )
```

(真 LLM 模式不动 — 第一版 LLM 自己决定动作, 不强加 take/stop)

- [ ] **Step 20: 跑测试验通过**

Run: `pytest tests/test_backtest_run_end_to_end_v2.py -v -m "slow"`
Expected: 2 pass (or skip 如缺 csi_fast)

### 1.6 后端全量回归 + commit

- [ ] **Step 21: 跑后端相关测试**

Run: `pytest tests/test_backtest_*.py tests/test_candidate_*.py tests/test_mock_agent_*.py -v --tb=short`
Expected: 全过 (老 tests 不破)

- [ ] **Step 22: 跑全仓库快速回归**

Run: `pytest -x -q --tb=line --ignore=tests/test_backtest_panel_ux_e2e.py -m "not slow"`
Expected: 全过 (修破的)

- [ ] **Step 23: Commit**

```bash
git add src/financial_analyst/buddy/server.py \
        src/financial_analyst/backtest/candidate.py \
        src/financial_analyst/buddy/backtest_run.py \
        tests/test_backtest_run_req_v2.py \
        tests/test_candidate_pool_mode.py \
        tests/test_mock_agent_v2.py \
        tests/test_backtest_run_end_to_end_v2.py
git commit -m "feat(backtest): BacktestRunReq 扩 pool/hold_days/factor/止盈止损 + CandidateConfig pool 语义切换 + _MockAgent 决策优先级"
```

---

## Task 2: 前端 BacktestMode 完整重构 (P0+P1+P2)

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (BacktestMode @ L1750-1896 整体重写)
- Modify: `src/financial_analyst/ui/quant.html` (`?v=` bump)

### 2.1 加 BacktestStrategyBanner (P0.1)

- [ ] **Step 1: 在 BacktestMode 上方加 component**

Edit `quant.jsx` 在 `function BacktestMode()` 上方插入:
```jsx
// ─────── BacktestStrategyBanner — Mock vs Real 说清楚回测什么 (P0.1) ───────
function BacktestStrategyBanner({ mode }) {
  if (mode === 'mock') {
    return (
      <div style={{
        padding: '12px 16px', marginBottom: 12,
        border: '1px solid var(--line)', background: 'var(--paper-1)',
        borderLeft: '3px solid var(--dai)', fontSize: 12,
      }}>
        <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
          📊 <strong>Mock 模式</strong> · 演示数据通路, <span style={{ color: 'var(--dai)' }}>⚠ 不是盈利策略</span>
        </div>
        <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
          每次空仓时买入候选池中 <code className="mono">rev_20</code> 分位最低 (跌得最惨) 的 1 只,
          持有 <code className="mono">N</code> 个交易日后无条件了结. 0 次 LLM 调用, 确定性, 可手算核对.
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 11, marginTop: 6 }}>
          用途: 验证 数据→决策→撮合→净值 链路通畅. 真实策略请切 <strong>Real LLM</strong> 模式.
        </div>
      </div>
    );
  }
  return (
    <div style={{
      padding: '12px 16px', marginBottom: 12,
      border: '1px solid var(--line)', background: 'var(--paper-1)',
      borderLeft: '3px solid var(--zhu)', fontSize: 12,
    }}>
      <div className="serif" style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>
        🤖 <strong>Real LLM</strong> · 真实策略回测 (慢, 单日窗口 ~6min)
      </div>
      <div style={{ color: 'var(--ink-2)', lineHeight: 1.7 }}>
        每日盘前调用 qwen3.5-plus, 输入: 候选池 Top-N · 当前持仓 · rev_20 分位 · 当日新闻 + 事件摘要 (PIT-safe).
        输出 5 档动作 (buy/add/hold/reduce/sell), 每条带 reason.
      </div>
      <div style={{ color: 'var(--ink-3)', fontSize: 11, marginTop: 6 }}>
        决策被 prompt 哈希缓存 — 同样输入只调一次 LLM (<code className="mono">.fa/decision_cache</code>).
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 在 BacktestMode JSX 控件条之后插 banner**

找 `<button onClick={start_run} ...>起回测 ▶</button>` 那段所在的 `<div>` 关闭后, 插:
```jsx
<BacktestStrategyBanner mode={mode} />
```

(在 `{(run.loading || polling) && <Loading ...>}` 之前)

### 2.2 加 BacktestSummaryChips + PoolFilterPopover (P0.2 + P1.3)

- [ ] **Step 3: 加 BacktestSummaryChips component**

在 BacktestStrategyBanner 下方加:
```jsx
// ─────── BacktestSummaryChips — 候选池+因子+窗口+持有期 一行 chip 串 (P0.2) ───────
function BacktestSummaryChips({ d, onPoolClick }) {
  const p = d.params || {};
  const tradeDays = d.nav && d.nav.dates ? d.nav.dates.length : '?';
  const factorLabel = p.factor_name || 'rev_20';
  const poolLabel = p.pool || '(旧 watchlist 模式)';
  const modeLabel = p.mode === 'mock' ? 'Mock' : 'Real LLM';
  return (
    <div style={{
      padding: '10px 14px', marginBottom: 14,
      border: '1px solid var(--line-soft)', background: 'var(--paper-2)',
      display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12,
      fontSize: 11, fontFamily: 'var(--mono)',
    }}>
      <span style={{ color: 'var(--ink-2)' }}>候选 N=<strong>{p.candidate_topn}</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>◀</span>
      <span onClick={onPoolClick} style={{
        cursor: 'pointer', textDecoration: 'underline dotted', color: 'var(--ink)',
      }} title="点开看候选池过滤逻辑">
        池: <strong>{poolLabel}</strong>
      </span>
      <span style={{ color: 'var(--ink-3)' }}>◀</span>
      <span style={{ color: 'var(--ink-2)' }}>排序: <code>{factorLabel}</code> ↑</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>窗口: <strong>{p.start}</strong> → <strong>{p.end}</strong> ({tradeDays} 个交易日)</span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>模式: <strong>{modeLabel}</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>持有: <strong>{p.hold_days || 3} 日</strong></span>
      <span style={{ color: 'var(--ink-3)' }}>·</span>
      <span style={{ color: 'var(--ink-2)' }}>撮合: <strong>{p.match_freq}</strong></span>
    </div>
  );
}
```

- [ ] **Step 4: 加 PoolFilterPopover (P1.3)**

```jsx
// ─────── PoolFilterPopover — 池过滤逻辑浮层 (P1.3) ───────
function PoolFilterPopover({ pool, topn, onClose }) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.4)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 540, padding: 24, background: 'var(--paper)', border: '1px solid var(--line)',
      }}>
        <div className="serif" style={{ fontSize: 14, marginBottom: 12 }}>
          候选池构造流程 · 当前 <code className="mono">{pool}</code>
        </div>
        <ol className="mono" style={{ fontSize: 11.5, lineHeight: 1.9, color: 'var(--ink-2)', paddingLeft: 22 }}>
          <li>全 <strong>{pool}</strong> 成分股 (来自 <code>stock_data/parquet/index_constituents.parquet</code>)</li>
          <li>叠加当前持仓 (避免持仓掉出候选导致无法平仓)</li>
          <li>排除 sentinel (SH999999 等占位代码)</li>
          <li>对每只在 ≤T-1 close 上算 <code>rev_20 = close[T-1]/close[T-21] - 1</code></li>
          <li>按 rev_20 <strong>升序</strong> 排列 (跌得最惨的优先)</li>
          <li>取前 N=<strong>{topn}</strong> 作为候选池</li>
        </ol>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 12 }}>
          注: 池子模式 (pool 非空) 不引入 watchlist; 老 WatchLoop 实盘盯盘仍走 holdings∪watchlist 路径.
        </div>
        <button onClick={onClose} style={{
          marginTop: 16, padding: '6px 14px', background: 'var(--ink)', color: 'var(--paper)',
          border: 'none', fontSize: 11, cursor: 'pointer', fontFamily: 'var(--serif)',
        }}>关闭</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: BacktestMode 加 popover state + 渲染**

`BacktestMode` 函数顶部 useState 区加:
```jsx
const [showPoolPopover, setShowPoolPopover] = useState(false);
const [selectedTrade, setSelectedTrade] = useState(null);
```

在 `{d && ...}` 渲染区, 把现 `<Panel title="组合表现" ...>` 上面加:
```jsx
<BacktestSummaryChips d={d} onPoolClick={() => setShowPoolPopover(true)} />
```

在 BacktestMode 返回的最外层 `<div>` 内末尾 (return 前) 加:
```jsx
{showPoolPopover && d && (
  <PoolFilterPopover
    pool={(d.params && d.params.pool) || 'csi300'}
    topn={(d.params && d.params.candidate_topn) || 20}
    onClose={() => setShowPoolPopover(false)} />
)}
{selectedTrade && <TradeReasonModal trade={selectedTrade} d={d} onClose={() => setSelectedTrade(null)} />}
```

### 2.3 加 TradeReasonModal (P0.3 + P1.2)

- [ ] **Step 6: 加 Section helper + TradeReasonModal component**

```jsx
// ─────── Section — modal 小标题 + 分隔线 ───────
function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="mono" style={{
        fontSize: 9.5, color: 'var(--ink-3)', letterSpacing: '0.15em',
        textTransform: 'uppercase', marginBottom: 6, paddingBottom: 4, borderBottom: '1px solid var(--line-soft)',
      }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}

// ─────── TradeReasonModal — 交易理由可点击展开 (P0.3 + P1.2) ───────
function TradeReasonModal({ trade, d, onClose }) {
  const [rawExpanded, setRawExpanded] = useState(false);
  if (!trade || !d) return null;
  const day = (d.decisions && d.decisions[trade.date]) || {};
  const legs = day.decisions || [];
  const marketView = day.market_view || '—';
  const raw = day.raw || null;
  const warnings = day.warnings || [];
  const isMock = (d.mode || (d.params && d.params.mode)) === 'mock';
  const rawStr = raw ? JSON.stringify(raw, null, 2) : null;
  const rawPreview = rawStr && rawStr.length > 200 ? rawStr.slice(0, 200) + '…' : rawStr;
  const rawHasError = raw && raw._error === 'json';
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,20,0.5)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        maxWidth: 680, width: '100%', maxHeight: '85vh', overflow: 'auto',
        padding: 22, background: 'var(--paper)', border: '1px solid var(--line)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
          <span className="serif" style={{ fontSize: 14 }}>
            {trade.date} · <code className="mono">{trade.code}</code> · <span style={{
              color: trade.action === 'buy' ? 'var(--zhu)' : 'var(--dai)',
            }}>{trade.action}</span>
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: 'var(--ink-3)',
          }}>×</button>
        </div>

        {rawHasError && (
          <div style={{ padding: 8, marginBottom: 12, background: '#fff5e6', border: '1px solid var(--jin)', fontSize: 11 }}>
            ⚠ LLM 输出非合法 JSON, 已 fallback (原始文本见 <code>raw._raw</code>)
          </div>
        )}

        <Section title="当日 market_view">
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7 }}>{marketView}</div>
        </Section>

        <Section title="本笔 reason">
          <div className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.7 }}>
            {trade.reason || (legs.find(l => l.code === trade.code) || {}).reason || '—'}
          </div>
        </Section>

        <Section title={`当日全部决策 (${legs.length} 条)`}>
          {legs.length === 0 ? <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>—</span> : (
            <ol style={{ paddingLeft: 18, margin: 0 }}>
              {legs.map((l, i) => (
                <li key={i} className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 4 }}>
                  [{i + 1}] <span style={{ color: l.action === 'buy' ? 'var(--zhu)' : 'var(--dai)' }}>{l.action}</span>
                  {' '}<code>{l.code}</code> {l.weight_pct ? `${l.weight_pct}%` : ''} stop={l.stop_loss}
                  <div style={{ paddingLeft: 18, color: 'var(--ink-3)', fontFamily: 'var(--serif)' }}>{l.reason}</div>
                </li>
              ))}
            </ol>
          )}
        </Section>

        {!isMock && rawStr && (
          <Section title="LLM 返回原文 (raw JSON)">
            <pre className="mono" style={{
              fontSize: 10.5, padding: 10, background: 'var(--paper-2)', border: '1px solid var(--line-soft)',
              whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
            }}>{rawExpanded ? rawStr : rawPreview}</pre>
            {rawStr.length > 200 && (
              <button onClick={() => setRawExpanded(!rawExpanded)} style={{
                marginTop: 6, border: 'none', background: 'transparent', color: 'var(--zhu)',
                fontSize: 10.5, cursor: 'pointer', fontFamily: 'var(--mono)',
              }}>{rawExpanded ? '收起 ▴' : '展开看全文 ▾'}</button>
            )}
          </Section>
        )}

        {warnings.length > 0 && (
          <Section title="当日警告">
            <ul style={{ paddingLeft: 18, fontSize: 11, color: 'var(--jin)' }}>
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </Section>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: 交易表行 onClick 接 modal**

找 `d.trades.map((t, i) => (...))` 那段, 给 `<div key={i} className="hover-row" ...>` 加:
```jsx
onClick={() => setSelectedTrade(t)}
style={{ cursor: 'pointer', /* ...existing... */ }}
```

理由列最右边加图标:
```jsx
<span className="serif" style={{ ... existing ... }}>{reasonFor(t)} <span style={{color:'var(--ink-3)',fontSize:9}}>🔍</span></span>
```

### 2.4 加 KPI tooltip (P1.1)

- [ ] **Step 8: 扩 Kpi component 接 tooltip prop**

找 `Kpi` 函数定义. 改成接 `tooltip` 并透传到外层 div title:
```jsx
function Kpi({ label, value, dir, last, tooltip }) {
  return (
    <div title={tooltip} style={{ /* ...existing... */ }}>
      <div className="mono" style={{ /* ...existing... */ }}>
        {label}{tooltip && <span style={{fontSize:8,color:'var(--ink-3)',marginLeft:2}}>ⓘ</span>}
      </div>
      {/* ...existing value/dir... */}
    </div>
  );
}
```

- [ ] **Step 9: 8 KPI 加 tooltip 文案**

替换现 8 个 Kpi 调用:
```jsx
<Kpi label="年化"     value={pct(k.ann_return)} dir={dirOf(k.ann_return)}
     tooltip="年化收益率 = (1 + 区间总收益)^(250/区间交易日) − 1" />
<Kpi label="Sharpe"   value={n2(k.sharpe, 2)}
     tooltip="夏普比率 = 年化收益 / 年化波动率 (无风险=0)" />
<Kpi label="最大回撤"  value={pct(k.max_drawdown)} dir={k.max_drawdown ? 'down' : undefined}
     tooltip="最大回撤 = max((peak − trough) / peak), 滚动统计" />
<Kpi label="Calmar"   value={n2(k.calmar, 2)} last
     tooltip="年化收益 / |最大回撤| · 抗回撤能力指标" />
<Kpi label="波动率"    value={pct(k.volatility)}
     tooltip="年化波动率 = std(日收益) × √250" />
<Kpi label="换手"     value={pct(k.turnover)}
     tooltip="区间总成交额 / 期末总资产 / 年化系数 (来自 portfolio.py)" />
<Kpi label="胜率(日)"  value={pct(k.win_rate)}
     tooltip="净值正收益日数 / 总交易日数" />
<Kpi label="逐笔胜率"  value={pct(k.trade_win_rate)} last
     tooltip="盈利卖单 / 总卖单 (action='sell' 且 pnl > 0)" />
```

### 2.5 加高级控件 (P2.4)

- [ ] **Step 10: 加 advanced state + UI**

`BacktestMode` 顶部 useState 加:
```jsx
const [showAdv, setShowAdv] = useState(false);
const [pool, setPool] = useState('csi300');
const [holdDays, setHoldDays] = useState(3);
const [factorName, setFactorName] = useState('rev_20');
const [stopLossEnabled, setStopLossEnabled] = useState(false);
const [stopLossPct, setStopLossPct] = useState(0.05);
const [takeProfitEnabled, setTakeProfitEnabled] = useState(false);
const [takeProfitPct, setTakeProfitPct] = useState(0.1);
```

控件条最后加 "高级 ▾" 按钮 (在 `<button onClick={start_run}>` 后):
```jsx
<button onClick={() => setShowAdv(!showAdv)} className="hover-pill"
  style={{ padding: '6px 12px', border: '1px solid var(--line)', background: 'transparent',
           fontFamily: 'var(--serif)', fontSize: 11, cursor: 'pointer' }}>
  高级 {showAdv ? '▴' : '▾'}
</button>
```

控件条 div 关闭后 (banner 上方), 加高级控件区:
```jsx
{showAdv && (
  <div style={{
    display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap',
    padding: '10px 12px', marginBottom: 12, border: '1px solid var(--line-soft)', background: 'var(--paper-1)',
  }}>
    <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>候选池
      <select value={pool} onChange={e => setPool(e.target.value)}
        style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)',
                 fontFamily: 'var(--mono)', fontSize: 12 }}>
        <option value="csi300">csi300 (300 只)</option>
        <option value="csi_fast">csi_fast (~100 大盘)</option>
        <option value="csi500">csi500 (500 只)</option>
        <option value="csi800">csi800 (800 只)</option>
      </select></label>
    <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>持有期 (日)
      <input type="number" min={1} max={60} value={holdDays}
        onChange={e => setHoldDays(Number(e.target.value))}
        style={{ display: 'block', marginTop: 3, padding: '5px 8px', width: 70,
                 border: '1px solid var(--line)', fontFamily: 'var(--mono)', fontSize: 12 }} /></label>
    <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}
           title="第一版只支持 rev_20, 其它因子下轮接 /factor/list">
      排序因子
      <select value={factorName} disabled
        style={{ display: 'block', marginTop: 3, padding: '5px 8px', border: '1px solid var(--line)',
                 fontFamily: 'var(--mono)', fontSize: 12, opacity: 0.6 }}>
        <option value="rev_20">rev_20 (反转)</option>
      </select></label>
    <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', gap: 6 }}>
      <input type="checkbox" checked={stopLossEnabled} onChange={e => setStopLossEnabled(e.target.checked)} />
      止损 %
      <input type="number" min={1} max={50} step={1} disabled={!stopLossEnabled}
        value={Math.round(stopLossPct * 100)}
        onChange={e => setStopLossPct(Number(e.target.value) / 100)}
        style={{ width: 50, padding: '4px 6px', border: '1px solid var(--line)',
                 fontFamily: 'var(--mono)', fontSize: 11, opacity: stopLossEnabled ? 1 : 0.4 }} />
    </label>
    <label className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', alignItems: 'center', gap: 6 }}>
      <input type="checkbox" checked={takeProfitEnabled} onChange={e => setTakeProfitEnabled(e.target.checked)} />
      止盈 %
      <input type="number" min={1} max={200} step={1} disabled={!takeProfitEnabled}
        value={Math.round(takeProfitPct * 100)}
        onChange={e => setTakeProfitPct(Number(e.target.value) / 100)}
        style={{ width: 50, padding: '4px 6px', border: '1px solid var(--line)',
                 fontFamily: 'var(--mono)', fontSize: 11, opacity: takeProfitEnabled ? 1 : 0.4 }} />
    </label>
  </div>
)}
```

- [ ] **Step 11: start_run 透传新字段**

修改 `postJSON('/backtest/run', {...})` payload:
```jsx
const r = await postJSON('/backtest/run', {
  start: start || null, end: end || null,
  init_cash: Number(cash), candidate_topn: Number(topn), mode,
  match_freq: 'day',
  // P2 ↓
  pool, hold_days: Number(holdDays), factor_name: factorName,
  stop_loss_pct: stopLossEnabled ? stopLossPct : null,
  take_profit_pct: takeProfitEnabled ? takeProfitPct : null,
});
```

### 2.6 quant.html cache buster + manual smoke

- [ ] **Step 12: bump quant.html ?v=**

```bash
grep -n '?v=' src/financial_analyst/ui/quant.html
```

把所有 `?v=<old>` 改成 `?v=<old+1>` (或换成今日时间戳 `20260602b`).

- [ ] **Step 13: 手工烟测 (可选, e2e 会替你跑)**

启 backend:
```bash
cd G:/financial-analyst && fa serve --port 9999 &
```

启 UI:
```bash
cd G:/financial-analyst/src/financial_analyst/ui && python -m http.server 5173 &
```

浏览器开 http://localhost:5173/quant.html?v=<new>, 点 "Agent 回测" tab.

### 2.7 前端 commit

- [ ] **Step 14: Commit**

```bash
git add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git commit -m "feat(ui): BacktestMode P0+P1+P2 — banner/横条/交易modal/KPI tooltip/高级控件 (池/持有期/止盈止损)"
```

---

## Task 3: Playwright E2E + 全量回归

**Files:**
- Create: `tests/test_backtest_panel_ux_e2e.py`

### 3.1 写 Playwright e2e 测试

- [ ] **Step 1: 写测试**

Create `tests/test_backtest_panel_ux_e2e.py`:
```python
"""Playwright e2e: backtest panel P0+P1+P2 UX 真浏览器烟测.

跑 fa serve + http.server, 切 Agent 回测 tab, 验:
* P0.1 banner Mock 文案出现, 切 real → 文案切换
* P0.2 高级控件展开 → 改 pool/hold_days, 跑 mock 完成 → 横条 chip 显示
* P0.3 点击交易行 → modal 出 market_view + legs
* P1.1 KPI 8 个 tooltip 出现 (title attr)
* P1.3 点池 chip → popover 出 6 步过滤说明
"""
import subprocess, time, os
import pytest


pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stack():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc_be = subprocess.Popen(["fa", "serve", "--port", "9999"],
                                cwd="G:/financial-analyst", env=env)
    proc_ui = subprocess.Popen(["python", "-m", "http.server", "5173"],
                                cwd="G:/financial-analyst/src/financial_analyst/ui", env=env)
    time.sleep(10)   # cold start: lazy imports + chromadb 等
    yield "http://localhost:5173/quant.html"
    proc_be.terminate(); proc_ui.terminate()
    proc_be.wait(timeout=5); proc_ui.wait(timeout=5)


def test_backtest_panel_ux(stack):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(stack)
        # 切 backtest tab
        page.click("text=Agent 回测", timeout=10_000)
        # P0.1 banner Mock 文案
        page.wait_for_selector("text=Mock 模式", timeout=5_000)
        page.wait_for_selector("text=不是盈利策略")
        # 切 Real → banner 文案变
        page.click("text=真 LLM(慢)")
        page.wait_for_selector("text=Real LLM", timeout=3_000)
        page.click("text=Mock(秒级)")
        # 展开高级 (P2.4)
        page.click("text=高级 ▾")
        page.wait_for_selector("text=候选池", timeout=3_000)
        # 改 pool=csi_fast
        page.select_option("select", value="csi_fast")
        # 起回测
        page.click("text=起回测 ▶")
        # 等结果 (mock ~45s 含 100 只 rev_20 计算)
        page.wait_for_selector("text=组合表现", timeout=120_000)
        # P0.2 横条 chip
        page.wait_for_selector("text=csi_fast", timeout=3_000)
        page.wait_for_selector("text=持有")
        # P1.3 池 popover
        page.click("text=csi_fast")
        page.wait_for_selector("text=候选池构造流程", timeout=3_000)
        page.click("text=关闭")
        # P1.1 KPI tooltip (title attr)
        kpi_div = page.locator("text=Calmar").locator("..").first
        tooltip = kpi_div.get_attribute("title")
        assert tooltip and "年化收益" in tooltip, f"Calmar tooltip 缺失: {tooltip!r}"
        # P0.3 交易 modal — 先确认有交易
        if page.locator(".hover-row").count() > 0:
            page.locator(".hover-row").first.click()
            page.wait_for_selector("text=当日 market_view", timeout=3_000)
        browser.close()
```

- [ ] **Step 2: 跑测试验通过**

Run: `pytest tests/test_backtest_panel_ux_e2e.py -v -s --tb=short -m "slow"`
Expected: 1 pass (~2 min 真启 backend + browser)

如失败: 看 stdout 截图 / page snapshot, 修 jsx 或 backend, 回到 Task 2 对应 step.

### 3.2 全量回归

- [ ] **Step 3: 跑全仓库快速 (不含 slow)**

Run: `pytest -x -q --tb=short -m "not slow"`
Expected: 全过 (老 tests 不破)

- [ ] **Step 4: 跑全仓库带 slow**

Run: `pytest -q --tb=short`
Expected: 全过

- [ ] **Step 5: 修破的 (如有)**

如果有破:
- 看是不是改 candidate.py 影响了 WatchLoop 老 test → 验 `pool=None` 路径分支
- 看是不是改 _MockAgent 改了影响其它 test → 验 `_MOCK_HOLD_DAYS` 引用 (已删)
- 看 BacktestRunReq 新字段默认值是否破坏现有 endpoint test (默认 csi300 可能 break 现 mock 测试因为它原期望 watchlist 模式)

修到全过.

### 3.3 Commit + summary

- [ ] **Step 6: Commit e2e**

```bash
git add tests/test_backtest_panel_ux_e2e.py
git commit -m "test(e2e): backtest panel P0+P1+P2 Playwright 烟测 (banner/横条/modal/tooltip/popover)"
```

- [ ] **Step 7: 总览 commits**

Run: `git log --oneline -6`
Expected:
```
<sha3> test(e2e): backtest panel P0+P1+P2 Playwright 烟测
<sha2> feat(ui): BacktestMode P0+P1+P2 — banner/横条/交易modal/...
<sha1> feat(backtest): BacktestRunReq 扩 pool/hold_days/factor/...
4a08319 docs(spec): 修 P2.3 — CandidateConfig.pool 语义切换
cfa6749 docs(spec): backtest panel UX P0+P1+P2 (banner/横条/...)
73571b8 fix(paths): 加 pit_store_root field (integrate watch+backtest 漏接)
```

5 commit 总和: spec × 2 + 实现 × 3. 不推 origin (保留等一起推).

---

## DoD (从 spec 复制, 验收 checklist)

- [ ] `POST /backtest/run` 接受 `pool=csi_fast hold_days=5 take_profit_pct=0.1` → 200
- [ ] `POST /backtest/run` 拒 `pool=all` → 400 "全市场池请用 csi800 替代"
- [ ] `_MockAgent(hold_days=5)` 跑 6 日窗口产 buy@day1 + sell@day6 (硬编码 3 → 5 生效)
- [ ] 前端 Mock banner 显示 "演示数据通路, ⚠ 不是盈利策略" 红字
- [ ] 前端 Real banner 显示 LLM 输入字段列表
- [ ] 运行后顶部横条显示 "候选 N=20 ◀ 池 csi_fast ◀ 排序 rev_20 ↑ 窗口 ... 持有 5 日 撮合 day"
- [ ] 点击交易表任一行 → modal 显示 market_view + 当日全部 legs + 持仓快照
- [ ] hover KPI "Calmar" → tooltip 显示 "年化收益 / |最大回撤|"
- [ ] 高级控件 "持有期" 改 5 → 跑出来真的 5 日才 sell (端到端)
- [ ] Playwright 烟测全过
- [ ] `quant.html` ?v= bump (旧浏览器拿新 jsx 不踩缓存)
- [ ] 全量回归 不破
- [ ] 工作分支 feat/backtest-panel-ux, main 不动, 不推 origin
