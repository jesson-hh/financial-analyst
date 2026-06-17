"""Ongoing data updaters — incremental update sources for daily / 5min / daily_basic.

区别于 ``ingest/`` (一次性 bootstrap, CSV/ZIP→bin) 和 ``loaders/`` (运行时读
本地 bin). ``updaters/`` 是**每日增量**: 用直连数据源 (pytdx 主站 / 腾讯实时)
追加新数据到 Qlib bin, 不依赖 Tushare token.

模块:
  - ``pytdx_pool``  : pytdx 主站多 host 连接池, 自动 failover
  - ``pytdx_kline`` : 日线 + 5min 拉取并 safe_merge_write 到 bin
  - ``tencent_basic``: 腾讯实时 PE/PB/MV 快照写 daily_basic bin
"""
