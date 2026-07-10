# -*- coding: utf-8 -*-
"""板块资金流聚合 + 快照沉淀。母版 macro/pulse.py。

现拉当前档(concept|industry)画板块图/排行;每次同时拉行业档做大盘分解与全A涨跌
(行业板块=全市场互斥全覆盖划分,加总=全市场);概念/行业涨跌数=各档板块涨跌计数。
每次真拉且(交易时段或显式 refresh)则向 var/fundflow/<当日>.jsonl 追加 concept+industry 两行快照。
纯展示,绝不回写信号。"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import sources

_SNAP_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "fundflow"
_LIVE_CACHE_DEFAULT = Path(__file__).resolve().parents[2] / "var" / "live"
_LIVE_TTL_S = int(os.environ.get("GUANLAN_FUNDFLOW_TTL_S", "180"))
_live_lock = threading.Lock()
_live_inflight: dict[str, bool] = {}   # kind -> 是否有后台刷新在跑(单飞)


def _is_trading(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 60 + dt.minute
    return (9 * 60 + 30) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


def _snapshot_path(snapshot_dir, dt: datetime) -> Path:
    base = Path(snapshot_dir) if snapshot_dir else _SNAP_DEFAULT
    return base / f"{dt.strftime('%Y%m%d')}.jsonl"


def _breadth_count(rows: list) -> dict:
    """板块级涨跌计数(数的是板块个数,不涉股票重叠,故正确)。"""
    up = sum(1 for r in rows if float(r.get("change_pct") or 0) > 0)
    down = sum(1 for r in rows if float(r.get("change_pct") or 0) < 0)
    return {"up": up, "down": down}


# 全A 涨跌家数是**股票级**计数,无法由板块 up_count/down_count 加总得出——
# 东财 t:2 混排一/二/三级行业(航天装备Ⅱ/Ⅲ 同值重复),股票重复归属,
# 真机加总 up+down=16545 >> A股约 5400。乐咕 legu 源已失效,暂无独立源。
# 按 spec §4.3 兜底C:诚实置 None + note,绝不用板块数冒充全A。挂账待接源。
_ALLA_UNAVAILABLE_NOTE = "全A 涨跌家数暂无独立源(板块重叠不可加总,乐咕源失效),已挂账"


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
               sector_fn=None, market_fn=None, now=None) -> dict:
    """当前档板块图/排行 + 大盘五档(独立源)+ 板块级涨跌数。

    大盘五档一律取自独立源 fetch_market(沪深合计 fflow),源挂则 market={} + note,
    绝不回落到「板块加总」——板块重叠会给出连符号都相反的错数(真机 +963.50亿 vs -397.91亿)。
    """
    if sector_fn is None:
        sector_fn = sources.fetch_sector
    if market_fn is None:
        market_fn = sources.fetch_market
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
                     f"该档板块涨跌数降级:{other.get('note') or '空'}")
    concept_rows = cur["rows"] if k == "concept" else other["rows"]
    industry_rows = other["rows"] if k == "concept" else cur["rows"]

    # 大盘五档:独立源。失败 → 空 + note,绝不加总冒充。
    mk = market_fn()
    if mk.get("ok"):
        market = dict(mk.get("row") or {})
    else:
        market = {}
        notes.append(f"大盘资金五档不可用:{mk.get('note') or '空'}")

    notes.append(_ALLA_UNAVAILABLE_NOTE)
    breadth = {
        "allA": {"up": None, "down": None},          # 股票级计数,板块不可加总(见模块注释)
        "industry": _breadth_count(industry_rows) if industry_rows else {"up": None, "down": None},
        "concept": _breadth_count(concept_rows) if concept_rows else {"up": None, "down": None},
    }

    # 数据源标注:板块行带 src_host(push2 被掐时为 push2delay=延时行情)
    src_host = ""
    for _r in (cur.get("rows") or []):
        if _r.get("src_host"):
            src_host = str(_r["src_host"])
            break
    if "delay" in src_host:
        notes.append("板块资金流走延时源 push2delay(push2 主节点被掐),盘中可能有延时")

    path = _snapshot_path(snapshot_dir, dt)
    first = _first_snapshot_today(path, k)
    boards = _board_view(cur["rows"], first)

    payload = {"ok": True, "kind": k, "pulled_at": dt.strftime("%Y-%m-%dT%H:%M:%S"),
               "trading": trading, "market": market, "breadth": breadth,
               "boards": boards, "src_host": src_host, "notes": notes}

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


# ── SWR 秒回层(收敛到 market_tape 已建立的范式)──────────────────────────────────
# 收口「/fundflow/live 每次真拉两个 probe 子进程(cur+other),反复刷新=反复打东财」。
# 与 market_tape 同款:磁盘缓存 + 过期后台单飞刷新;差别=冷启动阻塞首拉(2 探针~3s,一次性,
# payload 契约不变故前端零改),而非 warming 占位。TTL 内重复读全命中缓存;refresh=True 显式绕缓存。
# 纯展示,绝不回写信号。锚点用 build_live 自带的 pulled_at(失败沿用上轮=真陈旧,不伪造新鲜)。
def _norm_kind(kind: str) -> str:
    return "industry" if str(kind).lower().startswith("ind") else "concept"


def _live_cache_path(kind: str, cache_dir=None) -> Path:
    base = Path(cache_dir) if cache_dir else Path(
        os.environ.get("GUANLAN_FUNDFLOW_LIVE_DIR") or _LIVE_CACHE_DEFAULT)
    return base / f"fundflow_live_{kind}.json"


def _load_live_cache(kind: str, cache_dir=None) -> dict | None:
    try:
        d = json.loads(_live_cache_path(kind, cache_dir).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_live_cache(kind: str, data: dict, cache_dir=None) -> None:
    path = _live_cache_path(kind, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _refresh_live(kind: str, refresh: bool = False, snapshot_dir=None, cache_dir=None,
                  sector_fn=None, now=None, build_fn=None) -> dict:
    """真拉一档(经 build_live)并落缓存。ok→缓存并返;失败且有旧缓存→沿用上轮(诚实降级,
    标 note 不伪造新鲜);失败且无旧缓存→原样返失败 payload(不缓存)。"""
    build_fn = build_fn or build_live
    payload = build_fn(kind, refresh=refresh, snapshot_dir=snapshot_dir, sector_fn=sector_fn, now=now)
    k = payload.get("kind") or _norm_kind(kind)
    if payload.get("ok"):
        _write_live_cache(k, payload, cache_dir)
        return payload
    prev = _load_live_cache(k, cache_dir)
    if prev:
        kept = dict(prev)
        reason = "; ".join(payload.get("notes") or []) or "空"
        kept["notes"] = list(kept.get("notes") or []) + [f"刷新失败沿用上轮:{reason}"]
        return kept
    return payload   # 无旧缓存 → 诚实失败,不缓存


def _live_age_s(data: dict, ref_now: datetime):
    try:
        return int((ref_now - datetime.fromisoformat(str(data.get("pulled_at")))).total_seconds())
    except (TypeError, ValueError):
        return None


def _annotate_live(data: dict, ref_now: datetime, ttl: int, triggered: bool = False) -> dict:
    age = _live_age_s(data, ref_now)
    stale = age is None or age > ttl
    out = dict(data)
    out["freshness"] = {"age_s": age, "stale": bool(stale), "ttl_s": ttl}
    # 仅在「本次真触发了后台刷新」时才这么标——refresh=True 强拉路径、单飞旗已占(_trigger 返 False)
    # 都不该谎报「已触发后台刷新」(评审 minor:freshness 本就诚实,note 不再自相矛盾)。
    if stale and out.get("ok") and triggered:
        out["notes"] = list(out.get("notes") or []) + [
            "缓存过期,已触发后台刷新;本次返回现有值(龄期见 freshness)"]
    return out


def _trigger_live_refresh(kind: str, snapshot_dir=None, cache_dir=None,
                          sector_fn=None, build_fn=None) -> bool:
    """单飞:该 kind 已有刷新在跑→返 False;否则起 daemon 后台刷新(线程起不来即复位旗,不永冻)。"""
    with _live_lock:
        if _live_inflight.get(kind):
            return False
        _live_inflight[kind] = True

    def _run() -> None:
        try:
            _refresh_live(kind, refresh=False, snapshot_dir=snapshot_dir, cache_dir=cache_dir,
                          sector_fn=sector_fn, build_fn=build_fn)
        except Exception:  # noqa: BLE001 — 后台刷新失败绝不冒泡
            pass
        finally:
            with _live_lock:
                _live_inflight[kind] = False
    try:
        threading.Thread(target=_run, name=f"fundflow_live_{kind}", daemon=True).start()
    except Exception:  # noqa: BLE001
        with _live_lock:
            _live_inflight[kind] = False
        return False
    return True


def read_live(kind: str = "concept", refresh: bool = False, snapshot_dir=None,
              cache_dir=None, ttl_s=None, now=None, sector_fn=None, build_fn=None) -> dict:
    """SWR 只读门户(/fundflow/live 走它):缓存新鲜→秒回;缺失→阻塞首拉(一次性);过期→返旧值
    +触发后台单飞刷新。refresh=True 显式强拉绕缓存。TTL 内重复读绝不反复打东财。纯展示不回写。"""
    k = _norm_kind(kind)
    ttl = _LIVE_TTL_S if ttl_s is None else int(ttl_s)
    ref_now = now or datetime.now()
    if refresh:
        data = _refresh_live(k, refresh=True, snapshot_dir=snapshot_dir, cache_dir=cache_dir,
                             sector_fn=sector_fn, now=now, build_fn=build_fn)
        return _annotate_live(data, ref_now, ttl)
    cached = _load_live_cache(k, cache_dir)
    if not cached:                       # 冷启动:阻塞首拉,落缓存
        data = _refresh_live(k, refresh=False, snapshot_dir=snapshot_dir, cache_dir=cache_dir,
                             sector_fn=sector_fn, now=now, build_fn=build_fn)
        return _annotate_live(data, ref_now, ttl)
    triggered = False
    if _live_age_s(cached, ref_now) is None or _live_age_s(cached, ref_now) > ttl:
        triggered = _trigger_live_refresh(k, snapshot_dir, cache_dir, sector_fn, build_fn)
    return _annotate_live(cached, ref_now, ttl, triggered=triggered)


def _read_day(path: Path, kind: str) -> list:
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
        if isinstance(row, dict) and row.get("kind") == kind and row.get("ts"):
            out.append(row)
    return out


def load_history(kind: str = "concept", date: str = "", snapshot_dir=None,
                 top_each: int = 8) -> dict:
    k = "industry" if str(kind).lower().startswith("ind") else "concept"
    stamp = "".join(ch for ch in str(date) if ch.isdigit()) or datetime.now().strftime("%Y%m%d")
    base = Path(snapshot_dir) if snapshot_dir else _SNAP_DEFAULT
    path = base / f"{stamp}.jsonl"
    snaps = _read_day(path, k)
    if not snaps:
        return {"date": stamp, "kind": k, "ticks": [], "boards": [],
                "market_series": {"main_net": []}}
    ticks = [s["ts"] for s in snaps]
    # 选线:末快照 main_net 净流入前 top_each + 净流出前 top_each
    last = sorted(snaps[-1].get("boards", []),
                  key=lambda b: float(b.get("main_net") or 0.0), reverse=True)
    inflow = [b["name"] for b in last[:top_each]]
    outflow = [b["name"] for b in last[-top_each:] if b["name"] not in inflow]
    picked = inflow + outflow
    boards = []
    for name in picked:
        series = []
        for s in snaps:
            val = next((float(b.get("main_net")) for b in s.get("boards", [])
                        if b.get("name") == name and b.get("main_net") is not None), None)
            series.append(val)
        boards.append({"name": name, "series": series})
    market_series = {"main_net": [float((s.get("market") or {}).get("main_net") or 0.0)
                                  for s in snaps]}
    return {"date": stamp, "kind": k, "ticks": ticks, "boards": boards,
            "market_series": market_series}
