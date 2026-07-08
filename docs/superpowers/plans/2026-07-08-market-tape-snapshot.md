# 盘口实时快照中台 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 9 个全市场无-code 实时源(打板生态/龙虎榜/北向/热榜/行业)聚合进一份统一只读盘口快照,SWR 保鲜、data_health 纳管、帷幄 `ww_market_tape` 可读、宏观页面板展示,并收敛打板温度的重复拉取。

**Architecture:** 新增 `guanlan_v2/datafeed/market_tape.py`(第④块中台砖,与 live_client/sentiment/health 平级)。零重造:拉取全走已建 `live_client.probe`;本模块只做 10 源聚合 + stale-while-revalidate 磁盘缓存 + 只读 `read_tape`。落地面 = `GET /data/market_tape` + `ww_market_tape` + health 项 + 宏观页面板。

**Tech Stack:** Python 3.13 / FastAPI(datafeed 路由)/ 既有 `live_client` 子进程正典 probe / 前端 `ui/macro/`(React JSX,htm,无构建)。

## Global Constraints

- 展示/上下文型,`derived` 纯算术,**绝不回写 v4/blend/picks/seats 任何信号或排序**。
- 诚实降级绝不伪造新鲜:每源独立 `pulled_at`,陈旧经龄期显形;首拉无缓存返回 `warming` 不阻塞不编造。
- UI 只填现有页(`ui/macro/` 全球情绪温度计所在页)不新建/不重构。
- 零重造:一切拉取经 `live_client.probe`(观澜唯一现拉门户),不新增直连外源 HTTP。
- 不自 HTTP:帷幄工具/端点进程内直调 `read_tape()`(必要时 `asyncio.to_thread`),严禁协程内同步自 HTTP。
- 数据型帷幄工具必须自带全量 `content` + 信封级 `_wrap` 穿透测试。
- 守护计数四面同步:`test_console_tools`(5 断言 53→54 / 78→79)+ `test_guanlan_mcp`(3 断言 57→58)+ glmcp README + `_WW_REACHABLE_ENDPOINTS`。
- PIT 零动:live 缓存绝不进回测/vintage 通道。

---

## File Structure

- **Create** `guanlan_v2/datafeed/market_tape.py` — 拉 10 源 + SWR 缓存 + `read_tape` 只读 API。单一职责:实时快照聚合与保鲜。
- **Create** `tests/test_datafeed_market_tape.py` — 全离线,桩 `live_client.probe`/`native_rows`。
- **Modify** `guanlan_v2/macro/astock.py` — `build_astock` 优先读快照,缺席回落直拉。
- **Modify** `guanlan_v2/datafeed/health.py` — `collect_data_health` 加 `market_tape` 数据项。
- **Modify** `guanlan_v2/datafeed/api.py` — 加 `GET /data/market_tape`。
- **Modify** `guanlan_v2/console/tools.py` — `market_tape_impl` + `WW_TOOL_TABLE` 加 `ww_market_tape` + reachable。
- **Modify** `tests/test_console_tools.py` + `tests/test_guanlan_mcp.py` + `guanlan_v2/glmcp/README*` — 守护计数四面 +1。
- **Modify** `ui/macro/macro-data.jsx` + `ui/macro/macro-app.jsx` + `ui/macro/观澜 · 全球情绪.html` — 只读盘口面板 + `?v` bump。
- **Runtime** `var/live/market_tape.json` — 缓存(确认 `var/` 已 gitignore,同 `var/sentiment/`)。

---

### Task 1: market_tape.py — 刷新 + derive + 原子缓存写

**Files:**
- Create: `guanlan_v2/datafeed/market_tape.py`
- Test: `tests/test_datafeed_market_tape.py`

**Interfaces:**
- Consumes: `live_client.probe(source, code, date, limit) -> {ok,source,status,items,n,note,pulled_at}`;`live_client.native_rows(items) -> list[dict]`;`live_client.resolve_source(alias) -> canonical_sid`。
- Produces: `_refresh(ttl_s=180) -> dict`(落 `var/live/market_tape.json` 并返回 `{pulled_at,ttl_s,sources,derived}`);`_derive(sources) -> dict`;`_load_cache()`/`_write_cache_atomic(data)`。

- [ ] **Step 1: 写失败测试** `tests/test_datafeed_market_tape.py`

