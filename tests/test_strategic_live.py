from datetime import date, timedelta

import pytest
import httpx

from guanlan_v2.datafeed import strategic_live as live


@pytest.mark.parametrize("header", ["COUNTRY,NAME,ADDRESS", "Country,Name,Address"])
def test_bis_csv_accepts_observed_uppercase_uvl_without_losing_subject(header):
    raw = (header + '\nArmenia,Example Entity,"Example address"\n').encode()
    rows = live._parse_bis_csv(raw, "BIS Unverified List", "Example Entity", "")
    assert len(rows) == 1
    assert rows[0]["name"] == "Example Entity" and rows[0]["country"] == "Armenia"


@pytest.mark.parametrize("raw", [b"<html>WAF challenge</html>", b"Name,NAME\na,b\n", b"Country\nUS\n"])
def test_bad_bis_body_cannot_be_a_successful_empty_screen(raw):
    with pytest.raises(ValueError):
        live._parse_bis_csv(raw, "BIS Entity List", "not present", "")


@pytest.mark.parametrize("raw", [b"<html>WAF challenge</html>", b"", b"a,b,c,d\n"])
def test_bad_ofac_body_cannot_be_a_successful_empty_screen(raw):
    with pytest.raises(ValueError):
        live._parse_ofac_csv(raw, "not present")


