# 观澜 · 帷幄 一期·骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地帷幄一期骨架——单核心对话 agent(扩展 buddy)经事件日志+SSE 操控平台,三栏前端按需滑出嵌入的真选股/工作流页,一句话跑通「验证因子→回测→选股」全链。

**Architecture:** 后端新增 `guanlan_v2/console/`(jsonl 事件存储 + SSE 流 + 工具集注册进 buddy TOOL_REGISTRY + 显式 allowed_tools 白名单);前端新增 `ui/console/`(对话壳 + iframe 工作台宿主);旧页改动仅限授权清单(nav embed 守卫、选股/工作流的页头与 agent 窗口隐藏、screen take 通道小扩展)。

**Tech Stack:** FastAPI + asyncio(StreamingResponse SSE)、buddy BuddyAgent(async generator TurnEvent)、React 18 UMD + babel standalone(no-build)、native EventSource、pytest + TestClient。

**Spec:** `docs/superpowers/specs/2026-06-11-weiwo-console-design.md`

---

## ⚠ 仓库约定(执行前必读)

- **本仓无 git**(`Is a git repository: false`)。计划里没有 commit 步骤,每个任务以「检查点」收尾:跑 pytest / 重启 9999 / 浏览器验真。**不要 git init**。
- **改 python 必须重启 9999**:杀监听 PID(`Get-NetTCPConnection -LocalPort 9999 -State Listen | Select -Expand OwningProcess | % { Stop-Process -Id $_ -Force }`),看门狗 ~10s 拉新代码。agent 自己起的 9998 会被回收,验证一律用 9999。
- **改 jsx 必 bump `?v=`,用 Edit 不用 sed**;浏览器按 `?v` 缓存 jsx,验证前先 bump 再 reload。
- **pytest**:仓根运行 `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests -q`(与看门狗同解释器)。现有基线 99 绿,收尾必须 ≥99 绿。
- **测试不 import 引擎**:现有 tests 全部「裸 FastAPI + 单 router」模式(见 tests/test_screen_api.py:8-21),engine 导入只发生在 9999 进程(`_ensure_engine_importable` 先于一切)。console 的引擎依赖一律**懒导入到函数体内**,测试用注入/monkeypatch 绕开。
- **诚实口径**:工具失败返回 `ToolResult(..., is_error=True)` 文案直说原因;不造假数据;JSONResponse 失败统一 `{ok:False, reason}` HTTP 200。

## File Structure(一次看全)

```
guanlan_v2/console/
  __init__.py          # export build_console_router
  store.py             # ConsoleStore: sessions/<sid>/{meta.json,events.jsonl},线程安全追加/读取/计划
  tools.py             # 控制台工具(注册进 buddy TOOL_REGISTRY)+ contextvar 会话上下文 + artifact 信封
  api.py               # /console/* 路由:send/stream/confirm/sessions;agent 轮编排;事件广播
guanlan_v2/server.py   # +3 行:挂 console router
tests/
  test_console_store.py
  test_console_tools.py
  test_console_api.py
ui/console/
  观澜 · 帷幄.html      # 页面模板(canonical 模式)
  console-data.jsx     # 纯逻辑:EventSource 客户端 + reducer + API 封装(window.WW)
  console-rail.jsx     # 左栏:会话 + 计划任务
  console-thread.jsx   # 中栏:消息流/计划块/工具卡/确认卡/输入坞
  console-bench.jsx    # 右栏:按需滑出的 iframe 宿主(handoff 驱动)
  console-app.jsx      # 主壳 + masthead
  README.md
ui/_shared/guanlan-nav.js        # embed=1 守卫 + 帷幄入口(授权改动)
ui/screen/screen-app.jsx         # EMBED/LEGACY 隐藏 + take('screen') 收 cfg(授权改动)
ui/screen/观澜 · 选股.html       # bump ?v
ui/factor/workflow.jsx           # EMBED/LEGACY 隐藏(授权改动)
ui/factor/观澜 · AI 工作流.html  # bump ?v
```

事件七型(spec §4.1):`user_msg / agent_delta / tool_call / tool_result / plan_update / task_update / condensation(二期)`,本期另用 `confirm_request`(确认门)与 SSE 专用帧 `snapshot`。artifact 信封:`{kind, page, channel, payload, ref}`。

---

### Task 1: ConsoleStore(事件日志 + 会话 + 计划)

**Files:**
- Create: `guanlan_v2/console/__init__.py`
- Create: `guanlan_v2/console/store.py`
- Test: `tests/test_console_store.py`

- [ ] **Step 1.1: 写失败测试**

```python
# tests/test_console_store.py
"""ConsoleStore: 会话目录、事件追加(单调id)、计划、重读。纯文件级,不碰引擎。"""
from guanlan_v2.console.store import ConsoleStore


def test_create_and_list_sessions(tmp_path):
    st = ConsoleStore(root=tmp_path)
    meta = st.create_session(title="动量全流程")
    assert meta["id"].startswith("cs_") and meta["title"] == "动量全流程"
    assert meta["plan"] == [] and meta["status"] == "idle"
    metas = st.list_sessions()
    assert [m["id"] for m in metas] == [meta["id"]]


def test_append_and_read_events_monotonic(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    e1 = st.append_event(sid, "user_msg", text="你好")
    e2 = st.append_event(sid, "agent_delta", text="收到")
    assert (e1["id"], e2["id"]) == (1, 2)
    assert e1["type"] == "user_msg" and e1["ts"]
    evs = st.read_events(sid)
    assert [e["id"] for e in evs] == [1, 2]
    assert st.read_events(sid, after_id=1)[0]["text"] == "收到"


def test_plan_roundtrip(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    todos = [{"id": "t1", "text": "验证动量因子", "status": "in_progress"}]
    meta = st.set_plan(sid, todos)
    assert meta["plan"] == todos
    assert st.get_meta(sid)["plan"][0]["status"] == "in_progress"


def test_delete_session(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    st.append_event(sid, "user_msg", text="x")
    assert st.delete_session(sid) is True
    assert st.list_sessions() == [] and st.get_meta(sid) is None
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_console_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guanlan_v2.console'`

- [ ] **Step 1.3: 实现**

`guanlan_v2/console/__init__.py` 本步只写一行 docstring(`"""帷幄 · 单核心对话总控台(一期骨架)。"""`),Task 5 末尾再补 `from guanlan_v2.console.api import build_console_router` ——避免中间状态 import 失败。

```python
# guanlan_v2/console/store.py
"""ConsoleStore — 帷幄会话的文件级事实库。

每会话一目录:var/console/sessions/<sid>/
  meta.json     {id,title,created,updated,status,plan,next_event_id}
  events.jsonl  一行一事件,只追加,id 单调递增
恢复 = 重放 events.jsonl;线程安全(工具在 to_thread 里写)。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "var" / "console"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ConsoleStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else _DEFAULT_ROOT
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── 会话 ──
    def _sdir(self, sid: str) -> Path:
        return self.sessions_dir / sid

    def create_session(self, title: str = "新对话") -> Dict[str, Any]:
        sid = "cs_" + uuid.uuid4().hex[:12]
        meta = {"id": sid, "title": title, "created": _now(), "updated": _now(),
                "status": "idle", "plan": [], "next_event_id": 1}
        d = self._sdir(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").touch()
        self._save_meta(meta)
        return meta

    def get_meta(self, sid: str) -> Optional[Dict[str, Any]]:
        p = self._sdir(sid) / "meta.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        p = self._sdir(meta["id"]) / "meta.json"
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def list_sessions(self) -> List[Dict[str, Any]]:
        out = []
        if self.sessions_dir.exists():
            for d in self.sessions_dir.iterdir():
                m = self.get_meta(d.name)
                if m:
                    out.append(m)
        return sorted(out, key=lambda m: m.get("updated", ""), reverse=True)

    def delete_session(self, sid: str) -> bool:
        d = self._sdir(sid)
        if not d.exists():
            return False
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        return True

    # ── 事件 ──
    def append_event(self, sid: str, etype: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                raise KeyError(f"unknown session {sid}")
            ev = {"id": meta["next_event_id"], "ts": _now(), "type": etype}
            ev.update(fields)
            with (self._sdir(sid) / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            meta["next_event_id"] = ev["id"] + 1
            meta["updated"] = _now()
            self._save_meta(meta)
            return ev

    def read_events(self, sid: str, after_id: int = 0, limit: int = 2000) -> List[Dict[str, Any]]:
        p = self._sdir(sid) / "events.jsonl"
        if not p.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("id", 0) > after_id:
                out.append(ev)
        return out[-limit:]

    # ── 计划 / 状态 ──
    def set_plan(self, sid: str, todos: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                raise KeyError(f"unknown session {sid}")
            meta["plan"] = todos
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta

    def set_status(self, sid: str, status: str) -> None:
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return
            meta["status"] = status
            meta["updated"] = _now()
            self._save_meta(meta)
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_console_store.py -q`
Expected: 4 passed

- [ ] **Step 1.5: 检查点** — 全量 `pytest tests -q` 仍 ≥99 绿(基线 99 + 新 4)。

---

### Task 2: console/tools.py — 会话上下文 + plan.update + 摘要器

**Files:**
- Create: `guanlan_v2/console/tools.py`
- Test: `tests/test_console_tools.py`

设计要点:工具函数是**同步纯 python**,经 `asyncio.to_thread(tool.run, **args)` 调用(buddy agent.py:420),contextvars 被 to_thread 复制——用 ContextVar 传 sid/store。引擎 `Tool/ToolResult/TOOL_REGISTRY` 只在 `register_console_tools()` 里懒导入;纯逻辑(摘要、信封、plan 校验)独立成可测函数。

- [ ] **Step 2.1: 写失败测试**

