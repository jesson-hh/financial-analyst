# P4 研究回路全图执行升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 后端图执行器(24 类节点直调模块函数)替换研究回路小灶求值,加 Sharpe>0 联合门、产物三通道 draft、vintage 前向跟踪。

**Architecture:** 新模块 `guanlan_v2/workflow/executor.py` 镜像前端 runGraph(拓扑/股票池回溯/端口传递/失败不中断),计算全部直调 `workflow/api.py` 既有模块级函数;`research/loop.py` 求值段换执行器;产物按图形状路由三通道(单因子/组合物化/模型 train_promote);vintage 扫描面并入 draft。

**Tech Stack:** Python 3.11 / FastAPI / pydantic / pytest;前端 babel-standalone JSX(只填充)。

Spec: `docs/superpowers/specs/2026-07-03-p4-graph-executor-research-loop-design.md`(唯一需求源)。

## Global Constraints(逐字来自 spec)

- 门 = `rank_ic ≥ min_rank_ic 且 oos_verdict=="robust" 且 sharpe > 0`;gate dict 记 `sharpe_required: true`。
- 达标产物一律 draft、采纳永远人审;`promoted.status` 枚举:`draft | draft_compose | draft_model | save_failed | null`(`skipped_multi` 退役)。
- 主终端优先级 `backtest > analysis > iccalc`;诊断终端(tsic/event/relstat/risk/garch/attrib/tvbeta)照跑存档不参与过门;metrics 抽不出 → `null` + warning,绝不编数。
- 节点失败不中断:记 `node_errors` 继续跑;无主终端 → `ok:false`。
- overrides(universe/freq/oos_frac 恒锁;start/end 给了才压)压过图内 source;研究回路恒传 `oos_frac=0.3`。
- 停滞守卫比较键 = 规范化图签名(nodes 的 type+params + edges,**不含 x/y**)。
- 无新 env 开关、无定时器、无新 ww_ 工具(守护计数 **44/69/48 不动**);`/research/*` 契约不变;不碰 engine/。
- executor 纯同步,只在 daemon 线程 / FastAPI 线程池跑,绝不进事件循环协程。
- 测试命令:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest`;全量回归基线 ≥840 passed 0 failed。
- UI 只填充不重建;改 jsx 必 Edit bump 对应 html `?v=`(本期 `20260703p4`)。
- 分支从 main(`b67fdda` 或更新)新开 `p4-graph-executor`;台账 `.superpowers/sdd/progress-p4.md`(progress.md 属并行会话)。
- 真机 e2e 亲手执行绝不转包;9998 隔离端口;FA_CONFIG_DIR 隔离配置 deepseek-v4-pro;生产 9999 全程不碰,收尾重启。

## 关键既有契约(实现者速查,均已核实)

- 直调函数(`guanlan_v2/workflow/api.py` 模块级):`_train_eval(body, kind)->JSONResponse`(kind∈xgboost/lightgbm/svm/rf/mlp)、`_lstm_eval(body)`、`_pca_factor(body)`、`_spearman_factor(body)`、`_factor_compose(body)`、`_factor_report2(body)`、`_backtest_vector(body)`、`_portfolio_build(body)`、`_factor_tsic/_factor_event/_factor_relstat/_factor_risk/_garch/_attrib/_tvbeta(body)`。
- 入参模型:`ModelTrainIn`(kind/features/label/fwd_days=5/universe/codes/benchmark/leader/oos_frac/start/end/freq/params…);`PCAFactorIn(ModelTrainIn)+k/component`;`SpearmanFactorIn(ModelTrainIn)`;`BacktestVectorIn(ModelTrainIn)+topn=30/cash/rebalance="month"/weighting/vol_forecast`;`PortfolioBuildIn(ModelTrainIn)+topn/weighting/vol_forecast/max_weight/industry_neutral`;`FactorComposeIn(ModelTrainIn)+members/method/n_groups/direction/freq`;`FactorReport2In(BaseModel)`(expr_or_name/universe/codes/benchmark/leader/oos_frac/start/end/freq/n_groups/fwd_days/direction/neutralize);`FactorTsicIn(ModelTrainIn)+expr_or_name/fwd_days=20/direction`;`FactorEventIn(ModelTrainIn)+trigger/horizons/direction`;`FactorRelstatIn(ModelTrainIn)+expr_or_name`;`FactorRiskIn/FactorGarchIn(+horizon=12)/FactorAttribIn/FactorTVBetaIn` 均继承 `BacktestVectorIn`(因子走 features)。
- compose 响应:顶层 `{ok, weights, members, composite:{...报告+weights+members}}`;`weights=[{name,weight,ic?,icir?}]` 与 members **按序对齐**,weight 已 round(4),equal 时=1/n。
- 模型入库:`guanlan_v2/strategy/compute/model_workflow.py: train_promote(spec)->dict` 模块级直调;`spec={variant_id,name,kind,recipe:{features,label,fwd_days,universe,start,end,params},created}`;kind 限 `("lightgbm","xgboost","rf")`,非树 → `{ok:False,reason:"kind 'X' 暂不支持生产入库…"}`(诚实,直接映射 save_failed);`_apply_promote_gate` 只降不升。
- registry:`guanlan_v2/screen/model_registry.py: save_variant(vid, ranking_df, meta)`;`meta.status=="draft"` 不进正式列表(:113)。
- factorlib:`LibraryFactorStore.list_factors(validate=False)->[{name,expr,family,source,origin,status?…}]`;`_save_factor(SaveIn, store)`、SaveIn 含 status/meta(P2)。
- vintage:`guanlan_v2/screen/factor_vintage.py: compute_factor_vintage(universe,years,horizon,end,pool_codes)` 扫 `FACTOR_DEFS`(:65);`cs_vintage_asof(factor_id, date)->{ic,n,dir,asof}|None`。
- 前端镜像源:`ui/factor/workflow.jsx` — topoOrder:715 / _universeForNode:747 / _universeOf:246 / _trainModel:272(hpMap payload)/ _modelReport:297 / NODE_EXEC:311 / deriveRecipeForNode:779 / TERMINAL_DT:817。
- research/loop.py 现状(b6e7841 后):`_pick_dish/_metrics_of/_gate/_save_draft/_write_lesson/_save_graph/_call_generate/_call_critique(constraints)/_CRITIQUE_CONSTRAINTS/停滞守卫(比较 _pick_dish)`。

## File Structure

- Create `guanlan_v2/workflow/executor.py` — 图执行器(纯函数层+分发表+run_graph)。
- Create `tests/test_workflow_executor.py`、`tests/test_workflow_run_endpoint.py`。
- Modify `guanlan_v2/workflow/api.py` — 挂 `POST /workflow/run`(薄壳)。
- Modify `guanlan_v2/research/loop.py` — 求值段/门/停滞/产物路由/教训文案。
- Modify `guanlan_v2/strategy/compute/model_workflow.py` — spec["status"] 强制 draft 钩子(只降不升)。
- Modify `guanlan_v2/screen/factor_vintage.py` — 扫描面并入 draft。
- Modify `guanlan_v2/factorlib/api.py` — list 响应给 draft 附 vintage。
- Modify `ui/screen/screen-app.jsx`(vintage 徽章)、`ui/seats/luozi-panels.jsx`(promoBadge 两态)、两 html `?v=`。
- Modify `tests/test_research_loop.py`、`tests/test_model_workflow_promote.py`、`tests/test_factorlib_draft.py`。

---

### Task 0: 开分支+台账

- [ ] `git checkout -b p4-graph-executor main`
- [ ] 建 `.superpowers/sdd/progress-p4.md`(标题+Plan/Spec 路径+空 Ledger),`git add + commit -m "chore(p4): 开工台账"`

### Task 1: 执行器纯函数层

**Files:** Create `guanlan_v2/workflow/executor.py`;Test `tests/test_workflow_executor.py`

**Interfaces (Produces):**
- `topo_order(nodes: list, edges: list) -> list[str]`
- `universe_for_node(nid: str, nodes: list, edges: list) -> dict`(键:universe,start,end,codes,benchmark,leader,oos_frac,wf_refit,wired)
- `graph_signature(graph: dict) -> str`(sha1 hex;不含 x/y)
- `metrics_of_terminal(payload: dict) -> dict`(六键 rank_ic/sharpe/ann_return/oos_verdict/n_dates/factor)
- `_universe_of(params: dict) -> str`、`_resp_json(resp) -> dict`

- [ ] **Step 1: 写失败测试**(文件头 `# -*- coding: utf-8 -*-`,下同)

