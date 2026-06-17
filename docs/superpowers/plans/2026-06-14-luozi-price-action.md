# 落子 · 价格行为(价量几何 + 方法论 prompt)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给落子每个策略实例装上确定性「价量几何特征」层(替掉 scanSeat 拍脑袋骨架 + 喂 LLM 研判),与一段每策略可编辑、校场可开关的「价格行为方法论」prompt。

**Architecture:** 后端 `seats/price_action.py` 纯函数从 OHLC 算几何 + 渲染 prompt 块,decide 始终算几何并随响应回前端、仅 `pa` 开才注入 prompt;前端 `luozi-data.jsx` 一套 JS 镜像(scanSeat 用 + 决策卡显示);校场每策略一个开关 + 可编辑方法论 textarea。几何**常显**(标确定性·非LLM),开关只管 LLM prompt 注入。默认关,零回归。

**Tech Stack:** Python(FastAPI · pytest TDD)/ React UMD(no-build,in-browser babel,`?v=` bump)/ 引擎 `fetch_quote`。

**仓库约束(铁律):** 本仓**无 git**——「提交」= 跑 pytest 全绿,**绝不 git init/commit**。改 python 须**重启 9999**(杀监听 PID 等 ~10s 看门狗拉新代码)。改 jsx 必 **bump `?v`**(用 Edit,非 sed)。pytest 命令:`G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`。spec:`docs/superpowers/specs/2026-06-14-luozi-price-action-design.md`。

**契约红线:** `compute_pa_features`(Python 权威)与 `paFeatures`(JS 镜像)**同一公式/枚举**,改一边必同步另一边。PIT:只用 ≤决策 bar 的数据;`follow` 仅 prev→current 向后看,绝不取未来 bar。诚实降级:不足窗口 → None → 渲染「—」,绝不补合成值。

---

## File Structure

- `guanlan_v2/seats/price_action.py` **(新建)** — `compute_pa_features` / `render_pa_block` / `PA_METHOD_DEFAULT`,纯函数零 I/O。
- `tests/test_price_action.py` **(新建)** — 纯函数 TDD。
- `tests/test_seats_decide_pa.py` **(新建)** — decide 接线测试(monkeypatch LLM/loader)。
- `guanlan_v2/seats/api.py` **(改)** — decide:读 pa/pa_method、算 pa_feat、注入 prompt、响应 + 落盘。
- `ui/seats/luozi-data.jsx` **(改)** — `paFeatures`/`renderPaNote`/`LZ_PA_METHOD_DEFAULT`/scanSeat 升级/strategySave +pa+paMethod。
- `ui/seats/luozi-foundry.jsx` **(改)** — 开关 + 方法论 textarea + 保存。
- `ui/seats/luozi-panels.jsx` **(改)** — DecisionCard 几何块 + runDecide 传 pa。
- `ui/seats/观澜 · 落子.html` **(改)** — bump data/panels/foundry `?v`。
- `ui/seats/README.md`、memory **(改)** — 收尾。

---

## Task 1: 后端 `price_action.py` 纯函数(TDD)

**Files:**
- Create: `guanlan_v2/seats/price_action.py`
- Test: `tests/test_price_action.py`

- [ ] **Step 1: 写失败测试** — `tests/test_price_action.py`

