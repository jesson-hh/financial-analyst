# -*- coding: utf-8 -*-
"""环节聚合:量化侧(Task 7 追加文本侧与 board 组装)。

一切产物缺失 → 字段 None + reason,绝不静默补零(诚实红线)。
"""
from __future__ import annotations

from typing import Optional


def _fetch_quotes(codes: list, days: int = 45) -> dict:
    """真数据路径:引擎 loader 逐票取(照 seats/api.py:784-812 先例)。单票失败跳过。"""
    out: dict = {}
    try:
        import pandas as pd
        from financial_analyst.data import loader_factory as _lf
        loader = _lf.get_default_loader()
        end = str(pd.Timestamp.now().date())
        start = str((pd.Timestamp.now() - pd.Timedelta(days=days + 30)).date())
        for c in codes:
            try:
                df = loader.fetch_quote(c, start, end, "day")
                if df is not None and len(df) and "close" in df.columns:
                    out[c] = df
            except Exception:  # noqa: BLE001 — 单票失败=该票缺
                continue
    except Exception:  # noqa: BLE001 — loader 整体失败=全缺
        return {}
    return out


def _v4_pct_map() -> Optional[dict]:
    """{code: pct(0-100)};列名/量纲归一走单一入口 strategy.ranking.v4_pct_map
    (与 rescore.v4_pool 同源,防口径漂移)。缺产物/缺列 → None(诚实降级)。"""
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
        from guanlan_v2.strategy.ranking import v4_pct_map
        return v4_pct_map(pd.read_parquet(V4_RANKING_PARQUET))
    except Exception:  # noqa: BLE001
        return None


def _fundflow_map() -> Optional[dict]:
    """近5日主力净流入 {code: 合计};文件缺/列不识 → None(诚实降级)。列名以实测 rename。"""
    try:
        import os
        import pandas as pd
        from pathlib import Path
        root = Path(os.environ.get("GL_PARQUET_ROOT") or r"G:/stocks/stock_data/parquet")
        p = root / "stock_fund_flow_daily.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        # 实现时实测列名后 rename 成 code/date/main_net;不识则返回 None
        cols = {c.lower(): c for c in df.columns}
        codec = cols.get("code") or cols.get("ts_code") or cols.get("stock_code")
        datec = cols.get("date") or cols.get("trade_date")
        netc = cols.get("main_net") or cols.get("main_net_inflow") or cols.get("主力净流入")
        if not (codec and datec and netc):
            return None
        df = df.rename(columns={codec: "code", datec: "date", netc: "main_net"})
        df["date"] = df["date"].astype(str).str[:10]
        last5 = sorted(df["date"].unique())[-5:]
        sub = df[df["date"].isin(last5)]
        return sub.groupby("code")["main_net"].sum().to_dict()
    except Exception:  # noqa: BLE001
        return None


def _eqw_ret20() -> Optional[float]:
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import EQW_MARKET_RET_PARQUET
        df = pd.read_parquet(EQW_MARKET_RET_PARQUET)
        retcol = "ret" if "ret" in df.columns else df.columns[-1]
        r = df[retcol].astype(float).tail(20)
        if len(r) < 20:
            return None
        return float((1 + r).prod() - 1)
    except Exception:  # noqa: BLE001
        return None


