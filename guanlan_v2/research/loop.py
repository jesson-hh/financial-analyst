# -*- coding: utf-8 -*-
"""研究回路编排器(P2/P4):提案→求值(整图执行)→过门→批判改进,逐轮落档。

确定性编排,LLM 只在提案/批判两个接缝:经**同步 HTTP 自调**本进程 /workflow/generate|critique
(handler 是 router 闭包不可 import;且 engine LLM 客户端连接池绑事件循环,daemon 线程反复
asyncio.run 会炸 "Event loop is closed"——HTTP 自调让 LLM 落在 server 主循环上,零复制同一实现)。
本模块只会在 9999 进程内的 daemon 线程里跑(api.py 状态机拉起),同步自 HTTP 安全(仓级红线
只禁「协程内」同步自 HTTP)。求值段(P4)整图交给 workflow.executor.run_graph 真执行——节点
参数(ML 超参/回测 topn/analysis dir 等)全部真生效,不再是"三道菜"(report2/compose/backtest)
直调固定形状的窄口径。

红线:提案失败诚实终止(绝不降级模板,严于前端 aiLoop);规则兜底显形 source=rule;
产物一律 draft 人审,绝不自动上架(P4 三通道:ML 图→模型 train_promote 强制 draft;
≥2 表达式→组合权重物化 draft;单表达式→现状;skipped_multi 语义已退役);失败也写教训。
停滞守卫(2026-07-03 真机 v4-pro 暴露,P4 升级为图签名比较):批判若产出与上一轮完全相同的图
(graph_signature 相等)则指标必然逐位不变=烧轮次装研究;批判环显式声明求值语义(constraints),
改进产出同签名图 → 带停滞警告重批一次,仍相同 → 诚实中断。
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
from guanlan_v2.workflow import executor as wex

GATE_OOS_OK = "robust"      # 过门要求的样本外判定
_EVAL_OOS_FRAC = 0.3        # 求值一律开样本外(oos 不开 verdict 恒缺 → 门永不过)

# 批判环求值语义声明(经 CritiqueIn.constraints 送进 LLM prompt,P4:整图执行器真跑全部节点)。
_CRITIQUE_CONSTRAINTS = (
    "本图由研究回路后端全图执行:所有节点参数均真实生效(ML 超参、回测 topn/weighting、"
    "分析 rebal/groups/dir、组合 combine 等都会改变指标);股票池与调仓频率由回路参数固定,"
    "图内数据源节点的 universe 不生效。改进可落在:因子表达式、特征组合、模型类型与超参、"
    "多因子合成方式、回测/分析参数。多特征 ML:把多个 formula 各连一条边到同一个 feature "
    "节点的 feat 口即聚合为多特征训练(保序去重);其余同一输入口多条边仅最后一条生效并记"
    "警告。与上一轮完全相同的图不会产生不同指标。")


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


def _call_generate(goal: str) -> Dict[str, Any]:
    return _self_post("/workflow/generate", {"goal": goal}, timeout=300)


def _call_critique(goal: str, metrics: Dict[str, Any], graph: Dict[str, Any],
                   constraints: str = "") -> Dict[str, Any]:
    return _self_post("/workflow/critique",
                      {"goal": goal, "metrics": metrics, "graph": graph,
                       "constraints": constraints}, timeout=300)


def _run_graph_eval(graph: Dict[str, Any], p: Dict[str, Any],
                    progress: Callable[..., None], k: int, max_rounds: int) -> Dict[str, Any]:
    """整图执行薄桥(独立便于 monkeypatch)。on_node 把当前节点类型打进进度 label。"""
    def _on(nid, typ, state):
        if state == "running":
            progress(phase="evaluate",
                     label=f"② 第 {k + 1}/{max_rounds} 轮 · 图执行:{typ}…", round_k=k)
    return wex.run_graph(graph, overrides={
        "universe": p["universe"], "freq": p["freq"], "oos_frac": _EVAL_OOS_FRAC,
        "start": p.get("start"), "end": p.get("end")}, on_node=_on)


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


def _save_compose_expr(name: str, expr: str, goal: str, diag: str,
                       meta: Dict[str, Any]) -> Dict[str, Any]:
    """组合物化表达式存 factorlib draft(独立便于 monkeypatch)。"""
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore
    body = SaveIn(name=name, expr=expr, family="library_mined",
                  description=f"研究回路组合产出:{goal[:60]} · {str(diag)[:80]}",
                  source="research_loop", status="draft", meta=meta)
    return _save_factor(body, LibraryFactorStore())


def _call_train_promote(spec: Dict[str, Any]) -> Dict[str, Any]:
    from guanlan_v2.strategy.compute.model_workflow import train_promote
    return train_promote(spec)


def _derive_ml_recipe(graph: Dict[str, Any], universe: str) -> Optional[Dict[str, Any]]:
    """镜像前端 deriveRecipeForNode(workflow.jsx:779-807):首个 ML 节点 → recipe。
    features=其上游 feature 节点 feat 口表达式保序去重;label 同前端语义;params 经
    executor._HPMAP 画布名→后端名;universe=回路 run 参数权威。查无 ML 节点 → None。"""
    from guanlan_v2.workflow.executor import _HPMAP, _ML_KINDS, _num
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    ml = next((n for n in nodes if isinstance(n, dict) and n.get("type") in _ML_KINDS), None)
    if ml is None:
        return None
    feats: List[str] = []
    label: Optional[str] = None
    for e in edges:
        to = e.get("to") or [None, None]
        if to[0] != ml.get("id") or to[1] != "fe":
            continue
        fn = by_id.get((e.get("from") or [None])[0])
        if not (fn and fn.get("type") == "feature"):
            continue
        for e2 in edges:
            t2 = e2.get("to") or [None, None]
            if t2[0] != fn.get("id"):
                continue
            up = by_id.get((e2.get("from") or [None])[0])
            expr = str(((up or {}).get("params") or {}).get("expr") or "").strip()
            if t2[1] == "feat" and expr and expr not in feats:
                feats.append(expr)
            if t2[1] == "label" and label is None and expr:
                label = expr
        if label is None:
            tag = str((fn.get("params") or {}).get("tag") or "").strip()
            if tag.lower() not in ("", "ic", "fwd_ret"):
                label = tag
    params: Dict[str, Any] = {}
    for bk, ck in _HPMAP.get(ml["type"], {}).items():
        v = (ml.get("params") or {}).get(ck)
        if v in (None, ""):
            continue
        n = _num(v)
        params[bk] = (int(n) if (n is not None and float(n).is_integer())
                      else (n if n is not None else v))
    return {"kind": _ML_KINDS[ml["type"]], "features": feats, "label": label,
            "fwd_days": 5, "universe": universe, "params": params}


def _route_product(run_id: str, k: int, graph: Dict[str, Any], goal: str, diag: str,
                   metrics: Dict[str, Any], terminal: Optional[Dict[str, Any]],
                   universe: str) -> Dict[str, Any]:
    """达标产物三通道路由(spec §4):ML 图→模型 draft;≥2 表达式→组合物化 draft;单→现状。"""
    recipe = _derive_ml_recipe(graph, universe)
    if recipe is not None:                                  # ── 模型通道
        if not recipe["features"]:
            return {"name": None, "status": "save_failed",
                    "reason": "ML 图缺 feature 上游表达式,无法提取 recipe"}
        spec = {"variant_id": f"m_rl_{run_id[-6:]}_r{k}",
                "name": f"研究·{goal[:12]}·r{k}", "kind": recipe.pop("kind"),
                "recipe": recipe, "created": _now(), "status": "draft"}
        try:
            pr = _call_train_promote(spec)
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if pr.get("ok"):
            return {"name": pr.get("variant_id"), "status": "draft_model"}
        return {"name": None, "status": "save_failed", "reason": pr.get("reason")}
    _, exprs2 = _pick_dish(graph)
    if len(exprs2) >= 2:                                    # ── 组合通道(权重物化)
        payload = (terminal or {}).get("payload") or {}
        members = payload.get("members") or exprs2
        weights = payload.get("weights") or []
        wvals = [(w or {}).get("weight") for w in weights]
        if len(wvals) != len(members) or any(not isinstance(w, (int, float)) for w in wvals):
            wvals = [round(1.0 / len(members), 4)] * len(members)   # 无权重 → 等权(诚实缺省)
        expr = " + ".join(f"({w})*({m})" for m, w in zip(members, wvals))
        name = f"lib_rl_{run_id[-6:]}_r{k}"
        try:
            pr = _save_compose_expr(name, expr, goal, diag,
                                    {"members": members, "weights": weights,
                                     "metrics": metrics, "run_id": run_id, "round": k})
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        return ({"name": pr.get("name") or name, "status": "draft_compose"} if pr.get("ok")
                else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
    if len(exprs2) == 1:                                    # ── 单因子通道(现状)
        try:
            pr = _save_draft(run_id, k, exprs2[0], goal, diag, metrics)
        except Exception as exc:  # noqa: BLE001
            pr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        return ({"name": pr.get("name"), "status": "draft"} if pr.get("ok")
                else {"name": None, "status": "save_failed", "reason": pr.get("reason")})
    return {"name": None, "status": "save_failed", "reason": "图内无可入库表达式"}


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


def _gate(metrics: Dict[str, Any], min_rank_ic: float) -> Dict[str, Any]:
    """过门(P4 联合门):rank_ic ≥ 门槛 且 样本外 robust 且 组合 Sharpe > 0。"""
    ric, shp = metrics.get("rank_ic"), metrics.get("sharpe")
    ok_ric = isinstance(ric, (int, float)) and ric == ric
    ok_shp = isinstance(shp, (int, float)) and shp == shp and shp > 0
    passed = bool(ok_ric and float(ric) >= float(min_rank_ic)
                  and metrics.get("oos_verdict") == GATE_OOS_OK and ok_shp)
    return {"passed": passed, "min_rank_ic": float(min_rank_ic),
            "oos_required": GATE_OOS_OK, "sharpe_required": True}


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
        # ── 求值(后端整图执行;消掉 critique「指标自报」缺口)──
        progress(phase="evaluate", label=f"② 第 {k + 1}/{max_rounds} 轮 · 后端整图执行…", round_k=k)
        try:
            ex = _run_graph_eval(graph, params, progress, k, max_rounds)
        except Exception as exc:  # noqa: BLE001
            ex = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "metrics": None,
                  "exprs": [], "has_ml": False, "node_errors": [], "terminal": None}
        failed = not ex.get("ok")
        err = ex.get("reason") if failed else None
        metrics = ex.get("metrics") or {}
        exprs = list(ex.get("exprs") or [])
        dish, _ = _pick_dish(graph)                    # 展示口径保留(产物路由 T5 也用)
        gate = (_gate(metrics, min_rank_ic) if not failed
                else {"passed": False, "min_rank_ic": float(min_rank_ic),
                      "oos_required": GATE_OOS_OK, "sharpe_required": True})
        row = {"run_id": run_id, "k": k, "ts": _now(),
               "stage": ("propose" if k == 0 else "improve"),
               "diag": diag, "critique_source": crit_source,
               "exprs": exprs, "dish": dish, "metrics": metrics, "gate": gate,
               "failed": failed, "error": err, "graph": graph,
               "terminal_kind": ((ex.get("terminal") or {}) or {}).get("kind"),
               "node_errors": (ex.get("node_errors") or [])[:5]}
        if not rstore.append_round(row):
            rounds_ok = False
        rounds.append(row)
        # ── 过门 → draft 入库 + 提前收工 ──
        if gate["passed"]:
            progress(phase="promote", label="③ 达标 · 产物入库(draft 待人审)…", round_k=k)
            promoted = _route_product(run_id, k, graph, goal, diag, metrics,
                                      ex.get("terminal"), universe)
            break
        if k + 1 >= max_rounds:
            break
        # ── 批判改进(LLM;失败规则兜底显形;批判环整体失败 → 诚实中断)──
        progress(phase="critique", label=f"④ 第 {k + 1} 轮未达标 · LLM 批判改进…", round_k=k)
        try:
            cr = _call_critique(goal, metrics, graph, constraints=_CRITIQUE_CONSTRAINTS)
        except Exception as exc:  # noqa: BLE001
            cr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not cr.get("ok") or not isinstance(cr.get("graph"), dict):
            error = f"批判环失败: {cr.get('reason')}"
            break
        # ── 停滞守卫(P4:图签名比较):改进图与本轮图签名相同 → 指标必不变(求值确定性),
        #    复算是烧轮次装研究。带停滞警告重批一次;仍相同 → 诚实中断。──
        retried = False
        if wex.graph_signature(cr["graph"]) == wex.graph_signature(graph):
            progress(phase="critique", label="④b 改进图与上一轮相同 · 停滞重批…", round_k=k)
            fb = (_CRITIQUE_CONSTRAINTS
                  + f"\n【停滞警告】你上一份改进(诊断:{str(cr.get('diagnosis'))[:100]})图与上一轮"
                    "完全相同(签名一致),指标必然与本轮完全相同。这次必须对图做出实质修改"
                    "(换表达式/换模型类型或超参/改合成方式或回测参数皆可)。")
            try:
                cr2 = _call_critique(goal, metrics, graph, constraints=fb)
            except Exception as exc:  # noqa: BLE001
                cr2 = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            if (cr2.get("ok") and isinstance(cr2.get("graph"), dict)
                    and wex.graph_signature(cr2["graph"]) != wex.graph_signature(graph)):
                cr, retried = cr2, True
            else:
                error = ("批判环停滞: 两次改进产出的图与上一轮完全相同(签名一致,节点参数亦未变),"
                         "指标不会变,诚实中断")
                break
        crit_source = str(cr.get("source") or "?")
        diag = (("(停滞重批) " if retried else "")
                + ("(规则兜底·非 LLM) " if crit_source == "rule" else "")
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
    if promoted and promoted.get("status") in ("draft", "draft_compose", "draft_model"):
        lesson = (f"研究「{goal[:40]}」{n}轮达标:{promoted['name']}(status={promoted['status']}) "
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')} 已入draft待人审;"
                  f"诊断:{str(diag)[:80]}")
    elif promoted and promoted.get("status") == "save_failed":
        lesson = (f"研究「{goal[:40]}」{n}轮达标但入库失败:{str(promoted.get('reason'))[:80]};"
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')}")
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
