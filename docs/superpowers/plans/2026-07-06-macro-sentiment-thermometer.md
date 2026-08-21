# 全球情绪温度计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一页双半球看板:PolyMarket+Kalshi 全球宏观预期概率 × A股本土打板温度,附帷幄查询工具 ww_macro_pulse。

**Architecture:** 新模块 `guanlan_v2/macro/`(themes.yaml 唯一事实源 → sources.py 双源现拉 → pulse.py 温度合成+快照沉淀 → api.py 薄壳路由);A股侧复用 console.tools.live_text_impl 子进程 probe;前端照 industry 三件套。

**Tech Stack:** FastAPI + requests + PyYAML;React 18 UMD + babel standalone;pytest 全 mock。

**Spec:** `docs/superpowers/specs/2026-07-06-macro-sentiment-thermometer-design.md`(commit 82e9146)

## Global Constraints

- 诚实红线:纯展示层,绝不混入交易信号;外部失败 → 空 + note 记因,绝不编造(与 ww_news_live 同约)。
- 测试绝不打真 API(全 mock requests / live_text_impl)。
- 协程内严禁同步 HTTP:路由一律 `asyncio.to_thread` 包同步实现。
- 守护计数四处同步现值(未提交态):ww_ 工具 50、CONSOLE_ALLOWED 75;新工具 +1 → 51/76;_WW_REACHABLE_ENDPOINTS 期望集加 `/macro/pulse`。
- 前端 bump 必须动 `?v=`(用 Edit);Windows 控制台勿打印非 GBK 字符。
- 温度公式确定性纯算术,常数全在 themes.yaml,无 LLM 参与。

---

### Task 1: themes.yaml + sources.py(PM/Kalshi 双源客户端)

**Files:**
- Create: `guanlan_v2/macro/__init__.py`(空包 + `build_macro_router` 惰性导出,照 industry/__init__.py)
- Create: `guanlan_v2/macro/themes.yaml`
- Create: `guanlan_v2/macro/sources.py`
- Test: `tests/test_macro_sources.py`

**Interfaces:**
- Produces: `sources.fetch_polymarket(tags: list[str], limit: int = 12, http=None) -> tuple[list[dict], list[str]]`(markets, notes);`sources.fetch_kalshi(series: list[str], limit: int = 12, http=None) -> tuple[list[dict], list[str]]`;`sources.load_themes() -> dict`。
- Market dict schema:`{"source": "polymarket"|"kalshi", "id": str, "question": str, "prob": float(0-1), "volume": float, "close_time": str, "url": str}`。

**themes.yaml 内容(锚定市场按 2026-07 实测活跃市场选,子串小写匹配):**

```yaml
# 全球情绪温度计 · 主题与锚定市场(唯一事实源)
# anchor.direction: +1=概率升→risk-on / -1=概率升→risk-off;只有锚定市场参与温度合成
themes:
  - id: fed
    label: 美联储 · 利率
    polymarket_tags: [fed-rates]
    kalshi_series: [KXFED]
    anchors:
      - {match: "fed rate hike", direction: -1, weight: 1.0}
      - {match: "rate cuts in 2026", direction: 1, weight: 1.0}
  - id: inflation_recession
    label: 通胀 · 衰退
    polymarket_tags: [inflation, recession]
    kalshi_series: []
    anchors:
      - {match: "recession", direction: -1, weight: 1.0}
  - id: geopolitics
    label: 地缘政治
    polymarket_tags: [geopolitics]
    kalshi_series: []
    anchors:
      - {match: "military clash", direction: -1, weight: 1.0}
      - {match: "nuclear", direction: -1, weight: 1.0}
  - id: china
    label: 中美 · 中国
    polymarket_tags: [china]
    kalshi_series: []
    anchors:
      - {match: "china x taiwan", direction: -1, weight: 1.5}
      - {match: "tariff", direction: -1, weight: 1.0}
  - id: crypto_risk
    label: 加密 · 风险偏好
    polymarket_tags: [crypto]
    kalshi_series: []
    anchors: []   # 展示为主,方向语义混杂不硬标
astock:
  # 打板温度 = clamp(base + k_zt*涨停数 + k_streak*最高连板 - k_break*炸板率(0-1), 0, 100)
  base: 30.0
  k_zt: 0.35
  k_streak: 3.0
  k_break: 30.0
display_top_n: 8
```

