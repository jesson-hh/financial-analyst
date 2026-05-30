"""ETF 日线价格更新器 (pytdx → cn_data_etf qlib bins, 单进程).

直连 TDX 主站拉 ETF 日线 OHLCV, 通过 ``safe_merge_write`` 增量写入独立 ETF 数据目录
``cn_data_etf/``. **不与股票 bin 共用目录**, 避免污染主库.

设计原则:
- **单进程顺序写入** — bin 写入不支持并发; 历史上并发写导致 calendar 损坏事故.
- 复用 ``pytdx_kline._DAILY_FIELD_MAP`` 和 ``fetch_daily``，保证 vol 单位 (手) 一致.
- ETF 代码须含前缀: ``SH510300``、``SZ159001`` 等.

公开 API:
- ``update_etf_one(etf_uri, client, code, n_bars)`` → int (写入 bar 数)
- ``update_etf_daily_batch(etf_uri, codes, ...)`` → dict stats
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from financial_analyst.data.bin_writer import (
    build_calendar_index,
    load_calendar,
    read_bin,
    safe_merge_write,
    save_instruments,
)
from financial_analyst.data.updaters.pytdx_kline import (
    _DAILY_FIELD_MAP,
    fetch_daily,
)
from financial_analyst.data.updaters.pytdx_pool import PytdxClient

log = logging.getLogger(__name__)


def update_etf_one(
    etf_uri: str,
    client,
    code: str,
    n_bars: int = 800,
) -> int:
    """拉单只 ETF 日线并写入 bin.

    Args:
        etf_uri: ETF 专用 Qlib 目录 (``cn_data_etf/``). 日历须已存在.
        client:  PytdxClient 实例, 或 None (测试时 monkeypatch fetch_daily 即可).
        code:    SH/SZ 前缀 ETF 代码, 例如 ``'SH510300'``.
        n_bars:  拉最近多少根日线 (默认 800, pytdx 单次上限).

    Returns:
        实际命中日历并写入的 bar 数 (0 = 空/日历无匹配).
    """
    cal = load_calendar(etf_uri, freq="day")
    cidx = build_calendar_index(cal)

    bars = fetch_daily(client, code, n_bars=n_bars)
    if not bars:
        log.debug("update_etf_one: %s empty bars (delisted / unknown)", code)
        return 0

    # 把 pytdx bars 转换成 {position: {field: value}}
    rows: List[Tuple[int, Dict[str, float]]] = []
    for b in bars:
        key = b.get("datetime", "")[:10]
        if key not in cidx:
            continue
        rec = {
            dst: float(b[src]) * mul
            for src, (dst, mul) in _DAILY_FIELD_MAP.items()
            if src in b and b[src] is not None
        }
        rows.append((cidx[key], rec))

    if not rows:
        return 0

    # 按字段逐一 safe_merge_write (单进程顺序写, 绝不并行)
    all_dst_fields = {dst for _, (dst, _) in _DAILY_FIELD_MAP.items()}
    for dst in all_dst_fields:
        pv = [(pos, rec[dst]) for pos, rec in rows if dst in rec]
        if pv:
            positions = [p for p, _ in pv]
            values = [v for _, v in pv]
            safe_merge_write(code, dst, "day", etf_uri,
                             positions=positions, values=values)

    return len(rows)


def _rebuild_etf_instruments(etf_uri: str, codes: List[str]) -> None:
    """扫描已写入的 close bin, 重建 instruments/all.txt 中各 ETF 的有效日期范围."""
    cal = load_calendar(etf_uri, freq="day")
    inst: Dict[str, Tuple[str, str]] = {}
    for code in codes:
        si, arr = read_bin(code, "close", "day", etf_uri)
        if arr is None or len(arr) == 0:
            continue
        valid = np.where(~np.isnan(arr))[0]
        if len(valid) == 0:
            continue
        s = si + int(valid[0])
        e = si + int(valid[-1])
        if 0 <= s < len(cal) and 0 <= e < len(cal):
            inst[code] = (cal[s], cal[e])
    if inst:
        save_instruments(inst, etf_uri, market="all")


def update_etf_daily_batch(
    etf_uri: str,
    codes: List[str],
    n_bars: int = 800,
    client: Optional[PytdxClient] = None,
    progress: bool = True,
) -> dict:
    """批量更新 ETF 日线 (单进程顺序).

    Args:
        etf_uri:  ETF 专用 Qlib 目录.
        codes:    ETF 代码列表, 例如 ``['SH510300', 'SZ159001']``.
        n_bars:   每只拉多少根 (默认 800 全量; 增量用 30).
        client:   共享 PytdxClient. None = 函数内自建 + 用完关.
        progress: 每 50 只打印进度.

    Returns:
        ``{"total": N, "ok": N, "empty": N, "failed": N}``
    """
    own = client is None
    if own:
        client = PytdxClient()

    stats = {"total": len(codes), "ok": 0, "empty": 0, "failed": 0}
    try:
        for i, code in enumerate(codes, 1):
            try:
                n = update_etf_one(etf_uri, client, code, n_bars=n_bars)
                if n > 0:
                    stats["ok"] += 1
                else:
                    stats["empty"] += 1
            except Exception as exc:
                stats["failed"] += 1
                log.warning("update_etf_daily_batch: %s failed: %s", code, exc)
            if progress and i % 50 == 0:
                print(f"  [etf {i}/{len(codes)}] {stats}")
    finally:
        if own:
            client.close()

    _rebuild_etf_instruments(etf_uri, codes)
    return stats
