from __future__ import annotations

import json
import sqlite3
import zlib

import pytest

from guanlan_v2.seats.realtime_store import RealtimeBarStore, RealtimeStoreError
from guanlan_v2.seats.minute_evidence import fetch_live_snapshot, frame_from_snapshot


def _bar(ts: str, close: float = 10.2, vol: float = 120.0) -> dict:
    return {
        "date": ts,
        "open": 10.0,
        "high": max(10.3, close),
        "low": min(9.9, close),
        "close": close,
        "vol": vol,
    }


def test_ingest_is_durable_unique_and_replayable(tmp_path):
    path = tmp_path / "bars.sqlite3"
    store = RealtimeBarStore(path)
    out = store.ingest(
        "600667",
        "1min",
        [_bar("2026-08-14 09:31", 10.1), _bar("2026-08-14 09:32", 10.2)],
        pulled_at="2026-08-14T01:33:00Z",
    )
    assert out["ok"] is True
    assert out["accepted"] == 2
    assert out["snapshot"]["code"] == "SH600667"
    assert out["snapshot"]["last_bar_time"] == "2026-08-14 09:32"

    # Reopen the database and upsert the same natural key: no duplicate rows.
    reopened = RealtimeBarStore(path)
    reopened.ingest(
        "SH600667",
        "1m",
        [_bar("2026-08-14 09:32", 10.25)],
        pulled_at="2026-08-14T01:34:00Z",
    )
    rows = reopened.load("600667.SH", "1", limit=50)
    assert len(rows) == 2
    assert rows[-1]["close"] == 10.25


def test_stale_pull_cannot_overwrite_newer_observation(tmp_path):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    store.ingest("SZ300750", "5min", [_bar("2026-08-14 10:00", 20.0)],
                 pulled_at="2026-08-14T02:06:00Z")
    store.ingest("SZ300750", "5min", [_bar("2026-08-14 10:00", 11.0)],
                 pulled_at="2026-08-14T02:05:00Z")
    assert store.load("300750", "5min")[-1]["close"] == 20.0


def test_bad_ohlc_is_rejected_and_subnormal_volume_is_flagged(tmp_path):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    bad = _bar("2026-08-14 14:58")
    bad["high"] = 9.0
    tiny = _bar("2026-08-14 14:59", vol=5.877471754111438e-41)
    out = store.ingest("SH600667", "1min", [bad, tiny],
                       pulled_at="2026-08-14T07:00:00Z")
    assert out["accepted"] == 1
    assert out["rejected"] == 1
    row = store.load("SH600667", "1min")[0]
    assert row["volume"] == 0.0
    assert row["quality_flags"] == ["volume_subnormal_zeroed"]


@pytest.mark.parametrize(
    ("freq", "bar_time", "pulled_at", "expected"),
    [
        ("1min", "2026-08-14 09:31", "2026-08-14T01:31:01Z", True),
        ("1min", "2026-08-14 09:31", "2026-08-14T01:30:59Z", False),
        ("5min", "2026-08-14 09:35", "2026-08-14T01:35:01Z", True),
        ("5min", "2026-08-14 09:35", "2026-08-14T01:34:59Z", False),
    ],
)
def test_tdx_end_label_is_final_as_soon_as_bar_timestamp_arrives(
        freq, bar_time, pulled_at, expected, tmp_path):
    store = RealtimeBarStore(tmp_path / f"{freq}-{expected}.sqlite3")
    store.ingest("SH600667", freq, [_bar(bar_time)], pulled_at=pulled_at)
    assert store.load("SH600667", freq)[0]["is_final"] is expected


