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


def _theme_temp(markets, anchors, min_volume=None):
    """锚定温度;返回 (temp|None, hits, hit_ids)。markets 须已按 volume 降序,每锚吃首个命中。

    hit_ids 供展示层把合成温度的市场提进列表——否则温度由页面上看不见的市场算出,
    读者无从核对(诚实红线:凡显形的数字,其依据必须可见)。

    min_volume 按源分别设门槛(如 {"polymarket": 5000, "kalshi": 0}):预测市场的概率
    只在有人真金白银下注时才有信息量,$11 成交额的市场不配决定一个主题的温度。
    按源分别配置是因为两家的 volume 字段语义不同(PM=volume24hr,Kalshi=liquidity_dollars),
    量纲不可比,统一门槛会把 Kalshi 锚点全数误杀。
    """
    floors = min_volume or {}
    tot_w, acc, hit_ids = 0.0, 0.0, []
    for a in anchors or []:
        needle = str(a.get("match", "")).lower()
        if not needle:
            continue
        for m in markets:
            if needle not in m["question"].lower() and needle not in m["id"].lower():
                continue
            if (m.get("volume") or 0) < float(floors.get(m.get("source"), 0)):
                continue  # 流动性不足,继续找同锚的下一个命中
            w = float(a.get("weight", 1.0))
            d = int(a.get("direction", 0))
            acc += w * d * (m["prob"] - 0.5)
            tot_w += w
            hit_ids.append(m["id"])
            break
    if tot_w <= 0:
        return None, 0, []
    return round(max(0.0, min(100.0, 50.0 + 50.0 * acc / tot_w)), 1), len(hit_ids), hit_ids


def _anchor_matched_ignoring_volume(markets, anchors) -> bool:
    """是否有锚点在忽略流动性门槛时能命中(用于区分「措辞不匹配」与「流动性不足」)。"""
    for a in anchors or []:
        needle = str(a.get("match", "")).lower()
        if needle and any(needle in m["question"].lower() or needle in m["id"].lower()
                          for m in markets):
            return True
    return False


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


def build_pulse(refresh: bool = False, snapshot_path=None, astock_fn=None, http=None,
                translate_fn=None) -> dict:
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
    vol_floors = cfg.get("anchor_min_volume") or {}
    notes, themes_out, all_markets, temps = [], [], [], {}
    for t in cfg.get("themes") or []:
        pm_rows, pm_notes = sources.fetch_polymarket(t.get("polymarket_tags") or [], http=http)
        k_rows, k_notes = sources.fetch_kalshi(t.get("kalshi_series") or [], http=http)
        notes.extend(pm_notes + k_notes)
        rows = sorted(pm_rows + k_rows, key=lambda m: m.get("volume") or 0, reverse=True)
        anchors = t.get("anchors") or []
        temp, hits, hit_ids = _theme_temp(rows, anchors, min_volume=vol_floors)
        temps[t["id"]] = temp
        # 声明了锚点却一个没命中:静默显示 "—" 会把「配置写错」或「市场没人交易」
        # 伪装成「无数据」。两种病因排查方向不同,告警必须区分。
        if anchors and hits == 0:
            if _anchor_matched_ignoring_volume(rows, anchors):
                notes.append(f"主题 {t['id']} 的锚定市场全部因流动性不足被拒"
                             f"(门槛 {vol_floors}),温度显示 —;这些市场概率无信息量")
            else:
                notes.append(f"主题 {t['id']} 声明了 {len(anchors)} 个锚定市场但当前一个都没命中"
                             f"(池中 {len(rows)} 个市场),温度显示 —;请核对 themes.yaml 的 match 措辞")
        # 展示 = 量前 top_n ∪ 锚定命中市场(后者常是低量尾部市场,但温度由它们合成,
        # 必须可见可核);顺序仍按量降序,锚定行带 is_anchor 徽章。
        hit_set = set(hit_ids)
        picked = [m for m in rows[:top_n]]
        picked += [m for m in rows if m["id"] in hit_set and m not in picked]
        picked.sort(key=lambda m: m.get("volume") or 0, reverse=True)
        shown = []
        for m in picked:
            row = dict(m)
            row["theme"] = t["id"]
            row["is_anchor"] = m["id"] in hit_set
            row["delta24h"] = _delta24h(m["id"], m["prob"], snapshots, now)
            shown.append(row)
            all_markets.append(row)
        themes_out.append({"id": t["id"], "label": t["label"], "temp": temp,
                           "anchor_hits": hits, "markets": shown})

    # 中文翻译层(缓存优先,一次批量;失败英文回落+note,绝不拖垮主体)
    if translate_fn is None:
        from .translate import translate_questions as translate_fn
    try:
        zh_map, zh_note = translate_fn([m["question"] for m in all_markets])
    except Exception as e:  # noqa: BLE001
        zh_map, zh_note = {}, f"翻译层异常: {type(e).__name__}: {e}"
    if zh_note:
        notes.append(zh_note)
    for m in all_markets:
        z = zh_map.get(m["question"])
        if z:
            m["question_zh"] = z

    # A 股侧:默认走 astock.build_astock(stocks probe);失败降级不拖垮全球侧
    if astock_fn is None:
        from .astock import build_astock as astock_fn
    try:
        astock = astock_fn()
    except Exception as e:
        astock = {"available": False, "temp": None,
                  "notes": [f"astock 侧异常: {type(e).__name__}: {e}"]}
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
