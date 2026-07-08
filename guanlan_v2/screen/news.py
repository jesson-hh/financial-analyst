# -*- coding: utf-8 -*-
"""C:真消息面 —— 薄适配器,委托引擎共享核心 news_pulse。

选股页 C 节返回契约不变:ok/source/as_of/market/by_code/covered/uncovered/model/market_read/sentiment/note。

诚实现状(2026-06 实测):引擎 NewsDB 为**空**(无缓存新闻),但东方财富快讯可**实时**取到真数据。
``fetch()`` 只读外部源、**不写任何引擎数据**(写库/文件的是 ``collect()``),故红线干净。

LLM(deepseek,经 ``llm._call_llm_json``)注入 news_pulse;失败仍返回**真快讯原文**(原始数据是真的)。
"""
from __future__ import annotations

# ── 向后兼容 re-export(tests/test_screen_news.py 直接 import 这些符号)──────────
# news_pulse 是引擎 fork 侧模块;服务器把 engine/ 放在 sys.path 最前,运行时解析正确。
from financial_analyst.data.news_pulse import (  # noqa: F401
    em_to_qlib,
    build_news_prompt,
    NEWS_SYSTEM as _NEWS_SYSTEM,   # 旧名 _NEWS_SYSTEM → 透传给既有测试
)

from typing import Any, Dict, List


async def news_sentiment(codes: List[str], *, limit: int = 200,
                         timeout: float = 60.0) -> Dict[str, Any]:
    """真消息面:实时快讯 + LLM 情绪。委托引擎共享核心 news_pulse(单一事实来源)。
    保持既有返回字段不变(选股页 C 节在用)。"""
    from financial_analyst.data import news_pulse
    from guanlan_v2.datafeed import kuaixun as _kuaixun
    from guanlan_v2.screen.llm import _call_llm_json

    try:
        market = _kuaixun.fetch_kuaixun(limit=limit)   # T2 收敛:唯一快讯门户(opencli,带 codes)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"快讯拉取失败:{type(exc).__name__}: {str(exc)[:200]}"}
    if not market:
        return {"ok": False, "reason": "快讯源返回空(可能限频/网络)"}

    codeset = [str(c) for c in (codes or [])]
    by_code = {c: [it for it in market if c in it.get("codes", [])][:4] for c in codeset}
    by_code = {c: v for c, v in by_code.items() if v}

    async def _llm(system: str, user: str) -> Dict[str, Any]:
        return await _call_llm_json(system, user, timeout=timeout, temperature=0.2)

    r = await news_pulse.judge_sentiment(market[:15], by_code, None, llm_json_call=_llm)
    return {
        "ok": True, "source": r["source"], "as_of": r["as_of"],
        "market": [{"time": it.get("time"), "title": it.get("title")} for it in market[:8]],
        "by_code": {c: [{"time": x.get("time"), "title": x.get("title")} for x in v]
                    for c, v in by_code.items()},
        "covered": r["covered"],
        "uncovered": [c for c in codeset if c not in by_code],
        "model": r["model"], "market_read": r["market_read"],
        "market_tilt": r.get("market_tilt"),
        "sentiment": r["sentiment"], "note": r["note"],
    }
