import json
from datetime import date

import httpx
import pytest

from guanlan_v2.datafeed import official_futures_live as official
from guanlan_v2.datafeed import strategic_live as live


def _shfe_payload(day="07", *, settled=True):
    return {
        "o_code": "0000", "o_year": "2026", "o_month": "09", "o_day": day,
        "o_year_num": "165" if settled else "", "o_trade_day": "8237" if settled else "",
        "update_date": f"202609{day} 15:33:02",
        "o_curinstrument": [
            {"PRODUCTGROUPID": "rb", "PRODUCTNAME": "螺纹钢", "DELIVERYMONTH": "2610",
             "CLOSEPRICE": "3158", "SETTLEMENTPRICE": "3150", "PRESETTLEMENTPRICE": 3120,
             "VOLUME": "300000", "OPENINTEREST": "400000", "OPENINTERESTCHG": -50},
            {"PRODUCTGROUPID": "rb", "PRODUCTNAME": "螺纹钢", "DELIVERYMONTH": "2701",
             "CLOSEPRICE": "3188", "SETTLEMENTPRICE": "3180", "PRESETTLEMENTPRICE": 3150,
             "VOLUME": "200000", "OPENINTEREST": "900000", "OPENINTERESTCHG": 100},
            {"PRODUCTGROUPID": "rb", "DELIVERYMONTH": "小计", "VOLUME": 500000},
        ],
    }


def test_shfe_daily_rejects_wrong_day_and_unsettled_file():
    rows = official.parse_shfe_daily(_shfe_payload(), "SHFE", date(2026, 9, 7))
    assert len(rows) == 2
    assert rows[0]["volume"] == 300000
    assert rows[0]["open_interest"] == 400000
    assert rows[0]["settlement"] == 3150
    assert rows[0]["freshness_label"] == "official_daily_not_realtime"
    assert "last" not in rows[0]
    with pytest.raises(ValueError, match="date"):
        official.parse_shfe_daily(_shfe_payload(), "SHFE", date(2026, 9, 8))
    with pytest.raises(official.UnsettledDailyReport):
        official.parse_shfe_daily(_shfe_payload(settled=False), "SHFE", date(2026, 9, 7))


def test_official_daily_skips_unsettled_and_resolves_continuous_honestly(monkeypatch):
    visited = []

    def fake_fetch(url):
        visited.append(url)
        payload = _shfe_payload("08", settled=False) if "20260908" in url else _shfe_payload()
        return json.dumps(payload).encode()

    monkeypatch.setattr(official, "_fetch_bytes", fake_fetch)
    result = official.query_official_futures_daily(["RB0", "RB2610", "M0"], today=date(2026, 9, 8))
    assert len(visited) == 2
    assert result["rows"][0]["symbol"] == "RB0"
    assert result["rows"][0]["actual_contract"] == "RB2701"
    assert result["rows"][0]["contract_selection"] == "largest_daily_open_interest_not_vendor_continuous"
    assert result["rows"][1]["actual_contract"] == "RB2610"
    assert result["attempts"][0]["status"] == "not_yet_settled"
    assert result["coverage"]["M0"]["status"] == "not_connected"
    assert result["rows"][0]["source_trade_date"] == "2026-09-07"


def test_official_denial_is_preserved_without_date_retries(monkeypatch):
    calls = []

    def fail(url):
        calls.append(url)
        response = httpx.Response(403, request=httpx.Request("GET", url))
        response.raise_for_status()

    monkeypatch.setattr(official, "_fetch_bytes", fail)
    result = official.query_official_futures_daily(["RB0"], today=date(2026, 9, 8))
    assert len(calls) == 1
    assert result["sources"][0]["status"] == "http_403"
    assert result["coverage"]["RB0"]["status"] == "unavailable"
    assert result["rows"] == []


def test_czce_daily_parser_keeps_report_date_settlement_and_oi():
    text = (
        "郑州商品交易所期货每日行情表(2026-09-07)\n"
        "合约代码|昨结算|今开盘|最高价|最低价|今收盘|今结算|涨跌1|涨跌2|成交量(手)|持仓量|增减量|成交额(万元)|交割结算价\n"
        "TA701|5,958|5888|5974|5844|5924|5922|-34|-36|1,038,686|1,075,843|11,597|12|\n"
        "总计|||||||||999999||||\n"
    )
    rows = official.parse_czce_daily(text, date(2026, 9, 7))
    assert len(rows) == 1
    assert rows[0]["actual_contract"] == "TA701"
    assert rows[0]["settlement"] == 5922
    assert rows[0]["volume"] == 1038686
    assert rows[0]["open_interest"] == 1075843
    with pytest.raises(ValueError, match="title/date"):
        official.parse_czce_daily(text, date(2026, 9, 8))


def _official_result():
    row = official.parse_shfe_daily(_shfe_payload(), "SHFE", date(2026, 9, 7))[0]
    row.update({"symbol": "RB0", "requested_symbol": "RB0"})
    return {"rows": [row], "sources": [{"status": "ok", "authority": "SHFE"}],
            "coverage": {"RB0": {"status": "ok"}}, "attempts": []}


