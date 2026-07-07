# -*- coding: utf-8 -*-
"""落子 K 线新闻标记装配器 —— 回测态 PIT(pit_store/PitReader)+ 实时态(KuaixunNewsProvider)。

红线:回测绝不返回 as-of 之后的新闻(ts≤boundary / ann_date≤date,双由 PitReader 保证);
失败/无数据 → 空 items + note,恒 ok:True,绝不编造。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_READER: Any = None      # 懒单例 PitReader(构造含日历/探针,较重 → 复用)
_LIVE: Any = None        # 懒单例 KuaixunNewsProvider
# 富层(公告/政策)parquet:env 可覆写(stocks 把 _news_staging 迁到 canonical text_source 时
# 改配置不改码;缺/读失败 → 该路静默空,rich_available:false,主链不受影响)。
_STOCKS_NEWS_PARQUET = Path(os.environ.get(
    "GUANLAN_NEWS_EVENTS_PARQUET", r"G:\stocks\_news_staging\normalized\news_events.parquet"))


def _norm_code(code: str) -> str:
    s = re.sub(r"\D", "", str(code or ""))
    if len(s) != 6:
        return str(code or "").upper()
    up = str(code).upper()
    if up.startswith(("SH", "SZ", "BJ")):
        return up[:2] + s
    return ("SH" if s[0] == "6" else "SZ") + s


def _get_reader():
    global _READER
    if _READER is None:
        from financial_analyst.backtest.pit_reader import PitReader
        _READER = PitReader()
    return _READER


def _get_live():
    global _LIVE
    if _LIVE is None:
        from financial_analyst.watch.news import KuaixunNewsProvider
        _LIVE = KuaixunNewsProvider()
    return _LIVE


def _load_meta(root) -> dict:
    try:
        return json.loads((Path(root) / "_meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _head(s, n: int = 120) -> str:
    return str(s or "").strip().replace("\n", " ")[:n]


def _normalize(vi, norm: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for it in vi.news:
        items.append({"ts": it.ts, "date": it.date, "title": it.title,
                      "source": it.source, "code": it.code,
                      "level": "stock" if it.code == norm else "macro",
                      "body_head": _head(it.body)})
    for it in vi.policy:
        items.append({"ts": it.ts, "date": it.pub_date, "title": it.title,
                      "source": it.source, "code": it.code, "level": "policy",
                      "body_head": _head(it.summary)})
    for it in vi.events:
        vts = (it.fields or {}).get("visible_ts") or (str(it.ann_date) + "T00:00:00")
        items.append({"ts": vts, "date": it.ann_date,
                      "title": it.summary or it.type, "source": it.source,
                      "code": it.code, "level": "event", "body_head": _head(it.summary)})
    items.sort(key=lambda r: str(r.get("ts") or ""))
    return items


def _assemble_pit(code: str, asof: str, window: int, reader=None) -> dict:
    norm = _norm_code(code)
    asof = str(asof or "")
    base = {"ok": True, "code": norm, "mode": "pit", "asof": asof}
    if len(asof) < 10:
        return {**base, "items": [], "coverage": {"partial": False, "note": "缺 as-of"},
                "provenance": {"source": "pit_store", "rows": 0}}
    day = asof[:10]
    tm = asof[11:16] if len(asof) >= 16 else "15:00"
    rdr = reader or _get_reader()
    try:
        vi = rdr.get_visible_info(day, codes=[norm], as_of=tm,
                                  lookback_days=max(1, min(int(window or 250), 400)),
                                  include=("news", "events", "policy"))
        items = _normalize(vi, norm)
    except Exception as exc:  # noqa: BLE001 — 诚实降级,绝不 500
        return {**base, "items": [], "coverage": {"partial": False, "note": f"读取失败: {type(exc).__name__}"},
                "provenance": {"source": "pit_store", "rows": 0}}
    meta = _load_meta(getattr(rdr, "_root", None))  # getattr:_root 私有属性防御,缺失也不崩(诚实降级红线)
    floor = meta.get("news_coverage_floor")
    rng = [meta.get("cal_start"), meta.get("cal_end")]
    partial = bool(floor and day < floor)
    note = ""
    if partial:
        note = f"{floor} 之前语料稀疏,覆盖不全"
    elif rng[1] and day > str(rng[1]):
        note = "超出 pit_store 覆盖范围"
    return {**base, "items": items,
            "coverage": {"floor": floor, "range": rng, "partial": partial, "note": note},
            "provenance": {"source": "pit_store", "rows": len(items)}}


def _stock_name(norm: str) -> str:
    """本票名称(快讯名称匹配用);失败→''(只按 code 匹配,诚实降级)。"""
    try:
        from financial_analyst.data.stock_basic import get_basic
        return str((get_basic(norm) or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        return ""


def _t16(s) -> str:
    """'2026-07-04 09:31[:00]' / Timestamp → '2026-07-04T09:31';空/坏→''。"""
    t = str(s or "").strip().replace(" ", "T")[:16]
    return t if len(t) >= 10 else ""


def _assemble_live(code: str, provider=None, *, stock_news_fn=None,
                   kuaixun_fn=None, parquet_path=None, limit: int = 20) -> dict:
    norm = _norm_code(code)
    pulled_at = datetime.now().isoformat(timespec="seconds")
    # —— 兼容:显式传 provider(既有测试/旧调用)→ 维持原 headlines 语义,不走三路 ——
    if provider is not None:
        try:
            heads = provider.headlines(code) or []
        except Exception:  # noqa: BLE001
            heads = []
        items = [{"ts": "", "date": "", "title": h, "source": "eastmoney_kuaixun",
                  "code": norm, "level": "stock", "body_head": ""} for h in heads]
        return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": items,
                "coverage": {"partial": False, "note": ""},
                "freshness": {"pulled_at": pulled_at, "rich_asof": None, "rich_available": False},
                "provenance": {"source": "kuaixun", "rows": len(items)}}
    notes: List[str] = []
    stock_items: List[Dict[str, Any]] = []
    broad_items: List[Dict[str, Any]] = []
    # ① akshare 东财个股新闻(引擎现成 fetch_stock_news;其自身失败已降级 [])
    try:
        fn = stock_news_fn
        if fn is None:
            from financial_analyst.data.news_pulse import fetch_stock_news as fn
        rows = fn(norm, limit=max(int(limit or 20), 20)) or []
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        notes.append("个股新闻源不可用或无条目")
    for r in rows:
        ts = _t16(r.get("time"))
        stock_items.append({"ts": ts, "date": ts[:10], "title": str(r.get("title") or "").strip(),
                            "source": str(r.get("source") or "") or "eastmoney_stock_news",
                            "code": norm, "level": "stock", "body_head": _head(r.get("summary"))})
    # ② 东财 7×24 快讯(codes 已 qlib 归一):命中本票(code或名)→stock,其余→macro 补位
    try:
        kfn = kuaixun_fn
        if kfn is None:
            from financial_analyst.data.news_pulse import fetch_kuaixun as kfn
        flash = kfn(limit=200) or []
    except Exception:  # noqa: BLE001
        flash = []
        notes.append("快讯源不可用")
    name = _stock_name(norm)
    for it in flash:
        ts = _t16(it.get("time"))
        title = str(it.get("title") or "").strip()
        hit = (norm in (it.get("codes") or [])) or (
            bool(name) and (name in title or name in str(it.get("summary") or "")))
        rec = {"ts": ts, "date": ts[:10], "title": title, "source": "eastmoney_kuaixun",
               "code": norm if hit else None, "level": "stock" if hit else "macro",
               "body_head": _head(it.get("summary"))}
        (stock_items if hit else broad_items).append(rec)
    # ③ stocks 富 parquet(公告/政策)可选加菜:纯文件读、近3日窗;缺/错→rich_available False
    rich_asof = None
    rich_available = False
    ppath = Path(parquet_path) if parquet_path else _STOCKS_NEWS_PARQUET
    try:
        if ppath.exists():
            import pandas as pd
            df = pd.read_parquet(ppath)
            rich_asof = _t16(df["publish_ts"].max())
            rich_available = True
            cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            recent = df[df["publish_ts"].astype(str) >= cutoff].copy()
            sc = recent["stock_codes"].astype(str)
            mine = recent[sc.str.contains(norm, na=False)]
            pol = recent[recent["is_policy"].fillna(False).astype(bool)
                         & ~sc.str.contains(norm, na=False)]
            for _, r in mine.iterrows():
                ts = _t16(r.get("publish_ts"))
                stock_items.append({"ts": ts, "date": ts[:10],
                                    "title": str(r.get("title") or "").strip(),
                                    "source": str(r.get("source") or "stocks_staging"),
                                    "code": norm, "level": "event",
                                    "body_head": _head(r.get("content"))})
            for _, r in pol.iterrows():
                ts = _t16(r.get("publish_ts"))
                broad_items.append({"ts": ts, "date": ts[:10],
                                    "title": str(r.get("title") or "").strip(),
                                    "source": str(r.get("source") or "stocks_staging"),
                                    "code": None, "level": "policy",
                                    "body_head": _head(r.get("content"))})
        else:
            notes.append("stocks 富 parquet 不在,公告/政策层缺席")
    except Exception:  # noqa: BLE001
        rich_available = False
        notes.append("stocks 富 parquet 读取失败")
    # 去重(①②同源标题重叠;seen 跨两组共享)→ 本票优先补位 → 展示按 ts 降序
    seen: set = set()

    def _dedup(arr):
        out = []
        for x in arr:
            if x["title"] and x["title"] not in seen:
                seen.add(x["title"])
                out.append(x)
        return out

    stock_items = _dedup(sorted(stock_items, key=lambda x: x["ts"], reverse=True))
    broad_items = _dedup(sorted(broad_items, key=lambda x: x["ts"], reverse=True))
    lim = max(1, int(limit or 20))
    picked = stock_items[:lim]
    if len(picked) < lim:
        picked += broad_items[:lim - len(picked)]
    picked.sort(key=lambda x: x["ts"], reverse=True)
    return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": picked,
            "coverage": {"partial": False, "note": ";".join(notes)},
            "freshness": {"pulled_at": pulled_at, "rich_asof": rich_asof,
                          "rich_available": rich_available},
            "provenance": {"source": "stock_news+kuaixun+staging_parquet", "rows": len(picked)}}


def assemble_news_marks(code: str, asof: str = "", mode: str = "pit",
                        window: int = 250, *, reader=None, provider=None,
                        stock_news_fn=None, kuaixun_fn=None,
                        parquet_path=None, limit: int = 20) -> dict:
    if mode == "live":
        return _assemble_live(code, provider=provider, stock_news_fn=stock_news_fn,
                              kuaixun_fn=kuaixun_fn, parquet_path=parquet_path, limit=limit)
    return _assemble_pit(code, asof, window, reader=reader)
