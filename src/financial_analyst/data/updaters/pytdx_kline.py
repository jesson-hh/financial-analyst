"""pytdx 日线 + 5min 拉取 + 写 Qlib bin.

直连 TDX 主站, 不需要 Tushare token. 配合 ``bin_writer.safe_merge_write`` 增量
追加, 不会覆盖历史数据.

关键约定:
- **vol 单位**: pytdx 返回 vol (股), Tushare/Qlib 用 vol (手). 写入时除 100.
- **datetime**: pytdx 返回 ``'YYYY-MM-DD HH:MM'``. 日线截 ``[:10]`` 作 trade_date.
- **复权**: pytdx ``get_security_bars`` 是**不复权**价格. 复权因子要单独拉
  ``get_xdxr_info``. 第一版先不算复权, 与 G:/stocks/incremental_update_tushare 一致.
- **退市股**: pytdx 返回空 bars, ``update_daily`` 安全返回 0, 不抛.

API:
- ``fetch_daily(client, code, n_bars=800)`` → list of bar dicts
- ``fetch_5min(client, code, n_bars=240)`` → list of bar dicts
- ``update_daily(provider_uri, client, code)`` → 拉昨日开始的增量, 写 bin
- ``update_daily_batch(provider_uri, codes)`` → 批量更新, 进度条
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from pytdx.params import TDXParams

from financial_analyst.data.bin_writer import (
    append_calendar, build_calendar_index, get_bin_range,
    load_calendar, load_instruments, safe_merge_write, update_instrument_range,
)
from financial_analyst.data.updaters.pytdx_pool import (
    PytdxClient, qlib_code_to_pytdx,
)

log = logging.getLogger(__name__)


# pytdx 字段名 → Qlib bin 字段名 (含单位转换)
_DAILY_FIELD_MAP = {
    "open": ("open", 1.0),
    "high": ("high", 1.0),
    "low": ("low", 1.0),
    "close": ("close", 1.0),
    "vol": ("volume", 1 / 100.0),   # 股 → 手
    "amount": ("amount", 1.0),       # 元
}
_5MIN_FIELD_MAP = _DAILY_FIELD_MAP   # 字段一样


# ─────────────────────────── 拉取 (纯) ───────────────────────────


def fetch_daily(client: PytdxClient, code: str, n_bars: int = 800) -> List[dict]:
    """拉最近 n_bars 根日线. pytdx 单次上限 800 (TDXParams.MAX_KLINE_COUNT).

    Returns:
        list of dict with keys: datetime ('YYYY-MM-DD HH:MM'), open, high, low,
        close, vol (股), amount, year, month, day. 退市/不存在 → 空 list.
    """
    if n_bars > 800:
        # 分页
        all_bars = []
        offset = 0
        while offset < n_bars:
            count = min(800, n_bars - offset)
            mkt, c = qlib_code_to_pytdx(code)
            chunk = client.call("get_security_bars",
                                TDXParams.KLINE_TYPE_DAILY, mkt, c, offset, count)
            if not chunk:
                break
            all_bars = chunk + all_bars   # 越前面的 offset 越大 (越早)
            offset += count
            if len(chunk) < count:
                break
        return all_bars

    mkt, c = qlib_code_to_pytdx(code)
    bars = client.call("get_security_bars",
                       TDXParams.KLINE_TYPE_DAILY, mkt, c, 0, n_bars)
    return bars or []


def fetch_5min(client: PytdxClient, code: str, n_bars: int = 240) -> List[dict]:
    """拉最近 n_bars 根 5min K. 240 根 ≈ 5 个交易日 (一天 48 根)."""
    if n_bars > 800:
        # 同 daily 分页
        all_bars = []
        offset = 0
        while offset < n_bars:
            count = min(800, n_bars - offset)
            mkt, c = qlib_code_to_pytdx(code)
            chunk = client.call("get_security_bars",
                                TDXParams.KLINE_TYPE_5MIN, mkt, c, offset, count)
            if not chunk:
                break
            all_bars = chunk + all_bars
            offset += count
            if len(chunk) < count:
                break
        return all_bars

    mkt, c = qlib_code_to_pytdx(code)
    bars = client.call("get_security_bars",
                       TDXParams.KLINE_TYPE_5MIN, mkt, c, 0, n_bars)
    return bars or []


# ─────────────────────────── 写入 ───────────────────────────


def _bars_to_positions(
    bars: List[dict], calendar_index: dict, freq: str = "day"
) -> List[tuple]:
    """把 pytdx bars 列出 ``[(position, field_dict), ...]`` 准备喂 safe_merge_write.

    日线 trade_date 用 ``YYYY-MM-DD``, 5min 用 ``YYYY-MM-DD HH:MM:SS``.
    跳过日历里没有的 date (新交易日要先 append_calendar).
    """
    result = []
    for bar in bars:
        raw_dt = bar.get("datetime", "")
        if freq == "day":
            key = raw_dt[:10]
        else:
            # pytdx 返回 'YYYY-MM-DD HH:MM', 加 :00 秒位
            key = raw_dt if len(raw_dt) > 16 else raw_dt + ":00"
        if key not in calendar_index:
            continue
        pos = calendar_index[key]
        result.append((pos, bar))
    return result


def update_daily(
    provider_uri: str,
    client: PytdxClient,
    code: str,
    n_bars: int = 30,
    fields: Optional[List[str]] = None,
) -> int:
    """拉某只股票最近 ``n_bars`` 天日线, append 到 Qlib bin.

    Args:
        provider_uri: Qlib 数据目录 (要写到的 cn_data)
        client: pytdx 连接 (建议复用)
        code: SH600519 / SZ300750 / ...
        n_bars: 拉最近多少根 K. 增量更新建议 30 (含周末容错); 全量回补给大值
        fields: 写哪些字段, 默认 OHLCV + amount

    Returns:
        实际写入的 bar 数 (0 = 退市 / 拉空)
    """
    bars = fetch_daily(client, code, n_bars=n_bars)
    if not bars:
        log.debug("update_daily: %s empty (likely delisted)", code)
        return 0

    fields_to_write = fields or ["open", "high", "low", "close", "vol", "amount"]

    # 1. 先把新日期 append 到日历
    new_dates = sorted({b["datetime"][:10] for b in bars})
    n_added = append_calendar(new_dates, provider_uri, freq="day")
    if n_added > 0:
        log.debug("update_daily: %s added %d calendar dates", code, n_added)

    # 2. 重新读日历 + index
    calendar = load_calendar(provider_uri, freq="day")
    cal_index = build_calendar_index(calendar)

    # 3. 按字段批量 safe_merge_write
    positions_bars = _bars_to_positions(bars, cal_index, freq="day")
    if not positions_bars:
        return 0

    for ptdx_field in fields_to_write:
        bin_field, scale = _DAILY_FIELD_MAP.get(ptdx_field, (ptdx_field, 1.0))
        positions = [p for p, _ in positions_bars]
        values = [float(b.get(ptdx_field, 0.0)) * scale for _, b in positions_bars]
        safe_merge_write(code, bin_field, "day", provider_uri,
                         positions=positions, values=values)

    # 4. 更新 instruments 文件 (扩展该 code 的 [start, end])
    start = bars[0]["datetime"][:10]
    end = bars[-1]["datetime"][:10]
    update_instrument_range(code, start, end, provider_uri, market="all")

    return len(positions_bars)


def update_5min(
    provider_uri: str,
    client: PytdxClient,
    code: str,
    n_bars: int = 240,
    fields: Optional[List[str]] = None,
) -> int:
    """5min 版本. 注意 5min 日历更新更复杂 (一天 48 根).

    Args:
        n_bars: 默认 240 ≈ 5 个交易日. ``.lc5`` 文件保留~7 天, 每周必跑.
    """
    bars = fetch_5min(client, code, n_bars=n_bars)
    if not bars:
        return 0

    fields_to_write = fields or ["open", "high", "low", "close", "vol", "amount"]

    # 5min datetime 'YYYY-MM-DD HH:MM' → 'YYYY-MM-DD HH:MM:00'
    new_dts = sorted({
        b["datetime"] if len(b["datetime"]) > 16 else b["datetime"] + ":00"
        for b in bars
    })
    n_added = append_calendar(new_dts, provider_uri, freq="5min")
    if n_added > 0:
        log.debug("update_5min: %s added %d calendar bars", code, n_added)

    calendar = load_calendar(provider_uri, freq="5min")
    cal_index = build_calendar_index(calendar)

    positions_bars = _bars_to_positions(bars, cal_index, freq="5min")
    if not positions_bars:
        return 0

    for ptdx_field in fields_to_write:
        bin_field, scale = _5MIN_FIELD_MAP.get(ptdx_field, (ptdx_field, 1.0))
        positions = [p for p, _ in positions_bars]
        values = [float(b.get(ptdx_field, 0.0)) * scale for _, b in positions_bars]
        safe_merge_write(code, bin_field, "5min", provider_uri,
                         positions=positions, values=values)

    start = bars[0]["datetime"]
    end = bars[-1]["datetime"]
    update_instrument_range(code, start, end, provider_uri, market="all")

    return len(positions_bars)


# ─────────────────────────── 批量 ───────────────────────────


def update_daily_batch(
    provider_uri: str,
    codes: Iterable[str],
    n_bars: int = 30,
    client: Optional[PytdxClient] = None,
    progress: bool = True,
) -> dict:
    """批量增量日线. 单连接顺序处理 (~27ms/股, 5500 只 ~2.5 min).

    Args:
        provider_uri: Qlib 目录
        codes: 股票代码迭代器
        n_bars: 每只拉多少天 (30=增量, 800=全量补)
        client: 共享 PytdxClient (None=自动建+关)
        progress: 是否打印进度

    Returns:
        ``{"total": N, "ok": N, "empty": N, "failed": N, "errors": [...]}``
    """
    own_client = client is None
    if own_client:
        client = PytdxClient()
    stats = {"total": 0, "ok": 0, "empty": 0, "failed": 0, "errors": []}
    codes_list = list(codes)
    try:
        for i, code in enumerate(codes_list, 1):
            stats["total"] += 1
            try:
                n = update_daily(provider_uri, client, code, n_bars=n_bars)
                if n > 0:
                    stats["ok"] += 1
                else:
                    stats["empty"] += 1
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append({"code": code, "err": f"{type(e).__name__}: {e}"})
                log.warning("update_daily_batch: %s failed: %s", code, e)
            if progress and i % 100 == 0:
                print(f"  [{i}/{len(codes_list)}] ok={stats['ok']} "
                      f"empty={stats['empty']} failed={stats['failed']}")
    finally:
        if own_client:
            client.close()
    return stats


def update_5min_batch(
    provider_uri: str,
    codes: Iterable[str],
    n_bars: int = 240,
    client: Optional[PytdxClient] = None,
    progress: bool = True,
) -> dict:
    """批量增量 5min. 用法同 ``update_daily_batch``."""
    own_client = client is None
    if own_client:
        client = PytdxClient()
    stats = {"total": 0, "ok": 0, "empty": 0, "failed": 0, "errors": []}
    codes_list = list(codes)
    try:
        for i, code in enumerate(codes_list, 1):
            stats["total"] += 1
            try:
                n = update_5min(provider_uri, client, code, n_bars=n_bars)
                if n > 0:
                    stats["ok"] += 1
                else:
                    stats["empty"] += 1
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append({"code": code, "err": f"{type(e).__name__}: {e}"})
                log.warning("update_5min_batch: %s failed: %s", code, e)
            if progress and i % 100 == 0:
                print(f"  [{i}/{len(codes_list)}] ok={stats['ok']} "
                      f"empty={stats['empty']} failed={stats['failed']}")
    finally:
        if own_client:
            client.close()
    return stats
