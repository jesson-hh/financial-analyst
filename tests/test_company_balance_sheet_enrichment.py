from __future__ import annotations

import sys
from http.client import RemoteDisconnected
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from guanlan_v2.console import workspace_tools as wt


PERIODS = [
    "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30",
    "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30",
    "2024-06-30", "2024-03-31", "2023-12-31", "2023-09-30",
]


class Response:
    text = '<input id="hidctype" value="4" />'

    def __init__(self, data=None, *, status_code=200, text=None):
        self.data = data
        self.status_code = status_code
        if text is not None:
            self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"private response {self.status_code}", response=self)
        return None

    def json(self):
        return {"data": self.data}


def _statement(period, **overrides):
    return {
        "REPORT_DATE": period + " 00:00:00", "SECURITY_CODE": "600667",
        "SECUCODE": "600667.SH", "MONETARYFUNDS": 12_000,
        "SHARE_CAPITAL": 2_000, "TOTAL_PARENT_EQUITY": 50_000,
        "SHORT_LOAN": 500, "LONG_LOAN": 1_000, "BOND_PAYABLE": 0,
        "SHORT_BOND_PAYABLE": 0, "LEASE_LIAB": 100, **overrides,
    }


def _mock_get(monkeypatch, responder=None, index_responder=None):
    calls = []

    def get(url, *, params, timeout, allow_redirects):
        calls.append((url, dict(params), timeout))
        assert 0 < timeout[0] <= 5 and 0 < timeout[1] <= 15
        assert allow_redirects is False
        if url.endswith("/Index"):
            assert params == {"type": "web", "code": "sh600667"}
            return index_responder() if index_responder else Response()
        assert url.endswith("/zcfzbAjaxNew")
        assert params["code"] == "SH600667"
        batch = params["dates"].split(",")
        return responder(batch) if responder else Response([_statement(day) for day in batch])

    monkeypatch.setattr(requests, "get", get)
    return calls


def test_only_selected_twelve_periods_are_requested_with_bounded_timeouts(monkeypatch):
    calls = _mock_get(monkeypatch)
    balances, warnings = wt._company_balance_sheets_for_periods(
        "SH600667", PERIODS + ["2003-12-31", "2002-12-31"],
    )
    assert list(balances) == PERIODS
    assert warnings == []
    batches = [call[1]["dates"].split(",") for call in calls[1:]]
    assert [len(batch) for batch in batches] == [5, 5, 2]
    assert [day for batch in batches for day in batch] == PERIODS
    assert len(calls) == 4
    assert all(call[2] == (5, 15) for call in calls)


@pytest.mark.parametrize("failed_offset", [0, 5])
def test_failed_batch_preserves_other_periods_and_never_backfills_latest(monkeypatch, failed_offset):
    def responder(batch):
        if batch[0] == PERIODS[failed_offset]:
            raise requests.ConnectTimeout("simulated batch timeout")
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert set(balances) == set(PERIODS) - set(PERIODS[failed_offset:failed_offset + 5])
    assert (PERIODS[0] in balances) is (failed_offset != 0)
    assert len([warning for warning in warnings if "batch_failed" in warning]) == 1
    assert any("ConnectTimeout" in warning for warning in warnings)
    assert len(calls) == 5
    assert sum("transient_retry" in warning for warning in warnings) == 1
    assert sum("retry_failed" in warning for warning in warnings) == 1


def test_unrequested_period_wrong_company_and_conflicts_cannot_fill_a_balance(monkeypatch):
    def responder(batch):
        return Response([
            _statement("2003-12-31"),
            _statement(batch[0], SECURITY_CODE="600519"),
            _statement(batch[1], SECUCODE="600667.SZ"),
            _statement(batch[2]), _statement(batch[2], MONETARYFUNDS=999_000),
        ])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS[:3])
    assert balances == {}
    assert any("unrequested_period" in warning for warning in warnings)
    assert any("symbol_mismatch" in warning for warning in warnings)
    assert any("conflicting_period" in warning for warning in warnings)
    assert len([warning for warning in warnings if "period_missing" in warning]) == 3
    assert len(calls) == 2
    assert not any("retry" in warning for warning in warnings)


@pytest.mark.parametrize("error", [
    requests.ConnectTimeout("private connect failure"),
    requests.ReadTimeout("private read failure"),
    requests.ConnectionError(ConnectionResetError("private reset")),
    requests.ConnectionError(RemoteDisconnected("private disconnect")),
])
def test_latest_batch_recovers_once_with_bounded_retry_and_source_warning(monkeypatch, error):
    attempts = 0

    def responder(batch):
        nonlocal attempts
        if batch[0] == PERIODS[0]:
            attempts += 1
            if attempts == 1:
                raise error
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert list(balances) == PERIODS
    assert len(calls) == 5 and attempts == 2
    assert calls[2][2] == (3, 7)
    recovered = next(w for w in warnings if "retry_recovered" in w)
    assert "eastmoney.zcfzbAjaxNew:SH600667:2026-06-30" in recovered
    assert f"cause={type(error).__name__}" in recovered
    assert "attempts=2" in recovered and "accepted_periods=5/5" in recovered
    assert not any("private" in w or "batch_failed" in w for w in warnings)


