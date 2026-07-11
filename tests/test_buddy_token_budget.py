# -*- coding: utf-8 -*-
"""帷幄长轮 token 预算闸:纯函数判定矩阵 + 工厂 env 解析。默认 0=关,行为逐字节不变。"""
import asyncio

from financial_analyst.buddy.agent import BuddyAgent, _budget_verdict
from financial_analyst.buddy.tools import Tool, ToolResult


def test_verdict_matrix():
    assert _budget_verdict(0, 10 ** 9, 5, False) == "ok"      # 预算关=永不干预
    assert _budget_verdict(1000, 100, 3, False) == "ok"
    assert _budget_verdict(1000, 850, 3, False) == "warn"     # ≥80% 注入收敛提示
    assert _budget_verdict(1000, 850, 3, True) == "ok"        # 已警告过不重复
    assert _budget_verdict(1000, 1000, 3, False) == "stop"    # 耗尽=停循环诚实显形
    assert _budget_verdict(1000, 1200, 3, True) == "stop"
    assert _budget_verdict(1000, 1200, 0, False) == "ok"      # 首轮永不拦(至少答一次)


def test_agent_default_budget_off():
    a = BuddyAgent(system_prompt="t")
    assert a.turn_token_budget == 0


def test_factory_reads_env(monkeypatch):
    monkeypatch.setenv("CONSOLE_TURN_TOKEN_BUDGET", "50000")
    from guanlan_v2.console import api as capi
    a = capi._default_agent_factory("sid-test")
    assert a.turn_token_budget == 50000


class _StubClient:
    """假 LLM client:每次 ``chat()`` 都请求一次工具调用,并把
    ``total_completion_tokens`` 向前推进(模拟真实用量记账),直到 budget 判定 stop。
    第 3 次判定(iteration=2)应命中 stop——不应再有第 3 次 chat() 调用。"""

    def __init__(self):
        self.total_completion_tokens = 0
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.2):
        self.calls += 1
        # 第 1 次调用花 60 token,第 2 次再花 70 → 累计 130,越过 budget=100。
        self.total_completion_tokens += 60 if self.calls == 1 else 70
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call_{self.calls}",
                        "type": "function",
                        "function": {"name": "noop_tool", "arguments": "{}"},
                    }],
                }
            }]
        }


def _fake_get_tool(name):
    if name == "noop_tool":
        return Tool(
            name="noop_tool",
            description="test-only no-op tool",
            input_schema={"type": "object", "properties": {}},
            run=lambda **kwargs: ToolResult("ok"),
        )
    return None


def test_budget_stop_ends_turn_cleanly_no_phantom_error(monkeypatch):
    """端到端回归(评审 Critical):budget stop 分支必须直接 error+done+return,
    绝不能落进循环耗尽后的"达到 tool 调用上限"收尾块,产生事实错误的第二条 error。
    真跑 run_turn(桩 client + 桩工具),断言整个事件序列只有一条 error 且以
    error→done 收尾,不含"达到 tool 调用上限"字样。"""
    monkeypatch.setattr("financial_analyst.buddy.agent.get_tool", _fake_get_tool)

    agent = BuddyAgent(system_prompt="test", turn_token_budget=100, max_tool_iters=15)
    agent._client = _StubClient()

    async def _run():
        return [ev async for ev in agent.run_turn("测试问题")]

    events = asyncio.run(_run())
    kinds = [e.kind for e in events]
    errors = [e for e in events if e.kind == "error"]

    # 桩 client 只应被调用 2 次(iteration 0/1);第 3 次判定(iteration=2)在
    # 发起下一次 chat() 之前就应 stop——若 stop 分支误用 break 落进旧收尾块,
    # calls 数不变但会多出一条"达到 tool 调用上限"的假 error。
    assert agent._client.calls == 2
    assert len(errors) == 1, f"expected exactly one error event, got kinds={kinds}"
    assert "token 预算耗尽" in errors[0].payload
    assert "达到 tool 调用上限" not in errors[0].payload
    assert not any("达到 tool 调用上限" in str(e.payload) for e in events)
    assert kinds[-2:] == ["error", "done"]
