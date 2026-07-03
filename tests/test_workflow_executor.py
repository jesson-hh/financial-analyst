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
