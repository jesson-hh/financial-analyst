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

# ── 常驻 probe worker(2026-07-30 T2 提速)────────────────────────────────────
# 单条 probe() 走常驻 stocks worker(stdin/stdout JSON-lines,RS=0x1e 前缀防杂散打印),
# 摊薄每次 0.4-2.6s 的子进程冷启动;probe_many 维持 --batch 一次性路径。
# 红线:1s 最小间隔对 worker 请求**原样保留**(节流语义=相邻上游探测操作的间隔,
# 不是"起子进程"——上游请求频率与一次性路径逐位相同,提速只来自消灭 spawn+import)。
# 超时=杀 worker 惰性重生绝不读半截;worker 不可用(缺文件/起不来/管道断)诚实回落
# 一次性 CLI。GUANLAN_PROBE_WORKER=0 禁用(tests/conftest.py 默认禁,worker 有专属测试)。
_STOCKS_WORKER = Path(os.environ.get(
    "GUANLAN_LIVE_WORKER", str(_STOCKS_PROBE.parent / "probe_live_worker.py")))
_WORKER_RS = "\x1e"
_WORKER_LOCK = threading.Lock()   # 串行整个"请求→响应"往返(协议是串行的)
_WORKER: Dict[str, Any] = {"w": None, "seq": 0}
# 评审加固(2026-07-30):
# ①抢锁预算——worker 单飞,抢不到锁(前一条慢源在飞/正在 spawn)立即回落一次性子进程
#   (按隔离并行)。并发语义回到旧世界:争用下 N 个调用方并行、各自 timeout 封顶,
#   绝不在锁上排队逃逸预算(此前一个 90s 挂死源能让第 K 个排队者等 K×90s)。
# ②连败冷却(负缓存)——spawn 失败进入冷却窗,窗内直接回落不再反复付 spawn/30s ready
#   等待;成功清零。③通道可观测——状态进 worker_status()(health 总闸 probe_worker
#   运维项消费),worker stderr 落 var/live/probe_worker.stderr.log(每次 spawn 截断),
#   spawn 失败把日志尾带进 last_error,绝不再 DEVNULL 吞遗言。
_WORKER_LOCK_WAIT_S = float(os.environ.get("GUANLAN_PROBE_WORKER_LOCK_WAIT_S", "2.0"))
_WORKER_COOLDOWN_S = float(os.environ.get("GUANLAN_PROBE_WORKER_COOLDOWN_S", "120"))
_WORKER_STDERR_LOG = Path(os.environ.get(
    "GUANLAN_PROBE_WORKER_LOG",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "probe_worker.stderr.log")))
_WORKER_STATE: Dict[str, Any] = {"state": "cold", "spawn_fails": 0, "cooldown_until": 0.0,
                                 "last_error": "", "last_ok_ts": 0.0}


def _worker_enabled() -> bool:
    return os.environ.get("GUANLAN_PROBE_WORKER", "1").strip().lower() not in ("0", "false", "no")


def worker_status() -> Dict[str, Any]:
    """worker 通道自检快照(health 总闸 probe_worker 运维项消费;只读)。"""
    return {"enabled": _worker_enabled(), "script_exists": _STOCKS_WORKER.exists(),
            "alive": bool(_WORKER.get("w") and _WORKER["w"]["proc"].poll() is None),
            **{k: _WORKER_STATE[k] for k in
               ("state", "spawn_fails", "cooldown_until", "last_error", "last_ok_ts")}}


