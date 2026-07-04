# 落子 K 线新闻标记泳道 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在落子校场 K 线图上叠加一条 PIT 安全的"新闻标记泳道"(回测回放 + 实时盯盘两态),聚类 `▣N` 徽章 + 关键词过滤 + 侧栏下钻,绝不显示决策日之后的新闻。

**Architecture:** 后端新增 `guanlan_v2/seats/news_marks.py` 装配器 —— 回测态复用现成 `PitReader`(`engine/financial_analyst/backtest/pit_reader.py`)按 `ts≤as-of边界` / `ann_date≤as-of日` 从 `G:\stocks\stock_data\pit_store` 取可见新闻/事件/政策;实时态复用 `KuaixunNewsProvider`。薄壳路由 `GET /seats/news`。前端在 `ui/seats/luozi-data.jsx` 加 `fetchNews`+`mapNewsToFrame`(镜像既有 `mapDecsToFrame` 的时间戳定位),`luozi-chart.jsx` 加一条 `<g>` 新闻泳道(受 `revealTo` 前端揭示墙约束),`luozi-app.jsx` 加关键词框 + 下钻面板并接线。

**Tech Stack:** Python 3 / FastAPI(APIRouter 工厂 `build_seats_router`);pandas(PitReader);浏览器全局脚本 + React(经 Babel,`ui/seats/*.jsx`);pytest(`PYTHONPATH=engine`);preview_* 工具做前端验证。

## Global Constraints

- **红线·无前视**:回测态绝不返回/渲染 `as-of` 之后的新闻。后端 `ts≤boundary`(news/policy)、`ann_date≤as-of日`(events)一闸;前端 `idx>revealTo` 不渲染二闸。两闸都要有测试/验证。
- **红线·诚实降级**:任何失败 / 无数据 → 空 `items` + note,`ok:True` 恒 HTTP200,**绝不编造**。`asof<news_coverage_floor`(2026-05-20)→ `coverage.partial=true`。
- **不动既有**:B/S 研判落子、真·思考金框、条件单触发环全部原样;新闻泳道只新增一个 `<g>` 与并行数据数组。
- **A 股代码前缀**:pit_store code 形如 `SH600519`/`SZ000630`;装配器入口统一 `_norm_code`(`600xxx→SH`、其余→`SZ`,已带前缀则保留)。
- **pit_store 事实源**:根 `G:\stocks\stock_data\pit_store\{YYYY-MM-DD}\{news,events,policy}.jsonl` + `_meta.json`;范围 2026-03-13→2026-07-01(74 交易日);`news_coverage_floor=2026-05-20`。字段见 `pit_reader.py` 的 `NewsItem/EventItem/PolicyItem`。
- **PitReader 真实语义**(以代码为准,非 spec 措辞):`get_visible_info(date, codes=[norm], as_of, lookback_days, include)` → `.news`(`ts≤boundary` 且 `code∈codes 或 code is None`)、`.events`(`ann_date≤date`,`code∈codes`)、`.policy`(`ts≤boundary` 且 `code∈codes 或 None`)。即"本票 tagged + 全市场 null-code 快讯/政策"入图,他票 specific-tagged 不入(避免串票噪声);关键词过滤在前端。
- **运行环境**:后端改动需**重启 9999** 才生效;前端改动需**bump 载入 HTML 里 `?v=` 查询串**(否则浏览器吃旧缓存);裸 pytest 必带 `PYTHONPATH=engine`(否则吃 pinned 旧引擎)。
- **本仓检查点约定**:每个 Task 末尾"检查点 = 全量 pytest 绿";**除非用户明确要求提交,不 `git commit`**(与近期 F10 批次同约定)。

---

## Phase A — 回测 PIT 新闻泳道(可离线全测,独立可交付)

### Task 1: 新闻装配器 `news_marks.py`(回测 PIT 态)

**Files:**
- Create: `guanlan_v2/seats/news_marks.py`
- Test: `tests/test_news_marks.py`

**Interfaces:**
- Consumes: `financial_analyst.backtest.pit_reader.PitReader.get_visible_info(date, codes, as_of, lookback_days, include)` → `VisibleInfo(.news/.events/.policy)`;`PitReader(store_root=, day_loader=)` 可注入(测试用假 loader)。
- Produces:
  - `assemble_news_marks(code:str, asof:str="", mode:str="pit", window:int=250, *, reader=None, provider=None) -> dict`
  - 返回 `{"ok":bool,"code":str,"mode":str,"asof":str,"items":[{"ts","date","title","source","code","level","body_head"}],"coverage":{"floor","range","partial","note"},"provenance":{"source","rows"}}`
  - `level ∈ {"stock","macro","policy","event"}`;`items` 按 `ts` 升序。
  - `_norm_code(code)->str`。

- [ ] **Step 1: 写失败测试** `tests/test_news_marks.py`

