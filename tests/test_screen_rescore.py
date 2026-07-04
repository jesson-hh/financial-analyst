# -*- coding: utf-8 -*-
"""P5 再打分引擎单测:产业链分/情绪分缓存/综合分/池来源。零网络零引擎(全打桩)。"""
import math

import pytest

import guanlan_v2.screen.rescore as rs


def _board(ok=True):
    return {"ok": ok, "reason": None if ok else "板坏了",
            "freshness": {"quote_date": "2026-07-03", "last_ingest_at": "2026-07-03T20:00"},
            "segments": [
                {"id": "A1", "name": "算力", "display_name": "AI算力", "adjacent": False,
                 "research": {"score": 3.0}, "therm": 80.0, "quadrant": "hh"},
                {"id": "A2", "name": "制造", "display_name": "先进制造", "adjacent": False,
                 "research": {"score": -1.5}, "therm": None, "quadrant": "ll"},
                {"id": "ADJ", "name": "邻接", "display_name": "邻接", "adjacent": True},
            ]}


_FW_SEGS = [
    {"id": "A1", "stocks": [{"code": "SH688256", "name": "甲"}, {"code": "SH600000", "name": "乙"}]},
    {"id": "A2", "stocks": [{"code": "SH600000", "name": "乙"}]},
    {"id": "ADJ", "stocks": [{"code": "SZ000001", "name": "丙"}]},
]


def test_industry_scores_strongest_seg_and_off_chain_none(monkeypatch):
    monkeypatch.setattr(rs, "_load_board", lambda: _board())
    monkeypatch.setattr(rs, "_load_framework_segments", lambda: _FW_SEGS)
    out, fresh = rs.industry_scores(["SH600000", "SH688256", "SH999999"])
    assert fresh["quote_date"] == "2026-07-03"
    a = out["SH600000"]                      # 两环(A1/A2),取 chain 最强环 A1
    assert a["seg"] == "A1" and a["seg_name"] == "AI算力" and a["quadrant"] == "hh"
    exp = (math.tanh(3.0 / 3.0) + (80.0 / 50.0 - 1.0)) / 2   # research 归一 + therm 归一 等权
    assert abs(a["chain"] - round(exp, 4)) < 1e-9
    assert out["SH688256"]["seg"] == "A1"
    assert out["SH999999"] is None           # 不在链上 → None(诚实)


def test_industry_scores_adjacent_skip_and_single_part(monkeypatch):
    monkeypatch.setattr(rs, "_load_board", lambda: _board())
    monkeypatch.setattr(rs, "_load_framework_segments",
                        lambda: [{"id": "ADJ", "stocks": [{"code": "SZ000001"}]}])
    out, _ = rs.industry_scores(["SZ000001"])
    assert out["SZ000001"] is None           # 只挂邻接环 → None
    monkeypatch.setattr(rs, "_load_framework_segments",
                        lambda: [{"id": "A2", "stocks": [{"code": "SH000002"}]}])
    out2, _ = rs.industry_scores(["SH000002"])
    assert abs(out2["SH000002"]["chain"] - round(math.tanh(-1.5 / 3.0), 4)) < 1e-9  # 单成分可算


def test_industry_scores_board_fail_raises(monkeypatch):
    monkeypatch.setattr(rs, "_load_board", lambda: _board(ok=False))
    with pytest.raises(rs.RescoreError):
        rs.industry_scores(["SH600000"])


def test_news_scores_cache_hit_skips_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "NEWS_CACHE_PATH", tmp_path / "cache.jsonl")
    calls = []

    def fake_news(codes):
        calls.append(list(codes))
        return {"ok": True, "as_of": "10:00", "market_read": "平", "market_tilt": "中性",
                "sentiment": {"SH600000": {"tag": "利好", "read": "中标"}}}

    monkeypatch.setattr(rs, "_call_news", fake_news)
    out1, st1 = rs.news_scores(["SH600000", "SH600001"], top_n=50)
    assert out1["SH600000"] == {"tag": "利好", "read": "中标", "score": 1.0}
    assert out1["SH600001"] is None          # LLM 没判(无新闻)→ None,且落缓存
    assert st1["llm_calls"] == 1 and st1["cache_hits"] == 0
    out2, st2 = rs.news_scores(["SH600000", "SH600001"], top_n=50)
    assert len(calls) == 1                   # 当日缓存命中,不再调 LLM
    assert st2["llm_calls"] == 0 and st2["cache_hits"] == 2
    assert out2["SH600000"]["score"] == 1.0 and out2["SH600001"] is None


