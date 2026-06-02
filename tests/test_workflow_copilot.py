"""SP-W2B Workflow Copilot — NL → workflow JSON 草案 测试.

3 块覆盖:
1. 上下文收集 (collect_context): 节点 / 因子 / 经验 chunks 形态
2. SSE 事件顺序 (POST /workflow/copilot/draft 端点): thought*N + draft + done
3. 错误处理: LLM 返非 JSON / workflow_json 引用未注册节点 → error event

LLM 一律走注入式 stub (``complete_fn`` / monkeypatch ``_complete_default``), 不调
真 LLM (qwen key 可能 401 用户已知).

KnowledgeIndex 一律走 stub (chroma 慢 + 测试机可能没数据).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

# 触发 @node 注册 — Copilot 需要节点目录非空才有意义
import financial_analyst.workflow.mock_nodes  # noqa: F401
import financial_analyst.factors.workflow_nodes  # noqa: F401

from financial_analyst.buddy.server import build_app
from financial_analyst.workflow import copilot as _copilot
from financial_analyst.workflow.copilot import (
    CopilotContext, build_messages, collect_context, parse_and_validate,
)


# ---------------------------------------------------------------------------
# 共用 fixture: workflow 子系统 tmp + 无网络/无 KnowledgeIndex
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_workflow_env(tmp_path, monkeypatch):
    """同 test_workflow_rest.py 套路, 隔离 workflow 目录."""
    defs_root = tmp_path / "workflow_defs"
    parquet_root = tmp_path / "parquet"
    parquet_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FA_WORKFLOW_DEFS_ROOT", str(defs_root))
    monkeypatch.setenv("FA_PARQUET_ROOT", str(parquet_root))
    yield {"defs_root": defs_root, "parquet_root": parquet_root}


@pytest.fixture
def stub_knowledge(monkeypatch):
    """让 collect_context 拿假经验片段, 不调真 KnowledgeIndex (chroma)."""

    class _FakeChunk:
        def __init__(self, source, section, text, score=0.5):
            self.source = source
            self.section = section
            self.text = text
            self.score = score

    class _FakeIndex:
        def __init__(self, *a, **kw):
            pass
        def search(self, query, k=5):
            return [
                _FakeChunk("factor_insights.md", "rev_20 历史",
                          "rev_20 在 A 股截面是最强反转因子, ICIR 约 0.06."),
                _FakeChunk("pitfalls.md", "游资博弈票排除",
                          "小盘+高 PE+短期暴涨的票模型失效."),
                _FakeChunk("rating_system.md", "市值分层",
                          "大盘股因子面强制归零."),
            ]

    monkeypatch.setattr(
        "financial_analyst.data.knowledge_index.KnowledgeIndex",
        _FakeIndex,
    )
    yield


# ---------------------------------------------------------------------------
# 1. 上下文收集 — collect_context 拿到的 nodes/factors/knowledge_chunks 形态
# ---------------------------------------------------------------------------


def test_collect_context_basic(isolated_workflow_env, stub_knowledge):
    """节点 + 因子 + 经验 都非空, 节点 demo group 被过滤."""
    ctx = collect_context("用反转因子在 csi300 跑 IC", "csi300_active", "day")
    # 节点: ≥5 (SP-W2A 5 个真节点), demo 节点被过滤
    assert len(ctx.nodes) >= 5
    types = {n["type"] for n in ctx.nodes}
    assert "data.universe" in types
    assert "data.load_panel" in types
    assert "factor.from_registry" in types
    assert "factor.from_expression" in types
    assert "eval.factor_report" in types
    # demo 节点不应出现 (group='demo')
    assert "data.constant_universe" not in types
    assert "factor.zeros" not in types
    assert "eval.row_count" not in types

    # 节点形态 — group + tag 字段
    n = next(x for x in ctx.nodes if x["type"] == "data.universe")
    assert n["group"] == "data"
    assert "data" in n["tag"]
    assert n["params_schema_summary"]  # 非空 — 至少 'name' 字段

    # 因子 ≥ 100 (442 alpha + user)
    assert len(ctx.factors) >= 100
    names = {f["name"] for f in ctx.factors}
    assert any(re.match(r"alpha0\d{2}", n) for n in names) or any(re.match(r"gtja0\d{2}", n) for n in names)

    # 经验 = stub 喂的 3 条
    assert len(ctx.knowledge_chunks) == 3
    chk = ctx.knowledge_chunks[0]
    assert "source" in chk and "section" in chk and "text" in chk
    assert chk["source"] == "factor_insights.md"
    assert chk["section"] == "rev_20 历史"

    # goal / universe / freq 透传
    assert ctx.goal == "用反转因子在 csi300 跑 IC"
    assert ctx.universe_default == "csi300_active"
    assert ctx.freq_default == "day"


def test_collect_context_skip_knowledge(isolated_workflow_env):
    """skip_knowledge=True → knowledge_chunks 空, 不调 KnowledgeIndex."""
    ctx = collect_context("无所谓", skip_knowledge=True)
    assert ctx.knowledge_chunks == []
    # 但节点 + 因子还是要拿到
    assert len(ctx.nodes) >= 5


def test_build_messages_shape(isolated_workflow_env, stub_knowledge):
    """build_messages 返 [{role:system}, {role:user}] 形态, user 含 JSON dump."""
    ctx = collect_context("反转因子", "csi300_active", "day")
    msgs = build_messages(ctx)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "workflow_json" in msgs[0]["content"]  # 提示里含示意
    assert "alpha003" in msgs[0]["content"]  # 禁用因子提示
    assert msgs[1]["role"] == "user"
    # user content 是 JSON dump, 应能 parse 回
    payload = json.loads(msgs[1]["content"])
    assert payload["goal"] == "反转因子"
    assert payload["default_universe"] == "csi300_active"
    assert "available_nodes" in payload
    assert "available_factors_brief" in payload
    assert "experience_chunks" in payload
    assert len(payload["experience_chunks"]) == 3


# ---------------------------------------------------------------------------
# 2. parse_and_validate — LLM raw → payload, 含校验
# ---------------------------------------------------------------------------


def _good_workflow_json() -> Dict[str, Any]:
    """合法 4 节点链路."""
    return {
        "name": "反转因子 IC 测试",
        "nodes": [
            {"id": "u", "type": "data.universe", "params": {"name": "csi300_active"}},
            {"id": "p", "type": "data.load_panel", "inputs": {"codes": "u.output"},
             "params": {"start": "2025-01-01", "end": "2025-03-01"}},
            {"id": "f", "type": "factor.from_registry", "inputs": {"panel": "p.output"},
             "params": {"name": "alpha001"}},
            {"id": "r", "type": "eval.factor_report",
             "inputs": {"alpha": "f.output", "panel": "p.output"},
             "params": {"fwd_days": 5}},
        ],
    }


def _ctx_with_nodes() -> CopilotContext:
    """构造一个最小 ctx (5 节点 type 已知)."""
    return CopilotContext(
        goal="x",
        nodes=[
            {"type": "data.universe", "description": "", "group": "data", "tag": [], "params_schema_summary": {}},
            {"type": "data.load_panel", "description": "", "group": "data", "tag": [], "params_schema_summary": {}},
            {"type": "factor.from_registry", "description": "", "group": "factor", "tag": [], "params_schema_summary": {}},
            {"type": "factor.from_expression", "description": "", "group": "factor", "tag": [], "params_schema_summary": {}},
            {"type": "eval.factor_report", "description": "", "group": "eval", "tag": [], "params_schema_summary": {}},
        ],
    )


def test_parse_valid_payload():
    """合法 LLM 输出 → payload, 缺省字段补空 list."""
    raw = json.dumps({
        "workflow_json": _good_workflow_json(),
        "cited_experiences": [{"source": "factor_insights.md", "section": "rev_20"}],
        "risk_flags": ["反转因子在系统性下跌中失效"],
        "used_factors": ["alpha001"],
    }, ensure_ascii=False)
    payload, err = parse_and_validate(raw, _ctx_with_nodes())
    assert err is None
    assert payload is not None
    assert payload["workflow_json"]["name"] == "反转因子 IC 测试"
    assert payload["cited_experiences"][0]["source"] == "factor_insights.md"
    assert payload["risk_flags"] == ["反转因子在系统性下跌中失效"]
    assert payload["used_factors"] == ["alpha001"]


def test_parse_strips_markdown_fence():
    """LLM 偶尔会带 ```json ... ``` fence, parse_and_validate 应剥掉."""
    inner = json.dumps({"workflow_json": _good_workflow_json()}, ensure_ascii=False)
    raw = f"```json\n{inner}\n```"
    payload, err = parse_and_validate(raw, _ctx_with_nodes())
    assert err is None
    assert payload["workflow_json"]["name"] == "反转因子 IC 测试"


def test_parse_invalid_json_returns_error():
    """LLM 返非 JSON → error msg."""
    payload, err = parse_and_validate("这不是 JSON", _ctx_with_nodes())
    assert payload is None
    assert err is not None
    assert "JSON" in err


def test_parse_missing_workflow_json_returns_error():
    """JSON 合法但缺 workflow_json 字段 → error."""
    raw = json.dumps({"something_else": 1})
    payload, err = parse_and_validate(raw, _ctx_with_nodes())
    assert payload is None
    assert "workflow_json" in err


def test_parse_empty_nodes_returns_error():
    """workflow_json.nodes 是空 list → error."""
    raw = json.dumps({"workflow_json": {"name": "x", "nodes": []}})
    payload, err = parse_and_validate(raw, _ctx_with_nodes())
    assert payload is None
    assert "nodes" in err


def test_parse_unknown_node_type_returns_error():
    """workflow 引用了 ctx 里没有的 type → error."""
    bad_wf = {
        "name": "bad",
        "nodes": [{"id": "x", "type": "nonexistent.node", "params": {}}],
    }
    raw = json.dumps({"workflow_json": bad_wf})
    payload, err = parse_and_validate(raw, _ctx_with_nodes())
    assert payload is None
    assert "未注册" in err or "nonexistent" in err


# ---------------------------------------------------------------------------
# 3. SSE 事件顺序 — POST /workflow/copilot/draft 端点 (mock LLM)
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> List[Dict[str, Any]]:
    """同 test_workflow_sse.py — SSE raw → [{event, data}]."""
    events: List[Dict[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        ev_m = re.search(r"^event:\s*(.+)$", block, re.MULTILINE)
        dt_m = re.search(r"^data:\s*(.+)$", block, re.MULTILINE)
        if not ev_m or not dt_m:
            continue
        try:
            data = json.loads(dt_m.group(1))
        except Exception:
            data = {"_raw": dt_m.group(1)}
        events.append({"event": ev_m.group(1).strip(), "data": data})
    return events


def test_sse_emits_thought_draft_done_in_order(isolated_workflow_env, stub_knowledge, monkeypatch):
    """mock LLM 返合法 JSON → SSE 事件: thought*N + draft + done, 顺序正确."""
    canned = json.dumps({
        "workflow_json": _good_workflow_json(),
        "cited_experiences": [{"source": "factor_insights.md", "section": "rev_20 历史"}],
        "risk_flags": ["反转因子在系统性下跌中失效"],
        "used_factors": ["alpha001"],
    }, ensure_ascii=False)
    monkeypatch.setattr(_copilot, "_complete_default", lambda messages: canned)

    client = TestClient(build_app())
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "用反转因子在 csi300 跑 IC", "universe": "csi300_active"},
        timeout=15.0,
    ) as resp:
        assert resp.status_code == 200
        chunks: List[str] = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
            if "event: done" in "".join(chunks):
                break

    raw = "".join(chunks)
    events = _parse_sse(raw)
    event_kinds = [e["event"] for e in events]

    # 至少有: thought (>=1) + draft + done
    assert event_kinds.count("draft") == 1
    assert event_kinds.count("done") == 1
    assert event_kinds.count("thought") >= 1
    # draft 在 done 之前
    draft_idx = event_kinds.index("draft")
    done_idx = event_kinds.index("done")
    assert draft_idx < done_idx
    # thought 在 draft 之前 (至少有一个)
    thought_idx = next(i for i, k in enumerate(event_kinds) if k == "thought")
    assert thought_idx < draft_idx

    # draft 形状
    draft = next(e["data"] for e in events if e["event"] == "draft")
    assert "workflow_json" in draft
    assert draft["workflow_json"]["name"] == "反转因子 IC 测试"
    assert draft["cited_experiences"][0]["source"] == "factor_insights.md"
    assert draft["risk_flags"] == ["反转因子在系统性下跌中失效"]
    assert draft["used_factors"] == ["alpha001"]


def test_sse_cites_knowledge_index(isolated_workflow_env, stub_knowledge, monkeypatch):
    """LLM 返的 draft 含 cited_experiences, source 来自 KnowledgeIndex 命中.

    我们不验 LLM "真" 引用了 (stub LLM 是固定输出); 而是验 endpoint 把 LLM 的
    cited_experiences 字段完整透传到 draft 事件 — 这是 contract.
    """
    canned = json.dumps({
        "workflow_json": _good_workflow_json(),
        "cited_experiences": [
            {"source": "factor_insights.md", "section": "rev_20 历史"},
            {"source": "pitfalls.md", "section": "游资博弈票排除"},
        ],
        "risk_flags": [],
        "used_factors": ["alpha001"],
    }, ensure_ascii=False)
    monkeypatch.setattr(_copilot, "_complete_default", lambda messages: canned)

    client = TestClient(build_app())
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "反转因子"}, timeout=15.0,
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(raw)
    draft = next(e["data"] for e in events if e["event"] == "draft")
    sources = {c["source"] for c in draft["cited_experiences"]}
    assert "factor_insights.md" in sources
    assert "pitfalls.md" in sources


def test_sse_invalid_json_emits_error(isolated_workflow_env, stub_knowledge, monkeypatch):
    """LLM 返非 JSON → SSE 推 error event, 不抛."""
    monkeypatch.setattr(_copilot, "_complete_default", lambda messages: "这不是 JSON!")

    client = TestClient(build_app())
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "随便"}, timeout=15.0,
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(raw)
    event_kinds = [e["event"] for e in events]
    assert "error" in event_kinds
    err_evt = next(e["data"] for e in events if e["event"] == "error")
    assert "JSON" in err_evt["message"] or "解析" in err_evt["message"] or "非合法" in err_evt["message"]
    # 不应有 draft
    assert "draft" not in event_kinds


def test_sse_llm_error_emits_error(isolated_workflow_env, stub_knowledge, monkeypatch):
    """LLM 调用本身抛异常 → error event."""
    def boom(messages):
        raise RuntimeError("llm down")
    monkeypatch.setattr(_copilot, "_complete_default", boom)

    client = TestClient(build_app())
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "x"}, timeout=15.0,
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(raw)
    err_evt = next(e["data"] for e in events if e["event"] == "error")
    assert "llm down" in err_evt["message"] or "LLM" in err_evt["message"]


def test_sse_unknown_node_emits_error(isolated_workflow_env, stub_knowledge, monkeypatch):
    """LLM 输出引用了未注册节点 → error event."""
    bad = json.dumps({
        "workflow_json": {
            "name": "bad",
            "nodes": [{"id": "x", "type": "nonexistent.node", "params": {}}],
        },
    }, ensure_ascii=False)
    monkeypatch.setattr(_copilot, "_complete_default", lambda messages: bad)

    client = TestClient(build_app())
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "x"}, timeout=15.0,
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(chunk for chunk in resp.iter_text())

    events = _parse_sse(raw)
    err_evt = next(e["data"] for e in events if e["event"] == "error")
    assert "未注册" in err_evt["message"] or "nonexistent" in err_evt["message"]


# ---------------------------------------------------------------------------
# 4. 端到端: draft → POST /workflow/create 形成可执行 workflow
# ---------------------------------------------------------------------------


def test_draft_workflow_json_is_createable(isolated_workflow_env, stub_knowledge, monkeypatch):
    """Copilot 出的 workflow_json 应能直接 POST /workflow/create 落盘 (schema 合规)."""
    canned = json.dumps({
        "workflow_json": _good_workflow_json(),
        "cited_experiences": [],
        "risk_flags": [],
        "used_factors": ["alpha001"],
    }, ensure_ascii=False)
    monkeypatch.setattr(_copilot, "_complete_default", lambda messages: canned)

    client = TestClient(build_app())
    # 1. 拉草稿
    with client.stream(
        "POST", "/workflow/copilot/draft",
        json={"goal": "反转因子"}, timeout=15.0,
    ) as resp:
        raw = "".join(chunk for chunk in resp.iter_text())
    draft = next(e["data"] for e in _parse_sse(raw) if e["event"] == "draft")

    # 2. 草稿 workflow_json → /workflow/create
    r = client.post("/workflow/create", json=draft["workflow_json"])
    assert r.status_code == 200, r.text
    wf_id = r.json()["wf_id"]
    assert isinstance(wf_id, str) and len(wf_id) == 12

    # 3. GET 读回, 形状合规
    r2 = client.get(f"/workflow/{wf_id}")
    assert r2.status_code == 200
    got = r2.json()
    assert len(got["nodes"]) == 4
    assert got["nodes"][0]["type"] == "data.universe"
    assert got["nodes"][-1]["type"] == "eval.factor_report"
