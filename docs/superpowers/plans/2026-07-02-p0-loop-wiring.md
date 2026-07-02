# P0 闭环接线 + 诚实收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给选股结果建档案(picks 落盘)、给帷幄补 7 个薄工具(读取面+regen 触发)、MCP 诚实收尾(bg-spawn 补测试+排除 ww_seats_bind)。

**Architecture:** 新纯函数模块 `guanlan_v2/screen/picks.py`(append-only JSONL,模块级路径常量);`/screen/run` v4 主路径尾部薄钩子 + `picks_recorded` 显形;7 个工具全部照抄 console/tools.py 既有 `_self_get/_self_post` 薄壳模式;MCP 侧只动 `_EXCLUDED` 与测试/README。

**Tech Stack:** FastAPI + pydantic(既有)、pytest;python 一律用 `G:/financial-analyst/.venv/Scripts/python.exe`。

**Spec:** `docs/superpowers/specs/2026-07-02-p0-loop-wiring-design.md`

## Global Constraints

- 分支:`git checkout -b p0-loop-wiring`(从当前 main;计划写就时 main=69008cf)。每任务完成即 commit。
- 测试命令统一:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file>::<test> -v`(conftest 已钉 engine 路径,无需 PYTHONPATH)。全量回归基线 **676 passed**(本机数据在位)。
- **守护计数精确值**:WW_TOOL_TABLE 32→**39**;CONSOLE_ALLOWED 57→**64**;MCP 工具数 37→(Task 3 完成时 **44**)→(Task 4 排除 ww_seats_bind 后 **43**)。
- **四处同步铁律**(加任何 ww_ 工具必须同步四处):①WW_TOOL_TABLE 注册 ②CONSOLE_ALLOWED(自动派生,无需手改)③`console/api.py _SYSTEM_PROMPT` 具名介绍 ④守护计数测试(tests/test_console_tools.py 三处断言 + tests/test_guanlan_mcp.py 两处断言 + `test_ww_reachable_endpoints_matches_expected` 期望集)。
- **红线(逐字来自 spec)**:落盘失败显形不阻断;无任何假成功;critique 摘要必注明指标自报;regen 过确认门;绝不自动采纳;不碰交易信号。零前端改动;不改选股算法/v4 模型。
- 端点风格:闭包内 def、依赖延迟 import、`JSONResponse({"ok": ...})`、诚实失败 HTTP 200 + `ok:False/reason`。
- impl 风格:返回 `{"ok": bool, "content": str, "artifact": None|dict, "raw": ...}`;HTTP 异常由 `_self_get/_self_post` 抛 `RuntimeError`,impl 捕获转 `ok:False`。`raw` 不塞超大对象(tsic 只放 summary)。
- Windows 噪声:git 的 LF→CRLF warning 全部忽略;GateGuard hook 会在首个 Bash/每文件首次编辑时要求陈述 facts——照要求陈述后重试同一操作即可。
- 运行态文件 `var/screen_picks.jsonl` 不入 git(var/ 已 ignore)。

---

## File Structure(全景)

| 文件 | 动作 | 职责 |
|---|---|---|
| `guanlan_v2/screen/picks.py` | **新建** | picks 档案纯函数(append_pick/read_picks/PICKS_PATH) |
| `guanlan_v2/screen/api.py` | 修改 | ScreenIn+2 字段;`_record_picks` 钩子;GET /screen/picks |
| `guanlan_v2/console/tools.py` | 修改 | 7 个 impl + 7 条注册 + screen_impl/schema 透传 snapshot/note |
| `guanlan_v2/console/api.py` | 修改 | _SYSTEM_PROMPT 加一行「另有」+ 纪律 13 |
| `guanlan_v2/glmcp/tooltable.py` | 修改 | _EXCLUDED += ww_seats_bind |
| `guanlan_v2/glmcp/README.md` | 修改 | 计数 43 + bg 真跑说明 + seats_bind 排除说明 |
| `tests/test_screen_picks.py` | **新建** | picks 模块单测 |
| `tests/test_screen_api.py` | 修改 | /screen/run 落档集成测 ×3 |
| `tests/test_console_tools.py` | 修改 | 7 impl 单测 + 守护计数 39/64 + 期望端点集 |
| `tests/test_guanlan_mcp.py` | 修改 | 计数 37→44→43 + 排除断言 + bg-spawn 四分支测 |

---

### Task 1: picks 档案纯函数模块

**Files:**
- Create: `guanlan_v2/screen/picks.py`
- Test: `tests/test_screen_picks.py`(新建)

**Interfaces:**
- Produces: `PICKS_PATH: Path`(模块常量,测试 monkeypatch 用)、`append_pick(record: dict) -> bool`、`read_picks(snapshot_only: bool = False, limit: int = 50) -> list[dict]`。Task 2 的 `_record_picks` 与 GET /screen/picks 消费。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_screen_picks.py`:

```python
"""picks 档案纯函数单测(P0 §1)。全部经 monkeypatch PICKS_PATH 指 tmp,零生产污染。"""
import json


def test_append_and_read_roundtrip(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "p.jsonl")
    assert picks.append_pick({"ts": "t1", "snapshot": False, "note": None}) is True
    assert picks.append_pick({"ts": "t2", "snapshot": True, "note": "官方"}) is True
    rows = picks.read_picks(limit=10)
    assert [r["ts"] for r in rows] == ["t2", "t1"]          # 新在前


def test_snapshot_only_filter(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "p.jsonl")
    picks.append_pick({"ts": "a", "snapshot": False})
    picks.append_pick({"ts": "b", "snapshot": True})
    rows = picks.read_picks(snapshot_only=True, limit=10)
    assert len(rows) == 1 and rows[0]["ts"] == "b"


def test_dirty_line_skipped_and_limit(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    p = tmp_path / "p.jsonl"
    monkeypatch.setattr(picks, "PICKS_PATH", p)
    picks.append_pick({"ts": "a"})
    with p.open("a", encoding="utf-8") as f:
        f.write("{oops 不是JSON\n")
    picks.append_pick({"ts": "b"})
    picks.append_pick({"ts": "c"})
    rows = picks.read_picks(limit=2)
    assert [r["ts"] for r in rows] == ["c", "b"]            # 坏行跳过,limit 生效


def test_missing_file_returns_empty(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "nope" / "p.jsonl")
    assert picks.read_picks() == []


def test_append_failure_returns_false(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")               # 父路径是文件 → mkdir 必炸
    monkeypatch.setattr(picks, "PICKS_PATH", blocker / "p.jsonl")
    assert picks.append_pick({"ts": "a"}) is False          # 吞异常回 False,绝不抛


def test_chinese_not_escaped(monkeypatch, tmp_path):
    import guanlan_v2.screen.picks as picks
    p = tmp_path / "p.jsonl"
    monkeypatch.setattr(picks, "PICKS_PATH", p)
    picks.append_pick({"name": "宁德时代"})
    assert "宁德时代" in p.read_text(encoding="utf-8")       # ensure_ascii=False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_picks.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'guanlan_v2.screen.picks'`

- [ ] **Step 3: 写实现** — 新建 `guanlan_v2/screen/picks.py`:

