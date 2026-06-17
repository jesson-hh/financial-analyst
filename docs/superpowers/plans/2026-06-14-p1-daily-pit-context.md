# P1 每日 PIT 上下文(叙事流 + 大盘日产物 + 删假料)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development(推荐)或 superpowers:executing-plans 逐任务实现。步骤用 checkbox(`- [ ]`)跟踪。
> 依据 spec:`docs/superpowers/specs/2026-06-14-backtest-trustworthy-cards-design.md`(P1 节)。

**Goal:** 让回测 agent 每个决策日拿到**当天 PIT 浮出的真叙事卡 + 当天真大盘日产物**,并删除所有没接入的假料(伪 IC、mock 证据层、demo 占位)。

**Architecture:** 新增后端纯函数模块 `seats/narrative.py`(叙事卡池装配 + `surface_narratives` 按日 PIT 浮出 + `regime_asof` 读 breadth/mainline 逐日产物);decide 接线用它替换"固定 research 透传"和"regime=null";前端 runRealThink 不再逐日重复喂固定 research(改后端按 `bar.date` 浮出),RunDecCard 显真叙事+大盘、删 mock 证据,distillToCard 降 draft,默认策略剥 demo 研报,校场/图谱 demo 改诚实空态。

**Tech Stack:** Python(FastAPI 薄壳 + 引擎 fork,pandas 读 parquet)、pytest(子进程钉 engine)、无构建前端(UMD React + babel,改 jsx 必 bump `?v`)。

**红线(贯穿所有任务):**
- **严格 PIT**:`surface_narratives`/`regime_asof` 绝不取 `as_of/date > D`;测试必须证伪未来泄漏。
- **诚实空**:无相关叙事卡 / 无大盘产物 → 返回空/None,**绝不补 mock**。
- 改 `.jsx` 必 bump `?v`(用 Edit,非 sed);改 `.py` 须重启 9999(杀监听 PID 等 ~10s 看门狗拉新)。
- 本仓**无 git**:任务里的"Commit"步 = **跑 pytest 绿**(不要 `git add/commit`);`G:/stocks` 只读;密钥不入库。
- 不做 P2/P3(vintage IC、量化卡加权混合进信号)。

---

## 文件结构(P1 落点)

- **新建** `guanlan_v2/seats/narrative.py` —— 纯逻辑:`build_pool()` 装配池、`surface_narratives(...)` 按日 PIT 选卡、`regime_asof(date, breadth_df)` 读逐日大盘产物。无 FastAPI 依赖,可子进程单测。
- **改** `guanlan_v2/seats/api.py` —— decide 内 research/regime 注入点(已读准 `:965-975`/`:1038`/落盘 `:1091`):调 narrative 模块替换固定透传 + 落盘 `narratives_surfaced`/`regime_asof_used`。
- **新建** `tests/test_seats_narrative.py` —— surface/regime PIT + decide 接线测试(子进程钉 engine)。
- **改** `ui/seats/luozi-app.jsx` —— runRealThink 不传固定 `rcp.research`(后端按日浮出);`distillToCard` 降 draft;默认策略剥 demo 研报 ref。bump `?v`。
- **改** `ui/seats/luozi-panels.jsx` —— RunDecCard 显真叙事+大盘(读落盘 `narratives_surfaced`)、删回测 regime/触发因子 mock。bump `?v`。
- **改** `ui/seats/luozi-data.jsx` —— 删 `evidenceFor()` 回测 mock 证据分支(无真则诚实空)。bump `?v`。
- **改** `ui/_shared/guanlan-bus.js` + `ui/seats/luozi-foundry.jsx` + `ui/graph/graph.jsx` —— demo 种子料退出真路径 + 列表改诚实空态(不再 demo 填充)。bump `?v`(html)。

---

## Task 1: `surface_narratives` 纯函数(按日 PIT 浮出)

**Files:**
- Create: `guanlan_v2/seats/narrative.py`
- Test: `tests/test_seats_narrative.py`

- [ ] **Step 1: 写失败测试**(`tests/test_seats_narrative.py`)

