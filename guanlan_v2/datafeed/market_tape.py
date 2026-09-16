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
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.datafeed import live_client as _lc

_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_MARKET_TAPE_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "market_tape.json")))
_DEFAULT_TTL_S = int(os.environ.get("GUANLAN_MARKET_TAPE_TTL_S", "180"))
_CLOSED_TTL_S = int(os.environ.get("GUANLAN_MARKET_TAPE_CLOSED_TTL_S", "1800"))

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

# The four board pools are the core Market Tape contract.  LHB, rankings,
# northbound and industry rows are useful enrichments, but a partial outage in
# one of them must not make a fresh board snapshot look wholly dead.  Their age
# remains visible per source and through ``freshness.degraded_sources``.
_CORE_SOURCE_ALIASES = (
    "em_zt_pool", "em_zb_pool", "em_dt_pool", "em_yzt_pool",
)
_SESSION_DATED_ALIASES = (
    *_CORE_SOURCE_ALIASES, "eastmoney_lhb", "ths_hot_reason",
)

# A completed close is a data event, not a cache-age accident.  These sources
# describe one A-share session and remain analytically current after that
# session closes (and through weekends/pre-open) when the successful pull can
# be bound to the latest completed session.  Popularity lists are deliberately
# excluded: they continue to move after-hours and still use the bounded cache
# TTL below.
_CLOSED_SESSION_STABLE_ALIASES = (
    *_CORE_SOURCE_ALIASES,
    "eastmoney_lhb",
    "northbound",
    "industry_rank",
    "ths_hot_reason",
)
_DYNAMIC_AUX_ALIASES = ("em_hot_rank", "ths_hot_list")
_EXPECTED_NONEMPTY_ALIASES = ("northbound", "em_hot_rank", "ths_hot_list", "industry_rank")

# HKEX stopped disseminating Northbound real-time buy, sell and total turnover
# on 2024-05-13.  A legacy third-party series can still return plausible-looking
# numbers after that date, but those numbers no longer have the disclosure basis
# required for a "沪/深股通净流入" fact.  Keep transport health separate from
# metric availability and suppress both legs instead of publishing false
# precision.  After-close official *turnover* is a different metric and must not
# be substituted for net flow.
_NORTHBOUND_INTRADAY_WITHDRAWN_FROM = "20240513"
_NORTHBOUND_DISCLOSURE_NOTICE = (
    "https://www.hkex.com.hk/News/Market-Communications/2024/2404122news"
)

_LOCK = threading.Lock()
_REFRESH_INFLIGHT = [False]
_MEM_CACHE: Dict[str, Any] = {"data": None}


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


def _canon(alias: str) -> str:
    return _lc.resolve_source(alias) or alias


def _apply_northbound_disclosure_contract(
    rows: List[dict], content_session: Optional[str],
) -> tuple[List[dict], Dict[str, Any]]:
    """Suppress legacy pseudo-net values after the official disclosure change."""
    digits = "".join(ch for ch in str(content_session or "") if ch.isdigit())[:8]
    if len(digits) != 8 or digits < _NORTHBOUND_INTRADAY_WITHDRAWN_FROM:
        return rows, {}
    safe_rows: List[dict] = []
    for value in rows:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row["hgt_yi"] = None
        row["sgt_yi"] = None
        row["metric_status"] = "official_intraday_net_unavailable"
        safe_rows.append(row)
    return safe_rows, {
        "disclosure_status": "official_intraday_net_unavailable",
        "disclosure_effective_date": _NORTHBOUND_INTRADAY_WITHDRAWN_FROM,
        "disclosure_notice": _NORTHBOUND_DISCLOSURE_NOTICE,
    }


def _latest_session_yyyymmdd(today=None) -> Optional[str]:
    """Return the latest authoritative A-share session on/before ``today``.

    There is deliberately no Monday-to-Friday fallback.  If the configured
    calendar cannot be loaded, refresh falls back to the existing bounded
    upstream probe and leaves the board content date unknown rather than
    guessing through a holiday.
    """
    base = today or datetime.now().date()
    try:
        from guanlan_v2.channels.trading_calendar import AShareTradingCalendar

        days = AShareTradingCalendar().trading_days(
            base - timedelta(days=40), base,
        )
    except Exception:  # noqa: BLE001 — fail closed; no weekday guess
        return None
    if not days:
        return None
    return max(days).strftime("%Y%m%d")


