# 分钟级 agent 真跑(30分钟实验版) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent 真跑(`/seats/decide` + 落子复盘)支持 **30 分钟级**逐 bar 研判——复用现有 `compute_factors`(纯窗口运算)喂 30 分钟聚合序列,走 intraday PIT(只用 ≤决策时刻的 bar),目标:立昂微(SH605358)30 分钟跑 5 次、区间 2 周到现在,产出可审查的真实分钟级 run。

**Architecture:** 后端 `/seats/decide` 加 `freq` 参数;`freq=30min` 时把 date 当**带时分的 datetime**(不再 `.date()` 截断)、拉 5min 数据按 `perGroup=6` 聚成 30 分钟序列(与前端 `frameData` 同口径)、`compute_factors` 算「20 根 30 分钟 bar」窗口的因子(rev_20 不再是「20日」而是「20根30min bar」≈2.5 交易日)、PIT 只取 ≤决策 datetime 的 bar。前端 `runRealThink` 去掉 `tf==='D'` 硬守卫、30 分钟模式迭代 `bars30`(全量 5min→30min 聚合)逐 bar 调 decide(传 datetime + freq)、run 头记 `tf:'30min'`;`lzRunBacktest` 已是泛型按 idx 模拟成交、对 30 分钟 bar 直接可用。语义渲染加「单位」参数,分钟级标「根30分钟bar」消灭「20日」误读。

**实验边界(用户原话「先做实验,后面搭完整分钟因子库」):** 本计划 = **复用日线 `compute_factors` 跑 30 分钟 bar** 的最小实验;**不**新建分钟专属因子(日内动量/开盘缺口/分时量能等)——那是后续「完整分钟因子库」另案。新闻/大盘**不接**(用户:分钟级新闻/大盘输入与日线相同,暂沿用 decide 现状即 `regime=null`、研报按策略 refs)。

**Tech Stack:** FastAPI + pandas(后端 intraday 聚合/因子)/ no-build React UMD jsx(`ui/seats/`)/ pytest(后端 TDD,引擎测试走子进程指仓内 engine)

**硬约束(全程):**
- 本仓无 git,「提交」=跑 pytest 全绿(基线 **214 绿**,口径 `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`)
- 改 python 须重启 9999(杀监听 PID,看门狗 ~10s 拉新);改 jsx 必 bump `观澜 · 落子.html` 的 `?v=`(用 Edit 非 sed);改引擎须重启 9999;G:/stocks 只读
- `/seats/decide` 直调必传 date;30 分钟 date 形如 `'2026-06-11 10:30'`;落盘 code=normalize 后 SH/SZ 前缀;**PIT 红线**:绝不用 >决策 datetime 的 bar
- 前后端 30 分钟聚合**必须同口径**(`perGroup=6`、按日分组、不跨午休拼块),否则前端图与后端因子对不上
- 5min 数据走双 provider:`{'day':'G:/stocks/stock_data/cn_data','5min':'G:/stocks/stock_data/cn_data_5min'}`;`fetch_quote(c,start,end,'5min')` 已含 VN1b 量纲交叉定标

**勘察锚点(行号以 2026-06-13 为准,实现时以实际为准):**
- 后端:`guanlan_v2/seats/api.py` decide 在 `~:809-1052`(date 解析 `:851-855`、compute_factors `:862-868`、prompt 组装 `:885-980`、落盘 `:1028-1040`、响应 `:1041-1050`)
- 语义:`guanlan_v2/factorlib/semantics.py`(`render_factor`/`render_factors`,rev_20 渲染含「过去20日」字样)
- 因子:`engine/financial_analyst/factors/core.py` `compute_factors(df)`(纯窗口运算,读 `close`/`vol` 列;`_ret`/`_rsi`/`_macd_bar`/`_obv_slope` 全是 rolling/shift)
- 前端:`ui/seats/luozi-data.jsx`(`frameData` 5min→30min 聚合 `:515-550` perGroup=6;`runBacktest` `~:1072`;`lzSeatDecide` 封装 `~:978`;window 导出区 `~:1322`)
- 前端:`ui/seats/luozi-app.jsx`(`runRealThink` `:190-238`,守卫 `:192` `tf !== 'D'`;选 run→runDecs effect `:294-320` 按 `symbol.bars` 日期映射;`runPerf` `:177-181`)

