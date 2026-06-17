"""市场状态 (market_status.json) 读取 — 交易盯盘台 P4.1-full.

research 端 (收盘后, scripts/export_market_status.py) 写, fa 端 (盘前/盘中) 读市场级三源:
regime (牛/熊/震荡) / 涨停家数 / 主线雷达. 镜像 signal_pack.py 的 parquet-root 同目录解析.
缺文件 / 坏 json / 非 dict → 带结构的空 dict (容错, 同 signal_pack 纪律, 调用方据空字段判定).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger(__name__)

_DEFAULT_FILENAME = "market_status.json"


def default_market_status_path() -> Path:
    """market_status.json 路径解析。

    优先 env ``MARKET_STATUS_PATH``(guanlan 自包含:由 guanlan_v2 原生生成器写仓内
    ``data/market_status.json``, **不写 G:/stocks**)。该 env 指向的文件存在则用它;
    否则回退共享 parquet root(与 daily_signal_pack / 推荐日志同目录, 旧行为)。
    回退保证迁移期 guanlan json 未生成时仍读得到老数据(向后兼容)。
    """
    import os

    override = os.environ.get("MARKET_STATUS_PATH")
    if override:
        p = Path(override)
        if p.exists():
            return p
    from financial_analyst.watch.store import default_recs_path

    return default_recs_path().parent / _DEFAULT_FILENAME


def _empty() -> dict:
    """顶层结构占位 (前端/端点统一按这些键读)."""
    return {
        "date": None,
        "regime": {},
        "limit_ups": {},
        "mainline": {"as_of": None, "n_mainline": 0, "top": []},
    }


def load_market_status(path: Optional[Union[str, Path]] = None) -> dict:
    """读 market_status.json → dict.

    缺文件 / 读失败 / 坏 json / 非 dict → 带结构的空 dict (顶层键齐全).
    部分写 (缺某顶层键) → 用空结构补齐缺键.
    """
    p = Path(path) if path is not None else default_market_status_path()
    if not p.exists():
        return _empty()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as exc:                       # 坏 json 不抛
        log.warning("load_market_status: 读 %s 失败: %s", p, exc)
        return _empty()
    if not isinstance(d, dict):
        return _empty()
    base = _empty()
    base.update(d)                                 # 已有键覆盖默认, 缺键保留结构占位
    return base