```python
# tests/test_console_tools.py
"""console 工具纯逻辑:plan 校验、指标摘要、artifact 信封。不 import 引擎。"""
from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console import tools as ct


def test_plan_update_normalizes_and_writes(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    tok_s = ct.CTX_STORE.set(st)
    tok_i = ct.CTX_SID.set(sid)
    try:
        out = ct.plan_update_impl(todos=[
            {"text": "验证动量因子", "status": "done"},
            {"text": "回测", "status": "in_progress"},
            {"text": "选股"},                       # 缺 status → pending
        ])
    finally:
        ct.CTX_SID.reset(tok_i)
        ct.CTX_STORE.reset(tok_s)
    assert out["ok"] is True and out["n"] == 3
    plan = st.get_meta(sid)["plan"]
    assert plan[2]["status"] == "pending" and plan[0]["id"] == "t1"


def test_plan_update_without_context_fails_honest():
    out = ct.plan_update_impl(todos=[{"text": "x"}])
    assert out["ok"] is False and "会话上下文" in out["reason"]


def test_plan_update_rejects_bad_status(tmp_path):
    st = ConsoleStore(root=tmp_path)
    sid = st.create_session()["id"]
    tok_s = ct.CTX_STORE.set(st); tok_i = ct.CTX_SID.set(sid)
    try:
        out = ct.plan_update_impl(todos=[{"text": "x", "status": "weird"}])
    finally:
        ct.CTX_SID.reset(tok_i); ct.CTX_STORE.reset(tok_s)
    assert out["ok"] is False and "status" in out["reason"]


def test_summarize_factor_report():
    r = {"ok": True, "headline_ic": {"rank_ic": 0.052, "rank_icir": 0.31},
         "oos": {"is_rank_ic": 0.06, "oos_rank_ic": 0.04}, "n_dates": 23}
    s = ct.summarize_factor_report(r)
    assert "0.052" in s and "OOS" in s


def test_summarize_backtest():
    r = {"ok": True, "backtest": {"net_ann": 0.124, "portfolio_kpi": {
        "sharpe": 0.68, "max_drawdown": -0.18, "win_rate": 0.55}}}
    s = ct.summarize_backtest(r)
    assert "0.68" in s and "12.4%" in s


def test_artifact_envelope():
    a = ct.artifact("screen_result", page="screen", channel="screen",
                    payload={"cfg": {"pool": "csi300"}})
    assert a == {"kind": "screen_result", "page": "screen", "channel": "screen",
                 "payload": {"cfg": {"pool": "csi300"}}, "ref": None}
```

- [ ] **Step 2.2: 跑测试确认失败**(`cannot import name 'tools'`)

- [ ] **Step 2.3: 实现(本任务落上下文/plan/摘要/信封/自HTTP助手;引擎调用工具在 Task 3-4 追加)**

```python
# guanlan_v2/console/tools.py
"""帷幄控制台工具。

注册机制:register_console_tools() 把 Tool 字面量追加进 buddy TOOL_REGISTRY
(engine 懒导入,仅 9999 进程调用);CONSOLE_ALLOWED 是 run_turn 的显式白名单。
会话上下文经 ContextVar 传入(asyncio.to_thread 会复制 context)。
纯逻辑(plan 校验/指标摘要/artifact 信封)保持可单测、不依赖引擎。
"""
from __future__ import annotations

import contextvars
import json
import os
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

CTX_SID: contextvars.ContextVar = contextvars.ContextVar("weiwo_sid", default=None)
CTX_STORE: contextvars.ContextVar = contextvars.ContextVar("weiwo_store", default=None)

_VALID_STATUS = {"pending", "in_progress", "done"}
_REPORTS_STORE = Path(__file__).resolve().parents[1] / "reports" / "store"


# ── artifact 信封(spec §4.1)──
def artifact(kind: str, page: Optional[str] = None, channel: Optional[str] = None,
             payload: Optional[Dict[str, Any]] = None, ref: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "page": page, "channel": channel,
            "payload": payload or {}, "ref": ref}


# ── plan.update(TodoWrite 式整单替换)──
def plan_update_impl(todos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    store = CTX_STORE.get()
    sid = CTX_SID.get()
    if store is None or sid is None:
        return {"ok": False, "reason": "无会话上下文(只能在帷幄会话内调用)"}
    norm: List[Dict[str, Any]] = []
    for i, t in enumerate(todos or []):
        text = str((t or {}).get("text", "")).strip()
        if not text:
            return {"ok": False, "reason": f"第{i + 1}项缺 text"}
        status = str((t or {}).get("status", "pending"))
        if status not in _VALID_STATUS:
            return {"ok": False, "reason": f"第{i + 1}项 status 非法: {status}(允许 pending/in_progress/done)"}
        norm.append({"id": (t or {}).get("id") or f"t{i + 1}", "text": text, "status": status})
    store.set_plan(sid, norm)
    return {"ok": True, "n": len(norm), "todos": norm}


# ── 指标摘要(给 LLM 看的一行人话)──
def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def summarize_factor_report(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"因子分析失败: {r.get('reason', '未知原因')}"
    h = r.get("headline_ic") or {}
    oos = r.get("oos") or {}
    parts = [f"RankIC {h.get('rank_ic')}", f"RankICIR {h.get('rank_icir')}",
             f"期数 {r.get('n_dates')}"]
    if oos:
        parts.append(f"OOS RankIC {oos.get('oos_rank_ic')}(IS {oos.get('is_rank_ic')})")
    return "因子分析完成: " + " · ".join(str(p) for p in parts)


def summarize_backtest(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"回测失败: {r.get('reason', '未知原因')}"
    bt = r.get("backtest") or {}
    k = bt.get("portfolio_kpi") or {}
    return ("回测完成: 净年化 " + _pct(bt.get("net_ann"))
            + f" · Sharpe {k.get('sharpe')} · 最大回撤 {_pct(k.get('max_drawdown'))}"
            + f" · 胜率 {_pct(k.get('win_rate'))}")


def summarize_screen(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"选股失败: {r.get('reason', '未知原因')}"
    rows = r.get("chosen") or []
    head = []
    for row in rows[:5]:
        s = row.get("s") or {}
        head.append(f"{s.get('name')}({s.get('code')}) {s.get('rating', '')}")
    return f"选股完成: 入选 {len(rows)} 只,前5 = " + ";".join(head)


# ── 同进程自 HTTP(handler 是工厂闭包不可导入的模块用;跑在 to_thread,不堵事件循环)──
def _self_post(path: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    port = os.environ.get("GUANLAN_PORT", "9999")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _self_get(path: str, timeout: int = 30) -> Dict[str, Any]:
    port = os.environ.get("GUANLAN_PORT", "9999")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _two_years_ago() -> str:
    return (date.today() - timedelta(days=365 * 2)).isoformat()
```

- [ ] **Step 2.4: 跑测试确认通过**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_console_tools.py -q`
Expected: 6 passed

---

### Task 3: 引擎调用工具(因子分析 / 回测 — 进程内直调 workflow 模块级助手)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(追加)
- Test: `tests/test_console_tools.py`(追加)

事实依据:`_factor_report2(body) -> JSONResponse`(workflow/api.py:2830)与 `_backtest_vector(body) -> JSONResponse`(:2249)是**模块级**可导入;输入模型 `FactorReport2In`(:2634)/`BacktestVectorIn`(:1455,继承 ModelTrainIn)。**1y 默认窗坑**:不传 start 回测只取 1 年(api.py:402, 2282-2284),工具显式默认 2 年对齐 UI。

- [ ] **Step 3.1: 追加失败测试(monkeypatch 桥函数,不碰引擎)**

```python
# tests/test_console_tools.py 追加
def test_factor_analyze_impl_summary_and_artifact(monkeypatch):
    fake = {"ok": True, "headline_ic": {"rank_ic": 0.05, "rank_icir": 0.3},
            "oos": {}, "n_dates": 23}
    monkeypatch.setattr(ct, "_call_factor_report2", lambda **kw: fake)
    res = ct.factor_analyze_impl(expr="rank(-delta(close,20))")
    assert res["ok"] is True
    assert "RankIC 0.05" in res["content"]
    assert res["artifact"]["kind"] == "ic_report" and res["artifact"]["page"] == "factor"
    assert res["artifact"]["payload"]["expr"] == "rank(-delta(close,20))"


def test_backtest_impl_defaults_two_years(monkeypatch):
    seen = {}
    def fake_call(**kw):
        seen.update(kw)
        return {"ok": True, "backtest": {"net_ann": 0.1, "portfolio_kpi": {"sharpe": 1.0,
                "max_drawdown": -0.1, "win_rate": 0.5}}}
    monkeypatch.setattr(ct, "_call_backtest_vector", fake_call)
    res = ct.backtest_impl(expr="rank(roe)")
    assert res["ok"] is True and seen["start"] == ct._two_years_ago()
    assert res["artifact"]["channel"] == "workflow"
```

- [ ] **Step 3.2: 跑测试确认失败**(`AttributeError: module ... has no attribute '_call_factor_report2'`)

- [ ] **Step 3.3: 实现(tools.py 追加)**

```python
# guanlan_v2/console/tools.py 追加
# ── 引擎/模块桥(独立小函数便于 monkeypatch;懒导入 workflow 重模块)──
def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(workflow 模块级助手返回 JSONResponse)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _call_factor_report2(**kw: Any) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorReport2In, _factor_report2
    return _resp_json(_factor_report2(FactorReport2In(**kw)))


def _call_backtest_vector(**kw: Any) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import BacktestVectorIn, _backtest_vector
    return _resp_json(_backtest_vector(BacktestVectorIn(**kw)))


def factor_analyze_impl(expr: str, universe: str = "csi300", freq: str = "month",
                        oos_frac: float = 0.3, start: Optional[str] = None,
                        end: Optional[str] = None) -> Dict[str, Any]:
    expr = (expr or "").strip()
    if not expr:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    r = _call_factor_report2(expr_or_name=expr, universe=universe, freq=freq,
                             oos_frac=oos_frac, start=start, end=end)
    return {"ok": bool(r.get("ok")), "content": summarize_factor_report(r),
            "artifact": artifact("ic_report", page="factor", channel="workflow",
                                 payload={"expr": expr, "name": f"因子 {expr[:24]}"}),
            "raw": r}


def backtest_impl(expr: str, universe: str = "csi300", topn: int = 30,
                  weighting: str = "equal", rebalance: str = "month",
                  oos_frac: float = 0.3, start: Optional[str] = None,
                  end: Optional[str] = None) -> Dict[str, Any]:
    expr = (expr or "").strip()
    if not expr:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    r = _call_backtest_vector(features=[expr], universe=universe, topn=topn,
                              weighting=weighting, rebalance=rebalance,
                              oos_frac=oos_frac,
                              start=start or _two_years_ago(), end=end)
    return {"ok": bool(r.get("ok")), "content": summarize_backtest(r),
            "artifact": artifact("backtest_report", page="factor", channel="workflow",
                                 payload={"expr": expr, "name": f"回测 {expr[:24]}"}),
            "raw": r}
