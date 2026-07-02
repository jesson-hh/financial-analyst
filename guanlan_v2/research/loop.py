# -*- coding: utf-8 -*-
"""研究回路编排器(P2):提案→求值(小灶直调)→过门→批判改进,逐轮落档。

确定性编排,LLM 只在提案/批判两个接缝:经**同步 HTTP 自调**本进程 /workflow/generate|critique
(handler 是 router 闭包不可 import;且 engine LLM 客户端连接池绑事件循环,daemon 线程反复
asyncio.run 会炸 "Event loop is closed"——HTTP 自调让 LLM 落在 server 主循环上,零复制同一实现)。
本模块只会在 9999 进程内的 daemon 线程里跑(api.py 状态机拉起),同步自 HTTP 安全(仓级红线
只禁「协程内」同步自 HTTP)。求值三道菜直调模块级 sync 函数(与画布/帷幄 ww_factor_analyze
同一批函数,口径逐位一致)。

红线:提案失败诚实终止(绝不降级模板,严于前端 aiLoop);规则兜底显形 source=rule;
draft 绝不自动上架;多因子合成达标不自动入库(skipped_multi);失败也写教训。
独立小函数便于 monkeypatch(仓例 console/tools.py)。
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from guanlan_v2.research import store as rstore

GATE_OOS_OK = "robust"      # 过门要求的样本外判定
_EVAL_OOS_FRAC = 0.3        # 求值一律开样本外(oos 不开 verdict 恒缺 → 门永不过)


def new_run_id() -> str:
    return "rr_" + uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 桥(独立小函数便于 monkeypatch)──────────────────────────────────────

def _self_post(path: str, payload: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    """同步自 HTTP(仅 daemon 线程调用,永不进事件循环)。"""
    port = os.environ.get("GUANLAN_PORT", "9999")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(镜像 console/tools.py:_resp_json)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _call_generate(goal: str) -> Dict[str, Any]:
    return _self_post("/workflow/generate", {"goal": goal}, timeout=300)


def _call_critique(goal: str, metrics: Dict[str, Any], graph: Dict[str, Any]) -> Dict[str, Any]:
    return _self_post("/workflow/critique",
                      {"goal": goal, "metrics": metrics, "graph": graph}, timeout=300)


def _eval_report2(expr: str, p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorReport2In, _factor_report2
    return _resp_json(_factor_report2(FactorReport2In(
        expr_or_name=expr, universe=p["universe"], freq=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _eval_compose(exprs: List[str], p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import FactorComposeIn, _factor_compose
    return _resp_json(_factor_compose(FactorComposeIn(
        members=exprs, universe=p["universe"], freq=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _eval_backtest(exprs: List[str], p: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.workflow.api import BacktestVectorIn, _backtest_vector
    return _resp_json(_backtest_vector(BacktestVectorIn(
        features=exprs, universe=p["universe"], rebalance=p["freq"],
        oos_frac=_EVAL_OOS_FRAC, start=p.get("start"), end=p.get("end"))))


def _save_draft(run_id: str, k: int, expr: str, goal: str, diag: str,
                metrics: Dict[str, Any]) -> Dict[str, Any]:
    """达标因子存 factorlib(status=draft,绝不自动上架;人审 /factorlib/promote 转正)。"""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore
    name = f"lib_rl_{run_id[-6:]}_r{k}"
    body = SaveIn(name=name, expr=expr, family="library_mined",
                  description=f"研究回路产出:{goal[:60]} · {str(diag)[:80]}",
                  source="research_loop", status="draft",
                  meta={"metrics": metrics, "run_id": run_id, "round": k})
    return _save_factor(body, LibraryFactorStore())


def _save_graph(goal: str, run_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    """最佳轮图存工作流库(用户在工作流页点开上画布;与 /workflow/save 同一 store 文件)。"""
    try:
        from guanlan_v2.workflow.store import WorkflowStore
        rec = WorkflowStore().save(f"研究·{goal[:16]}·{run_id[-6:]}", graph)
        return {"ok": True, "id": rec["id"], "name": rec["name"]}
    except Exception as exc:  # noqa: BLE001 — 存图失败显形进终态行,不挡收工
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _write_lesson(goal: str, summary: str) -> bool:
    """每 run 一条 keyed 常驻记忆(失败也记);同 key 收敛覆盖=同一目标最新认知。
    单行化在此做(memory_write_impl 正常路径不去换行);cap 280 由其内做。"""
    try:
        from guanlan_v2.console.tools import memory_write_impl
        key = "研究·" + goal.replace("\n", " ").strip()[:24]
        r = memory_write_impl(text=summary.replace("\n", " ").replace("\r", " ").strip(),
                              scope="global", key=key)
        return bool(r.get("ok"))
    except Exception:  # noqa: BLE001 — 教训写失败不挡收工,memory_written=False 显形
        return False


# ── 纯函数 ───────────────────────────────────────────────────────────────

def _pick_dish(graph: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """图形状→(菜名, exprs)。formula.params.expr + factorlib.params.name;
    有 backtest 节点→backtest;≥2 表达式→compose;1→report2;0→(None,[]) 诚实不支持。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    exprs: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        if n.get("type") == "formula":
            e = str(p.get("expr") or "").strip()
            if e:
                exprs.append(e)
        elif n.get("type") == "factorlib":
            e = str(p.get("name") or p.get("expr") or "").strip()
            if e:
                exprs.append(e)
    if not exprs:
        return None, []
    if any(isinstance(n, dict) and n.get("type") == "backtest" for n in nodes):
        return "backtest", exprs
    if len(exprs) >= 2:
        return "compose", exprs
    return "report2", exprs


