# -*- coding: utf-8 -*-
"""研报证据包生产器(数据中台→研报的传递缝,单元A第一块)。

背景:研报 17 节点 DAG 跑在引擎子进程里,导不到 guanlan_v2.*(进程边界),故 17 个数据面
(实时盘口/资金流/打板生态/统一情绪/快讯/产业链/量化榜/主线雷达/宏观温度/持仓台账)全部
闲置。本模块在 guanlan_v2 侧研报前一次性组装「证据包」JSON 落盘,经 env FA_EVIDENCE_PACK
路径传给子进程(接缝见单元A第2步,另 diff),下游 evidence-loader 零 LLM 节点读取。

红线:纯只读展示,零回写任何 picks/blend/seats 信号通路;每 section 独立 try/except——
单 section 失败(含其 import)= 该键 null + errors[名]=原因,包永远能产出(诚实缺斤少两,
绝不编造)。sections_ok 记录「无异常」的 section(即便其合法返回值本身是 None,例如未持有
该票时 holding=None、或该票不在产业链框架内时 chain=None,都算「成功查过」而非「失败」)。

十 section 各自一个模块级薄函数 `_sec_xxx(code) -> dict | None`,便于测试逐个打桩
(build_evidence_pack 本身零网络,全部 IO 由这些薄函数触发)。耗时预算(同步函数,
调用方自行 to_thread 摘出事件循环):
  - quote_live: ≤3 次 live_client.probe(orderbook/ticks/quote-failover),秒级
  - fundflow:  1 次个股 probe(eastmoney_fund_flow)+ fundflow.pulse SWR 秒回(缓存命中)
  - kuaixun:   1 次网络调(fetch_kuaixun,内部 15s 缓存)
  - 其余(board_eco/sentiment/chain/quant/mainline/macro/holding):纯文件读或 SWR 缓存读

个股资金流探针实测(2026-07-12,真机 `live_client.probe("eastmoney_fund_flow",
code="SH603986", limit=5)`):items 按 publish_ts 升序(最旧在前),每条 `raw` = {code, date,
main_net, small_net, mid_net, large_net, super_net}(单位:元,与 fundflow/sources.py 板块
口径一致);取 `items[-1]`(最新一天)。字段名与预期一致,故非 best-effort 降级式探测,
仅 probe 调用本身包 try/except(源不可达时 stock_main_net=None,不挡其余字段)。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "var" / "reports" / "evidence"


def _norm_code(code: str) -> str:
    """归一到 SH/SZ/BJ 前缀形式(仓内惯例:financial_analyst.buddy.tools.normalize_code;
    引擎不可用/未在 sys.path → 退大写原样,诚实降级,不挡包产出)。"""
    try:
        from financial_analyst.buddy.tools import normalize_code
        return normalize_code(code)
    except Exception:  # noqa: BLE001
        return str(code or "").strip().upper()


# ── ① quote_live:五档盘口 + 逐笔 + 报价 failover(seats/live_book,≤3 probe)──────

def _sec_quote_live(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.seats import live_book
    as_of = datetime.now().isoformat(timespec="seconds")   # 三腿均不透传 pulled_at,以取数时刻为准
    q = live_book.read_quote_failover(code)
    ob = live_book.read_orderbook(code)
    tk = live_book.read_ticks(code, limit=10)

    price = q.get("price") if q.get("ok") else (ob.get("price") if ob.get("ok") else None)
    pct = q.get("changePercent") if q.get("ok") else None

    ob_summary = None
    if ob.get("ok"):
        levels = ob.get("levels") or []
        top = next((lv for lv in levels if lv.get("level") == 1), {})
        ob_summary = {"n_levels": len(levels), "best_bid": top.get("bid"),
                      "best_ask": top.get("ask"), "best_bid_vol": top.get("bid_vol"),
                      "best_ask_vol": top.get("ask_vol")}

    tk_summary = None
    if tk.get("ok"):
        ticks = tk.get("ticks") or []
        buy_vol = sum(float(t.get("vol") or 0) for t in ticks if t.get("side") == "buy")
        sell_vol = sum(float(t.get("vol") or 0) for t in ticks if t.get("side") == "sell")
        tk_summary = {"n": tk.get("n"), "buy_vol": buy_vol, "sell_vol": sell_vol,
                      "last_price": (ticks[0].get("price") if ticks else None)}

    if price is None and ob_summary is None and tk_summary is None:
        return None   # 三腿全不可达(非交易时段/tdx 断连)→ 诚实空,不是「失败」
    return {"as_of": as_of, "price": price, "pct": pct,
            "orderbook_summary": ob_summary, "ticks_summary": tk_summary}


# ── ② fundflow:个股资金流(live_client 有源探针)+ 板块/大盘(fundflow.pulse)──────

def _sec_fundflow(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.fundflow import pulse as ff_pulse
    from guanlan_v2.strategy.ranking import name_industry_map

    stock_main_net = None
    as_of_stock = None
    try:
        from guanlan_v2.datafeed import live_client as lc
        bare = "".join(ch for ch in code if ch.isdigit())[-6:] or code
        r = lc.probe("eastmoney_fund_flow", code=bare, limit=5)
        items = r.get("items") or []
        if r.get("ok") and items:
            raw = items[-1].get("raw") or {}   # 升序(最旧在前),取最新一天
            stock_main_net = raw.get("main_net")
            as_of_stock = raw.get("date")
    except Exception:  # noqa: BLE001 — 个股资金流探针 best-effort,失败诚实 None
        pass

    industry = None
    try:
        industry = (name_industry_map().get(code) or (None, None))[1]
    except Exception:  # noqa: BLE001
        industry = None

    sector = sector_rank = market_main = None
    as_of = as_of_stock
    d = ff_pulse.read_live("industry")
    if d and not d.get("warming"):
        as_of = d.get("pulled_at") or as_of
        boards = d.get("boards") or []
        if industry:
            b = next((x for x in boards if str(x.get("name") or "") == industry), None)
            if b:
                sector = b.get("main_net")
                sector_rank = b.get("rank")
        mn = (d.get("market") or {}).get("main_net")
        if mn is not None:
            try:
                market_main = round(float(mn) / 1e8, 2)
            except (TypeError, ValueError):
                market_main = None

    if stock_main_net is None and sector is None and market_main is None:
        return None
    return {"as_of": as_of, "stock_main_net": stock_main_net,
            "sector": sector, "sector_rank": sector_rank, "market_main": market_main}


# ── ③ board_eco:打板生态 + 龙虎榜 + 北向(market_tape,SWR 永不阻塞)───────────

def _sec_board_eco(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed.market_tape import read_tape
    t = read_tape()
    if t.get("warming"):
        return None   # 预热中诚实空,绝不假装有数
    der = t.get("derived") or {}
    sources = t.get("sources") or {}
    lhb_rows = (sources.get("lhb") or {}).get("rows") or []
    code_lhb = [r for r in lhb_rows if isinstance(r, dict)
                and "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())
                == "".join(ch for ch in code if ch.isdigit())]
    return {"as_of": t.get("pulled_at"), "zt_count": der.get("zt_count"),
            "zb_count": der.get("zb_count"), "break_rate": der.get("break_rate"),
            "promotion_rate": der.get("promotion_rate"),
            "lhb": code_lhb[:5], "north_net": der.get("north_net")}


# ── ④ sentiment:统一情绪 store 当日判读(纯文件读)────────────────────────────

def _sec_sentiment(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed import sentiment as sm
    s = sm.read_summary(code)
    market = s.get("market") or {}
    judgment = s.get("judgment")
    if not judgment and not market.get("market_read") and not market.get("market_tilt"):
        return None
    return {"as_of": market.get("as_of") or s.get("date"),
            "tag": (judgment or {}).get("tag"), "read": (judgment or {}).get("read"),
            "market_read": market.get("market_read"), "market_tilt": market.get("market_tilt")}


# ── ⑤ kuaixun:东财 7×24 快讯,按票过滤(codes 命中或名字命中标题/摘要)─────────

def _sec_kuaixun(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed.kuaixun import fetch_kuaixun
    from guanlan_v2.strategy.ranking import name_industry_map
    rows = fetch_kuaixun(200) or []
    if not rows:
        return None
    name = ""
    try:
        name = (name_industry_map().get(code) or (None, None))[0] or ""
    except Exception:  # noqa: BLE001
        name = ""
    items: List[Dict[str, Any]] = []
    for it in rows:
        title = str(it.get("title") or "")
        summary = str(it.get("summary") or "")
        hit = code in (it.get("codes") or []) or (bool(name) and (name in title or name in summary))
        if hit:
            items.append({"time": it.get("time"), "title": title})
        if len(items) >= 8:
            break
    return {"as_of": rows[0].get("time"), "items": items}


# ── ⑥ chain:产业链分 + 段内研报观点(rescore.industry_scores + industry.aggregate)──

def _sec_chain(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.screen.rescore import industry_scores
    scores, _fresh = industry_scores([code])
    best = scores.get(code)
    if not best:
        return None   # 链外票,诚实 None(合法常态,非失败)
    views: List[Dict[str, Any]] = []
    try:
        from guanlan_v2.industry.aggregate import segment_detail
        detail = segment_detail(best.get("seg"))
        if detail.get("ok"):
            for op in (detail.get("opinions") or [])[:3]:
                views.append({"org": op.get("org"), "stance": op.get("stance"),
                              "quote": op.get("quote"), "publish_ts": op.get("publish_ts")})
    except Exception:  # noqa: BLE001 — 段内观点是加菜,失败不挡主字段
        pass
    return {"seg": best.get("seg_name") or best.get("seg"), "quadrant": best.get("quadrant"),
            "therm": best.get("therm"), "industry_views": views}


# ── ⑦ quant:v4 榜 + DL 逐票预测 + rerank 档案(strategy.ranking + rescore_runs.jsonl)──

def _dl_scores(code: str) -> Dict[str, Optional[float]]:
    """三 DL 源(FinCast/LSTM/GAT)逐票最新 pred_ret_5d;LGB 已是 v4_pct 基座、无独立
    parquet,占位 null。逐源独立 try/except,单源缺失不挡其余。"""
    import pandas as pd
    var = Path(__file__).resolve().parents[2] / "var"
    files = {"fc": var / "v4_fincast_pred.parquet", "lstm": var / "dl_pred_lstm.parquet",
             "gat": var / "dl_pred_gat.parquet"}
    out: Dict[str, Optional[float]] = {"lgb": None}
    for key, p in files.items():
        try:
            d = pd.read_parquet(p, columns=["eval_date", "instrument", "pred_ret_5d"])
            d = d[d["instrument"] == code]
            if d.empty:
                out[key] = None
                continue
            d = d.sort_values("eval_date")
            out[key] = round(float(d["pred_ret_5d"].iloc[-1]), 4)
        except Exception:  # noqa: BLE001
            out[key] = None
    return out


def _rerank_lookup(code: str) -> Optional[Dict[str, Any]]:
    """倒扫 rescore_runs.jsonl 最多 200 行,首个 rerank.ok 且 rows 含该票的 run。"""
    from guanlan_v2.screen.rescore import RUNS_PATH
    try:
        text = RUNS_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for line in reversed(lines[-200:]):
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            continue
        rk = run.get("rerank") or {}
        if not rk.get("ok"):
            continue
        for row in rk.get("rows") or []:
            if str(row.get("code")) == code:
                return {"rank_before": row.get("rank_before"), "rank_after": row.get("rank_after"),
                        "stance": row.get("stance"), "run_id": run.get("run_id"), "ts": run.get("ts")}
    return None


def _sec_quant(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.strategy.ranking import load_v4_ranking, v4_pct_map
    df = load_v4_ranking()                      # 缺产物 → FileNotFoundError,整节 null(合理:核心榜缺失)
    pmap = v4_pct_map(df)
    v4_pct = pmap.get(code)
    v4_rank = None
    if v4_pct is not None:
        ranked = sorted(pmap.items(), key=lambda kv: kv[1], reverse=True)
        v4_rank = next((i + 1 for i, (c, _) in enumerate(ranked) if c == code), None)
    dl = _dl_scores(code)
    rerank = _rerank_lookup(code)
    return {"v4_rank": v4_rank, "v4_pct": (round(v4_pct, 2) if v4_pct is not None else None),
            "dl": dl, "rerank": rerank}


# ── ⑧ mainline:主线雷达(仓内新鲜月度面板,lru_cache;测试直接桩本函数)──────────

def _sec_mainline(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.strategy.ranking import mainline_status_map, name_industry_map
    smap = mainline_status_map()
    if not smap:
        return None
    as_of = next(iter(smap.values()), {}).get("as_of")
    top = sorted(
        [{"industry": k, "status": v.get("status"), "golden": v.get("golden")}
         for k, v in smap.items() if v.get("status") == "mainline"],
        key=lambda x: bool(x.get("golden")), reverse=True)[:10]
    ind = None
    try:
        ind = (name_industry_map().get(code) or (None, None))[1]
    except Exception:  # noqa: BLE001
        ind = None
    stock_hit = None
    if ind and ind in smap:
        stock_hit = {"industry": ind, **smap[ind]}
    return {"as_of": as_of, "top": top, "stock_hit": stock_hit}


# ── ⑨ macro:宏观/A股打板双温度 + 决策护盾档位(screen.market_temp,便宜安全)────

def _sec_macro(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.screen.market_temp import build_market_temp
    mt = build_market_temp()
    g = mt.get("global") or {}
    llm = mt.get("llm") or {}
    gate = mt.get("gate") or {}
    if not g and not llm and not gate:
        return None
    return {"as_of": llm.get("as_of") or g.get("ts"), "astock_temp": g.get("astock_temp"),
            "global_temp": g.get("g_temp"), "market_temp_stance": gate.get("level")}


# ── ⑩ holding:自有台账持仓视角(seats.api._ledger_events/_ledger_replay)────────

def _sec_holding(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.seats.api import _ledger_events, _ledger_replay
    snap = _ledger_replay(_ledger_events())
    if not snap.get("opened"):
        return None
    pos = (snap.get("positions") or {}).get(code)
    if not pos:
        return None   # 未持有该票 → 诚实 None(合法常态,非失败)
    avg_cost = pos.get("avg_cost")
    qty = pos.get("qty")
    price = None
    try:
        from guanlan_v2.seats import live_book
        q = live_book.read_quote_failover(code)
        if q.get("ok"):
            price = q.get("price")
    except Exception:  # noqa: BLE001 — upl 是加菜,报价拿不到就 null,不挡 held/avg_cost/qty
        price = None
    upl = None
    if price is not None and avg_cost is not None and qty is not None:
        try:
            upl = round((float(price) - float(avg_cost)) * float(qty), 2)
        except (TypeError, ValueError):
            upl = None
    return {"held": True, "avg_cost": avg_cost, "qty": qty, "upl": upl}


# ── 十 section 注册表(顺序=spec §2 schema 顺序;打桩靶点=各 _sec_xxx)────────────
# 注意:按名字动态查 globals() 而非直接存函数引用——测试
# monkeypatch.setattr(evidence, "_sec_quote_live", stub) 改写的是模块属性(即
# module.__dict__/globals()),若这里在 import 时就把函数对象绑进元组,后续打桩不会生效。

_SECTION_NAMES: Tuple[str, ...] = (
    "quote_live", "fundflow", "board_eco", "sentiment", "kuaixun",
    "chain", "quant", "mainline", "macro", "holding",
)


def build_evidence_pack(code: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """组装研报证据包并落盘。十 section 各自独立 try/except:单 section 异常 → 该键 null
    + errors[名]=原因(包永远能产出,不因单块挂而整体失败);合法的「查过但无数据」
    (如未持有/链外/预热中)也是 None,但不计入 errors——sections_ok 只区分「有没有异常」。

    返回 ``{ok, path, sections_ok:[名,...], errors:{名:原因,...}}``;
    落盘 ``var/reports/evidence/{norm_code}_{YYYYMMDDHHMM}.json``(spec §2 schema)。
    """
    norm = _norm_code(code)
    sections: Dict[str, Any] = {}
    sections_ok: List[str] = []
    errors: Dict[str, str] = {}
    for name in _SECTION_NAMES:
        fn = globals()[f"_sec_{name}"]   # 动态查名,拿到打桩后的当前绑定
        try:
            sections[name] = fn(norm)
            sections_ok.append(name)
        except Exception as exc:  # noqa: BLE001 — 单 section 失败绝不拖垮整包
            sections[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"

    pack = {"code": norm, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sections": sections}
    out_base = Path(out_dir) if out_dir else _DEFAULT_OUT_DIR
    ts = datetime.now().strftime("%Y%m%d%H%M")
    path = out_base / f"{norm}_{ts}.json"
    ok = True
    try:
        out_base.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 — 落盘失败诚实 ok:False,section 结果仍如实返回
        ok = False
        errors.setdefault("_write", f"{type(exc).__name__}: {exc}")

    return {"ok": ok, "path": (str(path) if ok else None),
            "sections_ok": sections_ok, "errors": errors}


__all__ = ["build_evidence_pack"]