---

## Task 1: 后端 decide 加 `freq=30min` —— intraday 30 分钟 PIT 因子(TDD)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(decide 函数体加 freq 分支 + 模块级聚合 helper)
- Test: `tests/test_seats_decide_intraday.py`(新建)

- [ ] **Step 1: 写聚合 helper 的失败测试** —— 创建 `tests/test_seats_decide_intraday.py`:

```python
# tests/test_seats_decide_intraday.py
# 30分钟 intraday 真跑(2026-06-13 minute-level-agent-backtest Task 1):
# - 模块级 _agg_5min_to_30min:按日分组、perGroup=6 聚 OHLCV(与前端 frameData 同口径)
# - decide freq=30min:date 带时分、PIT 只取 ≤datetime 的 5min、聚 30min、compute_factors 算分钟因子、asof 带时分
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd  # noqa: E402
from guanlan_v2.seats import api as seats_api  # noqa: E402


def _mk5(day, n, base=50.0):
    # 造 n 根 5min bar(同一天,09:35 起每 5 分钟一根),close 线性上行便于校验聚合
    ts = pd.date_range(f"{day} 09:35", periods=n, freq="5min")
    return pd.DataFrame({
        "trade_date": ts,
        "open": [base + i * 0.1 for i in range(n)],
        "high": [base + i * 0.1 + 0.05 for i in range(n)],
        "low": [base + i * 0.1 - 0.05 for i in range(n)],
        "close": [base + i * 0.1 for i in range(n)],
        "vol": [1000.0] * n,
        "amount": [base * 1000.0 * (1 + i * 0.002) for i in range(n)],
    })


def test_agg_5min_to_30min_perGroup6():
    # 12 根 5min → 2 根 30min(每 6 根一块);末块 close = 第12根 close、vol = 6×1000
    df5 = _mk5("2026-06-11", 12)
    df30 = seats_api._agg_5min_to_30min(df5)
    assert len(df30) == 2
    assert abs(float(df30["close"].iloc[0]) - float(df5["close"].iloc[5])) < 1e-9
    assert abs(float(df30["close"].iloc[1]) - float(df5["close"].iloc[11])) < 1e-9
    assert float(df30["vol"].iloc[0]) == 6000.0
    # high/low 是块内极值
    assert abs(float(df30["high"].iloc[0]) - float(df5["high"].iloc[0:6].max())) < 1e-9
    assert abs(float(df30["low"].iloc[0]) - float(df5["low"].iloc[0:6].min())) < 1e-9
    # trade_date 取块末根
    assert str(df30["trade_date"].iloc[0]) == str(df5["trade_date"].iloc[5])


def test_agg_groups_by_day_no_cross_lunch():
    # 两天数据:各 6 根 → 各 1 根 30min,绝不跨日拼块
    df5 = pd.concat([_mk5("2026-06-10", 6), _mk5("2026-06-11", 6)], ignore_index=True)
    df30 = seats_api._agg_5min_to_30min(df5)
    assert len(df30) == 2
    assert str(df30["trade_date"].iloc[0])[:10] == "2026-06-10"
    assert str(df30["trade_date"].iloc[1])[:10] == "2026-06-11"


def test_agg_empty_and_short():
    assert len(seats_api._agg_5min_to_30min(pd.DataFrame())) == 0
    # 不足 6 根:仍聚成 1 根(末块允许 <6)
    df30 = seats_api._agg_5min_to_30min(_mk5("2026-06-11", 4))
    assert len(df30) == 1
```

- [ ] **Step 2: 跑测试确认失败** —— `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_seats_decide_intraday.py -q` → Expected: 3 failed(`_agg_5min_to_30min` 不存在,AttributeError)

- [ ] **Step 3: 实现聚合 helper** —— `guanlan_v2/seats/api.py` 模块级(放在 `_persist_decision` 附近,如 `:177` 之后):

