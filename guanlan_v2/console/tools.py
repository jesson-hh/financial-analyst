"""帷幄控制台工具。

注册机制:register_console_tools() 把 Tool 字面量追加进 buddy TOOL_REGISTRY
(engine 懒导入,仅 9999 进程调用);CONSOLE_ALLOWED 是 run_turn 的显式白名单。
会话上下文经 ContextVar 传入(asyncio.to_thread 会复制 context)。
纯逻辑(plan 校验/指标摘要/artifact 信封)保持可单测、不依赖引擎。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re as _re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

CTX_SID: contextvars.ContextVar = contextvars.ContextVar("weiwo_sid", default=None)
CTX_STORE: contextvars.ContextVar = contextvars.ContextVar("weiwo_store", default=None)

# 复盘模式(阶段1 自学回路):None=正常路径(真落盘);"monitor"=复盘干跑(写工具不落盘只回"将写入");
# "enforce"=复盘真写。仅 _run_review_bg 的 fork agent 在其 task 上下文里 set 它;主对话 turn 不动
# (主 turn 的 ContextVar 在 spawn 复盘前已 reset,复盘 task 自起自落,不串味)。
CTX_REVIEW_MODE: contextvars.ContextVar = contextvars.ContextVar("weiwo_review_mode", default=None)

# 复盘 fork 的工具白名单(借鉴 Hermes 式两工具沙箱:只能写记忆 + draft 经验卡)。
# run_turn(allowed_tools=...) 双门物理保证它调不了任何第三个工具(creed/α/落子/factorlib_save
# 等敏感写工具都不在此集合 → allowed_tools 门硬拦,见 engine agent.py 执行兜底)。
REVIEW_ALLOWED = {"ww_memory_write", "ww_cards_save"}

_VALID_STATUS = {"pending", "in_progress", "done"}
_REPORTS_STORE = Path(__file__).resolve().parents[1] / "reports" / "store"
_MEMORY_PATH = Path(__file__).resolve().parents[2] / "var" / "console" / "memory.md"
_MEMORY_LOCK = threading.Lock()
# 阶段2 记忆有界化:单条记忆硬上限(字符)。一处定义,memory_write_impl 对所有写一律 cap;
# 复盘路径的去换行消毒也复用此常量,与通用 cap 同源(避免两条互相冲突的截断)。
_MEMORY_MAX_LINE = 280
# 阶段2 收敛接线:全局记忆归档文件 + 自动收敛触发阈值(行数)。memory_write_impl 写 global
# 超此行数即在 _MEMORY_LOCK 内调 curator.consolidate_memory(常驻 keyed 永不归档)。
_ARCHIVE_PATH = _MEMORY_PATH.parent / "memory.archive.md"
_CURATOR_TRIGGER_LINES = 120


def _session_notes_path(sid: str) -> Path:
    """会话级笔记:与会话事件同树 <store>/sessions/<sid>/notes.md(delete_session 连笔记一起删)。
    优先取 CTX_STORE 的 sessions_dir(store 可注入 root);无 store 上下文锚定仓内 var/console。"""
    st = CTX_STORE.get(None)
    base = st.sessions_dir if st is not None else _MEMORY_PATH.parent / "sessions"
    return base / sid / "notes.md"


_CODE_RE = _re.compile(r"^(SH|SZ|BJ)\d{6}$")
_SHOW_PAGES = {"screen": "选股", "factor": "工作流", "cards": "经验卡", "graph": "研究图谱", "seats": "落子"}


# ── artifact 信封(spec §4.1)──
def artifact(kind: str, page: Optional[str] = None, channel: Optional[str] = None,
             payload: Optional[Dict[str, Any]] = None, ref: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "page": page, "channel": channel,
            "payload": payload or {}, "ref": ref}


# ── plan.update(TodoWrite 式整单替换)──
def plan_update_impl(todos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    store = CTX_STORE.get()
    sid = CTX_SID.get()
    if store is None or sid is None:
        return {"ok": False, "reason": "无会话上下文(只能在帷幄会话内调用)"}
    norm: List[Dict[str, Any]] = []
    for i, t in enumerate(todos or []):
        if not isinstance(t, dict):
            return {"ok": False, "reason": f"第{i + 1}项不是对象"}
        text = str(t.get("text", "")).strip()
        if not text:
            return {"ok": False, "reason": f"第{i + 1}项缺 text"}
        status = str(t.get("status", "pending"))
        if status not in _VALID_STATUS:
            return {"ok": False, "reason": f"第{i + 1}项 status 非法: {status}(允许 pending/in_progress/done)"}
        norm.append({"id": t.get("id") or f"t{i + 1}", "text": text, "status": status})
    try:
        store.set_plan(sid, norm)
    except KeyError:
        return {"ok": False, "reason": "会话不存在"}
    return {"ok": True, "n": len(norm), "todos": norm}


# ── 指标摘要(给 LLM 看的一行人话)──
def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def summarize_factor_report(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"因子分析失败: {r.get('reason', '未知原因')}"
    h = r.get("headline_ic") or {}
    parts = [f"RankIC {h.get('rank_ic')}", f"RankICIR {h.get('rank_icir')}",
             f"期数 {r.get('n_dates')}"]
    oos = r.get("oos") or {}
    if oos.get("enabled"):
        o = (oos.get("oos") or {}).get("rank_ic")
        i = (oos.get("is") or {}).get("rank_ic")
        v = oos.get("verdict") or ""
        parts.append(f"OOS RankIC {o}(IS {i}){(' · ' + str(v)) if v else ''}")
    return "因子分析完成: " + " · ".join(str(p) for p in parts)


def summarize_backtest(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"回测失败: {r.get('reason', '未知原因')}"
    bt = r.get("backtest") or {}
    k = bt.get("portfolio_kpi") or {}
    return ("回测完成: 净年化 " + _pct(bt.get("net_ann"))
            + f" · Sharpe {k.get('sharpe')} · 最大回撤 {_pct(k.get('max_drawdown'))}"
            + f" · 胜率 {_pct(k.get('win_rate'))}")


def summarize_screen(r: Dict[str, Any]) -> str:
    if not r.get("ok"):
        return f"选股失败: {r.get('reason', '未知原因')}"
    rows = r.get("chosen") or []
    head = []
    for row in rows[:5]:
        s = row.get("s") or {}
        head.append(f"{s.get('name')}({s.get('code')}) {s.get('rating', '')}")
    return f"选股完成: 入选 {len(rows)} 只,前5 = " + ";".join(head)


# ── 同进程自 HTTP(handler 是工厂闭包不可导入的模块用;跑在 to_thread,不堵事件循环)──
def _self_post(path: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    port = os.environ.get("GUANLAN_PORT", "9999")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {body or e.reason}")


def _self_get(path: str, timeout: int = 30) -> Dict[str, Any]:
    port = os.environ.get("GUANLAN_PORT", "9999")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {body or e.reason}")


def _two_years_ago() -> str:
    return (date.today() - timedelta(days=365 * 2)).isoformat()


# ── 引擎/模块桥(独立小函数便于 monkeypatch;懒导入 workflow 重模块)──
def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(workflow 模块级助手返回 JSONResponse)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _call_factor_report2(**kw: Any) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorReport2In, _factor_report2
    return _resp_json(_factor_report2(FactorReport2In(**kw)))


def _call_backtest_vector(**kw: Any) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import BacktestVectorIn, _backtest_vector
    return _resp_json(_backtest_vector(BacktestVectorIn(**kw)))


def factor_analyze_impl(expr: str, universe: str = "csi300", freq: str = "month",
                        oos_frac: float = 0.3, start: Optional[str] = None,
                        end: Optional[str] = None) -> Dict[str, Any]:
    expr = (expr or "").strip()
    if not expr:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    try:
        r = _call_factor_report2(expr_or_name=expr, universe=universe, freq=freq,
                                 oos_frac=oos_frac, start=start, end=end)
    except Exception as e:
        return {"ok": False, "content": f"因子分析调用失败: {e}", "artifact": None}
    return {"ok": bool(r.get("ok")), "content": summarize_factor_report(r),
            "artifact": artifact("ic_report", page="factor", channel="workflow",
                                 payload={"expr": expr, "name": f"因子 {expr[:24]}"}),
            "raw": r}


def backtest_impl(expr: str, universe: str = "csi300", topn: int = 30,
                  weighting: str = "equal", rebalance: str = "month",
                  oos_frac: float = 0.3, start: Optional[str] = None,
                  end: Optional[str] = None) -> Dict[str, Any]:
    expr = (expr or "").strip()
    if not expr:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    try:
        r = _call_backtest_vector(features=[expr], universe=universe, topn=topn,
                                  weighting=weighting, rebalance=rebalance,
                                  oos_frac=oos_frac,
                                  start=start or _two_years_ago(), end=end)
    except Exception as e:
        return {"ok": False, "content": f"回测调用失败: {e}", "artifact": None}
    return {"ok": bool(r.get("ok")), "content": summarize_backtest(r),
            "artifact": artifact("backtest_report", page="factor", channel="workflow",
                                 payload={"expr": expr, "name": f"回测 {expr[:24]}"}),
            "raw": r}


def screen_impl(factors: Optional[List[Any]] = None, pool: str = "all",
                blend: float = 1.0, topN: int = 20, liqMin: float = 5.0,
                mlStatus: Optional[List[str]] = None,
                industryNeutral: Optional[bool] = None, indCap: Optional[float] = None,
                exclST: Optional[bool] = None, exclHalt: Optional[bool] = None,
                exclLimit: Optional[bool] = None, exclNew: Optional[bool] = None,
                model: Optional[str] = None,
                snapshot: Optional[bool] = None, note: Optional[str] = None) -> Dict[str, Any]:
    norm_factors: List[Dict[str, Any]] = []
    for i, f in enumerate(factors or []):
        if isinstance(f, str):
            f = {"id": f}
        if not isinstance(f, dict):
            return {"ok": False, "content": f"factors 第{i + 1}项格式非法(应为 {{id,w}} 或 id 字符串)",
                    "artifact": None}
        fid = f.get("id")
        if not fid:
            return {"ok": False, "content": f"factors 第{i + 1}项格式非法(应为 {{id,w}} 或 id 字符串)",
                    "artifact": None}
        w = f.get("w")
        try:
            w = 1.0 if w is None else float(w)
        except Exception:
            return {"ok": False, "content": f"factors 第{i + 1}项 w 非数值", "artifact": None}
        norm_factors.append({"id": str(fid), "w": w})
    # 默认与选股页 defaultCfg 同源(pool=all/blend=1.0/liqMin=5),保证 headless 摘要与可见 UI 同口径
    # (掌控审计 2026-06-15:原默认 pool=csi300/blend=0.6/liqMin=0 与 UI 分叉,导致「报的≠看到的」)。
    # 约束类字段仅在显式提供时下送(None→后端默认,后端默认与 UI defaultCfg 一致)。
    cfg: Dict[str, Any] = {"factors": norm_factors, "pool": pool, "blend": blend,
                           "topN": topN, "liqMin": liqMin}
    for _k, _v in (("mlStatus", mlStatus), ("industryNeutral", industryNeutral),
                   ("indCap", indCap), ("exclST", exclST), ("exclHalt", exclHalt),
                   ("exclLimit", exclLimit), ("exclNew", exclNew), ("model", model),
                   ("snapshot", snapshot), ("note", note)):
        if _v is not None:
            cfg[_k] = _v
    try:
        r = _self_post("/screen/run", cfg)
    except Exception as e:
        return {"ok": False, "content": f"选股调用失败: {e}", "artifact": None}
    # 诚实:后端若回报未识别因子(回退路径带 unsupported_factors)→ 透传给 agent,避免「错而不报」
    _unsup = r.get("unsupported_factors") if isinstance(r, dict) else None
    _unsup_line = ("\n⚠ 未识别因子(已忽略,未参与混合): " + ", ".join(str(x) for x in _unsup)
                   + "(用 ww_screen_factors 查合法 id)") if _unsup else ""
    # 诚实:回报实际所用 v4 模型;请求了变体却回落 prod(变体不可用)时显式告警,绝不假装用了变体
    _used = r.get("model") if isinstance(r, dict) else None
    _model_line = ""
    if model and _used and str(_used) != str(model):
        _model_line = f"\n⚠ 变体 {model} 不可用,已回落生产 v4(prod)"
    elif _used and str(_used) != "prod":
        _model_line = f"\n模型: 变体 {_used}"
    _pr = r.get("picks_recorded") if isinstance(r, dict) else None
    _picks_line = ""
    if _pr is True:
        _picks_line = "\n✓ picks 已落档" + ("(正式选股 snapshot)" if cfg.get("snapshot") else "")
    elif _pr is False:
        _picks_line = "\n⚠ picks 档案落盘失败(不影响本次选股结果)"
    return {"ok": bool(r.get("ok")), "content": summarize_screen(r) + _unsup_line + _model_line + _picks_line,
            "artifact": artifact("screen_result", page="screen", channel="screen",
                                 payload={"cfg": cfg}),
            "raw": r}


def screen_factors_impl(family: str = "", supported_only: bool = True) -> Dict[str, Any]:
    """列出选股因子目录(/screen/factors)的合法 id,供 ww_screen_run 的 factors 取用。
    传错 id 会被后端静默忽略 → 先查目录再选因子。"""
    try:
        r = _self_get("/screen/factors")
    except Exception as e:
        return {"ok": False, "content": f"因子目录拉取失败: {e}", "artifact": None}
    facs = r.get("factors") or []
    fam = (family or "").strip()
    rows = [f for f in facs
            if (not supported_only or f.get("supported"))
            and (not fam or f.get("family") == fam)]
    if not rows:
        avail = sorted({str(f.get("family") or "") for f in facs if f.get("family")})
        hint = ("(无匹配,可选族: " + " / ".join(avail) + ")") if fam else "(目录为空)"
        return {"ok": True, "content": hint, "artifact": None, "raw": {"n": 0}}
    by_fam: Dict[str, List[str]] = {}
    for f in rows:
        ic = f.get("ic")
        tag = f"{f.get('id')}({f.get('short', '')}{'' if ic is None else f' IC{float(ic):+.3f}'})"
        by_fam.setdefault(str(f.get("family") or "其他"), []).append(tag)
    lines = [f"【{k}】 " + " · ".join(v) for k, v in by_fam.items()]
    head = (f"选股因子目录(可用作 ww_screen_run.factors 的 id),共 {len(rows)} 个"
            + (f"·族「{fam}」" if fam else "") + ":\n")
    return {"ok": True, "content": head + "\n".join(lines), "artifact": None,
            "raw": {"n": len(rows), "ids": [f.get("id") for f in rows]}}


def model_list_impl(include_draft: bool = False) -> Dict[str, Any]:
    """列出已训练的 v4 变体(供 ww_screen_run 的 model 取 id;生产 v4 隐含=prod)。"""
    try:
        r = _self_get("/screen/models" + ("?include_draft=1" if include_draft else ""))
    except Exception as e:
        return {"ok": False, "content": f"变体列表拉取失败: {e}", "artifact": None}
    vs = r.get("variants") or []
    dflt = r.get("default_model")
    if not vs:
        return {"ok": True, "artifact": None, "raw": {"n": 0},
                "content": "暂无训练好的 v4 变体(生产 v4=model 省略或 prod)。"
                           "用 ww_model_train 选基础特征+库因子训练一个。"}
    lines = []
    for m in vs:
        oi = m.get("oos_ic")
        oid = "" if oi is None else f" 留出OOS IC {float(oi):+.3f}"
        uns = m.get("unsupported_factors") or []
        unl = f" ⚠{len(uns)}未用" if uns else ""
        dr = " ⚠draft未过门" if m.get("status") == "draft" else ""
        star = " ★默认" if m.get("id") == dflt else ""
        lines.append(f"{m.get('id')}「{m.get('name')}」· {m.get('n_features', '—')}特征{oid}{unl}{dr}{star}")
    dl = f"(当前默认 = {dflt})" if dflt else "(当前默认 = 生产 prod)"
    head = f"已训练 v4 变体 {len(vs)} 个{dl}(ww_screen_run 传 model=<id> 用其选股;省略=默认):\n"
    return {"ok": True, "content": head + "\n".join(lines), "artifact": None,
            "raw": {"n": len(vs), "ids": [m.get("id") for m in vs], "default": dflt}}


def model_train_impl(name: str = "", factor_ids: Optional[List[str]] = None,
                     base_features: Optional[List[str]] = None,
                     universe: str = "all") -> Dict[str, Any]:
    """训练一个 v4 变体(选基础特征+库因子)。后台子进程 ~4min,完成后 ww_model_list 可见。
    base_features 省略=默认全部基础特征(与工坊 UI 一致);要纯库因子模型显式传 base_features=[]。
    生产 v4 全程不动。需用户确认。"""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "content": "请给变体起个名(name)", "artifact": None}
    fids = [str(x) for x in (factor_ids or [])]
    base = base_features
    if base is None:                       # 省略 → 取全部基础特征(对齐工坊 UI 默认全勾)
        try:
            bf = _self_get("/screen/base_features")
            base = (bf.get("features") or []) if isinstance(bf, dict) else []
        except Exception as e:
            return {"ok": False, "content": f"基础特征拉取失败: {e}", "artifact": None}
    base = [str(x) for x in base]
    if not fids and not base:
        return {"ok": False, "content": "至少选 1 个库因子或基础特征", "artifact": None}
    try:
        r = _self_post("/screen/model/train",
                       {"name": nm, "factor_ids": fids, "base_features": base, "universe": universe})
    except Exception as e:
        return {"ok": False, "content": f"训练启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"训练未启动: {r.get('reason')}", "artifact": None}
    vid = r.get("variant_id")
    return {"ok": True, "artifact": None, "raw": r,
            "content": f"已启动训练变体「{nm}」(id={vid}·{len(base)}基础特征+{len(fids)}库因子)。"
                       f"后台 ~4min,完成后 ww_model_list 查看、ww_screen_run model={vid} 选股。生产 v4 不受影响。"}


def _recipe_features_from_factor_ids(factor_ids: List[str]) -> "tuple[List[str], List[str]]":
    """Map screen factor ids to workflow-replayable zoo expressions."""
    if not factor_ids:
        return [], []
    r = _self_get("/screen/factors")
    rows = r.get("factors") or []
    by_id = {str(f.get("id")): f for f in rows if f.get("id")}
    exprs: List[str] = []
    missing: List[str] = []
    for fid in factor_ids:
        f = by_id.get(str(fid))
        expr = str((f or {}).get("expr") or "").strip()
        if not f or not expr:
            missing.append(str(fid))
        else:
            exprs.append(expr)
    return exprs, missing


def model_promote_impl(name: str = "", features: Optional[List[str]] = None,
                       factor_ids: Optional[List[str]] = None, kind: str = "lightgbm",
                       universe: str = "csi800", label: str = "fwd_ret",
                       fwd_days: int = 5, start: str = "2022-01-01",
                       end: str = "", params: Optional[Dict[str, Any]] = None,
                       benchmark: str = "", leader: str = "",
                       wait: bool = False, poll_seconds: float = 5.0,
                       timeout_seconds: float = 900.0) -> Dict[str, Any]:
    """Promote a workflow-replayable model variant with recipe, so strict CPCV can retrain it."""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "content": "请给可重训模型起名(name)", "artifact": None}
    raw_features = [str(x).strip() for x in (features or []) if str(x).strip()]
    fids = [str(x).strip() for x in (factor_ids or []) if str(x).strip()]
    try:
        factor_exprs, missing = _recipe_features_from_factor_ids(fids)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"因子目录读取失败: {e}", "artifact": None}
    if missing:
        return {"ok": False, "content": "这些 factor_ids 无法映射为可复算表达式: " + ", ".join(missing),
                "artifact": None, "raw": {"missing": missing}}
    recipe_features: List[str] = []
    seen = set()
    for expr in raw_features + factor_exprs:
        if expr and expr not in seen:
            seen.add(expr)
            recipe_features.append(expr)
    if not recipe_features:
        return {"ok": False, "content": "至少传 1 个 features 表达式或 factor_ids", "artifact": None}
    bench = (benchmark or "").strip()
    lead = (leader or "").strip()
    if not bench and any("idx_ret" in expr for expr in recipe_features):
        bench = "csi300"
    if any("ref_ret" in expr for expr in recipe_features) and not lead:
        return {"ok": False, "content": "recipe.features 引用了 ref_ret, 需要同时传 leader 龙头代码",
                "artifact": None}
    recipe: Dict[str, Any] = {
        "features": recipe_features,
        "label": (label or "fwd_ret").strip() or "fwd_ret",
        "fwd_days": int(fwd_days or 5),
        "universe": (universe or "csi800").strip() or "csi800",
        "start": (start or "2022-01-01").strip() or "2022-01-01",
        "params": dict(params or {}),
    }
    if (end or "").strip():
        recipe["end"] = str(end).strip()
    if bench:
        recipe["benchmark"] = bench
    if lead:
        recipe["leader"] = lead
    body = {"name": nm, "kind": (kind or "lightgbm").strip() or "lightgbm", "recipe": recipe}
    try:
        r = _self_post("/model/promote", body)
    except Exception as e:
        return {"ok": False, "content": f"可重训模型入库启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"可重训模型未启动: {r.get('reason')}", "artifact": None, "raw": r}
    vid = r.get("variant_id")
    if wait:
        import time as _time
        deadline = _time.time() + float(timeout_seconds or 900.0)
        state: Dict[str, Any] = {}
        done = False
        while _time.time() <= deadline:
            try:
                s = _self_get("/model/promote/status")
            except Exception as e:
                return {"ok": False, "content": f"入库状态读取失败: {e}", "artifact": None}
            state = s.get("state") or {}
            if not state.get("running") and state.get("phase") == "done":
                done = True
                break
            if poll_seconds:
                _time.sleep(float(poll_seconds))
        if not done:
            return {"ok": False, "artifact": None, "raw": {"state": state},
                    "content": f"入库轮询超时 {vid}:后端可能仍在跑,稍后 ww_model_list 查"}
        if not state.get("ok"):
            return {"ok": False, "artifact": None, "raw": {"state": state},
                    "content": f"入库失败 {vid}: {state.get('error')}"}
        mrow = None
        try:
            mr = _self_get("/screen/models?include_draft=1")
            mrow = next((m for m in (mr.get("variants") or []) if m.get("id") == vid), None)
        except Exception:  # noqa: BLE001
            mrow = None
        g = (mrow or {}).get("gate") or {}
        if (mrow or {}).get("status") == "draft":
            return {"ok": True, "artifact": None, "raw": {"variant": mrow},
                    "content": (f"入库完成但未过门槛:oos_ic {g.get('oos_ic')} < 门槛 "
                                f"{g.get('min_oos_ic')},已落 draft 区(不进正式列表、"
                                f"不能设默认;ww_model_list include_draft=true 可见)")}
        oi = (mrow or {}).get("oos_ic")
        return {"ok": True, "artifact": None, "raw": {"variant": mrow},
                "content": f"入库完成 {vid}:留出 OOS IC {oi}"
                           + (f"(过门槛 {g.get('min_oos_ic')})" if g else "")
                           + f"。ww_model_validate id={vid} tier=strict 可跑 CPCV。"}
    return {"ok": True, "artifact": None,
            "raw": {"response": r, "recipe": recipe, "factor_ids": fids},
            "content": f"已启动可重训模型入库「{nm}」id={vid}。recipe 已随模型保存；完成后 ww_model_validate id={vid} tier=strict 可跑 CPCV。"}


def _model_validate_summary(result: Dict[str, Any]) -> str:
    if not result:
        return "暂无验证结果"
    if not result.get("ready"):
        return str(result.get("note") or "验证未就绪")
    dsr = result.get("dsr")
    n_paths = result.get("n_paths")
    ic_med = (result.get("ic_dist") or {}).get("median")
    sh_med = (result.get("sharpe_dist") or {}).get("median")
    parts = ["ready=True", f"路径 {n_paths}" if n_paths is not None else ""]
    if dsr is not None:
        parts.append(f"DSR {float(dsr):.3f}")
    if ic_med is not None:
        parts.append(f"IC中位 {float(ic_med):+.4f}")
    if sh_med is not None:
        parts.append(f"Sharpe中位 {float(sh_med):+.3f}")
    return " · ".join([p for p in parts if p])


def model_validate_impl(id: str = "", tier: str = "strict", n_groups: int = 6, k: int = 2,
                        purge: int = 5, embargo: int = 5, wait: bool = False,
                        poll_seconds: float = 4.0, timeout_seconds: float = 600.0) -> Dict[str, Any]:
    """Run quick/strict CPCV validation for a model variant."""
    vid = (id or "").strip()
    if not vid:
        return {"ok": False, "content": "请给模型 id(ww_model_list 查询)", "artifact": None}
    tier = (tier or "strict").strip().lower()
    body: Dict[str, Any] = {"id": vid, "tier": tier}
    if tier != "quick":
        body.update({"tier": "strict", "n_groups": int(n_groups or 6), "k": int(k or 2),
                     "purge": int(purge or 5), "embargo": int(embargo or 5)})
    try:
        r = _self_post("/screen/model/validate", body)
    except Exception as e:
        return {"ok": False, "content": f"CPCV 验证启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"CPCV 验证未启动: {r.get('reason') or r.get('note')}", "artifact": None, "raw": r}
    if tier == "quick":
        result = r.get("result") or {}
        return {"ok": True, "artifact": None, "raw": {"response": r, "result": result},
                "content": f"快验完成 {vid}: {_model_validate_summary(result)}"}
    if not wait:
        return {"ok": True, "artifact": None, "raw": r,
                "content": f"已启动 strict CPCV 验证 {vid}。稍后调用 ww_model_validate id={vid} tier=strict wait=true 读取结果。"}
    import time as _time
    deadline = _time.time() + float(timeout_seconds or 600.0)
    state: Dict[str, Any] = {}
    while _time.time() <= deadline:
        try:
            s = _self_get("/screen/model/validate/status")
        except Exception as e:
            return {"ok": False, "content": f"CPCV 状态读取失败: {e}", "artifact": None, "raw": {"response": r}}
        state = s.get("state") or {}
        if not state.get("running") and state.get("phase") == "done":
            result = state.get("result") or {}
            ok = bool(state.get("ok"))
            return {"ok": ok, "artifact": None,
                    "raw": {"response": r, "state": state, "result": result},
                    "content": f"strict CPCV 完成 {vid}: {_model_validate_summary(result)}" if ok
                    else f"strict CPCV 失败 {vid}: {state.get('error') or _model_validate_summary(result)}"}
        if poll_seconds:
            _time.sleep(float(poll_seconds))
    return {"ok": False, "artifact": None, "raw": {"response": r, "state": state},
            "content": f"strict CPCV 轮询超时 {vid}: 后端可能仍在运行，请稍后再查"}


def model_delete_impl(id: str = "") -> Dict[str, Any]:
    """删一个 v4 变体(生产 prod 不可删)。删的若是当前默认变体,后端连带回落 prod。需用户确认。"""
    vid = (id or "").strip()
    if not vid:
        return {"ok": False, "content": "请给要删除的变体 id(ww_model_list 查)", "artifact": None}
    try:
        r = _self_post("/screen/model/delete", {"id": vid})
    except Exception as e:
        return {"ok": False, "content": f"删除失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"未删除: {r.get('reason')}", "artifact": None}
    try:
        left = (_self_get("/screen/models").get("variants") or [])
    except Exception:
        left = []
    tail = ("剩余变体: " + "、".join(m.get("id") for m in left)) if left else "已无自训变体(默认=生产 prod)。"
    return {"ok": True, "artifact": None, "raw": {"id": vid, "left": [m.get("id") for m in left]},
            "content": f"已删除变体 {vid}。{tail}"}


def model_set_default_impl(id: str = "") -> Dict[str, Any]:
    """设为默认变体:之后选股页/ww_screen_run 不指定模型时缺省用它;id=prod/省略=清除回官方 prod。
    生产 prod 文件不动,随时可切回。需用户确认。"""
    vid = (id or "").strip()
    try:
        r = _self_post("/screen/model/default", {"id": vid})
    except Exception as e:
        return {"ok": False, "content": f"设置失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"未设置: {r.get('reason')}", "artifact": None}
    cur = r.get("default")
    msg = (f"已设默认变体 = {cur}(选股缺省用它,显式 model 仍优先;ww_model_set_default id=prod 可切回官方)。"
           if cur else "已清除默认变体,选股缺省回生产 prod。")
    return {"ok": True, "artifact": None, "raw": {"default": cur}, "content": msg}


# ── P0 §2: 闭环读取面 + regen 触发(全部薄壳,无新算法)─────────────────────

def ledger_state_impl() -> Dict[str, Any]:
    """实盘台账快照:组合持仓/已实现盈亏/胜率/MTM 权益(缺价诚实置空)。"""
    try:
        r = _self_get("/seats/ledger/state")
    except Exception as e:
        return {"ok": False, "content": f"台账读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"台账读取失败: {r.get('reason') or r}", "artifact": None, "raw": r}
    if not r.get("opened"):
        return {"ok": True, "content": "台账未开账(尚无实盘组合记录)。", "artifact": None, "raw": r}
    eq = r.get("equity")
    eq_line = (f"MTM权益 {eq:,.0f}(估值日 {r.get('equity_date')})"
               if isinstance(eq, (int, float)) else "MTM权益 缺价不可估(诚实置空)")
    wr = r.get("win_rate")
    wr_line = f"{float(wr) * 100:.0f}%" if isinstance(wr, (int, float)) else "—(无已了结)"
    pos_lines = []
    for p in (r.get("positions") or [])[:12]:
        upl = p.get("upl")
        tail = (f" 现价 {p.get('last_close')} 浮盈 {upl:+,.0f}"
                if isinstance(upl, (int, float)) else " 现价缺")
        pos_lines.append(f"{p.get('code')} {p.get('name', '')} 持 {p.get('qty')} 成本 {p.get('avg_cost')}{tail}")
    content = (f"实盘台账(开账 {r.get('start_date')} · 组合一本账):现金 {r.get('cash'):,.0f} · "
               f"持仓 {r.get('n_positions')} 只(估到价 {r.get('covered')})· {eq_line}\n"
               f"已实现盈亏 {r.get('realized'):+,.0f} · 已了结 {r.get('n_closed')} 笔 · 胜率 {wr_line}"
               + ("\n" + "\n".join(pos_lines) if pos_lines else ""))
    return {"ok": True, "content": content, "artifact": None, "raw": r}


def calibration_impl(horizon: int = 5) -> Dict[str, Any]:
    """置信度校准全表:各置信档的真实 N 日方向命中率(评估自己研判先看它)。"""
    hz = max(1, min(int(horizon or 5), 20))
    try:
        r = _self_get(f"/seats/calibration?horizon={hz}")
    except Exception as e:
        return {"ok": False, "content": f"校准读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"校准读取失败: {r.get('reason')}", "artifact": None, "raw": r}
    def _fmt(b: Dict[str, Any]) -> str:
        seg = b.get("bucket") or b.get("range") or b.get("label") or "?"
        n, hr = b.get("n"), b.get("hit_rate")
        hr_s = f"{float(hr) * 100:.0f}%" if isinstance(hr, (int, float)) else "—"
        low = "(样本不足)" if isinstance(n, int) and n < 5 else ""
        return f"{seg}: n={n} 命中 {hr_s}{low}"
    bks = r.get("buckets") or []
    content = (f"置信校准(horizon={r.get('horizon')}日):研判 {r.get('total_decides')} 条 · 成熟 {r.get('mature')} 条\n"
               + ("; ".join(_fmt(b) for b in bks) if bks else "(暂无成熟样本)")
               + f"\n口径: {r.get('note')}")
    return {"ok": True, "content": content, "artifact": None, "raw": r}


def seats_runs_impl(code: str = "", limit: int = 10) -> Dict[str, Any]:
    """落子回测 run 历史头(新在前;code 数字核匹配)。"""
    cap = max(1, min(int(limit or 10), 50))
    q = f"/seats/runs?limit={cap}" + (f"&code={(code or '').strip()}" if (code or "").strip() else "")
    try:
        r = _self_get(q)
    except Exception as e:
        return {"ok": False, "content": f"回测 run 列表读取失败: {e}", "artifact": None}
    runs = r.get("runs") or []
    if not runs:
        return {"ok": True, "content": "暂无落子回测 run 记录。", "artifact": None, "raw": r}
    lines = []
    for x in runs:
        seg = f"{x.get('run_id')} · {x.get('code')} · {str(x.get('ts', ''))[:16]}"
        if x.get("start") or x.get("end"):
            seg += f" · {x.get('start')}→{x.get('end')}"
        if x.get("n_buy") is not None:
            seg += f" · 买{x.get('n_buy')}/卖{x.get('n_sell')}/观{x.get('n_hold')}"
        lines.append(seg)
    return {"ok": True, "content": f"落子回测 run 头(近 {len(runs)} 条,新在前):\n" + "\n".join(lines),
            "artifact": None, "raw": r}


def model_health_impl() -> Dict[str, Any]:
    """模型体检:v4 产物新鲜度 + 市场宽度 as_of + 模型健康(vintage OOS IC/告警)。"""
    try:
        r = _self_get("/screen/health")
    except Exception as e:
        return {"ok": False, "content": f"模型体检读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"模型体检读取失败: {r.get('reason')}", "artifact": None, "raw": r}
    v4 = r.get("v4_ranking") or {}
    lines = [f"v4 排名产物: date {v4.get('date')} · {v4.get('rows')} 行 · 陈旧 {v4.get('stale_days')} 天"]
    mb = r.get("market_breadth")
    if mb:
        lines.append(f"市场宽度: as_of {mb.get('as_of')} · 阶段 {mb.get('stage')}")
    mh = r.get("model_health")
    if isinstance(mh, dict) and mh:
        for k, v in mh.items():          # load_health_summary 键随产物在场程度变化 → 按在场键渲染
            if v is not None and not isinstance(v, (list, dict)):
                lines.append(f"{k}: {v}")
        for k in ("alert", "trend", "vintage"):
            if isinstance(mh.get(k), (list, dict)) and mh.get(k):
                lines.append(f"{k}: {json.dumps(mh[k], ensure_ascii=False, default=str)[:300]}")
    else:
        lines.append("模型体检块: 无(产物缺失,诚实缺席)")
    return {"ok": True, "content": "模型体检:\n" + "\n".join(lines), "artifact": None, "raw": r}


def factor_tsic_impl(expr: str = "", code: str = "", codes: Optional[List[str]] = None,
                     universe: str = "", fwd_days: int = 20,
                     start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """个股时序 IC:逐票 Spearman(因子值ₜ, 自身未来收益ₜ)——单票/小池的正确口径。"""
    ex = (expr or "").strip()
    if not ex:
        return {"ok": False, "content": "请给因子表达式/注册名 expr(先用 ww_factor_fields 查合法字段)",
                "artifact": None}
    body: Dict[str, Any] = {"expr_or_name": ex, "fwd_days": int(fwd_days or 20)}
    cs = [str(c).strip() for c in ([code] if (code or "").strip() else (codes or [])) if str(c).strip()]
    if cs:
        body["codes"] = cs
    elif (universe or "").strip():
        body["universe"] = universe.strip()
    if start:
        body["start"] = start
    if end:
        body["end"] = end
    try:
        r = _self_post("/factor/tsic", body, timeout=300)
    except Exception as e:
        return {"ok": False, "content": f"时序IC计算失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"时序IC计算失败: {r.get('reason') or r.get('status')}",
                "artifact": None, "raw": {"summary": r.get("summary")}}
    s = r.get("summary") or {}
    rows = r.get("codes_tsic") or []
    per = "; ".join(f"{x.get('code')}: tsic {x.get('tsic'):+.4f}(n={x.get('n')})"
                    for x in rows[:5] if isinstance(x.get("tsic"), (int, float)))
    content = (f"个股时序IC({ex} · 前向{s.get('fwd_days')}日 · {s.get('n_codes')}票):"
               f"均值 {s.get('mean_tsic')} · 中位 {s.get('median_tsic')} · 正占比 {s.get('pos_ratio')}"
               + (f"\n{per}" if per else "")
               + "\n口径: 逐票 Spearman(因子值ₜ, 自身未来收益ₜ),因子取 RAW 值")
    return {"ok": True, "content": content, "artifact": None,
            "raw": {"summary": s, "n_rows": len(rows)}}   # raw 瘦身:codes_tsic 全池行不进上下文


def workflow_critique_impl(goal: str = "", graph: Optional[Dict[str, Any]] = None,
                           metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """AI 批判环:目标+当前图+回测指标 → 诊断+改进图(LLM,失败规则兜底)。"""
    g = graph if isinstance(graph, dict) else {}
    if not (g.get("nodes") or g.get("edges")):
        return {"ok": False, "artifact": None,
                "content": "请给当前工作流 graph({nodes,edges});metrics 传该图的真实回测指标"
                           "(rank_ic/sharpe/ann_return/oos_verdict 等)"}
    body = {"goal": (goal or "").strip(), "graph": g, "metrics": metrics or {}}
    try:
        r = _self_post("/workflow/critique", body, timeout=120)
    except Exception as e:
        return {"ok": False, "content": f"批判环调用失败: {e}", "artifact": None}
    gr = r.get("graph") or {}
    src = r.get("source") or "?"
    content = (f"AI 批判({'真·LLM' if src == 'llm' else '规则兜底·非LLM'}):{r.get('diagnosis')}\n"
               f"改进图: {len(gr.get('nodes') or [])} 节点(nodes/edges 见 raw.graph)\n"
               "⚠ 注意: metrics 为调用方自报,后端不复算(P2 将加强为后端取数)。")
    return {"ok": bool(r.get("ok")), "content": content, "artifact": None, "raw": r}


def regen_impl(end: str = "", wait: bool = True,
               poll_seconds: float = 15.0, timeout_seconds: float = 600.0) -> Dict[str, Any]:
    """三产物(breadth/mainline/v4)再生:后台子进程 ~5min;wait=true 轮询到完成。"""
    body: Dict[str, Any] = {}
    if (end or "").strip():
        body["end"] = end.strip()
    try:
        r = _self_post("/screen/regen", body)
    except Exception as e:
        return {"ok": False, "content": f"再生启动失败: {e}", "artifact": None}
    if not r.get("ok"):
        st = r.get("state") or {}
        return {"ok": False, "artifact": None, "raw": r,
                "content": f"再生未启动: {r.get('reason')}(phase={st.get('phase')} step={st.get('step')})"}
    if not wait:
        return {"ok": True, "artifact": None, "raw": r,
                "content": "三产物再生已启动(后台~5分钟)。稍后 ww_regen wait=true 续查,或 ww_model_health 验新鲜度。"}
    import time as _time
    deadline = _time.time() + float(timeout_seconds or 600.0)
    state: Dict[str, Any] = {}
    while _time.time() <= deadline:
        try:
            s = _self_get("/screen/regen/status")
        except Exception as e:
            return {"ok": False, "content": f"再生状态读取失败: {e}", "artifact": None, "raw": {"state": state}}
        state = s.get("state") or {}
        if not state.get("running") and state.get("phase") in ("done", "error"):
            ok = bool(state.get("ok"))
            return {"ok": ok, "artifact": None, "raw": {"state": state},
                    "content": (f"再生完成: 新数据日 {state.get('new_date')} · 用时 {state.get('elapsed_sec')}s"
                                if ok else f"再生失败: {state.get('error')}")}
        if poll_seconds:
            _time.sleep(float(poll_seconds))
    return {"ok": False, "artifact": None, "raw": {"state": state},
            "content": "再生轮询超时: 后端可能仍在跑,稍后用 ww_model_health 查产物新鲜度验新。"}


def picks_perf_impl(date: str = "", horizon: int = 5) -> Dict[str, Any]:
    """查正式选股成绩单:picks 档案 snapshot 行 → 篮子前向收益 vs 全A等权(校准同口径)。"""
    try:
        r = _self_get("/screen/picks?snapshot_only=1&limit=50")
    except Exception as e:
        return {"ok": False, "content": f"picks 档案读取失败: {e}", "artifact": None}
    items = r.get("items") or []
    want = (date or "").strip()
    if want:
        items = [it for it in items
                 if str(it.get("date")) == want or str(it.get("ts", "")).startswith(want)]
    if not items:
        return {"ok": True, "artifact": None, "raw": {"n": 0},
                "content": ("暂无正式选股档案" + (f"(date={want})" if want else "")
                            + "。ww_screen_run 传 snapshot=true 落档后才可跟踪成绩。")}
    it = items[0]                                              # read_picks 新在前
    codes = [str(p.get("code")) for p in (it.get("picks") or []) if p.get("code")][:40]
    if not codes:
        return {"ok": False, "content": f"档案 {it.get('date')} 无 picks 行,无法跟踪",
                "artifact": None, "raw": {"item": it}}
    hz = max(1, min(int(horizon or 5), 60))
    q = f"/seats/basket_perf?codes={','.join(codes)}&start={it.get('date')}&horizon={hz}"
    try:
        b = _self_get(q, timeout=120)
    except Exception as e:
        return {"ok": False, "content": f"篮子收益计算失败: {e}", "artifact": None}
    if not b.get("ok"):
        return {"ok": False, "content": f"篮子收益计算失败: {b.get('reason')}",
                "artifact": None, "raw": b}
    def _pct(v):
        return "—" if v is None else f"{float(v):+.2%}"
    content = (f"{it.get('date')} 正式选股 {b.get('n')} 只 · {b.get('horizon')}日等权 "
               f"{_pct(b.get('avg_ret'))} vs 全A等权 {_pct(b.get('bench_ret'))} · "
               f"超额 {_pct(b.get('excess'))} · 成熟 {b.get('matured_n')}/{b.get('n')}"
               + (f"\n⚠ {'; '.join(str(w) for w in b.get('warnings'))}" if b.get("warnings") else "")
               + f"\n口径: {b.get('note')}")
    return {"ok": True, "content": content, "artifact": None,
            "raw": {"pick_date": it.get("date"), "model": it.get("model"), "perf": b}}


def seats_decide_impl(code: str, name: str = "", creed: str = "",
                      mode: str = "fast") -> Dict[str, Any]:
    try:
        r = _self_post("/seats/decide", {"code": code, "name": name, "creed": creed,
                                         "mode": mode,
                                         "date": date.today().isoformat()}, timeout=180)
    except Exception as e:
        return {"ok": False, "content": f"研判调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"研判失败: {r.get('reason')}", "artifact": None}
    _af = r.get("audit_flags") or []
    _af_line = ("\n⚠ 断言质检 " + str(len(_af)) + " 处: " + "; ".join(str(x) for x in _af[:3])) if _af else ""
    _cal_line = ""
    try:
        conf = r.get("confidence")
        if isinstance(conf, (int, float)):
            cal = _self_get("/seats/calibration?horizon=5")
            if cal.get("ok"):
                from guanlan_v2.seats.calibration import bucket_of
                b = bucket_of(float(conf))
                row = next((x for x in (cal.get("buckets") or []) if x.get("bucket") == b), None)
                if row and (row.get("n") or 0) >= 5:
                    _cal_line = (f"\n📐 置信校准: {b}档历史5日命中率 "
                                 f"{row['hit_rate'] * 100:.0f}%(n={row['n']})")
                elif row is not None:
                    _cal_line = f"\n📐 置信校准: {b}档成熟样本不足(n={row.get('n', 0)}),暂以原始置信为准"
    except Exception:  # noqa: BLE001 — 校准失败静默,不影响研判内容(但留 debug 痕迹便于排障)
        logging.getLogger(__name__).debug("calibration fetch failed", exc_info=True)
        _cal_line = ""
    return {"ok": True,
            "content": (f"落子研判 {r.get('name')}({r.get('code')}): 方向 {r.get('direction')}"
                        f" · 置信 {r.get('confidence')} · {str(r.get('rationale', ''))[:200]}"
                        + _af_line + _cal_line),
            "artifact": artifact("seat_decision", page="seats", channel="cockpit",
                                 payload={"code": code, "name": name}),
            "raw": r}


def cards_query_impl(status: str = "all") -> Dict[str, Any]:
    if status not in {"draft", "approved", "rejected", "all"}:
        return {"ok": False, "content": f"status 非法: {status}(允许 draft/approved/rejected/all)",
                "artifact": None}
    try:
        r = _self_get(f"/cards/list?status={status}")
    except Exception as e:
        return {"ok": False, "content": f"经验卡查询失败: {e}", "artifact": None}
    cards = r.get("cards") or []
    lines = [f"{c.get('id')} [{c.get('status')}] {c.get('title')} ({c.get('verdict')}, ic={c.get('ic')})"
             for c in cards[:20]]
    return {"ok": True, "content": f"经验卡 {len(cards)} 张:\n" + "\n".join(lines), "artifact": None,
            "raw": {"n": len(cards)}}


def reports_query_impl(q: str = "") -> Dict[str, Any]:
    q = str(q or "")
    items = []
    try:
        for p in sorted(_REPORTS_STORE.glob("*.json"), reverse=True):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if q and q not in str(rec.get("name", "")):
                continue
            items.append(f"{rec.get('id')} · {rec.get('name')} · {rec.get('method')} · kpi={rec.get('kpi')}")
    except Exception as e:
        return {"ok": False, "content": f"报告库读取失败: {e}", "artifact": None}
    return {"ok": True, "content": f"报告库匹配 {len(items)} 篇:\n" + "\n".join(items[:20]), "artifact": None}


# ── 五件新工具 impl ──

def report_run_impl(code: str, name: str = "", asof: Optional[str] = None) -> Dict[str, Any]:
    """受理深度研报(不在工具线程跑 5-8 分钟子进程——返回 background 信封,由 api 后台跑道执行)。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):          # 裸码 → 引擎规范化(懒导入)
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ/BJ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH600519 形)", "artifact": None}
    return {"ok": True,
            "content": f"研报已受理:{name or code} 后台生成中(真实约 5-8 分钟),完成后会在对话里通知并可直接翻阅。期间可以继续下其他指令。",
            "artifact": None,
            "background": {"kind": "report", "code": code, "name": name, "asof": (asof or None)}}


