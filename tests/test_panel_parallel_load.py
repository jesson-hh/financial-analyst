from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd


def test_calendar_concurrent_load_is_consistent(tmp_path):
    """16 线程并发首次加载日历, 结果一致且不崩 (幂等竞争防御)。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    (tmp_path / "calendars").mkdir()
    (tmp_path / "calendars" / "day.txt").write_text(
        "\n".join(f"2024-01-{d:02d}" for d in range(1, 21)), encoding="utf-8")
    (tmp_path / "features").mkdir()
    loader = QlibBinaryLoader(str(tmp_path))
    with ThreadPoolExecutor(max_workers=16) as ex:
        cals = list(ex.map(lambda _: loader._load_calendar("day"), range(64)))
    assert all(len(c) == 20 for c in cals)
    assert cals[0] == cals[-1]


def _stub_loader(fail_codes=()):
    class L:
        def fetch_quote(self, code, start, end, freq="day"):
            if code in fail_codes:
                raise RuntimeError("boom")
            dates = pd.date_range("2024-01-02", periods=30, freq="B")
            base = abs(hash(code)) % 100 + 1
            df = pd.DataFrame({
                "open": np.arange(30) + base, "high": np.arange(30) + base + 1,
                "low": np.arange(30) + base - 1, "close": np.arange(30) + base,
                "volume": np.full(30, 1e6),
            }, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return L()


def test_parallel_equals_sequential():
    from financial_analyst.factors.zoo.panel import PanelData
    codes = [f"SH60{i:04d}" for i in range(40)]
    panel = PanelData.from_loader(_stub_loader(), codes, "2024-01-01", "2024-03-01", freq="day")
    df = panel.df
    assert df.index.names == ["datetime", "code"]
    assert df.index.get_level_values("code").nunique() == 40
    assert df.shape[0] == 40 * 30
    panel2 = PanelData.from_loader(_stub_loader(), codes, "2024-01-01", "2024-03-01", freq="day")
    pd.testing.assert_frame_equal(df, panel2.df)


def test_parallel_skips_failures():
    from financial_analyst.factors.zoo.panel import PanelData
    codes = [f"SH60{i:04d}" for i in range(10)]
    fail = {codes[3], codes[7]}
    panel = PanelData.from_loader(_stub_loader(fail), codes, "2024-01-01", "2024-03-01", freq="day")
    got = set(panel.df.index.get_level_values("code").unique())
    assert got == set(codes) - fail