```python
# -*- coding: utf-8 -*-
"""选股 picks 档案:每次 /screen/run 主路径落一行(append-only JSONL)——闭环的「跟踪对象」。

snapshot=true 的行是「正式选股」(P1 收益跟踪只认它们);其余为实验记录。
纯函数 + 模块级路径常量(便于测试 monkeypatch,对齐 seats/api.py _LEDGER_LOG 先例)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

PICKS_PATH = Path(__file__).resolve().parents[2] / "var" / "screen_picks.jsonl"


def append_pick(record: Dict[str, Any]) -> bool:
    """append 一行;任何异常吞掉回 False(绝不阻断选股),由调用方以 picks_recorded 显形。"""
    try:
        PICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with PICKS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001 — 落盘失败不阻断选股,调用方 picks_recorded=False 显形
        return False


def read_picks(snapshot_only: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    """读尾部 limit 条(新在前);坏行跳过(诚实容错);snapshot_only 只回正式选股行。"""
    cap = max(1, min(int(limit or 50), 500))
    out: List[Dict[str, Any]] = []
    try:
        if not PICKS_PATH.exists():
            return out
        for ln in reversed(PICKS_PATH.read_text(encoding="utf-8").splitlines()):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001 — 坏行跳过
                continue
            if snapshot_only and not r.get("snapshot"):
                continue
            out.append(r)
            if len(out) >= cap:
                break
    except Exception:  # noqa: BLE001 — 读失败 = 已收集的(或空),诚实降级
        return out
    return out
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_picks.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/screen/picks.py tests/test_screen_picks.py
git commit -m "feat(screen): picks 档案纯函数模块(append-only JSONL·snapshot 标记·坏行容错·失败回 False)"
```

---

### Task 2: /screen/run 落档接线 + GET /screen/picks

**Files:**
- Modify: `guanlan_v2/screen/api.py`(三处:ScreenIn 约 41-62 行、v4 返回块约 941-966 行、build_screen_router 内新 GET 端点)
- Test: `tests/test_screen_api.py`(追加 3 个测试)

**Interfaces:**
- Consumes: Task 1 的 `append_pick/read_picks/PICKS_PATH`。
- Produces: `/screen/run` v4 主路径响应新增顶层键 `picks_recorded: bool`;`GET /screen/picks?snapshot_only=<0|1>&limit=<N>` → `{ok, items, n, path}`;`ScreenIn.snapshot: bool=False`、`ScreenIn.note: Optional[str]=None`。Task 3 的 ww_screen_run 透传依赖这两个字段名。

**背景**(实现者须知):`/screen/run` 的 v4 主路径在模块级函数 `_screen_via_v4(body)`(api.py:686 起)内,因子混合(blend)也发生在其中;`screen_run` 端点(api.py:1067)先调它,返回 None 才落入函数体内联的玩具回退路径(api.py:1076 起)。**只在 v4 主路径落档;回退路径不落**(spec §1)。实际生效模型在变量 `_mid`,产物日在 `rdate`(api.py:694-704)。

- [ ] **Step 1: 写失败测试** — 在 `tests/test_screen_api.py` 文件尾部追加:

```python
# ── P0 §1: picks 落档 ──────────────────────────────────────────────────────

def test_screen_run_records_picks(monkeypatch, tmp_path):
    """v4 主路径成功 → picks 落档一行 + 响应 picks_recorded:true + GET /screen/picks 读回。"""
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "picks.jsonl")
    c = _client()
    j = c.post("/screen/run", json={**_CFG, "snapshot": True, "note": "t_p0"}).json()
    assert j["ok"] is True and j["source"] == "v4_ranking"
    assert j["picks_recorded"] is True
    rows = picks.read_picks(snapshot_only=True, limit=5)
    assert rows and rows[0]["note"] == "t_p0" and rows[0]["snapshot"] is True
    assert rows[0]["model"] == j["model"] and rows[0]["date"] == j["date"]
    assert rows[0]["picks"] and rows[0]["picks"][0]["rank"] == 1
    assert rows[0]["picks"][0]["code"] and "score" in rows[0]["picks"][0]
    assert rows[0]["topN"] == _CFG["topN"] and "constraints" in rows[0]
    g = c.get("/screen/picks?snapshot_only=1&limit=3").json()
    assert g["ok"] is True and g["n"] >= 1 and g["items"][0]["note"] == "t_p0"


def test_screen_run_picks_failure_is_visible(monkeypatch, tmp_path):
    """落盘失败 → 选股照常成功,但 picks_recorded:false 显形(红线:失败显形不阻断)。"""
    import guanlan_v2.screen.picks as picks
    monkeypatch.setattr(picks, "append_pick", lambda rec: False)
    j = _client().post("/screen/run", json=_CFG).json()
    assert j["ok"] is True and j["picks_recorded"] is False


def test_screen_fallback_path_does_not_record(monkeypatch):
    """玩具回退路径(非生产口径)不落档(spec §1)。"""
    import guanlan_v2.screen.api as api
    import guanlan_v2.screen.picks as picks
    calls = {"n": 0}
    def _spy(rec):
        calls["n"] += 1
        return True
    monkeypatch.setattr(picks, "append_pick", _spy)
    monkeypatch.setattr(api, "_screen_via_v4", lambda body: None)
    j = _client().post("/screen/run", json=_CFG).json()
    assert j["ok"] is True and calls["n"] == 0 and "picks_recorded" not in j
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py -k picks -v`
Expected: 3 FAIL(`KeyError: 'picks_recorded'` / 断言失败)

- [ ] **Step 3: 改 ScreenIn** — 在 `guanlan_v2/screen/api.py` 的 ScreenIn 类(`freq: str = "day"` 行后)追加两字段:

```python
    snapshot: bool = False       # P0:标记「正式选股」落 picks 档案(P1 收益跟踪只认 snapshot 行)
    note: Optional[str] = None   # P0:选股备注,随 picks 档案落盘
```

- [ ] **Step 4: 加 `_record_picks` 模块级 helper** — 放在 `_resolve_model_id`(api.py:673-683)之后:

```python
def _record_picks(body: "ScreenIn", resp: Dict[str, Any], model_id: str, rdate: str) -> bool:
    """v4 主路径选股结果 → picks 档案一行(P0 §1)。失败回 False,由 picks_recorded 显形。"""
    from datetime import datetime as _dt
    from guanlan_v2.screen import picks as _picks
    chosen = resp.get("chosen") or []
    rec = {
        "ts": _dt.now().isoformat(timespec="seconds"),
        "date": rdate,
        "snapshot": bool(getattr(body, "snapshot", False)),
        "note": getattr(body, "note", None),
        "model": model_id,
        "pool": body.pool,
        "alpha": body.blend,
        "factors": [{"id": f.id, "w": f.w} for f in (body.factors or [])],
        "topN": body.topN,
        "n_universe": len(resp.get("pool") or []),
        "picks": [{"code": (x.get("s") or {}).get("code"),
                   "name": (x.get("s") or {}).get("name"),
                   "score": x.get("score"), "rank": i + 1}
                  for i, x in enumerate(chosen)],
        "constraints": {"liqMin": body.liqMin, "mlStatus": body.mlStatus,
                        "industryNeutral": body.industryNeutral, "indCap": body.indCap,
                        "exclST": body.exclST, "exclHalt": body.exclHalt,
                        "exclLimit": body.exclLimit, "exclNew": body.exclNew},
    }
    return _picks.append_pick(rec)
```