def seats_bind_impl(code: str, name: str = "", creed: str = "",
                    template: str = "momentum") -> Dict[str, Any]:
    """为某只票在校场创建专属盯盘 agent(纯前端落地:后端只产 seat_bind 信封,
    控制台前端 applySeatBind 写 window.GL 策略 bind=[code] = 盯盘)。
    诚实口径:盯盘 = 校场绑定的 agent、页面开着时前端循环研判,非服务器 7×24。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):          # 裸码 → 引擎规范化(同 report_run_impl)
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ/BJ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH600519 形)", "artifact": None}
    if template not in {"momentum", "reversal", "event"}:
        template = "momentum"
    bare = code[2:]
    nm = (name or "").strip() or code
    return {"ok": True,
            "content": (f"已为 {nm}({bare}) 在校场创建盯盘 agent「{nm} · 盯盘」({template} 模板)。"
                        f"它会显现在校场,页面开着时由前端盯盘循环持续研判提醒;"
                        f"这不是服务器 7×24 常驻盯盘。需要立刻看一次研判,我再跑 ww_seats_decide。"),
            "artifact": artifact("seat_bind", page="seats", channel="cockpit",
                                 payload={"code": code, "bareCode": bare, "name": nm,
                                          "creed": (creed or "").strip(), "template": template})}


def show_page_impl(page: str = "") -> Dict[str, Any]:
    page = (page or "").strip()
    if page not in _SHOW_PAGES:
        return {"ok": False, "content": f"未知界面: {page}(可选 {'/'.join(_SHOW_PAGES)})", "artifact": None}
    return {"ok": True, "content": f"已调出「{_SHOW_PAGES[page]}」界面(右栏)。",
            "artifact": artifact("page_view", page=page, channel=None, payload={})}


def cards_save_impl(title: str, insight: str = "", expr: str = "", verdict: str = "存疑",
                    conf: int = 0, ic: str = "", cat: str = "其他",
                    status: str = "draft") -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "content": "缺少卡片标题 title", "artifact": None}
    if status not in {"draft", "approved"}:
        return {"ok": False, "content": f"status 非法: {status}(允许 draft/approved)", "artifact": None}
    # 复盘 monitor 干跑(阶段1):不真 POST /cards 只回"将沉淀",由 _run_review_bg emit 成 review_proposal。
    if CTX_REVIEW_MODE.get(None) == "monitor":
        return {"ok": True, "content": f"【monitor·未落盘】将沉淀 draft 经验卡:「{title}」", "artifact": None}
    # 红线硬保证(代码层,非靠系统提示):复盘路径(CTX_REVIEW_MODE 非 None)无条件强制 draft,
    # 杜绝复盘 agent(或被注入诱导)在 enforce 下写自动批准卡绕过 draft→人审 门。
    if CTX_REVIEW_MODE.get(None) is not None:
        status = "draft"
    try:
        r = _self_post("/cards", {"title": title, "insight": insight, "expr": expr,
                                  "verdict": verdict, "conf": int(conf or 0), "ic": str(ic or ""),
                                  "cat": cat, "status": status, "src": "帷幄 · ww_cards_save"})
    except Exception as e:
        return {"ok": False, "content": f"经验卡保存失败: {e}", "artifact": None}
    cid = r.get("id", "?")
    advisory = ""
    try:
        from guanlan_v2.factorlib.claim_audit import unsourced_percents
        rogue = unsourced_percents(insight, " ".join([title, expr, str(ic or "")]))
        if rogue:
            advisory = (f"\n⚠ insight 含 {len(rogue)} 个未注明出处的数字断言"
                        f"({', '.join(f'{x:g}%' for x in rogue[:3])}),建议核对后再 approve。")
    except Exception:  # noqa: BLE001
        advisory = ""
    return {"ok": True, "content": f"经验卡已沉淀: {cid}「{title}」({status})" + advisory,
            "artifact": artifact("card", page="cards", channel="validation",
                                 payload={"focusCardName": title})}


def memory_write_impl(text: str = "", scope: str = "global", key: str = "") -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "content": "空记忆不写", "artifact": None}
    if scope not in {"global", "session"}:
        return {"ok": False, "content": f"scope 非法: {scope}(允许 global/session)", "artifact": None}
    # 复盘 monitor 干跑(阶段1):不落盘只回"将写入",由 _run_review_bg emit 成 review_proposal。
    # 置于一切落盘逻辑(path 解析 / open(a))之前,确保 monitor 下文件系统零副作用。
    if CTX_REVIEW_MODE.get(None) == "monitor":
        return {"ok": True, "content": f"【monitor·未落盘】将写入帷幄记忆({scope}): {text[:120]}",
                "artifact": None}
    # ── 归一化(阶段2,对所有写一律生效;一处 _MEMORY_MAX_LINE 同源,不与复盘消毒互相冲突)──
    # (a) 通用上限:每条记忆 cap 到 _MEMORY_MAX_LINE 字,防 memory.md 无界膨胀。
    text = text[:_MEMORY_MAX_LINE]
    # (b) 复盘路径(CTX_REVIEW_MODE 非 None,阶段1)额外去换行:memory.md 会注入未来每轮主对话 =
    #     持久注入 channel,把单条多行外部料压成一行(防御纵深;cap 已在上面做过,这里只去换行不重复截断)。
    if CTX_REVIEW_MODE.get(None) is not None:
        text = text.replace("\n", " ").replace("\r", " ")
    key = (key or "").strip()
    # key 消毒:只剔除会破坏 `(key)` 标签格式/行匹配的字符(圆括号/方括号/换行),其余
    # (. / : 空格 ; 等)一律保留 → 仅标点不同的 key 不再被折叠成同一个而跨主题误删(#2)。
    # 匹配侧 re.escape(key) 已足够中和残余正则元字符;消毒后为空则下方按无 key 处理。
    if key:
        key = _re.sub(r"[\[\]()\r\n]", "", key).strip()
    if scope == "session":
        sid = CTX_SID.get(None)
        if not sid:
            return {"ok": False, "content": "无会话上下文,session 笔记不可用", "artifact": None}
        path = _session_notes_path(sid)
    else:
        path = _MEMORY_PATH
    line = f"- [{date.today().isoformat()}] {f'({key}) ' if key else ''}{text}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _MEMORY_LOCK:
            # replace 收敛(阶段2,仅 global + 显式 key):同 key 旧行先删再 append → 同主题写入覆盖不累加。
            # 锚定行首日期前缀 + 精确 key(re.escape),与写入行格式 `- [YYYY-MM-DD] (key) text` 一致,
            # 杜绝子串过匹配误删恰好含字面 `(key) ` 的非 key 正文行(数据丢失防护)。
            if key and scope == "global" and path.exists():
                _pat = _re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \(" + _re.escape(key) + r"\) ")
                old = path.read_text(encoding="utf-8").splitlines()
                kept = [ln for ln in old if not _pat.match(ln)]
                path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            # 阶段2 收敛接线:仅 global,超阈值在同一 _MEMORY_LOCK 内调 curator(其自身不持锁,
            # 单次持锁安全;杜绝与复盘 fork 竞争)。收敛失败不影响写入成败(写已落盘)。
            if scope == "global":
                try:
                    n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
                    if n > _CURATOR_TRIGGER_LINES:
                        from guanlan_v2.console.curator import consolidate_memory
                        consolidate_memory(path, _ARCHIVE_PATH, max_lines=_CURATOR_TRIGGER_LINES)
                except Exception:  # noqa: BLE001 — 收敛是增强项,失败不挡写入
                    pass
    except Exception as e:
        return {"ok": False, "content": f"记忆写入失败: {e}", "artifact": None}
    return {"ok": True, "content": "已记入本会话笔记。" if scope == "session" else "已记入帷幄记忆。",
            "artifact": None}


def _run_news_sentiment(codes, limit):
    """桥:在工具线程内跑异步 news_sentiment(测试可 monkeypatch 此函数)。
    工具经 asyncio.to_thread 跑,无运行中 loop → asyncio.run 安全。"""
    import asyncio
    from guanlan_v2.screen.news import news_sentiment
    return asyncio.run(news_sentiment(codes, limit=limit))


def news_search_impl(code: str = "", scope: str = "both", query: str = "",
                     limit: int = 200) -> Dict[str, Any]:
    """实时联网检索个股/大盘新闻 + 情绪研判(东财快讯,带引用,无则诚实标注)。"""
    code = (code or "").strip().upper()
    if scope == "stock" and not code:
        return {"ok": False, "content": "scope=stock 需要提供股票代码 code", "artifact": None}
    codes = [code] if (code and scope in ("stock", "both")) else []
    r = _run_news_sentiment(codes, limit)
    if not r.get("ok"):
        return {"ok": False, "content": f"消息面拉取失败:{r.get('reason','')}", "artifact": None}

    lines = []
    if scope in ("market", "both"):
        mr = r.get("market_read") or "(LLM 情绪未判读,见原文)"
        lines.append(f"大盘消息面:{mr}")
        for it in (r.get("market") or [])[:5]:
            t = it.get("title", "")
            if not query or query in t:
                lines.append(f"  · [{it.get('time','')}] {t}")
    if scope in ("stock", "both") and codes:
        c = codes[0]
        sent = (r.get("sentiment") or {}).get(c) or {}
        if sent:
            lines.append(f"本票 {c}:{sent.get('tag','')} — {sent.get('read','')}")
            for it in (r.get("by_code") or {}).get(c, [])[:4]:
                if not query or query in it.get("title", ""):
                    lines.append(f"  · [{it.get('time','')}] {it.get('title','')}")
        else:
            lines.append(f"本票 {c}:近期无相关快讯(不编造)")
    content = "\n".join(lines) if lines else "无可用消息面"
    art = artifact("news_sentiment", page=None, channel="console",
                   payload={"scope": scope, "code": code, "as_of": r.get("as_of"),
                            "market_read": r.get("market_read"), "sentiment": r.get("sentiment"),
                            "model": r.get("model")})
    return {"ok": True, "content": content, "artifact": art, "raw": r}


def seats_history_impl(code: str = "", limit: int = 10) -> Dict[str, Any]:
    """查询落子哨兵的研判/条件单历史(平台级,跨会话;读 GET /seats/decisions,逆序最新在前)。
    真实响应形状 {"ok": True, "decisions": [...], "total": N}(seats/api.py /decisions)。"""
    code = (code or "").strip().upper()
    # 前置校验:带前缀 SZ000001 形(_CODE_RE)或裸 6 位 000001 形均放行(服务端比较时统一 upper)
    if code and not (_CODE_RE.match(code) or _re.match(r"^\d{6}$", code)):
        return {"ok": False, "content": f"代码非法: {code}(应为 SZ000001 / 000001 形)", "artifact": None}
    try:
        lim = max(1, min(int(limit or 10), 50))
        r = _self_get(f"/seats/decisions?code={urllib.parse.quote(code)}&limit={lim}")
    except Exception as e:
        return {"ok": False, "content": f"研判历史查询失败: {e}", "artifact": None}
    items = r.get("decisions") or []
    if not items:
        return {"ok": True, "content": "暂无哨兵研判记录。", "artifact": None, "raw": r}
    lines = [f"{str(it.get('ts', ''))[:16]} {it.get('kind', '')} {it.get('name') or it.get('code', '')} "
             f"{it.get('direction', '')} 置信{it.get('confidence', '-')}" for it in items[:lim]]
    return {"ok": True, "content": f"哨兵研判最近 {len(lines)} 条:\n" + "\n".join(lines),
            "artifact": None, "raw": r}


def f10_impl(code: str, category: Optional[str] = None, asof: Optional[str] = None,
             keyword: Optional[str] = None) -> Dict[str, Any]:
    """查本票 F10 结构化事实(估值/事件/龙虎榜/券商目标价)。确定性 parser,数字逐字来自
    f10_corpus,不经 LLM;asof 透传 load_facts 做 PIT 裁剪(晚于 asof 的行被裁,不前视);
    缺料 → 诚实 None/空,绝不编造。结构化事实经 envelope 的 raw 字段透传 to_dict()。"""
    code = (code or "").strip().upper()
    if not (_CODE_RE.match(code) or _re.match(r"^\d{6}$", code)):
        return {"ok": False, "content": f"代码非法: {code}(应为 SZ000001 / 000001 形)", "artifact": None}
    try:
        from financial_analyst.data import f10_corpus
        facts = f10_corpus.load_facts(code, asof).to_dict()
    except Exception as e:
        return {"ok": False, "content": f"F10 事实读取失败: {e}", "artifact": None}
    if category:
        keep = {"估值": "valuation", "事件": "events", "龙虎榜": "lhb", "券商": "broker"}.get(category, category)
        meta = {"code", "asof", "snapshot_date", "honest_note", "provenance"}
        facts = {k: v for k, v in facts.items() if k == keep or k in meta}
    if keyword and facts.get("events"):
        facts["events"] = [e for e in facts["events"] if keyword in e.get("title", "")]
    val = facts.get("valuation") or {}
    n_ev = len(facts.get("events") or [])
    n_rt = len((facts.get("broker") or {}).get("ratings") or [])
    parts = [f"F10 事实 · {code}"]
    if asof:
        parts.append(f"(口径 asof={asof})")
    if val.get("total_shares") is not None:
        parts.append(f"总股本={val['total_shares']}")
    parts.append(f"事件 {n_ev} 条 / 券商评级 {n_rt} 条")
    if facts.get("honest_note"):
        parts.append(str(facts["honest_note"]))
    return {"ok": True, "content": " · ".join(parts), "artifact": None, "raw": facts}


def factorlib_save_impl(name: str = "", expr: str = "", family: str = "library_mined",
                        description: str = "", is_qlib: bool = False) -> Dict[str, Any]:
    """把一条因子表达式存入 guanlan 因子库 mined/ 并运行期注册进 zoo registry。
    透传后端 /factorlib/save(校验 validate_expr+compile_factor → 重名拒绝 → 落盘 → register)。
    诚实:落盘成功即 ok:True,运行期注册是否生效看 registered;非法/重名 → 后端 ok:False 原样回。
    is_qlib=true 的 Qlib→zoo 译写($close/Ref/Std → zoo DSL)由后端 /factorlib/save 完成(qlib_to_zoo)。
    """
    nm = (name or "").strip()
    ex = (expr or "").strip()
    if not nm:
        return {"ok": False, "content": "缺少因子名 name", "artifact": None}
    if not ex:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    try:
        r = _self_post("/factorlib/save", {"name": nm, "expr": ex, "family": family or "library_mined",
                                           "description": description or "", "is_qlib": bool(is_qlib),
                                           "source": "帷幄 · ww_factorlib_save"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"因子入库调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"因子入库失败: {r.get('reason', '未知原因')}", "artifact": None}
    registered = bool(r.get("registered"))
    zoo = r.get("expr", ex)
    if registered:
        msg = f"因子已入库并注册:「{nm}」= {zoo}(已注册进 zoo,可被选股/工作流复用)。"
    else:
        msg = (f"因子已入库(落盘成功):「{nm}」= {zoo},但运行期未注册"
               f"({r.get('reason', '原因未知')})——重启后随库加载或核对后重试。")
    return {"ok": True, "content": msg,
            "artifact": artifact("factor_saved", page="factor", channel="workflow",
                                 payload={"name": nm, "expr": zoo, "registered": registered})}


def _proxy_engine_tool(tool_name: str, fail_label: str, **kw: Any) -> Dict[str, Any]:
    """薄包装:代理执行一个已注册的引擎工具(返回其 ToolResult 的 content/is_error)。
    在 ww_ 层加确认门(specs 里 confirm_required=True),引擎工具本身不进白名单。"""
    try:
        bt = _buddy_tools_mod()
        tool = bt.get_tool(tool_name)
        if tool is None:
            return {"ok": False, "content": f"引擎 {tool_name} 工具不可用(未注册)", "artifact": None}
        res = tool.run(**{k: v for k, v in kw.items() if v is not None})
        return {"ok": not getattr(res, "is_error", False),
                "content": str(getattr(res, "content", "")), "artifact": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"{fail_label}: {e}", "artifact": None}


def update_data_impl(codes: Optional[str] = None, mode: str = "quick") -> Dict[str, Any]:
    """增量更新行情数据(代理引擎 update_data;ww_ 层确认门防误触 all 全市场重拉)。"""
    return _proxy_engine_tool("update_data", "数据更新调用失败", codes=codes, mode=mode or "quick")


def news_collect_impl(sources: str = "kuaixun,longhu,sinafinance",
                      limit: int = 200, code: Optional[str] = None) -> Dict[str, Any]:
    """从上游抓新闻入本地库(代理引擎 news_collect;ww_ 层确认门)。"""
    # limit 归一用 `is not None`(本仓红线:`x or default` 会把合法的 0 吞掉,如 screen_impl 的 alpha=0)
    return _proxy_engine_tool("news_collect", "新闻抓取调用失败",
                              sources=sources or "kuaixun,longhu,sinafinance",
                              limit=int(limit) if limit is not None else 200, code=code)


# ── Phase B:因子炼制工作流 ──

def factor_compose_impl(members: Optional[List[str]] = None, method: str = "equal",
                        universe: str = "csi300", oos_frac: float = 0.3) -> Dict[str, Any]:
    """多因子合成(equal/ic/icir 加权)→ OOS 报告 + 各腿权重(/workflow/compose)。只评测不入库。"""
    mem = [str(m).strip() for m in (members or []) if str(m).strip()]
    if len(mem) < 2:
        return {"ok": False, "content": "至少需要 2 个因子(members)才能合成", "artifact": None}
    if method not in {"equal", "ic", "icir"}:
        method = "equal"
    try:
        r = _self_post("/workflow/compose", {"members": mem, "method": method,
                                             "universe": universe, "oos_frac": oos_frac})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"因子合成调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"因子合成失败: {r.get('reason', '未知原因')}", "artifact": None}
    # 真后端 /workflow/compose 把 headline_ic 嵌在 composite 里、权重键名是 weight(非 w);
    # 兼容两形(顶层 / composite 嵌套、w / weight),读不到诚实显 None,绝不臆造。
    comp = r.get("composite") or {}
    h = r.get("headline_ic") or comp.get("headline_ic") or {}
    w = r.get("weights") or comp.get("weights") or []
    wline = (" · ".join(f"{x.get('name')}={x.get('w', x.get('weight'))}" for x in w[:6])
             if w else "")
    return {"ok": True,
            "content": (f"合成完成({method}): RankIC {h.get('rank_ic')} · RankICIR {h.get('rank_icir')}"
                        f" · 期数 {r.get('n_dates')}" + (f"\n权重: {wline}" if wline else "")),
            "artifact": artifact("compose_report", page="factor", channel="workflow",
                                 payload={"members": mem, "method": method}),
            "raw": r}


def feature_build_impl(features: Optional[List[str]] = None, label: str = "",
                       fwd_days: int = 5, universe: str = "csi_fast",
                       oos_frac: float = 0.0) -> Dict[str, Any]:
    """物化特征工程(真 X/y)→ 真统计 + 逐特征 RankIC(/feature/build)。label 空=前向收益。"""
    feats = [str(f).strip() for f in (features or []) if str(f).strip()]
    if not feats:
        return {"ok": False, "content": "缺少特征表达式 features", "artifact": None}
    body: Dict[str, Any] = {"features": feats, "fwd_days": int(fwd_days or 5),
                            "universe": universe, "oos_frac": oos_frac}
    if (label or "").strip():
        body["label"] = label.strip()
    try:
        r = _self_post("/feature/build", body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"特征物化调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"特征物化失败: {r.get('reason', '未知原因')}", "artifact": None}
    # 真后端 /feature/build 的逐特征 IC 在 "ic" 列(键 feature/rank_ic_mean),非 "features"/rank_ic;
    # 兼容两形,读不到诚实跳过该腿(不臆造 IC)。
    fs = r.get("ic") or r.get("features") or []

    def _fname(x):
        return x.get("feature", x.get("name"))

    def _fic(x):
        v = x.get("rank_ic_mean")
        return x.get("rank_ic") if v is None else v

    ic_line = " · ".join(f"{_fname(x)} IC{float(_fic(x)):+.3f}"
                         for x in fs[:8] if _fic(x) is not None)
    return {"ok": True,
            "content": (f"特征物化完成: {r.get('n_codes')} 票 × {r.get('n_dates')} 期"
                        f" · 覆盖 {_pct(r.get('coverage'))}" + (f"\n逐特征 RankIC: {ic_line}" if ic_line else "")),
            "artifact": artifact("feature_matrix", page="factor", channel="workflow",
                                 payload={"features": feats}),
            "raw": r}


_FACTOR_FIELD_EXAMPLES = (
    "rank(-delta(close,20))       动量反转(20日跌幅排名)",
    "-stddev(returns,20)          低波(20日收益波动取反)",
    "rank(roe)                    高 ROE",
    "rank(-amihud_20)             高流动性(Amihud 取反)",
    "regbeta(returns,idx_ret,60)  对大盘 60 日滚动 β(共振/跟随)",
)


def factor_fields_impl() -> Dict[str, Any]:
    """返回 zoo DSL 字段+算子词表 + 几条范例,供写因子表达式前查合法字段名(治猜错字段→validate 失败)。
    诚实:这是 DSL 词表(字段含中文名/方向/频率/口径),不是完整方向语义层。"""
    try:
        from financial_analyst.factors.zoo.expr import FACTOR_VOCAB
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"字段词表读取失败: {e}", "artifact": None}
    examples = "\n".join("  " + s for s in _FACTOR_FIELD_EXAMPLES)
    return {"ok": True,
            "content": ("zoo 因子 DSL 词表(写表达式只能用这些字段/算子,否则校验失败):\n"
                        + str(FACTOR_VOCAB) + "\n\n范例:\n" + examples),
            "artifact": None}


def etf_report_run_impl(code: str, name: str = "", asof: Optional[str] = None) -> Dict[str, Any]:
    """受理 ETF 深度研报(后台跑引擎 run_etf_report,5-8 分钟,不阻塞)。返回 background 信封。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH510300 形)", "artifact": None}
    return {"ok": True,
            "content": f"ETF 研报已受理:{name or code} 后台生成中(约 5-8 分钟),完成后通知并可翻阅。",
            "artifact": None,
            "background": {"kind": "etf_report", "code": code, "name": name, "asof": (asof or None)}}


