# -*- coding: utf-8 -*-
"""统一快讯门户 —— 观澜侧东财 7×24 快讯唯一入口(T2 收敛,数据中台件④)。

背景:东财快讯历史上三路并拉——① screen/news(选股页情绪链)② seats/news_marks
(盘中新闻泳道)③ 统一客户端 eastmoney_global_news(getFastNewsList)。其中 ①② 实为
**同一函数**(engine fork `news_pulse.fetch_kuaixun`,opencli 采集器),③ 是独立实现且
真机踩两坑:np-weblist.eastmoney.com **TCP 不可达**(直连即超时,非代理问题)+ 每条
`stock_codes` **恒空**(选股页 by_code/sentiment 契约全靠 per-flash codes,无则塌成
『全票 uncovered』)。故收敛口径:唯一可达且带 codes 的 opencli 为规范源,三路全改走本门户。

返回规范行 `{time(16位), title, summary, codes(qlib列表)}`,与旧 `news_pulse.fetch_kuaixun`
逐字段一致(选股页 C 节 / rescore / 情绪 store / news_marks 契约零改)。

诚实约定:源抛错(网络/子进程失败)向上传播,让上层区分『拉取失败』vs『返回空』;
源空返 → `[]`(上层走『快讯源返回空』分支),绝不编造。

注:研报 tier1 子 agent(`agent/tier1/news_sentiment.py`)在引擎子进程内跑、导不到
`guanlan_v2.*`,仍直调 `news_pulse.fetch_kuaixun`——与本门户共用同一 opencli 采集器
(含 @rate_limited 15s 缓存),已在引擎层收敛,是正确边界而非缺口。
"""
from __future__ import annotations

from typing import Any, Dict, List


def _engine_fetch(limit: int) -> List[Dict[str, Any]]:
    """背靠引擎 fork opencli `fetch_kuaixun`(唯一带 per-flash codes 且真机可达)。

    late import:引擎仅在服务器运行时置于 sys.path(server 前插 engine/);
    单测 monkeypatch 本函数即可脱引擎,不触 opencli 子进程。抛错不吞,交由 caller。
    """
    from financial_analyst.data.news_pulse import fetch_kuaixun as _f
    return _f(limit=limit)


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """收敛成规范 4 键,防引擎 shape 漂移:time 截 16、title/summary strip、codes 兜成 str list。"""
    codes = row.get("codes")
    if not isinstance(codes, list):
        codes = [codes] if codes else []
    return {
        "time": str(row.get("time") or "")[:16],
        "title": str(row.get("title") or "").strip(),
        "summary": str(row.get("summary") or "").strip(),
        "codes": [str(c) for c in codes if str(c)],
    }


def fetch_kuaixun(limit: int = 200) -> List[Dict[str, Any]]:
    """东财 7×24 快讯规范行 `[{time, title, summary, codes}]`(最新在前)。

    观澜三路(选股页情绪链 / news_marks 泳道 / ww_live_text global_news)唯一现拉门户。
    源不可用/抛错 → 向上传播(上层据此走『拉取失败』);空 → `[]`(『返回空』),绝不编造。
    """
    rows = _engine_fetch(limit) or []
    return [_normalize_row(r) for r in rows if isinstance(r, dict)]


__all__ = ["fetch_kuaixun"]
