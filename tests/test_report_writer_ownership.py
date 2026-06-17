import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier3 import report_writer as rw


def test_render_ownership_section():
    facts = {"holders": {"report_date": "2026-03-31",
                         "controlling_holder": "铜陵有色金属集团控股有限公司(45.58%)",
                         "a_share_holders": 866667.0,
                         "top_holders": [{"name": "铜陵有色金属集团控股有限公司", "pct": 34.51}]},
             "main_capital": {"report_period": "2025-12-31", "inst_holding_pct": 13.29}}
    s = rw.render_ownership_section(facts)
    assert "铜陵有色金属集团" in s and "45.58" in s and "13.29" in s and "86" in s


def test_render_ownership_empty():
    assert "无" in rw.render_ownership_section({"holders": None, "main_capital": None})
