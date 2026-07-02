# P2 自主研究回路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端 aiLoop 闭环(提案→求值→批判→改进)搬到后端:可后台运行的 research_loop 编排器,逐轮落档 jsonl,达标因子入 factorlib 为 draft(绝不自动采纳),每 run 一条 keyed 教训,最佳图存工作流库,帷幄两工具接入。

**Architecture:** 新模块 `guanlan_v2/research/`(store.py 双 jsonl 档案 + loop.py 确定性编排器 + api.py regen 式单飞状态机);LLM 两接缝(generate/critique)从 daemon 线程同步 HTTP 自调本进程端点(规避 engine LLM 客户端事件循环亲和坑);求值三道菜直调 workflow 模块级 sync 函数(与画布/帷幄同一批函数)。factorlib 加 draft 状态位 + 人审 promote 端点。

**Tech Stack:** FastAPI/pydantic、threading(daemon 线程+单飞锁)、append-only JSONL、pytest+TestClient(monkeypatch 打桩)。

**Spec:** `docs/superpowers/specs/2026-07-02-p2-research-loop-design.md`(已获批,含实现注记)。

## Global Constraints

- **测试命令**:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file> -v`(仓根 G:\guanlan-v2 执行;conftest 已钉 engine 路径)。全量回归基线 **760 passed**。
- **零新 env 开关、零定时器、零子进程**:回路只能被显式 POST 发起,合并即零行为变化。
- **诚实红线**:提案 LLM 失败 → run 诚实终止绝不降级模板;规则兜底 → `critique_source:"rule"` 落档 + diag 前缀「(规则兜底·非 LLM) 」;怪图诚实 skip;draft 绝不自动上架(转正=人的动作);恒 HTTP 200 `{ok:false,reason}` 诚实失败;落盘失败显形(`rounds_recorded/memory_written/workflow_saved` 进终态行)。
- **过门判据**:`rank_ic ≥ min_rank_ic(默认 0.02) 且 oos_verdict == "robust"`;求值一律 `oos_frac=0.3`(oos 不开 verdict 恒缺 → 门永不过)。
- **universe 合法枚举** = `workflow.api._UNIVERSE_OK` = `{csi300_active, csi_fast, csi300_2024h2, csi500, csi800, all, etf, sample30, 自动}`;回路默认 `csi300_active`。**注意 `csi300` 不合法(它是 benchmark id)**。
- **多因子达标不自动入库**:达标轮 dish=compose/≥2 表达式 → promoted 标 `skipped_multi`,不 save(库以单表达式为单位)。
- **JSONL 三件套**:模块级路径常量(monkeypatch 可测)/ append 吞异常返 bool / read 新在前+坏行跳过+limit 钳制;`encoding="utf-8"` + `ensure_ascii=False` + `default=str`;ts=`datetime.now().isoformat(timespec="seconds")`(本地无时区,全仓统一)。
- **锁纪律**:threading.Lock 非可重入,「只取一次锁绝不嵌套」(仓例 regen `_regen_public_state`);loop 线程 finally 必清 running。
- **daemon 线程红线**:同步自 HTTP 只许在 daemon 线程(协程内同步自 HTTP=堵 loop→看门狗杀 9999,仓级红线);**不要**在 daemon 线程 `asyncio.run` 调 `_llm_complete`(engine `_PROVIDER_CLIENTS` 缓存的 httpx.AsyncClient 绑首个事件循环,跨 run 复用炸 "Event loop is closed")。
- **工具四处同步铁律**(加 2 个 ww_ 工具):WW_TOOL_TABLE(40→**42**)/ console/api.py `_SYSTEM_PROMPT` / tests/test_console_tools.py(40→42、65→**67**、expected-endpoints 集 +4)/ tests/test_guanlan_mcp.py(44→**46** 三处)。glmcp/README.md 计数同步(注意它现在写 43,是 P1 后的陈账,一并修到 46)。
- **命名空间**:`/research/*` 已核实引擎与 guanlan 两侧全空闲,直接用。
- **改后端要重启 9999 才生效**;e2e 用独立端口 9998 起测试 server,不碰生产 9999。
- 代码注释密度/风格对齐仓内(中文注释、诚实口径注记)。

## File Structure

- Create: `guanlan_v2/research/__init__.py`(导出 build_research_router)
- Create: `guanlan_v2/research/store.py`(runs/rounds 双 jsonl:append_run/append_round/read_runs/read_rounds)
- Create: `guanlan_v2/research/loop.py`(编排器:桥函数/选菜/六键提取/过门/draft 入库/存图/教训/run_research_loop)
- Create: `guanlan_v2/research/api.py`(单飞状态机 + 4 端点)
- Modify: `guanlan_v2/factorlib/api.py`(SaveIn+status;闭包内核提取为模块级 `_save_factor`;新增 `_promote_factor`+`POST /factorlib/promote`)
- Modify: `guanlan_v2/factorlib/store.py`(list_factors 透传 status)
- Modify: `guanlan_v2/screen/catalog.py`(_build factorlib 段跳过 draft)
- Modify: `guanlan_v2/server.py`(挂载 research router)
- Modify: `guanlan_v2/console/tools.py`(+research_loop_impl/research_runs_impl/_research_run_line + 2 表条目 + critique 注记文案)
- Modify: `guanlan_v2/console/api.py`(_SYSTEM_PROMPT 能力行+纪律14)
- Modify: `guanlan_v2/glmcp/README.md`(计数 43→46)
- Modify: `tests/test_console_tools.py`(计数 42/67 + expected 集 +4 + 新工具测试)
- Modify: `tests/test_guanlan_mcp.py`(44→46 三处)
- Test: `tests/test_research_store.py`、`tests/test_factorlib_draft.py`、`tests/test_research_loop.py`、`tests/test_research_api.py`

---

### Task 1: research/store.py — runs/rounds 双 jsonl 档案

**Files:**
- Create: `guanlan_v2/research/__init__.py`
- Create: `guanlan_v2/research/store.py`
- Test: `tests/test_research_store.py`

**Interfaces:**
- Produces: `RUNS_PATH: Path`、`ROUNDS_PATH: Path`(模块级常量);`append_run(record: dict) -> bool`;`append_round(record: dict) -> bool`;`read_runs(limit: int = 20, running_run_id: str | None = None) -> list[dict]`(每 run 合并 start/end 行为一条,新在前,带推导 `status: "done"|"error"|"running"|"interrupted"`);`read_rounds(run_id: str | None = None, limit: int = 50) -> list[dict]`(新在前)。
- Consumes: 无(纯文件)。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_research_store.py`:

```python
"""研究回路档案纯函数单测(P2 §3)。全部 monkeypatch 路径常量指 tmp,零生产污染。照 test_screen_picks.py 形状。"""


def _rs(monkeypatch, tmp_path):
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    return rs


def test_runs_merge_and_status_derivation(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    assert rs.append_run({"run_id": "rr_a", "kind": "start", "goal": "反转", "ts": "t1"}) is True
    assert rs.append_run({"run_id": "rr_b", "kind": "start", "goal": "动量", "ts": "t2"}) is True
    assert rs.append_run({"run_id": "rr_a", "kind": "end", "ok": True, "n_rounds": 2, "ts": "t3"}) is True
    rows = rs.read_runs(limit=10)
    assert [r["run_id"] for r in rows] == ["rr_b", "rr_a"]          # 新在前(按 start 序)
    by = {r["run_id"]: r for r in rows}
    assert by["rr_a"]["status"] == "done" and by["rr_a"]["n_rounds"] == 2
    assert by["rr_a"]["goal"] == "反转"                              # start 字段保留,end 字段合并
    assert by["rr_b"]["status"] == "interrupted"                     # 无终态且非在跑 → 中断显形


def test_runs_status_error_and_running(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_run({"run_id": "rr_e", "kind": "start", "ts": "t1"})
    rs.append_run({"run_id": "rr_e", "kind": "end", "ok": False, "error": "LLM 不可用", "ts": "t2"})
    rs.append_run({"run_id": "rr_live", "kind": "start", "ts": "t3"})
    rows = rs.read_runs(limit=10, running_run_id="rr_live")
    by = {r["run_id"]: r for r in rows}
    assert by["rr_e"]["status"] == "error"
    assert by["rr_live"]["status"] == "running"


def test_rounds_filter_dirty_and_limit(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_round({"run_id": "rr_a", "k": 0, "diag": "初始"})
    with (tmp_path / "rounds.jsonl").open("a", encoding="utf-8") as f:
        f.write("{oops 不是JSON\n")
    rs.append_round({"run_id": "rr_a", "k": 1})
    rs.append_round({"run_id": "rr_b", "k": 0})
    rows = rs.read_rounds(run_id="rr_a", limit=10)
    assert [r["k"] for r in rows] == [1, 0]                          # 过滤+坏行跳过+新在前
    assert rs.read_rounds(limit=1)[0]["run_id"] == "rr_b"            # limit 钳制


def test_missing_files_return_empty(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    assert rs.read_runs() == [] and rs.read_rounds() == []


def test_append_failure_returns_false(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")                        # 父路径是文件 → mkdir 必炸
    monkeypatch.setattr(rs, "RUNS_PATH", blocker / "runs.jsonl")
    assert rs.append_run({"run_id": "rr_x", "kind": "start"}) is False


def test_chinese_not_escaped(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_round({"run_id": "rr_a", "diag": "样本外塌缩"})
    assert "样本外塌缩" in (tmp_path / "rounds.jsonl").read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_store.py -v`
Expected: FAIL/ERROR(`ModuleNotFoundError: No module named 'guanlan_v2.research'`)

- [ ] **Step 3: 实现**

创建 `guanlan_v2/research/__init__.py`(**Task 1 版只含 docstring**——Task 4 创建 api.py 后才补导出,否则本任务 import store 会因 `__init__` 导入不存在的 api 而炸):

```python
# -*- coding: utf-8 -*-
"""P2 自主研究回路:提案→求值→批判→改进 后台编排 + 逐轮落档 + draft 入库。"""
```

创建 `guanlan_v2/research/store.py`:

```python
# -*- coding: utf-8 -*-
"""研究回路档案:runs(run 头+终态行)+ rounds(每轮一行)双 append-only JSONL。

照 screen/picks.py 三件套(P0 先例):模块级路径常量便于测试 monkeypatch;
append 吞异常返 bool(绝不阻断回路,调用方以 rounds_recorded 显形);
read 新在前/坏行跳过/limit 钳制。run 状态推导在读取时做:有终态行→done/error;
无终态行且非当前在跑→interrupted(9999 重启即中断,诚实显形,无需启动扫描)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

RUNS_PATH = Path(__file__).resolve().parents[2] / "var" / "research_runs.jsonl"
ROUNDS_PATH = Path(__file__).resolve().parents[2] / "var" / "research_rounds.jsonl"


def _append(path: Path, record: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001 — 落盘失败不阻断回路,调用方显形
        return False


def append_run(record: Dict[str, Any]) -> bool:
    """append 一行 run 事件(kind=start|end)。"""
    return _append(RUNS_PATH, record)


def append_round(record: Dict[str, Any]) -> bool:
    """append 一行轮次记录。"""
    return _append(ROUNDS_PATH, record)


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        if not path.exists():
            return out
        for ln in path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001 — 坏行跳过
                continue
    except Exception:  # noqa: BLE001 — 读失败=已收集的(或空),诚实降级
        return out
    return out


def read_runs(limit: int = 20, running_run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """合并 start/end 行 → 每 run 一条(新在前,按 start 出现序)。"""
    cap = max(1, min(int(limit or 20), 100))
    runs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in _read_lines(RUNS_PATH):
        rid = str(r.get("run_id") or "")
        if not rid:
            continue
        if rid not in runs:
            runs[rid] = {}
            order.append(rid)
        runs[rid].update({k: v for k, v in r.items() if k != "kind"})
        if r.get("kind") == "end":
            runs[rid]["_ended"] = True
    out: List[Dict[str, Any]] = []
    for rid in reversed(order):
        row = runs[rid]
        if row.pop("_ended", False):
            row["status"] = "done" if row.get("ok") else "error"
        elif rid == running_run_id:
            row["status"] = "running"
        else:
            row["status"] = "interrupted"
        out.append(row)
        if len(out) >= cap:
            break
    return out


def read_rounds(run_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """读轮次行(新在前;可按 run_id 过滤)。"""
    cap = max(1, min(int(limit or 50), 200))
    out: List[Dict[str, Any]] = []
    for r in reversed(_read_lines(ROUNDS_PATH)):
        if run_id and str(r.get("run_id") or "") != run_id:
            continue
        out.append(r)
        if len(out) >= cap:
            break
    return out
```

- [ ] **Step 4: 跑测确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_store.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/research/__init__.py guanlan_v2/research/store.py tests/test_research_store.py
git commit -m "feat(research): P2 T1 轮次档案 store(runs/rounds 双 jsonl+状态推导)"
```

---

### Task 2: factorlib draft 门 + 人审 promote 端点 + 选股目录过滤

**Files:**
- Modify: `guanlan_v2/factorlib/api.py`(SaveIn 加 status;闭包内核提取为模块级 `_save_factor`;新增 `FactorPromoteIn`/`_promote_factor`/`POST /factorlib/promote`)
- Modify: `guanlan_v2/factorlib/store.py:163-209`(list_factors 透传 status)
- Modify: `guanlan_v2/screen/catalog.py`(_build factorlib 段跳过 draft)
- Test: `tests/test_factorlib_draft.py`

**Interfaces:**
- Produces: `SaveIn` 新字段 `status: str = ""`(合法 `""|"draft"`);模块级 `_save_factor(body: SaveIn, store: LibraryFactorStore) -> dict`(原闭包逐字搬出,JSONResponse 改 dict;Task 3 回路直调);模块级 `_promote_factor(name: str, store: LibraryFactorStore) -> dict`;`POST /factorlib/promote {name}` → `{ok,name,file}` | `{ok:false,reason:"not_found: <name>"}`;`/factorlib/list` 行含 `status`(有才带);draft 因子不进 `FACTOR_DEFS`。
- Consumes: 现有 `LibraryFactorStore`(base_dir/mined_dir 可注入)、`_safe_filename`、`qlib_to_zoo`、引擎 `validate_expr/compile_factor/registry`。

**重构说明(必须逐字保真):`_save_factor` 的函数体 = 现闭包 `factorlib_save`(api.py:119-220)的 8 步流程逐字搬出**,仅四类改动:①每个 `return JSONResponse({...})` 改 `return {...}`;②函数签名改 `def _save_factor(body: SaveIn, store: LibraryFactorStore) -> dict:`(模块级,缩进减 4);③步骤 2 之后插入 status 校验(见下);④步骤 6 的 rec 构造后、`saved_at` 之前插入 status 写入。原闭包改为薄壳:

```python
    @router.post("/save")
    def factorlib_save(body: SaveIn):
        """把一条好因子(表达式)存入因子库 mined/ 并运行时注册进引擎 zoo。

        内核在模块级 _save_factor(P2 研究回路直调复用);本闭包只包 JSONResponse。
        """
        return JSONResponse(_save_factor(body, store))
```

- [ ] **Step 1: 写失败测试**

创建 `tests/test_factorlib_draft.py`:

```python
"""factorlib draft 门(P2 §4):status 落盘/list 显形/目录过滤/人审 promote。tmp store 零生产污染。"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.factorlib.api import SaveIn, _promote_factor, _save_factor, build_factorlib_router
from guanlan_v2.factorlib.store import LibraryFactorStore


def _store(tmp_path) -> LibraryFactorStore:
    (tmp_path / "base").mkdir()
    (tmp_path / "mined").mkdir()
    return LibraryFactorStore(base_dir=tmp_path / "base", mined_dir=tmp_path / "mined")


def _client(store) -> TestClient:
    app = FastAPI()
    app.include_router(build_factorlib_router(store=store))
    return TestClient(app)


def test_save_with_draft_status_persisted(tmp_path):
    st = _store(tmp_path)
    r = _save_factor(SaveIn(name="lib_rl_t1", expr="rank(-delta(close,5))", status="draft"), st)
    assert r["ok"] is True
    data = json.loads((st.mined_dir / "lib_rl_t1.json").read_text(encoding="utf-8"))
    assert data[0]["status"] == "draft"
    rows = {f["name"]: f for f in st.list_factors(validate=False)}
    assert rows["lib_rl_t1"]["status"] == "draft"                    # list 显形


def test_save_without_status_unchanged(tmp_path):
    st = _store(tmp_path)
    r = _save_factor(SaveIn(name="lib_rl_t2", expr="rank(-delta(close,5))"), st)
    assert r["ok"] is True
    data = json.loads((st.mined_dir / "lib_rl_t2.json").read_text(encoding="utf-8"))
    assert "status" not in data[0]                                   # 旧行为零变化
    assert "status" not in {f["name"]: f for f in st.list_factors(validate=False)}["lib_rl_t2"]


def test_save_invalid_status_rejected(tmp_path):
    r = _save_factor(SaveIn(name="lib_x", expr="rank(close)", status="published"), _store(tmp_path))
    assert r["ok"] is False and "status" in r["reason"]


def test_promote_strips_status_and_idempotent(tmp_path):
    st = _store(tmp_path)
    _save_factor(SaveIn(name="lib_rl_t3", expr="rank(-delta(close,5))", status="draft"), st)
    r = _promote_factor("lib_rl_t3", st)
    assert r["ok"] is True and r["name"] == "lib_rl_t3"
    data = json.loads((st.mined_dir / "lib_rl_t3.json").read_text(encoding="utf-8"))
    assert "status" not in data[0]
    assert _promote_factor("lib_rl_t3", st)["ok"] is True            # 幂等:已转正再转正仍 ok
    assert _promote_factor("lib_nope", st)["ok"] is False            # 不存在 → not_found
    assert "not_found" in _promote_factor("lib_nope", st)["reason"]


def test_promote_endpoint(tmp_path):
    st = _store(tmp_path)
    _save_factor(SaveIn(name="lib_rl_t4", expr="rank(-delta(close,5))", status="draft"), st)
    c = _client(st)
    j = c.post("/factorlib/promote", json={"name": "lib_rl_t4"}).json()
    assert j["ok"] is True
    j2 = c.post("/factorlib/promote", json={"name": ""}).json()
    assert j2["ok"] is False


def test_catalog_excludes_draft(monkeypatch, tmp_path):
    import guanlan_v2.factorlib.store as fstore
    import guanlan_v2.screen.catalog as cat
    (tmp_path / "base").mkdir()
    (tmp_path / "mined").mkdir()
    monkeypatch.setattr(fstore, "_BASE_DIR", tmp_path / "base")
    monkeypatch.setattr(fstore, "_MINED_DIR", tmp_path / "mined")
    st = LibraryFactorStore()                                        # 读 monkeypatch 后的默认目录
    _save_factor(SaveIn(name="lib_draft_x", expr="rank(-delta(close,5))", status="draft"), st)
    _save_factor(SaveIn(name="lib_ok_y", expr="rank(-delta(close,9))"), st)
    defs = cat._build()
    assert "lib_ok_y" in defs                                        # 正式因子照常上目录
    assert "lib_draft_x" not in defs                                 # draft 不上选股货架(红线)
```

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factorlib_draft.py -v`
Expected: FAIL(`ImportError: cannot import name '_save_factor'`)

- [ ] **Step 3: 实现 factorlib/api.py**

3a. `SaveIn`(api.py:49-56)追加字段(放 meta 之前):

```python
class SaveIn(BaseModel):
    name: str
    expr: str
    family: str = "library_mined"   # 默认带 library 前缀 → 进 /factorlib/registered
    description: str = ""
    source: str = ""
    is_qlib: bool = False
    status: str = ""                # P2:空=正式;"draft"=研究回路产物待人审(不上选股货架)
    meta: dict = Field(default_factory=dict)   # 展示用快照(_label/universe/ic…);store 忽略未知键
```

3b. 在 `build_factorlib_router` 定义之前加模块级常量与 `_save_factor`(内核逐字搬自闭包 api.py:131-220,含全部中文注释,只做「JSONResponse({...}) → {...}」替换与两处 status 插入;搬完后原闭包体只剩薄壳委托):

```python
_VALID_SAVE_STATUS = {"", "draft"}


def _save_factor(body: SaveIn, store: LibraryFactorStore) -> dict:
    """/factorlib/save 内核(模块级;P2 研究回路直调复用)。返回 dict,端点包 JSONResponse。

    流程与诚实失败契约同原闭包(见 /save docstring);新增 status 校验与落盘
    (空=正式,draft=待人审——draft 仍注册进 zoo 可按名复验,只是不上选股货架)。
    """
    # 1) 表达式非空
    raw = (body.expr or "").strip()
    if not raw:
        return {"ok": False, "reason": "空表达式"}
    # 2) 因子名非空
    nm = (body.name or "").strip()
    if not nm:
        return {"ok": False, "reason": "因子名不能为空"}
    # 2.5) P2:status 校验(空=正式;draft=待人审)
    status = (body.status or "").strip()
    if status not in _VALID_SAVE_STATUS:
        return {"ok": False, "reason": f"status 非法: {status}(允许空或 draft)"}
    # …… 3)译写 / 4)校验 / 5)重名检查 / 6)落盘 / 7)运行时注册 / 8)成功返回 ——
    # 从现文件 api.py:131-220 逐字拷贝(含全部中文注释),仅:
    #   a. 每个 return JSONResponse({...}) → return {...}
    #   b. 步骤 6 的 rec 构造(rec = {...} 之后、rec["saved_at"] 之前)插入:
    #        if status:
    #            rec["status"] = status   # P2:draft 落盘(promote 转正时摘除)
```

> 实现者注意:上方省略号=**从现文件逐字拷贝**,不是重写。拷贝范围含 qlib 译写 try/except、validate_expr+compile_factor、重名双重拒绝、_safe_filename+穿越双保险、registered 注册段与最终 resp 组装。

3c. `FactorPromoteIn` + `_promote_factor`(模块级,放 `_save_factor` 之后):

```python
class FactorPromoteIn(BaseModel):
    """``POST /factorlib/promote`` 入参:draft 因子人审转正(摘 status)。"""

    name: str = ""


def _promote_factor(name: str, store: LibraryFactorStore) -> dict:
    """人审转正:在 mined/ 各 JSON 里找 name 条目,摘掉 status(draft→正式)。幂等;
    找不到 → not_found 诚实失败。转正后下次 /screen/factors 入口热刷新即上货架。"""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "reason": "因子名不能为空"}
    try:
        for fp in sorted(store.mined_dir.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — 坏文件跳过(与 store._read_json_dir 同口径)
                continue
            entries = data if isinstance(data, list) else [data]
            hit = False
            for e in entries:
                if isinstance(e, dict) and e.get("name") == nm:
                    e.pop("status", None)
                    hit = True
            if hit:
                fp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
                return {"ok": True, "name": nm, "file": fp.name}
        return {"ok": False, "reason": f"not_found: {nm}"}
    except Exception as exc:  # noqa: BLE001 — 诚实失败 HTTP 200,不抛 500
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
```

3d. router 内(紧跟 `/save` 闭包后)加端点:

```python
    @router.post("/promote")
    def factorlib_promote(body: FactorPromoteIn):
        """draft 因子人审转正(P2):摘 status → 下次选股目录刷新即上货架。"""
        return JSONResponse(_promote_factor(body.name, store))
```

- [ ] **Step 4: 实现 store.py list_factors 透传 status**

在 `list_factors` 的 row 构造(`"description": entry.get("description", "")` 行后)插入:

```python
            if entry.get("status"):
                row["status"] = str(entry.get("status"))    # P2:draft 显形(空/缺省不带键)
```

- [ ] **Step 5: 实现 catalog.py 过滤 draft**

在 `_build()` factorlib 并入段、`if not fid or fid in out: continue` 之后插入:

```python
            if str(entry.get("status") or "").strip() == "draft":
                continue   # P2:研究回路 draft 因子不上选股货架(人审 /factorlib/promote 转正后才可见)
```

- [ ] **Step 6: 跑测确认通过 + 回归相邻**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_factorlib_draft.py -v`
Expected: 6 passed
Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py tests/test_console_tools.py -q`
Expected: 全绿(save 旧行为零变化)

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/factorlib/api.py guanlan_v2/factorlib/store.py guanlan_v2/screen/catalog.py tests/test_factorlib_draft.py
git commit -m "feat(factorlib): P2 T2 draft 状态位+人审 promote 端点+选股目录过滤(save 内核提取为模块级)"
```

---

### Task 3: research/loop.py — 编排器核心

**Files:**
- Create: `guanlan_v2/research/loop.py`
- Test: `tests/test_research_loop.py`

**Interfaces:**
- Consumes: Task 1 `store.append_run/append_round`;Task 2 `_save_factor(SaveIn(...), LibraryFactorStore())`;workflow 模块级 `FactorReport2In/_factor_report2、FactorComposeIn/_factor_compose、BacktestVectorIn/_backtest_vector`(sync,返回 JSONResponse);`POST /workflow/generate|critique`(HTTP 自调);`WorkflowStore().save(name, graph)`;`console.tools.memory_write_impl(text, scope, key)`。
- Produces: `new_run_id() -> str`("rr_"+uuid4.hex[:10]);`run_research_loop(run_id, goal, max_rounds, min_rank_ic, universe, freq, start, end, progress) -> dict`(终态行,已落档;`progress(**kw)` 回调由 api.py 提供);内部可 monkeypatch 的独立小函数:`_call_generate/_call_critique/_eval_report2/_eval_compose/_eval_backtest/_pick_dish/_metrics_of/_gate/_save_draft/_save_graph/_write_lesson`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_research_loop.py`:

```python
"""研究回路编排器单测(P2 §2):纯函数 + 假 LLM/求值桥全链干跑。零网络零引擎数据。"""
import guanlan_v2.research.loop as rl


# ── 纯函数 ───────────────────────────────────────────────────────────────

def test_pick_dish_shapes():
    g1 = {"nodes": [{"type": "formula", "params": {"expr": "rank(-delta(close,5))"}},
                    {"type": "feature"}, {"type": "analysis"}], "edges": []}
    assert rl._pick_dish(g1) == ("report2", ["rank(-delta(close,5))"])
    g2 = {"nodes": [{"type": "formula", "params": {"expr": "a"}},
                    {"type": "formula", "params": {"expr": "b"}}], "edges": []}
    assert rl._pick_dish(g2) == ("compose", ["a", "b"])
    g3 = {"nodes": [{"type": "formula", "params": {"expr": "a"}}, {"type": "backtest"}], "edges": []}
    assert rl._pick_dish(g3) == ("backtest", ["a"])
    assert rl._pick_dish({"nodes": [{"type": "source"}], "edges": []}) == (None, [])
    g5 = {"nodes": [{"type": "factorlib", "params": {"name": "lib_x"}}], "edges": []}
    assert rl._pick_dish(g5) == ("report2", ["lib_x"])


def test_metrics_of_report2_and_compose():
    rep = {"status": "ok", "headline_ic": {"rank_ic": 0.031}, "ic": {"rank_ic_mean": 0.02},
           "portfolio": {"sharpe": 0.8, "ann_return": 0.12},
           "oos": {"verdict": "robust"}, "n_dates": 30, "composite": True}   # report2 的 composite 是 bool
    m = rl._metrics_of(rep, "expr1")
    assert m == {"rank_ic": 0.031, "sharpe": 0.8, "ann_return": 0.12,
                 "oos_verdict": "robust", "n_dates": 30, "factor": "expr1"}
    comp = {"ok": True, "composite": {"headline_ic": {"rank_ic": 0.04},
                                      "portfolio": {"sharpe": 1.1, "ann_return": 0.2},
                                      "oos": {"verdict": "degraded"}, "n_dates": 24}}
    m2 = rl._metrics_of(comp, "a + b")
    assert m2["rank_ic"] == 0.04 and m2["oos_verdict"] == "degraded"         # composite 块展开


def test_gate():
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "robust"}, 0.02)["passed"] is True
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "overfit"}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": 0.01, "oos_verdict": "robust"}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": None, "oos_verdict": "robust"}, 0.02)["passed"] is False
    assert rl._gate({}, 0.02)["passed"] is False


# ── 全链干跑(假桥)────────────────────────────────────────────────────────

_G0 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,5))"}}],
       "edges": []}
_G1 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,20))"}}],
       "edges": []}


def _wire(monkeypatch, tmp_path, evals, critique=None, generate=None):
    """接假桥:evals=逐轮 report2 求值响应队列;critique/generate 可覆盖。返回 (lessons, graphs, drafts)。"""
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    q = list(evals)
    monkeypatch.setattr(rl, "_call_generate",
                        generate or (lambda goal: {"ok": True, "graph": _G0, "attempts": 1}))
    monkeypatch.setattr(rl, "_call_critique",
                        critique or (lambda goal, metrics, graph:
                                     {"ok": True, "diagnosis": "换更长窗口", "graph": _G1, "source": "llm"}))
    monkeypatch.setattr(rl, "_eval_report2", lambda expr, p: q.pop(0))
    lessons, graphs, drafts = [], [], []
    monkeypatch.setattr(rl, "_write_lesson", lambda goal, s: lessons.append(s) or True)
    monkeypatch.setattr(rl, "_save_graph",
                        lambda goal, rid, g: graphs.append(g) or {"ok": True, "id": "w1", "name": "研究·x"})
    monkeypatch.setattr(rl, "_save_draft",
                        lambda rid, k, expr, goal, diag, m: drafts.append(expr) or
                        {"ok": True, "name": f"lib_rl_{rid[-6:]}_r{k}", "registered": True})
    return lessons, graphs, drafts


_PASS = {"status": "ok", "headline_ic": {"rank_ic": 0.05}, "portfolio": {"sharpe": 1.0, "ann_return": 0.2},
         "oos": {"verdict": "robust"}, "n_dates": 30}
_WEAK = {"status": "ok", "headline_ic": {"rank_ic": 0.001}, "portfolio": {"sharpe": 0.1, "ann_return": 0.01},
         "oos": {"verdict": "degraded"}, "n_dates": 30}


def test_loop_pass_first_round_early_stop(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[_PASS])
    end = rl.run_research_loop("rr_test01", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 1 and end["best_k"] == 0
    assert end["promoted"]["status"] == "draft" and drafts == ["rank(-delta(close,5))"]
    assert end["workflow_saved"]["ok"] is True and graphs == [_G0]   # 达标轮的图存工作流库
    assert end["memory_written"] is True and "达标" in lessons[0]
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test01")
    assert len(rows) == 1 and rows[0]["gate"]["passed"] is True
    assert rs.read_runs()[0]["status"] == "done"


def test_loop_exhausts_rounds_no_pass(monkeypatch, tmp_path):
    lessons, graphs, _ = _wire(monkeypatch, tmp_path, evals=[_WEAK, _WEAK])
    end = rl.run_research_loop("rr_test02", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2 and end["promoted"] is None
    assert "未达标" in lessons[0]                                     # 失败也沉淀教训
    assert graphs == [_G0]                                           # 两轮同 rank_ic → 第 0 轮为最佳
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test02", limit=10)
    assert [r["stage"] for r in rows] == ["improve", "propose"]      # 新在前
    assert rows[0]["diag"] == "换更长窗口" and rows[0]["critique_source"] == "llm"


def test_loop_generate_fail_honest_stop(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[],
                                    generate=lambda goal: {"ok": False, "reason": "LLM 不可用: timeout"})
    end = rl.run_research_loop("rr_test03", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is False and "提案失败" in end["error"] and "不降级模板" in end["error"]
    assert end["n_rounds"] == 0 and graphs == [] and drafts == []
    assert lessons and "提案即失败" in lessons[0]                     # 失败也记教训
    import guanlan_v2.research.store as rs
    assert rs.read_runs()[0]["status"] == "error"


def test_loop_eval_fail_round_continues(monkeypatch, tmp_path):
    bad = {"ok": False, "reason": "缺少数据"}
    _wire(monkeypatch, tmp_path, evals=[bad, _PASS])
    end = rl.run_research_loop("rr_test04", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2                # 求值失败轮继续批判改进
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test04", limit=10)
    assert rows[1]["failed"] is True and "缺少数据" in rows[1]["error"]
    assert rows[0]["gate"]["passed"] is True


def test_loop_rule_critique_prefix(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, evals=[_WEAK, _WEAK],
          critique=lambda goal, metrics, graph:
          {"ok": True, "diagnosis": "方向反了", "graph": _G1, "source": "rule", "llm_error": "x"})
    rl.run_research_loop("rr_test05", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                         progress=lambda **kw: None)
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test05", limit=10)
    assert rows[0]["diag"].startswith("(规则兜底·非 LLM) ")           # 诚实标注(对齐前端)
    assert rows[0]["critique_source"] == "rule"


def test_loop_multi_expr_pass_skips_autosave(monkeypatch, tmp_path):
    comp_pass = {"ok": True, "composite": {"headline_ic": {"rank_ic": 0.05},
                                           "portfolio": {"sharpe": 1.0, "ann_return": 0.2},
                                           "oos": {"verdict": "robust"}, "n_dates": 24}}
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[])
    g2 = {"nodes": [{"type": "formula", "params": {"expr": "a"}},
                    {"type": "formula", "params": {"expr": "b"}}], "edges": []}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": g2})
    monkeypatch.setattr(rl, "_eval_compose", lambda exprs, p: comp_pass)
    end = rl.run_research_loop("rr_test06", "找组合", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "skipped_multi" and drafts == []   # 多因子不自动入库(红线)


def test_write_lesson_real_memory(monkeypatch, tmp_path):
    """_write_lesson 真调 memory_write_impl(conftest 已把 _MEMORY_PATH 隔离到 tmp)。"""
    import guanlan_v2.console.tools as ct
    mp = tmp_path / "memory.md"
    monkeypatch.setattr(ct, "_MEMORY_PATH", mp)
    assert rl._write_lesson("找一个反转因子", "研究「找一个反转因子」1轮达标:lib_x") is True
    txt = mp.read_text(encoding="utf-8")
    assert "(研究·找一个反转因子)" in txt and "lib_x" in txt          # keyed 常驻行
```

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_loop.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'guanlan_v2.research.loop'`)

- [ ] **Step 3: 实现 guanlan_v2/research/loop.py**

```python
# -*- coding: utf-8 -*-
"""研究回路编排器(P2):提案→求值(小灶直调)→过门→批判改进,逐轮落档。

确定性编排,LLM 只在提案/批判两个接缝:经**同步 HTTP 自调**本进程 /workflow/generate|critique
(handler 是 router 闭包不可 import;且 engine LLM 客户端连接池绑事件循环,daemon 线程反复
asyncio.run 会炸 "Event loop is closed"——HTTP 自调让 LLM 落在 server 主循环上,零复制同一实现)。
本模块只会在 9999 进程内的 daemon 线程里跑(api.py 状态机拉起),同步自 HTTP 安全(仓级红线
只禁「协程内」同步自 HTTP)。求值三道菜直调模块级 sync 函数(与画布/帷幄 ww_factor_analyze
同一批函数,口径逐位一致)。

红线:提案失败诚实终止(绝不降级模板,严于前端 aiLoop);规则兜底显形 source=rule;
draft 绝不自动上架;多因子合成达标不自动入库(skipped_multi);失败也写教训。
独立小函数便于 monkeypatch(仓例 console/tools.py)。
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from guanlan_v2.research import store as rstore

GATE_OOS_OK = "robust"      # 过门要求的样本外判定
_EVAL_OOS_FRAC = 0.3        # 求值一律开样本外(oos 不开 verdict 恒缺 → 门永不过)


def new_run_id() -> str:
    return "rr_" + uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 桥(独立小函数便于 monkeypatch)──────────────────────────────────────

def _self_post(path: str, payload: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    """同步自 HTTP(仅 daemon 线程调用,永不进事件循环)。"""
    port = os.environ.get("GUANLAN_PORT", "9999")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(镜像 console/tools.py:_resp_json)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _call_generate(goal: str) -> Dict[str, Any]:
    return _self_post("/workflow/generate", {"goal": goal}, timeout=300)


def _call_critique(goal: str, metrics: Dict[str, Any], graph: Dict[str, Any]) -> Dict[str, Any]:
    return _self_post("/workflow/critique",
                      {"goal": goal, "metrics": metrics, "graph": graph}, timeout=300)


def _eval_report2(expr: str, p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorReport2In, _factor_report2
    return _resp_json(_factor_report2(FactorReport2In(
        expr_or_name=expr, universe=p["universe"], freq=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _eval_compose(exprs: List[str], p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorComposeIn, _factor_compose
    return _resp_json(_factor_compose(FactorComposeIn(
        members=exprs, universe=p["universe"], freq=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _eval_backtest(exprs: List[str], p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import BacktestVectorIn, _backtest_vector
    return _resp_json(_backtest_vector(BacktestVectorIn(
        features=exprs, universe=p["universe"], rebalance=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _save_draft(run_id: str, k: int, expr: str, goal: str, diag: str,
                metrics: Dict[str, Any]) -> Dict[str, Any]:
    """达标因子存 factorlib(status=draft,绝不自动上架;人审 /factorlib/promote 转正)。"""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore
    name = f"lib_rl_{run_id[-6:]}_r{k}"
    body = SaveIn(name=name, expr=expr, family="library_mined",
                  description=f"研究回路产出:{goal[:60]} · {str(diag)[:80]}",
                  source="research_loop", status="draft",
                  meta={"metrics": metrics, "run_id": run_id, "round": k})
    return _save_factor(body, LibraryFactorStore())


def _save_graph(goal: str, run_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """最佳轮图存工作流库(用户在工作流页点开上画布;与 /workflow/save 同一 store 文件)。"""
    try:
        from guanlan_v2.workflow.store import WorkflowStore
        rec = WorkflowStore().save(f"研究·{goal[:16]}·{run_id[-6:]}", graph)
        return {"ok": True, "id": rec["id"], "name": rec["name"]}
    except Exception as exc:  # noqa: BLE001 — 存图失败显形进终态行,不挡收工
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _write_lesson(goal: str, summary: str) -> bool:
    """每 run 一条 keyed 常驻记忆(失败也记);同 key 收敛覆盖=同一目标最新认知。
    单行化在此做(memory_write_impl 正常路径不去换行);cap 280 由其内做。"""
    try:
        from guanlan_v2.console.tools import memory_write_impl
        key = "研究·" + goal.replace("\n", " ").strip()[:24]
        r = memory_write_impl(text=summary.replace("\n", " ").replace("\r", " ").strip(),
                              scope="global", key=key)
        return bool(r.get("ok"))
    except Exception:  # noqa: BLE001 — 教训写失败不挡收工,memory_written=False 显形
        return False


# ── 纯函数 ───────────────────────────────────────────────────────────────

def _pick_dish(graph: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """图形状→(菜名, exprs)。formula.params.expr + factorlib.params.name;
    有 backtest 节点→backtest;≥2 表达式→compose;1→report2;0→(None,[]) 诚实不支持。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    exprs: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        if n.get("type") == "formula":
            e = str(p.get("expr") or "").strip()
            if e:
                exprs.append(e)
        elif n.get("type") == "factorlib":
            e = str(p.get("name") or p.get("expr") or "").strip()
            if e:
                exprs.append(e)
    if not exprs:
        return None, []
    if any(isinstance(n, dict) and n.get("type") == "backtest" for n in nodes):
        return "backtest", exprs
    if len(exprs) >= 2:
        return "compose", exprs
    return "report2", exprs


def _metrics_of(resp: Dict[str, Any], factor: str) -> Dict[str, Any]:
    """求值响应→六键(镜像前端 metricsOf;compose 嵌套 composite **dict** 先展开——
    report2 的 composite 是 bool 标志,isinstance 区分)。"""
    r = resp.get("composite") if isinstance(resp.get("composite"), dict) else resp
    hic = r.get("headline_ic") if isinstance(r.get("headline_ic"), dict) else {}
    ic = r.get("ic") if isinstance(r.get("ic"), dict) else {}
    met = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
    pf = r.get("portfolio") if isinstance(r.get("portfolio"), dict) else {}
    oos = r.get("oos") if isinstance(r.get("oos"), dict) else {}
    rank_ic = hic.get("rank_ic")
    if rank_ic is None:
        rank_ic = ic.get("rank_ic_mean")
    if rank_ic is None:
        rank_ic = met.get("rank_ic")
    return {"rank_ic": rank_ic, "sharpe": pf.get("sharpe"), "ann_return": pf.get("ann_return"),
            "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"), "factor": factor}


def _gate(metrics: Dict[str, Any], min_rank_ic: float) -> Dict[str, Any]:
    """过门:rank_ic ≥ 门槛 且样本外=robust(保守;draft 只是抽屉,严一点无妨)。"""
    ric = metrics.get("rank_ic")
    ok_num = isinstance(ric, (int, float)) and ric == ric
    passed = bool(ok_num and float(ric) >= float(min_rank_ic)
                  and metrics.get("oos_verdict") == GATE_OOS_OK)
    return {"passed": passed, "min_rank_ic": float(min_rank_ic), "oos_required": GATE_OOS_OK}


# ── 主体 ─────────────────────────────────────────────────────────────────

def run_research_loop(run_id: str, goal: str, max_rounds: int, min_rank_ic: float,
                      universe: str, freq: str, start: Optional[str], end: Optional[str],
                      progress: Callable[..., None]) -> Dict[str, Any]:
    """回路主体(daemon 线程内跑)。返回终态行(已落档)。progress(**kw) 由 api.py 状态机提供。"""
    params = {"max_rounds": max_rounds, "min_rank_ic": min_rank_ic,
              "universe": universe, "freq": freq, "start": start, "end": end}
    rounds_ok = rstore.append_run({"run_id": run_id, "kind": "start", "goal": goal,
                                   "params": params, "ts": _now()})
    rounds: List[Dict[str, Any]] = []
    promoted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    # ── 提案(失败诚实终止,绝不降级模板)──
    progress(phase="propose", label="① LLM 生成初始工作流…", round_k=0)
    try:
        gen = _call_generate(goal)
    except Exception as exc:  # noqa: BLE001
        gen = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not gen.get("ok") or not isinstance(gen.get("graph"), dict):
        error = f"提案失败(诚实终止,不降级模板): {gen.get('reason')}"
        mem = _write_lesson(goal, f"研究「{goal[:40]}」提案即失败:{str(gen.get('reason'))[:120]}")
        end_row = {"run_id": run_id, "kind": "end", "ok": False, "error": error,
                   "n_rounds": 0, "best_k": None, "best_metrics": None, "promoted": None,
                   "workflow_saved": None, "memory_written": mem,
                   "rounds_recorded": rounds_ok, "ts": _now()}
        rstore.append_run(end_row)
        return end_row

    graph: Dict[str, Any] = gen["graph"]
    diag = "初始生成(LLM propose)"
    crit_source: Optional[str] = None
    for k in range(max_rounds):
        # ── 求值(后端真算;消掉 critique「指标自报」缺口)──
        progress(phase="evaluate", label=f"② 第 {k + 1}/{max_rounds} 轮 · 后端真算指标…", round_k=k)
        dish, exprs = _pick_dish(graph)
        failed, err = False, None
        metrics: Dict[str, Any] = {}
        if dish is None:
            failed, err = True, "不支持的图形状(非配方模板)"
        else:
            try:
                if dish == "report2":
                    resp = _eval_report2(exprs[0], params)
                elif dish == "compose":
                    resp = _eval_compose(exprs, params)
                else:
                    resp = _eval_backtest(exprs, params)
                if resp.get("ok") is False or resp.get("status") not in (None, "ok"):
                    failed, err = True, str(resp.get("reason") or resp.get("status") or "求值失败")
                else:
                    metrics = _metrics_of(resp, " + ".join(exprs))
            except Exception as exc:  # noqa: BLE001
                failed, err = True, f"{type(exc).__name__}: {exc}"
        gate = (_gate(metrics, min_rank_ic) if not failed
                else {"passed": False, "min_rank_ic": float(min_rank_ic), "oos_required": GATE_OOS_OK})
        row = {"run_id": run_id, "k": k, "ts": _now(),
               "stage": ("propose" if k == 0 else "improve"),
               "diag": diag, "critique_source": crit_source,
               "exprs": exprs, "dish": dish, "metrics": metrics, "gate": gate,
               "failed": failed, "error": err, "graph": graph}
        if not rstore.append_round(row):
            rounds_ok = False
        rounds.append(row)
        # ── 过门 → draft 入库 + 提前收工 ──
        if gate["passed"]:
            progress(phase="promote", label="③ 达标 · 存 draft 入库(待人审)…", round_k=k)
            if len(exprs) == 1:
                try:
                    pr = _save_draft(run_id, k, exprs[0], goal, diag, metrics)
                except Exception as exc:  # noqa: BLE001
                    pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
                promoted = ({"name": pr.get("name"), "status": "draft"} if pr.get("ok")
                            else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
            else:
                promoted = {"name": None, "status": "skipped_multi",
                            "reason": "多因子合成暂不自动入库(库以单表达式为单位),成分见轮次档案"}
            break
        if k + 1 >= max_rounds:
            break
        # ── 批判改进(LLM;失败规则兜底显形;批判环整体失败 → 诚实中断)──
        progress(phase="critique", label=f"④ 第 {k + 1} 轮未达标 · LLM 批判改进…", round_k=k)
        try:
            cr = _call_critique(goal, metrics, graph)
        except Exception as exc:  # noqa: BLE001
            cr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not cr.get("ok") or not isinstance(cr.get("graph"), dict):
            error = f"批判环失败: {cr.get('reason')}"
            break
        crit_source = str(cr.get("source") or "?")
        diag = (("(规则兜底·非 LLM) " if crit_source == "rule" else "")
                + str(cr.get("diagnosis") or ""))
        graph = cr["graph"]

    # ── 收工三件套:最佳图入工作流库 + 教训 + 终态行 ──
    best: Optional[Dict[str, Any]] = None
    for r in rounds:
        ric = (r.get("metrics") or {}).get("rank_ic")
        if r.get("failed") or not isinstance(ric, (int, float)) or ric != ric:
            continue
        if (r.get("gate") or {}).get("passed"):
            best = r
            break
        if best is None or float(ric) > float((best.get("metrics") or {}).get("rank_ic")):
            best = r
    ws = _save_graph(goal, run_id, best["graph"]) if best else None
    n = len(rounds)
    bm = (best or {}).get("metrics") or {}
    if promoted and promoted.get("status") == "draft":
        lesson = (f"研究「{goal[:40]}」{n}轮达标:{promoted['name']} rank_ic={bm.get('rank_ic')} "
                  f"oos={bm.get('oos_verdict')} 已入draft待人审;诊断:{str(diag)[:80]}")
    elif error:
        lesson = f"研究「{goal[:40]}」{n}轮中断:{str(error)[:120]}"
    else:
        lesson = (f"研究「{goal[:40]}」{n}轮未达标(门 rank_ic≥{min_rank_ic} 且 oos=robust):"
                  f"最佳 rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')};诊断:{str(diag)[:80]}")
    mem_ok = _write_lesson(goal, lesson)
    end_row = {"run_id": run_id, "kind": "end", "ok": error is None, "error": error,
               "n_rounds": n, "best_k": (best or {}).get("k"),
               "best_metrics": (bm or None), "promoted": promoted,
               "workflow_saved": ws, "memory_written": mem_ok,
               "rounds_recorded": rounds_ok, "ts": _now()}
    rstore.append_run(end_row)
    return end_row
```

- [ ] **Step 4: 跑测确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_loop.py -v`
Expected: 10 passed(`test_write_lesson_real_memory` 真调 memory_write_impl,验证 keyed 行落 tmp)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/research/loop.py tests/test_research_loop.py
git commit -m "feat(research): P2 T3 编排器核心(提案/小灶求值/过门/批判/draft/存图/教训)"
```

---

### Task 4: research/api.py — 单飞状态机 + 4 端点 + server 挂载

**Files:**
- Create: `guanlan_v2/research/api.py`
- Modify: `guanlan_v2/research/__init__.py`(补导出)
- Modify: `guanlan_v2/server.py`(console 挂载后 include research router)
- Test: `tests/test_research_api.py`

**Interfaces:**
- Consumes: Task 3 `rloop.run_research_loop/new_run_id`;Task 1 `rstore.read_runs/read_rounds/RUNS_PATH/ROUNDS_PATH`;workflow `_UNIVERSE_OK`。
- Produces: `build_research_router() -> APIRouter`;`POST /research/loop/start`(goal 必填、max_rounds 钳 1..5、min_rank_ic 钳 0..0.2、universe 校验)→ `{ok,started,run_id,state}` | `{ok:false,reason:"already_running",state}`;`GET /research/loop/status` → `{ok,state}`;`GET /research/runs?limit=` → `{ok,runs,n,path}`;`GET /research/rounds?run_id=&limit=` → `{ok,rounds,n,path}`。全部恒 HTTP 200。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_research_api.py`:

```python
"""研究回路端点+状态机单测(P2 §3):裸 FastAPI 挂 router;loop 主体打桩,不跑真 LLM。"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.research.api as rapi


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(rapi.build_research_router())
    return TestClient(app)


def _reset_state(monkeypatch):
    monkeypatch.setattr(rapi, "_RESEARCH_STATE", {
        "running": False, "phase": "idle", "label": "", "round_k": 0, "total_rounds": 0,
        "run_id": None, "started_at": None, "ended_at": None, "ok": None, "error": None,
        "lines": []})


def test_start_requires_goal(monkeypatch):
    _reset_state(monkeypatch)
    j = _client().post("/research/loop/start", json={"goal": "  "}).json()
    assert j["ok"] is False and "goal" in j["reason"]


def test_start_rejects_bad_universe(monkeypatch):
    _reset_state(monkeypatch)
    j = _client().post("/research/loop/start",
                       json={"goal": "找反转", "universe": "csi300"}).json()   # csi300 非法(是 benchmark id)
    assert j["ok"] is False and "universe" in j["reason"]


def test_start_clamps_and_runs(monkeypatch):
    _reset_state(monkeypatch)
    seen = {}

    def fake_loop(run_id, goal, max_rounds, min_rank_ic, universe, freq, start, end, progress):
        seen.update(run_id=run_id, goal=goal, max_rounds=max_rounds, min_rank_ic=min_rank_ic)
        progress(phase="evaluate", label="② 第 1/1 轮…", round_k=0)
        return {"ok": True}

    monkeypatch.setattr(rapi.rloop, "run_research_loop", fake_loop)
    j = _client().post("/research/loop/start",
                       json={"goal": "找反转", "max_rounds": 99, "min_rank_ic": 9.9,
                             "universe": "csi_fast"}).json()
    assert j["ok"] is True and j["run_id"].startswith("rr_")
    for _ in range(50):                                              # 等 daemon 线程收工
        time.sleep(0.02)
        if not rapi._research_public_state()["running"]:
            break
    st = rapi._research_public_state()
    assert st["phase"] == "done" and st["ok"] is True
    assert seen["max_rounds"] == 5 and seen["min_rank_ic"] == 0.2    # 服务端钳制
    assert any("第 1/1 轮" in ln for ln in st["lines"])              # progress 进 lines


def test_start_single_flight(monkeypatch):
    _reset_state(monkeypatch)
    with rapi._RESEARCH_LOCK:
        rapi._RESEARCH_STATE["running"] = True
    j = _client().post("/research/loop/start", json={"goal": "找反转"}).json()
    assert j["ok"] is False and j["reason"] == "already_running"
    with rapi._RESEARCH_LOCK:
        rapi._RESEARCH_STATE["running"] = False


def test_loop_thread_crash_clears_running(monkeypatch):
    _reset_state(monkeypatch)

    def boom(**kw):
        raise RuntimeError("炸")

    monkeypatch.setattr(rapi.rloop, "run_research_loop", boom)
    j = _client().post("/research/loop/start", json={"goal": "找反转"}).json()
    assert j["ok"] is True
    for _ in range(50):
        time.sleep(0.02)
        if not rapi._research_public_state()["running"]:
            break
    st = rapi._research_public_state()
    assert st["running"] is False and st["phase"] == "error" and "炸" in st["error"]


def test_status_and_archive_endpoints(monkeypatch, tmp_path):
    _reset_state(monkeypatch)
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    rs.append_run({"run_id": "rr_a", "kind": "start", "goal": "x", "ts": "t"})
    rs.append_round({"run_id": "rr_a", "k": 0, "diag": "初始"})
    c = _client()
    assert c.get("/research/loop/status").json()["state"]["phase"] == "idle"
    j = c.get("/research/runs").json()
    assert j["ok"] is True and j["runs"][0]["status"] == "interrupted"   # 无终态且不在跑 → 中断显形
    j2 = c.get("/research/rounds?run_id=rr_a").json()
    assert j2["ok"] is True and j2["n"] == 1
```

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_api.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'guanlan_v2.research.api'`)

- [ ] **Step 3: 实现 guanlan_v2/research/api.py**

```python
# -*- coding: utf-8 -*-
"""研究回路状态机 + 端点(照 screen/api.py regen 范式:daemon 线程+单飞锁+状态轮询)。

零 env 开关、零定时器、零子进程:回路只能被显式 POST 发起,合并即零行为变化。
锁纪律:threading.Lock 非可重入——快照只取一次锁绝不嵌套;线程体 finally 必清 running。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from guanlan_v2.research import loop as rloop
from guanlan_v2.research import store as rstore

_RESEARCH_LOCK = threading.Lock()
_RESEARCH_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "round_k": 0, "total_rounds": 0,
    "run_id": None, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "lines": [],
}


def _research_public_state() -> Dict[str, Any]:
    """快照(只取一次锁,绝不嵌套)+ elapsed_sec;lines 截尾 [-12:]。"""
    with _RESEARCH_LOCK:
        s = dict(_RESEARCH_STATE)
        s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or time.time()) - s["started_at"])
    return s


class ResearchLoopIn(BaseModel):
    """``POST /research/loop/start`` 入参(钳制在端点内做,服务端权威)。"""

    goal: str = ""
    max_rounds: int = 3
    min_rank_ic: float = 0.02
    universe: str = "csi300_active"
    freq: str = "month"
    start: Optional[str] = None
    end: Optional[str] = None


def _progress(**kw: Any) -> None:
    """loop 线程的进度回调:白名单键合并进状态 + label 追加进 lines(≤40)。"""
    with _RESEARCH_LOCK:
        for k, v in kw.items():
            if k in ("phase", "label", "round_k"):
                _RESEARCH_STATE[k] = v
        label = kw.get("label")
        if label:
            _RESEARCH_STATE["lines"].append(str(label))
            if len(_RESEARCH_STATE["lines"]) > 40:
                _RESEARCH_STATE["lines"] = _RESEARCH_STATE["lines"][-40:]


def _run_loop_thread(run_id: str, body: ResearchLoopIn) -> None:
    """线程体:跑回路;任何异常兜底,finally 必清 running(防卡死)。"""
    err: Optional[str] = None
    end_row: Dict[str, Any] = {}
    try:
        end_row = rloop.run_research_loop(
            run_id=run_id, goal=body.goal, max_rounds=body.max_rounds,
            min_rank_ic=body.min_rank_ic, universe=body.universe, freq=body.freq,
            start=body.start, end=body.end, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        ok = (err is None and bool(end_row.get("ok")))
        with _RESEARCH_LOCK:
            _RESEARCH_STATE.update(
                running=False, ended_at=time.time(), ok=ok,
                phase=("done" if ok else "error"),
                error=(err or end_row.get("error")))


def _start_loop_bg(body: ResearchLoopIn) -> Optional[str]:
    """抢单飞锁并起回路 daemon 线程;已在跑 → None。"""
    run_id = rloop.new_run_id()
    with _RESEARCH_LOCK:
        if _RESEARCH_STATE.get("running"):
            return None
        _RESEARCH_STATE.update(
            running=True, phase="starting", label="启动研究回路…", round_k=0,
            total_rounds=body.max_rounds, run_id=run_id,
            started_at=time.time(), ended_at=None, ok=None, error=None, lines=[])
    threading.Thread(target=lambda: _run_loop_thread(run_id, body),
                     name="research-loop", daemon=True).start()
    return run_id


def build_research_router() -> APIRouter:
    """研究回路路由组(/research/* 已核实引擎与 guanlan 两侧空闲,无遮蔽)。"""
    router = APIRouter(tags=["research"])

    @router.post("/research/loop/start")
    def research_loop_start(body: ResearchLoopIn):
        goal = (body.goal or "").strip()
        if not goal:
            return JSONResponse({"ok": False, "reason": "goal 不能为空"})
        body.goal = goal
        body.max_rounds = max(1, min(int(body.max_rounds or 3), 5))
        body.min_rank_ic = max(0.0, min(float(body.min_rank_ic or 0.02), 0.2))
        try:
            from guanlan_v2.workflow.api import _UNIVERSE_OK
            if body.universe not in _UNIVERSE_OK:
                return JSONResponse({"ok": False, "reason":
                                     f"universe 非法: {body.universe}(允许 {sorted(_UNIVERSE_OK)})"})
        except Exception:  # noqa: BLE001 — workflow 模块不可用时不拦(回路内求值会诚实失败)
            pass
        rid = _start_loop_bg(body)
        if rid is None:
            return JSONResponse({"ok": False, "reason": "already_running",
                                 "state": _research_public_state()})
        return JSONResponse({"ok": True, "started": True, "run_id": rid,
                             "state": _research_public_state()})

    @router.get("/research/loop/status")
    def research_loop_status():
        return JSONResponse({"ok": True, "state": _research_public_state()})

    @router.get("/research/runs")
    def research_runs(limit: int = 20):
        with _RESEARCH_LOCK:
            rid = _RESEARCH_STATE.get("run_id") if _RESEARCH_STATE.get("running") else None
        items = rstore.read_runs(limit=limit, running_run_id=rid)
        return JSONResponse({"ok": True, "runs": items, "n": len(items),
                             "path": str(rstore.RUNS_PATH)})

    @router.get("/research/rounds")
    def research_rounds(run_id: str = "", limit: int = 50):
        items = rstore.read_rounds(run_id=(run_id or None), limit=limit)
        return JSONResponse({"ok": True, "rounds": items, "n": len(items),
                             "path": str(rstore.ROUNDS_PATH)})

    return router
```

- [ ] **Step 4: 补 `__init__.py` 导出**

`guanlan_v2/research/__init__.py` 全文改为:

```python
# -*- coding: utf-8 -*-
"""P2 自主研究回路:提案→求值→批判→改进 后台编排 + 逐轮落档 + draft 入库。"""
from guanlan_v2.research.api import build_research_router

__all__ = ["build_research_router"]
```

- [ ] **Step 5: server.py 挂载**

在 `app.include_router(build_console_router())`(server.py:206 区域)之后、market 挂载注释之前插入:

```python
    # ── P2:自主研究回路(提案→求值→批判→改进 后台单飞;零开关零定时器,
    #     只能被显式 POST /research/loop/start 发起 → 合并零行为变化)──────
    from guanlan_v2.research import build_research_router

    app.include_router(build_research_router())
```

- [ ] **Step 6: 跑测确认通过 + server 装配冒烟**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_research_api.py -v`
Expected: 6 passed
Run: `G:/financial-analyst/.venv/Scripts/python.exe -c "from guanlan_v2.server import create_app; app=create_app(); rs=[r.path for r in app.routes if getattr(r,'path','').startswith('/research')]; print(sorted(rs)); assert len(rs)==4"`
Expected: 打印 4 条 /research 路由,无异常

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/research/api.py guanlan_v2/research/__init__.py guanlan_v2/server.py tests/test_research_api.py
git commit -m "feat(research): P2 T4 单飞状态机+4端点+server 挂载(/research 命名空间已核无遮蔽)"
```

---

### Task 5: 帷幄两工具 + 四处同步 + critique 注记 + MCP README

**Files:**
- Modify: `guanlan_v2/console/tools.py`(+`_research_run_line`/`research_loop_impl`/`research_runs_impl`;WW_TOOL_TABLE 插 2 条目;workflow_critique_impl 注记文案;ww_workflow_critique 描述补一句)
- Modify: `guanlan_v2/console/api.py`(_SYSTEM_PROMPT 能力行 + 纪律 14)
- Modify: `tests/test_console_tools.py`(计数 40→42、65→67 ×2处、explicit_ww_n 40→42;expected 集 +4;新增 5 个工具测试)
- Modify: `tests/test_guanlan_mcp.py`(三处 44→46,注释 37→39)
- Modify: `guanlan_v2/glmcp/README.md`(43→46 两处)

**Interfaces:**
- Consumes: Task 4 端点;既有 `_self_post(path, payload, timeout=120)`/`_self_get(path, timeout=30)`。
- Produces: `research_loop_impl(goal, max_rounds=3, min_rank_ic=0.02, universe="csi300_active", wait=True, poll_seconds=15.0, timeout_seconds=1800.0) -> dict`;`research_runs_impl(run_id="", limit=10) -> dict`;`_research_run_line(run: dict) -> str`。

- [ ] **Step 1: 写失败测试(先改计数+加工具测试)**

1a. `tests/test_console_tools.py` 计数修改(4 处):
- `assert len(out["registered_ww"]) == 40` → `== 42`(行尾注释追加 ` +2 P2 研究回路`)
- `assert out["console_n"] == 65 and out["console_missing"] == []` → `== 67`
- `assert out["explicit_n"] == 65 and out["explicit_ww_n"] == 40` → `== 67` / `== 42`
- `test_registry_derivation_consistent` 内两处:`== 40` → `== 42`;`== 65` → `== 67`

1b. `test_ww_reachable_endpoints_matches_expected` 的 expected 集追加(放 `"/screen/picks"` 行后):

```python
        "/research/loop/start",   # ww_research_loop(P2 发起)
        "/research/loop/status",  # ww_research_loop(wait 轮询)
        "/research/runs",         # ww_research_loop 成绩单 + ww_research_runs 列表
        "/research/rounds",       # ww_research_runs 逐轮详情
```

1c. 文件尾追加新工具测试:

```python
# ── P2: ww_research_loop / ww_research_runs ─────────────────────────────────

_RUN_ROW = {"run_id": "rr_ab12cd34ef", "status": "done", "ok": True, "goal": "找一个短周期反转因子",
            "n_rounds": 2, "best_k": 1,
            "best_metrics": {"rank_ic": 0.031, "oos_verdict": "robust"},
            "promoted": {"name": "lib_rl_cd34ef_r1", "status": "draft"},
            "workflow_saved": {"ok": True, "id": "w1", "name": "研究·找一个短周期反转因子·cd34ef"}}


def test_research_loop_impl_start_and_wait(monkeypatch):
    calls = {"status": 0}

    def fake_post(path, payload, timeout=120):
        assert path == "/research/loop/start" and payload["goal"] == "找一个短周期反转因子"
        return {"ok": True, "started": True, "run_id": "rr_ab12cd34ef", "state": {"running": True}}

    def fake_get(path, timeout=30):
        if path.startswith("/research/loop/status"):
            calls["status"] += 1
            done = calls["status"] >= 2
            return {"ok": True, "state": {"running": (not done),
                                          "phase": ("done" if done else "evaluate")}}
        assert path.startswith("/research/runs")
        return {"ok": True, "runs": [_RUN_ROW]}

    monkeypatch.setattr(ct, "_self_post", fake_post)
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.research_loop_impl(goal="找一个短周期反转因子", wait=True, poll_seconds=0, timeout_seconds=60)
    assert res["ok"] is True
    assert "lib_rl_cd34ef_r1" in res["content"] and "draft" in res["content"]
    assert "+0.0310" in res["content"] and "工作流库" in res["content"]


def test_research_loop_impl_already_running(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": False, "reason": "already_running", "state": {"phase": "evaluate"}})
    res = ct.research_loop_impl(goal="x", wait=False)
    assert res["ok"] is False and "already_running" in res["content"]


def test_research_loop_impl_timeout(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": True, "started": True, "run_id": "rr_x", "state": {}})
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30:
                        {"ok": True, "state": {"running": True, "phase": "evaluate"}})
    res = ct.research_loop_impl(goal="x", wait=True, poll_seconds=0, timeout_seconds=0.05)
    assert res["ok"] is False and "超时" in res["content"] and "ww_research_runs" in res["content"]


def test_research_runs_impl_list(monkeypatch):
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: {"ok": True, "runs": [_RUN_ROW]})
    res = ct.research_runs_impl()
    assert res["ok"] is True and "rr_ab12cd34ef" in res["content"] and "[done]" in res["content"]


def test_research_runs_impl_detail_strips_graph(monkeypatch):
    rounds = [{"run_id": "rr_a", "k": 1, "stage": "improve", "diag": "(规则兜底·非 LLM) 方向反了",
               "critique_source": "rule", "dish": "report2",
               "metrics": {"rank_ic": 0.02, "oos_verdict": "robust"},
               "gate": {"passed": True}, "failed": False, "error": None,
               "graph": {"nodes": [{"id": "n1"}], "edges": []}},
              {"run_id": "rr_a", "k": 0, "stage": "propose", "diag": "初始生成(LLM propose)",
               "critique_source": None, "dish": "report2",
               "metrics": {"rank_ic": -0.01, "oos_verdict": "degraded"},
               "gate": {"passed": False}, "failed": False, "error": None,
               "graph": {"nodes": [], "edges": []}}]
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: {"ok": True, "rounds": rounds})
    res = ct.research_runs_impl(run_id="rr_a")
    assert res["ok"] is True
    assert "第0轮" in res["content"] and "第1轮" in res["content"]     # 时间正序讲故事
    assert "规则兜底" in res["content"]                                # 诚实标注透传
    assert all("graph" not in r for r in res["raw"]["rounds"])        # graph 不进上下文
```

1d. `tests/test_guanlan_mcp.py` 三处:`== 44` → `== 46`;L13 行尾注释 `# 37 ww_(40−3 excluded) + 7 alpha-zoo` → `# 39 ww_(42−3 excluded) + 7 alpha-zoo`。

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: FAIL(计数 42≠40、46≠44、AttributeError research_loop_impl)

- [ ] **Step 3: 实现 tools.py**

3a. 在 `picks_perf_impl` 之后(工具 impl 区尾部)加三个函数:

```python
# ── P2 自主研究回路 ─────────────────────────────────────────────────────────

def _research_run_line(run: Dict[str, Any]) -> str:
    """run 行(read_runs 合并行)→ 一行成绩单人话。"""
    bm = run.get("best_metrics") or {}
    pr = run.get("promoted") or {}
    ws = run.get("workflow_saved") or {}
    ric = bm.get("rank_ic")
    ric_s = f"{float(ric):+.4f}" if isinstance(ric, (int, float)) else "—"
    if pr.get("status") == "draft":
        verdict = f"达标 ✅ 已入 draft:{pr.get('name')}(待人审 POST /factorlib/promote 转正)"
    elif pr.get("status") == "skipped_multi":
        verdict = "达标但为多因子合成,未自动入库(成分见 ww_research_runs run_id 详情)"
    elif run.get("error"):
        verdict = f"中断:{run.get('error')}"
    elif run.get("status") == "interrupted":
        verdict = "已中断(服务重启)"
    else:
        verdict = "未达标(逐轮诊断 ww_research_runs run_id 查)"
    tail = f" · 图已存工作流库「{ws.get('name')}」" if ws.get("ok") else ""
    return (f"研究「{str(run.get('goal') or '')[:40]}」{run.get('n_rounds', '?')} 轮 · "
            f"最佳 RankIC {ric_s}(第{run.get('best_k')}轮,oos={bm.get('oos_verdict')}) · "
            f"{verdict}{tail}")


def research_loop_impl(goal: str = "", max_rounds: int = 3, min_rank_ic: float = 0.02,
                       universe: str = "csi300_active", wait: bool = True,
                       poll_seconds: float = 15.0, timeout_seconds: float = 1800.0) -> Dict[str, Any]:
    """发起自主研究回路(后台单飞);wait 时轮询到收工并拼成绩单(三段式仓例 model_promote_impl)。"""
    g = (goal or "").strip()
    if not g:
        return {"ok": False, "content": "请给研究目标 goal(如:找一个短周期反转因子)", "artifact": None}
    body = {"goal": g, "max_rounds": int(max_rounds or 3),
            "min_rank_ic": float(min_rank_ic or 0.02), "universe": universe or "csi300_active"}
    try:
        r = _self_post("/research/loop/start", body)
    except Exception as e:
        return {"ok": False, "content": f"研究回路启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"研究回路未启动: {r.get('reason')}", "artifact": None, "raw": r}
    rid = r.get("run_id")
    if not wait:
        return {"ok": True, "artifact": None, "raw": r,
                "content": f"已启动研究回路 run_id={rid}(后台跑,数分钟);稍后 ww_research_runs 查成绩。"}
    import time as _time
    deadline = _time.time() + float(timeout_seconds or 1800.0)
    state: Dict[str, Any] = {}
    done = False
    while _time.time() <= deadline:
        try:
            s = _self_get("/research/loop/status")
        except Exception as e:
            return {"ok": False, "content": f"回路状态读取失败: {e}", "artifact": None}
        state = s.get("state") or {}
        if not state.get("running") and state.get("phase") in ("done", "error"):
            done = True
            break
        if poll_seconds:
            _time.sleep(float(poll_seconds))
    if not done:
        return {"ok": False, "artifact": None, "raw": {"state": state},
                "content": f"研究回路轮询超时 run_id={rid}:后端可能仍在跑,稍后 ww_research_runs 查"}
    run = None
    try:
        rr = _self_get("/research/runs?limit=10")
        run = next((x for x in (rr.get("runs") or []) if x.get("run_id") == rid), None)
    except Exception:  # noqa: BLE001
        run = None
    if run is None:
        return {"ok": False, "artifact": None, "raw": {"state": state},
                "content": f"回路已停但档案缺失 run_id={rid}(rounds_recorded 可能为 False,查 var/research_runs.jsonl)"}
    return {"ok": bool(run.get("ok")), "artifact": None, "raw": {"run": run},
            "content": _research_run_line(run)}


def research_runs_impl(run_id: str = "", limit: int = 10) -> Dict[str, Any]:
    """研究回路档案:无 run_id 列近期 run;有 run_id 出逐轮详情(graph 不进上下文防灌)。"""
    try:
        if (run_id or "").strip():
            rr = _self_get(f"/research/rounds?run_id={run_id.strip()}&limit=50")
            rows = list(reversed(rr.get("rounds") or []))   # 时间正序讲故事
            if not rows:
                return {"ok": True, "content": f"run {run_id} 无轮次记录", "artifact": None}
            lines = []
            for r in rows:
                m = r.get("metrics") or {}
                ric = m.get("rank_ic")
                ric_s = f"{float(ric):+.4f}" if isinstance(ric, (int, float)) else "—"
                mark = "❌" if r.get("failed") else ("✅" if (r.get("gate") or {}).get("passed") else "·")
                lines.append(
                    f"第{r.get('k')}轮{mark} {r.get('dish') or '不支持'} RankIC {ric_s} "
                    f"oos={m.get('oos_verdict') or '—'} | {str(r.get('diag') or '')[:70]}"
                    + (f" | 错误: {str(r.get('error'))[:60]}" if r.get("failed") else ""))
            slim = [{k_: v for k_, v in r.items() if k_ != "graph"} for r in rows]
            return {"ok": True, "content": "\n".join(lines), "artifact": None,
                    "raw": {"rounds": slim}}
        rr = _self_get(f"/research/runs?limit={max(1, min(int(limit or 10), 50))}")
        runs = rr.get("runs") or []
        if not runs:
            return {"ok": True, "artifact": None,
                    "content": "暂无研究回路档案。用 ww_research_loop 发起一次(需确认)。"}
        return {"ok": True, "artifact": None, "raw": {"runs": runs},
                "content": "\n".join(
                    f"{r.get('run_id')} [{r.get('status')}] {_research_run_line(r)}" for r in runs)}
    except Exception as e:
        return {"ok": False, "content": f"研究档案读取失败: {e}", "artifact": None}
```

3b. WW_TOOL_TABLE:在 `ww_picks_perf` 条目之后、`ww_capabilities` 之前插入(排序规律=功能批次时间序,自省两工具恒尾):

```python
    {"name": "ww_research_loop",
     "description":
         "自主研究回路(P2):goal 一句话 → 后端 LLM 生成因子工作流 → 真算指标(RankIC/样本外)→ "
         "未达标 LLM 批判改进 → 循环(服务端钳 ≤5 轮)。达标单因子自动存 factorlib 为 draft"
         "(不上选股货架,人审 POST /factorlib/promote 转正);逐轮落档;最佳图存工作流库。"
         "花 LLM 钱+可能写 draft,需确认。",
     "input_schema": {"type": "object", "properties": {
         "goal": {"type": "string", "description": "研究目标,如:找一个短周期反转因子"},
         "max_rounds": {"type": "integer", "default": 3, "description": "轮数上限(服务端钳 1-5)"},
         "min_rank_ic": {"type": "number", "default": 0.02, "description": "达标门:RankIC 下限(另要求 oos=robust)"},
         "universe": {"type": "string", "default": "csi300_active",
                      "description": "求值股票池(csi300_active/csi_fast/csi500/csi800/all/sample30)"},
         "wait": {"type": "boolean", "default": True},
         "poll_seconds": {"type": "number", "default": 15},
         "timeout_seconds": {"type": "number", "default": 1800}},
      "required": ["goal"]},
     "impl": research_loop_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/research/loop/start", "/research/loop/status", "/research/runs"]},
    {"name": "ww_research_runs",
     "description":
         "研究回路档案:无 run_id 列近期 run(状态/轮数/最佳指标/draft 名);带 run_id 出逐轮详情"
         "(诊断/指标/过门;规则兜底轮有「非 LLM」标注)。复盘研究成绩用它。",
     "input_schema": {"type": "object", "properties": {
         "run_id": {"type": "string", "description": "可选,某次 run 的 id(rr_ 开头)"},
         "limit": {"type": "integer", "default": 10}}},
     "impl": research_runs_impl, "cost": "instant", "confirm": False,
     "reachable": ["/research/runs", "/research/rounds"]},
```

3c. `workflow_critique_impl` 注记行替换:

原:`"⚠ 注意: metrics 为调用方自报,后端不复算(P2 将加强为后端取数)。")`
新:`"⚠ 注意: metrics 为调用方自报,后端不复算(要后端自算口径走 ww_research_loop 研究回路)。")`

3d. `ww_workflow_critique` 表条目 description 尾句 `"注意:指标由调用方自报,后端不复算。"` → `"注意:指标由调用方自报,后端不复算(后端自算走 ww_research_loop)。"`

- [ ] **Step 4: 实现 console/api.py `_SYSTEM_PROMPT`**

4a. 在 `另有:选股成绩单 ww_picks_perf(…)` 行后追加一行:

```
另有(P2 自主研究回路):发起研究回路 ww_research_loop(一句话目标→AI 生成因子工作流→后端真算指标→自我批判改进循环≤5轮,达标自动入 draft 待人审;花 LLM 钱+写 draft,需确认)、研究回路档案 ww_research_runs(列 run / run_id 逐轮详情)。
```

4b. 纪律区追加(纪律 13 之后):

```
14. 用户说「研究一个因子/让 AI 自己炼因子/自主研究」→ ww_research_loop(需确认;单飞,已在跑会拒);复盘研究历史/成绩 → ww_research_runs。draft 因子转正(上选股货架)是人的动作(POST /factorlib/promote),绝不擅自转正、绝不宣称 draft 已可用于选股。
```

- [ ] **Step 5: glmcp/README.md 计数**

L4 `暴露成 MCP 工具(**43 个**)` → `(**46 个**)`;L13 `(43 个 guanlan 工具)` → `(46 个 guanlan 工具)`。若文中有派生式说明,同步为 42−3+7=46。

- [ ] **Step 6: 跑测确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: 全绿(计数 42/67/46 三方一致;5 个新工具测试过)

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py guanlan_v2/glmcp/README.md tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(console): P2 T5 帷幄两工具 ww_research_loop/ww_research_runs(计数 42/67/46 四处同步+critique 注记兑现)"
```

---

### Task 6: 全量回归 + 真机 e2e(独立端口 9998)+ 还原现场

**Files:**
- Create(scratchpad,不入库): `<scratchpad>/p2_e2e.py`
- 无生产代码改动(除非 e2e 暴露 bug——修 bug 走正常 TDD+commit)

- [ ] **Step 1: 全量回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: ≥760+新增(约 33 条)全 passed,0 failed。若有失败先判定是否 P2 所致;非 P2 所致(并行会话基线漂移)原样上报不掩盖。

- [ ] **Step 2: 起独立测试 server(不碰生产 9999)**

```powershell
$env:GUANLAN_PORT="9998"; $env:PYTHONIOENCODING="utf-8"
Start-Process -WindowStyle Hidden G:/financial-analyst/.venv/Scripts/python.exe -ArgumentList "-c","from guanlan_v2.server import main; main()" -WorkingDirectory G:\guanlan-v2
# 就绪探测(重复直到 200):
Invoke-WebRequest http://127.0.0.1:9998/screen/health -UseBasicParsing | Select-Object -ExpandProperty StatusCode
```

- [ ] **Step 3: e2e 脚本(照 P1 p1_e2e_finish.py 形状写 `<scratchpad>/p2_e2e.py`,BASE=http://127.0.0.1:9998)**

检查项(全部诚实判定,PASS/FAIL 逐条打印,GBK 防护 `sys.stdout.reconfigure(encoding="utf-8")`):
1. `POST /research/loop/start` `{goal:"找一个短周期反转因子", max_rounds:2, universe:"csi_fast", freq:"month"}` → ok:true + run_id(rr_ 前缀)
2. 轮询 `GET /research/loop/status`(15s 间隔,≤15 分钟)到 phase∈{done,error};state.lines 有轮次足迹
3. `GET /research/runs` 首行=本 run:status∈{done,error};n_rounds/best_metrics/promoted/workflow_saved/memory_written/rounds_recorded 字段齐
4. `GET /research/rounds?run_id=` 逐轮行:diag/metrics/gate/critique_source/graph 齐;若有 rule 轮验证「(规则兜底·非 LLM) 」前缀
5. `GET /workflow/list` 出现 name 前缀 `研究·` 的条目(记 id 供还原)
6. 若 promoted.status=="draft":`GET /factorlib/list` 含该 name 且 status=draft;`GET /screen/factors` **不含**该 id;`POST /factorlib/promote {name}` → ok;再 `GET /screen/factors` 含该 id。若 skipped_multi/未达标/中断:如实记录=合法诚实结局,不算 FAIL
7. `var/console/memory.md` 含 `(研究·` keyed 行
8. 工具链冒烟:`GUANLAN_PORT=9998` 下进程内 `ct.research_runs_impl()` → content 含本 run_id
9. 单飞:回路在跑时再 POST start → already_running(若已收工则标 SKIP)

- [ ] **Step 4: 还原现场**

- 删 e2e 产出的 draft 因子 JSON(`guanlan_v2/factorlib/mined/lib_rl_*.json`)——**先 ls 确认只删本次 run_id 后缀的文件**
- `DELETE /workflow/delete/{id}` 删「研究·…」工作流库条目
- 编辑 `var/console/memory.md` 删本次 `(研究·找一个短周期反转因子)` 行
- `var/research_runs.jsonl`/`var/research_rounds.jsonl` **保留**(运行态产物已 gitignore,首批真档案有留存价值,报告注明)
- 杀 9998 测试 server 进程;`git status` 确认工作树只剩预期改动

- [ ] **Step 5: 汇报**

任务报告含:回归数字、e2e 逐条 PASS/FAIL、达标与否的真实结局(未达标=合法诚实结局照实报)、还原清单。

---

## 自审记录(writing-plans Self-Review)

1. **Spec 覆盖**:§1 架构=T1/T3/T4;§2 算法=T3;§3 档案+端点=T1/T4;§4 draft 门=T2;§5 两工具+教训+注记=T5(教训写入在 T3 `_write_lesson`);存图桥=T3 `_save_graph`;§6 红线分散在各任务测试;§7 测试=各任务+T6;零 env 开关=T4 明示。
2. **占位符**:唯一「省略号」在 T2 `_save_factor`(指令=从现文件 api.py:131-220 逐字拷贝+精确改动清单,非 TBD——拷贝优于重抄防漂移,现代码 102 行不宜全文重印)。
3. **类型一致性**:`_save_factor(body: SaveIn, store) -> dict` T2 定义 T3 消费;`run_research_loop(run_id, goal, max_rounds, min_rank_ic, universe, freq, start, end, progress)` T3 定义 T4 消费;`read_runs(limit, running_run_id)` T1 定义 T4 消费;`research_loop_impl/research_runs_impl` 签名与表条目 schema 对齐;`_self_post/_self_get` 打桩签名沿用仓例(`lambda path, payload, timeout=120` / `lambda path, timeout=30`)。
