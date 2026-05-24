"""Overseas-market-scanner — 拉国际指数 + 算 risk tone (无 LLM).

v1 覆盖 6 个核心国际指数 (tencent qt.gtimg.cn 国内镜像):
  美股: DJI / IXIC / INX (S&P500) / VIX
  港股: HSI / HSTECH

为啥不用 yfinance: yfinance 1.4 用 curl_cffi 跟 Clash MITM 冲突 TLS 失败.
tencent 是国内 endpoint, 跟 A 股 quote 同路径走 net.py.domestic, 不撞 Clash.

风险偏好判读 (risk_tone) 基于多个国际指数的方向 + VIX 水平:
  risk_on   — 美股 + 港股都涨 + VIX < 18 = 全球情绪好
  risk_off  — 美股 + 港股都跌 / VIX > 22  = 避险
  mixed     — 美股港股反向 / VIX 18-22   = 不明朗

A 股早盘开盘往往 follow 隔夜美股 + 港股早开. risk_off 状态下大盘大概率
低开 + 防御性板块占优.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent


class GlobalIndexSnapshot(BaseModel):
    code: str
    name: str
    price: Optional[float]
    change: Optional[float]
    changePercent: Optional[float]   # %
    high: Optional[float] = None
    low: Optional[float] = None
    prevClose: Optional[float] = None


class OverseasMarketOutput(BaseModel):
    as_of: str
    us_overnight: Dict[str, GlobalIndexSnapshot] = Field(default_factory=dict)
    hk_market: Dict[str, GlobalIndexSnapshot] = Field(default_factory=dict)
    risk_tone: str = "mixed"      # risk_on / risk_off / mixed
    risk_tone_detail: str = ""    # 1-句话解释
    vix_level: Optional[float] = None
    n_indices: int = 0


def _judge_risk_tone(us_snap: Dict[str, GlobalIndexSnapshot],
                      hk_snap: Dict[str, GlobalIndexSnapshot]) -> tuple[str, str]:
    """根据 US + HK 指数方向 + VIX 水平判读 risk tone.

    返回 (tone, detail). tone ∈ {risk_on, risk_off, mixed}.
    """
    def _pct(d: Dict[str, GlobalIndexSnapshot], code: str) -> Optional[float]:
        s = d.get(code)
        return s.changePercent if s else None

    spx = _pct(us_snap, "usINX")
    ndx = _pct(us_snap, "usIXIC")
    dji = _pct(us_snap, "usDJI")
    vix_snap = us_snap.get("usVIX")
    vix = vix_snap.price if vix_snap else None
    hsi = _pct(hk_snap, "hkHSI")
    hstech = _pct(hk_snap, "hkHSTECH")

    us_avg = [p for p in (spx, ndx, dji) if p is not None]
    us_avg_pct = sum(us_avg) / len(us_avg) if us_avg else None
    hk_avg = [p for p in (hsi, hstech) if p is not None]
    hk_avg_pct = sum(hk_avg) / len(hk_avg) if hk_avg else None

    bits: List[str] = []
    if us_avg_pct is not None:
        bits.append(f"美股隔夜 {us_avg_pct:+.2f}%")
    if hk_avg_pct is not None:
        bits.append(f"港股 {hk_avg_pct:+.2f}%")
    if vix is not None:
        bits.append(f"VIX={vix:.1f}")

    # Decide tone
    us_up = us_avg_pct is not None and us_avg_pct > 0.3
    us_down = us_avg_pct is not None and us_avg_pct < -0.3
    hk_up = hk_avg_pct is not None and hk_avg_pct > 0.3
    hk_down = hk_avg_pct is not None and hk_avg_pct < -0.3
    vix_high = vix is not None and vix > 22
    vix_low = vix is not None and vix < 18

    if (us_up and hk_up) or (us_up and vix_low):
        tone = "risk_on"
    elif (us_down and hk_down) or vix_high or (us_down and hk_up is False):
        tone = "risk_off"
    else:
        tone = "mixed"

    detail = (
        ("全球情绪 risk-on, " if tone == "risk_on" else
         "全球情绪 risk-off, " if tone == "risk_off" else
         "信号分化, ") + " · ".join(bits) if bits else tone
    )
    return tone, detail


class OverseasMarketScanner(SubAgent[OverseasMarketOutput]):
    """Pull 6 core overseas indices via tencent_global + judge risk tone.

    No LLM call. Used by morning-brief swarm (隔夜美股 + 港股早盘) and
    overseas-radar swarm (full international panorama).
    """

    NAME = "overseas-market-scanner"
    OUTPUT_SCHEMA = OverseasMarketOutput

    def __init__(self, memory_root, collector=None):
        super().__init__(memory_root=memory_root)
        self._collector = collector

    def _get_collector(self):
        if self._collector is not None:
            return self._collector
        from financial_analyst.data.collectors.tencent_global import TencentGlobalCollector
        return TencentGlobalCollector()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        asof = inputs.get("asof_date") or pd.Timestamp.today().strftime("%Y-%m-%d")
        collector = self._get_collector()
        raw = collector.fetch_default()

        us_codes = {"usDJI", "usIXIC", "usINX", "usVIX"}
        hk_codes = {"hkHSI", "hkHSTECH"}

        def _to_snap(code: str) -> Optional[GlobalIndexSnapshot]:
            d = raw.get(code)
            if not d:
                return None
            return GlobalIndexSnapshot(
                code=code,
                name=d.get("name") or code,
                price=d.get("price"),
                change=d.get("change"),
                changePercent=d.get("changePercent"),
                high=d.get("high"),
                low=d.get("low"),
                prevClose=d.get("prevClose"),
            )

        us_snap: Dict[str, GlobalIndexSnapshot] = {}
        hk_snap: Dict[str, GlobalIndexSnapshot] = {}
        for code in us_codes:
            s = _to_snap(code)
            if s:
                us_snap[code] = s
        for code in hk_codes:
            s = _to_snap(code)
            if s:
                hk_snap[code] = s

        vix_snap = us_snap.get("usVIX")
        vix_level = vix_snap.price if vix_snap else None
        tone, detail = _judge_risk_tone(us_snap, hk_snap)

        out = OverseasMarketOutput(
            as_of=asof,
            us_overnight=us_snap,
            hk_market=hk_snap,
            risk_tone=tone,
            risk_tone_detail=detail,
            vix_level=vix_level,
            n_indices=len(us_snap) + len(hk_snap),
        )
        return out.model_dump()
