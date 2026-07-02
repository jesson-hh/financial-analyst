# -*- coding: utf-8 -*-
"""选股 REST(guanlan 自有,挂到薄壳 app 上)—— ``/screen/*``。

随 cards / seats / factorlib / workflow 先例的工厂式:``build_screen_router()`` 返回
``/screen`` 路由组。核心 ``POST /screen/run`` 把前端约束编译成「最新截面打分→约束→
行业中性→TopN→分布统计」,返回与前端 ``window.xgBuild`` 同形的结果。

复用 ``/feature/build`` 同一条 universe→panel→eval 链(``resolve_universe_codes`` →
``get_default_loader`` → ``load_panel_cached`` → ``compile_factor``),引擎 primitive 全部
**函数体内延迟 import**;数据走引擎 ``get_data_paths``。诚实失败:异常 / 空 universe /
空面板 / 无可用因子 → ``ok:False`` + ``reason``,HTTP 200。

⚠ 范围(见 docs/superpowers/specs/2026-06-04-screen-backend-wiring-design.md §7):
仅价量类因子(reversal/distrib)已接真 DSL;北向/PEAD/消息面因子 expr=None(字段缺口,
Phase 2),诚实标 ``unsupported_factors``,**不伪造**。ST/停牌/涨跌停/次新 排除与个股
中文名同属字段缺口,framework 阶段标 ``unsupported_excl`` / name=code,不糊。
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# 因子目录(服务端):id → {expr(预定向 zoo-DSL), dir, short, family, desc}。
# 选股页 2.0:从硬编 5 因子(2 可用+3 幽灵)换成 catalog.py 动态目录(~56 因子·11 族,
# 含 idx_ret 大盘共振/跟随族);静态"展示 IC"废除,实测 IC 由 factor_ic.parquet 合并下发。
from guanlan_v2.screen.catalog import FACTOR_DEFS, FAMILY_ORDER  # noqa: E402


class FactorIn(BaseModel):
    id: str
    w: float = 1.0


class ScreenIn(BaseModel):
    """``POST /screen/run`` 入参(前端 cfg,camelCase 原样上送)。"""

    factors: List[FactorIn] = Field(default_factory=list)
    topN: int = 20
    blend: float = 1.0           # 混合重排 α:右侧排序 = α·v4模型 +(1-α)·所选因子复合(1=纯v4)
    pool: str = "all"            # 股票池:all=全A(v4评级榜) / csi300 / csi500 / csi800 / csi1000
    mlStatus: List[str] = Field(default_factory=list)  # 主线筛选:保留的状态(空/全选=不筛)
    industryNeutral: bool = True
    indCap: float = 0.25
    liqMin: float = 0.0          # 成交额下限(亿)
    exclST: bool = True
    exclHalt: bool = True
    exclLimit: bool = True
    exclNew: bool = False
    universe: str = "csi_fast"
    model: str = "prod"          # v4 模型:prod=生产 / 变体 id(读 models/<id>)
    regimeWeights: bool = False  # regime 因子族动态权重(opt-in;默认 False=路径零触碰,须过闸+新鲜双闸)
    start: Optional[str] = None
    end: Optional[str] = None
    freq: str = "day"
    snapshot: bool = False       # P0:标记「正式选股」落 picks 档案(P1 收益跟踪只认 snapshot 行)
    note: Optional[str] = None   # P0:选股备注,随 picks 档案落盘


class LLMIn(BaseModel):
    """``POST /screen/llm`` 入参:L5 ≤5 持仓(可带 views)+ 市场节奏。前端从 result.decision/market 组装。"""

    final: List[Dict[str, Any]] = Field(default_factory=list)
    market: Optional[Dict[str, Any]] = None


class PickIn(BaseModel):
    """``POST /screen/pick`` 入参:自然语言选股思路 → LLM 选因子。"""

    prompt: str = ""


class PhraseIn(BaseModel):
    """``POST /screen/phrase`` 入参:一句话调约束 + 当前 cfg → LLM 约束补丁。"""

    phrase: str = ""
    cfg: Dict[str, Any] = Field(default_factory=dict)


class NewsIn(BaseModel):
    """``POST /screen/news`` 入参:候选代码列表 → 实时快讯 + LLM 情绪。"""

    codes: List[str] = Field(default_factory=list)


class RegenIn(BaseModel):
    """``POST /screen/regen`` 入参:可选指定截止交易日(``YYYY-MM-DD``),缺省=最新数据日。"""

    end: Optional[str] = None


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _fail(reason: str, **extra: Any) -> JSONResponse:
    payload: Dict[str, Any] = {
        "ok": False, "reason": reason,
        "chosen": [], "benched": [], "pool": [], "scored": [],
        "stat": {"n": 0, "indDist": [], "combIC": None, "avgPct": 0, "newsDist": None},
    }
    payload.update(extra)
    return JSONResponse(payload)


# #3 去 mock(2026-06-08):原 V4_MODEL_RANKIC=0.05 装饰常数已废弃 —— 因子路径改算复合因子真
# rank-IC,v4 路径(vendored 产物)诚实给 None。保留占位仅防旧引用,**不再下发**。
V4_MODEL_RANKIC = None  # deprecated, unused


_SCREEN_LOADER = None


def _screen_loader():
    """缓存 default loader。``get_default_loader`` 非单例,每请求新建会让 ``fetch_financials``
    重读 3 张大财务表重建索引(~8s/次);常驻一份 → 财务索引只建一次,后续请求毫秒级。
    线程安全(建索引有 ``_fin_lock``;``_read_bin`` 每次独立 open)。"""
    global _SCREEN_LOADER
    if _SCREEN_LOADER is None:
        from financial_analyst.data.loader_factory import get_default_loader
        _SCREEN_LOADER = get_default_loader()
    return _SCREEN_LOADER


# ───────────────────────────────────────────────────────────────────────────
# 异步再生(「拉取最新数据」)—— 彻底掉 qlib 后,UI 一键触发引擎原生 compute.regen
# 子进程再生三产物;进度可轮询,退 0 即热加载(清 LRU 缓存,无需重启 9999)。
# 子进程隔离(崩溃/内存安全),单飞锁防并发,regen 内部 .tmp→os.replace 原子写防半截。
# ───────────────────────────────────────────────────────────────────────────
import threading as _threading

_REGEN_LOCK = _threading.Lock()
_REGEN_STATE: Dict[str, Any] = {
    "running": False, "phase": "idle", "label": "", "step": 0, "total": 4,
    "started_at": None, "ended_at": None, "ok": None, "error": None,
    "end": None, "new_date": None, "lines": [],
}


def _regen_public_state() -> Dict[str, Any]:
    """快照 _REGEN_STATE(只取一次锁,绝不嵌套)+ 补 elapsed_sec。"""
    import time as _t
    with _REGEN_LOCK:
        s = dict(_REGEN_STATE)
        s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _reload_artifacts_caches() -> None:
    """热加载:清三产物的进程级 LRU,使后端立即读到刚再生的 parquet(无需重启 9999)。
    load_v4_ranking 本就无缓存(每次读盘)→ v4 自动生效;此处清 lru_cache 的 mainline/名表 + market_cycle。"""
    try:
        from guanlan_v2 import strategy as S
        for fn in ("mainline_status_map", "name_industry_map"):
            f = getattr(S, fn, None)
            if f is not None and hasattr(f, "cache_clear"):
                f.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from guanlan_v2.strategy.perspectives import market_cycle as _mc
        if hasattr(_mc, "cache_clear"):
            _mc.cache_clear()
    except Exception:  # noqa: BLE001
        pass


def _regen_phase_from_line(line: str):
    """把 compute.regen 的 stdout 进度行映射到 (phase_key, 中文 label, step/4)。"""
    if "[regen]" not in line:
        return None
    if "breadth" in line:
        return ("breadth", "节奏 · 市场宽度残差", 1)
    if "mainline" in line:
        return ("mainline", "主线 · 月度行业面板", 2)
    if "v4" in line:
        return ("v4", "v4 排名 · LGB 训练(约 5 分钟)", 3)
    if "model_health" in line:
        return ("model_health", "模型体检(回看IC+快照+vintage)", 4)
    if "factor_ic" in line:
        return ("factor_ic", "因子库实测 IC(csi300·近60日)", 4)
    if "provenance" in line:
        return ("provenance", "校验哈希 · 收尾", 4)
    return None


def _run_regen_subprocess(end: Optional[str]) -> None:
    """后台线程体:跑 ``python -m guanlan_v2.strategy.compute.regen [END]`` 子进程,逐行解析进度;
    退 0 → 热加载缓存 + 记新日期;否则记错误。无论何种结局 finally 必清 running(防卡死)。
    子进程用同一解释器(sys.executable=引擎 venv),cwd=仓根;无网络/无 qlib/无 LLM。"""
    import os
    import sys as _sys
    import time as _t
    import subprocess
    from pathlib import Path as _P

    rc = None
    err = None
    nd = ""
    repo_root = _P(__file__).resolve().parents[2]   # guanlan_v2/screen/api.py 上 3 级 = G:/guanlan-v2
    cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.regen"] + ([end] if end else [])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        for raw in proc.stdout:   # 实时进度
            line = raw.rstrip("\r\n")
            if not line:
                continue
            ph = _regen_phase_from_line(line)
            with _REGEN_LOCK:
                _REGEN_STATE["lines"].append(line)
                if len(_REGEN_STATE["lines"]) > 40:
                    _REGEN_STATE["lines"] = _REGEN_STATE["lines"][-40:]
                if ph:
                    _REGEN_STATE["phase"], _REGEN_STATE["label"], _REGEN_STATE["step"] = ph
        proc.wait()
        rc = proc.returncode
        if rc == 0:
            _reload_artifacts_caches()
            try:
                from guanlan_v2 import strategy as S
                nd = S.ranking_date()
            except Exception:  # noqa: BLE001
                nd = ""
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        ok = (err is None and rc == 0)
        with _REGEN_LOCK:
            _REGEN_STATE.update(
                running=False, ended_at=_t.time(), ok=ok,
                phase=("done" if ok else "error"),
                step=(4 if ok else _REGEN_STATE.get("step", 0)),
                error=(err if err else (None if ok else f"再生子进程退出码 {rc}")),
                new_date=(nd if ok else _REGEN_STATE.get("new_date")),
            )


# ───────────────────────────────────────────────────────────────────────────
# 异步 v4 模型训练(「模型工坊」)—— UI 选因子/基础特征,一键训练新变体子进程。
# 镜像上面的 regen 机器:单飞锁 + 可轮询进度 + 子进程隔离;finally 必清 running。
# 子进程跑 ``python -m guanlan_v2.strategy.compute.model_train <spec.json>``(Task 5),
# 训练完落 models/<variant_id>/{v4_ranking.parquet,meta.json}(registry 写),供选股页 model 选用。
# ───────────────────────────────────────────────────────────────────────────
_MODEL_LOCK = _threading.Lock()
_MODEL_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _time_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def _model_public_state() -> Dict[str, Any]:
    import time as _t
    with _MODEL_LOCK:
        s = dict(_MODEL_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_model_train_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"mtrain_{spec['variant_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.model_train", str(sf)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _MODEL_LOCK:
                _MODEL_STATE["lines"].append(line)
                if "[model_train]" in line or "build_feature_panel" in line:
                    _MODEL_STATE["phase"], _MODEL_STATE["label"], _MODEL_STATE["step"] = ("train", "训练中(LGB)", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _MODEL_LOCK:
            _MODEL_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})


# ───────────────────────────────────────────────────────────────────────────
# 异步 CPCV 验证 —— UI 发起 quick(内联,秒级)或 strict(子进程,分钟级)验证。
# 镜像 model-train 机器:单飞锁 + 可轮询进度 + 子进程隔离;finally 必清 running。
# 子进程跑 ``python -m guanlan_v2.strategy.compute.cpcv <spec.json>``(CPCV __main__),
# 完成后由 /model/validate/status 从 model_health.load_cpcv_summary 取摘要。
# ───────────────────────────────────────────────────────────────────────────
_VALIDATE_LOCK = _threading.Lock()
_VALIDATE_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 15, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "model_id": None, "lines": []}


def _validate_public_state() -> Dict[str, Any]:
    import time as _t
    with _VALIDATE_LOCK:
        s = dict(_VALIDATE_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_validate_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"cpcv_{spec['model_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.Popen([_sys.executable, "-m", "guanlan_v2.strategy.compute.cpcv", str(sf)],
            cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line:
                with _VALIDATE_LOCK:
                    _VALIDATE_STATE["lines"].append(line)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _VALIDATE_LOCK:
            _VALIDATE_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 15})


def _panel_enrich(codes, freq: str = "day", factors=None):
    """引擎面板取 codes:最新截面 + 每股 L3 vol_regime + L4 位置指标(ret/RSI/位置)。

    只读数据(经 get_data_paths),非跨界面交互。窗口 ~130 自然日(够 60 日换手均值 + 20 日动量)。
    返回 ``(disp, regime, metrics)``:``disp={code:(price,chg,amt)}`` 最新截面;
    ``regime={code:label}``(L3,仅非 neutral);``metrics={code:{pos_pct,ret60,ret20,rsi}}``
    (L4 V4 位置/护盾用,均由日线 close 序列算)。缺引擎/数据 → ``({}, {}, {})``。
    """
    import pandas as pd
    from datetime import date, timedelta

    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.expr import compile_factor
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    except Exception:  # noqa: BLE001
        return {}, {}, {}
    end = date.today().isoformat()
    # 窗口自适应:基础 130 自然日(60日换手均值+20日动量);所选因子含更长回看
    # (如 ts_max(close,240)/mom_120)时按表达式内最大整数扩窗(交易日→自然日 ×1.6+40),封顶 470。
    _lookback = 130
    _sel_exprs: List[str] = []
    for f in (factors or []):
        fid = getattr(f, "id", None) if not isinstance(f, dict) else f.get("id")
        ex = (FACTOR_DEFS.get(fid) or {}).get("expr") if fid else None
        if ex:
            _sel_exprs.append(ex)
    if _sel_exprs:
        import re as _re
        _nums = [int(n) for ex in _sel_exprs for n in _re.findall(r"\d+", ex)]
        if _nums:
            _lookback = max(130, min(470, int(max(_nums) * 1.6) + 40))
    start = (date.today() - timedelta(days=_lookback)).isoformat()
    try:
        ind_loader = None
        if any("indmean(" in ex or "industry" in ex for ex in _sel_exprs):
            try:
                from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
                ind_loader = IndustryLoader() if industry_map_path().exists() else None
            except Exception:  # noqa: BLE001
                ind_loader = None
        panel = load_panel_cached(_screen_loader(), list(codes), start, end, freq=freq,
                                  industry_loader=ind_loader)
    except Exception:  # noqa: BLE001
        return {}, {}, {}
    # 大盘因子(共振/跟随族,表达式引用 idx_ret)→ 注入真沪深300 日收益列
    # (复用 workflow._inject_market_refs,返回新 PanelData 不污染共享缓存;失败→该族诚实算不出)。
    if any("idx_ret" in ex for ex in _sel_exprs):
        try:
            from guanlan_v2.workflow.api import _inject_market_refs
            panel, _ = _inject_market_refs(panel, "csi300", None, start, end, freq=freq)
        except Exception:  # noqa: BLE001
            pass

    def _col(expr: str):
        try:
            s = compile_factor(expr)(panel)
            return s if isinstance(s, pd.Series) else None
        except Exception:  # noqa: BLE001
            return None

    close_s, ret_s, amt_s, tr_s = (_col("close"), _col("returns"),
                                   _col("amount"), _col("turnover_rate"))
    open_s, high_s, low_s = _col("open"), _col("high"), _col("low")  # L4 V2 梯队/V5 反应
    if close_s is None:
        return {}, {}, {}

    def _latest(s):
        if s is None:
            return {}
        sd = s.dropna()
        if sd.empty:
            return {}
        dts = sd.index.get_level_values("datetime")
        last = pd.Index(dts).max()
        sub = sd[dts == last]
        return {str(c): float(v)
                for c, v in zip(sub.index.get_level_values("code"), sub.values)}

    cl, rt, am = _latest(close_s), _latest(ret_s), _latest(amt_s)
    disp = {}
    for c in set(cl) | set(rt) | set(am):
        disp[c] = (cl.get(c), rt.get(c), am.get(c))

    # close 按 code 分组(L4 指标 + L3 vol_regime 共用)
    try:
        close_g = {str(c): g.sort_index(level="datetime")
                   for c, g in close_s.dropna().groupby(level="code")}
    except Exception:  # noqa: BLE001
        close_g = {}

    # —— L4 V4 位置指标:pos_pct(窗口内位置)/ ret60 / ret20 / RSI14 ——
    metrics: Dict[str, Dict[str, Any]] = {}
    for c, cs in close_g.items():
        vals = [float(v) for v in cs.values if math.isfinite(float(v))]   # 排除 NaN + ±Inf(与 _num 口径一致)
        if len(vals) < 5:
            continue
        last, lo, hi = vals[-1], min(vals), max(vals)
        pos_pct = ((last - lo) / (hi - lo)) if hi > lo else 0.5
        # 序列不足 60/20 个交易日 → None(不拿「自上市起涨幅」冒充 ret60,否则误触 v4.2/V4 仕佳警示)
        ret60 = (last / vals[-61] - 1.0) if len(vals) > 60 else None
        ret20 = (last / vals[-21] - 1.0) if len(vals) > 20 else None
        rsi = None
        if len(vals) >= 15:
            gains = sum(max(vals[i] - vals[i - 1], 0.0) for i in range(len(vals) - 14, len(vals)))
            losses = sum(max(vals[i - 1] - vals[i], 0.0) for i in range(len(vals) - 14, len(vals)))
            if (gains + losses) > 0:
                rsi = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
        metrics[c] = {"pos_pct": pos_pct, "ret60": ret60, "ret20": ret20, "rsi": rsi}

    # —— L4 V2 梯队(连板高度/封板)+ V5 反应(gap/日内开收)—— 由日线 OHLC 现算 ——
    #   板涨停阈值:主板 9.5% / 双创(SH688·SZ30) 19.5% / 北交(BJ) 29.5%(与 r27/主线同口径)。
    def _board_thr(code: str) -> float:
        cc = str(code).upper()
        if cc.startswith("SH688") or cc.startswith("SZ30"):
            return 0.195
        if cc.startswith("BJ"):
            return 0.295
        return 0.095

    def _grp(s):
        if s is None:
            return {}
        try:
            return {str(c): g.sort_index(level="datetime")
                    for c, g in s.dropna().groupby(level="code")}
        except Exception:  # noqa: BLE001
            return {}

    open_g, high_g, low_g = _grp(open_s), _grp(high_s), _grp(low_s)
    for c, cs in close_g.items():
        if c not in metrics:
            continue
        cvals = [float(v) for v in cs.values if math.isfinite(float(v))]
        if len(cvals) < 2:
            continue
        thr = _board_thr(c)
        rets = [cvals[i] / cvals[i - 1] - 1.0 for i in range(1, len(cvals))]
        streak = 0                       # 连板高度:最新日往回数连续涨停
        for r in reversed(rets):
            if r >= thr - 1e-9:
                streak += 1
            else:
                break
        max20 = cur = 0                  # 近 20 日内最高连板(退潮判断)
        for r in rets[-20:]:
            if r >= thr - 1e-9:
                cur += 1
                max20 = max(max20, cur)
            else:
                cur = 0
        last_close, prev_close = cvals[-1], cvals[-2]
        o = float(open_g[c].values[-1]) if c in open_g and len(open_g[c].values) else None
        h = float(high_g[c].values[-1]) if c in high_g and len(high_g[c].values) else None
        lo = float(low_g[c].values[-1]) if c in low_g and len(low_g[c].values) else None
        _r1 = rets[-1] if rets else None
        metrics[c].update({
            "limit_streak": streak,
            "max_streak_20": max20,
            "sealed": bool(streak >= 1 and h is not None and abs(last_close - h) < 1e-6),
            "gap": (o / prev_close - 1.0) if o else None,
            "intraday": (last_close / o - 1.0) if o else None,
            "amp": ((h - lo) / prev_close) if (h is not None and lo is not None and prev_close) else None,
            # 排除开关「涨跌停」真剔用(阈值随板别,与连板/主线同口径)
            "ret_1d": _r1,
            "limit_today": bool(_r1 is not None and abs(_r1) >= thr - 1e-9),
            "limit_dir": ((1 if _r1 > 0 else -1) if (_r1 is not None and abs(_r1) >= thr - 1e-9) else 0),
        })

    # —— 排除开关「停牌/次新」字段(由同一面板诚实推导;screen 三开关真剔用)——
    #    停牌口径=「截至面板末日无当日 bar」(与 ingest 滞后在数据上不可分,UI 文案标「当日无成交」);
    #    次新口径=「首根 bar 落在窗口内 且 窗口内有效 bar < 60」(~130 自然日窗 ≈ 88 交易日)。
    #    短序列票(<5 bar,极端次新/长停)此前不进 metrics → setdefault 补空壳;消费方全 .get,None 安全。
    try:
        _glob_last = pd.Index(close_s.dropna().index.get_level_values("datetime")).max()
    except Exception:  # noqa: BLE001
        _glob_last = None
    _win_start = pd.Timestamp(start)
    for c, cs in close_g.items():
        m = metrics.setdefault(c, {})
        try:
            _dts = cs.index.get_level_values("datetime")
            m["last_lag_days"] = (int((_glob_last - _dts.max()).days)
                                  if _glob_last is not None else None)
            m["bars_n"] = int(len(cs))
            m["first_inside_win"] = bool(_dts.min() > _win_start + pd.Timedelta(days=7))
        except Exception:  # noqa: BLE001
            pass

    # —— L4 V7 催化:业绩日历(迁后财务 ann_date + 同期 YoY,引擎 fetch_financials 直读)——
    #   每股取 ≤ 今日的最近一期财报:cat_days(距今天数)/cat_np_yoy/cat_rev_yoy/cat_ann。
    #   财务索引在 loader 实例上缓存(首次 build,后续 O(1));缺财务则该股不带 cat_* → V7 回退。
    try:
        from datetime import date as _date
        _loader = _screen_loader()
        _today = pd.Timestamp(_date.today())
        for c in list(metrics.keys()):
            try:
                fin = _loader.fetch_financials(c)
            except Exception:  # noqa: BLE001
                fin = None
            if fin is None or len(fin) == 0:
                continue
            fin = fin.sort_index()
            past = fin.index[fin.index <= _today]
            if len(past) == 0:
                continue
            ann = past[-1]
            row = fin.loc[ann]
            if getattr(row, "ndim", 1) > 1:   # 同日多行(罕见)→ 取最后
                row = row.iloc[-1]

            def _fv(k):
                try:
                    v = float(row[k])
                    return v if v == v else None
                except Exception:  # noqa: BLE001
                    return None

            metrics[c]["cat_days"] = int((_today - pd.Timestamp(ann)).days)
            metrics[c]["cat_ann"] = str(pd.Timestamp(ann).date())
            metrics[c]["cat_np_yoy"] = _fv("np_yoy")
            metrics[c]["cat_rev_yoy"] = _fv("rev_yoy")
    except Exception:  # noqa: BLE001
        pass

    # —— L4 V8 资金:龙虎榜(机构/游资 净买额,新鲜到 ~T-数日)—— 读 wiki_source 明细 ——
    #   北向持股已于 2024-08 起停披(不可用);龙虎榜是当下唯一新鲜的真资金归因源。
    #   窗口 12 自然日内最近一次上榜:lhb_net(净买额·元)/lhb_pct/lhb_inst(机构席位?)/lhb_date/lhb_n。
    try:
        from datetime import date as _date2
        lhb_path = (_loader._roots["day"].parent / "wiki_source" / "raw"
                    / "market_events" / "lhb_detail.parquet")
        if lhb_path.exists():
            _lhb = pd.read_parquet(lhb_path, columns=[
                "代码", "上榜日", "解读", "龙虎榜净买额", "净买额占总成交比", "上榜原因"])
            _lhb["_d"] = pd.to_datetime(_lhb["上榜日"], errors="coerce")
            _cut = pd.Timestamp(_date2.today()) - pd.Timedelta(days=12)
            _lhb = _lhb[_lhb["_d"].notna() & (_lhb["_d"] >= _cut)]
            _recent = {}
            for code6, g in _lhb.groupby("代码"):
                g = g.sort_values("_d")
                last = g.iloc[-1]
                txt = f"{last.get('解读', '')} {last.get('上榜原因', '')}"

                def _nv(v):
                    try:
                        v = float(v)
                        return v if v == v else None
                    except Exception:  # noqa: BLE001
                        return None
                _recent[str(code6)] = {
                    "lhb_date": str(last["_d"].date()),
                    "lhb_net": _nv(last["龙虎榜净买额"]),
                    "lhb_pct": _nv(last["净买额占总成交比"]),
                    "lhb_n": int(len(g)),
                    "lhb_inst": ("机构" in txt),
                    "lhb_read": str(last.get("解读", ""))[:40],
                }
            for c in list(metrics.keys()):
                num = c[2:] if (len(c) > 2 and c[:2] in ("SH", "SZ", "BJ")) else c
                if num in _recent:
                    metrics[c].update(_recent[num])
    except Exception:  # noqa: BLE001
        pass

    # —— L3 vol_regime(需 close + turnover_rate 序列 ≥20 日;vendored 自包含评分器)——
    regime = {}
    try:
        from guanlan_v2.strategy.sentiment import compute_vol_regime
        if compute_vol_regime is not None and tr_s is not None:
            tr_g = {str(c): g.sort_index(level="datetime")
                    for c, g in tr_s.dropna().groupby(level="code")}
            for c, cs in close_g.items():
                ts = tr_g.get(c)
                if ts is None or len(cs) < 20:
                    continue
                try:
                    res = compute_vol_regime(cs.reset_index(drop=True), ts.reset_index(drop=True))
                    lab = res.get("regime_label")
                    if lab and lab != "neutral":
                        regime[c] = lab
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        regime = {}

    # —— 混合重排:所选因子在最新截面的 z-复合分(供 _screen_via_v4 与 v4 分数混合)——
    #   仅 expr 非空(可在面板实算)的因子参与;北向/PEAD/消息面(expr=None)无数据,不计入。
    if factors:
        try:
            sup = []
            for f in factors:
                fid = getattr(f, "id", None) if not isinstance(f, dict) else f.get("id")
                fw = getattr(f, "w", 1.0) if not isinstance(f, dict) else f.get("w", 1.0)
                if fid and FACTOR_DEFS.get(fid, {}).get("expr"):
                    sup.append((fid, float(fw)))
            wsum = sum(abs(w) for _, w in sup) or 1.0
            for fid, w in sup:
                d = float(FACTOR_DEFS[fid]["dir"])
                cross = _latest(_col(FACTOR_DEFS[fid]["expr"]))   # {code: 最新截面值}
                if not cross:
                    continue
                vv = list(cross.values())
                mu = sum(vv) / len(vv)
                sd = (sum((x - mu) ** 2 for x in vv) / len(vv)) ** 0.5
                if sd <= 0:
                    continue
                for code, val in cross.items():
                    if code in metrics:
                        metrics[code]["comp_score"] = (
                            metrics[code].get("comp_score", 0.0) + (w / wsum) * d * (val - mu) / sd)
        except Exception:  # noqa: BLE001
            pass

    return disp, regime, metrics


def _resolve_model_id(m):
    """model 省略/'prod' → 查默认变体指针;显式变体 id 原样(解析不校验存在)。
    没设指针 → 'prod'(零行为变化)。"""
    mid = (m or "prod").strip() or "prod"
    if mid != "prod":
        return mid
    try:
        from guanlan_v2.screen.model_registry import get_default_model
        return get_default_model() or "prod"
    except Exception:
        return "prod"


def _record_picks(body: "ScreenIn", resp: Dict[str, Any], model_id: str, rdate: str) -> bool:
    """v4 主路径选股结果 → picks 档案一行(P0 §1)。失败回 False,由 picks_recorded 显形。"""
    from datetime import datetime as _dt
    from guanlan_v2.screen import picks as _picks
    chosen = resp.get("chosen") or []
    rec = {
        "ts": _dt.now().isoformat(timespec="seconds"),
        "date": rdate,
        "snapshot": bool(getattr(body, "snapshot", False)),
        "note": getattr(body, "note", None),
        "model": model_id,
        "pool": body.pool,
        "alpha": body.blend,
        "factors": [{"id": f.id, "w": f.w} for f in (body.factors or [])],
        "topN": body.topN,
        "n_universe": len(resp.get("pool") or []),
        "picks": [{"code": (x.get("s") or {}).get("code"),
                   "name": (x.get("s") or {}).get("name"),
                   "score": x.get("score"), "rank": i + 1}
                  for i, x in enumerate(chosen)],
        "constraints": {"liqMin": body.liqMin, "mlStatus": body.mlStatus,
                        "industryNeutral": body.industryNeutral, "indCap": body.indCap,
                        "exclST": body.exclST, "exclHalt": body.exclHalt,
                        "exclLimit": body.exclLimit, "exclNew": body.exclNew},
    }
    return _picks.append_pick(rec)


def _screen_via_v4(body: "ScreenIn"):
    """L1 = vendored v4 真排名(消费产物)。缺产物 → None(调用方回退玩具因子路径)。

    score=lgb_pct(全市场分位),顶200 带 v4_total/v4_layer;名称/行业来自 vendored
    tushare_basic;当前价/量/行业从引擎面板取(只读)。约束:liqMin + ST(名称)+ 行业中性 + TopN。
    """
    import math as _math

    _mid = _resolve_model_id(getattr(body, "model", "prod"))
    try:
        from guanlan_v2 import strategy as S
        try:
            rank = S.load_v4_ranking(model_id=_mid)
        except FileNotFoundError:
            rank = S.load_v4_ranking(); _mid = "prod"        # 变体不可用 → 诚实回落 prod
    except Exception:  # noqa: BLE001  —— 产物缺失/读失败 → 回退
        return None

    rdate = S.ranking_date(model_id=_mid)
    nim = S.name_industry_map()

    # 股票池切换:指数成份(csi300/500/800/1000)→ 先把 v4 排名过滤到成份内。
    #   五维评级(v4_total)仅全局顶200,指数池内多数无 → 指数池一律按 lgb_pct(全市场模型分位)排。
    _pool_sel = (getattr(body, "pool", "all") or "all").strip()
    _IDX = {"csi300", "csi500", "csi800", "csi1000"}
    if _pool_sel in _IDX:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            _uc = {str(c) for c in resolve_universe_codes(_pool_sel)}
        except Exception:  # noqa: BLE001
            _uc = set()
        if _uc:
            rank = rank[rank["code"].astype(str).isin(_uc)].copy()

    # 候选池 = v4 五维评级顶200(按 v4_total 排,faithful 复现 v4「评级 Top」);
    # 指数池 / 产物无 v4_total(退化)→ 按 lgb_pct 全市场分位排。
    rated = rank.dropna(subset=["v4_total"]).copy()
    use_v4_total = (_pool_sel not in _IDX) and (not rated.empty)
    if use_v4_total:
        pool_df = rated.sort_values(["v4_total", "lgb_pct"], ascending=[False, False])
        _vals = pool_df["v4_total"].astype(float)
    else:
        pool_df = (rank.dropna(subset=["lgb_pct"])
                   .sort_values("lgb_pct", ascending=False)
                   .head(max(int(body.topN) * 8, 150)))
        _vals = pool_df["lgb_pct"].astype(float)
    _pct = _vals.rank(pct=True).tolist()
    codes = [str(c) for c in pool_df["code"].tolist()]
    # —— regime 条件化(opt-in;缺省 False 分支不执行任何新代码——红线1)——
    _rw_badge = None
    _factors_eff = body.factors
    if getattr(body, "regimeWeights", False):
        try:
            from guanlan_v2.strategy.compute.factor_regime import resolve_regime_weights
            _eff, _rw_badge = resolve_regime_weights(body.factors, rdate)
            if _eff is not None:
                _factors_eff = _eff
        except Exception as _e:  # noqa: BLE001
            _rw_badge = {"applied": False, "fallback_reason": f"{type(_e).__name__}: {_e}",
                         "regime_asof": None, "per_factor": []}
    disp, regime, metrics = _panel_enrich(codes, body.freq, _factors_eff)

    scored_rows = []
    for r, _pctv in zip(pool_df.itertuples(index=False), _pct):
        code = str(r.code)
        nm, ind = nim.get(code, (code, "—"))
        nm, ind = (nm or code), (ind or "—")
        price, chg, amt = disp.get(code, (None, None, None))
        v4t = None if (r.v4_total is None or r.v4_total != r.v4_total) else float(r.v4_total)
        v4l = None
        if r.v4_layer is not None and not (isinstance(r.v4_layer, float) and r.v4_layer != r.v4_layer):
            v4l = str(r.v4_layer)
        scored_rows.append({
            "s": {
                "code": code, "name": nm, "ind": ind,
                "price": price if price is not None else 0.0,
                "chg": (chg * 100.0) if chg is not None else 0.0,
                "amt": (amt / 1e5) if amt is not None else 0.0,   # 最新截面 amount=千元→亿(历史早期为元,此处只取最新bar)
                "st": ("ST" in nm),
                "halt": False, "limit": 0, "newish": False,
                "v4_total": v4t, "v4_layer": v4l, "lgb_rank": int(r.lgb_rank),
                "vol_regime": regime.get(code),
            },
            "score": float(r.v4_total) if use_v4_total else float(r.lgb_pct),
            "pct": round(float(_pctv) * 100),
            "_v4pct": float(_pctv),
        })

    # —— 混合重排:右侧排序 = α·v4分位 +(1-α)·因子复合分位(body.blend;α=1 纯 v4)——
    #   仅 expr 可实算的因子(_panel_enrich 已算 comp_score)参与;无则维持纯 v4。
    _b = getattr(body, "blend", 1.0)
    _alpha = 1.0 if _b is None else max(0.0, min(1.0, float(_b)))
    _sup_fac = [f for f in (body.factors or []) if FACTOR_DEFS.get(f.id, {}).get("expr")]
    # 掌控审计 2026-06-15:v4 主路径也诚实回报未识别因子(不在目录/无表达式 → 不参与混合),
    # 不再「静默退纯 v4」——前端/agent 据此提示「这些因子未生效」。
    _unsup_fac = list(dict.fromkeys(
        f.id for f in (body.factors or []) if not (FACTOR_DEFS.get(f.id, {}) or {}).get("expr")))
    if _alpha < 1.0 and _sup_fac:
        _comps = {x["s"]["code"]: (metrics.get(x["s"]["code"]) or {}).get("comp_score")
                  for x in scored_rows}
        _have = sorted(v for v in _comps.values() if v is not None)
        if _have:
            _n = len(_have)
            for x in scored_rows:
                cs = _comps.get(x["s"]["code"])
                comp_pct = (bisect.bisect_right(_have, cs) / _n) if cs is not None else 0.5
                x["_blend"] = _alpha * x["_v4pct"] + (1.0 - _alpha) * comp_pct
                x["pct"] = round(x["_blend"] * 100)
            scored_rows.sort(key=lambda x: x.get("_blend", x["_v4pct"]), reverse=True)

    # —— L2 主线状态(按行业 join vendored 月度面板;只读,非界面交互)——
    try:
        ml = S.mainline_status_map()
    except Exception:  # noqa: BLE001
        ml = {}
    _ml_asof = (next(iter(ml.values()))["as_of"] if ml else None)
    for x in scored_rows:
        info = ml.get(x["s"]["ind"])
        if info:
            x["s"]["mainline"] = info["status"]
            x["s"]["mainline_golden"] = info["golden"]

    # —— L4 九视角 readout + L5 评级/护盾(逐候选;market_cycle 全市场同值)——
    from guanlan_v2.strategy.decision import apply_shields
    from guanlan_v2.strategy.perspectives import market_cycle, nine_view_scan

    mkt = market_cycle()
    # V9 比较:同业 60 日涨幅排序(候选内)
    ind_ret: Dict[str, List[float]] = {}
    for x in scored_rows:
        r6 = (metrics.get(x["s"]["code"]) or {}).get("ret60")
        if r6 is not None:
            ind_ret.setdefault(x["s"]["ind"], []).append(r6)
    for k in ind_ret:
        ind_ret[k].sort()

    def _v9(code: str, ind: str):
        r6 = (metrics.get(code) or {}).get("ret60")
        arr = ind_ret.get(ind, [])
        if r6 is None or len(arr) < 2:
            return None
        return {"pct": bisect.bisect_right(arr, r6) / len(arr), "n": len(arr)}

    # —— 分位制评级(选股页2.0):评级池(v4_total 非空)内按 (v4_total, lgb_rank) 字典序的
    #    池内分位 → rate_from_pool_pct(前10% 5★/前30% 4★…)。背景:绝对阈值 ≥6 在顶200 内
    #    几乎人人达标 → 星级失去区分度(诊断确认);池外(v4_total 空)仍走 rate_v4 旧语义。
    from guanlan_v2.strategy.decision import rate_from_pool_pct
    _pool_s = [x["s"] for x in scored_rows if x["s"].get("v4_total") is not None]
    _pool_s.sort(key=lambda s_: (float(s_["v4_total"]), -int(s_.get("lgb_rank") or 10 ** 9)))  # 弱→强
    base_by_code: Dict[str, Dict[str, Any]] = {}
    if len(_pool_s) >= 10:   # 池太薄(指数池交集小)分位无意义 → 退旧语义
        _dn = len(_pool_s) - 1
        for _i, s_ in enumerate(_pool_s):
            base_by_code[s_["code"]] = rate_from_pool_pct(_i / _dn, s_.get("v4_total"))

    for x in scored_rows:
        s = x["s"]
        mc = metrics.get(s["code"])
        dec = apply_shields(s, mc, base=base_by_code.get(s["code"]))
        s["rating"] = dec["stars_str"]
        s["stars"] = dec["stars"]
        s["pos_band"] = dec["band"]
        s["shields"] = dec["shields"]
        x["views"] = nine_view_scan(s, mc, mkt, _v9(s["code"], s["ind"]))

    # —— 排除:liqMin + ST(名称)+ 停牌/涨跌停/次新(由面板诚实推导真剔;面板缺才标 unsupported)——
    unsupported_excl = []
    _panel_has = bool(metrics)
    if body.exclHalt and not _panel_has:
        unsupported_excl.append("停牌(面板不可用)")
    if body.exclLimit and not _panel_has:
        unsupported_excl.append("涨跌停(面板不可用)")
    if body.exclNew and not _panel_has:
        unsupported_excl.append("次新(面板不可用)")
    liq_ok = any(x["s"]["amt"] for x in scored_rows)
    if body.liqMin and not liq_ok:
        unsupported_excl.append("流动性(面板无成交额)")
    # 主线筛选:只保留 mlStatus 选中的主线状态(空/全选=不筛;无状态票在筛选时按不匹配剔除)
    _ALL_ML = {"mainline", "initiation", "revival", "decay", "cold", "neutral"}
    _ml_sel = {s for s in (getattr(body, "mlStatus", None) or []) if s}
    _ml_on = bool(_ml_sel) and (_ml_sel != _ALL_ML)
    for x in scored_rows:
        excl = []
        s = x["s"]
        mc = metrics.get(s["code"]) or {}
        # 展示字段始终如实填(开关只决定剔不剔)
        _lag = mc.get("last_lag_days")
        s["halt"] = bool(_lag is not None and _lag > 0)
        s["limit"] = int(mc.get("limit_dir") or 0)
        s["newish"] = bool(mc.get("first_inside_win") and (mc.get("bars_n") or 99) < 60)
        if body.exclST and s["st"]:
            excl.append("ST")
        if body.exclHalt and s["halt"]:
            excl.append("停牌(当日无成交)")
        if body.exclLimit and s["limit"]:
            excl.append("涨停" if s["limit"] > 0 else "跌停")
        if body.exclNew and s["newish"]:
            excl.append("次新(<60交易日)")
        if body.liqMin and liq_ok and s["amt"] < body.liqMin:
            excl.append("流动性")
        if _ml_on and (s.get("mainline") not in _ml_sel):
            excl.append("主线状态")
        x["excl"] = excl

    pool = [x for x in scored_rows if not x["excl"]]   # 已按 lgb_pct 降序

    cap_per = max(1, _math.ceil(body.topN * body.indCap)) if body.industryNeutral else 10 ** 9
    ind_count = {}
    chosen, benched = [], []
    for x in pool:
        if len(chosen) >= body.topN:
            benched.append(x)
            continue
        ind = x["s"]["ind"]
        c = ind_count.get(ind, 0)
        if c >= cap_per:
            benched.append({**x, "benchReason": "行业满额"})
            continue
        ind_count[ind] = c + 1
        chosen.append(x)

    by_ind = {}
    for x in chosen:
        by_ind[x["s"]["ind"]] = by_ind.get(x["s"]["ind"], 0) + 1
    n_c = len(chosen) or 1
    ind_dist = sorted(({"ind": k, "n": v, "frac": v / n_c} for k, v in by_ind.items()),
                      key=lambda d: d["n"], reverse=True)
    avg_pct = (sum(x["pct"] for x in chosen) / len(chosen)) if chosen else 0

    # —— L5 决策层:≤5 收敛(护盾后 ★★★★+ → 行业去重 → ≤5)+ 市场节奏(V1)——
    from guanlan_v2.strategy.decision import converge
    decision = converge(chosen, metrics, max_n=5, base_by_code=base_by_code)
    decision["market"] = mkt

    # —— 模型体检摘要(regen 顺算的 model_health.parquet;缺产物 → None,前端不显卡)——
    try:
        from guanlan_v2.strategy.model_health import load_health_summary
        _mh = load_health_summary()
    except Exception:  # noqa: BLE001
        _mh = None

    # —— #7 v4 B3 集成 provenance(regen 落盘的旁路 JSON):纯 LGB / FinCast 混合 + look-ahead,
    #    供 UI 诚实徽章。缺产物 / 无 FinCast → None 或 active:false(显「纯 LGB」)。——
    try:
        import json as _json
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET as _V4P
        _dlp = _V4P.parent / "v4_dl_provenance.json"
        if _dlp.exists():
            _b3prov = _json.loads(_dlp.read_text(encoding="utf-8"))
        else:   # 回退旧单源 FinCast provenance(过渡期 / regen 未重跑)
            _b3p = _V4P.parent / "v4_b3_provenance.json"
            _b3prov = _json.loads(_b3p.read_text(encoding="utf-8")) if _b3p.exists() else None
    except Exception:  # noqa: BLE001
        _b3prov = None

    resp = {
        "ok": True,
        "source": "v4_ranking",
        "model": _mid,              # 实际所跑 v4 模型(变体缺失已回落 prod)
        "date": rdate,
        "v4_provenance": _b3prov,   # #7 v4 排名口径:纯 LGB vs LGB+FinCast(w_fc)+look-ahead,诚实显形
        "chosen": chosen,
        "benched": benched[:40],
        "pool": [{"s": x["s"]} for x in pool],
        "scored": [{"s": x["s"]} for x in scored_rows],
        # #3 去 mock:v4 路径消费的是 vendored 排名产物(非表达式 alpha 时序),无法当窗实算
        #    rank-IC → 诚实给 None(前端显示「—」),绝不再下发装饰常数 0.05。
        "stat": {"n": len(chosen), "indDist": ind_dist,
                 "combIC": None, "avgPct": round(avg_pct, 1), "newsDist": None},
        "unsupported_excl": unsupported_excl,
        "unsupported_factors": _unsup_fac,   # 未识别/无表达式的请求因子(已忽略,未参与混合)
        "mainline_as_of": _ml_asof,
        "market": mkt,
        "decision": decision,
        "model_health": _mh,   # 模型体检(回看趋势+vintage;缺产物=None,TopBar 不显卡)
        # 诚实健康标:disp 全空 = 引擎面板不可用(价/量/L3/L4 指标缺),UI 据此区分「引擎宕机」vs「字段稀疏」
        "panel_ok": bool(disp),
        # regime 徽章:仅 opt-in 请求才带此键(缺省响应逐字节不变);降级带 fallback_reason 显形
        **({"regime_weights": _rw_badge} if _rw_badge is not None else {}),
        "note": "L1=v4 排名 · L2 主线 · L3 量能 · L4 九视角(逐行 views)· L5 评级/护盾/≤5 收敛(decision)",
    }
    resp["picks_recorded"] = _record_picks(body, resp, _mid, rdate)   # P0:落档显形,失败不阻断
    return JSONResponse(resp)


def build_screen_router() -> APIRouter:
    """选股自有路由组(prefix ``/screen``)。随 cards/seats/factorlib/workflow 工厂式。"""
    router = APIRouter(prefix="/screen", tags=["screen"])

    @router.get("/regime")
    def screen_regime():
        """因子族 regime 只读:各族 p_fav/confirmed_since + 激活闸状态(前端因子卡/帷幄消费)。
        缺产物 → ok:false 诚实缺席,不造数。从 factor_regime 模块命名空间取路径常量
        (非直接 paths)——测试 monkeypatch 才能生效。"""
        try:
            import json as _json

            import pandas as _pd

            import guanlan_v2.strategy.compute.factor_regime as _frm
            if not _frm.FACTOR_REGIME_PARQUET.exists():
                return {"ok": False, "reason": "regime 产物缺失(先全量回填)"}
            df = _pd.read_parquet(_frm.FACTOR_REGIME_PARQUET)
            last = df[df["date"] == df["date"].max()]
            gate = (_json.loads(_frm.FACTOR_REGIME_GATE_JSON.read_text(encoding="utf-8"))
                    if _frm.FACTOR_REGIME_GATE_JSON.exists() else None)
            return {"ok": True, "asof": str(_pd.Timestamp(df["date"].max()).date()),
                    "spec_hash": _frm.SPEC_HASH,
                    "families": [{"family": str(r["family"]),
                                  "p_fav": round(float(r["p_fav"]), 4),
                                  "state": int(r["state"]),
                                  "confirmed_since": str(_pd.Timestamp(r["confirmed_since"]).date())}
                                 for _, r in last.iterrows()],
                    "gate": ({"activated": gate.get("activated"), "asof": gate.get("asof"),
                              "stale": gate.get("spec_hash") != _frm.SPEC_HASH} if gate else None)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"{type(e).__name__}: {e}"}

    @router.get("/factors")
    def screen_factors():
        """因子目录(~56 因子·11 族 + factorlib「因子库」族)+ **实测 RankIC**(factor_ic.parquet,
        regen 顺算;缺产物 → ic:null,前端显示「—」,不下发静态装饰数)。"""
        try:
            from guanlan_v2.screen.catalog import refresh_factor_defs
            refresh_factor_defs()   # factorlib 新存因子即时进目录(P1⑥;原地更新,消费方引用不变)
        except Exception:  # noqa: BLE001
            pass
        try:
            from guanlan_v2.screen.factor_ic import load_factor_ic
            real = load_factor_ic()
        except Exception:  # noqa: BLE001
            real = {}
        rows = []
        for k, v in FACTOR_DEFS.items():
            r = real.get(k) or {}
            rows.append({"id": k, "expr": v["expr"], "dir": v["dir"], "short": v["short"],
                         "family": v.get("family", ""), "desc": v.get("desc", ""),
                         "supported": bool(v["expr"]),
                         "ic": r.get("ic"), "icir": r.get("icir"),
                         "ic_n": r.get("n_days"), "ic_asof": r.get("asof")})
        # 族序 + 族内按 |实测IC| 降序(无 IC 的沉底)
        _fo = {f: i for i, f in enumerate(FAMILY_ORDER)}
        rows.sort(key=lambda x: (_fo.get(x["family"], 99),
                                 -(abs(x["ic"]) if x["ic"] is not None else -1)))
        return JSONResponse({"ok": True, "factors": rows, "families": FAMILY_ORDER,
                             "ic_note": "实测 RankIC·近60交易日·沪深300·前瞻5日(regen 时更新)"})

    @router.get("/health")
    def screen_health():
        """vendored v4 排名产物新鲜度(date/rows/stale_days),供前端判是否需重跑 v4 刷新。"""
        from datetime import date as _date

        try:
            from guanlan_v2 import strategy as S
            rd = S.ranking_date()
            n = int(len(S.load_v4_ranking()))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}",
                                 "v4_ranking": None})
        stale_days = None
        try:
            y, m, d = (int(x) for x in rd.split("-"))
            stale_days = (_date.today() - _date(y, m, d)).days
        except Exception:  # noqa: BLE001
            pass
        # L4 V1 节奏产物新鲜度(市场宽度面板 as_of;进程级缓存,刷新产物需重启 9999 生效)
        mb = None
        try:
            from guanlan_v2.strategy.perspectives import market_cycle
            mc = market_cycle()
            if mc:
                mb = {"as_of": mc.get("as_of"), "stage": mc.get("stage"), "cached": True}
        except Exception:  # noqa: BLE001
            pass
        try:
            from guanlan_v2.strategy.model_health import load_health_summary
            _mh = load_health_summary()
        except Exception:  # noqa: BLE001
            _mh = None
        return JSONResponse({"ok": bool(rd), "source": "vendored",
                             "v4_ranking": {"date": rd, "rows": n, "stale_days": stale_days},
                             "market_breadth": mb, "model_health": _mh})

    @router.post("/run")
    def screen_run(body: ScreenIn):
        import pandas as pd

        # L1 优先用 vendored v4 真排名(消费产物);缺产物 → 回退下方玩具因子路径。
        _v4 = _screen_via_v4(body)
        if _v4 is not None:
            return _v4

        universe = (body.universe or "csi_fast").strip() or "csi_fast"

        # —— 1. 选因子 → 服务端表达式(只取已定义且 expr 非空的)——
        sel = [f for f in (body.factors or []) if f.id in FACTOR_DEFS]
        supported = [f for f in sel if FACTOR_DEFS[f.id]["expr"]]
        unsupported = [f.id for f in sel if not FACTOR_DEFS[f.id]["expr"]]
        if not supported:
            return _fail("无可用因子(所选因子表达式暂未接入真 DSL,见 spec §7)",
                         universe=universe, unsupported_factors=unsupported)

        # —— 2. universe 解析(对齐 feature/build)——
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail(f"universe_error: {type(exc).__name__}: {exc}", universe=universe)
        if not codes:
            return _fail(f"empty_universe: universe '{universe}' 解析为空", universe=universe)

        # —— 窗口:end=今天, start=今天−400d(够取最新截面 + 60 日窗算子)——
        end = (body.end or "").strip() or date.today().isoformat()
        start = (body.start or "").strip() or (date.today() - timedelta(days=400)).isoformat()
        freq = (body.freq or "day").strip() or "day"

        # —— 3. 面板加载(逐字对齐 feature/build)——
        try:
            from financial_analyst.data.loader_factory import get_default_loader
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            loader = get_default_loader()
            try:
                from financial_analyst.data.loaders.industry import (
                    IndustryLoader, industry_map_path,
                )
                ind_loader = IndustryLoader() if industry_map_path().exists() else None
            except Exception:  # noqa: BLE001  —— 行业图缺失只是少 industry 列,不阻断
                ind_loader = None
            panel = load_panel_cached(loader, codes, start, end, freq=freq,
                                      industry_loader=ind_loader)
        except Exception as exc:  # noqa: BLE001
            return _fail(f"load_error: {type(exc).__name__}: {exc}",
                         universe=universe, start=start, end=end)

        # —— primitive import ——
        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            from financial_analyst.factors.zoo.registry import get as _get_alpha
        except Exception as exc:  # noqa: BLE001
            return _fail(f"engine_import_error: {type(exc).__name__}: {exc}", universe=universe)

        def _eval(expr: str):
            """注册名优先(借 zoo registry),否则当 zoo-DSL 表达式编译(同 feature/build)。"""
            try:
                return _get_alpha(expr).compute(panel)
            except KeyError:
                validate_expr(expr)
                return compile_factor(expr)(panel)

        def _safe_eval(expr: str):
            try:
                s = _eval(expr)
                return s if isinstance(s, pd.Series) else None
            except Exception:  # noqa: BLE001
                return None

        # —— 展示字段(尽量从面板取;失败置 None,不阻断)——
        close_s = _safe_eval("close")
        if close_s is None or close_s.dropna().empty:
            return _fail("面板无 close 字段或全空", universe=universe, start=start, end=end)
        ret_s = _safe_eval("returns")
        amt_s = _safe_eval("amount")
        ind_s = _safe_eval("industry")

        # —— 截面日:close 的最新有效日 ——
        screen_dt = pd.Index(close_s.dropna().index.get_level_values("datetime")).max()

        def _xs(s):
            """取 s 在 screen_dt 的截面(index→code);s 为 None / 无该日 → 空 Series。"""
            if s is None:
                return pd.Series(dtype="float64")
            try:
                dts = s.index.get_level_values("datetime")
                sub = s[dts == screen_dt]
                sub.index = sub.index.get_level_values("code")
                return sub
            except Exception:  # noqa: BLE001
                return pd.Series(dtype="float64")

        close_x, ret_x, amt_x, ind_x = _xs(close_s), _xs(ret_s), _xs(amt_s), _xs(ind_s)

        # —— 4. 逐因子求值 → 截面 z(手算截面 z,避开 engine zscore 的分组假设)——
        wsum = sum(f.w for f in supported) or 1.0
        z_by_factor: Dict[str, "pd.Series"] = {}
        comp_ts = None   # #3 全 panel 复合因子时间序列(逐日截面 z × 权重 × 方向 累加)→ 算真 combIC
        for f in supported:
            expr = FACTOR_DEFS[f.id]["expr"]
            try:
                s = _eval(expr)
            except Exception as exc:  # noqa: BLE001
                return _fail(f"factor_eval_error[{f.id}]: {type(exc).__name__}: {exc}",
                             universe=universe, start=start, end=end)
            if not isinstance(s, pd.Series):
                return _fail(f"因子 {f.id} 求值非 Series", universe=universe)
            # #3 真 combIC:全 panel 逐日截面标准化 → 加权(含方向)累加成复合因子时序。
            try:
                zt = s.groupby(level="datetime").transform(
                    lambda x: (x - x.mean()) / (x.std(ddof=0) if x.std(ddof=0) else 1.0))
                term = (f.w / wsum) * float(FACTOR_DEFS[f.id]["dir"]) * zt
                comp_ts = term if comp_ts is None else comp_ts.add(term, fill_value=0.0)
            except Exception:  # noqa: BLE001 — 单因子时序 z 失败不阻断,仅少一腿
                pass
            xs = _xs(s).dropna()
            if xs.empty:
                continue
            sd = float(xs.std(ddof=0))
            z_by_factor[f.id] = (xs - float(xs.mean())) / (sd if sd else 1.0)

        if not z_by_factor:
            return _fail("所有因子在最新截面全 NaN(窗口或数据问题)",
                         universe=universe, start=start, end=end)

        # —— 复合分 Σ (w/Σw)·dir·z(按 code 对齐,缺失计 0)——
        code_set = set(close_x.index)
        for z in z_by_factor.values():
            code_set |= set(z.index)
        scored_rows: List[Dict[str, Any]] = []
        for code in sorted(code_set, key=str):
            sc = 0.0
            for f in supported:
                z = z_by_factor.get(f.id)
                if z is None or code not in z.index:
                    continue
                sc += (f.w / wsum) * FACTOR_DEFS[f.id]["dir"] * float(z.loc[code])
            price = _num(close_x.get(code))
            chg = _num(ret_x.get(code))
            amt = _num(amt_x.get(code))
            ind_v = ind_x.get(code) if code in getattr(ind_x, "index", []) else None
            if ind_v is None or _num(ind_v) is not None:
                ind = str(ind_v) if (ind_v is not None and _num(ind_v) is None) else "—"
            else:
                ind = str(ind_v)
            if ind in ("nan", "None", ""):
                ind = "—"
            scored_rows.append({
                "s": {
                    "code": str(code),
                    "name": str(code),                       # TODO 名称解析(Phase 2);framework 用 code
                    "ind": ind,
                    "price": price if price is not None else 0.0,
                    "chg": (chg * 100.0) if chg is not None else 0.0,
                    "amt": (amt / 1e5) if amt is not None else 0.0,   # qlib amount 单位=千元 → 亿(raw/1e5;已对 csi_fast 截面核对)
                    "st": False, "halt": False, "limit": 0, "newish": False,  # 字段缺口,Phase 2
                },
                "score": sc,
            })

        # —— 分位(pctRank over all scores)——
        scores = sorted(x["score"] for x in scored_rows)
        n_all = len(scores) or 1
        for x in scored_rows:
            x["pct"] = round(bisect.bisect_right(scores, x["score"]) / n_all * 100)

        # —— 5. 排除规则 → reason(framework 只实现流动性;ST/停牌/涨跌停/次新 字段缺口)——
        unsupported_excl: List[str] = []
        if body.exclST:
            unsupported_excl.append("ST")
        if body.exclHalt:
            unsupported_excl.append("停牌")
        if body.exclLimit:
            unsupported_excl.append("涨跌停")
        if body.exclNew:
            unsupported_excl.append("次新")
        liq_ok = amt_s is not None
        if body.liqMin and not liq_ok:
            unsupported_excl.append("流动性(无成交额字段)")
        for x in scored_rows:
            excl: List[str] = []
            if body.liqMin and liq_ok and x["s"]["amt"] < body.liqMin:
                excl.append("流动性")
            x["excl"] = excl

        pool = sorted((x for x in scored_rows if not x["excl"]),
                      key=lambda x: x["score"], reverse=True)

        # —— 行业中性:每行业上限 ceil(topN*indCap)——
        cap_per = max(1, math.ceil(body.topN * body.indCap)) if body.industryNeutral else 10 ** 9
        ind_count: Dict[str, int] = {}
        chosen: List[Dict[str, Any]] = []
        benched: List[Dict[str, Any]] = []
        for x in pool:
            if len(chosen) >= body.topN:
                benched.append(x)
                continue
            ind = x["s"]["ind"]
            c = ind_count.get(ind, 0)
            if c >= cap_per:
                benched.append({**x, "benchReason": "行业满额"})
                continue
            ind_count[ind] = c + 1
            chosen.append(x)

        # —— 分布统计(无仓位概念,按只数)——
        by_ind: Dict[str, int] = {}
        for x in chosen:
            by_ind[x["s"]["ind"]] = by_ind.get(x["s"]["ind"], 0) + 1
        n_c = len(chosen) or 1
        ind_dist = sorted(
            ({"ind": k, "n": v, "frac": v / n_c} for k, v in by_ind.items()),
            key=lambda d: d["n"], reverse=True,
        )
        # #3 去 mock:combIC = 复合因子 vs 前向收益的**真 rank-IC**(替代旧的静态
        #    FACTOR_DEFS["ic"] 加权和);样本不足/算不出 → 诚实降级 None(前端显示「—」)。
        comb_ic = None
        if comp_ts is not None:
            try:
                from financial_analyst.factors.eval.report import forward_simple_returns
                from financial_analyst.factors.eval.ic import ic_analysis
                ct = comp_ts.dropna()
                _fwd = forward_simple_returns(panel, 5)
                _icr = ic_analysis(ct, _fwd.reindex(ct.index))
                _v = _icr.rank_ic_mean
                comb_ic = round(float(_v), 4) if (_v == _v) else None
            except Exception:  # noqa: BLE001 — 算不出诚实给 None,绝不回退装饰常数
                comb_ic = None
        avg_pct = (sum(x["pct"] for x in chosen) / len(chosen)) if chosen else 0

        return JSONResponse({
            "ok": True,
            "universe": universe,
            "date": str(pd.Timestamp(screen_dt).date()),
            "chosen": chosen,
            "benched": benched[:40],
            "pool": [{"s": x["s"]} for x in pool],       # UI 仅用 .length,瘦身
            "scored": [{"s": x["s"]} for x in scored_rows],
            "stat": {"n": len(chosen), "indDist": ind_dist,
                     "combIC": comb_ic, "avgPct": round(avg_pct, 1), "newsDist": None},
            "unsupported_factors": unsupported,
            "unsupported_excl": unsupported_excl,
        })

    @router.post("/llm")
    async def screen_llm(body: LLMIn):
        """L4/L5 定性点评 —— 真 LLM(deepseek)。失败诚实返回 ok:False。"""
        from guanlan_v2.screen.llm import screen_commentary

        res = await screen_commentary(body.final, body.market)
        return JSONResponse(res)

    @router.post("/pick")
    async def screen_pick(body: PickIn):
        """LLM 选因子(替换前端正则)。失败 ok:False → 前端回落本地正则。"""
        from guanlan_v2.screen.llm import pick_factors

        return JSONResponse(await pick_factors(body.prompt))

    @router.post("/phrase")
    async def screen_phrase(body: PhraseIn):
        """LLM 一句话调约束(替换前端正则)。失败 ok:False → 前端回落本地正则。"""
        from guanlan_v2.screen.llm import parse_phrase

        return JSONResponse(await parse_phrase(body.phrase, body.cfg))

    @router.post("/news")
    async def screen_news(body: NewsIn):
        """C 真消息面:实时东方财富快讯(只读,不写引擎)+ deepseek 情绪。无相关快讯不编造。"""
        from guanlan_v2.screen.news import news_sentiment

        return JSONResponse(await news_sentiment(body.codes))

    @router.post("/regen")
    def screen_regen(body: RegenIn):
        """「拉取最新数据」:后台子进程跑引擎原生 compute.regen 再生三产物,立即返回(异步,
        v4 LGB ~5min);完成自动热加载缓存(无需重启)。单飞:已在跑 → ok:False/already_running。"""
        import time as _t
        import threading as _th

        with _REGEN_LOCK:
            busy = bool(_REGEN_STATE.get("running"))
            if not busy:
                _REGEN_STATE.update(
                    running=True, phase="starting", label="启动子进程…", step=0,
                    started_at=_t.time(), ended_at=None, ok=None, error=None,
                    end=(body.end or None), new_date=None, lines=[],
                )
        if busy:
            return JSONResponse({"ok": False, "reason": "already_running",
                                 "state": _regen_public_state()})
        # _safe 兜底外层异常;_run_regen_subprocess 内 finally 必清 running
        _th.Thread(
            target=lambda: _safe(lambda: _run_regen_subprocess(body.end or None)),
            daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "state": _regen_public_state()})

    @router.get("/regen/status")
    def screen_regen_status():
        """轮询再生进度/结果(前端「拉取最新数据」按钮用)。"""
        return JSONResponse({"ok": True, "state": _regen_public_state()})

    @router.get("/models")
    def screen_models():
        from guanlan_v2.screen.model_registry import list_variants, get_default_model
        dflt = get_default_model()
        vs = list_variants()
        for v in vs:
            v["is_default"] = (v.get("id") == dflt)
        return JSONResponse({"ok": True, "variants": vs, "default_model": dflt})

    @router.get("/picks")
    def screen_picks(snapshot_only: int = 0, limit: int = 50):
        """picks 档案读回(P0;P1 收益跟踪/前端将来消费)。坏行已在 read_picks 内跳过。"""
        from guanlan_v2.screen import picks as _picks
        items = _picks.read_picks(snapshot_only=bool(snapshot_only), limit=limit)
        return JSONResponse({"ok": True, "items": items, "n": len(items),
                             "path": str(_picks.PICKS_PATH)})

    @router.get("/model/ranking")
    def screen_model_ranking(id: str):
        """某 registry 模型最新截面 (code→lgb_pct),供工作流 model 节点下游求值/回测。"""
        import pandas as pd
        from guanlan_v2.screen.model_registry import variant_ranking_path
        p = variant_ranking_path(id)
        if not p.exists():
            return JSONResponse({"ok": False, "reason": "模型不存在"})
        df = pd.read_parquet(p)
        last = df[df["date"] == df["date"].max()] if "date" in df.columns and len(df) else df.iloc[0:0]
        if last.empty:
            return JSONResponse({"ok": False, "reason": "排名为空"})
        return JSONResponse({"ok": True, "date": str(last["date"].iloc[0]),
            "rows": [{"code": str(r.code), "score": float(r.lgb_pct)} for r in last.itertuples()]})

    @router.get("/model/status")
    def screen_model_status():
        return JSONResponse({"ok": True, "state": _model_public_state()})

    @router.get("/base_features")
    def screen_base_features():
        from guanlan_v2.strategy.compute.model_train import _base_feature_names
        try:
            return JSONResponse({"ok": True, "features": _base_feature_names()})
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(e).__name__}: {e}", "features": []})

    @router.post("/model/train")
    def screen_model_train(body: dict = Body(default={})):
        """模型工坊一键训练:校验非空选择(因子/基础特征)→ 单飞抢锁 → 起子进程训练新变体,
        立即返回(异步);完成后落 models/<variant_id>。空选择优先于运行锁拒绝(返回各自原因)。"""
        import time as _t, uuid
        name = str(body.get("name") or "").strip() or "未命名变体"
        fids, base = list(body.get("factor_ids") or []), list(body.get("base_features") or [])
        if not fids and not base:
            return JSONResponse({"ok": False, "reason": "至少选 1 个因子"})
        with _MODEL_LOCK:
            already = _MODEL_STATE["running"]
            if not already:
                vid = "m_" + uuid.uuid4().hex[:10]
                _MODEL_STATE.update({"running": True, "phase": "starting", "label": "启动训练子进程…",
                    "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                    "variant_id": vid, "lines": []})
        if already:
            return JSONResponse({"ok": False, "reason": "已有训练在跑", "state": _model_public_state()})
        spec = {"variant_id": vid, "name": name, "factor_ids": fids, "base_features": base,
                "universe": str(body.get("universe") or "all"), "created": _time_iso()}
        _threading.Thread(target=lambda: _safe(lambda: _run_model_train_subprocess(spec)), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "variant_id": vid, "state": _model_public_state()})

    @router.post("/model/delete")
    def screen_model_delete(body: dict = Body(default={})):
        from guanlan_v2.screen.model_registry import delete_variant
        try:
            delete_variant(str(body.get("id") or "")); return JSONResponse({"ok": True})
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": str(e)})

    @router.post("/model/default")
    def screen_model_default(body: dict = Body(default={})):
        from guanlan_v2.screen.model_registry import set_default_model, get_default_model
        try:
            set_default_model(str(body.get("id") or "") or None)
            return JSONResponse({"ok": True, "default": get_default_model()})
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": str(e)})

    @router.post("/model/validate")
    def screen_model_validate(body: dict = Body(default={})):
        import time as _t
        from guanlan_v2.strategy.compute import cpcv
        mid = str(body.get("id") or "prod"); tier = str(body.get("tier") or "quick")
        if tier == "quick":
            try:
                return JSONResponse({"ok": True, "result": cpcv.quick_validate(model_id=mid)})
            except Exception as e:  # noqa: BLE001 — 引擎/坏parquet → 诚实 ok:false 而非 500
                return JSONResponse({"ok": False, "note": f"{type(e).__name__}: {e}"})
        with _VALIDATE_LOCK:
            already = _VALIDATE_STATE["running"]
            if not already:
                _VALIDATE_STATE.update({"running": True, "phase": "starting", "label": "启动严格验证…",
                    "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                    "model_id": mid, "lines": []})
        if already:
            return JSONResponse({"ok": False, "reason": "已有验证在跑", "state": _validate_public_state()})
        spec = {"model_id": mid, "n_groups": int(body.get("n_groups") or 6), "k": int(body.get("k") or 2),
                "purge": int(body.get("purge") or 5), "embargo": int(body.get("embargo") or 5)}
        _threading.Thread(target=lambda: _run_validate_subprocess(spec), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "model_id": mid, "state": _validate_public_state()})

    @router.get("/model/validate/status")
    def screen_model_validate_status():
        from guanlan_v2.strategy import model_health as mh
        st = _validate_public_state()
        if not st["running"] and st.get("model_id"):
            st["result"] = mh.load_cpcv_summary(st["model_id"])
        return JSONResponse({"ok": True, "state": st})

    # 启动即后台预热 financials 索引(get_default_loader 非单例 + 冷建 ~8s);
    # daemon 线程不阻塞启动/健康检查,首个 /screen/run 命中已建好的常驻索引。
    import threading
    threading.Thread(
        target=lambda: _safe(lambda: _screen_loader().fetch_financials("SH600519")),
        daemon=True).start()

    return router


def _safe(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass
