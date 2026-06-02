"""P5: /backtest/run + /backtest/result on build_app(). 端点壳走 TestClient;
done 态映射走直接 await run_backtest (toy reader) → 0 LLM, 不依赖 DASHSCOPE key。

关键设计取舍 (对抗审查):
* 不依赖 asyncio.create_task 在 TestClient 多请求间跑完 (Starlette TestClient
  每请求各起/关一个事件循环, 后台 task 可能随 POST 的临时 loop 销毁)。done 态
  映射真实性全走 asyncio.run(run_backtest(req)) 直测。
* 端点壳只测 run_id / 404 / 400 / 429。
* 并发探针 (test_backtest_does_not_block_loop) 锁 "回测在工作线程, 不冻事件循环"。
"""
from __future__ import annotations
import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app, BacktestRunReq

# 复用 engine 测试的 toy harness (同包测试可直接 import)
from tests.test_backtest_engine import _ToyLoader, _ToyReader
from financial_analyst.backtest.decision import Decision, DecisionLeg


def _patch_toy(monkeypatch):
    """让 run_backtest 内的 PitReader() 换成 toy reader (无真实数据)。"""
    loader = _ToyLoader()
    reader = _ToyReader(loader)
    monkeypatch.setattr(
        "financial_analyst.backtest.pit_reader.PitReader",
        lambda *a, **k: reader)
    return reader, loader


