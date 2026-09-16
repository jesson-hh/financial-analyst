"""Phase-one Weiwo memory: explainable recall, durable roll-up, safer compaction."""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.api import build_console_router
from guanlan_v2.console.memory_recall import recall_memories
from guanlan_v2.console.store import ConsoleStore


class _Evt:
    def __init__(self, kind, payload=None):
        self.kind = kind
        self.payload = payload


def test_recall_finds_old_archived_fact_by_meaning_and_code():
    sources = {
        "global": "- [2026-08-20] (style) 用户偏好月频回测\n",
        "global_archive": (
            "## 归档于 2026-08-21T10:00:00\n"
            "- [2025-01-03] (hold-600667-cost) 用户持有太极实业(SH600667)，成本价24.29元\n"
        ),
        "workspace": "",
        "session": "",
    }
    hits = recall_memories("太极实业 600667 的持仓成本是多少", sources)
    assert hits
    assert hits[0]["scope"] == "global_archive"
    assert "24.29" in hits[0]["text"]
    assert "600667" in hits[0]["matched"]


def test_recall_never_uses_recency_without_relevance():
    hits = recall_memories(
        "分析五日均线趋势",
        {"global": "- [2026-08-23] (food) 用户午饭喜欢清淡\n"},
    )
    assert hits == []


def test_recall_does_not_cross_security_codes_on_generic_cost_words():
    hits = recall_memories(
        "风华高科000636的持仓成本是多少",
        {"global": (
            "- [2026-08-19] (hold-000636-cost) 风华高科(SZ000636)成本62.83元\n"
            "- [2026-08-20] (hold-600667-cost) 太极实业(SH600667)成本24.29元\n"
        )},
    )
    assert hits and all("600667" not in hit["text"] for hit in hits)


def test_recall_ignores_instruction_stopwords_in_unrelated_memory():
    hits = recall_memories(
        "再核对一次：风华高科000636的成本记忆来自哪个范围？请按系统回执准确说明，不要混淆。",
        {"global": (
            "- [2026-08-19] (hold-000636-cost) 风华高科(SZ000636)成本62.83元\n"
            "- [2026-06-18] (factor-state) 因子研究结论：市场状态不要单独做横截面因子\n"
        )},
    )
    assert hits
    assert all(hit["key"] != "factor-state" for hit in hits)


def test_recall_suppresses_superseded_archived_copy_of_live_key():
    hits = recall_memories(
        "我的股票池偏好是什么",
        {
            "global": "- [2026-08-23] (pool) 股票池偏好改为中证500\n",
            "global_archive": "- [2026-01-01] (pool) 股票池偏好是沪深300\n",
        },
    )
    assert len(hits) == 1
    assert hits[0]["scope"] == "global" and "中证500" in hits[0]["text"]


def test_memory_context_adds_older_workspace_hit_and_self_awareness(tmp_path, monkeypatch):
    import guanlan_v2.console.api as api
    import guanlan_v2.console.tools as tools

    root = tmp_path / "console"
    monkeypatch.setattr(tools, "_MEMORY_PATH", root / "memory.md")
    workspace_id = "rw_123456789abc"
    workspace_path = root / "workspace_memory" / workspace_id / "memory.md"
    workspace_path.parent.mkdir(parents=True)
    old = "- [2025-01-01] (alpha-beta) 太极实业相对沪深300的Beta研究需要使用复权共同样本\n"
    noise = "".join(f"- [2026-08-22] 临时噪声{i}\n" for i in range(20))
    workspace_path.write_text(old + noise, encoding="utf-8")

    block, hits = api._memory_context("cs_none", workspace_id, "重新计算 Alpha Beta")
    assert hits and hits[0]["key"] == "alpha-beta"
    assert "[本轮主动召回" in block and "复权共同样本" in block
    assert "[记忆自知]" in block and "不得假装" not in block


def test_keyed_session_rollup_replaces_previous_value(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as tools

    monkeypatch.setattr(tools, "_MEMORY_PATH", tmp_path / "memory.md")
    sid = "cs_123456789abc"
    token = tools.CTX_SID.set(sid)
    try:
        assert tools.memory_write_impl("旧摘要", scope="session", key="conversation-rollup")["ok"]
        assert tools.memory_write_impl("新摘要", scope="session", key="conversation-rollup")["ok"]
    finally:
        tools.CTX_SID.reset(token)
    body = (tmp_path / "sessions" / sid / "notes.md").read_text(encoding="utf-8")
    assert "新摘要" in body and "旧摘要" not in body
    assert body.count("(conversation-rollup)") == 1


def test_bounded_compaction_keeps_prior_digest_and_latest_correction():
    from financial_analyst.buddy.agent import Message, _bounded_compaction_transcript

    messages = [
        Message("user", "（前情摘要，仅供延续对话参考）\n旧摘要：太极实业研究中"),
        Message("tool", "很长的旧工具输出" * 3000),
        Message("user", "最新纠正：成本价改为24.29，下一步先核验分钟数据"),
    ]
    compact = _bounded_compaction_transcript(messages, max_chars=1200)
    assert "旧摘要：太极实业" in compact
    assert "最新纠正：成本价改为24.29" in compact
    assert len(compact) <= 1200


def test_router_emits_recall_receipt_and_injects_hit(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as tools

    monkeypatch.setenv("CONSOLE_REVIEW_MODE", "off")
    monkeypatch.setattr(tools, "_MEMORY_PATH", tmp_path / "memory.md")
    (tmp_path / "memory.md").write_text(
        "- [2026-08-19] (hold-000636-cost) 用户持有风华高科(SZ000636)，成本价62.83元\n",
        encoding="utf-8",
    )
    seen = {}

    class Agent:
        def __init__(self):
            self.messages = []

        async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
            seen["text"] = text
            yield _Evt("text", "已读取")
            yield _Evt("done")

    store = ConsoleStore(root=tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: Agent()))
    with TestClient(app) as client:
        sid = client.post("/console/sessions", json={}).json()["meta"]["id"]
        assert client.post("/console/send", json={
            "sid": sid, "text": "风华高科000636的持仓成本是多少",
        }).json()["ok"]
        for _ in range(60):
            events = store.read_events(sid)
            if any(e.get("type") == "task_update" and e.get("status") == "done" for e in events):
                break
            time.sleep(0.05)

    receipt = next(e for e in events if e.get("type") == "memory_recall")
    assert receipt["count"] >= 1
    assert receipt["hits"][0]["key"] == "hold-000636-cost"
    assert "62.83" in seen["text"] and "[记忆自知]" in seen["text"]


def test_memory_review_gate_detects_explicit_durable_cues():
    from guanlan_v2.console.api import _memory_review_reason

    assert _memory_review_reason("记住我持有风华高科，成本62.83", 0, True) == "explicit_memory_cue"
    assert _memory_review_reason("分析今天走势", 0, True) == ""
    assert _memory_review_reason("普通问题", 5, True) == "tools=5"
    assert _memory_review_reason("普通问题", 0, False) == "turn_failed"
