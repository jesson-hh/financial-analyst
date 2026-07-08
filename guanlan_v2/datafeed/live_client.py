"""统一实时源客户端 —— 观澜现拉的唯一门户(数据中台件①)。

子进程跑 stocks 正典 CLI `scripts/probe_live_sources.py`(统一信封 status:ok|planned|error),
零重造零落盘只读。模块级跨调用最小间隔补「跨进程零共享节流」缺口(东财防封);
catalog 动态派生源目录(缓存 1h,探针失败回静态兜底表)。
诚实约定:caller/机械错误 → ok:False;上游 planned/error → ok:True 经 status/error 显形,恒不编造。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_STOCKS_PROBE = Path(os.environ.get("GUANLAN_LIVE_PROBE",
                                    r"G:\stocks\scripts\probe_live_sources.py"))
_MIN_INTERVAL_S = 1.0     # 相邻子进程起跑最小间隔(probe 内另有请求级串行限流)
_TIMEOUT_S = 90
_CLIP = 400               # 顶层长字符串截断(截断非编造)
_CATALOG_TTL_S = 3600.0

_LOCK = threading.Lock()
_LAST_LAUNCH = [0.0]
_CATALOG_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": None}

# 静态兜底目录(source_id -> alias;与 stocks LIVE_SOURCE_REGISTRY 手抄同步,source_id
# 集合漂移由 test_datafeed_client::test_static_sources_reconcile_with_stocks_registry 守护
# (读 stocks 注册表文件对账,stocks 缺席则 skip);catalog 探针失败时仅用于校验放行。
# alias 为加法式 resolve 键,不要求与 stocks alias 字段逐一相等)。
_STATIC_SOURCES: Dict[str, str] = {
    "eastmoney_stock_news": "stock_news",
    "eastmoney_global_news": "global_news",
    "eastmoney_research_reports": "research",
    "eastmoney_industry_reports": "industry_research",
    "cninfo_announcements": "announcements",
    "cninfo_irm": "irm",
    "eastmoney_concept_blocks": "concept_blocks",
    "ths_hot_reason": "hot_reason",
    "em_limit_up_pool": "em_zt_pool",
    "ths_limit_up_pool": "ths_limit_up",
    "ths_hot_list": "hot_list",
    "eastmoney_hot_rank": "em_hot_rank",
    "eastmoney_hot_concept": "em_hot_concept",
    "em_zb_pool": "zb_pool",
    "em_dt_pool": "dt_pool",
    "em_yzt_pool": "yzt_pool",
    "eastmoney_fund_flow": "fund_flow",
    "eastmoney_fund_flow_minute": "fund_flow_minute",
    "eastmoney_lhb": "lhb",
    "eastmoney_lhb_stock": "lhb_stock",
    "eastmoney_unlock": "unlock",
    "eastmoney_margin": "margin",
    "eastmoney_block_trade": "block_trade",
    "eastmoney_holder_change": "holder_change",
    "eastmoney_dividend": "dividend",
    "tencent_realtime_quote": "realtime_quote",
    "ths_hsgt_realtime": "northbound",
    "eastmoney_industry_comparison": "industry_rank",
    "ths_eps_forecast": "eps_forecast",
    "eastmoney_sector_fund_flow": "sector_fund_flow",
    "iwencai_search": "iwencai",
}

# 必带 code 的源(6 位股票代码;lhb_stock/unlock 等个股口径)
NEED_CODE = {
    "eastmoney_stock_news", "cninfo_announcements", "eastmoney_concept_blocks",
    "cninfo_irm", "eastmoney_hot_concept", "eastmoney_research_reports",
    "eastmoney_fund_flow", "eastmoney_fund_flow_minute", "eastmoney_lhb_stock",
    "eastmoney_unlock", "eastmoney_margin", "eastmoney_block_trade",
    "eastmoney_holder_change", "eastmoney_dividend", "tencent_realtime_quote",
    "ths_eps_forecast",   # 同花顺一致预期 EPS,按 6 位股票代码查
}
# date 缺省补当日 YYYYMMDD 的源(上游对空/ISO date 静默返空,评审真机坐实)
DATE_POOLS = {"em_limit_up_pool", "em_zb_pool", "em_dt_pool", "em_yzt_pool",
              "ths_limit_up_pool", "eastmoney_lhb"}
# code 原样透传的源:ths_hot_list=榜期 / eastmoney_industry_reports=行业码 /
# tencent_realtime_quote=支持 SH/SZ/BJ 前缀+逗号分隔多码(6位提取会毁前缀·砍多码,故透传;
# stocks 侧 _tencent_symbol 自行处理前缀与裸码重推市场) / eastmoney_sector_fund_flow=概念/行业档
CODE_PASSTHROUGH = {"ths_hot_list", "eastmoney_industry_reports", "tencent_realtime_quote",
                    "eastmoney_sector_fund_flow"}


def _alias_index() -> Dict[str, str]:
    """alias/source_id → source_id(小写键)。"""
    out: Dict[str, str] = {}
    for sid, alias in _STATIC_SOURCES.items():
        out[sid.lower()] = sid
        out[str(alias).lower()] = sid
    rows = _CATALOG_CACHE.get("rows") or []
    for row in rows:
        sid = str(row.get("source_id") or "")
        if sid:
            out[sid.lower()] = sid
            out[str(row.get("alias") or sid).lower()] = sid
    return out


def resolve_source(source: str) -> str:
    """归一到 canonical source_id;未知返 ''(caller 据此拒)。catalog 保留原义。"""
    key = (source or "").strip().lower()
    if key == "catalog":
        return "catalog"
    return _alias_index().get(key, "")


def known_sources() -> List[str]:
    """canonical source_id 全集(动态 catalog ∪ 静态兜底)。"""
    ids = set(_STATIC_SOURCES)
    for row in _CATALOG_CACHE.get("rows") or []:
        if row.get("source_id"):
            ids.add(str(row["source_id"]))
    return sorted(ids)


def _clip_value(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _CLIP:
        return v[:_CLIP] + "…"
    return v


def _clip_row(row: Any) -> Any:
    """顶层+raw 内层长字符串截 400;raw 内层再嵌 raw 时剥掉(防御)。"""
    if not isinstance(row, dict):
        return row
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if k == "raw" and isinstance(v, dict):
            out[k] = {rk: _clip_value(rv) for rk, rv in v.items() if rk != "raw"}
        else:
            out[k] = _clip_value(v)
    return out


def native_rows(items: Optional[List[dict]]) -> List[dict]:
    """统一 item → 源原生行形(raw 优先平铺 + 补统一时间三字段)。
    消费方(ww_live_text/astock)沿用旧 CLI 时代的源原生键(zt_stat/question/answer…),
    统一信封的 title/text 归一不丢真:raw 缺席才退回 item 顶层字段。"""
    rows: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            rows.append(it)
            continue
        raw = it.get("raw")
        if isinstance(raw, dict) and raw:
            row = dict(raw)
            for k in ("publish_ts", "visible_ts", "visible_ts_reason"):
                if k not in row and it.get(k) is not None:
                    row[k] = it[k]
        else:
            row = {k: v for k, v in it.items() if k != "raw"}
        rows.append(_clip_row(row))
    return rows


def _normalize_args(sid: str, code: str, date: str) -> Dict[str, Any]:
    """code/date 归一;返回 {code, date, err}。err 非空=caller 错误。"""
    if sid not in CODE_PASSTHROUGH:
        m = re.search(r"\d{6}", code or "")
        code = m.group(0) if m else ""
    if sid in NEED_CODE and not code:
        return {"code": code, "date": date,
                "err": f"source={sid} 必须带 code(6 位股票代码,SZ000630/000630 均可)"}
    if sid in DATE_POOLS:
        digits = re.sub(r"\D", "", date or "")
        if (date or "").strip() and len(digits) != 8:
            return {"code": code, "date": date,
                    "err": f"date 非法: {date!r}(需 YYYYMMDD,如 20260706)"}
        date = digits or time.strftime("%Y%m%d")
    elif sid == "ths_hot_reason" and (date or "").strip():
        digits = re.sub(r"\D", "", date)
        if len(digits) != 8:
            return {"code": code, "date": date,
                    "err": f"date 非法: {date!r}(需 YYYY-MM-DD 或 YYYYMMDD)"}
        date = f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return {"code": code, "date": date, "err": ""}


def _run_probe(args: List[str], timeout: float = _TIMEOUT_S) -> Dict[str, Any]:
    """起子进程跑 probe CLI;{ok, payload}|{ok:False, note}。--opt=value 形态防 - 开头值。"""
    if not _STOCKS_PROBE.exists():
        return {"ok": False, "note": "stocks probe 不可用(G:\\stocks 缺席),该能力此机不可达"}
    cmd = [sys.executable, str(_STOCKS_PROBE), *args, "--json"]
    with _LOCK:                       # 只锁「起跑间隔」记账,不串行整个子进程生命周期
        wait = _MIN_INTERVAL_S - (time.time() - _LAST_LAUNCH[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_LAUNCH[0] = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              cwd=str(_STOCKS_PROBE.parents[1]),
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "note": f"probe 超时({int(timeout)}s),外源可能限流/阻塞;稍后再试"}
    except Exception as e:  # noqa: BLE001 — 启动失败诚实显形
        return {"ok": False, "note": f"probe 启动失败: {e}"}
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-300:]
        return {"ok": False, "note": f"probe 退出码 {proc.returncode}: {tail}"}
    try:
        return {"ok": True, "payload": json.loads(proc.stdout.strip())}
    except Exception:  # noqa: BLE001
        return {"ok": False, "note": "probe 输出非 JSON(截断/编码异常),该次结果作废不编造"}


def catalog(max_age_s: float = _CATALOG_TTL_S) -> Dict[str, Any]:
    """源目录:{ok, sources, origin:probe|cache|static, note}。探针失败回静态兜底。"""
    now = time.time()
    if _CATALOG_CACHE["rows"] is not None and now - _CATALOG_CACHE["ts"] < max_age_s:
        return {"ok": True, "sources": _CATALOG_CACHE["rows"], "origin": "cache", "note": ""}
    r = _run_probe(["--source=catalog"], timeout=30)
    if r.get("ok"):
        payload = r["payload"]
        rows = (payload.get("rows") if isinstance(payload, dict) else None) or []
        if rows:
            _CATALOG_CACHE.update(ts=now, rows=rows)
            return {"ok": True, "sources": rows, "origin": "probe", "note": ""}
    fallback = [{"source_id": sid, "alias": alias, "status": "unknown"}
                for sid, alias in _STATIC_SOURCES.items()]
    return {"ok": True, "sources": fallback, "origin": "static",
            "note": (r.get("note") or "catalog 探针空返") + ";用静态兜底表(status 未知)"}


def probe(source: str, code: str = "", date: str = "", limit: int = 20,
          timeout: float = _TIMEOUT_S) -> Dict[str, Any]:
    """统一探针。返回 {ok, source, status, items[≤limit], n, error, note, fetched_ts, pulled_at}。
    ok:False=caller/机械错误;上游 planned/error 时 ok:True + status/error 显形 + items 空。"""
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    base: Dict[str, Any] = {"ok": True, "source": (source or "").strip().lower(),
                            "code": code, "date": date, "status": "", "items": [],
                            "n": 0, "error": "", "note": "", "fetched_ts": None,
                            "pulled_at": pulled_at}
    sid = resolve_source(source)
    if not sid:
        return {**base, "ok": False,
                "note": f"source 非法: {source!r};合法值 {', '.join(['catalog'] + known_sources())}"}
    base["source"] = sid
    if sid == "catalog":
        c = catalog()
        rows = [_clip_row(r) for r in c["sources"]]
        return {**base, "items": rows, "n": len(rows), "note": c.get("note", ""),
                "status": "ok" if c["origin"] != "static" else "static"}
    norm = _normalize_args(sid, code, date)
    if norm["err"]:
        return {**base, "ok": False, "note": norm["err"]}
    base.update(code=norm["code"], date=norm["date"])
    try:
        lim = max(1, min(int(limit or 20), 300))
    except (TypeError, ValueError):
        return {**base, "ok": False, "note": f"limit 非法: {limit!r}(需整数)"}
    r = _run_probe([f"--source={sid}", f"--code={norm['code']}",
                    f"--date={norm['date']}", f"--limit={lim}"], timeout=timeout)
    if not r.get("ok"):
        return {**base, "note": r["note"]}
    p = r["payload"]
    if not isinstance(p, dict):           # 合法 JSON 但非对象(数组/标量)→ 诚实作废,不 .get 崩
        return {**base, "note": "probe 输出非对象 JSON(结构异常),该次结果作废不编造"}
    status = str(p.get("status") or "")
    items = [_clip_row(i) for i in (p.get("items") or [])[:lim]]
    note = ""
    if status == "planned":
        note = "该源已在 stocks 登记但探针未实现(planned),诚实空返"
    elif status == "error":
        note = f"上游探针出错: {p.get('error') or ''}"[:300]
    elif not items:
        note = "该源本次返回 0 行(非交易日/无相关条目等;上游不报原因,不臆断)"
    return {**base, "status": status or "ok", "items": items, "n": len(items),
            "error": str(p.get("error") or ""), "note": note,
            "fetched_ts": p.get("fetched_ts")}