```python
# -*- coding: utf-8 -*-
"""P4 执行器纯函数层单测:拓扑决序/池回溯/图签名/指标抽取。零引擎零网络。"""
import guanlan_v2.workflow.executor as ex


def _n(nid, typ, x=0, y=0, **params):
    return {"id": nid, "type": typ, "x": x, "y": y, "params": params}


def _e(f, fp, t, tp):
    return {"from": [f, fp], "to": [t, tp]}


def test_topo_order_kahn_x_tiebreak_and_cycle_fallback():
    nodes = [_n("b", "formula", x=200), _n("a", "source", x=50), _n("c", "feature", x=400)]
    edges = [_e("a", "data", "c", "src"), _e("b", "out", "c", "feat")]
    assert ex.topo_order(nodes, edges) == ["a", "b", "c"]      # 入度0 按 x 决序
    cyc = [_n("p", "formula", x=10), _n("q", "feature", x=20)]
    ce = [_e("p", "out", "q", "feat"), _e("q", "fe", "p", "out")]  # 环
    assert sorted(ex.topo_order(cyc, ce)) == ["p", "q"]        # 兜底不丢节点不死循环


def test_universe_for_node_multi_hop_and_fallback():
    nodes = [_n("s", "source", universe="csi300_active", oos_frac=0.3),
             _n("f", "formula", expr="rank(close)"), _n("fe", "feature"), _n("m", "xgb")]
    edges = [_e("s", "data", "fe", "src"), _e("f", "out", "fe", "feat"), _e("fe", "fe", "m", "fe")]
    u = ex.universe_for_node("m", nodes, edges)                # 多跳回溯 m→fe→s
    assert u["wired"] is True and u["universe"] == "csi300_active" and u["oos_frac"] == 0.3
    assert ex.universe_for_node("f", nodes, edges)["wired"] is False


def test_universe_of_mirror():
    assert ex._universe_of({"universe": "csi500"}) == "csi500"
    assert ex._universe_of({"universe": "自动", "code": "sh000300"}) == "csi300_active"
    assert ex._universe_of({"scope": "全市场"}) == "all"
    assert ex._universe_of({}) == "csi_fast"


def test_graph_signature_ignores_xy_catches_params():
    g1 = {"nodes": [_n("a", "formula", x=1, y=2, expr="rank(close)")], "edges": []}
    g2 = {"nodes": [_n("a", "formula", x=99, y=88, expr="rank(close)")], "edges": []}
    g3 = {"nodes": [_n("a", "formula", expr="rank(-close)")], "edges": []}
    assert ex.graph_signature(g1) == ex.graph_signature(g2)    # 挪位置不算变
    assert ex.graph_signature(g1) != ex.graph_signature(g3)    # 改参数算变


def test_metrics_of_terminal_three_fallbacks_and_composite():
    rep = {"headline_ic": {"rank_ic": 0.03}, "portfolio": {"sharpe": 1.1, "ann_return": 0.2},
           "oos": {"verdict": "robust"}, "n_dates": 24}
    m = ex.metrics_of_terminal(rep)
    assert m["rank_ic"] == 0.03 and m["sharpe"] == 1.1 and m["oos_verdict"] == "robust"
    comp = {"composite": {"ic": {"rank_ic_mean": 0.02}, "portfolio": {}, "oos": {}, "n_dates": 9}}
    assert ex.metrics_of_terminal(comp)["rank_ic"] == 0.02     # composite dict 展开+二层回退
    assert ex.metrics_of_terminal({"metrics": {"rank_ic": 0.01}})["rank_ic"] == 0.01  # 三层
```

- [ ] **Step 2:** Run `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_workflow_executor.py -q` → FAIL(module 不存在)
- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""后端图执行器(P4):画布 graph JSON 服务端整图执行,直调模块级计算函数。

镜像前端 ui/factor/workflow.jsx runGraph(topoOrder:715/_universeForNode:747/_universeOf:246/
NODE_EXEC:311),计算不走 HTTP——全部直调 workflow/api.py 模块级函数(P2 三道菜直调的推广),
与画布/帷幄同一批函数口径逐位一致。纯同步:只在 daemon 线程(研究回路)或 FastAPI 线程池
(/workflow/run)里跑,绝不进事件循环协程(仓级红线)。

诚实合约:节点失败记 node_errors 继续跑(下游缺输入自然失败显形);无主终端 → ok:False;
metrics 抽不出 → None + warning,绝不编数。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

_TERMINAL_PRIORITY = ("backtest", "analysis", "iccalc")   # 主终端优先级(过门指标来源)
_DIAG_TYPES = {"tsic", "event", "relstat", "risk", "garch", "attrib", "tvbeta"}  # 照跑存档不过门


def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(镜像 research/loop.py:_resp_json)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _universe_of(params: Dict[str, Any]) -> str:
    """source 节点 params → universe(镜像 workflow.jsx:_universeOf:246)。"""
    u = str((params or {}).get("universe") or "").strip()
    if u and u != "自动":
        return u
    code = str((params or {}).get("code") or "").lower()
    scope = (params or {}).get("scope") or ""
    if "500" in code:
        return "csi500"
    if "800" in code:
        return "csi800"
    if "300" in code:
        return "csi300_active"
    if scope == "全市场":
        return "all"
    return "csi_fast"


def topo_order(nodes: List[dict], edges: List[dict]) -> List[str]:
    """Kahn 拓扑,入度0/同层按 x 决序;有环兜底按 x 追加(镜像 workflow.jsx:715-738)。"""
    ids = [n["id"] for n in nodes]
    xmap = {n["id"]: n.get("x", 0) for n in nodes}
    indeg = {i: 0 for i in ids}
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        f, t = e["from"][0], e["to"][0]
        if f in indeg and t in indeg:
            indeg[t] += 1
            adj[f].append(t)
    q = sorted([i for i in ids if indeg[i] == 0], key=lambda i: xmap[i])
    order: List[str] = []
    while q:
        cur = q.pop(0)
        order.append(cur)
        nxt = []
        for t in adj[cur]:
            indeg[t] -= 1
            if indeg[t] == 0:
                nxt.append(t)
        q.extend(sorted(nxt, key=lambda i: xmap[i]))
    if len(order) < len(ids):                                  # 环兜底
        order.extend(sorted([i for i in ids if i not in set(order)], key=lambda i: xmap[i]))
    return order


def universe_for_node(nid: str, nodes: List[dict], edges: List[dict]) -> Dict[str, Any]:
    """沿入边 BFS 回溯最近上游 source(镜像 workflow.jsx:_universeForNode:747-765)。"""
    by_id = {n["id"]: n for n in nodes}
    seen = {nid}
    frontier = [nid]
    while frontier:
        nxt: List[str] = []
        for cur in frontier:
            for e in edges:
                if e["to"][0] == cur and e["from"][0] not in seen:
                    up = by_id.get(e["from"][0])
                    if up and up.get("type") == "source":
                        p = up.get("params") or {}
                        raw = str(p.get("codes") or "").replace("，", ",").replace("、", ",") \
                            .replace(";", ",")
                        codes = [c.strip() for c in raw.split(",") if c.strip()]
                        return {"universe": _universe_of(p),
                                "start": str(p.get("start") or "").strip(),
                                "end": str(p.get("end") or "").strip(),
                                "codes": codes,
                                "benchmark": str(p.get("benchmark") or "").strip(),
                                "leader": str(p.get("leader") or "").strip(),
                                "oos_frac": float(p.get("oos_frac") or 0) or 0.0,
                                "wf_refit": (p.get("wf_refit") == "是"), "wired": True}
                    seen.add(e["from"][0])
                    nxt.append(e["from"][0])
        frontier = nxt
    return {"universe": None, "start": "", "end": "", "codes": [], "benchmark": "",
            "leader": "", "oos_frac": 0.0, "wf_refit": False, "wired": False}


