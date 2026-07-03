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


def test_metrics_of_report2_and_compose():
    rep = {"status": "ok", "headline_ic": {"rank_ic": 0.031}, "ic": {"rank_ic_mean": 0.02},
           "portfolio": {"sharpe": 0.8, "ann_return": 0.12},
           "oos": {"verdict": "robust"}, "n_dates": 30, "composite": True}   # report2 的 composite 是 bool
    m = rl._metrics_of(rep, "expr1")
    assert m == {"rank_ic": 0.031, "sharpe": 0.8, "ann_return": 0.12,
                 "oos_verdict": "robust", "n_dates": 30, "factor": "expr1"}
    comp = {"ok": True, "composite": {"headline_ic": {"rank_ic": 0.04},
                                      "portfolio": {"sharpe": 1.1, "ann_return": 0.2},
                                      "oos": {"verdict": "degraded"}, "n_dates": 24}}
    m2 = rl._metrics_of(comp, "a + b")
    assert m2["rank_ic"] == 0.04 and m2["oos_verdict"] == "degraded"         # composite 块展开


def test_gate():
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "robust"}, 0.02)["passed"] is True
    assert rl._gate({"rank_ic": 0.03, "oos_verdict": "overfit"}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": 0.01, "oos_verdict": "robust"}, 0.02)["passed"] is False
    assert rl._gate({"rank_ic": None, "oos_verdict": "robust"}, 0.02)["passed"] is False
    assert rl._gate({}, 0.02)["passed"] is False


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
    monkeypatch.setattr(rl, "_eval_report2", lambda expr, p: q.pop(0))
    lessons, graphs, drafts = [], [], []
    monkeypatch.setattr(rl, "_write_lesson", lambda goal, s: lessons.append(s) or True)
    monkeypatch.setattr(rl, "_save_graph",
                        lambda goal, rid, g: graphs.append(g) or {"ok": True, "id": "w1", "name": "研究·x"})
    monkeypatch.setattr(rl, "_save_draft",
                        lambda rid, k, expr, goal, diag, m: drafts.append(expr) or
                        {"ok": True, "name": f"lib_rl_{rid[-6:]}_r{k}", "registered": True})
    return lessons, graphs, drafts


_PASS = {"status": "ok", "headline_ic": {"rank_ic": 0.05}, "portfolio": {"sharpe": 1.0, "ann_return": 0.2},
         "oos": {"verdict": "robust"}, "n_dates": 30}
_WEAK = {"status": "ok", "headline_ic": {"rank_ic": 0.001}, "portfolio": {"sharpe": 0.1, "ann_return": 0.01},
         "oos": {"verdict": "degraded"}, "n_dates": 30}


def test_loop_pass_first_round_early_stop(monkeypatch, tmp_path):
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[_PASS])
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


def test_loop_exhausts_rounds_no_pass(monkeypatch, tmp_path):
    lessons, graphs, _ = _wire(monkeypatch, tmp_path, evals=[_WEAK, _WEAK])
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
    bad = {"ok": False, "reason": "缺少数据"}
    _wire(monkeypatch, tmp_path, evals=[bad, _PASS])
    end = rl.run_research_loop("rr_test04", "找反转", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2                # 求值失败轮继续批判改进
    import guanlan_v2.research.store as rs
    rows = rs.read_rounds(run_id="rr_test04", limit=10)
    assert rows[1]["failed"] is True and "缺少数据" in rows[1]["error"]
    assert rows[0]["gate"]["passed"] is True


def test_loop_rule_critique_prefix(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, evals=[_WEAK, _WEAK],
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

    _wire(monkeypatch, tmp_path, evals=[_WEAK, _WEAK], critique=crit)
    end = rl.run_research_loop("rr_test08", "找反转", 2, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["ok"] is True and end["n_rounds"] == 2
    assert len(calls) == 2
    assert "formula" in calls[0]                                     # 首批就声明求值语义(只读表达式)
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

    lessons, _, _ = _wire(monkeypatch, tmp_path, evals=[_WEAK], critique=crit)
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


def test_loop_multi_expr_pass_skips_autosave(monkeypatch, tmp_path):
    comp_pass = {"ok": True, "composite": {"headline_ic": {"rank_ic": 0.05},
                                           "portfolio": {"sharpe": 1.0, "ann_return": 0.2},
                                           "oos": {"verdict": "robust"}, "n_dates": 24}}
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[])
    g2 = {"nodes": [{"type": "formula", "params": {"expr": "a"}},
                    {"type": "formula", "params": {"expr": "b"}}], "edges": []}
    monkeypatch.setattr(rl, "_call_generate", lambda goal: {"ok": True, "graph": g2})
    monkeypatch.setattr(rl, "_eval_compose", lambda exprs, p: comp_pass)
    end = rl.run_research_loop("rr_test06", "找组合", 3, 0.02, "csi_fast", "month", None, None,
                               progress=lambda **kw: None)
    assert end["promoted"]["status"] == "skipped_multi" and drafts == []   # 多因子不自动入库(红线)
    assert "达标" in lessons[0] and "未达标" not in lessons[0]              # 教训诚实:达标≠未达标


def test_loop_save_failed_lesson_honest(monkeypatch, tmp_path):
    """过门但落库失败(如重名/磁盘错):教训须写「达标但入库失败」,绝不误写「未达标」。"""
    lessons, graphs, drafts = _wire(monkeypatch, tmp_path, evals=[_PASS])
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
