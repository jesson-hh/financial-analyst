"""研究回路编排器单测(P2 §2):纯函数 + 假 LLM/求值桥全链干跑。零网络零引擎数据。"""
import guanlan_v2.research.loop as rl


# ── 纯函数 ───────────────────────────────────────────────────────────────

def test_pick_dish_shapes():
    g1 = {"nodes": [{"type": "formula", "params": {"expr": "rank(-delta(close,5))"}},
                    {"type": "feature"}, {"type": "analysis"}], "edges": []}
    assert rl._pick_dish(g1) == ("report2", ["rank(-delta(close,5))"])
    g2 = {"nodes": [{"type": "formula", "params": {"expr": "a"}},
                    {"type": "formula", "params": {"expr": "b"}}], "edges": []}
    assert rl._pick_dish(g2) == ("compose", ["a", "b"])
    g3 = {"nodes": [{"type": "formula", "params": {"expr": "a"}}, {"type": "backtest"}], "edges": []}
    assert rl._pick_dish(g3) == ("backtest", ["a"])
    assert rl._pick_dish({"nodes": [{"type": "source"}], "edges": []}) == (None, [])
    g5 = {"nodes": [{"type": "factorlib", "params": {"name": "lib_x"}}], "edges": []}
    assert rl._pick_dish(g5) == ("report2", ["lib_x"])


def test_gate():
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "robust", "sharpe": 1.0}, 0.02)["passed"] is True
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "overfit", "sharpe": 1.0}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": 0.01, "oos_verdict": "robust", "sharpe": 1.0}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": None, "oos_verdict": "robust", "sharpe": 1.0}, 0.02)["passed"] is False
    assert rl._gate({}, 0.02)["passed"] is False


def test_gate_requires_positive_sharpe():
    assert rl._gate({"rank_ic": 0.05, "oos_verdict": "robust", "sharpe": 1.0}, 0.02)["passed"] is True
    g = rl._gate({"rank_ic": 0.05, "oos_verdict": "robust", "sharpe": -0.9}, 0.02)
    assert g["passed"] is False and g["sharpe_required"] is True     # Sharpe 负 → 拦(今日教训)
    assert rl._gate({"rank_ic": 0.05, "oos_verdict": "robust"}, 0.02)["passed"] is False  # 缺 sharpe 拦


# ── 全链干跑(假桥)────────────────────────────────────────────────────────

_G0 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,5))"}}],
       "edges": []}
_G1 = {"nodes": [{"id": "n1", "type": "formula", "params": {"expr": "rank(-delta(close,20))"}}],
       "edges": []}


def _wire(monkeypatch, tmp_path, evals, critique=None, generate=None):
    """接假桥:evals=逐轮 report2 求值响应队列;critique/generate 可覆盖。返回 (lessons, graphs, drafts)。"""
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    q = list(evals)
    monkeypatch.setattr(rl, "_call_generate",
                        generate or (lambda goal: {"ok": True, "graph": _G0, "attempts": 1}))
    monkeypatch.setattr(rl, "_call_critique",
                        critique or (lambda goal, metrics, graph, constraints="":
                                     {"ok": True, "diagnosis": "换更长窗口", "graph": _G1, "source": "llm"}))
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: q.pop(0))
    lessons, graphs, drafts = [], [], []
    monkeypatch.setattr(rl, "_write_lesson", lambda goal, s: lessons.append(s) or True)
    monkeypatch.setattr(rl, "_save_graph",
                        lambda goal, rid, g: graphs.append(g) or {"ok": True, "id": "w1", "name": "研究·x"})
    monkeypatch.setattr(rl, "_save_draft",
                        lambda rid, k, expr, goal, diag, m: drafts.append(expr) or
                        {"ok": True, "name": f"lib_rl_{rid[-6:]}_r{k}", "registered": True})
    return lessons, graphs, drafts


def _ex_ok(rank_ic=0.05, sharpe=1.0, oos="robust", exprs=("rank(-delta(close,5))",), has_ml=False):
    return {"ok": True, "reason": None,
            "metrics": {"rank_ic": rank_ic, "sharpe": sharpe, "ann_return": 0.1,
                        "oos_verdict": oos, "n_dates": 20, "factor": " + ".join(exprs)},
            "terminal": {"kind": "analysis", "node_id": "an", "payload": {}},
            "exprs": list(exprs), "has_ml": has_ml, "node_errors": [], "warnings": []}