```python
# -*- coding: utf-8 -*-
"""datafeed.market_tape 单测(全离线,桩 live_client)。"""
import json
import types
from pathlib import Path

import pytest

import guanlan_v2.datafeed.market_tape as mt
import guanlan_v2.datafeed.live_client as lc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    cache = tmp_path / "var" / "live" / "market_tape.json"
    monkeypatch.setattr(mt, "_CACHE_PATH", cache)
    monkeypatch.setattr(mt, "_MEM_CACHE", {"data": None})
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [False])
    yield


def _probe_ok(source, code="", date="", limit=20):
    canon = lc.resolve_source(source) or source
    fixtures = {
        "em_limit_up_pool": [{"raw": {"code": "000656", "zt_stat": "7天7板", "break_times": 0, "limit_days": 7}},
                             {"raw": {"code": "300001", "zt_stat": "2天2板", "break_times": 1, "limit_days": 2}}],
        "ths_hsgt_realtime": [{"raw": {"name": "北向", "net": 12.3}}],
    }
    items = fixtures.get(canon, [{"raw": {"code": "600000", "x": 1}}])
    return {"ok": True, "source": canon, "status": "ok", "items": items,
            "n": len(items), "note": "", "pulled_at": "2026-07-08T10:15:01"}


def test_refresh_pulls_all_sources_writes_cache_and_derives(monkeypatch):
    monkeypatch.setattr(lc, "probe", _probe_ok)
    data = mt._refresh(ttl_s=180)
    # 每个 _SOURCES(10)都进 sources,键为 canonical source_id
    assert set(data["sources"]) == {lc.resolve_source(s["sid"]) for s in mt._SOURCES}
    zt = lc.resolve_source("em_zt_pool")
    assert data["sources"][zt]["rows"][0]["code"] == "000656"     # native_rows 平铺保真
    assert data["derived"]["zt_count"] == 2
    assert data["derived"]["max_streak"] == 7
    assert data["derived"]["break_ratio"] == 0.5
    assert data["derived"]["north_net"] == 12.3
    assert mt._CACHE_PATH.exists()                                 # 原子落盘
    on_disk = json.loads(mt._CACHE_PATH.read_text(encoding="utf-8"))
    assert on_disk["pulled_at"] == data["pulled_at"]
```

- [ ] **Step 2: 跑测确认失败** — `pytest tests/test_datafeed_market_tape.py -x -q` → FAIL(`module has no attribute _refresh`)。

- [ ] **Step 3: 写实现** `guanlan_v2/datafeed/market_tape.py`

