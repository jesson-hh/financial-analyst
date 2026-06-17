"""因子语义契约层(0612演习修复#1)。

唯一一份「字段 → 中文名/方向/口径/渲染句式」字典。所有把因子值喂给 LLM 的
prompt 必须经 render_factor()/render_factors() 把裸值渲染成带方向语义的中文,
LLM 永远不直接解读裸字段名(演习事故:rev_20=0.217 被读成"20日+20%"实为-21.7%)。

口径来源 engine/financial_analyst/factors/core.py:
  rev_w  = -pct_change(close, w)   # 正值 = 过去 w 日下跌(超跌)
  mom_w  = +pct_change(close, w)
  turnover_w = vol[-1]/avg(vol,w)  # ⚠ 字段名叫 turnover,口径实为「量比」(倍数)
  ma_diff_w  = close/MA(w)-1;  rsi_14 ∈ [0,100]
另 seats/quote 的 vol_ratio = 当日量/10日均量(腾讯实时,与 turnover_20 不同窗口)。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def _pct(v: float) -> str:
    return f"{abs(v) * 100:.1f}%"


def _fmt(v: float, p: int) -> str:
    return ("%." + str(p) + "f") % v


def _rev_20(v: float, unit: str = "日") -> str:
    chg = -v                       # 还原真实 20 日涨跌幅
    side = "下跌" if chg < 0 else "上涨"
    if v >= 0.10:
        tag = ",超跌状态"
    elif v <= -0.10:
        tag = ",强势上行"
    else:
        tag = ""
    return f"过去20{unit}{side}{_pct(chg)}{tag}"


def _mom_60(v: float, unit: str = "日") -> str:
    side = "下跌" if v < 0 else "上涨"
    return f"过去60{unit}累计{side}{_pct(v)}"


def _rsi_14(v: float, unit: str = "日") -> str:  # noqa: ARG001 — 无窗口,unit 仅统一签名
    if v < 30:
        return "超卖区,<30"
    if v > 70:
        return "超买区,>70"
    return "中性区,30-70"


def _ma_diff_20(v: float, unit: str = "日") -> str:
    side = "低于" if v < 0 else "高于"
    return f"收盘{side}20{unit}均线{_pct(v)}"


def _vol_tag(v: float) -> str:
    if v >= 1.5:
        return ",明显放量"
    if v <= 0.8:
        return ",缩量"
    return ",量能平稳"


def _turnover_20(v: float, unit: str = "日") -> str:
    return f"当日量为20{unit}均量的{_fmt(v, 2)}倍{_vol_tag(v)}"


def _vol_ratio(v: float, unit: str = "日") -> str:  # noqa: ARG001 — 自带10日窗,unit 仅统一签名
    return f"10日窗{_vol_tag(v)}"


def _turnover_rate(v: float, unit: str = "日") -> str:  # noqa: ARG001 — 句式与值/窗口无关
    return "成交量/流通股本"


FACTOR_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "rev_20":        {"cn": "反转20",     "prec": 3, "explain": _rev_20},
    "mom_60":        {"cn": "动量60",     "prec": 3, "explain": _mom_60},
    "rsi_14":        {"cn": "RSI14",      "prec": 1, "explain": _rsi_14},
    "ma_diff_20":    {"cn": "均线乖离20", "prec": 3, "explain": _ma_diff_20},
    "turnover_20":   {"cn": "20日量比",   "prec": 2, "explain": _turnover_20, "unit": "倍"},
    "vol_ratio":     {"cn": "实时量比",   "prec": 2, "explain": _vol_ratio},
    "turnover_rate": {"cn": "换手率",     "prec": 2, "explain": _turnover_rate, "unit": "%"},
}


def render_factor(field: str, value: Any, unit: str = "日") -> str:
    """单字段渲染:「中文名=值(方向语义句)」;未知字段诚实回落「field=value」;None/NaN → —。

    unit 是「回看窗口」单位(缺省「日」),分钟级传「根30分钟bar」等防 LLM 把 N 根 bar 误读为 N 日;
    与值后缀 meta['unit'](倍/%)是两回事,后者不受影响。
    """
    meta = FACTOR_SEMANTICS.get(field)
    v = _num(value)
    if meta is None:
        if v is None:
            return f"{field}={value}"
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return f"{field}={s or '0'}"
    if v is None:
        return f"{meta['cn']}=—"
    v = v + 0.0  # IEEE -0.0+0.0=+0.0,防 RSI14=-0.0 自相矛盾显示
    val = _fmt(v, meta["prec"]) + meta.get("unit", "")
    return f"{meta['cn']}={val}({meta['explain'](v, unit)})"


def render_factors(fac: Dict[str, Any], fields: Iterable[str] | None = None,
                   unit: str = "日") -> str:
    """多字段渲染拼行(分号分隔)。fields 给定则按其顺序(缺失值渲染为 —),否则按 fac 自身顺序。

    unit 透传给每个 explain 的「回看窗口」单位(缺省「日」=既有行为零变化)。
    """
    keys = list(fields) if fields is not None else list(fac)
    return "; ".join(render_factor(k, fac.get(k), unit) for k in keys)
