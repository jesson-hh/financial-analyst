# P6′ 行业判断上下文重排层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P5 再打分之上加 LLM 行业重排层:上下文包(链环景气/情绪/大盘/教训)→ 一次整批 LLM → top-N 内自由重排(带 stance+理由),双轨落档攒 A/B 前向证据,帷幄可反思蒸馏教训并反哺。

**Architecture:** 新纯函数模块 `guanlan_v2/screen/rerank.py`(上下文包/LLM/硬校验/rerank 块),由 `screen/rescore.py` 状态机在打分后作为新 phase 编排;A/B 双篮走 `screen/picks.py` 现成档案(kind=rerank_ab);`GET /seats/basket_perf` 加 kind 分支;帷幄 +2 工具;选股页只加一列。

**Tech Stack:** FastAPI + 纯 Python(daemon 线程 asyncio.run 调 `screen/llm._call_llm_json`)+ babel-standalone JSX。

## Global Constraints(spec 逐字)

- **数据榜/正式 picks 零行为变化**;LLM 失败/校验失败 → `rerank.ok:false` 显形,数据榜照旧,**绝不部分采用、绝不编序**。
- 硬校验:输出票集合与输入**逐一相等**(无新增/缺失/重复),`stance ∈ {顺风,逆风,中性}`,reason 非空;违者整体 `rerank_failed`。
- 采纳(口径切换/教训入库)全人审;**无新定时器**——日跑复用 P1 调度器,`GUANLAN_RERANK_DAILY=1` opt-in,默认关=合并零行为变化。
- A/B 篮 = 各榜 **top-min(10, top_n)**,picks 行 `{kind:"rerank_ab", arm:"data"|"rerank", codes, run_id, ts, snapshot:false}`;现有 picks 消费方默认过滤 kind=rerank_ab。
- 教训 key 前缀「**行业·**」;重排上下文注入最近 **K=5** 条。
- 工具计数 **46→48 ww / 71→73 console / 50→52 MCP**,四处同步:`WW_TOOL_TABLE` / `console/api.py _SYSTEM_PROMPT` / `tests/test_console_tools.py` / `tests/test_guanlan_mcp.py`×3 / `glmcp/README.md`×2。
- UI 只填充;改 jsx 必 Edit bump html `?v=`(本期 `?v=20260705p6`)。
- 测试命令:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file> -q`;全量基线 ~890 passed(test_industry_ingest 有既知套件序依赖失败,隔离跑 6/6 过即归档)。
- git:分支 `p6-rerank` 自 main `8bede5b`;**逐文件 git add 绝不 -A**;提交末尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 真机 e2e @**9998** 隔离、`top_n=5` 控 LLM 成本、控制器亲手;改后端 9999 重启才生效。

---

### Task 0: 开分支+台账(控制器自做,不派发)

- [ ] `git checkout -b p6-rerank 8bede5b`
- [ ] 建 `.superpowers/sdd/progress-p6.md`(任务清单+每任务完成行)

---

### Task 1: 重排引擎纯函数层 `rerank.py`

**Files:**
- Create: `guanlan_v2/screen/rerank.py`
- Test: `tests/test_screen_rerank.py`

**Interfaces:**
- Consumes: `guanlan_v2.screen.llm._call_llm_json(system, user, *, timeout, temperature) -> {ok,data,model,tokens}|{ok:False,reason}`(llm.py:75);`guanlan_v2.console.tools._MEMORY_PATH`(keyed 行格式 `- [YYYY-MM-DD] (key) text`,tools.py:1225);`guanlan_v2.industry.aggregate.build_board()`。
- Produces(Task 2/5 依赖):`read_industry_lessons(k:int=5)->List[str]`;`build_context_pack(ranked_rows, board, market, lessons)->dict`;`build_prompt(pack)->Tuple[str,str]`;`validate_order(codes_in:List[str], order:List[dict])->Tuple[bool,str]`;`run_rerank(rows:List[dict], market:dict)->dict`(rerank 块,成功含 `rows/model/overall/lessons_injected/board_snapshot/elapsed_sec`,失败 `{ok:False,reason}`)。

- [ ] **Step 1: 写失败测试**(先建测试文件,全部打桩零网络)

```python
# tests/test_screen_rerank.py
# -*- coding: utf-8 -*-
"""P6′ 重排引擎单测:教训读回/上下文包/硬校验/失败降级/rerank 块 schema(打桩 LLM 零网络)。"""
import json

import pytest

from guanlan_v2.screen import rerank as rk


def _rows():
    return [
        {"code": "SH600000", "v4pct": 99.0,
         "chain": {"seg_name": "光芯片", "chain": 0.5, "quadrant": "hh",
                   "research": 2.1, "therm": 80.0},
         "news": {"tag": "利好", "read": "订单超预期", "score": 1.0}},
        {"code": "SZ000001", "v4pct": 98.0, "chain": None, "news": None},
        {"code": "SH600519", "v4pct": 97.0, "chain": None,
         "news": {"tag": "中性", "read": "例行公告", "score": 0.0}},
    ]


def _order(codes, stance="中性", reason="理由"):
    return [{"code": c, "stance": stance, "reason": reason} for c in codes]


def _order_rev(codes):
    return [{"code": c, "stance": "中性", "reason": "r"} for c in reversed(codes)]


def test_read_lessons_filters_prefix_and_tail(tmp_path, monkeypatch):
    p = tmp_path / "memory.md"
    lines = ["- [2026-07-01] (研究·某目标) 因子教训",
             "- [2026-07-02] (行业·光芯片) 教训A",
             "普通行不带key",
             "- [2026-07-03] (行业·情绪) 教训B",
             "- [2026-07-04] (行业·风格) 教训C"]
    p.write_text("\n".join(lines), encoding="utf-8")
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", p)
    got = rk.read_industry_lessons(k=2)
    assert got == ["(行业·情绪) 教训B", "(行业·风格) 教训C"]   # 只「行业·」前缀,取尾部k条,保序