# ── Phase C:自省 / 自学治本 ──

def capabilities_impl() -> Dict[str, Any]:
    """列出帷幄当前真正能调用的全部工具(TOOL_REGISTRY ∩ CONSOLE_ALLOWED)+ 用途/确认/成本。
    自省工具:回答『你能做什么/有哪些工具』。"""
    try:
        bt = _buddy_tools_mod()
        rows = [t for t in bt.TOOL_REGISTRY if t.name in CONSOLE_ALLOWED]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"工具清单读取失败: {e}", "artifact": None}
    lines = []
    for t in sorted(rows, key=lambda x: x.name):
        head = next(iter(str(getattr(t, "description", "")).splitlines()), "")[:70]
        flag = "(需确认)" if getattr(t, "confirm_required", False) else ""
        lines.append(f"· {t.name}{flag} — {head}")
    return {"ok": True,
            "content": f"我当前能调用 {len(rows)} 个工具:\n" + "\n".join(lines), "artifact": None}


# ww_ 工具能直接触达的后端路径(C2 诚实可达性标注依据)现由 WW_TOOL_TABLE 的 reachable 字段
# 单一声明、模块级派生为 _WW_REACHABLE_ENDPOINTS(见文件后部「注册表数据化」段)。每条路径都有某个
# ww_/白名单工具会真正打到它:
#   /screen/run ← screen_impl(ww_screen_run)         /screen/factors ← screen_factors_impl
#   /seats/decide ← seats_decide_impl                 /seats/decisions ← seats_history_impl
#   /seats/calibration ← seats_decide_impl(置信校准附注)
#   /cards/list ← cards_query_impl                    /cards ← cards_save_impl
#   /factor/report2 ← factor_analyze_impl             /backtest/vector ← backtest_impl
#   /factorlib/save ← factorlib_save_impl(A)          /workflow/compose ← factor_compose_impl(B)
#   /feature/build ← feature_build_impl(B)            /openapi.json ← endpoints_impl(C 本身)
# endpoints_impl 在调用期(模块已完全加载)引用模块级派生集合 _WW_REACHABLE_ENDPOINTS。


