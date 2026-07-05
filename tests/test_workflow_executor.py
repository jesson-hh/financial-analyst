# -*- coding: utf-8 -*-
"""P4 执行器纯函数层单测:拓扑决序/池回溯/图签名/指标抽取。零引擎零网络。"""
import guanlan_v2.workflow.executor as ex
from fastapi.responses import JSONResponse


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
    nodes = [_n("s", "source", universe="csi300_active", oos_frac=0.3, codes="600000.SH 600001.SH,600002.SH"),
             _n("f", "formula", expr="rank(close)"), _n("fe", "feature"), _n("m", "xgb")]
    edges = [_e("s", "data", "fe", "src"), _e("f", "out", "fe", "feat"), _e("fe", "fe", "m", "fe")]
    u = ex.universe_for_node("m", nodes, edges)                # 多跳回溯 m→fe→s
    assert u["wired"] is True and u["universe"] == "csi300_active" and u["oos_frac"] == 0.3
    assert u["codes"] == ["600000.SH", "600001.SH", "600002.SH"]  # 空白+逗号混合分隔
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


# ── run_graph 分发(假计算函数)──────────────────────────────────────────


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


def test_run_graph_feature_multi_feat_edges_aggregate(monkeypatch):
    # 多条 formula→feature.feat 边 → ML 收到多特征(保序去重,镜像前端 deriveRecipeForNode)
    g = {"nodes": [
        {"id": "f1", "type": "formula", "x": 0, "y": 0, "params": {"expr": "rank(pb)"}},
        {"id": "f2", "type": "formula", "x": 0, "y": 1, "params": {"expr": "rank(-turnover_rate)"}},
        {"id": "f3", "type": "formula", "x": 0, "y": 2, "params": {"expr": "rank(-turnover_rate)"}},
        {"id": "fe", "type": "feature", "x": 1, "y": 0, "params": {"tag": "IC"}},
        {"id": "m", "type": "lgbm", "x": 2, "y": 0, "params": {}},
        {"id": "mf", "type": "mf", "x": 3, "y": 0, "params": {}},
        {"id": "an", "type": "analysis", "x": 4, "y": 0, "params": {}},
    ], "edges": [
        {"from": ["f1", "out"], "to": ["fe", "feat"]},
        {"from": ["f2", "out"], "to": ["fe", "feat"]},
        {"from": ["f3", "out"], "to": ["fe", "feat"]},   # 重复表达式 → 去重
        {"from": ["fe", "fe"], "to": ["m", "fe"]},
        {"from": ["m", "model"], "to": ["mf", "m1"]},
        {"from": ["mf", "factor"], "to": ["an", "factor"]},
    ]}
    seen = {}

    def fake_train(body, kind):
        seen["features"] = list(body.features or [])
        return JSONResponse(_REPORT)

    monkeypatch.setattr(ex, "_call_train", fake_train)
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is True
    assert seen["features"] == ["rank(pb)", "rank(-turnover_rate)"]  # 聚合+保序去重
    assert out["warnings"] == []                           # 白名单口聚合不告警


def test_run_graph_multi_edge_nonwhitelist_warns_last_wins(monkeypatch):
    # 两个 feature 连同一 lgbm.fe 口(非白名单)→ 仅最后一条边生效 + warnings 显形(不再静默)
    g = {"nodes": [
        {"id": "f1", "type": "formula", "x": 0, "y": 0, "params": {"expr": "rank(pb)"}},
        {"id": "f2", "type": "formula", "x": 0, "y": 1, "params": {"expr": "rank(dv_ttm)"}},
        {"id": "fe1", "type": "feature", "x": 1, "y": 0, "params": {"tag": "IC"}},
        {"id": "fe2", "type": "feature", "x": 1, "y": 1, "params": {"tag": "IC"}},
        {"id": "m", "type": "lgbm", "x": 2, "y": 0, "params": {}},
        {"id": "mf", "type": "mf", "x": 3, "y": 0, "params": {}},
        {"id": "an", "type": "analysis", "x": 4, "y": 0, "params": {}},
    ], "edges": [
        {"from": ["f1", "out"], "to": ["fe1", "feat"]},
        {"from": ["f2", "out"], "to": ["fe2", "feat"]},
        {"from": ["fe1", "fe"], "to": ["m", "fe"]},
        {"from": ["fe2", "fe"], "to": ["m", "fe"]},        # 同口第二条边
        {"from": ["m", "model"], "to": ["mf", "m1"]},
        {"from": ["mf", "factor"], "to": ["an", "factor"]},
    ]}
    seen = {}

    def fake_train(body, kind):
        seen["features"] = list(body.features or [])
        return JSONResponse(_REPORT)

    monkeypatch.setattr(ex, "_call_train", fake_train)
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert seen["features"] == ["rank(dv_ttm)"]            # 后到覆盖先到(边序最后)
    assert any("m" in w and "fe" in w and "2" in w for w in out["warnings"])  # 显形


