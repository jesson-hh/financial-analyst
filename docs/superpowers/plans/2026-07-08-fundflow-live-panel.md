# 观澜 · 板块资金流向(实时)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在观澜新增一个独立「板块资金流向」实时页,复刻截图:盘中累计净流入多线图 + 大盘超大/大/中/小/主力分解 + 全A/行业/概念涨跌头条 + 板块净流入排行榜,概念/行业双档切换。

**Architecture:** 数据经既有唯一现拉门户 `guanlan_v2/datafeed/live_client.py` → `G:\stocks` 探针子进程。stocks 探针加**一个**新东财 push2 源(板块资金流,概念+行业两档);大盘分解与涨跌头条由行业档全板块加总导出(行业板块=全市场互斥全覆盖划分),消除未验证接口。guanlan 新增 `guanlan_v2/fundflow/` 模块(聚合 + 按日快照 jsonl 沉淀 + history 重建盘中多线),前端 `ui/fundflow/` 无构建 React + 纯 SVG。

**Tech Stack:** Python 3.13 / FastAPI(guanlan)、requests(stocks 探针,http 明文 push2)、React 18.3.1 UMD + Babel standalone + 纯 SVG(前端,无构建无图表库)、pytest。

## Global Constraints

以下为全计划通用约束,每个任务隐含遵守(值逐字取自 spec):