```python
from guanlan_v2.seats.narrative import surface_narratives

# 合成池:每条 {id, as_of, codes, industry, kind, title, insight}
_POOL = [
    {"id": "n1", "as_of": "2026-05-01", "codes": ["605358"], "industry": "半导体", "kind": "研报", "title": "立昂微深度", "insight": "硅片景气"},
    {"id": "n2", "as_of": "2026-06-09", "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "立昂微涨停", "insight": "放量封板"},
    {"id": "n3", "as_of": "2026-06-30", "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "未来新闻", "insight": "不该出现"},
    {"id": "n4", "as_of": "2026-06-08", "codes": ["300750"], "industry": "电池", "kind": "新闻", "title": "别的票", "insight": "不相关"},
]
_WIN = {"研报": 60, "新闻": 10, "复盘": 30}

def test_pit_never_future():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    assert "n3" not in [c["id"] for c in out], "未来卡泄漏 = PIT 破"

def test_window_by_kind():
    # 新闻窗 10 天 → n2(06-09,距1天)在;研报窗 60 天 → n1(05-01,距40天)在
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    ids = [c["id"] for c in out]
    assert "n2" in ids and "n1" in ids

def test_news_window_expires():
    # D=06-25:新闻 n2(距16天)超 10 天窗 → 掉;研报 n1(距55天)仍在 60 天窗
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-25", k=10, windows=_WIN)
    ids = [c["id"] for c in out]
    assert "n2" not in ids and "n1" in ids

def test_relevance_code_or_industry():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    assert "n4" not in [c["id"] for c in out]

def test_topk_recency():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=1, windows=_WIN)
    assert len(out) == 1 and out[0]["id"] == "n2"

def test_empty_honest():
    out = surface_narratives(_POOL, "999999", "无此行业", "2026-06-10", k=10, windows=_WIN)
    assert out == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -q`
Expected: FAIL(`ModuleNotFoundError` 或 `surface_narratives` 未定义)。

- [ ] **Step 3: 写最小实现**(`guanlan_v2/seats/narrative.py`)

```python
"""叙事卡池 + 按日 PIT 浮出 + 大盘日产物读取(纯逻辑,无 FastAPI 依赖)。

红线:绝不取 as_of/date > D(PIT);无料返回空/None,绝不补 mock。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Optional

DEFAULT_WINDOWS = {"研报": 60, "新闻": 10, "复盘": 30}
DEFAULT_K = 6


def _d(s) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:  # noqa: BLE001
        return None


def surface_narratives(pool, code, industry, as_of, k=DEFAULT_K, windows=None):
    """选出 as_of≤D、关联本票/行业、在各 kind 新近度窗口内的叙事卡,按 as_of 倒序取 topK。无料→[]。"""
    windows = windows or DEFAULT_WINDOWS
    dd = _d(as_of)
    if dd is None:
        return []
    code = str(code or "")
    out = []
    for c in pool or []:
        ad = _d(c.get("as_of"))
        if ad is None or ad > dd:           # PIT:无日期或未来 → 丢
            continue
        codes = [str(x) for x in (c.get("codes") or [])]
        rel = (code and code in codes) or (industry and c.get("industry") == industry)
        if not rel:
            continue
        if (dd - ad).days > windows.get(c.get("kind"), 30):   # 超新近度窗口 → 丢
            continue
        out.append(c)
    out.sort(key=lambda c: str(c.get("as_of")), reverse=True)
    return out[: max(0, int(k))]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -q`
Expected: PASS(6 passed)。

- [ ] **Step 5: "Commit"= 全量 pytest 绿**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest -q`
Expected: 既有 246 + 新增 全绿。**不要 git。**

---

## Task 2: 叙事卡池装配 `build_pool`

**Files:** Modify `guanlan_v2/seats/narrative.py` · Test `tests/test_seats_narrative.py`

两源:(a) GL 镜像档案 card 且 `tier=='narrative'`;(b) `out/` 深度研报(带落款日)。归一成池,**丢弃无 as_of(draft 不入池)与非 narrative**。

- [ ] **Step 1: 写失败测试**(追加)

```python
from guanlan_v2.seats.narrative import build_pool

