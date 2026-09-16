# -*- coding: utf-8 -*-
"""研报证据包生产器(数据中台→研报的传递缝,单元A第一块)。

背景:研报 17 节点 DAG 跑在引擎子进程里,导不到 guanlan_v2.*(进程边界),故 17 个数据面
(实时盘口/资金流/打板生态/统一情绪/快讯/产业链/量化榜/主线雷达/宏观温度/持仓台账)全部
闲置。本模块在 guanlan_v2 侧研报前一次性组装「证据包」JSON 落盘,经 env FA_EVIDENCE_PACK
路径传给子进程(接缝见单元A第2步,另 diff),下游 evidence-loader 零 LLM 节点读取。

红线:纯只读展示,零回写任何 picks/blend/seats 信号通路;每 section 独立 try/except——
单 section 失败(含其 import)= 该键 null + errors[名]=原因,包永远能产出(诚实缺斤少两,
绝不编造)。sections_ok 记录「无异常」的 section(即便其合法返回值本身是 None,例如未持有
该票时 holding.status=not_applicable、或链外 chain=None,都算「成功查过」而非「失败」)。

十五 section 各自一个模块级薄函数 `_sec_xxx(code) -> dict | None`,便于测试逐个打桩
(build_evidence_pack 本身零网络,全部 IO 由这些薄函数触发)。耗时预算(同步函数,
调用方自行 to_thread 摘出事件循环):
  - quote_live: ≤4 次 live_client.probe(orderbook/ticks/quote-failover + 日线日期核验),秒级
  - fundflow:  1 次个股 probe(eastmoney_fund_flow)+ fundflow.pulse SWR 秒回(缓存命中)
  - kuaixun:   1 次网络调(fetch_kuaixun,内部 15s 缓存)
  - company_facts/sentiment:各 1 次东财固定实时接口；持股披露/原文/判读分开
  - 其余(board_eco/chain/quant/mainline/macro/holding):纯文件读或 SWR 缓存读

个股资金流探针实测(2026-07-12,真机 `live_client.probe("eastmoney_fund_flow",
code="SH603986", limit=5)`):items 按 publish_ts 升序(最旧在前),每条 `raw` = {code, date,
main_net, small_net, mid_net, large_net, super_net}(单位:元,与 fundflow/sources.py 板块
口径一致);取 `items[-1]`(最新一天)。字段名与预期一致,故非 best-effort 降级式探测,
仅 probe 调用本身包 try/except(源不可达时 stock_main_net=None,不挡其余字段)。
"""
from __future__ import annotations

import json
import os
import importlib
import hashlib
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

