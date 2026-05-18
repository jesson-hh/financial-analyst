import pandas as pd
import pytest
from pathlib import Path
from financial_analyst.agent.mainline.mainline_classifier import MainlineClassifier


def _make_panel(tmp_path: Path) -> Path:
    """Create a small synthetic panel with 2 dates, 5 industries, statuses set."""
    rows = []
    for date_str in ["2026-04-15", "2026-05-15"]:
        for i, (industry, status) in enumerate([
            ("AI算力", "mainline"),
            ("半导体", "initiation"),
            ("白酒", "cold"),
            ("光伏", "decay"),
            ("机器人", "revival"),
        ]):
            rows.append({
                "datetime": pd.Timestamp(date_str),
                "industry": industry,
                "status": status,
                "ex_60d": 10.0 - i * 2,
                "ex_20d": 3.0 - i * 0.5,
                "ex_10d": 1.0 - i * 0.2,
                "top10_ratio_60d": 0.3,
                "lu_count_60d_sum": 10,
                "lu_max_mv_60d_mean": 100.0,
            })
    # Add a switch: 半导体 was initiation in April, became mainline in May
    for r in rows:
        if r["industry"] == "半导体" and r["datetime"] == pd.Timestamp("2026-05-15"):
            r["status"] = "mainline"
    df = pd.DataFrame(rows)
    path = tmp_path / "panel.parquet"
    df.to_parquet(path)
    return path


@pytest.mark.asyncio
async def test_classifier_loads_panel_and_groups(tmp_path):
    panel = _make_panel(tmp_path)
    agent = MainlineClassifier(memory_root=tmp_path, panel_path=str(panel))
    result = await agent.run({"asof_date": "2026-05-15"})
    assert result.ok is True
    out = result.output
    assert out.as_of == "2026-05-15"
    assert "mainline" in out.status_groups
    # AI算力 + 半导体 both mainline today
    mainlines = [r.industry for r in out.status_groups["mainline"]]
    assert "AI算力" in mainlines
    assert "半导体" in mainlines


@pytest.mark.asyncio
async def test_classifier_detects_golden_signal(tmp_path):
    panel = _make_panel(tmp_path)
    agent = MainlineClassifier(memory_root=tmp_path, panel_path=str(panel))
    result = await agent.run({"asof_date": "2026-05-15"})
    assert result.ok is True
    # 半导体 just switched initiation -> mainline
    golden = [r.industry for r in result.output.just_become_mainline]
    assert "半导体" in golden


@pytest.mark.asyncio
async def test_classifier_missing_panel_raises(tmp_path):
    agent = MainlineClassifier(memory_root=tmp_path, panel_path=str(tmp_path / "missing.parquet"))
    result = await agent.run({"asof_date": "2026-05-15"})
    assert result.ok is False
    assert "panel" in result.error.lower()


@pytest.mark.asyncio
async def test_classifier_no_asof_uses_latest(tmp_path):
    panel = _make_panel(tmp_path)
    agent = MainlineClassifier(memory_root=tmp_path, panel_path=str(panel))
    result = await agent.run({})
    assert result.ok is True
    assert result.output.as_of == "2026-05-15"


@pytest.mark.asyncio
async def test_classifier_returns_alpha_summary(tmp_path):
    panel = _make_panel(tmp_path)
    agent = MainlineClassifier(memory_root=tmp_path, panel_path=str(panel))
    result = await agent.run({"asof_date": "2026-05-15"})
    summary = result.output.alpha_summary
    assert "mainline" in summary
    assert "4.05" in summary["mainline"]   # the empirical alpha number
    assert "init_to_main_switch" in summary
