"""断言质检(0612演习修复#2)。

对 LLM 研判产出做确定性核查(advisory,不阻断):
① 方向矛盾——文本断言与喂入因子真值方向相反(演习事故:rev_20=0.217 实为跌21.7%,
   LLM 说成"20日+20%");② 百分数出处——文本里的 X% 必须能在喂入证据(source)或
   因子真值里找到(±0.55pp 容差,容忍"21.7%→约22%"的合理改写,抓凭空数字)。
返回 flags 列表(空=干净);任何输入异常都吞掉返回 [](质检绝不挡研判)。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PCT_TOL = 0.55     # 百分数出处容差(百分点):容忍取整改写,抓 ≥0.6pp 的凭空数字
_DIR_DEAD = 0.02    # 方向断言死区:|涨跌幅|<2% 不判方向矛盾


def _v(fac: Dict[str, Any], k: str):
    try:
        x = float(fac.get(k))
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _pcts(text: str) -> List[float]:
    return [float(m.group(1)) for m in _PCT.finditer(text or "")]


def unsourced_percents(text: str, source: str) -> List[float]:
    """text 中无法在 source 找到出处的百分数(±0.55pp)。供经验卡 insight 质检复用。"""
    legit = _pcts(source)
    return [x for x in _pcts(text) if not any(abs(x - p) <= _PCT_TOL for p in legit)]


def audit_claims(claims: str, fac: Dict[str, Any], source: str = "") -> List[str]:
    """研判文本 vs 喂入因子真值+证据源。返回违规描述列表(advisory)。"""
    try:
        flags: List[str] = []
        t = claims or ""
        rev, mom = _v(fac, "rev_20"), _v(fac, "mom_60")
        rsi, mad, t20 = _v(fac, "rsi_14"), _v(fac, "ma_diff_20"), _v(fac, "turnover_20")

        # ① 方向矛盾(模式都限定在量名近旁,降误报)
        if rev is not None and rev >= _DIR_DEAD and re.search(r"20日(?!高点|新高|低点|新低|均线|线)[^。;,\n]{0,8}(上涨|涨幅|\+\d)", t):
            flags.append(f"方向矛盾:近20日实际下跌{rev * 100:.1f}%,文中称20日上涨")
        if rev is not None and rev <= -_DIR_DEAD and re.search(r"20日(?!高点|新高|低点|新低|均线|线)[^。;,\n]{0,8}(下跌|跌幅)", t):
            flags.append(f"方向矛盾:近20日实际上涨{-rev * 100:.1f}%,文中称20日下跌")
        if mom is not None and mom >= _DIR_DEAD and re.search(r"60日(?!高点|新高|低点|新低|均线|线)[^。;,\n]{0,8}(下跌|跌幅)", t):
            flags.append(f"方向矛盾:近60日实际上涨{mom * 100:.1f}%,文中称60日下跌")
        if mom is not None and mom <= -_DIR_DEAD and re.search(r"60日(?!高点|新高|低点|新低|均线|线)[^。;,\n]{0,8}(上涨|涨幅)", t):
            flags.append(f"方向矛盾:近60日实际下跌{-mom * 100:.1f}%,文中称60日上涨")
        if rsi is not None and rsi < 30 and "超买" in t:
            flags.append(f"方向矛盾:RSI14={rsi:.1f}处于超卖区,文中称超买")
        if rsi is not None and rsi > 70 and "超卖" in t:
            flags.append(f"方向矛盾:RSI14={rsi:.1f}处于超买区,文中称超卖")
        if mad is not None and mad <= -_DIR_DEAD and re.search(r"(站上|高于)20日均线", t):
            flags.append(f"方向矛盾:收盘低于20日均线{-mad * 100:.1f}%,文中称站上均线")
        if mad is not None and mad >= _DIR_DEAD and re.search(r"(跌破|低于)20日均线", t):
            flags.append(f"方向矛盾:收盘高于20日均线{mad * 100:.1f}%,文中称跌破均线")
        if t20 is not None and t20 <= 0.8 and re.search(r"20日量比[^。;,\n]{0,10}放量", t):
            flags.append(f"方向矛盾:20日量比{t20:.2f}缩量,文中称放量")
        if t20 is not None and t20 >= 1.5 and re.search(r"20日量比[^。;,\n]{0,10}缩量", t):
            flags.append(f"方向矛盾:20日量比{t20:.2f}放量,文中称缩量")

        # ② 百分数出处:合法源 = source 文本里的数字 + 因子真值换算的百分数
        legit = _pcts(source)
        for k in ("rev_20", "mom_60", "ma_diff_20"):
            x = _v(fac, k)
            if x is not None:
                legit.append(abs(x) * 100)
        for x in _pcts(t):
            if not any(abs(x - p) <= _PCT_TOL for p in legit):
                flags.append(f"数字{x:g}%在喂入证据中无出处")
        return flags
    except Exception:  # noqa: BLE001 — 质检自身故障绝不挡研判
        return []
