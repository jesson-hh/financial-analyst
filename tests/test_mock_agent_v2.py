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