```python
# tests/test_price_action.py
# 落子价量几何特征纯函数(price_action.py)单测:逐特征公式 + A股 涨跌停 + PIT 降级 + 渲染。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from guanlan_v2.seats.price_action import (  # noqa: E402
    compute_pa_features, render_pa_block, PA_METHOD_DEFAULT, _board_limit, _bar_type)


def _df(rows):
    """rows: list of (open, high, low, close, vol);trade_date 自动按日填。"""
    ts = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame({
        "trade_date": ts,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "vol": [r[4] for r in rows],
    })


def test_empty_or_missing_cols():
    assert compute_pa_features(pd.DataFrame()) == {}
    assert compute_pa_features(pd.DataFrame({"open": [1]})) == {}


def test_single_bar_geometry_trend_bull():
    # o=10 h=11 l=9.8 c=10.9 → rng=1.2,body=0.9/1.2=0.75,close_pos=1.1/1.2≈0.917
    feat = compute_pa_features(_df([(10, 11, 9.8, 10.9, 1000)]))
    assert feat["bar_type"] == "趋势阳"
    assert feat["body"] == 0.75
    assert feat["close_pos"] == 0.917
    assert feat["upper_wick"] == 0.083
    assert feat["lower_wick"] == 0.167
    # 首根:无 prev → limit/gap None,breakout/vol_ratio/ema/atr None(窗口不足)
    assert feat["limit"] is None and feat["gap"] is None
    assert feat["range_atr"] is None and feat["ema20_rel"] is None


def test_doji_and_flat():
    assert compute_pa_features(_df([(10, 10.5, 9.5, 10.02, 1000)]))["bar_type"] == "十字"
    assert compute_pa_features(_df([(10, 10, 10, 10, 1000)]))["bar_type"] == "平"


def test_inside_and_outside_and_streak():
    # 第2根被第1根包住 = 内含bar;第3根再内含 → streak=2
    feat = compute_pa_features(_df([(10, 12, 8, 11, 1000), (10, 11, 9, 10, 1000), (9.5, 10.5, 9.2, 10, 1000)]))
    assert feat["bar_type"] == "内含bar"
    assert feat["inside_streak"] == 2
    # 外包:末根包住前根且收阳
    feat2 = compute_pa_features(_df([(10, 11, 9, 10, 1000), (9, 12, 8.5, 11.5, 1000)]))
    assert feat2["bar_type"] == "外包阳"


def test_breakout_up_and_down():
    base = [(10, 10.5, 9.5, 10, 1000)] * 5
    up = compute_pa_features(_df(base + [(10, 12, 9.8, 11.8, 1000)]))
    assert up["breakout"] == "突破前5高"
    down = compute_pa_features(_df(base + [(10, 10.2, 9.0, 9.1, 1000)]))
    assert down["breakout"] == "跌破前5低"


def test_vol_ratio():
    rows = [(10, 10.5, 9.5, 10, 1000)] * 5 + [(10, 10.5, 9.5, 10, 2000)]
    assert compute_pa_features(_df(rows))["vol_ratio"] == 2.0


def test_limit_by_board():
    # 主板 600:+10% → 涨停;+8% → 接近涨停
    main = _df([(10, 11, 10, 10, 1000), (10.9, 11.2, 10.9, 11.0, 1000)])
    assert compute_pa_features(main, code="SH600519")["limit"] == "涨停"
    near = _df([(10, 11, 10, 10, 1000), (10.7, 10.9, 10.7, 10.8, 1000)])
    assert compute_pa_features(near, code="SH600519")["limit"] == "接近涨停"
    # 科创 688:同样 +10% 不算涨停也不接近(板幅 20%,阈值 0.7×0.20=0.14)→ 正常(与主板对照)
    star = _df([(10, 11, 10, 10, 1000), (10.9, 11.2, 10.9, 11.0, 1000)])
    assert compute_pa_features(star, code="SH688111")["limit"] == "正常"
    # ST:+5% → 涨停
    st = _df([(10, 11, 10, 10, 1000), (10.4, 10.6, 10.4, 10.5, 1000)])
    assert compute_pa_features(st, code="SH600519", name="*ST 测试")["limit"] == "涨停"


def test_gap():
    up = _df([(10, 10.5, 9.5, 10, 1000), (10.2, 10.6, 10.1, 10.3, 1000)])
    assert compute_pa_features(up)["gap"] == "高开"
    down = _df([(10, 10.5, 9.5, 10, 1000), (9.8, 10.0, 9.6, 9.9, 1000)])
    assert compute_pa_features(down)["gap"] == "低开"


def test_follow_confirm_and_weaken():
    # 前根趋势阳(o9 c10 实体大),本根收更高且收阳 → 已确认(多)
    conf = _df([(9, 10.1, 8.9, 10, 1000), (10, 10.8, 9.9, 10.6, 1000)])
    assert compute_pa_features(conf)["follow"] == "已确认(多)"
    # 前根趋势阳,本根收阴且跌破前低 → 转弱
    weak = _df([(9, 10.1, 8.9, 10, 1000), (10, 10.1, 8.5, 8.6, 1000)])
    assert compute_pa_features(weak)["follow"] == "转弱"


def test_atr_and_ema_need_window():
    rows = [(10 + i * 0.1, 10.5 + i * 0.1, 9.5 + i * 0.1, 10 + i * 0.1, 1000) for i in range(25)]
    feat = compute_pa_features(_df(rows))
    assert feat["range_atr"] is not None   # ≥15 根
    assert feat["ema20_rel"] is not None   # ≥20 根
    short = compute_pa_features(_df(rows[:10]))
    assert short["range_atr"] is None and short["ema20_rel"] is None


def test_recent_three():
    rows = [(10, 11, 9, 10.8, 1000), (10, 10.2, 9.9, 10.0, 1000), (10, 11.5, 8.5, 8.7, 1000), (9, 9.5, 8.8, 9.4, 1000)]
    feat = compute_pa_features(_df(rows))
    assert len(feat["recent"]) == 3
    assert all(r is not None for r in feat["recent"])


def test_helpers():
    assert _board_limit("SH688111", "") == 0.20
    assert _board_limit("SZ300001", "") == 0.20
    assert _board_limit("BJ830001", "") == 0.30
    assert _board_limit("SH600519", "*ST x") == 0.05
    assert _board_limit("SH600519", "") == 0.10
    assert _bar_type(10, 11, 9.8, 10.9, None, None) == "趋势阳"


def test_render_block():
    feat = compute_pa_features(_df([(10, 11, 9.8, 10.9, 1000)]))
    s = render_pa_block(feat)
    assert "趋势阳" in s and "—" in s   # 含型态,缺窗项渲染 —
    assert render_pa_block({}) == ""
    assert "(每根=根30分钟bar)" in render_pa_block(feat, unit="根30分钟bar")
    assert isinstance(PA_METHOD_DEFAULT, str) and "T+1" in PA_METHOD_DEFAULT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_price_action.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'guanlan_v2.seats.price_action'`

- [ ] **Step 3: 写实现** — `guanlan_v2/seats/price_action.py`