def test_snapshot_asof_digest_and_ref_are_stable(tmp_path):
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    store.ingest(
        "BJ833509",
        "1min",
        [_bar("2026-08-14 09:31"), _bar("2026-08-14 09:32"),
         _bar("2026-08-14 09:33")],
        pulled_at="2026-08-14T01:34:00Z",
    )
    first = store.snapshot("833509", "1min", asof="2026-08-14 09:32")
    second = store.snapshot("BJ833509", "1min", asof="2026-08-14 09:32")
    assert first == second
    assert first["n"] == 2
    assert first["last_bar_time"] == "2026-08-14 09:32"
    assert first["data_ref"].startswith("seats-realtime://BJ833509/1min?")
    assert f"sha256={first['digest']}" in first["data_ref"]


def test_live_adapter_uses_the_persisted_validated_snapshot(tmp_path):
    import pandas as pd

    class Feed:
        closed = False

        def bars1(self, code, n):
            return pd.DataFrame([
                {"trade_date": "2026-08-14 14:58", "open": 10.0, "high": 10.2,
                 "low": 9.9, "close": 10.1, "vol": 100.0},
                {"trade_date": "2026-08-14 14:59", "open": 10.1, "high": 10.1,
                 "low": 10.0, "close": 10.0, "vol": 5.877471754111438e-41},
            ])

        def bars5(self, code, n):  # pragma: no cover - wrong branch guard
            raise AssertionError("1min must not call bars5")

        def close(self):
            self.closed = True

    snapshot = fetch_live_snapshot(
        "SH600667", "1min", store=RealtimeBarStore(tmp_path / "bars.sqlite3"),
        feed_factory=Feed, pulled_at="2026-08-14T07:00:00Z",
    )
    assert snapshot["n"] == 2
    assert snapshot["quality_flagged_n"] == 1
    assert snapshot["bars"][-1]["volume"] == 0.0
    frame = frame_from_snapshot(snapshot, asof="2026-08-14 14:58")
    assert list(frame["close"]) == [10.1]


def test_same_market_evidence_has_stable_digest_across_pull_times(tmp_path):
    """Polling lineage may advance without changing evidence identity."""
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    row = {**_bar("2026-08-14 14:59", close=10.2), "is_final": True}
    first = store.ingest(
        "SH600667", "1min", [row], pulled_at="2026-08-14T07:00:00Z",
    )["snapshot"]
    second = store.ingest(
        "SH600667", "1min", [row], pulled_at="2026-08-14T07:01:00Z",
    )["snapshot"]

    assert first["digest"] == second["digest"]
    assert first["data_ref"] == second["data_ref"]
    assert first["bars"][0]["bar_digest"] == second["bars"][0]["bar_digest"]
    assert first["bars"][0]["pulled_at"] != second["bars"][0]["pulled_at"]
    sealed = store.seal_snapshot("SH600667", "1min", limit=20)
    assert sealed is not None and sealed["digest"] == first["digest"]
    receipt = store.receipt(sealed["digest"])
    assert receipt is not None and receipt["digest"] == sealed["digest"]
    assert receipt["bars"][0]["close"] == 10.2


def test_forming_bar_revision_keeps_old_receipt_replayable(tmp_path):
    """An upsert changes the live view, never an already-cited Agent receipt."""
    path = tmp_path / "bars.sqlite3"
    store = RealtimeBarStore(path)
    forming = {
        **_bar("2026-08-14 14:59", close=10.10),
        "high": 10.20,
        "is_final": False,
    }
    store.ingest(
        "SH600667", "1min", [forming], pulled_at="2026-08-14T06:59:30Z",
    )
    old = store.seal_snapshot("SH600667", "1min", limit=20)
    assert old is not None

    revised = {
        **_bar("2026-08-14 14:59", close=10.25),
        "high": 10.30,
        "is_final": True,
    }
    store.ingest(
        "SH600667", "1min", [revised], pulled_at="2026-08-14T07:00:30Z",
    )
    new = store.seal_snapshot("SH600667", "1min", limit=20)
    assert new is not None
    assert new["digest"] != old["digest"]
    assert new["data_ref"] != old["data_ref"]
    assert store.load("SH600667", "1min")[-1]["close"] == 10.25

    # Receipts survive both the natural-key overwrite and a process restart.
    reopened = RealtimeBarStore(path)
    old_replay = reopened.receipt(old["digest"])
    new_replay = reopened.receipt(new["digest"])
    assert old_replay is not None and new_replay is not None
    assert old_replay["data_ref"] == old["data_ref"]
    assert old_replay["bars"][-1]["close"] == 10.10
    assert old_replay["bars"][-1]["is_final"] is False
    assert new_replay["data_ref"] == new["data_ref"]
    assert new_replay["bars"][-1]["close"] == 10.25
    assert new_replay["bars"][-1]["is_final"] is True