def _market_clock(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return the session whose snapshot is expected at ``now``.

    On an A-share trading day the new session is not expected before 09:15.
    Treating 00:00--09:14 as "today" would incorrectly invalidate the prior
    close before the opening auction has started.  At/after 09:15 (including
    lunch) the expected session is today; after 15:10 it remains today's
    completed close.  Holidays/weekends use the latest authoritative session.
    """
    instant = now or datetime.now()
    calendar_latest = _latest_session_yyyymmdd(instant.date())
    today = instant.strftime("%Y%m%d")
    minute = instant.hour * 60 + instant.minute
    preopen = bool(calendar_latest == today and minute < 9 * 60 + 15)
    expected = (
        _latest_session_yyyymmdd(instant.date() - timedelta(days=1))
        if preopen else calendar_latest
    )
    # Include lunch in the active service window: the snapshot should continue
    # refreshing even though continuous matching is paused.
    active = bool(calendar_latest == today and 9 * 60 + 15 <= minute <= 15 * 60 + 10)
    phase = "unknown" if calendar_latest is None else (
        "preopen" if preopen else ("active" if active else "closed")
    )
    return {"latest_session": expected, "calendar_latest_session": calendar_latest,
            "today": today, "phase": phase, "active": active,
            "closed": False if expected is None else not active}


def _derive(sources: Dict[str, Any]) -> Dict[str, Any]:
    def rows(alias: str) -> List[dict]:
        return (sources.get(_lc.resolve_source(alias) or alias) or {}).get("rows") or []
    zt, zb, dt = rows("em_zt_pool"), rows("em_zb_pool"), rows("em_dt_pool")
    yzt = rows("em_yzt_pool")
    north = rows("northbound")
    streaks, breaks = [], 0
    for r in zt:
        # 最高连板取真连板 limit_days(lbc);绝不抓 zt_stat『N天M板』里的 M——那是统计窗内
        # 累计涨停次数 ct(≥ 真连板),会系统性推高打板温度、让 risk_off 冰点闸更难触发。
        streaks.append(int(r.get("limit_days") or 1))
        if int(r.get("break_times") or 0) > 0:
            breaks += 1
    zt_n, zb_n = len(zt), len(zb)
    promoted = sum(1 for r in zt if int(r.get("limit_days") or 1) >= 2)
    # 连板梯队直方图:温度是标量、梯队是形状。首板堆量(虚胖)vs 高标接力(真亢奋)标量分辨不出。
    ladder = {"first": 0, "2": 0, "3": 0, "4": 0, "5plus": 0}
    for lb in streaks:                            # streaks 已是每只真连板 lbc(缺省 1)
        if lb <= 1:
            ladder["first"] += 1
        elif lb >= 5:
            ladder["5plus"] += 1
        else:
            ladder[str(lb)] += 1
    # 三个语义不同的口径,分别命名消歧(评审「两套炸板率定义并存」的收敛):
    #   break_ratio = 开板率  = 涨停中曾开板家数 / 涨停数(astock 打板温度用此·系数已标定,不并入)
    #   break_rate  = 炸板率  = 炸板数 / 涨停尝试 = zb/(zt+zb)(收敛 limit_up_sentiment 权威口径)
    #   promotion_rate = 晋级率 = 今日涨停池里真连板(limit_days>=2)家数 / 昨日涨停池家数
    #                    分子改数今日 em_zt_pool 的真连板(每只 limit_days>=2 必是昨日也涨停=晋级成功),
    #                    替代旧的『昨池今日 pct≥9.8』代理(双创未封误算+/ST 封板漏算-);零新增拉取。
    # 空池诚实:无涨停/无昨池 → 派生值 None,绝不用假 0.0 冒充真事实(进研报/UI)。
    d: Dict[str, Any] = {"zt_count": zt_n, "zb_count": zb_n, "dt_count": len(dt),
                         "yzt_count": len(yzt),
                         "max_streak": max(streaks) if streaks else None,
                         "break_ratio": round(breaks / zt_n, 4) if zt else None,
                         "break_rate": round(zb_n / (zt_n + zb_n), 4) if (zt_n + zb_n) else None,
                         "promotion_rate": round(promoted / len(yzt), 4) if yzt else None,
                         "ladder": ladder if zt else None,
                         "north_net": None, "north_scope": None}
    if north and isinstance(north[0], dict):     # 北向净额=最新一分钟(newest-first)沪股通+深股通,单位亿
        r0 = north[0]
        hg, sg = r0.get("hgt_yi"), r0.get("sgt_yi")
        if hg is not None or sg is not None:
            try:
                d["north_net"] = round(float(hg or 0) + float(sg or 0), 2)
                # sg 为 None = stocks 已判深股通源退化置空 → 诚实标『仅沪股通』,不冒充沪+深(vibe⑥实测坐实)
                d["north_scope"] = "沪+深股通" if sg is not None else "沪股通"
            except (TypeError, ValueError):
                pass
        else:                                     # 兜底:别的源若给单一净额字段
            for k in ("net", "net_inflow", "north_net", "成交净买额", "净买额"):
                v = r0.get(k)
                if v is not None:
                    try:
                        d["north_net"] = float(v)
                        break
                    except (TypeError, ValueError):
                        pass
    return d


_POOL_ALIASES = ("em_zt_pool", "em_zb_pool", "em_dt_pool", "em_yzt_pool")
_BACKFILL_MAX_DAYS = 8   # 往回探交易日上限(覆盖周末+短假;超长假如春节>8日则诚实空由 _derive 兜)


def _backfill_board_pools(sources: Dict[str, Any], today=None):
    """今日涨停池空(周末/盘前/长假)→ 往回探至上一有数据交易日(≤8天),用同一日期重拉四池,
    打「回溯至 MM-DD」徽章,返回 (board_date_yyyymmdd, backfilled)。非交易日上游静默返空,
    首个非空即上一交易日;找不到(超上限/全宕)→ (None, False),诚实空由 _derive 兜。
    仅在今日池空时回溯,故正常交易时段零额外触网。"""
    zt_canon = _lc.resolve_source("em_zt_pool") or "em_zt_pool"
    if (sources.get(zt_canon) or {}).get("rows"):
        return None, False                          # 今日有数据,无需回溯
    base = today or datetime.now().date()
    for k in range(1, _BACKFILL_MAX_DAYS + 1):
        d = base - timedelta(days=k)
        ds = d.strftime("%Y%m%d")
        # 两段式(2026-07-28 评审修):空候选日只探涨停池 1 条(与旧实现同上游成本——
        # 长假可有多个空日,四池齐拉会把空日上游请求量翻 4 倍);命中日再把其余三池
        # 一批拉(1 个子进程,较旧实现省 2 次起跑)。
        try:
            zt_probe = _lc.probe("em_zt_pool", date=ds, limit=300)
        except Exception:  # noqa: BLE001
            continue
        zt_rows = _lc.native_rows(zt_probe.get("items")) if (
            zt_probe.get("ok") and zt_probe.get("status") == "ok") else []
        if not zt_rows:
            continue                                # 非交易日/无数据,继续往回
        badge = f"回溯至 {d.strftime('%m-%d')}"
        attempt_at = datetime.now().isoformat(timespec="seconds")
        sources[zt_canon] = {"status": "ok", "n": len(zt_rows),
                             "pulled_at": zt_probe.get("pulled_at") or attempt_at,
                             "content_date": ds, "last_attempt_at": attempt_at,
                             "last_attempt_status": "ok", "last_attempt_error": "",
                             "consecutive_failures": 0, "note": badge, "rows": zt_rows}
        rest = _POOL_ALIASES[1:]
        try:
            rs = _lc.probe_many([{"source": a, "date": ds, "limit": 300} for a in rest])
        except Exception:  # noqa: BLE001
            rs = []
        for alias, r in zip(rest, rs):              # 其余三池同日对齐
            canon = _lc.resolve_source(alias) or alias
            if r.get("ok") and r.get("status") in ("ok", "empty"):
                rr = _lc.native_rows(r.get("items"))
                status = r.get("status") or "ok"
                sources[canon] = {"status": status, "n": len(rr),
                                  "pulled_at": r.get("pulled_at") or attempt_at,
                                  "content_date": ds, "last_attempt_at": attempt_at,
                                  "last_attempt_status": status, "last_attempt_error": "",
                                  "consecutive_failures": 0, "note": badge, "rows": rr}
        return ds, True
    return None, False


def _refresh(ttl_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    """10 源经 live_client.probe_many **一次批量**拉取(2026-07-28 提速:此前逐源 10 个
    子进程 + 1s 起跑间隔 ≈10-26s,批量后一个子进程 ≈3-5s;批量通道失败由 probe_many
    内部诚实回退逐条),原子落盘。失败源保留上一轮条目(局部陈旧诚实显形,不整份作废)。"""
    prev = _load_cache() or {}
    prev_sources = prev.get("sources") or {}
    refresh_now = datetime.now()
    now_iso = refresh_now.isoformat(timespec="seconds")
    sources: Dict[str, Any] = {}
    # Several pool APIs return the last trading day's rows on weekends while
    # stamping the request time as pulled_at.  Resolve and request the content
    # session explicitly so retrieval time cannot masquerade as trade date.
    session_date = _market_clock(refresh_now).get("latest_session")
    reqs = []
    for spec in _SOURCES:
        kw = dict(spec["kw"])
        if session_date and spec["sid"] in _SESSION_DATED_ALIASES:
            kw["date"] = session_date
        reqs.append({"source": spec["sid"], **kw})
    try:
        results = _lc.probe_many(reqs)
    except Exception as exc:  # noqa: BLE001
        results = [{"ok": False, "note": f"{type(exc).__name__}: {exc}"} for _ in _SOURCES]
    if len(results) != len(_SOURCES):   # 长度失配=通道异常,整批按失败处理(保留上轮)
        results = [{"ok": False, "note": "probe_many 返回长度失配"} for _ in _SOURCES]
    refreshed_ids = set()
    for spec, r in zip(_SOURCES, results):
        alias = spec["sid"]
        canon = _lc.resolve_source(alias) or alias
        # 成功闸只认 status=="ok"(2026-07-28 评审修既有缺陷):probe 机械失败信封是
        # ok:True/status:""——旧闸 in ("ok","") 会把它当"成功拉到 0 行"用空行覆盖上轮
        # 并盖新鲜 pulled_at,击穿"失败保留上轮"红线。真成功恒 status:"ok"(_shape_payload
        # 归一),status:"" 只可能是机械/caller 失败。
        # ``empty`` is a successful semantic result, not a transport failure.
        # Keeping yesterday's rows when today's pool is truly empty corrupts
        # dt_count/break_rate/LHB, so only error/stale/circuit states retain old.
        if r.get("ok") and r.get("status") in ("ok", "empty"):
            rows = _lc.native_rows(r.get("items"))
            policy_meta: Dict[str, Any] = {}
            if alias == "northbound":
                rows, policy_meta = _apply_northbound_disclosure_contract(
                    rows, session_date or refresh_now.strftime("%Y%m%d"),
                )
            refreshed_ids.add(canon)
            source_record = {"status": r.get("status") or "ok",
                             "n": len(rows),
                             "pulled_at": r.get("pulled_at") or now_iso,
                             # Retrieval binding, distinct from content_date:
                             # some quote/rank endpoints do not expose a
                             # trustworthy row-level trading date.
                             "market_session": session_date,
                             "content_date": (session_date if session_date
                                              and alias in _SESSION_DATED_ALIASES else None),
                             "last_attempt_at": now_iso,
                             "last_attempt_status": r.get("status") or "ok",
                             "last_attempt_error": "", "consecutive_failures": 0,
                             "note": r.get("note") or "", "rows": rows}
            if policy_meta:
                source_record.update(policy_meta)
                source_record["note"] = (
                    "北向盘中净流入自 2024-05-13 起不再由交易所公开；"
                    "旧第三方沪/深分腿值已抑制，不以盘后成交总额冒充净流入"
                )
            sources[canon] = source_record
        else:   # 本轮失败/planned/error → 保留上一轮该源
            note = (r.get("note") or r.get("error") or "本轮拉取失败")
            old = prev_sources.get(canon)
            if old:
                kept = dict(old)
                kept["note"] = f"(旧)|新失败:{note}"[:400]
                kept["last_attempt_at"] = now_iso
                kept["last_attempt_status"] = str(r.get("status") or "error")
                kept["last_attempt_error"] = str(note)[:400]
                kept["consecutive_failures"] = int(old.get("consecutive_failures") or 0) + 1
                sources[canon] = kept
            else:
                sources[canon] = {"status": "error", "n": 0, "pulled_at": None,
                                  "content_date": None, "last_attempt_at": now_iso,
                                  "last_attempt_status": str(r.get("status") or "error"),
                                  "last_attempt_error": str(note)[:400],
                                  "consecutive_failures": 1, "note": note, "rows": []}

    # Two display legs have a same-domain alternative already exposed by the
    # unified live-client catalog.  Keep the primary failure visible, but serve
    # current usable rows instead of a five-day-old list:
    #   industry comparison -> sector fund-flow rows re-ranked by change_pct
    #   Eastmoney popularity -> already-fetched THS popularity rows
    # These remain ``degraded`` (last_attempt_status=fallback_ok), not greenwashed.
    industry_id = _canon("industry_rank")
    industry_item = sources.get(industry_id) or {}
    if industry_item.get("last_attempt_status") not in ("ok", "empty"):
        try:
            alt = _lc.probe("eastmoney_sector_fund_flow", code="industry", limit=100)
        except Exception as exc:  # noqa: BLE001 — primary retained above
            alt = {"ok": False, "note": f"{type(exc).__name__}: {exc}"}
        if alt.get("ok") and alt.get("status") in ("ok", "empty"):
            alt_rows = [row for row in _lc.native_rows(alt.get("items")) if isinstance(row, dict)]
            for row in alt_rows:
                if row.get("pct") is None:
                    row["pct"] = row.get("change_pct")
            def _change_pct(row: Dict[str, Any]) -> float:
                try:
                    return float(row.get("change_pct"))
                except (TypeError, ValueError):
                    return float("-inf")
            alt_rows.sort(key=_change_pct, reverse=True)
            sources[industry_id] = {
                "status": alt.get("status") or "ok", "n": len(alt_rows),
                "pulled_at": alt.get("pulled_at") or now_iso, "content_date": None,
                "market_session": session_date,
                "last_attempt_at": now_iso, "last_attempt_status": "fallback_ok",
                "last_attempt_error": str(industry_item.get("last_attempt_error") or "")[:400],
                "consecutive_failures": int(industry_item.get("consecutive_failures") or 0),
                "fallback_source": "eastmoney_sector_fund_flow",
                "note": "主行业涨幅源不可用；已用行业资金流源按 change_pct 重排(最新可得)",
                "rows": alt_rows,
            }

    hot_id, ths_hot_id = _canon("em_hot_rank"), _canon("ths_hot_list")
    hot_item, ths_hot_item = sources.get(hot_id) or {}, sources.get(ths_hot_id) or {}
    if (hot_item.get("last_attempt_status") not in ("ok", "empty")
            and ths_hot_item.get("status") in ("ok", "empty")
            and ths_hot_item.get("last_attempt_status") in ("ok", "empty")):
        rows = [dict(row) for row in (ths_hot_item.get("rows") or []) if isinstance(row, dict)]
        sources[hot_id] = {
            "status": ths_hot_item.get("status") or "ok", "n": len(rows),
            "pulled_at": ths_hot_item.get("pulled_at") or now_iso, "content_date": None,
            "market_session": session_date,
            "last_attempt_at": now_iso, "last_attempt_status": "fallback_ok",
            "last_attempt_error": str(hot_item.get("last_attempt_error") or "")[:400],
            "consecutive_failures": int(hot_item.get("consecutive_failures") or 0),
            "fallback_source": "ths_hot_list",
            "note": "东方财富人气榜不可用；已回落同花顺热榜(口径不同)", "rows": rows,
        }
    # If the authoritative target was successfully queried, a semantic empty is
    # still today's/last-session truth and must not be replaced by an older busy
    # day.  Backfill is only a fail-closed escape hatch for an unavailable target.
    zt_canon = _canon("em_zt_pool")
    fallback_date, fallback_used = None, False
    if zt_canon not in refreshed_ids and not (sources.get(zt_canon) or {}).get("rows"):
        fallback_date, fallback_used = _backfill_board_pools(sources, today=refresh_now.date())

    core_ids = [_canon(alias) for alias in _CORE_SOURCE_ALIASES]
    core_content_dates = {
        sid: (sources.get(sid) or {}).get("content_date") for sid in core_ids
    }
    known_core_dates = [d for d in core_content_dates.values() if d]
    core_session_aligned = bool(
        len(known_core_dates) == len(core_ids) and len(set(known_core_dates)) == 1
    )
    core_content_date = known_core_dates[0] if core_session_aligned else None
    board_date = core_content_date
    board_backfilled = bool(
        board_date and (fallback_used or board_date != refresh_now.strftime("%Y%m%d"))
    )

    # overall 取**最旧**分量(min):失败被保留的陈旧源会把整体龄期拖旧,health/UI/read_tape
    # 的兼容字段继续保留此语义。新增 core_pulled_at 单独表达核心四池水位，避免
    # 一个可选行业榜旧源把核心盘口误判为停摆，同时不隐藏任何旧分量。
    pulled_list = [v["pulled_at"] for v in sources.values() if v.get("pulled_at")]
    overall = min(pulled_list) if pulled_list else prev.get("pulled_at")
    core_pulled = [
        (sources.get(sid) or {}).get("pulled_at") for sid in core_ids
        if (sources.get(sid) or {}).get("pulled_at")
    ]
    core_watermark = (
        min(core_pulled) if core_pulled
        else prev.get("core_pulled_at") or prev.get("pulled_at")
    )
    core_refresh_complete = all(sid in refreshed_ids for sid in core_ids)
    target_session_complete = bool(
        core_refresh_complete and core_session_aligned
        and (session_date is None or core_content_date == session_date)
    )
    snapshot_refreshed_at = (
        now_iso if target_session_complete
        else prev.get("snapshot_refreshed_at")
    )
    derived = _derive(sources)
    derived["core_session_aligned"] = core_session_aligned
    derived["core_content_date"] = core_content_date
    if not core_session_aligned:
        # Never combine today's zt pool with retained prior-session zb/dt/yzt.
        # Northbound is an independent leg and may remain available; all board
        # ecology values are unavailable until the four pools share one date.
        for key in ("zt_count", "zb_count", "dt_count", "yzt_count", "max_streak",
                    "break_ratio", "break_rate", "promotion_rate", "ladder"):
            derived[key] = None
        derived["degraded_reason"] = "core_session_mixed_or_unknown"
    data = {"pulled_at": overall, "core_pulled_at": core_watermark,
            "snapshot_refreshed_at": snapshot_refreshed_at,
            "refresh_attempted_at": now_iso,
            "core_refresh_complete": core_refresh_complete,
            "core_session_aligned": core_session_aligned,
            "core_content_date": core_content_date,
            "core_content_dates": core_content_dates,
            "expected_session": session_date,
            "core_sources": core_ids,
            "ttl_s": ttl_s, "sources": sources, "derived": derived,
            "board_date": board_date, "board_backfilled": board_backfilled}
    _write_cache_atomic(data)
    _MEM_CACHE["data"] = data
    return data


def _freshness(data: Dict[str, Any], fresh_within_s: int = _DEFAULT_TTL_S) -> Dict[str, Any]:
    now = datetime.now()

    def age(iso: Any) -> Optional[int]:
        try:
            return int((now - datetime.fromisoformat(str(iso))).total_seconds())
        except (TypeError, ValueError):
            return None
    sources = data.get("sources") or {}
    per = {sid: age(v.get("pulled_at")) for sid, v in sources.items()}
    core_ids = list(data.get("core_sources") or [_canon(a) for a in _CORE_SOURCE_ALIASES])
    core_times = [
        (sources.get(sid) or {}).get("pulled_at") for sid in core_ids
        if (sources.get(sid) or {}).get("pulled_at")
    ]
    core_anchor = data.get("core_pulled_at") or (
        min(core_times) if core_times else data.get("pulled_at")
    )
    missing_core = [
        sid for sid in core_ids
        if not isinstance(sources.get(sid), dict)
        or sources[sid].get("status") not in ("ok", "empty")
        or not sources[sid].get("pulled_at")
    ]
    aged_core = [
        sid for sid in core_ids
        if sid in sources and (per.get(sid) is None or per[sid] > fresh_within_s)
    ]
    clock = _market_clock(now)
    source_age_limit = (
        max(fresh_within_s, _CLOSED_TTL_S) if clock.get("closed") else fresh_within_s
    )
    expected_session = clock.get("latest_session")
    stable_ids = {_canon(alias) for alias in _CLOSED_SESSION_STABLE_ALIASES}
    dynamic_ids = {_canon(alias) for alias in _DYNAMIC_AUX_ALIASES}
    expected_nonempty_ids = {_canon(alias) for alias in _EXPECTED_NONEMPTY_ALIASES}

    def _compact_day(value: Any) -> Optional[str]:
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:8] if len(digits) >= 8 else None

    def _parse_local(value: Any) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(str(value))
            # All Market Tape timestamps are local wall-clock values.  If an
            # upstream row is aware, compare using its own local clock rather
            # than mixing aware and naive datetimes.
            return parsed.replace(tzinfo=None)
        except (TypeError, ValueError):
            return None

    def _is_completed_session_item(item: Dict[str, Any]) -> bool:
        """Prove that a successful source item belongs to the completed close.

        ``content_date`` is authoritative when present.  For endpoints without
        a row-level date, the refresh transaction's explicit expected_session
        may bind the item only when its attempt timestamp is the transaction's
        own timestamp.  A same-day pull must be at/after 15:00; a later-day pull
        of an earlier completed session is also valid.
        """
        if not clock.get("closed") or not expected_session:
            return False
        if item.get("status") not in ("ok", "empty"):
            return False
        if item.get("last_attempt_status") not in (None, "ok", "empty"):
            return False
        if "新失败" in str(item.get("note") or ""):
            return False
        bound_session = _compact_day(item.get("content_date") or item.get("market_session"))
        attempt_value = item.get("last_attempt_at") or item.get("pulled_at")
        if not bound_session:
            refresh_attempt = data.get("refresh_attempted_at")
            if (str(attempt_value or "") == str(refresh_attempt or "")
                    and _compact_day(data.get("expected_session")) == expected_session):
                bound_session = expected_session
        if bound_session != expected_session:
            return False
        attempt = _parse_local(attempt_value)
        if attempt is None:
            return False
        attempt_day = attempt.strftime("%Y%m%d")
        return bool(attempt_day > expected_session or (
            attempt_day == expected_session and (attempt.hour, attempt.minute) >= (15, 0)
        ))

    details: Dict[str, Dict[str, Any]] = {}

    def _flag(sid: str, code: str, reason: str) -> None:
        source_item = sources.get(sid)
        source_item = source_item if isinstance(source_item, dict) else {}
        source_rows = source_item.get("rows") if isinstance(source_item.get("rows"), list) else []
        try:
            row_count = int(source_item.get("n") if source_item.get("n") is not None
                            else len(source_rows))
        except (TypeError, ValueError):
            row_count = len(source_rows)
        entry = details.setdefault(sid, {
            "source": sid,
            "reason_codes": [],
            "reasons": [],
            "age_s": per.get(sid),
            "status": source_item.get("status"),
            "last_attempt_status": source_item.get("last_attempt_status"),
            "rows": row_count,
        })
        if code not in entry["reason_codes"]:
            entry["reason_codes"].append(code)
            entry["reasons"].append(reason)

    for sid, item in sources.items():
        source_age = per.get(sid)
        if not isinstance(item, dict):
            _flag(sid, "invalid_source_record", "源记录结构不可解析")
            continue
        if item.get("status") not in ("ok", "empty"):
            _flag(sid, "source_status_error", "当前源状态不是成功或合法空")
        attempt_status = item.get("last_attempt_status")
        if attempt_status not in (None, "ok", "empty"):
            if attempt_status == "fallback_ok" or item.get("fallback_source"):
                _flag(sid, "fallback_active", "主源失败，当前使用备用口径")
            else:
                _flag(sid, "last_attempt_failed", "最近一次抓取失败，保留旧值")
        elif "新失败" in str(item.get("note") or ""):
            _flag(sid, "last_attempt_failed", "最近一次抓取失败，保留旧值")

        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if item.get("status") == "empty" and sid in expected_nonempty_ids:
            _flag(sid, "unexpected_empty", "该排行榜/跨境资金源本轮为零行")

        # The THS endpoint can return a syntactically successful series with
        # only one Stock Connect leg.  This is usable but not the advertised
        # 沪+深 total, so keep it yellow for coverage rather than age/failure.
        if (sid == _canon("northbound")
                and item.get("disclosure_status") == "official_intraday_net_unavailable"):
            _flag(
                sid,
                "official_intraday_metric_unavailable",
                "规则调整后沪股通和深股通盘中净流入均不再公开；当前仅验证通路，不发布伪精确值",
            )
        elif sid == _canon("northbound") and rows:
            hgt = any(isinstance(row, dict) and row.get("hgt_yi") is not None for row in rows)
            sgt = any(isinstance(row, dict) and row.get("sgt_yi") is not None for row in rows)
            if not hgt or not sgt:
                missing = "沪股通和深股通" if not hgt and not sgt else ("沪股通" if not hgt else "深股通")
                _flag(sid, "partial_market_coverage", f"仅部分可用：{missing}分量缺失")

        age_expired = source_age is None or source_age > source_age_limit
        completed_close = sid in stable_ids and _is_completed_session_item(item)
        if age_expired and not completed_close:
            if source_age is None:
                _flag(sid, "missing_pull_time", "缺少可验证的抓取时间")
            elif sid in dynamic_ids and clock.get("closed"):
                minutes = max(1, int(source_age_limit / 60))
                _flag(sid, "dynamic_cache_expired",
                      f"盘后仍会变化的热度榜缓存超过 {minutes} 分钟")
            else:
                _flag(sid, "cache_expired", "缓存超过当前阶段的新鲜度窗口")
    core_age = age(core_anchor)
    oldest_age = age(data.get("pulled_at"))
    mismatched_core = [
        sid for sid in core_ids
        if expected_session and (sources.get(sid) or {}).get("content_date") != expected_session
    ]
    closed_snapshot_valid = bool(
        clock.get("closed") and expected_session
        and data.get("board_date") == expected_session
        and not missing_core and not mismatched_core
        and all(_is_completed_session_item(sources.get(sid) or {}) for sid in core_ids)
    )
    for sid in mismatched_core:
        _flag(sid, "session_mismatch", "核心池交易日与最近完成交易日不一致")
    degraded = sorted(details)
    stale_core = [] if closed_snapshot_valid else aged_core
    stale = bool(missing_core or mismatched_core or (
        not closed_snapshot_valid
        and (stale_core or core_age is None or core_age > fresh_within_s)
    ))
    attempted_age = age(data.get("refresh_attempted_at")
                        or data.get("snapshot_refreshed_at") or core_anchor)
    refresh_ttl = source_age_limit
    refresh_due = attempted_age is None or attempted_age > refresh_ttl
    return {"overall_age_s": core_age, "oldest_source_age_s": oldest_age,
            "per_source": per, "core_sources": core_ids,
            "missing_core_sources": sorted(set(missing_core)),
            "mismatched_core_sources": sorted(set(mismatched_core)),
            "stale_core_sources": sorted(set(stale_core)),
            "degraded_sources": degraded,
            "degraded_details": [details[sid] for sid in degraded],
            "market_closed": clock.get("closed"),
            "latest_session": expected_session, "market_phase": clock.get("phase"),
            "closed_snapshot_valid": closed_snapshot_valid,
            "refresh_attempt_age_s": attempted_age, "refresh_due": refresh_due,
            "stale": stale, "degraded": bool(degraded)}


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
    try:
        threading.Thread(target=_run, name="market_tape_refresh", daemon=True).start()
    except Exception:  # noqa: BLE001 — 线程起不来(资源耗尽)→ 立即复位旗,不永久冻结禁刷
        with _LOCK:
            _REFRESH_INFLIGHT[0] = False
        return False
    return True


def read_tape(fresh_within_s: int = _DEFAULT_TTL_S, *,
              allow_refresh: bool = True) -> Dict[str, Any]:
    """SWR 只读:读永远秒回缓存;缺失→warming+触发首拉;过期→返回旧值+触发后台刷新。
    绝不阻塞在网络、绝不伪造新鲜(freshness 龄期显形)。"""
    refresh_state = "idle" if allow_refresh else "read_only"
    data = _MEM_CACHE.get("data") or _load_cache()
    if not data:
        if allow_refresh:
            started = _trigger_refresh(fresh_within_s)
            refresh_state = "started" if started else "inflight" if _REFRESH_INFLIGHT[0] else "start_failed"
        return {"ok": True, "warming": True, "pulled_at": None, "sources": {}, "derived": {},
                "refresh_state": refresh_state,
                "freshness": {"overall_age_s": None, "per_source": {}, "stale": True},
                "note": ("预热未能启动;当前没有可用缓存" if refresh_state == "start_failed" else
                         "预热中,后台首拉已触发;稍后重试" if allow_refresh
                         else "只读模式无盘口缓存;未启动刷新")}
    _MEM_CACHE["data"] = data
    fr = _freshness(data, fresh_within_s)
    stale = bool(fr["stale"])
    note = ""
    if fr.get("refresh_due"):
        if allow_refresh:
            started = _trigger_refresh(fresh_within_s)
            refresh_state = "started" if started else "inflight" if _REFRESH_INFLIGHT[0] else "start_failed"
            note = ("后台刷新未能启动;本次仅返回旧缓存,不能视为最新数据"
                    if refresh_state == "start_failed" else
                    "核心缓存过期,已触发后台刷新;本次返回现有值(龄期见 freshness)"
                    if stale else "已到刷新周期,后台刷新中;当前核心快照仍可用")
        else:
            note = ("只读模式返回现有盘口缓存;缓存已过期且未启动刷新"
                    if stale else "只读模式返回现有盘口缓存;未启动周期刷新")
    return {"ok": True, "warming": False, "pulled_at": data.get("pulled_at"),
            "refresh_state": refresh_state,
            "core_pulled_at": data.get("core_pulled_at") or data.get("pulled_at"),
            "snapshot_refreshed_at": data.get("snapshot_refreshed_at"),
            "refresh_attempted_at": data.get("refresh_attempted_at"),
            "expected_session": data.get("expected_session") or fr.get("latest_session"),
            "core_session_aligned": bool(data.get("core_session_aligned")),
            "core_content_date": data.get("core_content_date"),
            "board_date": data.get("board_date"),
            "board_backfilled": bool(data.get("board_backfilled")),
            "sources": data.get("sources") or {}, "derived": data.get("derived") or {},
            "freshness": fr, "note": note}


__all__ = ["read_tape", "_refresh", "_derive"]