def test_build_pool_normalizes_and_drops_undated():
    archive = [
        {"id": "a1", "type": "card", "tier": "narrative", "as_of": "2026-06-01",
         "codes": ["605358"], "industry": "半导体", "kind": "复盘", "title": "T", "insight": "I"},
        {"id": "a2", "type": "card", "tier": "narrative", "title": "无日期", "insight": "x"},   # 无 as_of → 丢
        {"id": "a3", "type": "card", "tier": "quant", "as_of": "2026-06-01", "title": "量化卡"},  # 非 narrative → 丢
    ]
    reports = [{"id": "r1", "as_of": "2026-05-20", "codes": ["605358"], "industry": "半导体",
                "title": "立昂微深度", "insight": "硅片", "path": "out/x.md"}]
    pool = build_pool(archive, reports)
    ids = {c["id"] for c in pool}
    assert ids == {"a1", "r1"}
    assert all(c.get("as_of") for c in pool)
    assert next(c for c in pool if c["id"] == "r1")["kind"] == "研报"
```

- [ ] **Step 2: 跑确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py::test_build_pool_normalizes_and_drops_undated -q`
Expected: FAIL(`build_pool` 未定义)。

- [ ] **Step 3: 写实现**(追加到 `narrative.py`)

```python
def build_pool(archive_cards, reports):
    """GL 档案叙事卡 + out/ 研报 → 统一池(丢无 as_of / 非 narrative)。"""
    pool = []
    for c in archive_cards or []:
        if c.get("type") == "card" and c.get("tier") != "narrative":
            continue
        if not _d(c.get("as_of")):
            continue
        pool.append({
            "id": c.get("id"), "as_of": str(c.get("as_of"))[:10],
            "codes": [str(x) for x in (c.get("codes") or [])],
            "industry": c.get("industry") or "", "kind": c.get("kind") or "复盘",
            "title": c.get("title") or "", "insight": c.get("insight") or c.get("verdict") or "",
            "source": c.get("source") or {}, "path": c.get("path"),
        })
    for r in reports or []:
        if not _d(r.get("as_of")):
            continue
        pool.append({
            "id": r.get("id"), "as_of": str(r.get("as_of"))[:10],
            "codes": [str(x) for x in (r.get("codes") or [])],
            "industry": r.get("industry") or "", "kind": r.get("kind") or "研报",
            "title": r.get("title") or "", "insight": r.get("insight") or "",
            "source": {"from": r.get("from") or ""}, "path": r.get("path"),
        })
    return pool
```

- [ ] **Step 4: 跑确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -q`
Expected: PASS(7 passed)。

- [ ] **Step 5: "Commit"= 全量 pytest 绿。**

---

## Task 3: `regime_asof(date, breadth_df)` 读逐日大盘产物(PIT)

**Files:** Modify `guanlan_v2/seats/narrative.py` · Test `tests/test_seats_narrative.py`

大盘日产物 = `MARKET_BREADTH_PARQUET`(datetime 索引逐日,已 Read 确认 `index.name='datetime'`)。`regime_asof` 取 **≤date 末行**(PIT)拼点评;无 ≤date 行 → None(诚实空)。测试用合成 df,不依赖真列名。

- [ ] **Step 1: 写失败测试**(追加)

```python
import pandas as pd
from guanlan_v2.seats.narrative import regime_asof

def _bdf():
    idx = pd.to_datetime(["2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10"])
    return pd.DataFrame({"breadth": [0.40, 0.55, 0.62, 0.58]}, index=idx)

def test_regime_pit_picks_le_date():
    s = regime_asof("2026-06-09", _bdf())
    assert s is not None and "2026-06-09" in s

def test_regime_never_future():
    s = regime_asof("2026-06-08", _bdf())
    assert "2026-06-09" not in s and "2026-06-10" not in s

def test_regime_empty_before_data():
    assert regime_asof("2026-01-01", _bdf()) is None
```

- [ ] **Step 2: 跑确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -k regime -q`
Expected: FAIL(`regime_asof` 未定义)。

- [ ] **Step 3: 写实现**(追加到 `narrative.py`)

