# -*- coding: utf-8 -*-
"""/workflow/critique 端点 constraints 透传单测(研究回路停滞守卫的端点侧)。

只测 prompt 组装与向后兼容:constraints 非空 → 拼进 user prompt;缺省 → prompt
逐字不变(现有画布/帷幄调用方零行为变化)。LLM 打桩为抛错 → 规则兜底路径,零网络。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as wapi


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    return TestClient(app)


def _capture_llm(monkeypatch, seen):
    async def fake(system, user, temperature=0.2):
        seen["system"], seen["user"] = system, user
        raise RuntimeError("stub: 不走真 LLM")                     # → 规则兜底,端点仍 200
    monkeypatch.setattr(wapi, "_llm_complete", fake)


_G = {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}}], "edges": []}


def test_critique_constraints_in_prompt(monkeypatch):
    seen = {}
    _capture_llm(monkeypatch, seen)
    r = _client().post("/workflow/critique",
                       json={"goal": "g", "graph": _G, "metrics": {"rank_ic": -0.01},
                             "constraints": "求值只读 formula.expr,改方向必须改写表达式"})
    assert r.status_code == 200 and r.json()["ok"] is True          # 规则兜底,不断闭环
    assert "求值环境约束" in seen["user"]
    assert "求值只读 formula.expr" in seen["user"]


def test_critique_no_constraints_prompt_unchanged(monkeypatch):
    seen = {}
    _capture_llm(monkeypatch, seen)
    r = _client().post("/workflow/critique",
                       json={"goal": "g", "graph": _G, "metrics": {"rank_ic": -0.01}})
    assert r.status_code == 200
    assert "求值环境约束" not in seen["user"]                        # 缺省零行为变化
    assert seen["user"].endswith("请据指标诊断并只输出改进后的 {diagnosis,nodes,edges}。")