def test_news_scores_fail_none_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "NEWS_CACHE_PATH", tmp_path / "cache.jsonl")
    monkeypatch.setattr(rs, "_call_news", lambda codes: {"ok": False, "reason": "限频"})
    out, st = rs.news_scores(["SH600000"], top_n=50)
    assert out["SH600000"] is None and "限频" in st["news_fail"]
    assert not (tmp_path / "cache.jsonl").exists()   # 失败不写缓存(下次可重试)


def test_news_scores_top_n_clips(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "NEWS_CACHE_PATH", tmp_path / "c.jsonl")
    seen = {}
    monkeypatch.setattr(rs, "_call_news",
                        lambda codes: seen.update(codes=list(codes)) or {"ok": True, "sentiment": {}})
    rs.news_scores(["A", "B", "C"], top_n=2)
    assert seen["codes"] == ["A", "B"]


def test_composite_parts():
    assert rs.composite_score(75.0, 0.5, 1.0) == {
        "score": round(((75 / 50 - 1) + 0.5 + 1.0) / 3, 4), "parts": 3}
    assert rs.composite_score(None, 0.5, None) == {"score": 0.5, "parts": 1}
    assert rs.composite_score(None, None, None) == {"score": None, "parts": 0}


def test_v4_pool_reads_parquet(monkeypatch, tmp_path):
    import pandas as pd
    p = tmp_path / "v4.parquet"
    pd.DataFrame({"code": ["SH1", "SH2", "SH3"], "lgb_pct": [0.10, 0.99, 0.50]}).to_parquet(p)
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda: p)
    pool = rs.v4_pool(2)
    assert [r["code"] for r in pool] == ["SH2", "SH3"]   # lgb_pct 降序前 2
    assert pool[0]["v4pct"] == 99.0   # 0-1 → 0-100 归一
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda: tmp_path / "missing.parquet")
    with pytest.raises(rs.RescoreError):
        rs.v4_pool(2)


def test_industry_scores_seg_no_signal_none(monkeypatch):
    """环存在但 research/therm 双 None → 该票 None"""
    board = {"ok": True, "freshness": {}, "segments": [
        {"id": "A9", "name": "空环", "display_name": "空环", "adjacent": False,
         "research": {"score": None}, "therm": None, "quadrant": "ll"}]}
    monkeypatch.setattr(rs, "_load_board", lambda: board)
    monkeypatch.setattr(rs, "_load_framework_segments",
                        lambda: [{"id": "A9", "stocks": [{"code": "SH000009"}]}])
    out, _ = rs.industry_scores(["SH000009"])
    assert out["SH000009"] is None      # 挂正常环但环无信号 → None


def test_v4_pool_pct_column_compat(monkeypatch, tmp_path):
    """旧列名 pct (0-100) 兼容"""
    import pandas as pd
    p = tmp_path / "v4_old.parquet"
    pd.DataFrame({"code": ["SH1"], "pct": [88.0]}).to_parquet(p)
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda: p)
    pool = rs.v4_pool(1)
    assert pool[0]["v4pct"] == 88.0   # 0-100 原样


def test_v4_pool_ts_code_fallback(monkeypatch, tmp_path):
    """ts_code 列回退"""
    import pandas as pd
    p = tmp_path / "v4b.parquet"
    pd.DataFrame({"ts_code": ["SH7", "SH8"], "lgb_pct": [0.05, 0.90]}).to_parquet(p)
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda: p)
    assert [r["code"] for r in rs.v4_pool(1)] == ["SH8"]