def _metrics_of(resp: Dict[str, Any], factor: str) -> Dict[str, Any]:
    """求值响应→六键(镜像前端 metricsOf;compose 嵌套 composite **dict** 先展开——
    report2 的 composite 是 bool 标志,isinstance 区分)。"""
    r = resp.get("composite") if isinstance(resp.get("composite"), dict) else resp
    hic = r.get("headline_ic") if isinstance(r.get("headline_ic"), dict) else {}
    ic = r.get("ic") if isinstance(r.get("ic"), dict) else {}
    met = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
    pf = r.get("portfolio") if isinstance(r.get("portfolio"), dict) else {}
    oos = r.get("oos") if isinstance(r.get("oos"), dict) else {}
    rank_ic = hic.get("rank_ic")
    if rank_ic is None:
        rank_ic = ic.get("rank_ic_mean")
    if rank_ic is None:
        rank_ic = met.get("rank_ic")
    return {"rank_ic": rank_ic, "sharpe": pf.get("sharpe"), "ann_return": pf.get("ann_return"),
            "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"), "factor": factor}


def _gate(metrics: Dict[str, Any], min_rank_ic: float) -> Dict[str, Any]:
    """过门:rank_ic ≥ 门槛 且样本外=robust(保守;draft 只是抽屉,严一点无妨)。"""
    ric = metrics.get("rank_ic")
    ok_num = isinstance(ric, (int, float)) and ric == ric
    passed = bool(ok_num and float(ric) >= float(min_rank_ic)
                  and metrics.get("oos_verdict") == GATE_OOS_OK)
    return {"passed": passed, "min_rank_ic": float(min_rank_ic), "oos_required": GATE_OOS_OK}


# ── 主体 ─────────────────────────────────────────────────────────────────