def quant_signals(fw: dict, quotes: Optional[dict] = None) -> dict:
    import numpy as np

    all_codes = sorted({x["code"] for s in fw["segments"] if not s.get("adjacent") for x in s.get("stocks", [])})
    if quotes is None:
        quotes = _fetch_quotes(all_codes)
    v4map = _v4_pct_map()
    eqw20 = _eqw_ret20()
    ffmap = _fundflow_map()

    out: dict = {}
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        codes = [x["code"] for x in s.get("stocks", [])]
        moms, amts5, amts20, v4s = [], [], [], []
        qdate = None
        for c in codes:
            df = quotes.get(c)
            if df is None or len(df) < 21:
                continue
            close = df["close"].astype(float).to_numpy()
            moms.append(close[-1] / close[-21] - 1.0)
            if "amount" in df.columns:
                amt = df["amount"].astype(float).to_numpy()
                if len(amt) >= 20:
                    amts5.append(float(amt[-5:].mean()))
                    amts20.append(float(amt[-20:].mean()))
            if "trade_date" in df.columns:
                qdate = max(qdate or "", str(df["trade_date"].iloc[-1])[:10])
            if v4map:
                hit = v4map.get(c) or v4map.get(c[2:]) or v4map.get(f"{c[2:]}.{c[:2]}")
                if hit is not None:
                    v4s.append(float(hit))
        if not moms:
            out[s["id"]] = {"momentum20": None, "excess20": None, "amount_share_delta20": None,
                            "fundflow5": None, "v4_pct_mean": None, "breadth": None, "quote_date": None,
                            "reason": "票池行情不可得"}
            continue
        mom = float(np.mean(moms))
        ff = None
        if ffmap:
            hits = [ffmap.get(c) or ffmap.get(c[2:]) or ffmap.get(f"{c[2:]}.{c[:2]}") for c in codes]
            hits = [h for h in hits if h is not None]
            ff = float(np.sum(hits)) if hits else None
        out[s["id"]] = {
            "momentum20": mom,
            "excess20": (mom - eqw20) if eqw20 is not None else None,
            "amount_share_delta20": (float(np.sum(amts5) / np.sum(amts20)) - 1.0) if amts20 and np.sum(amts20) > 0 else None,
            "fundflow5": ff,
            "v4_pct_mean": (float(np.mean(v4s)) if v4s else None),
            "breadth": float(np.mean([1.0 if m > 0 else 0.0 for m in moms])),
            "quote_date": qdate,
            "reason": None if (eqw20 is not None and v4map and ffmap) else "部分产物缺失(eqw/v4/资金流)→对应字段null",
        }
    return out


# ── 文本侧 + board 组装(Task 7)────────────────────────────────

_STANCE_VAL = {"多": 1.0, "中": 0.0, "空": -1.0}
_BOARD_CACHE: dict = {}
_BOARD_TTL = 600.0


def _dedupe_latest(extractions: list) -> list:
    """同 doc_id 保留 extracted_at 最新一条(失败重跑会产生重复)。"""
    best: dict = {}
    for rec in extractions:
        k = rec.get("doc_id")
        if k not in best or str(rec.get("extracted_at") or "") > str(best[k].get("extracted_at") or ""):
            best[k] = rec
    return list(best.values())


