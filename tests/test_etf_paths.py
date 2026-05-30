"""Tests for ETF path resolution in DataPaths."""
from financial_analyst.data.paths import get_data_paths


def test_qlib_etf_defaults_beside_day(monkeypatch, tmp_path):
    for v in ("FA_QLIB_URI", "FA_QLIB_ETF_URI"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("FA_QLIB_URI", str(tmp_path / "cn_data"))
    assert get_data_paths().qlib_etf == tmp_path / "cn_data_etf"


def test_qlib_etf_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_QLIB_ETF_URI", str(tmp_path / "myetf"))
    assert get_data_paths().qlib_etf == tmp_path / "myetf"
