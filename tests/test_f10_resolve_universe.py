"""Regression tests for f10.resolve_universe schema handling.

Locks in the 2026-05-30 fix: the research-lab index_constituents.parquet ships
Chinese headers (成分券代码) with bare 6-digit constituent codes, which the old
English-only ``stock_code`` resolver could not read — csi300/csi500 silently
returned [] and ``all`` leaked the date column (df.columns[0]).
"""
from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst.data.updaters.f10 import resolve_universe


def _write(tmp_path, df: pd.DataFrame):
    p = tmp_path / "index_constituents.parquet"
    df.to_parquet(p)
    return tmp_path


def _chinese_schema() -> pd.DataFrame:
    """Mirror the real G:/stocks parquet: Chinese 成分券代码 + bare codes + a
    leading date column (the thing ``all`` used to leak)."""
    return pd.DataFrame(
        {
            "日期": ["2026-03-31"] * 4,
            "指数代码": ["000300", "000300", "000905", "000905"],
            "成分券代码": ["600000", "000001", "600004", "300750"],
            "index_code": ["000300", "000300", "000905", "000905"],
            "index_name": ["csi300", "csi300", "csi500", "csi500"],
        }
    )


def test_chinese_schema_csi300_filters_and_prefixes(tmp_path):
    root = _write(tmp_path, _chinese_schema())
    assert resolve_universe(root, "csi300") == ["SH600000", "SZ000001"]
    assert resolve_universe(root, "csi500") == ["SH600004", "SZ300750"]


def test_all_returns_every_constituent_not_the_date(tmp_path):
    root = _write(tmp_path, _chinese_schema())
    codes = resolve_universe(root, "all")
    assert codes == ["SH600000", "SH600004", "SZ000001", "SZ300750"]
    # the date column must never leak in as a "code"
    assert "2026-03-31" not in codes
    assert all(c[:2] in ("SH", "SZ", "BJ") for c in codes)


def test_unknown_universe_raises(tmp_path):
    root = _write(tmp_path, _chinese_schema())
    with pytest.raises(ValueError):
        resolve_universe(root, "totally_unknown_xyz")


def test_english_schema_backward_compatible(tmp_path):
    """A bootstrapped bundle may use English stock_code + suffixed index_code;
    resolution must still work (index_code fallback + prefixing)."""
    df = pd.DataFrame(
        {
            "stock_code": ["600000", "000001"],
            "index_code": ["000300.SH", "000300.SH"],
        }
    )
    root = _write(tmp_path, df)
    assert resolve_universe(root, "csi300") == ["SH600000", "SZ000001"]


def test_already_prefixed_codes_pass_through(tmp_path):
    df = pd.DataFrame(
        {
            "stock_code": ["SH600000", "SZ000001"],
            "index_name": ["csi300", "csi300"],
        }
    )
    root = _write(tmp_path, df)
    assert resolve_universe(root, "csi300") == ["SH600000", "SZ000001"]