```python
"""落子 · 价量几何特征(clean-room,A股 适配)。

借鉴 PA_Agent 思路,公式为公知技术分析数学,独立实现;特征选择 / A股 适配 / 命名为本仓自有。
纯函数、零 I/O:只吃 OHLC DataFrame(已 PIT≤asof、时间升序),供 decide LLM 与前端镜像。
**契约**:前端 ui/seats/luozi-data.jsx `paFeatures()` 是本函数的 JS 镜像 —— 改一边必同步另一边。
PIT 红线:只用 ≤最新根(决策 bar)的数据;follow 仅向后看(prev→current),不取未来 bar。
"""
from __future__ import annotations

PA_METHOD_DEFAULT = (
    "价格行为读法(A股·做多为主):\n"
    "1. 趋势 vs 区间:连续同向趋势棒(实体大、影线短、收于端部)= 趋势;互相重叠、影线长、收于中部 = 区间/震荡。趋势中顺势,区间中高抛低吸或观望。\n"
    "2. 突破与回踩:放量突破前高(实体强、收于上沿)后,优先等第一次缩量回踩不破前高/均线企稳再进,胜率高于追突破当根。突破后迅速收回、留长上影 = 假突破,警惕。\n"
    "3. 信号棒 + 跟随确认:孤立一根强棒不够,要看其后是否被同向棒跟随确认;无跟随、被反向吞没 = 信号失效。\n"
    "4. 两腿回调:上升趋势中的回调常走两腿,第二腿缩量不破关键支撑后的转强棒,是较稳的右侧买点。\n"
    "5. 位置感:同样形态在低位/超跌区比在高位/拥挤区可靠;高位放量滞涨、长上影、量价背离 = 退潮信号,降权或止盈。\n"
    "6. A股 特有口径:T+1 当日买入次日才能卖,需为隔夜留余地;涨停封板≠可任意买卖(流动性骤降),涨停打开放量要警惕,跌停同理;ST 股 ±5% 幅度小、波动定义不同;不做空,只在做多方向取信号,看空时以「观望/减仓」表达。\n"
    "几何特征是确定性事实(本席已附),本读法只是推理框架,不替代证据;证据不足时给「观望」。"
)


def _rnd(x, p=3):
    if x is None:
        return None
    try:
        return round(float(x), p)
    except Exception:  # noqa: BLE001
        return None


def _board_limit(code: str, name: str = "") -> float:
    """涨跌停板幅:ST 0.05 / 科创(688)·创业(300) 0.20 / 北交(8/4/BJ) 0.30 / 主板 0.10。"""
    if "ST" in (name or "").upper().replace(" ", ""):
        return 0.05
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if digits[:3] in ("688", "300"):
        return 0.20
    if (digits[:1] in ("8", "4")) or (code or "").upper().startswith("BJ"):
        return 0.30
    return 0.10


def _bar_type(o, h, l, c, ph, pl):
    rng = h - l
    if rng <= 0:
        return "平"
    if ph is not None and pl is not None:
        if h <= ph and l >= pl:
            return "内含bar"
        if h >= ph and l <= pl:
            return "外包阳" if c >= o else "外包阴"
    body = abs(c - o) / rng
    if body < 0.1:
        return "十字"
    if body >= 0.5:
        return "趋势阳" if c > o else "趋势阴"
    return "小阳" if c >= o else "小阴"


def compute_pa_features(df, code: str = "", name: str = "") -> dict:
    """算最新一根(决策 bar)的价量几何特征。空/列缺 → {};不足窗口的项诚实 None。
    code/name 仅用于涨跌停板幅判定。纯函数,无 I/O。"""
    need = ("open", "high", "low", "close", "vol")
    if df is None or len(df) == 0 or any(col not in df.columns for col in need):
        return {}
    o = [float(x) for x in df["open"]]
    h = [float(x) for x in df["high"]]
    lo = [float(x) for x in df["low"]]
    c = [float(x) for x in df["close"]]
    v = [float(x) for x in df["vol"]]
    n = len(c)
    i = n - 1
    rng = h[i] - lo[i]
    prev_close = c[i - 1] if i >= 1 else None
    ph = h[i - 1] if i >= 1 else None
    pl = lo[i - 1] if i >= 1 else None

    body = _rnd(abs(c[i] - o[i]) / rng) if rng > 0 else None
    upper = _rnd((h[i] - max(o[i], c[i])) / rng) if rng > 0 else None
    lower = _rnd((min(o[i], c[i]) - lo[i]) / rng) if rng > 0 else None
    close_pos = _rnd((c[i] - lo[i]) / rng) if rng > 0 else None

    range_atr = None
    if n >= 15:
        trs = [max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
               for k in range(n - 14, n)]
        atr = sum(trs) / len(trs)
        range_atr = _rnd(rng / atr) if atr > 0 else None

    ema20_rel = None
    if n >= 20:
        kf = 2.0 / 21.0
        ema = c[0]
        for x in c[1:]:
            ema = x * kf + ema * (1 - kf)
        ema20_rel = _rnd((c[i] - ema) / ema) if ema != 0 else None

    bar_type = _bar_type(o[i], h[i], lo[i], c[i], ph, pl)

    breakout = None
    if i >= 5:
        prev5_high = max(h[i - 5:i])
        prev5_low = min(lo[i - 5:i])
        if h[i] > prev5_high:
            breakout = "突破前5高"
        elif lo[i] < prev5_low:
            breakout = "跌破前5低"
        else:
            breakout = "区间内"

    inside_streak = 0
    for k in range(i, 0, -1):
        if h[k] <= h[k - 1] and lo[k] >= lo[k - 1]:
            inside_streak += 1
        else:
            break

    vol_ratio = None
    if i >= 5:
        base = sum(v[i - 5:i]) / 5.0
        vol_ratio = _rnd(v[i] / base, 2) if base > 0 else None

    limit = None
    gap = None
    if prev_close not in (None, 0):
        L = _board_limit(code, name)
        pct = (c[i] - prev_close) / prev_close
        if pct >= L - 0.003:
            limit = "涨停"
        elif pct >= 0.7 * L:
            limit = "接近涨停"
        elif pct <= -(L - 0.003):
            limit = "跌停"
        elif pct <= -0.7 * L:
            limit = "接近跌停"
        else:
            limit = "正常"
        if o[i] > prev_close * 1.002:
            gap = "高开"
        elif o[i] < prev_close * 0.998:
            gap = "低开"
        else:
            gap = "无"

    follow = None
    if i >= 1:
        pp_h = h[i - 2] if i >= 2 else None
        pp_l = lo[i - 2] if i >= 2 else None
        pbt = _bar_type(o[i - 1], h[i - 1], lo[i - 1], c[i - 1], pp_h, pp_l)
        if pbt in ("趋势阳", "外包阳"):
            if c[i] > c[i - 1] and c[i] > o[i]:
                follow = "已确认(多)"
            elif c[i] < lo[i - 1]:
                follow = "转弱"
        elif pbt in ("趋势阴", "外包阴"):
            if c[i] < c[i - 1]:
                follow = "已确认(空)"
            elif c[i] > h[i - 1]:
                follow = "转弱"

    recent = []
    for back in (1, 2, 3):
        k = i - back
        if k >= 0:
            recent.append(_bar_type(o[k], h[k], lo[k], c[k],
                                    h[k - 1] if k >= 1 else None, lo[k - 1] if k >= 1 else None))
        else:
            recent.append(None)

    date = str(df["trade_date"].iloc[i]) if "trade_date" in df.columns else None

    return {
        "date": date, "bar_type": bar_type,
        "body": body, "upper_wick": upper, "lower_wick": lower, "close_pos": close_pos,
        "range_atr": range_atr, "ema20_rel": ema20_rel,
        "breakout": breakout, "inside_streak": inside_streak, "vol_ratio": vol_ratio,
        "limit": limit, "gap": gap, "follow": follow, "recent": recent,
    }


def render_pa_block(feat: dict, unit: str = "日") -> str:
    """渲染成 prompt 文本块。None → 「—」;feat 空 → 空串。"""
    if not feat:
        return ""

    def s(x):
        return "—" if x in (None, "") else str(x)

    def f(x):
        if x is None:
            return "—"
        return ("%.3f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)

    parts = [
        f"最新K({s(feat.get('date'))}):{s(feat.get('bar_type'))}",
        f"实体{f(feat.get('body'))}",
        f"收盘位{f(feat.get('close_pos'))}",
        f"上影{f(feat.get('upper_wick'))}/下影{f(feat.get('lower_wick'))}",
        f"振幅{f(feat.get('range_atr'))}×ATR",
        f"距EMA20{f(feat.get('ema20_rel'))}",
        s(feat.get("breakout")),
        f"量比{f(feat.get('vol_ratio'))}×",
        s(feat.get("limit")),
        f"跳空{s(feat.get('gap'))}",
    ]
    if feat.get("inside_streak"):
        parts.append(f"连续内含{feat['inside_streak']}根")
    if feat.get("follow"):
        parts.append(f"跟随{feat['follow']}")
    line = "·".join(parts)
    rec = "/".join(s(r) for r in (feat.get("recent") or []))
    if rec:
        line += f";近3根:{rec}"
    if unit and unit != "日":
        line += f"(每根={unit})"
    return line
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_price_action.py -q`
Expected: PASS(全部用例绿)

