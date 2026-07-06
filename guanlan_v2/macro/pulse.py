# -*- coding: utf-8 -*-
"""全球情绪温度计聚合层:主题现拉 → 锚定温度合成 → 快照沉淀 → Δ24h 诚实读。

温度只由 themes.yaml 显式标注方向的锚定市场合成(50+50·Σ(w·dir·(prob-0.5))/Σw,
clamp 0-100,高=risk-on);其余市场只展示——不自动猜方向。快照 append-only jsonl,
每次真拉成功顺手落一行,Δ24h/历史曲线全部从快照读,无历史诚实 None。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import sources

_SNAP_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "macro_pulse" / "snapshots.jsonl"
_STALE_MAX_MIN = 24 * 60      # 非 refresh 时快照可用窗
_DELTA_MIN_AGE_H = 20         # Δ24h:取最近一条 ≥20h 前的记录


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _theme_temp(markets, anchors):
    """锚定温度;返回 (temp|None, hits)。markets 须已按 volume 降序,每锚吃首个命中。"""
    tot_w, acc, hits = 0.0, 0.0, 0
    for a in anchors or []:
        needle = str(a.get("match", "")).lower()
        if not needle:
            continue
        for m in markets:
            if needle in m["question"].lower() or needle in m["id"].lower():
                w = float(a.get("weight", 1.0))
                d = int(a.get("direction", 0))
                acc += w * d * (m["prob"] - 0.5)
                tot_w += w
                hits += 1
                break
    if tot_w <= 0:
        return None, 0
    return max(0.0, min(100.0, 50.0 + 50.0 * acc / tot_w)), hits


def _read_snapshots(path: Path):
    """逐行读快照,脏行跳过(append-only jsonl 惯例)。"""
    if not path.exists():
        return []
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("ts"):
            out.append(row)
    return out


def _delta24h(market_id: str, prob: float, snapshots, now: datetime):
    """Δ24h=当前概率-最近一条 ≥20h 前同 id 概率;无历史 None。"""
    best = None
    for snap in snapshots:
        try:
            ts = datetime.strptime(snap["ts"], "%Y-%m-%dT%H:%M:%S")
        except (KeyError, ValueError):
            continue
        if (now - ts).total_seconds() < _DELTA_MIN_AGE_H * 3600:
            continue
        for m in snap.get("markets") or []:
            if m.get("id") == market_id and isinstance(m.get("prob"), (int, float)):
                if best is None or ts > best[0]:
                    best = (ts, float(m["prob"]))
    if best is None:
        return None
    return round(prob - best[1], 4)


def _snapshot_view(snap: dict, cfg: dict, now: datetime) -> dict:
    """非 refresh 且快照够新:从快照还原 payload,stale_minutes 显形。"""
    ts = datetime.strptime(snap["ts"], "%Y-%m-%dT%H:%M:%S")
    temps = snap.get("temps") or {}
    by_theme = {}
    for m in snap.get("markets") or []:
        by_theme.setdefault(m.get("theme") or "", []).append(m)
    themes_out = []
    for t in cfg.get("themes") or []:
        tid = t["id"]
        themes_out.append({"id": tid, "label": t["label"],
                           "temp": temps.get(tid), "anchor_hits": None,
                           "markets": by_theme.get(tid, [])})
    with_temp = [v for v in temps.values() if isinstance(v, (int, float))]
    return {"ok": True, "pulled_at": snap["ts"],
            "stale_minutes": round((now - ts).total_seconds() / 60, 1),
            "thermometer": {
                "global": round(sum(with_temp) / len(with_temp), 1) if with_temp else None,
                "astock": snap.get("astock_temp")},
            "themes": themes_out,
            "astock": snap.get("astock") or {"available": False, "notes": ["快照未含A股侧"]},
            "notes": ["快照态(未现拉),点刷新取实时"]}


def build_pulse(refresh: bool = False, snapshot_path=None, astock_fn=None, http=None) -> dict:
    cfg = sources.load_themes()
    path = Path(snapshot_path) if snapshot_path else _SNAP_DEFAULT
    now = _now()
    snapshots = _read_snapshots(path)

    if not refresh and snapshots:
        latest = snapshots[-1]
        try:
            age_min = (now - datetime.strptime(latest["ts"], "%Y-%m-%dT%H:%M:%S")).total_seconds() / 60
        except ValueError:
            age_min = None
        if age_min is not None and age_min <= _STALE_MAX_MIN:
            return _snapshot_view(latest, cfg, now)

    # ── 现拉 ──
    top_n = int(cfg.get("display_top_n") or 8)
    notes, themes_out, all_markets, temps = [], [], [], {}
    for t in cfg.get("themes") or []:
        pm_rows, pm_notes = sources.fetch_polymarket(t.get("polymarket_tags") or [], http=http)
        k_rows, k_notes = sources.fetch_kalshi(t.get("kalshi_series") or [], http=http)
        notes.extend(pm_notes + k_notes)
        rows = sorted(pm_rows + k_rows, key=lambda m: m.get("volume") or 0, reverse=True)
        temp, hits = _theme_temp(rows, t.get("anchors"))
        temps[t["id"]] = temp
        shown = []
        for m in rows[:top_n]:
            row = dict(m)
            row["theme"] = t["id"]
            row["delta24h"] = _delta24h(m["id"], m["prob"], snapshots, now)
            shown.append(row)
            all_markets.append(row)
        themes_out.append({"id": t["id"], "label": t["label"], "temp": temp,
                           "anchor_hits": hits, "markets": shown})

    # A 股侧(Task 3 接默认;None=未接线,available False)
    if astock_fn is None:
        astock = {"available": False, "temp": None, "notes": []}
    else:
        astock = astock_fn()
    astock_temp = astock.get("temp")

    with_temp = [v for v in temps.values() if isinstance(v, (int, float))]
    g_temp = round(sum(with_temp) / len(with_temp), 1) if with_temp else None

    payload = {"ok": True, "pulled_at": _iso(now), "stale_minutes": None,
               "thermometer": {"global": g_temp, "astock": astock_temp},
               "themes": themes_out, "astock": astock, "notes": notes}

    # 顺手落快照:仅真拉到市场时写(全空不写,避免污染 Δ 基线)
    if all_markets:
        line = {"ts": _iso(now), "markets": all_markets, "temps": temps,
                "astock_temp": astock_temp, "astock": astock}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as e:
            payload["notes"] = notes + [f"快照落盘失败: {e}"]
    return payload


def load_history(market_id: str = "", theme: str = "", snapshot_path=None) -> list:
    """概率/温度时间序列(供前端曲线);market_id 与 theme 二选一。"""
    path = Path(snapshot_path) if snapshot_path else _SNAP_DEFAULT
    out = []
    for snap in _read_snapshots(path):
        if market_id:
            for m in snap.get("markets") or []:
                if m.get("id") == market_id:
                    out.append({"ts": snap["ts"], "prob": m.get("prob")})
                    break
        elif theme:
            temps = snap.get("temps") or {}
            if theme in temps:
                out.append({"ts": snap["ts"], "temp": temps[theme]})
    return out
