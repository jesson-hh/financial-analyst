# -*- coding: utf-8 -*-
"""海外个股行情看板中台(datafeed)—— 美股/港股精选清单实时行情,SWR 只读快照。

经统一实时门户 live_client.probe("overseas_stock_quote", code="A,B,...")(腾讯 qt 境内端点,
与本地指数源同上游)**一次批量拉全清单**(2026-07-28 提速:此前逐只 14 子进程被 1s 起跑
节流串行化 ~25s,批量后 ~2s;stocks 侧旧版不认逗号时诚实回退逐只)。SWR(秒回缓存 +
过期后台单飞刷新 + 首拉 warming)+ **磁盘快照**(var/live/overseas.json,重启不再冷)。
纯展示,绝不混入 A 股信号/星级。个股 lookup 走 /data/overseas?code= 直拉。
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
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
_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_OVERSEAS_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "overseas.json")))

_LOCK = threading.Lock()
_INFLIGHT = [False]
_MEM_CACHE: Dict[str, Any] = {"data": None}

# The generic facade's publish/visible timestamps can be local fetch time. Only
# explicit upstream quote/trade fields may prove a market timestamp.
_SOURCE_TRADE_TS_FIELDS = (
    "source_trade_ts", "quote_ts", "trade_ts", "quote_time", "trade_time",
)


def _with_quote_time_semantics(row: Dict[str, Any]) -> Dict[str, Any]:
    """Attach conservative quote-time semantics without inventing freshness."""
    out = dict(row)
    source_trade_ts = next(
        (
            str(out[field]).strip()
            for field in _SOURCE_TRADE_TS_FIELDS
            if out.get(field) is not None and str(out[field]).strip()
        ),
        "",
    )
    if source_trade_ts:
        out["source_trade_ts"] = source_trade_ts
        out["quote_time_status"] = "source_reported"
        out["quote_time_note"] = "成交时间由上游显式提供；抓取时间仍不等于成交时间"
    else:
        out["source_trade_ts"] = None
        out["quote_time_status"] = "unknown"
        out["quote_time_note"] = (
            "上游未提供成交时间；publish_ts/visible_ts 仅是抓取或可见时间，"
            "不得据此称实时成交价"
        )
    return out


def _now_ts() -> int:
    return int(datetime.now().timestamp())


def _write_disk(data: Dict[str, Any]) -> None:
    """原子落盘(tmp+replace);失败静默吞——落盘只是重启保温,绝不拖垮刷新。"""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except OSError:
        pass


def _load_disk() -> Optional[Dict[str, Any]]:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    # pulled_ts 必须是数字(评审:合法 JSON 但非数字会让 stale 减法处永久抛异常且毒化内存缓存)
    if (isinstance(data, dict) and isinstance(data.get("pulled_ts"), (int, float))
            and not isinstance(data.get("pulled_ts"), bool) and isinstance(data.get("rows"), list)):
        return data
    return None


def probe_one(code: str) -> Optional[Dict[str, Any]]:
    """单只海外行情(个股 lookup 用):经 live_client 拉一行,失败/缺 → None(诚实空)。"""
    try:
        r = _lc.probe("overseas_stock_quote", code=code, limit=1)
    except Exception:  # noqa: BLE001
        return None
    if not (r.get("ok") and r.get("status") == "ok"):   # 真成功恒 "ok";"" 是机械失败
        return None
    rows = _lc.native_rows(r.get("items")) or []
    return _with_quote_time_semantics(rows[0]) \
        if rows and isinstance(rows[0], dict) else None


def _placeholder(w: Dict[str, str]) -> Dict[str, Any]:
    return _with_quote_time_semantics({
        "code": w["code"], "label": w["label"], "market": w["market"],
        "name": w["label"], "price": None, "change_pct": None, "note": "缺价/不可达",
    })


def _fetch_all_percode() -> List[Dict[str, Any]]:
    """逐只回退路径(stocks 侧不认逗号批量时):并发拉每一只;单只失败=该行缺价占位。"""
    label_map = {w["code"]: w["label"] for w in WATCHLIST}

    def _one(w: Dict[str, str]) -> Dict[str, Any]:
        row = probe_one(w["code"])
        if row is None:
            return _placeholder(w)
        row["label"] = label_map.get(row.get("code")) or w["label"]
        return row

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(WATCHLIST))) as ex:
        return [r for r in ex.map(_one, WATCHLIST)]


def _fetch_all() -> List[Dict[str, Any]]:
    """一次批量 probe 拉全清单(2026-07-28 提速);批量空/失败诚实回退逐只,绝不整表缺价。
    行序恒=WATCHLIST 序;批量结果里的缺行(退市/停牌)落缺价占位不拖垮整表。"""
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    try:
        codes = [w["code"] for w in WATCHLIST]
        r = _lc.probe("overseas_stock_quote", code=",".join(codes), limit=len(codes))
        if r.get("ok") and r.get("status") == "ok":
            for row in _lc.native_rows(r.get("items")) or []:
                if isinstance(row, dict) and row.get("code"):
                    rows_by_code[str(row["code"])] = _with_quote_time_semantics(row)
    except Exception:  # noqa: BLE001
        rows_by_code = {}
    if not rows_by_code:
        return _fetch_all_percode()
    out: List[Dict[str, Any]] = []
    for w in WATCHLIST:
        row = rows_by_code.get(w["code"])
        if row is None:
            out.append(_placeholder(w))
        else:
            row["label"] = w["label"]
            out.append(row)
    return out


def _refresh() -> Dict[str, Any]:
    rows = _fetch_all()
    data = {"pulled_ts": _now_ts(), "pulled_at": datetime.now().isoformat(timespec="seconds"),
            "rows": rows, "n": len(rows)}
    _MEM_CACHE["data"] = data
    _write_disk(data)
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
    """SWR:有 TTL 内缓存 → 秒回;过期 → 返回旧值 + 后台刷;无缓存 → warming 不阻塞。
    内存空时先试磁盘快照(重启保温):旧值秒回并 stale 显形,不再 warming 空表。"""
    data = _MEM_CACHE.get("data")
    if data is None:
        data = _load_disk()
        if data is not None:
            _MEM_CACHE["data"] = data
    if data is None:
        _trigger()
        return {"warming": True, "rows": [], "note": "海外行情预热中(后台首拉已触发),稍后重试。",
                "watchlist": WATCHLIST}
    stale = (_now_ts() - int(data.get("pulled_ts") or 0)) >= fresh_within_s
    if stale:
        _trigger()
    rows = [
        _with_quote_time_semantics(row) if isinstance(row, dict) else row
        for row in (data.get("rows") or [])
    ]
    return {"warming": False, "stale": stale, "pulled_at": data.get("pulled_at"),
            "n": data.get("n"), "rows": rows, "watchlist": WATCHLIST}
