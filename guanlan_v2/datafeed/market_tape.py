# -*- coding: utf-8 -*-
"""盘口实时快照中台(datafeed 中台第④块)—— 全市场无-code 实时源统一只读快照。

零重造:拉取全走统一客户端 live_client.probe(观澜唯一现拉门户)。本模块只做
「10 全市场源聚合 + stale-while-revalidate 磁盘缓存 + 只读 read_tape」。
红线:展示/上下文型,derived 纯算术无信号;每源独立 pulled_at,诚实降级绝不伪造新鲜;
首拉无缓存 warming 不阻塞;后台刷新单飞(防外源被打)。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.datafeed import live_client as _lc

_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_MARKET_TAPE_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "market_tape.json")))
_DEFAULT_TTL_S = int(os.environ.get("GUANLAN_MARKET_TAPE_TTL_S", "180"))

# 9 展示源(show=True)+ 1 收敛源 ths_hot_reason(show=False,打板温度 top_reasons 用)。
# sid 走 live_client alias 解析;date="" 由 live_client DATE_POOLS 补当日 YYYYMMDD。
# 打板生态/龙虎榜按家数计温,limit=300(probe 上限)保 zt_count/连板/炸板率不被默认 20 截断;
# 行业 100 兜住全 A ~86 行业(可取涨跌两端);热榜取 top 50。
_SOURCES: List[Dict[str, Any]] = [
    {"sid": "em_zt_pool",     "kw": {"date": "", "limit": 300}, "show": True},
    {"sid": "em_zb_pool",     "kw": {"date": "", "limit": 300}, "show": True},
    {"sid": "em_dt_pool",     "kw": {"date": "", "limit": 300}, "show": True},
    {"sid": "em_yzt_pool",    "kw": {"date": "", "limit": 300}, "show": True},
    {"sid": "eastmoney_lhb",  "kw": {"date": "", "limit": 300}, "show": True},
    {"sid": "northbound",     "kw": {},                          "show": True},
    {"sid": "em_hot_rank",    "kw": {"limit": 50},               "show": True},
    {"sid": "ths_hot_list",   "kw": {"limit": 50},               "show": True},
    {"sid": "industry_rank",  "kw": {"limit": 100},              "show": True},
    {"sid": "ths_hot_reason", "kw": {"date": "", "limit": 20},   "show": False},
]

_LOCK = threading.Lock()
_REFRESH_INFLIGHT = [False]
_MEM_CACHE: Dict[str, Any] = {"data": None}
_STREAK_RE = re.compile(r"(\d+)板")


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache_atomic(data: Dict[str, Any]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _CACHE_PATH)


def _derive(sources: Dict[str, Any]) -> Dict[str, Any]:
    def rows(alias: str) -> List[dict]:
        return (sources.get(_lc.resolve_source(alias) or alias) or {}).get("rows") or []
    zt, zb, dt = rows("em_zt_pool"), rows("em_zb_pool"), rows("em_dt_pool")
    north = rows("northbound")
    streaks, breaks = [], 0
    for r in zt:
        m = _STREAK_RE.search(str(r.get("zt_stat") or ""))
        streaks.append(int(m.group(1)) if m else int(r.get("limit_days") or 1))
        if int(r.get("break_times") or 0) > 0:
            breaks += 1
    d: Dict[str, Any] = {"zt_count": len(zt), "zb_count": len(zb), "dt_count": len(dt),
                         "max_streak": max(streaks) if streaks else 0,
                         "break_ratio": round(breaks / len(zt), 4) if zt else 0.0,
                         "north_net": None}
    if north and isinstance(north[0], dict):     # 北向净额字段名多变,多候选探测,缺则 None
        for k in ("net", "net_inflow", "north_net", "成交净买额", "净买额"):
            v = north[0].get(k)
            if v is not None:
                try:
                    d["north_net"] = float(v)
                    break
                except (TypeError, ValueError):
                    pass
    return d


def _refresh(ttl_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """逐源经 live_client.probe 拉取(沿用其跨启动节流),原子落盘。
    失败源保留上一轮条目(局部陈旧诚实显形,不整份作废)。"""
    prev = _load_cache() or {}
    prev_sources = prev.get("sources") or {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    sources: Dict[str, Any] = {}
    for spec in _SOURCES:
        alias = spec["sid"]
        canon = _lc.resolve_source(alias) or alias
        try:
            r = _lc.probe(alias, **spec["kw"])
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "note": f"{type(exc).__name__}: {exc}"}
        if r.get("ok") and r.get("status") in ("ok", ""):
            rows = _lc.native_rows(r.get("items"))
            sources[canon] = {"status": r.get("status") or "ok",
                              "n": int(r.get("n") or len(rows)),
                              "pulled_at": r.get("pulled_at") or now_iso,
                              "note": r.get("note") or "", "rows": rows}
        else:   # 本轮失败/planned/error → 保留上一轮该源
            note = (r.get("note") or r.get("error") or "本轮拉取失败")
            old = prev_sources.get(canon)
            if old:
                kept = dict(old)
                kept["note"] = f"(旧){old.get('note') or ''}|新失败:{note}"[:400]
                sources[canon] = kept
            else:
                sources[canon] = {"status": "error", "n": 0, "pulled_at": None,
                                  "note": note, "rows": []}
    pulled_list = [v["pulled_at"] for v in sources.values() if v.get("pulled_at")]
    overall = max(pulled_list) if pulled_list else prev.get("pulled_at")
    data = {"pulled_at": overall, "ttl_s": ttl_s, "sources": sources, "derived": _derive(sources)}
    _write_cache_atomic(data)
    _MEM_CACHE["data"] = data
    return data


def _freshness(data: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now()

    def age(iso: Any) -> Optional[int]:
        try:
            return int((now - datetime.fromisoformat(str(iso))).total_seconds())
        except (TypeError, ValueError):
            return None
    per = {sid: age(v.get("pulled_at")) for sid, v in (data.get("sources") or {}).items()}
    return {"overall_age_s": age(data.get("pulled_at")), "per_source": per}


def _trigger_refresh(ttl_s: int = _DEFAULT_TTL_S) -> bool:
    """单飞:已有刷新在跑则返回 False;否则起 daemon 线程后台刷新。"""
    with _LOCK:
        if _REFRESH_INFLIGHT[0]:
            return False
        _REFRESH_INFLIGHT[0] = True

    def _run() -> None:
        try:
            _refresh(ttl_s)
        except Exception:  # noqa: BLE001 — 后台刷新失败绝不冒泡
            pass
        finally:
            with _LOCK:
                _REFRESH_INFLIGHT[0] = False
    threading.Thread(target=_run, name="market_tape_refresh", daemon=True).start()
    return True


def read_tape(fresh_within_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """SWR 只读:读永远秒回缓存;缺失→warming+触发首拉;过期→返回旧值+触发后台刷新。
    绝不阻塞在网络、绝不伪造新鲜(freshness 龄期显形)。"""
    data = _MEM_CACHE.get("data") or _load_cache()
    if not data:
        _trigger_refresh(fresh_within_s)
        return {"ok": True, "warming": True, "pulled_at": None, "sources": {}, "derived": {},
                "freshness": {"overall_age_s": None, "per_source": {}, "stale": True},
                "note": "预热中,后台首拉已触发;稍后重试"}
    _MEM_CACHE["data"] = data
    fr = _freshness(data)
    stale = fr["overall_age_s"] is None or fr["overall_age_s"] > fresh_within_s
    fr["stale"] = bool(stale)
    note = ""
    if stale:
        _trigger_refresh(fresh_within_s)
        note = "缓存过期,已触发后台刷新;本次返回现有值(龄期见 freshness)"
    return {"ok": True, "warming": False, "pulled_at": data.get("pulled_at"),
            "sources": data.get("sources") or {}, "derived": data.get("derived") or {},
            "freshness": fr, "note": note}


__all__ = ["read_tape", "_refresh", "_derive"]