def _age_days(ts, now) -> float:
    import pandas as pd
    try:
        return max(0.0, (now - pd.Timestamp(str(ts)[:10])).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        return 9e9


def research_signals(fw: dict, extractions: list, now=None) -> dict:
    import numpy as np
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    out = {s["id"]: {"score": 0.0, "n30": 0, "bull": 0, "bear": 0, "neutral": 0, "vals": [],
                     "orgs": set(), "rating_up": 0, "rating_dn": 0, "fc_up": 0, "fc_dn": 0}
           for s in fw["segments"] if not s.get("adjacent")}
    for rec in _dedupe_latest(extractions):
        age = _age_days(rec.get("publish_ts"), now)
        if age > 30:
            continue
        decay = 0.5 ** (age / 7.0)
        # 文级硬指标(2026-07-03 扩展):评级变化/盈利修正方向,归到该文触达的每个环节
        rc = (rec.get("report_meta") or {}).get("rating_change")
        frs = rec.get("forecast_revisions") or []
        fc_up = sum(1 for f in frs if f.get("direction") in ("上调", "新增"))
        fc_dn = sum(1 for f in frs if f.get("direction") == "下调")
        for seg in rec.get("segments", []):
            sid = seg.get("segment_id")
            if sid not in out:
                continue
            v = _STANCE_VAL.get(seg.get("stance"), 0.0)
            out[sid]["score"] += v * float(seg.get("strength", 1)) * decay
            out[sid]["n30"] += 1
            out[sid]["vals"].append(v)
            if v > 0:
                out[sid]["bull"] += 1
            elif v < 0:
                out[sid]["bear"] += 1
            else:
                out[sid]["neutral"] += 1
            if rec.get("org"):
                out[sid]["orgs"].add(rec["org"])
            if rc in ("上调", "首次覆盖"):
                out[sid]["rating_up"] += 1
            elif rc == "下调":
                out[sid]["rating_dn"] += 1
            out[sid]["fc_up"] += fc_up
            out[sid]["fc_dn"] += fc_dn
    for sid, d in out.items():
        vals = d.pop("vals")
        d["disagreement"] = float(np.var(vals)) if len(vals) >= 2 else None
        d["score"] = round(d["score"], 3)
        d["n_orgs"] = len(d.pop("orgs"))
    return out


def edge_verdicts(fw: dict, extractions: list, now=None) -> dict:
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    out = {e["id"]: {"support": 0, "refute": 0} for e in fw["edges"]}
    for rec in _dedupe_latest(extractions):
        if _age_days(rec.get("publish_ts"), now) > 30:
            continue
        for e in rec.get("edges", []):
            eid = e.get("edge_id")
            if eid in out:
                out[eid]["support" if e.get("verdict") == "支持" else "refute"] += 1
    return out


def _mom_rankpct(qsig: dict) -> dict:
    moms = {sid: d["momentum20"] for sid, d in qsig.items() if d.get("momentum20") is not None}
    if not moms:
        return {}
    ordered = sorted(moms, key=lambda k: moms[k])
    n = len(ordered)
    return {sid: (i + 0.5) / n for i, sid in enumerate(ordered)}


def narrative_temps(fw: dict, qsig: dict, extractions: list, now=None) -> list:
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    rank = _mom_rankpct(qsig)
    plus: dict = {}
    minus: dict = {}
    for rec in _dedupe_latest(extractions):
        if _age_days(rec.get("publish_ts"), now) > 7:
            continue
        for n in rec.get("narratives", []):
            nid = n.get("narrative_id")
            if n.get("stance") == "多":
                plus[nid] = plus.get(nid, 0) + 1
            elif n.get("stance") == "空":
                minus[nid] = minus.get(nid, 0) + 1
    out = []
    for n in fw["narratives"]:
        num, den = 0.0, 0.0
        for a in n.get("activates", []):
            rp = rank.get(a["segment"])
            if rp is None:
                continue
            num += a["weight"] * rp
            den += a["weight"]
        out.append({"id": n["id"], "name": n["name"],
                    "display_name": n.get("display_name", n["name"]), "status": n.get("status"),
                    "validation": n.get("validation", []), "risks": n.get("risks", []),
                    "activates": n.get("activates", []),
                    "temp": round(100.0 * num / den, 1) if den > 0 else None,
                    "plus7": plus.get(n["id"], 0), "minus7": minus.get(n["id"], 0)})
    return out


def _drivers_with_updates(fw: dict, extractions: list, now=None) -> list:
    """驱动卡带近30日研报读数证据(Kimi driver_updates,最新5条)——人审后更新 YAML reading。"""
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    upd: dict = {d["id"]: [] for d in fw["drivers"]}
    for rec in _dedupe_latest(extractions):
        if _age_days(rec.get("publish_ts"), now) > 30:
            continue
        for du in rec.get("driver_updates") or []:
            did = du.get("driver_id")
            if did in upd:
                upd[did].append({"note": du.get("note"), "org": rec.get("org"),
                                 "publish_ts": rec.get("publish_ts")})
    out = []
    for d in fw["drivers"]:
        lst = sorted(upd[d["id"]], key=lambda x: str(x.get("publish_ts")), reverse=True)[:5]
        out.append(dict(d, updates=lst))
    return out


def quadrant(q: dict, r: dict, rankpct) -> str:
    hot_q = rankpct is not None and rankpct >= 0.5
    hot_r = (r or {}).get("score", 0) > 0
    return ("h" if hot_q else "l") + ("h" if hot_r else "l")


def build_board(refresh: bool = False, fw_id: str = "ai_chain") -> dict:
    import time
    import pandas as pd
    from . import corpus, store
    from .framework import load_framework
    cache_key = f"board:{fw_id}"
    if not refresh:
        hit = _BOARD_CACHE.get(cache_key)
        if hit and time.time() - hit[0] < _BOARD_TTL:
            return hit[1]
    try:
        fw = load_framework(fw=fw_id)
        qsig = quant_signals(fw)
        ext = store.load_extractions(window_days=45, fw=fw_id)
        rsig = research_signals(fw, ext)
        rank = _mom_rankpct(qsig)
        ev = edge_verdicts(fw, ext)
        st = store.load_state(fw_id)
        segments = []
        qdate = None
        for s in fw["segments"]:
            if s.get("adjacent"):
                segments.append({"id": s["id"], "name": s["name"],
                                 "display_name": s.get("display_name", s["name"]),
                                 "group": s["group"], "adjacent": True, "logic": s["logic"]})
                continue
            q = qsig.get(s["id"], {})
            r = rsig.get(s["id"], {})
            qdate = q.get("quote_date") or qdate
            g = s.get("global", {})
            eqlog = g.get("equity_logic", [])
            rp = rank.get(s["id"])
            segments.append({
                "id": s["id"], "name": s["name"], "display_name": s.get("display_name", s["name"]),
                "group": s["group"], "adjacent": False,
                "logic": s["logic"], "keywords": s.get("keywords", []),
                "stars": g.get("stars", 0), "mrow": g.get("mrow"), "good": bool(g.get("good", False)),
                "equity_logic": eqlog, "eq": "·".join(eqlog),
                "mcol": (eqlog[0] if eqlog else None),
                "dual": ("Δ" in eqlog and "Ω" in eqlog),
                "global": g,
                "quant": q, "research": r,
                "therm": (round(rp * 100.0, 1) if rp is not None else None),
                "quadrant": quadrant(q, r, rp),
            })
        ccfg = (fw.get("meta") or {}).get("corpus") or {}
        meta = fw.get("meta") or {}
        n_signal = sum(1 for s in fw["segments"] if not s.get("adjacent"))
        board = {
            "ok": True, "reason": None,
            "meta": {"id": meta.get("id") or fw_id, "name": meta.get("name") or fw_id,
                     "n_segments": n_signal, "n_drivers": len(fw["drivers"]),
                     "n_edges": len(fw["edges"]), "version": meta.get("version")},
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "freshness": {"corpus": corpus.corpus_freshness(seed=ccfg.get("seed"), themes=ccfg.get("themes")),
                          "last_ingest_at": st.get("last_ingest_at"),
                          "extracted_total": st.get("totals", {}).get("docs", 0),
                          "quote_date": qdate},
            "drivers": _drivers_with_updates(fw, ext), "groups": fw["groups"], "segments": segments,
            "edges": [dict(e, verdict_counts=ev.get(e["id"], {"support": 0, "refute": 0})) for e in fw["edges"]],
            "narratives": narrative_temps(fw, qsig, ext),
        }
        _BOARD_CACHE[cache_key] = (time.time(), board)
        return board
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"board 组装失败: {exc}"}


def _stock_rows(stocks: list, quotes: dict, ffmap: Optional[dict], v4map: Optional[dict]) -> list:
    """票池逐票真读数:px/当日涨跌/资金5日/v4分位。缺失字段 None(不编数)。
    资金流单位随源(GL_PARQUET_ROOT/stock_fund_flow_daily),这里按元→亿换算;源缺时为 None。"""
    rows = []
    for x in stocks:
        c = x["code"]
        df = quotes.get(c)
        px = chg = None
        if df is not None and len(df) >= 2 and "close" in df.columns:
            close = df["close"].astype(float).to_numpy()
            px = round(float(close[-1]), 2)
            prev = float(close[-2])
            chg = round((float(close[-1]) / prev - 1.0) * 100.0, 2) if prev else None
        ff5 = None
        if ffmap:
            hit = ffmap.get(c) or ffmap.get(c[2:]) or ffmap.get(f"{c[2:]}.{c[:2]}")
            ff5 = round(float(hit) / 1e8, 2) if hit is not None else None
        v4 = None
        if v4map:
            hit = v4map.get(c) or v4map.get(c[2:]) or v4map.get(f"{c[2:]}.{c[:2]}")
            v4 = round(float(hit), 0) if hit is not None else None
        rows.append({"code": c, "name": x.get("name"), "role": x.get("role"), "note": x.get("note"),
                     "px": px, "chg": chg, "ff5": ff5, "v4pct": v4})
    return rows