- [ ] **Step 5: Checkpoint(本仓无 git = 跑全量 pytest)**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`
Expected: 全绿(原 290 + 本任务新增,无回归)

---

## Task 2: decide 接线(`seats/api.py`)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(`seats_decide`,1254–1554)
- Test: `tests/test_seats_decide_pa.py`(新建)

- [ ] **Step 1: 写失败测试** — `tests/test_seats_decide_pa.py`

```python
# tests/test_seats_decide_pa.py
# decide 接线价格行为:pa 开→prompt 含两块 + 响应/落盘带 pa_features;pa 关→prompt 不含但响应仍带几何。
import sys
import json as _json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from guanlan_v2.seats import api as seats_api  # noqa: E402

_CAP = {}


class _CapLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        _CAP["user"] = messages[-1]["content"]
        return {"choices": [{"message": {
            "content": '{"direction":"观望","confidence":50,"rationale":"r","key_evidence":[]}',
            "reasoning_content": ""}}]}


class _DayLoader:
    def fetch_quote(self, code, start, end, freq):
        ts = pd.date_range("2026-02-01", periods=60, freq="D")
        return pd.DataFrame({"trade_date": ts,
                             "open": [50 + i * 0.1 for i in range(60)],
                             "high": [50 + i * 0.1 + 0.5 for i in range(60)],
                             "low": [50 + i * 0.1 - 0.5 for i in range(60)],
                             "close": [50 + i * 0.1 + 0.3 for i in range(60)],
                             "vol": [1000.0 + i for i in range(60)]})


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
    monkeypatch.setattr(_llm, "LLMClient", _CapLLM)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _post(client, **extra):
    body = {"code": "SH600519", "name": "茅台", "date": "2026-04-01",
            "seat_cn": "动量席", "creed": "x", "mode": "fast"}
    body.update(extra)
    return client.post("/seats/decide", json=body).json()


def test_pa_on_injects_blocks_and_returns_features(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), pa=True, pa_method="我的读法ABC")
    assert r["ok"] is True
    assert isinstance(r.get("pa_features"), dict) and r["pa_features"].get("bar_type")
    assert "【价量形态·确定性" in _CAP["user"]
    assert "我的读法ABC" in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("pa") is True
    assert isinstance(rec.get("pa_features"), dict)


def test_pa_on_empty_method_uses_default(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), pa=True)
    assert r["ok"] is True
    assert "【价格行为读法" in _CAP["user"]
    assert "T+1" in _CAP["user"]   # 默认模板兜底


def test_pa_off_no_blocks_but_features_present(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path))
    assert r["ok"] is True
    assert isinstance(r.get("pa_features"), dict)          # 几何常显:响应仍带
    assert "【价量形态·确定性" not in _CAP["user"]
    assert "【价格行为读法" not in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("pa") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_seats_decide_pa.py -q`
Expected: FAIL(`pa_features` 不在响应 / prompt 无两块 / `pa` 未落盘)

- [ ] **Step 3a: 读 pa/pa_method 入参** — `seats/api.py`,在 `regime = payload.get("regime")`(L1275)后插入:

```python
        # 价格行为(price-action):pa 开关 + 可编辑方法论(几何始终算/回响应,pa 仅控制 prompt 注入)。
        pa = bool(payload.get("pa"))
        pa_method = str(payload.get("pa_method") or "")
```

- [ ] **Step 3b: 算几何** — 在 fac 块(L1332–1340)之后、mdl 块(L1342)之前插入:

```python
            # 价量几何特征(确定性,§ price_action.py):始终算(便宜),随响应回前端供决策卡显示;
            # 仅 pa 开时注入 LLM prompt。df 已 PIT≤asof,故取最新根=决策 bar 不越界。
            pa_feat: dict = {}
            if df is not None and len(df) > 0:
                try:
                    from guanlan_v2.seats.price_action import compute_pa_features
                    pa_feat = compute_pa_features(df, c, name)
                except Exception:  # noqa: BLE001 — 几何失败不挡研判
                    pa_feat = {}
```

- [ ] **Step 3c: 构建两块 + 注入 usr_p** — 在 `rf_line, _rf_vint = _rf_vintage_line(...)`(L1449)之后、`sys_p = (...)`(L1451)之前插入:

```python
            # 价格行为两块(仅 pa 开):几何=确定性事实;方法论=推理框架(可编辑,空则默认模板)。
            pa_block_line = ""
            pa_method_line = ""
            if pa:
                from guanlan_v2.seats.price_action import render_pa_block, PA_METHOD_DEFAULT
                _pb = render_pa_block(pa_feat, unit)
                if _pb:
                    pa_block_line = f"【价量形态·确定性(PIT≤决策bar·{unit})】{_pb}\n"
                pa_method_line = ("【价格行为读法(本席方法论·推理框架·不替代证据·证据不足给观望)】"
                                  f"{pa_method or PA_METHOD_DEFAULT}\n")
```

  然后把 `usr_p`(L1462–1468)改为(在「量化因子」行后加 `pa_block_line`,在 `_ask` 前加 `pa_method_line`):

```python
            usr_p = (f"【标的】{name} {c} 截至 {asof}（{('30分钟K·日内' if freq=='30min' else '日线')}）\n"
                     f"【量化因子·PIT≤当日收盘】{fac_line}\n"
                     + pa_block_line +
                     f"【本席经验卡】{card_line}\n"
                     f"【相关研报/情绪】{res_line}\n"
                     + res_excerpt +
                     f"【本席配方因子·vintage OOS IC(as-of·真历史外样本)·供研判参考·不进信号】{rf_line}\n"
                     f"【市况】{regime or '—'}\n"
                     + pa_method_line + _ask)
```

- [ ] **Step 3d: 落盘 + 响应带 pa** — `_persist_decision("decide", {...})`(L1521–1539)的 rec 里、`"creed": creed,` 行后加:

```python
                "pa": pa, "pa_features": pa_feat,   # 价格行为:开关 + 确定性几何特征