```

- [ ] **Step 3.4: 跑测试确认通过**(8 passed)

---

### Task 4: 自 HTTP 工具(选股/研判/经验卡/报告)+ register_console_tools

**Files:**
- Modify: `guanlan_v2/console/tools.py`(追加)
- Test: `tests/test_console_tools.py`(追加)

事实依据:screen `/screen/run` handler 在 build_screen_router 闭包内(screen/api.py:873 ∈ :804 工厂)不可导入 → 自 HTTP;ScreenIn 字段(:36-60):factors[{id,w}]/topN/blend/pool…;seats `/seats/decide` 同理(seats/api.py:417 ∈ :158);裸 dict body;reports 直接读 `guanlan_v2/reports/store/*.json`(reports/api.py:26)。

- [ ] **Step 4.1: 追加失败测试**

```python
# tests/test_console_tools.py 追加
def test_screen_impl(monkeypatch):
    fake = {"ok": True, "chosen": [{"s": {"code": "688283", "name": "坤恒顺维", "rating": "强"}}]}
    sent = {}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path; sent.update(payload); return fake
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.screen_impl(factors=[{"id": "lib_turnover_cv20", "w": 1.0}], pool="csi300", blend=0.6, topN=20)
    assert sent["path"] == "/screen/run" and sent["pool"] == "csi300" and sent["blend"] == 0.6
    assert res["ok"] is True and "坤恒顺维" in res["content"]
    assert res["artifact"]["channel"] == "screen"
    assert res["artifact"]["payload"]["cfg"]["topN"] == 20


def test_reports_query_impl_reads_store(tmp_path, monkeypatch):
    import json as _json
    d = tmp_path / "store"; d.mkdir()
    (d / "r1.json").write_text(_json.dumps({"id": "r1", "name": "动量验证", "ts": 1,
        "method": "report2", "kpi": {"rank_ic": 0.05}}), encoding="utf-8")
    monkeypatch.setattr(ct, "_REPORTS_STORE", d)
    res = ct.reports_query_impl(q="动量")
    assert res["ok"] is True and "动量验证" in res["content"]


def test_register_console_tools_idempotent(monkeypatch):
    import types
    class _T:
        def __init__(self, **kw): self.__dict__.update(kw)
    reg = []
    fake_mod = types.SimpleNamespace(Tool=_T, ToolResult=None, TOOL_REGISTRY=reg)
    monkeypatch.setattr(ct, "_buddy_tools_mod", lambda: fake_mod)
    n1 = ct.register_console_tools()
    n2 = ct.register_console_tools()
    names = {t.name for t in reg}
    assert n1 == n2 == len(names)
    assert {"ww_plan_update", "ww_factor_analyze", "ww_backtest", "ww_screen_run"} <= names
```

- [ ] **Step 4.2: 跑测试确认失败**

- [ ] **Step 4.3: 实现(tools.py 追加)**

```python
# guanlan_v2/console/tools.py 追加
def screen_impl(factors: Optional[List[Dict[str, Any]]] = None, pool: str = "csi300",
                blend: float = 0.6, topN: int = 20) -> Dict[str, Any]:
    cfg = {"factors": [{"id": str(f.get("id")), "w": float(f.get("w", 1.0))} for f in (factors or [])],
           "pool": pool, "blend": blend, "topN": topN}
    try:
        r = _self_post("/screen/run", cfg)
    except Exception as e:
        return {"ok": False, "content": f"选股调用失败: {e}", "artifact": None}
    return {"ok": bool(r.get("ok")), "content": summarize_screen(r),
            "artifact": artifact("screen_result", page="screen", channel="screen",
                                 payload={"cfg": cfg}),
            "raw": r}


def seats_decide_impl(code: str, name: str = "", creed: str = "",
                      mode: str = "fast") -> Dict[str, Any]:
    try:
        r = _self_post("/seats/decide", {"code": code, "name": name, "creed": creed,
                                         "mode": mode}, timeout=180)
    except Exception as e:
        return {"ok": False, "content": f"研判调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"研判失败: {r.get('reason')}", "artifact": None}
    return {"ok": True,
            "content": (f"落子研判 {r.get('name')}({r.get('code')}): 方向 {r.get('direction')}"
                        f" · 置信 {r.get('confidence')} · {str(r.get('rationale', ''))[:200]}"),
            "artifact": artifact("seat_decision", page="seats", channel="cockpit",
                                 payload={"code": code, "name": name}),
            "raw": r}


def cards_query_impl(status: str = "all") -> Dict[str, Any]:
    try:
        r = _self_get(f"/cards/list?status={status}")
    except Exception as e:
        return {"ok": False, "content": f"经验卡查询失败: {e}", "artifact": None}
    cards = r.get("cards") or []
    lines = [f"{c.get('id')} [{c.get('status')}] {c.get('title')} ({c.get('verdict')}, ic={c.get('ic')})"
             for c in cards[:20]]
    return {"ok": True, "content": f"经验卡 {len(cards)} 张:\n" + "\n".join(lines), "artifact": None,
            "raw": {"n": len(cards)}}


def reports_query_impl(q: str = "") -> Dict[str, Any]:
    items = []
    try:
        for p in sorted(_REPORTS_STORE.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if q and q not in str(rec.get("name", "")):
                continue
            items.append(f"{rec.get('id')} · {rec.get('name')} · {rec.get('method')} · kpi={rec.get('kpi')}")
    except Exception as e:
        return {"ok": False, "content": f"报告库读取失败: {e}", "artifact": None}
    return {"ok": True, "content": f"报告库匹配 {len(items)} 篇:\n" + "\n".join(items[:20]), "artifact": None}


# ── 注册进 buddy TOOL_REGISTRY ──
def _buddy_tools_mod():
    """懒导入引擎 buddy.tools(便于测试替身)。"""
    from financial_analyst.buddy import tools as bt
    return bt


def _wrap(impl):
    """impl dict → ToolResult(side_effect 携带 artifact / plan)。"""
    def run(**args):
        bt = _buddy_tools_mod()
        out = impl(**args)
        se: Dict[str, Any] = {}
        if out.get("artifact"):
            se["artifact"] = out["artifact"]
        if out.get("todos") is not None:          # plan_update 专属
            se["plan"] = out["todos"]
        if out.get("content"):
            content = str(out["content"])
        elif out.get("ok") and out.get("todos") is not None:
            content = f"计划已更新,{out.get('n')} 项"
        else:
            content = json.dumps({k: v for k, v in out.items() if k != "raw"},
                                 ensure_ascii=False)[:400]
        return bt.ToolResult(content=content, is_error=not out.get("ok", False),
                             side_effect=se or None)
    return run


_TODO_SCHEMA = {"type": "object", "properties": {
    "todos": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "text": {"type": "string"},
        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
        "required": ["text"]}}}, "required": ["todos"]}


def _expr_schema(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    props = {"expr": {"type": "string", "description": "zoo 因子表达式,如 rank(-delta(close,20))"},
             "universe": {"type": "string", "default": "csi300"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": ["expr"]}


def register_console_tools() -> int:
    """把帷幄工具追加进 TOOL_REGISTRY(幂等),返回帷幄工具总数。"""
    bt = _buddy_tools_mod()
    existing = {t.name for t in bt.TOOL_REGISTRY}
    specs = [
        ("ww_plan_update",
         "更新当前会话的任务计划(整单替换;TodoWrite 式)。复杂任务先拆计划再执行,每完成一步就更新 status。",
         _TODO_SCHEMA, _wrap(plan_update_impl), "instant", False),
        ("ww_factor_analyze",
         "因子截面分析(真引擎 RankIC/分组/样本外体检)。输入 zoo 表达式。Cross-sectional factor IC analysis.",
         _expr_schema({"freq": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                       "oos_frac": {"type": "number", "default": 0.3}}),
         _wrap(factor_analyze_impl), "seconds", False),
        ("ww_backtest",
         "因子向量化回测(分腿成本/定权/默认2年窗)。输入 zoo 表达式。Vector backtest with costs.",
         _expr_schema({"topn": {"type": "integer", "default": 30},
                       "weighting": {"type": "string", "enum": ["equal", "mktcap", "inv_vol", "risk_parity"], "default": "equal"},
                       "rebalance": {"type": "string", "enum": ["day", "week", "month"], "default": "month"}}),
         _wrap(backtest_impl), "seconds", False),
        ("ww_screen_run",
         "九视角选股(v4 模型 + 因子混合 α)。factors 的 id 来自 /screen/factors 目录;不确定就传空 factors 纯 v4 跑。Stock screening.",
         {"type": "object", "properties": {
             "factors": {"type": "array", "items": {"type": "object", "properties": {
                 "id": {"type": "string"}, "w": {"type": "number", "default": 1.0}}, "required": ["id"]}},
             "pool": {"type": "string", "enum": ["all", "csi300", "csi500", "csi800", "csi1000"], "default": "csi300"},
             "blend": {"type": "number", "default": 0.6}, "topN": {"type": "integer", "default": 20}}},
         _wrap(screen_impl), "seconds", False),
        ("ww_seats_decide",
         "触发落子席位研判(哨兵 agent,LLM 真研判并落盘 var/seats_decisions.jsonl)。需要用户确认。",
         {"type": "object", "properties": {
             "code": {"type": "string"}, "name": {"type": "string"},
             "creed": {"type": "string"}, "mode": {"type": "string", "enum": ["fast", "deep"], "default": "fast"}},
          "required": ["code"]},
         _wrap(seats_decide_impl), "seconds", True),
        ("ww_cards_query", "查询经验卡库(draft/approved/rejected/all)。",
         {"type": "object", "properties": {"status": {"type": "string", "default": "all"}}},
         _wrap(cards_query_impl), "instant", False),
        ("ww_reports_query", "检索工作流报告库(名称子串匹配)。",
         {"type": "object", "properties": {"q": {"type": "string", "default": ""}}},
         _wrap(reports_query_impl), "instant", False),
    ]
    for name, desc, schema, run, cost, confirm in specs:
        if name not in existing:
            bt.TOOL_REGISTRY.append(bt.Tool(name=name, description=desc, input_schema=schema,
                                            run=run, cost_hint=cost, confirm_required=confirm))
    return len(specs)


# run_turn 白名单 = 帷幄工具 + 精选 buddy 研究工具(全在 research 域,无因子域)
CONSOLE_ALLOWED = {
    "ww_plan_update", "ww_factor_analyze", "ww_backtest", "ww_screen_run",
    "ww_seats_decide", "ww_cards_query", "ww_reports_query",
    "quote_lookup", "realtime_quote", "stock_brief", "financials",
    "news_query", "wisdom_search", "quant_reports",
}
```

- [ ] **Step 4.4: 跑测试确认通过**(11 passed)

---

### Task 5: console/api.py — 会话 CRUD + SSE 流 + agent 轮编排

**Files:**
- Create: `guanlan_v2/console/api.py`
- Modify: `guanlan_v2/console/__init__.py`(补 export)
- Test: `tests/test_console_api.py`

设计:`build_console_router(store=None, agent_factory=None)`。`agent_factory(sid)` 返回有 `messages`、`async run_turn(text, confirm_callback, allowed_tools)`(yield 鸭子型 `.kind/.payload`)的对象——生产默认 BuddyAgent(签名 agent.py:256-258),测试注入 FakeAgent 不碰引擎。

- [ ] **Step 5.1: 写失败测试**

```python
# tests/test_console_api.py
"""console API:会话 CRUD / send→事件落盘(FakeAgent)/ snapshot 流 / 诚实失败。"""
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console.api import build_console_router


class _Evt:
    def __init__(self, kind, payload=None):
        self.kind, self.payload = kind, payload


class FakeAgent:
    """计划(side_effect.plan)→ 工具(artifact)→ 文本 → done。"""
    def __init__(self):
        self.messages = []

    async def run_turn(self, text, confirm_callback=None, allowed_tools=None):
        yield _Evt("tool_call", {"name": "ww_plan_update", "args": {"todos": [{"text": "回测", "status": "in_progress"}]}})
        yield _Evt("tool_result", {"name": "ww_plan_update", "content": "计划已更新,1 项", "is_error": False,
                                   "side_effect": {"plan": [{"id": "t1", "text": "回测", "status": "in_progress"}]}})
        yield _Evt("tool_call", {"name": "ww_backtest", "args": {"expr": "rank(roe)"}})
        yield _Evt("tool_result", {"name": "ww_backtest", "content": "回测完成: 净年化 12.4%", "is_error": False,
                                   "side_effect": {"artifact": {"kind": "backtest_report", "page": "factor",
                                                                "channel": "workflow", "payload": {"expr": "rank(roe)"}, "ref": None}}})
        yield _Evt("text", "回测结果已就绪。")
        yield _Evt("done", None)


def _client(tmp_path):
    app = FastAPI()
    store = ConsoleStore(root=tmp_path)
    app.include_router(build_console_router(store=store, agent_factory=lambda sid: FakeAgent()))
    return TestClient(app), store


def test_sessions_crud(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/console/sessions", json={"title": "测试"}).json()
    assert r["ok"] and r["meta"]["title"] == "测试"
    sid = r["meta"]["id"]
    assert any(m["id"] == sid for m in c.get("/console/sessions").json()["sessions"])
    assert c.request("DELETE", f"/console/sessions/{sid}").json()["ok"]


def test_send_runs_turn_and_persists_events(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    r = c.post("/console/send", json={"sid": sid, "text": "回测 rank(roe)"}).json()
    assert r["ok"] and r["sid"] == sid
    evs = []
    for _ in range(50):                      # 后台任务最多等 5s
        evs = store.read_events(sid)
        if any(e["type"] == "task_update" and e.get("status") == "done" for e in evs):
            break
        time.sleep(0.1)
    types = [e["type"] for e in evs]
    assert "user_msg" in types and "plan_update" in types and "agent_delta" in types
    tr = [e for e in evs if e["type"] == "tool_result" and e.get("artifact")]
    assert tr and tr[0]["artifact"]["kind"] == "backtest_report"
    assert store.get_meta(sid)["plan"][0]["text"] == "回测"


def test_send_unknown_session_fails_honest(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/console/send", json={"sid": "cs_nope", "text": "x"}).json()
    assert r["ok"] is False and "会话" in r["reason"]


def test_stream_snapshot_first_frame(tmp_path):
    c, store = _client(tmp_path)
    sid = c.post("/console/sessions", json={}).json()["meta"]["id"]
    store.append_event(sid, "user_msg", text="历史一条")
    with c.stream("GET", f"/console/stream/{sid}") as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            if "\n\n" in buf:
                break
    head = buf.split("\n\n")[0]
    assert head.startswith("event: snapshot")
    data = json.loads(head.split("data: ", 1)[1])
    assert data["meta"]["id"] == sid and data["events"][0]["text"] == "历史一条"
```

- [ ] **Step 5.2: 跑测试确认失败**

- [ ] **Step 5.3: 实现 api.py(完整代码)**

```python
# guanlan_v2/console/api.py
"""帷幄 /console 路由:事件日志 + SSE + buddy agent 轮编排。

事实流:POST /send 落 user_msg → 后台 asyncio task 跑 agent.run_turn →
TurnEvent 映射成事件,逐条 append 到 jsonl 并广播给 SSE 订阅者。
SSE:连上先发 snapshot(meta+全事件),再续直播;15s 注释心跳保活。
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse

from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console import tools as ct

_SYSTEM_PROMPT = """你是「观澜 · 帷幄」——A股投研平台的统帅 agent,在一个对话里指挥全平台。

可用能力(工具):任务计划 ww_plan_update、因子分析 ww_factor_analyze、回测 ww_backtest、
选股 ww_screen_run、落子研判 ww_seats_decide(需用户确认)、经验卡 ww_cards_query、
报告库 ww_reports_query,以及行情/财务/新闻/经验检索等查询工具。

纪律:
1. 复杂任务(≥2 步)先调 ww_plan_update 拆计划,每完成一步立即更新对应项 status,全部完成后收尾更新。
2. 数字必须来自工具结果,严禁编造;工具失败就直说失败原因,不装作成功。
3. 因子表达式用 zoo DSL(如 rank(-delta(close,20))、-std(returns,20)、rank(roe))。
4. 回答用中文,简洁;关键指标(RankIC/Sharpe/回撤)报数字。
5. 选股 factors 的 id 必须来自因子目录(不确定就先传空 factors 纯 v4 模型跑)。"""


def _safe(v: Any) -> Any:
    """递归清掉非有限 float(JSON 不接受 NaN/Inf)。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_safe(x) for x in v]
    return v


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(_safe(data), ensure_ascii=False)}\n\n"


def _default_agent_factory(sid: str):
    """生产路径:BuddyAgent + 帷幄工具注册(仅 9999 进程触达引擎)。"""
    from financial_analyst.buddy.agent import BuddyAgent
    ct.register_console_tools()
    return BuddyAgent(system_prompt=_SYSTEM_PROMPT)


def _reseed(agent, events: List[Dict[str, Any]], max_msgs: int = 16, max_chars: int = 8000) -> None:
    """进程重启后从事件日志重灌对话史(对齐 buddy _seed_agent_history 口径,server.py:80-105)。"""
    if getattr(agent, "messages", None):
        return
    msgs: List[Dict[str, str]] = []
    for ev in events:
        if ev.get("type") == "user_msg" and ev.get("text"):
            msgs.append({"role": "user", "content": str(ev["text"])})
        elif ev.get("type") == "agent_delta" and ev.get("text"):
            if msgs and msgs[-1]["role"] == "assistant":
                msgs[-1]["content"] += "\n" + str(ev["text"])
            else:
                msgs.append({"role": "assistant", "content": str(ev["text"])})
    msgs = msgs[-max_msgs:]
    while len(msgs) > 1 and sum(len(m["content"]) for m in msgs) > max_chars:
        msgs.pop(0)
    try:
        from financial_analyst.buddy.agent import Message
        for m in msgs:
            agent.messages.append(Message(role=m["role"], content=m["content"]))
    except Exception:
        pass  # FakeAgent / 测试路径:reseed 是增强项,不阻塞


def _plan_block(meta: Optional[Dict[str, Any]]) -> str:
    plan = (meta or {}).get("plan") or []
    if not plan:
        return ""
    mark = {"done": "✓", "in_progress": "▶", "pending": "○"}
    lines = [f"{mark.get(t.get('status'), '○')} {t.get('text')}" for t in plan]
    return "[当前任务计划——执行中随时用 ww_plan_update 更新]\n" + "\n".join(lines) + "\n\n"


def build_console_router(store: Optional[ConsoleStore] = None,
                         agent_factory=None) -> APIRouter:
    router = APIRouter(prefix="/console", tags=["console"])
    st = store or ConsoleStore()
    factory = agent_factory or _default_agent_factory

    agents: "OrderedDict[str, Any]" = OrderedDict()   # sid → agent(LRU 12,对话史进程内)
    subs: Dict[str, List[asyncio.Queue]] = {}          # sid → SSE 订阅队列
    pending: Dict[str, "asyncio.Future[str]"] = {}     # turn_id → confirm future
    running: set = set()                                # 正在跑轮的 sid

    def _agent_for(sid: str):
        if sid in agents:
            agents.move_to_end(sid)
            return agents[sid]
        a = factory(sid)
        _reseed(a, st.read_events(sid))
        agents[sid] = a
        while len(agents) > 12:
            agents.popitem(last=False)
        return a

    def _emit(sid: str, etype: str, **fields: Any) -> Dict[str, Any]:
        ev = st.append_event(sid, etype, **fields)
        for q in subs.get(sid, []):
            try:
                q.put_nowait(ev)
            except Exception:
                pass
        return ev

    # ── 会话 CRUD ──
    @router.get("/sessions")
    def sessions_list():
        return {"ok": True, "sessions": st.list_sessions()}

    @router.post("/sessions")
    def sessions_create(body: dict = Body(default={})):
        meta = st.create_session(title=str(body.get("title") or "新对话"))
        return {"ok": True, "meta": meta}

    @router.delete("/sessions/{sid}")
    def sessions_delete(sid: str):
        agents.pop(sid, None)
        return {"ok": st.delete_session(sid), "id": sid}

    # ── 发令 ──
    @router.post("/send")
    async def send(body: dict = Body(default={})):
        text = str(body.get("text") or "").strip()
        sid = str(body.get("sid") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "reason": "空指令"})
        if not sid:
            sid = st.create_session(title=text[:18])["id"]
        if st.get_meta(sid) is None:
            return JSONResponse({"ok": False, "reason": f"会话不存在: {sid}"})
        if sid in running:
            return JSONResponse({"ok": False, "reason": "该会话正有任务在跑,稍候再发"})
        _emit(sid, "user_msg", text=text)
        turn_id = uuid.uuid4().hex
        asyncio.get_running_loop().create_task(_run_turn(sid, text, turn_id))
        return {"ok": True, "sid": sid, "turn_id": turn_id}

    async def _run_turn(sid: str, text: str, turn_id: str):
        running.add(sid)
        st.set_status(sid, "running")
        _emit(sid, "task_update", task_id=turn_id, status="running", note="运筹中")
        tok_s = ct.CTX_STORE.set(st)
        tok_i = ct.CTX_SID.set(sid)

        async def confirm_cb(tool_name: str, args: dict) -> bool:
            fut: "asyncio.Future[str]" = asyncio.get_running_loop().create_future()
            pending[turn_id] = fut
            _emit(sid, "confirm_request", turn_id=turn_id, tool=tool_name, args=_safe(args))
            try:
                choice = await asyncio.wait_for(fut, timeout=600)
            except asyncio.TimeoutError:
                choice = "n"
            finally:
                pending.pop(turn_id, None)
            return choice in ("y", "a", "yes", "always")

        try:
            agent = _agent_for(sid)
            turn_text = _plan_block(st.get_meta(sid)) + text
            async for evt in agent.run_turn(turn_text, confirm_callback=confirm_cb,
                                            allowed_tools=ct.CONSOLE_ALLOWED):
                kind, payload = evt.kind, evt.payload
                if kind == "text" and payload:
                    _emit(sid, "agent_delta", text=str(payload))
                elif kind == "tool_call":
                    _emit(sid, "tool_call", tool=(payload or {}).get("name"),
                          args=_safe((payload or {}).get("args")))
                elif kind == "tool_result":
                    p = payload or {}
                    se = p.get("side_effect") or {}
                    if "plan" in se:
                        _emit(sid, "plan_update", todos=se["plan"])
                    _emit(sid, "tool_result", tool=p.get("name"),
                          ok=not p.get("is_error"), summary=str(p.get("content", ""))[:500],
                          artifact=_safe(se.get("artifact")))
                elif kind == "error":
                    _emit(sid, "task_update", task_id=turn_id, status="error",
                          note=str(payload)[:300])
        except Exception as e:
            _emit(sid, "task_update", task_id=turn_id, status="error",
                  note=f"{type(e).__name__}: {e}"[:300])
        finally:
            ct.CTX_SID.reset(tok_i)
            ct.CTX_STORE.reset(tok_s)
            running.discard(sid)
            st.set_status(sid, "idle")
            _emit(sid, "task_update", task_id=turn_id, status="done")

    # ── 确认门 ──
    @router.post("/confirm")
    def confirm(body: dict = Body(default={})):
        turn_id = str(body.get("turn_id") or "")
        fut = pending.get(turn_id)
        if fut is None or fut.done():
            return JSONResponse({"ok": False, "reason": "no pending confirm"})
        fut.set_result(str(body.get("choice") or "n"))
        return {"ok": True}

    # ── SSE ──
    @router.get("/stream/{sid}")
    async def stream(sid: str):
        if st.get_meta(sid) is None:
            return JSONResponse({"ok": False, "reason": f"会话不存在: {sid}"})

        async def gen():
            q: asyncio.Queue = asyncio.Queue()
            subs.setdefault(sid, []).append(q)
            try:
                yield _sse("snapshot", {"meta": st.get_meta(sid),
                                        "events": st.read_events(sid, limit=500)})
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse("ev", ev)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                try:
                    subs.get(sid, []).remove(q)
                except ValueError:
                    pass

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router
```

并把 `guanlan_v2/console/__init__.py` 改为:

```python
"""帷幄 · 单核心对话总控台(一期骨架)。"""
from guanlan_v2.console.api import build_console_router  # noqa: F401
```

- [ ] **Step 5.4: 跑测试确认通过**(4 passed)

- [ ] **Step 5.5: 检查点** — 全量 pytest ≥99+15 绿。

---

### Task 6: 挂载到 9999 + 真机冒烟

**Files:**
- Modify: `guanlan_v2/server.py`(reports router 之后,参照 server.py:199-201 模式)

- [ ] **Step 6.1: server.py 插入**

```python
    # ── 帷幄 console(单核心对话总控台,一期)─────────────────────
    from guanlan_v2.console import build_console_router

    app.include_router(build_console_router())
```

- [ ] **Step 6.2: 重启 9999**

```powershell
Get-NetTCPConnection -LocalPort 9999 -State Listen | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
Start-Sleep 12
Invoke-RestMethod http://127.0.0.1:9999/health
```
Expected: 看门狗拉新,/health 返回 ok。

- [ ] **Step 6.3: 真机冒烟(真 LLM + 真引擎)**

```powershell
$s = Invoke-RestMethod -Method Post http://127.0.0.1:9999/console/sessions -ContentType 'application/json' -Body '{"title":"冒烟"}'
$sid = $s.meta.id
Invoke-RestMethod -Method Post http://127.0.0.1:9999/console/send -ContentType 'application/json' -Body (@{sid=$sid; text="先拆个两步计划,然后用 ww_factor_analyze 分析 rank(-delta(close,20)),报 RankIC"} | ConvertTo-Json)
Start-Sleep 40
Get-Content "G:\guanlan-v2\var\console\sessions\$sid\events.jsonl"
```
Expected: jsonl 依次出现 user_msg / task_update(running) / plan_update / tool_call(ww_factor_analyze) / tool_result(artifact.kind=ic_report,summary 含真 RankIC) / agent_delta / task_update(done)。LLM 不拆计划属模型行为非 bug,工具链真跑通即可。

---

### Task 7: 前端壳 — html + console-data.jsx(事件客户端)

**Files:**
- Create: `ui/console/观澜 · 帷幄.html`
- Create: `ui/console/console-data.jsx`

- [ ] **Step 7.1: html(canonical 模板,对齐 ui/seats/观澜 · 落子.html:1-57)**

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>观澜 · 帷幄</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&family=Noto+Serif+SC:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600&display=swap" />
<link rel="stylesheet" href="../_shared/tokens.css" />
<style>
  html, body, #root { height: 100%; margin: 0; }
  body { font-family: var(--sans); color: var(--ink); background: var(--paper); overflow: hidden; }
  #root { min-width: 1280px; height: 100%; }
  .num, .mono { font-variant-numeric: tabular-nums; }
  *::-webkit-scrollbar { width: 8px; height: 8px; }
  *::-webkit-scrollbar-thumb { background: rgba(28,24,20,0.16); border-radius: 4px; }
  *::-webkit-scrollbar-track { background: transparent; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
  @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
  select { appearance: none; -webkit-appearance: none; }
</style>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" integrity="sha384-hD6/rw4ppMLGNu3tX5cjIb+uRZ7UkRJ6BPkLpg4hAu/6onKUg4lLsHAs9EBPT82L" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" integrity="sha384-u6aeetuaXnQ38mYT8rp6sbXaQe3NL9t+IBXmnYxwkUI2Hw4bsp2Wvmx4yRQF1uAm" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y" crossorigin="anonymous"></script>
</head>
<body>
<div id="root"></div>
<script src="../_shared/guanlan-bus.js?v=3"></script>
<script src="../_shared/guanlan-nav.js"></script>
<script>
  // 帷幄必须经 9999 同源服务(SSE/工具都在后端);file:// 直开诚实显示「需经服务打开」。
  window.GUANLAN_BACKEND =
    (location.protocol === 'http:' || location.protocol === 'https:') ? location.origin : null;
</script>
<script type="text/babel" data-presets="env,react" src="console-data.jsx?v=20260612a"></script>
<script type="text/babel" data-presets="env,react" src="console-rail.jsx?v=20260612a"></script>
<script type="text/babel" data-presets="env,react" src="console-thread.jsx?v=20260612a"></script>
<script type="text/babel" data-presets="env,react" src="console-bench.jsx?v=20260612a"></script>
<script type="text/babel" data-presets="env,react" src="console-app.jsx?v=20260612a"></script>
<script type="text/babel" data-presets="env,react">
  ReactDOM.createRoot(document.getElementById('root')).render(<WeiwoApp />);
</script>
</body>
</html>
```

- [ ] **Step 7.2: console-data.jsx(纯逻辑,挂 window.WW)**

```jsx
// console-data.jsx — 帷幄事件客户端:EventSource(原生,自动重连)+ reducer + API。
// 前端 = 事件流的纯渲染器:状态全部由 wwApply 从事件推导,刷新/重连 = snapshot 重放。
const WW_API = window.GUANLAN_BACKEND || '';

const WW_TOOL_CN = {
  ww_plan_update: '任务计划', ww_factor_analyze: '因子分析', ww_backtest: '回测',
  ww_screen_run: '选股', ww_seats_decide: '落子研判', ww_cards_query: '经验卡',
  ww_reports_query: '报告库', quote_lookup: '行情', realtime_quote: '实时行情',
  stock_brief: '个股速览', financials: '财务', news_query: '新闻',
  wisdom_search: '经验检索', quant_reports: '量化报告',
};

// 页面注册表:artifact.page → 嵌入目标(channel 与各页 take() 通道一致)
const WW_PAGES = {
  screen: { label: '选股', file: '../screen/观澜 · 选股.html', channel: 'screen' },
  factor: { label: '工作流', file: '../factor/观澜 · AI 工作流.html', channel: 'workflow' },
};

function wwInitState() {
  return { sid: null, meta: null, events: [], plan: [], busy: false,
           artifacts: [], activated: [], confirm: null, connected: false, benchClosed: false };
}

// 单事件折叠进状态(snapshot 重放与直播共用)
function wwApply(s, ev) {
  const n = { ...s, events: s.events.concat([ev]) };
  if (ev.type === 'plan_update') n.plan = ev.todos || [];
  if (ev.type === 'task_update') {
    if (ev.status === 'running') n.busy = true;
    if (ev.status === 'done' || ev.status === 'error') { n.busy = false; n.confirm = null; }
  }
  if (ev.type === 'confirm_request') n.confirm = ev;
  if (ev.type === 'tool_result' && ev.artifact && ev.artifact.page && WW_PAGES[ev.artifact.page]) {
    n.artifacts = s.artifacts.concat([{ ...ev.artifact, evId: ev.id, ts: ev.ts, tool: ev.tool }]);
    if (n.activated.indexOf(ev.artifact.page) < 0) n.activated = n.activated.concat([ev.artifact.page]);
    n.benchClosed = false;   // 新产物 → 工作台重新滑出
  }
  return n;
}

function wwConnect(sid, dispatch) {
  const es = new EventSource(WW_API + '/console/stream/' + sid);
  es.addEventListener('snapshot', (m) => {
    try {
      const d = JSON.parse(m.data);
      dispatch({ type: 'snapshot', meta: d.meta, events: d.events || [] });
    } catch (e) {}
  });
  es.addEventListener('ev', (m) => {
    try { dispatch({ type: 'ev', ev: JSON.parse(m.data) }); } catch (e) {}
  });
  es.onopen = () => dispatch({ type: 'conn', ok: true });
  es.onerror = () => dispatch({ type: 'conn', ok: false }); // EventSource 自动重连,重连即重收 snapshot
  return es;
}

async function wwSend(sid, text) {
  const r = await fetch(WW_API + '/console/send', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid, text }),
  });
  return r.json();
}

async function wwConfirm(turnId, choice) {
  const r = await fetch(WW_API + '/console/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ turn_id: turnId, choice }),
  });
  return r.json();
}

async function wwSessions() {
  const r = await fetch(WW_API + '/console/sessions');
  return (await r.json()).sessions || [];
}

async function wwNewSession(title) {
  const r = await fetch(WW_API + '/console/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title || '新对话' }),
  });
  return (await r.json()).meta;
}

window.WW = { API: WW_API, TOOL_CN: WW_TOOL_CN, PAGES: WW_PAGES,
              initState: wwInitState, apply: wwApply, connect: wwConnect,
              send: wwSend, confirm: wwConfirm, sessions: wwSessions, newSession: wwNewSession };
```

- [ ] **Step 7.3: 检查点** — 浏览器开 `http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html`,console 执行 `window.WW && WW.PAGES.screen.label` 返回 `'选股'`(WeiwoApp 未定义报错属预期,Task 8 解决)。

---

### Task 8: 前端三栏 — rail / thread / bench / app

**Files:**
- Create: `ui/console/console-rail.jsx`
- Create: `ui/console/console-thread.jsx`
- Create: `ui/console/console-bench.jsx`
- Create: `ui/console/console-app.jsx`

视觉基准 = 设计稿 `ui/_mockups/console-mockup.html`(masthead/任务卡/计划块/工具卡/输入坞/tabs 样式平移)。以下为完整骨架代码(一期求形准,书卷气打磨留 Task 12 后可选)。

- [ ] **Step 8.1: console-rail.jsx**

```jsx
// console-rail.jsx — 左栏:新对话 / 计划任务(agent 记忆镜像)/ 会话列表。
const WW_RAIL_H3 = { fontFamily: 'var(--serif)', fontSize: 11, fontWeight: 600, letterSpacing: 3, color: 'var(--ink-2)', margin: '16px 14px 6px', display: 'flex', alignItems: 'center', gap: 8 };

function WwRail({ state, sessions, onNew, onSwitch }) {
  const mark = { done: { c: 'var(--dai)', t: '✓' }, in_progress: { c: 'var(--zhu)', t: '▶' }, pending: { c: 'var(--jin)', t: '○' } };
  return (
    <div style={{ borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div onClick={onNew} style={{ margin: '12px 12px 4px', padding: '9px 0', textAlign: 'center', fontFamily: 'var(--serif)', fontSize: 13, letterSpacing: 4, border: '1.5px solid var(--ink)', cursor: 'pointer', background: 'var(--paper-2)' }}>新 对 话</div>
      <h3 style={WW_RAIL_H3}>任务计划</h3>
      {(state.plan.length === 0) && <div style={{ margin: '0 14px', fontSize: 11, color: 'var(--ink-3)' }}>暂无——下达复杂指令后 agent 会拆计划挂在这里</div>}
      {state.plan.map(t => (
        <div key={t.id} style={{ margin: '0 10px 6px', padding: '8px 10px', border: '1px solid var(--line-soft)', background: 'var(--paper-2)', fontSize: 12, opacity: t.status === 'done' ? 0.65 : 1 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, color: 'var(--ink-1)' }}>
            <span className="mono" style={{ color: (mark[t.status] || mark.pending).c, animation: t.status === 'in_progress' ? 'pulse 1.4s infinite' : 'none' }}>{(mark[t.status] || mark.pending).t}</span>
            <span>{t.text}</span>
          </div>
        </div>
      ))}
      <h3 style={WW_RAIL_H3}>会话</h3>
      <div style={{ overflowY: 'auto', minHeight: 0 }}>
        {sessions.map(m => (
          <div key={m.id} onClick={() => onSwitch(m.id)}
            style={{ margin: '0 10px 4px', padding: '7px 10px', fontSize: 12.5, color: 'var(--ink-1)', cursor: 'pointer', borderLeft: '2px solid ' + (m.id === state.sid ? 'var(--yin)' : 'transparent'), background: m.id === state.sid ? 'var(--paper-2)' : 'transparent', display: 'flex', justifyContent: 'space-between', gap: 6 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.title}</span>
            <span style={{ color: 'var(--ink-3)', fontSize: 10.5, whiteSpace: 'nowrap' }}>{(m.updated || '').slice(5, 16).replace('T', ' ')}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 'auto', padding: '10px 14px', borderTop: '1px solid var(--line-soft)', fontSize: 11, color: 'var(--ink-3)', display: 'flex', gap: 12 }}>
        <span>{state.connected ? '● 已连流' : '○ 重连中…'}</span>
        <span style={{ marginLeft: 'auto' }}>档案 {(window.GL && GL.stats && GL.stats().total) || 0} 件</span>
      </div>
    </div>
  );
}
window.WwRail = WwRail;
```

- [ ] **Step 8.2: console-thread.jsx**

```jsx
// console-thread.jsx — 中栏对话:事件流按序渲染;大 payload 永不内联(工具卡只显摘要)。
function WwToolCard({ ev, done }) {
  const cn = (window.WW.TOOL_CN[ev.tool] || ev.tool);
  return (
    <div style={{ border: '1px solid var(--line)', borderLeft: '3px solid var(--jin)', background: 'var(--paper-2)', margin: '6px 0', fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 12px' }}>
        <span>⚙</span>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-1)' }}>{cn}</span>
        <span style={{ color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{ev.args ? JSON.stringify(ev.args).slice(0, 90) : ''}</span>
        {!done && <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap', fontSize: 11, color: 'var(--zhu)', animation: 'pulse 1.4s infinite' }}>▶ 运行中</span>}
        {done && <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap', fontSize: 11, color: done.ok ? 'var(--dai)' : 'var(--yin)' }}>{done.ok ? '✓' : '✗ 失败'}</span>}
      </div>
      {done && done.summary && (
        <div style={{ borderTop: '1px dashed var(--line-soft)', padding: '7px 12px', color: 'var(--ink-2)', whiteSpace: 'pre-wrap' }}>{done.summary}</div>
      )}
    </div>
  );
}

function WwThread({ state, onSend, onConfirm }) {
  const [draft, setDraft] = React.useState('');
  const endRef = React.useRef(null);
  React.useEffect(() => { if (endRef.current) endRef.current.scrollIntoView({ block: 'end' }); }, [state.events.length]);

  // 事件 → 渲染单元:tool_call 与 tool_result 按 tool 名就近配对
  const items = [];
  const openCalls = {};
  state.events.forEach(ev => {
    if (ev.type === 'user_msg') items.push({ k: 'u', ev });
    else if (ev.type === 'agent_delta') items.push({ k: 'a', ev });
    else if (ev.type === 'tool_call') { const it = { k: 't', ev, done: null }; openCalls[ev.tool] = it; items.push(it); }
    else if (ev.type === 'tool_result') { const it = openCalls[ev.tool]; if (it && !it.done) { it.done = ev; delete openCalls[ev.tool]; } else items.push({ k: 't', ev: { ...ev, args: null }, done: ev }); }
    else if (ev.type === 'task_update' && ev.status === 'error') items.push({ k: 'err', ev });
  });

  const send = () => { const t = draft.trim(); if (!t || state.busy) return; setDraft(''); onSend(t); };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }} className="paper-bg">
      <div style={{ flex: 1, overflowY: 'auto', padding: '22px 0 8px' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', padding: '0 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {items.length === 0 && (
            <div style={{ textAlign: 'center', color: 'var(--ink-3)', fontSize: 13, marginTop: 80, fontFamily: 'var(--serif)', letterSpacing: 1 }}>
              对观澜下令——选股、回测、因子验证、研判,一句话即可。<br /><span style={{ fontSize: 11 }}>例:「验证动量因子 rank(-delta(close,20)),回测过了就去选股池混排 top20」</span>
            </div>
          )}
          {items.map((it, i) => {
            if (it.k === 'u') return <div key={i} style={{ alignSelf: 'flex-end', maxWidth: '78%', background: 'var(--paper-2)', border: '1px solid var(--line)', padding: '10px 14px', fontSize: 13.5, lineHeight: 1.7 }}>{it.ev.text}</div>;
            if (it.k === 'a') return (
              <div key={i} style={{ display: 'flex', gap: 12 }}>
                <span className="seal" style={{ width: 28, height: 28, flex: 'none', fontSize: 13, marginTop: 2 }}>觀</span>
                <div style={{ fontSize: 13.5, lineHeight: 1.85, color: 'var(--ink-1)', minWidth: 0, whiteSpace: 'pre-wrap' }}>{it.ev.text}</div>
              </div>);
            if (it.k === 't') return <div key={i} style={{ marginLeft: 40 }}><WwToolCard ev={it.ev} done={it.done} /></div>;
            if (it.k === 'err') return <div key={i} style={{ marginLeft: 40, color: 'var(--yin)', fontSize: 12 }}>✗ {it.ev.note}</div>;
            return null;
          })}
          {state.busy && <div style={{ marginLeft: 40, color: 'var(--ink-3)', fontSize: 12 }}><span style={{ display: 'inline-block', width: 8, height: 14, background: 'var(--ink)', animation: 'blink 1s infinite', verticalAlign: 'middle' }} /> 帷幄运筹中…</div>}
          {state.confirm && (
            <div style={{ marginLeft: 40, border: '1.5px solid var(--yin)', background: 'var(--paper-2)', padding: '10px 14px', fontSize: 12.5 }}>
              <div style={{ marginBottom: 8 }}>⚠ agent 请求执行 <b className="mono">{window.WW.TOOL_CN[state.confirm.tool] || state.confirm.tool}</b>(有落盘副作用):{JSON.stringify(state.confirm.args || {}).slice(0, 120)}</div>
              <span onClick={() => onConfirm('y')} style={{ cursor: 'pointer', background: 'var(--yin)', color: 'var(--paper)', padding: '4px 14px', marginRight: 8, fontFamily: 'var(--serif)' }}>准</span>
              <span onClick={() => onConfirm('n')} style={{ cursor: 'pointer', border: '1px solid var(--line)', padding: '4px 14px' }}>驳回</span>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div style={{ padding: '10px 24px 16px' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', border: '1.5px solid var(--ink)', background: 'var(--paper)' }}>
          <textarea value={draft} onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="对观澜下令——选股、回测、研判、经验沉淀,一句话即可。Shift+Enter 换行"
            style={{ width: '100%', border: 0, outline: 0, resize: 'none', background: 'transparent', color: 'var(--ink)', fontFamily: 'var(--sans)', fontSize: 13.5, lineHeight: 1.6, padding: '12px 14px 4px', height: 54, boxSizing: 'border-box' }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px 8px' }}>
            <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>{state.busy ? '执行中,完成后可继续下令' : 'Enter 发送'}</span>
            <div onClick={send} style={{ marginLeft: 'auto', width: 34, height: 34, background: state.busy ? 'var(--ink-3)' : 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 15, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: state.busy ? 'default' : 'pointer' }}>令</div>
          </div>
        </div>
      </div>
    </div>
  );
}
window.WwThread = WwThread;
```

- [ ] **Step 8.3: console-bench.jsx(iframe 宿主,handoff 驱动)**

```jsx
// console-bench.jsx — 右栏工作台:现有页同源 iframe 原样嵌入,零新渲染。
// 驱动协议:新 artifact → GL.handoff(channel, payload) → 重载该页 iframe(?embed=1&_t=ts)
// → 页面 mount 时 take(channel) 自取自渲染。tab 只列本会话激活过的页;⌖钉住阻止自动跟随。
function WwBench({ state, onClose }) {
  const PAGES = window.WW.PAGES;
  const [tab, setTab] = React.useState(null);
  const [pinned, setPinned] = React.useState(false);
  const [srcs, setSrcs] = React.useState({});         // page → iframe src(_t 时间戳强制重载)
  const lastRef = React.useRef(0);
  const wrapRef = React.useRef(null);
  const [scale, setScale] = React.useState(1);

  React.useEffect(() => {
    const arts = state.artifacts;
    if (!arts.length) return;
    const a = arts[arts.length - 1];
    if (a.evId === lastRef.current) return;
    lastRef.current = a.evId;
    const pg = PAGES[a.page];
    if (!pg) return;
    if (window.GL && a.channel) GL.handoff(a.channel, a.payload);
    setSrcs(s => ({ ...s, [a.page]: pg.file + '?embed=1&_t=' + Date.now() }));
    if (!pinned) setTab(a.page);
  }, [state.artifacts.length]);

  // 缩放:各页 min-width 1280 → scale = clamp(w/1280, 0.6, 1)
  React.useEffect(() => {
    const fit = () => { if (wrapRef.current) setScale(Math.max(0.6, Math.min(1, wrapRef.current.clientWidth / 1280))); };
    fit(); window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  if (!state.activated.length) return null;
  const cur = (tab && state.activated.indexOf(tab) >= 0) ? tab : state.activated[state.activated.length - 1];
  return (
    <div style={{ borderLeft: '1px solid var(--line)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--paper)' }}>
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--line)', padding: '0 8px' }}>
        {state.activated.map(p => (
          <span key={p} onClick={() => setTab(p)} style={{ padding: '10px 11px 8px', fontSize: 12, cursor: 'pointer', letterSpacing: 1, color: cur === p ? 'var(--ink)' : 'var(--ink-3)', borderBottom: '2px solid ' + (cur === p ? 'var(--yin)' : 'transparent'), marginBottom: -1, fontWeight: cur === p ? 500 : 400 }}>{PAGES[p].label}</span>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, paddingRight: 4, fontSize: 11, color: 'var(--ink-3)' }}>
          <span onClick={() => setPinned(p => !p)} style={{ cursor: 'pointer', color: pinned ? 'var(--yin)' : 'var(--ink-3)' }} title="钉住:agent 产出新产物时不自动切换">⌖ {pinned ? '已钉住' : '钉住'}</span>
          <span onClick={() => window.open(PAGES[cur].file, '_blank')} style={{ cursor: 'pointer' }} title="在原独立页全宽打开">↗</span>
          <span onClick={onClose} style={{ cursor: 'pointer' }} title="收起工作台(下个产物自动滑出)">✕</span>
        </div>
      </div>
      <div ref={wrapRef} style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {state.activated.map(p => (
          <iframe key={p} src={srcs[p] || (PAGES[p].file + '?embed=1')} title={PAGES[p].label}
            style={{ position: 'absolute', top: 0, left: 0, border: 0, background: 'var(--paper)',
                     width: (100 / scale) + '%', height: (100 / scale) + '%',
                     transform: 'scale(' + scale + ')', transformOrigin: '0 0',
                     visibility: cur === p ? 'visible' : 'hidden' }} />
        ))}
      </div>
    </div>
  );
}
window.WwBench = WwBench;
```

- [ ] **Step 8.4: console-app.jsx(主壳:布局态机 + masthead)**

```jsx
// console-app.jsx — 帷幄主壳。布局:无产物/✕收起 → [264px 1fr];有产物 → [264px 460px 1fr](工作台优先);
// ⇋ 对话优先 → [264px 1fr 560px]。新产物到来自动重新滑出(wwApply 置 benchClosed=false)。
function WeiwoApp() {
  const WW = window.WW;
  const [state, dispatch] = React.useReducer((s, a) => {
    if (a.type === 'snapshot') { let n = { ...WW.initState(), sid: s.sid, meta: a.meta, connected: true }; (a.events || []).forEach(ev => { n = WW.apply(n, ev); }); return n; }
    if (a.type === 'ev') return WW.apply(s, a.ev);
    if (a.type === 'conn') return { ...s, connected: a.ok };
    if (a.type === 'sid') return { ...WW.initState(), sid: a.sid };
    if (a.type === 'benchClosed') return { ...s, benchClosed: a.v };
    return s;
  }, WW.initState());
  const [sessions, setSessions] = React.useState([]);
  const [chatWide, setChatWide] = React.useState(false);
  const esRef = React.useRef(null);

  const refreshSessions = () => WW.sessions().then(setSessions);
  const attach = (sid) => {
    if (esRef.current) esRef.current.close();
    dispatch({ type: 'sid', sid });
    esRef.current = WW.connect(sid, dispatch);
    try { localStorage.setItem('guanlan:ww:sid', sid); } catch (e) {}
  };

  React.useEffect(() => {
    if (!WW.API) return;
    refreshSessions();
    const last = (() => { try { return localStorage.getItem('guanlan:ww:sid'); } catch (e) { return null; } })();
    if (last) { attach(last); }
    else { WW.newSession().then(m => attach(m.id)); }
    return () => { if (esRef.current) esRef.current.close(); };
  }, []);

  const onSend = async (text) => {
    const r = await WW.send(state.sid, text);
    if (!r.ok && r.reason && r.reason.indexOf('会话不存在') >= 0) {
      const m = await WW.newSession(text.slice(0, 18)); attach(m.id); await WW.send(m.id, text);
    }
    refreshSessions();
  };
  const onNew = async () => { const m = await WW.newSession(); attach(m.id); refreshSessions(); };

  if (!WW.API) return <div style={{ padding: 40, fontFamily: 'var(--serif)', color: 'var(--ink-2)' }}>帷幄需经 9999 服务打开(SSE 与工具都在后端):http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html</div>;

  const benchOpen = state.activated.length > 0 && !state.benchClosed;
  const cols = !benchOpen ? '264px 1fr' : (chatWide ? '264px 1fr 560px' : '264px 460px 1fr');

  return (
    <div style={{ display: 'grid', gridTemplateRows: '52px 1fr', height: '100vh', minWidth: 1280 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 18px', borderBottom: '2px solid var(--ink)', background: 'var(--paper)' }}>
        <span className="seal" style={{ fontSize: 13 }}>帷</span>
        <span style={{ fontFamily: 'var(--serif)', fontSize: 17, fontWeight: 600, letterSpacing: 2 }}>观澜 · 帷幄</span>
        <span style={{ fontSize: 11, color: 'var(--ink-3)', letterSpacing: 1 }}>一席对话 · 总揽全局</span>
        <span style={{ flex: 1 }} />
        {benchOpen && <span onClick={() => setChatWide(w => !w)} style={{ fontSize: 11, color: 'var(--ink-2)', cursor: 'pointer', border: '1px solid var(--line)', padding: '3px 9px', background: 'var(--paper-2)' }}>⇋ {chatWide ? '工作台优先' : '对话优先'}</span>}
        <span style={{ fontSize: 11, color: 'var(--ink-2)', border: '1px solid var(--line)', padding: '3px 9px', background: 'var(--paper-2)' }}>{state.busy ? '● 运筹中' : '○ 待命'}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: cols, minHeight: 0 }}>
        <WwRail state={state} sessions={sessions} onNew={onNew} onSwitch={attach} />
        <WwThread state={state} onSend={onSend} onConfirm={(c) => WW.confirm(state.confirm.turn_id, c)} />
        {benchOpen && <WwBench state={state} onClose={() => dispatch({ type: 'benchClosed', v: true })} />}
      </div>
    </div>
  );
}
window.WeiwoApp = WeiwoApp;
```

- [ ] **Step 8.5: 检查点(浏览器)** — 开帷幄页:两栏空态;发「用 ww_factor_analyze 分析 rank(-delta(close,20))」→ 工具卡 ▶→✓(摘要含真 RankIC)→ 右栏滑出「工作流」tab;重启 9999 → EventSource 自动重连,snapshot 恢复全部消息。

---

### Task 9: 嵌入卫生 — nav 守卫 + 帷幄入口

**Files:**
- Modify: `ui/_shared/guanlan-nav.js`

- [ ] **Step 9.1: IIFE 顶部加 embed 守卫(第 3 行 `(function () {` 之后)**

```js
  // 帷幄嵌入卫生:?embed=1 时本页被装进帷幄右栏 iframe → 不注入导航条与 body/#root 强样式
  if (new URLSearchParams(location.search).get('embed') === '1') return;
```

- [ ] **Step 9.2: MODULES 数组首位加帷幄(guanlan-nav.js:4-11)**

```js
  var MODULES = [
    { label: '帷幄', file: '../console/观澜 · 帷幄.html' },
    { label: '研究图谱', file: '../graph/观澜 · 研究图谱.html', home: true },
    { label: '对话 · 研报', file: '../chat/观澜 · 交互原型.html' },
    { label: '经验卡', file: '../cards/观澜 · 经验验证区.html' },
    { label: '因子 · 工作流', file: '../factor/观澜 · AI 工作流.html' },
    { label: '选股', file: '../screen/观澜 · 选股.html' },
    { label: '席位 · 落子', file: '../seats/观澜 · 落子.html' },
  ];
```

- [ ] **Step 9.3: 检查点** — 独立开选股页:导航条含「帷幄」;开 `选股.html?embed=1`:无导航条。nav 无 ?v= 参数,Ctrl+F5 强刷验证。

---

### Task 10: 选股页嵌入卫生 + agent 窗口全局隐藏 + take('screen') 收 cfg

**Files:**
- Modify: `ui/screen/screen-app.jsx`
- Modify: `ui/screen/观澜 · 选股.html`(bump ?v)

授权依据:spec §3.7 删除清单(用户拍板「全局隐藏 ?legacy=1 找回」)+ §3.4 handoff 驱动(take 收 cfg 是融合通道的最小扩展)。

- [ ] **Step 10.1: screen-app.jsx 顶部(`const ISOLATED = false;` 之后,screen-app.jsx:10)加旗**

```js
// 帷幄融合旗:EMBED=被帷幄嵌入(隐藏页头身份区);LEGACY=找回页内 agent 窗口(默认全局隐藏,spec §3.7)
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_LEGACY = new URLSearchParams(location.search).get('legacy') === '1';
```

- [ ] **Step 10.2: TopBar 品牌区包条件(screen-app.jsx:247-251 的 seal+标题+V1.0 三节点)**

```jsx
      {!WW_EMBED && (<React.Fragment>
        <div className="seal" style={{ width: 26, height: 26 }}>觀</div>
        <span className="serif" style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.04em' }}>觀瀾 · 选股</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px' }}>V1.0</span>
      </React.Fragment>)}
```
(v4 状态 chips、「据此落子」等功能件**保留不动**。)

- [ ] **Step 10.3: 「一句话调约束」LLM chip 包条件(screen-app.jsx:283-288 整个 div 原样移入)**

```jsx
        {WW_LEGACY && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(168,57,45,0.06)', border: '1px solid var(--zhu-soft)', borderRadius: 20, padding: '5px 8px 5px 11px' }}>
            {/* …原 283-288 行内容逐字保留… */}
          </div>
        )}