def _throttle_launch() -> None:
    """相邻上游探测操作最小起跑间隔(东财防封毯式闸)——一次性子进程与 worker 请求同闸。"""
    with _LOCK:
        wait = _MIN_INTERVAL_S - (time.time() - _LAST_LAUNCH[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_LAUNCH[0] = time.time()


def _worker_kill(w: Optional[Dict[str, Any]]) -> None:
    if not w:
        return
    try:
        w["proc"].kill()
    except Exception:  # noqa: BLE001
        pass


def _worker_spawn(ready_timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """起常驻 worker + reader 线程,等 __ready__(导入预热完成)。失败返 None(回落)。"""
    import queue as _queue

    # stderr 落文件(每次 spawn 截断):worker 的 import 崩遗言/运行期噪声都可查,
    # 绝不再 DEVNULL 吞掉唯一诊断(评审 important)。文件打不开才退 DEVNULL。
    try:
        _WORKER_STDERR_LOG.parent.mkdir(parents=True, exist_ok=True)
        stderr_sink: Any = open(_WORKER_STDERR_LOG, "wb")
    except OSError:
        stderr_sink = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [sys.executable, str(_STOCKS_WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_sink,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(_STOCKS_PROBE.parents[1]),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception as _se:  # noqa: BLE001
        _worker_note_spawn_failure(f"spawn 异常: {type(_se).__name__}: {_se}")
        return None
    finally:
        try:
            if stderr_sink is not subprocess.DEVNULL:
                stderr_sink.close()      # 子进程已持有 fd;父进程句柄即关
        except Exception:  # noqa: BLE001
            pass
    q: Any = _queue.Queue()

    def _read() -> None:
        try:
            for ln in proc.stdout:          # RS 前缀行才是协议响应,其余(杂散)丢弃
                if ln.startswith(_WORKER_RS):
                    q.put(ln[len(_WORKER_RS):])
        except Exception:  # noqa: BLE001
            pass
        q.put(None)                          # EOF 哨兵

    threading.Thread(target=_read, daemon=True, name="probe-worker-reader").start()
    w = {"proc": proc, "q": q}
    deadline = time.time() + ready_timeout
    while True:
        remain = deadline - time.time()
        if remain <= 0:
            _worker_kill(w)
            _worker_note_spawn_failure(f"ready 等待超时({int(ready_timeout)}s)")
            return None
        try:
            line = q.get(timeout=remain)
        except Exception:  # noqa: BLE001
            _worker_kill(w)
            _worker_note_spawn_failure("ready 等待读取异常")
            return None
        if line is None:                     # 起来即死(EOF)——遗言在 stderr 日志里
            _worker_note_spawn_failure("worker 起来即死(EOF)")
            return None
        try:
            if json.loads(line).get("id") == "__ready__":
                _WORKER_STATE.update(state="ready", spawn_fails=0, cooldown_until=0.0,
                                     last_error="", last_ok_ts=time.time())
                return w
        except Exception:  # noqa: BLE001
            continue


def _worker_note_spawn_failure(reason: str) -> None:
    """spawn 失败记账:连败进冷却窗(负缓存,窗内直接回落不再付 spawn 成本),
    stderr 日志尾并进 last_error 显形(health probe_worker 项消费)。"""
    tail = ""
    try:
        if _WORKER_STDERR_LOG.exists():
            raw = _WORKER_STDERR_LOG.read_bytes()[-600:]
            tail = raw.decode("utf-8", "replace").strip()
    except OSError:
        pass
    _WORKER_STATE["spawn_fails"] = int(_WORKER_STATE["spawn_fails"]) + 1
    _WORKER_STATE["state"] = "cooldown"
    _WORKER_STATE["cooldown_until"] = time.time() + _WORKER_COOLDOWN_S
    _WORKER_STATE["last_error"] = (reason + ((";stderr尾: " + tail) if tail else ""))[:500]
    try:
        print(f"[live_client][PROBE-WORKER-SPAWN-FAILED] {_WORKER_STATE['last_error']}",
              file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _worker_request(spec: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    """经常驻 worker 执行一条 probe。返回 {ok, payload} | {ok:False, note} |
    {ok:False, note, worker_unavailable:True}(后者=通道级不可用,调用方回落一次性 CLI)。
    机械失败(超时/损坏)语义与一次性路径逐位一致:诚实作废绝不编造、绝不读半截。"""
    if not _worker_enabled() or not _STOCKS_WORKER.exists():
        return {"ok": False, "note": "worker 通道不可用", "worker_unavailable": True}
    if time.time() < float(_WORKER_STATE.get("cooldown_until") or 0.0):
        # 连败冷却窗(评审):不再逐条付 spawn/30s ready 等待,直接回落一次性子进程
        return {"ok": False, "note": "worker 连败冷却中(回落一次性)", "worker_unavailable": True}
    # 抢锁预算(评审 important):worker 单飞,前一条慢源在飞就立即回落一次性子进程
    # (按隔离并行)——争用下并发语义回到旧世界,各调用方 timeout 各自封顶,绝不排队。
    if not _WORKER_LOCK.acquire(timeout=_WORKER_LOCK_WAIT_S):
        return {"ok": False, "note": "worker 忙(并发回落一次性)", "worker_unavailable": True}
    try:
        w = _WORKER.get("w")
        if w is None or w["proc"].poll() is not None:
            w = _worker_spawn()
            _WORKER["w"] = w
            if w is None:
                return {"ok": False, "note": "worker 启动失败", "worker_unavailable": True}
        _WORKER["seq"] += 1
        rid = str(_WORKER["seq"])
        _throttle_launch()                   # 与一次性路径同闸:上游频率零变化
        try:
            w["proc"].stdin.write(json.dumps({**spec, "id": rid}, ensure_ascii=False) + "\n")
            w["proc"].stdin.flush()
        except Exception as e:  # noqa: BLE001
            _worker_kill(w)
            _WORKER["w"] = None
            return {"ok": False, "note": f"worker 写入失败: {e}", "worker_unavailable": True}
        deadline = time.time() + max(1.0, float(timeout))
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                # 超时:杀掉重生(绝不留半截响应污染下一条),按机械失败诚实作废
                _worker_kill(w)
                _WORKER["w"] = None
                return {"ok": False,
                        "note": f"probe 超时({int(timeout)}s),外源可能限流/阻塞;稍后再试"}
            try:
                line = w["q"].get(timeout=remain)
            except Exception:  # noqa: BLE001
                continue
            if line is None:                 # worker 中途死亡(EOF)
                _WORKER["w"] = None
                return {"ok": False, "note": "worker 进程中断", "worker_unavailable": True}
            try:
                resp = json.loads(line)
            except Exception:  # noqa: BLE001
                _worker_kill(w)
                _WORKER["w"] = None
                return {"ok": False, "note": "worker 输出损坏,该次结果作废不编造"}
            if str(resp.get("id")) != rid:
                continue                     # 陈旧 id(防御;超时路径本已杀进程)
            if "error" in resp:
                return {"ok": False, "note": f"worker 执行失败: {resp['error']}"[:300]}
            return {"ok": True, "payload": resp.get("payload")}
    finally:
        _WORKER_LOCK.release()


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
    "eastmoney_market_fund_flow": "market_fund_flow",   # 全市场五档(沪深合计),无参
    "eastmoney_sector_flow_minute": "sector_flow_minute",  # 单板块当日分钟累计线(code=BKxxxx)
    "eastmoney_market_flow_minute": "market_flow_minute",  # 全市场当日分钟累计线,无参
    "iwencai_search": "iwencai",
    # 2026-07-09 补:与 stocks 47 源正典对齐(通达信实时套件/新浪期权·财报/个股信息/
    # 观澜合成源/问财结构化)。别名沿用 stocks alias 字段;arg 口径见下方三张分类表。
    "tdx_realtime_quote": "tdx_quote",
    "tdx_kline": "tdx_kline",
    "tdx_orderbook": "orderbook",
    "tdx_transaction": "transaction",
    "tdx_finance_snapshot": "finance_snapshot",
    "tdx_f10_text": "tdx_f10",
    "baidu_kline_ma": "baidu_kline",
    "baidu_valuation_percentile": "valuation_percentile",   # 百度股市通 PE-TTM/PB 近5年分位带
    "overseas_stock_quote": "overseas_quote",               # 美股/港股个股实时行情(海外维度·腾讯 qt)
    "overseas_index_quote": "overseas_index",               # 海外六大指数(道指/纳指/标普/VIX/恒指/恒生科技)
    "sina_option_codes": "option_codes",
    "sina_option_tquote": "option_tquote",
    "sina_option_greeks": "option_greeks",
    "sina_financial_report": "financial_report",
    "eastmoney_stock_info": "stock_info",
    "stock_live_brief": "live_brief",
    "full_valuation": "full_valuation",
    "limit_up_sentiment": "limit_up_sentiment",
    "iwencai_query": "iwencai_query",
}

# 必带 code 的源(6 位股票代码;lhb_stock/unlock 等个股口径)
NEED_CODE = {
    "eastmoney_stock_news", "cninfo_announcements", "eastmoney_concept_blocks",
    "cninfo_irm", "eastmoney_hot_concept", "eastmoney_research_reports",
    "eastmoney_fund_flow", "eastmoney_fund_flow_minute", "eastmoney_lhb_stock",
    "eastmoney_unlock", "eastmoney_margin", "eastmoney_block_trade",
    "eastmoney_holder_change", "eastmoney_dividend", "tencent_realtime_quote",
    "ths_eps_forecast",   # 同花顺一致预期 EPS,按 6 位股票代码查
    "baidu_valuation_percentile",   # 百度股市通估值分位,按 6 位股票代码查
    "overseas_stock_quote",   # 海外个股行情,按代码查(美股 AAPL / 港股 00700,透传见 CODE_PASSTHROUGH)
    # 板块分钟线必带板块码 BKxxxx(非 6 位,故同时进 CODE_PASSTHROUGH)
    "eastmoney_sector_flow_minute",
    # 2026-07-09 补:tdx 实时套件/新浪财报/个股信息/观澜合成源均按 6 位代码查。
    # sina_option_tquote/greeks 亦必带 code,但其 code=期权合约 id(非6位),故同时进
    # CODE_PASSTHROUGH 保原样(见下),NEED_CODE 只负责「缺 code 报错」。
    "tdx_realtime_quote", "tdx_kline", "tdx_orderbook", "tdx_transaction",
    "tdx_finance_snapshot", "tdx_f10_text", "baidu_kline_ma",
    "sina_option_tquote", "sina_option_greeks", "sina_financial_report",
    "eastmoney_stock_info", "stock_live_brief", "full_valuation",
}
# date 缺省补当日 YYYYMMDD 的源(上游对空/ISO date 静默返空,评审真机坐实)
DATE_POOLS = {"em_limit_up_pool", "em_zb_pool", "em_dt_pool", "em_yzt_pool",
              "ths_limit_up_pool", "eastmoney_lhb",
              "limit_up_sentiment"}   # 2026-07-09 补:打板情绪快照 date 缺省=当日
# code 原样透传的源:ths_hot_list=榜期 / eastmoney_industry_reports=行业码 /
# tencent_realtime_quote=支持 SH/SZ/BJ 前缀+逗号分隔多码(6位提取会毁前缀·砍多码,故透传;
# stocks 侧 _tencent_symbol 自行处理前缀与裸码重推市场) / eastmoney_sector_fund_flow=概念/行业档
CODE_PASSTHROUGH = {"ths_hot_list", "eastmoney_industry_reports", "tencent_realtime_quote",
                    "eastmoney_sector_fund_flow",
                    # 板块码 BK0963 数字只 4 位,\d{6} 提取会把 code 清空 → 必须透传
                    "eastmoney_sector_flow_minute",
                    # 2026-07-09 补:code 不是 6 位股票代码,禁 \d{6} 提取——
                    # sina_option_tquote/greeks=期权合约 id(CON_OP_10004949 等 8 位);
                    # iwencai_query/iwencai_search=自然语言 query(问财)。iwencai_search
                    # 修既有隐患:此前未列入,文本 query 被 \d{6} 清空致恒空。
                    "sina_option_tquote", "sina_option_greeks",
                    "iwencai_query", "iwencai_search",
                    # 海外行情:美股字母代码(AAPL)/港股数字码(00700),\d{6} 提取会毁代码 → 透传;
                    # 海外指数 code=usDJI/HSI 等短名同理透传(可空=全部 6 指数)
                    "overseas_stock_quote", "overseas_index_quote"}


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
    _throttle_launch()                # 只节流「起跑间隔」,不串行整个子进程生命周期
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


def _run_probe_batch(specs: List[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
    """一个子进程跑多条 probe(CLI --batch,specs 经 stdin JSON)。{ok, payloads}|{ok:False, note}。
    2026-07-28 提速:一批只占一次 1s 起跑记账——节流的是「起子进程」(东财防封毯式闸),
    进程内逐条请求与逐条子进程同速且东财另有跨进程租约,礼貌限速不减。"""
    if not _STOCKS_PROBE.exists():
        return {"ok": False, "note": "stocks probe 不可用(G:\\stocks 缺席),该能力此机不可达"}
    cmd = [sys.executable, str(_STOCKS_PROBE), "--batch"]
    _throttle_launch()
    try:
        proc = subprocess.run(cmd, input=json.dumps(specs, ensure_ascii=False),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              cwd=str(_STOCKS_PROBE.parents[1]),
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        # 评审红线:超时=子进程跑到一半被杀,已完成条目都真实打过上游——绝不回退重打
        # (恰在上游限流时加压),按机械失败诚实作废(channel_absent=False)。
        return {"ok": False, "channel_absent": False,
                "note": f"batch probe 超时({int(timeout)}s),外源可能限流/阻塞;本批作废不重拉"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "channel_absent": True, "note": f"batch probe 启动失败: {e}"}
    if proc.returncode != 0:
        # 退出码非 0=通道不存在(旧 CLI 不认 --batch,argparse 在任何上游请求前退出)
        # 或进程级崩溃——两者都未真正打过上游,允许逐条回退。
        tail = (proc.stderr or "").strip()[-300:]
        return {"ok": False, "channel_absent": True,
                "note": f"batch probe 退出码 {proc.returncode}: {tail}"}
    try:
        payload = json.loads(proc.stdout.strip())
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("no results array")
        return {"ok": True, "payloads": results}
    except Exception:  # noqa: BLE001
        # 解析失败=全部 N 条上游请求已完成后 stdout 损坏——同样绝不回退重打。
        return {"ok": False, "channel_absent": False,
                "note": "batch probe 输出损坏,本批作废不重拉(上游请求已发出,不打第二遍)"}


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
    # 传输层三级:常驻 worker(毫秒级往返)→ 不可用回落一次性子进程 → 机械失败诚实作废。
    # 两条路径的信封语义逐位一致(worker 机械失败与一次性同为 {**base, note})。
    wr = _worker_request({"source": sid, "code": norm["code"],
                          "date": norm["date"], "limit": lim}, timeout)
    if not wr.get("worker_unavailable"):
        if wr.get("ok"):
            return _shape_payload(base, wr.get("payload"), lim)
        return {**base, "note": wr["note"]}
    r = _run_probe([f"--source={sid}", f"--code={norm['code']}",
                    f"--date={norm['date']}", f"--limit={lim}"], timeout=timeout)
    if not r.get("ok"):
        return {**base, "note": r["note"], "worker_fallback": wr["note"]}
    out = _shape_payload(base, r["payload"], lim)
    out["worker_fallback"] = wr["note"]   # 附加显形键(评审:回落不能零痕迹;不扰 note 显示)
    return out


def _shape_payload(base: Dict[str, Any], p: Any, lim: int) -> Dict[str, Any]:
    """把上游 probe 载荷收进统一信封(probe/probe_many 共用,语义逐位一致)。"""
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


def probe_many(requests: List[Dict[str, Any]], timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """批量探针(2026-07-28 提速):一个子进程执行多条,省去每条 0.4-2.6s 解释器冷启动
    与 1s 起跑间隔(10 源冷刷 10-26s → ~3-5s)。返回与 probe() 同形信封列表,序=入参序;
    每条独立成败;非法条目就地 ok:False 不进批;批量通道整体失败(旧 stocks CLI 无
    --batch/超时/输出异常)→ 诚实回退逐条 probe(),绝不空手而归。"""
    reqs = list(requests or [])
    if not reqs:
        return []
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    prepared: List[Dict[str, Any]] = []
    for req in reqs:
        req = req or {}
        source = str(req.get("source") or "")
        code = str(req.get("code") or "")
        date = str(req.get("date") or "")
        base: Dict[str, Any] = {"ok": True, "source": source.strip().lower(), "code": code,
                                "date": date, "status": "", "items": [], "n": 0, "error": "",
                                "note": "", "fetched_ts": None, "pulled_at": pulled_at}
        sid = resolve_source(source)
        if not sid:
            prepared.append({"base": {**base, "ok": False,
                                      "note": f"source 非法: {source!r};合法值 "
                                              f"{', '.join(['catalog'] + known_sources())}"},
                             "spec": None})
            continue
        if sid == "catalog":
            prepared.append({"base": {**base, "ok": False,
                                      "note": "catalog 不支持批量;请用 probe('catalog')"},
                             "spec": None})
            continue
        base["source"] = sid
        norm = _normalize_args(sid, code, date)
        if norm["err"]:
            prepared.append({"base": {**base, "ok": False, "note": norm["err"]}, "spec": None})
            continue
        base.update(code=norm["code"], date=norm["date"])
        try:
            lim = max(1, min(int(req.get("limit") or 20), 300))
        except (TypeError, ValueError):
            prepared.append({"base": {**base, "ok": False,
                                      "note": f"limit 非法: {req.get('limit')!r}(需整数)"},
                             "spec": None})
            continue
        prepared.append({"base": base,
                         "spec": {"source": sid, "code": norm["code"],
                                  "date": norm["date"], "limit": lim}})

    specs = [p["spec"] for p in prepared if p["spec"] is not None]
    if not specs:
        return [p["base"] for p in prepared]
    if timeout is None:
        timeout = min(300.0, max(float(_TIMEOUT_S), 15.0 * len(specs)))
    r = _run_probe_batch(specs, timeout)
    if not r.get("ok"):
        note = str(r.get("note") or "batch probe 失败")
        if not r.get("channel_absent"):
            # 评审红线(important):超时/输出损坏时上游请求多已真实发出,逐条回退=对
            # 正在限流的上游整批重打第二遍。按机械失败逐条诚实作废(ok:False →
            # market_tape 等消费方保留上轮),绝不重拉。
            return [p["base"] if p["spec"] is None
                    else {**p["base"], "ok": False, "note": note}
                    for p in prepared]
        # 通道不存在(旧 CLI 无 --batch,argparse 在上游请求前退出)→ 逐条回退,
        # 透传调用方 timeout(评审:不透传会静默突破调用方预算)。
        per_item_timeout = min(float(_TIMEOUT_S), float(timeout))
        out: List[Dict[str, Any]] = []
        for p in prepared:
            if p["spec"] is None:
                out.append(p["base"])
            else:
                s = p["spec"]
                out.append(probe(s["source"], code=s["code"], date=s["date"],
                                 limit=s["limit"], timeout=per_item_timeout))
        return out
    payloads = r["payloads"]
    # 对位回核(评审:两仓漂移时 stocks 侧若 skip/重排任一条,纯下标消费会把 A 源信封
    # 装进 B 源数据且零显形)——数量或任一 source 名失配即整批作废,绝不串位消费。
    expected = [p["spec"]["source"] for p in prepared if p["spec"] is not None]
    aligned = len(payloads) == len(expected)
    if aligned:
        for want, got in zip(expected, payloads):
            got_sid = (got or {}).get("source_id") or (got or {}).get("source") if isinstance(got, dict) else None
            if str(got_sid or "") != want:
                aligned = False
                break
    if not aligned:
        note = "batch probe 结果与请求失配(数量/源名),整批作废不编造"
        return [p["base"] if p["spec"] is None
                else {**p["base"], "ok": False, "note": note}
                for p in prepared]
    out = []
    k = 0
    for p in prepared:
        if p["spec"] is None:
            out.append(p["base"])
            continue
        out.append(_shape_payload(p["base"], payloads[k], p["spec"]["limit"]))
        k += 1
    return out
