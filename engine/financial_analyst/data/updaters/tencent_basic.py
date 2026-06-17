"""腾讯实时接口拉 PE/PB/MV/换手率 当日快照 + 写 daily_basic Qlib bin.

替代 Tushare ``daily_basic`` 的**当日**截面数据. 腾讯一次拉 50 只 ~156ms, 0 token,
0 注册. **历史时间序列腾讯不给** — 那部分走 HuggingFace 历史包.

字段映射 (Tencent → Qlib bin)::

    tencent_quote.pe            → pe_ttm
    tencent_quote.pb            → pb
    tencent_quote.total_mv (亿)  → total_mv (万元, ×10000)
    tencent_quote.circ_mv (亿)   → circ_mv (万元, ×10000)
    tencent_quote.turnover_rate → turnover_rate (%)
    (无 ps_ttm)                  → 不写 (保留 NaN)
    (无 dv_ttm)                  → 不写 (保留 NaN)

**单位约定**: Tushare daily_basic 用 "万元", 腾讯用 "亿". 1 亿 = 10000 万元.
Tushare turnover_rate 是百分比 (例如 1.5 表示 1.5%), 腾讯一致.

API::

    >>> update_daily_basic_today(provider_uri, codes=["SH600519", "SZ300750"])
    {'total': 2, 'ok': 2, 'missing_pe': 0, 'missing_pb': 0, ...}
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Iterable, List, Optional

from financial_analyst.data.bin_writer import (
    append_calendar, build_calendar_index, load_calendar,
    safe_merge_write, update_instrument_range,
)
from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

log = logging.getLogger(__name__)


# Tencent 字段名 → (Qlib bin field, scale_factor)
_FIELD_MAP = {
    "pe":            ("pe_ttm",         1.0),       # PE 直接用
    "pb":            ("pb",             1.0),
    "total_mv":      ("total_mv",       10000.0),    # 亿 → 万元
    "circ_mv":       ("circ_mv",        10000.0),    # 亿 → 万元
    "turnover_rate": ("turnover_rate",  1.0),        # %
}

# Tushare daily_basic 全字段, 腾讯缺的两个写 NaN (供下游 isna 检查)
_TUSHARE_BASIC_FIELDS = [
    "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "total_mv", "circ_mv", "turnover_rate",
]


def _today_str() -> str:
    """今日交易日 (本地). 周末 fa data update 跑也会去交易所拉昨日, 不在这里管."""
    return _date.today().isoformat()


def _most_recent_weekday(d: str) -> str:
    """如果 d 是周末, 回退到最近一个周一-周五. 用于 fa data update 周末跑.

    A 股节假日不在此处理 (例如春节连休), 走 append_calendar 会拒绝写入,
    但本函数能 cover 80% 周末跑研究的场景.
    """
    from datetime import date as _date2, timedelta
    y, m, dd = map(int, d[:10].split("-"))
    dt = _date2(y, m, dd)
    while dt.weekday() >= 5:   # 5=Sat, 6=Sun
        dt -= timedelta(days=1)
    return dt.isoformat()


def update_daily_basic_today(
    provider_uri: str,
    codes: Iterable[str],
    trade_date: Optional[str] = None,
    chunk_size: int = 80,
) -> dict:
    """拉当日 PE/PB/MV 等估值快照, 写 daily_basic bin.

    Args:
        provider_uri: Qlib 目录
        codes: 股票代码列表
        trade_date: ``YYYY-MM-DD``, 默认今日. 写入这个日期的 bin 位置.
        chunk_size: 一次给腾讯多少只 (单 URL 太长会被截断, 默认 80)

    Returns:
        统计 dict {total, ok, missing_pe, missing_pb, missing_mv, errors}
    """
    raw_date = trade_date or _today_str()
    # 周末自动回退到周五 (节假日 fallback 不完美, 接 calendar 之后再校验)
    trade_date = _most_recent_weekday(raw_date)
    if trade_date != raw_date:
        log.info("update_daily_basic: %s 是周末, 回退到 %s", raw_date, trade_date)

    codes_list = list(codes)

    # 1. 把交易日加进日历 (允许已存在, append_calendar 自动去重)
    n_added = append_calendar([trade_date], provider_uri, freq="day")
    if n_added > 0:
        log.info("update_daily_basic: added calendar date %s", trade_date)

    calendar = load_calendar(provider_uri, freq="day")
    cal_index = build_calendar_index(calendar)
    if trade_date not in cal_index:
        # 走到这里通常是节假日 (例如春节). 退到 calendar 里最近的一天
        recent = next((d for d in reversed(calendar) if d <= trade_date), None)
        if recent is None:
            raise ValueError(
                f"trade_date {trade_date} 不在日历里且找不到更早交易日. "
                "calendar 是空? 先跑 fa data update (日线) 建日历."
            )
        log.warning("trade_date %s 不在日历 (节假日?), 回退到 %s", trade_date, recent)
        trade_date = recent

    today_pos = cal_index[trade_date]

    # 2. 分批拉腾讯
    collector = TencentQuoteCollector()
    all_quotes: dict = {}
    for i in range(0, len(codes_list), chunk_size):
        chunk = codes_list[i: i + chunk_size]
        try:
            quotes = collector.fetch(chunk, timeout=10.0)
            all_quotes.update(quotes)
        except Exception as e:
            log.warning("Tencent batch [%d:%d] failed: %s", i, i + chunk_size, e)

    # 3. 按字段批量 safe_merge_write
    stats = {"total": len(codes_list), "ok": 0, "no_quote": 0,
             "missing_pe": 0, "missing_pb": 0, "missing_mv": 0,
             "errors": []}

    for code in codes_list:
        q = all_quotes.get(code)
        if not q:
            stats["no_quote"] += 1
            continue

        try:
            wrote_any = False
            for tencent_field, (bin_field, scale) in _FIELD_MAP.items():
                v = q.get(tencent_field)
                if v is None:
                    if bin_field == "pe_ttm": stats["missing_pe"] += 1
                    elif bin_field == "pb": stats["missing_pb"] += 1
                    elif bin_field == "total_mv": stats["missing_mv"] += 1
                    continue
                value = float(v) * scale
                safe_merge_write(code, bin_field, "day", provider_uri,
                                 positions=[today_pos], values=[value])
                wrote_any = True

            if wrote_any:
                stats["ok"] += 1
                update_instrument_range(code, trade_date, trade_date,
                                        provider_uri, market="all")
        except Exception as e:
            stats["errors"].append({"code": code, "err": f"{type(e).__name__}: {e}"})
            log.warning("update_daily_basic %s failed: %s", code, e)

    return stats