```python
import json, sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd
from financial_analyst.backtest.pit_reader import PitReader
from guanlan_v2.seats import news_marks as nm


class _FakeLoader:
    """最小 day_loader:给 PitReader 一份日历 + data_end 探针。"""
    def __init__(self, days):
        self._days = [pd.Timestamp(d) for d in days]
    def _load_calendar(self, freq):
        return self._days
    def fetch_quote(self, code, s, e, f):
        return pd.DataFrame({"trade_date": [self._days[-1]], "close": [10.0]})


def _mk_store(tmp, day, rows):
    d = tmp / day
    d.mkdir(parents=True, exist_ok=True)
    for kind in ("news", "events", "policy"):
        lines = [json.dumps(r, ensure_ascii=False) for r in rows.get(kind, [])]
        (d / f"{kind}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _reader(tmp, days):
    return PitReader(store_root=tmp, day_loader=_FakeLoader(days))


def test_pit_drops_future_and_after_boundary(tmp_path):
    days = ["2026-05-25", "2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"news": [
        {"ts": "2026-05-27T09:00:00", "date": "2026-05-27", "session": "open", "code": None, "title": "早间宏观", "body": "x"},
        {"ts": "2026-05-27T16:30:00", "date": "2026-05-27", "session": "post", "code": None, "title": "盘后消息", "body": "x"},
    ]})
    _mk_store(tmp_path, "2026-05-28", {"news": [
        {"ts": "2026-05-28T09:00:00", "date": "2026-05-28", "session": "open", "code": None, "title": "未来新闻", "body": "x"},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "早间宏观" in titles
    assert "盘后消息" not in titles      # ts>15:00 边界
    assert "未来新闻" not in titles      # 未来日 未读
    assert all(it["ts"][:10] <= "2026-05-27" for it in out["items"])


def test_events_filtered_by_ann_date(tmp_path):
    days = ["2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"events": [
        {"ann_date": "2026-05-26", "code": "SZ000630", "type": "block_trade", "summary": "大宗交易", "fields": {"visible_ts": "2026-05-26T23:59:59"}},
        {"ann_date": "2026-05-28", "code": "SZ000630", "type": "x", "summary": "未来事件", "fields": {}},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "大宗交易" in titles and "未来事件" not in titles
    assert [it for it in out["items"] if it["title"] == "大宗交易"][0]["level"] == "event"


def test_code_filter_keeps_stock_and_macro_drops_others(tmp_path):
    days = ["2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"news": [
        {"ts": "2026-05-27T10:00:00", "date": "2026-05-27", "session": "am", "code": "SZ000630", "title": "本票消息", "body": "x"},
        {"ts": "2026-05-27T10:01:00", "date": "2026-05-27", "session": "am", "code": None, "title": "宏观加息", "body": "x"},
        {"ts": "2026-05-27T10:02:00", "date": "2026-05-27", "session": "am", "code": "SH600519", "title": "茅台消息", "body": "x"},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "本票消息" in titles and "宏观加息" in titles and "茅台消息" not in titles
    lv = {it["title"]: it["level"] for it in out["items"]}
    assert lv["本票消息"] == "stock" and lv["宏观加息"] == "macro"


def test_coverage_floor_partial(tmp_path):
    days = ["2026-05-18", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-18", {})
    (tmp_path / "_meta.json").write_text(json.dumps({
        "news_coverage_floor": "2026-05-20", "cal_start": "2026-03-13", "cal_end": "2026-07-01"}), encoding="utf-8")
    out = nm.assemble_news_marks("SZ000630", "2026-05-18", "pit", 250, reader=_reader(tmp_path, days))
    assert out["coverage"]["partial"] is True and out["coverage"]["note"]


def test_honest_empty_on_missing_asof(tmp_path):
    out = nm.assemble_news_marks("SZ000630", "", "pit", 250, reader=_reader(tmp_path, ["2026-05-27"]))
    assert out["ok"] is True and out["items"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `set PYTHONPATH=engine && python -m pytest tests/test_news_marks.py -q`
Expected: FAIL(`ModuleNotFoundError: guanlan_v2.seats.news_marks`)

- [ ] **Step 3: 写实现** `guanlan_v2/seats/news_marks.py`

```python
# -*- coding: utf-8 -*-
"""落子 K 线新闻标记装配器 —— 回测态 PIT(pit_store/PitReader)+ 实时态(KuaixunNewsProvider)。

红线:回测绝不返回 as-of 之后的新闻(ts≤boundary / ann_date≤date,双由 PitReader 保证);
失败/无数据 → 空 items + note,恒 ok:True,绝不编造。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_READER: Any = None      # 懒单例 PitReader(构造含日历/探针,较重 → 复用)
_LIVE: Any = None        # 懒单例 KuaixunNewsProvider


def _norm_code(code: str) -> str:
    s = re.sub(r"\D", "", str(code or ""))
    if len(s) != 6:
        return str(code or "").upper()
    up = str(code).upper()
    if up.startswith(("SH", "SZ", "BJ")):
        return up[:2] + s
    return ("SH" if s[0] == "6" else "SZ") + s


def _get_reader():
    global _READER
    if _READER is None:
        from financial_analyst.backtest.pit_reader import PitReader
        _READER = PitReader()
    return _READER


def _get_live():
    global _LIVE
    if _LIVE is None:
        from financial_analyst.watch.news import KuaixunNewsProvider
        _LIVE = KuaixunNewsProvider()
    return _LIVE


def _load_meta(root) -> dict:
    try:
        return json.loads((Path(root) / "_meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _head(s, n: int = 120) -> str:
    return str(s or "").strip().replace("\n", " ")[:n]


def _normalize(vi, norm: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for it in vi.news:
        items.append({"ts": it.ts, "date": it.date, "title": it.title,
                      "source": it.source, "code": it.code,
                      "level": "stock" if it.code == norm else "macro",
                      "body_head": _head(it.body)})
    for it in vi.policy:
        items.append({"ts": it.ts, "date": it.pub_date, "title": it.title,
                      "source": it.source, "code": it.code, "level": "policy",
                      "body_head": _head(it.summary)})
    for it in vi.events:
        vts = (it.fields or {}).get("visible_ts") or (str(it.ann_date) + "T00:00:00")
        items.append({"ts": vts, "date": it.ann_date,
                      "title": it.summary or it.type, "source": it.source,
                      "code": it.code, "level": "event", "body_head": _head(it.summary)})
    items.sort(key=lambda r: str(r.get("ts") or ""))
    return items


def _assemble_pit(code: str, asof: str, window: int, reader=None) -> dict:
    norm = _norm_code(code)
    asof = str(asof or "")
    base = {"ok": True, "code": norm, "mode": "pit", "asof": asof}
    if len(asof) < 10:
        return {**base, "items": [], "coverage": {"partial": False, "note": "缺 as-of"},
                "provenance": {"source": "pit_store", "rows": 0}}
    day = asof[:10]
    tm = asof[11:16] if len(asof) >= 16 else "15:00"
    rdr = reader or _get_reader()
    try:
        vi = rdr.get_visible_info(day, codes=[norm], as_of=tm,
                                  lookback_days=max(1, min(int(window or 250), 400)),
                                  include=("news", "events", "policy"))
        items = _normalize(vi, norm)
    except Exception as exc:  # noqa: BLE001 — 诚实降级,绝不 500
        return {**base, "items": [], "coverage": {"partial": False, "note": f"读取失败: {type(exc).__name__}"},
                "provenance": {"source": "pit_store", "rows": 0}}
    meta = _load_meta(rdr._root)
    floor = meta.get("news_coverage_floor")
    rng = [meta.get("cal_start"), meta.get("cal_end")]
    partial = bool(floor and day < floor)
    note = ""
    if partial:
        note = f"{floor} 之前语料稀疏,覆盖不全"
    elif rng[1] and day > str(rng[1]):
        note = "超出 pit_store 覆盖范围"
    return {**base, "items": items,
            "coverage": {"floor": floor, "range": rng, "partial": partial, "note": note},
            "provenance": {"source": "pit_store", "rows": len(items)}}


def _assemble_live(code: str, provider=None) -> dict:
    norm = _norm_code(code)
    prov = provider or _get_live()
    try:
        heads = prov.headlines(code) or []
    except Exception:  # noqa: BLE001
        heads = []
    items = [{"ts": "", "date": "", "title": h, "source": "eastmoney_kuaixun",
              "code": norm, "level": "stock", "body_head": ""} for h in heads]
    return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": items,
            "coverage": {"partial": False, "note": ""},
            "provenance": {"source": "kuaixun", "rows": len(items)}}


def assemble_news_marks(code: str, asof: str = "", mode: str = "pit",
                        window: int = 250, *, reader=None, provider=None) -> dict:
    if mode == "live":
        return _assemble_live(code, provider=provider)
    return _assemble_pit(code, asof, window, reader=reader)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `set PYTHONPATH=engine && python -m pytest tests/test_news_marks.py -q`
Expected: PASS(5 passed)

- [ ] **Step 5: 检查点** —— 全量后端测试绿:`set PYTHONPATH=engine && python -m pytest tests/test_news_marks.py -q`(本仓约定:测试绿即检查点,不提交除非用户要求)。

---

### Task 2: 路由 `GET /seats/news` + 真 pit_store 烟测

**Files:**
- Modify: `guanlan_v2/seats/api.py`(在 `build_seats_router()` 内、`@router.get("/decisions")`(约 581 行)之后加一个 GET 路由)
- Test: `tests/test_seats_news_route.py`

**Interfaces:**
- Consumes: `guanlan_v2.seats.news_marks.assemble_news_marks`(Task 1);`APIRouter`/`JSONResponse`(文件顶部 28-29 行已 import);`asyncio`(19 行已 import)。
- Produces: `GET /seats/news?code=&asof=&mode=pit&window=250` → Task 1 的 dict(恒 HTTP200)。

- [ ] **Step 1: 写失败测试** `tests/test_seats_news_route.py`

```python
import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from guanlan_v2.seats.api import build_seats_router


def _client():
    app = FastAPI()
    app.include_router(build_seats_router())
    return TestClient(app)


def test_news_route_missing_asof_is_honest_empty():
    r = _client().get("/seats/news?code=SZ000630&mode=pit")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["items"] == [] and j["mode"] == "pit"


def test_news_route_missing_code():
    r = _client().get("/seats/news?mode=pit&asof=2026-06-01")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_news_route_real_pit_smoke():
    import pytest
    if not pathlib.Path(r"G:\stocks\stock_data\pit_store").exists():
        pytest.skip("no pit_store on this machine")
    r = _client().get("/seats/news?code=SZ000630&asof=2026-06-01&mode=pit&window=60")
    j = r.json()
    assert j["ok"] is True and isinstance(j["items"], list)
    assert all(str(it["ts"])[:10] <= "2026-06-01" for it in j["items"])   # 无前视
```

- [ ] **Step 2: 跑测试确认失败**

Run: `set PYTHONPATH=engine && python -m pytest tests/test_seats_news_route.py -q`
Expected: FAIL(404 / 路由不存在 → 前两条断言失败)

- [ ] **Step 3: 写实现** —— 在 `guanlan_v2/seats/api.py` 的 `build_seats_router()` 内、`seats_decisions` 路由(约 581-611 行)之后插入:

```python
    @router.get("/news")
    async def seats_news(code: str = "", asof: str = "", mode: str = "pit", window: int = 250):
        """落子 K 线新闻标记流。回测 ``mode=pit`` 按 ``as-of`` PIT 过滤 pit_store;
        ``mode=live`` 取实时快讯。缺 code → ok:False;其余恒 HTTP200 诚实降级。"""
        if not str(code).strip():
            return JSONResponse({"ok": False, "reason": "缺 code", "items": []})
        try:
            from guanlan_v2.seats.news_marks import assemble_news_marks
            payload = await asyncio.to_thread(
                assemble_news_marks, code, asof, mode, int(window or 250))
            return JSONResponse(payload)
        except Exception as exc:  # noqa: BLE001 — 恒 200,诚实报因
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}", "items": []})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `set PYTHONPATH=engine && python -m pytest tests/test_seats_news_route.py -q`
Expected: PASS(真机若有 pit_store 则 3 passed,否则 2 passed + 1 skipped)

- [ ] **Step 5: 检查点** —— `set PYTHONPATH=engine && python -m pytest tests/test_news_marks.py tests/test_seats_news_route.py -q` 绿。后端改动**重启 9999** 方能被前端 fetch 到(执行时若要联调需重启)。

---

### Task 3: 前端 `fetchNews` + `mapNewsToFrame`(`luozi-data.jsx`)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(加两个函数;并入文件末尾的 `Object.assign(window, {...})` 导出)

**Interfaces:**
- Consumes: `window.GUANLAN_BACKEND`(基址,file:// 无则返回 null);`dispFrame.fbars`(帧 bar,`.date` 日线 10 位 / 日内 16 位 `YYYY-MM-DD HH:MM`)。
- Produces:
  - `window.lzFetchNews(code, asof, mode) -> Promise<payload|null>`
  - `window.lzMapNewsToFrame(items, fbars, keyword) -> [{idx, count, hit, items:[...]}]`

- [ ] **Step 1: 写实现**(纯函数,随后用 preview_eval 在真运行时验证)—— 在 `ui/seats/luozi-data.jsx` 中 `mapDecsToFrame`(约 754 行)之后加:

```javascript
// 拉新闻标记流(回测 PIT / 实时);无 GUANLAN_BACKEND 或失败 → null(调用方泳道静默空,诚实降级)。
async function fetchNews(code, asof, mode) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API) return null;
  const m = mode || 'pit';
  const q = '/seats/news?code=' + encodeURIComponent(code) + '&mode=' + m +
            (asof ? '&asof=' + encodeURIComponent(asof) : '') + '&window=250';
  try {
    const res = await fetch(API + q);
    if (!res.ok) return null;
    const j = await res.json();
    return (j && j.ok) ? j : null;
  } catch (e) { return null; }
}

// 把 PIT 新闻流按时间戳聚类到「当前显示帧」的 bar(镜像 mapDecsToFrame 的 locate 规则)。
//   pit_store ts 用 'T' 分隔(2026-06-01T09:31:00);日内帧 bar.date 用空格(YYYY-MM-DD HH:MM,收盘刻)→ 匹配前把 'T'→' '。
//   产出每 bar 一桶:{idx, count, hit(命中关键词), items}。keyword 空 → hit 恒 false(不高亮)。
function mapNewsToFrame(items, fbars, keyword) {
  if (!items || !items.length || !fbars || !fbars.length) return [];
  const intradayFrame = (fbars[0].date || '').length > 10;
  const byFull = {}, byDayLast = {}, dayBars = {};
  fbars.forEach((b, i) => {
    const dt = b.date || '';
    byFull[dt] = i;
    const day = dt.slice(0, 10);
    byDayLast[day] = i;
    (dayBars[day] || (dayBars[day] = [])).push({ i, dt });
  });
  const locate = (ts) => {
    if (!ts) return fbars.length - 1;                          // live 无 ts → 落最右 bar
    const day = ts.slice(0, 10);
    if (!intradayFrame) return byFull[day] != null ? byFull[day] : -1;
    if (ts.length > 10) {
      const norm = ts.replace('T', ' ');
      const key = norm.slice(0, 16);
      if (byFull[key] != null) return byFull[key];
      const arr = dayBars[day] || [];
      const hit = arr.find(x => x.dt >= norm);
      return hit ? hit.i : (byDayLast[day] != null ? byDayLast[day] : -1);
    }
    return byDayLast[day] != null ? byDayLast[day] : -1;
  };
  const kw = String(keyword || '').split('|').map(s => s.trim()).filter(Boolean);
  const matches = (t) => kw.length > 0 && kw.some(k => (t || '').indexOf(k) >= 0);
  const byIdx = {};
  items.forEach(it => {
    const idx = locate(String(it.ts || it.date || ''));
    if (idx < 0) return;
    (byIdx[idx] || (byIdx[idx] = [])).push(it);
  });
  const out = [];
  Object.keys(byIdx).forEach(k => {
    const grp = byIdx[k];
    out.push({ idx: +k, count: grp.length, hit: grp.some(it => matches(it.title)), items: grp });
  });
  return out;
}
```

- [ ] **Step 2: 并入导出** —— 在 `ui/seats/luozi-data.jsx` 末尾的 `Object.assign(window, { ... })` 中追加键:`lzFetchNews: fetchNews, lzMapNewsToFrame: mapNewsToFrame`。

- [ ] **Step 3: bump 缓存串** —— 在载入 `luozi-data.jsx` 的 HTML(落子页,搜 `luozi-data.jsx?v=`)把 `?v=` 版本号 +1,保证浏览器取新脚本。

- [ ] **Step 4: 真运行时验证**(preview)—— 起落子页,`preview_eval` 跑(日线帧,keyword 空 → hit 全 false;带 keyword → 命中桶 hit=true):

```javascript
(function(){
  const fbars=[{date:'2026-05-26'},{date:'2026-05-27'}];
  const items=[
    {ts:'2026-05-26T10:00:00',title:'宏观加息'},
    {ts:'2026-05-27T09:30:00',title:'本票公告'},
    {ts:'2026-05-27T14:00:00',title:'午后异动'},
  ];
  const m=window.lzMapNewsToFrame(items,fbars,'加息');
  return JSON.stringify(m.map(x=>({idx:x.idx,count:x.count,hit:x.hit})));
})()
```
Expected: `[{"idx":0,"count":1,"hit":true},{"idx":1,"count":2,"hit":false}]`(26 日 1 条命中关键词,27 日 2 条聚一桶未命中)。

- [ ] **Step 5: 检查点** —— preview_eval 输出与期望一致;后端全量 pytest 仍绿。

---

### Task 4: 图层 —— `CandleChart` 新闻泳道(`luozi-chart.jsx`)

**Files:**
- Modify: `ui/seats/luozi-chart.jsx`(`CandleChart` 签名 34 行;布局 48-52 行;新增泳道 `<g>` 在条件单触发标记后、as-of 墙前,约 251 行后)

**Interfaces:**
- Consumes: `newsMarkers` prop = `[{idx,count,hit,items}]`(Task 3);`onNewsClick(marker)` prop;既有 `xOf(i)`、`yP(p)`、`revealTo`(经 `rt`)、`vStart`、`vEnd`、`padT`。
- Produces: 价格区上方一条新闻泳道;`idx>Math.min(vEnd,rt)` 不渲染(前端揭示墙);命中 `hit` 金色高亮;`<title>` 悬停显前 3 标题;点击 `onNewsClick`。

- [ ] **Step 1: 扩签名 + 预留泳道高度** —— 34 行签名末尾加 `newsMarkers, onNewsClick`:

```javascript
function CandleChart({ bars, decisions, truedecs, activeSeats, selected, onSelect, revealTo, view, live, asOf, triggers, newsMarkers, onNewsClick }) {
```

48-52 行的布局改为(在 `priceTop` 之上预留 `newsLaneH`,量能区随 priceH 联动不变):

```javascript
  const padR = 50, padT = 10, padB = 20, padL = 6;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const newsLaneH = (newsMarkers && newsMarkers.length) ? 15 : 0;   // 新闻泳道预留带
  const priceH = plotH * 0.72 - newsLaneH, gap = plotH * 0.05, volH = plotH * 0.23;
  const priceTop = padT + newsLaneH, volTop = padT + priceH + gap;
```

- [ ] **Step 2: 渲染泳道** —— 在条件单触发 `<g>`(约 229-251 行 `))}` 结束)之后、as-of 墙(约 253 行 `{asOf && asOf.on &&`)之前插入:

```javascript
        {/* 新闻标记泳道(聚类 ▣N;命中关键词金色高亮;≤ revealTo 揭示墙约束;点击下钻)*/}
        {(newsMarkers || []).map((nmk, k) => {
          if (nmk.idx < vStart || nmk.idx > Math.min(vEnd, rt)) return null;
          const cx = xOf(nmk.idx);
          const stroke = nmk.hit ? 'var(--jin)' : 'var(--ink-3)';
          const label = '▣ ' + nmk.count;
          const wd = Math.max(26, 14 + String(nmk.count).length * 7);
          const ly = padT + 1;
          const tips = (nmk.items || []).slice(0, 3).map(it => (it.ts ? String(it.ts).slice(5, 16).replace('T', ' ') + ' ' : '') + it.title).join('\n');
          return (
            <g key={'nm' + k} style={{ cursor: 'pointer' }} onClick={() => onNewsClick && onNewsClick(nmk)}>
              <line x1={cx} x2={cx} y1={ly + 13} y2={yP(bars[nmk.idx].h) - 2} stroke={stroke} strokeWidth="0.6" strokeDasharray="2 2" opacity="0.35" />
              {nmk.hit && <rect x={cx - wd / 2 - 2} y={ly - 1} width={wd + 4} height={15} rx={4} fill="none" stroke="var(--jin)" strokeWidth="1.3" opacity="0.5" />}
              <rect x={cx - wd / 2} y={ly} width={wd} height={13} rx={3} fill={nmk.hit ? 'rgba(191,138,23,0.14)' : 'var(--paper)'} stroke={stroke} strokeWidth="0.7" filter="url(#lz-stamp)" />
              <text x={cx} y={ly + 9.5} textAnchor="middle" fontSize="9" fill={nmk.hit ? 'var(--jin)' : 'var(--ink-2)'} fontFamily="var(--mono)">{label}</text>
              <title>{tips}</title>
            </g>
          );
        })}
```

- [ ] **Step 3: bump 缓存串** —— 载入 `luozi-chart.jsx` 的 HTML 把 `luozi-chart.jsx?v=` 版本号 +1。

- [ ] **Step 4: 验证**(preview)—— 起落子页,`preview_eval` 临时塞一组标记验渲染(真数据由 Task 5 接线):

```javascript
window.__lztest = [{idx: 5, count: 3, hit: false, items:[{ts:'2026-05-20T10:00:00',title:'测试A'}]},
                   {idx: 9, count: 7, hit: true, items:[{ts:'2026-05-24T10:00:00',title:'加息'}]}];
```
确认(preview_snapshot / preview_screenshot):`▣ 3`(灰)、`▣ 7`(金)出现在对应 bar 上方;hover 显标题;既有 B/S 标记不变;越 `revealTo` 的标记不渲染。

- [ ] **Step 5: 检查点** —— 截图显示泳道正确叠加、不遮既有标记;越 `revealTo` 的桩标记不渲染。

---

### Task 5: 接线 —— 关键词框 + 取数 + 下钻面板(`luozi-app.jsx`)

**Files:**
- Modify: `ui/seats/luozi-app.jsx`(状态 + useEffect 取数;`chartMarks` 旁算 `newsMarkers`,约 651 行;`<CandleChart .../>` 传参,721 行;工具条加关键词框;图容器加下钻面板)

**Interfaces:**
- Consumes: `window.lzFetchNews`、`window.lzMapNewsToFrame`(Task 3);`dispFrame.fbars`、`asOfDate`、`pitOn`、当前 symbol 代码(与 `fetchQuote`/`fetchDailyBars` 同一 code 值);`onNewsClick`(Task 4)。
- Produces: 图上真新闻泳道;关键词即时过滤;点击徽章 → 下钻面板列该 bar 全部条目。

- [ ] **Step 1: 加状态 + 取数 effect** —— 在组件内(与其它 `useState` 相邻)加(文件既有 hook 用法为 `const { useState, useEffect, useMemo } = React` 解构则用同名;否则用 `React.useState`,以文件为准):

```javascript
  const [newsKw, setNewsKw] = useState('');
  const [newsPayload, setNewsPayload] = useState(null);
  const [newsPanel, setNewsPanel] = useState(null);   // 点开的 {idx,count,items}
```

在 `dispFrame` 定义(约 621 行)之后加取数 effect + 派生 markers(以当前 symbol 代码 + `asOfDate` 为键,debounce 250ms;PIT 态用 `asOfDate` 保证后端只回 ≤as-of):

```javascript
  const newsCode = symbol && (symbol.code || (symbol.meta && symbol.meta.code));   // 与 fetchQuote 同源代码
  useEffect(() => {
    if (!newsCode) { setNewsPayload(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      const mode = pitOn ? 'pit' : 'live';
      const asof = pitOn ? asOfDate : '';
      window.lzFetchNews && window.lzFetchNews(newsCode, asof, mode)
        .then(p => { if (alive) setNewsPayload(p); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [newsCode, asOfDate, pitOn]);

  const newsMarkers = useMemo(
    () => (window.lzMapNewsToFrame && newsPayload)
      ? window.lzMapNewsToFrame(newsPayload.items || [], dispFrame.fbars, newsKw) : [],
    [newsPayload, dispFrame, newsKw]);
```

- [ ] **Step 2: 传参给图 + 下钻回调** —— 721 行 `<CandleChart ... triggers={orderTriggers} />` 末尾(`/>` 前)加:

```javascript
 newsMarkers={newsMarkers} onNewsClick={setNewsPanel}
```

- [ ] **Step 3: 关键词框** —— 在图工具条(`ChartNav` 附近,约 724 行)加一个输入框(`|` 分割;命中金亮;覆盖不全时显角标):

```javascript
        <input value={newsKw} onChange={e => setNewsKw(e.target.value)}
          placeholder="新闻关键词 加息|非农|本票名"
          style={{ font: '12px var(--mono)', padding: '3px 8px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-1)', width: 180 }} />
        {newsPayload && newsPayload.coverage && newsPayload.coverage.partial &&
          <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 6 }}>· 覆盖不全 &lt;{newsPayload.coverage.floor}</span>}
```

- [ ] **Step 4: 下钻面板** —— 在图容器(含 `<CandleChart>` 的相对定位父 div;若无 `position:relative` 需补)内、图之后加:

```javascript
        {newsPanel && (
          <div style={{ position: 'absolute', top: 8, right: 8, width: 300, maxHeight: '70%', overflow: 'auto', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 8, boxShadow: '0 6px 20px rgba(28,24,20,0.18)', padding: '8px 10px', zIndex: 5 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <b style={{ fontSize: 12, color: 'var(--ink-1)' }}>当日快讯 · {newsPanel.count} 条</b>
              <span onClick={() => setNewsPanel(null)} style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--ink-3)', fontSize: 14 }}>×</span>
            </div>
            {(newsPanel.items || []).map((it, i) => {
              const hit = newsKw && newsKw.split('|').map(s => s.trim()).filter(Boolean).some(k => (it.title || '').indexOf(k) >= 0);
              return (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '5px 6px', borderRadius: 5, background: hit ? 'rgba(191,138,23,0.12)' : 'transparent' }}>
                  <span style={{ font: '11px var(--mono)', color: 'var(--ink-3)', minWidth: 40 }}>{String(it.ts || '').slice(5, 16).replace('T', ' ')}</span>
                  <span style={{ fontSize: 10, color: 'var(--ink-3)', border: '0.5px solid var(--line)', borderRadius: 4, padding: '0 5px' }}>{it.source || it.level}</span>
                  <span style={{ fontSize: 12, color: 'var(--ink-1)' }}>{it.title}</span>
                </div>
              );
            })}
          </div>
        )}
```

- [ ] **Step 5: bump 缓存串** —— 载入 `luozi-app.jsx` 的 HTML `?v=` +1。

- [ ] **Step 6: 端到端验证**(preview,真 pit_store)—— 重启 9999 → 起落子页 → 选一支票、回放到 05-20 之后覆盖良好的某日:
  - preview_screenshot:价格上方有 `▣N` 泳道;
  - 输入关键词(如某宏观词)→ 命中 bar 徽章转金;
  - 点一个徽章 → 右上"当日快讯"面板列出条目,时间/来源/标题齐全;
  - 把回放游标拖到更早 → as-of 墙右侧无任何新闻徽章(前端揭示墙生效);
  - preview_network 查 `/seats/news` 响应 `items` 全部 `ts[:10] ≤ asof`(无前视)。

- [ ] **Step 7: 检查点** —— 上述四条 preview 证据齐全;后端全量 pytest 仍绿。Phase A 独立可交付。

---

## Phase B — 实时新闻泳道(需 watch 实时环境;Phase A 之上的增量)

### Task 6: 实时态装配补测

**Files:**
- Modify: `tests/test_news_marks.py`(加 live 用例)
- (实现已在 Task 1 的 `_assemble_live` / Task 2 路由 `mode` 分发内 —— 本 Task 仅补测)

**Interfaces:**
- Consumes: `KuaixunNewsProvider.headlines(code) -> [title,...]`(可注入 `provider=` 测试桩)。
- Produces: `assemble_news_marks(code, mode="live", provider=stub)` → `items` 每条 `{ts:"",title,level:"stock",...}`。

- [ ] **Step 1: 写失败测试** —— 追加到 `tests/test_news_marks.py`:

```python
def test_live_uses_provider_headlines():
    class _Stub:
        def headlines(self, code):
            return ["实时利好一则", "实时利空一则"]
    out = nm.assemble_news_marks("SZ000630", mode="live", provider=_Stub())
    titles = [it["title"] for it in out["items"]]
    assert out["mode"] == "live" and titles == ["实时利好一则", "实时利空一则"]
    assert all(it["ts"] == "" for it in out["items"])


def test_live_provider_failure_is_empty():
    class _Boom:
        def headlines(self, code):
            raise RuntimeError("net down")
    out = nm.assemble_news_marks("SZ000630", mode="live", provider=_Boom())
    assert out["ok"] is True and out["items"] == []
```

- [ ] **Step 2: 跑测试** —— `set PYTHONPATH=engine && python -m pytest tests/test_news_marks.py -q`
Expected: PASS(Task 1 的 `_assemble_live` 已满足;若失败按报错修 `_assemble_live`)。

- [ ] **Step 3: 检查点** —— 全量 pytest 绿。

---

### Task 7: watch SSE 附 `news_marks`(实时上图)

**Files:**
- Modify: `engine/financial_analyst/buddy/server.py`(`/watch/stream` SSE 的 `quote_update` 事件体,约 1779-1827 行)
- (前端无需再改:Task 5 的取数 effect 在 `!pitOn` 时已走 `mode='live'` 定时拉 `/seats/news`;`ts=""` 条目经 Task 3 的 `locate` 落最右 bar。SSE 附字段为可选加速通道。)

**Interfaces:**
- Consumes: Task 1 的 `_assemble_live`(经 `assemble_news_marks(code, mode='live')`)。
- Produces: `/watch/stream` 的 `quote_update` 事件附 `news_marks` 字段(供未来直接消费;当前前端走 `/seats/news` 拉取即可)。

- [ ] **Step 1: SSE 附字段** —— 在 `/watch/stream` 组装 `quote_update` 数据的 dict(设其变量名为该事件既有 payload)处,追加:

```python
            # news_marks:当日实时快讯(诚实降级:失败/无源 → 空,绝不编造)
            try:
                from guanlan_v2.seats.news_marks import assemble_news_marks
                _nm = assemble_news_marks(code, mode="live").get("items", [])
            except Exception:  # noqa: BLE001
                _nm = []
            payload["news_marks"] = _nm
```
(`payload`/`code` 用该处既有变量名;若事件体是内联 dict,则加 `"news_marks": _nm` 键。)

- [ ] **Step 2: 验证**(需实时环境/开盘或 mock feed)—— 重启 9999;
  - 最低验收(无开盘也可):`curl` 或 preview_network 命中 `/seats/news?code=SZ000630&mode=live` 返回 `ok:True` 且 `items` 结构正确(`ts:""`);
  - 完整验收(开盘/mock feed):起盯盘态,最右 bar 出现实时快讯徽章、点开有条目;无 feed 时泳道诚实空、不报错。无实时环境则在交接标注"live 上图待开盘复验"。

- [ ] **Step 3: 检查点** —— 后端全量 pytest 绿;实时上图证据(截图)或"待开盘复验"诚实标注二选一记录。

---

## Phase C — 验收 + 记忆

### Task 8: 全局验收 + 更新记忆

- [ ] **Step 1: 全量测试** —— `set PYTHONPATH=engine && python -m pytest -q`;Expected: 全绿(新增 7 用例 + 既有不回归)。
- [ ] **Step 2: 真机端到端**(preview)—— 回测态一票(如 SZ000630,asof 取覆盖良好日)四条证据(泳道/关键词金亮/下钻/揭示墙无前视)截图留档;`/seats/news` network 响应抽查无前视。
- [ ] **Step 3: 更新记忆** —— 新建 `luozi-news-markers.md`(type project):FUSE 借鉴→落子新闻泳道全交付、PIT 双闸、pit_store 事实源与 `news_coverage_floor` 坑、`KuaixunNewsProvider` 实时源、`_norm_code`/events 按 `ann_date` 过滤(非 visible_ts)要点;并在 `MEMORY.md` 加一行钩子。链接 [[luozi-run-rework]] [[news-sentiment-research]] [[luozi-minute-backtest]]。
- [ ] **Step 4: 检查点** —— 交接:Phase A 已交付并验;Phase B live 上图若无开盘环境则标"待复验";backlog(方案③烤进 run 产物 / 全市场 live flash / 情绪上色)列明。

---

## 计划自审

- **Spec 覆盖**:§2 决策 1(两态)→ Task 5/7;决策 2(全量快讯+关键词)→ Task 1 code 过滤语义 + Task 3 关键词;决策 3(聚类+下钻)→ Task 4/5;决策 4(前端过滤)→ Task 3/5;决策 5(PIT 双闸)→ Task 1(后端)+ Task 4(前端 `idx>rt`);决策 6(诚实降级)→ Task 1 coverage/空 + Task 5 覆盖标注;决策 7(不接金十)→ 用 KuaixunNewsProvider。§5 契约 → Task 1/2/3 逐字落地。§9 降级表 → Task 1 note 分支。§10 测试 6 条 → Task 1(5)+Task 2(真烟测)+Task 6(live 2)。
- **spec 措辞纠偏**:spec §6 写"events 滤 visible_ts";PitReader 真实以 `ann_date≤date` 过滤(仍严格 PIT,ann_date=公告可见日)。计划以代码为准(Task 2 测试断言 ann_date),并在 Global Constraints 标明。
- **占位扫描**:无 TBD/TODO;每个改码步骤均附完整代码/命令/期望。
- **类型一致**:`assemble_news_marks(code,asof,mode,window,*,reader,provider)` 全 Task 一致;item 键 `{ts,date,title,source,code,level,body_head}` 前后端一致;`newsMarkers` 桶 `{idx,count,hit,items}` 在 Task 3 产出、Task 4 消费一致;`onNewsClick`/`newsMarkers` prop 名 Task 4/5 一致;`locate('')→最右 bar` 在 Task 3 定义、Task 7 依赖一致。