def graph_signature(graph: Dict[str, Any]) -> str:
    """规范化图签名(停滞守卫比较键):type+params+edges,不含 x/y(挪位置≠改图)。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    cn = sorted([{"id": str(n.get("id")), "type": str(n.get("type")),
                  "params": (n.get("params") or {})} for n in nodes if isinstance(n, dict)],
                key=lambda d: d["id"])
    ce = sorted([json.dumps(e, sort_keys=True, ensure_ascii=False)
                 for e in edges if isinstance(e, dict)])
    blob = json.dumps({"nodes": cn, "edges": ce}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def metrics_of_terminal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """终端载荷 → 六键指标(泛化 research/loop.py:_metrics_of;composite dict 先展开)。"""
    r = payload.get("composite") if isinstance(payload.get("composite"), dict) else payload
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
            "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"),
            "factor": r.get("_label") or r.get("factor")}
```

- [ ] **Step 4:** Run 同命令 → 全 PASS
- [ ] **Step 5:** `git add guanlan_v2/workflow/executor.py tests/test_workflow_executor.py && git commit -m "feat(executor): P4 图执行器纯函数层(拓扑/池回溯/图签名/指标抽取)"`

### Task 2: 节点分发表 + run_graph

**Files:** Modify `guanlan_v2/workflow/executor.py`(追加);Test `tests/test_workflow_executor.py`(追加)

**Interfaces (Produces):**
- `run_graph(graph: dict, overrides: dict|None = None, on_node: Callable[[str,str,str],None]|None = None) -> dict`,返回键:`ok, reason, terminal:{kind,node_id,payload}|None, metrics|None, exprs:[str], has_ml:bool, node_results:{nid:{ok,type}}, node_errors:[{nid,type,error}], warnings:[str], elapsed_sec`
- `_exec_<type>(inputs, params, ctx) -> {port: payload}` 模块级 + 直调薄桥 `_call_*`(便于 monkeypatch);ctx 键:`universe,start,end,codes,benchmark,leader,oos_frac,freq,all_exprs`
- 常量 `_HPMAP`、`_ML_KINDS`;`graph_exprs(graph) -> list[str]`

- [ ] **Step 1: 写失败测试(追加)** — 全部 monkeypatch 直调薄桥,零引擎:

```python
# ── run_graph 分发(假计算函数)──────────────────────────────────────────
from fastapi.responses import JSONResponse


_G_ML = {"nodes": [
    {"id": "s", "type": "source", "x": 0, "y": 0, "params": {"universe": "csi300_active"}},
    {"id": "f", "type": "formula", "x": 0, "y": 1, "params": {"expr": "rank(-delta(close,5))"}},
    {"id": "fe", "type": "feature", "x": 1, "y": 0, "params": {"tag": "IC"}},
    {"id": "m", "type": "xgb", "x": 2, "y": 0, "params": {"trees": 120, "depth": 3, "lr": 0.08}},
    {"id": "mf", "type": "mf", "x": 3, "y": 0, "params": {}},
    {"id": "an", "type": "analysis", "x": 4, "y": 0, "params": {"rebal": "month", "groups": 10}},
], "edges": [
    {"from": ["s", "data"], "to": ["fe", "src"]}, {"from": ["f", "out"], "to": ["fe", "feat"]},
    {"from": ["fe", "fe"], "to": ["m", "fe"]}, {"from": ["m", "model"], "to": ["mf", "m1"]},
    {"from": ["fe", "fe"], "to": ["mf", "f1"]}, {"from": ["mf", "factor"], "to": ["an", "factor"]},
]}

_REPORT = {"ok": True, "status": "ok", "headline_ic": {"rank_ic": 0.04},
           "portfolio": {"sharpe": 1.2, "ann_return": 0.15}, "oos": {"verdict": "robust"},
           "n_dates": 20, "ic": {"x": 1}, "quantile": {}}


def test_run_graph_ml_chain_hpmap_and_terminal(monkeypatch):
    seen = {}

    def fake_train(body, kind):
        seen["kind"] = kind
        seen["params"] = dict(body.params)
        seen["features"] = list(body.features or [])
        seen["universe"] = body.universe
        seen["oos_frac"] = body.oos_frac
        return JSONResponse(_REPORT)

    monkeypatch.setattr(ex, "_call_train", fake_train)
    out = ex.run_graph(_G_ML, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is True and out["has_ml"] is True
    assert seen["kind"] == "xgboost"
    assert seen["params"] == {"n_estimators": 120, "max_depth": 3, "learning_rate": 0.08}  # hpMap
    assert seen["features"] == ["rank(-delta(close,5))"]
    assert seen["universe"] == "csi_fast" and seen["oos_frac"] == 0.3   # overrides 权威
    assert out["terminal"]["kind"] == "analysis"          # mf 模型报告→analysis 透传为终端
    assert out["metrics"]["rank_ic"] == 0.04 and out["metrics"]["sharpe"] == 1.2
    assert out["exprs"] == ["rank(-delta(close,5))"]


def test_run_graph_node_fail_continues_and_honest(monkeypatch):
    monkeypatch.setattr(ex, "_call_train",
                        lambda body, kind: JSONResponse({"ok": False, "reason": "xgboost 未装"}))
    out = ex.run_graph(_G_ML, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is False                              # 无主终端 → 整图诚实失败
    assert any(e["nid"] == "m" and "未装" in e["error"] for e in out["node_errors"])
    assert any(e["nid"] == "an" for e in out["node_errors"])   # 下游因缺输入失败,同样显形


def test_run_graph_terminal_priority_backtest_over_analysis(monkeypatch):
    g = {"nodes": [
        {"id": "f", "type": "formula", "x": 0, "y": 0, "params": {"expr": "rank(close)"}},
        {"id": "an", "type": "analysis", "x": 1, "y": 0, "params": {}},
        {"id": "bt", "type": "backtest", "x": 1, "y": 1, "params": {"topn": 20}},
    ], "edges": [{"from": ["f", "out"], "to": ["an", "factor"]},
                 {"from": ["f", "out"], "to": ["bt", "factor"]}]}
    rep_an = dict(_REPORT, headline_ic={"rank_ic": 0.01})
    rep_bt = dict(_REPORT, headline_ic={"rank_ic": 0.09})
    monkeypatch.setattr(ex, "_call_report2", lambda body: JSONResponse(rep_an))
    seen = {}

    def fake_bt(body):
        seen["topn"] = body.topn
        seen["rebalance"] = body.rebalance
        return JSONResponse(rep_bt)

    monkeypatch.setattr(ex, "_call_backtest", fake_bt)
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["terminal"]["kind"] == "backtest" and out["metrics"]["rank_ic"] == 0.09
    assert seen["topn"] == 20 and seen["rebalance"] == "month"   # 参数真生效(旧盲区消失)


def test_run_graph_compose_route_weights(monkeypatch):
    g = {"nodes": [
        {"id": "f1", "type": "formula", "x": 0, "y": 0, "params": {"expr": "a"}},
        {"id": "f2", "type": "formula", "x": 0, "y": 1, "params": {"expr": "b"}},
        {"id": "fe", "type": "feature", "x": 1, "y": 0, "params": {}},
        {"id": "mf", "type": "mf", "x": 2, "y": 0, "params": {"combine": "icir"}},
        {"id": "an", "type": "analysis", "x": 3, "y": 0, "params": {}},
    ], "edges": [{"from": ["f1", "out"], "to": ["fe", "feat"]},
                 {"from": ["fe", "fe"], "to": ["mf", "f1"]},
                 {"from": ["mf", "factor"], "to": ["an", "factor"]}]}
    comp = {"ok": True, "members": ["a", "b"],
            "weights": [{"name": "a", "weight": 0.63}, {"name": "b", "weight": 0.37}],
            "composite": dict(_REPORT, _compose=True, members=["a", "b"],
                              weights=[{"name": "a", "weight": 0.63},
                                       {"name": "b", "weight": 0.37}])}

    def fake_compose(body):
        assert body.members == ["a", "b"] and body.method == "icir"
        return JSONResponse(comp)

    monkeypatch.setattr(ex, "_call_compose", fake_compose)
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is True
    assert out["terminal"]["payload"]["weights"][0]["weight"] == 0.63   # 权重透出(产物物化用)


def test_run_graph_diag_terminal_no_gate_metrics(monkeypatch):
    g = {"nodes": [
        {"id": "f", "type": "formula", "x": 0, "y": 0, "params": {"expr": "rank(close)"}},
        {"id": "t", "type": "tsic", "x": 1, "y": 0, "params": {"fwd_days": 20}},
    ], "edges": [{"from": ["f", "out"], "to": ["t", "factor"]}]}
    monkeypatch.setattr(ex, "_call_tsic", lambda body: JSONResponse({"ok": True, "status": "ok"}))
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is False and out["metrics"] is None   # 只有诊断终端 → 无过门指标,诚实
    assert out["node_results"]["t"]["ok"] is True          # 但诊断照跑存档


def test_run_graph_unsupported_node_type_honest():
    g = {"nodes": [{"id": "v", "type": "validate", "x": 0, "y": 0, "params": {}}], "edges": []}
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is False
    assert any("不支持的节点类型" in e["error"] for e in out["node_errors"])
```

- [ ] **Step 2:** Run → FAIL(run_graph 未定义)
- [ ] **Step 3: 实现(追加到 executor.py)** — 直调目标经**模块级薄桥**(`_call_*`)隔离,便于 monkeypatch(仓例 loop.py):

```python
# ── 直调薄桥(延迟 import;独立小函数便于 monkeypatch)────────────────────
def _call_train(body, kind):
    from guanlan_v2.workflow.api import _train_eval
    return _train_eval(body, kind)


def _call_lstm(body):
    from guanlan_v2.workflow.api import _lstm_eval
    return _lstm_eval(body)


def _call_pca(body):
    from guanlan_v2.workflow.api import _pca_factor
    return _pca_factor(body)


def _call_spearman(body):
    from guanlan_v2.workflow.api import _spearman_factor
    return _spearman_factor(body)


def _call_compose(body):
    from guanlan_v2.workflow.api import _factor_compose
    return _factor_compose(body)


def _call_report2(body):
    from guanlan_v2.workflow.api import _factor_report2
    return _factor_report2(body)


def _call_backtest(body):
    from guanlan_v2.workflow.api import _backtest_vector
    return _backtest_vector(body)


def _call_portfolio(body):
    from guanlan_v2.workflow.api import _portfolio_build
    return _portfolio_build(body)


def _call_tsic(body):
    from guanlan_v2.workflow.api import _factor_tsic
    return _factor_tsic(body)


def _call_event(body):
    from guanlan_v2.workflow.api import _factor_event
    return _factor_event(body)


def _call_relstat(body):
    from guanlan_v2.workflow.api import _factor_relstat
    return _factor_relstat(body)


def _call_risk(body):
    from guanlan_v2.workflow.api import _factor_risk
    return _factor_risk(body)


def _call_garch(body):
    from guanlan_v2.workflow.api import _garch
    return _garch(body)


def _call_attrib(body):
    from guanlan_v2.workflow.api import _attrib
    return _attrib(body)


def _call_tvbeta(body):
    from guanlan_v2.workflow.api import _tvbeta
    return _tvbeta(body)


class NodeError(Exception):
    """节点级诚实失败(记 node_errors 继续跑)。"""


_ML_KINDS = {"xgb": "xgboost", "lgbm": "lightgbm", "svm": "svm", "rf": "rf",
             "nn": "mlp", "lstm": "lstm"}
# 后端 _build_model 取值键 → 画布超参字段(逐字段镜像 workflow.jsx _trainModel:392-404)
_HPMAP = {
    "xgb": {"n_estimators": "trees", "max_depth": "depth", "learning_rate": "lr", "subsample": "sub"},
    "lgbm": {"num_leaves": "leaves", "learning_rate": "lr"},
    "svm": {"C": "c"},
    "rf": {"n_estimators": "trees"},
    "nn": {"hidden": "hidden", "layers": "layers", "lr": "lr", "epochs": "epochs", "alpha": "alpha"},
    "lstm": {"seq_len": "seq_len", "hidden": "hidden", "layers": "layers", "lr": "lr", "epochs": "epochs"},
}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _hp(node_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """画布超参 → 后端 params(数值化;空串/None 跳过;镜像 _trainModel:278;
    int 型超参保持 int——float 无小数则转 int,防 sklearn 拒 n_estimators=120.0)。"""
    out: Dict[str, Any] = {}
    for bk, ck in _HPMAP.get(node_type, {}).items():
        v = (params or {}).get(ck)
        if v is None or v == "":
            continue
        n = _num(v)
        if n is None:
            out[bk] = v
        else:
            out[bk] = int(n) if float(n).is_integer() else n
    return out


def _expr_of(payload: Optional[dict]) -> str:
    return str((payload or {}).get("expr") or (payload or {}).get("_factorName")
               or (payload or {}).get("_label") or "").strip()


def _check(resp_dict: Dict[str, Any], what: str) -> Dict[str, Any]:
    if resp_dict.get("ok") is False or resp_dict.get("status") not in (None, "ok"):
        raise NodeError(f"{what}: {resp_dict.get('reason') or resp_dict.get('status') or '失败'}")
    return resp_dict


def _features_of(payload: dict) -> List[str]:
    """复合因子载荷 → 可复算 features(镜像前端 backtest 统一走 features 的口径)。"""
    fe = payload.get("fe") if isinstance(payload.get("fe"), dict) else {}
    if isinstance(fe.get("features"), list) and fe["features"]:
        return [str(x) for x in fe["features"]]
    if isinstance(payload.get("members"), list) and payload["members"]:
        return [str(x) for x in payload["members"]]
    e = _expr_of(payload)
    if e:
        return [e]
    raise NodeError("上游因子无可复算表达式")


# ── 节点求值器(inputs, params, ctx) -> {port: payload} ─────────────────
def _mk_body(cls, ctx, **kw):
    """构直调入参:ctx 的 universe/oos_frac/start/end/benchmark/leader/codes 统一注入。"""
    base = dict(universe=ctx["universe"], oos_frac=ctx["oos_frac"])
    if ctx.get("start"):
        base["start"] = ctx["start"]
    if ctx.get("end"):
        base["end"] = ctx["end"]
    if ctx.get("codes"):
        base["codes"] = ctx["codes"]
    if ctx.get("benchmark"):
        base["benchmark"] = ctx["benchmark"]
    if ctx.get("leader"):
        base["leader"] = ctx["leader"]
    base.update(kw)
    return cls(**base)


def _exec_source(inputs, params, ctx):
    return {"data": {"universe": _universe_of(params)}}


def _exec_formula(inputs, params, ctx):
    e = str((params or {}).get("expr") or "").strip()
    if not e:
        raise NodeError("公式输入: expr 为空")
    return {"out": {"expr": e}}


def _exec_factorlib(inputs, params, ctx):
    picked = str((params or {}).get("expr") or "").strip()
    name = str((params or {}).get("name") or "").strip()
    if picked:
        return {"out": {"expr": picked, "_factorName": name, "_label": name or "因子"}}
    if not name:
        raise NodeError("因子库: 未选因子(params.expr/name 均空)")
    from guanlan_v2.factorlib.store import LibraryFactorStore
    allf = LibraryFactorStore().list_factors(validate=False)
    low = name.lower()
    hit = (next((f for f in allf if str(f.get("name", "")).lower() == low), None)
           or next((f for f in allf if low in str(f.get("name", "")).lower()), None))
    if not hit:
        raise NodeError(f"因子库: 查无因子 '{name}'")
    return {"out": {"expr": hit.get("expr") or hit["name"], "_factorName": hit["name"],
                    "_label": hit["name"]}}


def _exec_feature(inputs, params, ctx):
    feat = _expr_of(inputs.get("feat"))
    if not feat:
        raise NodeError("特征工程: 上游未提供特征表达式")
    label = _expr_of(inputs.get("label"))
    if not label:
        tag = str((params or {}).get("tag") or "").strip()
        label = "" if tag.lower() in ("", "ic", "fwd_ret") else tag
    return {"fe": {"features": [feat], "label": (label or None)}}


def _exec_ml(node_type):
    def _run(inputs, params, ctx):
        fe = inputs.get("fe")
        if not isinstance(fe, dict) or not fe.get("features"):
            raise NodeError(f"{node_type}: 上游缺特征工程(需 feature 直连 fe 口)")
        from guanlan_v2.workflow.api import ModelTrainIn
        kind = _ML_KINDS[node_type]
        body = _mk_body(ModelTrainIn, ctx, kind=kind, features=list(fe["features"]),
                        label=fe.get("label"), params=_hp(node_type, params))
        resp = _check(_resp_json(_call_lstm(body) if node_type == "lstm"
                                 else _call_train(body, kind)), node_type)
        resp["_kind"] = kind
        return {"model": resp}
    return _run


def _exec_pca(inputs, params, ctx):
    fe = inputs.get("fe")
    if not isinstance(fe, dict) or not fe.get("features"):
        raise NodeError("PCA: 上游缺特征工程")
    from guanlan_v2.workflow.api import PCAFactorIn
    kw = dict(features=list(fe["features"]), label=fe.get("label"))
    k = _num((params or {}).get("k"))
    if k:
        kw["k"] = int(k)
    resp = _check(_resp_json(_call_pca(_mk_body(PCAFactorIn, ctx, **kw))), "PCA")
    resp.setdefault("composite", True)
    return {"factor": resp}


def _exec_spearman(inputs, params, ctx):
    fe = inputs.get("fe")
    if not isinstance(fe, dict) or not fe.get("features"):
        raise NodeError("Spearman: 上游缺特征工程")
    from guanlan_v2.workflow.api import SpearmanFactorIn
    resp = _check(_resp_json(_call_spearman(
        _mk_body(SpearmanFactorIn, ctx, features=list(fe["features"]), label=fe.get("label")))),
        "Spearman")
    resp.setdefault("composite", True)
    return {"factor": resp}


def _model_report(m):
    """镜像前端 _modelReport:297:顶层/嵌套 report/composite 三形态取 OOS 报告。"""
    if not isinstance(m, dict):
        return None
    if m.get("ic") or m.get("portfolio") or m.get("quantile"):
        return m
    r = m.get("report")
    if isinstance(r, dict) and (r.get("ic") or r.get("portfolio") or r.get("quantile")):
        return r
    c = m.get("composite")
    if isinstance(c, dict) and (c.get("ic") or c.get("portfolio") or c.get("quantile")):
        return c
    return None


def _exec_mf(inputs, params, ctx):
    for port in ("m1", "m2"):                       # ①模型报告透传(model→factor 唯一桥)
        rep = _model_report(inputs.get(port))
        if rep:
            out = dict(rep)
            out["composite"] = True
            out.setdefault("_kind", (inputs.get(port) or {}).get("_kind"))
            return {"factor": out}
    exprs = [e for e in (_expr_of(inputs.get(p)) for p in ("f1", "f2", "m1", "m2")) if e]
    # fe 载荷经 f1/f2 连入时无 expr → 回退全图表达式集合(镜像前端 allExprs 兜底)
    if len(exprs) < 2:
        exprs = list(ctx.get("all_exprs") or [])
    if len(exprs) < 2:
        raise NodeError("多因子构建: 需要至少 2 个因子表达式")
    from guanlan_v2.workflow.api import FactorComposeIn
    body = _mk_body(FactorComposeIn, ctx, members=exprs,
                    method=str((params or {}).get("combine") or "equal"), freq=ctx["freq"])
    resp = _check(_resp_json(_call_compose(body)), "多因子构建")
    comp = resp.get("composite") if isinstance(resp.get("composite"), dict) else {}
    out = dict(comp)
    out["composite"] = True
    out.setdefault("members", resp.get("members"))
    out.setdefault("weights", resp.get("weights"))
    return {"factor": out}


def _exec_analysis(inputs, params, ctx):
    f = inputs.get("factor")
    if isinstance(f, dict) and f.get("composite"):
        return {"report": f}                        # 复合因子报告透传为终端(ML 图到终端的通路)
    e = _expr_of(f)
    if not e:
        raise NodeError("因子分析: 上游未提供因子表达式")
    from guanlan_v2.workflow.api import FactorReport2In
    p = params or {}
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e,
                    freq=str(p.get("rebal") or ctx["freq"]),
                    n_groups=int(_num(p.get("groups")) or 10),
                    direction=int(_num(p.get("dir")) or 0),
                    neutralize=(p.get("neutral") == "是"))
    return {"report": _check(_resp_json(_call_report2(body)), "因子分析")}


def _exec_iccalc(inputs, params, ctx):
    f = inputs.get("factor")
    if isinstance(f, dict) and f.get("composite"):
        return {"ic": f}
    e = _expr_of(f)
    if not e:
        raise NodeError("IC 计算: 上游缺因子")
    period = int(_num((params or {}).get("period")) or 5)
    freq = "month" if period >= 20 else ("week" if period >= 5 else "day")
    from guanlan_v2.workflow.api import FactorReport2In
    body = _mk_body(FactorReport2In, ctx, expr_or_name=e, freq=freq, fwd_days=period)
    return {"ic": _check(_resp_json(_call_report2(body)), "IC 计算")}


def _exec_backtest(inputs, params, ctx):
    f = inputs.get("pf") or inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("向量化回测: 上游缺因子/组合")
    from guanlan_v2.workflow.api import BacktestVectorIn
    p = params or {}
    kw = dict(features=_features_of(f), rebalance=ctx["freq"],
              topn=int(_num(p.get("topn")) or 30))
    if _num(p.get("cash")):
        kw["cash"] = float(_num(p.get("cash")))
    if p.get("weighting"):
        kw["weighting"] = str(p["weighting"])
    if p.get("vol_forecast"):
        kw["vol_forecast"] = str(p["vol_forecast"])
    return {"result": _check(_resp_json(_call_backtest(_mk_body(BacktestVectorIn, ctx, **kw))),
                             "向量化回测")}


def _exec_portfolio(inputs, params, ctx):
    f = inputs.get("factor")
    if not isinstance(f, dict):
        raise NodeError("组合构建: 上游缺因子")
    from guanlan_v2.workflow.api import PortfolioBuildIn
    p = params or {}
    kw = dict(features=_features_of(f), topn=int(_num(p.get("topn")) or 30))
    for key in ("weighting", "vol_forecast"):
        if p.get(key):
            kw[key] = str(p[key])
    if p.get("max_weight") not in (None, "") and _num(p.get("max_weight")) is not None:
        kw["max_weight"] = float(_num(p.get("max_weight")))
    if p.get("industry_neutral") in (True, "是"):
        kw["industry_neutral"] = True
    return {"pf": _check(_resp_json(_call_portfolio(_mk_body(PortfolioBuildIn, ctx, **kw))),
                         "组合构建")}


def _exec_diag(node_type):
    """七个诊断终端:表达式 + 各自 params → 对应直调;载荷原样出端口(照跑存档)。"""
    def _run(inputs, params, ctx):
        src = inputs.get("factor") or inputs.get("trigger")
        e = _expr_of(src)
        if not e:
            raise NodeError(f"{node_type}: 上游缺表达式")
        p = params or {}
        from guanlan_v2.workflow import api as wapi
        if node_type == "tsic":
            body = _mk_body(wapi.FactorTsicIn, ctx, expr_or_name=e,
                            fwd_days=int(_num(p.get("fwd_days")) or 20))
            resp = _call_tsic(body)
        elif node_type == "event":
            hz = [int(s) for s in str(p.get("horizons") or "1,5,10,20").split(",")
                  if s.strip().isdigit() and int(s) >= 1] or [1, 5, 10, 20]
            body = _mk_body(wapi.FactorEventIn, ctx, trigger=e, horizons=hz,
                            direction=int(_num(p.get("direction")) or 0))
            resp = _call_event(body)
        elif node_type == "relstat":
            body = _mk_body(wapi.FactorRelstatIn, ctx, expr_or_name=e)
            resp = _call_relstat(body)
        else:                                        # risk/garch/attrib/tvbeta 继承回测,因子走 features
            cls = {"risk": wapi.FactorRiskIn, "garch": wapi.FactorGarchIn,
                   "attrib": wapi.FactorAttribIn, "tvbeta": wapi.FactorTVBetaIn}[node_type]
            kw = dict(features=[e])
            if _num(p.get("topn")):
                kw["topn"] = int(_num(p.get("topn")))
            if node_type == "garch" and _num(p.get("horizon")):
                kw["horizon"] = int(_num(p.get("horizon")))
            body = _mk_body(cls, ctx, **kw)
            resp = {"risk": _call_risk, "garch": _call_garch,
                    "attrib": _call_attrib, "tvbeta": _call_tvbeta}[node_type](body)
        return {node_type: _check(_resp_json(resp), node_type)}
    return _run


_DISPATCH: Dict[str, Callable] = {
    "source": _exec_source, "formula": _exec_formula, "factorlib": _exec_factorlib,
    "feature": _exec_feature, "pca": _exec_pca, "spearman": _exec_spearman,
    "mf": _exec_mf, "analysis": _exec_analysis, "iccalc": _exec_iccalc,
    "backtest": _exec_backtest, "portfolio": _exec_portfolio,
}
for _t in _ML_KINDS:
    _DISPATCH[_t] = _exec_ml(_t)
for _t in _DIAG_TYPES:
    _DISPATCH[_t] = _exec_diag(_t)

# 各节点类型的出端口名(端口对端口传递用;与前端 SPECS 一致)
_OUT_PORT = {"source": "data", "formula": "out", "factorlib": "out", "feature": "fe",
             "xgb": "model", "lgbm": "model", "svm": "model", "rf": "model", "nn": "model",
             "lstm": "model", "pca": "factor", "spearman": "factor", "mf": "factor",
             "analysis": "report", "iccalc": "ic", "backtest": "result", "portfolio": "pf",
             "tsic": "tsic", "event": "event", "relstat": "relstat", "risk": "risk",
             "garch": "garch", "attrib": "attrib", "tvbeta": "tvbeta"}


def graph_exprs(graph: Dict[str, Any]) -> List[str]:
    """图内 formula/factorlib 表达式集合(保序;供档案 exprs 显示与 mf 兜底)。"""
    out: List[str] = []
    for n in (graph.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        if n.get("type") == "formula":
            e = str(p.get("expr") or "").strip()
        elif n.get("type") == "factorlib":
            e = str(p.get("expr") or p.get("name") or "").strip()
        else:
            continue
        if e and e not in out:
            out.append(e)
    return out


def run_graph(graph: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None,
              on_node: Optional[Callable[[str, str, str], None]] = None) -> Dict[str, Any]:
    """整图执行(spec §1.1)。on_node(nid, type, state) state∈running|done|error,供回路进度显形。"""
    t0 = time.time()
    ov = overrides or {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id")]
    edges = [e for e in edges if isinstance(e, dict)
             and isinstance(e.get("from"), list) and isinstance(e.get("to"), list)]
    by_id = {n["id"]: n for n in nodes}
    order = topo_order(nodes, edges)
    all_exprs = graph_exprs(graph)
    freq = str(ov.get("freq") or "month")
    outputs: Dict[str, Dict[str, Any]] = {}
    node_results: Dict[str, Dict[str, Any]] = {}
    node_errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    terminals: List[Dict[str, Any]] = []
    has_ml = any(n.get("type") in _ML_KINDS for n in nodes)

    for nid in order:
        node = by_id[nid]
        typ = str(node.get("type") or "")
        fn = _DISPATCH.get(typ)
        inputs: Dict[str, Any] = {}
        for e in edges:
            if e["to"][0] == nid and e["from"][0] in outputs:
                inputs[e["to"][1]] = outputs[e["from"][0]].get(e["from"][1])
        u = universe_for_node(nid, nodes, edges)
        ctx = {
            "universe": ov.get("universe") or (u["universe"] if u["wired"] else None) or "csi_fast",
            "freq": freq,
            "oos_frac": (ov["oos_frac"] if ov.get("oos_frac") is not None
                         else (u["oos_frac"] if u["wired"] else 0.0)),
            "start": ov.get("start") or (u["start"] if u["wired"] else ""),
            "end": ov.get("end") or (u["end"] if u["wired"] else ""),
            "codes": (u["codes"] if u["wired"] else []),
            "benchmark": (u["benchmark"] if u["wired"] else ""),
            "leader": (u["leader"] if u["wired"] else ""),
            "all_exprs": all_exprs,
        }
        if on_node:
            on_node(nid, typ, "running")
        try:
            if fn is None:
                raise NodeError(f"不支持的节点类型: {typ}")
            out = fn(inputs, node.get("params") or {}, ctx) or {}
            outputs[nid] = out
            node_results[nid] = {"ok": True, "type": typ}
            if typ in _TERMINAL_PRIORITY:
                payload = out.get(_OUT_PORT[typ])
                if isinstance(payload, dict):
                    terminals.append({"kind": typ, "node_id": nid, "payload": payload})
            if on_node:
                on_node(nid, typ, "done")
        except Exception as exc:  # noqa: BLE001 — 节点失败不中断(镜像前端)
            node_results[nid] = {"ok": False, "type": typ}
            node_errors.append({"nid": nid, "type": typ, "error": f"{exc}"})
            if on_node:
                on_node(nid, typ, "error")

    main = None
    for kind in _TERMINAL_PRIORITY:                 # 优先级取主终端;同类取拓扑序最后
        cand = [t for t in terminals if t["kind"] == kind]
        if cand:
            main = cand[-1]
            break
    metrics = metrics_of_terminal(main["payload"]) if main else None
    if main is None:
        warnings.append("无主终端产出(回测/分析/IC)——过门指标缺失")
    ok = main is not None
    reason = None if ok else (node_errors[0]["error"] if node_errors else "图无主终端节点")
    return {"ok": ok, "reason": reason, "terminal": main, "metrics": metrics,
            "exprs": all_exprs, "has_ml": has_ml, "node_results": node_results,
            "node_errors": node_errors, "warnings": warnings,
            "elapsed_sec": round(time.time() - t0, 1)}
```

- [ ] **Step 4:** Run `pytest tests/test_workflow_executor.py -q` → 全 PASS
- [ ] **Step 5:** `git commit -m "feat(executor): 24类节点分发表+run_graph(直调模块函数/失败不中断/主终端优先级)"`

### Task 3: POST /workflow/run 端点

**Files:** Modify `guanlan_v2/workflow/api.py`(WorkflowSaveIn 类旁加 In 模型;router 内 /workflow/save 之前加端点);Test `tests/test_workflow_run_endpoint.py`

**Interfaces (Consumes):** `executor.run_graph`。**Produces:** `POST /workflow/run` `{graph, universe?, freq?, start?, end?, oos_frac?}` → run_graph 结果(诚实失败 HTTP 200)。

- [ ] **Step 1: 失败测试**

```python
# -*- coding: utf-8 -*-
"""POST /workflow/run 端点三态(executor 打桩,零引擎)。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.workflow.api as wapi
import guanlan_v2.workflow.executor as wex