```

  并在 `return JSONResponse({...})`(L1540–1551)里、`"hybrid_bias": _hyb_bias, "hybrid_direction": _hyb_dir,` 行后加:

```python
                "pa_features": pa_feat,   # 几何常显:无论 pa 开关都回前端供决策卡显示
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests/test_seats_decide_pa.py tests/test_seats_decide_intraday.py -q`
Expected: PASS(新测全绿 + 既有 intraday 零回归)

- [ ] **Step 5: 重启 9999 + Checkpoint**

杀 9999 监听 PID 等 ~10s 看门狗拉新代码(改 python 必做);然后:
Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`
Expected: 全绿无回归。

---

## Task 3: 前端 `luozi-data.jsx`(几何镜像 + scanSeat 升级 + strategySave)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`

> 说明:本任务无独立单测(前端 no-build);正确性靠 Task 6 浏览器 e2e + 与后端黄金用例肉眼对齐。`paFeatures` 必须与 `compute_pa_features` **同公式/枚举**(契约红线)。

- [ ] **Step 1: 加 LZ_PA_METHOD_DEFAULT + 几何镜像 + renderPaNote** — 在 `luozi-data.jsx` 顶部工具函数区(`evidenceFor` 之前、`sma/volMA/ret5` 附近)加:

```javascript
// ───────── 价格行为:方法论默认模板 + 价量几何特征(price_action.py 的 JS 镜像)─────────
// 契约:与 guanlan_v2/seats/price_action.py 同公式/枚举,改一边必同步另一边。
// PIT:只用 bars[0..idx](≤决策 bar);follow 仅 prev→current 向后看,不取未来。
window.LZ_PA_METHOD_DEFAULT =
  '价格行为读法(A股·做多为主):\n' +
  '1. 趋势 vs 区间:连续同向趋势棒(实体大、影线短、收于端部)= 趋势;互相重叠、影线长、收于中部 = 区间/震荡。趋势中顺势,区间中高抛低吸或观望。\n' +
  '2. 突破与回踩:放量突破前高(实体强、收于上沿)后,优先等第一次缩量回踩不破前高/均线企稳再进,胜率高于追突破当根。突破后迅速收回、留长上影 = 假突破,警惕。\n' +
  '3. 信号棒 + 跟随确认:孤立一根强棒不够,要看其后是否被同向棒跟随确认;无跟随、被反向吞没 = 信号失效。\n' +
  '4. 两腿回调:上升趋势中的回调常走两腿,第二腿缩量不破关键支撑后的转强棒,是较稳的右侧买点。\n' +
  '5. 位置感:同样形态在低位/超跌区比在高位/拥挤区可靠;高位放量滞涨、长上影、量价背离 = 退潮信号,降权或止盈。\n' +
  '6. A股 特有口径:T+1 当日买入次日才能卖,需为隔夜留余地;涨停封板≠可任意买卖(流动性骤降),涨停打开放量要警惕,跌停同理;ST 股 ±5% 幅度小、波动定义不同;不做空,只在做多方向取信号,看空时以「观望/减仓」表达。\n' +
  '几何特征是确定性事实(本席已附),本读法只是推理框架,不替代证据;证据不足时给「观望」。';

function _paRnd(x, p) { if (x == null || !isFinite(x)) return null; const m = Math.pow(10, p == null ? 3 : p); return Math.round(x * m) / m; }
function _paBoardLimit(code, name) {
  if (String(name || '').toUpperCase().replace(/\s/g, '').indexOf('ST') >= 0) return 0.05;
  const d = String(code || '').replace(/\D/g, '');
  if (d.slice(0, 3) === '688' || d.slice(0, 3) === '300') return 0.20;
  if ((d.slice(0, 1) === '8' || d.slice(0, 1) === '4') || String(code || '').toUpperCase().indexOf('BJ') === 0) return 0.30;
  return 0.10;
}
function _paBarType(o, h, l, c, ph, pl) {
  const rng = h - l;
  if (rng <= 0) return '平';
  if (ph != null && pl != null) {
    if (h <= ph && l >= pl) return '内含bar';
    if (h >= ph && l <= pl) return c >= o ? '外包阳' : '外包阴';
  }
  const body = Math.abs(c - o) / rng;
  if (body < 0.1) return '十字';
  if (body >= 0.5) return c > o ? '趋势阳' : '趋势阴';
  return c >= o ? '小阳' : '小阴';
}
function paFeatures(bars, idx, code, name) {
  if (!bars || idx == null || idx < 0 || idx >= bars.length) return {};
  const o = [], h = [], l = [], c = [], v = [];
  for (let k = 0; k <= idx; k++) { const b = bars[k]; o.push(+b.o); h.push(+b.h); l.push(+b.l); c.push(+b.c); v.push(+b.v); }
  const n = c.length, i = n - 1;
  const rng = h[i] - l[i];
  const prevClose = i >= 1 ? c[i - 1] : null;
  const ph = i >= 1 ? h[i - 1] : null, pl = i >= 1 ? l[i - 1] : null;
  const body = rng > 0 ? _paRnd(Math.abs(c[i] - o[i]) / rng) : null;
  const upper = rng > 0 ? _paRnd((h[i] - Math.max(o[i], c[i])) / rng) : null;
  const lower = rng > 0 ? _paRnd((Math.min(o[i], c[i]) - l[i]) / rng) : null;
  const closePos = rng > 0 ? _paRnd((c[i] - l[i]) / rng) : null;
  let rangeAtr = null;
  if (n >= 15) {
    let s = 0; for (let k = n - 14; k < n; k++) s += Math.max(h[k] - l[k], Math.abs(h[k] - c[k - 1]), Math.abs(l[k] - c[k - 1]));
    const atr = s / 14; rangeAtr = atr > 0 ? _paRnd(rng / atr) : null;
  }
  let ema20Rel = null;
  if (n >= 20) {
    const kf = 2 / 21; let ema = c[0];
    for (let k = 1; k < n; k++) ema = c[k] * kf + ema * (1 - kf);
    ema20Rel = ema !== 0 ? _paRnd((c[i] - ema) / ema) : null;
  }
  const barType = _paBarType(o[i], h[i], l[i], c[i], ph, pl);
  let breakout = null;
  if (i >= 5) {
    const ph5 = Math.max.apply(null, h.slice(i - 5, i)), pl5 = Math.min.apply(null, l.slice(i - 5, i));
    breakout = h[i] > ph5 ? '突破前5高' : (l[i] < pl5 ? '跌破前5低' : '区间内');
  }
  let insideStreak = 0;
  for (let k = i; k >= 1; k--) { if (h[k] <= h[k - 1] && l[k] >= l[k - 1]) insideStreak++; else break; }
  let volRatio = null;
  if (i >= 5) { const base = (v[i - 5] + v[i - 4] + v[i - 3] + v[i - 2] + v[i - 1]) / 5; volRatio = base > 0 ? _paRnd(v[i] / base, 2) : null; }
  let limit = null, gap = null;
  // NaN 守卫(与后端 price_action.py 同口径):prev_close/今收均有限才判,否则诚实 null
  if (prevClose != null && prevClose === prevClose && prevClose !== 0 && c[i] === c[i]) {
    const L = _paBoardLimit(code, name), pct = (c[i] - prevClose) / prevClose;
    limit = pct >= L - 0.003 ? '涨停' : pct >= 0.7 * L ? '接近涨停' : pct <= -(L - 0.003) ? '跌停' : pct <= -0.7 * L ? '接近跌停' : '正常';
    if (o[i] === o[i]) gap = o[i] > prevClose * 1.002 ? '高开' : o[i] < prevClose * 0.998 ? '低开' : '无';
  }
  let follow = null;
  if (i >= 1) {
    const pph = i >= 2 ? h[i - 2] : null, ppl = i >= 2 ? l[i - 2] : null;
    const pbt = _paBarType(o[i - 1], h[i - 1], l[i - 1], c[i - 1], pph, ppl);
    if (pbt === '趋势阳' || pbt === '外包阳') { if (c[i] > c[i - 1] && c[i] > o[i]) follow = '已确认(多)'; else if (c[i] < l[i - 1]) follow = '转弱'; }
    else if (pbt === '趋势阴' || pbt === '外包阴') { if (c[i] < c[i - 1]) follow = '已确认(空)'; else if (c[i] > h[i - 1]) follow = '转弱'; }
  }
  const recent = [1, 2, 3].map(function (back) {
    const k = i - back;
    if (k < 0) return null;
    return _paBarType(o[k], h[k], l[k], c[k], k >= 1 ? h[k - 1] : null, k >= 1 ? l[k - 1] : null);
  });
  return { date: (bars[idx] && bars[idx].date) || null, bar_type: barType, body: body, upper_wick: upper, lower_wick: lower, close_pos: closePos, range_atr: rangeAtr, ema20_rel: ema20Rel, breakout: breakout, inside_streak: insideStreak, vol_ratio: volRatio, limit: limit, gap: gap, follow: follow, recent: recent };
}
function renderPaNote(feat) {
  if (!feat || !feat.bar_type) return '';
  const f = function (x) { return x == null ? '—' : x; };
  const bits = [feat.bar_type, '实体' + f(feat.body), '收盘位' + f(feat.close_pos)];
  if (feat.breakout && feat.breakout !== '区间内') bits.push(feat.breakout);
  if (feat.vol_ratio != null) bits.push('量比' + feat.vol_ratio + '×');
  if (feat.limit && feat.limit !== '正常') bits.push(feat.limit);
  if (feat.gap && feat.gap !== '无') bits.push(feat.gap);
  if (feat.follow) bits.push(feat.follow);
  return bits.join('·');
}
window.lzPaFeatures = paFeatures;
window.lzRenderPaNote = renderPaNote;
```