def test_validate_graph_allows_multi_feat_edges_only():
    # 服务端权威校验:feature.feat 口放行多边(多特征 ML 入口);其余口维持单入边
    import json
    import guanlan_v2.workflow.api as wapi
    base = {"nodes": [
        {"id": "s", "type": "source", "params": {}},
        {"id": "f1", "type": "formula", "params": {"expr": "rank(pb)"}},
        {"id": "f2", "type": "formula", "params": {"expr": "rank(roe)"}},
        {"id": "fe", "type": "feature", "params": {}},
        {"id": "m", "type": "lgbm", "params": {}},
        {"id": "mf", "type": "mf", "params": {}},
        {"id": "an", "type": "analysis", "params": {}},
    ], "edges": [
        {"from": ["s", "data"], "to": ["fe", "src"]},
        {"from": ["f1", "out"], "to": ["fe", "feat"]},
        {"from": ["f2", "out"], "to": ["fe", "feat"]},   # feat 口第二条边 → 放行
        {"from": ["fe", "fe"], "to": ["m", "fe"]},
        {"from": ["m", "model"], "to": ["mf", "m1"]},
        {"from": ["fe", "fe"], "to": ["mf", "f1"]},
        {"from": ["mf", "factor"], "to": ["an", "factor"]},
    ]}
    ok, errs = wapi.validate_graph(base)
    assert ok is True and errs == []
    bad = json.loads(json.dumps(base))                    # 非白名单口(lgbm.fe)多边仍拒
    bad["nodes"].append({"id": "fe2", "type": "feature", "params": {}})
    bad["edges"].append({"from": ["f2", "out"], "to": ["fe2", "feat"]})
    bad["edges"].append({"from": ["fe2", "fe"], "to": ["m", "fe"]})
    ok2, errs2 = wapi.validate_graph(bad)
    assert ok2 is False and any("m.fe" in e for e in errs2)
    # feat 口多边例外不放过 dt 不匹配:第二条边若 dt 错(model→series)仍拒
    bad_dt = json.loads(json.dumps(base))
    bad_dt["edges"].append({"from": ["m", "model"], "to": ["fe", "feat"]})
    ok3, errs3 = wapi.validate_graph(bad_dt)
    assert ok3 is False and any("dt 不匹配" in e for e in errs3)
    # LLM grounding 与验证器同步:硬约束行必须写明 feat 口例外
    line = next(ln for ln in wapi.SYSTEM_PROMPT.splitlines() if "最多一条入边" in ln)
    assert "feat" in line


def test_critique_constraints_states_multi_feature_route():
    # 批判环语义声明须明示多特征 ML 的表达方式(防 LLM 在单特征子空间打转)
    from guanlan_v2.research.loop import _CRITIQUE_CONSTRAINTS
    assert "多特征" in _CRITIQUE_CONSTRAINTS
    assert "feat" in _CRITIQUE_CONSTRAINTS


def test_run_graph_unsupported_node_type_honest():
    g = {"nodes": [{"id": "v", "type": "validate", "x": 0, "y": 0, "params": {}}], "edges": []}
    out = ex.run_graph(g, overrides={"universe": "csi_fast", "freq": "month", "oos_frac": 0.3})
    assert out["ok"] is False
    assert any("不支持的节点类型" in e["error"] for e in out["node_errors"])
