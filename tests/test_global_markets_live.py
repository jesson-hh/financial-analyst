import pytest

from guanlan_v2.datafeed import global_markets_live as live


@pytest.fixture(autouse=True)
def _reset_process_http_cache():
    # The cache deliberately remains a normal clearable mapping for tests and
    # process diagnostics.
    live._cache.clear()
    yield
    live._cache.clear()


class _FakeResponse:
    def __init__(self, url: str, body: bytes):
        self.url = url
        self.content = body
        self.headers = {"last-modified": "Sun, 30 Aug 2026 00:00:00 GMT"}

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, payloads: dict[str, bytes], calls: list[str]):
        self._payloads = payloads
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, url: str, *, headers: dict[str, str]):
        self._calls.append(url)
        return _FakeResponse(url, self._payloads[url])


def test_sec_submission_parser_keeps_accession_dates_and_evidence_urls():
    payload = {
        "cik": "320193",
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "filings": {"recent": {
            "accessionNumber": ["0000320193-26-000020", "0000320193-26-000018"],
            "filingDate": ["2026-07-31", "2026-07-30"],
            "reportDate": ["2026-06-27", "2026-06-27"],
            "acceptanceDateTime": ["20260731163000", "20260730160000"],
            "form": ["10-Q", "8-K"],
            "act": ["34", "34"],
            "fileNumber": ["001-36743", "001-36743"],
            "filmNumber": ["261234", "261233"],
            "isXBRL": [1, 1],
            "isInlineXBRL": [1, 1],
            "primaryDocument": ["aapl-20260627.htm", "aapl-20260730.htm"],
            "primaryDocDescription": ["10-Q", "8-K"],
        }},
    }
    rows = live.parse_sec_submissions(payload, forms=["10-Q"], limit=10)
    assert len(rows) == 1
    assert rows[0]["cik"] == "0000320193"
    assert rows[0]["report_date"] == "2026-06-27"
    assert rows[0]["accession_number"] == "0000320193-26-000020"
    assert rows[0]["filing_document_url"].endswith("/aapl-20260627.htm")
    assert rows[0]["evidence_label"] == "filing_source_reported"


def test_sec_companyfacts_keeps_units_periods_and_restatements_separate():
    payload = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": {"Assets": {
            "label": "Assets",
            "description": "Total assets.",
            "units": {"USD": [
                {"end": "2026-06-27", "val": 383266000000, "accn": "a", "fy": 2026,
                 "fp": "Q3", "form": "10-Q", "filed": "2026-07-31", "frame": "CY2026Q2I"},
                {"end": "2026-06-27", "val": 382000000000, "accn": "b", "fy": 2026,
                 "fp": "Q3", "form": "10-Q/A", "filed": "2026-08-02", "frame": ""},
            ]},
        }}},
    }
    rows = live.parse_sec_companyfacts(
        payload, concepts=["Assets"], unit="USD", limit=10,
    )
    assert [row["accession_number"] for row in rows] == ["b", "a"]
    assert rows[0]["unit"] == "USD"
    assert rows[0]["end"] == "2026-06-27"
    assert all(row["evidence_label"] == "fact_source_reported" for row in rows)


def test_finra_parser_preserves_fractional_shares_and_short_exempt_subset():
    raw = (
        "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
        "20260828|AAPL|7285857.056715|25670|13438049.945378|B,Q,N\n"
        "20260828|MSFT|1|0|10|Q\n"
    ).encode()
    rows = live.parse_finra_short_volume(raw, ["AAPL"])
    assert len(rows) == 1
    assert rows[0]["short_volume"] == 7285857.056715
    assert rows[0]["short_exempt_volume"] == 25670
    assert rows[0]["facilities"] == ["B", "Q", "N"]


def test_treasury_csv_parser_is_long_form_and_does_not_depend_on_column_order():
    text = (
        'Date,"10 Yr","2 Yr","1.5 Month"\n'
        '08/28/2026,4.73,4.34,3.83\n'
    )
    rows = live.parse_treasury_csv(text, "nominal_curve")
    by_tenor = {row["tenor"]: row for row in rows}
    assert by_tenor["10y"]["rate_pct"] == 4.73
    assert by_tenor["2y"]["tenor_months"] == 24
    assert by_tenor["1.5m"]["observation_date"] == "2026-08-28"
    assert all(row["rate_basis"] == "par_yield" for row in rows)