- [ ] **Step 5: 钩进 v4 返回块** — 把 `_screen_via_v4` 尾部的 `return JSONResponse({...})`(api.py:941-966,以 `"ok": True, "source": "v4_ranking"` 开头的那个)改为先存 dict、落档、再包 JSONResponse。**dict 字面量内容逐字不动**,只做包装:

```python
    resp = {
        "ok": True,
        "source": "v4_ranking",
        # …(原 JSONResponse 里的全部键值,逐字保留,一个不动)…
        "note": "L1=v4 排名 · L2 主线 · L3 量能 · L4 九视角(逐行 views)· L5 评级/护盾/≤5 收敛(decision)",
    }
    resp["picks_recorded"] = _record_picks(body, resp, _mid, rdate)   # P0:落档显形,失败不阻断
    return JSONResponse(resp)
```

- [ ] **Step 6: 加 GET /screen/picks** — 在 `build_screen_router` 内、`/models` 端点旁添加:

```python
    @router.get("/picks")
    def screen_picks(snapshot_only: int = 0, limit: int = 50):
        """picks 档案读回(P0;P1 收益跟踪/前端将来消费)。坏行已在 read_picks 内跳过。"""
        from guanlan_v2.screen import picks as _picks
        items = _picks.read_picks(snapshot_only=bool(snapshot_only), limit=limit)
        return JSONResponse({"ok": True, "items": items, "n": len(items),
                             "path": str(_picks.PICKS_PATH)})
```

- [ ] **Step 7: 跑新测试 + screen 全文件**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py tests/test_screen_picks.py -v`
Expected: 全绿(原有测试不破:`test_run_uses_vendored_v4` 等照过——新键是加法)

- [ ] **Step 8: Commit**

```bash
git add guanlan_v2/screen/api.py tests/test_screen_api.py
git commit -m "feat(screen): /screen/run v4主路径落 picks 档案(snapshot/note入参·picks_recorded显形·回退路径不落)+ GET /screen/picks"
```

---

### Task 3: 帷幄 7 个薄工具 + ww_screen_run 透传 + 提示词 + 守护计数

**Files:**
- Modify: `guanlan_v2/console/tools.py`(7 个 impl + 7 条注册 + screen_impl/schema)
- Modify: `guanlan_v2/console/api.py`(_SYSTEM_PROMPT)
- Test: `tests/test_console_tools.py`(7 个 impl 单测 + 3 处计数 + 期望端点集)、`tests/test_guanlan_mcp.py`(计数 37→44)

**Interfaces:**
- Consumes: 既有 `_self_get(path, timeout=30)` / `_self_post(path, payload, timeout=120)`(HTTP 错抛 RuntimeError);Task 2 的 `/screen/run` 响应键 `picks_recorded`。
- Produces: 7 个新工具名(`ww_ledger_state / ww_calibration / ww_seats_runs / ww_model_health / ww_factor_tsic / ww_workflow_critique / ww_regen`),Task 4 的 MCP 表自动继承。

**后端响应契约**(实现者不用再翻后端,以下为实测摘录口径):
- `GET /seats/ledger/state` → `{ok, opened, start_date, init_cash, cash, positions[{code,name,qty,avg_cost,last_close,mkt_value,upl}], n_positions, covered, equity(可null), equity_date, days, realized, n_closed, win_rate(可null)}`;未开账 `{ok:true, opened:false}`。
- `GET /seats/calibration?horizon=N` → `{ok, horizon, total_decides, mature, buckets, note}`;失败 `{ok:false, reason}`。buckets 行键名以 `guanlan_v2/seats/calibration.py` 的 `calibration_table` 为准(实现时读一眼该函数,下方 impl 已做防御性取键)。
- `GET /seats/runs?limit=N&code=` → `{ok, runs:[run头(字段透传自由)], total}`。
- `GET /screen/health` → `{ok, source, v4_ranking:{date,rows,stale_days}, market_breadth|null, model_health|null}`;失败 `{ok:false, reason, v4_ranking:null}`。model_health 内部键以 `guanlan_v2/strategy/model_health.py` 的 `load_health_summary` 为准(impl 按键防御渲染)。
- `POST /factor/tsic`(FactorTsicIn:`expr_or_name, fwd_days=20, direction, codes, universe, start, end, benchmark, leader…`)→ `{ok, universe, method, expr_or_name, fwd_days, direction, codes_tsic:[{code,tsic,n,…}], summary:{n_codes,mean_tsic,median_tsic,pos_ratio,…}, warnings, status}`。
- `POST /workflow/critique`(CritiqueIn:`goal, metrics, graph`)→ `{ok, diagnosis, graph:{nodes,edges}, source:"llm"|"rule", [llm_error]}`(nodes/edges 包在 `graph` 键下)。
- `POST /screen/regen`(`{end?}`)→ `{ok, started, state}` 或 `{ok:false, reason:"already_running", state}`;`GET /screen/regen/status` → `{ok, state:{running,phase,label,step,total,ok,error,new_date,elapsed_sec,…}}`;完成态 `running:false && phase in ("done","error")`。

- [ ] **Step 1: 写失败测试** — 在 `tests/test_console_tools.py` 尾部追加(import 段已有 `ct`):

```python
# ── P0 §2: 7 个闭环读取/触发薄工具 ─────────────────────────────────────────

def test_ledger_state_impl(monkeypatch):
    fake = {"ok": True, "opened": True, "start_date": "2026-06-12", "init_cash": 1000000.0,
            "cash": 400000.0, "n_positions": 2, "covered": 1, "equity": None, "equity_date": None,
            "days": [], "realized": 12000.0, "n_closed": 3, "win_rate": 2 / 3,
            "positions": [{"code": "SZ300750", "name": "宁德时代", "qty": 100, "avg_cost": 180.0,
                           "last_close": 190.0, "mkt_value": 19000.0, "upl": 1000.0},
                          {"code": "SH600519", "name": "贵州茅台", "qty": 10, "avg_cost": 1500.0,
                           "last_close": None, "mkt_value": None, "upl": None}]}
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: fake)
    res = ct.ledger_state_impl()
    assert res["ok"] is True
    assert "缺价" in res["content"]                      # equity=null 诚实显形
    assert "67%" in res["content"] and "宁德时代" in res["content"]


def test_ledger_state_impl_unopened(monkeypatch):
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: {"ok": True, "opened": False})
    res = ct.ledger_state_impl()
    assert res["ok"] is True and "未开账" in res["content"]


def test_calibration_impl(monkeypatch):
    sent = {}
    fake = {"ok": True, "horizon": 10, "total_decides": 30, "mature": 12,
            "buckets": [{"bucket": "60-70", "n": 3, "hit_rate": 0.667},
                        {"bucket": "70-80", "n": 9, "hit_rate": 0.556}],
            "note": "口径:asof收盘进+N根收盘出"}
    def fake_get(path, timeout=30):
        sent["path"] = path
        return fake
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.calibration_impl(horizon=10)
    assert sent["path"] == "/seats/calibration?horizon=10"
    assert res["ok"] is True and "成熟 12" in res["content"]
    assert "样本不足" in res["content"]                   # n=3 < 5 档注明