```python
def _agg_5min_to_30min(df5: "pd.DataFrame") -> "pd.DataFrame":
    """5min → 30min 聚合(与前端 frameData perGroup=6 同口径):按交易日分组,
    每 6 根 5min 切一块聚 OHLCV(末块允许 <6 根),绝不跨日拼块(午休/隔日断点天然分块)。
    入参须含 trade_date/open/high/low/close/vol(amount 可选);空/缺列 → 空 DataFrame。"""
    import pandas as _pd
    if df5 is None or len(df5) == 0:
        return _pd.DataFrame()
    need = {"trade_date", "open", "high", "low", "close", "vol"}
    if not need.issubset(set(df5.columns)):
        return _pd.DataFrame()
    df = df5.sort_values("trade_date").reset_index(drop=True)
    day = df["trade_date"].astype(str).str[:10]
    has_amt = "amount" in df.columns
    rows = []
    for _d, g in df.groupby(day, sort=True):
        g = g.reset_index(drop=True)
        for s in range(0, len(g), 6):
            ch = g.iloc[s:s + 6]
            rec = {
                "trade_date": ch["trade_date"].iloc[-1],
                "open": float(ch["open"].iloc[0]),
                "high": float(ch["high"].max()),
                "low": float(ch["low"].min()),
                "close": float(ch["close"].iloc[-1]),
                "vol": float(ch["vol"].sum()),
            }
            if has_amt:
                rec["amount"] = float(ch["amount"].sum())
            rows.append(rec)
    return _pd.DataFrame(rows)
```

- [ ] **Step 4: 跑测试确认聚合 helper 通过** —— `pytest tests/test_seats_decide_intraday.py -q` → 3 passed

- [ ] **Step 5: 写 decide freq=30min 分支的失败测试** —— 在同文件追加(monkeypatch loader 返 5min df + LLM 桩,参照 `tests/test_seats_runs.py` 的 `_FakeLLMClient`/`_patch_decide_chain` 装配):

```python
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeLLM:
    provider = "deepseek"; model = "deepseek-chat"
    @classmethod
    def for_agent(cls, name): return cls()
    def with_overrides(self, **kw): return self
    async def chat(self, messages, **kw):
        return {"choices": [{"message": {
            "content": '{"direction":"观望","confidence":55,"rationale":"分钟桩","key_evidence":["e"]}',
            "reasoning_content": ""}}]}


class _FakeLoader5:
    """fetch_quote(c,start,end,freq):freq=='5min' 返两天各 12 根 5min;'day' 返空(走分钟分支)。"""
    def fetch_quote(self, code, start, end, freq):
        if freq == "5min":
            return pd.concat([_mk5("2026-06-10", 12), _mk5("2026-06-11", 12, base=52.0)],
                             ignore_index=True)
        return pd.DataFrame()


def _client_intraday(monkeypatch, tmp_path):
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader5())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLM)
    app = FastAPI(); app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def test_decide_freq30min_pit_and_factors(tmp_path, monkeypatch):
    client = _client_intraday(monkeypatch, tmp_path)
    # 决策时刻 = 2026-06-11 10:05(覆盖 06-10 全天 + 06-11 前 6 根 5min)
    r = client.post("/seats/decide", json={
        "code": "SH605358", "name": "立昂微", "date": "2026-06-11 10:05",
        "seat_cn": "动量席", "creed": "测试", "mode": "fast", "freq": "30min"})
    j = r.json()
    assert j["ok"] is True
    # asof 带时分(分钟级),且 ≤ 决策时刻
    assert ":" in str(j["asof"])              # 'YYYY-MM-DD HH:MM' 含冒号
    assert str(j["asof"]) <= "2026-06-11 10:05"
    # 因子非空(compute_factors 跑在 30min 序列上)
    assert isinstance(j.get("factors"), dict) and len(j["factors"]) > 0
    # 落盘记录带 freq + asof 带时分
    import json as _json
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("freq") == "30min"
    assert ":" in str(rec.get("asof"))


def test_decide_freq_day_unchanged(tmp_path, monkeypatch):
    # freq 缺省/day → 旧日线路径(asof 不带时分),向后兼容
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    class _DayLoader:
        def fetch_quote(self, code, start, end, freq):
            ts = pd.date_range("2026-05-01", periods=80, freq="D")
            return pd.DataFrame({"trade_date": ts, "open": 50.0, "high": 51.0, "low": 49.0,
                                 "close": [50 + i * 0.1 for i in range(80)], "vol": 1000.0})
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLM)
    app = FastAPI(); app.include_router(seats_api.build_seats_router())
    c = TestClient(app)
    j = c.post("/seats/decide", json={"code": "SH600519", "name": "茅台", "date": "2026-06-11",
                                      "seat_cn": "动量席", "creed": "x", "mode": "fast"}).json()
    assert j["ok"] is True and ":" not in str(j["asof"])   # 日线 asof 'YYYY-MM-DD'
```