**sources.py(完整实现):**

```python
# -*- coding: utf-8 -*-
"""全球情绪温度计双源客户端:Polymarket Gamma + Kalshi(公开行情,免鉴权)。
外部失败恒不 raise:返回 (rows, notes),notes 记因——诚实降级由 pulse 层显形。"""
from __future__ import annotations
import json
from pathlib import Path

import yaml

_GAMMA = "https://gamma-api.polymarket.com"
_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_TIMEOUT = 10
_THEMES_PATH = Path(__file__).with_name("themes.yaml")


def load_themes() -> dict:
    with open(_THEMES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _http():
    import requests
    return requests


def fetch_polymarket(tags, limit=12, http=None):
    http = http or _http()
    rows, notes = [], []
    for tag in tags:
        try:
            r = http.get(f"{_GAMMA}/events", params={
                "tag_slug": tag, "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false", "limit": limit},
                timeout=_TIMEOUT)
            r.raise_for_status()
            events = r.json()
        except Exception as e:
            notes.append(f"polymarket tag={tag} 失败: {type(e).__name__}: {e}")
            continue
        for ev in events or []:
            for m in (ev.get("markets") or [])[:3]:
                row = _pm_market_row(m, ev)
                if row is not None:
                    rows.append(row)
    return rows, notes


def _pm_market_row(m, ev):
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = json.loads(m.get("outcomePrices") or "[]")
    except Exception:
        return None
    if not outcomes or not prices:
        return None
    binary_yes = len(outcomes) == 2 and str(outcomes[0]).lower() == "yes"
    prob = float(prices[0])
    q = m.get("question") or ev.get("title") or ""
    if not binary_yes:
        q = f"{q} → {outcomes[0]}"
    return {"source": "polymarket", "id": f"pm_{m.get('id')}", "question": q,
            "prob": round(prob, 4),
            "volume": _f(m.get("volume24hr") or ev.get("volume24hr")),
            "close_time": (m.get("endDate") or "")[:10],
            "url": f"https://polymarket.com/event/{ev.get('slug', '')}"}


def fetch_kalshi(series, limit=12, http=None):
    http = http or _http()
    rows, notes = [], []
    for st in series:
        try:
            r = http.get(f"{_KALSHI}/markets", params={
                "series_ticker": st, "status": "open", "limit": limit}, timeout=_TIMEOUT)
            r.raise_for_status()
            markets = (r.json() or {}).get("markets") or []
        except Exception as e:
            notes.append(f"kalshi series={st} 失败: {type(e).__name__}: {e}")
            continue
        skipped = 0
        for m in markets:
            prob = _kalshi_prob(m)
            if prob is None:
                skipped += 1
                continue
            rows.append({"source": "kalshi", "id": f"k_{m.get('ticker')}",
                         "question": str(m.get("title") or ""),
                         "prob": prob, "volume": _f(m.get("liquidity_dollars")),
                         "close_time": (m.get("close_time") or "")[:10],
                         "url": f"https://kalshi.com/markets/{st.lower()}"})
        if skipped:
            notes.append(f"kalshi series={st} 无价跳过 {skipped} 个")
    return rows, notes


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _kalshi_prob(m):
    last = _f(m.get("last_price_dollars"))
    if last > 0:
        return round(last, 4)
    yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if yb > 0 and ya > 0:
        return round((yb + ya) / 2, 4)
    return None
```

- [ ] **Step 1:** 写 `tests/test_macro_sources.py`:夹具=真实报文缩样(PM event 含 markets/outcomes/outcomePrices JSON 字符串;Kalshi last_price_dollars/无价市场);FakeHttp 类(get 按 URL 回夹具或抛异常)。断言:归一 schema 字段齐;单 tag 失败→其余照常+note;Kalshi 无价跳过+note;load_themes 解析出 5 主题+astock 常数。
- [ ] **Step 2:** `python -m pytest tests/test_macro_sources.py -x -q` 先红(模块缺席)。
- [ ] **Step 3:** 落 `__init__.py`/`themes.yaml`/`sources.py`。
- [ ] **Step 4:** 测试转绿。
- [ ] **Step 5:** commit `feat(macro): 全球情绪温度计双源客户端(PM Gamma+Kalshi 免鉴权现拉,失败降级不编造)`