def _client():
    app = FastAPI()
    app.include_router(wapi.build_workflow_router())
    return TestClient(app)


def test_run_empty_graph_honest():
    j = _client().post("/workflow/run", json={"graph": {}}).json()
    assert j["ok"] is False and j["reason"]


def test_run_ok_passes_overrides(monkeypatch):
    seen = {}

    def fake(graph, overrides=None, on_node=None):
        seen.update(overrides or {})
        return {"ok": True, "metrics": {"rank_ic": 0.02}, "terminal": {"kind": "analysis"}}

    monkeypatch.setattr(wex, "run_graph", fake)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}}],
                  "edges": []},
        "universe": "csi300_active", "freq": "month", "oos_frac": 0.3}).json()
    assert j["ok"] is True and seen["universe"] == "csi300_active" and seen["oos_frac"] == 0.3


def test_run_executor_exception_wrapped(monkeypatch):
    def boom(graph, overrides=None, on_node=None):
        raise RuntimeError("x")

    monkeypatch.setattr(wex, "run_graph", boom)
    j = _client().post("/workflow/run", json={
        "graph": {"nodes": [{"id": "f", "type": "formula", "params": {}}], "edges": []}}).json()
    assert j["ok"] is False and "RuntimeError" in j["reason"]
```

- [ ] **Step 2:** Run → FAIL(404)
- [ ] **Step 3: 实现** — api.py 模块级加 `WorkflowRunIn`,router 内加端点(**def 非 async**,FastAPI 自动进线程池,不堵事件循环):

```python
class WorkflowRunIn(BaseModel):
    """``POST /workflow/run`` 入参:服务端整图执行(P4 执行器门面;供 e2e 与 P5 复用)。"""

    graph: Dict[str, Any] = Field(default_factory=dict)
    universe: Optional[str] = None
    freq: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    oos_frac: Optional[float] = None