def segment_detail(sid: str, fw_id: str = "ai_chain") -> dict:
    from . import store
    from .framework import load_framework
    try:
        fw = load_framework(fw=fw_id)
        seg = next((s for s in fw["segments"] if s["id"] == sid), None)
        if seg is None:
            return {"ok": False, "reason": f"环节不存在: {sid}"}
        ext = _dedupe_latest(store.load_extractions(window_days=30, fw=fw_id))
        opinions = []
        datapoints = []
        for rec in ext:
            rm = rec.get("report_meta") or {}
            for s in rec.get("segments", []):
                if s.get("segment_id") == sid:
                    opinions.append({"doc_id": rec.get("doc_id"), "title": rec.get("title"),
                                     "org": rec.get("org"), "publish_ts": rec.get("publish_ts"),
                                     "stance": s.get("stance"), "strength": s.get("strength"),
                                     "quote": s.get("quote"), "quote_dropped": s.get("quote_dropped"),
                                     "rating": rm.get("rating"), "rating_change": rm.get("rating_change"),
                                     "target_price": rm.get("target_price")})
            for dp in rec.get("datapoints") or []:
                if dp.get("segment_id") == sid:   # 只收显式挂靠本环节的(不猜)
                    datapoints.append(dict(dp, org=rec.get("org"), publish_ts=rec.get("publish_ts"),
                                           doc_id=rec.get("doc_id")))
        opinions.sort(key=lambda x: str(x.get("publish_ts")), reverse=True)
        datapoints.sort(key=lambda x: str(x.get("publish_ts")), reverse=True)
        datapoints = datapoints[:40]
        pool_codes = [x["code"] for x in seg.get("stocks", [])]
        quotes = _fetch_quotes(pool_codes)
        qsig = quant_signals(fw, quotes=quotes)
        rsig = research_signals(fw, ext)
        stock_rows = _stock_rows(seg.get("stocks", []), quotes, _fundflow_map(), _v4_pct_map())
        return {"ok": True, "reason": None, "segment": seg, "quant": qsig.get(sid),
                "research": rsig.get(sid), "opinions": opinions, "datapoints": datapoints,
                "stocks": seg.get("stocks", []), "stock_rows": stock_rows}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"segment 明细失败: {exc}"}


def doc_detail(doc_id: str, fw_id: str = "ai_chain") -> dict:
    from . import store
    try:
        recs = [r for r in store.load_extractions(fw=fw_id) if r.get("doc_id") == doc_id]
        if not recs:
            return {"ok": False, "reason": f"无此 doc: {doc_id}"}
        recs.sort(key=lambda r: str(r.get("extracted_at") or ""), reverse=True)
        return {"ok": True, "reason": None, "extraction": recs[0]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"doc 明细失败: {exc}"}
