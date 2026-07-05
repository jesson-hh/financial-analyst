# -*- coding: utf-8 -*-
"""P6′ 重排引擎单测:教训读回/上下文包/硬校验/失败降级/rerank 块 schema(打桩 LLM 零网络)。"""
import json

import pytest

from guanlan_v2.screen import rerank as rk


def _rows():
    return [
        {"code": "SH600000", "v4pct": 99.0,
         "chain": {"seg_name": "光芯片", "chain": 0.5, "quadrant": "hh",
                   "research": 2.1, "therm": 80.0},
         "news": {"tag": "利好", "read": "订单超预期", "score": 1.0}},
        {"code": "SZ000001", "v4pct": 98.0, "chain": None, "news": None},
        {"code": "SH600519", "v4pct": 97.0, "chain": None,
         "news": {"tag": "中性", "read": "例行公告", "score": 0.0}},
    ]


def _order(codes, stance="中性", reason="理由"):
    return [{"code": c, "stance": stance, "reason": reason} for c in codes]


def _order_rev(codes):
    return [{"code": c, "stance": "中性", "reason": "r"} for c in reversed(codes)]


def test_read_lessons_filters_prefix_and_tail(tmp_path, monkeypatch):
    p = tmp_path / "memory.md"
    lines = ["- [2026-07-01] (研究·某目标) 因子教训",
             "- [2026-07-02] (行业·光芯片) 教训A",
             "普通行不带key",
             "- [2026-07-03] (行业·情绪) 教训B",
             "- [2026-07-04] (行业·风格) 教训C"]
    p.write_text("\n".join(lines), encoding="utf-8")
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", p)
    got = rk.read_industry_lessons(k=2)
    assert got == ["(行业·情绪) 教训B", "(行业·风格) 教训C"]   # 只「行业·」前缀,取尾部k条,保序


def test_read_lessons_missing_file_returns_empty(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "nope.md")
    assert rk.read_industry_lessons() == []


def test_context_pack_states():
    ranked = [dict(r, rank=i + 1) for i, r in enumerate(_rows())]
    pack = rk.build_context_pack(ranked, {"ok": True, "segments": []},
                                 {"market_read": "平淡", "market_tilt": "中性"}, [])
    t = pack["tickets"]
    assert t[0]["rank"] == 1 and t[0]["chain"]["seg_name"] == "光芯片"
    assert t[1]["chain"] == "不在链上" and t[1]["news"] == "无新闻"   # 诚实字面,不编数
    assert pack["market"]["market_tilt"] == "中性" and pack["lessons"] == []


@pytest.mark.parametrize("bad", [
    lambda c: [],
    lambda c: _order(c[:-1]),
    lambda c: _order(c + ["SH999999"]),
    lambda c: _order([c[0]] + c[1:-1] + [c[0]]),
    lambda c: _order(c, stance="看多"),
    lambda c: _order(c, reason="  "),
])
def test_validate_order_rejects(bad):
    codes = [r["code"] for r in _rows()]
    ok, msg = rk.validate_order(codes, bad(codes))
    assert not ok and msg


def test_validate_order_accepts_permutation():
    codes = [r["code"] for r in _rows()]
    ok, msg = rk.validate_order(codes, _order(list(reversed(codes))))
    assert ok and msg == ""


def test_run_rerank_llm_fail_is_honest(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": True, "segments": [],
                                                       "snapshot": {}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: [])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {"ok": False, "reason": "超时"})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "超时" in out["reason"]


def test_run_rerank_invalid_order_whole_fail(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": True, "segments": [],
                                                       "snapshot": {}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: [])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {
        "ok": True, "model": "m", "data": {"order": _order(["SH600000"]), "overall": "x"}})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "rerank_failed" in out["reason"]   # 绝不部分采用


def test_run_rerank_board_down_refuses(monkeypatch):
    monkeypatch.setattr(rk, "_board_summary", lambda: {"ok": False, "reason": "语料缺"})
    out = rk.run_rerank(_rows(), {})
    assert out["ok"] is False and "产业链板不可用" in out["reason"]


def test_run_rerank_success_block_schema(monkeypatch):
    codes = [r["code"] for r in _rows()]
    monkeypatch.setattr(rk, "_board_summary", lambda: {
        "ok": True, "segments": [{"name": "光芯片", "research": 2.1, "therm": 80,
                                  "quadrant": "hh"}],
        "snapshot": {"latest_publish_ts": "2026-07-02", "n_docs": 1841}})
    monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: ["(行业·x) 教训"])
    monkeypatch.setattr(rk, "_call_llm", lambda s, u: {
        "ok": True, "model": "deepseek-chat",
        "data": {"order": _order_rev(codes), "overall": "光芯片顺风"}})
    out = rk.run_rerank(_rows(), {"market_read": "平", "market_tilt": "中性"})
    assert out["ok"] is True and out["model"] == "deepseek-chat"
    assert out["lessons_injected"] == 1
    assert out["board_snapshot"] == {"latest_publish_ts": "2026-07-02", "n_docs": 1841}
    by = {r["code"]: r for r in out["rows"]}
    assert by["SH600000"]["rank_before"] == 1 and by["SH600000"]["rank_after"] == 3
    assert by["SH600519"]["rank_after"] == 1 and by["SH600519"]["stance"] == "中性"
    assert all(r["reason"] for r in out["rows"])