def test_seats_runs_impl(monkeypatch):
    fake = {"ok": True, "total": 1, "runs": [
        {"run_id": "r_1", "code": "SH605358", "ts": "2026-06-13T10:00:00",
         "start": "2026-03-01", "end": "2026-06-10", "n_buy": 7, "n_sell": 5, "n_hold": 60}]}
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: fake)
    res = ct.seats_runs_impl(limit=5)
    assert res["ok"] is True and "r_1" in res["content"] and "SH605358" in res["content"]


def test_model_health_impl(monkeypatch):
    fake = {"ok": True, "source": "vendored",
            "v4_ranking": {"date": "2026-07-01", "rows": 5027, "stale_days": 1},
            "market_breadth": {"as_of": "2026-07-01", "stage": "回暖", "cached": True},
            "model_health": None}
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: fake)
    res = ct.model_health_impl()
    assert res["ok"] is True and "2026-07-01" in res["content"] and "5027" in res["content"]
    assert "诚实缺席" in res["content"]                   # model_health=None 显形


def test_factor_tsic_impl(monkeypatch):
    sent = {}
    fake = {"ok": True, "summary": {"n_codes": 1, "mean_tsic": 0.119, "median_tsic": 0.119,
                                    "pos_ratio": 1.0, "fwd_days": 20},
            "codes_tsic": [{"code": "SH605358", "tsic": 0.1192, "n": 220}]}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path
        sent.update(payload)
        return fake
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.factor_tsic_impl(expr="correlation(returns, idx_ret, 20)", code="SH605358")
    assert sent["path"] == "/factor/tsic" and sent["codes"] == ["SH605358"]
    assert sent["expr_or_name"] == "correlation(returns, idx_ret, 20)"
    assert res["ok"] is True and "0.119" in res["content"]
    assert "codes_tsic" not in (res.get("raw") or {})     # raw 瘦身:只带 summary


def test_factor_tsic_impl_requires_expr():
    res = ct.factor_tsic_impl(expr="")
    assert res["ok"] is False and "表达式" in res["content"]


def test_workflow_critique_impl(monkeypatch):
    fake = {"ok": True, "diagnosis": "RankIC 为负,已取负", "source": "rule",
            "graph": {"nodes": [{"id": "n1"}], "edges": []}}
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120: fake)
    res = ct.workflow_critique_impl(goal="g", graph={"nodes": [{"id": "n0"}], "edges": []},
                                    metrics={"rank_ic": -0.02})
    assert res["ok"] is True and "非LLM" in res["content"]          # source=rule 诚实标注
    assert "自报" in res["content"]                                  # 红线:必注明指标自报
    res2 = ct.workflow_critique_impl(goal="g", graph={})
    assert res2["ok"] is False                                       # 缺图拒绝


def test_regen_impl_start_and_wait(monkeypatch):
    calls = {"n": 0}
    def fake_post(path, payload, timeout=120):
        assert path == "/screen/regen"
        return {"ok": True, "started": True, "state": {"running": True, "phase": "starting"}}
    def fake_get(path, timeout=30):
        calls["n"] += 1
        done = calls["n"] >= 2
        return {"ok": True, "state": {"running": (not done), "phase": ("done" if done else "v4"),
                                      "ok": done, "new_date": "2026-07-02", "elapsed_sec": 300}}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.regen_impl(wait=True, poll_seconds=0, timeout_seconds=60)
    assert res["ok"] is True and "2026-07-02" in res["content"]


def test_regen_impl_already_running(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": False, "reason": "already_running",
                         "state": {"phase": "v4", "step": 3}})
    res = ct.regen_impl(wait=False)
    assert res["ok"] is False and "already_running" in res["content"]


def test_screen_impl_passes_snapshot_note(monkeypatch):
    sent = {}
    fake = {"ok": True, "chosen": [], "picks_recorded": True, "model": "prod"}
    def fake_post(path, payload, timeout=120):
        sent.update(payload)
        return fake
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.screen_impl(snapshot=True, note="正式")
    assert sent["snapshot"] is True and sent["note"] == "正式"
    assert "picks 已落档" in res["content"]
    fake2 = {"ok": True, "chosen": [], "picks_recorded": False, "model": "prod"}
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120: fake2)
    res2 = ct.screen_impl()
    assert "落盘失败" in res2["content"]                  # 失败显形透传
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py -k "ledger_state or calibration_impl or seats_runs or model_health or factor_tsic or workflow_critique or regen_impl or passes_snapshot" -v`
Expected: 全 FAIL(`AttributeError: module ... has no attribute 'ledger_state_impl'` 等)

- [ ] **Step 3: 写 7 个 impl** — 加进 `guanlan_v2/console/tools.py`(放在 model_set_default_impl 之后、cards 区之前;风格对齐既有 impl):

```python
# ── P0 §2: 闭环读取面 + regen 触发(全部薄壳,无新算法)─────────────────────

