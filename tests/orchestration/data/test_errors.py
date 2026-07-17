"""Contract tests for the data error taxonomy (``orchestration.data.errors``).

The taxonomy is the router's whole control surface: narrow cross-vendor
fallback keys on ``isinstance`` against exactly two types, and every other
``DataError`` either terminates the chain typed or raises. These tests pin the
class hierarchy *and* the three-way routing disjointness those downstream
``isinstance`` checks depend on, plus the structured fields each carrier freezes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration.data import errors as e


# --------------------------------------------------------------------------- #
# Hierarchy
# --------------------------------------------------------------------------- #
def test_hierarchy():
    assert issubclass(e.NoDataError, e.DataError)
    assert issubclass(e.StaleDataError, e.DataError)
    assert issubclass(e.NotConfiguredError, (e.DataError, ValueError))
    assert issubclass(e.FutureDataRefused, e.DataError)
    assert issubclass(e.MissingAvailabilityRefused, e.DataError)
    assert issubclass(e.SourceBrokenError, e.DataIntegrityError)
    assert issubclass(e.RoutingConfigurationError, (e.DataIntegrityError, ValueError))
    assert issubclass(e.SnapshotMismatchError, e.DataIntegrityError)
    assert issubclass(e.CacheIntegrityError, e.DataIntegrityError)
    assert issubclass(e.LiveFallbackRefused, e.DataIntegrityError)


def test_rate_limit_and_integrity_are_data_errors():
    # RateLimitError (a fallback trigger) and the integrity base both descend
    # from DataError so a broad ``except DataError`` catches every taxonomy type.
    assert issubclass(e.RateLimitError, e.DataError)
    assert issubclass(e.DataIntegrityError, e.DataError)


def test_every_taxonomy_class_descends_from_data_error():
    taxonomy = [
        e.NoDataError, e.StaleDataError, e.RateLimitError, e.NotConfiguredError,
        e.FutureDataRefused, e.MissingAvailabilityRefused, e.DataIntegrityError,
        e.SourceBrokenError, e.RoutingConfigurationError, e.SnapshotMismatchError,
        e.CacheIntegrityError, e.LiveFallbackRefused,
    ]
    for cls in taxonomy:
        assert issubclass(cls, e.DataError)
        assert issubclass(cls, Exception)


# --------------------------------------------------------------------------- #
# Routing disjointness — the control surface downstream keys on
# --------------------------------------------------------------------------- #
#: The ONLY two types that may advance the (already frozen) vendor chain.
_FALLBACK = (e.RateLimitError, e.NotConfiguredError)


def test_only_ratelimit_and_notconfigured_are_fallback_triggers():
    # These two — and no others — are caught by the narrow-fallback tuple.
    assert isinstance(e.RateLimitError("throttled"), _FALLBACK)
    assert isinstance(e.NotConfiguredError("no key"), _FALLBACK)


@pytest.mark.parametrize(
    "err",
    [
        e.NoDataError(symbol="600519", canonical="600519.SH", detail="delisted"),
        e.StaleDataError("stale"),
        e.FutureDataRefused("leak", future_rows=1),
        e.MissingAvailabilityRefused("no available_at"),
        e.DataIntegrityError("broken"),
        e.SourceBrokenError("500"),
        e.RoutingConfigurationError("bad route"),
        e.SnapshotMismatchError("digest drift"),
        e.CacheIntegrityError("corrupt"),
        e.LiveFallbackRefused("replay may not go live"),
    ],
)
def test_terminal_and_raise_errors_never_trigger_fallback(err):
    # NoData/Stale terminate the chain typed; Future + every integrity error
    # must raise loud — none may be mistaken for a fallback trigger.
    assert not isinstance(err, _FALLBACK)


def test_ratelimit_and_notconfigured_are_disjoint():
    assert not isinstance(e.RateLimitError("x"), e.NotConfiguredError)
    assert not isinstance(e.NotConfiguredError("x"), e.RateLimitError)


def test_integrity_errors_are_not_fallback_family():
    # A broken source must never be masked by a fallback answer, so no integrity
    # error may be an instance of the fallback pair.
    for cls in (e.SourceBrokenError, e.SnapshotMismatchError,
                e.CacheIntegrityError, e.LiveFallbackRefused,
                e.RoutingConfigurationError):
        assert not issubclass(cls, e.RateLimitError)
        assert not issubclass(cls, e.NotConfiguredError)


# --------------------------------------------------------------------------- #
# Structured field carriers
# --------------------------------------------------------------------------- #
def test_no_data_carries_symbol_detail():
    err = e.NoDataError(symbol="600519", canonical="600519.SH", detail="delisted")
    assert err.symbol == "600519" and err.canonical == "600519.SH" and err.detail == "delisted"


def test_no_data_detail_defaults_empty_and_is_keyword_only():
    err = e.NoDataError(symbol="600519", canonical="600519.SH")
    assert err.detail == ""
    # symbol/canonical are keyword-only: positional construction is a TypeError.
    with pytest.raises(TypeError):
        e.NoDataError("600519", "600519.SH")  # type: ignore[misc]


def test_no_data_message_mentions_symbol_and_canonical():
    err = e.NoDataError(symbol="600519", canonical="600519.SH", detail="delisted")
    text = str(err)
    assert "600519" in text and "600519.SH" in text and "delisted" in text


def test_stale_carries_availability_and_audit():
    ts = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)
    audit = {"vintage": "v3"}
    err = e.StaleDataError("too old", latest_available_at=ts, pit_audit=audit)
    assert err.detail == "too old"
    assert err.latest_available_at == ts
    assert err.pit_audit is audit
    assert str(err) == "too old"


def test_stale_optional_fields_default_none():
    err = e.StaleDataError("too old")
    assert err.latest_available_at is None
    assert err.pit_audit is None
    # latest_available_at / pit_audit are keyword-only: a second positional
    # arg has nowhere to bind and must raise TypeError.
    with pytest.raises(TypeError):
        e.StaleDataError("too old", datetime(2026, 7, 16, tzinfo=timezone.utc))  # type: ignore[misc]


def test_future_refused_carries_count():
    err = e.FutureDataRefused("leak", future_rows=3)
    assert err.future_rows == 3
    assert str(err) == "leak"


def test_future_refused_requires_future_rows_keyword():
    # future_rows is required and keyword-only.
    with pytest.raises(TypeError):
        e.FutureDataRefused("leak")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        e.FutureDataRefused("leak", 3)  # type: ignore[misc]


def test_not_configured_is_valueerror():
    with pytest.raises(ValueError):
        raise e.NotConfiguredError("no key")


def test_routing_configuration_is_valueerror():
    with pytest.raises(ValueError):
        raise e.RoutingConfigurationError("no such vendor in chain")


def test_missing_availability_message_passthrough():
    err = e.MissingAvailabilityRefused("row missing available_at")
    assert str(err) == "row missing available_at"


# --------------------------------------------------------------------------- #
# Catch-as-DataError (broad handler still works after typed catches)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "factory",
    [
        lambda: e.NoDataError(symbol="600519", canonical="600519.SH"),
        lambda: e.StaleDataError("stale"),
        lambda: e.RateLimitError("throttled"),
        lambda: e.NotConfiguredError("no key"),
        lambda: e.FutureDataRefused("leak", future_rows=1),
        lambda: e.MissingAvailabilityRefused("no available_at"),
        lambda: e.SourceBrokenError("500"),
        lambda: e.RoutingConfigurationError("bad route"),
        lambda: e.SnapshotMismatchError("drift"),
        lambda: e.CacheIntegrityError("corrupt"),
        lambda: e.LiveFallbackRefused("no live"),
    ],
)
def test_each_is_catchable_as_data_error(factory):
    with pytest.raises(e.DataError):
        raise factory()
