from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import pytest

from guanlan_v2.datafeed import index_refresh as index


NOW = datetime(2026, 9, 8, 18, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "stocks"
    days = pd.bdate_range("2026-08-24", "2026-09-09")
    calendar = root / "stock_data/cn_data/calendars/day.txt"
    calendar.parent.mkdir(parents=True)
    calendar.write_text("\n".join(days.strftime("%Y-%m-%d")) + "\n", encoding="utf-8")
    rows = []
    for offset, day in enumerate(days):
        close = 100.0 + offset
        rows.append(dict(ts_code=index.CODE, trade_date=day.strftime("%Y%m%d"),
                         close=close, open=close - 0.2, high=close + 1, low=close - 1,
                         pre_close=close - 1, change=1.0, pct_chg=100 / (close - 1),
                         vol=100000.0 + offset, amount=1000000.0 + offset))
    source = pd.DataFrame(rows).loc[:, index.FIELDS]
    old = source[source.trade_date <= "20260904"].copy()
    other = old.iloc[[0]].copy()
    other["ts_code"] = "OTHER.INDEX"
    old = pd.concat([old, other], ignore_index=True)
    target = root / "stock_data/parquet/etf_index.parquet"
    target.parent.mkdir(parents=True)
    old.to_parquet(target, index=False)
    _quality(root, "2026-09-08")
    return root, target, source[source.trade_date <= "20260908"].copy(), tmp_path / "receipt.json"


def _quality(root, session, **overrides):
    path = root / "stock_data/parquet/audit/cn_day_quality/latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "guanlan.cn-day-quality.v1", "status": "pass",
               "run_id": "isolated-cn-day-run", "run_at": session + "T18:00:00+08:00",
               "verified_through": session, **overrides}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_append_preserves_every_existing_row_and_is_idempotent(data):
    root, target, source, receipt = data
    original = pq.read_table(target)
    digest = _sha(target)
    calls = []

    def fetch(r, start, end):
        calls.append((r, start, end))
        return source

    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=fetch)
    assert result["status"] == "complete" and result["applied"] is True
    assert result["appended_rows"] == 2 and result["new_latest"] == "20260908"
    assert calls == [(root, "20260805", "20260908")]
    updated = pq.read_table(target)
    assert updated.slice(0, original.num_rows).equals(original, check_metadata=True)
    assert _sha(Path(result["backup"])) == digest
    assert updated.num_rows == original.num_rows + 2
    assert json.loads(receipt.read_text())["historical_rows_unchanged"] is True
    new_digest = _sha(target)
    second = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: pytest.fail("no query when current"))
    assert second["status"] == "complete" and second["appended_rows"] == 0
    assert second["applied"] is False and _sha(target) == new_digest


def test_plan_does_not_query_or_create_outputs(data):
    root, target, _, receipt = data
    digest = _sha(target)
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, now=NOW,
                                  fetch=lambda *_: pytest.fail("plan must not query"))
    assert result["status"] == "planned" and result["network_attempted"] is False
    assert not receipt.exists() and _sha(target) == digest
    assert not (target.parent / "audit/index_daily").exists()