def test_run_rescore_carries_rerank_block_and_ab_baskets(tmp_path, monkeypatch):
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [
        {"code": f"SH60000{i}", "v4pct": 99.0 - i} for i in range(5)])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes},
        {"llm_calls": 0, "cache_hits": 0, "market_read": "平", "market_tilt": "中性"}))
    fake_rk = {"ok": True, "model": "m", "overall": "o", "lessons_injected": 0,
               "board_snapshot": {}, "elapsed_sec": 0.1,
               "rows": [{"code": f"SH60000{i}", "rank_before": i + 1,
                         "rank_after": 5 - i, "stance": "中性", "reason": "r"}
                        for i in range(5)]}
    monkeypatch.setattr(rs, "_run_rerank_bridge", lambda rows, market: fake_rk)
    end = rs.run_rescore("rs_test", top_n=5, note="t", progress=lambda **k: None)
    assert end["ok"] and end["rerank"]["ok"]
    rows = pk.read_picks(limit=10)
    ab = [r for r in rows if r.get("kind") == "rerank_ab"]
    assert len(ab) == 2 and {r["arm"] for r in ab} == {"data", "rerank"}
    data_arm = next(r for r in ab if r["arm"] == "data")
    rr_arm = next(r for r in ab if r["arm"] == "rerank")
    assert data_arm["codes"][0] == "SH600000" and rr_arm["codes"][0] == "SH600004"
    assert all(not r.get("snapshot") for r in ab)
    assert all(r["run_id"] == "rs_test" for r in ab)


def test_run_rescore_rerank_fail_no_baskets(tmp_path, monkeypatch):
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [{"code": "SH600000", "v4pct": 99.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes}, {"llm_calls": 0, "cache_hits": 0}))
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "LLM 失败: x"})
    end = rs.run_rescore("rs_t2", top_n=5, note="", progress=lambda **k: None)
    assert end["ok"] is True                      # 打分本身成功(重排失败不拖垮 run)
    assert end["rerank"]["ok"] is False           # 失败显形
    assert pk.read_picks(limit=10) == []          # 失败绝不落 A/B 篮


def test_run_rescore_bridge_exception_does_not_kill_run(tmp_path, monkeypatch):
    """桥异常(ImportError/KeyError 等)捕获显形,run 整体 ok 不挡。"""
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [{"code": "SH600000", "v4pct": 99.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes}, {"llm_calls": 0, "cache_hits": 0}))
    # 桥本身抛异常(模拟导入或结构错误)
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: (_ for _ in ()).throw(RuntimeError("boom")))
    end = rs.run_rescore("rs_t3", top_n=5, note="", progress=lambda **k: None)
    assert end["ok"] is True                      # run 不因桥异常挂掉
    assert end["rerank"]["ok"] is False           # 重排失败显形
    assert end["rerank"]["reason"] == "RuntimeError: boom"  # 异常显形
    assert len(end["rows"]) == 1 and end["rows"][0]["code"] == "SH600000"  # 打分行保留
    assert pk.read_picks(limit=10) == []          # A/B 篮空(桥失败故不落篮)


def test_run_rescore_ab_record_failure_surfaces(tmp_path, monkeypatch):
    """A/B 篮落档失败(权限/IO 等)时 ab_recorded 显形,run 不挂,rerank ok 保持。"""
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [
        {"code": f"SH60000{i}", "v4pct": 99.0 - i} for i in range(3)])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({c: None for c in codes}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {c: None for c in codes}, {"llm_calls": 0, "cache_hits": 0}))
    # 桥返回成功,但落篮时异常
    fake_rk = {"ok": True, "model": "m", "overall": "o", "lessons_injected": 0,
               "board_snapshot": {}, "elapsed_sec": 0.1,
               "rows": [{"code": f"SH60000{i}", "rank_before": i + 1,
                         "rank_after": 3 - i, "stance": "中性", "reason": "r"}
                        for i in range(3)]}
    monkeypatch.setattr(rs, "_run_rerank_bridge", lambda rows, market: fake_rk)
    monkeypatch.setattr(rs, "_record_rerank_ab",
                        lambda run_id, rows, rk, top_n: (_ for _ in ()).throw(OSError("权限拒绝")))
    end = rs.run_rescore("rs_t4", top_n=5, note="", progress=lambda **k: None)
    assert end["ok"] is True                      # run 整体成功(篮失败不拖垮)
    assert end["rerank"]["ok"] is True            # 重排本身 ok(落档失败不改信号)
    assert end["rerank"]["ab_recorded"] is False  # 落档失败显形
    assert pk.read_picks(limit=10) == []          # 失败绝不落篮