### Task 2: pulse.py(主题聚合 + 锚定温度 + 快照沉淀 + Δ)

**Files:**
- Create: `guanlan_v2/macro/pulse.py`
- Test: `tests/test_macro_pulse.py`

**Interfaces:**
- Consumes: Task 1 全部;Task 3 的 `astock.build_astock() -> dict`(本任务以参数 `astock_fn=None` 注入,None=跳过 A 股侧,Task 3 接默认)。
- Produces: `pulse.build_pulse(refresh: bool = False, snapshot_path=None, astock_fn=None, http=None) -> dict`;`pulse.load_history(market_id: str = "", theme: str = "", snapshot_path=None) -> list[dict]`。
- payload:`{"ok": True, "pulled_at": iso, "stale_minutes": float|None, "thermometer": {"global": float|None, "astock": float|None}, "themes": [{"id","label","temp","anchor_hits","markets":[market+{"delta24h": float|None}]}], "astock": {...}, "notes": [...]}`。

**核心算法(完整):**

```python
def _theme_temp(markets, anchors):
    """锚定温度:50 + 50*Σ(w*dir*(prob-0.5))/Σw,clamp 0-100;返回 (temp|None, hits)。"""
    tot_w, acc, hits = 0.0, 0.0, 0
    for a in anchors or []:
        needle = str(a.get("match", "")).lower()
        if not needle:
            continue
        for m in markets:
            if needle in m["question"].lower() or needle in m["id"].lower():
                w = float(a.get("weight", 1.0)); d = int(a.get("direction", 0))
                acc += w * d * (m["prob"] - 0.5); tot_w += w; hits += 1
                break  # 每锚只吃成交量最高一个命中(markets 已按 volume 降序)
    if tot_w <= 0:
        return None, 0
    return max(0.0, min(100.0, 50.0 + 50.0 * acc / tot_w)), hits
```

快照:`var/macro_pulse/snapshots.jsonl` append `{"ts", "markets": [{"source","id","prob"}], "temps": {theme_id: temp}, "astock_temp": float|None}`;Δ24h=同 id 最近一条 ts ≤ now-20h 的 prob 差;脏行跳过。非 refresh 且有 ≤24h 快照→回快照态(stale_minutes 显形);否则现拉。**双源全空且无快照 → ok True + 空 themes + notes,绝不编造。**总温度=有锚主题 temp 等权均值。

- [ ] **Step 1:** 写 `tests/test_macro_pulse.py`:温度算术表驱动(方向±/权重/0 锚点→None/clamp 边界);快照 append→老 ts 行→Δ 读回;脏行容忍;stale 回退路径。全部注入 FakeHttp+tmp_path。
- [ ] **Step 2:** 先红。
- [ ] **Step 3:** 实现 `pulse.py`。
- [ ] **Step 4:** 转绿。
- [ ] **Step 5:** commit `feat(macro): 温度合成+快照沉淀——锚定市场显式方向,Δ24h 从快照诚实读`

### Task 3: astock.py(A股本土温度,复用 probe)

**Files:**
- Create: `guanlan_v2/macro/astock.py`
- Modify: `guanlan_v2/macro/pulse.py`(默认 astock_fn 接线)
- Test: `tests/test_macro_astock.py`

**Interfaces:**
- Consumes: `guanlan_v2.console.tools.live_text_impl(source, code="", date="", limit=20) -> dict`(惰性 import 避免环导入);themes.yaml `astock` 常数。
- Produces: `astock.build_astock(live_fn=None) -> dict`:`{"available": bool, "temp": float|None, "zt_count": int, "max_streak": int, "break_ratio": float, "top_reasons": list, "hot_list": list, "notes": list}`。

**实现(完整逻辑,zt_stat 形如 "4天3板",break_times 炸板次数——2026-07-03 真机行结构):**