@pytest.mark.parametrize("fault,blocker", [
    ("wrong_symbol", "source_identity_or_dates_ambiguous"),
    ("duplicate", "source_identity_or_dates_ambiguous"),
    ("missing_day", "source_tail_incomplete"),
    ("future", "source_outside_completed_sessions"),
    ("nan", "source_numeric_invalid"),
    ("wrong_ohlc", "source_ohlc_incoherent"),
    ("negative_volume", "source_volume_amount_invalid"),
    ("wrong_overlap", "source_overlap_identity_failed"),
    ("wrong_units", "source_overlap_identity_failed"),
    ("missing_columns", "source_schema_or_rows_missing"),
    ("not_enough_overlap", "source_overlap_insufficient"),
    ("wrong_change", "source_change_incoherent"),
])
def test_untrusted_source_never_changes_canonical_data(data, fault, blocker):
    root, target, source, receipt = data
    digest = _sha(target)
    if fault == "wrong_symbol":
        source.loc[source.index[-1], "ts_code"] = "000300.SH"
    elif fault == "duplicate":
        source = pd.concat([source, source.iloc[[-1]]], ignore_index=True)
    elif fault == "missing_day":
        source = source[source.trade_date != "20260907"]
    elif fault == "future":
        source.loc[source.index[-1], "trade_date"] = "20260909"
    elif fault == "nan":
        source.loc[source.index[-1], "close"] = float("nan")
    elif fault == "wrong_ohlc":
        source.loc[source.index[-1], "high"] = 1.0
    elif fault == "negative_volume":
        source.loc[source.index[-1], "vol"] = -1.0
    elif fault == "wrong_overlap":
        source.loc[source.index[0], "open"] -= 0.5
    elif fault == "wrong_units":
        source["vol"] *= 100
    elif fault == "missing_columns":
        source = source.drop(columns="amount")
    elif fault == "not_enough_overlap":
        source = source.tail(5)
    elif fault == "wrong_change":
        source.loc[source.index[-1], "change"] = 2.0
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: source)
    assert result["status"] == "failed" and result["blocker"] == blocker
    assert result["applied"] is False and _sha(target) == digest


def test_intraday_uses_only_preceding_completed_session(data):
    root, _, source, receipt = data
    _quality(root, "2026-09-07")
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW.replace(hour=10),
                                  fetch=lambda _r, _s, end: source[source.trade_date <= end])
    assert result["status"] == "complete"
    assert result["new_latest"] == "20260907" and result["appended_rows"] == 1


def test_default_caps_pregenerated_calendar_at_actual_cn_day_watermark(data):
    root, _, source, receipt = data
    _quality(root, "2026-09-07")
    calls = []

    def fetch(_root, start, end):
        calls.append(end)
        return source[source.trade_date <= end]

    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=fetch)
    assert result["status"] == "complete" and result["new_latest"] == "20260907"
    assert result["clock_completed_session"] == "20260908"
    assert result["cn_day_verified_through"] == "20260907" and calls == ["20260907"]


def test_explicit_session_can_only_narrow_the_verified_window(data):
    root, _, source, receipt = data
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  through_session="2026-09-07", now=NOW,
                                  fetch=lambda _r, _s, end: source[source.trade_date <= end])
    assert result["status"] == "complete" and result["new_latest"] == "20260907"
    assert result["requested_through_session"] == "2026-09-07"
    assert result["cn_day_verified_through"] == "20260908"


@pytest.mark.parametrize("requested", ["2026-09-08", "20260909"])
def test_explicit_session_cannot_exceed_verified_cn_day(data, requested):
    root, target, _, receipt = data
    _quality(root, "2026-09-07")
    digest = _sha(target)
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  through_session=requested, now=NOW,
                                  fetch=lambda *_: pytest.fail("must reject before query"))
    assert result["blocker"] == "through_session_exceeds_verified_watermark"
    assert _sha(target) == digest and result["applied"] is False


@pytest.mark.parametrize("fault", ["missing", "failed", "future_receipt", "invalid_schema"])
def test_unverified_cn_day_receipt_blocks_index_write(data, fault):
    root, target, _, receipt = data
    path = _quality(root, "2026-09-08")
    if fault == "missing":
        path.unlink()
    elif fault == "failed":
        _quality(root, "2026-09-08", status="failed")
    elif fault == "future_receipt":
        _quality(root, "2026-09-08", run_at="2026-09-09T19:00:00+08:00")
    else:
        _quality(root, "2026-09-08", schema_version="unknown")
    digest = _sha(target)
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: pytest.fail("quality blocked"))
    assert result["status"] == "failed" and result["applied"] is False
    assert _sha(target) == digest