def endpoints_impl(filter_prefix: str = "") -> Dict[str, Any]:
    """列出后端能力地图(GET /openapi.json),诚实标注每项『我可直接调 / 仅界面可达』。
    用于回答『观澜平台能做什么』+ 诚实降级(有功能但我调不到→请在界面用),不冒充能调。"""
    try:
        r = _self_get("/openapi.json")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"能力地图读取失败: {e}", "artifact": None}
    paths = (r or {}).get("paths") or {}
    pref = (filter_prefix or "").strip()
    rows = []
    for path in sorted(paths):
        if pref and not path.startswith(pref):
            continue
        methods = ",".join(sorted(m.upper() for m in paths[path] if m.lower() in
                                  ("get", "post", "put", "delete", "patch")))
        summary = ""
        for m in paths[path].values():
            if isinstance(m, dict) and m.get("summary"):
                summary = str(m["summary"])[:50]
                break
        mark = "可直接调" if path in _WW_REACHABLE_ENDPOINTS else "仅界面可达"
        rows.append(f"· {methods} {path} [{mark}] {summary}")
    if not rows:
        return {"ok": True, "content": "(无匹配端点)", "artifact": None}
    head = f"后端能力地图(共 {len(rows)} 个端点;『仅界面可达』= 我没有对应工具、需你在网页操作):\n"
    body = "\n".join(rows[:120])
    more = f"\n…(端点较多,仅显示前 120 条,共 {len(rows)} 条;可用 filter_prefix 过滤)" if len(rows) > 120 else ""
    return {"ok": True, "content": head + body + more, "artifact": None}