- **push2 必须用 `http://` 明文**——https 的 push2/push2his `clist`/`fflow` 被东财风控 connection-abort(SKILL.md #18);http 返 200 同 payload。headers 至少带 `{"User-Agent": UA}`。
- **金额单位全链一致 = 元**;前端展示折亿(`÷ 1e8`,保留 2 位)。
- **纯展示层,绝不混入交易信号**——不回写任何选股/落子评分。
- **诚实降级**:单源失败/快照陈旧/poller 未开 → payload `notes[]` 显形 + 前端页脚降级条;真错(源非法/子进程失败)→ `ok:False`,前端断供占位,绝不合成假数据。
- **协程内严禁同步 HTTP**——`api.py` 所有同步实现一律 `await asyncio.to_thread(...)`(否则堵 loop 触发 9999 看门狗杀)。
- **`G:\stocks` 不是 git 仓库**——stocks 侧改动只保存文件 + 跑测试通过,**不 git commit**;仅 guanlan 仓(`G:\guanlan-v2`,分支 main)提交。
- **改 guanlan 后端须重启 9999 才生效**;真机验证用 **9998** 端口临时起服务(避免杀 9999 看门狗持有的实例)。
- **前端 bump `?v`**:改任何 `.jsx`/`guanlan-nav.js` 后在引用它的 `.html` 里递增 `?v=` 查询串,避免浏览器缓存串味。
- 板块档位缺省 `concept`(贴截图题材视角),Tab 可切 `industry`。
- 盘中图线数:今日净流入前 8 + 净流出前 8(≤16 条);排行榜前 20;自动落点间隔 3 分钟。

## 数据源字段契约(源 A 板块资金流,东财 clist/get)

- URL:`http://push2.eastmoney.com/api/qt/clist/get`(http 明文)
- `fs`:行业档 `m:90+t:2`,概念档 `m:90+t:3`
- 排序:`fid=f62`、`po=1`(按主力净额降序)、`pn=1`、`pz=200`、`np=1`、`fltt=2`、`invt=2`
- `fields`:`f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f104,f105,f204`
  - `f12`板块代码 `f14`板块名 `f3`涨跌幅 `f62`主力净额 `f184`主力净占比
  - `f66`超大净额 `f69`超大净占比 `f72`大单净额 `f75`大单净占比
  - `f78`中单净额 `f81`中单净占比 `f84`小单净额 `f87`小单净占比
  - `f104`上涨家数 `f105`下跌家数 `f204`主力净流入最大股名
- 响应:`data.diff`(dict 值集合 或 list);金额字段单位=元。
- **确切字段以 Task 1 Step 0 真机探针为准**;若某 f 编号与真机不符,按真机返回调整(spec 已声明源 A 待钉死点)。

---

### Task 1: stocks — `em_sector_fund_flow` handler

**Files:**
- Modify: `G:\stocks\src\data\live_text_sources.py`(加 handler 函数 + 加入模块 `__all__`)
- Test: `G:\stocks\scripts\test_live_text_sources.py`(加 FakeHttp 分支 + 测试)

**Interfaces:**
- Produces: `em_sector_fund_flow(kind: str = "concept", top: int = 200, http=None, min_interval: float = 1.0) -> list[dict]`,每行 `{"code","name","change_pct","main_net","main_pct","super_net","large_net","mid_net","small_net","up_count","down_count","leader"}`(金额=元)。

- [ ] **Step 0: 真机探针确认字段(诊断,非测试)**

Run(确认字段与单位,行业档):
```bash
cd "G:/stocks" && NO_PROXY="*" python -c "
import os; os.environ['NO_PROXY']='*'
from src.data.live_text_sources import em_get
r = em_get('http://push2.eastmoney.com/api/qt/clist/get', params={'pn':'1','pz':'5','po':'1','np':'1','fltt':'2','invt':'2','fid':'f62','fs':'m:90+t:2','fields':'f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f104,f105,f204'}, headers={'User-Agent':'Mozilla/5.0'}, min_interval=0)
import json; print(json.dumps((r.json().get('data') or {}).get('diff'), ensure_ascii=False)[:600])
"
```
Expected: 打印 5 个行业板块的 f12/f14/f62… 真值。若字段编号有出入,记下真值并在 Step 3 的 `fields` 与解析里对齐。概念档把 `fs` 换 `m:90+t:3` 再验一次。

- [ ] **Step 1: 写失败测试**

在 `G:\stocks\scripts\test_live_text_sources.py` 末尾追加(FakeDatacenterHttp 已有 clist 分支,这里用局部 FakeHttp 显式喂板块资金流响应):
```python
def test_em_sector_fund_flow_parses_clist_diff() -> None:
    from src.data.live_text_sources import em_sector_fund_flow

    class FakeSectorHttp:
        def get(self, url, params=None, headers=None, timeout=None, **kw):
            assert "push2.eastmoney.com/api/qt/clist/get" in url
            assert params["fs"] == "m:90+t:3"          # concept 档
            return FakeResponse(payload={"data": {"diff": {
                "0": {"f12": "BK0001", "f14": "算力概念", "f3": 2.1,
                      "f62": 9.45e9, "f184": 3.2, "f66": 6.0e9, "f69": 2.0,
                      "f72": 3.45e9, "f75": 1.2, "f78": -1.39e8, "f81": -0.1,
                      "f84": -4.0e9, "f87": -1.3, "f104": 30, "f105": 5, "f204": "某某股份"},
                "1": {"f12": "BK0002", "f14": "存储芯片", "f3": -3.4,
                      "f62": -1.52e10, "f184": -5.1, "f66": -9.0e9, "f69": -3.0,
                      "f72": -6.2e9, "f75": -2.1, "f78": 1.0e8, "f81": 0.03,
                      "f84": 1.42e10, "f87": 4.7, "f104": 4, "f105": 40, "f204": "另一股"},
            }}})

    rows = em_sector_fund_flow("concept", top=200, http=FakeSectorHttp(), min_interval=0)
    assert rows[0] == {
        "code": "BK0001", "name": "算力概念", "change_pct": 2.1,
        "main_net": 9.45e9, "main_pct": 3.2, "super_net": 6.0e9, "large_net": 3.45e9,
        "mid_net": -1.39e8, "small_net": -4.0e9, "up_count": 30, "down_count": 5,
        "leader": "某某股份",
    }
    assert rows[1]["name"] == "存储芯片" and rows[1]["main_net"] == -1.52e10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/stocks" && python -m pytest scripts/test_live_text_sources.py::test_em_sector_fund_flow_parses_clist_diff -v`
Expected: FAIL(`cannot import name 'em_sector_fund_flow'`)

- [ ] **Step 3: 实现 handler**

在 `live_text_sources.py` 里 `em_industry_comparison` 函数之后加:
```python
def em_sector_fund_flow(
    kind: str = "concept",
    top: int = 200,
    http: Any | None = None,
    min_interval: float = 1.0,
) -> list[dict[str, Any]]:
    """Sector/concept board fund flow ranking by main-force net (unit=yuan).

    kind='concept' -> fs=m:90+t:3 ; kind='industry' -> fs=m:90+t:2.
    http on purpose: see em_fund_flow_daily note (https clist is conn-reset).
    """
    fs = "m:90+t:2" if str(kind).lower().startswith("ind") else "m:90+t:3"
    response = em_get(
        "http://push2.eastmoney.com/api/qt/clist/get",
        params={
            "pn": "1", "pz": "200", "po": "1", "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": fs,
            "fields": "f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f104,f105,f204",
        },
        headers={"User-Agent": UA},
        timeout=15,
        http=http,
        min_interval=min_interval,
    )
    diff = (response.json().get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())

    def _num(v: object) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    rows = [
        {
            "code": item.get("f12", ""),
            "name": item.get("f14", ""),
            "change_pct": _num(item.get("f3")),
            "main_net": _num(item.get("f62")),
            "main_pct": _num(item.get("f184")),
            "super_net": _num(item.get("f66")),
            "large_net": _num(item.get("f72")),
            "mid_net": _num(item.get("f78")),
            "small_net": _num(item.get("f84")),
            "up_count": int(_num(item.get("f104"))),
            "down_count": int(_num(item.get("f105"))),
            "leader": item.get("f204", ""),
        }
        for item in diff
    ]
    return rows[: int(top)] if int(top) > 0 else rows
```
并把 `"em_sector_fund_flow"` 加入文件末尾的 `__all__` 列表(在 `"em_industry_comparison"` 一行之后加一行)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "G:/stocks" && python -m pytest scripts/test_live_text_sources.py::test_em_sector_fund_flow_parses_clist_diff -v`
Expected: PASS

- [ ] **Step 5: 保存(stocks 非 git,不 commit)**

无 commit。确认文件已存、测试绿即可。（Task 2 一并接线后跑 stocks 全测。）

---

### Task 2: stocks — 注册表登记 + dispatch 接线

**Files:**
- Modify: `G:\stocks\src\data\live_sources.py`(import handler + `LIVE_SOURCE_REGISTRY` 加条目 + `_call_handler` 加 kind)
- Test: `G:\stocks\scripts\test_live_text_sources.py`

**Interfaces:**
- Consumes: `em_sector_fund_flow`(Task 1)
- Produces: `probe_live_source("sector_fund_flow", code="concept"|"industry", http=…)` → `LiveSourceResult`,`items[i].raw` 含 `main_net` 等字段。

- [ ] **Step 1: 写失败测试**

追加到 `scripts/test_live_text_sources.py`:
```python
def test_probe_sector_fund_flow_wraps_rows() -> None:
    from src.data.live_sources import probe_live_source

    class FakeSectorHttp:
        def get(self, url, params=None, headers=None, timeout=None, **kw):
            assert params["fs"] == "m:90+t:2"          # industry 档(code=industry)
            return FakeResponse(payload={"data": {"diff": [
                {"f12": "BK0420", "f14": "半导体", "f3": -1.2, "f62": -1.34e10,
                 "f184": -2.0, "f66": -8e9, "f72": -5.4e9, "f78": 1e8, "f84": 1.3e10,
                 "f104": 10, "f105": 30, "f204": "中芯"},
            ]}})

    result = probe_live_source("sector_fund_flow", code="industry", limit=50,
                               http=FakeSectorHttp(), min_interval=0)
    d = result.as_dict()
    assert d["status"] == "ok" and len(d["items"]) == 1
    raw = d["items"][0]["raw"]
    assert raw["name"] == "半导体" and raw["main_net"] == -1.34e10
    assert d["items"][0]["title"] == "半导体"      # _best_title uses name
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/stocks" && python -m pytest scripts/test_live_text_sources.py::test_probe_sector_fund_flow_wraps_rows -v`
Expected: FAIL(`unknown live source 'sector_fund_flow'`)

- [ ] **Step 3: 实现接线**

在 `live_sources.py`:

(a) import 块(第 16-48 行的 `from src.data.live_text_sources import (...)`)加一行 `em_sector_fund_flow,`(字母序放 `em_margin,` 之前或 `em_lhb_stock,` 附近皆可)。

(b) `LIVE_SOURCE_REGISTRY` 在 `eastmoney_industry_comparison` 条目之后加:
```python
    {
        "source_id": "eastmoney_sector_fund_flow",
        "alias": "sector_fund_flow",
        "provider": "eastmoney",
        "category": "market",
        "source_type": "sector_fund_flow_live",
        "handler": em_sector_fund_flow,
        "handler_kind": "sector_flow",
    },
```

(c) `_call_handler` 在 `if kind == "top_rows":` 分支之前加:
```python
    if kind == "sector_flow":
        return handler(kind=code or "concept", top=limit, http=http, min_interval=min_interval)
```

- [ ] **Step 4: 跑测试确认通过 + stocks 全量 live-source 测试**

Run: `cd "G:/stocks" && python -m pytest scripts/test_live_text_sources.py -q`
Expected: 全绿(含新 2 测)。

- [ ] **Step 5: 保存(stocks 非 git,不 commit)**

---

### Task 3: stocks — 真机探活钉死(验证任务,非 TDD)

**Files:** 无改动(纯验证);若真机字段与 Task 1 不符,回到 Task 1 Step 3 调 `fields`/解析并复跑测试。

- [ ] **Step 1: 概念 + 行业双档真机探活**

Run:
```bash
cd "G:/stocks" && for k in concept industry; do echo "=== $k ==="; NO_PROXY="*" python scripts/probe_live_sources.py --source=eastmoney_sector_fund_flow --code=$k --limit=5 --json 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); print('status',d['status'],'n',len(d['items'])); [print(json.dumps(i['raw'],ensure_ascii=False)[:200]) for i in d['items'][:3]]"; done
```
Expected: 两档均 `status ok`,每行 raw 有真 `name`/`main_net`(元级大数)/`super_net`…/`up_count`/`down_count`。记录:非交易时段返回上一收盘档(可接受,值非零即为真)。

- [ ] **Step 2: 若字段异常则回修**

若某档 `n 0` 或 `main_net` 全 0:核对 Step 0 真机字段,修正 `fs`/`fields`/解析,复跑 Task 1+2 测试与本探活,直至两档真返数据。

---

### Task 4: guanlan — datafeed live_client 别名接线

**Files:**
- Modify: `G:\guanlan-v2\guanlan_v2\datafeed\live_client.py`(`_STATIC_SOURCES` + `CODE_PASSTHROUGH`)
- Test: `G:\guanlan-v2\tests\test_datafeed_client.py`

**Interfaces:**
- Produces: `resolve_source("sector_fund_flow") == "eastmoney_sector_fund_flow"`;`eastmoney_sector_fund_flow` ∈ `CODE_PASSTHROUGH`(code 承载 concept/industry,不被 6 位提取毁掉)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_datafeed_client.py`:
```python
def test_sector_fund_flow_alias_and_passthrough():
    from guanlan_v2.datafeed import live_client as lc
    assert lc.resolve_source("sector_fund_flow") == "eastmoney_sector_fund_flow"
    assert lc.resolve_source("eastmoney_sector_fund_flow") == "eastmoney_sector_fund_flow"
    # code 档位透传:concept 不被 \d{6} 提取清空
    norm = lc._normalize_args("eastmoney_sector_fund_flow", "concept", "")
    assert norm["err"] == "" and norm["code"] == "concept"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_datafeed_client.py::test_sector_fund_flow_alias_and_passthrough -v`
Expected: FAIL(resolve 返 `""`)

- [ ] **Step 3: 实现接线**

在 `live_client.py` `_STATIC_SOURCES` 字典末尾(`"iwencai_search": "iwencai",` 之前)加:
```python
    "eastmoney_sector_fund_flow": "sector_fund_flow",
```
在 `CODE_PASSTHROUGH` 集合加成员:
```python
CODE_PASSTHROUGH = {"ths_hot_list", "eastmoney_industry_reports", "tencent_realtime_quote",
                    "eastmoney_sector_fund_flow"}
```

- [ ] **Step 4: 跑测试确认通过 + 对账守护**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_datafeed_client.py -q`
Expected: 全绿(含 `test_static_sources_reconcile_with_stocks_registry` —— stocks 侧 Task 2 已同批登记,对账过)。

- [ ] **Step 5: Commit(guanlan)**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/datafeed/live_client.py tests/test_datafeed_client.py && git commit -m "feat(datafeed): 板块资金流源 sector_fund_flow 别名接入 live_client(概念/行业档 code 透传)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: guanlan — `fundflow/sources.py` 现拉腿

**Files:**
- Create: `G:\guanlan-v2\guanlan_v2\fundflow\__init__.py`
- Create: `G:\guanlan-v2\guanlan_v2\fundflow\sources.py`
- Test: `G:\guanlan-v2\tests\test_fundflow_sources.py`

**Interfaces:**
- Produces: `fetch_sector(kind: str, live_fn=None) -> dict`,返回 `{"ok": bool, "rows": list[dict], "note": str}`;`rows` 为源原生行(board dict)。`live_fn` 可注入以便测试(默认走 `datafeed.live_client.probe`)。

- [ ] **Step 1: 写失败测试**

Create `tests/test_fundflow_sources.py`:
```python
def _fake_live(ok_rows):
    def _fn(source, code="", date="", limit=20):
        return {"ok": bool(ok_rows), "status": "ok" if ok_rows else "error",
                "items": [{"raw": r} for r in ok_rows], "n": len(ok_rows), "note": ""}
    return _fn


def test_fetch_sector_returns_rows():
    from guanlan_v2.fundflow import sources
    rows = [{"code": "BK1", "name": "算力概念", "main_net": 9.45e9,
             "super_net": 6e9, "large_net": 3.45e9, "mid_net": -1e8, "small_net": -4e9,
             "change_pct": 2.1, "up_count": 30, "down_count": 5}]
    out = sources.fetch_sector("concept", live_fn=_fake_live(rows))
    assert out["ok"] is True and out["rows"][0]["name"] == "算力概念"


def test_fetch_sector_degrades_on_empty():
    from guanlan_v2.fundflow import sources
    out = sources.fetch_sector("industry", live_fn=_fake_live([]))
    assert out["ok"] is False and out["note"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_sources.py -v`
Expected: FAIL(`No module named 'guanlan_v2.fundflow'`)

- [ ] **Step 3: 实现**

Create `guanlan_v2/fundflow/__init__.py`(**本任务只写这一行注释体,不 import api——api 在 Task 8 才建**):
```python
# -*- coding: utf-8 -*-
"""观澜 · 板块资金流向(实时)——纯展示层,绝不混入交易信号。"""
```

Create `guanlan_v2/fundflow/sources.py`:
```python
# -*- coding: utf-8 -*-
"""板块资金流现拉腿:统一经 datafeed.live_client(stocks 探针)只读拉取。

一个源两档(concept/industry);大盘分解与涨跌头条由 pulse 层按行业档加总导出,
本模块只负责把一档板块资金流拉成源原生行,失败诚实降级不抛穿。"""
from __future__ import annotations


def _client_live(source: str, code: str = "", limit: int = 200) -> dict:
    from guanlan_v2.datafeed import live_client as lc
    r = lc.probe(source, code=code, limit=limit)
    return {"ok": bool(r.get("ok")) and r.get("status") in ("ok", ""),
            "rows": lc.native_rows(r.get("items")), "n": int(r.get("n") or 0),
            "note": r.get("note") or ""}


def fetch_sector(kind: str, live_fn=None) -> dict:
    """拉一档板块资金流(concept|industry)。返回 {ok, rows, note}。"""
    if live_fn is None:
        live_fn = _client_live
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    r = live_fn(source="eastmoney_sector_fund_flow", code=k, limit=200)
    rows = r.get("rows") or []
    if not rows:
        return {"ok": False, "rows": [],
                "note": r.get("note") or f"{k} 档板块资金流本次 0 行(非交易日/源降级)"}
    return {"ok": True, "rows": rows, "note": r.get("note") or ""}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_sources.py -v`
Expected: PASS(2 测)

- [ ] **Step 5: Commit**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/fundflow/__init__.py guanlan_v2/fundflow/sources.py tests/test_fundflow_sources.py && git commit -m "feat(fundflow): 板块资金流现拉腿 fetch_sector(concept/industry 双档,失败诚实降级)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: guanlan — `fundflow/pulse.py` build_live + 快照落点

**Files:**
- Create: `G:\guanlan-v2\guanlan_v2\fundflow\pulse.py`
- Test: `G:\guanlan-v2\tests\test_fundflow_pulse.py`

**Interfaces:**
- Consumes: `sources.fetch_sector`(Task 5)
- Produces:
  - `build_live(kind="concept", refresh=False, snapshot_dir=None, sector_fn=None, now=None) -> dict` —— payload `{ok, kind, pulled_at, trading, market{super_net,large_net,mid_net,small_net,main_net}, breadth{allA{up,down},industry{up,down},concept{up,down}}, boards[{code,name,main_net,change_pct,rank,delta_intraday}], notes[]}`。落点到 `var/fundflow/<YYYYMMDD>.jsonl`(见 spec §5.3)。
  - `_is_trading(dt) -> bool`;`_snapshot_path(snapshot_dir, dt) -> Path`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_fundflow_pulse.py`:
```python
import json
from datetime import datetime
from pathlib import Path


def _sector_fn(concept_rows, industry_rows):
    def _fn(kind, live_fn=None):
        rows = industry_rows if str(kind).startswith("ind") else concept_rows
        return {"ok": bool(rows), "rows": rows, "note": "" if rows else "empty"}
    return _fn


def _rows(*specs):
    # spec = (name, main, super, large, mid, small, chg, up, down)
    return [{"code": f"BK{i}", "name": n, "main_net": m, "super_net": su, "large_net": la,
             "mid_net": mi, "small_net": sm, "change_pct": c, "up_count": u, "down_count": d}
            for i, (n, m, su, la, mi, sm, c, u, d) in enumerate(specs)]


def test_build_live_aggregates_market_and_breadth(tmp_path):
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5),
                    ("存储芯片", -1.52e10, -9e9, -6.2e9, 1e8, 1.42e10, -3.4, 4, 40))
    industry = _rows(("半导体", -1.34e10, -8e9, -5.4e9, 1e8, 1.3e10, -1.2, 10, 30),
                     ("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    now = datetime(2026, 7, 8, 10, 57, 0)
    out = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn(concept, industry), now=now)
    assert out["ok"] and out["kind"] == "concept" and out["trading"] is True
    # 大盘分解 = 行业档加总
    assert round(out["market"]["main_net"]) == round(-1.34e10 + 2.66e8)
    assert round(out["market"]["super_net"]) == round(-8e9 + 1e8)
    # 全A 涨跌 = 行业档 up/down 加总;行业涨跌数=行业板块涨跌计数;概念涨跌数=概念板块计数
    assert out["breadth"]["allA"] == {"up": 30, "down": 38}
    assert out["breadth"]["industry"] == {"up": 1, "down": 1}   # 银行涨、半导体跌
    assert out["breadth"]["concept"] == {"up": 1, "down": 1}    # 算力涨、存储跌
    # boards = 当前档(concept),按 main_net 降序,带 rank
    assert out["boards"][0]["name"] == "算力概念" and out["boards"][0]["rank"] == 1
    # 落点:当日快照文件出现,含 concept + industry 两行
    snap = Path(tmp_path) / "20260708.jsonl"
    lines = [json.loads(l) for l in snap.read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds = {l["kind"] for l in lines}
    assert kinds == {"concept", "industry"}


def test_build_live_no_sink_when_not_trading_and_not_refresh(tmp_path):
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5))
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    now = datetime(2026, 7, 8, 20, 0, 0)   # 收盘后
    out = pulse.build_live("concept", refresh=False, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn(concept, industry), now=now)
    assert out["trading"] is False
    assert not (Path(tmp_path) / "20260708.jsonl").exists()   # 非交易且非 refresh 不落点