```

```python
    @router.post("/workflow/run")
    def workflow_run(body: WorkflowRunIn):
        """服务端整图执行(同步 def → FastAPI 线程池;绝不在事件循环里跑重活)。
        诚实失败 {ok:false,reason} HTTP 200;overrides 仅透传显式给出的键。"""
        from guanlan_v2.workflow import executor as _wex
        g = body.graph if isinstance(body.graph, dict) else {}
        if not (isinstance(g.get("nodes"), list) and g.get("nodes")):
            return JSONResponse({"ok": False, "reason": "graph.nodes 缺失或为空"})
        ov = {k: v for k, v in (("universe", body.universe), ("freq", body.freq),
                                ("start", body.start), ("end", body.end),
                                ("oos_frac", body.oos_frac)) if v is not None}
        try:
            return JSONResponse(_wex.run_graph(g, overrides=(ov or None)))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
```

- [ ] **Step 4:** Run 新测试文件 → PASS;顺跑 `pytest tests/test_workflow_critique.py tests/test_guanlan_mcp.py tests/test_console_tools.py -q` 守护无回归(新端点非 ww 工具,计数不动)
- [ ] **Step 5:** `git commit -m "feat(workflow): POST /workflow/run 执行器HTTP门面(线程池/诚实失败)"`

### Task 4: 回路升级(执行器求值+Sharpe门+图签名停滞)

**Files:** Modify `guanlan_v2/research/loop.py`;Test `tests/test_research_loop.py`

**Interfaces (Consumes):** `executor.run_graph/graph_signature`。**Produces:** 轮次行新键 `terminal_kind/node_errors`;`_gate` 增 sharpe 判 + `sharpe_required:true`;薄桥 `_run_graph_eval(graph, p, progress, k, max_rounds) -> dict`(T5/测试打桩点)。

- [ ] **Step 1: 写失败测试 + 改既有假桥** — `_wire` 中删 `_eval_report2` 打桩,改为 `monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: q.pop(0))`;删 `_PASS/_WEAK` 换 `_ex_ok`;`test_metrics_of_report2_and_compose` 删除(executor 测试已覆盖);`test_loop_multi_expr_pass_skips_autosave` 本任务先改 evals 形式保绿(T5 再替换语义);新增:

```python
def _ex_ok(rank_ic=0.05, sharpe=1.0, oos="robust", exprs=("rank(-delta(close,5))",), has_ml=False):
    return {"ok": True, "reason": None,
            "metrics": {"rank_ic": rank_ic, "sharpe": sharpe, "ann_return": 0.1,
                        "oos_verdict": oos, "n_dates": 20, "factor": " + ".join(exprs)},
            "terminal": {"kind": "analysis", "node_id": "an", "payload": {}},
            "exprs": list(exprs), "has_ml": has_ml, "node_errors": [], "warnings": []}