- [ ] **Step 2: scanSeat 升级(几何硬过滤 + 置信 + 注释 + geo 字段)** — 找到 `function scanSeat(bars, strat)`,改签名为 `function scanSeat(bars, strat, meta)`,在函数体顶部加 `meta = meta || {};`;在 `for (let i = 6; i < n; i++) {` 循环体内、读完 ma5/ma20/vm/r5 之后加 `const g = paFeatures(bars, i, meta.code, meta.name);`;然后把三个模板分支替换为:

```javascript
    if (tmpl === 'momentum') {
      const cross = ma5 && ma20 && ma5 > ma20 && ma5p <= ma20p;
      const dead = ma5 && ma20 && ma5 < ma20 && ma5p >= ma20p;
      const geoOk = (g.bar_type === '趋势阳' || g.breakout === '突破前5高')
        && (g.close_pos == null || g.close_pos >= 0.55)
        && (g.body == null || g.body >= 0.45)
        && (g.vol_ratio == null || g.vol_ratio >= 1.1)
        && g.limit !== '涨停';
      if (!holding && cross && bars[i].c > ma20 && bars[i].v > vm * 1.05 && geoOk) {
        const bump = Math.min(0.1, Math.max(0, (g.close_pos || 0.5) - 0.5) + Math.max(0, (g.body || 0.5) - 0.5) + Math.max(0, (g.vol_ratio || 1) - 1));
        push(i, 'buy', 0.7 + Math.min(0.2, r5 * 2) + bump, 0.6, { note: 'MA5 上穿 MA20 · ' + renderPaNote(g) + ',顺势进场。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && (dead || exitHit(i))) {
        push(i, 'sell', 0.66, 0, { note: dead ? 'MA5 下破 MA20,动量转弱,撤。' : '触止损/止盈/到期,离场。', geo: g });
        holding = false;
      }
    } else if (tmpl === 'reversal') {
      const turn = bars[i].c > bars[i - 1].c && bars[i - 1].c <= bars[i - 2].c;
      const belowTrend = ma20 && bars[i].c < ma20 * 0.96;
      const revGeo = ((g.lower_wick != null && g.lower_wick >= 0.3) || (g.close_pos != null && g.close_pos >= 0.6) || (g.lower_wick == null && g.close_pos == null))
        && g.bar_type !== '趋势阴' && g.limit !== '跌停';
      if (!holding && r5 < -0.05 && belowTrend && bars[i].v < vm * 1.0 && turn && revGeo) {
        push(i, 'buy', 0.62 + Math.min(0.22, -r5), 0.5, { note: '五日超跌 ' + (r5 * 100).toFixed(1) + '% · ' + renderPaNote(g) + ',左侧企稳。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        const win = bars[i].c >= entryPrice;
        push(i, 'sell', 0.6, 0, { note: win ? '已达反弹目标/到期,落袋。' : '跌破止损/到期,纪律离场。', geo: g });
        holding = false;
      }
    } else if (tmpl === 'event') {
      if (!holding && bars[i].event && (g.gap === '高开' || g.bar_type === '趋势阳')) {
        push(i, 'buy', 0.82, 0.55, { note: '业绩超预期跳空 · ' + renderPaNote(g) + ',博 PEAD 漂移。', geo: g });
        holding = true; entryIdx = i; entryPrice = bars[i].c;
      } else if (holding && exitHit(i)) {
        push(i, 'sell', 0.6, 0, { note: (i - entryIdx) >= maxHold ? '漂移窗口结束,兑现。' : '止损/止盈离场。', geo: g });
        holding = false;
      }
    }
```