@pytest.mark.parametrize("error", [
    requests.ConnectionError("reset / WAF text is not transport proof"),
    requests.exceptions.ProxyError(ConnectionResetError("private proxy")),
    requests.exceptions.SSLError(ConnectionResetError("private certificate")),
    requests.ConnectionError(requests.exceptions.ProxyError(ConnectionResetError("nested proxy"))),
    requests.ConnectionError(requests.exceptions.SSLError(ConnectionResetError("nested certificate"))),
    requests.Timeout("unknown timeout class"),
])
def test_unproven_or_policy_errors_are_not_retried(monkeypatch, error):
    def responder(batch):
        if batch[0] == PERIODS[0]:
            raise error
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert len(calls) == 4
    assert set(balances) == set(PERIODS[5:])
    assert not any("retry" in w or "private" in w for w in warnings)


@pytest.mark.parametrize("status_code", [301, 302, 307, 401, 403, 429, 500, 503])
def test_http_denial_or_redirect_is_not_retried_and_successful_batches_survive(monkeypatch, status_code):
    def responder(batch):
        if batch[0] == PERIODS[0]:
            return Response(status_code=status_code)
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert len(calls) == 4
    assert set(balances) == set(PERIODS[5:])
    assert any("HTTPError" in w and "batch_failed" in w for w in warnings)
    assert any(f"status={status_code}" in w for w in warnings)
    if status_code < 400:
        assert any("reason=redirect_refused" in w for w in warnings)
    assert not any("retry" in w or "private response" in w for w in warnings)


def test_business_empty_is_a_gap_not_a_transport_retry(monkeypatch):
    calls = _mock_get(monkeypatch, lambda batch: Response([]))
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert balances == {} and len(calls) == 4
    assert sum("period_missing" in w for w in warnings) == 12
    assert not any("retry" in w for w in warnings)


def test_waf_page_without_company_type_is_not_retried(monkeypatch):
    calls = _mock_get(monkeypatch, index_responder=lambda: Response(text="<html>WAF challenge</html>"))
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert balances == {} and len(calls) == 1
    assert len(warnings) == 1 and "enrichment_failed: RuntimeError" in warnings[0]
    assert "reason=company_type_missing" in warnings[0]


def test_waf_batch_non_json_is_not_retried(monkeypatch):
    class WafResponse(Response):
        def json(self):
            raise requests.exceptions.JSONDecodeError("challenge", "<html>WAF</html>", 0)

    calls = _mock_get(monkeypatch, lambda batch: WafResponse())
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert balances == {} and len(calls) == 4
    assert sum("batch_failed" in w and "JSONDecodeError" in w for w in warnings) == 3
    assert all("reason=non_json_response" in w for w in warnings)
    assert not any("retry" in w for w in warnings)


def test_http_denial_after_transient_is_not_reported_as_recovery(monkeypatch):
    attempts = 0

    def responder(batch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.ConnectTimeout("transient")
        return Response(status_code=403)

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS[:5])
    assert balances == {} and len(calls) == 3
    assert sum("retry_failed" in w and "HTTPError" in w for w in warnings) == 1
    assert any("status=403" in w for w in warnings)
    assert not any("retry_recovered" in w for w in warnings)


def test_one_retry_token_is_shared_with_index_and_later_batches(monkeypatch):
    index_attempts = 0

    def index_responder():
        nonlocal index_attempts
        index_attempts += 1
        if index_attempts == 1:
            raise requests.ConnectTimeout("index transient")
        return Response()

    def responder(batch):
        if batch[0] in {PERIODS[0], PERIODS[5]}:
            raise requests.ReadTimeout("batch transient")
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder, index_responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert len(calls) == 5 and index_attempts == 2
    assert set(balances) == set(PERIODS[10:])
    assert sum("transient_retry" in w for w in warnings) == 1
    assert sum("retry_skipped" in w for w in warnings) == 2
    assert any("retry_recovered: eastmoney.Index:SH600667" in w for w in warnings)


def test_retry_empty_response_is_not_labelled_complete_recovery(monkeypatch):
    attempts = 0

    def responder(batch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.ReadTimeout("transient")
        return Response([])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS[:5])
    assert balances == {} and len(calls) == 3
    assert any("retry_partial_response" in w and "accepted_periods=0/5" in w for w in warnings)
    assert not any("retry_recovered" in w for w in warnings)