```python
import re

def build_astock(live_fn=None):
    if live_fn is None:
        from guanlan_v2.console.tools import live_text_impl as live_fn
    from .sources import load_themes
    cfg = (load_themes().get("astock") or {})
    out = {"available": False, "temp": None, "zt_count": 0, "max_streak": 0,
           "break_ratio": 0.0, "top_reasons": [], "hot_list": [], "notes": []}
    zt = live_fn(source="em_zt_pool", limit=50)
    if not zt.get("ok") or zt.get("note"):
        out["notes"].append(f"em_zt_pool: {zt.get('note') or 'ok=False'}")
    rows = zt.get("rows") or []
    if rows:
        out["available"] = True
        out["zt_count"] = int(zt.get("n") or len(rows))
        streaks, breaks = [], 0
        for r in rows:
            m = re.search(r"(\d+)板", str(r.get("zt_stat") or ""))
            streaks.append(int(m.group(1)) if m else int(r.get("limit_days") or 1))
            if int(r.get("break_times") or 0) > 0:
                breaks += 1
        out["max_streak"] = max(streaks)
        out["break_ratio"] = round(breaks / len(rows), 4)
        out["temp"] = round(max(0.0, min(100.0,
            float(cfg.get("base", 30)) + float(cfg.get("k_zt", .35)) * out["zt_count"]
            + float(cfg.get("k_streak", 3)) * out["max_streak"]
            - float(cfg.get("k_break", 30)) * out["break_ratio"])), 1)
    for src, key, keep in (("ths_hot_reason", "top_reasons", 8), ("ths_hot_list", "hot_list", 10)):
        res = live_fn(source=src, limit=keep)
        if res.get("rows"):
            out[key] = res["rows"][:keep]
        elif res.get("note"):
            out["notes"].append(f"{src}: {res['note']}")
    return out
```

真机核验点:`zt.get("n")` 是否为全量涨停家数(rows 受 limit 截断时 n 语义);若 n==len(rows) 则温度输入标"≥"语义入 note。

- [ ] **Step 1:** 写 `tests/test_macro_astock.py`:fake live_fn 按 source 回夹具(zt 行含 zt_stat "4天3板"/break_times=1);断言温度算术、break_ratio、max_streak=3、probe 失败→available False+notes、hot 源失败不拖垮主体。
- [ ] **Step 2:** 先红。 **Step 3:** 实现+pulse 默认接线。 **Step 4:** 转绿。
- [ ] **Step 5:** commit `feat(macro): A股本土打板温度——复用 stocks probe 涨停池,确定性算术`

### Task 4: api.py + server.py 挂载

**Files:**
- Create: `guanlan_v2/macro/api.py`
- Modify: `guanlan_v2/server.py`(industry 挂载点旁,约 :258)
- Test: `tests/test_macro_api.py`

**Interfaces:**
- Produces: `GET /macro/pulse?refresh=0|1`、`GET /macro/history?market_id=&theme=`。

```python
# -*- coding: utf-8 -*-
"""全球情绪温度计路由(薄壳挂载,无 prefix,路由自带 /macro/)。"""
from __future__ import annotations
import asyncio
from fastapi import APIRouter


def build_macro_router() -> APIRouter:
    router = APIRouter()

    @router.get("/macro/pulse")
    async def pulse_ep(refresh: int = 0):
        from . import pulse
        return await asyncio.to_thread(pulse.build_pulse, bool(refresh))

    @router.get("/macro/history")
    async def history_ep(market_id: str = "", theme: str = ""):
        from . import pulse
        return await asyncio.to_thread(pulse.load_history, market_id, theme)

    return router
```

- [ ] **Step 1:** TestClient 冒烟先红(monkeypatch build_pulse 回哨兵,断言透传)。
- [ ] **Step 2:** 实现 + server.py `include_router`。
- [ ] **Step 3:** `python -m pytest tests/test_macro_*.py -q` 全绿。
- [ ] **Step 4:** commit `feat(macro): /macro/pulse /macro/history 薄壳路由挂主 app`

### Task 5: ww_macro_pulse 帷幄工具(四处同步)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(macro_pulse_impl + WW_TOOL_TABLE 表项,reachable: ["/macro/pulse"])
- Modify: `tests/test_console_tools.py`(计数 50→51、75→76;_WW_REACHABLE_ENDPOINTS 期望集 + `/macro/pulse`;新 impl 单测)
- 同步:帷幄工具 specs 文档(实现期定位仓内既有工具 spec 文件)+ `_SYSTEM_PROMPT`(若表派生则自动,手写则同步)。