def test_control_empty_optional_arrays_mean_documented_defaults(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if url == live.OFAC_SDN_URL:
            return b'123,Other Entity,-0-,PROGRAM,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'
        if "bis.gov" in url:
            return b'COUNTRY,NAME,ADDRESS\nUS,Other Entity,Somewhere\n'
        return b"<html><title>Policy page</title></html>"

    monkeypatch.setattr(live, "_control_cached_get", fetch)
    result = live.query_control_lists(query="Test Entity", jurisdictions=[], list_types=[])
    assert len(calls) == 6 and len(set(calls)) == 6
    assert result["coverage"]["sources_checked"] == 6
    assert result["coverage"]["sources_succeeded"] == 6
    assert result["coverage"]["jurisdictions_requested"] == ["cn", "us"]
    assert result["status"] == "partial_empty"  # market-access is reference only
    assert result["coverage"]["screening_complete"] is False
    assert result["coverage"]["zero_rows_means_no_risk"] is False


def test_control_failure_records_status_and_coverage_without_raw_error(monkeypatch):
    calls = []

    def denied(url):
        calls.append(url)
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("secret proxy text", request=response.request, response=response)

    monkeypatch.setattr(live, "_control_cached_get", denied)
    result = live.query_control_lists(query="Test", jurisdictions=["us"], list_types=["export_control"])
    assert len(calls) == 3 and result["status"] == "unavailable" and not result["ok"]
    assert result["coverage"]["sources_failed"] == 3
    assert all(source["http_status"] == 403 and source["error_kind"] == "access_denied"
               for source in result["sources"])
    assert "secret proxy" not in str(result)
    assert result["coverage"]["conclusion"] == "unavailable"


def test_market_access_reference_is_not_a_successful_source_check(monkeypatch):
    monkeypatch.setattr(live, "_control_cached_get", lambda url: pytest.fail("reference must not be fetched"))
    result = live.query_control_lists(jurisdictions=["cn"], list_types=["market_access"])
    assert result["status"] == "unavailable" and result["ok"] is False
    assert result["coverage"]["sources_checked"] == 0
    assert result["sources"][0]["status"] == "reference_only"


def test_control_get_does_not_follow_or_retry_access_denial(monkeypatch):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.invalid/challenge"})
    monkeypatch.setattr(live, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(live, "_cache", {})
    with pytest.raises(httpx.HTTPStatusError):
        live._control_cached_get(live.BIS_ENTITY_URL)
    assert calls == [live.BIS_ENTITY_URL]


def test_finish_zero_rows_with_mixed_source_health_is_unknown_not_empty():
    result = live._finish({
        "ok": False, "status": "unavailable", "rows": [],
        "sources": [{"status": "ok"}, {"status": "failed"}],
    })
    assert result["ok"] is True
    assert result["status"] == "partial_empty"

    healthy_empty = live._finish({
        "ok": False, "status": "unavailable", "rows": [],
        "sources": [{"status": "ok"}],
    })
    assert healthy_empty["status"] == "empty"


def _calendar_month(year: int, month: int):
    first = date(year, month, 1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    rows = []
    cursor = first
    while cursor < next_month:
        rows.append({
            "jyrq": cursor.isoformat(),
            "jybz": "0" if cursor.weekday() >= 5 else "1",
            "zrxh": str(cursor.isoweekday()),
        })
        cursor += timedelta(days=1)
    return {"data": rows}


def test_szse_calendar_requires_a_complete_month_and_binary_flags():
    payload = _calendar_month(2026, 9)
    rows = live.parse_szse_calendar_payload(payload)
    assert len(rows) == 30
    assert rows[0]["date"] == "2026-09-01"
    assert {row["trading"] for row in rows} == {True, False}

    missing = _calendar_month(2026, 9)
    missing["data"].pop()
    with pytest.raises(ValueError, match="不完整"):
        live.parse_szse_calendar_payload(missing)

    invalid = _calendar_month(2026, 9)
    invalid["data"][0]["jybz"] = "unknown"
    with pytest.raises(ValueError, match="0/1"):
        live.parse_szse_calendar_payload(invalid)


def test_cninfo_disclosure_parser_keeps_appointments_and_actual_date():
    rows = live.parse_cninfo_disclosure_payload({
        "prbookinfos": [{
            "seccode": "600007",
            "secname": "中国国贸",
            "f001d_0102": "2026-06-30",
            "f002d_0102": "2026-08-31",
            "f003d_0102": "2026-08-29",
            "f004d_0102": "",
            "f005d_0102": "",
            "f006d_0102": "",
            "orgId": "gssh0600007",
        }],
    }, "shmb")
    assert rows == [{
        "record_type": "disclosure_schedule",
        "code": "600007",
        "name": "中国国贸",
        "report_period": "2026-06-30",
        "first_scheduled_date": "2026-08-31",
        "first_change_date": "2026-08-29",
        "second_change_date": "",
        "third_change_date": "",
        "fourth_change_date": "",
        "fifth_change_date": "",
        "actual_date": "",
        "market": "shmb",
        "org_id": "gssh0600007",
        "source_updated_at": "",
        "revision_history_scope": "provider_current_record_up_to_three_changes",
    }]


def test_bse_disclosure_parser_keeps_five_changes_and_source_update():
    rows, total = live.parse_bse_disclosure_payload([{"result": {
        "totalElements": 1,
        "content": [{
            "id": 4704, "stockCode": "920059", "stockName": "双英集团",
            "reportYear": "2026", "reportType": "9503-1002",
            "reportTypeName": "半年度报告", "appointmentDate": "2026-07-31",
            "firstDate": "2026-07-27", "secondDate": "2026-07-28",
            "thirdDate": "2026-07-29", "forthDate": "2026-07-30",
            "fifthDate": "2026-08-01", "latestDate": "2026-08-01",
            "publishDate": "2026-08-01", "updateDate": "2026-09-04",
            "destFilePath": "/disclosure/example.pdf",
        }],
    }}])

    assert total == 1
    assert rows[0]["market"] == "bse"
    assert rows[0]["report_period"] == "2026-06-30"
    assert rows[0]["first_scheduled_date"] == "2026-07-31"
    assert rows[0]["fourth_change_date"] == "2026-07-30"
    assert rows[0]["fifth_change_date"] == "2026-08-01"
    assert rows[0]["source_updated_at"] == "2026-09-04"
    assert rows[0]["actual_document_url"] == \
        "https://www.bse.cn/disclosure/example.pdf"
    assert rows[0]["revision_history_scope"] == \
        "provider_current_record_up_to_five_changes"


def test_bse_report_selector_uses_bounded_official_report_types():
    assert live._bse_report_selector("2026-06-30") == ("2026", "9503-1002")
    assert live._bse_report_selector("", today=date(2026, 9, 5)) == \
        ("2026", "9503-1002")
    with pytest.raises(ValueError, match="季末"):
        live._bse_report_selector("2026-05-31")


def test_tw_openapi_parser_preserves_units_period_and_growth_fields():
    rows = live.parse_tw_revenue_openapi_rows([{
        "出表日期": "1150810",
        "資料年月": "11507",
        "公司代號": "2330",
        "公司名稱": "台積電",
        "產業別": "半導體業",
        "營業收入-當月營收": "467,580,548",
        "營業收入-上月營收": "442,014,123",
        "營業收入-去年當月營收": "323,165,707",
        "營業收入-上月比較增減(%)": "5.78",
        "營業收入-去年同月增減(%)": "44.69",
        "累計營業收入-當月累計營收": "2,872,064,238",
        "累計營業收入-去年累計營收": "2,096,211,240",
        "累計營業收入-前期比較增減(%)": "37.01",
        "備註": "",
    }], "TWSE listed", ["2330"])
    assert len(rows) == 1
    row = rows[0]
    assert row["period"] == "2026-07"
    assert row["revenue_thousand_twd"] == 467_580_548
    assert row["monthly_yoy_pct"] == 44.69
    assert row["ytd_yoy_pct"] == 37.01
    assert row["unit"] == "thousand_TWD"


def test_otc_official_csv_recovers_unavailable_openapi(monkeypatch):
    import httpx

    visited = []
    csv_body = ("\ufeff出表日期,資料年月,公司代號,公司名稱,營業收入-當月營收\n"
                "1150817,11507,6274,台燿,4879301\n").encode("utf-8")

    def respond(request):
        visited.append(str(request.url))
        if str(request.url) == live.TPEX_REVENUE_OPENAPI_O_URL:
            return httpx.Response(503)
        if str(request.url) == live.TW_REVENUE_CSV_URLS["TPEx OTC"]:
            return httpx.Response(200, content=csv_body)
        assert request.method == "GET", "CSV recovery should avoid single-company fallback"
        return httpx.Response(200, json=[])

    monkeypatch.setattr(live, "_client", lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    result = live.query_tw_monthly_revenue(["6274"])
    assert result["status"] == "partial"  # original primary outage stays visible
    assert result["rows"][0]["source_format"] == "official_csv_fallback"
    assert result["rows"][0]["period"] == "2026-07"
    assert result["rows"][0]["revenue_thousand_twd"] == 4879301
    assert result["coverage"]["missing_company_ids"] == []
    assert any(source["status"] == "http_503" for source in result["sources"])


def test_otc_csv_rejects_html_error_pages():
    with pytest.raises(ValueError, match="not CSV"):
        live.parse_tw_revenue_csv(b"<html>Unavailable</html>", "TPEx OTC", ["6274"])


def test_sina_futures_parser_never_substitutes_fetch_time_for_trade_time():
    text = (
        'var hq_str_nf_RB0="螺纹钢连续,230000,3160.000,3180.000,3159.000,0.000,'
        '3177.000,3178.000,3178.000,0.000,3151.000,6,8,1152964.000,221502,'
        '沪,螺纹钢,2026-08-28,1";'
    )
    rows = live.parse_sina_futures_text(text, ["RB0"])
    assert len(rows) == 1
    row = rows[0]
    assert row["last"] == 3178.0
    assert row["open_interest"] == 1_152_964
    assert row["volume"] == 221_502
    assert row["source_trade_date"] == "2026-08-28"
    assert row["source_trade_time"] == "23:00:00"
    assert row["freshness_label"] == "latest_available_from_source"
    assert "pulled_at" not in row


def test_policy_parsers_return_candidates_not_compliance_verdicts():
    updates = live.parse_bis_federal_register_payload({
        "results": [{
            "title": "Revisions to the Entity List",
            "publication_date": "2026-08-28",
            "document_number": "2026-12345",
            "type": "Rule",
            "html_url": "https://example.invalid/rule",
            "pdf_url": "https://example.invalid/rule.pdf",
        }],
    }, since="2026-08-01")
    assert updates[0]["source_list"] == "BIS Federal Register"
    assert updates[0]["published_at"] == "2026-08-28"
    assert "compliant" not in updates[0]

    cn = live.parse_mofcom_items(
        '<li><a href="/notice/1.html">出口管制公告</a><span>2026-08-20</span></li>',
        "https://example.invalid/list/",
        query="出口管制",
    )
    assert cn[0]["jurisdiction"] == "CN"
    assert cn[0]["url"] == "https://example.invalid/notice/1.html"


def test_invalid_inputs_fail_closed_before_network():
    calendar = live.query_trade_disclosure_calendar(kind="guess")
    assert calendar["ok"] is False and calendar["status"] == "invalid"
    revenue = live.query_tw_monthly_revenue(["../2330"])
    assert revenue["ok"] is False and revenue["status"] == "invalid"
    futures = live.query_cn_commodity_futures(["RB0,https://evil.invalid"])
    assert futures["ok"] is False and futures["status"] == "invalid"
    policy = live.query_control_lists(jurisdictions=["arbitrary"])
    assert policy["ok"] is False and policy["status"] == "invalid"


def test_trade_calendar_as_of_is_observation_time_not_future_event(monkeypatch):
    """Known future sessions/appointments are not themselves future evidence."""
    monkeypatch.setattr(
        live, "_query_trading_calendar",
        lambda start, end, out: [{"kind": "trading_day", "date": "2026-09-02"}],
    )
    monkeypatch.setattr(live, "_now_iso", lambda: "2026-08-30T01:02:03Z")

    calendar = live.query_trade_disclosure_calendar(
        kind="trading", start="2026-09-01", end="2026-09-02",
    )

    assert calendar["ok"] is True
    assert calendar["rows"][0]["date"] == "2026-09-02"
    assert calendar["as_of"] == "2026-08-30T01:02:03Z"


def test_trade_calendar_honours_limit(monkeypatch):
    monkeypatch.setattr(
        live, "_query_trading_calendar",
        lambda start, end, out: [
            {"kind": "trading_day", "date": "2026-09-01"},
            {"kind": "trading_day", "date": "2026-09-02"},
        ],
    )

    calendar = live.query_trade_disclosure_calendar(
        kind="trading", start="2026-09-01", end="2026-09-02", limit=1,
    )

    assert calendar["ok"] is True
    assert [row["date"] for row in calendar["rows"]] == ["2026-09-01"]


def test_disclosure_calendar_exposes_bse_and_history_boundary(monkeypatch):
    observed = {}

    def fake_disclosures(market, stock_code, report_period, limit, out):
        observed.update({
            "market": market, "stock_code": stock_code,
            "report_period": report_period, "limit": limit,
        })
        out["sources"].append({"status": "ok", "authority": "北京证券交易所"})
        return [{"record_type": "disclosure_schedule", "market": "bse", "code": stock_code}]

    monkeypatch.setattr(live, "_query_disclosures", fake_disclosures)
    calendar = live.query_trade_disclosure_calendar(
        kind="disclosure", market="bse", stock_code="BJ920059",
        report_period="2026-06-30", limit=5,
    )

    assert calendar["ok"] is True
    assert observed == {
        "market": "bse", "stock_code": "920059",
        "report_period": "2026-06-30", "limit": 5,
    }
    assert calendar["coverage"]["disclosure_schedule"]["authorities"] == ["BSE"]
    assert calendar["coverage"]["disclosure_schedule"]["historical_observation_archive"] is False
    assert "disclosure_revision_observation_history_unavailable" in calendar["reason_codes"]


def test_newsradar_live_adapter_preserves_warming_and_stale_state(monkeypatch):
    from guanlan_v2.datafeed import newsradar

    monkeypatch.setattr(
        newsradar, "load_sources",
        lambda: ([{"key": "ai"}, {"key": "semi"}], [{"name": "fixture"}]),
    )
    monkeypatch.setattr(
        newsradar, "read_radar_swr",
        lambda sectors, days, limit, **kwargs: {
            "warming": False,
            "stale": True,
            "pulled_at": "2026-08-30T01:02:03Z",
            "source_quality": "complete",
            "source_status": [{
                "source": "Fixture RSS", "sector": "ai", "url": "https://example.invalid/feed",
                "status": "ok", "items_parsed": 1,
            }],
            "items": [{
                "title": "model release", "url": "https://example.invalid/a",
                "source": "Fixture RSS", "sector": "ai", "ts": 1788051600,
            }],
        },
    )
    result = live.query_newsradar_live(["ai"], days=7, limit=10)
    assert result["dataset_id"] == "research.newsradar.live"
    assert result["status"] == "stale"
    assert result["ok"] is True
    assert result["rows"][0]["title"] == "model release"
    assert any("外部不可信" in warning for warning in result["warnings"])

    invalid = live.query_newsradar_live(["unknown"])
    assert invalid["status"] == "invalid"


def test_newsradar_live_can_use_cache_without_triggering_refresh(monkeypatch):
    from guanlan_v2.datafeed import newsradar

    monkeypatch.setattr(
        newsradar, "load_sources",
        lambda: ([{"key": "ai"}], [{"name": "fixture", "hint": "ai"}]),
    )
    observed = {}

    def fake_swr(*args, **kwargs):
        observed.update(kwargs)
        return {
            "warming": False, "stale": True, "items": [],
            "source_quality": "unknown", "sources_tried": 0,
            "pulled_at": "2026-08-31T00:00:00Z",
        }

    monkeypatch.setattr(newsradar, "read_radar_swr", fake_swr)
    live.query_newsradar_live(["ai"], allow_refresh=False)
    assert observed["allow_refresh"] is False


def test_newsradar_live_healthy_empty_is_not_a_source_failure(monkeypatch):
    from guanlan_v2.datafeed import newsradar

    monkeypatch.setattr(
        newsradar, "load_sources",
        lambda: ([{"key": "ai"}], [{"name": "fixture", "hint": "ai"}]),
    )
    monkeypatch.setattr(newsradar, "read_radar_swr", lambda *args, **kwargs: {
        "warming": False, "stale": False, "pulled_at": "2026-08-30T01:00:00",
        "retrieved_at": "2026-08-30T01:00:01Z",
        "refresh_attempted_at": "2026-08-30T01:00:01Z",
        "items": [], "source_quality": "complete", "sources_tried": 1,
        "sources_ok": 1, "sources_with_items": 0, "sources_empty": 1,
        "sources_failed": 0,
        "source_status": [{"source": "Fixture", "sector": "ai", "status": "empty",
                           "items_parsed": 0, "last_success_at": "2026-08-30T01:00:01Z"}],
    })
    result = live.query_newsradar_live(["ai"], days=7, limit=10)
    assert result["ok"] is True and result["status"] == "empty"
    assert result["retrieved_at"] == "2026-08-30T01:00:01Z"
    assert result["refresh_attempted_at"] == "2026-08-30T01:00:01Z"
    assert result["sources"][0]["status"] == "empty"