def test_quality_receipt_change_during_fetch_aborts_commit(data):
    root, target, source, receipt = data
    digest = _sha(target)

    def fetch(*_):
        _quality(root, "2026-09-08", status="failed", run_id="another-update")
        return source

    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=fetch)
    assert result["blocker"] == "cn_day_quality_changed_during_refresh"
    assert result["applied"] is False and _sha(target) == digest


def test_historical_saturday_does_not_invalidate_authoritative_calendar(data):
    root, _, source, receipt = data
    path = root / "stock_data/cn_data/calendars/day.txt"
    path.write_text("1991-01-05\n" + path.read_text(), encoding="utf-8")
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: source)
    assert result["status"] == "complete" and result["appended_rows"] == 2


def test_another_writer_during_fetch_is_detected_and_preserved(data):
    root, target, source, receipt = data
    expected_digest = None

    def concurrent_write(*_):
        nonlocal expected_digest
        updated = pd.read_parquet(target)
        updated.loc[0, "amount"] += 1
        updated.to_parquet(target, index=False)
        expected_digest = _sha(target)
        return source

    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=concurrent_write)
    assert result["blocker"] == "canonical_changed_during_refresh"
    assert result["applied"] is False and _sha(target) == expected_digest


def test_receipt_failure_after_commit_rolls_back_our_write(data, monkeypatch):
    root, target, source, receipt = data
    digest = _sha(target)
    write = index._write_receipt
    failed_once = False

    def fail_after_commit(path, payload):
        nonlocal failed_once
        if payload["status"] == "complete" and not failed_once:
            failed_once = True
            raise OSError("unavailable")
        write(path, payload)

    monkeypatch.setattr(index, "_write_receipt", fail_after_commit)
    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: source)
    assert result["status"] == "failed" and result["rolled_back"] is True
    assert result["applied"] is False and _sha(target) == digest


def test_final_receipt_failure_and_rollback_hold_lock_until_fully_resolved(data, monkeypatch):
    root, target, source, receipt = data
    digest = _sha(target)
    write = index._write_receipt
    copy = index.shutil.copyfile
    observations = []
    failed_once = False

    def contender(phase):
        result = index.refresh_csi300(
            stocks_root=root, receipt_path=receipt.with_name(phase + ".json"),
            execute=True, now=NOW, fetch=lambda *_: pytest.fail("contender acquired lock"),
        )
        observations.append((phase, result["status"], result.get("blocker")))

    def fail_final_receipt(path, payload):
        nonlocal failed_once
        if path == receipt and payload["status"] == "complete" and not failed_once:
            failed_once = True
            contender("during_final_receipt")
            raise OSError("receipt unavailable")
        write(path, payload)

    def observe_rollback(src, dst):
        if ".rollback." in Path(dst).name:
            contender("during_rollback")
        return copy(src, dst)

    monkeypatch.setattr(index, "_write_receipt", fail_final_receipt)
    monkeypatch.setattr(index.shutil, "copyfile", observe_rollback)
    first = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                 now=NOW, fetch=lambda *_: source)
    assert first["status"] == "failed" and first["rolled_back"] is True
    assert observations == [("during_final_receipt", "failed", "index_refresh_busy"),
                            ("during_rollback", "failed", "index_refresh_busy")]
    assert _sha(target) == digest
    archived = json.loads((Path(first["backup"]).parent / "receipt.json").read_text())
    assert archived["status"] == "failed" and archived["rolled_back"] is True
    second = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=lambda *_: source)
    assert second["status"] == "complete" and second["appended_rows"] == 2


def test_provider_failure_never_copies_exception_values(data):
    root, target, _, receipt = data
    digest = _sha(target)

    def fail(*_):
        raise RuntimeError("provider accidentally reflected SECRET_VALUE")

    result = index.refresh_csi300(stocks_root=root, receipt_path=receipt, execute=True,
                                  now=NOW, fetch=fail)
    assert result["blocker"] == "index_refresh_failed"
    assert "SECRET_VALUE" not in receipt.read_text()
    assert _sha(target) == digest