def _default_out_dir(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    return Path(source.get("GUANLAN_REPORT_EVIDENCE_DIR") or (
        Path(__file__).resolve().parents[2] / "var" / "reports" / "evidence"
    ))


_DEFAULT_OUT_DIR = _default_out_dir()
_SCHEMA_VERSION = "company-research-evidence@2"
_HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _hk_today() -> date:
    return datetime.now(_HONG_KONG).date()


def _engine_data_module(name: str):
    """Load a bundled engine data module when the saved external fork is older."""
    module_name = f"financial_analyst.data.{name}"
    try:
        return importlib.import_module(module_name)
    except ImportError:
        import financial_analyst.data as data_package

        bundled = Path(__file__).resolve().parents[2] / "engine" / "financial_analyst" / "data"
        package_paths = getattr(data_package, "__path__", None)
        if package_paths is None or not bundled.is_dir():
            raise
        bundled_text = str(bundled)
        if bundled_text not in package_paths:
            package_paths.append(bundled_text)
        return importlib.import_module(module_name)


# F10 fixtures intentionally replace ``_engine_data_module``.  Trust resolution
# must remain pinned to the real bundled module and cannot be redirected by a
# data-adapter test double.
_REAL_ENGINE_DATA_MODULE = _engine_data_module


def _trusted_receipts_module():
    return _REAL_ENGINE_DATA_MODULE("trusted_receipts")


def _norm_code(code: str) -> str:
    """归一到 SH/SZ/BJ 前缀形式(仓内惯例:financial_analyst.buddy.tools.normalize_code;
    引擎不可用/未在 sys.path → 退大写原样,诚实降级,不挡包产出)。"""
    try:
        from financial_analyst.buddy.tools import normalize_code
        return normalize_code(code)
    except Exception:  # noqa: BLE001
        return str(code or "").strip().upper()


# ── ① quote_live:五档盘口 + 逐笔 + 报价 failover(seats/live_book,≤3 probe)──────

def _attest_quote_market_session(code: str, price: Any) -> Optional[Dict[str, Any]]:
    """Bind a retained quote to a market day only via an exact daily bar.

    TDX's quote/book adapters expose capture time, not a trustworthy exchange
    date.  The daily-kline row does carry an explicit YYYY-MM-DD and close.  We
    accept only the latest exact-code daily row and only when its close equals
    the quote; an absent/malformed/mismatched row leaves the quote undated.
    """
    try:
        observed_price = float(price)
    except (TypeError, ValueError):
        return None
    try:
        from guanlan_v2.datafeed import live_client as lc

        bare = "".join(ch for ch in code if ch.isdigit())[-6:]
        result = lc.probe("tdx_kline", code=bare, limit=5)
        if (
            not result.get("ok")
            or result.get("status") not in {None, "", "ok"}
            or result.get("source") != "tdx_kline"
        ):
            return None
        candidates: List[Tuple[date, float, Mapping[str, Any], Mapping[str, Any]]] = []
        for item in result.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("source_id") != "tdx_kline":
                continue
            raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else item
            raw_code = "".join(ch for ch in str(
                raw.get("stock_code") or raw.get("code") or ""
            ) if ch.isdigit())[-6:]
            if not bare or raw_code != bare or str(raw.get("freq") or "").lower() != "day":
                continue
            raw_day = str(raw.get("datetime") or raw.get("date") or "")[:10]
            try:
                session = date.fromisoformat(raw_day)
                close = float(raw.get("close"))
            except (TypeError, ValueError):
                continue
            if session > _hk_today():
                continue
            candidates.append((session, close, raw, item))
        if not candidates:
            return None
        session, close, raw, item = max(candidates, key=lambda row: row[0])
        if abs(close - observed_price) > max(1e-6, abs(observed_price) * 1e-8):
            return None
        return {
            "market_data_as_of": session.isoformat(),
            "source": "tdx_kline",
            "provider": item.get("provider") or result.get("provider") or "tdx",
            "close": close,
            "datetime": raw.get("datetime"),
            "revision_id": item.get("revision_id"),
            "validation": "latest_exact_code_daily_close_equals_quote",
        }
    except Exception:  # noqa: BLE001 - optional verifier; fail closed to undated
        return None


def _sec_quote_live(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.seats import live_book
    # These three adapters expose an auditable completion time but do not
    # attest the exchange session to which a retained non-trading-day quote
    # belongs.  Keep acquisition time separate: it must never become the
    # market-data ``as_of`` merely because the read happened today.
    retrieved_at = datetime.now(_HONG_KONG).isoformat(timespec="seconds")
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
    attestation = _attest_quote_market_session(code, price)
    out = {"retrieved_at": retrieved_at, "price": price, "pct": pct,
           "orderbook_summary": ob_summary, "ticks_summary": tk_summary}
    if attestation:
        # ``as_of`` remains the compatibility field, but now it is copied from
        # a source-observed daily bar rather than from the wall clock.
        out["as_of"] = attestation["market_data_as_of"]
        out["market_data_as_of"] = attestation["market_data_as_of"]
        out["market_data_attestation"] = attestation
    else:
        out["market_data_date_status"] = "unverified"
    return out


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


# ── company_facts:本地 F10 PIT 公司事实(不依赖量化榜是否存在)────────────────

def _iso_snapshot(value: Any) -> Optional[str]:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value) if value else None


def _sec_company_facts(
        code: str, asof: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Local PIT facts plus a distinct current ownership disclosure query."""
    current = not asof or str(asof)[:10] >= _hk_today().isoformat()
    try:
        f10_corpus = _engine_data_module("f10_corpus")
        facts = f10_corpus.load_facts(code, asof=asof).to_dict()
    except Exception:
        if not current:
            raise
        facts = {"unavailable_sources": [{"source": "local_f10", "reason": "unavailable"}]}
    ownership = None
    if current:
        from guanlan_v2.datafeed.company_context_live import query_shareholder_disclosures
        ownership = query_shareholder_disclosures(code)
    meaningful = any((
        facts.get("valuation"), facts.get("events"),
        (facts.get("broker") or {}).get("ratings"), facts.get("holders"),
        facts.get("main_capital"), (facts.get("lhb") or {}).get("margin"),
        (ownership or {}).get("holders"), (ownership or {}).get("holder_counts"),
        (ownership or {}).get("top_float_holders"),
    ))
    if not meaningful and not current:
        return None
    valuation = facts.get("valuation") or {}
    as_of = (valuation.get("report_period") or _iso_snapshot(facts.get("snapshot_date"))
             or (ownership or {}).get("as_of"))
    out = {
        "as_of": as_of,
        "snapshot_date": _iso_snapshot(facts.get("snapshot_date")),
        "valuation": facts.get("valuation"),
        "events": list(facts.get("events") or [])[:20],
        "broker": {"ratings": list((facts.get("broker") or {}).get("ratings") or [])[:10]},
        "holders": facts.get("holders"),
        "main_capital": facts.get("main_capital"),
        "lhb": {
            "margin": list((facts.get("lhb") or {}).get("margin") or [])[:10],
            "moneyflow": list((facts.get("lhb") or {}).get("moneyflow") or [])[:10],
            "abnormal": list((facts.get("lhb") or {}).get("abnormal") or [])[:10],
        },
        "provenance": list(facts.get("provenance") or [])[:20],
        "snapshot_receipts": list(facts.get("snapshot_receipts") or []),
        "unavailable_sources": list(facts.get("unavailable_sources") or []),
        "note": facts.get("honest_note") or "",
    }
    if ownership is not None:
        # Keep differing local/live report periods separate; never overwrite a
        # PIT corpus fact with today's secondary rendering of a disclosure.
        out["ownership_live"] = ownership
        if not out["holders"] and ownership.get("holders"):
            out["holders"] = ownership["holders"]
        if ownership.get("status") == "unavailable":
            out["unavailable_sources"].append({
                "source": ownership["source"], "reason": ownership.get("error") or "unavailable",
            })
    if not meaningful:
        out["status"] = "unavailable" if (ownership or {}).get("status") == "unavailable" else "empty"
        out["note"] = "本地 F10 无可用公司事实，实时股东披露本次亦未补到数据。"
    return out


# ── announcements:巨潮公告元数据(官方索引;不声称读取 PDF 正文)──────────────

def _sec_announcements(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed import live_client as lc

    result = lc.probe("cninfo_announcements", code=code, limit=20)
    if not (result.get("ok") and result.get("status") == "ok"):
        raise RuntimeError(result.get("note") or result.get("error") or "cninfo unavailable")
    # Keep the normalized envelope here: unlike numeric probes, announcement
    # title/source URL live at the envelope level while ``native_rows`` flattens
    # only the provider's raw payload plus PIT timestamps.
    rows = [row for row in (result.get("items") or []) if isinstance(row, dict)]
    if not rows:
        return None
    items = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        items.append({
            "date": row.get("date") or row.get("announcement_date") or row.get("publish_ts"),
            "title": (row.get("title") or row.get("announcement_title")
                      or (row.get("raw") or {}).get("announcementTitle") or row.get("name")),
            "type": (row.get("type") or row.get("announcement_type")
                     or row.get("source_type") or row.get("text")
                     or (row.get("raw") or {}).get("announcementTypeName")
                     or (row.get("raw") or {}).get("announcementType")),
            "source_ref": (row.get("url") or row.get("source_url")
                           or row.get("adjunct_url")),
        })
    as_of = next((item.get("date") for item in items if item.get("date")), result.get("pulled_at"))
    return {"as_of": as_of, "retrieved_at": result.get("pulled_at"),
            "source": "cninfo_announcements", "scope": "metadata_only",
            "items": items, "n": len(items)}


# ── expectations:同花顺一致预期 EPS(外部预期,不是公司已实现业绩)─────────────

def _sec_expectations(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed import live_client as lc

    result = lc.probe("ths_eps_forecast", code=code, limit=20)
    if not (result.get("ok") and result.get("status") == "ok"):
        raise RuntimeError(result.get("note") or result.get("error") or "eps forecast unavailable")
    rows = [row for row in lc.native_rows(result.get("items")) if isinstance(row, dict)]
    if not rows:
        return None
    years = [str(row.get("year") or "") for row in rows if row.get("year")]
    return {"as_of": result.get("pulled_at"), "retrieved_at": result.get("pulled_at"),
            "content_period": {"first_year": min(years) if years else None,
                               "last_year": max(years) if years else None},
            "source": "ths_eps_forecast", "trust": "external_consensus",
            "items": rows[:12], "n": min(len(rows), 12)}


# ── research_views:有界研报观点与精确 evidence_id(外部不可信文本)───────────

def _sec_research_views(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.research.evidence import normalise_report_evidence_many
    from guanlan_v2.research.latest_reports import read_latest_report_views

    result = read_latest_report_views(
        security=code, limit=5, lookback_days=90, mode="live", as_of=None,
        include_live_metadata=True,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    rows = [row for row in (result.get("items") or []) if isinstance(row, dict)][:5]
    if not rows:
        return None
    evidence = normalise_report_evidence_many(rows, limit=5)
    items = []
    for index, row in enumerate(rows):
        ev = evidence[index] if index < len(evidence) else {}
        items.append({
            "evidence_id": ev.get("evidence_id"),
            "publish_ts": row.get("publish_ts"),
            "institution": row.get("institution"),
            "title": row.get("title"),
            "stance": row.get("stance"),
            "view": row.get("view"),
        })
    coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
    as_of = coverage.get("latest_publish_ts") or next(
        (row.get("publish_ts") for row in rows if row.get("publish_ts")), None,
    )
    return {"as_of": as_of, "trust": "external_untrusted",
            "items": items, "evidence": evidence, "coverage": coverage,
            "warning": result.get("warning") or ""}


# ── ③ board_eco:打板生态 + 龙虎榜 + 北向(market_tape,SWR 永不阻塞)───────────

def _sec_board_eco(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed import live_client as lc
    from guanlan_v2.datafeed.market_tape import read_tape
    t = read_tape()
    if t.get("warming"):
        return None   # 预热中诚实空,绝不假装有数
    der = t.get("derived") or {}
    sources = t.get("sources") or {}
    lhb_id = lc.resolve_source("eastmoney_lhb") or "eastmoney_lhb"
    north_id = lc.resolve_source("northbound") or "northbound"
    lhb_source = sources.get(lhb_id) or {}
    north_source = sources.get(north_id) or {}
    lhb_rows = lhb_source.get("rows") or []
    code_lhb = [r for r in lhb_rows if isinstance(r, dict)
                and "".join(ch for ch in str(r.get("code") or "") if ch.isdigit())
                == "".join(ch for ch in code if ch.isdigit())]
    board_as_of = (_iso_snapshot(t.get("board_date")) or t.get("core_pulled_at")
                   or t.get("pulled_at"))
    return {"as_of": board_as_of, "core_pulled_at": t.get("core_pulled_at"),
            "freshness": t.get("freshness") or {},
            "source_as_of": {
                "lhb": (_iso_snapshot(lhb_source.get("content_date"))
                        or lhb_source.get("pulled_at")),
                "northbound": (_iso_snapshot(north_source.get("content_date"))
                               or north_source.get("pulled_at")),
            },
            "zt_count": der.get("zt_count"),
            "zb_count": der.get("zb_count"), "break_rate": der.get("break_rate"),
            "promotion_rate": der.get("promotion_rate"), "max_streak": der.get("max_streak"),
            "ladder": der.get("ladder"),
            "lhb": code_lhb[:5], "north_net": der.get("north_net")}


# ── ④ sentiment:当日已判口径 + 实时公司新闻原文(零新增 LLM/缓存写入)────────

def _sec_sentiment(code: str) -> Optional[Dict[str, Any]]:
    from guanlan_v2.datafeed import sentiment as sm
    from guanlan_v2.datafeed.company_context_live import query_company_news
    s = sm.read_summary(code)
    market = s.get("market") or {}
    judgment = s.get("judgment")
    news = query_company_news(code, limit=10)
    meta = next((row for row in reversed(sm._read_lines(sm._file("judgments", s["date"])))
                 if row.get("date") == s["date"] and row.get("code") == code), {})
    has_market = bool(market.get("market_read") or market.get("market_tilt"))
    return {
        "source": "company_sentiment_context", "scope": "context",
        "as_of": (meta.get("as_of") or s.get("date")) if judgment else news.get("as_of"),
        "retrieved_at": news["retrieved_at"],
        "status": "ok" if judgment else "partial" if news["items"] or has_market else news["status"],
        "judgment_status": "available" if judgment else "no_evidence_when_judged" if s.get("judged") else "not_run",
        "judged": bool(s.get("judged")), "tag": (judgment or {}).get("tag"),
        "read": (judgment or {}).get("read"),
        "judgment_source": meta.get("source") if judgment else None,
        "judgment_recorded_at": meta.get("ts") if judgment else None,
        "judgment_evidence_refs_status": "not_retained_by_legacy_store" if judgment else "not_applicable",
        "news_evidence": news, "market_context": market,
        "market_read": market.get("market_read"), "market_tilt": market.get("market_tilt"),
        "warnings": ["新闻原文与情绪判读分开；tag/read 为空表示尚无个股判读，不等于中性。",
                     "市场判读仅为背景，不作为该公司情绪或发行人事实。"] + news["warnings"],
    }


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


def _valuation_bands(code: str) -> Optional[Dict[str, Any]]:
    """个股估值历史分位「贵不贵」锚:baidu_valuation_percentile 探针 → {pe_ttm:{...}, pb:{...}}。
    中立呈现(只标位置不划买卖线);缺源/触网失败 → None(诚实空,绝不编造)。"""
    try:
        from guanlan_v2.datafeed import live_client as lc
        r = lc.probe("baidu_valuation_percentile", code=code, limit=2)
    except Exception:  # noqa: BLE001
        return None
    if not (r.get("ok") and r.get("status") in ("ok", "")):
        return None
    keep = ("current", "percentile", "p20", "p50", "p80", "min", "max", "n")
    out: Dict[str, Any] = {}
    for row in (lc.native_rows(r.get("items")) or []):
        m = row.get("metric")
        if m in ("pe_ttm", "pb"):
            out[m] = {k: row.get(k) for k in keep}
    if out and r.get("pulled_at"):
        out["as_of"] = r.get("pulled_at")
    return out or None


def _read_local_valuation_frame(code: str, start: str, end: str):
    """Read existing local daily-basic bins, recording a stable-read receipt."""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.paths import get_data_paths

    paths = get_data_paths()
    root = paths.qlib_day.resolve()
    files = [root / "calendars" / "day.txt", *(
        root / "features" / code.lower() / f"{field}.day.bin"
        for field in ("pe_ttm", "pb")
    )]
    if any(not path.resolve().is_relative_to(root) for path in files):
        raise ValueError("valuation source file escapes the configured local data root")

    def signatures():
        result = []
        for path in files:
            stat = path.stat() if path.exists() else None
            result.append({"path": str(path), "present": stat is not None,
                           "size": stat.st_size if stat else None,
                           "mtime_ns": stat.st_mtime_ns if stat else None})
        return result

    before = signatures()
    frame = QlibBinaryLoader(paths.qlib_uri).fetch_daily_basic(code, start, end)
    if before != signatures():
        raise RuntimeError("local valuation files changed while being read; retry a stable read")
    return frame, before


def _local_valuation_history(code: str, asof: Optional[str] = None) -> Dict[str, Any]:
    """Expose real PE/PB observations for current research, never historical PIT."""
    import math
    import re
    from collections import Counter

    source = "qlib_local_daily_basic"
    result: Dict[str, Any] = {
        "source": source, "status": "unavailable", "series": {},
        "point_in_time": False, "historical_version_proven": False,
        "current_research_only": True,
        "retrieved_at": datetime.now(_HONG_KONG).isoformat(timespec="seconds"),
    }
    code = str(code or "").strip().upper()
    if not re.fullmatch(r"(?:SH6\d{5}|SZ[03]\d{5}|BJ[489]\d{5})", code):
        result["reason"] = "local valuation history requires a canonical A-share code"
        return result
    try:
        cutoff = date.fromisoformat(asof) if asof else _hk_today()
    except (TypeError, ValueError):
        result["reason"] = "invalid valuation history cutoff date"
        return result
    if cutoff != _hk_today():
        result["reason"] = "current mutable bins are not evidence of historical or future availability"
        return result
    try:
        start = cutoff.replace(year=cutoff.year - 5)
    except ValueError:
        start = cutoff.replace(year=cutoff.year - 5, day=28)
    result.update({"requested_asof": cutoff.isoformat(), "window_start": start.isoformat()})
    try:
        frame, source_files = _read_local_valuation_frame(code, start.isoformat(), cutoff.isoformat())
        records = frame.to_dict(orient="records")
    except Exception as exc:  # independent, optional local leg; no network fallback
        result["reason"] = f"local valuation history read failed: {type(exc).__name__}: {str(exc)[:240]}"
        return result
    result["source_files"] = source_files
    dated = []
    invalid_dates = 0
    outside_window = 0
    for row in records:
        try:
            observed = date.fromisoformat(str(row.get("trade_date"))[:10])
        except (TypeError, ValueError):
            invalid_dates += 1
            continue
        if not start <= observed <= cutoff:
            outside_window += 1
            continue
        dated.append((observed.isoformat(), row))
    counts = Counter(day for day, _ in dated)
    duplicates = sorted(day for day, count in counts.items() if count > 1)
    eligible = sorted((day, row) for day, row in dated if counts[day] == 1)
    warnings = [{
        "code": "current_mutable_history_not_pit",
        "message": "本地逐日值仅用于当前研究；当前可变 bin 不证明历史时点已经可得，也不保证复现百度分位口径。",
    }]
    for metric, field in (("ttm_pe", "pe_ttm"), ("pb", "pb")):
        points = []
        missing_dates = []
        nonpositive_dates = []
        for observed, row in eligible:
            raw = row.get(field)
            try:
                value = float(raw) if not isinstance(raw, bool) else math.nan
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value):
                missing_dates.append(observed)
                continue
            if value <= 0:
                nonpositive_dates.append(observed)
                continue
            points.append({
                "value": value, "unit": "x", "period": "TTM" if metric == "ttm_pe" else observed,
                "date": observed, "as_of": observed,
                "ref": f"{source}:{code}:{field}:{observed}",
            })
        coverage = {
            "calendar_rows": len(eligible), "valid_positive_points": len(points),
            "missing_dates": missing_dates, "nonpositive_dates": nonpositive_dates,
            "duplicate_dates_excluded": duplicates, "invalid_date_rows": invalid_dates,
            "outside_window_rows_excluded": outside_window,
        }
        metric_warnings = list(warnings)
        if missing_dates or nonpositive_dates or duplicates or invalid_dates:
            metric_warnings.append({
                "code": "local_history_incomplete_coverage",
                "message": (f"{field}: 有效正数 {len(points)}/{len(eligible)}；"
                            f"缺测 {len(missing_dates)}、非正数 {len(nonpositive_dates)}、"
                            f"重复日期 {len(duplicates)}、无效日期 {invalid_dates}，未填充。"),
            })
        result["series"][metric] = {
            "metric": metric, "source": source, "code": code,
            "status": "available" if len(points) >= 20 else "incomplete",
            "as_of": points[-1]["as_of"] if points else None,
            "retrieved_at": result["retrieved_at"],
            "point_in_time": False, "historical_version_proven": False,
            "points": points, "coverage": coverage, "warnings": metric_warnings,
            "content_sha256": _digest(points),
        }
    result["status"] = "available" if all(
        series["status"] == "available" for series in result["series"].values()
    ) else "partial" if any(series["points"] for series in result["series"].values()) else "unavailable"
    return result


def _sec_valuation(
        code: str, asof: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Five-year valuation bands plus the latest deterministic F10 period.

    This is separate from ``quant``: a missing v4 ranking must not sever the
    company's valuation evidence.
    """
    historical = bool(asof and asof < _hk_today().isoformat())
    bands = None if historical else _valuation_bands(code)
    local_history = None if historical else _local_valuation_history(code, asof=asof)
    f10_value = None
    snapshot_receipts: List[Dict[str, Any]] = []
    unavailable_sources: List[Dict[str, Any]] = []
    try:
        f10_corpus = _engine_data_module("f10_corpus")
        facts = f10_corpus.load_facts(code, asof=asof).to_dict()
        f10_value = facts.get("valuation")
        snapshot_receipts = [
            item for item in (facts.get("snapshot_receipts") or [])
            if isinstance(item, dict) and item.get("source") == "f10:最新提示"
        ]
        unavailable_sources = [
            item for item in (facts.get("unavailable_sources") or [])
            if isinstance(item, dict) and item.get("source") in {"f10:最新提示", "f10:corpus"}
        ]
    except Exception:  # noqa: BLE001 — independent source leg
        f10_value = None
    series = (local_history or {}).get("series") or {}
    if not bands and not f10_value and not any(item.get("points") for item in series.values()):
        return None
    history = {k: v for k, v in (bands or {}).items() if k != "as_of"}
    result = {"as_of": (bands or {}).get("as_of")
            or (series.get("ttm_pe") or {}).get("as_of")
            or (f10_value or {}).get("report_period"),
            "history_5y": history or None, "f10": f10_value,
            "snapshot_receipts": snapshot_receipts,
            "unavailable_sources": unavailable_sources}
    if local_history is not None:
        result["history_local"] = {key: value for key, value in local_history.items() if key != "series"}
        result["history_series"] = series.get("ttm_pe")
        result["history_pb_series"] = series.get("pb")
    return result


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
            "dl": dl, "rerank": rerank, "valuation": _valuation_bands(code)}


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
    from guanlan_v2.seats.api import _ledger_events, _ledger_replay, _LEDGER_LOG
    snap = _ledger_replay(_ledger_events())
    if not snap.get("opened"):
        if _LEDGER_LOG.exists() and _LEDGER_LOG.stat().st_size:
            return {"status": "unavailable", "source": "personal_portfolio_ledger",
                    "scope": "personal_portfolio", "held": None,
                    "note": "已有台账未能重放成有效账户；不能认定未持仓。"}
        return {"status": "not_configured", "source": "personal_portfolio_ledger",
                "scope": "personal_portfolio", "retrieved_at": datetime.now(_HONG_KONG).isoformat(),
                "held": None, "note": "自有持仓台账尚未开立；本字段不是上市公司股东持股。"}
    pos = (snap.get("positions") or {}).get(code)
    if not pos:
        return {"status": "not_applicable", "source": "personal_portfolio_ledger",
                "scope": "personal_portfolio", "retrieved_at": datetime.now(_HONG_KONG).isoformat(),
                "held": False, "note": "已查询自有台账，当前未持有本票；不表示公司股东数据缺失。"}
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
    return {"status": "ok", "source": "personal_portfolio_ledger",
            "scope": "personal_portfolio", "retrieved_at": datetime.now(_HONG_KONG).isoformat(),
            "as_of": datetime.now(_HONG_KONG).isoformat(),
            "held": True, "avg_cost": avg_cost, "qty": qty, "upl": upl}


# ── 十五 section 注册表(顺序=company-research-evidence@2;打桩靶点=各 _sec_xxx)───
# 注意:按名字动态查 globals() 而非直接存函数引用——测试
# monkeypatch.setattr(evidence, "_sec_quote_live", stub) 改写的是模块属性(即
# module.__dict__/globals()),若这里在 import 时就把函数对象绑进元组,后续打桩不会生效。

_SECTION_NAMES: Tuple[str, ...] = (
    "quote_live", "company_facts", "announcements", "expectations",
    "valuation", "research_views", "fundflow", "board_eco", "sentiment",
    "kuaixun", "chain", "quant", "mainline", "macro", "holding",
)
_COMPANY_CORE_SECTIONS: Tuple[str, ...] = (
    "company_facts", "announcements", "expectations", "valuation",
)

# Keep immutable references to the two real PIT-aware functions.  Tests and
# callers deliberately monkeypatch ``_sec_xxx`` with one-argument stubs; those
# stubs must keep working and must not be mistaken for the real functions.
_PIT_SECTION_FUNCTIONS = {
    "company_facts": _sec_company_facts,
    "valuation": _sec_valuation,
}

_MANIFEST_SCHEMA = "report-evidence-manifest/v1"
_NO_DATA_STATUSES = frozenset({
    "empty", "unavailable", "error", "failed", "not_applicable", "not_configured",
})


def _section_has_data(section: Any) -> bool:
    return section is not None and not (
        isinstance(section, dict) and section.get("status") in _NO_DATA_STATUSES
    )


def _digest(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _manifest_cache_key(code: str, asof: str, mode: str) -> str:
    return _digest({
        "source": "platform-evidence",
        "schema_version": _SCHEMA_VERSION,
        "code": code,
        "asof": asof,
        "mode": mode,
    })


def _sign_historical_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    trust = _trusted_receipts_module()
    unsigned = dict(receipt)
    unsigned.pop("auth", None)
    auth = trust.sign_payload(trust.REPORT_SECTION_DOMAIN, unsigned)
    if auth is not None:
        receipt["auth"] = auth
    else:
        receipt.pop("auth", None)
    return receipt


def _verify_historical_receipt_auth(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    trust = _trusted_receipts_module()
    unsigned = dict(receipt)
    auth = unsigned.pop("auth", None)
    return bool(trust.verify_payload(trust.REPORT_SECTION_DOMAIN, unsigned, auth))


def _seal_evidence_manifest(pack: Dict[str, Any], *, historical: bool) -> None:
    pack.pop("manifest_digest", None)
    pack.pop("manifest_auth", None)
    if historical:
        trust = _trusted_receipts_module()
        auth = trust.sign_payload(trust.REPORT_MANIFEST_DOMAIN, pack)
        if auth is not None:
            pack["manifest_auth"] = auth
    pack["manifest_digest"] = _digest(pack)


def _verify_evidence_manifest_auth(pack: Any) -> bool:
    if not isinstance(pack, dict):
        return False
    trust = _trusted_receipts_module()
    unsigned = dict(pack)
    unsigned.pop("manifest_digest", None)
    auth = unsigned.pop("manifest_auth", None)
    return bool(trust.verify_payload(trust.REPORT_MANIFEST_DOMAIN, unsigned, auth))


def _receipt_date_ok(value: Any, requested_asof: str) -> bool:
    """Compare receipt times against the product's Hong Kong day boundary.

    Keep this pure instead of resolving it through ``_engine_data_module``:
    callers replace that resolver while injecting F10 fixtures, and receipt
    validation must remain independent of the selected data adapter.
    """
    if value in (None, ""):
        return False
    try:
        cutoff = datetime.combine(
            date.fromisoformat(requested_asof), time.max, tzinfo=_HONG_KONG,
        )
        text = str(value).strip()
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        local = parsed.replace(tzinfo=_HONG_KONG) if parsed.tzinfo is None \
            else parsed.astimezone(_HONG_KONG)
        return local <= cutoff
    except (TypeError, ValueError):
        return False


def _strict_snapshot_receipts(section: Any, requested_asof: str) -> List[Dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    valid: List[Dict[str, Any]] = []
    for item in section.get("snapshot_receipts") or []:
        if not isinstance(item, dict) or item.get("status") != "available":
            continue
        if item.get("requested_asof") != requested_asof:
            continue
        if item.get("point_in_time") is not True or item.get("historical_version_proven") is not True:
            continue
        if not _receipt_date_ok(item.get("data_asof"), requested_asof):
            continue
        pulled_at = item.get("pulled_at")
        pulled_present = pulled_at not in (None, "")
        pulled_ok = pulled_present and _receipt_date_ok(pulled_at, requested_asof)
        version_ok = bool(
            item.get("snapshot_version")
            and item.get("snapshot_immutable") is True
            and _receipt_date_ok(item.get("snapshot_asof"), requested_asof)
        )
        proof_ok = pulled_ok if pulled_present else version_ok
        if proof_ok and item.get("content_sha256"):
            valid.append(item)
    return valid


def _section_receipt(
    *, name: str, code: str, requested_asof: str, historical: bool,
    section: Any, generated_at: str, reason: str = "",
) -> Dict[str, Any]:
    content_sha = _digest(section) if section is not None else None
    if historical:
        underlying = _strict_snapshot_receipts(section, requested_asof)
        if section is not None and underlying:
            data_asof = max(str(item["data_asof"])[:10] for item in underlying)
            versions = sorted(str(item.get("snapshot_version") or item.get("content_sha256"))
                              for item in underlying)
            version = _digest(versions)
            return _sign_historical_receipt({
                "source": name, "status": "available",
                "requested_asof": requested_asof, "data_asof": data_asof,
                "pulled_at": None, "snapshot_version": version,
                "snapshot_asof": data_asof, "snapshot_immutable": True,
                "content_sha256": content_sha,
                "cache_key": _digest({"source": name, "code": code,
                                      "asof": requested_asof, "version": version,
                                      "content_sha256": content_sha}),
                "point_in_time": True, "historical_version_proven": True,
                "proof": "verified_underlying_snapshot_receipts", "reason": None,
            })
        reason = reason or "no immutable acquisition snapshot at/before requested asof"
        return _sign_historical_receipt({
            "source": name, "status": "unavailable",
            "requested_asof": requested_asof, "data_asof": None,
            "pulled_at": None, "snapshot_version": None,
            "snapshot_asof": None, "snapshot_immutable": False,
            "content_sha256": content_sha,
            "cache_key": _digest({"source": name, "code": code,
                                  "asof": requested_asof, "status": "unavailable"}),
            "point_in_time": False, "historical_version_proven": False,
            "proof": None, "reason": reason,
        })
    if name == "quote_live" and isinstance(section, dict):
        # Only a provider-owned observation/session date may date a quote.
        # ``retrieved_at`` is deliberately excluded: on weekends a retained
        # Friday quote can be fetched on Saturday without becoming Saturday
        # market data.
        explicit_data_asof = (
            section.get("market_data_as_of") or section.get("as_of")
        )
        if explicit_data_asof in (None, ""):
            reason = reason or (
                "quote source did not attest a market-data observation date; "
                "retrieved_at is acquisition time only"
            )
    elif name in {"sentiment", "holding"} and isinstance(section, dict):
        explicit_data_asof = section.get("as_of")
    else:
        explicit_data_asof = (
            (section or {}).get("as_of") or requested_asof
            if isinstance(section, dict) else requested_asof
        )
    current_status = "available" if _section_has_data(section) else (
        section.get("status", "empty") if isinstance(section, dict) else "empty"
    )
    return {
        "source": name, "status": current_status,
        "requested_asof": requested_asof,
        "data_asof": (
            str(explicit_data_asof)[:10]
            if explicit_data_asof not in (None, "") else None
        ),
        "pulled_at": generated_at, "snapshot_version": None,
        "snapshot_asof": None, "snapshot_immutable": False,
        "content_sha256": content_sha,
        "cache_key": _digest({"source": name, "code": code,
                              "asof": requested_asof, "content_sha256": content_sha}),
        # A current mutable read is usable now, but is not a historical version.
        "point_in_time": False, "historical_version_proven": False,
        "proof": "current_unversioned_read", "reason": reason or (
            section.get("note") if isinstance(section, dict) and not _section_has_data(section) else None
        ),
    }


def _normalise_cutoff(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError("asof must be an ISO date/time") from exc
    if parsed > _hk_today():
        raise ValueError(f"asof {parsed.isoformat()} is in the future")
    return parsed.isoformat()


def build_evidence_pack(
        code: str, out_dir: Optional[Path] = None, *,
        asof: Optional[str] = None) -> Dict[str, Any]:
    """组装研报证据包并落盘。十五 section 各自独立 try/except:单 section 异常 → 该键 null
    + errors[名]=原因(包永远能产出,不因单块挂而整体失败);合法的「查过但无数据」
    (如未持有/链外/预热中)也是 None,但不计入 errors——sections_ok 只区分「有没有异常」。

    返回 ``{ok, path, sections_ok:[名,...], errors:{名:原因,...}}``;
    落盘 ``var/reports/evidence/{norm_code}_{YYYYMMDDHHMM}.json``(spec §2 schema)。
    """
    norm = _norm_code(code)
    cutoff = _normalise_cutoff(asof)
    requested_asof = cutoff or _hk_today().isoformat()
    historical = requested_asof < _hk_today().isoformat()
    mode = "historical" if historical else "current"
    generated_at = datetime.now(_HONG_KONG).isoformat(timespec="seconds")
    if historical and _trusted_receipts_module().active_key() is None:
        reason = (
            "trusted historical signing key unavailable; configure "
            "GL_PIT_HMAC_KEY_ID and GL_PIT_HMAC_KEY"
        )
        return {
            "ok": False,
            "path": None,
            "schema_version": _SCHEMA_VERSION,
            "manifest_schema_version": _MANIFEST_SCHEMA,
            "asof": requested_asof,
            "mode": mode,
            "cache_key": _manifest_cache_key(norm, requested_asof, mode),
            "manifest_digest": None,
            "coverage": {
                "present": [],
                "missing": list(_SECTION_NAMES),
                "company_core": {
                    "required": list(_COMPANY_CORE_SECTIONS),
                    "missing": list(_COMPANY_CORE_SECTIONS),
                    "status": "incomplete",
                },
            },
            "sections_ok": [],
            "receipts": {},
            "unavailable_sources": [{"source": "manifest", "reason": reason}],
            "errors": {"_trust": reason},
        }
    sections: Dict[str, Any] = {}
    sections_ok: List[str] = []
    errors: Dict[str, str] = {}
    receipts: Dict[str, Dict[str, Any]] = {}
    unavailable_sources: List[Dict[str, str]] = []
    for name in _SECTION_NAMES:
        fn = globals()[f"_sec_{name}"]   # 动态查名,拿到打桩后的当前绑定
        real_pit_fn = _PIT_SECTION_FUNCTIONS.get(name)
        if historical and real_pit_fn is None:
            reason = "current/unversioned source is unavailable for strict historical research"
            sections[name] = None
            sections_ok.append(name)
            receipts[name] = _section_receipt(
                name=name, code=norm, requested_asof=requested_asof,
                historical=True, section=None, generated_at=generated_at,
                reason=reason,
            )
            unavailable_sources.append({"source": name, "reason": reason})
            continue
        try:
            if real_pit_fn is not None and fn is real_pit_fn:
                sections[name] = fn(norm, asof=cutoff)
            else:
                # Compatibility boundary: existing one-argument monkeypatch
                # stubs and all non-F10 realtime sections remain unchanged.
                sections[name] = fn(norm)
            sections_ok.append(name)
        except Exception as exc:  # noqa: BLE001 — 单 section 失败绝不拖垮整包
            sections[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"
        receipt = _section_receipt(
            name=name, code=norm, requested_asof=requested_asof,
            historical=historical, section=sections[name], generated_at=generated_at,
            reason=errors.get(name, ""),
        )
        receipts[name] = receipt
        if receipt.get("status") == "unavailable":
            unavailable_sources.append({
                "source": name,
                "reason": str(receipt.get("reason") or "unavailable"),
            })

    present = [name for name in _SECTION_NAMES if _section_has_data(sections.get(name))]
    missing = [name for name in _SECTION_NAMES if not _section_has_data(sections.get(name))
               and not (isinstance(sections.get(name), dict)
                        and sections[name].get("status") == "not_applicable")]
    core_missing = [name for name in _COMPANY_CORE_SECTIONS if not _section_has_data(sections.get(name))]
    coverage = {"present": present, "missing": missing,
                "company_core": {"required": list(_COMPANY_CORE_SECTIONS),
                                 "missing": core_missing,
                                 "status": "complete" if not core_missing else "incomplete"}}
    pack = {
        "schema_version": _SCHEMA_VERSION,
        "manifest_schema_version": _MANIFEST_SCHEMA,
        "code": norm, "asof": requested_asof, "mode": mode,
        "generated_at": generated_at,
        "cache_key": _manifest_cache_key(norm, requested_asof, mode),
        "coverage": coverage, "errors": errors,
        "sections_ok": sections_ok, "sections": sections,
        "section_status": {name: receipts[name]["status"] for name in _SECTION_NAMES},
        "receipts": receipts, "unavailable_sources": unavailable_sources,
    }
    if cutoff is not None:
        pack["cutoff_as_of"] = cutoff
    _seal_evidence_manifest(pack, historical=historical)
    out_base = Path(out_dir) if out_dir else _DEFAULT_OUT_DIR
    ts = datetime.now(_HONG_KONG).strftime("%Y%m%d%H%M%S%f")
    path = out_base / f"{norm}_{ts}.json"
    ok = True
    try:
        out_base.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — 落盘失败诚实 ok:False,section 结果仍如实返回
        ok = False
        errors.setdefault("_write", f"{type(exc).__name__}: {exc}")

    return {"ok": ok, "path": (str(path) if ok else None),
            "schema_version": _SCHEMA_VERSION,
            "manifest_schema_version": _MANIFEST_SCHEMA,
            "asof": requested_asof, "mode": mode,
            "cache_key": pack["cache_key"],
            "manifest_digest": pack["manifest_digest"],
            "coverage": coverage, "sections_ok": sections_ok,
            "section_status": pack["section_status"],
            "receipts": receipts, "unavailable_sources": unavailable_sources,
            "errors": errors}


__all__ = ["build_evidence_pack"]