```python
def regime_asof(date, breadth_df):
    """大盘日产物(PIT):取 breadth_df 中 ≤date 末行拼点评;无则 None。绝不取 >date 行。"""
    if breadth_df is None or len(breadth_df) == 0:
        return None
    dd = _d(date)
    if dd is None:
        return None
    import pandas as pd
    sub = breadth_df[breadth_df.index <= pd.Timestamp(dd)]
    if len(sub) == 0:
        return None
    row = sub.iloc[-1]
    day = str(sub.index[-1].date())
    num_cols = [c for c in breadth_df.columns if pd.api.types.is_numeric_dtype(breadth_df[c])]
    if not num_cols:
        return f"大盘·{day}(无数值列)"
    parts = [f"{c}={row[c]:.3f}" for c in num_cols[:3] if pd.notna(row[c])]
    return (f"大盘·截至{day}:" + " ".join(parts)) if parts else f"大盘·{day}"
```

- [ ] **Step 4: 跑确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -q`
Expected: PASS(10 passed)。

- [ ] **Step 5: "Commit"= 全量 pytest 绿。**

---

## Task 4: decide 接线(后端按日浮出叙事 + 喂大盘 + 落盘审计)

**Files:** Modify `guanlan_v2/seats/api.py`(decide:`research` 装配 `:965-975`、`regime` 注入 `:1038`、落盘 `:1091-1094`) · Test `tests/test_seats_narrative.py`

> **实现前必做**:用 Read 工具读 `guanlan_v2/seats/api.py:850-1095` 确认 `research=payload.get(...)`、`research[:4]` 装配、`regime` 变量、落盘 dict 的**当前精确行号**(随改动漂移)。再读 `/archive` 端点(grep `archive` in `guanlan_v2/`)确认档案 store 读函数名,读 `out/` 任一 md 确认落款日格式 —— 这两处接口名/格式以现场为准。

- [ ] **Step 1: 写失败测试(decide 集成)**

照搬 `tests/test_seats_runs.py:1-80` 的子进程骨架(`_PY` + `_SCRIPT` 钉 `sys.path.insert(0,'G:/guanlan-v2/engine')` + monkeypatch `financial_analyst.llm.client.LLMClient` 返固定 JSON)。断言三条:
1. 同一 run 跨两 date(D1<D2)落盘的 `narratives_surfaced` **不同**(逐日浮出);
2. 任一 date 的 `narratives_surfaced` 内 id 对应卡 as_of 均 ≤ 该 date(**无未来卡**);
3. 落盘含 `regime_asof_used` 字段。

```python
# 骨架见 tests/test_seats_runs.py;此处仅示断言核(实现时填入子进程脚本):
def test_decide_surfaces_per_day_pit():
    # ... 子进程内:注入合成叙事池(monkeypatch build_pool/_load_* 返合成),对 D1/D2 各调一次 decide,
    #     读 var/seats_decisions.jsonl 末两条,断言 narratives_surfaced 逐日不同 + 无未来 + 有 regime_asof_used 字段。
    assert True  # 占位待子进程脚本填充(骨架照搬 test_seats_runs.py)
```

- [ ] **Step 2: 跑确认失败**(脚本填好后)

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -k decide -q`
Expected: FAIL(decide 尚未接 narrative)。

- [ ] **Step 3: 实现 decide 接线**(`guanlan_v2/seats/api.py`,decide 内)

在 `research = payload.get("research") or []` 之后插入(anchor 以现场 Read 为准):

```python
            # P1:后端按日 PIT 浮出叙事卡(替代前端固定 research 透传)+ 大盘日产物。无料诚实空(不退回 demo)。
            _narr_ids = []
            try:
                from guanlan_v2.seats.narrative import build_pool, surface_narratives, DEFAULT_WINDOWS, DEFAULT_K
                _pool = build_pool(_load_archive_cards(), _load_out_reports())
                _surf = surface_narratives(_pool, c, payload.get("industry") or "", asof, k=DEFAULT_K, windows=DEFAULT_WINDOWS)
                _narr_ids = [x.get("id") for x in _surf]
                research = [{"title": x.get("title"), "from": (x.get("source") or {}).get("from", ""),
                             "path": x.get("path")} for x in _surf]
            except Exception:  # noqa: BLE001 — 浮出失败 → 诚实空
                research = []
```

`regime` 注入(回测不传 regime → 后端补日产物):