def test_cftc_normalizer_keeps_weekly_date_categories_and_stable_composite_key():
    raw = [{
        "id": "260825043602F",
        "market_and_exchange_names": "10-YEAR U.S. TREASURY NOTES - CBT",
        "report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000",
        "contract_market_name": "10-YEAR U.S. TREASURY NOTES",
        "cftc_contract_market_code": "043602",
        "cftc_commodity_code": "043",
        "commodity_name": "U.S. TREASURY NOTES",
        "open_interest_all": "6208739",
        "change_in_open_interest_all": "612000",
        "dealer_positions_long_all": "163799",
        "dealer_positions_short_all": "744106",
        "dealer_positions_spread_all": "100",
        "asset_mgr_positions_long": "3282567",
        "asset_mgr_positions_short": "690495",
        "asset_mgr_positions_spread": "500",
    }]
    rows = live.normalize_cftc_rows(
        raw, report="tff", scope="futures_only", dataset_id="gpe5-46if",
    )
    row = rows[0]
    assert row["report_date"] == "2026-08-25"
    assert row["categories"]["dealer"]["net_outright"] == 163799 - 744106
    assert row["categories"]["dealer"]["spreading"] == 100
    assert row["stable_key"] == "gpe5-46if:2026-08-25:043602:futures_only"
    assert row["frequency"] == "weekly"


def test_cftc_normalizer_handles_disaggregated_typo_and_cit_mixed_case_fields():
    disaggregated = live.normalize_cftc_rows([{
        "report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000",
        "cftc_contract_market_code": "067651",
        "swap_positions_long_all": "111284",
        "swap__positions_short_all": "321000",
        "swap__positions_spread_all": "456",
    }], report="disaggregated", scope="futures_only", dataset_id="72hh-3qpy")[0]
    assert disaggregated["categories"]["swap_dealer"] == {
        "long": 111284, "short": 321000, "spreading": 456,
        "net_outright": 111284 - 321000,
    }

    cit = live.normalize_cftc_rows([{
        "report_date_as_yyyy_mm_dd": "2026-08-25T00:00:00.000",
        "cftc_contract_market_code": "083731",
        "NComm_Postions_Long_All_NoCIT": "100",
        "NComm_Postions_Short_All_NoCIT": "50",
        "NComm_Postions_Spread_All_NoCIT": "20",
        "comm_positions_long_all_nocit": "40",
        "Comm_Positions_Short_All_NoCIT": "70",
        "cit_positions_long_all": "30",
        "cit_positions_short_all": "10",
        "change_open_interest_all": "12",
    }], report="cit", scope="combined", dataset_id="4zgm-a668")[0]
    assert cit["categories"]["noncommercial"]["net_outright"] == 50
    assert cit["categories"]["commercial"]["short"] == 70
    assert cit["categories"]["index_traders"]["net_outright"] == 20
    assert cit["change_in_open_interest"] == 12


def test_nasdaq_parser_never_upgrades_provider_estimate_to_confirmed():
    payload = {"data": {"rows": [{
        "symbol": "PANW",
        "name": "Palo Alto Networks, Inc.",
        "time": "time-after-hours",
        "marketCap": "$312,022,761,000",
        "fiscalQuarterEnding": "Jul/2026",
        "epsForecast": "$0.51",
        "noOfEsts": "14",
        "lastYearRptDt": "8/18/2025",
        "lastYearEPS": "N/A",
    }]}}
    row = live.parse_nasdaq_earnings(payload, "2026-09-01")[0]
    assert row["session"] == "after_hours"
    assert row["market_cap_usd"] == 312022761000
    assert row["last_year_eps"] is None
    assert row["confirmation_status"] == "expected"
    assert row["date_confidence"] == "provider_algorithmic_estimate"


def test_invalid_filters_fail_closed_before_any_external_request():
    sec = live.query_sec_filings_xbrl(view="frames", concepts=["Assets"], unit="USD", frame="../../")
    assert sec["status"] == "invalid"

    finra = live.query_finra_short_volume(symbols=["AAPL|https://evil.invalid"])
    assert finra["status"] == "invalid"

    cftc = live.query_cftc_cot(contract_codes=["043602') OR 1=1 --"])
    assert cftc["status"] == "invalid"

    earnings = live.query_overseas_earnings_calendar(
        start="2026-08-01", end="2026-09-01",
    )
    assert earnings["status"] == "invalid"


def test_nasdaq_http_200_with_failed_business_status_is_not_fake_success(monkeypatch):
    body = b'{"data":null,"status":{"rCode":400,"developerMessage":"bad date"}}'

    def fake_get(url, **kwargs):
        return body, url, {}

    monkeypatch.setattr(live, "_cached_get", fake_get)
    result = live.query_overseas_earnings_calendar(
        start="2026-09-01", end="2026-09-01", symbols=["PANW"],
    )
    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["sources"][0]["status"] == "error"