```

- [ ] **Step 10.4: 「LLM 选因子」框包条件** — `LLMFactorPicker` 定义在 screen-app.jsx:337;Grep `<LLMFactorPicker` 找到唯一调用点,包成 `{WW_LEGACY && <LLMFactorPicker ... />}`(props 原样)。

- [ ] **Step 10.5: take('screen') 扩展收 cfg(screen-app.jsx:81-96 effect 内,`if (h && (h.factor || h.name || h.id))` 之前插入)**

```jsx
    // 帷幄驱动:整套选股配置(factors/pool/blend/topN)直接落地
    if (h && h.cfg && typeof h.cfg === 'object') {
      const c0 = h.cfg;
      setCfg(c => ({ ...c,
        ...(Array.isArray(c0.factors) ? { factors: c0.factors.map(f => ({ id: String(f.id), w: Number(f.w || 1) })) } : {}),
        ...(c0.pool ? { pool: c0.pool } : {}), ...(c0.blend != null ? { blend: Number(c0.blend) } : {}),
        ...(c0.topN ? { topN: Number(c0.topN) } : {}) }));
      flash('帷幄令到', '已按帷幄参数选股(α=' + (c0.blend != null ? c0.blend : '·') + ' · ' + (c0.pool || '') + ')');
      return;   // cfg 路径与单因子路径互斥
    }
