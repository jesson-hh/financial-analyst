"""WatchFeed — 盘中实时数据拉取封装 (Tencent 快照 + pytdx 5min, vol 归一).

盯盘 loop 每 tick 用这一个对象拉两类数据:

* ``snapshot(codes)`` —— 一次批量 HTTP 调 :class:`TencentQuoteCollector.fetch`,
  返回 ``{SH600519: {price, changePercent, vol_ratio, high, low, ...}}``
  (volume 单位已是 **手**, 见 tencent_quote.py 字段表).
* ``bars5(code, n)`` —— 调 :func:`fetch_5min` 拉近 N 根 5min K, 转成
  :class:`pandas.DataFrame`, 列对齐 :class:`IntradayTrigger` 期望的
  ``open/high/low/close/vol/trade_date``.

  ⚠ **vol 单位坑**: pytdx ``fetch_5min`` 的 vol 是 **股**, IntradayTrigger /
  Tencent 快照用 **手** —— 这里统一 **÷100** 转手 (与
  ``pytdx_kline._DAILY_FIELD_MAP`` 的 ``vol: 1/100`` 一致).

容错: 所有网络调用包 try/except, 失败 **返回 None/空 + log warning, 不抛**
(单 tick 单票挂掉不能拖垮整个盯盘 loop, 见 spec §10).

``PytdxClient`` 复用单连接 (盘中高频, 不每次重连); 构造时不连网 (惰性), 第一次
``bars5`` 才真正握手. ``NO_PROXY`` 由底层 collector (httpx ``trust_env=False``) /
pytdx 直连主站各自处理, 这里不动全局 env.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
from financial_analyst.data.updaters.pytdx_kline import fetch_5min
from financial_analyst.data.updaters.pytdx_pool import PytdxClient

log = logging.getLogger(__name__)

# 5min DataFrame 对外暴露的列 (= IntradayTrigger.check 读的列)
_BAR_COLUMNS = ["open", "high", "low", "close", "vol", "trade_date"]

# pytdx vol(股) → 手
_VOL_TO_LOTS = 1 / 100.0


def _empty_bars() -> pd.DataFrame:
    """列齐但 0 行的 DataFrame, 给容错/退市路径用 (下游 len()==0 安全返回)."""
    return pd.DataFrame(columns=_BAR_COLUMNS)


class WatchFeed:
    """盘中实时行情封装. 持有复用的 pytdx 连接 + Tencent collector.

    用法::

        feed = WatchFeed()
        snap = feed.snapshot(["SH600519", "SZ002594"])   # 批量快照
        df = feed.bars5("SH600519")                      # 5min K, vol 已转手
        feed.close()
    """

    def __init__(self, client: Optional[PytdxClient] = None) -> None:
        # client 缺省惰性创建 (PytdxClient() 不连网, 第一次 call 才握手) —— 盘中复用单连接.
        self._client = client
        self._collector = TencentQuoteCollector()

    # ─────────────────────── 内部 ───────────────────────

    def _get_client(self) -> PytdxClient:
        if self._client is None:
            self._client = PytdxClient()
        return self._client

    # ─────────────────────── 快照 ───────────────────────

    def snapshot(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量实时快照. 一次 HTTP 拉所有 ``codes``.

        Returns:
            ``{canonical_code: {price, changePercent, vol_ratio, high, low,
            volume(手), amount, ...}}``. 空 codes → ``{}``; 网络失败 → ``{}`` (log).
        """
        if not codes:
            return {}
        try:
            return self._collector.fetch(list(codes))
        except Exception as e:  # noqa: BLE001 — 容错: 单 tick 失败不拖垮 loop
            log.warning("WatchFeed.snapshot failed for %d codes: %s", len(codes), e)
            return {}

    # ─────────────────────── 5min K ───────────────────────

    def bars5(self, code: str, n: int = 240) -> pd.DataFrame:
        """拉近 ``n`` 根 5min K, 转 DataFrame.

        列: ``open/high/low/close/vol/trade_date`` (= IntradayTrigger 期望).
        **vol 已 ÷100 (股→手)**. datetime → ``trade_date``.

        退市/拉空/网络失败 → 列齐的空 DataFrame (不抛).
        """
        try:
            bars = fetch_5min(self._get_client(), code, n_bars=n)
        except Exception as e:  # noqa: BLE001 — 容错
            log.warning("WatchFeed.bars5 fetch failed for %s: %s", code, e)
            return _empty_bars()

        if not bars:
            return _empty_bars()

        rows = []
        for b in bars:
            try:
                vol_raw = b.get("vol")
                rows.append({
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "vol": (float(vol_raw) * _VOL_TO_LOTS) if vol_raw is not None else None,
                    "trade_date": b.get("datetime"),
                })
            except (KeyError, TypeError, ValueError) as e:
                log.debug("WatchFeed.bars5 skip bad bar for %s: %s (%r)", code, e, b)
                continue

        if not rows:
            return _empty_bars()
        return pd.DataFrame(rows, columns=_BAR_COLUMNS)

    # ─────────────────────── 资源 ───────────────────────

    def close(self) -> None:
        """关闭复用的 pytdx 连接 (盘后/停盘调)."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:  # noqa: BLE001
                log.debug("WatchFeed.close: pytdx close failed: %s", e)
            self._client = None
