"""后端审计三处修复回归锁(2026-06-15,用户「先修124」)。

BUG1 研报「上次研报时间线」死代码:factor-computer 节点已删,inputs.get 恒空 → 正文「上次回顾」写不出。
   修=report-writer 直接从 StockTimelineLoader 读真时间线 + 按 asof PIT 过滤未来回写。
   锁:_timeline_asof 丢弃晚于 asof 的条目、保留非日期行、asof 非日期时不过滤。

BUG2 /lesson 落 _shared/conversation_lessons.md 被 _collect_files 强载进所有研报 agent。
   修=挪到 _buddy/(不在 _shared/agent/borrows 任一被 glob 的目录)+ 旧 _shared 文件迁移走。
   锁:路径不含 _shared、旧文件被迁移、研报 agent 的 load_all 不含 lessons 内容。

BUG4 client.py chat() 零重试 → seats/各 SubAgent/wisdom 单次网络抖动即失败。
   修=chat() 对瞬时错误(超时/网络/5xx/429)指数退避重试,鉴权(401)/请求错(400)不重试。
   锁:_is_transient 分类正确、chat 重试瞬时错、不重试鉴权错。
"""
import asyncio

import pytest


# ── BUG1 ──
def test_timeline_asof_drops_future_keeps_past_and_headers():
    from financial_analyst.agent.tier3.report_writer import _timeline_asof
    tl = "# SH600519\n## 觀瀾研报回写 (自动)\n- 2026-06-10 评级8\n- 2026-06-20 评级7\n- 2026-07-01 评级6\n"
    out = _timeline_asof(tl, "2026-06-15")
    assert "2026-06-10" in out, "asof 当日及以前的条目保留"
    assert "2026-06-20" not in out and "2026-07-01" not in out, "晚于 asof 的回写必须丢(PIT)"
    assert "# SH600519" in out and "觀瀾研报回写" in out, "非日期行(标题)保留"


def test_timeline_asof_noop_when_asof_not_a_date():
    from financial_analyst.agent.tier3.report_writer import _timeline_asof
    tl = "- 2026-06-20 评级7\n"
    assert _timeline_asof(tl, "UNKNOWN") == tl, "asof 非 YYYY-MM-DD(live)时不过滤"


def test_timeline_asof_filters_real_table_format():
    # 真实时间线是 markdown 表格行 `| 2026-04-29 | … |`,不是 `- ` 行
    from financial_analyst.agent.tier3.report_writer import _timeline_asof
    tl = ("| 日期 | 价 | 评级 |\n|---|---|---|\n"
          "| 2026-04-15 | 49.4 | ★★★★☆ |\n"
          "| 2026-04-29 | 50.95 | ★☆☆☆☆ |\n")
    out = _timeline_asof(tl, "2026-04-20")
    assert "2026-04-15" in out, "asof 前的表格行保留"
    assert "2026-04-29" not in out, "asof 后的表格行必须丢(PIT,真实表格格式)"
    assert "| 日期 |" in out and "|---|" in out, "表头/分隔行(无日期)保留"


def test_timeline_asof_historical_drops_latest_snapshot_and_intro():
    # 历史 as-of(asof < 今天):导入时间线的 intro/「最新快照」散文含「最新」日期,
    # 无逐行日期可挡 → 必须整段丢,否则泄露未来。
    from financial_analyst.agent.tier3.report_writer import _timeline_asof
    tl = ("# SH600111 北方稀土\n"
          "> 共 10 份历史研报. 最新: 2026-04-29 ★☆☆☆☆ (看空).\n\n"
          "## 最新快照\n"
          "- **最近价**: 50.95 元 (2026-04-29)\n\n"
          "## 历史研报时间线\n"
          "| 日期 | 价 |\n|---|---|\n"
          "| 2026-04-15 | 49.4 |\n"
          "| 2026-04-29 | 50.95 |\n")
    out = _timeline_asof(tl, "2026-04-20")
    assert "2026-04-15" in out, "过去表格行保留"
    assert "2026-04-29" not in out, "未来日期(表格行+快照散文+intro)零泄露"
    assert "# SH600111" in out, "标题保留"
    assert "最新快照" not in out, "「最新快照」段(无条件含最新)整段丢"


# ── BUG2 ──
def test_conversation_lessons_path_out_of_shared_and_migrates(tmp_path, monkeypatch):
    import financial_analyst.memory_paths as mp
    import financial_analyst.buddy.agent as ag
    monkeypatch.setattr(mp, "default_memory_root", lambda: tmp_path)
    old = tmp_path / "_shared" / "conversation_lessons.md"
    old.parent.mkdir(parents=True)
    old.write_text("- [t] lesson A\n", encoding="utf-8")
    p = ag._conversation_lessons_path()
    assert "_shared" not in p.parts, "lessons 不得落在 _shared(会被强载进研报 agent)"
    assert not old.exists(), "旧 _shared 文件应被迁移走(杜绝残留被强载)"
    assert "lesson A" in p.read_text(encoding="utf-8"), "迁移须保留旧内容"


def test_lessons_not_force_loaded_by_report_agent(tmp_path):
    from financial_analyst.agent.memory import AgentMemory
    (tmp_path / "_buddy").mkdir(parents=True)
    (tmp_path / "_buddy" / "conversation_lessons.md").write_text("BUDDY_ONLY_SECRET", encoding="utf-8")
    loaded = AgentMemory("report-writer", tmp_path).load_all()
    assert "BUDDY_ONLY_SECRET" not in loaded, "研报 agent 的记忆不应载入 buddy 对话经验"


# ── BUG4 ──
def test_is_transient_classification():
    from financial_analyst.llm.client import _is_transient

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class AuthenticationError(Exception):
        status_code = 401

    class BadRequestError(Exception):
        status_code = 400

    class _Server(Exception):
        status_code = 503

    assert _is_transient(APITimeoutError())
    assert _is_transient(APIConnectionError())
    assert _is_transient(_Server())
    assert not _is_transient(AuthenticationError())
    assert not _is_transient(BadRequestError())
    assert not _is_transient(asyncio.CancelledError())


def test_chat_retries_transient_then_succeeds(monkeypatch):
    from financial_analyst.llm.client import LLMClient
    c = LLMClient(provider="deepseek", model="deepseek-chat", config={})
    n = {"calls": 0}

    class APITimeoutError(Exception):
        pass

    async def flaky(*a, **k):
        n["calls"] += 1
        if n["calls"] < 2:
            raise APITimeoutError("hiccup")
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(c, "_chat_openai_compat", flaky)
    monkeypatch.setattr(asyncio, "sleep", _noop)
    r = asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    assert n["calls"] == 2, "瞬时错应被重试一次后成功"
    assert r["choices"][0]["message"]["content"] == "ok"


def test_chat_does_not_retry_auth_error(monkeypatch):
    from financial_analyst.llm.client import LLMClient
    c = LLMClient(provider="deepseek", model="deepseek-chat", config={})
    n = {"calls": 0}

    class AuthenticationError(Exception):
        status_code = 401

    async def fail(*a, **k):
        n["calls"] += 1
        raise AuthenticationError("401 bad key")

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(c, "_chat_openai_compat", fail)
    monkeypatch.setattr(asyncio, "sleep", _noop)
    with pytest.raises(Exception):
        asyncio.run(c.chat(messages=[{"role": "user", "content": "hi"}]))
    assert n["calls"] == 1, "鉴权错不应重试(白白浪费)"
