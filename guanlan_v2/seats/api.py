# -*- coding: utf-8 -*-
"""真 K 线 REST(guanlan 自有,挂到薄壳 app 上)—— 日线 + 历史 5min。

落子(seats)模块的「复盘 / 可变时间尺度」需要在**真实价格**上推演:
- 日线(``freq=day``)→ 日 / 周(周由前端聚合)。
- 历史 5min(``freq=5min``,stock_data `cn_data_5min`,**2018-01 起**)→ 60/30/15 分(前端聚合)。

均经引擎 ``data.loader_factory.get_default_loader()``(本机 = ``QlibBinaryLoader``,
读本地 qlib bin、无网络),数据根全经 ``get_data_paths()`` 解析(零硬编码、不复制
stock_data)。注:引擎 ``/watch/bars`` 是 pytdx **实时**口、仅最近 ~240 根,**不是**历史源。

其余证据层(量化因子 / 研报 / regime)当前仍 mock,待上游成型 —— 见 ``ui/seats/README.md``。

``build_seats_router()`` 返回 ``/seats/*`` 路由组(随 cards 先例的工厂式):
- ``GET /seats/daily?code=&freq=day|5min&n=&start=&end=``  最近 n 根 bar(或显式窗口)
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

# P2「量化卡 vintage IC」:decide 研判时按 catalog 反查配方因子 → 优先本票 tsic、退而截面 cs 的
# vintage(as-of 决策日)真 OOS IC。顶层 import 与 screen/api.py:33 同先例(catalog 模块级单例,
# factor_vintage 顶层仅轻量 import、引擎读数全惰性在函数内);**这两个名字必须是 api 模块的模块级属性**
# 才能被测试 monkeypatch(test_seats_vintage_wire.py),故顶层 import 而非惰性占位。
from guanlan_v2.screen.factor_vintage import cs_vintage_asof, factor_z_asof, tsic_vintage_asof
from guanlan_v2.screen.catalog import FACTOR_DEFS


def _num(v: Any) -> Optional[float]:
    """float 化并把 NaN/无法解析 → None(前端拿到 null 而非 NaN)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN != NaN → None


def _drop_unsettled(df):
    """丢弃 ``close`` 为空(NaN/None)的未结算占位行。

    qlib/loader 对**当日未 EOD 入库**会返回一根 ``close=NaN`` 的 today 占位行。不丢它会污染下游:
    ``last_bar_date`` 提前变今日 → ``fresh`` 永远 False(实盘看不到今日)、``/seats/daily`` 吐 null
    价柱、MA/asof/因子被 NaN 带偏。只保留有真实收盘的**已结算 bar**,让所有 seats 端点一致诚实。
    过滤失败则原样返回(防御,不致命)。
    """
    try:
        if df is None or len(df) == 0 or "close" not in df.columns:
            return df
        mask = df["close"].notna()
        if bool(mask.all()):
            return df                      # 无占位行 → 不动(常态,零开销)
        return df[mask].reset_index(drop=True)
    except Exception:  # noqa: BLE001 — 过滤失败原样返回,绝不让清洗本身把端点搞挂
        return df


def _ctx5_sync(c: str, q: dict) -> dict:
    """5min 触发上下文(实时 5min via ``WatchFeed.bars5`` + 当前快照 q):指标按**简单口径**算,
    与前端 ``buildTriggerCtx`` 对齐(MA=均值、RSI=简单涨跌均值、量比=当根/近10均量),
    使「5min 交易单」的 生成 / live 监控 / 回测检验 **同一套口径**。失败 → 仅 quote 字段(指标 None)。
    返回 ``{price,open,high,low,ma5,ma20,maDiff20,rsi14,volRatio,hi20,lo20,n5}``。
    """
    closes, vols, highs, lows = [], [], [], []
    try:
        from financial_analyst.watch.feed import WatchFeed
        feed = WatchFeed()
        try:
            df = feed.bars5(c, 240)
        finally:
            try:
                feed.close()
            except Exception:  # noqa: BLE001
                pass
        if df is not None and len(df) > 0:
            closes = [float(x) for x in df["close"].tolist() if x is not None]
            if "vol" in df.columns:
                vols = [float(x) for x in df["vol"].tolist() if x is not None]
            highs = [float(x) for x in df["high"].tolist() if x is not None]
            lows = [float(x) for x in df["low"].tolist() if x is not None]
    except Exception:  # noqa: BLE001
        pass

    def _mean(a):
        return (sum(a) / len(a)) if a else None

    ma5 = _mean(closes[-5:]) if len(closes) >= 5 else None
    ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
    last = _num(q.get("price")) or (closes[-1] if closes else None)
    rsi14 = None
    if len(closes) >= 15:
        up = dn = 0.0
        for k in range(len(closes) - 14, len(closes)):
            ch = closes[k] - closes[k - 1]
            if ch >= 0:
                up += ch
            else:
                dn -= ch
        au, ad = up / 14.0, dn / 14.0
        rsi14 = 50.0 if (ad == 0 and au == 0) else (100.0 if ad == 0 else round(100 - 100 / (1 + au / ad), 2))
    vol_ratio = None
    if len(vols) >= 10 and vols[-1] is not None:
        vm = _mean(vols[-10:])
        vol_ratio = round(vols[-1] / vm, 3) if vm else None
    hi = max(highs[-48:]) if highs else None
    lo = min(lows[-48:]) if lows else None
    return {
        "price": _num(last), "open": _num(q.get("open")),
        "high": _num(q.get("high")), "low": _num(q.get("low")),
        "ma5": _num(round(ma5, 3)) if ma5 else None,
        "ma20": _num(round(ma20, 3)) if ma20 else None,
        "maDiff20": _num(round(last / ma20 - 1, 4)) if (ma20 and last) else None,
        "rsi14": _num(rsi14), "volRatio": _num(vol_ratio),
        "hi20": _num(round(hi, 2)) if hi else None, "lo20": _num(round(lo, 2)) if lo else None,
        "n5": len(closes),
    }


def _load_csi300(start: Optional[str] = None, end: Optional[str] = None,
                 n: int = 250) -> list:
    """真·沪深300 日收盘行 ``[{"date": "YYYY-MM-DD", "close": float}, …]`` 升序。

    数据源与 workflow 绩效对标**同源**(guanlan_v2/workflow/api.py ``_benchmark_ret_series``):
    ``get_data_paths().parquet_root / etf_index.parquet`` 的 ``ts_code=399300.SZ``
    (2005~今,只读,不另造数据源)。窗口语义:``start`` 给了用显式窗口(忽略 n);
    ``end`` 可**单独生效**(先裁尾,再取截至 end 的最近 n 根);都没给取最近 n 根。
    无数据/读失败直接抛 —— 由端点捕获转 ok:False(诚实降级,不吐假基准)。"""
    import pandas as pd
    from financial_analyst.data.paths import get_data_paths

    f = get_data_paths().parquet_root / "etf_index.parquet"
    ei = pd.read_parquet(f, columns=["ts_code", "trade_date", "close"])
    ei = ei[ei["ts_code"] == "399300.SZ"]
    if ei.empty:
        raise ValueError("399300.SZ 不在 etf_index.parquet 中")
    ei = ei.assign(trade_date=ei["trade_date"].astype(str)).sort_values("trade_date")
    ei = ei[~ei["trade_date"].duplicated(keep="last")]
    if end:                                   # end 单独生效:先裁尾(无 start 时再取最近 n)
        e8 = str(end).replace("-", "")[:8]
        ei = ei[ei["trade_date"] <= e8]
    if start:
        s8 = str(start).replace("-", "")[:8]
        ei = ei[ei["trade_date"] >= s8]
    elif len(ei) > n:
        ei = ei.tail(n)
    rows = []
    for td, close in zip(ei["trade_date"], ei["close"]):
        c = _num(close)
        if c is None or c <= 0:
            continue                      # 坏行跳过,绝不下发非正价
        rows.append({"date": f"{td[:4]}-{td[4:6]}-{td[6:8]}", "close": c})
    if not rows:
        raise ValueError("窗口内无沪深300数据")
    return rows


# ───────── 研判 / 条件单落盘(append-only JSONL,供「研判历史」回看;失败静默不阻断研判)─────────
# 模块级常量 + 函数(非闭包):便于测试 monkeypatch(对齐 console/tools.py _MEMORY_PATH 先例)。
_DEC_LOG = Path(__file__).resolve().parents[2] / "var" / "seats_decisions.jsonl"
_RUNS_LOG = Path(__file__).resolve().parents[2] / "var" / "seats_runs.jsonl"