def test_read_lessons_missing_file_returns_empty(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "nope.md")
    assert rk.read_industry_lessons() == []


def test_context_pack_states():
    ranked = [dict(r, rank=i + 1) for i, r in enumerate(_rows())]
    pack = rk.build_context_pack(ranked, {"ok": True, "segments": []},
                                 {"market_read": "平淡", "market_tilt": "中性"}, [])
    t = pack["tickets"]
    assert t[0]["rank"] == 1 and t[0]["chain"]["seg_name"] == "光芯片"
    assert t[1]["chain"] == "不在链上" and t[1]["news"] == "无新闻"   # 诚实字面,不编数
    assert pack["market"]["market_tilt"] == "中性" and pack["lessons"] == []


@pytest.mark.parametrize("bad", [
    lambda c: [],
    lambda c: _order(c[:-1]),
    lambda c: _order(c + ["SH999999"]),
    lambda c: _order([c[0]] + c[1:-1] + [c[0]]),
    lambda c: _order(c, stance="看多"),
    lambda c: _order(c, reason="  "),
])
def test_validate_order_rejects(bad):
    codes = [r["code"] for r in _rows()]
    ok, msg = rk.validate_order(codes, bad(codes))
    assert not ok and msg


def test_validate_order_accepts_permutation():
    codes = [r["code"] for r in _rows()]
    ok, msg = rk.validate_order(codes, _order(list(reversed(codes))))
    assert ok and msg == ""


def test_run_rerank_llm_fail_is_honest(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": True, "segments": [],
                                                       "snapshot": {}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: [])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {"ok": False, "reason": "超时"})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "超时" in out["reason"]


def test_run_rerank_invalid_order_whole_fail(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": True, "segments": [],
                                                       "snapshot": {}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: [])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {
        "ok": True, "model": "m", "data": {"order": _order(["SH600000"]), "overall": "x"}})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "rerank_failed" in out["reason"]   # 绝不部分采用


