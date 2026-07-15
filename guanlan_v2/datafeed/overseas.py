# -*- coding: utf-8 -*-
"""海外个股行情看板中台(datafeed)—— 美股/港股精选清单实时行情,SWR 只读快照。

经统一实时门户 live_client.probe("overseas_stock_quote", code=...)(腾讯 qt 境内端点,与本地
指数源同上游)逐只拉取;SWR(秒回缓存 + 过期后台单飞刷新 + 首拉 warming),避免每次页面
打十几个子进程阻塞。纯展示,绝不混入 A 股信号/星级。个股 lookup 走 /data/overseas?code= 直拉。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from guanlan_v2.datafeed import live_client as _lc

# 精选清单:美股科技权重 + 港股中概龙头(纯展示的默认盯盘池;个股 lookup 不受此限)。
WATCHLIST: List[Dict[str, str]] = [
    {"code": "AAPL", "label": "苹果", "market": "US"},
    {"code": "MSFT", "label": "微软", "market": "US"},
    {"code": "NVDA", "label": "英伟达", "market": "US"},
    {"code": "GOOGL", "label": "谷歌", "market": "US"},
    {"code": "AMZN", "label": "亚马逊", "market": "US"},
    {"code": "META", "label": "Meta", "market": "US"},
    {"code": "TSLA", "label": "特斯拉", "market": "US"},
    {"code": "TSM", "label": "台积电ADR", "market": "US"},
    {"code": "00700", "label": "腾讯控股", "market": "HK"},
    {"code": "09988", "label": "阿里巴巴-W", "market": "HK"},
    {"code": "03690", "label": "美团-W", "market": "HK"},
    {"code": "09618", "label": "京东集团-SW", "market": "HK"},
    {"code": "01810", "label": "小米集团-W", "market": "HK"},
    {"code": "00981", "label": "中芯国际", "market": "HK"},
]

_TTL_S = int(os.environ.get("GUANLAN_OVERSEAS_TTL_S", "60"))
_MAX_WORKERS = 8

_LOCK = threading.Lock()
_INFLIGHT = [False]
_MEM_CACHE: Dict[str, Any] = {"data": None}


def _now_ts() -> int:
    return int(datetime.now().timestamp())


def probe_one(code: str) -> Optional[Dict[str, Any]]:
    """单只海外行情(个股 lookup 用):经 live_client 拉一行,失败/缺 → None(诚实空)。"""
    try:
        r = _lc.probe("overseas_stock_quote", code=code, limit=1)
    except Exception:  # noqa: BLE001
        return None
    if not (r.get("ok") and r.get("status") in ("ok", "")):
        return None
    rows = _lc.native_rows(r.get("items")) or []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _fetch_all() -> List[Dict[str, Any]]:
    """并发拉 WATCHLIST 每一只;单只失败=该行缺,不拖垮整表。带 label 便于前端展示。"""
    label_map = {w["code"]: w["label"] for w in WATCHLIST}

    def _one(w: Dict[str, str]) -> Optional[Dict[str, Any]]:
        row = probe_one(w["code"])
        if row is None:
            return {"code": w["code"], "label": w["label"], "market": w["market"],
                    "name": w["label"], "price": None, "change_pct": None, "note": "缺价/不可达"}
        row["label"] = label_map.get(row.get("code")) or w["label"]
        return row

    rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(WATCHLIST))) as ex:
        for r in ex.map(_one, WATCHLIST):
            if r is not None:
                rows.append(r)
    return rows


def _refresh() -> Dict[str, Any]:
    rows = _fetch_all()
    data = {"pulled_ts": _now_ts(), "pulled_at": datetime.now().isoformat(timespec="seconds"),
            "rows": rows, "n": len(rows)}
    _MEM_CACHE["data"] = data
    return data


def _trigger() -> bool:
    """单飞:已有刷新在跑 → False;否则起 daemon 线程后台刷新。"""
    with _LOCK:
        if _INFLIGHT[0]:
            return False
        _INFLIGHT[0] = True

    def _run() -> None:
        try:
            _refresh()
        finally:
            _INFLIGHT[0] = False

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError:
        _INFLIGHT[0] = False
        return False
    return True


def read_overseas(fresh_within_s: int = _TTL_S) -> Dict[str, Any]:
    """SWR:有 TTL 内缓存 → 秒回;过期 → 返回旧值 + 后台刷;无缓存 → warming 不阻塞。"""
    data = _MEM_CACHE.get("data")
    if data is None:
        _trigger()
        return {"warming": True, "rows": [], "note": "海外行情预热中(后台首拉已触发),稍后重试。",
                "watchlist": WATCHLIST}
    stale = (_now_ts() - int(data.get("pulled_ts") or 0)) >= fresh_within_s
    if stale:
        _trigger()
    return {"warming": False, "stale": stale, "pulled_at": data.get("pulled_at"),
            "n": data.get("n"), "rows": data.get("rows") or [], "watchlist": WATCHLIST}