def _persist_decision(kind: str, rec: dict) -> None:
    """追加一条研判/条件单记录到 JSONL(一行一条)。只在 LLM 成功路径调 → 失败不落盘。
    落盘失败(磁盘/权限)静默吞掉,绝不阻断主响应。"""
    try:
        _DEC_LOG.parent.mkdir(parents=True, exist_ok=True)
        full = {"id": f"{kind}_{int(time.time() * 1000)}_{random.randint(0, 9999)}",
                "ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
        full.update(rec)
        with _DEC_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(full, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 落盘失败不影响研判返回
        pass


def _agg_5min_to_30min(df5: "pd.DataFrame") -> "pd.DataFrame":
    """5min → 30min 聚合(与前端 frameData perGroup=6 同口径):按交易日分组,
    每 6 根 5min 切一块聚 OHLCV(末块允许 <6 根),绝不跨日拼块(午休/隔日断点天然分块)。
    入参须含 trade_date/open/high/low/close/vol(amount 可选);空/缺列 → 空 DataFrame。"""
    import pandas as _pd
    if df5 is None or len(df5) == 0:
        return _pd.DataFrame()
    need = {"trade_date", "open", "high", "low", "close", "vol"}
    if not need.issubset(set(df5.columns)):
        return _pd.DataFrame()
    df = df5.sort_values("trade_date").reset_index(drop=True)
    day = df["trade_date"].astype(str).str[:10]
    has_amt = "amount" in df.columns
    rows = []
    for _d, g in df.groupby(day, sort=True):
        g = g.reset_index(drop=True)
        for s in range(0, len(g), 6):
            ch = g.iloc[s:s + 6]
            rec = {
                "trade_date": ch["trade_date"].iloc[-1],
                "open": float(ch["open"].iloc[0]),
                "high": float(ch["high"].max()),
                "low": float(ch["low"].min()),
                "close": float(ch["close"].iloc[-1]),
                "vol": float(ch["vol"].sum()),
            }
            if has_amt:
                rec["amount"] = float(ch["amount"].sum())
            rows.append(rec)
    return _pd.DataFrame(rows)


# ───────── P1:叙事卡来源(GL 镜像档案 narrative card + out/ 研报)→ 喂 narrative.build_pool ─────────
import logging as _logging   # 诚实降级仍返空,但留痕(代码 bug/路径配错别被静默吞成空)

_log = _logging.getLogger("guanlan.seats.narrative")
_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "var" / "archive"
# out/ 报告文件名多为 ``<CODE>_<YYYY-MM-DD>.md``(如 SH605358_2026-06-10.md):从文件名
# 真取 code/落款日(非硬编关联),让大盘/行业级以外的个股研报也能按 code 浮出。
_OUT_NAME_RE = re.compile(r"^([A-Za-z]{2}\d{4,6})_(20\d{2}-\d{2}-\d{2})$")
_OUT_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

# 模块级 mtime 缓存:目录/文件 mtime 不变就复用,避免每次 decide 重扫盘(且 I/O 进 to_thread)。
_OUT_REPORTS_CACHE: tuple = (None, [])     # (out_dir mtime, results)
_ARCHIVE_CARDS_CACHE: tuple = (None, [])   # (archive_dir mtime, results)
_BREADTH_CACHE: tuple = (None, None)       # (parquet mtime, DataFrame)


def _load_archive_cards() -> list:
    """读 GL 镜像档案(``var/archive/*.json``)里的叙事卡;按目录 mtime 缓存;失败 / 无目录 → []。

    背后是 ``/archive/list`` 同源的薄 JSON 影子库。``build_pool`` 只收
    ``type==card & tier==narrative`` 的卡,故这里原样回吐(过滤由 build_pool 兜)。
    现存档案多为 strategy/research/decision 三类(无 narrative card)→ 自然返回空集,
    研报源走 ``_load_out_reports`` —— 诚实空,绝不补 demo。"""
    global _ARCHIVE_CARDS_CACHE
    try:
        if not _ARCHIVE_DIR.is_dir():
            return []
        mtime = _ARCHIVE_DIR.stat().st_mtime
        if _ARCHIVE_CARDS_CACHE[0] == mtime:
            return _ARCHIVE_CARDS_CACHE[1]
        out: list = []
        for p in sorted(_ARCHIVE_DIR.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — 单条坏 json 容错(合理),保持安静
                continue
        _ARCHIVE_CARDS_CACHE = (mtime, out)
        return out
    except Exception as _e:  # noqa: BLE001 — 目录级故障(路径配错等)留痕,仍诚实返空
        _log.warning("_load_archive_cards failed: %s", _e)
        return []


def _load_out_reports() -> list:
    """扫 ``out/*.md`` 取 ``{id,as_of(落款日),title,kind,path,codes,industry}``;按目录 mtime 缓存;失败 → []。

    落款日优先取文件名 ``<CODE>_<DATE>``;morning_brief / mainline 等无 code 文件名
    退回扫正文头部首个日期、``codes=[]``(只靠 industry 命中——多为大盘/行业级,
    都空则不浮出,可接受的保守行为)。"""
    global _OUT_REPORTS_CACHE
    try:
        from financial_analyst.buddy.tools import _project_root
        out_dir = (_project_root() / "out")
        if not out_dir.is_dir():
            return []
        mtime = out_dir.stat().st_mtime
        if _OUT_REPORTS_CACHE[0] == mtime:
            return _OUT_REPORTS_CACHE[1]
        out: list = []
        for p in out_dir.glob("*.md"):
            codes: list = []
            as_of = None
            m = _OUT_NAME_RE.match(p.stem)
            if m:
                codes = [m.group(1)]          # 文件名里的真 code(SH605358 等)
                as_of = m.group(2)
            else:
                txt = p.read_text(encoding="utf-8", errors="replace")[:2000]
                dm = _OUT_DATE_RE.search(txt)
                if dm:
                    as_of = dm.group(1)
            if not as_of:                     # 无落款日 → 不入池(无法 PIT)
                continue
            out.append({"id": p.stem, "as_of": as_of, "title": p.stem,
                        "kind": "研报", "path": str(p), "codes": codes, "industry": ""})
        _OUT_REPORTS_CACHE = (mtime, out)
        return out
    except Exception as _e:  # noqa: BLE001 — 留痕(别把代码 bug/路径配错静默吞成空),仍诚实返空
        _log.warning("_load_out_reports failed: %s", _e)
        return []


def _load_breadth_df():
    """读大盘 breadth 日产物(parquet);按文件 mtime 缓存 read_parquet;失败 → None。"""
    global _BREADTH_CACHE
    try:
        from guanlan_v2.strategy.compute.regen import MARKET_BREADTH_PARQUET
        import pandas as _pd
        p = Path(MARKET_BREADTH_PARQUET)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        if _BREADTH_CACHE[0] == mtime:
            return _BREADTH_CACHE[1]
        df = _pd.read_parquet(p)
        _BREADTH_CACHE = (mtime, df)
        return df
    except Exception as _e:  # noqa: BLE001 — 无产物/读盘失败留痕,仍诚实返 None
        _log.warning("_load_breadth_df failed: %s", _e)
        return None


def _surface_for_decide(code: str, industry: str, asof: str) -> list:
    """decide 内按日 PIT 浮出叙事卡的可单测封装:汇 GL 档案 + out/ 研报 → 池 → 当天浮出。
    任何环节失败 → []（诚实空,绝不退 demo / 编造）。返回 narrative.surface_narratives 的原始卡列表。"""
    try:
        from guanlan_v2.seats.narrative import build_pool, surface_narratives, DEFAULT_WINDOWS, DEFAULT_K
        pool = build_pool(_load_archive_cards(), _load_out_reports())
        return surface_narratives(pool, code, industry or "", asof, k=DEFAULT_K, windows=DEFAULT_WINDOWS)
    except Exception as _e:  # noqa: BLE001 — 留痕(别把浮出代码 bug 静默吞成空),仍诚实返空
        _log.warning("_surface_for_decide failed code=%s asof=%s: %s", code, asof, _e)
        return []


# ───────── 实盘仓位台账(append-only JSONL;全局一本账:实盘=一个组合,非按票)─────────
_LEDGER_LOG = Path(__file__).resolve().parents[2] / "var" / "seats_ledger.jsonl"


def _ledger_events() -> list:
    """读台账 JSONL 全量事件(升序=落盘序;坏行跳过、文件不存在 → 空,绝不抛)。"""
    out: list = []
    try:
        if _LEDGER_LOG.exists():
            for ln in _LEDGER_LOG.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(ln))
                except Exception:  # noqa: BLE001 — 坏行跳过
                    continue
    except Exception:  # noqa: BLE001
        pass
    return out


def _ledger_replay(events: list) -> dict:
    """重放**最后一个 open 之后**的事件 → 账本快照(纯函数,不含 MTM 取价)。

    再次 open = 重开新账:旧事件留档(append-only)但不参与重放。
    买入按加权平均成本;卖出减仓(avg_cost 不变),按 ``(price−avg_cost)×qty`` 计
    已实现盈亏,每笔 sell = 一笔了结(简化口径)逐笔记胜负;decision 纯记录不动仓位。
    返回 ``{opened, start_date, init_cash, cash, positions:{code:{code,name,qty,avg_cost}},
    days:[{date,trades,decisions}](按日逆序), realized, wins, n_closed}``;
    无 open → ``{"opened": False}``。"""
    last_open = -1
    for i, ev in enumerate(events):
        if ev.get("kind") == "open":
            last_open = i
    if last_open < 0:
        return {"opened": False}
    op = events[last_open]
    cash = float(op.get("cash") or 0)
    st: dict = {"opened": True, "start_date": str(op.get("date") or ""),
                "init_cash": cash, "cash": cash, "positions": {},
                "realized": 0.0, "wins": 0, "n_closed": 0}
    days: dict = {}

    def _day(d):
        return days.setdefault(str(d or ""), {"trades": [], "decisions": []})

    for ev in events[last_open + 1:]:
        k = ev.get("kind")
        if k == "trade":
            code = str(ev.get("code") or "")
            price = float(ev.get("price") or 0)
            qty = int(ev.get("qty") or 0)
            pos = st["positions"].get(code)
            if ev.get("side") == "buy":
                cost = price * qty
                st["cash"] -= cost
                if pos:
                    tot = pos["qty"] + qty
                    pos["avg_cost"] = (pos["qty"] * pos["avg_cost"] + cost) / tot
                    pos["qty"] = tot
                else:
                    st["positions"][code] = {"code": code, "name": ev.get("name") or code,
                                             "qty": qty, "avg_cost": price}
            elif ev.get("side") == "sell" and pos:
                sold = min(qty, pos["qty"])     # 防御:历史坏行也不把账卖穿
                pnl = (price - pos["avg_cost"]) * sold
                st["cash"] += price * sold
                st["realized"] += pnl
                st["n_closed"] += 1
                if pnl > 0:
                    st["wins"] += 1
                pos["qty"] -= sold
                if pos["qty"] <= 0:
                    st["positions"].pop(code, None)
            _day(ev.get("date"))["trades"].append(ev)
        elif k == "decision":
            _day(ev.get("date"))["decisions"].append(ev)
    st["days"] = [{"date": d, **days[d]} for d in sorted(days, reverse=True)]
    return st


# ───────────────────────── P2:配方因子 vintage IC 接线 ─────────────────────────
# decide 研判时,把本席配方因子 resolve 成 catalog id → 优先查本票 tsic、退而查截面 cs 的
# vintage(as-of 决策日)真 OOS IC,拼进 prompt 只喂 LLM(不进信号,加权混合是 P3)。
# 命中=真历史外样本 IC;未命中/样本不足=诚实「样本不足」(不再喂静态看未来 IC)。
_fid_index_cache = {"v": None, "n": -1}


def _factor_id_index() -> dict:
    """catalog 反查索引(缓存):expr→id、short/id→id。
    FACTOR_DEFS 被 refresh_factor_defs() 原地变更(.clear()+.update())→ 用条目数作版本探针重建,
    防新存 factorlib 因子(lib_*)resolve 不到而误判「样本不足」。"""
    if _fid_index_cache["v"] is None or _fid_index_cache["n"] != len(FACTOR_DEFS):
        by_expr, by_name = {}, {}
        for fid, m in FACTOR_DEFS.items():
            if m.get("expr"):
                by_expr[str(m["expr"])] = fid
            if m.get("short"):
                by_name[str(m["short"])] = fid
            by_name[str(fid)] = fid
        _fid_index_cache["v"] = {"by_expr": by_expr, "by_name": by_name}
        _fid_index_cache["n"] = len(FACTOR_DEFS)
    return _fid_index_cache["v"]


def _resolve_factor_id(rf: dict, index: dict):
    """配方因子 → catalog id:显式 id > expr > name/short。无则 None。"""
    if rf.get("id") and rf["id"] in index["by_name"]:
        return index["by_name"][rf["id"]]
    if rf.get("expr") and str(rf["expr"]) in index["by_expr"]:
        return index["by_expr"][str(rf["expr"])]
    if rf.get("name") and str(rf["name"]) in index["by_name"]:
        return index["by_name"][str(rf["name"])]
    return None


_HYBRID_TAU = 0.15   # 死区:|bias|≤τ → 观望(仅 w>0 混合路径)


def _llm_score(direction, confidence) -> float:
    """LLM 决策 → [-1,1]:买+ 卖− 观望0,幅度=confidence/100。"""
    d = str(direction or "")
    try:
        c = float(confidence) / 100.0
    except (TypeError, ValueError):
        return 0.0
    if "买" in d:
        return round(c, 4)
    if "卖" in d:
        return round(-c, 4)
    return 0.0


def _combine_factor_score(feats):
    """每因子 clip(dir·z,-1,1) 等权平均 → [-1,1];只纳入有 z 且有 dir 的因子;全无 → None。"""
    vals = []
    for f in (feats or []):
        z = f.get("z")
        dr = f.get("dir")
        if z is None or dr is None:
            continue
        vals.append(max(-1.0, min(1.0, float(dr) * float(z))))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _hybrid_direction(llm_dir, llm_score, factor_score, w):
    """返回 (hybrid_direction, bias)。w<=0 或 factor_score=None → 透传 LLM 方向(不经死区);
    否则 bias=(1-w)·llm+w·factor,sgn+死区 τ。"""
    try:
        w = float(w)
    except (TypeError, ValueError):
        w = 0.0
    if w <= 0 or factor_score is None:
        return (str(llm_dir or "观望"), round(float(llm_score), 4))
    bias = round((1.0 - w) * float(llm_score) + w * float(factor_score), 4)
    if bias > _HYBRID_TAU:
        return ("买入", bias)
    if bias < -_HYBRID_TAU:
        return ("卖出", bias)
    return ("观望", bias)


_TCAL_CACHE = {"cal": None, "tried": False}


def _trading_calendar():
    """A股全量交易日历(引擎 day 日历,DatetimeIndex);加载失败 → None。一次性缓存。
    注:用引擎全量日历(非 breadth 日产物索引——后者会滞后于真实交易日、漏算近端日)。"""
    if not _TCAL_CACHE["tried"]:
        _TCAL_CACHE["tried"] = True
        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            _cal = _lf.get_default_loader()._load_calendar("day")
            _TCAL_CACHE["cal"] = _pd.DatetimeIndex([_pd.Timestamp(d) for d in _cal])
        except Exception:  # noqa: BLE001 — 无日历退 None,_prev_trading_day 走日历减1兜底
            _TCAL_CACHE["cal"] = None
    return _TCAL_CACHE["cal"]


def _prev_trading_day(date_str: str, idx=None) -> str:
    """``date_str``(YYYY-MM-DD)的**上一交易日**(严格 < date_str)。日历优先级:显式 ``idx`` >
    引擎全量交易日历(`_trading_calendar`)> 日历 −1 天兜底(保守:只会更早、绝不更晚 → 不泄漏)。
    30min 盘中回退用——按真交易日历显式取(跳周末/假日:周一盘中→上周五,非日历周日),不靠下游
    ``idx≤date`` 隐式滑动(防未来有人把闸门改成精确等值匹配时悄悄看未来)。"""
    from datetime import date as _date, timedelta as _td
    d10 = str(date_str)[:10]
    cal = idx if idx is not None else _trading_calendar()
    try:
        if cal is not None and len(cal) > 0:
            import pandas as _pd
            ts = _pd.Timestamp(d10)
            earlier = [_pd.Timestamp(d) for d in cal if _pd.Timestamp(d) < ts]
            if earlier:
                return str(max(earlier).date())
    except Exception:  # noqa: BLE001 — 日历异常退保守日历减 1
        pass
    try:
        _y, _m, _d = (int(x) for x in d10.split("-"))
        return str(_date(_y, _m, _d) - _td(days=1))
    except Exception:  # noqa: BLE001
        return d10


def _rf_vintage_line(recipe_factors, code: str, asof: str, freq: str = "day"):
    """每因子 resolve→优先 tsic 退 cs vintage IC as-of;返回 (prompt 行, [vintage 记录])。
    PIT 锚:日线=决策日(EOD 已知当日 fwd 实现);30min 盘中=上一**交易**日(当日 EOD 那时未结算,
    realized_date=当日 的行=看未来 → 回退排除,同 regime_asof 口径;`_prev_trading_day` 按引擎全量
    交易日历显式取,跳周末/假日)。vintage 表是日粒度,asof 截到 10 字符比较 realized_date。"""
    d0 = asof[:10]
    if freq != "day":   # 30min 盘中:显式回退到严格早于决策日的上一交易日(引擎交易日历,不靠下游 ≤ 闸门隐式滑动)
        d0 = _prev_trading_day(d0)
    pit_date = d0
    idx = _factor_id_index()
    parts, vint = [], []
    for rf in (recipe_factors or [])[:8]:
        if not rf or not rf.get("name"):
            continue
        fid = _resolve_factor_id(rf, idx)
        r, kind = None, None
        if fid:
            r = tsic_vintage_asof(code, fid, pit_date)
            kind = "tsic" if r else None
            if not r:
                r = cs_vintage_asof(fid, pit_date)
                kind = "cs" if r else None
        if r:
            tag = "本票" if kind == "tsic" else "截面"
            parts.append(f"{rf['name']}(IC@{r['asof']}={r['ic']}·OOS·n={r['n']}·{tag})")
            # P3:命中 vintage 后再算该票因子 as-of 的 z 分,score=clip(dir·z) 进加权混合 + 前端显形。
            _z = factor_z_asof(code, fid, pit_date)
            _zv = (_z["z"] if _z else None)
            _sc = (max(-1.0, min(1.0, float(r["dir"]) * float(_zv)))
                   if (_zv is not None and r.get("dir") is not None) else None)
            vint.append({"name": rf["name"], "id": fid, "ic": r["ic"], "n": r["n"],
                         "kind": kind, "asof": r["asof"], "z": _zv, "dir": r.get("dir"), "score": _sc})
        else:
            parts.append(f"{rf['name']}(IC 样本不足)")
            vint.append({"name": rf["name"], "id": fid, "ic": None, "n": 0,
                         "kind": None, "asof": asof, "z": None, "dir": None, "score": None})
    return ("; ".join(parts) or "无"), vint


def build_seats_router() -> APIRouter:
    router = APIRouter(prefix="/seats", tags=["seats"])

    _CALIB_CACHE: dict = {}          # horizon → (epoch_ts, payload);TTL 600s
    _CALIB_TTL = 600.0

    @router.get("/decisions")
    async def seats_decisions(code: str = "", kind: str = "", limit: int = 50,
                              run_id: str = "", exclude_runs: int = 0):
        """读 var/seats_decisions.jsonl,逆序(最新在前),可按 code/kind 过滤。
        run 化(2026-06-12):``run_id`` 给定只看该 run 的记录;``exclude_runs=1`` 剔除
        一切带 run_id 的记录(哨兵轮询用,防 PIT 回放刷屏)。缺省两者都不生效 = 旧行为。
        文件不存在 / 坏行 → 空列表不崩(恒 HTTP200)。"""
        out: list = []
        try:
            if _DEC_LOG.exists():
                lines = _DEC_LOG.read_text(encoding="utf-8").splitlines()
                cap = max(1, min(int(limit or 50), 300))
                for ln in reversed(lines):
                    try:
                        r = json.loads(ln)
                    except Exception:  # noqa: BLE001 — 坏行跳过
                        continue
                    if code and re.sub(r"\D", "", str(r.get("code", ""))) != re.sub(r"\D", "", code):
                        continue
                    if kind and r.get("kind") != kind:
                        continue
                    if run_id and str(r.get("run_id") or "") != run_id:
                        continue
                    if exclude_runs and r.get("run_id"):
                        continue
                    out.append(r)
                    if len(out) >= cap:
                        break
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse({"ok": True, "decisions": out, "total": len(out)})

    @router.get("/news")
    async def seats_news(code: str = "", asof: str = "", mode: str = "pit", window: int = 250):
        """落子 K 线新闻标记流。回测 ``mode=pit`` 按 ``as-of`` PIT 过滤 pit_store;
        ``mode=live`` 取实时快讯。缺 code → ok:False;其余恒 HTTP200 诚实降级。"""
        if not str(code).strip():
            return JSONResponse({"ok": False, "reason": "缺 code", "items": []})
        try:
            from guanlan_v2.seats.news_marks import assemble_news_marks
            payload = await asyncio.to_thread(
                assemble_news_marks, code, asof, mode, int(window or 250))
            return JSONResponse(payload)
        except Exception as exc:  # noqa: BLE001 — 恒 200,诚实报因
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}", "items": []})

    # ───────── run 头注册 / 查询(「让 agent 真跑」批跑分组,append-only var/seats_runs.jsonl)─────────
    @router.post("/runs")
    async def seats_runs_register(payload: dict = Body(default={})):
        """注册一次 run 的头记录(前端跑完一轮「让 agent 真跑」后调用)。
        ``run_id`` / ``code`` 非空必填(缺 → 422);其余字段(strategy_id/strategy_name/tf/
        start_date/end_date/n_buy/n_sell/n_watch/n_err/model …)原样透传落盘,自动补 ts。
        返回 ``{ok:true, run_id}``;落盘失败 → ok:False(恒 HTTP200,诚实降级)。"""
        run_id = str(payload.get("run_id") or "").strip()
        code = str(payload.get("code") or "").strip()
        if not run_id or not code:
            return JSONResponse({"ok": False, "reason": "缺 run_id 或 code"}, status_code=422)
        rec = dict(payload)
        rec["run_id"], rec["code"] = run_id, code
        rec.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        try:
            _RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _RUNS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — 落盘失败诚实报因,不 500
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        return JSONResponse({"ok": True, "run_id": run_id})

    @router.post("/runs/clear")
    async def seats_runs_clear(payload: dict = Body(default={})):
        """「清空回测历史」= append 一条水位标记 ``{kind:'clear', code}``(**不改写/不删历史行**,
        append-only 红线)。``code`` 给定→只清该票(数字核);空→全局清空。列表端只显示
        水位之后注册的 run。"""
        code = re.sub(r"\D", "", str(payload.get("code") or ""))
        rec = {"kind": "clear", "code": code,
               "ts": datetime.now().isoformat(timespec="seconds")}
        try:
            _RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _RUNS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        return JSONResponse({"ok": True, "cleared": code or "all"})

    @router.get("/runs")
    async def seats_runs_list(code: Optional[str] = None, limit: int = 30):
        """run 头列表,逆序(最新在前)。``code`` 按**数字核**匹配(``688012`` ↔ ``SH688012``,
        对齐落子前端「数字核」客户端匹配口径);``clear`` 水位标记之前(更旧)的 run 不显示
        (标记行本身也不输出);文件不存在 / 坏行 → 空列表恒 HTTP200。"""
        out: list = []
        try:
            cap = max(1, min(int(limit or 30), 100))
            want = re.sub(r"\D", "", str(code or ""))
            cleared_all, cleared_codes = False, set()
            if _RUNS_LOG.exists():
                for ln in reversed(_RUNS_LOG.read_text(encoding="utf-8").splitlines()):
                    try:
                        r = json.loads(ln)
                    except Exception:  # noqa: BLE001 — 坏行跳过
                        continue
                    if r.get("kind") == "clear":   # 逆序遍历:标记之后(更旧)的同范围 run 全隐藏
                        c = re.sub(r"\D", "", str(r.get("code") or ""))
                        if c:
                            cleared_codes.add(c)
                        else:
                            cleared_all = True
                        continue
                    nuc = re.sub(r"\D", "", str(r.get("code") or ""))
                    if cleared_all or nuc in cleared_codes:
                        continue
                    if want and nuc != want:
                        continue
                    out.append(r)
                    if len(out) >= cap:
                        break
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse({"ok": True, "runs": out, "total": len(out)})

    # ───────── 实盘仓位台账(落子实盘:后端持久、设初始资金、逐日记录)─────────
    @router.post("/ledger")
    async def seats_ledger_append(payload: dict = Body(default={})):
        """台账落一笔事件(append-only ``var/seats_ledger.jsonl``,自动补 ts)。

        三种 kind:
        - ``open``:开账 ``{date, cash>0}``;**再次 open = 重开新账**(旧事件留档)。
        - ``trade``:``{date, code, name, side:buy|sell, price>0, qty:int>0(股),
          reason?, source:manual|order|decide, decision_id?}``;服务端**写前重放**当前账
          校验:buy 需 cash ≥ price×qty、sell 需该票持仓足额。
        - ``decision``:纯决策记录不动仓位 ``{date, code, name, direction, confidence,
          decision_id?, source:timer|manual|sentry}``。
        非法 → 422 ``{ok:False, reason}``;合法 → ``{ok:true}``。"""
        def _bad(reason: str):
            return JSONResponse({"ok": False, "reason": reason}, status_code=422)

        ev = dict(payload or {})
        kind = ev.get("kind")
        date = str(ev.get("date") or "").strip()
        if kind not in ("open", "trade", "decision"):
            return _bad("kind 须为 open/trade/decision")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return _bad("date 须为 YYYY-MM-DD")
        ev["date"] = date

        if kind == "open":
            cash = _num(ev.get("cash"))
            if cash is None or cash <= 0:
                return _bad("open 需 cash > 0")
            ev["cash"] = cash
        elif kind == "trade":
            code = str(ev.get("code") or "").strip()
            side = ev.get("side")
            price = _num(ev.get("price"))
            qty_raw = _num(ev.get("qty"))
            try:
                qty = int(ev.get("qty"))
            except (TypeError, ValueError):
                qty = 0
            if not code:
                return _bad("trade 需 code")
            if side not in ("buy", "sell"):
                return _bad("side 须为 buy/sell")
            if price is None or price <= 0:
                return _bad("price 须 > 0")
            if qty <= 0 or qty != qty_raw:
                return _bad("qty 须为正整数(股)")
            # 写前读:重放当前账校验(防超买超卖;拒绝的事件绝不落盘)
            st = _ledger_replay(_ledger_events())
            if not st.get("opened"):
                return _bad("未开账(先 kind=open 设初始资金)")
            if side == "buy" and price * qty > st["cash"] + 1e-6:
                return _bad(f"现金不足:需 {price * qty:.2f} > 可用 {st['cash']:.2f}")
            if side == "sell":
                pos = st["positions"].get(code)
                have = pos["qty"] if pos else 0
                if qty > have:
                    return _bad(f"持仓不足:卖 {qty} 股 > 持有 {have} 股")
            ev["code"], ev["side"], ev["price"], ev["qty"] = code, side, price, qty
            ev.setdefault("source", "manual")
        else:  # decision
            code = str(ev.get("code") or "").strip()
            if not code:
                return _bad("decision 需 code")
            ev["code"] = code

        ev.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        try:
            _LEDGER_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _LEDGER_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001 — 落盘失败诚实报因,不 500
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        return JSONResponse({"ok": True})

    @router.get("/ledger/state")
    async def seats_ledger_state():
        """重放台账 → 账户快照:现金 / 持仓(加权成本)/ MTM 权益 / 逐日时间线(逆序,
        今日在前)/ 已实现盈亏 + 胜率(每笔 sell=一笔了结)。

        MTM:持仓现价 = 引擎日线(近 30 天窗)末根收盘,code 经 normalize_code 归一;
        任一持仓缺价 → 该持仓 mkt_value=null 且 equity=null(诚实降级,covered 计
        已估值数);loader 整体失败不崩:equity=null 其余字段照常;无持仓 equity=cash。
        未开账 → ``{ok:true, opened:false}``。"""
        st = _ledger_replay(_ledger_events())
        if not st.get("opened"):
            return JSONResponse({"ok": True, "opened": False})

        positions = list(st["positions"].values())
        closes: dict = {}                       # code → (date, close) | None
        if positions:
            try:
                import pandas as _pd
                from financial_analyst.data import loader_factory as _lf
                try:
                    from financial_analyst.buddy.tools import normalize_code as _norm
                except Exception:  # noqa: BLE001 — 引擎不可导入时裸用 code
                    _norm = None
                loader = _lf.get_default_loader()
                end = str(_pd.Timestamp.now().date())
                start = str((_pd.Timestamp.now() - _pd.Timedelta(days=30)).date())

                def _last_close(c: str):
                    cc = c
                    if _norm is not None:
                        try:
                            cc = _norm(c)
                        except Exception:  # noqa: BLE001
                            cc = (c or "").strip().upper()
                    df = loader.fetch_quote(cc, start, end, "day")
                    df = _drop_unsettled(df)    # 当日未结算占位行不当现价
                    if df is None or len(df) == 0 or "close" not in df.columns:
                        return None
                    rec = df.iloc[-1]
                    px = _num(rec.get("close"))
                    if px is None or px <= 0:
                        return None
                    d = rec.get("trade_date")
                    return (str(d)[:10] if d is not None else None, px)

                for c in sorted({p["code"] for p in positions}):   # 合并去重后逐票取
                    try:
                        closes[c] = await asyncio.to_thread(_last_close, c)
                    except Exception:  # noqa: BLE001 — 单票失败 = 该票缺价
                        closes[c] = None
            except Exception:  # noqa: BLE001 — loader 整体失败 → 全缺价,不崩
                closes = {}

        covered = 0
        equity: Optional[float] = st["cash"]
        eq_dates: list = []
        pos_out = []
        for p in positions:
            hit = closes.get(p["code"])
            last_close = hit[1] if hit else None
            if last_close is not None:
                covered += 1
                mkt = last_close * p["qty"]
                upl = (last_close - p["avg_cost"]) * p["qty"]
                if equity is not None:
                    equity += mkt
                if hit[0]:
                    eq_dates.append(hit[0])
            else:
                mkt = upl = None
                equity = None               # 任一缺价 → 权益诚实置空,绝不半真半假
            pos_out.append({"code": p["code"], "name": p["name"], "qty": p["qty"],
                            "avg_cost": p["avg_cost"], "last_close": last_close,
                            "mkt_value": mkt, "upl": upl})

        return JSONResponse({
            "ok": True, "opened": True,
            "start_date": st["start_date"], "init_cash": st["init_cash"],
            "cash": st["cash"],
            "positions": pos_out, "n_positions": len(pos_out), "covered": covered,
            "equity": equity,
            # 估值日取覆盖票里最旧的末根日(最保守的 as-of;全缺价/无持仓 → null)
            "equity_date": (min(eq_dates) if (equity is not None and eq_dates) else None),
            "days": st["days"],
            "realized": st["realized"], "n_closed": st["n_closed"],
            "win_rate": ((st["wins"] / st["n_closed"]) if st["n_closed"] else None),
        })

    @router.get("/tca")
    async def seats_tca():
        """事后 TCA(执行质量):重放台账 trade 事件,对每笔成交算「成交价 vs 当日基准」执行成本(bps)。
        基准 = 当日 VWAP(5min 量价加权)/ 开盘 / 收盘 /(决策链接成交)到达价=决策当日收盘(asof 带
        时分则取 ≤asof 末根 5min)。成交额加权汇总 + 按日 + 按策略。**只读台账、不碰执行**;缺基准诚实
        None。台账日级+影子/纸面盘 → 非 tick 级真实回报(warnings 显形)。未开账 → {ok:true,opened:false}。"""
        from guanlan_v2.seats import tca as _tca

        st = _ledger_replay(_ledger_events())
        if not st.get("opened"):
            return JSONResponse({"ok": True, "opened": False})

        trades: list = []
        for day in st.get("days", []):
            for ev in day.get("trades", []):
                if ev.get("kind") == "trade":
                    trades.append(ev)

        warnings = [
            "TCA 口径=成交价 vs 当日基准(VWAP/开盘/收盘);台账为日级+影子/纸面盘,非 tick 级真实回报。",
            "到达价(IS)=决策当日收盘(asof 带时分则取≤asof末根5min),仅「决策链接成交」(有 decision_id)可得;manual 单无到达价。",
            "成本 bps 方向定号:买高于/卖低于基准=正成本(吃亏),负=占便宜;按成交额加权。",
        ]
        if not trades:
            return JSONResponse({"ok": True, "opened": True, "n_trades": 0,
                                 "start_date": st.get("start_date"),
                                 "tca": _tca.summarize_tca([]),
                                 "warnings": warnings + ["台账暂无成交事件(先记买卖才有 TCA)。"]})

        # 决策映射:decision_id → {asof, strategy_id, strategy_name}(供到达价 + 按策略归因)
        dec_map: dict = {}
        try:
            if _DEC_LOG.exists():
                for ln in _DEC_LOG.read_text(encoding="utf-8").splitlines():
                    try:
                        _r = json.loads(ln)
                    except Exception:  # noqa: BLE001 — 坏行跳过
                        continue
                    rid = _r.get("id")
                    if rid:
                        dec_map[rid] = {"asof": _r.get("asof") or _r.get("date"),
                                        "strategy_id": _r.get("strategy_id"),
                                        "strategy_name": _r.get("strategy_name")}
        except Exception as _dexc:  # noqa: BLE001 — 决策档读失败 = 无到达价/策略名,不崩
            dec_map = {}
            warnings.append(f"决策档读取失败({type(_dexc).__name__}),到达价 IS 与按策略归因不可用(非「全是 manual 单」)。")

        import pandas as _pd  # noqa: F401  (loader 返回 DataFrame)
        from financial_analyst.data import loader_factory as _lf
        try:
            from financial_analyst.buddy.tools import normalize_code as _norm
        except Exception:  # noqa: BLE001
            _norm = None
        try:
            loader = _lf.get_default_loader()
        except Exception:  # noqa: BLE001
            loader = None
        if loader is None:   # 取数器挂了 → 全部基准必为 None,显形(否则像「真无滑点」)
            warnings.append("数据 loader 初始化失败,所有 OHLC/VWAP/到达价基准均不可用,本次 TCA 成本全为 None(非真无滑点)。")

        cache: dict = {}

        def _fetch(code: str, d: str):
            key = (code, d)
            if key in cache:
                return cache[key]
            res = {"open": None, "close": None, "vwap": None, "bars5": []}
            if loader is not None and code and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d) or ""):
                cc = code
                if _norm is not None:
                    try:
                        cc = _norm(code)
                    except Exception:  # noqa: BLE001
                        cc = (code or "").strip().upper()
                try:
                    dfd = _drop_unsettled(loader.fetch_quote(cc, d, d, "day"))
                    if dfd is not None and len(dfd) and "close" in dfd.columns:
                        sub = (dfd[dfd["trade_date"].astype(str).str[:10] == d]
                               if "trade_date" in dfd.columns else dfd)
                        rec = sub.iloc[-1] if len(sub) else dfd.iloc[-1]
                        res["open"] = _num(rec.get("open"))
                        res["close"] = _num(rec.get("close"))
                except Exception:  # noqa: BLE001 — 该票该日日线缺 = OHLC 留 None
                    pass
                try:
                    df5 = loader.fetch_quote(cc, d, d, "5min")
                    if df5 is not None and len(df5):
                        recs = df5.to_dict("records")
                        res["bars5"] = recs
                        res["vwap"] = _tca.day_vwap(recs)
                except Exception:  # noqa: BLE001 — 5min 缺 = VWAP 留 None
                    pass
            cache[key] = res
            return res

        rows = []
        for ev in trades:
            code = str(ev.get("code") or "")
            d = str(ev.get("date") or "")
            day = await asyncio.to_thread(_fetch, code, d)
            refs = {"vwap": day["vwap"], "open": day["open"], "close": day["close"], "arrival": None}
            ev2 = dict(ev)
            did = ev.get("decision_id")
            dec = dec_map.get(did) if did else None
            if dec:
                if dec.get("strategy_id") is not None:
                    ev2["strategy_id"] = dec.get("strategy_id")
                if dec.get("strategy_name") is not None:
                    ev2["strategy_name"] = dec.get("strategy_name")
                asof = str(dec.get("asof") or "")
                ad = asof[:10]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ad):
                    adata = await asyncio.to_thread(_fetch, code, ad)
                    arr = adata["close"]                       # 到达价缺省=决策当日收盘
                    if len(asof) > 10 and adata["bars5"]:      # 盘中决策:≤asof 末根 5min 收盘
                        t = asof.replace("T", " ")
                        cand = [b for b in adata["bars5"] if str(b.get("trade_date") or "")[:16] <= t[:16]]
                        if cand:
                            arr = _num(cand[-1].get("close")) or arr
                    refs["arrival"] = arr
            rows.append(_tca.compute_trade_tca(ev2, refs))

        summary = _tca.summarize_tca(rows)
        cov = (summary.get("coverage") or {})
        if cov.get("vwap", 0) < len(rows):
            warnings.append(f"VWAP 覆盖 {cov.get('vwap', 0)}/{len(rows)} 笔(其余当日 5min 缺数,该笔 vs VWAP 留空)。")
        if cov.get("arrival", 0) < len(rows):
            warnings.append(f"到达价覆盖 {cov.get('arrival', 0)}/{len(rows)} 笔(其余为 manual 单或决策档缺,无到达价 IS)。")
        return JSONResponse({
            "ok": True, "opened": True, "start_date": st.get("start_date"),
            "n_trades": len(rows), "tca": summary, "warnings": warnings,
        })

    @router.get("/calibration")
    async def seats_calibration(horizon: int = 5):
        """置信度校准:各置信档的真实 N 日方向命中率(口径见 calibration.py docstring)。
        读全量 decide 记录 + 引擎日线;10 分钟缓存;失败恒 HTTP200 诚实降级。"""
        try:
            hz = max(1, min(int(horizon or 5), 20))
            import time as _t
            hit = _CALIB_CACHE.get(hz)
            if hit and _t.time() - hit[0] < _CALIB_TTL:
                return JSONResponse(hit[1])

            records: list = []
            if _DEC_LOG.exists():
                for ln in _DEC_LOG.read_text(encoding="utf-8").splitlines():
                    try:
                        _r = json.loads(ln)
                    except Exception:  # noqa: BLE001 — 坏行跳过
                        continue
                    if _r.get("run_id"):
                        continue   # run 批跑(PIT 回放)记录不进校准,防污染真实命中率
                    records.append(_r)
            codes = sorted({str(r.get("code", "")).upper() for r in records
                            if r.get("kind") == "decide" and r.get("code")})

            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            start = str((_pd.Timestamp.now() - _pd.Timedelta(days=400)).date())
            end = str(_pd.Timestamp.now().date())

            def _closes(c: str):
                df = loader.fetch_quote(c, start, end, "day")
                if df is None or len(df) == 0 or "close" not in df.columns:
                    return []
                dcol = "trade_date" if "trade_date" in df.columns else df.columns[0]
                return [(str(d)[:10], float(v)) for d, v in zip(df[dcol], df["close"])]

            closes_by_code: dict = {}
            for c in codes[:40]:   # 防御上限:跨票过多时截断(note 里写明)
                try:
                    closes_by_code[c] = await asyncio.to_thread(_closes, c)
                except Exception:  # noqa: BLE001 — 单票取数失败 → 该票记录自然剔除
                    closes_by_code[c] = []

            from guanlan_v2.seats.calibration import calibration_table, evaluate
            ev = evaluate(records, closes_by_code, horizon=hz)
            payload = {
                "ok": True, "horizon": hz,
                "total_decides": sum(1 for r in records if r.get("kind") == "decide"),
                "mature": len(ev),
                "buckets": calibration_table(ev),
                "note": ("口径:asof(或其后首根)收盘进、+N根收盘出,方向命中,不含成本;"
                         "观望不计入;未成熟剔除" + ("" if len(codes) <= 40 else f";票数{len(codes)}>40 已截断")),
            }
            _CALIB_CACHE[hz] = (_t.time(), payload)
            return JSONResponse(payload)
        except Exception as exc:  # noqa: BLE001 — 诚实降级
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/daily")
    async def seats_daily(code: str, n: int = 250,
                          start: Optional[str] = None,
                          end: Optional[str] = None,
                          freq: str = "day"):
        """历史 OHLCV bar(``freq=day`` 日线 / ``freq=5min`` 历史 5min)。

        参数:
          - ``code``  裸码或带前缀(``300750`` / ``SZ300750`` 均可,服务端归一化)。
          - ``freq``  ``day``(默认)或 ``5min``。
          - ``n``     返回最近 n 根 bar(day 默认 250、上限 1200;5min 上限 8000);
                      给了 ``start`` 则忽略 n,直接用窗口。
          - ``start`` / ``end``  显式窗口(``YYYY-MM-DD``,可选;``end`` 未给→取至最新)。

        ``date`` 日线为 ``YYYY-MM-DD``、5min 为 ``YYYY-MM-DD HH:MM``;``vol`` 原始
        股、``amount`` 元。坏 code / 无数据 / 异常 → ``ok:False`` + 空 bars(HTTP 200,
        前端降级,不抛 500)。
        返回 ``{ok, code, freq, n, bars:[{date,open,high,low,close,vol,amount}]}``。
        """
        # —— code 归一化(借引擎 normalize_code:300750→SZ300750;失败则裸用)——
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001  —— 引擎不可导入时也别 500
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "freq": freq,
                                 "bars": [], "reason": "code 为空"})

        freq = "5min" if str(freq or "").lower() in ("5min", "5", "min5", "5m") else "day"
        cap = 8000 if freq == "5min" else 1200
        n = max(1, min(int(n or 250), cap))

        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf

            e = end or "2100-01-01"                 # 未给 end → 远期,loader 自动裁到最新
            if start:
                s = start
            else:
                bars_per_day = 48 if freq == "5min" else 1   # A股日内 48 根 5min
                lookback = int((n / bars_per_day) * 1.7) + 15
                anchor = min(_pd.Timestamp(e), _pd.Timestamp.now())
                s = str((anchor - _pd.Timedelta(days=lookback)).date())

            loader = _lf.get_default_loader()
            # fetch_quote 同步阻塞(读 bin)→ to_thread 不冻事件循环(同引擎 /watch/bars)
            df = await asyncio.to_thread(loader.fetch_quote, c, s, e, freq)
            df = _drop_unsettled(df)        # 丢当日未结算 null 占位行 → 绝不吐 null 价柱

            bars = []
            if df is not None and len(df) > 0:
                if not start and len(df) > n:
                    df = df.tail(n)             # 默认取最近 n 根
                cut = 16 if freq == "5min" else 10    # 5min 保留 时:分
                for rec in df.to_dict("records"):
                    td = rec.get("trade_date")
                    bars.append({
                        "date": (str(td)[:cut] if td is not None else None),
                        "open": _num(rec.get("open")),
                        "high": _num(rec.get("high")),
                        "low": _num(rec.get("low")),
                        "close": _num(rec.get("close")),
                        "vol": _num(rec.get("vol")),
                        "amount": _num(rec.get("amount")),
                    })
            if not bars:
                return JSONResponse({"ok": False, "code": c, "freq": freq,
                                     "bars": [], "reason": "无数据(code 不存在或窗口无交易)"})
            return JSONResponse({"ok": True, "code": c, "freq": freq,
                                 "n": len(bars), "bars": bars})
        except Exception as exc:  # noqa: BLE001  —— 诚实失败,不退回假数据
            return JSONResponse({"ok": False, "code": c, "freq": freq,
                                 "bars": [], "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/factors")
    async def seats_factors(code: str, date: Optional[str] = None, freq: str = "day"):
        """历史交易日 D 的「触发·量化因子」证据(复盘落子证据链 ③b)。

        **价量/表达式因子(34 项)**:PIT 真算 —— 服务端固定 ``end=date`` 取 ≤D 含当日收盘
        的日线(qlib ``_slice`` 闭区间、day 频不延展),喂 ``factors.core.compute_factors``。
        复刻引擎 ``FactorComputer._execute`` 的 **EOD-of-D** 口径(收盘后该股研究因子,
        **非**盘前可交易信号)—— 故 ``end=date``,**绝不**走 ≤T-1 的 ``fetch_quote_leq_prev``。

        **模型因子(fm/combo/lgb/v4)**:旁路读 ``load_signal_pack(date=D)``。写入侧只产单日
        EOD 包(当前仅 2026-06-01),历史 D 多半无 → ``model.available=False`` + 各字段 null
        + note;**绝不**用今日值冒充历史 D(张冠李戴)。``load_signal_pack`` 显式传 ``date=end``
        而非 ``None``(None 会落到最新日 → 看未来)。

        ``code`` 裸码/带前缀(归一化);``date`` PIT 锚(``YYYY-MM-DD``,未给→最新);``freq``
        恒 day(价量窗口因子按日定义,5min 不进因子链)。坏 code / 无数据 / 异常 → ``ok:False``
        + reason(恒 HTTP200,前端降级不抛)。
        返回 ``{ok, code, date, freq, factors:{<34键>:float|null}, model:{...}}``。
        """
        def _sval(v: Any) -> Optional[str]:           # 字符串字段清洗:NaN/None/空 → None
            if v is None:
                return None
            if isinstance(v, float) and v != v:       # NaN
                return None
            s = str(v).strip()
            return s or None

        # —— code 归一化(同 seats_daily)——
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "date": date, "freq": "day",
                                 "factors": {}, "model": {"available": False},
                                 "reason": "code 为空"})

        # 模型因子骨架(未命中即诚实空,绝不伪造历史 D)
        model = {"available": False, "source": "daily_signal_pack.parquet", "asof": None,
                 "fm_pct": None, "combo_pct": None, "fm_cluster": None,
                 "lgb_rank": None, "lgb_pct": None, "v4_rating": None, "v4_score": None,
                 "mainline_state": None, "report_summary": None,
                 "note": "模型因子仅 EOD 批量产出,该日无 pack → null,需重算/收盘后回填"}

        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf

            # PIT 锚:end=date(未给→最新);start=end−180 日历日(≈120 交易 bar,够最长 60 窗口 + EMA 收敛)
            anchor = min(_pd.Timestamp(date), _pd.Timestamp.now()) if date else _pd.Timestamp.now()
            end = str(anchor.date())
            start = str((anchor - _pd.Timedelta(days=180)).date())

            loader = _lf.get_default_loader()
            # end=date 含当日收盘(EOD-of-D);qlib _slice index<=end_ts 闭区间,无 >D 泄漏
            df = await asyncio.to_thread(loader.fetch_quote, c, start, end, "day")
            df = _drop_unsettled(df)        # 丢未结算占位行:asof/因子只看已结算 bar(PIT 不被今日 null 带偏)

            factors: dict = {}
            asof: Optional[str] = None
            if df is not None and len(df) > 0:
                td = df["trade_date"].iloc[-1] if "trade_date" in df.columns else None
                asof = str(td)[:10] if td is not None else end   # 实际取到的最后 bar 日(可能 < 请求 date)
                try:
                    from financial_analyst.factors.core import FACTOR_NAMES, compute_factors
                    vals = compute_factors(df)
                    factors = {k: _num(vals.get(k)) for k in FACTOR_NAMES}
                except Exception:  # noqa: BLE001  —— 因子算不动也不 500,给空因子
                    factors = {}

            # —— 模型因子旁路:按请求日(pack 自身 EOD 键)取行,命中仅当日、历史多 null ——
            try:
                from financial_analyst.watch.signal_pack import load_signal_pack
                pack = await asyncio.to_thread(load_signal_pack, None, end)  # 显式 date=end,绝不落到 None=最新
                if pack is not None and len(pack) > 0 and "code" in pack.columns:
                    rows = pack[pack["code"].astype(str).str.upper() == c.upper()]
                    if len(rows) > 0:
                        r = rows.iloc[0].to_dict()
                        model.update({
                            "available": True, "asof": end,
                            "fm_pct": _num(r.get("fm_pct")), "combo_pct": _num(r.get("combo_pct")),
                            "fm_cluster": _num(r.get("fm_cluster")),
                            "lgb_rank": _num(r.get("lgb_rank")), "lgb_pct": _num(r.get("lgb_pct")),
                            "v4_rating": _sval(r.get("v4_rating")), "v4_score": _num(r.get("v4_score")),
                            "mainline_state": _sval(r.get("mainline_state")),
                            "report_summary": _sval(r.get("report_summary")),
                            "note": "",
                        })
            except Exception:  # noqa: BLE001  —— 模型旁路失败只让 model 退化,不影响价量因子、不 500
                pass

            # —— 模型因子第二源:guanlan FM backfill 缓存(历史 D 的 fm_pct/combo_pct/fm_cluster,
            #    离线 guanlan_v2/seats/fm_backfill.py 批算、按 date+code;LGB/v4 不在内)。
            #    daily_signal_pack 未命中(历史 D)时回退到这里。仍只读 parquet、不碰 G:/stocks。
            if not model["available"]:
                try:
                    from pathlib import Path as _Path
                    cache_p = _Path(__file__).resolve().parents[2] / "var" / "seats_fm_backfill.parquet"
                    if cache_p.exists():
                        fmdf = await asyncio.to_thread(_pd.read_parquet, cache_p)
                        if fmdf is not None and len(fmdf) > 0 and "date" in fmdf.columns:
                            hit = fmdf[(fmdf["date"].astype(str) == end)
                                       & (fmdf["code"].astype(str).str.upper() == c.upper())]
                            if len(hit) > 0:
                                r = hit.iloc[0].to_dict()
                                fmp, cmb = _num(r.get("fm_pct")), _num(r.get("combo_pct"))
                                if fmp is not None or cmb is not None:   # NaN(该股该日无效)→ 不冒充
                                    look_ahead = str(end) <= "2026-04-15"   # W11 训练截止
                                    model.update({
                                        "available": True, "asof": end,
                                        "source": "seats_fm_backfill.parquet (FM W11)",
                                        "fm_pct": fmp, "combo_pct": cmb,
                                        "fm_cluster": _num(r.get("fm_cluster")),
                                        "lookahead": bool(look_ahead),
                                        "note": ("⚠ D≤2026-04-15:FM/combo 为 W11 模型 look-ahead(训练见过该段);LGB/v4 未重算"
                                                 if look_ahead else
                                                 "FM/combo = W11 OOS·PIT 重算;LGB/v4 未重算"),
                                    })
                except Exception:  # noqa: BLE001  —— 缓存读失败只让 model 退化,不 500
                    pass

            if not factors and not model["available"]:
                return JSONResponse({"ok": False, "code": c, "date": (asof or end), "freq": "day",
                                     "factors": {}, "model": model,
                                     "reason": "无数据(code 不存在或窗口无交易)"})
            return JSONResponse({"ok": True, "code": c, "date": (asof or end), "freq": "day",
                                 "factors": factors, "model": model})
        except Exception as exc:  # noqa: BLE001  —— 诚实失败,不退回假数据
            return JSONResponse({"ok": False, "code": c, "date": date, "freq": "day",
                                 "factors": {}, "model": model,
                                 "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/decide")
    async def seats_decide(payload: dict = Body(default={})):
        """⑤ 席位 agent **真研判**(on-demand 单笔):综合 量化因子 + 经验卡 + 研报 + 市况,
        调**真 LLM**(deepseek,经引擎 ``watch-agent`` 配置)对 (seat, code, date) 给出
        方向 / 置信 / 理由。区别于 scanSeat 价量启发式(那是回放骨架),这里是真模型推理。

        PIT:因子服务端按 ``end=date`` 真算(≤当日收盘);system prompt 明令只用已发生信息。
        body: ``{code, name, date, seat_cn, creed, card:{name,insight,verdict,conf,ic}, research:[str], regime}``
        坏入参 / LLM 失败 → ``ok:False`` + reason(恒 HTTP200,前端降级)。
        """
        code = str(payload.get("code") or "").strip()
        date = payload.get("date")
        seat_cn = str(payload.get("seat_cn") or "席位")
        creed = str(payload.get("creed") or "")
        name = str(payload.get("name") or code)
        card = payload.get("card") or {}
        cards = payload.get("cards") or ([card] if card else [])
        recipe_factors = payload.get("recipe_factors") or []
        w = payload.get("w")   # P3:策略加权混合权重(0=纯 LLM;>0 把因子 z 分混进 hybrid_direction)
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_name = str(payload.get("strategy_name") or seat_cn)
        research = payload.get("research") or []
        regime = payload.get("regime")
        # 价格行为(price-action):pa 开关 + 可编辑方法论(几何始终算/回响应,pa 仅控制 prompt 注入)。
        pa = bool(payload.get("pa"))
        pa_method = str(payload.get("pa_method") or "")
        # ⑤ 决策频率:day=日线(既有行为,默认) / 30min=日内 30 分钟 PIT(date 带时分,5min 聚 30min)。
        freq = str(payload.get("freq") or "day").strip().lower()
        freq = "30min" if freq in ("30min", "30", "30m") else "day"
        # run 化:批跑分组标识,仅透传落盘(有值才落键 → 旧记录/手动研判形状不变)。
        run_id = str(payload.get("run_id") or "").strip()
        # ⑤ 研判模式:fast=deepseek-chat(几秒,无思维链) / deep=deepseek-reasoner(十几秒,有真思维链)。
        # 默认 deep(保持既有「真思维链」行为不回退);前端 toggle 显式传 mode。
        mode = str(payload.get("mode") or "deep").strip().lower()
        if mode not in ("fast", "deep"):
            mode = "deep"

        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = code.upper()
        except Exception:  # noqa: BLE001
            c = code.upper()
        if not c or not date:
            return JSONResponse({"ok": False, "reason": "缺 code 或 date"})

        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            unit = "日"

            if freq == "30min":
                # 日内 30 分钟:date 当带时分 datetime;拉 5min 按 PIT 砍掉 >决策时刻的 bar,
                # 每 6 根聚成 30min 序列再算因子(rev_20 等口径从「20日」变「20根30min bar」)。
                unit = "根30分钟bar"
                anchor_dt = _pd.Timestamp(date)
                if anchor_dt > _pd.Timestamp.now():
                    anchor_dt = _pd.Timestamp.now()
                end_day = str(anchor_dt.date())
                start_day = str((anchor_dt - _pd.Timedelta(days=40)).date())
                df5 = await asyncio.to_thread(loader.fetch_quote, c, start_day, end_day, "5min")
                df = _pd.DataFrame()
                asof = str(anchor_dt)[:16]
                if df5 is not None and len(df5) > 0:
                    df5 = df5[_pd.to_datetime(df5["trade_date"]) <= anchor_dt]   # PIT:只取 ≤决策时刻
                    df = _agg_5min_to_30min(df5)
                if df is not None and len(df) > 0:
                    td = df["trade_date"].iloc[-1]
                    asof = str(td)[:16]
            else:
                anchor = min(_pd.Timestamp(date), _pd.Timestamp.now())
                end = str(anchor.date())
                start = str((anchor - _pd.Timedelta(days=180)).date())
                df = await asyncio.to_thread(loader.fetch_quote, c, start, end, "day")  # end=date PIT,≤当日
                asof = end
                if df is not None and len(df) > 0:
                    td = df["trade_date"].iloc[-1] if "trade_date" in df.columns else None
                    asof = str(td)[:10] if td is not None else end

            fac: dict = {}
            if df is not None and len(df) > 0:
                try:
                    from financial_analyst.factors.core import compute_factors
                    v = compute_factors(df)
                    fac = {k: _num(v.get(k)) for k in
                           ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20")}
                except Exception:  # noqa: BLE001
                    fac = {}

            # 价量几何特征(确定性,§ price_action.py):始终算(便宜),随响应回前端供决策卡显示;
            # 仅 pa 开时注入 LLM prompt。df 已 PIT≤asof,故取最新根=决策 bar 不越界。
            pa_feat: dict = {}
            if df is not None and len(df) > 0:
                try:
                    from guanlan_v2.seats.price_action import compute_pa_features
                    pa_feat = compute_pa_features(df, c, name)
                except Exception:  # noqa: BLE001 — 几何失败不挡研判
                    pa_feat = {}

            # fm_backfill 查 parquet 分位仅日线有(W11 产物按 date 键),30min 保持空 dict。
            mdl: dict = {}
            if freq == "day":
                try:
                    from pathlib import Path as _Path
                    cache_p = _Path(__file__).resolve().parents[2] / "var" / "seats_fm_backfill.parquet"
                    if cache_p.exists():
                        fmdf = await asyncio.to_thread(_pd.read_parquet, cache_p)
                        if fmdf is not None and len(fmdf) > 0 and "date" in fmdf.columns:
                            hit = fmdf[(fmdf["date"].astype(str) == end)
                                       & (fmdf["code"].astype(str).str.upper() == c.upper())]
                            if len(hit) > 0:
                                r = hit.iloc[0].to_dict()
                                mdl = {"combo_pct": _num(r.get("combo_pct")), "fm_pct": _num(r.get("fm_pct"))}
                except Exception:  # noqa: BLE001
                    pass

            # P1:后端按日 PIT 浮出叙事卡(替代前端固定 research 透传)。无料诚实空(不退 demo)。
            # 用决策日 asof 作 PIT 锚(≤asof 的卡/研报才浮出);浮出结果覆盖入参 research。
            # I/O(扫 out/、读 archive)进 to_thread,不阻塞 async 事件循环。
            _narr_ids: list = []
            _surf = await asyncio.to_thread(_surface_for_decide, c, payload.get("industry") or "", asof)
            _narr_ids = [x.get("id") for x in _surf]
            research = [{"title": x.get("title"), "from": (x.get("source") or {}).get("from", ""),
                         "path": x.get("path")} for x in _surf]

            # 大盘市况(PIT):回测前端不再传 regime → 后端按大盘日产物(breadth)补。
            # 日线:决策在 D 收盘 → 用 D 的 EOD breadth;30min:盘中决策(如 10:30)→ 当日 EOD
            # breadth 那时**尚不存在**,喂它=看未来 → 显式回退到上一**交易**日 EOD
            # (`_prev_trading_day` 按引擎全量交易日历取,跳周末/假日,不靠 regime_asof 的 idx≤date 隐式滑动)。
            if not regime:
                try:
                    from guanlan_v2.seats.narrative import regime_asof
                    _bdf = _load_breadth_df()
                    _rg_date = (asof[:10] if freq == "day"
                                else _prev_trading_day(asof[:10]))
                    regime = await asyncio.to_thread(regime_asof, _rg_date, _bdf)
                except Exception as _e:  # noqa: BLE001 — 无产物/读盘失败留痕,仍诚实 None
                    _log.warning("regime_asof failed asof=%s freq=%s: %s", asof, freq, _e)
                    regime = None

            # —— 组装证据 + prompt（只喂真证据，prompt 明令 PIT、禁后见之明）——
            def _f(x, p=3):
                return (("%." + str(p) + "f") % x) if isinstance(x, (int, float)) else "—"

            from guanlan_v2.factorlib.semantics import render_factors
            fac_line = render_factors(
                fac, ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20"), unit=unit)
            if mdl.get("combo_pct") is not None or mdl.get("fm_pct") is not None:
                fac_line += f" | combo分位={_f(mdl.get('combo_pct'), 0)} FM分位={_f(mdl.get('fm_pct'), 0)}"

            card_line = "无"
            if cards:
                _cl = []
                for cd in cards[:3]:
                    if not cd:
                        continue
                    extra = " ".join(filter(None, [
                        cd.get("verdict") and ("验证" + str(cd.get("verdict"))),
                        (cd.get("conf") is not None) and ("conf" + str(cd.get("conf"))),
                        cd.get("ic") and ("IC" + str(cd.get("ic")))]))
                    _cl.append(f"{cd.get('name', '')}:{cd.get('insight', '')}" + (f"({extra})" if extra else ""))
                card_line = "\n".join(_cl) or "无"
            # 研报条目兼容两种形状:旧 list[str] / 新 list[{title,from,path}](P1⑤:带 path 才能喂正文)
            _res_items = []
            for x in research[:4]:
                if isinstance(x, dict):
                    _res_items.append({"title": str(x.get("title") or ""), "from": str(x.get("from") or ""),
                                       "path": x.get("path") or None})
                else:
                    _res_items.append({"title": str(x), "from": "", "path": None})
            res_line = " / ".join(
                (it["title"] + (f"({it['from']})" if it["from"] else "")) for it in _res_items if it["title"]
            ) or "无"
            # 研报正文摘录(out/ 深度研报才有 path):同 buddy._tool_report 口径抽「一、综合评级」「八、操作建议」,
            # 最多 2 篇、每篇 ≤700 字——研判真用上研报结论,而非只看一句标题(互通审计 P1⑤)。
            res_excerpt = ""
            _ex_n = 0
            try:
                import re as _rex
                from pathlib import Path as _P
                from financial_analyst.buddy.tools import _project_root as _proot
                _out_dir = (_proot() / "out").resolve()
                _ex = []
                for it in _res_items:
                    if not it["path"] or len(_ex) >= 2:
                        continue
                    _p = _P(str(it["path"])).resolve()
                    if _p.suffix.lower() != ".md" or not str(_p).startswith(str(_out_dir)) or not _p.exists():
                        continue   # 只认 out/ 下的 md(同 /report 端点的安全边界)
                    _body = _p.read_text(encoding="utf-8", errors="replace")
                    _parts = []
                    for _sect in (r"## 一、综合评级.*?(?=## 二)", r"## 八、操作建议.*?(?=---|\Z)"):
                        _m = _rex.search(_sect, _body, _rex.DOTALL)
                        if _m:
                            _parts.append(_m.group(0).strip())
                    _txt = ("\n".join(_parts) or _body[:700])[:700]
                    _ex.append(f"《{it['title']}》摘录:\n{_txt}")
                if _ex:
                    _ex_n = len(_ex)
                    res_excerpt = "【研报摘录·注意:研报观点截至其落款日,按 PIT 自行折价】\n" + "\n---\n".join(_ex) + "\n"
            except Exception:  # noqa: BLE001 — 摘录失败退回标题级,不挡研判
                res_excerpt = ""
                _ex_n = 0
            # 本席配方因子(用户在校场给该策略配的因子)—— P2:每因子按 catalog 反查 → 优先本票 tsic、
            # 退而截面 cs 的 vintage(as-of 决策日 asof)真 OOS IC,只喂 LLM 当参考视角,不进信号(加权混合 P3)。
            # 命中=真历史外样本 IC;未命中/样本不足=诚实「样本不足」(不再喂静态看未来 IC)。
            rf_line, _rf_vint = _rf_vintage_line(recipe_factors, c, asof, freq)

            # 价格行为两块(仅 pa 开):几何=确定性事实;方法论=推理框架(可编辑,空则默认模板)。
            pa_block_line = ""
            pa_method_line = ""
            if pa:
                from guanlan_v2.seats.price_action import render_pa_block, PA_METHOD_DEFAULT
                _pb = render_pa_block(pa_feat, unit)
                if _pb:
                    pa_block_line = f"【价量形态·确定性(PIT≤决策bar·{unit})】{_pb}\n"
                pa_method_line = ("【价格行为读法(本席方法论·推理框架·不替代证据·证据不足给观望)】"
                                  f"{pa_method or PA_METHOD_DEFAULT}\n")

            sys_p = (f"你是「观澜」量化交易系统中的{seat_cn}(信条:{creed})。"
                     f"基于**截至 {asof} 已发生的信息**(point-in-time,严禁使用该日之后任何信息或后见之明),"
                     f"判断此刻是否对 {name}({c}) 落子。只依据下方证据推理,不得编造数据;证据不足就给「观望」。")
            _json_fmt = ('JSON 格式:{"direction":"买入或卖出或观望","confidence":0到100整数,'
                         '"rationale":"≤140字结论理由","key_evidence":["最多3条支撑点"]}')
            if mode == "fast":
                _ask = (f"研判:此刻{seat_cn}是否落子?综合上面证据与本席信条权衡,"
                        f"**只输出一个 JSON 对象**(不要任何其他文字)。\n" + _json_fmt)
            else:
                _ask = (f"研判:此刻{seat_cn}是否落子?请**逐步分析**上面每条证据(扣住具体数值),"
                        f"再结合本席信条权衡,最后在**单独一行**给出 JSON 结论。\n" + _json_fmt)
            usr_p = (f"【标的】{name} {c} 截至 {asof}（{('30分钟K·日内' if freq=='30min' else '日线')}）\n"
                     f"【量化因子·PIT≤当日收盘】{fac_line}\n"
                     + pa_block_line +
                     f"【本席经验卡】{card_line}\n"
                     f"【相关研报/情绪】{res_line}\n"
                     + res_excerpt +
                     f"【本席配方因子·vintage OOS IC(as-of·真历史外样本)·供研判参考·不进信号】{rf_line}\n"
                     f"【市况】{regime or '—'}\n"
                     + pa_method_line + _ask)

            from financial_analyst.llm.client import LLMClient
            import json as _json
            import re as _re
            if mode == "fast":
                # 快:deepseek-chat + response_format=json_object,整段即 JSON 结论(无思维链,~秒级)。
                client = LLMClient.for_agent("watch-agent").with_overrides(
                    provider="deepseek", model="deepseek-chat")
                resp = await client.chat(
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    temperature=0.3, response_format={"type": "json_object"})
            else:
                # 深:deepseek-reasoner(R1)拿**真·思维链**(reasoning_content)。reasoner 不支持
                # response_format,故让它先逐步推理、末尾给一行 JSON,再 regex 抽取结论(~十几秒)。
                client = LLMClient.for_agent("watch-agent").with_overrides(
                    provider="deepseek", model="deepseek-reasoner")
                resp = await client.chat(
                    [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                    temperature=0.3)
            msg = (resp["choices"][0]["message"] if isinstance(resp, dict)
                   else resp.choices[0].message)
            reasoning = ((msg.get("reasoning_content") if isinstance(msg, dict)
                          else getattr(msg, "reasoning_content", None)) or "")
            content = ((msg.get("content") if isinstance(msg, dict)
                        else getattr(msg, "content", None)) or "")
            j = {}
            try:
                mt = _re.search(r"\{[\s\S]*\}", content)   # 抽 JSON 结论(深:末尾一行;快:整段即 JSON)
                if mt:
                    j = _json.loads(mt.group(0))
            except Exception:  # noqa: BLE001 — 抽不出 JSON 也别崩
                j = {}
            if not j:
                j = {"direction": "观望", "confidence": None,
                     "rationale": (content or reasoning)[:200], "key_evidence": []}
            # 断言质检(修复#2):方向矛盾+无出处百分数 → advisory flags,不阻断
            audit_flags: list = []
            try:
                from guanlan_v2.factorlib.claim_audit import audit_claims
                _claims = " ".join([str(j.get("rationale") or "")]
                                   + [str(x) for x in (j.get("key_evidence") or [])])
                _audit_src = "\n".join([fac_line, card_line, res_line, res_excerpt or "",
                                        rf_line, str(creed or ""), str(regime or "")])
                audit_flags = audit_claims(_claims, fac, _audit_src)
            except Exception:  # noqa: BLE001 — 质检失败不挡研判
                audit_flags = []
            # P3 加权混合:llm_score(LLM 方向×置信)+ factor_score(配方因子 dir·z clip 等权)→ hybrid。
            # w=0 / factor_score=None → hybrid_direction 透传 LLM 方向(纯 LLM,不经死区)。
            _llm_s = _llm_score(j.get("direction"), j.get("confidence"))
            _factor_s = _combine_factor_score(_rf_vint)
            _hyb_dir, _hyb_bias = _hybrid_direction(j.get("direction"), _llm_s, _factor_s, w)
            # 落盘(仅成功路径 → LLM 失败走 except 不到这里 = 不落盘);recipe_factors 仅记录,不参与计算。
            _persist_decision("decide", {
                "code": c, "name": name, "strategy_id": strategy_id, "strategy_name": strategy_name,
                "mode": mode, "freq": freq, "direction": j.get("direction"), "confidence": j.get("confidence"),
                "rationale": j.get("rationale"), "key_evidence": (j.get("key_evidence") or []),
                "reasoning": reasoning, "model_name": f"{client.provider}/{client.model}", "asof": asof,
                "factors_std": fac, "recipe_factors": recipe_factors,
                "recipe_factors_vintage": _rf_vint,   # P2:配方因子 vintage(as-of)OOS IC 记录,供 RunDecCard/审计
                "card_names": [cd.get("name") for cd in cards if cd and cd.get("name")],
                "research": [it["title"] for it in _res_items if it["title"]],
                "research_excerpt_n": _ex_n,   # 喂进 prompt 的研报正文篇数(0=只有标题级)
                "narratives_surfaced": _narr_ids,   # P1:后端按日 PIT 浮出的叙事卡 id(逐日不同)
                "regime_asof_used": bool(regime),   # P1:本笔是否用上大盘日产物补的 regime
                "regime_asof": regime,              # P1:当日大盘点评文本(PIT 日产物 / 实盘今日快照),供 RunDecCard 显形
                "audit_flags": audit_flags,
                "w": w, "llm_score": _llm_s, "factor_score": _factor_s,   # P3:加权混合输入/分量
                "hybrid_bias": _hyb_bias, "hybrid_direction": _hyb_dir,   # P3:混合偏置 + 最终方向(w=0 透传 LLM)
                "creed": creed,
                "pa": pa, "pa_features": pa_feat,   # 价格行为:开关 + 确定性几何特征
                **({"run_id": run_id} if run_id else {}),   # 有 run 才落键,绝不落空键
            })
            return JSONResponse({
                "ok": True, "code": c, "name": name, "asof": asof, "seat": seat_cn,
                "mode": mode, "freq": freq,
                "audit_flags": audit_flags,
                "model_name": f"{client.provider}/{client.model}",
                "direction": j.get("direction"), "confidence": j.get("confidence"),
                "rationale": j.get("rationale"), "key_evidence": (j.get("key_evidence") or []),
                "reasoning": reasoning,   # 真·思维链(仅深模式 reasoner 有;快模式为空串)
                "factors": fac, "model": mdl, "recipe_factors": recipe_factors,
                "w": w, "llm_score": _llm_s, "factor_score": _factor_s,   # P3:加权混合输入/分量
                "hybrid_bias": _hyb_bias, "hybrid_direction": _hyb_dir,   # P3:混合偏置 + 最终方向
                "pa_features": pa_feat,   # 几何常显:无论 pa 开关都回前端供决策卡显示
            })
        except Exception as exc:  # noqa: BLE001 — LLM/取数失败诚实降级,不 500
            return JSONResponse({"ok": False, "code": c, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/quote")
    async def seats_quote(code: str):
        """④ 实盘盘口实时价 —— 引擎 ``/quotes`` 同源(TencentQuoteCollector,腾讯实时),
        归一成盯盘面板需要的形状,并标注 ``fresh``(相对最后一根日 K 是否为更新的盘中报价)。

        · 盘中 → 报价日 > 最后日 K 日 → ``fresh=True``,前端标「实时盘中」并随轮询跳动;
        · 盘后/休市 → 腾讯回最后收盘快照(报价日 == 最后日 K 日)→ ``fresh=False``,标「最新收盘」。
        坏 code / 网络失败 → ``ok:False`` + reason(恒 HTTP200,前端降级回放历史,不抛 500)。
        返回 ``{ok, code, name, price, prevClose, open, high, low, change, changePercent,
        volume, amount, turnover_rate, vol_ratio, pe, pb, asof, asofDate, lastBarDate, lastClose, fresh}``。
        """
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "reason": "code 为空"})

        try:
            from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
            data = await asyncio.to_thread(TencentQuoteCollector().fetch, [c])
            q = (data or {}).get(c) or next(iter((data or {}).values()), None)
            if not q:
                return JSONResponse({"ok": False, "code": c, "reason": "无实时报价(腾讯未返回)"})

            # 盘口时间戳 'YYYYMMDDHHMMSS' → 'YYYY-MM-DD HH:MM'(+ asofDate 供 fresh 判定)
            asof_raw = str(q.get("asof") or "")
            asof = asof_date = None
            if len(asof_raw) >= 12 and asof_raw.isdigit():
                asof_date = f"{asof_raw[0:4]}-{asof_raw[4:6]}-{asof_raw[6:8]}"
                asof = f"{asof_date} {asof_raw[8:10]}:{asof_raw[10:12]}"

            # 最后一根真日 K(date+close):判定报价是否为更新的盘中价(fresh)
            last_bar_date = last_close = None
            try:
                import pandas as _pd
                from financial_analyst.data import loader_factory as _lf
                s = str((_pd.Timestamp.now() - _pd.Timedelta(days=20)).date())
                loader = _lf.get_default_loader()
                df = await asyncio.to_thread(loader.fetch_quote, c, s, "2100-01-01", "day")
                df = _drop_unsettled(df)    # 丢未结算占位行:last_bar_date 取最后已结算 bar,fresh 才准
                if df is not None and len(df) > 0:
                    last = df.iloc[-1].to_dict()
                    td = last.get("trade_date")
                    last_bar_date = str(td)[:10] if td is not None else None
                    last_close = _num(last.get("close"))
            except Exception:  # noqa: BLE001 — 最后日 K 取不到不致命,fresh 退化为 None
                pass

            fresh = (asof_date > last_bar_date) if (asof_date and last_bar_date) else None
            return JSONResponse({
                "ok": True, "code": c, "name": q.get("name"),
                "price": _num(q.get("price")), "prevClose": _num(q.get("prevClose")),
                "open": _num(q.get("open")), "high": _num(q.get("high")), "low": _num(q.get("low")),
                "change": _num(q.get("change")), "changePercent": _num(q.get("changePercent")),
                "volume": _num(q.get("volume")), "amount": _num(q.get("amount")),
                "turnover_rate": _num(q.get("turnover_rate")), "vol_ratio": _num(q.get("vol_ratio")),
                "pe": _num(q.get("pe")), "pb": _num(q.get("pb")),
                "asof": asof, "asofDate": asof_date,
                "lastBarDate": last_bar_date, "lastClose": last_close, "fresh": fresh,
            })
        except Exception as exc:  # noqa: BLE001 — 取数/网络失败诚实降级
            return JSONResponse({"ok": False, "code": c, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/live_eval")
    async def seats_live_eval(code: str, tf: str = "day"):
        """条件单·触发上下文(阶段1 地基):实时价(引擎 /quotes 同源 TencentQuoteCollector)
        + 真技术指标(compute_factors,≤最新真 K)+ MA5/MA20 绝对值,**一处算、live 与回测同一套**
        触发引擎评估「到价 / 放量 / 指标」条件。坏码 / 取数失败 → ok:False(恒 HTTP200,前端降级)。

        返回 ``{ok,code,name,asof,asofDate,fresh, price,prevClose,changePercent,open,high,low,
        volRatio,turnoverRate, ma5,ma20,rsi14,maDiff20,rev20,mom60,turnover20, lastClose,lastBarDate}``。
        指标语义:maDiff20=收盘/MA20-1(>0 站上 MA20);rsi14∈[0,100];
        turnover20=20日量比(当日量/20日均量,字段名沿袭叫 turnover 但口径是量比;
        与 volRatio(腾讯实时,10日窗)是两个不同窗口的量比)。
        """
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "reason": "code 为空"})

        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf

            # —— 实时报价(同 /seats/quote 源)——
            from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
            qd = await asyncio.to_thread(TencentQuoteCollector().fetch, [c])
            q = (qd or {}).get(c) or next(iter((qd or {}).values()), None) or {}
            asof_raw = str(q.get("asof") or "")
            asof = asof_date = None
            if len(asof_raw) >= 12 and asof_raw.isdigit():
                asof_date = f"{asof_raw[0:4]}-{asof_raw[4:6]}-{asof_raw[6:8]}"
                asof = f"{asof_date} {asof_raw[8:10]}:{asof_raw[10:12]}"

            # —— 真 K(≤今,PIT)→ compute_factors 指标 + MA5/MA20 绝对值 ——
            fac: dict = {}
            ma5 = ma20 = last_close = None
            last_bar_date = None
            start = str((_pd.Timestamp.now() - _pd.Timedelta(days=200)).date())
            loader = _lf.get_default_loader()
            df = await asyncio.to_thread(loader.fetch_quote, c, start, "2100-01-01", "day")
            df = _drop_unsettled(df)        # 丢未结算占位行:last_bar_date/fresh/MA 只算已结算 bar
            if df is not None and len(df) > 0:
                td = df["trade_date"].iloc[-1] if "trade_date" in df.columns else None
                last_bar_date = str(td)[:10] if td is not None else None
                closes = df["close"].astype(float)
                last_close = _num(closes.iloc[-1])
                if len(closes) >= 5:
                    ma5 = _num(round(float(closes.tail(5).mean()), 3))
                if len(closes) >= 20:
                    ma20 = _num(round(float(closes.tail(20).mean()), 3))
                try:
                    from financial_analyst.factors.core import compute_factors
                    v = compute_factors(df)
                    fac = {k: _num(v.get(k)) for k in
                           ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20")}
                except Exception:  # noqa: BLE001
                    fac = {}

            fresh = (asof_date > last_bar_date) if (asof_date and last_bar_date) else None
            if str(tf).lower() in ("5min", "5", "5m"):
                cx = await asyncio.to_thread(_ctx5_sync, c, q)
                return JSONResponse({
                    "ok": True, "code": c, "name": q.get("name"), "tf": "5min",
                    "asof": asof, "asofDate": asof_date, "fresh": fresh, "lastBarDate": last_bar_date,
                    "changePercent": _num(q.get("changePercent")), "prevClose": _num(q.get("prevClose")),
                    "price": cx.get("price"), "open": cx.get("open"), "high": cx.get("high"), "low": cx.get("low"),
                    "ma5": cx.get("ma5"), "ma20": cx.get("ma20"), "maDiff20": cx.get("maDiff20"),
                    "rsi14": cx.get("rsi14"), "volRatio": cx.get("volRatio"),
                    "hi20": cx.get("hi20"), "lo20": cx.get("lo20"), "n5": cx.get("n5"),
                })
            return JSONResponse({
                "ok": True, "code": c, "name": q.get("name"), "tf": "day",
                "asof": asof, "asofDate": asof_date, "fresh": fresh,
                "price": _num(q.get("price")), "prevClose": _num(q.get("prevClose")),
                "changePercent": _num(q.get("changePercent")),
                "open": _num(q.get("open")), "high": _num(q.get("high")), "low": _num(q.get("low")),
                "volRatio": _num(q.get("vol_ratio")), "turnoverRate": _num(q.get("turnover_rate")),
                "ma5": ma5, "ma20": ma20,
                "rsi14": fac.get("rsi_14"), "maDiff20": fac.get("ma_diff_20"),
                "rev20": fac.get("rev_20"), "mom60": fac.get("mom_60"), "turnover20": fac.get("turnover_20"),
                "lastClose": last_close, "lastBarDate": last_bar_date,
            })
        except Exception as exc:  # noqa: BLE001 — 取数/网络失败诚实降级
            return JSONResponse({"ok": False, "code": c, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/order")
    async def seats_order(code: str, seat: str = "momentum", tf: str = "day",
                          hold_entry: Optional[float] = Query(None),
                          hold_since: Optional[str] = Query(None),
                          hold_days: Optional[int] = Query(None),
                          creed: Optional[str] = Query(None),
                          note: Optional[str] = Query(None),
                          strategy_id: Optional[str] = Query(None),
                          strategy_name: Optional[str] = Query(None),
                          date: Optional[str] = Query(None)):
        """⑤+ agent 生成「条件单」(阶段2b):基于真实时上下文(现价+MA5/20+RSI+量比+近20日高低),
        LLM(deepseek-chat,快)按席位信条设计一张「到价/放量/指标」触发的条件单,直接喂盯盘触发引擎。
        triggers.kind 限引擎支持的 price/volRatio/maDiff20/rsi14;op 限 >= <= > <(服务端校验清洗)。
        坏码 / 取数 / LLM 失败 → ok:False(恒 HTTP200,前端降级)。
        """
        _CREEDS = {
            "reversal": ("反转席", "超跌缩量企稳即落子,搏短线反弹"),
            "momentum": ("动量席", "突破均线、量价齐升则顺势加仓"),
            "event": ("事件驱动席", "业绩超预期后博 60 日漂移"),
            "risk": ("风控席", "高位放量滞涨即减仓止盈,守住回撤"),
        }
        seat = seat if seat in _CREEDS else "momentum"
        seat_cn, creed_default = _CREEDS[seat]
        creed = (creed or "").strip() or creed_default   # 策略实例传入的信条优先,否则回退模板信条
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "reason": "code 为空"})

        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

            qd = await asyncio.to_thread(TencentQuoteCollector().fetch, [c])
            q = (qd or {}).get(c) or next(iter((qd or {}).values()), None) or {}
            name = q.get("name") or c
            asof_raw = str(q.get("asof") or "")
            asof = (f"{asof_raw[0:4]}-{asof_raw[4:6]}-{asof_raw[6:8]} {asof_raw[8:10]}:{asof_raw[10:12]}"
                    if len(asof_raw) >= 12 and asof_raw.isdigit() else None)
            pit = bool(date and str(date).strip())   # 给 date = 复盘 PIT(按该历史日思考,不串今天实时行情)
            if pit:
                asof = str(date)[:10]                 # 先置请求日,下方按实际取到的末根真 bar 日修正

            ctx: dict = {}
            tf = "5min" if str(tf).lower() in ("5min", "5", "5m") else "day"
            if tf == "5min":
                # 5min 交易单:用实时 5min 算指标(同前端口径),供日内短线设计
                ctx = await asyncio.to_thread(_ctx5_sync, c, q)
            else:
                # 复盘 PIT:窗口收到 date 当日(含)为止,只用 ≤date 的真历史 bar;实盘:取到最新
                _end = str(_pd.Timestamp(date).date()) if pit else "2100-01-01"
                _anchor = _pd.Timestamp(_end) if pit else _pd.Timestamp.now()
                start = str((_anchor - _pd.Timedelta(days=200)).date())
                loader = _lf.get_default_loader()
                df = await asyncio.to_thread(loader.fetch_quote, c, start, _end, "day")
                df = _drop_unsettled(df)    # 丢未结算占位行:MA/closes 只算已结算 bar
                if df is not None and len(df) > 0:
                    closes = df["close"].astype(float)
                    highs = df["high"].astype(float)
                    lows = df["low"].astype(float)
                    ma5 = float(closes.tail(5).mean()) if len(closes) >= 5 else None
                    ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
                    if pit:
                        # PIT:价/开高低/量比/asof 全取末根历史 bar(≤date),绝不碰实时 q(那是今天的)
                        _last = df.iloc[-1].to_dict()
                        price = _num(_last.get("close"))
                        _o, _h, _l = _num(_last.get("open")), _num(_last.get("high")), _num(_last.get("low"))
                        _td = _last.get("trade_date")
                        if _td is not None:
                            asof = str(_td)[:10]       # 实际末根真 bar 日(可能 < 请求 date)
                        _vr = None
                        if "vol" in df.columns and len(df) >= 11:
                            _vols = df["vol"].astype(float)
                            _vm = float(_vols.tail(11).head(10).mean())   # 截至前一日的 10 日均量
                            _vr = round(float(_vols.iloc[-1]) / _vm, 3) if _vm else None
                    else:
                        price = _num(q.get("price")) or _num(closes.iloc[-1])
                        _o, _h, _l = _num(q.get("open")), _num(q.get("high")), _num(q.get("low"))
                        _vr = _num(q.get("vol_ratio"))
                    ctx = {
                        "price": price,
                        "open": _o, "high": _h, "low": _l,
                        "ma5": (_num(round(ma5, 3)) if ma5 else None),
                        "ma20": (_num(round(ma20, 3)) if ma20 else None),
                        "maDiff20": (_num(round(price / ma20 - 1, 4)) if (ma20 and price) else None),
                        "volRatio": _vr,
                        "hi20": _num(round(float(highs.tail(20).max()), 2)),
                        "lo20": _num(round(float(lows.tail(20).min()), 2)),
                    }
                    try:
                        from financial_analyst.factors.core import compute_factors
                        v = compute_factors(df)
                        ctx["rsi14"] = _num(v.get("rsi_14"))
                    except Exception:  # noqa: BLE001
                        ctx["rsi14"] = None
            if not ctx.get("price"):
                return JSONResponse({"ok": False, "code": c, "reason": "无现价 / 真 K"})

            price = ctx.get("price")
            held = hold_entry is not None and hold_entry > 0
            pnl_pct = ((price / hold_entry - 1.0) * 100.0) if (held and price) else None

            def _f(x, p=2):
                return (("%." + str(p) + "f") % x) if isinstance(x, (int, float)) else "—"
            tf_cn = "5 分钟 K(日内短线)" if tf == "5min" else "日线(波段)"
            ind_cn = "(MA/RSI/量比 均为 5min 周期)" if tf == "5min" else ""
            _data_cn = "已发生数据(point-in-time · 严禁使用该日之后信息或后见之明)" if pit else "真实时数据"
            sys_p = (f"你是「观澜」量化系统的{seat_cn}(信条:{creed})。基于截至 {asof or '最新'} 的**{tf_cn}{_data_cn}**"
                     f"(只用已发生信息、不编造),为 {name}({c}) 设计一张**{('日内 5min ' if tf == '5min' else '')}条件单**:方向 + 触发条件"
                     f"(到价 / 放量 / 站上均线等),供用户盯盘到点手动下单。"
                     + ("**5min 单触发价请贴近日内波动(参考近端高低与现价),止盈止损更紧。**" if tf == "5min" else ""))
            if note and str(note).strip():   # 策略实例配方首卡一句洞见,作本席经验喂进设计上下文
                sys_p += f" 本席经验参考:{str(note).strip()[:80]}。"
            _ctx_line = (f"现价 {_f(ctx.get('price'))} 今开 {_f(ctx.get('open'))} 高 {_f(ctx.get('high'))} 低 {_f(ctx.get('low'))}\n"
                         f"MA5 {_f(ctx.get('ma5'))} MA20 {_f(ctx.get('ma20'))} 乖离MA20 {_f(ctx.get('maDiff20'), 4)} "
                         f"RSI14 {_f(ctx.get('rsi14'), 1)} 实时量比(10日窗,>1放量) {_f(ctx.get('volRatio'))} {ind_cn}\n"
                         f"近端 高 {_f(ctx.get('hi20'))} 低 {_f(ctx.get('lo20'))}\n")
            _json_schema = ('{"side":"买入或卖出或观望","triggers":[{"kind":"price|volRatio|maDiff20|rsi14","op":">=|<=|>|<","value":数字}],'
                            '"logic":"AND或OR","stop":数字或null,"take":数字或null,"note":"≤60字理由","validity":"今日有效或3日有效"}')
            if held:
                _pnl_str = f"{pnl_pct:.2f}%" if pnl_pct is not None else "—"
                usr_p = (_ctx_line
                         + f"【持仓】进场价 {_f(hold_entry)} · 持有约 {hold_days if hold_days is not None else '—'} 日 · 当前价 {_f(price)} · 浮动盈亏 {_pnl_str}\n"
                         + "你已持有该股。请结合当前量价/指标判断该【继续持有】还是【了结卖出】。\n"
                         + "继续持有 → side 填\"观望\";了结卖出 → side 填\"卖出\"并在 note 给理由。\n"
                         + "仍按原 JSON 结构**只输出一个 JSON 对象**:\n"
                         + _json_schema + "\n"
                         + "卖出时 triggers 可为空列表或给一个保护性触发;price 用绝对价位。")
            else:
                usr_p = (_ctx_line
                         + "按本席信条设计条件单,**只输出一个 JSON 对象**:\n"
                         + _json_schema + "\n"
                         + "要求:price 用绝对价位(参考现价与近20日高低);kind 仅限 price/volRatio/maDiff20/rsi14;触发价合理可达。")

            from financial_analyst.llm.client import LLMClient
            import json as _json
            client = LLMClient.for_agent("watch-agent").with_overrides(
                provider="deepseek", model="deepseek-chat")
            resp = await client.chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
                temperature=0.3, response_format={"type": "json_object"})
            msg = (resp["choices"][0]["message"] if isinstance(resp, dict)
                   else resp.choices[0].message)
            content = ((msg.get("content") if isinstance(msg, dict)
                        else getattr(msg, "content", None)) or "")
            order = {}
            try:
                order = _json.loads(content)
            except Exception:  # noqa: BLE001
                order = {}
            # 校验 / 清洗 triggers(只留引擎认得的 kind/op + 数值 value)
            _KINDS = {"price", "volRatio", "maDiff20", "rsi14"}
            _OPS = {">=", "<=", ">", "<"}
            clean = []
            for t in (order.get("triggers") or []):
                if isinstance(t, dict) and t.get("kind") in _KINDS and t.get("op") in _OPS:
                    try:
                        clean.append({"kind": t["kind"], "op": t["op"], "value": float(t["value"])})
                    except (TypeError, ValueError):
                        pass
            order["triggers"] = clean
            order["tf"] = tf
            # 落盘(仅成功路径 → LLM/取数失败走 except 不到这里 = 不落盘)。
            _persist_decision("order", {
                "code": c, "name": name, "strategy_id": str(strategy_id or ""),
                "strategy_name": str(strategy_name or seat_cn), "tf": tf,
                "side": order.get("side"), "triggers": order.get("triggers") or [],
                "logic": order.get("logic"), "stop": order.get("stop"), "take": order.get("take"),
                "note": order.get("note"), "validity": order.get("validity"),
                "model_name": f"{client.provider}/{client.model}", "asof": asof, "pit": pit,
                "creed": creed,
            })
            return JSONResponse({
                "ok": True, "code": c, "name": name, "seat": seat, "seat_cn": seat_cn, "tf": tf,
                "asof": asof, "model_name": f"{client.provider}/{client.model}",
                "ctx": ctx, "order": order, "pit": pit,
                "held": held, "pnlPct": pnl_pct,
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "code": c, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/bars_live")
    async def seats_bars_live(code: str, freq: str = "1min", n: int = 480):
        """盘中实时 K(引擎 pytdx ``WatchFeed``):``freq=1min``(bars1) / ``5min``(bars5)。
        「实时加 1min」盯盘用:1min 历史库个股为空,故 1min 只走实时口。坏码/网络失败 →
        ok:False + 空 bars(恒 HTTP200,前端降级回退合成/历史)。
        返回 ``{ok, code, freq, n, bars:[{date,open,high,low,close,vol}]}``(date='YYYY-MM-DD HH:MM',vol 手)。
        """
        try:
            from financial_analyst.buddy.tools import normalize_code
            try:
                c = normalize_code(code)
            except Exception:  # noqa: BLE001
                c = (code or "").strip().upper()
        except Exception:  # noqa: BLE001
            c = (code or "").strip().upper()
        if not c:
            return JSONResponse({"ok": False, "code": code, "freq": freq, "bars": [], "reason": "code 为空"})
        freq = "1min" if str(freq or "").lower() in ("1min", "1", "min1", "1m") else "5min"
        n = max(1, min(int(n or 480), 800))
        feed = None
        try:
            from financial_analyst.watch.feed import WatchFeed
            feed = WatchFeed()
            fn = feed.bars1 if freq == "1min" else feed.bars5
            df = await asyncio.to_thread(fn, c, n)
            bars = []
            if df is not None and len(df) > 0:
                for rec in df.to_dict("records"):
                    td = rec.get("trade_date")
                    bars.append({
                        "date": (str(td)[:16] if td is not None else None),
                        "open": _num(rec.get("open")), "high": _num(rec.get("high")),
                        "low": _num(rec.get("low")), "close": _num(rec.get("close")),
                        "vol": _num(rec.get("vol")),
                    })
            return JSONResponse({"ok": True, "code": c, "freq": freq, "n": len(bars), "bars": bars})
        except Exception as exc:  # noqa: BLE001 — 实时拉取失败诚实降级
            return JSONResponse({"ok": False, "code": c, "freq": freq, "bars": [],
                                 "reason": f"{type(exc).__name__}: {exc}"})
        finally:
            if feed is not None:
                try:
                    feed.close()
                except Exception:  # noqa: BLE001
                    pass

    @router.get("/basket_perf")
    async def seats_basket_perf(codes: str = "", start: str = "", horizon: int = 5):
        """篮子前向持有收益 vs 全A等权基准(P1 §2;口径=收盘进→N根收盘出,同置信校准,
        note 随响应下发)。codes 逗号分隔 ≤40(超截断并注明);失败恒 HTTP200 ok:false。"""
        try:
            raw = [c.strip() for c in (codes or "").split(",") if c.strip()]
            if not raw or not (start or "").strip():
                return JSONResponse({"ok": False, "reason": "codes 与 start 必填"})
            truncated = len(raw) > 40
            raw = raw[:40]
            try:
                from financial_analyst.buddy.tools import normalize_code as _norm
            except Exception:  # noqa: BLE001 — 引擎不可导入时裸用 code
                _norm = None
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            end = str(_pd.Timestamp.now().date())

            def _closes(c: str):
                df = loader.fetch_quote(c, str(start), end, "day")
                df = _drop_unsettled(df)               # 当日未结算占位行不当收盘
                if df is None or len(df) == 0 or "close" not in df.columns:
                    return []
                dcol = "trade_date" if "trade_date" in df.columns else df.columns[0]
                return [(str(d)[:10], float(v)) for d, v in zip(df[dcol], df["close"])
                        if v == v]

            closes_by_code: dict = {}
            for c in raw:
                cc = c
                if _norm is not None:
                    try:
                        cc = _norm(c)
                    except Exception:  # noqa: BLE001
                        cc = (c or "").strip().upper()
                try:
                    closes_by_code[cc] = await asyncio.to_thread(_closes, cc)
                except Exception:  # noqa: BLE001 — 单票取数失败=空序列 → 纯函数记 warning 剔除
                    closes_by_code[cc] = []

            from guanlan_v2.seats.basket_perf import compute_basket_perf
            from guanlan_v2.strategy.compute import eqw_market as _eqw
            bench_df = _eqw.load_eqw_ret()
            out = compute_basket_perf(closes_by_code, start=str(start), horizon=horizon,
                                      bench_df=bench_df)
            if truncated:
                out.setdefault("warnings", []).append("codes>40 已截断")
            if bench_df is None:
                out.setdefault("warnings", []).append("全A等权基准产物缺失(跑 ww_regen 生成)")
            return JSONResponse(out)
        except Exception as exc:  # noqa: BLE001 — 诚实降级
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/benchmark")
    async def seats_benchmark(start: Optional[str] = None, end: Optional[str] = None,
                              n: int = 250):
        """真·沪深300 日收盘(与 workflow 绩效同源 etf_index.parquet 399300.SZ),供
        盯盘/舰队净值对标(替代前端 mulberry32 合成指数)。只作展示对标,**不入影子
        台账/合议计算**。失败 ok:False(HTTP 200),前端隐藏基准线诚实降级。
        返回 ``{ok, code:"csi300", bars:[{date:"YYYY-MM-DD", close:float}]}`` 升序。"""
        try:
            rows = await asyncio.to_thread(
                _load_csi300, start=start, end=end, n=max(10, min(int(n or 250), 1200)))
            return JSONResponse({"ok": True, "code": "csi300", "bars": rows})
        except Exception as e:  # noqa: BLE001 —— 诚实降级,不让基准把整页打挂
            return JSONResponse({"ok": False, "error": str(e)[:200]})

    return router