def test_vendor_quote_failure_returns_explicit_daily_fallback(monkeypatch):
    monkeypatch.setattr(official, "query_official_futures_daily", lambda symbols: _official_result())
    monkeypatch.setattr(live, "_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503)
    )))
    result = live.query_cn_commodity_futures(["RB0"])
    assert result["status"] == "partial"
    assert result["rows"][0]["fallback_reason"] == "vendor_quote_unavailable"
    assert result["rows"][0]["record_type"] == "cn_commodity_futures_daily"
    assert result["rows"][0]["freshness_label"] == "official_daily_not_realtime"
    assert result["as_of"] == "2026-09-07"


def test_quote_and_daily_remain_different_observations(monkeypatch):
    monkeypatch.setattr(official, "query_official_futures_daily", lambda symbols: _official_result())
    quote = 'var hq_str_nf_RB0="螺纹钢连续,091400,3152,3165,3145,0,3158,3159,3158,0,3158,2,3,1488342,268906,沪,螺纹钢,2026-09-08,1";'
    monkeypatch.setattr(live, "_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=quote.encode("gb18030"))
    )))
    result = live.query_cn_commodity_futures(["RB0"])
    row = result["rows"][0]
    assert row["source_is_official"] is False
    assert row["source_trade_date"] == "2026-09-08"
    assert row["official_daily"]["source_trade_date"] == "2026-09-07"
    assert row["official_daily"]["settlement"] == 3150
    assert row["last"] == 3158
    assert row["volume"] == 268906
    assert row["open_interest"] == 1488342


def test_official_symbols_fail_before_network(monkeypatch):
    monkeypatch.setattr(official, "_fetch_bytes", lambda _: pytest.fail("unexpected network"))
    with pytest.raises(ValueError, match="invalid"):
        official.query_official_futures_daily(["RB0/../../foo"])


def test_czce_four_digit_year_does_not_alias_a_different_decade(monkeypatch):
    row = official._row("CZCE", "TA701", "TA", "2026-09-07")
    row.update({"volume": 10, "open_interest": 20})
    monkeypatch.setattr(official, "_latest_exchange", lambda exchange, today: {
        "rows": [row], "sources": [], "attempts": [], "status": "ok",
    })
    result = official.query_official_futures_daily(["TA2701", "TA1701", "TA3701"], today=date(2026, 9, 8))
    assert [row["symbol"] for row in result["rows"]] == ["TA2701"]
    assert result["coverage"]["TA1701"]["status"] == "not_returned"
    assert result["coverage"]["TA3701"]["status"] == "not_returned"


def test_empty_vendor_quote_is_partial_even_with_official_daily(monkeypatch):
    monkeypatch.setattr(official, "query_official_futures_daily", lambda symbols: _official_result())
    monkeypatch.setattr(live, "_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text='var hq_str_nf_RB0="";')
    )))
    result = live.query_cn_commodity_futures(["RB0"])
    assert result["status"] == "partial"
    assert result["rows"][0]["freshness_label"] == "official_daily_not_realtime"


def test_missing_oi_does_not_invent_a_continuous_contract(monkeypatch):
    row = official._row("SHFE", "RB2701", "RB", "2026-09-07")
    row.update({"volume": 100, "open_interest": None})
    monkeypatch.setattr(official, "_latest_exchange", lambda exchange, today: {
        "rows": [row], "sources": [], "attempts": [], "status": "ok",
    })
    result = official.query_official_futures_daily(["RB0", "RB2701"], today=date(2026, 9, 8))
    assert [row["symbol"] for row in result["rows"]] == ["RB2701"]
    assert result["coverage"]["RB0"]["status"] == "not_returned"


def test_gfex_uses_bounded_public_query_and_retains_http_transport(monkeypatch):
    visited = []

    def respond(url, *, method, data):
        visited.append((url, method, data))
        payload = {"code": "0", "param": {k: [v] for k, v in data.items()}, "data": [
            {"varietyOrder": "lc", "delivMonth": "2611", "variety": "碳酸锂",
             "volumn": 8123, "openInterest": 13045, "clearPrice": 142200, "close": 143000},
            {"varietyOrder": "", "delivMonth": "", "variety": "总计", "volumn": 8123},
        ]}
        return json.dumps(payload).encode()

    monkeypatch.setattr(official, "_request_bytes", respond)
    result = official.query_official_futures_daily(["LC0"], today=date(2026, 9, 7))
    assert visited == [(official.GFEX_DAILY_URL, "POST", {"trade_date": "20260907", "trade_type": "0", "variety": ""})]
    assert result["rows"][0]["volume"] == 8123
    assert result["rows"][0]["open_interest"] == 13045
    assert result["rows"][0]["settlement"] == 142200
    assert result["rows"][0]["transport_security"] == "unencrypted_http"
    assert result["sources"][0]["query"]["trade_date"] == "20260907"


def test_gfex_empty_aggregate_is_not_a_successful_contract_day():
    payload = {"code": "0", "param": {"trade_date": ["20260908"], "trade_type": ["0"]},
               "data": [{"varietyOrder": "", "delivMonth": "", "variety": "总计", "volumn": 0}]}
    with pytest.raises(official.UnsettledDailyReport):
        official.parse_gfex_daily(payload, date(2026, 9, 8))
    with pytest.raises(ValueError, match="requested date/type"):
        official.parse_gfex_daily(payload, date(2026, 9, 7))