def _read_memory_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


def _archive_tail() -> str:
    """全局归档尾部(_ARCHIVE_PATH 尾 4000),供 memory_read 召回已归档的易逝笔记;无则空串。"""
    try:
        if _ARCHIVE_PATH.exists():
            a = _ARCHIVE_PATH.read_text(encoding="utf-8")
            if a.strip():
                return "\n\n归档(更早易逝笔记,可恢复):\n" + a[-4000:]
    except Exception:  # noqa: BLE001
        pass
    return ""


def memory_read_impl(scope: str = "all") -> Dict[str, Any]:
    if scope not in {"global", "session", "all"}:
        return {"ok": False, "content": f"scope 非法: {scope}(允许 global/session/all)", "artifact": None}
    if scope == "global":
        try:
            body = _MEMORY_PATH.read_text(encoding="utf-8") if _MEMORY_PATH.exists() else ""
        except Exception as e:
            return {"ok": False, "content": f"记忆读取失败: {e}", "artifact": None}
        arch = _archive_tail()
        content = (("帷幄记忆:\n" + body[-4000:]) if body.strip() else "记忆为空。") + arch
        return {"ok": True, "content": content, "artifact": None}
    sid = CTX_SID.get(None)
    if scope == "session":
        if not sid:
            return {"ok": False, "content": "无会话上下文,session 笔记不可用", "artifact": None}
        body = _read_memory_file(_session_notes_path(sid))
        return {"ok": True, "content": ("本会话笔记:\n" + body[-4000:]) if body.strip() else "(本会话暂无笔记)",
                "artifact": None}
    # all:全局 + 本会话两段拼接,各截尾 4000;全局段附归档尾部
    parts: List[str] = []
    g = _read_memory_file(_MEMORY_PATH)
    arch = _archive_tail()
    if g.strip() or arch:
        parts.append((("帷幄记忆(全局):\n" + g[-4000:]) if g.strip() else "帷幄记忆(全局):(空)") + arch)
    s = _read_memory_file(_session_notes_path(sid)) if sid else ""
    if s.strip():
        parts.append("本会话笔记:\n" + s[-4000:])
    return {"ok": True, "content": "\n\n".join(parts) if parts else "记忆为空。", "artifact": None}


