# autonomy 运行时 + 盘后自主复盘官(帷幄智能体化一期·单元二)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建 guanlan_v2/autonomy/(job 池+账本+运行时+子 agent 派工)并交付第一个 playbook「盘后自主复盘官」:五段(A/B 成绩单→落子复盘→数据巡检→综合晨报→批判)+三新职责(大盘判读日更/蒸馏草稿/macro 快照搭车),日报落盘+console 晨报卡+ww_review_report。

**Architecture:** 结构补智能——运行时确定性负重(单飞状态机照 research/api.py、jsonl 账本照 research/store.py、预算护栏照 watcher),LLM 只在有界处出现:段 agent=BuddyAgent fork(工具白名单+max_tool_iters+单元一 turn_token_budget,照 _run_review_bg 先例),汇总/批判/蒸馏草稿=review_officer deep 座席一次性 json 调用。全链只读+写报告。

**Tech Stack:** Python/FastAPI/threading daemon + BuddyAgent fork + 单元一思考档位(config/llm.yaml rerank/review_officer/review_section 座席已在)。

**Spec:** docs/superpowers/specs/2026-07-12-weiwo-autonomy-runtime-design.md(§4-5)

## Global Constraints

- **只读+写报告红线**:复盘官全链绝不写 picks/信号/blend/seats/记忆;段 agent 工具白名单全 read-only,confirm 型工具一律被 `_auto_decline` 拒;蒸馏草稿只进报告(入记忆仍走现有 ww_rerank_distill confirm 门)。
- **诚实显形**:段失败/超时/预算耗尽 → 该段标 `degraded`+原因进报告,绝不编造;批判不过 → 对应段标降级。
- **opt-in 默认关**:`GUANLAN_REVIEW_DAILY`、`CONSOLE_REVIEW_MODE` 默认不开;开关只认 var/secrets.env(Task 9 控制器)。
- **UI 只填充**:console 页加卡不动现有布局;改 jsx 必 bump `?v=`(用 Edit 工具)。
- **计数四处同步**(加 ww_review_report 后现值→新值):WW_TOOL_TABLE 57→58;CONSOLE_ALLOWED 82→83;tests/test_console_tools.py :613 `registered_ww==58`、:619-620 `console_n==83`/`explicit_ww_n==58`、:1086 `ww_ 前缀==58`、:1088 `len==83`、:1093 reachable 集合加 `/autonomy/report/latest`;tests/test_guanlan_mcp.py :13/:71/:100 三处 `61→62`;_SYSTEM_PROMPT 加血缘句(test_console_api.py:932 漂移守护)。
- 协程红线:async 端点内同步工作一律 `asyncio.to_thread`;daemon 线程内可 `asyncio.run`(watcher 先例);**绝不**协程内同步自 HTTP。
- 提交:逐文件 `git add`(绝不 -A);尾注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 引擎/后端改动生效须重启 9999——统一 Task 9(控制器),子任务不碰生产进程。

## 测绘事实(2026-07-12 recon,实施依据,不得推翻)

- 段 agent 先例:console/api.py:642 `_run_review_bg`——`BuddyAgent(system_prompt=…)`、`ra.max_tool_iters=8`、`run_turn(text, confirm_callback, allowed_tools=集合)`;REVIEW_ALLOWED@tools.py:35。
- impl 直调信封:`{ok, content, artifact, raw?}`;ww 工具经 `register_console_tools()`(tools.py:2766,幂等)进 TOOL_REGISTRY。
- 单飞状态机范式:research/api.py:20-95(_LOCK/_STATE/_progress 白名单键/_run_thread finally 必清/start_bg 锁内查)。
- jsonl 档案范式:research/store.py:15-96(_append 吞异常返 bool;read_runs start/end 合并推导 done/error/running/interrupted)。
- 调度钩子落点:rescore.py:362 `_run_thread` finally(rerank 落定唯一收尾);`_maybe_daily_rerank`@screen/api.py:204 双门先例。
- /screen/health 显形先例:api.py:1369-1412(rerank_scheduler 只有 env 开关)。
- 大盘判读:news.py:24 `news_sentiment(codes, *, limit=200, timeout=60)` 返 market_read/market_tilt/as_of;sentiment.py:151 `write_market(day, read, tilt, as_of=None, source='')`。
- **as_of 停 2026-06-13 根因=测试污染**:tests/test_console_tools.py:509-521 桩 `_run_news_sentiment` 后走真 `_sentiment_write_through`(tools.py:1388→1409-1411)写真 var/sentiment;conftest 无 `sentiment._ROOT` 隔离;生产链路健康(真机 /screen/news as_of 03:15 新鲜)。
- macro 搭车:pulse.py:137 `build_pulse(refresh: bool=False, …)`;refresh=True 现拉成功才 append snapshots.jsonl。
- rerank_ab 读取现状:seats/api.py:2073 `read_picks(limit=500)` 尾窗;read_picks@picks.py:27(limit 夹 1..500)。
- UI:ui/console/观澜 · 帷幄.html `<script type="text/babel" src="xxx.jsx?v=…">`;跨文件 window 全局;WwMd 渲染器 window.WwMd。
- 挂载:server.py create_app 内 `app.include_router(...)`,factorlib 带 try/except 先例(:196-199)。

---

### Task 1: sentiment store 测试隔离 + 泄漏双堵(TDD)

**Files:**
- Modify: `tests/conftest.py`(追加 autouse fixture)、`tests/test_console_tools.py`(:509 一带补桩)
- Test: `tests/test_sentiment_isolation.py`(新建)

