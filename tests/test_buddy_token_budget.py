# -*- coding: utf-8 -*-
"""帷幄长轮 token 预算闸:纯函数判定矩阵 + 工厂 env 解析。默认 0=关,行为逐字节不变。"""
import importlib

from financial_analyst.buddy.agent import BuddyAgent, _budget_verdict


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