**Interfaces:**
- Produces: `macro_pulse_impl(refresh: bool = True) -> dict`(ok/content;content=总温度+各主题温度与 top3 概率+A股计数摘要;desc 强调"预测市场概率=展示型参考,非交易信号")。

- [ ] **Step 1:** 单测先红(mock build_pulse);守护计数断言改 51/76。
- [ ] **Step 2:** 实现 impl + 表项。
- [ ] **Step 3:** `python -m pytest tests/test_console_tools.py -q` 全绿。
- [ ] **Step 4:** commit `feat(console): ww_macro_pulse 全球情绪温度计查询(计数 51/76 四处同步)`

### Task 6: 前端 ui/macro/ 双半球看板 + 导航

**Files:**
- Create: `ui/macro/观澜 · 全球情绪.html`(照 industry html:React UMD+babel;`<link href="../industry/gl-ds.css?v=1">` 复用设计系统;guanlan-bus.js?v=4 + guanlan-nav.js?v=4)
- Create: `ui/macro/macro-data.jsx`(`window.MacroData = { fetchPulse(refresh), fetchHistory(marketId) }`,fetch `${GUANLAN_BACKEND}/macro/...`)
- Create: `ui/macro/macro-app.jsx`(组件树:`App > [HeaderGauges(双仪表+新鲜度徽章+刷新钮), GlobalHalf(ThemeCard×5 > MarketRow(概率条+Δ徽章+源徽章PM/K+截止日;点击展开 HistorySpark SVG polyline+外链)), AStockHalf(打板温度卡+涨停/连板/炸板率计数+题材榜+热榜), DegradeBar(notes 灰条)]`;温度色带 <35 青(risk-off)/35-65 纸金/>65 朱红(risk-on);概率条纯 CSS;无第三方图表库)
- Modify: `ui/_shared/guanlan-nav.js`(MODULES 追加 `{ label: '全球情绪', file: '../macro/观澜 · 全球情绪.html' }`)
- Modify: 各页 html `guanlan-nav.js?v=3`→`?v=4`(console/seats/screen/industry/graph/factor/cards/chat 逐一 Edit)

**Interfaces:**
- Consumes: Task 4 两端点,payload 逐字对齐 Task 2 Produces;缺席字段显示 "—",notes 非空渲染降级条。

- [ ] **Step 1:** 三件套落盘。
- [ ] **Step 2:** nav 加项 + 全页 ?v bump。
- [ ] **Step 3:** 9998 起服冒烟(preview 截图):双半球渲染、刷新钮、缺 Δ 显示 "—"。
- [ ] **Step 4:** commit `feat(ui): 全球情绪温度计双半球看板+顶栏入口(nav v=4)`

### Task 7: 全量回归 + 真机验证 + 收尾

- [ ] **Step 1:** `python -m pytest tests/ -q` 全绿(既有未提交改动的基线波及须甄别非本次引入)。
- [ ] **Step 2:** 9998 起服 `GET /macro/pulse?refresh=1` 真数据:双源真概率+快照文件落地;ww_macro_pulse 冒烟。
- [ ] **Step 3:** 重启 9999(生产吃新后端;冻死看门狗持 mutex 须先杀的历史坑注意)。
- [ ] **Step 4:** 更新记忆(MEMORY.md + macro-sentiment-thermometer.md)。
- [ ] **Step 5:** 最终收尾汇报(真概率数字/温度/截图)。

## Self-Review 备注

- Spec 全节有对应任务(3.1→T1,3.2→T1,3.3→T2,3.4→T3,3.5→T4,3.6→T6,3.7→T5;错误表分散于 T1-T3 测试;测试策略→各任务 Step 1)。
- 类型一致性:market dict、build_pulse 签名、payload 结构在 T1/T2 定义并被 T4/T5/T6 引用。
- 有意留白(实现期真机定,非占位):probe n 语义核验(T3)、帷幄 spec 文档定位与 _SYSTEM_PROMPT 派生方式(T5)。
