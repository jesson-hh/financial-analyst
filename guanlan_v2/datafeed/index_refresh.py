"""Bounded CSI 300 append after the canonical Stocks daily refresh.

The old ETF updater supplies the query seam, but its all-index overwrite is
deliberately not called. Only dates after the existing 399300.SZ tail may be
appended. An immutable backup and an independent receipt accompany each write.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "csi300-append-refresh/v1"
CODE = "399300.SZ"
FIELDS = ("ts_code", "trade_date", "close", "open", "high", "low", "pre_close",
          "change", "pct_chg", "vol", "amount")
HK = ZoneInfo("Asia/Hong_Kong")
MAX_CATCHUP_DAYS = 120
MIN_OVERLAP = 5


class IndexRefreshError(RuntimeError):
    """A closed, value-free reason code suitable for the run receipt."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - production host is Windows
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise IndexRefreshError("index_refresh_busy") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fetch_tail(stocks_root: Path, start: str, end: str):
    # Reuse Stocks' current credential resolver, which intentionally ignores
    # retired legacy env tokens. Never copy a secret into argv or receipts.
    resolver = stocks_root / "src" / "data" / "tushare_credentials.py"
    spec = importlib.util.spec_from_file_location("_guanlan_index_credentials", resolver)
    if spec is None or spec.loader is None:
        raise IndexRefreshError("credential_resolver_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = str(Path(__file__).resolve().parents[2] / "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from financial_analyst.data.updaters.etf_fund import _fund_query

    return _fund_query(module.load_tushare_token(), "index_daily", fields=",".join(FIELDS),
                       ts_code=CODE, start_date=start, end_date=end)


def _calendar(stocks_root: Path, now: datetime) -> list[str]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise IndexRefreshError("clock_timezone_missing")
    local = now.astimezone(HK)
    upper = local.date()
    if local.time() < time(15, 15):
        upper -= timedelta(days=1)
    path = stocks_root / "stock_data" / "cn_data" / "calendars" / "day.txt"
    try:
        dates = [datetime.strptime(line.strip(), "%Y-%m-%d").date()
                 for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except (OSError, ValueError):
        raise IndexRefreshError("canonical_calendar_unavailable") from None
    # Do not reinterpret the historical exchange calendar using weekdays:
    # older market history can legitimately contain Saturday sessions.
    if dates != sorted(set(dates)):
        raise IndexRefreshError("canonical_calendar_invalid")
    completed = [day.strftime("%Y%m%d") for day in dates if day <= upper]
    if not completed or (upper - datetime.strptime(completed[-1], "%Y%m%d").date()).days > 10:
        raise IndexRefreshError("canonical_calendar_stale")
    return completed


def _session(value: Any) -> str:
    raw = str(value or "").strip()
    pattern = "%Y-%m-%d" if len(raw) == 10 else "%Y%m%d" if len(raw) == 8 else ""
    if not pattern:
        raise IndexRefreshError("session_date_invalid")
    try:
        parsed = datetime.strptime(raw, pattern)
    except ValueError:
        raise IndexRefreshError("session_date_invalid") from None
    return parsed.strftime("%Y%m%d")


def _verified_sessions(stocks_root: Path, now: datetime,
                       through_session: str | None) -> tuple[list[str], dict[str, Any]]:
    """Cap the pre-generated exchange calendar at actually audited OHLCV.

    A calendar entry proves that a session is scheduled, not that this host's
    daily data has finished. Only a successful canonical coverage receipt can
    advance the benchmark refresh watermark.
    """
    calendar = _calendar(stocks_root, now)
    path = stocks_root / "stock_data/parquet/audit/cn_day_quality/latest.json"
    try:
        raw = path.read_bytes()
        quality = json.loads(raw)
    except (OSError, ValueError):
        raise IndexRefreshError("cn_day_quality_unavailable") from None
    if (not isinstance(quality, dict)
            or quality.get("schema_version") != "guanlan.cn-day-quality.v1"
            or quality.get("status") != "pass"
            or not isinstance(quality.get("run_id"), str) or not quality["run_id"].strip()):
        raise IndexRefreshError("cn_day_quality_not_verified")
    verified = _session(quality.get("verified_through"))
    try:
        audited_at = datetime.fromisoformat(str(quality.get("run_at") or "").replace("Z", "+00:00"))
    except ValueError:
        raise IndexRefreshError("cn_day_quality_time_invalid") from None
    if (audited_at.tzinfo is None or audited_at.utcoffset() is None
            or audited_at > now):
        raise IndexRefreshError("cn_day_quality_time_invalid")
    if verified > audited_at.astimezone(HK).strftime("%Y%m%d"):
        raise IndexRefreshError("cn_day_quality_time_invalid")
    clock_ceiling = calendar[-1]
    ceiling = min(clock_ceiling, verified)
    if ceiling not in calendar:
        raise IndexRefreshError("cn_day_quality_session_invalid")
    requested = _session(through_session) if through_session is not None else ceiling
    if requested > ceiling:
        raise IndexRefreshError("through_session_exceeds_verified_watermark")
    if requested not in calendar:
        raise IndexRefreshError("through_session_not_a_session")
    if (now.astimezone(HK).date() - datetime.strptime(ceiling, "%Y%m%d").date()).days > 10:
        raise IndexRefreshError("cn_day_quality_stale")
    return [day for day in calendar if day <= requested], {
        "requested_through_session": through_session,
        "clock_completed_session": clock_ceiling,
        "cn_day_verified_through": verified,
        "cn_day_quality_run_id": quality["run_id"],
        "cn_day_quality_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _assert_quality_unchanged(stocks_root: Path, proof: dict[str, Any]) -> None:
    path = stocks_root / "stock_data/parquet/audit/cn_day_quality/latest.json"
    if _digest(path) != proof["cn_day_quality_sha256"]:
        raise IndexRefreshError("cn_day_quality_changed_during_refresh")


def _prepare(old_table, source, calendar: list[str], start: str, end: str):
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    old = old_table.to_pandas()
    if set(old.columns) != set(FIELDS) or old.empty:
        raise IndexRefreshError("canonical_schema_invalid")
    if old.duplicated(["ts_code", "trade_date"]).any():
        raise IndexRefreshError("canonical_duplicate_dates")
    baseline = old.loc[old.ts_code == CODE].copy().sort_values("trade_date")
    if baseline.empty:
        raise IndexRefreshError("canonical_csi300_missing")
    latest = str(baseline.trade_date.iloc[-1])
    if source is None or source.empty or set(source.columns) != set(FIELDS):
        raise IndexRefreshError("source_schema_or_rows_missing")
    fresh = source.copy()
    if not fresh.ts_code.eq(CODE).all() or fresh.trade_date.duplicated().any():
        raise IndexRefreshError("source_identity_or_dates_ambiguous")
    if not fresh.trade_date.map(lambda d: isinstance(d, str) and len(d) == 8 and d.isdigit()).all():
        raise IndexRefreshError("source_date_invalid")
    if not fresh.trade_date.isin(calendar).all() or not fresh.trade_date.between(start, end).all():
        raise IndexRefreshError("source_outside_completed_sessions")
    if len(fresh) > 160:
        raise IndexRefreshError("source_window_too_large")
    numeric = list(FIELDS[2:])
    try:
        fresh[numeric] = fresh[numeric].apply(pd.to_numeric, errors="raise")
        values = fresh[numeric].to_numpy(dtype=float)
    except (ValueError, TypeError):
        raise IndexRefreshError("source_numeric_invalid") from None
    if not np.isfinite(values).all():
        raise IndexRefreshError("source_numeric_invalid")
    if (fresh[["open", "high", "low", "close", "pre_close"]] <= 0).any().any():
        raise IndexRefreshError("source_price_invalid")
    if (fresh[["vol", "amount"]] < 0).any().any():
        raise IndexRefreshError("source_volume_amount_invalid")
    if ((fresh.high < fresh[["open", "low", "close"]].max(axis=1)) |
            (fresh.low > fresh[["open", "close"]].min(axis=1))).any():
        raise IndexRefreshError("source_ohlc_incoherent")
    if not np.allclose(fresh.change, fresh.close - fresh.pre_close, rtol=0, atol=0.001):
        raise IndexRefreshError("source_change_incoherent")
    if not np.allclose(fresh.pct_chg, 100 * fresh.change / fresh.pre_close, rtol=0, atol=0.01):
        raise IndexRefreshError("source_return_incoherent")
    overlap = baseline.merge(fresh, on="trade_date", suffixes=("_old", "_source")).tail(20)
    if len(overlap) < MIN_OVERLAP:
        raise IndexRefreshError("source_overlap_insufficient")
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if not np.allclose(overlap[column + "_old"], overlap[column + "_source"],
                           rtol=0.0001, atol=0.001, equal_nan=False):
            raise IndexRefreshError("source_overlap_identity_failed")
    tail = fresh.loc[fresh.trade_date > latest].sort_values("trade_date")
    expected = [day for day in calendar if latest < day <= end]
    if tail.trade_date.tolist() != expected:
        raise IndexRefreshError("source_tail_incomplete")
    previous = float(baseline.close.iloc[-1])
    for row in tail.itertuples():
        if not np.isclose(row.pre_close, previous, rtol=0.0001, atol=0.001):
            raise IndexRefreshError("source_tail_continuity_failed")
        previous = float(row.close)
    addition = pa.Table.from_pandas(tail.loc[:, old.columns], schema=old_table.schema,
                                   preserve_index=False, safe=True)
    combined = pa.concat_tables([old_table, addition])
    if not combined.slice(0, old_table.num_rows).equals(old_table, check_metadata=True):
        raise IndexRefreshError("historical_rows_changed")
    return combined, {"overlap_rows": len(overlap), "appended_rows": len(tail),
                      "old_latest": latest, "new_latest": end}


def refresh_csi300(*, stocks_root: Path, receipt_path: Path, execute: bool = False,
                  now: datetime | None = None, fetch: Callable | None = None,
                  through_session: str | None = None) -> dict[str, Any]:
    started = now or datetime.now(HK)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "code": CODE, "source": "tushare.index_daily",
        "started_at": started.isoformat(timespec="seconds"), "applied": False,
        "status": "planned", "historical_rows_unchanged": None,
        "requested_through_session": through_session,
    }
    root = Path(stocks_root).resolve()
    target = root / "stock_data" / "parquet" / "etf_index.parquet"
    if not execute:
        result.update(target_exists=target.is_file(), network_attempted=False)
        try:
            calendar, proof = _verified_sessions(root, started, through_session)
            result.update(proof, expected_session=calendar[-1])
        except (IndexRefreshError, OSError) as exc:
            result.update(status="blocked", blocker=(str(exc) if isinstance(exc, IndexRefreshError)
                                                      else "cn_day_quality_unavailable"))
        return result
    backup = None
    run_dir = None
    written_digest = None
    # Keep the lock through final receipt persistence and any rollback. A
    # second appender must never acknowledge a commit that may still revert.
    guard = contextlib.ExitStack()
    try:
        if target.resolve() != target or not target.is_file():
            raise IndexRefreshError("canonical_target_missing_or_redirected")
        calendar, proof = _verified_sessions(root, started, through_session)
        result.update(proof)
        expected = calendar[-1]
        audit = target.parent / "audit" / "index_daily"
        if audit.resolve() != audit:
            raise IndexRefreshError("audit_path_redirected")
        guard.enter_context(_lock(audit / "refresh.lock"))
        _assert_quality_unchanged(root, proof)
        import pyarrow.parquet as pq

        original_digest = _digest(target)
        old_table = pq.read_table(target)
        old = old_table.to_pandas()
        if set(old.columns) != set(FIELDS) or old.empty:
            raise IndexRefreshError("canonical_schema_invalid")
        baseline = old.loc[old.ts_code == CODE]
        if baseline.empty:
            raise IndexRefreshError("canonical_csi300_missing")
        latest = str(baseline.trade_date.max())
        if latest not in calendar or old.duplicated(["ts_code", "trade_date"]).any():
            raise IndexRefreshError("canonical_dates_invalid")
        result.update({"expected_session": expected, "old_latest": latest,
                       "old_rows": old_table.num_rows, "old_sha256": original_digest})
        if latest == expected:
            result.update(status="complete", new_latest=latest, appended_rows=0,
                          historical_rows_unchanged=True, network_attempted=False)
        else:
            age = (datetime.strptime(expected, "%Y%m%d") - datetime.strptime(latest, "%Y%m%d")).days
            if age < 0 or age > MAX_CATCHUP_DAYS:
                raise IndexRefreshError("canonical_tail_requires_manual_backfill")
            start = (datetime.strptime(latest, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
            result["network_attempted"] = True
            source = (fetch or _fetch_tail)(root, start, expected)
            combined, checks = _prepare(old_table, source, calendar, start, expected)
            run_dir = audit / (started.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
            run_dir.mkdir(parents=True)
            stage = run_dir / "etf_index.staged.parquet"
            backup = run_dir / "etf_index.before.parquet"
            pq.write_table(combined, stage)
            if not pq.read_table(stage).equals(combined, check_metadata=True):
                raise IndexRefreshError("staged_verification_failed")
            shutil.copyfile(target, backup)
            if _digest(backup) != original_digest or _digest(target) != original_digest:
                raise IndexRefreshError("canonical_changed_during_refresh")
            written_digest = _digest(stage)
            result.update(checks, new_rows=combined.num_rows, new_sha256=written_digest,
                          backup=str(backup), historical_rows_unchanged=True)
            _write_receipt(run_dir / "receipt.json", {**result, "status": "prepared"})
            # Recheck immediately before the only canonical replacement.
            if _digest(target) != original_digest:
                raise IndexRefreshError("canonical_changed_before_commit")
            _assert_quality_unchanged(root, proof)
            os.replace(stage, target)
            result["applied"] = True
            if _digest(target) != written_digest:
                raise IndexRefreshError("committed_verification_failed")
            result["status"] = "complete"
            _write_receipt(run_dir / "receipt.json", result)
        result["ended_at"] = datetime.now(HK).isoformat(timespec="seconds")
        _write_receipt(receipt_path, result)
        return result
    except Exception as exc:
        # No raw provider errors/credentials are ever reflected in receipts.
        result.update(status="failed", blocker=(str(exc) if isinstance(exc, IndexRefreshError)
                                                else "index_refresh_failed"))
        if result["applied"] and backup and written_digest and _digest(target) == written_digest:
            rollback = target.with_name(f".{target.name}.rollback.{uuid.uuid4().hex}")
            try:
                shutil.copyfile(backup, rollback)
                if (_digest(rollback) == result["old_sha256"]
                        and _digest(target) == written_digest):
                    os.replace(rollback, target)
                    result.update(applied=False, rolled_back=True)
                else:
                    result["rollback_blocker"] = "rollback_digest_changed"
            finally:
                rollback.unlink(missing_ok=True)
        result["ended_at"] = datetime.now(HK).isoformat(timespec="seconds")
        if run_dir is not None:
            _write_receipt(run_dir / "receipt.json", result)
        _write_receipt(receipt_path, result)
        return result
    finally:
        guard.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks-root", type=Path, required=True)
    parser.add_argument("--receipt-path", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--through-session", help="Optional YYYY-MM-DD/YYYYMMDD cap; may not exceed audited OHLCV")
    args = parser.parse_args()
    result = refresh_csi300(stocks_root=args.stocks_root, receipt_path=args.receipt_path,
                            execute=args.execute, through_session=args.through_session)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"complete", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
