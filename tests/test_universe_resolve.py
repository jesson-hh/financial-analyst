from __future__ import annotations
import pandas as pd
import pytest

from financial_analyst.data.universe import resolve_universe_codes


def test_resolves_bundled_csi300_active():
    codes = resolve_universe_codes("csi300_active")
    assert isinstance(codes, list) and len(codes) > 0
    assert all(isinstance(c, str) for c in codes)


def test_explicit_file_path(tmp_path):
    f = tmp_path / "my_uni.txt"
    f.write_text("SH600519\nSZ000858  # 五粮液\n\n", encoding="utf-8")
    codes = resolve_universe_codes(str(f))
    assert codes == ["SH600519", "SZ000858"]


def test_f10_fallback_for_index_universe(tmp_path, monkeypatch):
    """csi500 has no bundled .txt → falls back to f10 index-constituent parquet."""
    import financial_analyst.data.universe as uni
    monkeypatch.setattr(uni, "_f10_codes", lambda universe: ["SH600000", "SZ000001"] if universe == "csi500" else [])
    codes = resolve_universe_codes("csi500")
    assert codes == ["SH600000", "SZ000001"]


def test_unknown_returns_empty(monkeypatch):
    import financial_analyst.data.universe as uni
    monkeypatch.setattr(uni, "_f10_codes", lambda universe: [])
    assert resolve_universe_codes("totally_unknown_xyz") == []


def test_txt_preferred_over_f10(monkeypatch):
    """When a .txt universe resolves, the f10 fallback must NOT be consulted."""
    import financial_analyst.data.universe as uni

    def _boom(u):
        raise AssertionError("f10 fallback should not be called when .txt resolves")

    monkeypatch.setattr(uni, "_f10_codes", _boom)
    codes = resolve_universe_codes("csi300_active")  # bundled .txt exists
    assert isinstance(codes, list) and len(codes) > 0


def test_resolves_csi_fast_pool():
    """快测池 csi_fast.txt (~100 大盘) 解析为带前缀真实码。"""
    codes = resolve_universe_codes("csi_fast")
    assert 80 <= len(codes) <= 100
    assert all(c[:2] in ("SH", "SZ", "BJ") for c in codes)