_EX_WEAK = dict  # 语义帮助:_ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")


def test_loop_pass_first_round_early_stop(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[_ex_ok()])
    end = rl.run_research_loop("rr_test01", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 1 and end["best_k"] == 0
    assert end["promoted"]["status"] == "draft" and drafts == ["rank(-delta(close,5))"]
    assert end["workflow_saved"]["ok"] is True and graphs == [_G0]   # 达标轮的图存工作流库
    assert end["memory_written"] is True and "达标" in lessons[0]
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test01")
    assert len(rows) == 1 and rows[0]["gate"]["passed"] is True
    assert rs.read_runs()[0]["status"] == "done"


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


def test_loop_exhausts_rounds_no_pass(monkeypatch, tmp_path):
    weak = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")
    lessons, graphs, _ = _wire(monkeypatch, tmp_path, evals=[dict(weak), dict(weak)])
    end = rl.run_research_loop("rr_test02", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2 and end["promoted"] is None
    assert "未达标" in lessons[0]                                     # 失败也沉淀教训
    assert graphs == [_G0]                                           # 两轮同 rank_ic → 第 0 轮为最佳
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test02", limit=10)
    assert [r["stage"] for r in rows] == ["improve", "propose"]      # 新在前
    assert rows[0]["diag"] == "换更长窗口" and rows[0]["critique_source"] == "llm"


def test_loop_generate_fail_honest_stop(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[],
                                    generate=lambda goal: {"ok": False, "reason": "LLM 不可用: timeout"})
    end = rl.run_research_loop("rr_test03", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is False and "提案失败" in end["error"] and "不降级模板" in end["error"]
    assert end["n_rounds"] == 0 and graphs == [] and drafts == []
    assert lessons and "提案即失败" in lessons[0]                     # 失败也记教训
    import guanlan_v2.research.store as rs
    assert rs.read_runs()[0]["status"] == "error"


def test_loop_eval_fail_round_continues(monkeypatch, tmp_path):
    bad = {"ok": False, "reason": "缺少数据", "metrics": None, "exprs": [], "has_ml": False,
           "node_errors": [], "terminal": None, "warnings": []}
    _wire(monkeypatch, tmp_path, evals=[bad, _ex_ok()])
    end = rl.run_research_loop("rr_test04", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2                # 求值失败轮继续批判改进
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test04", limit=10)
    assert rows[1]["failed"] is True and "缺少数据" in rows[1]["error"]
    assert rows[0]["gate"]["passed"] is True


def test_loop_rule_critique_prefix(monkeypatch, tmp_path):
    weak = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")
    _wire(monkeypatch, tmp_path, evals=[dict(weak), dict(weak)],
          critique=lambda goal, metrics, graph, constraints="":
          {"ok": True, "diagnosis": "方向反了", "graph": _G1, "source": "rule", "llm_error": "x"})
    rl.run_research_loop("rr_test05", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                         progress=lambda **kw: None)
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test05", limit=10)
    assert rows[0]["diag"].startswith("(规则兜底·非 LLM) ")           # 诚实标注(对齐前端)
    assert rows[0]["critique_source"] == "rule"


def test_loop_critique_stagnant_retry_then_progress(monkeypatch, tmp_path):
    """停滞守卫:批判改进没改求值表达式(如只调 analysis.dir)→ 带停滞警告重批一次;
    重批换了表达式 → 回路继续,轮次 diag 带「(停滞重批)」显形。"""
    calls = []

    def crit(goal, metrics, graph, constraints=""):
        calls.append(constraints)
        if len(calls) == 1:                                          # 第一次:原图原样回(表达式没变)
            return {"ok": True, "diagnosis": "调 dir 参数", "graph": _G0, "source": "llm"}
        return {"ok": True, "diagnosis": "换20日窗口", "graph": _G1, "source": "llm"}

    weak0 = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")
    weak1 = _ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded", exprs=("rank(-delta(close,20))",))
    _wire(monkeypatch, tmp_path, evals=[weak0, weak1], critique=crit)
    end = rl.run_research_loop("rr_test08", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2
    assert len(calls) == 2
    assert "参数均真实生效" in calls[0]                               # 首批就声明求值语义(整图执行)
    assert "停滞" in calls[1]                                        # 重批带停滞警告
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test08", limit=10)
    assert rows[0]["diag"].startswith("(停滞重批) ")                  # 显形,luozi 卡直显
    assert rows[0]["exprs"] == ["rank(-delta(close,20))"]            # 第二轮真的换了表达式


def test_loop_critique_stagnant_twice_honest_stop(monkeypatch, tmp_path):
    """两次批判都不改求值表达式 → 诚实中断,绝不烧轮次复算同一个数(v4-pro 真机暴露的缺陷)。"""
    calls = []

    def crit(goal, metrics, graph, constraints=""):
        calls.append(constraints)
        return {"ok": True, "diagnosis": "还是只调参数", "graph": _G0, "source": "llm"}

    lessons, _, _ = _wire(monkeypatch, tmp_path,
                          evals=[_ex_ok(rank_ic=0.001, sharpe=0.1, oos="degraded")], critique=crit)
    end = rl.run_research_loop("rr_test09", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is False and "停滞" in end["error"]
    assert end["n_rounds"] == 1                                      # 只真算了一轮,没浪费复算
    assert len(calls) == 2                                           # 首批 + 停滞重批各一次
    assert lessons and "停滞" in lessons[0]                          # 教训如实记停滞
    import guanlan_v2.research.store as rs
    assert rs.read_runs()[0]["status"] == "error"


def test_call_critique_payload_has_constraints(monkeypatch):
    """真桥 _call_critique 把 constraints 透传进 /workflow/critique payload。"""
    seen = {}
    monkeypatch.setattr(rl, "_self_post",
                        lambda path, payload, timeout=300:
                        seen.update(path=path, payload=payload) or {"ok": True})
    rl._call_critique("目标", {"rank_ic": 0.1}, {"nodes": []}, constraints="只读表达式")
    assert seen["path"] == "/workflow/critique"
    assert seen["payload"]["constraints"] == "只读表达式"
    assert seen["payload"]["goal"] == "目标"


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


def test_product_route_ml_beats_compose(monkeypatch, tmp_path):
    """回归(上任务评审 Minor):图同时含 ML 子图(xgb + feature/formula 上游)与第二条
    独立 formula(≥2 表达式凑够 compose 门槛)→ 仍须走模型通道,组合通道绝不触发。"""
    _wire(monkeypatch, tmp_path, evals=[])
    gml = {"nodes": [
        {"id": "f", "type": "formula", "params": {"expr": "rank(close)"}},
        {"id": "fe", "type": "feature", "params": {"tag": "IC"}},
        {"id": "m", "type": "xgb", "params": {"trees": 200, "depth": 4}},
        {"id": "mf", "type": "mf", "params": {}},
        {"id": "an", "type": "analysis", "params": {}},
        {"id": "f2", "type": "formula", "params": {"expr": "rank(open)"}},   # 独立第二条表达式
    ], "edges": [{"from": ["f", "out"], "to": ["fe", "feat"]},
                 {"from": ["fe", "fe"], "to": ["m", "fe"]},
                 {"from": ["m", "model"], "to": ["mf", "m1"]},
                 {"from": ["mf", "factor"], "to": ["an", "factor"]}]}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": gml})
    monkeypatch.setattr(rl, "_run_graph_eval",
                        lambda graph, p, pr, k, mr: _ex_ok(exprs=("rank(close)", "rank(open)"),
                                                           has_ml=True))
    train_calls = []
    monkeypatch.setattr(rl, "_call_train_promote",
                        lambda spec: train_calls.append(spec) or
                        {"ok": True, "variant_id": spec["variant_id"], "status": "draft"})

    def _fail_compose(*a, **kw):
        raise AssertionError("组合通道不应被触发(ML 图须优先路由模型通道)")

    monkeypatch.setattr(rl, "_save_compose_expr", _fail_compose)
    end = rl.run_research_loop("rr_t14b", "ML找因子", 3, 0.02, "csi300_active", "month",
                               None, None, progress=lambda **kw: None)
    assert end["promoted"]["status"] == "draft_model"
    assert len(train_calls) == 1 and train_calls[0]["kind"] == "xgboost"


def test_product_route_ml_failed_falls_back_to_compose(monkeypatch, tmp_path):
    """保真度1(rr_3af347074b 实证):ML 节点求值失败(node_errors 有记录)、mf 已回落
    纯组合过闸 → 产物路由必须落组合通道并带 note 显形,绝不走模型通道 save_failed 丢产物。"""
    _wire(monkeypatch, tmp_path, evals=[])
    gml = {"nodes": [
        {"id": "f1", "type": "formula", "params": {"expr": "rank(roe)"}},
        {"id": "f2", "type": "formula", "params": {"expr": "rank(total_equity/total_mv)"}},
        {"id": "fe", "type": "feature", "params": {"tag": "IC"}},
        {"id": "m", "type": "xgb", "params": {"trees": 200}},
        {"id": "mf", "type": "mf", "params": {}},
        {"id": "an", "type": "analysis", "params": {}},
    ], "edges": [{"from": ["f1", "out"], "to": ["fe", "feat"]},
                 {"from": ["f2", "out"], "to": ["fe", "feat"]},
                 {"from": ["fe", "fe"], "to": ["m", "fe"]},
                 {"from": ["m", "model"], "to": ["mf", "m1"]},
                 {"from": ["fe", "fe"], "to": ["mf", "f1"]},
                 {"from": ["mf", "factor"], "to": ["an", "factor"]}]}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": gml})
    ok = _ex_ok(exprs=("rank(roe)", "rank(total_equity/total_mv)"), has_ml=True)
    ok["node_errors"] = [{"nid": "m", "type": "xgb", "error": "label_error: 标签表达式非法"}]
    ok["terminal"] = {"kind": "analysis", "node_id": "an", "payload": {
        "members": ["rank(roe)", "rank(total_equity/total_mv)"],
        "weights": [{"name": "rank(roe)", "weight": 0.5},
                    {"name": "rank(total_equity/total_mv)", "weight": 0.5}]}}
    monkeypatch.setattr(rl, "_run_graph_eval", lambda graph, p, pr, k, mr: dict(ok))

    def _fail_train(spec):
        raise AssertionError("ML 节点本轮求值已失败,模型通道不应被触发")

    monkeypatch.setattr(rl, "_call_train_promote", _fail_train)
    saved = {}
    monkeypatch.setattr(rl, "_save_compose_expr",
                        lambda name, expr, goal, diag, meta:
                        saved.update(name=name, expr=expr) or {"ok": True, "name": name})
    end = rl.run_research_loop("rr_t16", "ML找因子", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "draft_compose"      # 过闸口径=mf 回退组合 → 组合通道
    assert "rank(roe)" in saved["expr"]
    note = end["promoted"].get("note") or ""
    assert "m" in note and "失败" in note                    # 改道显形,绝不静默


def test_run_graph_eval_opts_in_model_terminal(monkeypatch):
    """保真度2:研究回路求值桥必须开 prefer_model_terminal=True(过门语义=模型真实成绩);
    画布 /workflow/run 不开(默认 False,零行为变化)。"""
    seen = {}

    def fake_run_graph(graph, overrides=None, on_node=None, prefer_model_terminal=False):
        seen["flag"] = prefer_model_terminal
        return {"ok": True}

    monkeypatch.setattr(rl.wex, "run_graph", fake_run_graph)
    out = rl._run_graph_eval({"nodes": [], "edges": []},
                             {"universe": "csi_fast", "freq": "month", "start": None, "end": None},
                             lambda **kw: None, 0, 3)
    assert out == {"ok": True} and seen["flag"] is True


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


def test_loop_save_failed_lesson_honest(monkeypatch, tmp_path):
    """过门但落库失败(如重名/磁盘错):教训须写「达标但入库失败」,绝不误写「未达标」。"""
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[_ex_ok()])
    monkeypatch.setattr(rl, "_save_draft",
                        lambda rid, k, expr, goal, diag, m:
                        {"ok": False, "reason": "因子名已存在: lib_x"})
    end = rl.run_research_loop("rr_test07", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "save_failed"
    assert "达标但入库失败" in lessons[0] and "因子名已存在" in lessons[0]
    assert "未达标" not in lessons[0]


def test_write_lesson_real_memory(monkeypatch, tmp_path):
    """_write_lesson 真调 memory_write_impl(conftest 已把 _MEMORY_PATH 隔离到 tmp)。"""
    import guanlan_v2.console.tools as ct
    mp = tmp_path / "memory.md"
    monkeypatch.setattr(ct, "_MEMORY_PATH", mp)
    assert rl._write_lesson("找一个反转因子", "研究「找一个反转因子」1轮达标:lib_x") is True
    txt = mp.read_text(encoding="utf-8")
    assert "(研究·找一个反转因子)" in txt and "lib_x" in txt          # keyed 常驻行