# ---- 端点壳: run 返 run_id ----
def test_run_returns_run_id(monkeypatch):
    _patch_toy(monkeypatch)
    c = TestClient(build_app())
    r = c.post("/backtest/run", json={"start": "2026-04-01", "end": "2026-04-03",
                                      "mode": "mock", "candidate_topn": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"].startswith("bt_")
    assert body["status"] == "running"
    assert body["mode"] == "mock"


# ---- 端点壳: 未知 run_id → 404 ----
def test_result_unknown_run_id():
    c = TestClient(build_app())
    r = c.get("/backtest/result/bt_doesnotexist")
    assert r.status_code == 404
    assert r.json()["status"] == "not_found"


# ---- 端点壳: 非法参数 → 400 ----
@pytest.mark.parametrize("payload", [
    {"mode": "wizardry"},
    {"mode": "mock", "init_cash": 0},
    {"mode": "mock", "candidate_topn": 0},
    {"mode": "mock", "start": "not-a-date"},
    {"mode": "mock", "start": "2026-04-05", "end": "2026-04-01"},
])
def test_run_bad_params(payload):
    c = TestClient(build_app())
    r = c.post("/backtest/run", json=payload)
    assert r.status_code == 400
    assert r.json()["status"] == "bad_request"


# ---- 映射真实性: 直接 await run_backtest (toy) → nav/kpi 对齐, 0 LLM ----
def test_run_backtest_maps_result(monkeypatch):
    _patch_toy(monkeypatch)
    from financial_analyst.buddy.backtest_run import run_backtest
    req = BacktestRunReq(start="2026-04-01", end="2026-04-03", mode="mock",
                         candidate_topn=5)
    body = asyncio.run(run_backtest(req))
    assert body["mode"] == "mock"
    # 归一化首点 (toy 无候选 → 无成交 → 首点恰为 1.0; 见下条 trades 覆盖用专门 stub)
    assert body["nav"]["series"][0] == 1.0
    assert len(body["nav"]["series"]) == len(body["nav"]["dates"])
    assert isinstance(body["trades"], list)
    assert isinstance(body["decisions"], dict)
    assert "ann_return" in body["kpi"] and "n_llm_calls" in body["kpi"]
    assert body["kpi"]["n_llm_calls"] == 0


# ---- mock 不构造 LLMClient (不依赖 DASHSCOPE key) ----
def test_mock_does_not_touch_llm(monkeypatch):
    _patch_toy(monkeypatch)
    import financial_analyst.backtest.decision as dec

    def _boom(*a, **k):
        raise AssertionError("mock 模式不应构造 LLMClient")

    monkeypatch.setattr(dec.LLMClient, "for_agent", staticmethod(_boom))
    from financial_analyst.buddy.backtest_run import run_backtest
    body = asyncio.run(run_backtest(BacktestRunReq(start="2026-04-01",
                       end="2026-04-03", mode="mock", candidate_topn=5)))
    assert body["kpi"]["n_llm_calls"] == 0


# ---- trades + reason 回退覆盖 (engine 的 _StubAgent raw={} 测不了; 用真 raw) ----
class _BuyThenSellAgent:
    """day1 buy (raw 带结构化 decisions), day3 sell → 强制产生 ≥1 笔成交,
    覆盖 _fills_to_trades + 前端 reasonFor 的 decisions 回退契约。"""
    def __init__(self):
        self._n = 0

    @property
    def n_calls(self):
        return self._n

    async def decide(self, inp):
        from dataclasses import asdict
        legs = []
        if inp.date == "2026-04-01":
            legs = [DecisionLeg(code="SH600001", action="buy", weight_pct=90.0,
                                stop_loss=8.0, reason="entry rev20")]
        elif inp.date == "2026-04-03" and inp.holdings:
            legs = [DecisionLeg(code="SH600001", action="sell", reason="take")]
        raw = {"market_view": "stub", "decisions": [asdict(l) for l in legs],
               "warnings": []}
        return Decision("stub", legs, [], raw)


def test_trades_and_reason_fallback(monkeypatch):
    reader, loader = _patch_toy(monkeypatch)
    import financial_analyst.buddy.backtest_run as br
    monkeypatch.setattr(br, "_MockAgent", _BuyThenSellAgent)  # mock 路径换成会交易的 stub
    body = asyncio.run(br.run_backtest(BacktestRunReq(
        start="2026-04-01", end="2026-04-03", mode="mock", candidate_topn=5)))
    trades = body["trades"]
    assert len(trades) >= 1
    buy = next(t for t in trades if t["action"] == "buy")
    assert buy["code"] == "SH600001" and buy["pnl"] == 0.0
    assert buy["reason"] == ""                 # broker 恒清空 fill.reason
    # 前端 reasonFor 会从 decisions[date] 回退 — 这里验数据通路存在该 reason
    day = body["decisions"][buy["date"]]
    leg = next(x for x in day["decisions"] if x["code"] == "SH600001")
    assert "entry" in leg["reason"]
    # 卖出有 realized_pnl, profit_factor 可能 inf → _jsonable 已转 null/有限值
    import json
    assert "Infinity" not in json.dumps(body) and "NaN" not in json.dumps(body)


# ---- 并发探针: 回测在工作线程, 不冻事件循环 (修了 blocker 才会绿) ----
def test_backtest_does_not_block_loop(monkeypatch):
    """起一个 mock 回测的同时打 /health, 断言 /health 不被回测阻塞。
    TestClient 串行泵 loop, 故这里用真 ASGI + httpx.AsyncClient 在同一 loop 并发。"""
    _patch_toy(monkeypatch)
    app = build_app()
    import httpx

    async def _probe():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as ac:
            r = await ac.post("/backtest/run",
                              json={"start": "2026-04-01", "end": "2026-04-03",
                                    "mode": "mock", "candidate_topn": 5})
            rid = r.json()["run_id"]
            # 回测在 to_thread 跑, 主 loop 应能立刻回 /health
            t0 = time.perf_counter()
            h = await ac.get("/health")
            dt = time.perf_counter() - t0
            assert h.status_code == 200
            # 轮询到终态
            res = {"status": "running"}
            for _ in range(100):
                res = (await ac.get("/backtest/result/" + rid)).json()
                if res["status"] != "running":
                    break
                await asyncio.sleep(0.05)
            assert res["status"] == "done"
            return dt
    dt = asyncio.run(_probe())
    assert dt < 2.0     # /health 不该被整段回测阻塞 (toy 很快, 给宽限)
