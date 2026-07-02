# P1 收益回流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全A等权基准产物 + `GET /seats/basket_perf`(选股篮子前向收益 vs 基准)+ `ww_picks_perf` 成绩单工具 + 两个 opt-in 开关(regen 每日定时 / promote 阈值门,默认关=零行为变化)。

**Architecture:** 基准=regen 顺算的小产物(`eqw_market_ret.parquet`,date/ret/n);收益计算=seats 纯函数模块+薄端点(口径对齐 calibration:收盘进→N根收盘出);工具照抄 console 薄壳模式;两个开关全部 env-gate,缺省不改任何行为。

**Tech Stack:** pandas + FastAPI(既有);python 一律 `G:/financial-analyst/.venv/Scripts/python.exe`。

**Spec:** `docs/superpowers/specs/2026-07-02-p1-return-feedback-design.md`

## Global Constraints

- 分支:`git checkout -b p1-return-feedback`(从 main;计划写就时 main=04f527e)。每任务完成即 commit。
- 测试命令:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file> -v`(conftest 已钉 engine 路径)。全量回归基线 **737 passed**。
- **守护计数精确值**:WW_TOOL_TABLE 39→**40**;CONSOLE_ALLOWED 64→**65**;MCP 43→**44**(tests/test_guanlan_mcp.py **三处**:L13、L71、L100)。
- **四处同步铁律**(新 ww_ 工具):①WW_TOOL_TABLE ②CONSOLE_ALLOWED(派生勿手改)③`_SYSTEM_PROMPT` 具名 ④守护计数三件+`test_ww_reachable_endpoints_matches_expected` 期望集(+`/seats/basket_perf`、`/screen/picks` 两项)。
- **红线(逐字来自 spec)**:基准缺失显形 null 不编造;未成熟不冒充已实现;两开关默认关(合并即零行为变化);绝不自动采纳;失败恒 HTTP 200 + ok:false 显形。零前端;不改交易信号/选股算法;不重写工坊 promote 主流程(门是尾部包裹)。
- 端点风格:JSONResponse、诚实降级;产物写盘一律 tmp+`os.replace` 原子;regen 顺算步骤一律非阻断 try/except(模板见 Task 1)。
- GateGuard hook 会在首个 Bash/每文件首编辑要求陈述 facts——照陈述后重试即可;git LF/CRLF warning 忽略。
- 用户多会话并行活跃:每任务开工前 `git status` 对表;若守护计数现值与本计划锚不符,以盘上守护测试现值为准换算增量。

---

## File Structure(全景)

| 文件 | 动作 | 职责 |
|---|---|---|
| `guanlan_v2/strategy/paths.py` | 修改 | +`EQW_MARKET_RET_PARQUET` 常量 |
| `guanlan_v2/strategy/compute/eqw_market.py` | **新建** | 等权基准产物:compute/load(mtime缓存)/eqw_cum_ret |
| `guanlan_v2/strategy/compute/regen.py` | 修改 | breadth 后插非阻断顺算步 |
| `guanlan_v2/seats/basket_perf.py` | **新建** | 篮子收益纯函数 compute_basket_perf |
| `guanlan_v2/seats/api.py` | 修改 | GET /seats/basket_perf 薄端点 |
| `guanlan_v2/console/tools.py` | 修改 | picks_perf_impl+注册;model_list/model_promote 增强 |
| `guanlan_v2/console/api.py` | 修改 | _SYSTEM_PROMPT 一句+纪律13 补一句 |
| `guanlan_v2/screen/api.py` | 修改 | regen 调度器+_start_regen_bg 抽取+health 显形+models include_draft |
| `guanlan_v2/server.py` | 修改 | 启动处 env-gate 调 start_regen_daily_scheduler |
| `guanlan_v2/strategy/compute/model_workflow.py` | 修改 | `_apply_promote_gate` 尾部包裹 |
| `guanlan_v2/screen/model_registry.py` | 修改 | set_default_model 拒 draft |
| `tests/test_eqw_market.py` `tests/test_basket_perf.py` | **新建** | 对应单测 |
| `tests/test_screen_api.py` `tests/test_console_tools.py` `tests/test_guanlan_mcp.py` `tests/test_model_workflow_promote.py` | 修改 | 调度器/门/工具/计数 |

---

### Task 1: 全A等权基准产物(eqw_market.py + regen 挂钩)

**Files:**
- Modify: `guanlan_v2/strategy/paths.py`(尾部加常量)
- Create: `guanlan_v2/strategy/compute/eqw_market.py`
- Modify: `guanlan_v2/strategy/compute/regen.py`(resid 落盘 print 之后、`# 2) 主线` 之前插一步)
- Test: `tests/test_eqw_market.py`(新建)

**Interfaces:**
- Produces: `EQW_MARKET_RET_PARQUET: Path`;`compute_eqw_market(provider_uri, end=None, codes=None, start="2019-11-01", loader=None) -> int`(写产物回行数,loader 可注入测试);`load_eqw_ret() -> DataFrame|None`(列 date/ret/n,mtime 缓存);`eqw_cum_ret(df, entry_date: str, exit_date: str) -> float|None`((entry,exit] 累计,窗口头尾任一不被产物覆盖 → None)。Task 2 消费 load_eqw_ret/eqw_cum_ret。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_eqw_market.py`:

```python
"""全A等权基准产物单测(P1 §1)。fake loader 注入,零真实取数。"""
import pandas as pd
import pytest


class _FakeLoader:
    def __init__(self, series_by_code):
        self.s = series_by_code

    def _read_bin(self, code, field):
        assert field == "close"
        return self.s.get(code)


def _mk(dates, vals):
    return pd.Series([float(v) if v == v else float("nan") for v in vals],
                     index=pd.to_datetime(dates), dtype="float64")


_D = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]


def test_compute_mean_and_n(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    loader = _FakeLoader({
        "SHA": _mk(_D, [10, 11, 11, 12]),            # ret: -, +10%, 0%, +9.0909%
        "SHB": _mk(_D, [20, 20, float("nan"), 22]),  # ret: -, 0%, NaN(停牌), NaN(复牌prev=NaN)
    })
    n = EQ.compute_eqw_market("ignored", end=_D[-1], codes=["SHA", "SHB"], start=_D[0],
                              loader=loader)
    df = pd.read_parquet(tmp_path / "eqw.parquet")
    assert n == len(df) == 3                                  # 首日无 ret,不落
    r2 = df[df["date"] == "2026-06-02"].iloc[0]
    assert r2["ret"] == pytest.approx((0.10 + 0.0) / 2) and r2["n"] == 2
    r3 = df[df["date"] == "2026-06-03"].iloc[0]
    assert r3["ret"] == pytest.approx(0.0) and r3["n"] == 1   # B 停牌剔除,不当 0 收益
    r4 = df[df["date"] == "2026-06-04"].iloc[0]
    assert r4["n"] == 1                                        # B 复牌首日 prev=NaN 保守剔除


def test_compute_idempotent_and_all_nan_code(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    loader = _FakeLoader({"SHA": _mk(_D, [10, 11, 12, 13]),
                          "SHZ": _mk(_D, [float("nan")] * 4), "SHY": None})
    n1 = EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA", "SHZ", "SHY"], start=_D[0],
                               loader=loader)
    n2 = EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA", "SHZ", "SHY"], start=_D[0],
                               loader=loader)
    assert n1 == n2 == 3                                       # 幂等覆盖;坏票不炸