def test_run_rerank_board_down_refuses(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": False, "reason": "语料缺"})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "产业链板不可用" in out["reason"]


def test_run_rerank_success_block_schema(monkeypatch):
    codes = [r["code"] for r in _rows()]
    monkeypatch.setattr(rk, "_board_summary", lambda: {
        "ok": True, "segments": [{"name": "光芯片", "research": 2.1, "therm": 80,
                                  "quadrant": "hh"}],
        "snapshot": {"latest_publish_ts": "2026-07-02", "n_docs": 1841}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: ["(行业·x) 教训"])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {
        "ok": True, "model": "deepseek-chat",
        "data": {"order": _order_rev(codes), "overall": "光芯片顺风"}})
    out = rk.run_rerank(_rows(), {"market_read": "平", "market_tilt": "中性"})
    assert out["ok"] is True and out["model"] == "deepseek-chat"
    assert out["lessons_injected"] == 1
    assert out["board_snapshot"] == {"latest_publish_ts": "2026-07-02", "n_docs": 1841}
    by = {r["code"]: r for r in out["rows"]}
    assert by["SH600000"]["rank_before"] == 1 and by["SH600000"]["rank_after"] == 3
    assert by["SH600519"]["rank_after"] == 1 and by["SH600519"]["stance"] == "中性"
    assert all(r["reason"] for r in out["rows"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_rerank.py -q`
Expected: FAIL(`ModuleNotFoundError: guanlan_v2.screen.rerank`)

- [ ] **Step 3: 实现 `guanlan_v2/screen/rerank.py`**

```python
# -*- coding: utf-8 -*-
"""P6′ 行业判断上下文重排层:上下文包 → LLM 一次整批 → top-N 内自由重排(带理由)。

红线:数据榜与正式 picks 零变化(本模块只产 rerank 块,落档由 rescore 编排);
LLM 失败/校验失败 → {"ok": False, "reason": ...} 显形,绝不部分采用、绝不编序。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

_LESSON_PAT = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \((行业·[^)]*)\) (.+)$")
_STANCES = ("顺风", "逆风", "中性")

_SYSTEM = (
    "你是A股行业研究员,基于行业材料对候选票的现有量化排名做行业视角重排。"
    "规则:只能重排给定候选票,绝不新增/删除/重复;每票给 stance(顺风/逆风/中性)"
    "与一句具体 reason(引用链环/新闻/大盘/教训中的事实);材料不支持判断时保持原名次附近并给中性。"
    '只输出 JSON:{"order":[{"code":"...","stance":"...","reason":"..."}...],"overall":"一句总览"};'
    "order 按新排名从第1名开始,必须包含全部候选票各一次。")


# ── 桥(独立小函数便于 monkeypatch,仓例 rescore.py §桥)──────────────────

def _board_summary() -> Dict[str, Any]:
    """链环景气全景摘要;board 坏 → {ok:False}(上游诚实失败传导)。"""
    from guanlan_v2.industry.aggregate import build_board
    b = build_board()
    if not b.get("ok"):
        return {"ok": False, "reason": b.get("reason"), "segments": [], "snapshot": {}}
    segs = []
    for s in (b.get("segments") or []):
        if not isinstance(s, dict) or s.get("adjacent"):
            continue
        segs.append({"name": s.get("display_name") or s.get("name"),
                     "research": (s.get("research") or {}).get("score"),
                     "therm": s.get("therm"), "quadrant": s.get("quadrant")})
    corpus = dict(((b.get("freshness") or {}).get("corpus")) or {})
    return {"ok": True, "segments": segs,
            "snapshot": {"latest_publish_ts": corpus.get("latest_publish_ts"),
                         "n_docs": corpus.get("n_docs")}}


def _call_llm(system: str, user: str) -> Dict[str, Any]:
    """daemon 线程内同步跑异步 _call_llm_json(仓内已验模式,rescore._call_news 同款)。"""
    import asyncio
    from guanlan_v2.screen.llm import _call_llm_json
    return asyncio.run(_call_llm_json(system, user, timeout=120.0, temperature=0.2))


# ── 纯函数 ───────────────────────────────────────────────────────────────

def read_industry_lessons(k: int = 5) -> List[str]:
    """读帷幄全局记忆「行业·」keyed 行尾部 k 条(反哺;无/不可读 → [] 诚实不挡重排)。"""
    try:
        from guanlan_v2.console.tools import _MEMORY_PATH
        lines = _MEMORY_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return []
    hits: List[str] = []
    for ln in lines:
        m = _LESSON_PAT.match(ln.strip())
        if m:
            hits.append(f"({m.group(1)}) {m.group(2)}")
    return hits[-max(0, int(k)):] if k else []


def build_context_pack(ranked_rows: List[dict], board: Dict[str, Any],
                       market: Optional[Dict[str, Any]],
                       lessons: List[str]) -> Dict[str, Any]:
    """行业材料上下文包;不含任何因子明细(行业判断只用行业材料,边界干净)。"""
    tickets = []
    for r in ranked_rows:
        ch, nw = r.get("chain"), r.get("news")
        tickets.append({
            "code": r.get("code"), "rank": r.get("rank"), "v4pct": r.get("v4pct"),
            "chain": ({"seg_name": ch.get("seg_name"), "chain": ch.get("chain"),
                       "quadrant": ch.get("quadrant"), "research": ch.get("research"),
                       "therm": ch.get("therm")} if isinstance(ch, dict) else "不在链上"),
            "news": ({"tag": nw.get("tag"), "read": nw.get("read")}
                     if isinstance(nw, dict) else "无新闻")})
    return {"tickets": tickets, "board": board,
            "market": {"market_read": (market or {}).get("market_read"),
                       "market_tilt": (market or {}).get("market_tilt")},
            "lessons": list(lessons or [])}


def build_prompt(pack: Dict[str, Any]) -> Tuple[str, str]:
    user = ("行业材料(JSON):\n" + json.dumps(pack, ensure_ascii=False)
            + "\n\n请输出重排 JSON。")
    return _SYSTEM, user


def validate_order(codes_in: List[str], order: List[dict]) -> Tuple[bool, str]:
    """硬校验:票集合逐一相等/无重复/stance 合法/reason 非空;违者整体拒。"""
    if not isinstance(order, list) or not order:
        return False, "order 缺失或为空"
    codes_out = [str((o or {}).get("code") or "") for o in order]
    if len(codes_out) != len(set(codes_out)):
        return False, "order 含重复票"
    want = {str(c) for c in codes_in}
    got = set(codes_out)
    if got != want:
        return False, f"票集合不等: 缺{sorted(want - got)[:3]} 多{sorted(got - want)[:3]}"
    for o in order:
        if str((o or {}).get("stance") or "") not in _STANCES:
            return False, f"stance 非法: {o.get('code')}={o.get('stance')}"
        if not str((o or {}).get("reason") or "").strip():
            return False, f"reason 为空: {o.get('code')}"
    return True, ""


def run_rerank(rows: List[dict], market: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """重排主体(rescore rows 就绪后在同一 daemon 线程调用)。任何失败 → ok:false 显形。"""
    t0 = time.time()
    try:
        board = _board_summary()
        if not board.get("ok"):
            return {"ok": False, "reason": f"产业链板不可用: {board.get('reason')}"}
        lessons = read_industry_lessons(k=5)
        ranked = [dict(r, rank=i + 1) for i, r in enumerate(rows)]
        pack = build_context_pack(ranked, board, market, lessons)
        system, user = build_prompt(pack)
        resp = _call_llm(system, user)
        if not resp.get("ok"):
            return {"ok": False, "reason": f"LLM 失败: {resp.get('reason')}"}
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        order = data.get("order")
        ok, why = validate_order([r["code"] for r in ranked], order or [])
        if not ok:
            return {"ok": False, "reason": f"rerank_failed: {why}"}
        pos = {str(o["code"]): i + 1 for i, o in enumerate(order)}
        meta = {str(o["code"]): o for o in order}
        out_rows = [{"code": r["code"], "rank_before": r["rank"],
                     "rank_after": pos[str(r["code"])],
                     "stance": meta[str(r["code"])]["stance"],
                     "reason": str(meta[str(r["code"])]["reason"]).strip()[:160]}
                    for r in ranked]
        return {"ok": True, "model": resp.get("model"),
                "overall": str(data.get("overall") or "")[:200],
                "lessons_injected": len(lessons),
                "board_snapshot": dict(board.get("snapshot") or {}),
                "elapsed_sec": round(time.time() - t0, 1), "rows": out_rows}
    except Exception as exc:  # noqa: BLE001 — 重排层任何异常绝不炸 rescore run
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_rerank.py -q`
Expected: 13 passed(参数化 6 + 单测 7)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/rerank.py tests/test_screen_rerank.py
git commit -m "feat(rerank): P6' 重排引擎纯函数层(上下文包/硬校验/诚实降级)"
```

---

### Task 2: rescore 编排接线 + rerank_ab 双篮 + picks 消费方过滤

**Files:**
- Modify: `guanlan_v2/screen/rescore.py`(`run_rescore` 尾段,:246-266)
- Modify: `guanlan_v2/screen/api.py`(GET /screen/picks 端点——用 `grep -n "screen/picks" guanlan_v2/screen/api.py` 定位)
- Test: `tests/test_screen_rerank.py`(追加编排段)、`tests/test_rescore_api.py`(追加)

**Interfaces:**
- Consumes: Task 1 `run_rerank(rows, market)->dict`;`guanlan_v2.screen.picks.append_pick(record)->bool`(picks.py:16)。
- Produces: rescore run 行新增 `rerank` 键;picks 档案 `{kind:"rerank_ab", arm, codes, run_id, ts, snapshot:False}` 成对;`GET /screen/picks` 默认不含 kind=rerank_ab、`?kind=rerank_ab` 只回它们。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_screen_rerank.py`)

```python
def test_run_rescore_carries_rerank_block_and_ab_baskets(tmp_path, monkeypatch):
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [
        {"code": f"SH60000{i}", "v4pct": 99.0 - i} for i in range(5)])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes},
        {"llm_calls": 0, "cache_hits": 0, "market_read": "平", "market_tilt": "中性"}))
    fake_rk = {"ok": True, "model": "m", "overall": "o", "lessons_injected": 0,
               "board_snapshot": {}, "elapsed_sec": 0.1,
               "rows": [{"code": f"SH60000{i}", "rank_before": i + 1,
                         "rank_after": 5 - i, "stance": "中性", "reason": "r"}
                        for i in range(5)]}
    monkeypatch.setattr(rs, "_run_rerank_bridge", lambda rows, market: fake_rk)
    end = rs.run_rescore("rs_test", top_n=5, note="t", progress=lambda **k: None)
    assert end["ok"] and end["rerank"]["ok"]
    rows = pk.read_picks(limit=10)
    ab = [r for r in rows if r.get("kind") == "rerank_ab"]
    assert len(ab) == 2 and {r["arm"] for r in ab} == {"data", "rerank"}
    data_arm = next(r for r in ab if r["arm"] == "data")
    rr_arm = next(r for r in ab if r["arm"] == "rerank")
    assert data_arm["codes"][0] == "SH600000" and rr_arm["codes"][0] == "SH600004"
    assert all(not r.get("snapshot") for r in ab)
    assert all(r["run_id"] == "rs_test" for r in ab)


def test_run_rescore_rerank_fail_no_baskets(tmp_path, monkeypatch):
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [{"code": "SH600000", "v4pct": 99.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes}, {"llm_calls": 0, "cache_hits": 0}))
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "LLM 失败: x"})
    end = rs.run_rescore("rs_t2", top_n=5, note="", progress=lambda **k: None)
    assert end["ok"] is True                      # 打分本身成功(重排失败不拖垮 run)
    assert end["rerank"]["ok"] is False           # 失败显形
    assert pk.read_picks(limit=10) == []          # 失败绝不落 A/B 篮
```

追加到 `tests/test_rescore_api.py`(picks 端点过滤;client fixture 沿用本文件既有 TestClient 构造):

```python
def test_screen_picks_filters_rerank_ab_by_default(tmp_path, monkeypatch, client):
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600000"],
                    "run_id": "rs_x", "ts": "2026-07-05T10:00:00", "snapshot": False})
    pk.append_pick({"codes": ["SZ000001"], "snapshot": True, "ts": "2026-07-05T10:01:00"})
    r = client.get("/screen/picks").json()
    body = r.get("picks") or r.get("items") or []
    assert all(x.get("kind") != "rerank_ab" for x in body)     # 默认过滤=现有消费方零变化
    r2 = client.get("/screen/picks", params={"kind": "rerank_ab"}).json()
    rows2 = r2.get("picks") or r2.get("items") or []
    assert rows2 and all(x.get("kind") == "rerank_ab" for x in rows2)
```

(响应列表键名以现有端点实现为准——先读端点代码再对齐断言键;改断言,不改端点既有键名。)

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_rerank.py tests/test_rescore_api.py -q`
Expected: 新增 3 条 FAIL(`_run_rerank_bridge` 不存在 / kind 参数不识别)

- [ ] **Step 3: 实现编排**(`rescore.py`)

3a. 模块级桥+落篮(放在 `run_rescore` 之前):

```python
def _run_rerank_bridge(rows: List[dict], market: Dict[str, Any]) -> Dict[str, Any]:
    """桥(便于 monkeypatch):行业重排。"""
    from guanlan_v2.screen.rerank import run_rerank
    return run_rerank(rows, market)


def _record_rerank_ab(run_id: str, rows: List[dict], rk: Dict[str, Any],
                      top_n: int) -> None:
    """A/B 双篮并行落 picks 档案(kind=rerank_ab;snapshot=False 绝不占正式语义)。"""
    from guanlan_v2.screen.picks import append_pick
    k = min(10, int(top_n))
    data_codes = [r["code"] for r in rows[:k]]
    after = sorted(rk.get("rows") or [], key=lambda x: x.get("rank_after", 0))
    rr_codes = [x["code"] for x in after[:k]]
    ts = _now()
    for arm, codes in (("data", data_codes), ("rerank", rr_codes)):
        append_pick({"kind": "rerank_ab", "arm": arm, "codes": codes,
                     "run_id": run_id, "ts": ts, "snapshot": False})
```

3b. `run_rescore` 内、`rows` 组装完成后(现 :256 `end = {...}` 之前)插入:

```python
        progress(phase="rerank", label="④ 行业重排(LLM 整批)…")
        rk = _run_rerank_bridge(rows, {"market_read": nstats.get("market_read"),
                                       "market_tilt": nstats.get("market_tilt")})
        if rk.get("ok"):
            _record_rerank_ab(run_id, rows, rk, top_n)
```

并把成功分支 `end` 字典加键 `"rerank": rk`(两个异常分支的 `end` 不加 rerank 键——上游失败重排不跑)。

3c. `GET /screen/picks` 端点加 `kind: str = ""` 参数:默认过滤掉 `r.get("kind") == "rerank_ab"` 的行;`kind == "rerank_ab"` 时只返回这些行;其余 kind 值维持现行为。

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_rerank.py tests/test_rescore_api.py tests/test_screen_rescore.py -q`
Expected: 全绿(含 P5 既有回归)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/rescore.py guanlan_v2/screen/api.py tests/test_screen_rerank.py tests/test_rescore_api.py
git commit -m "feat(rerank): rescore 编排接线+rerank_ab 双篮+picks 消费方默认过滤"
```

---

### Task 3: opt-in 日跑(复用 P1 调度器)+ 模块级 start 重构

**Files:**
- Modify: `guanlan_v2/screen/rescore.py`(端点闭包提取为模块级 `start_rescore_bg`,:329-345)
- Modify: `guanlan_v2/screen/api.py`(:187-230 调度器判定/触发体、:1163 health)
- Test: `tests/test_rescore_api.py`(追加)

**Interfaces:**
- Produces: `rescore.start_rescore_bg(top_n:int=50, note:str="")->dict`(`{ok,started,run_id,state}|{ok:False,reason:"already_running",state}`,与端点同一状态机);`screen/api.py._maybe_daily_rerank()`(`GUANLAN_RERANK_DAILY=1` 才调 start);health `rerank_scheduler` 字段。

- [ ] **Step 1: 写失败测试**(追加 `tests/test_rescore_api.py`)

```python
def test_start_rescore_bg_module_level(monkeypatch):
    import time as _t

    from guanlan_v2.screen import rescore as rs
    calls = {}
    monkeypatch.setattr(rs, "run_rescore",
                        lambda run_id, top_n, note, progress: calls.setdefault(
                            "args", (top_n, note)) or {"ok": True})
    r = rs.start_rescore_bg(top_n=7, note="daily-scheduler")
    assert r["ok"] and r["started"] and r["run_id"].startswith("rs_")
    for _ in range(50):
        if calls.get("args"):
            break
        _t.sleep(0.05)
    assert calls["args"] == (7, "daily-scheduler")
    for _ in range(50):                       # finally 必清 running
        if not rs._RESCORE_STATE.get("running"):
            break
        _t.sleep(0.05)
    assert not rs._RESCORE_STATE.get("running")


def test_daily_rerank_hook_default_off(monkeypatch):
    """GUANLAN_RERANK_DAILY 缺省 → 绝不调 start_rescore_bg(零行为变化);=1 → 调。"""
    import guanlan_v2.screen.api as sapi
    from guanlan_v2.screen import rescore as rs
    monkeypatch.delenv("GUANLAN_RERANK_DAILY", raising=False)
    called = []
    monkeypatch.setattr(rs, "start_rescore_bg",
                        lambda **k: called.append(k) or {"ok": True})
    sapi._maybe_daily_rerank()
    assert called == []
    monkeypatch.setenv("GUANLAN_RERANK_DAILY", "1")
    sapi._maybe_daily_rerank()
    assert called and called[0].get("note") == "daily-scheduler"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_rescore_api.py -q`
Expected: 2 FAIL(`start_rescore_bg`/`_maybe_daily_rerank` 不存在)

- [ ] **Step 3: 实现**

3a. `rescore.py`:把 `rescore_start` 端点体提取为模块级(端点变薄壳 `return JSONResponse(start_rescore_bg(body.top_n, body.note))`):

```python
def start_rescore_bg(top_n: int = 50, note: str = "") -> Dict[str, Any]:
    """模块级发起(端点/调度器共用同一状态机)。已在跑 → ok:false(单飞让路)。"""
    top_n = max(5, min(int(top_n or 50), 100))
    run_id = new_run_id()
    with _RESCORE_LOCK:
        busy = bool(_RESCORE_STATE.get("running"))
        if not busy:
            _RESCORE_STATE.update(running=True, phase="starting", label="启动再打分…",
                                  run_id=run_id, started_at=_time.time(), ended_at=None,
                                  ok=None, error=None, lines=[])
    if busy:                                    # 锁外读状态(锁不可重入,绝不嵌套)
        return {"ok": False, "reason": "already_running",
                "state": _rescore_public_state()}
    _threading.Thread(target=lambda: _run_thread(run_id, top_n, (note or "").strip()),
                      name="rescore", daemon=True).start()
    return {"ok": True, "started": True, "run_id": run_id,
            "state": _rescore_public_state()}
```

3b. `screen/api.py`:

```python
def _maybe_daily_rerank() -> None:
    """opt-in:GUANLAN_RERANK_DAILY=1 时 regen 后顺跑一次打分+重排(复用 rescore
    单飞锁,already_running 自然让路;失败显形于 rescore 档案,绝不重试风暴)。"""
    if _os.environ.get("GUANLAN_RERANK_DAILY") != "1":
        return
    try:
        from guanlan_v2.screen import rescore as _rs
        _rs.start_rescore_bg(top_n=50, note="daily-scheduler")
    except Exception:  # noqa: BLE001 — 顺跑失败不挡 regen 主流程
        pass
```

在调度器 regen 成功触发处(先读 :187-230 触发体,挂在触发调用之后)加 `_maybe_daily_rerank()`;health(:1163 `regen_scheduler` 旁)加:

```python
"rerank_scheduler": {"enabled": _os.environ.get("GUANLAN_RERANK_DAILY") == "1",
                     "requires": "GUANLAN_REGEN_DAILY=1(随 regen 顺跑)"},
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_rescore_api.py tests/test_screen_rescore.py -q`
Expected: 全绿(端点薄壳化后 P5 并发/三态测试不回归——尤其 `test_endpoint_already_running_no_deadlock`)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/rescore.py guanlan_v2/screen/api.py tests/test_rescore_api.py
git commit -m "feat(rerank): opt-in 日跑 GUANLAN_RERANK_DAILY(复用 P1 调度器)+start_rescore_bg 模块级重构"
```

---

### Task 4: basket_perf kind=rerank_ab 扩展(A/B 对照)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(:1991-2043 `seats_basket_perf`)
- Test: 先 `Glob tests/*basket*` 定位既有测试文件归属;有则追加,无则新建 `tests/test_basket_perf_api.py`(client fixture 照 tests/test_rescore_api.py 范式)

**Interfaces:**
- Consumes: `guanlan_v2.screen.picks.read_picks(limit)->List[dict]`;端点内既有 `_closes`/`_norm`/`compute_basket_perf`/`bench_df` 机制;`guanlan_v2/seats/basket_perf.py` 的返回键名(**实现前先读该文件对齐 excess 键名,不臆造**)。
- Produces: `GET /seats/basket_perf?kind=rerank_ab&limit=5` → `{ok, kind:"rerank_ab", pairs:[{run_id, ts, arms:{data:{...}, rerank:{...}}, excess_diff}], n}`;默认(无 kind)行为逐字不变。

- [ ] **Step 1: 写失败测试**

```python
def test_basket_perf_default_behavior_unchanged(client):
    """无 kind:codes/start 必填契约原样(守护现有消费方零变化)。"""
    r = client.get("/seats/basket_perf").json()
    assert r["ok"] is False and "必填" in r["reason"]


def test_basket_perf_rerank_ab_pairs(tmp_path, monkeypatch, client):
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    ts = "2026-07-01T18:00:00"
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600000"],
                    "run_id": "rs_a", "ts": ts, "snapshot": False})
    pk.append_pick({"kind": "rerank_ab", "arm": "rerank", "codes": ["SZ000001"],
                    "run_id": "rs_a", "ts": ts, "snapshot": False})
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600001"],
                    "run_id": "rs_half", "ts": ts, "snapshot": False})   # 半对→跳过
    r = client.get("/seats/basket_perf", params={"kind": "rerank_ab", "limit": 5}).json()
    assert r["ok"] and r["kind"] == "rerank_ab" and r["n"] == 1
    pair = r["pairs"][0]
    assert pair["run_id"] == "rs_a" and set(pair["arms"]) == {"data", "rerank"}
    # 两臂各为 compute_basket_perf 结果;测试环境无行情时两臂 ok:false 也如实并列(不编数)
```

- [ ] **Step 2: 跑确认失败** — Expected: 2 FAIL(kind 未识别 → 走 codes 必填分支返回后 `kind` 键缺失)

- [ ] **Step 3: 实现**:`seats_basket_perf` 签名加 `kind: str = "", limit: int = 5`;在既有局部函数(`_closes` 等)与 `bench_df` 就绪之后、对 `codes` 的必填校验**之前**插入分支(需要把 `_closes`/`bench_df` 的构造提到 codes 校验之前——保持原逻辑不变仅调序;调序后原路径行为必须由 Step 1 守护测试证明不变):

```python
        if (kind or "").strip() == "rerank_ab":
            from guanlan_v2.screen.picks import read_picks
            rows = [r for r in read_picks(limit=500) if r.get("kind") == "rerank_ab"]
            by_run: dict = {}
            for r in rows:
                by_run.setdefault(r.get("run_id"), {})[r.get("arm")] = r
            pairs = []
            for rid, arms in by_run.items():
                if "data" not in arms or "rerank" not in arms:
                    continue                     # 半对(写一半失败)诚实跳过
                start_d = str(arms["data"].get("ts") or "")[:10]
                out_arms = {}
                for arm in ("data", "rerank"):
                    codes_a = [str(c) for c in (arms[arm].get("codes") or [])][:40]
                    closes: dict = {}
                    for c in codes_a:
                        cc = c
                        if _norm is not None:
                            try:
                                cc = _norm(c)
                            except Exception:  # noqa: BLE001
                                cc = (c or "").strip().upper()
                        try:
                            closes[cc] = await asyncio.to_thread(_closes, cc)
                        except Exception:  # noqa: BLE001
                            closes[cc] = []
                    out_arms[arm] = compute_basket_perf(closes, start=start_d,
                                                        horizon=horizon,
                                                        bench_df=bench_df)
                ex_r = out_arms["rerank"].get("excess")
                ex_d = out_arms["data"].get("excess")
                diff = (round(ex_r - ex_d, 4)
                        if isinstance(ex_r, (int, float)) and isinstance(ex_d, (int, float))
                        else None)
                pairs.append({"run_id": rid, "ts": arms["data"].get("ts"),
                              "arms": out_arms, "excess_diff": diff})
                if len(pairs) >= max(1, min(int(limit or 5), 20)):
                    break
            return JSONResponse({"ok": True, "kind": "rerank_ab",
                                 "pairs": pairs, "n": len(pairs)})
```

(`excess` 键名以 `guanlan_v2/seats/basket_perf.py` 实际返回为准——若实际叫别名,同步改本分支与测试,绝不改纯函数。)

- [ ] **Step 4: 跑确认通过** — 新测 2 条 + 既有 basket_perf/seats 测试全绿
- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/seats/api.py tests/test_basket_perf_api.py
git commit -m "feat(rerank): basket_perf kind=rerank_ab A/B 对照(默认行为零变化)"
```

---

### Task 5: 帷幄三件 + 四处同步(48/73/52)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(`rescore_impl`/`_rescore_lines` 升级 + 新 `rerank_perf_impl`/`rerank_distill_impl` + `WW_TOOL_TABLE` +2)
- Modify: `guanlan_v2/console/api.py`(`_SYSTEM_PROMPT` 能力行 + 纪律 16)
- Modify: `glmcp/README.md`(两处 "50 个"→"52 个")
- Test: `tests/test_console_tools.py`(计数 46→48/71→73 + 两 impl 单测)、`tests/test_guanlan_mcp.py`(×3 处 50→52)

**Interfaces:**
- Consumes: Task 2 rescore run 行 `rerank` 块;Task 4 `GET /seats/basket_perf?kind=rerank_ab&limit=`(transport **逐字沿用** tools.py 中 `rescore_impl` 对 /screen/rescore 端点的既有调用方式,先读再仿);`memory_write_impl(text, scope, key)`(tools.py:1194,key 消毒其内已做)。
- Produces: `ww_rerank_perf`(instant/只读)/`ww_rerank_distill`(confirm)两工具 + `ww_rescore` 输出重排摘要。

- [ ] **Step 1: 写失败测试**(追加 `tests/test_console_tools.py`;计数断言 46→48、71→73;`tests/test_guanlan_mcp.py` 三处 50→52)

```python
def test_rerank_distill_enforces_prefix(monkeypatch):
    import guanlan_v2.console.tools as ct
    seen = {}
    monkeypatch.setattr(ct, "memory_write_impl",
                        lambda text, scope, key: seen.update(k=key, t=text) or {"ok": True})
    r = ct.rerank_distill_impl(key="光芯片顺风判断",
                               text="6月底顺风提升的光芯片票 20日超额 +2.1pp")
    assert r["ok"] and seen["k"] == "行业·光芯片顺风判断"     # 强制前缀
    ct.rerank_distill_impl(key="行业·情绪", text="x")
    assert seen["k"] == "行业·情绪"                            # 已带前缀不重复加
    r3 = ct.rerank_distill_impl(key="", text="x")
    assert r3["ok"] is False                                   # key 必填


def test_rerank_perf_impl_renders_pairs(monkeypatch):
    import guanlan_v2.console.tools as ct
    fake = {"ok": True, "kind": "rerank_ab", "n": 1, "pairs": [
        {"run_id": "rs_a", "ts": "2026-07-01T18:00:00", "excess_diff": 0.021,
         "arms": {"data": {"ok": True, "excess": -0.01},
                  "rerank": {"ok": True, "excess": 0.011}}}]}
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: fake)   # 桥打桩
    r = ct.rerank_perf_impl(limit=5)
    assert r["ok"] and "rs_a" in r["content"] and "+2.1pp" in r["content"]
```

- [ ] **Step 2: 跑确认失败**(计数守护先红 46≠48,两 impl AttributeError)

- [ ] **Step 3: 实现**:
  - `WW_TOOL_TABLE` +2:`ww_rerank_perf`(instant,只读,「重排 A/B 前向对照成绩单」)/`ww_rerank_distill`(confirm,「A/B 结论蒸馏为行业教训入帷幄记忆(key 强制行业·前缀)」);
  - `_rerank_perf_fetch(limit)` 桥(transport 照 `rescore_impl` 既有端点调用方式指向 `/seats/basket_perf?kind=rerank_ab&limit=`)+ `rerank_perf_impl(limit=5)`:成绩单文本——每对一行 `run_id · 日期 · data臂 excess · rerank臂 excess · Δ=+x.xpp`(`excess_diff*100` 保留 1 位),未成熟/失败臂如实标注;无对时回「暂无 A/B 档案(先跑再打分+重排攒档案)」;
  - `rerank_distill_impl(key, text)`:key 空 → ok:false;消毒后无「行业·」前缀则加上;经 `memory_write_impl(text=text, scope="global", key=key)` 写入;
  - `_rescore_lines` 追加重排段:`run.get("rerank")` 存在时——ok 则输出 `overall`+model+`教训注入 n` 一行 + 按 |rank_before-rank_after| 降序前 5 行(`SH600000 7→2 ↑5 顺风·理由前40字`);ok:false 则 `重排失败: reason` 一行;无 rerank 键零输出(旧档案兼容);
  - `console/api.py _SYSTEM_PROMPT`:能力行加两工具;纪律 16:「重排是展示参考双轨,正式 picks 未经人审切换前绝不改;蒸馏教训必须引用 ww_rerank_perf 的真实 A/B 数字,绝不凭印象编教训」;
  - `glmcp/README.md` 两处 "50 个"→"52 个"。

- [ ] **Step 4: 跑确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: 全绿(计数 48/73/52)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py glmcp/README.md tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(rerank): ww_rerank_perf+ww_rerank_distill+ww_rescore 摘要升级(计数 48/73/52 四处同步)"
```

---

### Task 6: 选股页名次对照列(UI 只填充)

**Files:**
- Modify: `ui/screen/screen-app.jsx`(P5 段 :1111-1310——RescoreBar 定义/挂载、Row 三列渲染处 :1296 附近)
- Modify: `ui/screen/观澜 · 选股.html`(jsx 引用 bump `?v=20260705p6`,用 Edit)

**Interfaces:**
- Consumes: `GET /screen/rescore/latest` 的 run 行(Task 2 起带 `rerank` 块:`{ok, model, elapsed_sec, lessons_injected, overall, reason?, rows:[{code, rank_before, rank_after, stance, reason}]}`)。
- Produces: 结果表新列 + 按钮/元数据升级;无 rerank 块零占位;失败徽章。

- [ ] **Step 1: 实现**(前端无单测,验证在 Task 7 浏览器亲手;**babel 全局词法域:绝不在顶层重新解构 React**;色变量/传参路径照 P5 rsMap 现成写法逐字仿):
  - RescoreBar:按钮文案 `再打分+重排 ✦`(title 追加「+行业重排(LLM 整批)」);元数据行追加(有 latest 且带 rerank 块时):ok → `· 重排 {model} {elapsed_sec}s · 教训注入 {lessons_injected}`;ok:false → `· 重排失败:{reason 前60字}`(醒目色);
  - `rkMap` 构建:latest run `rerank.ok` 时 `Object.fromEntries(run.rerank.rows.map(r => [r.code, r]))`,沿 rsMap 同一路径传 XuanguApp → RankTable → Row(chosen+benched 两处调用点都补);
  - Row 内三列之后新增:

```jsx
{rkMap && rkMap[s.code] && (() => {
  const k = rkMap[s.code];
  const d = k.rank_before - k.rank_after;                    // >0 = 提升
  const big = Math.abs(d) >= 10;
  const col = k.stance === '顺风' ? 'var(--dai)'
            : k.stance === '逆风' ? 'var(--zhu)' : 'var(--ink-3)';
  return <span className="mono"
    title={`${k.stance} · ${k.reason || ''}(LLM 重排)`}
    style={{ fontSize: 8.5, marginLeft: 6, flexShrink: 0,
             color: big ? 'var(--jin)' : 'var(--ink-2)' }}>
    <span style={{ color: col }}>●</span> {k.rank_before}→{k.rank_after}
    {d !== 0 && <span style={{ color: d > 0 ? 'var(--dai)' : 'var(--zhu)' }}>
      {d > 0 ? `↑${d}` : `↓${-d}`}</span>}
  </span>;
})()}
```

  (色变量名 `--dai/--zhu/--jin/--ink-*` 以文件内 P5 列既有用法为准,若不同照抄现名。)
  - html 内 jsx 引用 `?v=` 用 Edit bump 为 `20260705p6`。

- [ ] **Step 2: Commit**

```bash
git add "ui/screen/screen-app.jsx" "ui/screen/观澜 · 选股.html"
git commit -m "feat(ui): 选股页名次对照列(Δ徽章+stance色点+理由tooltip)+按钮/元数据升级"
```

---

### Task 7: 全量回归 + 真机 e2e(控制器亲手,绝不转包)

- [ ] 全量:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest -q`(基线 ~890+新增;test_industry_ingest 套件序失败按惯例隔离复跑 6/6 归档)
- [ ] e2e @9998(`FA_CONFIG_DIR` 隔离配置起 9998 实例,勿动 9999/watchdog):
  1. `POST /screen/rescore {top_n:5, note:"p6-e2e"}` → 轮询 status 至 done;
  2. `GET /screen/rescore/latest`:`rerank` 块真值断言——成功则验 5 票集合等/reason 非空/board_snapshot 有值;失败则验 reason 显形+数据榜 rows 照常(两种都是合法真机结局,如实记录);
  3. `GET /screen/picks?kind=rerank_ab` 两臂成对;`GET /screen/picks` 默认不含;
  4. `GET /seats/basket_perf?kind=rerank_ab&limit=5`:pairs 结构+未成熟 matured:false 显形;
  5. 浏览器(playwright):再打分+重排按钮 → 名次对照列/↑↓徽章/tooltip 理由;旧档案零占位回归;
  6. 失败注入:`FA_CONFIG_DIR` 指向坏 LLM 配置副本再跑一次 → UI 失败徽章+数据榜照旧;
  7. 工具冒烟:`ww_rescore`(带重排摘要)/`ww_rerank_perf`/`ww_rerank_distill`(含拒确认路径);
  8. 收尾:杀 9998、9999 重启吃新代码(watchdog 自愈)+ 生产 `/screen/rescore/latest` 探活。
- [ ] 台账记全部证据(命令+关键输出)

### Task 8: 终审 + 合并收尾(控制器)

- [ ] `review-package <merge-base> HEAD` → opus 最终评审(READY TO MERGE 才走下一步)
- [ ] finishing-a-development-branch:全量测试 → FF 合 main → 删分支 → 9999 重启探活
- [ ] 记忆更新(topic 段落 + MEMORY.md 钩子)、任务 #92 完结

## Self-Review

- **Spec 覆盖**:§1 引擎→T1;§2 编排+日跑→T2/T3;§3 双轨+A/B→T2/T4;§4 反思闭环→T5(教训读回在 T1);§5 UI→T6;§6 四处同步→T5;§7 诚实合约→各任务测试+T7 失败注入;§8 测试计划→T1-T7 逐条落。无缺口。
- **占位符**:无 TBD/TODO;三处「以现有实现为准」(picks 响应键名/excess 键名/色变量名)均给定位方法与对齐规则,属防臆造指令而非留白。
- **类型一致性**:`run_rerank(rows, market)` T1 定义=T2 桥消费;`start_rescore_bg(top_n, note)` T3 定义=日跑钩子消费;`rerank_ab` 行字段 T2 产=T4/T7 消费;`rerank` 块字段 T1 产=T5/T6 消费;计数 48/73/52 全文一致。