```
**执行注**:setCfg 后确认页面的重算触发机制——Grep `setCfg(` 既有消费链;若 cfg 变化不自动重算,在本块末尾补调用页内现有的提交/计算入口(与「据此落子」同源的 commit 流程),以页内真实机制为准。

- [ ] **Step 10.6: bump `?v=`** — `ui/screen/观澜 · 选股.html:43` `screen-app.jsx?v=20260611a` → `?v=20260612a`(用 Edit)。

- [ ] **Step 10.7: 检查点(浏览器,三态)** — ① 独立打开:页头在、LLM chip/选因子框**不在**;② `?legacy=1`:两个 agent 窗口回来;③ `?embed=1`:页头身份区也消失。控制台 `GL.handoff('screen',{cfg:{factors:[],pool:'csi300',blend:1,topN:10}})` 后刷新:自动按 cfg 出 top10。

---

### Task 11: 工作流页嵌入卫生 + AI 窗口全局隐藏

**Files:**
- Modify: `ui/factor/workflow.jsx`
- Modify: `ui/factor/观澜 · AI 工作流.html`(bump ?v=73)

- [ ] **Step 11.1: workflow.jsx 顶部加同款 `WW_EMBED/WW_LEGACY` 两行旗(同 Task 10.1,放文件顶部 const 区)**

- [ ] **Step 11.2: TopBar 的 AI pill 簇包条件(workflow.jsx:1205-1211 整个 div:「AI 生成 ✦」输入 +「AI 闭环 ✦」按钮,原样移入)**

```jsx
        {WW_LEGACY && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(168,57,45,0.06)', border: '1px solid var(--zhu-soft)', borderRadius: 20, padding: '5px 8px 5px 11px' }}>
            {/* …原 1205-1211 行内容逐字保留(✦图标 + aiQ 输入 + AI 生成 ✦ + AI 闭环 ✦)… */}
          </div>
        )}