def test_admission_budget_preserves_success_and_does_not_start_more_requests(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wt.time, "monotonic", lambda: clock[0])

    def responder(batch):
        clock[0] = 81.0
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS)
    assert set(balances) == set(PERIODS[:5])
    assert len(calls) == 2
    assert sum(w.startswith("balance_sheet_budget_exhausted:") for w in warnings) == 2
    assert sum("batch_failed" in w for w in warnings) == 2


def test_retry_timeout_is_clamped_to_remaining_admission_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wt.time, "monotonic", lambda: clock[0])
    attempts = 0

    def responder(batch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            clock[0] = 76.0
            raise requests.ConnectTimeout("transient")
        return Response([_statement(day) for day in batch])

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS[:5])
    assert set(balances) == set(PERIODS[:5])
    assert calls[2][2] == pytest.approx((1.2, 2.8))
    assert sum(calls[2][2]) == pytest.approx(4.0)
    assert any("retry_recovered" in w for w in warnings)
    assert any("retry_timeout_s=1.2,2.8" in w for w in warnings)


def test_expired_budget_does_not_claim_or_attempt_a_retry(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wt.time, "monotonic", lambda: clock[0])

    def responder(batch):
        clock[0] = 80.0
        raise requests.ConnectTimeout("private timeout")

    calls = _mock_get(monkeypatch, responder)
    balances, warnings = wt._company_balance_sheets_for_periods("SH600667", PERIODS[:5])
    assert balances == {} and len(calls) == 2
    assert any("retry_skipped" in w and "cause=ConnectTimeout" in w for w in warnings)
    assert any("budget_exhausted" in w for w in warnings)
    assert not any("transient_retry" in w or "retry_recovered" in w or "private" in w for w in warnings)


@pytest.mark.parametrize("code,periods", [("600667", PERIODS), ("SH600667", ["2026-02-31"]), ("SH600667", ["20260630"])])
def test_invalid_subject_or_dates_fail_before_network(monkeypatch, code, periods):
    calls = _mock_get(monkeypatch)
    with pytest.raises(ValueError):
        wt._company_balance_sheets_for_periods(code, periods)
    assert calls == []


def _mock_abstract(monkeypatch):
    rows = []
    for index, label in enumerate(("营业总收入", "归母净利润", "扣非净利润", "基本每股收益")):
        rows.append({
            "选项": "常用指标", "指标": label,
            **{day.replace("-", ""): 1000.0 + index for day in PERIODS},
        })
    fake_ak = SimpleNamespace(stock_financial_abstract=lambda **kwargs: pd.DataFrame(rows))
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)


def test_abstract_preserves_latest_enrichment_when_older_batch_fails(monkeypatch):
    _mock_abstract(monkeypatch)

    def responder(batch):
        if batch[0] == PERIODS[5]:
            raise requests.ReadTimeout("older batch is unavailable")
        return Response([_statement(day) for day in batch])

    _mock_get(monkeypatch, responder)
    result = wt._akshare_financials("SH600667", 12)
    latest = result["rows"][0]
    assert latest["period"] == PERIODS[0]
    assert latest["cash_and_equivalents"] == 12_000
    assert latest["total_debt"] == 1_600
    assert latest["book_value"] == 50_000
    assert latest["balance_source_ref"] == "eastmoney.zcfzbAjaxNew:SH600667:2026-06-30"
    assert "cash_and_equivalents" not in result["rows"][5]
    assert all(row["revenue_cum"] == 1000.0 for row in result["rows"])
    assert any("batch_failed" in warning for warning in result["warnings"])


@pytest.mark.parametrize("missing", [None, True, float("nan"), float("inf")])
def test_missing_debt_component_stays_unknown_and_does_not_discard_other_fields(monkeypatch, missing):
    _mock_abstract(monkeypatch)
    _mock_get(monkeypatch, lambda batch: Response([
        _statement(day, LEASE_LIAB=missing) for day in batch
    ]))
    result = wt._akshare_financials("SH600667", 12)
    latest = result["rows"][0]
    assert latest["total_debt"] is None
    assert latest["cash_and_equivalents"] == 12_000
    assert latest["book_value"] == 50_000
    assert any("debt_components_incomplete: 2026-06-30" in warning for warning in result["warnings"])
    assert latest["debt_missing_components"] == ["LEASE_LIAB"]
    assert latest["debt_components_status"] == "incomplete"
    assert any("missing=LEASE_LIAB: null_not_zero" in warning for warning in result["warnings"])


def test_failed_company_type_keeps_core_financials_usable(monkeypatch):
    _mock_abstract(monkeypatch)

    def fail(*args, **kwargs):
        raise requests.ConnectTimeout("index unavailable")

    monkeypatch.setattr(requests, "get", fail)
    result = wt._akshare_financials("SH600667", 8)
    assert len(result["rows"]) == 8
    assert "cash_and_equivalents" not in result["rows"][0]
    assert any("balance_sheet_enrichment_failed: ConnectTimeout" in w for w in result["warnings"])
    assert sum("transient_retry" in w for w in result["warnings"]) == 1