**Interfaces:** Produces: 全套件对 `guanlan_v2.datafeed.sentiment._ROOT` 的 autouse tmp 隔离(生产 var/sentiment 从此不可能被测试写入)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sentiment_isolation.py
# -*- coding: utf-8 -*-
"""守护:测试进程内 sentiment._ROOT 必须已被 conftest autouse 隔离到 tmp,
且 write_market 真的写进隔离目录而非生产 var/sentiment(2026-07-12 as_of 冻结事故根修)。"""
from pathlib import Path

from guanlan_v2.datafeed import sentiment as sm

REPO_VAR = Path(__file__).resolve().parents[1] / "var" / "sentiment"


def test_root_is_isolated():
    assert Path(sm._ROOT).resolve() != REPO_VAR.resolve()


def test_write_market_lands_in_isolated_root():
    assert sm.write_market("2026-01-02", "偏多", None, "2026-01-02 09:31", "unit-test")
    files = list(Path(sm._ROOT).glob("market-*.jsonl"))
    assert files, "写入未落隔离目录"
    assert not (REPO_VAR / "market-202601.jsonl").exists()
```

- [ ] **Step 2: 跑 `python -m pytest tests/test_sentiment_isolation.py -q` → FAIL(_ROOT 仍指生产)**

- [ ] **Step 3: 实现**

tests/conftest.py 在 `_isolate_screen_archives` 之后追加(同款模式):

```python
@pytest.fixture(autouse=True)
def _isolate_sentiment_store(tmp_path, monkeypatch):
    """统一情绪 store 隔离:任何测试对 judgments/market 的写入落 tmp,绝不碰生产
    var/sentiment(2026-07-12 事故:test_console_tools 桩数据经 _sentiment_write_through
    写真档案,大盘判读 as_of 被冻在桩值 2026-06-13)。"""
    from guanlan_v2.datafeed import sentiment as sm
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
```

tests/test_console_tools.py `test_news_search_impl_both_scope`(:509 一带)在 monkeypatch `_run_news_sentiment` 的同处补一行显式意图(保险带,隔离已一刀切):

```python
    monkeypatch.setattr(ct, "_sentiment_write_through", lambda r: None)
```

- [ ] **Step 4: 跑 `python -m pytest tests/test_sentiment_isolation.py tests/test_console_tools.py -q` → 全绿**
- [ ] **Step 5: 提交**

```bash
git add tests/conftest.py tests/test_console_tools.py tests/test_sentiment_isolation.py
git commit -m "fix(tests): sentiment store autouse 隔离+news_search 桩补写穿断路——as_of 冻结污染源根堵"
```

(生产档案清污=Task 9 控制器亲手,备份后剔 `source=="news_search" and as_of=="2026-06-13 09:31"` 行。)

---

### Task 2: autonomy 底座——jobs 账本 + runtime 单飞状态机(TDD)

**Files:**
- Create: `guanlan_v2/autonomy/__init__.py`、`guanlan_v2/autonomy/jobs.py`、`guanlan_v2/autonomy/runtime.py`
- Test: `tests/test_autonomy_runtime.py`(新建)

**Interfaces:**
- Produces:
  - jobs: `JOBS_PATH`(var/jobs/jobs.jsonl)、`job_dir(job_id)->Path`(var/jobs/<id>/,mkdir)、`append_event(row:dict)->bool`(自动补 ts)、`read_jobs(limit=20, running_job_id=None)->List[dict]`(start/end 合并,status∈queued/running/done/failed/interrupted,新在前)、`new_job_id()->'aj_'+hex10`。
  - runtime: `Budget(max_llm=12)`(`.charge(n=1)->bool` 超限 False 并置 `.exhausted`;`.used`)、`JobCtx(job_id, dir, budget, progress, deadline_ts)`(`ctx.over_deadline()->bool`)、`start_job_bg(playbook:str)->dict`(单飞;未注册 playbook → {ok:False,reason:'unknown_playbook'})、`_autonomy_public_state()->dict`、模块级 `_AUTONOMY_LOCK/_AUTONOMY_STATE`(键:running,phase,label,job_id,playbook,started_at,ended_at,ok,error,lines)。
  - 常量:`JOB_MAX_LLM=12`、`JOB_DEADLINE_SEC=1800`、`SECTION_TIMEOUT_SEC=300`。

- [ ] **Step 1: 写失败测试**(全打桩零 LLM 零网络)

```python
# tests/test_autonomy_runtime.py
# -*- coding: utf-8 -*-
import json
import time

from guanlan_v2.autonomy import jobs as J, runtime as R


def _iso(path, monkeypatch, tmp_path):
    monkeypatch.setattr(J, "JOBS_PATH", tmp_path / "jobs.jsonl")
    monkeypatch.setattr(J, "JOBS_DIR", tmp_path / "jobs")


def test_append_and_read_jobs_status(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    J.append_event({"job_id": "aj_a", "kind": "start", "playbook": "review_officer"})
    J.append_event({"job_id": "aj_a", "kind": "end", "ok": True})
    J.append_event({"job_id": "aj_b", "kind": "start", "playbook": "review_officer"})
    rows = J.read_jobs(limit=10)
    by = {r["job_id"]: r for r in rows}
    assert by["aj_a"]["status"] == "done"
    assert by["aj_b"]["status"] == "interrupted"     # 无 end 且非 running=重启即中断诚实显形
    assert rows[0]["job_id"] == "aj_b"               # 新在前


def test_read_jobs_running_marker(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    J.append_event({"job_id": "aj_c", "kind": "start", "playbook": "review_officer"})
    rows = J.read_jobs(limit=10, running_job_id="aj_c")
    assert rows[0]["status"] == "running"


def test_budget_charge_and_exhaust():
    b = R.Budget(max_llm=2)
    assert b.charge() and b.charge()
    assert not b.charge()
    assert b.exhausted and b.used == 2


def test_ctx_deadline():
    ctx = R.JobCtx(job_id="aj_x", dir=None, budget=R.Budget(1),
                   progress=lambda **k: None, deadline_ts=time.time() - 1)
    assert ctx.over_deadline()


def test_start_job_bg_single_flight_and_unknown(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    assert R.start_job_bg("no_such_playbook")["reason"] == "unknown_playbook"
    ran = {}

    def slow_pb(ctx):
        ran["hit"] = True
        time.sleep(0.3)
        return {"ok": True}

    monkeypatch.setitem(R._PLAYBOOKS_FOR_TEST(), "slow", slow_pb)
    r1 = R.start_job_bg("slow")
    assert r1["ok"] and r1["job_id"].startswith("aj_")
    r2 = R.start_job_bg("slow")
    assert r2["ok"] is False and r2["reason"] == "already_running"
    for _ in range(60):
        if not R._AUTONOMY_STATE["running"]:
            break
        time.sleep(0.05)
    assert ran.get("hit") and R._AUTONOMY_STATE["ok"] is True
    rows = J.read_jobs(limit=5)
    assert rows[0]["status"] == "done"


def test_job_thread_records_failure(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)

    def boom(ctx):
        raise RuntimeError("x")

    monkeypatch.setitem(R._PLAYBOOKS_FOR_TEST(), "boom", boom)
    R.start_job_bg("boom")
    for _ in range(60):
        if not R._AUTONOMY_STATE["running"]:
            break
        time.sleep(0.05)
    assert R._AUTONOMY_STATE["ok"] is False and "RuntimeError" in (R._AUTONOMY_STATE["error"] or "")
    assert J.read_jobs(limit=5)[0]["status"] == "failed"
```

- [ ] **Step 2: 跑 `python -m pytest tests/test_autonomy_runtime.py -q` → FAIL(模块不存在)**

- [ ] **Step 3: 实现**

`guanlan_v2/autonomy/__init__.py`:

```python
from .api import build_autonomy_router  # noqa: F401  (Task 5 提供;本任务先建空 api 占位)
```

(本任务 api.py 先落最小占位 `def build_autonomy_router(): raise NotImplementedError`,Task 5 替换——避免 __init__ 导入炸。)

`guanlan_v2/autonomy/jobs.py`(照 research/store.py 范式):

```python
# -*- coding: utf-8 -*-
"""autonomy job 池账本:var/jobs/jobs.jsonl 事件流 + var/jobs/<job_id>/ 工作目录。
append 吞异常返 bool(落盘失败不阻断 job,由调用方显形);read 侧坏行跳过绝不抛。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

JOBS_DIR = Path(__file__).resolve().parents[2] / "var" / "jobs"
JOBS_PATH = JOBS_DIR / "jobs.jsonl"


def new_job_id() -> str:
    return "aj_" + uuid.uuid4().hex[:10]


def job_dir(job_id: str) -> Path:
    d = JOBS_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_event(row: Dict[str, Any]) -> bool:
    try:
        rec = dict(row)
        rec.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOBS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def read_jobs(limit: int = 20, running_job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """start/end 合并成每 job 一条,新在前。status: done/failed(有 end)、
    running(==running_job_id)、interrupted(无 end 且非 running=进程重启中断,诚实显形)。"""
    lim = max(1, min(int(limit), 100))
    if not JOBS_PATH.exists():
        return []
    jobs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for line in open(JOBS_PATH, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        jid = str(r.get("job_id") or "")
        if not jid:
            continue
        if jid not in jobs:
            jobs[jid] = {"job_id": jid}
            order.append(jid)
        j = jobs[jid]
        if r.get("kind") == "start":
            j.update(playbook=r.get("playbook"), started_ts=r.get("ts"))
        elif r.get("kind") == "end":
            j.update(ended_ts=r.get("ts"), ok=r.get("ok"), error=r.get("error"),
                     report=r.get("report"))
    out = []
    for jid in reversed(order):
        j = jobs[jid]
        if "ended_ts" in j:
            j["status"] = "done" if j.get("ok") else "failed"
        elif jid == running_job_id:
            j["status"] = "running"
        else:
            j["status"] = "interrupted"
        out.append(j)
    return out[:lim]
```

`guanlan_v2/autonomy/runtime.py`(照 research/api.py 单飞范式):

```python
# -*- coding: utf-8 -*-
"""autonomy 运行时:单飞状态机 + 预算护栏 + daemon 线程跑 playbook。
红线:playbook 只读+写报告;任何异常 finally 必清 running 并落 end 事件(诚实显形)。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from guanlan_v2.autonomy import jobs as J

JOB_MAX_LLM = 12          # 每 job LLM 动作(fork/单发)上限
JOB_DEADLINE_SEC = 1800   # 全 job 软墙钟:段间检查,超则跳过余段标 degraded
SECTION_TIMEOUT_SEC = 300  # 每段(fork/单发)硬超时

_AUTONOMY_LOCK = threading.Lock()
_AUTONOMY_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "job_id": None, "playbook": None,
    "started_at": None, "ended_at": None, "ok": None, "error": None, "lines": []}


class Budget:
    def __init__(self, max_llm: int = JOB_MAX_LLM):
        self.max_llm = max(1, int(max_llm))
        self.used = 0
        self.exhausted = False

    def charge(self, n: int = 1) -> bool:
        if self.used + n > self.max_llm:
            self.exhausted = True
            return False
        self.used += n
        return True


@dataclass
class JobCtx:
    job_id: str
    dir: Optional[Path]
    budget: Budget
    progress: Callable[..., None]
    deadline_ts: float
    extras: Dict[str, Any] = field(default_factory=dict)

    def over_deadline(self) -> bool:
        return time.time() >= self.deadline_ts


def _progress(**kw) -> None:
    with _AUTONOMY_LOCK:
        for k in ("phase", "label"):
            if k in kw:
                _AUTONOMY_STATE[k] = kw[k]
        if kw.get("label"):
            _AUTONOMY_STATE["lines"].append(str(kw["label"]))
            if len(_AUTONOMY_STATE["lines"]) > 40:
                _AUTONOMY_STATE["lines"][:] = _AUTONOMY_STATE["lines"][-40:]


def _autonomy_public_state() -> Dict[str, Any]:
    with _AUTONOMY_LOCK:
        st = dict(_AUTONOMY_STATE)
        st["lines"] = list(st["lines"])[-12:]
    if st.get("started_at"):
        st["elapsed_sec"] = int((st.get("ended_at") or time.time()) - st["started_at"])
    return st


def _playbooks() -> Dict[str, Callable[[JobCtx], Dict[str, Any]]]:
    from guanlan_v2.autonomy.playbooks import PLAYBOOKS
    return PLAYBOOKS


def _PLAYBOOKS_FOR_TEST() -> Dict[str, Any]:
    """测试注入口(monkeypatch.setitem);生产不调。"""
    return _playbooks()


def _run_job_thread(job_id: str, playbook: str) -> None:
    ok, err, report = False, None, None
    try:
        fn = _playbooks()[playbook]
        ctx = JobCtx(job_id=job_id, dir=J.job_dir(job_id), budget=Budget(),
                     progress=_progress, deadline_ts=time.time() + JOB_DEADLINE_SEC)
        out = fn(ctx) or {}
        ok = bool(out.get("ok"))
        err = out.get("error")
        report = out.get("report")
    except Exception as exc:  # noqa: BLE001
        ok, err = False, f"{type(exc).__name__}: {exc}"
    finally:
        J.append_event({"job_id": job_id, "kind": "end", "ok": ok, "error": err,
                        "report": report})
        with _AUTONOMY_LOCK:
            _AUTONOMY_STATE.update(running=False, ended_at=time.time(), ok=ok,
                                   error=err, phase="done" if ok else "error")


def start_job_bg(playbook: str) -> Dict[str, Any]:
    if playbook not in _playbooks():
        return {"ok": False, "reason": "unknown_playbook"}
    with _AUTONOMY_LOCK:
        if _AUTONOMY_STATE["running"]:
            return {"ok": False, "reason": "already_running",
                    "state": dict(_AUTONOMY_STATE, lines=[])}
        job_id = J.new_job_id()
        _AUTONOMY_STATE.update(running=True, phase="starting", label="", job_id=job_id,
                               playbook=playbook, started_at=time.time(), ended_at=None,
                               ok=None, error=None, lines=[])
    J.append_event({"job_id": job_id, "kind": "start", "playbook": playbook})
    threading.Thread(target=_run_job_thread, args=(job_id, playbook),
                     name="autonomy-job", daemon=True).start()
    return {"ok": True, "job_id": job_id}
```

`guanlan_v2/autonomy/playbooks.py` 本任务先落注册表壳(Task 4 填 review_officer):

```python
# -*- coding: utf-8 -*-
"""playbook 注册表:名字 -> callable(JobCtx)->{ok,error?,report?}。v1 只有 review_officer。"""
from typing import Any, Callable, Dict

PLAYBOOKS: Dict[str, Callable[..., Dict[str, Any]]] = {}
```

`guanlan_v2/autonomy/api.py` 占位:

```python
def build_autonomy_router():  # Task 5 实现
    raise NotImplementedError
```

- [ ] **Step 4: 跑 `python -m pytest tests/test_autonomy_runtime.py -q` → 6 passed**
- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/autonomy/__init__.py guanlan_v2/autonomy/jobs.py guanlan_v2/autonomy/runtime.py guanlan_v2/autonomy/playbooks.py guanlan_v2/autonomy/api.py tests/test_autonomy_runtime.py
git commit -m "feat(autonomy): job池账本+单飞运行时+预算护栏(照research/watcher范式,全打桩测试)"
```

---

### Task 3: 子 agent 派工 subagent.py(BuddyAgent fork 包装,TDD)

**Files:**
- Create: `guanlan_v2/autonomy/subagent.py`
- Test: `tests/test_autonomy_subagent.py`(新建)

**Interfaces:**
- Produces: `run_section_agent(*, name, system_prompt, brief_text, allowed_tools, out_path, seat="review_section", max_iters=6, token_budget=6000, timeout_sec=300) -> Dict`,返回 `{ok, name, text, tool_calls, error?}`;成功把最终文本写 out_path。
- Consumes: 单元一 `BuddyAgent(turn_token_budget=)`;思考档位座席(review_section/review_officer);console 工具注册。

- [ ] **Step 1: 写失败测试**(桩 BuddyAgent,零 LLM)

```python
# tests/test_autonomy_subagent.py
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
```

- [ ] **Step 2: 跑 → FAIL(模块不存在)**

- [ ] **Step 3: 实现 `guanlan_v2/autonomy/subagent.py`**

```python
# -*- coding: utf-8 -*-
"""段 agent 派工:BuddyAgent fork(照 console _run_review_bg 先例)。
隔离上下文=只喂简报;工具白名单;confirm 型工具一律拒(_auto_decline,只读红线);
产物写文件(段间文件交接);daemon 线程内 asyncio.run 独立事件循环(watcher 先例)。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Set

from guanlan_v2.screen.llm import LLM_CONFIG_PATH


async def _auto_decline(tool_name: str, args) -> bool:
    """复盘官全链只读:任何 confirm_required 工具直接拒。"""
    return False


def _make_agent(system_prompt: str, max_iters: int, token_budget: int, seat: str):
    from financial_analyst.buddy.agent import BuddyAgent
    from financial_analyst.llm.client import LLMClient
    from guanlan_v2.console import tools as ct
    ct.register_console_tools()
    ra = BuddyAgent(system_prompt=system_prompt, max_tool_iters=max_iters,
                    turn_token_budget=token_budget)
    # 座席换脑(fast=review_section / deep=review_officer,单元一思考档位)
    ra._client = LLMClient.for_agent(seat, config_path=LLM_CONFIG_PATH)
    return ra


def run_section_agent(*, name: str, system_prompt: str, brief_text: str,
                      allowed_tools: Set[str], out_path: Path,
                      seat: str = "review_section", max_iters: int = 6,
                      token_budget: int = 6000,
                      timeout_sec: float = 300) -> Dict[str, Any]:
    texts: list = []
    calls = {"n": 0}

    async def _drive():
        ra = _make_agent(system_prompt, max_iters, token_budget, seat)
        async for evt in ra.run_turn(brief_text, confirm_callback=_auto_decline,
                                     allowed_tools=set(allowed_tools)):
            if evt.kind == "tool_call":
                calls["n"] += 1
            elif evt.kind == "text" and evt.payload:
                texts.append(str(evt.payload))

    try:
        asyncio.run(asyncio.wait_for(_drive(), timeout=float(timeout_sec)))
    except asyncio.TimeoutError:
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": f"段超时(>{int(timeout_sec)}s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": f"{type(exc).__name__}: {exc}"}
    final = "\n\n".join(t for t in texts if t).strip()
    if not final:
        return {"ok": False, "name": name, "text": "", "tool_calls": calls["n"],
                "error": "段 agent 无文本产出"}
    try:
        Path(out_path).write_text(final, encoding="utf-8")
    except Exception:  # noqa: BLE001 — 落盘失败不吞文本,调用方仍拿到 text
        pass
    return {"ok": True, "name": name, "text": final, "tool_calls": calls["n"]}
```

注意:`asyncio.run(asyncio.wait_for(coro, ...))` 需把 wait_for 放进同一 loop——实现为
`asyncio.run(_outer())` 其中 `async def _outer(): await asyncio.wait_for(_drive(), timeout_sec)`。

- [ ] **Step 4: 跑 `python -m pytest tests/test_autonomy_subagent.py -q` → 4 passed**
- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/autonomy/subagent.py tests/test_autonomy_subagent.py
git commit -m "feat(autonomy): 段agent派工——BuddyAgent fork+白名单+confirm一律拒+超时/无产出诚实降级"
```

---

### Task 4: 盘后复盘官 playbook(五段+三职责+报告落盘,TDD)

**Files:**
- Create: `guanlan_v2/autonomy/review_officer.py`
- Modify: `guanlan_v2/autonomy/playbooks.py`(注册)
- Test: `tests/test_review_officer.py`(新建)

**Interfaces:**
- Produces: `run_review_officer(ctx: JobCtx) -> {ok, error?, report}`;`REPORTS_DIR = var/reports/daily`;报告 `YYYY-MM-DD.md` + `.json`(键:date,job_id,sections:{name:{ok,degraded?,error?,tool_calls}},duties:{market_refresh,macro_snapshot,distill_draft},critic:{ok,issues[]},budget_used,generated_at);`read_report(date: str = "") -> {ok, date, md, json}|{ok:False, reason}`(date 空=最新一份;Task 6 ww 工具与 Task 5 端点共同消费)。
- 在案简化(相对 spec §4"段产物已落盘的段跳过重跑"):v1 只做 interrupted 诚实标记,不做段级续跑——复盘官全程 ≤5 分钟,重跑成本低于续跑复杂度;账本结构(job_dir 段产物文件)已为将来续跑留位。
- Consumes: Task 2 JobCtx/Budget、Task 3 run_section_agent;console impls(`_rerank_perf_fetch`);`screen.news.news_sentiment`;`datafeed.sentiment.write_market`;`macro.pulse.build_pulse`;`screen.llm._call_llm_json(agent="review_officer")`。

**段/职责编排(顺序执行,每步前查 `ctx.over_deadline()` 与 `budget.charge()`,不过则该步标 degraded 跳过):**

| 步 | 类型 | 白名单/座席 |
|---|---|---|
| duty_market_refresh | 确定性+1 LLM:`asyncio.run(news_sentiment([]))` → ok 且 as_of 非空才 `write_market(today, read, tilt, as_of, source="review_officer")` | — |
| sec_ab(A/B 成绩单) | 确定性预取 `_rerank_perf_fetch(limit=10)` 摘要进简报 + 段 agent(fast) | {"ww_rerank_perf"} |
| duty_distill_draft | 仅当预取 raw 里存在两臂 ok 且 matured_n==n>0 的对:deep 一次性 `_call_llm_json(agent="review_officer")` 起草「行业·」教训草稿,**只进报告**标"待人审·未入记忆" | — |
| sec_seats(落子复盘) | 段 agent(fast) | {"ww_ledger_state", "ww_picks_perf"} |
| sec_data(数据+调度巡检) | 段 agent(fast) | {"ww_data_health", "ww_model_health"} |
| duty_macro_snapshot | 零 LLM:`build_pulse(refresh=True)`,失败只记 note | — |
| sec_report(综合晨报) | deep 一次性 `_call_llm_json(agent="review_officer")`,输入=三段产物文件全文+职责结果摘要,输出 JSON {"morning_report_md": str, "tomorrow_todos": [str]} | — |
| sec_critic(批判) | deep 一次性 `_call_llm_json`,输入=晨报+各段原文,输出 JSON {"pass": bool, "issues": [{"section","problem"}]};不过 → 对应段 degraded | — |
| 落盘 | 确定性拼 md+json 写 REPORTS_DIR;md 头注明各段状态徽章与"数字出处=各段工具调用" | — |

段 agent 系统提示词共用骨架(写死在模块顶,含红线):
"你是观澜盘后复盘官的「{段名}」段分析师。只用给定工具取数,只汇报工具返回的事实,每个数字给出处(工具名);查不到就写「该项无数据」,严禁编造;输出 ≤400 字中文小节。"

- [ ] **Step 1: 写失败测试**(run_section_agent/_call_llm_json/news_sentiment/build_pulse/_rerank_perf_fetch 全打桩;断言:①五段+三职责全走到且报告 md/json 落盘;②matured 对存在才有 distill_draft;③某段桩 ok:False → 报告该段 degraded 且 job 仍 ok;④预算 2 时后续步全 degraded(预算耗尽显形);⑤critic 不过 → issues 进 json 且对应段标 degraded;⑥write_market 只在 as_of 非空时被调)

```python
# tests/test_review_officer.py 核心桩样例(其余 case 同构)
import json

from guanlan_v2.autonomy import review_officer as RO, runtime as R


def _ctx(tmp_path, max_llm=12):
    return R.JobCtx(job_id="aj_t", dir=tmp_path, budget=R.Budget(max_llm),
                    progress=lambda **k: None, deadline_ts=9e18)


def _stub_all(monkeypatch, tmp_path, matured=False, sec_fail=None):
    monkeypatch.setattr(RO, "REPORTS_DIR", tmp_path / "reports")
    pairs = [{"run_id": "rs_1", "arms": {
        "data": {"ok": True, "n": 3, "matured_n": 3 if matured else 0},
        "rerank": {"ok": True, "n": 3, "matured_n": 3 if matured else 0}},
        "excess_diff": 0.01, "model": "deepseek/deepseek-reasoner"}]
    monkeypatch.setattr(RO, "_fetch_ab", lambda: {"ok": True, "pairs": pairs})
    monkeypatch.setattr(RO, "_refresh_market", lambda: {
        "ok": True, "as_of": "2026-07-12 15:00", "market_read": "偏多", "market_tilt": None})
    written = {}
    monkeypatch.setattr(RO, "_write_market", lambda *a: written.setdefault("args", a) or True)
    monkeypatch.setattr(RO, "_macro_snapshot", lambda: {"ok": True})

    def fake_section(**kw):
        nm = kw["name"]
        if sec_fail == nm:
            return {"ok": False, "name": nm, "text": "", "tool_calls": 0, "error": "boom"}
        return {"ok": True, "name": nm, "text": f"{nm} 小节", "tool_calls": 1}

    monkeypatch.setattr(RO, "run_section_agent", lambda **kw: fake_section(**kw))

    async def fake_llm(system, user, **kw):
        if "批判" in system:
            return {"ok": True, "data": {"pass": True, "issues": []}}
        if "教训" in system:
            return {"ok": True, "data": {"draft": "(行业·光芯片) 示例草稿"}}
        return {"ok": True, "data": {"morning_report_md": "# 晨报", "tomorrow_todos": ["t"]}}

    monkeypatch.setattr(RO, "_call_llm_json", fake_llm)
    return written


def test_full_run_writes_report(tmp_path, monkeypatch):
    _stub_all(monkeypatch, tmp_path)
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"] and out["report"]
    j = json.loads((RO.REPORTS_DIR / (out["report"][:-3] + ".json")).read_text(encoding="utf-8"))
    assert set(j["sections"]) == {"ab", "seats", "data"}
    assert j["duties"]["market_refresh"]["ok"] and j["duties"]["macro_snapshot"]["ok"]
    assert j["duties"]["distill_draft"] is None            # 无 matured 对不起草
```

(其余 5 个 case 按上表断言写全;实施者补齐,断言点不许删减。)

- [ ] **Step 2: 跑 → FAIL** → **Step 3: 实现**(模块内把外部依赖全收敛为模块级薄函数便于打桩:`_fetch_ab/_refresh_market/_write_market/_macro_snapshot/_call_llm_json/run_section_agent` 均以 `from x import y as _z` 或包装函数形式落模块顶;编排主体按上表顺序,每步 try/except 单步失败只降级该步,end 报告永远落盘——**报告落盘失败才算 job 失败**)→ **Step 4: 全绿** → **Step 5: 提交**

```bash
git add guanlan_v2/autonomy/review_officer.py guanlan_v2/autonomy/playbooks.py tests/test_review_officer.py
git commit -m "feat(autonomy): 盘后复盘官playbook——五段+三职责+批判降级+日报md/json落盘(全桩测试)"
```

---

### Task 5: 端点+挂载+调度钩子(TDD)

**Files:**
- Modify: `guanlan_v2/autonomy/api.py`(替换占位)、`guanlan_v2/server.py`(挂载)、`guanlan_v2/screen/rescore.py:371-376`(_run_thread finally 加钩)、`guanlan_v2/screen/api.py`(/screen/health 加 review_scheduler 显形)
- Test: `tests/test_autonomy_api.py`(新建)

**Interfaces:**
- Produces: `GET /autonomy/jobs?limit=`(`{ok, state, jobs}`)、`GET /autonomy/report/latest?date=`(`{ok, date, md, json}`,无报告 `{ok:False, reason:"no_report"}`)、`POST /autonomy/run`(body `{playbook:"review_officer"}`,透传 start_job_bg 结果)、`maybe_enqueue_daily_review(note:str)->bool`(runtime.py:门=env `GUANLAN_REVIEW_DAILY=="1"` 且 note=="daily-scheduler" 且当日未跑过[查 read_jobs 当日 review_officer done/running];过门 start_job_bg)。
- 钩子:rescore.py `_run_thread` finally 里 ok 分支后追加(try/except pass,绝不影响 rescore):

```python
                try:
                    from guanlan_v2.autonomy.runtime import maybe_enqueue_daily_review
                    maybe_enqueue_daily_review(note)
                except Exception:  # noqa: BLE001 — 复盘官排队失败绝不拖垮 rescore
                    pass
```

- /screen/health 响应加 `"review_scheduler": {"enabled": os.environ.get("GUANLAN_REVIEW_DAILY") == "1", "requires": "GUANLAN_RERANK_DAILY=1(随重排落定后排队)"}`。
- server.py 挂载(macro 先例,factorlib try/except 款):

```python
    try:
        from guanlan_v2.autonomy import build_autonomy_router
        app.include_router(build_autonomy_router())
    except Exception as exc:  # noqa: BLE001 — autonomy 注册失败不阻断启动
        print(f"[guanlan_v2] autonomy router skipped: {exc}", file=sys.stderr)
```

- api.py 端点全用 `await asyncio.to_thread(...)` 包同步读(协程红线);POST /autonomy/run 校验 playbook∈PLAYBOOKS。

- [ ] **Step 1: 写失败测试**(TestClient 上 create_app?太重——照 research 测试惯例直接对 router 建最小 FastAPI app;maybe_enqueue_daily_review 三门各一测:env 关不排/note 非 daily 不排/当日已跑不排/全过排队[桩 start_job_bg 记录];/screen/health review_scheduler 键存在)
- [ ] **Step 2 → 4: RED→实现→GREEN**(`python -m pytest tests/test_autonomy_api.py tests/test_rescore_api.py -q`)
- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/autonomy/api.py guanlan_v2/server.py guanlan_v2/screen/rescore.py guanlan_v2/screen/api.py tests/test_autonomy_api.py
git commit -m "feat(autonomy): 三端点+server挂载+rerank落定后排队复盘官(三门)+health显形"
```

---

### Task 6: ww_review_report 工具 + 计数四处同步(TDD)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(impl+表项)、`guanlan_v2/console/api.py:26`(_SYSTEM_PROMPT 血缘句)、`tests/test_console_tools.py`(计数 57→58/82→83、reachable 集合、新工具 2 测)、`tests/test_guanlan_mcp.py`(:13/:71/:100 61→62)

impl(照 data_health_impl 直调模式):

```python
def review_report_impl(date: str = "") -> Dict[str, Any]:
    """读复盘官最新(或指定日)晨报:全量 content 自带(_wrap 信封红线)。"""
    try:
        from guanlan_v2.autonomy.review_officer import read_report
        r = read_report(date=str(date or "").strip())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"晨报读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"暂无晨报({r.get('reason')})——复盘官尚未跑过或当日未出报",
                "artifact": None, "raw": r}
    md = str(r.get("md") or "")
    return {"ok": True, "content": md[:8000], "artifact": None, "raw": r}
```

(Task 4 的 review_officer.py 须已提供 `read_report(date="") -> {ok, date, md, json}|{ok:False,reason}`——实施 Task 4 时一并落,此处为消费方。)表项:`cost:'instant'`、`confirm:False`、`reachable:['/autonomy/report/latest']`;description 含"盘后复盘官晨报;纯展示绝不进信号"。_SYSTEM_PROMPT 加:"ww_review_report 读盘后复盘官晨报(autonomy 日跑产物)。"

- [ ] TDD 步骤同构(新 2 测:经真 _wrap 信封全量 content/无报告诚实降级);计数断言逐处改。
- [ ] 提交:

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(console): ww_review_report 晨报工具+计数四处同步 58/83/62"
```

---

### Task 7: console 晨报卡(UI 只填充)

**Files:**
- Create: `ui/console/console-report-card.jsx`
- Modify: `ui/console/观澜 · 帷幄.html`(加 script 标签,新文件带 `?v=20260712a`)、`ui/console/console-rail.jsx`(左栏挂卡,bump 该文件 ?v=)

卡组件(照 ResearchLoopCard 折叠卡范式,window 全局互见):

```jsx
// console-report-card.jsx — 复盘官晨报卡:GET /autonomy/report/latest + /autonomy/jobs
// 折叠默认收起;open 时拉取+60s 轮询;无报告诚实空态;WwMd 渲染 md;
// 蒸馏草稿段若在 → 「复制蒸馏指令」按钮(把 ww_rerank_distill key/text 预填文本
// navigator.clipboard 复制,人到输入框粘贴发送=人审 confirm 门不绕过)。
function WwReviewReportCard() {
  const [open, setOpen] = React.useState(false);
  const [rep, setRep] = React.useState(null);   // null=未拉, {ok:false}=无报告
  const [jobs, setJobs] = React.useState([]);
  React.useEffect(() => {
    if (!open) return;
    let dead = false;
    const pull = () => {
      fetch(window.WW.API + '/autonomy/report/latest').then(r => r.json())
        .then(d => { if (!dead) setRep(d); }).catch(() => { if (!dead) setRep({ ok: false, reason: '后端不可达' }); });
      fetch(window.WW.API + '/autonomy/jobs?limit=8').then(r => r.json())
        .then(d => { if (!dead) setJobs(d.jobs || []); }).catch(() => {});
    };
    pull();
    const t = setInterval(pull, 60000);
    return () => { dead = true; clearInterval(t); };
  }, [open]);
  /* 渲染:标题「盘后复盘官」+状态点;body= rep.ok ? WwMd(rep.md) : 空态文案;
     历史 jobs 列表 status 字形 done✓/failed✗/running⟳/interrupted⚠(照 ResearchLoopCard)。*/
  // …完整 JSX 按上述结构展开(实施者照 ResearchLoopCard 991-1027 的折叠/轮询/清理模式写)
}
window.WwReviewReportCard = WwReviewReportCard;
```

- [ ] 无单测(UI);验收=Task 9 浏览器真机。html 加载顺序:新 jsx 放 console-rail.jsx 之前。
- [ ] 提交:

```bash
git add "ui/console/console-report-card.jsx" "ui/console/观澜 · 帷幄.html" ui/console/console-rail.jsx
git commit -m "feat(console-ui): 复盘官晨报卡——latest报告+job历史+蒸馏指令复制(人审门不绕过)"
```

---

### Task 8: rerank_ab 档案摆脱 500 行尾窗(TDD)

**Files:**
- Modify: `guanlan_v2/screen/picks.py`(加 `read_picks_by_kind(kind, limit=200)` 全文件流式扫描)、`guanlan_v2/seats/api.py:2072-2073`(rerank_ab 分支改用它)
- Test: `tests/test_basket_perf.py` 加录音回归(600 行填充档案,rerank_ab 行在最老端仍被读到——旧尾窗会丢)

```python
# picks.py 新函数
def read_picks_by_kind(kind: str, limit: int = 200) -> List[Dict[str, Any]]:
    """按 kind 全文件流式过滤(新在前)。rerank_ab 证据留存专用:不吃 500 行尾窗,
    防日常 run 把 A/B 对挤出窗口(2026-07-12 单元二)。"""
    want = str(kind or "").strip()
    lim = max(1, min(int(limit), 1000))
    if not want or not PICKS_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in open(PICKS_PATH, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("kind") == want:
            out.append(r)
    return list(reversed(out))[:lim]
```

seats/api.py :2072-2073 改:

```python
                from guanlan_v2.screen.picks import read_picks_by_kind
                rows = read_picks_by_kind("rerank_ab", limit=400)
```

- [ ] TDD 同构;`python -m pytest tests/test_basket_perf.py tests/test_rescore_api.py -q` 全绿。
- [ ] 提交:

```bash
git add guanlan_v2/screen/picks.py guanlan_v2/seats/api.py tests/test_basket_perf.py
git commit -m "fix(rerank-ab): 档案读取改按kind全文件流式扫描——A/B证据不再被500行尾窗挤出"
```

---

### Task 9: 全量回归 + 清污 + 开关 + 真机 e2e(控制器亲手,不派发)

- [ ] 全量 `python -m pytest tests/ -q` 全绿。
- [ ] **清污 var/sentiment**(备份 .bak-20260712 后剔 `source=="news_search" and as_of=="2026-06-13 09:31"` 行,market-2026*.jsonl 与 judgments-2026*.jsonl 都查;逐条打印剔除计数)。
- [ ] secrets.env 加 `GUANLAN_REVIEW_DAILY=1` + `CONSOLE_REVIEW_MODE=monitor`(带注释);杀 9999 看门狗自愈。
- [ ] 真机手动 `POST /autonomy/run {"playbook":"review_officer"}` 全链:五段产物文件+晨报 md/json 落盘、GET /autonomy/report/latest 200、ww_review_report content 全量、console 晨报卡渲染(浏览器)、大盘判读 write_market 后 latest_market as_of=当日、macro snapshots.jsonl 增点;若 matured 对已出现(07-13 后)→ 报告含蒸馏草稿标"待人审"。
- [ ] 隔日验证调度自触发(rerank 日跑落定后 review job 排队);交易日顺带验收 watcher 首 tick + 复盘向导 JudgeCard(既有欠账)。
- [ ] 更新台账;终审整分支;合 main(推远端须再问)。