```python
            if not regime:
                try:
                    import pandas as _pd
                    from guanlan_v2.seats.narrative import regime_asof
                    from guanlan_v2.strategy.compute.regen import MARKET_BREADTH_PARQUET
                    regime = regime_asof(asof, _pd.read_parquet(MARKET_BREADTH_PARQUET))
                except Exception:  # noqa: BLE001
                    regime = None
```

落盘 dict 加(`:1091` 附近):

```python
                "narratives_surfaced": _narr_ids,
                "regime_asof_used": bool(regime),
```

模块级辅助(decide 外):

```python
def _load_archive_cards():
    """读 GL 镜像档案里的 card(复用既有 /archive store);失败 → []。"""
    try:
        return _archive_store_list(kind="card")   # 实现时对齐既有 store 函数名(grep 'archive' 确认)
    except Exception:  # noqa: BLE001
        return []

def _load_out_reports():
    """扫 out/*.md 取 {id,as_of(落款日),title,kind,path};失败 → []。"""
    try:
        from financial_analyst.buddy.tools import _project_root
        import re
        out = []
        for p in (_project_root() / "out").glob("*.md"):
            txt = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", txt[:2000])
            if not m:
                continue
            out.append({"id": p.stem, "as_of": m.group(1), "title": p.stem,
                        "kind": "研报", "path": str(p), "codes": [], "industry": ""})
        return out
    except Exception:  # noqa: BLE001
        return []
```

> out 报告若无 code 标注 → 按 industry/全市场关联(诚实:研报多为大盘/行业级,`codes=[]` 时 surface 靠 industry 命中;industry 也空则该报告只对"全市场"浮出——P1 可先让无 codes/industry 的研报对所有票浮出,或在 Step 里加 `is_market_wide` 标志,实现时与既有 out 报告头格式对齐定夺)。

- [ ] **Step 4: 重启 9999 + 跑测试**

```
# 杀 9999 监听 PID,等 ~10s 看门狗拉新代码
G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_seats_narrative.py -q
```
Expected: PASS(含 decide 集成:逐日浮出不同 + 无未来 + regime 字段)。

- [ ] **Step 5: "Commit"= 全量 pytest 绿。**

---

## Task 5: 前端 runRealThink 改后端按日浮出

**Files:** Modify `ui/seats/luozi-app.jsx`(decide payload `:244-254`) · `ui/seats/观澜 · 落子.html`(bump `?v`)

- [ ] **Step 1: 改 payload**(Read `luozi-app.jsx:244-254` 确认 anchor)

删 `research: (rcp.research||[]).map(...)`(后端浮出),加 `industry`:

```jsx
          res = await window.lzSeatDecide({
            code: codeNow, name: meta.name, date: bar.date,
            seat_cn: seatName, creed: creed, mode: 'fast',
            strategy_id: sid, strategy_name: seatName,
            industry: meta.industry || '',                 // P1:后端按行业/票浮出叙事卡
            // research 不再前端透传 —— 后端按 date PIT 浮出(Task4)
            card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
            cards: rcp.cards, recipe_factors: rcp.factors,
            regime: null,                                  // 回测 PIT:后端用大盘日产物补(Task4)
            run_id: runId,
            freq: isMin ? '30min' : 'day',
          });
```

- [ ] **Step 2: bump `?v`**(Edit `观澜 · 落子.html`:`luozi-app.jsx?v=20260613l` → `20260614a`)。

- [ ] **Step 3: 浏览器验证**(playwright 指 `http://localhost:9999/ui/seats/观澜 · 落子.html`):立昂微跑一次 30min 真跑;`/seats/decisions?run_id=…` 核对 **`narratives_surfaced` 逐日不同**、`regime_asof_used` 多为 true;0 console error。

- [ ] **Step 4: "Commit"= 全量 pytest 绿(确认前端改不连带后端测试失败)。**

---

## Task 6: 删假料(伪 IC / mock 证据 / demo 占位 → 诚实空)

**Files:** `ui/seats/luozi-app.jsx`(`distillToCard:504-513` + 默认策略剥 demo)· `ui/seats/luozi-panels.jsx`(RunDecCard `:1247/1361`)· `ui/seats/luozi-data.jsx`(`evidenceFor():387`)· `ui/_shared/guanlan-bus.js` + `ui/seats/luozi-foundry.jsx`(`:322/429`)+ `ui/graph/graph.jsx` · 各 html bump `?v`

