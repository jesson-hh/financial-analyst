# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
import pytest


def _quotes_for(codes, trend=0.02, days=45):
    out = {}
    dates = pd.date_range("2026-05-01", periods=days, freq="B")
    for i, c in enumerate(codes):
        base = 10 + i
        close = base * (1 + trend) ** np.arange(days)
        out[c] = pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d"), "close": close,
                               "amount": np.full(days, 1e8)})
    return out


def test_quant_signals_with_injected_quotes():
    from guanlan_v2.industry.framework import load_framework, segment_pool
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    quotes = _quotes_for(pool)
    sig = quant_signals(fw, quotes=quotes)
    c2 = sig["C2"]
    assert c2["momentum20"] is not None and c2["momentum20"] > 0.3   # 每日+2%,20日≈+48.6%
    assert c2["breadth"] == 1.0
    assert c2["quote_date"] == quotes[pool[0]]["trade_date"].iloc[-1]
    assert ("v4_pct_mean" in c2) and ("excess20" in c2)   # 产物缺→None+reason 而非崩


def test_adjacent_stub_excluded():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert "G3" not in sig and "G4" not in sig


def test_empty_quotes_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert sig["C2"]["momentum20"] is None and sig["C2"]["reason"]


def test_segment_detail_fetches_only_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import aggregate
    from guanlan_v2.industry.framework import load_framework, segment_pool
    fw = load_framework()
    seen = {}

    def _fake_fetch(codes, days=45):
        seen["codes"] = list(codes)
        return {}

    monkeypatch.setattr(aggregate, "_fetch_quotes", _fake_fetch)
    r = aggregate.segment_detail("C2")
    assert r["ok"] is True
    assert set(seen["codes"]) == set(segment_pool(fw, "C2"))


def test_v4_pct_map_production_lgb_pct_column(monkeypatch, tmp_path):
    """生产 parquet 列为 lgb_pct(rank(pct=True) 值域 0-1)而非 pct —— 回退链须识别并归一 0-100。"""
    from guanlan_v2.industry.aggregate import _v4_pct_map
    from guanlan_v2.strategy import paths
    p = tmp_path / "v4_ranking_latest.parquet"
    pd.DataFrame({"code": ["SH688498", "SZ000001"], "lgb_score": [1.2, 0.3],
                  "lgb_pct": [0.987, 0.5], "lgb_rank": [1, 2],
                  "v4_total": [88.0, float("nan")], "v4_layer": ["L1", None],
                  "date": ["2026-07-04", "2026-07-04"]}).to_parquet(p)
    monkeypatch.setattr(paths, "V4_RANKING_PARQUET", p)
    m = _v4_pct_map()
    assert m is not None, "生产列名 lgb_pct 不该整体降级为 None"
    assert m["SH688498"] == pytest.approx(98.7)
    assert m["SZ000001"] == pytest.approx(50.0)


def test_v4_pct_map_legacy_pct_column(monkeypatch, tmp_path):
    """旧格式 pct(0-100)原样返回,不二次缩放。"""
    from guanlan_v2.industry.aggregate import _v4_pct_map
    from guanlan_v2.strategy import paths
    p = tmp_path / "v4_ranking_legacy.parquet"
    pd.DataFrame({"code": ["SH688498"], "pct": [98.7]}).to_parquet(p)
    monkeypatch.setattr(paths, "V4_RANKING_PARQUET", p)
    m = _v4_pct_map()
    assert m is not None
    assert m["SH688498"] == pytest.approx(98.7)


def test_board_exposes_editorial_and_derived_fields(monkeypatch, tmp_path):
    """河图设计所需字段:display_name/mrow/good/eq/mcol/dual/therm/keywords + 叙事 validation/risks。"""
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import aggregate
    monkeypatch.setattr(aggregate, "_fetch_quotes", lambda codes, days=45: {})
    monkeypatch.setattr(aggregate, "_fundflow_map", lambda: None)
    monkeypatch.setattr(aggregate, "_v4_pct_map", lambda: None)
    b = aggregate.build_board(refresh=True)
    assert b["ok"] is True
    seg = {s["id"]: s for s in b["segments"]}
    c2 = seg["C2"]
    assert c2["display_name"] == "光芯片"
    assert c2["mrow"] == "追赶" and c2["good"] is True
    assert c2["eq"] == "Δ·Ω" and c2["mcol"] == "Δ" and c2["dual"] is True
    assert "therm" in c2 and "keywords" in c2 and len(c2["keywords"]) > 0
    # 无行情注入 → 派生动量缺失,therm/quadrant 诚实降级(不编数)
    assert c2["therm"] is None
    # 相邻链 stub 也带 display_name
    assert seg["G3"]["display_name"] == "机器人 · 具身智能" and seg["G3"]["adjacent"] is True
    n1 = next(n for n in b["narratives"] if n["id"] == "N1")
    assert n1["display_name"] == "英伟达链" and "validation" in n1 and "risks" in n1


def test_segment_detail_stock_rows_shape(monkeypatch, tmp_path):
    """票池逐票行:缺行情/资金/v4 → 字段 None,键始终在。"""
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import aggregate
    monkeypatch.setattr(aggregate, "_fetch_quotes", lambda codes, days=45: {})
    monkeypatch.setattr(aggregate, "_fundflow_map", lambda: None)
    monkeypatch.setattr(aggregate, "_v4_pct_map", lambda: None)
    det = aggregate.segment_detail("C2")
    assert det["ok"] is True and len(det["stock_rows"]) == 5
    for r in det["stock_rows"]:
        assert set(["code", "name", "role", "px", "chg", "ff5", "v4pct"]).issubset(r.keys())
        assert r["px"] is None and r["ff5"] is None and r["v4pct"] is None  # 无源→诚实 None


def test_fundflow_dotted_code_matched(monkeypatch):
    from guanlan_v2.industry import aggregate
    from guanlan_v2.industry.framework import load_framework, segment_pool
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    dates = pd.date_range("2026-05-01", periods=45, freq="B")
    quotes = {c: pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d"),
                               "close": 10 * (1.02) ** np.arange(45),
                               "amount": np.full(45, 1e8)}) for c in pool}
    dotted = {f"{c[2:]}.{c[:2]}": 1.0e8 for c in pool}
    monkeypatch.setattr(aggregate, "_fundflow_map", lambda: dotted)
    sig = aggregate.quant_signals(fw, quotes=quotes)
    assert sig["C2"]["fundflow5"] == 1.0e8 * len(pool)