```python
# -*- coding: utf-8 -*-
"""盘口实时快照中台(datafeed 中台第④块)—— 全市场无-code 实时源统一只读快照。

零重造:拉取全走统一客户端 live_client.probe(观澜唯一现拉门户)。本模块只做
「10 全市场源聚合 + stale-while-revalidate 磁盘缓存 + 只读 read_tape」。
红线:展示/上下文型,derived 纯算术无信号;每源独立 pulled_at,诚实降级绝不伪造新鲜;
首拉无缓存 warming 不阻塞;后台刷新单飞(防外源被打)。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.datafeed import live_client as _lc

_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_MARKET_TAPE_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "market_tape.json")))
_DEFAULT_TTL_S = int(os.environ.get("GUANLAN_MARKET_TAPE_TTL_S", "180"))

# 9 展示源(show=True)+ 1 收敛源 ths_hot_reason(show=False,打板温度 top_reasons 用)。
# sid 走 live_client alias 解析;date="" 由 live_client DATE_POOLS 补当日 YYYYMMDD。
_SOURCES: List[Dict[str, Any]] = [
    {"sid": "em_zt_pool",     "kw": {"date": ""},   "show": True},
    {"sid": "em_zb_pool",     "kw": {"date": ""},   "show": True},
    {"sid": "em_dt_pool",     "kw": {"date": ""},   "show": True},
    {"sid": "em_yzt_pool",    "kw": {"date": ""},   "show": True},
    {"sid": "eastmoney_lhb",  "kw": {"date": ""},   "show": True},
    {"sid": "northbound",     "kw": {},             "show": True},
    {"sid": "em_hot_rank",    "kw": {"limit": 50},  "show": True},
    {"sid": "ths_hot_list",   "kw": {"limit": 50},  "show": True},
    {"sid": "industry_rank",  "kw": {"limit": 50},  "show": True},
    {"sid": "ths_hot_reason", "kw": {"date": ""},   "show": False},
]

_LOCK = threading.Lock()
_REFRESH_INFLIGHT = [False]
_MEM_CACHE: Dict[str, Any] = {"data": None}
_STREAK_RE = re.compile(r"(\d+)板")


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache_atomic(data: Dict[str, Any]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _CACHE_PATH)


def _derive(sources: Dict[str, Any]) -> Dict[str, Any]:
    def rows(alias: str) -> List[dict]:
        return (sources.get(_lc.resolve_source(alias) or alias) or {}).get("rows") or []
    zt, zb, dt = rows("em_zt_pool"), rows("em_zb_pool"), rows("em_dt_pool")
    north = rows("northbound")
    streaks, breaks = [], 0
    for r in zt:
        m = _STREAK_RE.search(str(r.get("zt_stat") or ""))
        streaks.append(int(m.group(1)) if m else int(r.get("limit_days") or 1))
        if int(r.get("break_times") or 0) > 0:
            breaks += 1
    d: Dict[str, Any] = {"zt_count": len(zt), "zb_count": len(zb), "dt_count": len(dt),
                         "max_streak": max(streaks) if streaks else 0,
                         "break_ratio": round(breaks / len(zt), 4) if zt else 0.0,
                         "north_net": None}
    if north and isinstance(north[0], dict):     # 北向净额字段名多变,多候选探测,缺则 None
        for k in ("net", "net_inflow", "north_net", "成交净买额", "净买额"):
            v = north[0].get(k)
            if v is not None:
                try:
                    d["north_net"] = float(v)
                    break
                except (TypeError, ValueError):
                    pass
    return d


def _refresh(ttl_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    prev = _load_cache() or {}
    prev_sources = prev.get("sources") or {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    sources: Dict[str, Any] = {}
    for spec in _SOURCES:
        alias = spec["sid"]
        canon = _lc.resolve_source(alias) or alias
        try:
            r = _lc.probe(alias, **spec["kw"])
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "note": f"{type(exc).__name__}: {exc}"}
        if r.get("ok") and r.get("status") in ("ok", ""):
            rows = _lc.native_rows(r.get("items"))
            sources[canon] = {"status": r.get("status") or "ok",
                              "n": int(r.get("n") or len(rows)),
                              "pulled_at": r.get("pulled_at") or now_iso,
                              "note": r.get("note") or "", "rows": rows}
        else:   # 本轮失败/planned/error → 保留上一轮该源(局部陈旧诚实显形)
            note = (r.get("note") or r.get("error") or "本轮拉取失败")
            old = prev_sources.get(canon)
            if old:
                kept = dict(old)
                kept["note"] = f"(旧){old.get('note') or ''}|新失败:{note}"[:400]
                sources[canon] = kept
            else:
                sources[canon] = {"status": "error", "n": 0, "pulled_at": None,
                                  "note": note, "rows": []}
    pulled_list = [v["pulled_at"] for v in sources.values() if v.get("pulled_at")]
    overall = max(pulled_list) if pulled_list else prev.get("pulled_at")
    data = {"pulled_at": overall, "ttl_s": ttl_s, "sources": sources, "derived": _derive(sources)}
    _write_cache_atomic(data)
    _MEM_CACHE["data"] = data
    return data
```

- [ ] **Step 4: 跑测确认通过** — `pytest tests/test_datafeed_market_tape.py -x -q` → PASS。

- [ ] **Step 5: 提交** — `git add guanlan_v2/datafeed/market_tape.py tests/test_datafeed_market_tape.py && git commit`（消息见 Task 8 风格）。

---

### Task 2: market_tape.py — SWR read_tape + 单飞后台刷新 + warming

**Files:**
- Modify: `guanlan_v2/datafeed/market_tape.py`
- Test: `tests/test_datafeed_market_tape.py`