- [ ] **Step 1: distillToCard 降 draft**(`luozi-app.jsx:504-513`)删伪 IC `(0.02 + ... sharpe*0.008)`,加 `status:'draft'`,insight 标「复盘草稿·未经验证,不入信号」。

- [ ] **Step 2: RunDecCard 显真叙事+大盘、删 mock**(`luozi-panels.jsx`)证据区改读 runDec 落盘 `narratives_surfaced`(标题)+ `regime_asof_used`(有则显大盘行);删 `:1247`(触发因子 mock)/`:1361`(regime 示例值)两 mock 分支;无 → 诚实空。

- [ ] **Step 3: 删 evidenceFor 回测 mock**(`luozi-data.jsx:387`)删回测分支编造的因子/研报/卡/regime;live 真源(signal_pack 等)不动。

- [ ] **Step 4: demo 退真路径 + 诚实空态**:默认策略 refs 剥 demo 研报(`_stripDeadDemoRecipe` 补剥 `rs_*` demo);`luozi-foundry.jsx:322/429` 料库列表过滤 `demo:true` → 空态文案「尚无真料·去验证区/工作流生成」;`graph.jsx` demo 过滤 + 空态。

- [ ] **Step 5: bump 所有改动 jsx 的 `?v`**(各对应 html)。

- [ ] **Step 6: 浏览器验证**:校场料库无「示例」卡(空态或只真料);复盘 RunDecCard 显当天真叙事+大盘、无 mock;复盘 distill 卡标"草稿不入信号";0 error。

- [ ] **Step 7: "Commit"= 全量 pytest 绿。**

---

## Task 7: 收口(全链验真 + 文档 + 记忆)

- [ ] **Step 1: 全量 pytest** → 246+新增 全绿。
- [ ] **Step 2: 重启 9999** 确认起服无 import 错;`/seats/decisions?run_id=…` 真机抽查 `narratives_surfaced` 逐日不同、未来卡零泄漏。
- [ ] **Step 3: 浏览器端到端**:立昂微回测 → 逐日叙事/大盘随游标变、无假料(料库无示例、复盘无 mock);两 TF 0 console error;截图留证。
- [ ] **Step 4: 文档**:`ui/seats/README.md` 补「叙事流 + 大盘日产物」节 + P2/P3 挂账;spec 标 P1 done。
- [ ] **Step 5: memory**:更新 `luozi-minute-backtest.md` 记 P1 交付 + `?v` + 挂账 P2/P3。

---

## Self-Review(对 spec 核查)

- **spec 覆盖**:叙事卡池(T2)/按日 PIT 浮出(T1)/大盘日产物(T3+T4)/decide 接线落盘(T4)/前端按日取(T5)/删假料四项(T6:伪IC+mock证据+demo退路+空态)/收口(T7)—— 全覆盖。P2/P3 明确不做。
- **占位扫描**:T4 的"实现时 Read 确认 anchor 行号 / archive store 函数名 / out 报告落款日格式"是**真实必要的现场读取**(行号会漂移、既有 store 接口名需现场对齐),非偷懒占位;纯函数 T1-T3 给了完整代码+测试;T4 decide 集成测试明确要照搬 `test_seats_runs.py` 子进程骨架。
- **类型一致**:`surface_narratives(pool,code,industry,as_of,k,windows)` / `build_pool(archive_cards,reports)` / `regime_asof(date,breadth_df)` 三签名跨任务一致;池条目字段 `{id,as_of,codes,industry,kind,title,insight,source,path}` 全程一致;`DEFAULT_WINDOWS`/`DEFAULT_K` 常量一处定义全程引用。
- **红线**:每任务 Step5 = pytest 绿(非 git);PIT 在 T1/T3 测试证伪未来;改 jsx bump ?v、改 py 重启 9999 写入对应任务。
- **已知现场依赖(实现时必读)**:① decide 精确 anchor 行号;② `/archive` 档案 store 读函数名;③ `out/` 研报落款日格式与 code/industry 关联(无标注→行业/全市场级浮出);④ `test_seats_runs.py` 子进程骨架。