def test_build_live_degrades_when_sector_empty(tmp_path):
    from guanlan_v2.fundflow import pulse
    out = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn([], []), now=datetime(2026, 7, 8, 10, 0, 0))
    assert out["ok"] is False and out["notes"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_pulse.py -v`
Expected: FAIL(`No module named 'guanlan_v2.fundflow.pulse'`)

- [ ] **Step 3: 实现**

Create `guanlan_v2/fundflow/pulse.py`:
```python
# -*- coding: utf-8 -*-
"""板块资金流聚合 + 快照沉淀。母版 macro/pulse.py。

现拉当前档(concept|industry)画板块图/排行;每次同时拉行业档做大盘分解与全A涨跌
(行业板块=全市场互斥全覆盖划分,加总=全市场);概念/行业涨跌数=各档板块涨跌计数。
每次真拉且(交易时段或显式 refresh)则向 var/fundflow/<当日>.jsonl 追加 concept+industry 两行快照。
纯展示,绝不回写信号。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import sources

_SNAP_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "fundflow"


def _is_trading(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


def _snapshot_path(snapshot_dir, dt: datetime) -> Path:
    base = Path(snapshot_dir) if snapshot_dir else _SNAP_DEFAULT
    return base / f"{dt.strftime('%Y%m%d')}.jsonl"


def _market_from(rows: list) -> dict:
    out = {"super_net": 0.0, "large_net": 0.0, "mid_net": 0.0, "small_net": 0.0}
    for r in rows:
        for k in out:
            out[k] += float(r.get(k) or 0.0)
    out["main_net"] = out["super_net"] + out["large_net"]
    return out


def _breadth_count(rows: list) -> dict:
    up = sum(1 for r in rows if float(r.get("change_pct") or 0) > 0)
    down = sum(1 for r in rows if float(r.get("change_pct") or 0) < 0)
    return {"up": up, "down": down}


def _allA_from(industry_rows: list) -> dict:
    return {"up": sum(int(r.get("up_count") or 0) for r in industry_rows),
            "down": sum(int(r.get("down_count") or 0) for r in industry_rows)}


def _first_snapshot_today(path: Path, kind: str) -> dict | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("kind") == kind:
                return row
    except OSError:
        return None
    return None


def _board_view(rows: list, first_snap: dict | None) -> list:
    ranked = sorted(rows, key=lambda r: float(r.get("main_net") or 0.0), reverse=True)
    base = {}
    if first_snap:
        base = {b.get("name"): float(b.get("main_net") or 0.0) for b in first_snap.get("boards", [])}
    out = []
    for i, r in enumerate(ranked):
        name = r.get("name")
        delta = (float(r.get("main_net") or 0.0) - base[name]) if name in base else None
        out.append({"code": r.get("code"), "name": name,
                    "main_net": float(r.get("main_net") or 0.0),
                    "change_pct": float(r.get("change_pct") or 0.0),
                    "rank": i + 1, "delta_intraday": delta})
    return out


def _snap_boards(rows: list) -> list:
    return [{"code": r.get("code"), "name": r.get("name"),
             "main_net": float(r.get("main_net") or 0.0),
             "change_pct": float(r.get("change_pct") or 0.0)} for r in rows]


def build_live(kind: str = "concept", refresh: bool = False, snapshot_dir=None,
               sector_fn=None, now=None) -> dict:
    if sector_fn is None:
        sector_fn = sources.fetch_sector
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    dt = now or datetime.now()
    trading = _is_trading(dt)
    notes: list[str] = []

    cur = sector_fn(k)
    other = sector_fn("industry" if k == "concept" else "concept")
    concept_rows = cur["rows"] if k == "concept" else other["rows"]
    industry_rows = other["rows"] if k == "concept" else cur["rows"]

    if not cur.get("ok"):
        notes.append(f"{k} 档板块资金流不可用:{cur.get('note') or '空'}")
        return {"ok": False, "kind": k, "pulled_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "trading": trading, "market": {}, "breadth": {}, "boards": [], "notes": notes}
    if not other.get("ok"):
        notes.append(f"{'industry' if k=='concept' else 'concept'} 档缺失,"
                     f"大盘分解/全A涨跌降级:{other.get('note') or '空'}")

    market = _market_from(industry_rows) if industry_rows else {}
    breadth = {
        "allA": _allA_from(industry_rows) if industry_rows else {"up": None, "down": None},
        "industry": _breadth_count(industry_rows) if industry_rows else {"up": None, "down": None},
        "concept": _breadth_count(concept_rows) if concept_rows else {"up": None, "down": None},
    }

    path = _snapshot_path(snapshot_dir, dt)
    first = _first_snapshot_today(path, k)
    boards = _board_view(cur["rows"], first)

    payload = {"ok": True, "kind": k, "pulled_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
               "trading": trading, "market": market, "breadth": breadth,
               "boards": boards, "notes": notes}

    # 落点:真拉到且(交易时段 或 显式 refresh);concept+industry 各落一行
    if trading or refresh:
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                if concept_rows:
                    f.write(json.dumps({"ts": ts, "kind": "concept", "market": market,
                                        "breadth": breadth, "boards": _snap_boards(concept_rows)},
                                       ensure_ascii=False) + "\n")
                if industry_rows:
                    f.write(json.dumps({"ts": ts, "kind": "industry", "market": market,
                                        "breadth": breadth, "boards": _snap_boards(industry_rows)},
                                       ensure_ascii=False) + "\n")
        except OSError as e:
            payload["notes"].append(f"快照落盘失败: {e}")
    return payload
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_pulse.py -v`
Expected: PASS(3 测)

- [ ] **Step 5: Commit**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/fundflow/pulse.py tests/test_fundflow_pulse.py && git commit -m "feat(fundflow): build_live 聚合(大盘分解/全A涨跌由行业档加总)+按日快照落点

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: guanlan — `fundflow/pulse.py` load_history 盘中多线重建

**Files:**
- Modify: `G:\guanlan-v2\guanlan_v2\fundflow\pulse.py`(加 `load_history` + `_read_day`)
- Test: `G:\guanlan-v2\tests\test_fundflow_pulse.py`(加测试)

**Interfaces:**
- Produces: `load_history(kind="concept", date="", snapshot_dir=None, top_each=8) -> dict` —— `{date, kind, ticks:[ts…], boards:[{name, series:[main_net@each tick or None]}], market_series:{main_net:[…]}}`。选线=末快照 main_net 净流入前 8 + 净流出前 8;某 tick 缺该板块 → series 该点 `None`(前端断线,不插值)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_fundflow_pulse.py`:
```python
def test_load_history_top_in_out_and_gap(tmp_path):
    from guanlan_v2.fundflow import pulse
    snap = Path(tmp_path) / "20260708.jsonl"
    def line(ts, kind, boards):
        return json.dumps({"ts": ts, "kind": kind,
                           "market": {"main_net": sum(b[1] for b in boards)},
                           "boards": [{"name": n, "main_net": v} for n, v in boards]},
                          ensure_ascii=False)
    snap.write_text("\n".join([
        line("2026-07-08T09:33:00", "concept", [("算力概念", 1e9), ("存储芯片", -1e9)]),
        line("2026-07-08T09:36:00", "concept", [("算力概念", 3e9)]),                    # 存储缺该 tick
        line("2026-07-08T09:36:00", "industry", [("银行", 5e8)]),                        # 别档,忽略
        line("2026-07-08T09:39:00", "concept", [("算力概念", 9.45e9), ("存储芯片", -1.52e10)]),
    ]) + "\n", encoding="utf-8")
    out = pulse.load_history("concept", date="20260708", snapshot_dir=str(tmp_path))
    assert out["ticks"] == ["2026-07-08T09:33:00", "2026-07-08T09:36:00", "2026-07-08T09:39:00"]
    names = {b["name"]: b["series"] for b in out["boards"]}
    assert names["算力概念"] == [1e9, 3e9, 9.45e9]
    assert names["存储芯片"] == [-1e9, None, -1.52e10]      # 中间 tick 缺 → None(断线)
    assert out["market_series"]["main_net"][0] == 0.0        # 1e9 + (-1e9)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_pulse.py::test_load_history_top_in_out_and_gap -v`
Expected: FAIL(`module 'guanlan_v2.fundflow.pulse' has no attribute 'load_history'`)

- [ ] **Step 3: 实现**

在 `pulse.py` 末尾加:
```python
def _read_day(path: Path, kind: str) -> list:
    if not path.exists():
        return []
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("kind") == kind and row.get("ts"):
            out.append(row)
    return out


def load_history(kind: str = "concept", date: str = "", snapshot_dir=None,
                 top_each: int = 8) -> dict:
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    stamp = "".join(ch for ch in str(date) if ch.isdigit()) or datetime.now().strftime("%Y%m%d")
    base = Path(snapshot_dir) if snapshot_dir else _SNAP_DEFAULT
    path = base / f"{stamp}.jsonl"
    snaps = _read_day(path, k)
    if not snaps:
        return {"date": stamp, "kind": k, "ticks": [], "boards": [],
                "market_series": {"main_net": []}}
    ticks = [s["ts"] for s in snaps]
    # 选线:末快照 main_net 净流入前 top_each + 净流出前 top_each
    last = sorted(snaps[-1].get("boards", []),
                  key=lambda b: float(b.get("main_net") or 0.0), reverse=True)
    inflow = [b["name"] for b in last[:top_each]]
    outflow = [b["name"] for b in last[-top_each:] if b["name"] not in inflow]
    picked = inflow + outflow
    boards = []
    for name in picked:
        series = []
        for s in snaps:
            val = next((float(b.get("main_net")) for b in s.get("boards", [])
                        if b.get("name") == name and b.get("main_net") is not None), None)
            series.append(val)
        boards.append({"name": name, "series": series})
    market_series = {"main_net": [float((s.get("market") or {}).get("main_net") or 0.0)
                                  for s in snaps]}
    return {"date": stamp, "kind": k, "ticks": ticks, "boards": boards,
            "market_series": market_series}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_pulse.py -v`
Expected: PASS(4 测)

- [ ] **Step 5: Commit**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/fundflow/pulse.py tests/test_fundflow_pulse.py && git commit -m "feat(fundflow): load_history 盘中多线重建(净流入/出各前8·缺tick断线不插值)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: guanlan — `fundflow/api.py` 路由 + server 接线 + opt-in poller

**Files:**
- Create: `G:\guanlan-v2\guanlan_v2\fundflow\api.py`
- Modify: `G:\guanlan-v2\guanlan_v2\fundflow\__init__.py`(补 api import)
- Modify: `G:\guanlan-v2\guanlan_v2\server.py`(挂路由 + 起 poller)
- Test: `G:\guanlan-v2\tests\test_fundflow_api.py`

**Interfaces:**
- Consumes: `pulse.build_live` / `pulse.load_history`(Task 6/7)
- Produces: `build_fundflow_router() -> APIRouter`(`GET /fundflow/live`、`GET /fundflow/history`);`start_fundflow_poller() -> None`(opt-in,`GUANLAN_FUNDFLOW_POLL=1` 才真起)。

- [ ] **Step 1: 写失败测试**

Create `tests/test_fundflow_api.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_FUNDFLOW_DIR", str(tmp_path))
    from guanlan_v2.fundflow.api import build_fundflow_router
    app = FastAPI()
    app.include_router(build_fundflow_router())
    return TestClient(app)


def test_live_endpoint_returns_json(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/fundflow/live?kind=concept&refresh=1")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


def test_history_endpoint_shape(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/fundflow/history?kind=concept&date=20260708")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"date", "kind", "ticks", "boards", "market_series"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_api.py -v`
Expected: FAIL(`No module named 'guanlan_v2.fundflow.api'`)

- [ ] **Step 3: 实现**

Create `guanlan_v2/fundflow/api.py`:
```python
# -*- coding: utf-8 -*-
"""板块资金流路由(薄壳,无 prefix)+ opt-in 盘中 poller。
协程内严禁同步 HTTP——一律 asyncio.to_thread。"""
from __future__ import annotations

import asyncio
import os
import threading
import time

from fastapi import APIRouter


def _snapshot_dir():
    return os.environ.get("GUANLAN_FUNDFLOW_DIR") or None


def build_fundflow_router() -> APIRouter:
    router = APIRouter()

    @router.get("/fundflow/live")
    async def live_ep(kind: str = "concept", refresh: int = 0):
        from . import pulse
        return await asyncio.to_thread(pulse.build_live, kind, bool(refresh), _snapshot_dir())

    @router.get("/fundflow/history")
    async def history_ep(kind: str = "concept", date: str = ""):
        from . import pulse
        return await asyncio.to_thread(pulse.load_history, kind, date, _snapshot_dir())

    return router


_POLLER_STARTED = [False]


def start_fundflow_poller() -> None:
    """opt-in:GUANLAN_FUNDFLOW_POLL=1 才起。进程内 daemon,交易时段每 N 秒拉两档落点。
    随本进程存亡——非 24/7 保证。幂等只起一次。"""
    if _POLLER_STARTED[0] or os.environ.get("GUANLAN_FUNDFLOW_POLL") != "1":
        return
    _POLLER_STARTED[0] = True
    interval = int(os.environ.get("GUANLAN_FUNDFLOW_POLL_SEC") or 180)

    def _loop():
        from datetime import datetime
        from . import pulse
        while True:
            try:
                if pulse._is_trading(datetime.now()):
                    pulse.build_live("concept", refresh=False, snapshot_dir=_snapshot_dir())
            except Exception:
                pass
            time.sleep(max(30, interval))

    threading.Thread(target=_loop, name="fundflow-poller", daemon=True).start()
```

改 `guanlan_v2/fundflow/__init__.py` 为:
```python
# -*- coding: utf-8 -*-
"""观澜 · 板块资金流向(实时)——纯展示层,绝不混入交易信号。"""
from .api import build_fundflow_router, start_fundflow_poller  # noqa: F401
```

在 `guanlan_v2/server.py` macro router 挂载之后(第 264 行 `app.include_router(build_macro_router())` 下方)加:
```python
    # ── 板块资金流向(fundflow):GET /fundflow/live、/fundflow/history ──
    # 纯展示层(2026-07-08 spec);盘中多线由 var/fundflow/<当日>.jsonl 自累快照重建
    from guanlan_v2.fundflow import build_fundflow_router, start_fundflow_poller
    app.include_router(build_fundflow_router())
    start_fundflow_poller()   # opt-in;GUANLAN_FUNDFLOW_POLL=1 才真起(非 24/7)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_fundflow_api.py -v`
Expected: PASS(2 测;`live` 端点真调 stocks 探针,机器无 stocks 时返 `ok:False` 但 200,断言仅查 `"ok" in body`,通过)

- [ ] **Step 5: Commit**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/fundflow/api.py guanlan_v2/fundflow/__init__.py guanlan_v2/server.py tests/test_fundflow_api.py && git commit -m "feat(fundflow): /fundflow/live+history 路由 + opt-in 盘中 poller + server 接线

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: guanlan 前端 — 外壳 + data 层

**Files:**
- Create: `G:\guanlan-v2\ui\fundflow\观澜 · 资金流向.html`
- Create: `G:\guanlan-v2\ui\fundflow\fundflow-data.jsx`
- Create: `G:\guanlan-v2\ui\fundflow\fundflow-app.jsx`(本任务先放占位,Task 10 覆盖)

**Interfaces:**
- Produces: `window.glFetchFundflowLive(kind, refresh)`、`window.glFetchFundflowHistory(kind, date)`。

- [ ] **Step 1: 写外壳 HTML**

Create `ui/fundflow/观澜 · 资金流向.html`:
```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>观澜 · 资金流向</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500;600;700;900&family=Noto+Sans+SC:wght@300;400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" />
<link rel="stylesheet" href="../industry/gl-ds.css?v=1" />
<script crossorigin src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js"></script>
</head>
<body>
<div id="root"></div>
<script>
  window.GUANLAN_BACKEND = (location.protocol === "http:" || location.protocol === "https:") ? location.origin : "";
</script>
<script src="../_shared/guanlan-bus.js?v=4"></script>
<script src="../_shared/guanlan-nav.js?v=5"></script>
<script type="text/babel" data-presets="env,react" src="fundflow-data.jsx?v=1"></script>
<script type="text/babel" data-presets="env,react" src="fundflow-app.jsx?v=1"></script>
</body>
</html>
```

- [ ] **Step 2: 写 data 层**

Create `ui/fundflow/fundflow-data.jsx`:
```jsx
/* 观澜 · 资金流向 — 数据层:真后端优先,file:// 直开显示断供占位(不合成假数据)。 */
const API = window.GUANLAN_BACKEND || "";

async function glFetchFundflowLive(kind, refresh) {
  if (!API) return { ok: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    const q = `kind=${encodeURIComponent(kind || "concept")}${refresh ? "&refresh=1" : ""}`;
    return await (await fetch(`${API}/fundflow/live?${q}`)).json();
  } catch (e) {
    return { ok: false, reason: `后端不可达: ${e}` };
  }
}

async function glFetchFundflowHistory(kind, date) {
  if (!API) return { ticks: [], boards: [], market_series: { main_net: [] } };
  try {
    const q = `kind=${encodeURIComponent(kind || "concept")}${date ? `&date=${date}` : ""}`;
    return await (await fetch(`${API}/fundflow/history?${q}`)).json();
  } catch (e) {
    return { ticks: [], boards: [], market_series: { main_net: [] } };
  }
}

Object.assign(window, { glFetchFundflowLive, glFetchFundflowHistory });
```

- [ ] **Step 3: 写占位 app + 验证外壳可载**

Create `ui/fundflow/fundflow-app.jsx`(临时占位,Task 10 覆盖):
```jsx
ReactDOM.createRoot(document.getElementById("root")).render(
  React.createElement("div", { style: { padding: 40 } }, "资金流向页 · 骨架就位"));
```
起 9998,浏览器开 `http://127.0.0.1:9998/app/fundflow/观澜 · 资金流向.html` 应见导航条 + "骨架就位",F12 无报错。

Run(临时起服务,避杀 9999):
```bash
cd "G:/guanlan-v2" && GUANLAN_PORT=9998 python -m guanlan_v2.server &
```

- [ ] **Step 4: Commit**

```bash
cd "G:/guanlan-v2" && git add "ui/fundflow/观澜 · 资金流向.html" ui/fundflow/fundflow-data.jsx ui/fundflow/fundflow-app.jsx && git commit -m "feat(fundflow-ui): 资金流向页外壳 + data 层(真后端优先·file直开断供占位)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: guanlan 前端 — `fundflow-app.jsx` 组件树

**Files:**
- Modify: `G:\guanlan-v2\ui\fundflow\fundflow-app.jsx`(覆盖占位为完整组件树)
- Modify: `G:\guanlan-v2\ui\fundflow\观澜 · 资金流向.html`(bump `fundflow-app.jsx?v=1` → `?v=2`)

**Interfaces:**
- Consumes: `glFetchFundflowLive`、`glFetchFundflowHistory`(Task 9)

- [ ] **Step 1: 写完整组件树**

覆盖 `ui/fundflow/fundflow-app.jsx`:
```jsx
/* 观澜 · 板块资金流向 — 纯展示层(绝不混入交易信号)。红涨绿跌 A股口径。 */
const { useState, useEffect, useCallback } = React;

const YI = 1e8;
const fmtYi = (v) => (v == null ? "—" : (v / YI).toFixed(2) + "亿");
const flowColor = (v) => (v == null ? "var(--ink-3)" : v >= 0 ? "var(--zhu)" : "var(--dai)");

/* 涨跌头条 */
function BreadthStrip({ b }) {
  const cell = (label, up, down) => (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginRight: 22 }}>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--zhu)" }}>涨 {up == null ? "—" : up}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--dai)" }}>跌 {down == null ? "—" : down}</span>
    </div>
  );
  const a = (b && b.allA) || {}, i = (b && b.industry) || {}, c = (b && b.concept) || {};
  return (
    <div style={{ display: "flex", flexWrap: "wrap", padding: "8px 12px", background: "var(--paper-1)",
                  border: "1px solid var(--line-2)", borderRadius: 6, marginBottom: 12 }}>
      {cell("全A", a.up, a.down)}{cell("行业", i.up, i.down)}{cell("概念", c.up, c.down)}
    </div>
  );
}

/* 大盘五档分解(水平条) */
function MarketFlowBars({ m }) {
  const items = [["超大单", m && m.super_net], ["大单", m && m.large_net],
                 ["中单", m && m.mid_net], ["小单", m && m.small_net], ["主力", m && m.main_net]];
  const max = Math.max(1, ...items.map(([, v]) => Math.abs(v || 0)));
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  padding: "10px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", marginBottom: 6 }}>大盘资金</div>
      {items.map(([label, v]) => (
        <div key={label} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0" }}>
          <span style={{ width: 44, fontSize: 12, color: "var(--ink-2)" }}>{label}</span>
          <div style={{ flex: 1, height: 12, position: "relative", background: "var(--paper-sink)", borderRadius: 3 }}>
            <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--line-3)" }} />
            <div style={{ position: "absolute", top: 1, bottom: 1, borderRadius: 2, background: flowColor(v),
                          width: `${(Math.abs(v || 0) / max) * 50}%`,
                          left: (v || 0) >= 0 ? "50%" : undefined,
                          right: (v || 0) < 0 ? "50%" : undefined }} />
          </div>
          <span style={{ width: 74, textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12,
                         color: flowColor(v) }}>{fmtYi(v)}</span>
        </div>
      ))}
    </div>
  );
}

/* 盘中多线图(纯 SVG,放大版 Spark) */
function IntradayChart({ hist }) {
  const boards = (hist && hist.boards) || [];
  const ticks = (hist && hist.ticks) || [];
  if (ticks.length < 2 || !boards.length)
    return <div style={{ padding: 24, fontSize: 12, color: "var(--ink-3)", background: "var(--paper-1)",
                         border: "1px solid var(--line-2)", borderRadius: 6, marginBottom: 12 }}>
      盘中数据累计中(每次刷新落一点,开盘后逐步成线;开 GUANLAN_FUNDFLOW_POLL=1 全时段成线)</div>;
  const W = 900, H = 380, PL = 8, PR = 120, PT = 12, PB = 18;
  const all = boards.flatMap((b) => b.series).filter((v) => v != null);
  const maxAbs = Math.max(1, ...all.map((v) => Math.abs(v)));
  const x = (i) => PL + (i * (W - PL - PR)) / (ticks.length - 1);
  const y = (v) => PT + (H - PT - PB) * (0.5 - (v / maxAbs) * 0.5);
  const seg = (series) => {
    const parts = [];
    let cur = [];
    series.forEach((v, i) => {
      if (v == null) { if (cur.length) parts.push(cur); cur = []; }
      else cur.push(`${x(i)},${y(v)}`);
    });
    if (cur.length) parts.push(cur);
    return parts;
  };
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6,
                  padding: 10, marginBottom: 12, overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block" }}>
        <line x1={PL} y1={y(0)} x2={W - PR} y2={y(0)} stroke="var(--line-3)" strokeWidth="1" />
        {boards.map((b) => {
          const last = [...b.series].reverse().find((v) => v != null);
          const col = flowColor(last);
          const li = b.series.map((v, i) => (v == null ? null : i)).filter((i) => i != null).slice(-1)[0];
          return (
            <g key={b.name}>
              {seg(b.series).map((pts, pi) => (
                <polyline key={pi} points={pts.join(" ")} fill="none" stroke={col} strokeWidth="1.4" opacity="0.85" />
              ))}
              {li != null && (
                <text x={x(li) + 4} y={y(b.series[li]) + 3} fontSize="10" fill={col}
                      style={{ fontFamily: "var(--font-mono)" }}>{b.name} {fmtYi(last)}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* 板块排行榜 */
function BoardRankTable({ boards }) {
  const top = (boards || []).slice(0, 20);
  return (
    <div style={{ background: "var(--paper-1)", border: "1px solid var(--line-2)", borderRadius: 6, padding: "6px 12px" }}>
      <div style={{ fontSize: 12, color: "var(--ink-2)", padding: "4px 0 6px" }}>板块净流入排行(前 20)</div>
      {top.map((b) => (
        <div key={b.code || b.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0",
                                             borderTop: "1px solid var(--line-1)", fontSize: 12 }}>
          <span style={{ width: 20, fontFamily: "var(--font-mono)", color: "var(--ink-3)" }}>{b.rank}</span>
          <span style={{ flex: 1, color: "var(--ink-1)" }}>{b.name}</span>
          {typeof b.delta_intraday === "number" && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: flowColor(b.delta_intraday) }}>
              {b.delta_intraday >= 0 ? "▲" : "▼"}{fmtYi(Math.abs(b.delta_intraday))}</span>
          )}
          <span style={{ fontFamily: "var(--font-mono)", color: flowColor(b.change_pct) }}>
            {b.change_pct >= 0 ? "+" : ""}{Number(b.change_pct).toFixed(2)}%</span>
          <span style={{ width: 82, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700,
                         color: flowColor(b.main_net) }}>{fmtYi(b.main_net)}</span>
        </div>
      ))}
    </div>
  );
}

function App() {
  const [kind, setKind] = useState("concept");
  const [live, setLive] = useState(null);
  const [hist, setHist] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async (k, refresh) => {
    setLoading(true);
    const [lv, hs] = await Promise.all([window.glFetchFundflowLive(k, refresh),
                                        window.glFetchFundflowHistory(k, "")]);
    setLive(lv); setHist(hs); setLoading(false);
  }, []);
  useEffect(() => { load(kind, true); }, [kind, load]);

  if (live && live.ok === false && live.reason)
    return <div style={{ padding: 40, fontFamily: "var(--font-serif)", color: "var(--ink-2)" }}>
      资金流向断供:{live.reason}</div>;

  const notes = (live && live.notes) || [];
  const tab = (k, label) => (
    <button onClick={() => setKind(k)} data-hv="zhu"
      style={{ fontFamily: "var(--font-serif)", fontSize: 13, padding: "4px 14px", cursor: "pointer",
               background: kind === k ? "var(--zhu)" : "var(--paper-0)",
               color: kind === k ? "var(--text-on-ink)" : "var(--ink-1)",
               border: "1px solid var(--line-3)", borderRadius: 4 }}>{label}</button>
  );
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "18px 22px 40px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h1 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--ink-0)", margin: 0 }}>
          板块资金流向</h1>
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>盘中主力净流入 · 纯展示参考,非交易信号</span>
        <span style={{ display: "flex", gap: 6, marginLeft: 8 }}>{tab("concept", "概念")}{tab("industry", "行业")}</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          {live && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10,
                                  color: live.trading ? "var(--zhu)" : "var(--ink-3)" }}>
            {live.trading ? "盘中" : "非交易"} · {String(live.pulled_at || "").slice(0, 16).replace("T", " ")}</span>}
          <button onClick={() => load(kind, true)} disabled={loading} data-hv="zhu"
            style={{ fontFamily: "var(--font-serif)", fontSize: 12, padding: "5px 16px", cursor: "pointer",
                     background: "var(--paper-0)", color: "var(--ink-1)", border: "1px solid var(--line-3)", borderRadius: 4 }}>
            {loading ? "拉取中…" : "刷新"}</button>
        </span>
      </div>

      <BreadthStrip b={live && live.breadth} />
      <MarketFlowBars m={(live && live.market) || {}} />
      <IntradayChart hist={hist} />
      <BoardRankTable boards={(live && live.boards) || []} />

      {notes.length > 0 && (
        <div style={{ marginTop: 14, padding: "8px 12px", background: "var(--paper-sink)",
                      border: "1px solid var(--line-2)", borderRadius: 4 }}>
          {notes.map((n, i) => (
            <div key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-2)", padding: "1px 0" }}>⚠ {n}</div>
          ))}
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
```

- [ ] **Step 2: bump `?v` 并验证渲染**

把 `观澜 · 资金流向.html` 里 `fundflow-app.jsx?v=1` 改为 `?v=2`。起 9998(若未起),浏览器开页面确认:概念/行业 Tab、涨跌头条、大盘五档条、盘中图占位或线、排行榜均渲染,F12 console 干净。

- [ ] **Step 3: Commit**

```bash
cd "G:/guanlan-v2" && git add ui/fundflow/fundflow-app.jsx "ui/fundflow/观澜 · 资金流向.html" && git commit -m "feat(fundflow-ui): 组件树(涨跌头条/大盘分解/SVG盘中多线/排行榜)+概念行业双档

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: guanlan 前端 — 观澜导航加页签

**Files:**
- Modify: `G:\guanlan-v2\ui\_shared\guanlan-nav.js`(`MODULES` +1)
- Modify: 全站 `ui\*\观澜 · *.html`(bump `guanlan-nav.js?v=4` → `?v=5`)

**Interfaces:** 无(纯前端导航)。

- [ ] **Step 1: 加页签**

`ui/_shared/guanlan-nav.js` 的 `MODULES` 数组在 `{ label: '全球情绪', ... }` 之后加:
```js
    { label: '资金流向', file: '../fundflow/观澜 · 资金流向.html' },
```

- [ ] **Step 2: 全站 bump nav 版本**

把每个 `ui/*/观澜 · *.html` 里的 `guanlan-nav.js?v=4` 改为 `guanlan-nav.js?v=5`(资金流向页 Task 9 已写 v=5)。定位:
```bash
cd "G:/guanlan-v2" && grep -rl "guanlan-nav.js?v=4" ui/
```
逐个改为 `?v=5`(约 9 个活跃页面;`ui/_archive/` 归档页可跳过)。

- [ ] **Step 3: 验证导航互通**

起 9998,任开一页(如全球情绪),确认顶栏出现「资金流向」页签、点击跳转到资金流向页、高亮正确。

- [ ] **Step 4: Commit**

```bash
cd "G:/guanlan-v2" && git add ui/_shared/guanlan-nav.js ui/ && git commit -m "feat(nav): 观澜顶栏加「资金流向」页签 + 全站 nav ?v=5 bump

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: guanlan — `ww_fundflow` 帷幄工具

**Files:**
- Modify: `G:\guanlan-v2\guanlan_v2\console\tools.py`(加工具函数 + 工具表登记)
- Modify: `G:\guanlan-v2\guanlan_v2\console\api.py`(`CONSOLE_ALLOWED` + `_SYSTEM_PROMPT` + 守护计数,四处同步)
- Test: `G:\guanlan-v2\tests\test_console_tools.py`

**前置阅读**(实现者先做,确认真实签名):`grep -n "def ww_live_text\|def ww_macro_pulse\|def _wrap\|CONSOLE_ALLOWED\|WW_TOOL_TABLE\|_SYSTEM_PROMPT" guanlan_v2/console/tools.py guanlan_v2/console/api.py`。照现有数据型 ww 工具(如 `ww_live_text`)的 `_wrap` 信封 + 全量 content 组装惯例;memory:weiwo-capability-expansion —— 改帷幄工具须四处同步(工具表 + `CONSOLE_ALLOWED` + `_SYSTEM_PROMPT` + 守护计数)。

**Interfaces:**
- Produces: `ww_fundflow(kind="concept")` —— 现拉板块资金流,返回**全量 content**(排行榜前 20 + 大盘分解 + 涨跌头条),content 键完整不被 `_wrap` 截 400。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_console_tools.py`(照现有 ww 工具测试模式,穿 `_wrap` 信封):
```python
def test_ww_fundflow_full_content_envelope(monkeypatch):
    from guanlan_v2.console import tools
    fake = {"ok": True, "kind": "concept", "trading": True, "pulled_at": "2026-07-08T10:57:00",
            "market": {"super_net": -1.93e10, "large_net": -2.17e10, "mid_net": -1.39e8,
                       "small_net": 4.11e10, "main_net": -4.10e10},
            "breadth": {"allA": {"up": 1886, "down": 3458}, "industry": {"up": 149, "down": 347},
                        "concept": {"up": 178, "down": 317}},
            "boards": [{"code": "BK1", "name": "算力概念", "main_net": 9.45e9, "change_pct": 2.1, "rank": 1}],
            "notes": []}
    monkeypatch.setattr("guanlan_v2.fundflow.pulse.build_live", lambda *a, **k: fake)
    out = tools.ww_fundflow(kind="concept")
    body = out.get("content") if isinstance(out, dict) else str(out)
    assert "算力概念" in body and "9.45" in body        # 全量,未被 400 截
    assert "1886" in body                                # 涨跌头条在
    assert len(body) > 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_console_tools.py::test_ww_fundflow_full_content_envelope -v`
Expected: FAIL(`module 'guanlan_v2.console.tools' has no attribute 'ww_fundflow'`)

- [ ] **Step 3: 实现工具 + 四处同步**

(a) `console/tools.py` 加工具(`_wrap` 的确切签名以前置阅读为准;关键是 content 键含全量文本、不被 400 截——memory 大教训):
```python
def ww_fundflow(kind: str = "concept") -> dict:
    """现拉板块资金流(concept|industry):排行榜前 20 + 大盘分解 + 涨跌头条。纯展示。"""
    from guanlan_v2.fundflow import pulse
    d = pulse.build_live(kind, refresh=True)
    if not d.get("ok"):
        return _wrap("ww_fundflow", {"ok": False}, content=";".join(d.get("notes") or ["不可用"]))
    yi = lambda v: "—" if v is None else f"{v/1e8:.2f}亿"
    m, b = d.get("market") or {}, d.get("breadth") or {}
    lines = [f"板块资金流 · {d.get('kind')} · {'盘中' if d.get('trading') else '非交易'} · {d.get('pulled_at')}",
             f"大盘:主力{yi(m.get('main_net'))} 超大{yi(m.get('super_net'))} 大{yi(m.get('large_net'))} "
             f"中{yi(m.get('mid_net'))} 小{yi(m.get('small_net'))}",
             f"涨跌:全A 涨{(b.get('allA') or {}).get('up')}/跌{(b.get('allA') or {}).get('down')} "
             f"行业 涨{(b.get('industry') or {}).get('up')}/跌{(b.get('industry') or {}).get('down')} "
             f"概念 涨{(b.get('concept') or {}).get('up')}/跌{(b.get('concept') or {}).get('down')}",
             "排行(前20):"]
    for x in (d.get("boards") or [])[:20]:
        lines.append(f"  {x.get('rank')}. {x.get('name')} 主力{yi(x.get('main_net'))} "
                     f"涨跌{float(x.get('change_pct') or 0):+.2f}%")
    return _wrap("ww_fundflow", {"ok": True}, content="\n".join(lines))
```
注:若 `_wrap` 无 `content=` 形参,按 `ww_live_text` 的真实调用形组装(把全量文本放进信封的 content 键)。

(b) 四处同步(照 memory weiwo-capability-expansion):
- `console/tools.py`:`ww_fundflow` 登记进工具表(照 `ww_live_text`/`ww_macro_pulse` 同处;如有 `WW_TOOL_TABLE` 就加一行)。
- `console/api.py` `CONSOLE_ALLOWED` 白名单加 `"ww_fundflow"`。
- `console/api.py` `_SYSTEM_PROMPT` 工具清单加一行 `ww_fundflow` 说明。
- 守护计数断言 +1(定位:`grep -rn "CONSOLE_ALLOWED\|守护计数\|len(.*ALLOWED\|工具.*计数" tests/`,把期望计数值 +1)。

- [ ] **Step 4: 跑测试确认通过 + console 全测**

Run: `cd "G:/guanlan-v2" && python -m pytest tests/test_console_tools.py -q`
Expected: 全绿(含新测 + 守护计数)。

- [ ] **Step 5: Commit**

```bash
cd "G:/guanlan-v2" && git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py && git commit -m "feat(console): ww_fundflow 帷幄工具(全量content信封)+白名单四处同步

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 13: 真机端到端 + 全量回归

**Files:** 无代码改动(验证任务;发现问题回相应 Task 修)。

- [ ] **Step 1: guanlan 全量回归**

Run: `cd "G:/guanlan-v2" && python -m pytest -q`
Expected: 全绿(基线 ~1024 + 本计划新测,0 fail)。

- [ ] **Step 2: stocks live-source 全测**

Run: `cd "G:/stocks" && python -m pytest scripts/test_live_text_sources.py -q`
Expected: 全绿。

- [ ] **Step 3: 真机起 9998 端到端**

Run:
```bash
cd "G:/guanlan-v2" && GUANLAN_FUNDFLOW_POLL=1 GUANLAN_PORT=9998 python -m guanlan_v2.server &
sleep 6
curl -s "http://127.0.0.1:9998/fundflow/live?kind=concept&refresh=1" | python -c "import sys,json; d=json.load(sys.stdin); print('ok',d['ok'],'trading',d['trading'],'boards',len(d.get('boards',[])),'main_net',d.get('market',{}).get('main_net')); print('note',d.get('notes'))"
sleep 200   # 让 poller 多落几点
curl -s "http://127.0.0.1:9998/fundflow/history?kind=concept" | python -c "import sys,json; d=json.load(sys.stdin); print('ticks',len(d['ticks']),'lines',len(d['boards']))"
```
Expected(交易时段):`ok True`、`boards` 非空、`main_net` 元级大数;`history` 的 `ticks` 随 poller 增长、`boards` ≤16 条。非交易时段:`trading False` 但仍 `ok True` 返上一档,`notes` 交代。

- [ ] **Step 4: 浏览器核对**

浏览器开 `http://127.0.0.1:9998/app/fundflow/观澜 · 资金流向.html`:
- 概念/行业 Tab 切换数据变化;
- 大盘五档条正红负绿、数值合理(亿级);
- 涨跌头条全A/行业/概念三组有数;
- 盘中图:交易时段多线成型(右缘板块名+亿);数据不足显累计中占位;
- 排行榜前 20 主力净额降序、Δ 徽章、涨跌幅红绿;
- F12 console 无报错。
截图留证。

- [ ] **Step 5: 收尾**

停 9998 实例(按 PID kill,或 `pkill -f "GUANLAN_PORT=9998"`),确认 9999 未受影响。全绿即功能交付完成。

---

## Self-Review(计划对 spec 的覆盖核对)

- spec §1 四要素(盘中多线/大盘分解/涨跌头条/排行榜)→ Task 6/7(数据)+ Task 10(BreadthStrip/MarketFlowBars/IntradayChart/BoardRankTable)。✓
- spec §3 架构(stocks 源 → datafeed → fundflow → ui)→ Task 1-2 / 4 / 5-8 / 9-11。✓
- spec §4 三源 → 简化为**一源 A** + 行业档加总导出大盘/涨跌(spec §4.3 兜底①提为主路径,计划 Task 6 实现;已在架构与 Task 6 注明,消除 B/C 未验证接口)。✓(优化,功能等价)
- spec §5 快照/history → Task 6(落点)/ Task 7(重建)。✓
- spec §6 实时驱动(on-view 落点 + opt-in poller)→ Task 6(refresh 落点)+ Task 8(poller)。✓
- spec §7 前端 → Task 9(外壳/data)+ Task 10(组件)。✓
- spec §8 server 接线 → Task 8 Step 3。✓
- spec §9 ww_fundflow → Task 12。✓
- spec §10 诚实红线 → Global Constraints + 各 degrade 分支 + notes。✓
- spec §11 测试 → Task 1/2(stocks)、4/5/6/7/8/12(guanlan 单测)、13(真机+回归)。✓
- spec §12 默认参数(前8+8/3分钟/前20/缺省concept)→ Global Constraints + Task 7(top_each=8)/ Task 8(180s)/ Task 10(slice 20)。✓

**偏离说明**:计划将 spec 的三源缩为一源 + 加总导出(行业板块=全市场互斥全覆盖,加总严格等于全市场分解与全A涨跌;概念档不可加总,故大盘/全A只用行业档)。这是对 spec §4.3 兜底①的主路径化,功能等价且更稳(不赌未验证的大盘/breadth 接口)。若后续要独立的大盘实时接口,可另起小任务补源 B。