def ledger_state_impl() -> Dict[str, Any]:
    """实盘台账快照:组合持仓/已实现盈亏/胜率/MTM 权益(缺价诚实置空)。"""
    try:
        r = _self_get("/seats/ledger/state")
    except Exception as e:
        return {"ok": False, "content": f"台账读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"台账读取失败: {r.get('reason') or r}", "artifact": None, "raw": r}
    if not r.get("opened"):
        return {"ok": True, "content": "台账未开账(尚无实盘组合记录)。", "artifact": None, "raw": r}
    eq = r.get("equity")
    eq_line = (f"MTM权益 {eq:,.0f}(估值日 {r.get('equity_date')})"
               if isinstance(eq, (int, float)) else "MTM权益 缺价不可估(诚实置空)")
    wr = r.get("win_rate")
    wr_line = f"{float(wr) * 100:.0f}%" if isinstance(wr, (int, float)) else "—(无已了结)"
    pos_lines = []
    for p in (r.get("positions") or [])[:12]:
        upl = p.get("upl")
        tail = (f" 现价 {p.get('last_close')} 浮盈 {upl:+,.0f}"
                if isinstance(upl, (int, float)) else " 现价缺")
        pos_lines.append(f"{p.get('code')} {p.get('name', '')} 持 {p.get('qty')} 成本 {p.get('avg_cost')}{tail}")
    content = (f"实盘台账(开账 {r.get('start_date')} · 组合一本账):现金 {r.get('cash'):,.0f} · "
               f"持仓 {r.get('n_positions')} 只(估到价 {r.get('covered')})· {eq_line}\n"
               f"已实现盈亏 {r.get('realized'):+,.0f} · 已了结 {r.get('n_closed')} 笔 · 胜率 {wr_line}"
               + ("\n" + "\n".join(pos_lines) if pos_lines else ""))
    return {"ok": True, "content": content, "artifact": None, "raw": r}


def calibration_impl(horizon: int = 5) -> Dict[str, Any]:
    """置信度校准全表:各置信档的真实 N 日方向命中率(评估自己研判先看它)。"""
    hz = max(1, min(int(horizon or 5), 20))
    try:
        r = _self_get(f"/seats/calibration?horizon={hz}")
    except Exception as e:
        return {"ok": False, "content": f"校准读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"校准读取失败: {r.get('reason')}", "artifact": None, "raw": r}
    def _fmt(b: Dict[str, Any]) -> str:
        seg = b.get("bucket") or b.get("range") or b.get("label") or "?"
        n, hr = b.get("n"), b.get("hit_rate")
        hr_s = f"{float(hr) * 100:.0f}%" if isinstance(hr, (int, float)) else "—"
        low = "(样本不足)" if isinstance(n, int) and n < 5 else ""
        return f"{seg}: n={n} 命中 {hr_s}{low}"
    bks = r.get("buckets") or []
    content = (f"置信校准(horizon={r.get('horizon')}日):研判 {r.get('total_decides')} 条 · 成熟 {r.get('mature')} 条\n"
               + ("; ".join(_fmt(b) for b in bks) if bks else "(暂无成熟样本)")
               + f"\n口径: {r.get('note')}")
    return {"ok": True, "content": content, "artifact": None, "raw": r}


def seats_runs_impl(code: str = "", limit: int = 10) -> Dict[str, Any]:
    """落子回测 run 历史头(新在前;code 数字核匹配)。"""
    cap = max(1, min(int(limit or 10), 50))
    q = f"/seats/runs?limit={cap}" + (f"&code={(code or '').strip()}" if (code or "").strip() else "")
    try:
        r = _self_get(q)
    except Exception as e:
        return {"ok": False, "content": f"回测 run 列表读取失败: {e}", "artifact": None}
    runs = r.get("runs") or []
    if not runs:
        return {"ok": True, "content": "暂无落子回测 run 记录。", "artifact": None, "raw": r}
    lines = []
    for x in runs:
        seg = f"{x.get('run_id')} · {x.get('code')} · {str(x.get('ts', ''))[:16]}"
        if x.get("start") or x.get("end"):
            seg += f" · {x.get('start')}→{x.get('end')}"
        if x.get("n_buy") is not None:
            seg += f" · 买{x.get('n_buy')}/卖{x.get('n_sell')}/观{x.get('n_hold')}"
        lines.append(seg)
    return {"ok": True, "content": f"落子回测 run 头(近 {len(runs)} 条,新在前):\n" + "\n".join(lines),
            "artifact": None, "raw": r}


def model_health_impl() -> Dict[str, Any]:
    """模型体检:v4 产物新鲜度 + 市场宽度 as_of + 模型健康(vintage OOS IC/告警)。"""
    try:
        r = _self_get("/screen/health")
    except Exception as e:
        return {"ok": False, "content": f"模型体检读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"模型体检读取失败: {r.get('reason')}", "artifact": None, "raw": r}
    v4 = r.get("v4_ranking") or {}
    lines = [f"v4 排名产物: date {v4.get('date')} · {v4.get('rows')} 行 · 陈旧 {v4.get('stale_days')} 天"]
    mb = r.get("market_breadth")
    if mb:
        lines.append(f"市场宽度: as_of {mb.get('as_of')} · 阶段 {mb.get('stage')}")
    mh = r.get("model_health")
    if isinstance(mh, dict) and mh:
        for k, v in mh.items():          # load_health_summary 键随产物在场程度变化 → 按在场键渲染
            if v is not None and not isinstance(v, (list, dict)):
                lines.append(f"{k}: {v}")
        for k in ("alert", "trend", "vintage"):
            if isinstance(mh.get(k), (list, dict)) and mh.get(k):
                lines.append(f"{k}: {json.dumps(mh[k], ensure_ascii=False, default=str)[:300]}")
    else:
        lines.append("模型体检块: 无(产物缺失,诚实缺席)")
    return {"ok": True, "content": "模型体检:\n" + "\n".join(lines), "artifact": None, "raw": r}


def factor_tsic_impl(expr: str = "", code: str = "", codes: Optional[List[str]] = None,
                     universe: str = "", fwd_days: int = 20,
                     start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """个股时序 IC:逐票 Spearman(因子值ₜ, 自身未来收益ₜ)——单票/小池的正确口径。"""
    ex = (expr or "").strip()
    if not ex:
        return {"ok": False, "content": "请给因子表达式/注册名 expr(先用 ww_factor_fields 查合法字段)",
                "artifact": None}
    body: Dict[str, Any] = {"expr_or_name": ex, "fwd_days": int(fwd_days or 20)}
    cs = [str(c).strip() for c in ([code] if (code or "").strip() else (codes or [])) if str(c).strip()]
    if cs:
        body["codes"] = cs
    elif (universe or "").strip():
        body["universe"] = universe.strip()
    if start:
        body["start"] = start
    if end:
        body["end"] = end
    try:
        r = _self_post("/factor/tsic", body, timeout=300)
    except Exception as e:
        return {"ok": False, "content": f"时序IC计算失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"时序IC计算失败: {r.get('reason') or r.get('status')}",
                "artifact": None, "raw": {"summary": r.get("summary")}}
    s = r.get("summary") or {}
    rows = r.get("codes_tsic") or []
    per = "; ".join(f"{x.get('code')}: tsic {x.get('tsic'):+.4f}(n={x.get('n')})"
                    for x in rows[:5] if isinstance(x.get("tsic"), (int, float)))
    content = (f"个股时序IC({ex} · 前向{s.get('fwd_days')}日 · {s.get('n_codes')}票):"
               f"均值 {s.get('mean_tsic')} · 中位 {s.get('median_tsic')} · 正占比 {s.get('pos_ratio')}"
               + (f"\n{per}" if per else "")
               + "\n口径: 逐票 Spearman(因子值ₜ, 自身未来收益ₜ),因子取 RAW 值")
    return {"ok": True, "content": content, "artifact": None,
            "raw": {"summary": s, "n_rows": len(rows)}}   # raw 瘦身:codes_tsic 全池行不进上下文


def workflow_critique_impl(goal: str = "", graph: Optional[Dict[str, Any]] = None,
                           metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """AI 批判环:目标+当前图+回测指标 → 诊断+改进图(LLM,失败规则兜底)。"""
    g = graph if isinstance(graph, dict) else {}
    if not (g.get("nodes") or g.get("edges")):
        return {"ok": False, "artifact": None,
                "content": "请给当前工作流 graph({nodes,edges});metrics 传该图的真实回测指标"
                           "(rank_ic/sharpe/ann_return/oos_verdict 等)"}
    body = {"goal": (goal or "").strip(), "graph": g, "metrics": metrics or {}}
    try:
        r = _self_post("/workflow/critique", body, timeout=120)
    except Exception as e:
        return {"ok": False, "content": f"批判环调用失败: {e}", "artifact": None}
    gr = r.get("graph") or {}
    src = r.get("source") or "?"
    content = (f"AI 批判({'真·LLM' if src == 'llm' else '规则兜底·非LLM'}):{r.get('diagnosis')}\n"
               f"改进图: {len(gr.get('nodes') or [])} 节点(nodes/edges 见 raw.graph)\n"
               "⚠ 注意: metrics 为调用方自报,后端不复算(P2 将加强为后端取数)。")
    return {"ok": bool(r.get("ok")), "content": content, "artifact": None, "raw": r}


def regen_impl(end: str = "", wait: bool = True,
               poll_seconds: float = 15.0, timeout_seconds: float = 600.0) -> Dict[str, Any]:
    """三产物(breadth/mainline/v4)再生:后台子进程 ~5min;wait=true 轮询到完成。"""
    body: Dict[str, Any] = {}
    if (end or "").strip():
        body["end"] = end.strip()
    try:
        r = _self_post("/screen/regen", body)
    except Exception as e:
        return {"ok": False, "content": f"再生启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        st = r.get("state") or {}
        return {"ok": False, "artifact": None, "raw": r,
                "content": f"再生未启动: {r.get('reason')}(phase={st.get('phase')} step={st.get('step')})"}
    if not wait:
        return {"ok": True, "artifact": None, "raw": r,
                "content": "三产物再生已启动(后台~5分钟)。稍后 ww_regen wait=true 续查,或 ww_model_health 验新鲜度。"}
    import time as _time
    deadline = _time.time() + float(timeout_seconds or 600.0)
    state: Dict[str, Any] = {}
    while _time.time() <= deadline:
        try:
            s = _self_get("/screen/regen/status")
        except Exception as e:
            return {"ok": False, "content": f"再生状态读取失败: {e}", "artifact": None, "raw": {"state": state}}
        state = s.get("state") or {}
        if not state.get("running") and state.get("phase") in ("done", "error"):
            ok = bool(state.get("ok"))
            return {"ok": ok, "artifact": None, "raw": {"state": state},
                    "content": (f"再生完成: 新数据日 {state.get('new_date')} · 用时 {state.get('elapsed_sec')}s"
                                if ok else f"再生失败: {state.get('error')}")}
        if poll_seconds:
            _time.sleep(float(poll_seconds))
    return {"ok": False, "artifact": None, "raw": {"state": state},
            "content": "再生轮询超时: 后端可能仍在跑,稍后用 ww_model_health 查产物新鲜度验新。"}
```

- [ ] **Step 4: screen_impl 透传 snapshot/note** — 三处小改:
  1. 签名尾部加 `snapshot: Optional[bool] = None, note: Optional[str] = None`;
  2. 「仅显式提供时下送」的 for 循环元组(tools.py:249-251 附近)追加 `("snapshot", snapshot), ("note", note)`;
  3. return 前(`_model_line` 之后)追加 picks 显形行,并把 content 改为 `summarize_screen(r) + _unsup_line + _model_line + _picks_line`:

```python
    _pr = r.get("picks_recorded") if isinstance(r, dict) else None
    _picks_line = ""
    if _pr is True:
        _picks_line = "\n✓ picks 已落档" + ("(正式选股 snapshot)" if cfg.get("snapshot") else "")
    elif _pr is False:
        _picks_line = "\n⚠ picks 档案落盘失败(不影响本次选股结果)"
```

- [ ] **Step 5: 7 条注册进 WW_TOOL_TABLE** — 追加在 `ww_capabilities` 条目之前(保持 meta 工具在表尾):

```python
    {"name": "ww_ledger_state",
     "description":
         "实盘台账快照:组合持仓、现金、已实现盈亏、胜率、MTM 权益(缺价诚实置空)。"
         "看组合真实成绩用它,评估研判质量用 ww_calibration。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": ledger_state_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/ledger/state"]},
    {"name": "ww_calibration",
     "description":
         "置信度校准全表:各置信档研判的真实 N 日方向命中率(『我说80%把握时实际对几成』)。"
         "复盘/评估自己研判先看它;n<5 的档样本不足仅供参考。",
     "input_schema": {"type": "object", "properties": {
         "horizon": {"type": "integer", "default": 5, "description": "N 日方向命中窗口(1-20)"}}},
     "impl": calibration_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/calibration"]},
    {"name": "ww_seats_runs",
     "description": "落子回测 run 历史头(run_id/票/时间窗/买卖观计数,新在前)。可选 code 按票过滤。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "可选,按票数字核过滤,如 605358"},
         "limit": {"type": "integer", "default": 10}}},
     "impl": seats_runs_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/runs"]},
    {"name": "ww_model_health",
     "description":
         "模型体检:v4 排名产物新鲜度(date/rows/陈旧天数)+ 市场宽度 as_of + 模型健康"
         "(vintage OOS IC/衰减告警,缺产物诚实缺席)。动因子/模型/选股前先用它核数据新鲜度。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": model_health_impl, "cost": "instant", "confirm": False,
     "reachable": ["/screen/health"]},
    {"name": "ww_factor_tsic",
     "description":
         "个股时序 IC:逐票 Spearman(因子值, 自身未来收益)——单票/小池口径(截面 IC 单票退化时用它)。"
         "expr 为 zoo 表达式或注册名(ww_factor_fields 查字段)。",
     "input_schema": {"type": "object", "properties": {
         "expr": {"type": "string", "description": "因子表达式/注册名"},
         "code": {"type": "string", "description": "单票代码,如 SH605358(与 codes 二选一)"},
         "codes": {"type": "array", "items": {"type": "string"}},
         "universe": {"type": "string", "description": "不给 code/codes 时的小池 id,如 csi_fast"},
         "fwd_days": {"type": "integer", "default": 20},
         "start": {"type": "string"}, "end": {"type": "string"}},
      "required": ["expr"]},
     "impl": factor_tsic_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/factor/tsic"]},
    {"name": "ww_workflow_critique",
     "description":
         "AI 批判环:给出研究目标+当前工作流 graph({nodes,edges})+该图真实回测指标,"
         "LLM 诊断问题并产改进图(LLM 不可用时规则兜底,来源诚实标注)。"
         "注意:指标由调用方自报,后端不复算。",
     "input_schema": {"type": "object", "properties": {
         "goal": {"type": "string"},
         "graph": {"type": "object", "description": "{nodes:[],edges:[]} 当前工作流图"},
         "metrics": {"type": "object",
                     "description": "该图真实回测指标 {rank_ic,sharpe,ann_return,oos_verdict,n_dates,factor}"}},
      "required": ["graph"]},
     "impl": workflow_critique_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/workflow/critique"]},
    {"name": "ww_regen",
     "description":
         "三产物再生(breadth/mainline/v4 排名重算,即选股页『拉取最新数据』):后台子进程 ~5 分钟,"
         "单飞锁防并发;wait=true(默认)轮询到完成并报新数据日。更完行情(ww_update_data)后要选股吃到新数据,必须跑它。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "end": {"type": "string", "description": "可选截止交易日 YYYY-MM-DD,缺省=最新数据日"},
         "wait": {"type": "boolean", "default": True},
         "poll_seconds": {"type": "number", "default": 15},
         "timeout_seconds": {"type": "number", "default": 600}}},
     "impl": regen_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/screen/regen", "/screen/regen/status"]},
```

  同时 ww_screen_run 注册条目的 schema properties 追加:

```python
         "snapshot": {"type": "boolean", "default": False,
                      "description": "标记为「正式选股」落 picks 档案(供后续收益跟踪);实验/参数扫描别开"},
         "note": {"type": "string", "description": "选股备注,随 picks 档案落盘"},
```

- [ ] **Step 6: _SYSTEM_PROMPT 同步** — `guanlan_v2/console/api.py`:
  1. 「另有:自省 ww_capabilities…」行(api.py:309)之后加一行:

```
另有(闭环读取面):实盘台账 ww_ledger_state(组合持仓/已实现盈亏/胜率)、置信校准 ww_calibration(各置信档真实N日命中率)、回测run历史 ww_seats_runs、模型体检 ww_model_health(v4新鲜度/vintage OOS IC/告警)、个股时序IC ww_factor_tsic(单票口径)、AI批判 ww_workflow_critique(据真实指标产改进图;指标自报)、数据再生 ww_regen(三产物重算~5分钟,选股吃新数据必跑,需确认)。
```

  2. 纪律 12 之后加纪律 13(注意三引号字符串收尾跟随最后一条):

```
13. 研究/复盘先核真实成绩:动因子/模型/选股前先 ww_model_health 查产物新鲜度;评估自己研判用 ww_calibration;看组合真实盈亏用 ww_ledger_state。选股要作为「正式选股」被跟踪时,ww_screen_run 传 snapshot=true(可带 note)。
```

- [ ] **Step 7: 守护计数同步** — 精确改四处:
  - `tests/test_console_tools.py` `test_engine_profile_excludes_ww_but_console_whitelist_resolves`:`assert len(out["registered_ww"]) == 32`→`== 39`;`out["console_n"] == 57`→`== 64`;`out["explicit_n"] == 57 and out["explicit_ww_n"] == 32`→`== 64` / `== 39`。
  - 同文件 `test_registry_derivation_consistent`:`== 32`→`== 39`;`== 57`→`== 64`。
  - 同文件 `test_ww_reachable_endpoints_matches_expected` 的 expected 集合追加 7 项(注释风格对齐):

```python
        "/seats/ledger/state",    # ww_ledger_state(P0 闭环读取面)
        "/seats/runs",            # ww_seats_runs
        "/screen/health",         # ww_model_health
        "/factor/tsic",           # ww_factor_tsic
        "/workflow/critique",     # ww_workflow_critique
        "/screen/regen",          # ww_regen(触发)
        "/screen/regen/status",   # ww_regen(wait 轮询)
```

  (注:`/seats/calibration` 已在集合里——ww_seats_decide 在用;集合语义不重复。)
  - `tests/test_guanlan_mcp.py` 两处 `== 37`→`== 44`,注释改 `# 37 ww_ + 7 alpha-zoo`(Task 4 将排除 ww_seats_bind 后再改 43)。

- [ ] **Step 8: 跑受影响测试**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -v`
Expected: 全绿(新 impl 测 ×11 + 守护计数三件 + MCP 计数 44)

- [ ] **Step 9: Commit**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(console): P0 闭环读取面 7 薄工具(ledger/calibration/runs/health/tsic/critique/regen)+ ww_screen_run 透传 snapshot/note + 提示词纪律13 + 守护计数 39/64"
```

---

### Task 4: MCP 诚实收尾(排除 ww_seats_bind + bg-spawn 单测 + README)

**Files:**
- Modify: `guanlan_v2/glmcp/tooltable.py`(_EXCLUDED)
- Modify: `guanlan_v2/glmcp/README.md`(全文重写,内容见 Step 5)
- Test: `tests/test_guanlan_mcp.py`(排除断言 + 计数 44→43 + bg-spawn 四分支)

**Interfaces:**
- Consumes: 既有 `_spawn_background_detached(bg: dict) -> str`(glmcp/server.py:45-83,已合 main 未带测)与 `dispatch_tool` 的 background 信封分支(server.py:100-107)。
- Produces: MCP 工具数定格 **43**。

- [ ] **Step 1: 写失败测试** — `tests/test_guanlan_mcp.py` 追加:

```python
def test_mcp_excludes_frontend_envelope_tools():
    """ww_seats_bind 靠前端 window.GL 落地,MCP 语境=空转假成功 → 排除(同 ww_show_page)。"""
    from guanlan_v2.glmcp.tooltable import build_mcp_tools
    names = {t["name"] for t in build_mcp_tools()}
    assert "ww_seats_bind" not in names
    assert "ww_report_run" in names          # 研报经 detached 子进程真跑,保留(gated)
    assert len(names) == 43


def test_spawn_background_report_branch(monkeypatch):
    import subprocess
    from pathlib import Path
    import guanlan_v2.glmcp.server as ms
    calls = {}
    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd
            calls["kw"] = kw
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    receipt = ms._spawn_background_detached({"kind": "report", "code": "SZ000630"})
    assert "已真启动后台研报" in receipt
    assert calls["cmd"][1] == "report" and calls["cmd"][2] == "SZ000630"
    assert calls["kw"]["creationflags"] == (0x00000008 | 0x00000200)   # detached
    # 清理:FakePopen 未写内容 → 本测新建的空日志删掉
    for p in (Path(ms.__file__).resolve().parents[2] / "var").glob("mcp_bg_*.log"):
        if p.stat().st_size == 0:
            p.unlink()


def test_spawn_background_etf_branch(monkeypatch):
    import subprocess
    from pathlib import Path
    import guanlan_v2.glmcp.server as ms
    calls = {}
    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    receipt = ms._spawn_background_detached({"kind": "etf_report", "code": "510300", "asof": None})
    assert "已真启动" in receipt and calls["cmd"][1] == "-c" and "run_etf_report" in calls["cmd"][2]
    for p in (Path(ms.__file__).resolve().parents[2] / "var").glob("mcp_bg_*.log"):
        if p.stat().st_size == 0:
            p.unlink()


def test_spawn_background_unknown_kind_refuses(monkeypatch):
    """未知 kind → 诚实拒绝文案,绝不 spawn。"""
    import subprocess
    import guanlan_v2.glmcp.server as ms
    def _boom(*a, **k):
        raise AssertionError("不应 spawn")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    msg = ms._spawn_background_detached({"kind": "weird"})
    assert "暂不支持" in msg


def test_dispatch_background_spawn_failure_is_visible(monkeypatch):
    """spawn 抛错 → dispatch 回错误显形,绝不假成功(红线)。"""
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "已受理", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    def boom(bg):
        raise RuntimeError("spawn炸了")
    monkeypatch.setattr(ms, "_spawn_background_detached", boom)
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "后台任务启动失败" in res[0].text and "spawn炸了" in res[0].text


def test_dispatch_background_success_appends_receipt(monkeypatch):
    import guanlan_v2.glmcp.server as ms
    async def fake_to_thread(fn, **kw):
        return {"ok": True, "content": "已受理研报", "background": {"kind": "report", "code": "X"}}
    monkeypatch.setattr(ms.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("GUANLAN_MCP_WRITE", "1")
    monkeypatch.setattr(ms, "_spawn_background_detached", lambda bg: "已真启动后台研报(job t)")
    res = asyncio.run(ms.dispatch_tool("ww_report_run", {"code": "X"}))
    assert "已受理研报" in res[0].text and "已真启动后台研报" in res[0].text
```

  同时把 Task 3 改成 44 的两处计数断言改为 `== 43`(注释:`# 36 ww_(39−3 excluded) + 7 alpha-zoo`)。

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_guanlan_mcp.py -v`
Expected: `test_mcp_excludes_frontend_envelope_tools` FAIL(ww_seats_bind 仍在);两处计数断言 FAIL(44≠43);bg-spawn 四测预期 PASS(代码已在,测的是现状);dispatch 两测 PASS。

- [ ] **Step 3: 改 _EXCLUDED** — `guanlan_v2/glmcp/tooltable.py:8` 替换为:

```python
# console-UI-only(改会话计划/往右栏弹页面)与前端信封类(靠 window.GL 落地,MCP 语境=空转假成功)
# → 不暴露。ww_report_run/ww_etf_report_run 不在此列:其 background 信封经 dispatch_tool 的
# _spawn_background_detached detached 子进程真跑(见 server.py)。
_EXCLUDED = {"ww_plan_update", "ww_show_page", "ww_seats_bind"}
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_guanlan_mcp.py -v`
Expected: 全绿(计数 43 + 排除 + bg 四分支 + dispatch 两测 + 既有测)

- [ ] **Step 5: 重写 README** — `guanlan_v2/glmcp/README.md` 全文替换为:

```markdown
# guanlan MCP server

把帷幄的 `ww_*` 工具(去 3 个仅 console 语境可用的,见下)+ 7 个引擎 alpha-zoo 研究工具
暴露成 MCP 工具(**43 个**),供外部 MCP 客户端(别的 Claude / IDE 插件 / agent)驱动 guanlan。

## 两种传输(任选)
- **HTTP**:随 9999 后端一起跑,挂在 `http://127.0.0.1:9999/gl-mcp`。
- **stdio**:`python -m guanlan_v2.glmcp`(本地客户端启动它)。

`example.mcp.json` 是两种的客户端配置样例。

## 与引擎 MCP 并存
9999 上 `/mcp` 是引擎自带 MCP(20 个引擎研究/dream 工具);本 server 是 `/gl-mcp`(43 个 guanlan 工具)。两者并存、各管各的。

## 排除的 3 个工具(为什么 MCP 里没有)
- `ww_plan_update` / `ww_show_page`:console-UI-only(改会话计划/往 console 右栏弹页面),MCP 无意义。
- `ww_seats_bind`:产 seat_bind 信封靠**前端 window.GL 落地**建盯盘 agent;MCP 语境无页面
  = 调了也不会发生任何事(空转假成功)→ 诚实排除。要建盯盘请经帷幄 console。

## 研报类长任务(MCP 通道真执行)
`ww_report_run` / `ww_etf_report_run` 在 console 里由事件循环起后台任务;MCP 通道没有该跑道,
由 `dispatch_tool` 检测 background 信封 → **detached 子进程真跑**(不随 MCP 客户端退出而死),
返回带 job id 与日志路径的受理凭证(`var/mcp_bg_<job>.log`)。启动失败会显形报错,绝不假成功。

## 写操作默认锁
写/销毁类工具(`ww_model_train/promote/validate/delete/set_default`、`ww_factorlib_save`、`ww_cards_save`、
`ww_seats_decide`、`ww_update_data`、`ww_news_collect`、`ww_report_run`、`ww_etf_report_run`、`ww_regen`、
`alpha_forge`)默认**调不动**——外部客户端无帷幄确认弹窗,故需在 9999 启动环境设
`GUANLAN_MCP_WRITE=1` 后重启才放行。只读工具与 `ww_memory_write` 不受锁。
`list_tools` 始终列出全部并标注 `readOnlyHint`/`destructiveHint`。

## 无真下单
guanlan 是研究平台,无券商真实下单 → MCP 不暴露下单工具(诚实)。
```

- [ ] **Step 6: Commit**

```bash
git add guanlan_v2/glmcp/tooltable.py guanlan_v2/glmcp/README.md tests/test_guanlan_mcp.py
git commit -m "fix(mcp): 排除 ww_seats_bind(前端信封空转)+ bg-spawn detached 四分支补测 + README 对表 43 工具"
```

---

### Task 5: 全量回归 + 真机 e2e + 还原

**Files:**
- 无代码改动(纯验证);产出证据写进任务报告

- [ ] **Step 1: 全量回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest -q`
Expected: 全绿(基线 676 + 新增 ≈26 ≈ 702 passed;精确数以实跑为准,**0 failed 是硬要求**)

- [ ] **Step 2: 重启 9999 加载新代码**(看门狗已死,手动;先杀 9999 监听进程再拉起):

```powershell
$pid9999 = (Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -First 1
if ($pid9999) { Stop-Process -Id $pid9999 -Force }
Start-Process G:\financial-analyst\.venv\Scripts\python.exe -ArgumentList "guanlan_v2\server.py" -WorkingDirectory G:\guanlan-v2
```

等 ~30s 后 `curl http://127.0.0.1:9999/workflow/list` 通。

- [ ] **Step 3: e2e — picks 链路**:`POST /screen/run` 带 `{"factors": [], "topN": 20, "snapshot": false, "note": "e2e"}` → 响应含 `picks_recorded: true`;`GET /screen/picks?limit=3` → items[0].note == "e2e" 且 picks 有 topN 行真代码。

- [ ] **Step 4: e2e — 新工具真调**(scratchpad 脚本直调 impl,impl 打真 9999):逐个调 `ledger_state_impl() / calibration_impl() / model_health_impl() / seats_runs_impl() / factor_tsic_impl(expr="rank(mom_20)", code="SH600519")`,断言各返回 ok∈(True,False) 且 content 非空、无异常穿透;打印各 content 首行留证。台账未开账也算真态(「未开账」即诚实)。

- [ ] **Step 5: e2e — regen 只验连通**:`GET /screen/regen/status` → `{ok:true, state:{phase:…}}`(**不真跑** 5 分钟 regen)。

- [ ] **Step 6: e2e — MCP 面**:scratchpad 脚本(mcp streamablehttp_client 打 `http://127.0.0.1:9999/gl-mcp`,模式照 session 既有 mcp_e2e_http.py)list_tools → **43 个**;`ww_seats_bind` 缺席;`ww_regen`/`ww_ledger_state` 在列;未设写门时调 `ww_regen` → 拒绝文案含 `GUANLAN_MCP_WRITE`(ASCII 断言防编码坑)。

- [ ] **Step 7: 还原检查**:e2e 的 picks 记录(note="e2e"、snapshot:false)append-only 留档无害不清;确认无残留测试进程;`git status` 只余已提交内容。

- [ ] **Step 8: Commit**(仅当 e2e 揪出计划外修复;否则本任务无提交)

---

## Self-Review(计划自审记录)

1. **Spec coverage**:§1 picks(Task 1+2:模块/字段/钩子/端点/显形/回退不落;ww 透传→Task 3 Step 4)✓;§2 七工具+四处同步(Task 3)✓;§3 MCP(Task 4:排除/计数/bg 四分支测/README)✓;§4 测试验收(各任务 TDD + Task 5 回归/e2e)✓;红线在 Global Constraints 逐字✓。
2. **Placeholder scan**:无 TBD/TODO;两处「以后端源码为准」(calibration_table 行键、load_health_summary 键)均已配防御性渲染代码,不阻塞实现✓。
3. **Type consistency**:`append_pick(record: dict)->bool` / `read_picks(snapshot_only,limit)->list` Task 1 定义、Task 2 消费一致;`picks_recorded` 键名 Task 2 产、Task 3 消费一致;7 工具名在注册/提示词/期望端点集/计数一致;MCP 计数链 37→44(Task 3)→43(Task 4)自洽✓。
