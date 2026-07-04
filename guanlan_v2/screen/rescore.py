# -*- coding: utf-8 -*-
"""P5 选股池再打分:产业链分(board 读数)+ 新闻情绪分(LLM 整批+当日缓存)+ 展示型综合分。

展示型红线:产物只落档案与展示,绝不回写 v4/picks/blend/seats 任何信号通路。
诚实合约:board 不可用→run 整体失败(绝不伪装成"全池不在链");逐票链外/无新闻/LLM失败→None;
成本显形(llm_calls/cache_hits)。LLM 经 asyncio.run(news_sentiment) 在 daemon 线程内跑
(仓内生产已验模式:console/tools.py:_run_news_sentiment;screen 侧 LLM 不绑事件循环)。
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RUNS_PATH = Path(__file__).resolve().parents[2] / "var" / "rescore_runs.jsonl"
NEWS_CACHE_PATH = Path(__file__).resolve().parents[2] / "var" / "rescore_news_cache.jsonl"

_TAG_SCORE = {"利好": 1.0, "中性": 0.0, "利空": -1.0}   # news_pulse.NEWS_SYSTEM tag 枚举
_RESEARCH_NORM = 3.0   # tanh 归一常数:≈3 份当周强看多研报饱和(board research.score 衰减求和量级);
                       # 展示层启发式参数,真机观察失真可调,不碰任何信号红线


class RescoreError(Exception):
    """run 级诚实失败(board 坏/v4 榜不可用)——绝不降级成逐票 None 冒充。"""


def new_run_id() -> str:
    return "rs_" + uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 桥(独立小函数便于 monkeypatch,仓例 research/loop.py)────────────────

def _load_board() -> Dict[str, Any]:
    from guanlan_v2.industry.aggregate import build_board
    return build_board()


def _load_framework_segments() -> List[dict]:
    from guanlan_v2.industry.framework import load_framework
    return list(load_framework().get("segments") or [])


def _v4_ranking_path():
    from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
    return V4_RANKING_PARQUET


def _call_news(codes: List[str]) -> Dict[str, Any]:
    """daemon 线程内同步跑异步 news_sentiment(整批一次 LLM;仓内已验模式)。"""
    import asyncio
    from guanlan_v2.screen.news import news_sentiment
    return asyncio.run(news_sentiment(codes, limit=200))


# ── 产业链分(纯函数,零 LLM)─────────────────────────────────────────────

def industry_scores(codes: List[str]) -> Tuple[Dict[str, Optional[dict]], Dict[str, Any]]:
    """逐票产业链分:一票多环取 chain 最强环;链外/环无信号 → None。
    board ok:false → raise RescoreError(整体诚实失败,绝不伪装"全池不在链")。"""
    board = _load_board()
    if not board.get("ok"):
        raise RescoreError(f"产业链板不可用: {board.get('reason')}")
    segs = {s["id"]: s for s in (board.get("segments") or []) if isinstance(s, dict)}
    smap: Dict[str, List[str]] = {}
    for s in _load_framework_segments():
        for x in (s.get("stocks") or []):
            c = str(x.get("code") or "").strip()
            if c:
                smap.setdefault(c, []).append(s.get("id"))
    out: Dict[str, Optional[dict]] = {}
    for c in codes:
        best = None
        for sid in smap.get(str(c), []):
            s = segs.get(sid)
            if not s or s.get("adjacent"):
                continue
            research = (s.get("research") or {}).get("score")
            therm = s.get("therm")
            r_n = math.tanh(float(research) / _RESEARCH_NORM) \
                if isinstance(research, (int, float)) else None
            t_n = (float(therm) / 50.0 - 1.0) if isinstance(therm, (int, float)) else None
            parts = [v for v in (r_n, t_n) if v is not None]
            if not parts:
                continue                         # 环无任何信号 → 不参与最强环竞选
            cand = {"seg": sid, "seg_name": s.get("display_name") or s.get("name"),
                    "chain": round(sum(parts) / len(parts), 4),
                    "research": (round(float(research), 3)
                                 if isinstance(research, (int, float)) else None),
                    "therm": (round(float(therm), 1)
                              if isinstance(therm, (int, float)) else None),
                    "quadrant": s.get("quadrant")}
            if best is None or cand["chain"] > best["chain"]:
                best = cand
        out[str(c)] = best                       # 不在链上 → None(诚实,合法常态)
    return out, dict(board.get("freshness") or {})


# ── 情绪分(LLM 整批 + 当日缓存)──────────────────────────────────────────

def _load_news_cache(day: str) -> Dict[str, Optional[dict]]:
    out: Dict[str, Optional[dict]] = {}
    try:
        with open(NEWS_CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("date") == day and r.get("code"):
                    out[str(r["code"])] = r.get("news")   # None=当日判过无新闻,也算缓存
    except FileNotFoundError:
        pass
    return out


def _append_news_cache(day: str, rows: Dict[str, Optional[dict]]) -> None:
    try:
        NEWS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(NEWS_CACHE_PATH, "a", encoding="utf-8") as f:
            for c, v in rows.items():
                f.write(json.dumps({"date": day, "code": c, "news": v},
                                   ensure_ascii=False) + "\n")
    except OSError:
        pass    # 缓存写失败不挡 run(代价=下次重调)


def news_scores(codes: List[str], top_n: int = 50
                ) -> Tuple[Dict[str, Optional[dict]], Dict[str, Any]]:
    """前 top_n 只:先查当日缓存,余下整批一次 LLM;无新闻/没判 → None(不编造)。
    stats: {llm_calls, cache_hits, as_of, market_read, market_tilt[, news_fail]}。"""
    day = date.today().isoformat()
    pool = [str(c) for c in codes][: int(top_n)]
    cache = _load_news_cache(day)
    hit = {c: cache[c] for c in pool if c in cache}
    todo = [c for c in pool if c not in cache]
    stats: Dict[str, Any] = {"llm_calls": 0, "cache_hits": len(hit), "as_of": None,
                             "market_read": None, "market_tilt": None}
    fresh: Dict[str, Optional[dict]] = {}
    if todo:
        try:
            r = _call_news(todo)
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            stats.update(llm_calls=1, as_of=r.get("as_of"),
                         market_read=r.get("market_read"), market_tilt=r.get("market_tilt"))
            sent = r.get("sentiment") or {}
            for c in todo:
                v = sent.get(c)
                if isinstance(v, dict) and v.get("tag") in _TAG_SCORE:
                    fresh[c] = {"tag": v["tag"], "read": v.get("read"),
                                "score": _TAG_SCORE[v["tag"]]}
                else:
                    fresh[c] = None              # 无相关新闻/LLM 没判 → None(不编造)
            _append_news_cache(day, fresh)       # 成功批次才缓存(含 None=当日判过)
        else:
            for c in todo:
                fresh[c] = None                  # 失败:本次 None,不写缓存(可重试)
            stats["news_fail"] = str(r.get("reason") or "")[:120]
    out = dict(hit)
    out.update(fresh)
    return {c: out.get(c) for c in pool}, stats


# ── 综合分(展示型)──────────────────────────────────────────────────────

def composite_score(v4_pct: Optional[float], chain: Optional[float],
                    news_score: Optional[float]) -> Dict[str, Any]:
    """mean(有值成分),各成分先归一 [-1,1];缺成分不补零;全缺 → score None(parts 显形)。"""
    parts: List[float] = []
    if isinstance(v4_pct, (int, float)):
        parts.append(float(v4_pct) / 50.0 - 1.0)
    if isinstance(chain, (int, float)):
        parts.append(float(chain))
    if isinstance(news_score, (int, float)):
        parts.append(float(news_score))
    if not parts:
        return {"score": None, "parts": 0}
    return {"score": round(sum(parts) / len(parts), 4), "parts": len(parts)}


# ── 池来源(v4 榜)────────────────────────────────────────────────────────

def v4_pool(top_n: int) -> List[dict]:
    """v4 榜按 pct 降序前 top_n:[{code, v4pct}];不可用 → RescoreError 拒开跑。"""
    import pandas as pd
    try:
        df = pd.read_parquet(_v4_ranking_path())
    except Exception as exc:  # noqa: BLE001
        raise RescoreError(f"v4 榜不可用: {type(exc).__name__}: {exc}")
    codecol = "code" if "code" in df.columns else ("ts_code" if "ts_code" in df.columns else None)
    if not codecol or "pct" not in df.columns:
        raise RescoreError("v4 榜列缺失(code/pct)")
    df = df.sort_values("pct", ascending=False).head(int(top_n))
    return [{"code": str(r[codecol]), "v4pct": float(r["pct"])} for _, r in df.iterrows()]


# ── run 主体 + 档案 ───────────────────────────────────────────────────────

def append_run(row: Dict[str, Any]) -> bool:
    try:
        RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def read_latest() -> Optional[Dict[str, Any]]:
    try:
        last = None
        with open(RUNS_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last) if last else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_rescore(run_id: str, top_n: int, note: str, progress) -> Dict[str, Any]:
    """再打分主体(daemon 线程内):池→产业链分→情绪分→综合→落档。展示型,零信号回写。"""
    try:
        progress(phase="pool", label=f"① 取 v4 榜前 {top_n}…")
        pool = v4_pool(top_n)
        codes = [r["code"] for r in pool]
        progress(phase="industry", label="② 产业链分(board 读数)…")
        ind, fresh = industry_scores(codes)
        progress(phase="news", label=f"③ 情绪分(LLM 整批,{len(codes)} 票)…")
        news, nstats = news_scores(codes, top_n=top_n)
        rows = []
        for r in pool:
            c = r["code"]
            ch = ind.get(c)
            nw = news.get(c)
            comp = composite_score(r.get("v4pct"),
                                   (ch or {}).get("chain") if ch else None,
                                   (nw or {}).get("score") if nw else None)
            rows.append({"code": c, "v4pct": r.get("v4pct"), "chain": ch, "news": nw,
                         "composite": comp["score"], "parts": comp["parts"]})
        end = {"run_id": run_id, "ts": _now(), "note": note, "top_n": top_n,
               "ok": True, "error": None, "rows": rows,
               "stats": dict(nstats, board_freshness=fresh)}
    except RescoreError as exc:
        end = {"run_id": run_id, "ts": _now(), "note": note, "top_n": top_n,
               "ok": False, "error": str(exc), "rows": [], "stats": {}}
    except Exception as exc:  # noqa: BLE001
        end = {"run_id": run_id, "ts": _now(), "note": note, "top_n": top_n,
               "ok": False, "error": f"{type(exc).__name__}: {exc}", "rows": [], "stats": {}}
    append_run(end)
    return end


# ── 单飞状态机 + 路由(照 research/api.py 范式:锁不嵌套/finally 必清 running)──

import threading as _threading
import time as _time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_RESCORE_LOCK = _threading.Lock()
_RESCORE_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "run_id": None,
    "started_at": None, "ended_at": None, "ok": None, "error": None, "lines": []}


def _rescore_public_state() -> Dict[str, Any]:
    with _RESCORE_LOCK:
        s = dict(_RESCORE_STATE)
        s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _time.time()) - s["started_at"])
    return s


def _progress(**kw: Any) -> None:
    with _RESCORE_LOCK:
        for k, v in kw.items():
            if k in ("phase", "label"):
                _RESCORE_STATE[k] = v
        if kw.get("label"):
            _RESCORE_STATE["lines"].append(str(kw["label"]))
            _RESCORE_STATE["lines"] = _RESCORE_STATE["lines"][-40:]


class RescoreIn(BaseModel):
    """``POST /screen/rescore`` 入参(钳制在端点内做,服务端权威)。"""

    top_n: int = 50
    note: str = ""


def _run_thread(run_id: str, top_n: int, note: str) -> None:
    err = None
    end: Dict[str, Any] = {}
    try:
        end = run_rescore(run_id, top_n=top_n, note=note, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        ok = err is None and bool(end.get("ok"))
        with _RESCORE_LOCK:
            _RESCORE_STATE.update(running=False, ended_at=_time.time(), ok=ok,
                                  phase=("done" if ok else "error"),
                                  error=(err or end.get("error")))


def build_rescore_router() -> APIRouter:
    """再打分路由组(/screen/rescore*;展示型,无开关无定时器无子进程)。"""
    router = APIRouter(tags=["rescore"])

    @router.post("/screen/rescore")
    def rescore_start(body: RescoreIn):
        top_n = max(5, min(int(body.top_n or 50), 100))
        run_id = new_run_id()
        with _RESCORE_LOCK:
            busy = bool(_RESCORE_STATE.get("running"))
            if not busy:
                _RESCORE_STATE.update(running=True, phase="starting", label="启动再打分…",
                                      run_id=run_id, started_at=_time.time(), ended_at=None,
                                      ok=None, error=None, lines=[])
        if busy:                                    # 锁外读状态(锁不可重入,绝不嵌套)
            return JSONResponse({"ok": False, "reason": "already_running",
                                 "state": _rescore_public_state()})
        _threading.Thread(target=lambda: _run_thread(run_id, top_n, (body.note or "").strip()),
                          name="rescore", daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "run_id": run_id,
                             "state": _rescore_public_state()})

    @router.get("/screen/rescore/status")
    def rescore_status():
        return JSONResponse({"ok": True, "state": _rescore_public_state()})

    @router.get("/screen/rescore/latest")
    def rescore_latest():
        return JSONResponse({"ok": True, "run": read_latest()})

    return router