```
(旁边 1199-1204 的非 LLM「一句话生成」pill 是确定性关键词模板,不属 agent 窗口,**保留**。)

- [ ] **Step 11.3: TopBar 品牌区包 `!WW_EMBED`**(TopBar 组件内 seal/「觀瀾」标题块,Grep `觀瀾` 定位后同 Task 10.2 包法)。

- [ ] **Step 11.4: bump** — `观澜 · AI 工作流.html:33` `workflow.jsx?v=72` → `?v=73`(用 Edit;该 tag 无 data-presets,保持原样)。

- [ ] **Step 11.5: 检查点** — 独立打开:AI 生成/AI 闭环不见,画布/运行/载入保存正常;`?legacy=1` 找回;`?embed=1` 页头身份区消失;`GL.handoff('workflow',{expr:'rank(roe)',name:'测试'})` 后刷新 → tplG 自动建图(P0④ 既有通道,零改动复用)。

---

### Task 12: 端到端验收 + 文档收口

**Files:**
- Create: `ui/console/README.md`
- Modify: `ui/screen/README.md`、`ui/factor/README.md`
- Modify: memory(新条目 + connectivity 索引)
- Modify: `docs/superpowers/specs/2026-06-11-weiwo-console-design.md`(状态行)

- [ ] **Step 12.1: 全量 pytest** — ≥99+15 绿。

- [ ] **Step 12.2: 重启 9999 后浏览器全链验收(Chrome MCP 真点击,逐步截图)**

剧本:开帷幄 → 空态两栏 → 输入「验证动量因子 rank(-delta(close,20)) 的截面IC,过了就回测,再去 csi300 选股池混排 top20」→ 验:
1. 左栏出现 agent 拆的计划(plan_update);
2. 工具卡依次:因子分析 ✓(真 RankIC)→ 回测 ✓(Sharpe/回撤)→ 选股 ✓;
3. 右栏滑出,「工作流」「选股」tab 激活,选股 iframe 内是**真选股页按 cfg 跑出的 top20**(无导航条/页头身份区/LLM 窗口);
4. F5 刷新:snapshot 恢复全部消息与计划;
5. 「✕」收起 → 纯对话;再发一令产物到来 → 自动滑出;
6. 重启 9999 → 流断 → EventSource 自动重连恢复。

- [ ] **Step 12.3: 验收口径(诚实)** — 若 LLM 不按期望顺序调工具,只要每个工具单独可被指令触发且事件/artifact/iframe 链路真,骨架验收通过;编排质量靠 system prompt 调优,不算阻塞。

- [ ] **Step 12.4: 文档** — `ui/console/README.md`(模块定位/事件协议/WW_PAGES 注册表/已知边界:condenser、memory.md、后台 runner、拖宽、落子/经验页接入均二期);`ui/screen/README.md`、`ui/factor/README.md` 加 2026-06-12 帷幄融合批条目(EMBED/LEGACY 旗、take cfg、?v 现值);memory 新条目「帷幄一期已落」(事件七型/CONSOLE_ALLOWED/自HTTP原因=工厂闭包/screen take cfg 扩展/测试不碰引擎模式)。

- [ ] **Step 12.5: spec 状态** — 头部状态行改「一期已实现(2026-06-12),二期待启动」。

---

## Self-Review 结论(已自查)

- **Spec 覆盖**:一期范围逐项有任务(jsonl+SSE=T1/T5、plan 工具=T2、核心工具=T3/T4、挂载=T6、三栏+按需滑出+⇋=T7/T8、iframe 宿主+handoff 驱动=T8.3、embed 卫生+agent 窗口隐藏=T9-T11、e2e=T12)。**显式移到二期**(spec §5 已注或本计划标注):condenser、memory.md、后台 runner/report.run、@引物料、/快捷令、落子/经验页接入、研报 md 抽屉(artifact 通道本期已就绪)、右栏拖宽(一期用 ⇋ 两档代偿)。
- **占位扫描**:Task 10.3/11.2 的「原样移入」= 既有代码块整体包裹(file:line 已给);Task 10.5 执行注是对未读消费链的诚实标注,执行者第一步 Grep 即消解;无 TBD/TODO。
- **类型一致**:`ToolResult.side_effect.artifact` ↔ api `_emit(..., artifact=...)` ↔ 前端 `ev.artifact` ↔ `WW_PAGES[artifact.page]` 字段链一致;`plan_update.todos` ↔ `state.plan` 一致;FakeAgent 鸭子型与 `BuddyAgent.run_turn(text, confirm_callback, allowed_tools)`(agent.py:256-258)一致;`_two_years_ago` 在 Task 2 定义、Task 3 使用一致。