# ── 注册进 buddy TOOL_REGISTRY ──
def _buddy_tools_mod():
    """懒导入引擎 buddy.tools(便于测试替身)。"""
    from financial_analyst.buddy import tools as bt
    return bt


def _wrap(impl):
    """impl dict → ToolResult(side_effect 携带 artifact / plan)。"""
    def run(**args):
        bt = _buddy_tools_mod()
        out = impl(**args)
        se: Dict[str, Any] = {}
        if out.get("artifact"):
            se["artifact"] = out["artifact"]
        if out.get("todos") is not None:          # plan_update 专属
            se["plan"] = out["todos"]
        if out.get("background"):
            se["background"] = out["background"]
        if out.get("content"):
            content = str(out["content"])
        elif out.get("ok") and out.get("todos") is not None:
            content = f"计划已更新,{out.get('n')} 项"
        else:
            content = json.dumps({k: v for k, v in out.items() if k != "raw"},
                                 ensure_ascii=False)[:400]
        return bt.ToolResult(content=content, is_error=not out.get("ok", False),
                             side_effect=se or None)
    return run


_TODO_SCHEMA = {"type": "object", "properties": {
    "todos": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "text": {"type": "string"},
        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
        "required": ["text"]}}}, "required": ["todos"]}


def _expr_schema(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    props = {"expr": {"type": "string", "description": "zoo 因子表达式,如 rank(-delta(close,20))"},
             "universe": {"type": "string", "default": "csi300"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": ["expr"]}


# ── 注册表数据化(单一声明源)──
# 每条 ww_ 工具一处定义:name/description/input_schema/impl/cost/confirm 逐条迁移自原
# register_console_tools 的 specs 列表(值完全不变);新增 reachable = 该工具触达的后端路径列表
# (空列表 = 不打任何后端端点的纯进程内/文件系统工具)。CONSOLE_ALLOWED / _WW_REACHABLE_ENDPOINTS /
# 守护计数全部从这派生。reachable 取自各 *_impl 的真实调用(_self_post/_self_get/_call_*),
# 与 endpoints_impl 的可达性标注一致;某些工具触达多个端点(如 ww_seats_decide 同打
# /seats/decide 与 /seats/calibration)→ 用列表,不能用单值。
WW_TOOL_TABLE = [
    {"name": "ww_plan_update",
     "description":
         "更新当前会话的任务计划(整单替换;TodoWrite 式)。复杂任务先拆计划再执行,每完成一步就更新 status。",
     "input_schema": _TODO_SCHEMA, "impl": plan_update_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_factor_analyze",
     "description":
         "因子截面分析(真引擎 RankIC/分组/样本外体检)。输入 zoo 表达式。Cross-sectional factor IC analysis.",
     "input_schema": _expr_schema({"freq": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                                   "oos_frac": {"type": "number", "default": 0.3},
                                   "start": {"type": "string", "description": "起始日 YYYY-MM-DD,缺省回测2年/分析后端默认"},
                                   "end": {"type": "string"}}),
     "impl": factor_analyze_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/factor/report2"]},
    {"name": "ww_backtest",
     "description":
         "因子向量化回测(分腿成本/定权/默认2年窗)。输入 zoo 表达式。Vector backtest with costs."
         "注意参数名是 topn(小写),选股工具才是 topN。",
     "input_schema": _expr_schema({"topn": {"type": "integer", "default": 30},
                                   "weighting": {"type": "string", "enum": ["equal", "mktcap", "inv_vol", "risk_parity"], "default": "equal"},
                                   "rebalance": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                                   "start": {"type": "string", "description": "起始日 YYYY-MM-DD,缺省回测2年/分析后端默认"},
                                   "end": {"type": "string"}}),
     "impl": backtest_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/backtest/vector"]},
    {"name": "ww_screen_run",
     "description":
         "九视角选股(v4 模型 + 因子混合 α)+ 约束。运行后自动把选股界面弹到右栏,无需再调 ww_show_page。"
         "factors 的 id 用 ww_screen_factors 查目录(传错会被静默忽略);不确定就传空 factors 纯 v4 跑。"
         "可选 model=变体 id(ww_model_list 查)用自训 v4 变体选股,省略=生产 prod。"
         "默认与选股页一致(pool=all·blend=1.0纯v4·topN=20·liqMin=5亿·剔ST/停牌/涨跌停·行业中性)。Stock screening.",
     "input_schema": {"type": "object", "properties": {
         "factors": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "w": {"type": "number", "default": 1.0}}, "required": ["id"]}},
         "pool": {"type": "string", "enum": ["all", "csi300", "csi500", "csi800", "csi1000"], "default": "all"},
         "blend": {"type": "number", "default": 1.0,
                   "description": "因子混合 α:1=纯v4模型 / 0=纯因子重排;<1 才让所选因子参与重排(需 factors 非空,否则无效)"},
         "topN": {"type": "integer", "default": 20},
         "liqMin": {"type": "number", "default": 5.0, "description": "成交额下限(亿),低于此剔除;0=不过滤"},
         "mlStatus": {"type": "array", "items": {"type": "string",
                      "enum": ["mainline", "initiation", "revival", "decay", "cold", "neutral"]},
                      "description": "主线状态筛选,只保留所列状态;省略/空=不筛"},
         "industryNeutral": {"type": "boolean", "description": "行业中性(默认 true)"},
         "indCap": {"type": "number", "description": "单行业持仓上限占比 0.1~0.5(默认 0.25)"},
         "exclST": {"type": "boolean", "description": "剔除 ST(默认 true)"},
         "exclHalt": {"type": "boolean", "description": "剔除停牌(默认 true)"},
         "exclLimit": {"type": "boolean", "description": "剔除涨跌停(默认 true)"},
         "exclNew": {"type": "boolean", "description": "剔除次新(默认 false)"},
         "model": {"type": "string", "description": "用哪个 v4 模型:省略/prod=生产 v4;传变体 id(ww_model_list 查)=用该变体选股,不可用则回落 prod"},
         "snapshot": {"type": "boolean", "default": False,
                      "description": "标记为「正式选股」落 picks 档案(供后续收益跟踪);实验/参数扫描别开"},
         "note": {"type": "string", "description": "选股备注,随 picks 档案落盘"}}},
     "impl": screen_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/screen/run"]},
    {"name": "ww_screen_factors",
     "description":
         "列出选股因子目录(/screen/factors 的合法 id + 名称 + 实测RankIC),供 ww_screen_run 的 factors 取 id。"
         "想做因子混合(blend<1)选股前先用它查 id,避免传错被静默忽略。可选 family 过滤。",
     "input_schema": {"type": "object", "properties": {
         "family": {"type": "string", "description": "可选,只列某一族,如「动量反转」「估值」「成长」"}}},
     "impl": screen_factors_impl, "cost": "instant", "confirm": False,
     "reachable": ["/screen/factors"]},
    {"name": "ww_model_list",
     "description":
         "列出已训练的 v4 模型变体(id+名称+留出OOS IC+特征数),供 ww_screen_run 的 model 取 id。"
         "用户问『有哪些(自训)模型/变体』或要用变体选股前先调它查 id。生产 v4 隐含=prod 不在此列。",
     "input_schema": {"type": "object", "properties": {
         "include_draft": {"type": "boolean", "default": False,
                           "description": "true=连 draft 区(未过 promote 门槛)一起列,带 ⚠ 标"},
     }},
     "impl": model_list_impl, "cost": "instant", "confirm": False,
     "reachable": ["/screen/models"]},
    {"name": "ww_model_train",
     "description":
         "训练一个新 v4 模型变体:选『基础特征 + 我的库因子』子集训练(后台子进程 ~4min,完成后 "
         "ww_model_list 可见、ww_screen_run model=<id> 即用其选股)。生产 v4 全程不动。需用户确认。"
         "factor_ids 用 ww_screen_factors 查(库因子族 id);base_features 省略=全部基础特征(同工坊默认)。Train v4 variant.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "变体名,如「低波动量组」"},
         "factor_ids": {"type": "array", "items": {"type": "string"},
                        "description": "库因子 id 列表(ww_screen_factors 查;价量/技术类可训,财务字段类暂不支持会被标未用)"},
         "base_features": {"type": "array", "items": {"type": "string"},
                           "description": "基础特征名列表;省略=全部基础特征(同工坊默认全勾);传 [] = 纯库因子模型"},
         "universe": {"type": "string", "default": "all", "description": "训练股票池,默认 all 全A"}},
      "required": ["name"]},
     "impl": model_train_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/screen/base_features", "/screen/model/train"]},
    {"name": "ww_model_promote",
     "description":
         "把可复算因子表达式/因子ID训练成 workflow 模型并入模型库,保存 recipe 且 retrainable=true,供 strict CPCV 重训验证。"
         "与 ww_model_train 不同:本工具不使用 v4 内部 base_features,只用 workflow 可物化 features/factor_ids,因此后续可 ww_model_validate tier=strict。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "模型名"},
         "features": {"type": "array", "items": {"type": "string"},
                      "description": "zoo DSL 因子表达式列表,会原样写入 recipe.features"},
         "factor_ids": {"type": "array", "items": {"type": "string"},
                        "description": "ww_screen_factors 返回的 id,工具会映射为 expr 写入 recipe.features"},
         "kind": {"type": "string", "enum": ["lightgbm", "xgboost", "rf"], "default": "lightgbm"},
         "universe": {"type": "string", "default": "csi800"},
         "label": {"type": "string", "default": "fwd_ret"},
         "fwd_days": {"type": "integer", "default": 5},
         "start": {"type": "string", "default": "2022-01-01"},
         "end": {"type": "string"},
         "benchmark": {"type": "string", "description": "可选; 表达式含 idx_ret 时默认 csi300,也可显式传 csi300"},
         "leader": {"type": "string", "description": "可选; 表达式含 ref_ret 时必须传龙头代码"},
         "params": {"type": "object", "description": "模型超参,如 lightgbm: {leaves, lr}"},
         "wait": {"type": "boolean", "default": False,
                  "description": "true=轮询到入库完成并报 oos_ic/是否过门槛(draft 区诚实显形)"},
         "poll_seconds": {"type": "number", "default": 5},
         "timeout_seconds": {"type": "number", "default": 900}},
      "required": ["name"]},
     "impl": model_promote_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/screen/factors", "/model/promote"]},
    {"name": "ww_model_validate",
     "description":
         "对模型变体跑 quick 或 strict CPCV 验证。strict 会调用官方 retrain-CPCV(purge/embargo)并写 model_cpcv_<id>.json;"
         "wait=true 会轮询到完成并返回 DSR/IC/Sharpe 摘要。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "模型 id, ww_model_list 查看"},
         "tier": {"type": "string", "enum": ["quick", "strict"], "default": "strict"},
         "n_groups": {"type": "integer", "default": 6},
         "k": {"type": "integer", "default": 2},
         "purge": {"type": "integer", "default": 5},
         "embargo": {"type": "integer", "default": 5},
         "wait": {"type": "boolean", "default": False},
         "poll_seconds": {"type": "number", "default": 4},
         "timeout_seconds": {"type": "number", "default": 600}},
      "required": ["id"]},
     "impl": model_validate_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/screen/model/validate", "/screen/model/validate/status"]},
    {"name": "ww_model_delete",
     "description":
         "删除一个已训练的 v4 模型变体(生产 prod 不可删;删的若是当前默认变体,自动回落 prod)。"
         "用户说『删掉变体 X/不要这个模型了』时用。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "变体 id(ww_model_list 查)"}},
      "required": ["id"]},
     "impl": model_delete_impl, "cost": "instant", "confirm": True,
     "reachable": ["/screen/model/delete"]},
    {"name": "ww_model_set_default",
     "description":
         "把某个 v4 变体设为平台默认(之后选股页/ww_screen_run 不指定模型时缺省用它;显式 model 仍优先)。"
         "id=prod 或省略 = 清除回官方生产 prod。生产 prod 文件不动,随时可切回。需用户确认。"
         "用户说『把这个变体设为默认/上线/以后默认用它』时用。",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "变体 id(ww_model_list 查);传 prod/省略=清除回官方"}}},
     "impl": model_set_default_impl, "cost": "instant", "confirm": True,
     "reachable": ["/screen/model/default"]},
    {"name": "ww_seats_decide",
     "description":
         "触发落子席位研判(哨兵 agent,LLM 真研判并落盘 var/seats_decisions.jsonl)。需要用户确认。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string"}, "name": {"type": "string"},
         "creed": {"type": "string"}, "mode": {"type": "string", "enum": ["fast", "deep"], "default": "fast"}},
      "required": ["code"]},
     "impl": seats_decide_impl, "cost": "seconds", "confirm": True,
     "reachable": ["/seats/decide", "/seats/calibration"]},
    {"name": "ww_seats_bind",
     "description":
         "为某只票在校场创建专属盯盘 agent(绑定策略 bind=该票=盯盘,显现在校场,页面开着时前端循环持续研判)。"
         "用户说『加入盯盘/配个 agent 盯住 X/专门盯这只票』时用。需用户确认。"
         "诚实:盯盘=校场绑定 agent+页面开着时前端研判,非服务器 7×24。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "股票代码,如 SZ000630 或 000630"},
         "name": {"type": "string"},
         "creed": {"type": "string", "description": "盯盘信条/重点关注条件(喂给 agent 的依据)"},
         "template": {"type": "string", "enum": ["momentum", "reversal", "event"], "default": "momentum"}},
      "required": ["code"]},
     "impl": seats_bind_impl, "cost": "instant", "confirm": True,
     "reachable": []},
    {"name": "ww_cards_query",
     "description": "查询经验卡库(draft/approved/rejected/all)。",
     "input_schema": {"type": "object", "properties": {"status": {
         "type": "string", "enum": ["draft", "approved", "rejected", "all"], "default": "all"}}},
     "impl": cards_query_impl, "cost": "instant", "confirm": False,
     "reachable": ["/cards/list"]},
    {"name": "ww_reports_query",
     "description": "检索工作流报告库(名称子串匹配)。",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string", "default": ""}}},
     "impl": reports_query_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_report_run",
     "description":
         "生成单票深度研报(真引擎 16-agent,5-8 分钟,后台跑不阻塞;完成自动通知并可翻阅)。需要用户确认。Deep-dive stock research report.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "股票代码,如 SZ300750 或 300750"},
      "name": {"type": "string"}, "asof": {"type": "string", "description": "YYYY-MM-DD,缺省今天"}},
      "required": ["code"]},
     "impl": report_run_impl, "cost": "minutes", "confirm": True,
     "reachable": []},
    {"name": "ww_show_page",
     "description":
         "把平台某个界面调出到右栏给用户看(screen=选股/factor=工作流/cards=经验卡/graph=研究图谱/seats=落子,即盯盘/席位/研判)。用户说『调出/打开/看看XX界面』时用。",
     "input_schema": {"type": "object", "properties": {"page": {"type": "string", "enum": ["screen", "factor", "cards", "graph", "seats"]}},
      "required": ["page"]},
     "impl": show_page_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_cards_save",
     "description":
         "把验证过的结论沉淀为经验卡(默认 draft)。需要用户确认。",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "insight": {"type": "string"},
      "expr": {"type": "string"}, "verdict": {"type": "string"}, "conf": {"type": "integer"},
      "ic": {"type": "string"}, "cat": {"type": "string"},
      "status": {"type": "string", "enum": ["draft", "approved"], "default": "draft"}},
      "required": ["title"]},
     "impl": cards_save_impl, "cost": "instant", "confirm": True,
     "reachable": ["/cards"]},
    {"name": "ww_memory_write",
     "description":
         "往帷幄记忆追加一条。稳定偏好(池子/频率/风格)→ scope=global;仅本会话相关的任务笔记 → scope=session(不污染其他会话)。",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string"},
         "scope": {"type": "string", "enum": ["global", "session"], "default": "global",
                   "description": "稳定偏好(池子/频率/风格)→ global;仅本会话相关的任务笔记 → session"},
         "key": {"type": "string", "default": "",
                 "description": "可选·同主题去重键(仅 global 生效)。给定后同 key 旧条目先删再写 = 收敛覆盖不累加,如池子偏好/频率。"}},
      "required": ["text"]},
     "impl": memory_write_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_memory_read",
     "description":
         "读取帷幄记忆(global=全局偏好 / session=本会话笔记 / all=两段拼接)。",
     "input_schema": {"type": "object", "properties": {
         "scope": {"type": "string", "enum": ["global", "session", "all"], "default": "all",
                   "description": "global=稳定偏好;session=仅本会话任务笔记;all=两段拼接"}}},
     "impl": memory_read_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_seats_history",
     "description":
         "查询落子哨兵的研判/条件单历史(全局,跨会话;最新在前)。用户问『哨兵最近研判了什么/某票的研判记录』时用。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "可选,按股票代码过滤,如 SZ300750"},
         "limit": {"type": "integer", "default": 10}}},
     "impl": seats_history_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/decisions"]},
    {"name": "ww_news_search",
     "description":
         "实时联网检索个股/大盘新闻与情绪研判(东方财富 7×24 快讯,带引用理由,无相关新闻则诚实标注不编造)。"
         "用户问『XX 最近有什么消息/大盘消息面/新闻情绪』时用。scope=stock/market/both。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "可选,个股代码如 SZ300750 或 300750"},
         "scope": {"type": "string", "enum": ["stock", "market", "both"], "default": "both"},
         "query": {"type": "string", "description": "可选,关键词过滤标题"},
         "limit": {"type": "integer", "default": 200}}},
     "impl": news_search_impl, "cost": "seconds", "confirm": False,
     "reachable": []},
    {"name": "ww_f10",
     "description":
         "查本票 F10 结构化事实(估值/总股本/事件公告/龙虎榜两融/券商评级与目标价)。数字逐字来自 "
         "F10 档案不经 LLM,缺料诚实标注不编造。带 asof(YYYY-MM-DD)即历史口径(PIT 裁晚于该日的行,不前视)。"
         "category 可选 估值/事件/龙虎榜/券商 只取一段;keyword 过滤事件标题。"
         "用户问『XX 的基本面/总股本/有什么公告/券商目标价/两融余额』时用。F10 corporate facts.",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "股票代码,如 SZ000630 或 000630"},
         "category": {"type": "string", "enum": ["估值", "事件", "龙虎榜", "券商"],
                      "description": "可选,只取某一段;省略=全量"},
         "asof": {"type": "string", "description": "可选 YYYY-MM-DD,历史口径(PIT,不前视);省略=最新"},
         "keyword": {"type": "string", "description": "可选,按子串过滤事件标题"}},
      "required": ["code"]},
     "impl": f10_impl, "cost": "seconds", "confirm": False,
     "reachable": []},
    {"name": "ww_factorlib_save",
     "description":
         "把一条因子表达式存进因子库并注册进引擎(校验+落盘 mined/+运行期注册→选股/工作流可复用)。"
         "用户说『把这条因子存下来/入库/沉淀成因子』时用。需用户确认。is_qlib=true 则先把 Qlib 形($close/Ref/Std)译成 zoo。",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "因子名(唯一,重名后端拒绝覆盖)"},
         "expr": {"type": "string", "description": "zoo 因子表达式,如 rank(-delta(close,20));is_qlib=true 时填 Qlib 形"},
         "family": {"type": "string", "default": "library_mined"},
         "description": {"type": "string"},
         "is_qlib": {"type": "boolean", "default": False}},
      "required": ["name", "expr"]},
     "impl": factorlib_save_impl, "cost": "seconds", "confirm": True,
     "reachable": ["/factorlib/save"]},
    {"name": "ww_update_data",
     "description":
         "增量更新行情数据(pytdx+腾讯,quick只日线/full含5min+daily_basic)。用户说『更新数据/拉最新/同步行情』时用。"
         "需用户确认(codes=all 是全市场 5-10 分钟重拉)。",
     "input_schema": {"type": "object", "properties": {
         "codes": {"type": "string", "description": "逗号分隔代码 SH600519,SZ300750;all=全市场(慎用);省略=全部 instruments"},
         "mode": {"type": "string", "enum": ["quick", "full"], "default": "quick"}}},
     "impl": update_data_impl, "cost": "seconds", "confirm": True,
     "reachable": []},
    {"name": "ww_news_collect",
     "description":
         "从上游抓最新新闻入本地库(快讯/龙虎榜/新浪/雪球情绪等)。news_query 查空或要最新时用。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "sources": {"type": "string", "default": "kuaixun,longhu,sinafinance",
                     "description": "逗号分隔源:kuaixun,longhu,sinafinance,shareholders,ths-hot(公开);xueqiu-*(需cookie)"},
         "limit": {"type": "integer", "default": 200},
         "code": {"type": "string", "description": "仅 xueqiu-comments 个股情绪需要"}}},
     "impl": news_collect_impl, "cost": "seconds", "confirm": True,
     "reachable": []},
    {"name": "ww_factor_compose",
     "description":
         "多因子合成(equal/ic/icir 加权)→ 样本外 OOS 报告 + 各腿权重。用户说『把这几个因子合成/做个多因子模型』时用。只评测不入库。",
     "input_schema": {"type": "object", "properties": {
         "members": {"type": "array", "items": {"type": "string"},
                     "description": "≥2 个 zoo 因子表达式或已注册因子名"},
         "method": {"type": "string", "enum": ["equal", "ic", "icir"], "default": "equal"},
         "universe": {"type": "string", "default": "csi300"},
         "oos_frac": {"type": "number", "default": 0.3}},
      "required": ["members"]},
     "impl": factor_compose_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/workflow/compose"]},
    {"name": "ww_feature_build",
     "description":
         "物化特征工程(真 X/y)→ 逐特征对前向收益的 RankIC + 覆盖统计。搭多特征矩阵/做模型前的特征体检用。",
     "input_schema": {"type": "object", "properties": {
         "features": {"type": "array", "items": {"type": "string"}, "description": "zoo 特征表达式列表"},
         "label": {"type": "string", "description": "标签表达式;留空=前向收益"},
         "fwd_days": {"type": "integer", "default": 5},
         "universe": {"type": "string", "default": "csi_fast"},
         "oos_frac": {"type": "number", "default": 0.0}},
      "required": ["features"]},
     "impl": feature_build_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/feature/build"]},
    {"name": "ww_factor_fields",
     "description":
         "列出 zoo 因子 DSL 的合法字段(价量/基本面/技术/财务/参照)+算子+范例。写因子表达式前查字段名,避免拼错被校验拒绝。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": factor_fields_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_etf_report_run",
     "description":
         "生成 ETF 深度研报(持仓/技术/申赎/折溢价/风控,后台 5-8 分钟,完成通知)。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "ETF 代码,如 SH510300 或 510300"},
         "name": {"type": "string"}, "asof": {"type": "string"}},
      "required": ["code"]},
     "impl": etf_report_run_impl, "cost": "minutes", "confirm": True,
     "reachable": []},
    {"name": "ww_ledger_state",
     "description":
         "实盘台账快照:组合持仓、现金、已实现盈亏、胜率、MTM 权益(缺价诚实置空)。"
         "看组合真实成绩用它,评估研判质量用 ww_calibration。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": ledger_state_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/ledger/state"]},
    {"name": "ww_calibration",
     "description":
         "置信度校准全表:各置信档研判的真实 N 日方向命中率(『我说80%把握时实际对几成』)。"
         "复盘/评估自己研判先看它;n<5 的档样本不足仅供参考。",
     "input_schema": {"type": "object", "properties": {
         "horizon": {"type": "integer", "default": 5, "description": "N 日方向命中窗口(1-20)"}}},
     "impl": calibration_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/calibration"]},
    {"name": "ww_seats_runs",
     "description": "落子回测 run 历史头(run_id/票/时间窗/买卖观计数,新在前)。可选 code 按票过滤。",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "可选,按票数字核过滤,如 605358"},
         "limit": {"type": "integer", "default": 10}}},
     "impl": seats_runs_impl, "cost": "instant", "confirm": False,
     "reachable": ["/seats/runs"]},
    {"name": "ww_model_health",
     "description":
         "模型体检:v4 排名产物新鲜度(date/rows/陈旧天数)+ 市场宽度 as_of + 模型健康"
         "(vintage OOS IC/衰减告警,缺产物诚实缺席)。动因子/模型/选股前先用它核数据新鲜度。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": model_health_impl, "cost": "instant", "confirm": False,
     "reachable": ["/screen/health"]},
    {"name": "ww_factor_tsic",
     "description":
         "个股时序 IC:逐票 Spearman(因子值, 自身未来收益)——单票/小池口径(截面 IC 单票退化时用它)。"
         "expr 为 zoo 表达式或注册名(ww_factor_fields 查字段)。",
     "input_schema": {"type": "object", "properties": {
         "expr": {"type": "string", "description": "因子表达式/注册名"},
         "code": {"type": "string", "description": "单票代码,如 SH605358(与 codes 二选一)"},
         "codes": {"type": "array", "items": {"type": "string"}},
         "universe": {"type": "string", "description": "不给 code/codes 时的小池 id,如 csi_fast"},
         "fwd_days": {"type": "integer", "default": 20},
         "start": {"type": "string"}, "end": {"type": "string"}},
      "required": ["expr"]},
     "impl": factor_tsic_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/factor/tsic"]},
    {"name": "ww_workflow_critique",
     "description":
         "AI 批判环:给出研究目标+当前工作流 graph({nodes,edges})+该图真实回测指标,"
         "LLM 诊断问题并产改进图(LLM 不可用时规则兜底,来源诚实标注)。"
         "注意:指标由调用方自报,后端不复算。",
     "input_schema": {"type": "object", "properties": {
         "goal": {"type": "string"},
         "graph": {"type": "object", "description": "{nodes:[],edges:[]} 当前工作流图"},
         "metrics": {"type": "object",
                     "description": "该图真实回测指标 {rank_ic,sharpe,ann_return,oos_verdict,n_dates,factor}"}},
      "required": ["graph"]},
     "impl": workflow_critique_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/workflow/critique"]},
    {"name": "ww_regen",
     "description":
         "三产物再生(breadth/mainline/v4 排名重算,即选股页『拉取最新数据』):后台子进程 ~5 分钟,"
         "单飞锁防并发;wait=true(默认)轮询到完成并报新数据日。更完行情(ww_update_data)后要选股吃到新数据,必须跑它。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "end": {"type": "string", "description": "可选截止交易日 YYYY-MM-DD,缺省=最新数据日"},
         "wait": {"type": "boolean", "default": True},
         "poll_seconds": {"type": "number", "default": 15},
         "timeout_seconds": {"type": "number", "default": 600}}},
     "impl": regen_impl, "cost": "minutes", "confirm": True,
     "reachable": ["/screen/regen", "/screen/regen/status"]},
    {"name": "ww_picks_perf",
     "description":
         "查正式选股成绩单:读 picks 档案(snapshot 行),算该篮子前向持有收益 vs 全A等权基准"
         "(收盘进/收盘出,与置信校准同口径)。默认最新一次正式选股;date 可选某天;"
         "未成熟票诚实标注,基准缺失显形 null。",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "可选,选某天的正式选股档案 YYYY-MM-DD"},
         "horizon": {"type": "integer", "default": 5, "description": "持有窗口(交易日 1-60)"}}},
     "impl": picks_perf_impl, "cost": "seconds", "confirm": False,
     "reachable": ["/screen/picks", "/seats/basket_perf"]},
    {"name": "ww_capabilities",
     "description":
         "列出我(帷幄)当前能调用的全部工具及用途。用户问『你能做什么/有哪些功能/会用什么工具』,或我不确定该用哪个工具时,先调它自查。",
     "input_schema": {"type": "object", "properties": {}},
     "impl": capabilities_impl, "cost": "instant", "confirm": False,
     "reachable": []},
    {"name": "ww_endpoints",
     "description":
         "列出观澜后端的能力地图(所有端点),并标注哪些我能直接调、哪些只能在界面操作。用户问『平台/系统能做什么』或我遇到自己没有的能力时用。",
     "input_schema": {"type": "object", "properties": {
         "filter_prefix": {"type": "string", "description": "可选,只列某前缀,如 /workflow 或 /seats"}}},
     "impl": endpoints_impl, "cost": "instant", "confirm": False,
     "reachable": ["/openapi.json"]},
]