- [ ] **Step 3: scanSeat 调用方传 meta** — Grep `scanSeat(` 找所有调用点(主要在 `buildSymbolFromBars(meta, bars, strategies)` 内)。把每处 `scanSeat(bars, strat)` 改为 `scanSeat(bars, strat, meta)`(`meta` 在 buildSymbolFromBars 形参中已在作用域)。若某调用点无 meta 在作用域,传 `{}`(几何照算,仅涨跌停按主板默认板幅)。

- [ ] **Step 4: strategySave 持久化 pa/paMethod** — 找到 `function strategySave(o)` 的 `const obj = {...}`,在 `w:` 行后加:

```javascript
    // 价格行为:pa 开关(默认关)+ 可编辑方法论(空串=用默认模板)
    pa: o.pa === true,
    paMethod: (o.paMethod != null) ? String(o.paMethod) : '',
```

- [ ] **Step 5: Checkpoint** — bump ?v 在 Task 6 统一做。本步只做静态自检:`paFeatures` 与 `compute_pa_features` 字段名逐一对照(date/bar_type/body/upper_wick/lower_wick/close_pos/range_atr/ema20_rel/breakout/inside_streak/vol_ratio/limit/gap/follow/recent),确认返回键名完全一致。

---

## Task 4: 校场开关 + 可编辑方法论(`luozi-foundry.jsx`)

**Files:**
- Modify: `ui/seats/luozi-foundry.jsx`

- [ ] **Step 1: newDraft 默认 pa/paMethod** — 找到 `newDraft`(`setEditing({ name:'', template:'momentum', ... })`),在对象里加 `pa: false, paMethod: ''`。

- [ ] **Step 2: 编辑既有策略带出 pa/paMethod** — 找到打开编辑既有策略处(把 strategy 装进 editing 的地方)。若显式构造 editing,补 `pa: !!strategy.pa, paMethod: strategy.paMethod || ''`;若整体拷贝(`Object.assign({}, strategy)` 之类),确保不丢这两字段(必要时补默认)。

- [ ] **Step 3: 表单加开关 + textarea** — 在「信条(creed)」textarea 块之后、「配方」区之前,插入:

```jsx
        {/* 价格行为研判:开关 + 可编辑方法论(几何始终算/显;开关只控制是否注入 LLM 研判 prompt)*/}
        <div style={{ margin: '22px 0 8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', letterSpacing: '.16em' }}>价格行为</span>
            <span onClick={() => setEditing(s => ({ ...s, pa: !s.pa, paMethod: (!s.pa && !s.paMethod) ? (window.LZ_PA_METHOD_DEFAULT || '') : s.paMethod }))}
              className="serif"
              style={{ fontSize: 11.5, cursor: 'pointer', padding: '4px 12px', borderRadius: 8, transition: 'all .12s',
                border: '1px solid ' + (editing.pa ? 'var(--ink)' : 'var(--line)'),
                background: editing.pa ? 'var(--ink)' : 'var(--paper)', color: editing.pa ? 'var(--paper)' : 'var(--ink-2)' }}>
              {editing.pa ? '✓ 注入几何 + 方法论' : '关 · 点击开启'}
            </span>
            <span className="serif" style={{ fontSize: 10, color: 'var(--ink-3)' }}>几何特征始终计算并在决策卡显示;开关只控制是否注入 LLM 研判 prompt</span>
          </div>
          {editing.pa && (
            <textarea value={editing.paMethod} onChange={e => setEditing(s => ({ ...s, paMethod: e.target.value }))}
              placeholder={window.LZ_PA_METHOD_DEFAULT || ''} rows={7}
              style={{ width: '100%', marginTop: 8, fontFamily: 'var(--serif)', fontSize: 11.5, lineHeight: 1.65,
                color: 'var(--ink)', background: 'transparent', border: '1px solid var(--line)', borderRadius: 8,
                padding: '8px 10px', outline: 'none', resize: 'vertical', boxSizing: 'border-box' }} />
          )}
        </div>
```

- [ ] **Step 4: 保存传 pa/paMethod** — 找到钤印保存的 `window.lzStrategySave({ id: editing.id, name: editing.name, ... })`,在对象里加:

```jsx
          pa: editing.pa, paMethod: editing.paMethod,
```

- [ ] **Step 5: Checkpoint** — 静态自检:开关点击切换 editing.pa、textarea 受控、保存对象含 pa/paMethod(留待 Task 6 浏览器验证)。

---

