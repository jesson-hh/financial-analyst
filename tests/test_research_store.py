"""研究回路档案纯函数单测(P2 §3)。全部 monkeypatch 路径常量指 tmp,零生产污染。照 test_screen_picks.py 形状。"""


def _rs(monkeypatch, tmp_path):
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    return rs


def test_runs_merge_and_status_derivation(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    assert rs.append_run({"run_id": "rr_a", "kind": "start", "goal": "反转", "ts": "t1"}) is True
    assert rs.append_run({"run_id": "rr_b", "kind": "start", "goal": "动量", "ts": "t2"}) is True
    assert rs.append_run({"run_id": "rr_a", "kind": "end", "ok": True, "n_rounds": 2, "ts": "t3"}) is True
    rows = rs.read_runs(limit=10)
    assert [r["run_id"] for r in rows] == ["rr_b", "rr_a"]          # 新在前(按 start 序)
    by = {r["run_id"]: r for r in rows}
    assert by["rr_a"]["status"] == "done" and by["rr_a"]["n_rounds"] == 2
    assert by["rr_a"]["goal"] == "反转"                              # start 字段保留,end 字段合并
    assert by["rr_b"]["status"] == "interrupted"                     # 无终态且非在跑 → 中断显形


def test_runs_status_error_and_running(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_run({"run_id": "rr_e", "kind": "start", "ts": "t1"})
    rs.append_run({"run_id": "rr_e", "kind": "end", "ok": False, "error": "LLM 不可用", "ts": "t2"})
    rs.append_run({"run_id": "rr_live", "kind": "start", "ts": "t3"})
    rows = rs.read_runs(limit=10, running_run_id="rr_live")
    by = {r["run_id"]: r for r in rows}
    assert by["rr_e"]["status"] == "error"
    assert by["rr_live"]["status"] == "running"


def test_rounds_filter_dirty_and_limit(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_round({"run_id": "rr_a", "k": 0, "diag": "初始"})
    with (tmp_path / "rounds.jsonl").open("a", encoding="utf-8") as f:
        f.write("{oops 不是JSON\n")
    rs.append_round({"run_id": "rr_a", "k": 1})
    rs.append_round({"run_id": "rr_b", "k": 0})
    rows = rs.read_rounds(run_id="rr_a", limit=10)
    assert [r["k"] for r in rows] == [1, 0]                          # 过滤+坏行跳过+新在前
    assert rs.read_rounds(limit=1)[0]["run_id"] == "rr_b"            # limit 钳制


def test_missing_files_return_empty(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    assert rs.read_runs() == [] and rs.read_rounds() == []


def test_append_failure_returns_false(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")                        # 父路径是文件 → mkdir 必炸
    monkeypatch.setattr(rs, "RUNS_PATH", blocker / "runs.jsonl")
    assert rs.append_run({"run_id": "rr_x", "kind": "start"}) is False


def test_chinese_not_escaped(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    rs.append_round({"run_id": "rr_a", "diag": "样本外塌缩"})
    assert "样本外塌缩" in (tmp_path / "rounds.jsonl").read_text(encoding="utf-8")