**Interfaces:**
- Produces: `read_tape(fresh_within_s=180) -> {ok,warming,pulled_at,sources,derived,freshness,note}`;`_trigger_refresh(ttl_s) -> bool`(单飞:已在跑返回 False);`_freshness(data) -> {overall_age_s,per_source,stale}`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_datafeed_market_tape.py`)

```python
def test_read_warming_when_no_cache_triggers_refresh(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape()
    assert out["warming"] is True and out["sources"] == {} and fired["n"] == 1
    assert "预热" in out["note"]


def test_read_fresh_cache_no_refresh(monkeypatch):
    now = mt.datetime.now().isoformat(timespec="seconds")
    mt._MEM_CACHE["data"] = {"pulled_at": now, "ttl_s": 180,
                             "sources": {"em_limit_up_pool": {"pulled_at": now, "rows": [{"code": "1"}], "n": 1}},
                             "derived": {"zt_count": 1}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is False and fired["n"] == 0
    assert out["derived"]["zt_count"] == 1


def test_read_stale_cache_returns_now_and_triggers(monkeypatch):
    old = "2020-01-01T00:00:00"
    mt._MEM_CACHE["data"] = {"pulled_at": old, "ttl_s": 180, "sources": {}, "derived": {}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is True and fired["n"] == 1
    assert out["pulled_at"] == old              # 本次仍返回旧值(诚实龄期)


def test_trigger_refresh_single_flight(monkeypatch):
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [True])   # 已有刷新在跑
    started = {"n": 0}
    monkeypatch.setattr(mt.threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=lambda: started.__setitem__("n", started["n"] + 1)))
    assert mt._trigger_refresh() is False and started["n"] == 0
```

- [ ] **Step 2: 跑测确认失败** — → FAIL(`read_tape` 未定义)。

- [ ] **Step 3: 写实现**(追加到 `market_tape.py`)

```python
def _freshness(data: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now()

    def age(iso: Any) -> Optional[int]:
        try:
            return int((now - datetime.fromisoformat(str(iso))).total_seconds())
        except (TypeError, ValueError):
            return None
    per = {sid: age(v.get("pulled_at")) for sid, v in (data.get("sources") or {}).items()}
    return {"overall_age_s": age(data.get("pulled_at")), "per_source": per}


def _trigger_refresh(ttl_s: int = _DEFAULT_TTL_S) -> bool:
    with _LOCK:
        if _REFRESH_INFLIGHT[0]:
            return False
        _REFRESH_INFLIGHT[0] = True

    def _run() -> None:
        try:
            _refresh(ttl_s)
        except Exception:  # noqa: BLE001 — 后台刷新失败绝不冒泡
            pass
        finally:
            with _LOCK:
                _REFRESH_INFLIGHT[0] = False
    threading.Thread(target=_run, name="market_tape_refresh", daemon=True).start()
    return True


def read_tape(fresh_within_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """SWR 只读:读永远秒回缓存;缺失→warming+触发首拉;过期→返回旧值+触发后台刷新。
    绝不阻塞在网络、绝不伪造新鲜(freshness 龄期显形)。"""
    data = _MEM_CACHE.get("data") or _load_cache()
    if not data:
        _trigger_refresh(fresh_within_s)
        return {"ok": True, "warming": True, "pulled_at": None, "sources": {}, "derived": {},
                "freshness": {"overall_age_s": None, "per_source": {}, "stale": True},
                "note": "预热中,后台首拉已触发;稍后重试"}
    _MEM_CACHE["data"] = data
    fr = _freshness(data)
    stale = fr["overall_age_s"] is None or fr["overall_age_s"] > fresh_within_s
    fr["stale"] = bool(stale)
    note = ""
    if stale:
        _trigger_refresh(fresh_within_s)
        note = "缓存过期,已触发后台刷新;本次返回现有值(龄期见 freshness)"
    return {"ok": True, "warming": False, "pulled_at": data.get("pulled_at"),
            "sources": data.get("sources") or {}, "derived": data.get("derived") or {},
            "freshness": fr, "note": note}
```

- [ ] **Step 4: 跑测确认通过** — → PASS。
- [ ] **Step 5: 提交**。

---

### Task 3: 打板温度收敛(macro/astock.py 读快照)

**Files:**
- Modify: `guanlan_v2/macro/astock.py`
- Test: `tests/test_macro_astock.py`(既有,追加收敛用例)

**Interfaces:**
- Consumes: `market_tape.read_tape() -> {sources: {canonical_sid: {rows}}}`。
- Produces: `build_astock(live_fn=None)` 行为不变的对外契约;新增内部:快照在→读之,缺席/warming→回落 `live_fn` 直拉。

- [ ] **Step 1: 写失败测试**(`tests/test_macro_astock.py` 追加)

```python
def test_build_astock_reads_from_tape_when_fresh(monkeypatch):
    import guanlan_v2.macro.astock as A
    tape = {"warming": False, "sources": {
        "em_limit_up_pool": {"rows": [{"zt_stat": "3天3板", "break_times": 0, "limit_days": 3}]},
        "ths_hot_reason": {"rows": [{"reason": "AI 算力"}]},
        "ths_hot_list": {"rows": [{"name": "寒武纪"}]}}}
    monkeypatch.setattr(A, "_read_tape_safe", lambda: tape)
    called = {"n": 0}
    monkeypatch.setattr(A, "_client_live", lambda **k: called.__setitem__("n", called["n"] + 1) or {"ok": True, "rows": []})
    out = A.build_astock()
    assert out["available"] is True and out["zt_count"] == 1 and out["max_streak"] == 3
    assert called["n"] == 0                                   # 快照在→零直拉(收敛)
    assert out["top_reasons"] and out["hot_list"]


def test_build_astock_falls_back_when_tape_warming(monkeypatch):
    import guanlan_v2.macro.astock as A
    monkeypatch.setattr(A, "_read_tape_safe", lambda: {"warming": True, "sources": {}})
    hits = []
    def fake_live(**kw):
        hits.append(kw.get("source"))
        rows = [{"zt_stat": "2天2板", "break_times": 0}] if kw.get("source") == "em_zt_pool" else []
        return {"ok": True, "rows": rows, "n": len(rows), "note": ""}
    monkeypatch.setattr(A, "_client_live", fake_live)
    out = A.build_astock()
    assert "em_zt_pool" in hits and out["zt_count"] == 1      # warming→回落直拉,温度计不破
```

- [ ] **Step 2: 跑测确认失败** — → FAIL(`_read_tape_safe` 未定义)。

- [ ] **Step 3: 写实现** —— 改 `guanlan_v2/macro/astock.py`,`build_astock` 顶部加快照读取,`live_fn` 变为「快照缺失时的回落腿」。

```python
def _read_tape_safe() -> dict:
    """读盘口快照;任何失败返回 warming 让 build_astock 回落直拉(绝不硬依赖)。"""
    try:
        from guanlan_v2.datafeed import market_tape as mt
        return mt.read_tape()
    except Exception:  # noqa: BLE001
        return {"warming": True, "sources": {}}


def _tape_rows(tape: dict, alias: str) -> list | None:
    """快照里取某源 rows;缺席返回 None(→回落直拉该源)。"""
    if not tape or tape.get("warming"):
        return None
    from guanlan_v2.datafeed import live_client as lc
    src = (tape.get("sources") or {}).get(lc.resolve_source(alias) or alias)
    return (src.get("rows") if isinstance(src, dict) else None)
```

改 `build_astock`:先取快照,`em_zt_pool`/`ths_hot_reason`/`ths_hot_list` 优先从 `_tape_rows` 取,`None` 时才 `live_fn(source=...)`。涨停 rows 段落改为:

```python
    tape = _read_tape_safe()
    zt_rows = _tape_rows(tape, "em_zt_pool")
    if zt_rows is None:
        zt = live_fn(source="em_zt_pool", limit=_ZT_LIMIT)
        if not zt.get("ok") or zt.get("note"):
            out["notes"].append(f"em_zt_pool: {zt.get('note') or 'ok=False'}")
        zt_rows = zt.get("rows") or []
    rows = zt_rows
    if rows:
        out["available"] = True
        out["zt_count"] = len(rows)
        # …(既有 streak/break/temp 计算逐字不变)…
    for src, key, keep in (("ths_hot_reason", "top_reasons", 8), ("ths_hot_list", "hot_list", 10)):
        r = _tape_rows(tape, src)
        if r is None:
            res = live_fn(source=src, limit=keep)
            r = res.get("rows") or []
            if not r and res.get("note"):
                out["notes"].append(f"{src}: {res['note']}")
        if r:
            out[key] = r[:keep]
```

（温度公式、`cfg`、`_ZT_LIMIT` 截断逻辑保持既有;仅取数腿改为「快照优先、直拉回落」。）

- [ ] **Step 4: 跑测确认通过** — `pytest tests/test_macro_astock.py -q` → PASS(含既有温度用例不回归)。
- [ ] **Step 5: 提交**。

---

### Task 4: data_health 加 market_tape 项

**Files:**
- Modify: `guanlan_v2/datafeed/health.py`
- Test: `tests/test_datafeed_health.py`(既有)

**Interfaces:**
- Consumes: `var/live/market_tape.json` 的 `pulled_at`(经 `market_tape._CACHE_PATH`)。
- Produces: `collect_data_health()["items"]["market_tape"] = {status,pulled_at,age_min,note}`;参与 overall(数据项)。

- [ ] **Step 1: 写失败测试**(`tests/test_datafeed_health.py` 追加)

```python
def test_market_tape_health_fresh_stale_missing(monkeypatch, tmp_path):
    import guanlan_v2.datafeed.health as H
    import guanlan_v2.datafeed.market_tape as mt
    p = tmp_path / "market_tape.json"
    monkeypatch.setattr(mt, "_CACHE_PATH", p)
    # 缺文件 → missing
    assert H._item_market_tape()["status"] == "missing"
    # 新鲜 → fresh
    now = H.datetime.now().isoformat(timespec="seconds")
    p.write_text('{"pulled_at": "%s"}' % now, encoding="utf-8")
    assert H._item_market_tape()["status"] == "fresh"
    # 陈旧 → stale
    p.write_text('{"pulled_at": "2020-01-01T00:00:00"}', encoding="utf-8")
    assert H._item_market_tape()["status"] == "stale"
```

- [ ] **Step 2: 跑测确认失败** — → FAIL。

- [ ] **Step 3: 写实现**(`guanlan_v2/datafeed/health.py`)

```python
_TAPE_STALE_MIN = 30    # 盘口快照:SWR 常态 <3min;超 30min 未刷(服务停摆/盘后)→ stale


def _age_minutes(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return round((datetime.now() - datetime.fromisoformat(str(iso))).total_seconds() / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _item_market_tape() -> Dict[str, Any]:
    try:
        from guanlan_v2.datafeed.market_tape import _CACHE_PATH
        m = _read_json(_CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not m:
        return {"status": "missing", "note": "无盘口快照(未预热/首拉未完成)"}
    age = _age_minutes(m.get("pulled_at"))
    status = "unknown" if age is None else ("stale" if age > _TAPE_STALE_MIN else "fresh")
    return {"status": status, "pulled_at": m.get("pulled_at"), "age_min": age,
            "note": "盘口快照久未刷新(服务停摆/盘后?)" if status == "stale" else ""}
```

在 `_ITEMS` 字典追加:`"market_tape": _item_market_tape`(不进 `_OPS_ITEMS`,参与 overall)。

- [ ] **Step 4: 跑测确认通过** — → PASS。
- [ ] **Step 5: 提交**。

---

### Task 5: GET /data/market_tape 端点

**Files:**
- Modify: `guanlan_v2/datafeed/api.py`
- Test: `tests/test_datafeed_api.py`(既有;无则建)

**Interfaces:**
- Produces: `GET /data/market_tape` → `asyncio.to_thread(read_tape)` 的 JSON。

- [ ] **Step 1: 写失败测试**

```python
def test_market_tape_endpoint(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import guanlan_v2.datafeed.market_tape as mt
    monkeypatch.setattr(mt, "read_tape", lambda *a, **k: {"ok": True, "warming": False, "derived": {"zt_count": 5}})
    from guanlan_v2.datafeed.api import build_datafeed_router
    app = FastAPI(); app.include_router(build_datafeed_router())
    r = TestClient(app).get("/data/market_tape")
    assert r.status_code == 200 and r.json()["derived"]["zt_count"] == 5
```

- [ ] **Step 2: 跑测确认失败** — → FAIL(404)。

- [ ] **Step 3: 写实现**(`datafeed/api.py`,`build_datafeed_router` 内追加)

```python
    @router.get("/data/market_tape")
    async def market_tape_ep():
        from guanlan_v2.datafeed.market_tape import read_tape
        return JSONResponse(await asyncio.to_thread(read_tape))
```

- [ ] **Step 4: 跑测确认通过** — → PASS。
- [ ] **Step 5: 提交**。

---

### Task 6: ww_market_tape 帷幄工具 + 守护计数四面同步

**Files:**
- Modify: `guanlan_v2/console/tools.py`
- Modify: `tests/test_console_tools.py`、`tests/test_guanlan_mcp.py`、`guanlan_v2/glmcp/README*`
- Test: `tests/test_console_tools.py`(信封级)

**Interfaces:**
- Consumes: `market_tape.read_tape()`。
- Produces: `market_tape_impl(fresh_within_s=180) -> {ok,content,artifact,raw}`(**自带全量 content**);`WW_TOOL_TABLE` +1 项 `ww_market_tape`,`reachable=["/data/market_tape"]`。

- [ ] **Step 1: 写失败测试**(`tests/test_console_tools.py`)——**信封级 _wrap 穿透 + 计数**

```python
def test_market_tape_impl_full_content_through_wrap(monkeypatch):
    import guanlan_v2.console.tools as ct
    import guanlan_v2.datafeed.market_tape as mt
    monkeypatch.setattr(mt, "read_tape", lambda *a, **k: {
        "ok": True, "warming": False, "pulled_at": "2026-07-08T10:15:03",
        "freshness": {"overall_age_s": 40, "stale": False},
        "derived": {"zt_count": 64, "max_streak": 7, "break_ratio": 0.08, "dt_count": 3, "north_net": 12.3},
        "sources": {"eastmoney_lhb": {"rows": [{"name": "寒武纪", "net": 1.2}]},
                    "eastmoney_hot_rank": {"rows": [{"name": "中际旭创"}]},
                    "eastmoney_industry_comparison": {"rows": [{"name": "光模块", "pct": 5.1}]}}})
    wrapped = ct._wrap(ct.market_tape_impl)("{}")           # 穿真交付信封
    text = wrapped if isinstance(wrapped, str) else wrapped.get("content", "")
    assert "涨停" in text and "64" in text and "寒武纪" in text   # 全量 content,非 json[:400] 断裂
    assert "10:15" in text                                      # pulled_at 显形


def test_market_tape_warming_is_honest(monkeypatch):
    import guanlan_v2.console.tools as ct
    import guanlan_v2.datafeed.market_tape as mt
    monkeypatch.setattr(mt, "read_tape", lambda *a, **k: {"ok": True, "warming": True, "sources": {}, "derived": {}})
    out = ct.market_tape_impl()
    assert "预热" in out["content"]
```

同时更新既有计数断言:`test_console_tools` 中 `registered_ww` `53→54`、`console_n`/`explicit_n` `78→79`、`explicit_ww_n` `53→54`、`CONSOLE_ALLOWED` `78→79`、ww startswith `53→54`;`test_guanlan_mcp` 中 `57→58`(三处 tools/_DECLS/names)。

- [ ] **Step 2: 跑测确认失败** — → FAIL(`market_tape_impl` 未定义 + 计数不符)。

- [ ] **Step 3: 写实现**(`guanlan_v2/console/tools.py`)

```python
def market_tape_impl(fresh_within_s: int = 180) -> Dict[str, Any]:
    """读盘口实时快照(零 LLM,进程内直调 read_tape 不自 HTTP):北向净额 / 涨停家数·连板高度 /
    跌停·炸板率 / 龙虎榜 top / 人气榜 top / 行业涨跌前后 + 整体 pulled_at·龄期。warming/空诚实标注。
    content 必须自带全量(避免 _wrap 兜底 json[:400] 断裂,历史交付层缺陷)。"""
    from guanlan_v2.datafeed.market_tape import read_tape
    try:
        t = read_tape(int(fresh_within_s or 180))
    except (TypeError, ValueError):
        t = read_tape(180)
    if t.get("warming"):
        return {"ok": True, "content": "盘口快照预热中(后台首拉已触发),稍后重试。", "artifact": None, "raw": t}
    d = t.get("derived") or {}
    fr = t.get("freshness") or {}
    src = t.get("sources") or {}

    def top(canon, key, n=5):
        rows = (src.get(canon) or {}).get("rows") or []
        return "、".join(str(r.get(key) or r.get("name") or "") for r in rows[:n] if isinstance(r, dict)) or "—"
    age = fr.get("overall_age_s")
    fresh_mark = "" if not fr.get("stale") else "(已过期,后台刷新中)"
    lines = [
        f"盘口快照 · {str(t.get('pulled_at') or '')[:16]}(龄 {age}s{fresh_mark},读缓存零 LLM)",
        f"打板:涨停 {d.get('zt_count', '—')} 家 · 最高 {d.get('max_streak', '—')} 连板 · 炸板率 {d.get('break_ratio', '—')} · 跌停 {d.get('dt_count', '—')} · 炸板池 {d.get('zb_count', '—')}",
        f"北向净额:{d.get('north_net') if d.get('north_net') is not None else '—'}",
        f"龙虎榜 top:{top('eastmoney_lhb', 'name')}",
        f"人气榜 top:{top('eastmoney_hot_rank', 'name')}",
        f"行业涨幅榜:{top('eastmoney_industry_comparison', 'name')}",
    ]
    stale_srcs = [s for s, v in src.items() if isinstance(v, dict) and "新失败" in (v.get("note") or "")]
    if stale_srcs:
        lines.append("局部陈旧(保留上轮):" + "、".join(stale_srcs))
    return {"ok": True, "content": "\n".join(lines), "artifact": None, "raw": t}
```

`WW_TOOL_TABLE` 在 `ww_data_health` 项后追加:

```python
    {"name": "ww_market_tape",
     "description":
         "盘口实时快照(只读、零 LLM、秒回):全市场打板生态(涨停/炸板/跌停/一字家数·最高连板·炸板率)+"
         "北向净额 + 全市场龙虎榜 top + 人气榜 + 行业涨幅榜,SWR 保鲜(过期后台异步刷新,首拉预热)。"
         "用户问『今天盘口热不热/涨停多少/连板高度/北向流向/龙虎榜谁上榜/哪个行业强』时用。"
         "展示型绝不进信号;龄期显形绝不伪造新鲜。market microstructure live snapshot.",
     "input_schema": {"type": "object", "properties": {
         "fresh_within_s": {"type": "integer", "description": "可选,新鲜窗秒数(默认 180);超则触发后台刷新"}}},
     "impl": market_tape_impl, "cost": "instant", "confirm": False,
     "reachable": ["/data/market_tape"]},
```

- [ ] **Step 4: 跑测确认通过** — `pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q` → PASS。
- [ ] **Step 5: 同步 glmcp README** —— 工具数 57→58 及新工具一行说明。
- [ ] **Step 6: 提交**。

---

### Task 7: 宏观页只读盘口面板(只填现有页)

**Files:**
- Modify: `ui/macro/macro-data.jsx`(fetch `/data/market_tape` + 渲染面板)
- Modify: `ui/macro/macro-app.jsx`(挂载面板到温度计旁)
- Modify: `ui/macro/观澜 · 全球情绪.html`(`?v` bump)

**Interfaces:**
- Consumes: `GET /data/market_tape` → `{warming,pulled_at,derived,freshness,sources}`。
- Produces: 只读盘口面板组件(展示 9 组 + 龄期徽章 + warming 态)。

- [ ] **Step 1: 先读现有面板** —— Read `ui/macro/macro-data.jsx` 与 `macro-app.jsx`,定位打板温度(astock)面板块,**逐字镜像其结构/类名**(红线:只填不重建)。
- [ ] **Step 2: 加 fetch** —— 在既有数据加载处并入 `fetch('/data/market_tape')`,存入 state(失败/warming 诚实降级,不阻塞温度计)。
- [ ] **Step 3: 加面板** —— 温度计旁渲染盘口面板:涨停/连板/炸板率/跌停 + 北向净额 + 龙虎榜 top + 人气榜 + 行业涨幅榜 + `pulled_at·龄期`徽章(`freshness.stale` → ⚠“数据 N 分钟前”);`warming` → “预热中”。纯展示,无任何写回。
- [ ] **Step 4: `?v` bump** —— `观澜 · 全球情绪.html` 里 macro-data/macro-app 引用的 `?v=` 递增(用 Edit,不整文件重写)。
- [ ] **Step 5: 真机验收** —— preview 或 9999 起服务,`preview_snapshot`/`preview_screenshot` 确认面板渲染、龄期徽章显形、warming 态诚实。
- [ ] **Step 6: 提交**。

---

### Task 8: 全量 + Workflow 对抗评审 + 9999 真机验收 + 推送 + 记忆

**Files:**
- Verify: 全仓测试
- Modify: `.gitignore`(确认 `var/live/` 忽略,同 `var/sentiment/`)、`MEMORY.md` + `memory/data-seams-audit-2026-07-06.md`

- [ ] **Step 1: 全量测试** —— `pytest -q`(裸 pytest 会自 prepend engine;预期 1024+新增全绿,0 败)。GBK 打印慎用(²/…崩)。
- [ ] **Step 2: Workflow 对抗评审** —— 用 Workflow 跑多镜头 review(correctness/red-line 信号泄漏/交付层 _wrap/SWR 竞态/北向字段假设),每 finding 3 反驳者裁决,CONFIRMED 才修。
- [ ] **Step 3: 修评审** —— 逐条修 + 补回归测试。
- [ ] **Step 4: `var/live/` gitignore 核** —— 确认缓存不入库(runtime data)。
- [ ] **Step 5: 9999 重启真机验收** —— PowerShell 杀旧 9999 + pinned venv Start-Process 起新;`/health` 200;`curl /data/market_tape` 返 warming 或真数据;`ww_market_tape` 经 _wrap 全量 content;`/data/health` 含 market_tape 项;打板温度仍出数(收敛不破)。
- [ ] **Step 6: 提交 + 推送** —— `git push`。
- [ ] **Step 7: 记忆** —— 更新 `memory/data-seams-audit-2026-07-06.md`(中台第④块盘口快照交付 + 计数 54/79/58)与 `MEMORY.md` 钩子行。

---

## Self-Review

- **Spec 覆盖**:源集(§1)→T1;数据文件(§2)→T1;SWR(§3)→T2;打板温度收敛(§4)→T3;端点(§5a)→T5;帷幄工具(§5b)→T6;宏观页(§5c)→T7;health(§6)→T4;测试矩阵(§7)→各任务 + T8。全覆盖。
- **占位符**:无 TBD;北向字段名以多候选探测 + 缺则 None 明确化。
- **类型一致**:`read_tape` 返回键(warming/pulled_at/sources/derived/freshness/note)在 T2 定义,T3/T5/T6 消费一致;`_tape_rows` 按 canonical(resolve_source)查,与 `_refresh` 落键一致。
- **计数一致**:53→54 / 78→79 / 57→58 三处四面同步在 T6 显式列出。