- [ ] **Step 6: 跑测试确认失败** —— `pytest tests/test_seats_decide_intraday.py -q` → Expected: 2 new failed(freq 分支未实现:freq=30min 走了日线空 df → ok:False 或 asof 无时分)

- [ ] **Step 7: 实现 decide freq=30min 分支** —— `guanlan_v2/seats/api.py` decide 函数体。在 payload 取参区(`~:827`,`regime = payload.get("regime")` 之后)加:

```python
            freq = str(payload.get("freq") or "day").strip().lower()
            freq = "30min" if freq in ("30min", "30", "30m") else "day"
```

把原日线取数块(`~:847-868`,`anchor = ... compute_factors(df)` 那段)替换为 freq 分派:

```python
        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            unit = "日"                                          # 语义渲染单位(Task 2)

            if freq == "30min":
                # —— 30 分钟 intraday PIT:date 带时分,只用 ≤决策时刻的 5min,聚 30min ——
                unit = "根30分钟bar"
                anchor_dt = _pd.Timestamp(date)                  # 'YYYY-MM-DD HH:MM',不截时分
                if anchor_dt > _pd.Timestamp.now():
                    anchor_dt = _pd.Timestamp.now()
                end_day = str(anchor_dt.date())
                start_day = str((anchor_dt - _pd.Timedelta(days=40)).date())  # 够 mom_60=60根30min(~7.5日)
                df5 = await asyncio.to_thread(loader.fetch_quote, c, start_day, end_day, "5min")
                df = _pd.DataFrame()
                asof = str(anchor_dt)[:16]
                if df5 is not None and len(df5) > 0:
                    df5 = df5[_pd.to_datetime(df5["trade_date"]) <= anchor_dt]   # ★ PIT:绝不用未来 bar
                    df = _agg_5min_to_30min(df5)
                if df is not None and len(df) > 0:
                    td = df["trade_date"].iloc[-1]
                    asof = str(td)[:16]                          # 末根 30min bar 'YYYY-MM-DD HH:MM'
            else:
                # —— 日线(原路径)——
                anchor = min(_pd.Timestamp(date), _pd.Timestamp.now())
                end = str(anchor.date())
                start = str((anchor - _pd.Timedelta(days=180)).date())
                df = await asyncio.to_thread(loader.fetch_quote, c, start, end, "day")
                asof = end
                if df is not None and len(df) > 0:
                    td = df["trade_date"].iloc[-1] if "trade_date" in df.columns else None
                    asof = str(td)[:10] if td is not None else end

            fac: dict = {}
            if df is not None and len(df) > 0:
                try:
                    from financial_analyst.factors.core import compute_factors
                    v = compute_factors(df)                      # ★ 30min 序列 → 分钟因子(窗口=根30min bar)
                    fac = {k: _num(v.get(k)) for k in
                           ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20")}
                except Exception:  # noqa: BLE001
                    fac = {}
```

注意:① 删掉原 `td = df["trade_date"].iloc[-1] ... asof = str(td)[:10]` 旧块(已并入上面分派);② `fm_backfill`(combo/fm 分位,`~:870-883`)只对日线有产物,30 分钟分支 `mdl` 留空 `{}` 即可——把 fm_backfill 查 parquet 块用 `if freq == "day":` 包住(30 分钟 mdl 保持空)。

落盘记录(`~:1028`)加 `"freq": freq,`;响应(`~:1041`)加 `"freq": freq,`。fac_line 渲染传 unit(Task 2 接线):

```python
            from guanlan_v2.factorlib.semantics import render_factors
            fac_line = render_factors(
                fac, ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20"), unit=unit)
```

usr_p 标的行改 `f"【标的】{name} {c} 截至 {asof}（{('30分钟K·日内' if freq=='30min' else '日线')}）\n"`(sys_p 的「截至 {asof} 已发生的信息」对 30 分钟天然成立,asof 带时分)。

- [ ] **Step 8: 跑测试** —— `pytest tests/test_seats_decide_intraday.py -q` → 5 passed
- [ ] **Step 9: 全量回归** —— Expected: **214 + 5 = 219 passed**(若日线分支被 freq 包裹改动碰红既有 decide 测试,修到绿,绝不改松断言)

## Task 2: 分钟语义渲染 —— `render_factors` 加 `unit` 参数(TDD)

**Files:**
- Modify: `guanlan_v2/factorlib/semantics.py`
- Test: `tests/test_factor_semantics.py`(追加)

**背景:** 日线渲染把 rev_20 说成「过去20**日**下跌X%」。30 分钟下应说「过去20**根30分钟bar**下跌X%」,否则重蹈「20日 vs 20bar」误读(修复#1 的同类坑)。

- [ ] **Step 1: 写失败测试** —— `tests/test_factor_semantics.py` 追加:

```python
def test_render_factors_unit_minute():
    from guanlan_v2.factorlib.semantics import render_factors
    fac = {"rev_20": 0.105, "rsi_14": 37.1}
    # 缺省单位=日(向后兼容,既有调用不传 unit)
    day = render_factors(fac, ("rev_20",))
    assert "20日" in day or "过去20日" in day
    # 分钟单位:出现「根30分钟bar」、不出现「20日」
    mn = render_factors(fac, ("rev_20",), unit="根30分钟bar")
    assert "30分钟bar" in mn
    assert "20日" not in mn
```

- [ ] **Step 2: 跑测试确认失败** —— `pytest tests/test_factor_semantics.py::test_render_factors_unit_minute -q` → FAIL(`render_factors` 不接受 unit kwarg → TypeError)

- [ ] **Step 3: 实现** —— `semantics.py` 读现有 `render_factor`/`render_factors`。给 `render_factors(fac, fields, unit="日")` 加形参;渲染窗口因子(rev_20/mom_60/ma_diff_20/turnover_20 等带「日」字样处)把字面「日」替换为 `unit`。最小改:`render_factor(field, value, unit="日")` 内把硬编「日」用 `unit` 拼;`render_factors` 透传 unit。rev_20 句例:`f"过去{w}{unit}下跌{...}%,超跌状态"`(w=窗口数 20)。**保持 unit 缺省=「日」**,既有不传 unit 的调用零行为变化。

- [ ] **Step 4: 跑测试** —— `pytest tests/test_factor_semantics.py -q` → 全绿(既有 + 新 1)
- [ ] **Step 5: 全量回归** —— Expected: 219 passed(decide 30min 分支 fac_line 现传 unit='根30分钟bar')
- [ ] **Step 6: 重启 9999 + 冒烟** —— 杀 9999 监听 PID 等看门狗拉新;curl 真机一笔:`POST /seats/decide {code:SH605358,date:'2026-06-11 10:30',freq:'30min',mode:fast,seat_cn:动量席,creed:x}` → 看响应 `asof` 带时分、`factors` 非空、`freq:'30min'`;rationale/key_evidence 里因子叙述出现「30分钟bar」而非「日」

## Task 3: 前端数据层 —— `bars30` 全量聚合 helper

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(新增 `bars30` + 导出 `lzBars30`)

**背景:** `runRealThink`(Task 4)与 30 分钟 run 审查(Task 5)都要**全量** 30 分钟序列(`frameData` 的 fbars 是视图窗口化的,只覆盖近 8 日,不够 2 周真跑)。新增不窗口化的全量聚合,口径与 `frameData` 的 perGroup=6 完全一致。

- [ ] **Step 1: 实现 `bars30`** —— `luozi-data.jsx`(放在 `frameData` 之后,`~:568`):

```javascript
// 全量 5min→30min 聚合(与 frameData perGroup=6 同口径,但不做视图窗口裁剪):
//   供 30 分钟 agent 真跑 + run 回放净值。无 bars5 → []。每根 {i,day,date:'YYYY-MM-DD HH:MM',o,c,h,l,v}。
function bars30(symbol) {
  const b5 = (symbol && symbol.bars5) || [];
  if (!b5.length) return [];
  const byDay = {}, dayOrder = [];
  b5.forEach(b => { if (!byDay[b.day]) { byDay[b.day] = []; dayOrder.push(b.day); } byDay[b.day].push(b); });
  const out = [];
  dayOrder.forEach(day => {
    const arr = byDay[day];
    for (let s = 0; s < arr.length; s += 6) {
      const grp = arr.slice(s, s + 6);
      out.push({ i: out.length, day, date: grp[grp.length - 1].date,
        o: grp[0].o, c: grp[grp.length - 1].c,
        h: Math.max.apply(null, grp.map(x => x.h)), l: Math.min.apply(null, grp.map(x => x.l)),
        v: +grp.reduce((a, x) => a + x.v, 0).toFixed(2) });
    }
  });
  return out;
}
```

- [ ] **Step 2: 导出** —— window 导出区(`~:1322`,`lzRunBacktest: runBacktest` 那行)加 `lzBars30: bars30,`
- [ ] **Step 3: 校验** —— `npx esbuild --loader:.jsx=jsx "G:\guanlan-v2\ui\seats\luozi-data.jsx" --outfile=NUL` → 0 error。bump `观澜 · 落子.html` 的 luozi-data.jsx `?v=`(下一档,如 20260613i);浏览器控制台 `window.lzBars30` 存在、对已载 5min 的票返回非空数组(date 带时分)

## Task 4: 前端 `runRealThink` 支持 30 分钟真跑

**Files:**
- Modify: `ui/seats/luozi-app.jsx`(runRealThink `:190-238`)

**背景:** 现守卫 `tf !== 'D' → return`,30 分钟下真跑按钮不执行。改为支持 `tf==='30'`:迭代全量 `bars30`、区间取**最近 2 周**(按 day 去重数末 10 个交易日的 30 分钟 bar)、逐 bar 调 decide 传 datetime + `freq:'30min'`、run 头记 `tf:'30min'`。

- [ ] **Step 1: 改守卫 + 取 bar 源** —— runRealThink 开头(`:192`):

```javascript
    if (realRun.running) { realStopRef.current = true; return; }
    if (mode !== 'backtest' || !window.lzSeatDecide) return;
    const isMin = (tf === '30');                       // 实验:仅放开 30 分钟;日线保持 tf==='D'
    if (tf !== 'D' && !isMin) return;                  // 其余 TF(周/60/15/5/1)暂不真跑
    const runBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    if (!runBars.length) return;
```

- [ ] **Step 2: 区间 = 最近 2 周(仅 30 分钟)** —— 紧接其后(替换原 `startIdx = Math.min(...)` 一行):

```javascript
    // 2 周窗口:30 分钟取末 10 个交易日的 30min bar;日线沿用游标 startIdx
    let startIdx, total = runBars.length;
    if (isMin) {
      const seen = {}; const days = [];
      for (let i = total - 1; i >= 0; i--) { const d = runBars[i].day; if (!seen[d]) { seen[d] = 1; days.push(d); } if (days.length >= 10) break; }
      const firstDay = days[days.length - 1];
      startIdx = runBars.findIndex(b => b.day >= firstDay);
      if (startIdx < 0) startIdx = 0;
    } else {
      startIdx = Math.min(Math.max(6, cursorRef.current || 0), total - 1);
    }
```

(同步把后续循环里引用 `bars`/`total`/`startIdx` 的地方改用 `runBars`/上面的 `total`/`startIdx`;原 `const ... total = n` 删除。)

- [ ] **Step 3: 循环传 datetime + freq** —— 循环里 `bars`→`runBars`、`bar.date`(30 分钟时已是 'YYYY-MM-DD HH:MM');cursor 推进**只在日线**做(`if (!isMin) { cursorRef.current = idx; setCursor(idx); setMarkerReveal(idx); }`——30 分钟不动日线游标,防视图错乱);`firstDate`/`lastDate` 记 `bar.date`(30 分钟含时分)。lzSeatDecide payload(`:218-227`)加 `freq`:

```javascript
        res = await window.lzSeatDecide({
          code: codeNow, name: meta.name, date: bar.date,     // 30分钟=带时分 datetime
          seat_cn: seatName, creed: creed, mode: 'fast',
          strategy_id: sid, strategy_name: seatName,
          card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
          cards: rcp.cards, recipe_factors: rcp.factors,
          research: (rcp.research || []).map(r => r.title + (r.from ? ' · ' + r.from : '')),
          regime: null, run_id: runId,
          freq: isMin ? '30min' : 'day',                      // ★ 分钟级走 intraday PIT
        });
```

- [ ] **Step 4: run 头记 tf** —— 循环后注册 run(`~:227-238`)POST /seats/runs 的 `tf` 由固定 `'D'` 改 `tf: isMin ? '30min' : 'D'`;`code` 仍传裸数字核;`start_date`/`end_date` = firstDate/lastDate(30 分钟为含时分字符串,RunPicker 直接显示)

- [ ] **Step 5: 校验** —— esbuild luozi-app.jsx 0 error;bump `?v=`;浏览器:切立昂微 + 30 分 TF + 当前策略(动量席或新建「立昂微·30分」席)→ 点「让 agent 真跑」→ 后端 `var/seats_decisions.jsonl` 出现 `freq:'30min'`、asof 带时分的记录,POST /seats/runs 出现 `tf:'30min'` 的 run 头

## Task 5: 30 分钟 run 审查 —— selRun 映射 bars30 + 流水/净值

**Files:**
- Modify: `ui/seats/luozi-app.jsx`(选 run→runDecs effect `:294-320`、runPerf `:177-181`)

**背景:** 现选 run 把 decision 的 asof(YYYY-MM-DD)映射 `symbol.bars`(日线)。30 分钟 run 的 asof 是 datetime,要映射到 `bars30`,且净值走 bars30。

- [ ] **Step 1: runDecs effect 按 run.tf 选 bar 源** —— `:294` effect 改:

```javascript
  useEffect(() => {
    let dead = false;
    if (!selRun) { setRunDecs([]); return; }
    (async () => {
      const rows = window.lzRunDecisions ? await window.lzRunDecisions(selRun.run_id) : [];
      if (dead) return;
      const isMin = (selRun.tf === '30min');
      const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : (symbol.bars || []);
      const byKey = {};                                  // 30分钟按完整 datetime;日线按 YYYY-MM-DD
      refBars.forEach((b, i) => { byKey[b.date] = i; });
      setRunDecs(rows.map(r => {
        const key = isMin ? String(r.asof || '').slice(0, 16) : String(r.asof || '').slice(0, 10);
        const idx = byKey[key] != null ? byKey[key] : -1;
        const dir = String(r.direction || '');
        const side = /买/.test(dir) ? 'buy' : (/卖/.test(dir) ? 'sell' : 'watch');
        return { key: 'run_' + (r.id || key), seat: r.strategy_id || 'run', idx, date: key, side,
                 direction: dir, conf: (r.confidence == null ? null : +r.confidence),
                 rationale: r.rationale || '', reasoning: r.reasoning || null, asof: r.asof,
                 model_name: r.model_name || '', key_evidence: r.key_evidence || [],
                 recipe_factors: r.recipe_factors || [], card_names: r.card_names || [],
                 research: r.research || [], factors_std: r.factors_std || null,
                 offChart: byKey[key] == null, _isRun: true };
      }));
    })();
    return () => { dead = true; };
  }, [selRun, symbol]);
```

- [ ] **Step 2: runPerf 按 run.tf 选 bar 源** —— `:177` runPerf 改:

```javascript
  const runPerf = useMemo(() => {
    if (mode !== 'backtest' || !selRun || !window.lzRunBacktest) return null;
    const isMin = (selRun.tf === '30min');
    if (!isMin && tf !== 'D') return null;               // 日线 run 仅日线 TF 算;30分钟 run 任意 TF 都算
    const refBars = isMin ? (window.lzBars30 ? window.lzBars30(symbol) : []) : symbol.bars;
    return window.lzRunBacktest(runDecs, refBars);
  }, [mode, selRun, tf, runDecs, symbol]);
```

(`runDecs` 的 idx 已在 Step 1 按 bars30 映射,`lzRunBacktest(runDecs, bars30)` 直接出净值/指标。)

- [ ] **Step 3: 校验审查链** —— esbuild 0 error;bump `?v=`;浏览器:选中一个 30 分钟 run → 右栏「回测历史」内嵌流水每行显示 `2026-06-11 10:30 观望 …`(datetime)、点行 RunDecCard 显示分钟因子读数(factors_std)+「30分钟bar」叙述;头部 MetricsStrip 显示该 run 真实净值/Sharpe(由 bars30 模拟成交)。**K 线 30 分钟视图的金框 marks 对齐为已知限制**:`dispFrame.fbars` 是窗口化的 30 分钟,truedecs idx 来自 bars30 全量——若 marks 错位,本实验以**流水+RunDecCard 审查为准**(用户审查主路径),marks 对齐留作后续细化挂账

## Task 6: 收口 + 真机跑 5 次 + memory

- [ ] **Step 1: 全量 pytest** —— Expected: **219 passed**(214 + 5;Task 2 语义测试含在内)
- [ ] **Step 2: 重启 9999 + 探活** —— 杀监听 PID 等看门狗 ~10s;`/health` 200
- [ ] **Step 3: 终版 bump** —— `观澜 · 落子.html` 的 luozi-data.jsx / luozi-app.jsx `?v=` 统一终档
- [ ] **Step 4: 真机跑 5 次(立昂微 30 分钟 · 2 周)** —— 浏览器:切立昂微 → 30 分 TF → 当前策略(动量席或新建「立昂微·30分」席)→ 点「让 agent 真跑」连跑 5 次;每次约 80 根 30 分钟 bar × deepseek-chat ~2s ≈ 2.5 分钟/次。验真:① RunPicker 出现 5 个 `tf:30min` run;② 选中任一 → 流水每笔带 datetime(同日多根 30 分钟 bar **研判不再趋同**=因子有日内分辨率的铁证,对比日线级的「同日重复」);③ RunDecCard 因子读数标「根30分钟bar」;④ 净值由 bars30 模拟成交、真实有涨跌
- [ ] **Step 5: PIT 抽查** —— 控制台对某笔 30 分钟决策核 asof ≤ 决策 datetime(后端 PIT 过滤生效);随机抽一根 10:30 的决策,确认其因子只用了 ≤10:30 的 bar
- [ ] **Step 6: memory + README** —— `ui/seats/README.md` 加「30 分钟 agent 真跑(实验版)」节(freq=30min 链路 + perGroup=6 同口径 + 复用 compute_factors + 单位语义);新建/更新 memory `luozi-minute-backtest.md`(实验落地 + 复用日线因子的「尺度不同」口径 + 完整分钟因子库挂账 + K线marks对齐挂账 + 新闻分钟级挂账)

---

## Self-Review(已执行)

- **Spec 覆盖:** 复用 compute_factors 喂 30min(Task 1 Step 7 `compute_factors(df30)`)✓;decide freq=30min intraday PIT(Task 1)✓;前端 runRealThink 30 分钟(Task 4)✓;lzRunBacktest 兼容(Task 5,泛型按 idx 直接可用,refBars=bars30)✓;分钟语义(Task 2)✓;立昂微 5 次 2 周(Task 6 Step 4)✓。
- **占位符扫描:** 无 TBD;聚合/PIT/因子/区间/映射全给实码。唯一显式标注的限制 = 30 分钟 K 线 marks 对齐(Task 5 Step 3,降级到流水审查,非占位符是诚实边界)。
- **类型一致:** `_agg_5min_to_30min(df5)->df30`(列 trade_date/open/high/low/close/vol[/amount])Task1 定义、Task1 Step7 消费一致;`bars30(symbol)->[{i,day,date,o,c,h,l,v}]` Task3 定义、Task4/5 消费一致;run 头 `tf:'30min'` Task4 写、Task5 读一致;`render_factors(fac,fields,unit)` Task2 定义、Task1 Step7 调用一致;`freq` 字段 decide 落盘(Task1)与 runRealThink payload(Task4)一致。
- **已知坑回灌:** date 必传 + 30 分钟 datetime 格式;前后端 perGroup=6 同口径(否则图/因子错位);PIT 过滤 `≤ anchor_dt`(分钟红线);fm_backfill 仅日线(freq 包裹);引擎/python 改动重启 9999;jsx 改 bump ?v;子进程测试指仓内 engine。