def test_compute_no_codes_raises(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    with pytest.raises(RuntimeError):
        EQ.compute_eqw_market("x", end=_D[-1], codes=["NOPE"], start=_D[0],
                              loader=_FakeLoader({}))


def test_load_missing_and_cache(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    monkeypatch.setattr(EQ, "_eqw_cache", {"mtime": None, "df": None})
    assert EQ.load_eqw_ret() is None                           # 缺失=None 诚实缺席
    EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA"], start=_D[0],
                          loader=_FakeLoader({"SHA": _mk(_D, [10, 11, 12, 13])}))
    df1 = EQ.load_eqw_ret()
    assert df1 is not None and EQ.load_eqw_ret() is df1        # mtime 缓存同对象


def test_eqw_cum_ret_windows():
    import guanlan_v2.strategy.compute.eqw_market as EQ
    df = pd.DataFrame({"date": ["2026-06-02", "2026-06-03", "2026-06-04"],
                       "ret": [0.01, 0.02, -0.01], "n": [100, 100, 100]})
    got = EQ.eqw_cum_ret(df, "2026-06-02", "2026-06-04")       # (entry, exit] = 03,04
    assert got == pytest.approx(1.02 * 0.99 - 1)
    assert EQ.eqw_cum_ret(df, "2026-06-02", "2026-06-05") is None   # 尾部不覆盖
    assert EQ.eqw_cum_ret(df, "2026-05-01", "2026-06-03") is None   # 头部不覆盖
    assert EQ.eqw_cum_ret(df, "2026-06-04", "2026-06-04") is None   # 空窗
    assert EQ.eqw_cum_ret(None, "2026-06-02", "2026-06-04") is None # 产物缺席
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_eqw_market.py -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'guanlan_v2.strategy.compute.eqw_market'`

- [ ] **Step 3: paths.py 加常量** — `guanlan_v2/strategy/paths.py` 尾部追加:

```python
# P1 收益回流:全A等权日收益基准(basket_perf 的公平尺;regen 顺算)
EQW_MARKET_RET_PARQUET = ARTIFACTS_DIR / "eqw_market_ret.parquet"
```

- [ ] **Step 4: 写实现** — 新建 `guanlan_v2/strategy/compute/eqw_market.py`:

```python
# -*- coding: utf-8 -*-
"""全A等权基准产物(P1 §1):当日全市场 close/prev_close−1 的截面均值日线。

给 basket_perf(选股篮子收益跟踪)一把公平尺子。regen 顺算(breadth 后非阻断步),
产物 = ARTIFACTS_DIR/eqw_market_ret.parquet(date/ret/n 三列)。
口径:逐股 close.pct_change(fill_method=None)——停牌日 close=NaN 自然剔除
(**不 ffill**,否则停牌日 ret=0 污染均值);复牌首日 prev=NaN 亦剔除(保守);
当日未结算 bar 在二进制里本就无 close,天然无前视。全量重算幂等覆盖(原子写)。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from guanlan_v2.strategy.paths import EQW_MARKET_RET_PARQUET

DEFAULT_START = "2019-11-01"   # 对齐 breadth FETCH_START,足够覆盖任何 picks 跟踪窗


def compute_eqw_market(provider_uri: str, end: Optional[str] = None,
                       codes: Optional[List[str]] = None, start: str = DEFAULT_START,
                       loader=None) -> int:
    """全量重算等权日收益产物 → 行数。loader 可注入(测试);None=QlibBinaryLoader。"""
    import pandas as pd
    if loader is None:
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        loader = QlibBinaryLoader(provider_uri)
    if codes is None:
        from guanlan_v2.strategy.compute.breadth import list_all_instruments
        codes = list_all_instruments(provider_uri)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end else None
    rets: List["pd.Series"] = []
    for code in codes:
        c = loader._read_bin(code, "close")
        if c is None or len(c) == 0:
            continue
        c = c.loc[c.index >= start_ts]
        if end_ts is not None:
            c = c.loc[c.index <= end_ts]
        if len(c) < 2:
            continue
        rets.append(c.pct_change(fill_method=None))   # 停牌 NaN 剔除;复牌首日保守剔除
    if not rets:
        raise RuntimeError("compute_eqw_market: 无任何可读股票(检查 provider_uri/窗口)")
    wide = pd.concat(rets, axis=1)
    mean = wide.mean(axis=1, skipna=True)
    n = wide.notna().sum(axis=1)
    out = pd.DataFrame({
        "date": [pd.Timestamp(d).date().isoformat() for d in mean.index],
        "ret": mean.values.astype(float),
        "n": n.values.astype(int),
    })
    out = out[out["n"] > 0].reset_index(drop=True)
    EQW_MARKET_RET_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(EQW_MARKET_RET_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(EQW_MARKET_RET_PARQUET))
    return len(out)


_eqw_cache: Dict[str, Any] = {"mtime": None, "df": None}


def load_eqw_ret():
    """读产物 → DataFrame(date/ret/n)|None(缺失=消费方显形)。mtime 缓存(同 factor_vintage 模式)。"""
    import pandas as pd
    p = EQW_MARKET_RET_PARQUET
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _eqw_cache["mtime"] != mt:
        try:
            _eqw_cache["df"] = pd.read_parquet(p)
            _eqw_cache["mtime"] = mt
        except Exception:  # noqa: BLE001 — 读失败=None,诚实缺席
            return None
    return _eqw_cache["df"]


def eqw_cum_ret(df, entry_date: str, exit_date: str) -> Optional[float]:
    """(entry_date, exit_date] 窗口等权累计收益 ∏(1+ret)−1。
    产物缺席/窗口头尾任一不被产物覆盖/空窗 → None(诚实,绝不编造基准)。"""
    if df is None or len(df) == 0 or not entry_date or not exit_date \
            or str(exit_date) <= str(entry_date):
        return None
    if str(df["date"].min()) > str(entry_date) or str(df["date"].max()) < str(exit_date):
        return None                                   # 头/尾不覆盖 → 诚实 None
    sub = df[(df["date"] > str(entry_date)) & (df["date"] <= str(exit_date))]
    if len(sub) == 0:
        return None
    total = 1.0
    for r in sub["ret"]:
        total *= (1.0 + float(r))
    return total - 1.0
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_eqw_market.py -v`
Expected: 5 passed

- [ ] **Step 6: regen 挂钩** — `guanlan_v2/strategy/compute/regen.py`:在 `print(f"  resid {len(resid)} 行 -> {MARKET_BREADTH_PARQUET}", flush=True)`(约 164 行)之后、`# 2) 主线` 注释之前插入(模板与 3.4/3.5 步一致,非阻断):

```python
        # 1b) 全A等权日收益基准(P1 收益回流的公平尺;失败不阻断三产物)
        print("[regen] eqw_market → 全A等权日收益 ...", flush=True)
        try:
            from guanlan_v2.strategy.compute.eqw_market import (
                EQW_MARKET_RET_PARQUET, compute_eqw_market,
            )
            n_eqw = compute_eqw_market(provider_uri, end=end, codes=codes)
            out["eqw_market"] = (n_eqw, str(EQW_MARKET_RET_PARQUET))
            print(f"  eqw_market {n_eqw} 日 -> {EQW_MARKET_RET_PARQUET}", flush=True)
        except Exception as e:  # noqa: BLE001
            out["eqw_market"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] eqw_market 失败(不阻断): {type(e).__name__}: {e}", flush=True)
```

- [ ] **Step 7: 验证** — Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_eqw_market.py -v` + `G:/financial-analyst/.venv/Scripts/python.exe -c "import guanlan_v2.strategy.compute.regen"`
Expected: 5 passed;import 无报错

- [ ] **Step 8: Commit**

```bash
git add guanlan_v2/strategy/paths.py guanlan_v2/strategy/compute/eqw_market.py guanlan_v2/strategy/compute/regen.py tests/test_eqw_market.py
git commit -m "feat(strategy): 全A等权日收益基准产物 eqw_market(regen非阻断顺算·停牌NaN剔除不ffill·mtime缓存·窗口不覆盖诚实None)"
```

---

### Task 2: basket_perf 纯函数 + GET /seats/basket_perf

**Files:**
- Create: `guanlan_v2/seats/basket_perf.py`
- Modify: `guanlan_v2/seats/api.py`(`/benchmark` 端点之前加新端点)
- Test: `tests/test_basket_perf.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `load_eqw_ret()`/`eqw_cum_ret(df, entry, exit)`;既有 `_drop_unsettled`(seats/api.py:48)、`get_default_loader().fetch_quote`、`normalize_code` 容错引入模式(seats/api.py:1060-1068)。
- Produces: `compute_basket_perf(closes_by_code, start, horizon, bench_df=None) -> dict`(纯函数);`GET /seats/basket_perf?codes=&start=&horizon=` → `{ok, n, matured_n, horizon, avg_ret, bench_ret, excess, per_code, warnings, note}`。Task 3 的 ww_picks_perf 消费端点。

- [ ] **Step 1: 写失败测试** — 新建 `tests/test_basket_perf.py`:

```python
"""篮子前向收益(P1 §2)单测:纯函数 + 端点(fake loader)。"""
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import guanlan_v2.seats.api as seats_api  # noqa: E402
from guanlan_v2.seats.basket_perf import compute_basket_perf  # noqa: E402


_SER = [("2026-06-02", 10.0), ("2026-06-03", 10.5), ("2026-06-04", 11.0),
        ("2026-06-05", 10.8), ("2026-06-08", 11.2), ("2026-06-09", 11.5)]
_BENCH = pd.DataFrame({"date": [d for d, _ in _SER],
                       "ret": [0.0, 0.01, 0.01, -0.005, 0.01, 0.005], "n": [100] * 6})


def test_matured_basket_with_bench():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                              bench_df=_BENCH)
    assert out["ok"] is True and out["n"] == 1 and out["matured_n"] == 1
    p = out["per_code"][0]
    assert p["entry_date"] == "2026-06-02" and p["exit_date"] == "2026-06-05"
    assert p["ret"] == pytest.approx(10.8 / 10.0 - 1) and p["matured"] is True
    assert out["bench_ret"] == pytest.approx(1.01 * 1.01 * 0.995 - 1)
    assert out["excess"] == pytest.approx(out["avg_ret"] - out["bench_ret"])
    assert "口径" in out["note"]


def test_entry_shifts_to_first_bar_after_start():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-06", horizon=1,
                              bench_df=_BENCH)                 # 06-06/07 无bar → 首根 06-08
    p = out["per_code"][0]
    assert p["entry_date"] == "2026-06-08" and p["exit_date"] == "2026-06-09"


def test_immature_honest():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-08", horizon=5,
                              bench_df=_BENCH)                 # 只剩1根后续bar
    p = out["per_code"][0]
    assert p["matured"] is False and out["matured_n"] == 0
    assert p["exit_date"] == "2026-06-09"                      # 给到最新段,不冒充已实现
    assert out["bench_ret"] is not None                        # 同窗基准仍可算


def test_bench_missing_and_partial():
    out = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                              bench_df=None)
    assert out["ok"] is True and out["bench_ret"] is None and out["excess"] is None
    short_bench = _BENCH[_BENCH["date"] <= "2026-06-04"]       # 尾部不覆盖 → 整体 null
    out2 = compute_basket_perf({"SH600001": _SER}, start="2026-06-02", horizon=3,
                               bench_df=short_bench)
    assert out2["bench_ret"] is None


def test_bad_codes_warned_and_all_bad_fails():
    out = compute_basket_perf({"SH600001": _SER, "SHBAD": []}, start="2026-06-02",
                              horizon=3, bench_df=None)
    assert out["n"] == 1 and any("SHBAD" in w for w in out["warnings"])
    out2 = compute_basket_perf({"SHBAD": []}, start="2026-06-02", horizon=3)
    assert out2["ok"] is False and "reason" in out2


class _FakeLoader:
    def fetch_quote(self, code, start, end, freq):
        if re.sub(r"\D", "", str(code)) != "600001":
            return None
        return pd.DataFrame({"trade_date": [d for d, _ in _SER],
                             "close": [v for _, v in _SER]})


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def test_endpoint_basket_perf(monkeypatch):
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
    j = _client().get("/seats/basket_perf?codes=600001,999999&start=2026-06-02&horizon=3").json()
    assert j["ok"] is True and j["n"] == 1 and j["matured_n"] == 1
    assert j["bench_ret"] == pytest.approx(1.01 * 1.01 * 0.995 - 1)
    assert any("999999" in w for w in j["warnings"])           # 坏票剔除+显形


def test_endpoint_requires_params():
    j = _client().get("/seats/basket_perf").json()
    assert j["ok"] is False and "必填" in j["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_basket_perf.py -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'guanlan_v2.seats.basket_perf'`

- [ ] **Step 3: 写纯函数** — 新建 `guanlan_v2/seats/basket_perf.py`:

```python
# -*- coding: utf-8 -*-
"""篮子前向持有收益(P1 §2)纯函数:闭环第 3 环「D 日选的股后来怎么样」的计算件。

口径对齐置信校准(seats/calibration.py):start(或其后首根)收盘进 → +horizon 根收盘出,
等权,不含成本;出场 bar 未到 → matured:false,ret 给到最新可算段(entry→最新收盘,
绝不冒充已实现)。基准 = 全A等权同窗累计(eqw_market 产物;缺失/不覆盖 → None 显形)。
纯函数零 IO:closes_by_code 与 bench_df 由调用方注入(端点层负责拉日线/读产物)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from guanlan_v2.strategy.compute.eqw_market import eqw_cum_ret

NOTE = ("口径:start(或其后首根)收盘进、+N根收盘出,等权,不含成本;"
        "基准=全A等权同窗累计;未成熟给到最新段并标 matured:false")


def compute_basket_perf(closes_by_code: Dict[str, Sequence[Tuple[str, float]]],
                        start: str, horizon: int, bench_df=None) -> Dict[str, Any]:
    """closes_by_code: {code: [(date 'YYYY-MM-DD', close), ...] 升序}。返回响应形 dict。"""
    hz = max(1, min(int(horizon or 5), 60))
    per: List[Dict[str, Any]] = []
    warnings: List[str] = []
    bench_vals: List[float] = []
    for code, series in (closes_by_code or {}).items():
        rows = [(str(d), float(v)) for d, v in (series or []) if v is not None and v == v]
        idx = next((i for i, (d, _) in enumerate(rows) if d >= str(start)), None)
        if idx is None:
            warnings.append(f"{code}: start 之后无可用bar,剔除")
            continue
        entry_date, entry = rows[idx]
        if entry <= 0:
            warnings.append(f"{code}: 入场价非正,剔除")
            continue
        matured = (idx + hz) < len(rows)
        exit_i = (idx + hz) if matured else (len(rows) - 1)
        if exit_i <= idx:
            warnings.append(f"{code}: 入场后无后续bar(未成熟且无可算段),剔除")
            continue
        exit_date, exitp = rows[exit_i]
        per.append({"code": code, "entry_date": entry_date, "entry": entry,
                    "exit_date": exit_date, "exit": exitp,
                    "ret": exitp / entry - 1.0, "matured": matured})
        b = eqw_cum_ret(bench_df, entry_date, exit_date)
        if b is not None:
            bench_vals.append(b)
    if not per:
        return {"ok": False, "reason": "无任何可算票", "warnings": warnings}
    avg = sum(p["ret"] for p in per) / len(per)
    bench: Optional[float] = None
    if bench_vals and len(bench_vals) == len(per):
        bench = sum(bench_vals) / len(bench_vals)
    elif bench_vals:                                   # 部分覆盖=口径不齐 → 整体 null 显形
        warnings.append("基准窗口未全覆盖,bench_ret 置 null(诚实缺席)")
    return {"ok": True, "n": len(per), "matured_n": sum(1 for p in per if p["matured"]),
            "horizon": hz, "avg_ret": avg, "bench_ret": bench,
            "excess": (avg - bench) if bench is not None else None,
            "per_code": per, "warnings": warnings, "note": NOTE}
```

- [ ] **Step 4: 挂端点** — `guanlan_v2/seats/api.py` 在 `/benchmark` 端点之前加(async + to_thread,风格照 calibration):

```python
    @router.get("/basket_perf")
    async def seats_basket_perf(codes: str = "", start: str = "", horizon: int = 5):
        """篮子前向持有收益 vs 全A等权基准(P1 §2;口径=收盘进→N根收盘出,同置信校准,
        note 随响应下发)。codes 逗号分隔 ≤40(超截断并注明);失败恒 HTTP200 ok:false。"""
        try:
            raw = [c.strip() for c in (codes or "").split(",") if c.strip()]
            if not raw or not (start or "").strip():
                return JSONResponse({"ok": False, "reason": "codes 与 start 必填"})
            truncated = len(raw) > 40
            raw = raw[:40]
            try:
                from financial_analyst.buddy.tools import normalize_code as _norm
            except Exception:  # noqa: BLE001 — 引擎不可导入时裸用 code
                _norm = None
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            end = str(_pd.Timestamp.now().date())

            def _closes(c: str):
                df = loader.fetch_quote(c, str(start), end, "day")
                df = _drop_unsettled(df)               # 当日未结算占位行不当收盘
                if df is None or len(df) == 0 or "close" not in df.columns:
                    return []
                dcol = "trade_date" if "trade_date" in df.columns else df.columns[0]
                return [(str(d)[:10], float(v)) for d, v in zip(df[dcol], df["close"])
                        if v == v]

            closes_by_code: dict = {}
            for c in raw:
                cc = c
                if _norm is not None:
                    try:
                        cc = _norm(c)
                    except Exception:  # noqa: BLE001
                        cc = (c or "").strip().upper()
                try:
                    closes_by_code[cc] = await asyncio.to_thread(_closes, cc)
                except Exception:  # noqa: BLE001 — 单票取数失败=空序列 → 纯函数记 warning 剔除
                    closes_by_code[cc] = []

            from guanlan_v2.seats.basket_perf import compute_basket_perf
            from guanlan_v2.strategy.compute import eqw_market as _eqw
            bench_df = _eqw.load_eqw_ret()
            out = compute_basket_perf(closes_by_code, start=str(start), horizon=horizon,
                                      bench_df=bench_df)
            if truncated:
                out.setdefault("warnings", []).append("codes>40 已截断")
            if bench_df is None:
                out.setdefault("warnings", []).append("全A等权基准产物缺失(跑 ww_regen 生成)")
            return JSONResponse(out)
        except Exception as exc:  # noqa: BLE001 — 诚实降级
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
```

- [ ] **Step 5: 跑测试确认全绿**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_basket_perf.py tests/test_seats_benchmark.py -v`
Expected: 全绿(新 7 + benchmark 既有不破)

- [ ] **Step 6: Commit**

```bash
git add guanlan_v2/seats/basket_perf.py guanlan_v2/seats/api.py tests/test_basket_perf.py
git commit -m "feat(seats): GET /seats/basket_perf 篮子前向收益vs全A等权(校准同口径·未成熟诚实标注·基准缺失/半覆盖null显形)"
```

---

### Task 3: ww_picks_perf 工具 + 四处同步(40/65/44)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(picks_perf_impl + 注册,条目放 ww_regen 之后、ww_capabilities 之前)
- Modify: `guanlan_v2/console/api.py`(_SYSTEM_PROMPT)
- Test: `tests/test_console_tools.py`(2 个 impl 测 + 3 处计数 + 期望集 +2)、`tests/test_guanlan_mcp.py`(**三处** 43→44:L13/L71/L100)

**Interfaces:**
- Consumes: Task 2 的 `GET /seats/basket_perf`;P0 的 `GET /screen/picks?snapshot_only=1`(items 新在前,行含 date/model/picks[{code,rank,…}]);既有 `_self_get`。
- Produces: 工具名 `ww_picks_perf`。

- [ ] **Step 1: 写失败测试** — `tests/test_console_tools.py` 尾部追加:

```python
# ── P1 §3: ww_picks_perf 成绩单 ────────────────────────────────────────────

def test_picks_perf_impl(monkeypatch):
    picks = {"ok": True, "items": [
        {"date": "2026-06-30", "snapshot": True, "model": "prod",
         "picks": [{"code": "SH600001", "rank": 1}, {"code": "SZ000002", "rank": 2}]}]}
    perf = {"ok": True, "n": 2, "matured_n": 2, "horizon": 5, "avg_ret": 0.021,
            "bench_ret": 0.004, "excess": 0.017, "per_code": [], "warnings": [],
            "note": "口径:收盘进收盘出"}
    sent = {}
    def fake_get(path, timeout=30):
        if path.startswith("/screen/picks"):
            return picks
        sent["path"] = path
        return perf
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.picks_perf_impl()
    assert "codes=SH600001,SZ000002" in sent["path"] and "start=2026-06-30" in sent["path"]
    assert res["ok"] is True
    assert "+2.10%" in res["content"] and "+0.40%" in res["content"] and "+1.70%" in res["content"]
    assert "成熟 2/2" in res["content"] and "口径" in res["content"]


def test_picks_perf_impl_no_archive(monkeypatch):
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: {"ok": True, "items": []})
    res = ct.picks_perf_impl()
    assert res["ok"] is True and "暂无正式选股档案" in res["content"]
    assert "snapshot=true" in res["content"]                   # 教用户怎么落档
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py -k picks_perf -v`
Expected: 2 FAIL(`AttributeError: ... no attribute 'picks_perf_impl'`)

- [ ] **Step 3: 写 impl** — `guanlan_v2/console/tools.py`(放 regen_impl 之后):

```python
def picks_perf_impl(date: str = "", horizon: int = 5) -> Dict[str, Any]:
    """查正式选股成绩单:picks 档案 snapshot 行 → 篮子前向收益 vs 全A等权(校准同口径)。"""
    try:
        r = _self_get("/screen/picks?snapshot_only=1&limit=50")
    except Exception as e:
        return {"ok": False, "content": f"picks 档案读取失败: {e}", "artifact": None}
    items = r.get("items") or []
    want = (date or "").strip()
    if want:
        items = [it for it in items
                 if str(it.get("date")) == want or str(it.get("ts", "")).startswith(want)]
    if not items:
        return {"ok": True, "artifact": None, "raw": {"n": 0},
                "content": ("暂无正式选股档案" + (f"(date={want})" if want else "")
                            + "。ww_screen_run 传 snapshot=true 落档后才可跟踪成绩。")}
    it = items[0]                                              # read_picks 新在前
    codes = [str(p.get("code")) for p in (it.get("picks") or []) if p.get("code")][:40]
    if not codes:
        return {"ok": False, "content": f"档案 {it.get('date')} 无 picks 行,无法跟踪",
                "artifact": None, "raw": {"item": it}}
    hz = max(1, min(int(horizon or 5), 60))
    q = f"/seats/basket_perf?codes={','.join(codes)}&start={it.get('date')}&horizon={hz}"
    try:
        b = _self_get(q, timeout=120)
    except Exception as e:
        return {"ok": False, "content": f"篮子收益计算失败: {e}", "artifact": None}
    if not b.get("ok"):
        return {"ok": False, "content": f"篮子收益计算失败: {b.get('reason')}",
                "artifact": None, "raw": b}
    def _pct(v):
        return "—" if v is None else f"{float(v):+.2%}"
    content = (f"{it.get('date')} 正式选股 {b.get('n')} 只 · {b.get('horizon')}日等权 "
               f"{_pct(b.get('avg_ret'))} vs 全A等权 {_pct(b.get('bench_ret'))} · "
               f"超额 {_pct(b.get('excess'))} · 成熟 {b.get('matured_n')}/{b.get('n')}"
               + (f"\n⚠ {'; '.join(str(w) for w in b.get('warnings'))}" if b.get("warnings") else "")
               + f"\n口径: {b.get('note')}")
    return {"ok": True, "content": content, "artifact": None,
            "raw": {"pick_date": it.get("date"), "model": it.get("model"), "perf": b}}
```

- [ ] **Step 4: 注册 + 提示词**:
  1. WW_TOOL_TABLE:`ww_regen` 条目之后、`ww_capabilities` 之前插:

```python
    {"name": "ww_picks_perf",
     "description":
         "查正式选股成绩单:读 picks 档案(snapshot 行),算该篮子前向持有收益 vs 全A等权基准"
         "(收盘进/收盘出,与置信校准同口径)。默认最新一次正式选股;date 可选某天;"
         "未成熟票诚实标注,基准缺失显形 null。",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "可选,选某天的正式选股档案 YYYY-MM-DD"},
         "horizon": {"type": "integer", "default": 5, "description": "持有窗口(交易日 1-60)"}}},
     "impl": picks_perf_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/screen/picks", "/seats/basket_perf"]},
```

  2. `guanlan_v2/console/api.py` _SYSTEM_PROMPT:「另有(闭环读取面):…ww_regen(…需确认)。」行之后加一行:

```
另有:选股成绩单 ww_picks_perf(读 picks 档案 snapshot 行 → 前向持有收益 vs 全A等权基准,与置信校准同口径;看『上次正式选股赚没赚/跑没跑赢』用它)。
```

  3. 纪律 13 句尾追加(同一条内):`复盘选股成绩用 ww_picks_perf。`

- [ ] **Step 5: 守护计数同步**:
  - `tests/test_console_tools.py`:`== 39`→`== 40`(registered_ww、explicit_ww_n 两处);`== 64`→`== 65`(console_n、explicit_n 两处);`test_registry_derivation_consistent` 内 `== 39`→`== 40`、`== 64`→`== 65`;期望集追加:

```python
        "/seats/basket_perf",     # ww_picks_perf(P1 成绩单)
        "/screen/picks",          # ww_picks_perf(读 snapshot 档案)
```

  - `tests/test_guanlan_mcp.py`:**三处** `== 43`→`== 44`(L13 注释改 `# 37 ww_(40−3 excluded) + 7 alpha-zoo`、L71、L100)。

- [ ] **Step 6: 跑受影响测试**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -v`
Expected: 全绿(新 2 + 计数守护 + MCP 44)

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(console): ww_picks_perf 选股成绩单(picks snapshot→basket_perf vs 全A等权)+ 守护计数 40/65/44"
```

---

### Task 4: regen 每日定时(opt-in 默认关)+ health 显形

**Files:**
- Modify: `guanlan_v2/screen/api.py`(`_start_regen_bg` 抽取 + 调度器三件 + health 附块 + 端点改用抽取函数)
- Modify: `guanlan_v2/server.py`(`start_market_status_scheduler()` 调用行之后)
- Test: `tests/test_screen_api.py`(4 个新测)

**Interfaces:**
- Consumes: 既有 `_REGEN_LOCK`/`_REGEN_STATE`/`_run_regen_subprocess`/`_safe`/`_regen_public_state`(screen/api.py:140-251)。
- Produces: `_start_regen_bg(end=None) -> bool`、`_regen_sched_tick(now) -> bool`、`start_regen_daily_scheduler() -> None`、`_REGEN_SCHED` dict;`/screen/health` 新键 `regen_scheduler:{enabled,last_auto_ts}`。

- [ ] **Step 1: 写失败测试** — `tests/test_screen_api.py` 尾部追加:

```python
# ── P1 §4: regen 每日定时(opt-in 默认关)────────────────────────────────────

def test_regen_scheduler_default_off(monkeypatch):
    import guanlan_v2.screen.api as api
    monkeypatch.delenv("GUANLAN_REGEN_DAILY", raising=False)
    monkeypatch.setattr(api, "_regen_sched_started", False)
    monkeypatch.setattr(api, "_REGEN_SCHED",
                        {"enabled": False, "last_auto_ts": None, "last_auto_date": None})
    api.start_regen_daily_scheduler()
    assert api._REGEN_SCHED["enabled"] is False                # env 缺省=不起线程,零行为变化
    assert api._regen_sched_started is False


def test_regen_sched_tick_fires_once_per_day(monkeypatch):
    import datetime as dt
    import guanlan_v2.screen.api as api
    calls = {"n": 0}
    monkeypatch.setattr(api, "_start_regen_bg",
                        lambda end=None: calls.__setitem__("n", calls["n"] + 1) or True)
    monkeypatch.setattr(api, "_REGEN_SCHED",
                        {"enabled": True, "last_auto_ts": None, "last_auto_date": None})
    monkeypatch.delenv("GUANLAN_REGEN_DAILY_HOUR", raising=False)
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 17, 59)) is False   # 未到 18 点
    assert calls["n"] == 0
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 18, 1)) is True     # 触发
    assert calls["n"] == 1 and api._REGEN_SCHED["last_auto_ts"].startswith("2026-07-02T18:01")
    assert api._regen_sched_tick(dt.datetime(2026, 7, 2, 20, 0)) is False    # 当日不重复
    assert calls["n"] == 1
    assert api._regen_sched_tick(dt.datetime(2026, 7, 3, 18, 5)) is True     # 次日再触发
    assert calls["n"] == 2


def test_start_regen_bg_singleflight(monkeypatch):
    import guanlan_v2.screen.api as api
    monkeypatch.setattr(api, "_run_regen_subprocess", lambda end: None)      # 桩:不真跑
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = True
    assert api._start_regen_bg() is False                                    # 已在跑 → False
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = False
    assert api._start_regen_bg() is True                                     # 空闲 → 启动
    import time
    time.sleep(0.2)                                                          # 桩线程瞬时结束
    with api._REGEN_LOCK:
        api._REGEN_STATE["running"] = False                                  # 复位防跨测污染


def test_health_has_regen_scheduler_block():
    j = _client().get("/screen/health").json()
    assert "regen_scheduler" in j
    assert set(j["regen_scheduler"].keys()) == {"enabled", "last_auto_ts"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py -k "regen_sched or regen_bg or scheduler_block" -v`
Expected: 4 FAIL(AttributeError / KeyError)

- [ ] **Step 3: 写调度器三件** — `guanlan_v2/screen/api.py`,放 `_regen_public_state`(~158 行)之后:

```python
# ── P1 §4:regen 每日定时(opt-in;GUANLAN_REGEN_DAILY=1 才启;默认关=零行为变化)──
# 诚实口径:定时器随 9999 进程存亡,进程死=定时停,非 24/7 保证。
_REGEN_SCHED: Dict[str, Any] = {"enabled": False, "last_auto_ts": None, "last_auto_date": None}
_regen_sched_started = False


def _start_regen_bg(end: Optional[str] = None) -> bool:
    """抢单飞锁并起再生后台线程;已在跑 → False。POST /screen/regen 与定时调度共用。"""
    import time as _t
    import threading as _th
    with _REGEN_LOCK:
        busy = bool(_REGEN_STATE.get("running"))
        if not busy:
            _REGEN_STATE.update(
                running=True, phase="starting", label="启动子进程…", step=0,
                started_at=_t.time(), ended_at=None, ok=None, error=None,
                end=(end or None), new_date=None, lines=[],
            )
    if busy:
        return False
    _th.Thread(target=lambda: _safe(lambda: _run_regen_subprocess(end or None)),
               daemon=True).start()
    return True


def _regen_sched_tick(now) -> bool:
    """定时判定+触发(注入 now 可测)。每日 GUANLAN_REGEN_DAILY_HOUR(默认18)点后、
    当日未自动处理过 → 触发一次;已有再生在跑(手动)也记当日已处理(单飞语义,不重复)。"""
    import os as _os
    hour = int(_os.environ.get("GUANLAN_REGEN_DAILY_HOUR", "18"))
    today = now.date().isoformat()
    if now.hour < hour or _REGEN_SCHED.get("last_auto_date") == today:
        return False
    _REGEN_SCHED["last_auto_date"] = today
    _REGEN_SCHED["last_auto_ts"] = now.isoformat(timespec="seconds")
    _start_regen_bg(None)   # 已在跑返 False 亦视为当日已处理(手动跑过就不叠一次)
    return True


def start_regen_daily_scheduler() -> None:
    """opt-in 每日 EOD 自动再生(env GUANLAN_REGEN_DAILY=1 才起 daemon 线程;缺省直接返回)。"""
    global _regen_sched_started
    import os as _os
    if _regen_sched_started or _os.environ.get("GUANLAN_REGEN_DAILY") != "1":
        return
    _regen_sched_started = True
    _REGEN_SCHED["enabled"] = True
    check_every = max(60, int(_os.environ.get("GUANLAN_REGEN_CHECK_EVERY", "600")))

    def _loop():
        import datetime as _dt
        import time as _t
        while True:
            try:
                _t.sleep(check_every)
                _regen_sched_tick(_dt.datetime.now())
            except Exception:  # noqa: BLE001 — 调度循环永不因单次异常退出
                continue

    _threading.Thread(target=_loop, name="regen-daily-scheduler", daemon=True).start()
```

- [ ] **Step 4: 端点改用抽取函数** — `screen_regen`(~1379 行)函数体替换为(docstring 逐字保持):

```python
    @router.post("/regen")
    def screen_regen(body: RegenIn):
        """「拉取最新数据」:后台子进程跑引擎原生 compute.regen 再生三产物,立即返回(异步,
        v4 LGB ~5min);完成自动热加载缓存(无需重启)。单飞:已在跑 → ok:False/already_running。"""
        started = _start_regen_bg(body.end or None)
        if not started:
            return JSONResponse({"ok": False, "reason": "already_running",
                                 "state": _regen_public_state()})
        return JSONResponse({"ok": True, "started": True, "state": _regen_public_state()})
```

- [ ] **Step 5: health 显形** — `screen_health` 最终 `return JSONResponse({...})` 里加一键:

```python
                             "regen_scheduler": {"enabled": bool(_REGEN_SCHED.get("enabled")),
                                                 "last_auto_ts": _REGEN_SCHED.get("last_auto_ts")},
```

- [ ] **Step 6: server.py 挂载** — `start_market_status_scheduler()` 调用行(~218)之后加:

```python
    # P1:regen 每日 EOD 自动再生(opt-in;GUANLAN_REGEN_DAILY=1 才启;
    # 定时器随本进程存亡,非 24/7 保证——进程死定时即停,health.regen_scheduler 显形)
    from guanlan_v2.screen.api import start_regen_daily_scheduler
    start_regen_daily_scheduler()
```

- [ ] **Step 7: 跑受影响测试**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_screen_api.py -v`
Expected: 全绿(4 新 + 既有含 P0 picks 测不破)

- [ ] **Step 8: Commit**

```bash
git add guanlan_v2/screen/api.py guanlan_v2/server.py tests/test_screen_api.py
git commit -m "feat(screen): regen 每日定时 opt-in(GUANLAN_REGEN_DAILY=1·18点后每日一次·单飞共用·health显形·默认关零行为变化)"
```

---

### Task 5: promote 阈值门(opt-in 默认关)

**Files:**
- Modify: `guanlan_v2/strategy/compute/model_workflow.py`(`_apply_promote_gate` + train_promote 尾部包裹 + `__main__` print)
- Modify: `guanlan_v2/screen/model_registry.py`(set_default_model 拒 draft)
- Modify: `guanlan_v2/screen/api.py`(`/screen/models` 加 include_draft)
- Modify: `guanlan_v2/console/tools.py`(model_list_impl 加 include_draft+⚠;model_promote_impl 加 wait 轮询与 draft 诚实报告;两注册条目 schema 同步)
- Test: `tests/test_model_workflow_promote.py`(门单测)、`tests/test_screen_api.py`(models 过滤/set_default 拒)、`tests/test_console_tools.py`(两 impl 测)

**Interfaces:**
- Consumes: 既有 `train_promote`(meta 构造在 save_variant 之前)、`variant_meta`/`save_variant`/`set_default_model`(model_registry.py)、`/model/promote`+`/model/promote/status`(workflow/api.py:6104-6134;train_promote 结果经子进程退出码与 `[model_promote] done …` 打印行透出到 state.lines)。
- Produces: `_apply_promote_gate(meta: dict, oos_ic) -> dict`;meta 新键 `status:"draft"`/`gate:{min_oos_ic,oos_ic,passed}`;`GET /screen/models?include_draft=<0|1>`;set_default_model 对 draft 抛 ValueError。

- [ ] **Step 1: 写失败测试**:
  1. `tests/test_model_workflow_promote.py` 尾部追加:

```python
# ── P1 §5: promote 阈值门(opt-in 默认关)────────────────────────────────────

def test_apply_promote_gate(monkeypatch):
    from guanlan_v2.strategy.compute.model_workflow import _apply_promote_gate
    monkeypatch.delenv("GUANLAN_PROMOTE_MIN_OOS_IC", raising=False)
    m = {"id": "m_x"}
    assert _apply_promote_gate(dict(m), 0.001) == m            # env 缺省=零行为变化
    monkeypatch.setenv("GUANLAN_PROMOTE_MIN_OOS_IC", "not-a-float")
    assert _apply_promote_gate(dict(m), 0.001) == m            # 非法值=门未启用
    monkeypatch.setenv("GUANLAN_PROMOTE_MIN_OOS_IC", "0.01")
    lo = _apply_promote_gate(dict(m), 0.004)
    assert lo["status"] == "draft" and lo["gate"]["passed"] is False
    assert lo["gate"]["min_oos_ic"] == 0.01 and lo["gate"]["oos_ic"] == 0.004
    none_ic = _apply_promote_gate(dict(m), None)
    assert none_ic["status"] == "draft"                        # 算不出 OOS 也进 draft
    hi = _apply_promote_gate(dict(m), 0.01)
    assert hi["gate"]["passed"] is True and "status" not in hi # ≥ 门槛(含相等)通过
```

  2. `tests/test_screen_api.py` 尾部追加:

```python
# ── P1 §5: models draft 过滤 + set_default 拒 draft ─────────────────────────

def _seed_variants(monkeypatch, tmp_path):
    import pandas as pd
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    row = pd.DataFrame({"code": ["SH600519"], "lgb_score": [1.0], "lgb_pct": [0.9],
                        "lgb_rank": [1], "v4_total": [5], "v4_layer": ["大盘"],
                        "date": ["2026-07-01"]})
    reg.save_variant("m_ok", row, {"id": "m_ok", "name": "过门", "oos_ic": 0.03})
    reg.save_variant("m_dr", row, {"id": "m_dr", "name": "草稿", "oos_ic": 0.001,
                                   "status": "draft",
                                   "gate": {"min_oos_ic": 0.01, "oos_ic": 0.001,
                                            "passed": False}})
    return reg


def test_models_filters_draft_by_default(monkeypatch, tmp_path):
    _seed_variants(monkeypatch, tmp_path)
    c = _client()
    ids = [v["id"] for v in c.get("/screen/models").json()["variants"]]
    assert "m_ok" in ids and "m_dr" not in ids                 # 默认不见 draft
    j = c.get("/screen/models?include_draft=1").json()
    ids2 = {v["id"]: v for v in j["variants"]}
    assert "m_dr" in ids2 and ids2["m_dr"]["status"] == "draft"


def test_set_default_rejects_draft(monkeypatch, tmp_path):
    reg = _seed_variants(monkeypatch, tmp_path)
    import pytest as _pt
    with _pt.raises(ValueError):
        reg.set_default_model("m_dr")
    j = _client().post("/screen/model/default", json={"id": "m_dr"}).json()
    assert j["ok"] is False and "draft" in j["reason"]
    reg.set_default_model("m_ok")                              # 非 draft 照常可设
    assert reg.get_default_model() == "m_ok"
```

  3. `tests/test_console_tools.py` 尾部追加:

```python
def test_model_list_impl_draft_badge(monkeypatch):
    sent = {}
    fake = {"ok": True, "default_model": None, "variants": [
        {"id": "m_dr", "name": "草稿", "n_features": 5, "oos_ic": 0.001, "status": "draft"}]}
    def fake_get(path, timeout=30):
        sent["path"] = path
        return fake
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.model_list_impl(include_draft=True)
    assert sent["path"] == "/screen/models?include_draft=1"
    assert "⚠draft未过门" in res["content"]
    ct.model_list_impl()
    assert sent["path"] == "/screen/models"                    # 默认不带参


def test_model_promote_impl_wait_reports_draft(monkeypatch):
    calls = {"status": 0}
    def fake_post(path, payload, timeout=120):
        return {"ok": True, "started": True, "variant_id": "m_w1"}
    def fake_get(path, timeout=30):
        if path.startswith("/model/promote/status"):
            calls["status"] += 1
            done = calls["status"] >= 2
            return {"ok": True, "state": {"running": (not done),
                                          "phase": ("done" if done else "train"),
                                          "ok": done, "error": None}}
        return {"ok": True, "default_model": None, "variants": [
            {"id": "m_w1", "status": "draft", "oos_ic": 0.004,
             "gate": {"min_oos_ic": 0.01, "oos_ic": 0.004, "passed": False}}]}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    monkeypatch.setattr(ct, "_self_get", fake_get)
    res = ct.model_promote_impl(name="w", features=["rev_20"], wait=True, poll_seconds=0)
    assert res["ok"] is True
    assert "draft 区" in res["content"] and "0.01" in res["content"]   # 诚实报未过门
```

  (注:model_promote_impl 现有签名以盘上为准——features 经何参名传入以现有 impl 为准,测试调用处按现有签名对齐;若其收 `factor_ids`/`features` 组合,沿用现有必填参最小集。)

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_model_workflow_promote.py -k gate tests/test_screen_api.py -k draft tests/test_console_tools.py -k "draft or promote_impl_wait" -v`
Expected: 全 FAIL(无 _apply_promote_gate / models 不过滤 / set_default 不拒 / impl 无参)

- [ ] **Step 3: model_workflow.py 门** — train_promote 之前加纯函数;train_promote 的 `meta = {...}` 构造之后、`reg.save_variant` 之前插 `meta = _apply_promote_gate(meta, oos_ic)`;成功 return 与 `__main__` 同步:

```python
def _apply_promote_gate(meta: Dict[str, Any], oos_ic) -> Dict[str, Any]:
    """P1 §5 opt-in 阈值门:env GUANLAN_PROMOTE_MIN_OOS_IC 设了才生效(缺省零行为变化)。
    不达标(含 oos_ic=None)→ meta.status="draft"(不进正式列表/不能设默认);达标记 passed。
    门只拦「不合格自动进正式货架」;采纳(设默认)永远人工确认。"""
    import os
    raw = os.environ.get("GUANLAN_PROMOTE_MIN_OOS_IC")
    if not raw:
        return meta
    try:
        gate = float(raw)
    except ValueError:
        print(f"[model_promote] warn: GUANLAN_PROMOTE_MIN_OOS_IC 非法值 '{raw}',门未启用", flush=True)
        return meta
    passed = (oos_ic is not None) and (float(oos_ic) >= gate)
    meta["gate"] = {"min_oos_ic": gate, "oos_ic": oos_ic, "passed": bool(passed)}
    if not passed:
        meta["status"] = "draft"
    return meta
```

  成功 return 改为:

```python
    res: Dict[str, Any] = {"ok": True, "variant_id": spec["variant_id"], "oos_ic": oos_ic}
    if meta.get("status") == "draft":
        res["status"] = "draft"
        res["gate"] = meta.get("gate")
    return res
```

  `__main__` done print 改为(draft 状态入 promote status 的 state.lines):

```python
    print(f"[model_promote] done ok={r.get('ok')} oos_ic={r.get('oos_ic')} "
          f"status={r.get('status') or 'ok'} reason={r.get('reason')}", flush=True)
```

- [ ] **Step 4: registry 拒 draft** — `set_default_model` 的存在性校验(`if not variant_ranking_path(...)`)之后、写指针之前插:

```python
    try:
        _m = variant_meta(model_id)
    except Exception:  # noqa: BLE001 — meta 读不了不拦(存在性已校验)
        _m = {}
    if (_m or {}).get("status") == "draft":
        raise ValueError(f"变体 {model_id} 处于 draft 区(未过 promote 门槛),不能设默认;"
                         f"复核后重训过门再设")
```

- [ ] **Step 5: /screen/models 过滤** — 端点改为:

```python
    @router.get("/models")
    def screen_models(include_draft: int = 0):
        from guanlan_v2.screen.model_registry import list_variants, get_default_model
        dflt = get_default_model()
        vs = list_variants()
        if not include_draft:
            vs = [v for v in vs if v.get("status") != "draft"]   # P1 门:draft 不进正式货架
        for v in vs:
            v["is_default"] = (v.get("id") == dflt)
        return JSONResponse({"ok": True, "variants": vs, "default_model": dflt})
```

- [ ] **Step 6: console 两 impl 增强** — `guanlan_v2/console/tools.py`:
  1. `model_list_impl` 签名改 `def model_list_impl(include_draft: bool = False)`;取数行改 `r = _self_get("/screen/models" + ("?include_draft=1" if include_draft else ""))`;行构造在 `star` 前加 `dr = " ⚠draft未过门" if m.get("status") == "draft" else ""`,行尾拼接改 `{oid}{unl}{dr}{star}`。
  2. `ww_model_list` 注册条目 schema properties 加:

```python
         "include_draft": {"type": "boolean", "default": False,
                           "description": "true=连 draft 区(未过 promote 门槛)一起列,带 ⚠ 标"},
```

  3. `model_promote_impl` 签名尾部加 `wait: bool = False, poll_seconds: float = 5.0, timeout_seconds: float = 900.0`;成功启动(拿到 `vid`)后、现有「已启动」return 之前插:

```python
    if wait:
        import time as _time
        deadline = _time.time() + float(timeout_seconds or 900.0)
        state: Dict[str, Any] = {}
        done = False
        while _time.time() <= deadline:
            try:
                s = _self_get("/model/promote/status")
            except Exception as e:
                return {"ok": False, "content": f"入库状态读取失败: {e}", "artifact": None}
            state = s.get("state") or {}
            if not state.get("running") and state.get("phase") == "done":
                done = True
                break
            if poll_seconds:
                _time.sleep(float(poll_seconds))
        if not done:
            return {"ok": False, "artifact": None, "raw": {"state": state},
                    "content": f"入库轮询超时 {vid}:后端可能仍在跑,稍后 ww_model_list 查"}
        if not state.get("ok"):
            return {"ok": False, "artifact": None, "raw": {"state": state},
                    "content": f"入库失败 {vid}: {state.get('error')}"}
        mrow = None
        try:
            mr = _self_get("/screen/models?include_draft=1")
            mrow = next((m for m in (mr.get("variants") or []) if m.get("id") == vid), None)
        except Exception:  # noqa: BLE001
            mrow = None
        g = (mrow or {}).get("gate") or {}
        if (mrow or {}).get("status") == "draft":
            return {"ok": True, "artifact": None, "raw": {"variant": mrow},
                    "content": (f"入库完成但未过门槛:oos_ic {g.get('oos_ic')} < 门槛 "
                                f"{g.get('min_oos_ic')},已落 draft 区(不进正式列表、"
                                f"不能设默认;ww_model_list include_draft=true 可见)")}
        oi = (mrow or {}).get("oos_ic")
        return {"ok": True, "artifact": None, "raw": {"variant": mrow},
                "content": f"入库完成 {vid}:留出 OOS IC {oi}"
                           + (f"(过门槛 {g.get('min_oos_ic')})" if g else "")
                           + f"。ww_model_validate id={vid} tier=strict 可跑 CPCV。"}
```

  4. `ww_model_promote` 注册条目 schema properties 加:

```python
         "wait": {"type": "boolean", "default": False,
                  "description": "true=轮询到入库完成并报 oos_ic/是否过门槛(draft 区诚实显形)"},
         "poll_seconds": {"type": "number", "default": 5},
         "timeout_seconds": {"type": "number", "default": 900},
```

- [ ] **Step 7: 跑受影响测试**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_model_workflow_promote.py tests/test_screen_api.py tests/test_console_tools.py -v`
Expected: 全绿(门 1 + draft 2 + impl 2 新;既有全不破——env 缺省下 train_promote 零行为变化)

- [ ] **Step 8: Commit**

```bash
git add guanlan_v2/strategy/compute/model_workflow.py guanlan_v2/screen/model_registry.py guanlan_v2/screen/api.py guanlan_v2/console/tools.py tests/test_model_workflow_promote.py tests/test_screen_api.py tests/test_console_tools.py
git commit -m "feat(workshop): promote 阈值门 opt-in(GUANLAN_PROMOTE_MIN_OOS_IC·不达标落draft区不进货架不能设默认·ww_model_promote wait诚实报门·默认关零行为变化)"
```

---

### Task 6: 全量回归 + 真机 e2e + 还原

**Files:** 无代码改动(纯验证,证据写任务报告)

- [ ] **Step 1: 全量回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest -q`
Expected: 全绿(基线 737 + 新增 ≈20 ≈ 757 passed;**0 failed 硬要求**)

- [ ] **Step 2: 生成真实 eqw 产物**(不跑 5 分钟全 regen,单独跑基准计算,全市场逐股读约 1-3 分钟):

```
G:/financial-analyst/.venv/Scripts/python.exe -c "from guanlan_v2.strategy.compute.eqw_market import compute_eqw_market; print(compute_eqw_market('G:/stocks/stock_data/cn_data'))"
```

Expected: 打印行数(≈1600+ 交易日);`guanlan_v2/strategy/vendor/artifacts/eqw_market_ret.parquet` 在位。

- [ ] **Step 3: 重启 9999**(P0 同款:找 9999 监听 PID → Stop-Process → Start-Process server.py → 等 ~30-60s 轮询 `/workflow/list` 通)。

- [ ] **Step 4: e2e — basket_perf 真算**:`GET /seats/basket_perf?codes=SH600519,SZ300750&start=<约30个交易日前>&horizon=5` → `ok:true`、`matured_n==2`、`avg_ret`/`bench_ret` 真数、`excess=avg−bench`;再用近几日 start 验未成熟分支(per_code 带 matured:false)。

- [ ] **Step 5: e2e — 成绩单全链**:`POST /screen/run` 带 `{"snapshot": true, "note": "p1-e2e", "factors": [], "topN": 10}` 落正式档案 → 直调 `picks_perf_impl()`(env GUANLAN_PORT=9999、PYTHONPATH 含 engine 的脚本)→ 断言 ok∈(True,False) 且 content 非空(当日选股无后续 bar 时诚实文案即正确结局);打印 content 留证。

- [ ] **Step 6: e2e — 开关默认关显形**:`GET /screen/health` → `regen_scheduler.enabled == false`;`GET /screen/models` 形状不变(本机无 draft);MCP `tools/list == 44` 且含 `ww_picks_perf`(scratchpad mcp 客户端脚本,P0 同款)。

- [ ] **Step 7: 还原检查**:e2e picks 档案(note="p1-e2e",snapshot:true)**保留**——它是 P1 第一条真实跟踪对象,数日后 ww_picks_perf 将对它算出真实成绩;`git status` 干净;无残留测试进程。

- [ ] **Step 8: Commit**(仅当 e2e 揪出计划外修复)

---

## Self-Review(计划自审记录)

1. **Spec coverage**:§1 产物(Task 1:parquet/regen 钩/mtime 缓存/PIT 不 ffill)✓;§2 端点(Task 2:口径/未成熟/基准 null/warnings/≤40 截断)✓;§3 工具+四处同步 40/65/44(Task 3,MCP 三处)✓;§4 定时 opt-in+health 显形+server 挂载(Task 4)✓;§5 门 opt-in+draft 区+set_default 拒+ww 两 impl(Task 5)✓;§6 验收(各任务 TDD+Task 6)✓;红线逐字入 Global Constraints ✓。
2. **Placeholder scan**:无 TBD/TODO;Task 5 测试调用 model_promote_impl 的参数以盘上现有签名对齐(已注明),非占位 ✓。
3. **Type consistency**:`eqw_cum_ret(df, entry, exit)->float|None` Task 1 定义 Task 2 消费;`compute_basket_perf(...)->dict` 键与端点响应、picks_perf_impl 消费一致;`_start_regen_bg(end)->bool` 端点/tick 一致;meta `status`/`gate` 键在 workflow/registry/api/console 四处一致;计数 40/65/44 全文一致 ✓。