def test_nasdaq_calendar_anchors_asof_to_observation_not_future_event(monkeypatch):
    body = (
        b'{"data":{"rows":[{"symbol":"PANW","name":"Palo Alto Networks",'
        b'"time":"time-after-hours"}]},"status":{"rCode":200}}'
    )
    monkeypatch.setattr(live, "_now_iso", lambda: "2026-09-05T01:02:03Z")
    monkeypatch.setattr(
        live, "_cached_get",
        lambda url, **kwargs: (body, url, {"last-modified": "Sat, 05 Sep 2026 01:00:00 GMT"}),
    )

    result = live.query_overseas_earnings_calendar(
        start="2026-09-10", end="2026-09-10", symbols=["PANW"],
    )

    assert result["ok"] is True
    assert result["rows"][0]["scheduled_date"] == "2026-09-10"
    assert result["calendar_event_max_date"] == "2026-09-10"
    assert result["as_of"] == "2026-09-05T01:02:03Z"
    assert result["provenance"] == {
        "retrieved_at": "2026-09-05T01:02:03Z",
        "source_as_of": "2026-09-05T01:02:03Z",
        "observation_basis": "retrieval_time_of_provider_estimate",
        "source_digests_present": True,
    }
    assert result["pit"]["status"] == "current_observation_only"
    assert result["pit"]["immutable_archive_receipt"] is False
    assert result["pit"]["reason_code"] == "unified_pit_receipt_unavailable"
    assert result["identity_scope"] == {
        "scope": "explicit_calendar_symbols",
        "workspace_subject_bound": False,
        "issuer_mapping": "explicit_symbols_only",
        "contract_mapping": "not_applicable",
        "reason_code": "workspace_issuer_mapping_unreviewed",
    }


def test_cftc_contract_code_remains_explicit_unbound_mapping(monkeypatch):
    body = (
        b'[{"report_date_as_yyyy_mm_dd":"2026-09-01T00:00:00.000",'
        b'"cftc_contract_market_code":"043602",'
        b'"contract_market_name":"10-YEAR U.S. TREASURY NOTES"}]'
    )
    monkeypatch.setattr(live, "_cached_get", lambda url, **kwargs: (body, url, {}))

    result = live.query_cftc_cot(contract_codes=["043602"])

    assert result["ok"] is True
    assert result["identity_scope"] == {
        "scope": "explicit_cftc_contract_filter",
        "workspace_subject_bound": False,
        "issuer_mapping": "not_applicable",
        "contract_mapping": "explicit_official_contract_codes",
        "reason_code": "workspace_contract_mapping_unreviewed",
    }
    assert result["provenance"]["observation_basis"] == "cftc_weekly_report_date"
    assert result["pit"]["source_as_of"] == "2026-09-01"
    assert result["pit"]["reason_code"] == "unified_pit_receipt_unavailable"


def test_http_cache_expires_entries_and_refetches(monkeypatch):
    clock = [100.0]
    calls: list[str] = []
    url = "https://authority.invalid/daily"
    payloads = {url: b"daily-data"}

    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live, "_client", lambda: _FakeClient(payloads, calls))

    assert live._cached_get(url, ttl=10)[0] == b"daily-data"
    clock[0] = 109.0
    assert live._cached_get(url, ttl=10)[0] == b"daily-data"
    assert calls == [url]

    # The exact expiration boundary is stale and is removed before refetch.
    clock[0] = 110.0
    assert live._cached_get(url, ttl=10)[0] == b"daily-data"
    assert calls == [url, url]
    assert len(live._cache) == 1


def test_http_cache_enforces_lru_entry_and_total_body_limits(monkeypatch):
    clock = [200.0]
    calls: list[str] = []
    urls = {name: f"https://authority.invalid/{name}" for name in "abc"}
    payloads = {urls["a"]: b"aaa", urls["b"]: b"bbb", urls["c"]: b"ccc"}

    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live, "_client", lambda: _FakeClient(payloads, calls))
    monkeypatch.setattr(live, "_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(live, "_CACHE_MAX_BODY_BYTES", 10)
    monkeypatch.setattr(live, "_CACHE_MAX_TOTAL_BODY_BYTES", 6)

    live._cached_get(urls["a"], ttl=100)
    clock[0] += 1
    live._cached_get(urls["b"], ttl=100)
    clock[0] += 1
    live._cached_get(urls["a"], ttl=100)  # promote a; b is now LRU
    clock[0] += 1
    live._cached_get(urls["c"], ttl=100)  # entry cap evicts b
    assert len(live._cache) == 2

    clock[0] += 1
    live._cached_get(urls["b"], ttl=100)
    assert calls.count(urls["a"]) == 1
    assert calls.count(urls["b"]) == 2
    assert sum(len(entry.body) for entry in live._cache.values()) <= 6


def test_http_cache_never_retains_oversized_response(monkeypatch):
    clock = [300.0]
    calls: list[str] = []
    url = "https://authority.invalid/large"
    payloads = {url: b"12345"}

    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live, "_client", lambda: _FakeClient(payloads, calls))
    monkeypatch.setattr(live, "_CACHE_MAX_BODY_BYTES", 4)

    assert live._cached_get(url, ttl=100)[0] == b"12345"
    clock[0] += 1
    assert live._cached_get(url, ttl=100)[0] == b"12345"
    assert calls == [url, url]
    assert not live._cache
