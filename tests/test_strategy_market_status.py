# -*- coding: utf-8 -*-
"""market_status 主线面板双源收敛:artifacts(regen 自产,新鲜)优先,缺失回落 stocks 侧。"""
import guanlan_v2.strategy.market_status as ms
import guanlan_v2.strategy.ranking as R


def test_mainline_panel_prefers_fresh_artifacts(monkeypatch, tmp_path):
    art = tmp_path / "artifacts" / "monthly_mainlines_panel.parquet"
    monkeypatch.setattr(R, "MAINLINE_PARQUET", art)
    # ① artifacts 缺失 → 回落 stocks 侧(<stocks>/strategy/mainline/)
    p = ms._mainline_panel_path("G:/stocks/stock_data/cn_data")
    assert p.name == "monthly_mainlines_panel.parquet"
    assert "mainline" in p.parts and "stocks" in str(p) and p != art
    # ② artifacts 存在 → 优先它(不再读 stocks 侧陈尸面板)
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text("x", encoding="utf-8")
    assert ms._mainline_panel_path("G:/stocks/stock_data/cn_data") == art