_EX_WEAK = dict  # 语义帮助:_ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")


def test_gate_requires_positive_sharpe():
    assert rl._gate({"rank_ic": 0.05, "oos_verdict": "robust", "sharpe": 1.0}, 0.02)["passed"] is True
    g = rl._gate({"rank_ic": 0.05, "oos_verdict": "robust", "sharpe": -0.9}, 0.02)
    assert g["passed"] is False and g["sharpe_required"] is True     # Sharpe 负 → 拦(今日教训)
    assert rl._gate({"rank_ic": 0.05, "oos_verdict": "robust"}, 0.02)["passed"] is False  # 缺 sharpe 拦


def test_loop_uses_executor_and_records_terminal(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, evals=[_ex_ok()])
    end = rl.run_research_loop("rr_t10", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["promoted"]["status"] == "draft"
    import guanlan_v2.research.store as rs
    row = rs.read_rounds(run_id="rr_t10")[0]
    assert row["terminal_kind"] == "analysis" and row["node_errors"] == []


def test_stagnation_by_graph_signature_param_change_not_stagnant(monkeypatch, tmp_path):
    """批判只改 ML 超参(表达式没变)→ 新语义下不算停滞(全图执行参数真生效)。"""
    g2 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,5))"}},
                    {"id": "m", "type": "xgb", "params": {"trees": 300}}], "edges": []}
    calls = []

    def crit(goal, metrics, graph, constraints=""):
        calls.append(constraints)
        return {"ok": True, "diagnosis": "加树", "graph": g2, "source": "llm"}

    weak = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")
    _wire(monkeypatch, tmp_path, evals=[dict(weak), dict(weak)], critique=crit)
    end = rl.run_research_loop("rr_t11", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["n_rounds"] == 2 and len(calls) == 1        # 没触发停滞重批
    assert "参数均真实生效" in calls[0]                     # constraints 新文案


def test_stagnation_identical_graph_still_caught(monkeypatch, tmp_path):
    calls = []

    def crit(goal, metrics, graph, constraints=""):
        calls.append(constraints)
        return {"ok": True, "diagnosis": "原样", "graph": _G0, "source": "llm"}

    lessons, _, _ = _wire(monkeypatch, tmp_path,
                          evals=[_ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")], critique=crit)
    end = rl.run_research_loop("rr_t12", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is False and "停滞" in end["error"] and len(calls) == 2
```

既有测试适配:`test_loop_pass_first_round_early_stop` evals=[_ex_ok()];`test_loop_exhausts_rounds_no_pass` evals=两个 weak(断言 rows[0]["diag"]/critique_source 保留);`test_loop_eval_fail_round_continues` 的 bad 换 `{"ok": False, "reason": "缺少数据", "metrics": None, "exprs": [], "has_ml": False, "node_errors": [], "terminal": None, "warnings": []}`,断言 `"缺少数据" in rows[1]["error"]` 不变;`test_loop_generate_fail_honest_stop`/`test_loop_rule_critique_prefix`/停滞两测(P4 语义:`_G0→_G1` 图签名不同=非停滞,天然兼容)按 `_ex_ok` 形喂;`test_loop_critique_stagnant_retry_then_progress` 中 calls[0] 断言 `"formula" in calls[0]` 改 `"参数均真实生效" in calls[0]`,rows[0]["exprs"] 断言不变(_G1 表达式)。

- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3: 实现** — loop.py:
  - 顶部 `from guanlan_v2.workflow import executor as wex`;删 `_eval_report2/_eval_compose/_eval_backtest` 与 `_metrics_of`(_resp_json 保留给 factorlib 桥用则留,若无引用一并删);加:

```python
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
```

  - 求值段整块替换(原 `dish, exprs = _pick_dish(graph)` 至 metrics 计算):

```python
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
```

  - row 增两键:`"terminal_kind": ((ex.get("terminal") or {}) or {}).get("kind"), "node_errors": (ex.get("node_errors") or [])[:5]`。
  - `_gate` 整体替换:

```python
def _gate(metrics: Dict[str, Any], min_rank_ic: float) -> Dict[str, Any]:
    """过门(P4 联合门):rank_ic ≥ 门槛 且 样本外 robust 且 组合 Sharpe > 0。"""
    ric, shp = metrics.get("rank_ic"), metrics.get("sharpe")
    ok_ric = isinstance(ric, (int, float)) and ric == ric
    ok_shp = isinstance(shp, (int, float)) and shp == shp and shp > 0
    passed = bool(ok_ric and float(ric) >= float(min_rank_ic)
                  and metrics.get("oos_verdict") == GATE_OOS_OK and ok_shp)
    return {"passed": passed, "min_rank_ic": float(min_rank_ic),
            "oos_required": GATE_OOS_OK, "sharpe_required": True}
```

  - 停滞守卫两处比较 `_pick_dish(cr["graph"]) == (dish, exprs)` / `_pick_dish(cr2["graph"]) != (dish, exprs)` 改 `wex.graph_signature(cr["graph"]) == wex.graph_signature(graph)` / `wex.graph_signature(cr2["graph"]) != wex.graph_signature(graph)`。
  - `_CRITIQUE_CONSTRAINTS` 整体替换:

```python
_CRITIQUE_CONSTRAINTS = (
    "本图由研究回路后端全图执行:所有节点参数均真实生效(ML 超参、回测 topn/weighting、"
    "分析 rebal/groups/dir、组合 combine 等都会改变指标);股票池与调仓频率由回路参数固定,"
    "图内数据源节点的 universe 不生效。改进可落在:因子表达式、特征组合、模型类型与超参、"
    "多因子合成方式、回测/分析参数。与上一轮完全相同的图不会产生不同指标。")
```

  - 停滞重批 fb 内「这次必须给出不同的 formula 表达式(换因子逻辑/窗口/组合皆可,方向反转直接对表达式取负)。」改「这次必须对图做出实质修改(换表达式/换模型类型或超参/改合成方式或回测参数皆可)。」;`【停滞警告】…没有改变任何会被求值的因子表达式` 改 `…图与上一轮完全相同(签名一致)`。
  - 最佳轮选取/教训段不动(T5 改产物)。docstring 首段"三道菜"表述更新为"整图执行"。
- [ ] **Step 4:** Run `pytest tests/test_research_loop.py tests/test_research_api.py tests/test_workflow_executor.py -q` → 全 PASS
- [ ] **Step 5:** `git commit -m "feat(research): 回路求值换整图执行器+Sharpe>0联合门+图签名停滞守卫"`

### Task 5: 产物三通道

**Files:** Modify `guanlan_v2/research/loop.py`、`guanlan_v2/strategy/compute/model_workflow.py`;Test `tests/test_research_loop.py`、`tests/test_model_workflow_promote.py`

**Interfaces (Produces):** `_save_compose_expr(name,expr,goal,diag,meta)->dict`、`_call_train_promote(spec)->dict`、`_derive_ml_recipe(graph,universe)->dict|None`、`_route_product(run_id,k,graph,goal,diag,metrics,terminal,universe)->dict`;model_workflow `spec["status"]` 钩子。

- [ ] **Step 1: 失败测试**

`tests/test_model_workflow_promote.py` 追加:

```python
def test_spec_status_forces_draft(monkeypatch):
    """P4:spec.status='draft' → meta.status 恒 draft(研究回路产物绝不自动上正式货架);
    门(GUANLAN_PROMOTE_MIN_OOS_IC)只降不升,与强制 draft 叠加仍是 draft。"""
    import pandas as pd
    import guanlan_v2.strategy.compute.model_workflow as mw
    saved = {}

    def fake_mat(body, universe, feats, start, end):
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2026-01-01", periods=30), [f"SH60000{i}" for i in range(5)]],
            names=["datetime", "code"])
        fe = pd.DataFrame({"f1": range(len(idx))}, index=idx, dtype="float64")
        lab = pd.Series(range(len(idx)), index=idx, dtype="float64")
        return (None, fe, lab, ["f1"])

    class _M:
        def fit(self, X, y):
            return self

        def predict(self, X):
            import numpy as np
            return np.arange(len(X), dtype="float64")

    monkeypatch.setattr("guanlan_v2.workflow.api._materialize_xy", fake_mat)
    monkeypatch.setattr("guanlan_v2.workflow.api._build_model", lambda k, p: (_M(), {}))
    monkeypatch.setattr(mw, "_holdout_oos_ic", lambda *a, **kw: 0.9)
    import guanlan_v2.screen.model_registry as reg
    monkeypatch.setattr(reg, "save_variant", lambda vid, df, meta: saved.update(meta=meta))
    r = mw.train_promote({"variant_id": "m_rl_x_r0", "name": "t", "kind": "xgboost",
                          "recipe": {"features": ["rank(close)"], "universe": "csi_fast",
                                     "start": "2026-01-01"},
                          "status": "draft"})
    assert r["ok"] is True and saved["meta"]["status"] == "draft"
```

`tests/test_research_loop.py` 追加(复用 T4 `_ex_ok`/`_wire`):

```python
def test_product_route_compose_materializes_weights(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[])
    g2 = {"nodes": [{"id": "a", "type": "formula", "params": {"expr": "rank(x)"}},
                    {"id": "b", "type": "formula", "params": {"expr": "rank(y)"}}], "edges": []}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": g2})
    ok = _ex_ok(exprs=("rank(x)", "rank(y)"))
    ok["terminal"] = {"kind": "analysis", "node_id": "an", "payload": {
        "members": ["rank(x)", "rank(y)"],
        "weights": [{"name": "rank(x)", "weight": 0.63}, {"name": "rank(y)", "weight": 0.37}]}}
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: dict(ok))
    saved = {}
    monkeypatch.setattr(rl, "_save_compose_expr",
                        lambda name, expr, goal, diag, meta:
                        saved.update(name=name, expr=expr) or {"ok": True, "name": name})
    end = rl.run_research_loop("rr_t13", "找组合", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "draft_compose"
    assert saved["expr"] == "(0.63)*(rank(x)) + (0.37)*(rank(y))"   # 权重物化线性表达式
    assert "达标" in lessons[0]


def test_product_route_model_channel(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, evals=[])
    gml = {"nodes": [
        {"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": "fe", "type": "feature", "params": {"tag": "IC"}},
        {"id": "m", "type": "xgb", "params": {"trees": 200, "depth": 4}},
        {"id": "mf", "type": "mf", "params": {}},
        {"id": "an", "type": "analysis", "params": {}},
    ], "edges": [{"from": ["f", "out"], "to": ["fe", "feat"]},
                 {"from": ["fe", "fe"], "to": ["m", "fe"]},
                 {"from": ["m", "model"], "to": ["mf", "m1"]},
                 {"from": ["mf", "factor"], "to": ["an", "factor"]}]}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": gml})
    monkeypatch.setattr(rl, "_run_graph_eval",
                        lambda graph, p, pr, k, mr: _ex_ok(exprs=("rank(close)",), has_ml=True))
    seen = {}
    monkeypatch.setattr(rl, "_call_train_promote",
                        lambda spec: seen.update(spec=spec) or
                        {"ok": True, "variant_id": spec["variant_id"], "status": "draft"})
    end = rl.run_research_loop("rr_t14", "ML找因子", 3, 0.02, "csi300_active", "month",
                               None, None, progress=lambda **kw: None)
    assert end["promoted"]["status"] == "draft_model"
    sp = seen["spec"]
    assert sp["kind"] == "xgboost" and sp["status"] == "draft"
    assert sp["recipe"]["features"] == ["rank(close)"]
    assert sp["recipe"]["params"] == {"n_estimators": 200, "max_depth": 4}   # hpMap 反向
    assert sp["recipe"]["universe"] == "csi300_active"                       # run 参数权威


def test_product_route_model_save_failed_honest(monkeypatch, tmp_path):
    lessons, _, _ = _wire(monkeypatch, tmp_path, evals=[])
    gml = {"nodes": [{"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
                     {"id": "fe", "type": "feature", "params": {}},
                     {"id": "m", "type": "lstm", "params": {}}],
           "edges": [{"from": ["f", "out"], "to": ["fe", "feat"]},
                     {"from": ["fe", "fe"], "to": ["m", "fe"]}]}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": gml})
    monkeypatch.setattr(rl, "_run_graph_eval",
                        lambda graph, p, pr, k, mr: _ex_ok(has_ml=True))
    monkeypatch.setattr(rl, "_call_train_promote",
                        lambda spec: {"ok": False,
                                      "reason": "kind 'lstm' 暂不支持生产入库(首期树模型)"})
    end = rl.run_research_loop("rr_t15", "lstm", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "save_failed"
    assert "暂不支持生产入库" in end["promoted"]["reason"]     # train_promote 诚实拒绝原样透出
    assert lessons and "入库失败" in lessons[0]
```

同任务删 `test_loop_multi_expr_pass_skips_autosave`(`skipped_multi` 语义退役,由 compose 通道测试取代)。

- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3: 实现**
  - `model_workflow.py` `train_promote` 中 `meta = {...}` 之后、`meta = _apply_promote_gate(meta, oos_ic)` 之前插:

```python
    if spec.get("status"):                   # P4:调用方强制状态(研究回路恒 draft);门只降不升
        meta["status"] = str(spec["status"])
```

  - `loop.py` 加四个函数(放 `_save_draft` 之后):

```python
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
```

  - 过门块产物段(原 `if len(exprs) == 1: … else: skipped_multi`)整体替换为:

```python
        if gate["passed"]:
            progress(phase="promote", label="③ 达标 · 产物入库(draft 待人审)…", round_k=k)
            promoted = _route_product(run_id, k, graph, goal, diag, metrics,
                                      ex.get("terminal"), universe)
            break
```

  - 教训分支:`draft` 分支条件改 `promoted.get("status") in ("draft", "draft_compose", "draft_model")`(文案插入 status);`skipped_multi` 分支整块删除;`save_failed` 分支保留。
- [ ] **Step 4:** Run `pytest tests/test_research_loop.py tests/test_model_workflow_promote.py -q` → 全 PASS
- [ ] **Step 5:** `git commit -m "feat(research): 达标产物三通道(单因子/组合权重物化/模型train_promote强制draft)"`

### Task 6: vintage 扩面 + UI 小填充

**Files:** Modify `guanlan_v2/screen/factor_vintage.py`、`guanlan_v2/factorlib/api.py`、`ui/screen/screen-app.jsx`、`ui/screen/观澜 · 选股.html`、`ui/seats/luozi-panels.jsx`、`ui/seats/观澜 · 落子.html`;Test `tests/test_factorlib_draft.py`(追加)

- [ ] **Step 1: 失败测试**(追加进 `tests/test_factorlib_draft.py`;TestClient/store 隔离方式**以该文件既有 fixture 为准,同风格复用**)

```python
def test_vintage_sweep_includes_drafts(monkeypatch):
    """P4:vintage 扫描面 = FACTOR_DEFS + factorlib draft(度量不上架)。"""
    import guanlan_v2.screen.factor_vintage as fv

    class _S:
        def list_factors(self, validate=True):
            return [{"name": "lib_rl_x_r0", "expr": "rank(close)", "status": "draft"},
                    {"name": "lib_ok", "expr": "rank(open)"}]          # 非 draft 不并入

    monkeypatch.setattr("guanlan_v2.screen.catalog.FACTOR_DEFS",
                        {"mom20": {"expr": "rank(mom_20)", "family": "动量"}})
    monkeypatch.setattr("guanlan_v2.factorlib.store.LibraryFactorStore", lambda: _S())
    ids = [i[0] for i in fv._sweep_items()]
    assert "mom20" in ids and "lib_rl_x_r0" in ids and "lib_ok" not in ids


def test_factorlib_list_attaches_vintage_for_drafts(monkeypatch, tmp_path):
    client = _client(tmp_path)          # 以本文件既有构造为准
    client.post("/factorlib/save", json={"name": "lib_d1", "expr": "rank(close)",
                                         "status": "draft"})
    client.post("/factorlib/save", json={"name": "lib_p1", "expr": "rank(open)"})
    monkeypatch.setattr("guanlan_v2.screen.factor_vintage.cs_vintage_asof",
                        lambda fid, d, **kw: ({"ic": 0.021, "n": 40, "dir": 1,
                                               "asof": "2026-07-02"}
                                              if fid == "lib_d1" else None))
    j = client.get("/factorlib/list?validate=false").json()
    by = {f["name"]: f for f in j["factors"]}
    assert by["lib_d1"]["vintage"] == {"ic": 0.021, "n": 40, "asof": "2026-07-02"}
    assert "vintage" not in by["lib_p1"]        # 只给 draft 附;无值不附(诚实空态)
```

- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3: 实现**
  - `factor_vintage.py` 加模块级 `_sweep_items()` 并在 `compute_factor_vintage` 内把 `for fid, meta in FACTOR_DEFS.items():` 改 `for fid, meta in _sweep_items():`:

```python
def _sweep_items():
    """vintage 扫描面 = 选股目录 FACTOR_DEFS + factorlib 待审 draft(P4:度量不上架——
    draft 仍不进选股目录,但前向真实表现从出生起就有档可查)。"""
    from guanlan_v2.screen.catalog import FACTOR_DEFS
    items = list(FACTOR_DEFS.items())
    have = {str(k) for k, _ in items}
    try:
        from guanlan_v2.factorlib.store import LibraryFactorStore
        for f in LibraryFactorStore().list_factors(validate=False):
            nm, expr = str(f.get("name") or ""), f.get("expr")
            if f.get("status") == "draft" and expr and nm and nm not in have:
                items.append((nm, {"expr": expr, "family": "draft"}))
    except Exception:  # noqa: BLE001 — draft 并入失败不挡正式因子 vintage
        pass
    return items
```

  (fl_ids 判定 `str(fid).startswith("lib_")` 已覆盖 draft 名,tsic 表自然并入。)
  - `factorlib/api.py` list 端点 factors 组装后追加:

```python
        # P4:draft 附前向 vintage(有值才附=诚实空态;失败静默不挡清单)
        try:
            from datetime import date as _d
            from guanlan_v2.screen.factor_vintage import cs_vintage_asof
            for f in factors:
                if f.get("status") == "draft":
                    v = cs_vintage_asof(str(f.get("name")), _d.today().isoformat())
                    if v:
                        f["vintage"] = {"ic": v.get("ic"), "n": v.get("n"),
                                        "asof": v.get("asof")}
        except Exception:  # noqa: BLE001
            pass
```

  - `ui/screen/screen-app.jsx` DraftFactorSection 行内(IC span 与转正按钮之间)插:

```jsx
          {f.vintage && <span className="mono" title={'前向 vintage IC(出生后真实 OOS,截至 ' + (f.vintage.asof || '—') + ')'} style={{ fontSize: 9, color: f.vintage.ic >= 0 ? 'var(--zhu)' : 'var(--dai)', flexShrink: 0 }}>前向 {(f.vintage.ic >= 0 ? '+' : '') + (+f.vintage.ic).toFixed(3)}·n{f.vintage.n}</span>}
```

  - `ui/seats/luozi-panels.jsx` promoBadge:`skipped_multi` 分支删除,原位插:

```jsx
    if (pr.status === 'draft_compose') return <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--jin)', color: 'var(--jin)', flexShrink: 0 }}>组合draft·待人审</span>;
    if (pr.status === 'draft_model') return <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--jin)', color: 'var(--jin)', flexShrink: 0 }}>模型draft·工坊待审</span>;
```

  - 两 html `?v=` bump(用 Edit 精确替换,勿动其他 `?v=`):`观澜 · 选股.html` 的 `screen-app.jsx?v=…` → `?v=20260703p4`;`观澜 · 落子.html` 的 `luozi-panels.jsx?v=…` → `?v=20260703p4`。
- [ ] **Step 4:** Run `pytest tests/test_factorlib_draft.py -q` → PASS;`grep -rn "20260703p4" ui/` 两处命中
- [ ] **Step 5:** `git commit -m "feat(vintage+ui): draft进vintage扫描面+list附前向IC+待审区徽章+promoBadge两态"`

### Task 7: 全量回归 + 真机 e2e@9998 + 还原(控制器亲手执行,绝不转包)

- [ ] **Step 1:** 全量 `pytest tests/ -q` → ≥840 passed 0 failed(若有失败先归属:单跑失败文件;非本分支引入则记录并继续,本分支引入必修)。
- [ ] **Step 2:** 起 9998:scratchpad 写 `run_server_p4.py`(`os.environ.setdefault("GUANLAN_PORT","9998")` + `from guanlan_v2.server import main; main()`);`FA_CONFIG_DIR` 指 scratchpad `e2e_config/`(llm.yaml 副本 + `workflow: {provider: deepseek, model: deepseek-v4-pro}`,保留 industry_extract 条目);`PYTHONPATH=G:\guanlan-v2`;健康轮询 200。
- [ ] **Step 3:** `POST /workflow/run` 冒烟:最小图(formula `rank(-delta(close,5))` → analysis)→ `ok:true` + metrics.rank_ic 非空。
- [ ] **Step 4:** 回路真跑 A(ML 图):goal=「用 xgboost 机器学习模型在沪深300活跃股里找一个量价因子并检验」,max_rounds=3,min_rank_ic=0.02;轮询 done;核验:轮次档案 `terminal_kind/node_errors` 显形;达标 → `promoted.status ∈ {draft_model, save_failed}`,若 draft_model → registry `m_rl_*` meta.status=="draft" 且 `GET /screen/models` 默认列表**不含**它(红线在拦);未达标 → promoted null 诚实。
- [ ] **Step 5:** 回路真跑 B(Sharpe 门):goal=「构造一个量价背离的选股因子,并检验它的截面选股能力」(2026-07-03 真机同款,彼时 rank_ic=0.0376/sharpe=-0.98 过旧门),min_rank_ic=0.03 → 新门下该形态**不得**入 draft(promoted null,档案 gate.sharpe_required=true;若 v4-pro 提出 Sharpe>0 的更优因子过门=合法,核验其指标真实)。
- [ ] **Step 6:** 浏览器速核(playwright):落子研究卡新 run 显形+徽章;选股待审区正常渲染(vintage 无值不显=合法空态)。
- [ ] **Step 7:** 还原:杀 9998;删 e2e 产物(factorlib mined 新 json、registry 新 `m_rl_*` 变体);run 档案(var/research_*.jsonl)**保留**=真实历史;`git status` 干净;9999 健康 200。
- [ ] **Step 8:** 台账 Ledger 记录;如 e2e 只读无代码改动则无 commit。

### Task 8: 合并收尾

- [ ] finishing-a-development-branch:全量绿 → 合 main(用户已预授权惯例)→ 重启 9999(杀进程,看门狗 ~41s 自愈,健康核验)→ 更新记忆 topic 文件 + MEMORY.md 一行。

## Self-Review 记录

- **Spec 覆盖:**§1→T1/T2;§2→T4;§3→T4;§4→T5;§5→T6;§6→T3;§7→各任务+T7;§8 展望不实现 ✓。
- **占位符扫描:**T6 `_client` 指令为"复用该文件既有 fixture 同风格"(明确指令非 TBD)✓;无 TODO/TBD ✓。
- **类型一致:**`_run_graph_eval(graph,p,progress,k,max_rounds)` T4 定义、T5 测试同签名;`_ex_ok` T4 定义 T5 复用;run_graph 返回键 T2/T3/T4 一致;`_HPMAP/_ML_KINDS/_num` T2 定义 T5 复用 ✓。
- **已知连带(实现者必做):**T4 改 `_wire`/删 `test_metrics_of_report2_and_compose`/适配既有 8 测试;T5 删 `test_loop_multi_expr_pass_skips_autosave`;loop.py 删 `_metrics_of` 与三道菜函数(`_resp_json` 若仍被 loop 其他处引用则保留)。
- **风险注记:**真实 In 模型字段以 api.py 现状为准(本计划已核实至 2026-07-03);若 pydantic 校验拒绝某 kw(如 label=None),实现者以最小改动对齐(ModelTrainIn.label 是 Optional[str],None 合法)。
