# -*- coding: utf-8 -*-
"""板块资金流聚合 + 快照沉淀。母版 macro/pulse.py。

现拉当前档(concept|industry)画板块图/排行;每次同时拉行业档做大盘分解与全A涨跌
(行业板块=全市场互斥全覆盖划分,加总=全市场);概念/行业涨跌数=各档板块涨跌计数。
每次真拉且(交易时段或显式 refresh)则向 var/fundflow/<当日>.jsonl 追加 concept+industry 两行快照。
纯展示,绝不回写信号。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import sources

_SNAP_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "fundflow"


def _is_trading(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


def _snapshot_path(snapshot_dir, dt: datetime) -> Path:
    base = Path(snapshot_dir) if snapshot_dir else _SNAP_DEFAULT
    return base / f"{dt.strftime('%Y%m%d')}.jsonl"


def _market_from(rows: list) -> dict:
    out = {"super_net": 0.0, "large_net": 0.0, "mid_net": 0.0, "small_net": 0.0}
    for r in rows:
        for k in out:
            out[k] += float(r.get(k) or 0.0)
    out["main_net"] = out["super_net"] + out["large_net"]
    return out


def _breadth_count(rows: list) -> dict:
    up = sum(1 for r in rows if float(r.get("change_pct") or 0) > 0)
    down = sum(1 for r in rows if float(r.get("change_pct") or 0) < 0)
    return {"up": up, "down": down}


def _allA_from(industry_rows: list) -> dict:
    return {"up": sum(int(r.get("up_count") or 0) for r in industry_rows),
            "down": sum(int(r.get("down_count") or 0) for r in industry_rows)}


def _first_snapshot_today(path: Path, kind: str) -> dict | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("kind") == kind:
                return row
    except OSError:
        return None
    return None


def _board_view(rows: list, first_snap: dict | None) -> list:
    ranked = sorted(rows, key=lambda r: float(r.get("main_net") or 0.0), reverse=True)
    base = {}
    if first_snap:
        base = {b.get("name"): float(b.get("main_net") or 0.0) for b in first_snap.get("boards", [])}
    out = []
    for i, r in enumerate(ranked):
        name = r.get("name")
        delta = (float(r.get("main_net") or 0.0) - base[name]) if name in base else None
        out.append({"code": r.get("code"), "name": name,
                    "main_net": float(r.get("main_net") or 0.0),
                    "change_pct": float(r.get("change_pct") or 0.0),
                    "rank": i + 1, "delta_intraday": delta})
    return out


def _snap_boards(rows: list) -> list:
    return [{"code": r.get("code"), "name": r.get("name"),
             "main_net": float(r.get("main_net") or 0.0),
             "change_pct": float(r.get("change_pct") or 0.0)} for r in rows]


def build_live(kind: str = "concept", refresh: bool = False, snapshot_dir=None,
               sector_fn=None, now=None) -> dict:
    if sector_fn is None:
        sector_fn = sources.fetch_sector
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    dt = now or datetime.now()
    trading = _is_trading(dt)
    notes: list[str] = []

    cur = sector_fn(k)
    if not cur.get("ok"):
        notes.append(f"{k} 档板块资金流不可用:{cur.get('note') or '空'}")
        return {"ok": False, "kind": k, "pulled_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "trading": trading, "market": {}, "breadth": {}, "boards": [], "notes": notes}
    other = sector_fn("industry" if k == "concept" else "concept")
    if not other.get("ok"):
        notes.append(f"{'industry' if k=='concept' else 'concept'} 档缺失,"
                     f"大盘分解/全A涨跌降级:{other.get('note') or '空'}")
    concept_rows = cur["rows"] if k == "concept" else other["rows"]
    industry_rows = other["rows"] if k == "concept" else cur["rows"]

    market = _market_from(industry_rows) if industry_rows else {}
    breadth = {
        "allA": _allA_from(industry_rows) if industry_rows else {"up": None, "down": None},
        "industry": _breadth_count(industry_rows) if industry_rows else {"up": None, "down": None},
        "concept": _breadth_count(concept_rows) if concept_rows else {"up": None, "down": None},
    }

    path = _snapshot_path(snapshot_dir, dt)
    first = _first_snapshot_today(path, k)
    boards = _board_view(cur["rows"], first)

    payload = {"ok": True, "kind": k, "pulled_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
               "trading": trading, "market": market, "breadth": breadth,
               "boards": boards, "notes": notes}

    # 落点:真拉到且(交易时段 或 显式 refresh);concept+industry 各落一行
    if trading or refresh:
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                if concept_rows:
                    f.write(json.dumps({"ts": ts, "kind": "concept", "market": market,
                                        "breadth": breadth, "boards": _snap_boards(concept_rows)},
                                       ensure_ascii=False) + "\n")
                if industry_rows:
                    f.write(json.dumps({"ts": ts, "kind": "industry", "market": market,
                                        "breadth": breadth, "boards": _snap_boards(industry_rows)},
                                       ensure_ascii=False) + "\n")
        except OSError as e:
            payload["notes"].append(f"快照落盘失败: {e}")
    return payload