def run_research_loop(run_id: str, goal: str, max_rounds: int, min_rank_ic: float,
                      universe: str, freq: str, start: Optional[str], end: Optional[str],
                      progress: Callable[..., None]) -> Dict[str, Any]:
    """回路主体(daemon 线程内跑)。返回终态行(已落档)。progress(**kw) 由 api.py 状态机提供。"""
    params = {"max_rounds": max_rounds, "min_rank_ic": min_rank_ic,
              "universe": universe, "freq": freq, "start": start, "end": end}
    rounds_ok = rstore.append_run({"run_id": run_id, "kind": "start", "goal": goal,
                                   "params": params, "ts": _now()})
    rounds: List[Dict[str, Any]] = []
    promoted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    # ── 提案(失败诚实终止,绝不降级模板)──
    progress(phase="propose", label="① LLM 生成初始工作流…", round_k=0)
    try:
        gen = _call_generate(goal)
    except Exception as exc:  # noqa: BLE001
        gen = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not gen.get("ok") or not isinstance(gen.get("graph"), dict):
        error = f"提案失败(诚实终止,不降级模板): {gen.get('reason')}"
        mem = _write_lesson(goal, f"研究「{goal[:40]}」提案即失败:{str(gen.get('reason'))[:120]}")
        end_row = {"run_id": run_id, "kind": "end", "ok": False, "error": error,
                   "n_rounds": 0, "best_k": None, "best_metrics": None, "promoted": None,
                   "workflow_saved": None, "memory_written": mem,
                   "rounds_recorded": rounds_ok, "ts": _now()}
        rstore.append_run(end_row)
        return end_row

    graph: Dict[str, Any] = gen["graph"]
    diag = "初始生成(LLM propose)"
    crit_source: Optional[str] = None
    for k in range(max_rounds):
        # ── 求值(后端真算;消掉 critique「指标自报」缺口)──
        progress(phase="evaluate", label=f"② 第 {k + 1}/{max_rounds} 轮 · 后端真算指标…", round_k=k)
        dish, exprs = _pick_dish(graph)
        failed, err = False, None
        metrics: Dict[str, Any] = {}
        if dish is None:
            failed, err = True, "不支持的图形状(非配方模板)"
        else:
            try:
                if dish == "report2":
                    resp = _eval_report2(exprs[0], params)
                elif dish == "compose":
                    resp = _eval_compose(exprs, params)
                else:
                    resp = _eval_backtest(exprs, params)
                if resp.get("ok") is False or resp.get("status") not in (None, "ok"):
                    failed, err = True, str(resp.get("reason") or resp.get("status") or "求值失败")
                else:
                    metrics = _metrics_of(resp, " + ".join(exprs))
            except Exception as exc:  # noqa: BLE001
                failed, err = True, f"{type(exc).__name__}: {exc}"
        gate = (_gate(metrics, min_rank_ic) if not failed
                else {"passed": False, "min_rank_ic": float(min_rank_ic), "oos_required": GATE_OOS_OK})
        row = {"run_id": run_id, "k": k, "ts": _now(),
               "stage": ("propose" if k == 0 else "improve"),
               "diag": diag, "critique_source": crit_source,
               "exprs": exprs, "dish": dish, "metrics": metrics, "gate": gate,
               "failed": failed, "error": err, "graph": graph}
        if not rstore.append_round(row):
            rounds_ok = False
        rounds.append(row)
        # ── 过门 → draft 入库 + 提前收工 ──
        if gate["passed"]:
            progress(phase="promote", label="③ 达标 · 存 draft 入库(待人审)…", round_k=k)
            if len(exprs) == 1:
                try:
                    pr = _save_draft(run_id, k, exprs[0], goal, diag, metrics)
                except Exception as exc:  # noqa: BLE001
                    pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
                promoted = ({"name": pr.get("name"), "status": "draft"} if pr.get("ok")
                            else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
            else:
                promoted = {"name": None, "status": "skipped_multi",
                            "reason": "多因子合成暂不自动入库(库以单表达式为单位),成分见轮次档案"}
            break
        if k + 1 >= max_rounds:
            break
        # ── 批判改进(LLM;失败规则兜底显形;批判环整体失败 → 诚实中断)──
        progress(phase="critique", label=f"④ 第 {k + 1} 轮未达标 · LLM 批判改进…", round_k=k)
        try:
            cr = _call_critique(goal, metrics, graph)
        except Exception as exc:  # noqa: BLE001
            cr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not cr.get("ok") or not isinstance(cr.get("graph"), dict):
            error = f"批判环失败: {cr.get('reason')}"
            break
        crit_source = str(cr.get("source") or "?")
        diag = (("(规则兜底·非 LLM) " if crit_source == "rule" else "")
                + str(cr.get("diagnosis") or ""))
        graph = cr["graph"]

    # ── 收工三件套:最佳图入工作流库 + 教训 + 终态行 ──
    best: Optional[Dict[str, Any]] = None
    for r in rounds:
        ric = (r.get("metrics") or {}).get("rank_ic")
        if r.get("failed") or not isinstance(ric, (int, float)) or ric != ric:
            continue
        if (r.get("gate") or {}).get("passed"):
            best = r
            break
        if best is None or float(ric) > float((best.get("metrics") or {}).get("rank_ic")):
            best = r
    ws = _save_graph(goal, run_id, best["graph"]) if best else None
    n = len(rounds)
    bm = (best or {}).get("metrics") or {}
    if promoted and promoted.get("status") == "draft":
        lesson = (f"研究「{goal[:40]}」{n}轮达标:{promoted['name']} rank_ic={bm.get('rank_ic')} "
                  f"oos={bm.get('oos_verdict')} 已入draft待人审;诊断:{str(diag)[:80]}")
    elif error:
        lesson = f"研究「{goal[:40]}」{n}轮中断:{str(error)[:120]}"
    else:
        lesson = (f"研究「{goal[:40]}」{n}轮未达标(门 rank_ic≥{min_rank_ic} 且 oos=robust):"
                  f"最佳 rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')};诊断:{str(diag)[:80]}")
    mem_ok = _write_lesson(goal, lesson)
    end_row = {"run_id": run_id, "kind": "end", "ok": error is None, "error": error,
               "n_rounds": n, "best_k": (best or {}).get("k"),
               "best_metrics": (bm or None), "promoted": promoted,
               "workflow_saved": ws, "memory_written": mem_ok,
               "rounds_recorded": rounds_ok, "ts": _now()}
    rstore.append_run(end_row)
    return end_row
