"""Smoke test that example stubs import cleanly and conform to base interfaces."""
import sys
from pathlib import Path

# Add repo root to sys.path so `examples` is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_fm_cluster_example_implements_base_model():
    from financial_analyst.models.base import BaseModel
    from examples.custom_model_fm_cluster import FMClusterModel
    assert issubclass(FMClusterModel, BaseModel)
    m = FMClusterModel()
    pred = m.predict("SH600519", "2026-05-15")
    assert "score" in pred
    meta = m.metadata()
    assert meta["name"] == "fm_cluster"


def test_csv_loader_example_implements_base_loader(tmp_path):
    from financial_analyst.data.loaders.base import BaseLoader
    from examples.custom_loader_csv_only import SimpleCsvLoader
    assert issubclass(SimpleCsvLoader, BaseLoader)
    # Create a sample CSV
    import pandas as pd
    pd.DataFrame({
        "trade_date": ["2026-05-15"], "open": [100], "high": [105],
        "low": [95], "close": [102], "vol": [1e6], "amount": [1e8],
    }).to_csv(tmp_path / "SH600519.csv", index=False)
    loader = SimpleCsvLoader(csv_dir=str(tmp_path))
    df = loader.fetch_quote("SH600519", "2026-05-01", "2026-05-31")
    assert len(df) == 1


def test_f10_collector_example_implements_base(tmp_path):
    from financial_analyst.data.collectors.f10.base import BaseF10Collector
    from examples.custom_f10_collector import TdxF10Collector
    assert issubclass(TdxF10Collector, BaseF10Collector)
    c = TdxF10Collector()
    written = c.collect("SH600519", target_dir=tmp_path)
    assert len(written) >= 1


def test_news_collector_example_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    from examples.custom_news_collector import TushareNewsCollector
    import pytest
    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        TushareNewsCollector()