## Task 5: DecisionCard 几何块 + runDecide 传 pa(`luozi-panels.jsx`)

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`

- [ ] **Step 1: runDecide 传 pa/pa_method** — 在 `runDecide`(~L1413)里,`window.lzSeatDecide({...})` 的 payload 中(`regime: regimeNow,` 附近)加:

```javascript
                pa: !!(s && s.pa),
                pa_method: (s && s.pa) ? (s.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : '',
```

  (`s` 已在 DecisionCard 顶部 `const s = lzSeatMeta(dec.seat)`。若 `lzSeatMeta` 不带 pa/paMethod,则在 runDecide 内先取 `const st = window.lzStrategyGet ? window.lzStrategyGet(dec.seat) : s;` 并用 `st.pa`/`st.paMethod`。)

- [ ] **Step 2: DecisionCard 加「价量形态 · 确定性」块** — 在「触发 · 量化因子」`<Field>` 块之后、「命中 · 经验卡」`<Field label="命中 · 经验卡">`(~L1364)之前,插入:

```jsx
        {/* 价量形态 · 确定性:scanSeat 真算(dec.geo)/ decide 后端真算(pa_features);非 LLM,几何常显 */}
        {(() => {
          const g = (decide && decide.pa_features) || dec.geo;
          if (!g || !g.bar_type) return null;
          const fv = (x) => (x == null ? '—' : x);
          const cells = [
            ['K线型态', g.bar_type], ['实体比', fv(g.body)], ['收盘位', fv(g.close_pos)],
            ['上影/下影', fv(g.upper_wick) + ' / ' + fv(g.lower_wick)], ['振幅÷ATR', fv(g.range_atr)],
            ['距EMA20', fv(g.ema20_rel)], ['突破', fv(g.breakout)],
            ['量比', g.vol_ratio == null ? '—' : g.vol_ratio + '×'], ['涨跌停', fv(g.limit)], ['跳空', fv(g.gap)],
          ];
          if (g.inside_streak) cells.push(['连续内含', g.inside_streak + ' 根']);
          if (g.follow) cells.push(['跟随', g.follow]);
          return (
            <Field label="价量形态 · 确定性">
              <span className="mono" title="价量几何特征由价量数据确定性算出(非 LLM);PIT≤决策bar" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)', marginBottom: 6, display: 'inline-block' }}>确定性 · 非 LLM</span>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px' }}>
                {cells.map(([k, val]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{k}</span>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-1)' }}>{val}</span>
                  </div>
                ))}
              </div>
              {(g.recent || []).filter(Boolean).length > 0 && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 5 }}>近3根:{(g.recent || []).map(r => r || '—').join(' / ')}</div>}
              {decide && decide.pa_features && s && s.pa && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>已注入本席方法论(可在校场编辑)</div>}
            </Field>
          );
        })()}
```

- [ ] **Step 3: Checkpoint** — 静态自检:几何块对启发式(dec.geo)与真 agent(decide.pa_features)两源都渲染,None→「—」,徽章「确定性 · 非 LLM」(留待 Task 6 浏览器验证)。

---

## Task 6: 收口(bump ?v + pytest + 重启 + 浏览器 e2e + 文档 + memory)

**Files:**
- Modify: `ui/seats/观澜 · 落子.html`、`ui/seats/README.md`、memory

- [ ] **Step 1: bump ?v(用 Edit,非 sed)** — `ui/seats/观澜 · 落子.html`:
  - `luozi-data.jsx?v=20260614f` → `?v=20260614g`
  - `luozi-panels.jsx?v=20260614f` → `?v=20260614g`
  - `luozi-foundry.jsx?v=20260614h` → `?v=20260614i`

- [ ] **Step 2: 全量 pytest**

Run: `G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`
Expected: 全绿(原 290 + 本次 price_action + decide_pa,无回归)。

- [ ] **Step 3: 确认 9999 已重启**(Task 2 已重启;如期间又改后端则再杀监听 PID 等 ~10s)。

- [ ] **Step 4: 浏览器 e2e**(经 9999 同源真后端;Playwright/Chrome MCP 自启浏览器,勿占 9999):
  - 打开 `观澜 · 落子` 页面,确认 0 解析错(除 favicon/babel 警告)。
  - **校场**:新建策略 → 点「价格行为」开启 → 方法论 textarea 露出且预填默认(含「T+1」)→ 改写一句 → 钤印保存 → 重开该策略,pa 开 + 文本持久。
  - **决策卡(启发式)**:点 K 线买点,展开「价量形态 · 确定性」块,有「确定性 · 非 LLM」徽章 + 真几何(型态/实体/收盘位/量比…);pa 关时该块仍显(几何常显)。
  - **真 LLM(让 agent 真跑,pa 开策略)**:对某买点跑真研判 → 卡片几何块显示后端 pa_features + 「已注入本席方法论」小字;查 `var/seats_decisions.jsonl` 末条含 `"pa": true` 且 `"pa_features": {...}`。
  - **pa 关**:对 pa 关策略真研判 → `seats_decisions.jsonl` 末条 `"pa": false`,研判行为与现状一致。
  - **清理自注入的研判 run 行**(验证后删 jsonl 里本次测试追加的记录,避免污染台账/校准)。

- [ ] **Step 5: 文档** — `ui/seats/README.md` 追加一条:价格行为(价量几何 + 方法论 prompt)= `price_action.py` 纯函数 + decide 接线 + `paFeatures` JS 镜像 + 校场开关/可编辑方法论 + DecisionCard 几何块;默认关零回归;`?v` 新版本。

- [ ] **Step 6: memory** — 新建 memory 记一条:落子价格行为层(借鉴 PA_Agent clean-room)= 确定性几何特征 + 每策略可编辑方法论·校场可开关·默认关;契约红线(Python 权威 ↔ JS 镜像同公式)、PIT(follow 向后看)、几何常显·开关只管注入;链 `[[luozi-fake-audit]]`。MEMORY.md 加指针。

- [ ] **Step 7: 最终评审** — 派 react-reviewer(前端 3 个 jsx)+ python-reviewer(price_action.py + decide 接线)各一轮,修 Important 级问题后复跑 pytest。

---

## Self-Review(对照 spec)

- **§3 特征集** → Task 1 全覆盖(13 特征 + recent + ATR/EMA 窗口 + 涨跌停按板 + follow PIT)。
- **§4 纯函数** → Task 1(compute/render/DEFAULT)。
- **§5 decide 接线**(payload/算/注入/响应/落盘)→ Task 2 全覆盖 + 测试三态(开/默认/关)。
- **§6 scanSeat + 镜像 + strategySave** → Task 3。
- **§7 方法论默认模板** → Task 1(Python)+ Task 3(JS),两处文本一致。
- **§8 校场开关 + textarea** → Task 4。
- **§9 runDecide + DecisionCard 几何块** → Task 5。
- **§11 契约/PIT/几何常显** → Task 1(PIT/降级)、Task 2(几何常显:pa 关响应仍带)、Task 3 Step5(字段对照)、Task 6 Step4(e2e 双源)。
- **§12 测试** → Task 1 纯函数 TDD + Task 2 接线 TDD + Task 6 e2e。
- **§13 文件清单** → 与本计划 File Structure 一致。
- **类型一致性**:`pa`(bool)/payload 键 `pa_method` ↔ 前端 strategy 字段 `paMethod`(已在 Task 3/5 显式映射)、`pa_features`(响应/落盘)、`geo`(scanSeat 决策字段)、15 个特征键两端一致。
- **无占位**:每步含完整代码 / 精确插入点 / 真命令 + 预期。
- **无 git**:所有「提交」替换为「跑 pytest」/「重启 9999」/「bump ?v」。
```
