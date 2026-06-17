import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier3 import report_writer as rw


def test_render_broker_section_quotes_target_price():
    broker = {"ratings": [
        {"date": "2026-03-31", "org": "国泰海通", "rating": "增持", "report_price": 5.81, "target_price": 6.80},
        {"date": "2026-04-22", "org": "国信证券", "rating": "增持", "report_price": None, "target_price": None},
    ]}
    s = rw.render_broker_section(broker)
    assert "国泰海通" in s and "6.80" in s and "增持" in s


def test_render_broker_section_empty_is_honest():
    assert "无" in rw.render_broker_section({"ratings": []})
