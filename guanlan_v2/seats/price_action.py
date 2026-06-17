"""落子 · 价量几何特征(clean-room,A股 适配)。

借鉴 PA_Agent 思路,公式为公知技术分析数学,独立实现;特征选择 / A股 适配 / 命名为本仓自有。
纯函数、零 I/O:只吃 OHLC DataFrame(已 PIT≤asof、时间升序),供 decide LLM 与前端镜像。
**契约**:前端 ui/seats/luozi-data.jsx `paFeatures()` 是本函数的 JS 镜像 —— 改一边必同步另一边。
PIT 红线:只用 ≤最新根(决策 bar)的数据;follow 仅向后看(prev→current),不取未来 bar。
"""
from __future__ import annotations

import math

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
        v = float(x)
        return None if math.isnan(v) or math.isinf(v) else round(v, p)
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
        # span-20 EMA:seed=首根、迭代全历史(== pandas ewm(span=20, adjust=False));JS 镜像须同算法
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
    # 仅当 prev_close 与今收均为有限值才判涨跌停/跳空(NaN→诚实 None,不冒充「正常」/「无」)
    if (prev_close is not None and prev_close == prev_close and prev_close != 0
            and c[i] == c[i]):
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
        if o[i] == o[i]:
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