def test_seal_receipt_persists_the_exact_snapshot_already_used(tmp_path):
    """A concurrent forming-bar update cannot change an Agent's receipt."""
    store = RealtimeBarStore(tmp_path / "bars.sqlite3")
    store.ingest(
        "SH600667", "1min",
        [{**_bar("2026-08-14 14:59", close=10.10), "is_final": False}],
        pulled_at="2026-08-14T06:59:30Z",
    )
    used = store.snapshot("SH600667", "1min", limit=20)
    assert used is not None and used["bars"][-1]["close"] == 10.10

    # Another poll revises the same natural key after the Agent frame was made.
    store.ingest(
        "SH600667", "1min",
        [{**_bar("2026-08-14 14:59", close=10.25), "is_final": True}],
        pulled_at="2026-08-14T07:00:30Z",
    )
    sealed = store.seal_receipt(used)
    replay = store.receipt(sealed["digest"])
    assert replay is not None and replay["bars"][-1]["close"] == 10.10
    assert store.snapshot("SH600667", "1min")["bars"][-1]["close"] == 10.25


def test_plain_snapshot_polling_does_not_grow_receipts_until_sealed(tmp_path):
    """Twenty-second UI polling must not turn into an unbounded audit log."""
    path = tmp_path / "bars.sqlite3"
    store = RealtimeBarStore(path)
    store.ingest(
        "SH600667", "1min", [_bar("2026-08-14 14:59", close=10.2)],
        pulled_at="2026-08-14T07:00:00Z",
    )

    def receipt_count():
        with sqlite3.connect(path) as conn:
            return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    assert receipt_count() == 0
    snapshots = [store.snapshot("SH600667", "1min", limit=20) for _ in range(100)]
    assert all(item is not None for item in snapshots)
    assert len({item["digest"] for item in snapshots}) == 1
    assert receipt_count() == 0

    sealed = store.seal_snapshot("SH600667", "1min", limit=20)
    assert sealed is not None
    assert receipt_count() == 1
    assert store.receipt(sealed["digest"])["data_ref"] == sealed["data_ref"]
    # Sealing the exact same evidence is idempotent by digest.
    assert store.seal_snapshot("SH600667", "1min", limit=20)["digest"] == sealed["digest"]
    assert receipt_count() == 1


def test_receipt_recomputes_digest_instead_of_trusting_embedded_value(tmp_path):
    path = tmp_path / "bars.sqlite3"
    store = RealtimeBarStore(path)
    store.ingest("SH600667", "1min", [_bar("2026-08-14 14:59", 10.2)],
                 pulled_at="2026-08-14T07:00:00Z")
    sealed = store.seal_snapshot("SH600667", "1min")
    assert sealed is not None
    with sqlite3.connect(path) as conn:
        blob = conn.execute(
            "SELECT payload_zlib FROM snapshots WHERE digest=?", (sealed["digest"],)
        ).fetchone()[0]
        payload = json.loads(zlib.decompress(blob).decode("utf-8"))
        payload["bars"][0]["close"] = 99.0  # embedded digest deliberately unchanged
        conn.execute(
            "UPDATE snapshots SET payload_zlib=? WHERE digest=?",
            (sqlite3.Binary(zlib.compress(json.dumps(payload).encode("utf-8"))),
             sealed["digest"]),
        )
    with pytest.raises(RealtimeStoreError, match="内容校验失败"):
        store.receipt(sealed["digest"])
