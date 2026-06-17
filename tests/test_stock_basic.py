# tests/test_stock_basic.py
import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.data import stock_basic as sb


def test_get_basic_real():
    b = sb.get_basic("SZ000630")           # 也接受 000630 / 000630.SZ
    assert b["name"] == "铜陵有色" and b["industry"] == "铜" and b["area"] == "安徽"
    assert b["market"] == "主板" and b["list_date"] == "19961120"


def test_get_basic_missing_is_none():
    assert sb.get_basic("SZ999999") is None