# 放行的引擎工具(已注册在 TOOL_REGISTRY,只进白名单不包装):7 原 buddy 研究 + 11 Phase-A 引擎。
# 全在 research 域、无因子域。
_ALLOWED_ENGINE_TOOLS = [
    "quote_lookup", "realtime_quote", "stock_brief", "financials",
    "news_query", "wisdom_search", "quant_reports",
    # A 新增:直接放行的只读引擎研究工具(已注册,只缺白名单)
    "iwencai_search", "ths_fund_flow", "fund_flow_change", "ths_concept_board",
    "market_status", "mainline_radar", "overseas_radar", "morning_brief",
    "quote_batch", "chain_for", "industry_show",
    # B 类:引擎 alpha-zoo 因子研究线(442 学术因子库/事件研究/炼因子;各自带 confirm 门或只读)
    "alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
    "event_report", "alpha_forge", "factor_report",
]

# run_turn 白名单 = 帷幄工具(WW_TOOL_TABLE) + 精选 buddy/引擎研究工具(_ALLOWED_ENGINE_TOOLS)。
CONSOLE_ALLOWED = {t["name"] for t in WW_TOOL_TABLE} | set(_ALLOWED_ENGINE_TOOLS)

# ww_ 工具能直接触达的后端路径,从 WW_TOOL_TABLE 的 reachable 列表并集派生(C2 可达性标注依据)。
_WW_REACHABLE_ENDPOINTS = {ep for t in WW_TOOL_TABLE for ep in t.get("reachable", [])}


def register_console_tools() -> int:
    """把帷幄工具追加进 TOOL_REGISTRY(幂等),返回帷幄工具总数。"""
    bt = _buddy_tools_mod()
    existing = {t.name for t in bt.TOOL_REGISTRY}
    for t in WW_TOOL_TABLE:
        if t["name"] not in existing:
            bt.TOOL_REGISTRY.append(bt.Tool(
                name=t["name"], description=t["description"], input_schema=t["input_schema"],
                run=_wrap(t["impl"]), cost_hint=t["cost"], confirm_required=t["confirm"]))
    return len(WW_TOOL_TABLE)
