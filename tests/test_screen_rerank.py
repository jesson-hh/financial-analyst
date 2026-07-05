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
