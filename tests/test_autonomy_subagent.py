# -*- coding: utf-8 -*-
import asyncio

from guanlan_v2.autonomy import subagent as SA


class _Evt:
    def __init__(self, kind, payload=None):
        self.kind, self.payload = kind, payload


class _FakeAgent:
    """最小 BuddyAgent 桩:2 个 tool_call + 一段最终文本。记录构造参数供断言。"""
    created = {}

    def __init__(self, system_prompt=None, max_tool_iters=15, turn_token_budget=0):
        _FakeAgent.created = {"sp": system_prompt, "iters": max_tool_iters,
                              "budget": turn_token_budget}
        self._client = type("C", (), {"total_completion_tokens": 0, "n_calls": 0})()

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        _FakeAgent.created["allowed"] = set(allowed_tools or [])
        yield _Evt("tool_call", {"name": "ww_data_health"})
        yield _Evt("tool_result", {"ok": True})
        yield _Evt("text", "段落结论:数据全新鲜。")
        yield _Evt("done")


def test_run_section_agent_happy(tmp_path, monkeypatch):
    monkeypatch.setattr(SA, "_make_agent", lambda sp, iters, budget, seat: _FakeAgent(sp, iters, budget))
    out = tmp_path / "sec_c.md"
    r = SA.run_section_agent(name="data", system_prompt="s", brief_text="b",
                             allowed_tools={"ww_data_health"}, out_path=out)
    assert r["ok"] and "数据全新鲜" in r["text"] and r["tool_calls"] == 1
    assert out.read_text(encoding="utf-8") == r["text"]
    assert _FakeAgent.created["iters"] == 6 and _FakeAgent.created["budget"] == 6000
    assert _FakeAgent.created["allowed"] == {"ww_data_health"}


def test_run_section_agent_no_text_is_degraded(tmp_path, monkeypatch):
    class _Silent(_FakeAgent):
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            yield _Evt("done")

    monkeypatch.setattr(SA, "_make_agent", lambda *a: _Silent())
    r = SA.run_section_agent(name="x", system_prompt="s", brief_text="b",
                             allowed_tools=set(), out_path=tmp_path / "x.md")
    assert r["ok"] is False and "无文本产出" in r["error"]


def test_run_section_agent_timeout(tmp_path, monkeypatch):
    class _Hang(_FakeAgent):
        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            await asyncio.sleep(5)
            yield _Evt("done")

    monkeypatch.setattr(SA, "_make_agent", lambda *a: _Hang())
    r = SA.run_section_agent(name="x", system_prompt="s", brief_text="b",
                             allowed_tools=set(), out_path=tmp_path / "x.md",
                             timeout_sec=0.2)
    assert r["ok"] is False and "超时" in r["error"]


def test_confirm_tools_are_declined():
    assert asyncio.run(SA._auto_decline("ww_memory_write", {})) is False
