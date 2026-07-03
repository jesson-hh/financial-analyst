# -*- coding: utf-8 -*-
"""落子 K 线新闻标记装配器 —— 回测态 PIT(pit_store/PitReader)+ 实时态(KuaixunNewsProvider)。

红线:回测绝不返回 as-of 之后的新闻(ts≤boundary / ann_date≤date,双由 PitReader 保证);
失败/无数据 → 空 items + note,恒 ok:True,绝不编造。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_READER: Any = None      # 懒单例 PitReader(构造含日历/探针,较重 → 复用)
_LIVE: Any = None        # 懒单例 KuaixunNewsProvider


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
    meta = _load_meta(rdr._root)
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


def _assemble_live(code: str, provider=None) -> dict:
    norm = _norm_code(code)
    prov = provider or _get_live()
    try:
        heads = prov.headlines(code) or []
    except Exception:  # noqa: BLE001
        heads = []
    items = [{"ts": "", "date": "", "title": h, "source": "eastmoney_kuaixun",
              "code": norm, "level": "stock", "body_head": ""} for h in heads]
    return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": items,
            "coverage": {"partial": False, "note": ""},
            "provenance": {"source": "kuaixun", "rows": len(items)}}


def assemble_news_marks(code: str, asof: str = "", mode: str = "pit",
                        window: int = 250, *, reader=None, provider=None) -> dict:
    if mode == "live":
        return _assemble_live(code, provider=provider)
    return _assemble_pit(code, asof, window, reader=reader)
