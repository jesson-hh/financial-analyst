# -*- coding: utf-8 -*-
"""Durable, replayable intraday bars used by Seats decisions.

The live pytdx endpoint used to be an ephemeral pass-through: the browser saw
the bars, but a later Agent decision could not prove which bars it used.  This
store keeps a small global market-data cache (not a workspace copy) at the
natural grain ``(code, freq, bar_time)``.  It is intentionally separate from
the settled Qlib repository; the official EOD updater remains the only writer
to that repository.

SQLite WAL gives the desktop process a single durable writer with idempotent
upserts.  Every returned snapshot carries a deterministic digest and a
``seats-realtime://`` reference so decisions can be replayed without placing
the full minute series in ``seats_decisions.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


VAR = Path(__file__).resolve().parents[2] / "var"
DEFAULT_DB_PATH = VAR / "realtime" / "seats_bars.sqlite3"
SUPPORTED_FREQS = frozenset({"1min", "5min"})
_CODE_RE = re.compile(r"^(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?$", re.I)
_SCHEMA_VERSION = 1
_SUBNORMAL_VOLUME = 1e-6  # less than 0.0001 share after lot conversion is noise


class RealtimeStoreError(ValueError):
    """Invalid bar data or an unavailable realtime store."""


def normalize_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    match = _CODE_RE.fullmatch(raw)
    if not match:
        raise RealtimeStoreError("code 必须是 A 股六位代码(可带 SH/SZ/BJ 前后缀)")
    prefix, digits, suffix = match.groups()
    market = prefix or suffix
    if prefix and suffix and prefix != suffix:
        raise RealtimeStoreError("code 交易所前后缀冲突")
    if not market:
        market = "SH" if digits[0] in "569" else ("BJ" if digits[0] in "48" else "SZ")
    return f"{market}{digits}"


def normalize_freq(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = {"1": "1min", "1m": "1min", "5": "5min", "5m": "5min"}.get(raw, raw)
    if raw not in SUPPORTED_FREQS:
        raise RealtimeStoreError("freq 仅支持 1min/5min")
    return raw


def _utc_iso(value: Optional[Any] = None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise RealtimeStoreError("pulled_at 不是 ISO 时间") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bar_time(value: Any) -> str:
    raw = str(value or "").strip().replace("T", " ")
    if not raw:
        raise RealtimeStoreError("bar 缺时间")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RealtimeStoreError(f"bar 时间非法:{raw}") from exc
    # TDX minute bars are exchange-local (Asia/Shanghai) and normally naive.
    # Store that wall-clock value explicitly and keep timezone in the schema
    # contract instead of letting host timezone conversion shift the key.
    return dt.replace(tzinfo=None, second=0, microsecond=0).isoformat(sep=" ", timespec="minutes")


def _finite_number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RealtimeStoreError(f"bar {name} 非数字") from exc
    if not math.isfinite(result):
        raise RealtimeStoreError(f"bar {name} 非有限数")
    return result


def _normalise_bar(row: dict, pulled_at: str, freq: str, source: str) -> dict:
    if not isinstance(row, dict):
        raise RealtimeStoreError("bar 必须是对象")
    ts = _bar_time(row.get("bar_time") or row.get("trade_date") or row.get("date")
                   or row.get("datetime"))
    op = _finite_number(row.get("open", row.get("o")), "open")
    hi = _finite_number(row.get("high", row.get("h")), "high")
    lo = _finite_number(row.get("low", row.get("l")), "low")
    cl = _finite_number(row.get("close", row.get("c")), "close")
    if min(op, hi, lo, cl) <= 0:
        raise RealtimeStoreError("bar OHLC 必须大于 0")
    if hi < max(op, lo, cl) or lo > min(op, hi, cl):
        raise RealtimeStoreError("bar OHLC 几何关系非法")

    flags: list[str] = []
    raw_vol = row.get("vol", row.get("volume", row.get("v")))
    volume: Optional[float]
    if raw_vol is None:
        volume = None
        flags.append("volume_missing")
    else:
        try:
            volume = float(raw_vol)
        except (TypeError, ValueError):
            volume = None
            flags.append("volume_invalid")
        else:
            if not math.isfinite(volume) or volume < 0:
                volume = None
                flags.append("volume_invalid")
            elif 0 < volume < _SUBNORMAL_VOLUME:
                # Some TDX hosts return IEEE subnormal garbage for an empty
                # minute.  Treat it as zero volume and preserve the evidence.
                volume = 0.0
                flags.append("volume_subnormal_zeroed")

    if isinstance(row.get("quality_flags"), list):
        flags.extend(str(x) for x in row["quality_flags"] if str(x).strip())
    flags = sorted(set(flags))

    try:
        pulled = datetime.fromisoformat(pulled_at.replace("Z", "+00:00"))
        local_now = pulled.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
        bar_dt = datetime.fromisoformat(ts)
        interval_minutes = 1 if freq == "1min" else 5
        if bar_dt > local_now + timedelta(minutes=interval_minutes):
            raise RealtimeStoreError("bar 时间晚于采集时刻")
    except ValueError as exc:
        raise RealtimeStoreError("bar/采集时间不可比较") from exc

    explicit_final = row.get("is_final")
    if explicit_final is None:
        # TDX labels 1m/5m bars by their interval *end* (09:31, 09:35, ...), so
        # the bar is complete as soon as that timestamp is reached.  Adding a
        # second interval here would incorrectly mark already-closed bars as
        # forming for another 1/5 minutes.
        try:
            is_final = bar_dt <= local_now
        except ValueError:
            is_final = True
    else:
        is_final = bool(explicit_final)

    canonical = {
        "bar_time": ts,
        "open": op,
        "high": hi,
        "low": lo,
        "close": cl,
        "volume": volume,
        "source": source,
        "pulled_at": pulled_at,
        "is_final": is_final,
        "quality_flags": flags,
    }
    # Observation time/source are lineage, not market state.  Excluding them
    # keeps the evidence identity stable when an unchanged bar is re-polled.
    evidence_identity = {k: canonical[k] for k in (
        "bar_time", "open", "high", "low", "close", "volume",
        "is_final", "quality_flags")}
    canonical["bar_digest"] = hashlib.sha256(
        json.dumps(evidence_identity, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return canonical


def _snapshot_digest(code: str, freq: str, bars: list[dict]) -> str:
    try:
        digest_rows = [{k: row[k] for k in (
            "bar_time", "open", "high", "low", "close", "volume",
            "is_final", "quality_flags", "bar_digest"
        )} for row in bars]
    except (KeyError, TypeError) as exc:
        raise RealtimeStoreError("snapshot receipt bars 字段不完整") from exc
    return hashlib.sha256(
        json.dumps({"code": code, "freq": freq, "bars": digest_rows},
                   ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class RealtimeBarStore:
    """SQLite-backed minute bar cache with deterministic snapshot receipts."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            found = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if found is not None and int(found[0]) != _SCHEMA_VERSION:
                raise RealtimeStoreError("realtime store schema_version 不支持")
            conn.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(_SCHEMA_VERSION),),
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bars (
                    code TEXT NOT NULL,
                    freq TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL,
                    source TEXT NOT NULL,
                    pulled_at TEXT NOT NULL,
                    is_final INTEGER NOT NULL,
                    quality_flags TEXT NOT NULL,
                    bar_digest TEXT NOT NULL,
                    PRIMARY KEY(code, freq, bar_time)
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bars_lookup ON bars(code,freq,bar_time DESC)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                    digest TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    freq TEXT NOT NULL,
                    first_bar_time TEXT NOT NULL,
                    last_bar_time TEXT NOT NULL,
                    bar_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_zlib BLOB NOT NULL
                )"""
            )

    def ingest(self, code: Any, freq: Any, rows: Iterable[dict], *,
               source: str = "pytdx", pulled_at: Optional[Any] = None) -> dict:
        canonical_code = normalize_code(code)
        canonical_freq = normalize_freq(freq)
        source = str(source or "unknown").strip()[:64] or "unknown"
        pulled_iso = _utc_iso(pulled_at)
        if hasattr(rows, "to_dict"):
            rows = rows.to_dict("records")  # pandas DataFrame without importing pandas here

        accepted: dict[str, dict] = {}
        rejected: list[dict] = []
        for idx, raw in enumerate(rows or []):
            try:
                item = _normalise_bar(raw, pulled_iso, canonical_freq, source)
            except RealtimeStoreError as exc:
                rejected.append({"index": idx, "reason": str(exc)})
                continue
            accepted[item["bar_time"]] = item  # last observation wins within one pull

        ordered = [accepted[key] for key in sorted(accepted)]
        if ordered:
            with self._connect() as conn:
                conn.executemany(
                    """INSERT INTO bars(
                        code,freq,bar_time,open,high,low,close,volume,source,
                        pulled_at,is_final,quality_flags,bar_digest
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(code,freq,bar_time) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, volume=excluded.volume,
                        source=excluded.source, pulled_at=excluded.pulled_at,
                        is_final=excluded.is_final,
                        quality_flags=excluded.quality_flags,
                        bar_digest=excluded.bar_digest
                    WHERE excluded.pulled_at >= bars.pulled_at""",
                    [(
                        canonical_code, canonical_freq, item["bar_time"],
                        item["open"], item["high"], item["low"], item["close"],
                        item["volume"], item["source"], item["pulled_at"],
                        1 if item["is_final"] else 0,
                        json.dumps(item["quality_flags"], ensure_ascii=False),
                        item["bar_digest"],
                    ) for item in ordered],
                )

        snapshot = self.snapshot(canonical_code, canonical_freq,
                                 limit=max(1, len(ordered))) if ordered else None
        return {
            "ok": bool(ordered),
            "code": canonical_code,
            "freq": canonical_freq,
            "accepted": len(ordered),
            "rejected": len(rejected),
            "rejections": rejected[:20],
            "quality_flagged": sum(bool(x["quality_flags"]) for x in ordered),
            "snapshot": snapshot,
        }

    def load(self, code: Any, freq: Any, *, limit: int = 480,
             asof: Optional[Any] = None) -> list[dict]:
        canonical_code = normalize_code(code)
        canonical_freq = normalize_freq(freq)
        try:
            lim = max(1, min(5000, int(limit)))
        except (TypeError, ValueError) as exc:
            raise RealtimeStoreError("limit 必须是整数") from exc
        params: list[Any] = [canonical_code, canonical_freq]
        where = "code=? AND freq=?"
        if asof is not None:
            where += " AND bar_time<=?"
            params.append(_bar_time(asof))
        params.append(lim)
        with self._connect() as conn:
            records = conn.execute(
                f"""SELECT bar_time,open,high,low,close,volume,source,pulled_at,
                           is_final,quality_flags,bar_digest
                    FROM bars WHERE {where}
                    ORDER BY bar_time DESC LIMIT ?""",
                params,
            ).fetchall()
        result: list[dict] = []
        for row in reversed(records):
            item = dict(row)
            item["is_final"] = bool(item["is_final"])
            try:
                item["quality_flags"] = json.loads(item["quality_flags"])
            except (TypeError, json.JSONDecodeError):
                item["quality_flags"] = ["quality_flags_corrupt"]
            result.append(item)
        return result

    def snapshot(self, code: Any, freq: Any, *, limit: int = 480,
                 asof: Optional[Any] = None,
                 persist_receipt: bool = False) -> Optional[dict]:
        canonical_code = normalize_code(code)
        canonical_freq = normalize_freq(freq)
        bars = self.load(canonical_code, canonical_freq, limit=limit, asof=asof)
        if not bars:
            return None
        digest = _snapshot_digest(canonical_code, canonical_freq, bars)
        first = bars[0]["bar_time"]
        last = bars[-1]["bar_time"]
        ref = (f"seats-realtime://{canonical_code}/{canonical_freq}"
               f"?from={first.replace(' ', 'T')}&to={last.replace(' ', 'T')}"
               f"&n={len(bars)}&sha256={digest}")
        result = {
            "code": canonical_code,
            "freq": canonical_freq,
            "timezone": "Asia/Shanghai",
            "first_bar_time": first,
            "last_bar_time": last,
            "n": len(bars),
            "complete_n": sum(1 for row in bars if row["is_final"]),
            "quality_flagged_n": sum(bool(row["quality_flags"]) for row in bars),
            "digest": digest,
            "data_ref": ref,
            "bars": bars,
        }
        if persist_receipt:
            self._persist_receipt(result)
        return result

    def _persist_receipt(self, snapshot: dict) -> None:
        payload = zlib.compress(json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"), level=6)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO snapshots(
                    digest,code,freq,first_bar_time,last_bar_time,bar_count,
                    created_at,payload_zlib) VALUES(?,?,?,?,?,?,?,?)""",
                (snapshot["digest"], snapshot["code"], snapshot["freq"],
                 snapshot["first_bar_time"], snapshot["last_bar_time"],
                 int(snapshot["n"]), _utc_iso(), sqlite3.Binary(payload)),
            )

    def seal_snapshot(self, code: Any, freq: Any, *, limit: int = 480,
                      asof: Optional[Any] = None) -> Optional[dict]:
        """Create an immutable receipt for evidence actually used by an Agent."""
        snapshot = self.snapshot(code, freq, limit=limit, asof=asof)
        return self.seal_receipt(snapshot) if snapshot is not None else None

    def seal_receipt(self, snapshot: dict) -> dict:
        """Persist the exact already-read snapshot consumed by an Agent.

        Sealing by value avoids a read/seal race where a forming bar is revised
        between building the Agent context and querying the store a second time.
        The full digest/ref envelope is recomputed before persistence so callers
        cannot attach an arbitrary payload to a trusted digest.
        """
        if not isinstance(snapshot, dict):
            raise RealtimeStoreError("snapshot receipt 必须是对象")
        code = normalize_code(snapshot.get("code"))
        freq = normalize_freq(snapshot.get("freq"))
        bars = snapshot.get("bars")
        if not isinstance(bars, list) or not bars:
            raise RealtimeStoreError("snapshot receipt bars 不能为空")
        digest = _snapshot_digest(code, freq, bars)
        first = bars[0].get("bar_time")
        last = bars[-1].get("bar_time")
        ref = (f"seats-realtime://{code}/{freq}"
               f"?from={str(first).replace(' ', 'T')}"
               f"&to={str(last).replace(' ', 'T')}"
               f"&n={len(bars)}&sha256={digest}")
        expected = {
            "code": code,
            "freq": freq,
            "first_bar_time": first,
            "last_bar_time": last,
            "n": len(bars),
            "digest": digest,
            "data_ref": ref,
        }
        for key, value in expected.items():
            if snapshot.get(key) != value:
                raise RealtimeStoreError(f"snapshot receipt {key} 不匹配")
        self._persist_receipt(snapshot)
        return snapshot

    def receipt(self, digest: str) -> Optional[dict]:
        """Load an immutable snapshot by the sha256 embedded in ``data_ref``."""
        key = str(digest or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise RealtimeStoreError("digest 必须是 64 位 sha256")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_zlib FROM snapshots WHERE digest=?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(zlib.decompress(bytes(row[0])).decode("utf-8"))
        except Exception as exc:
            raise RealtimeStoreError("snapshot receipt 损坏") from exc
        if not isinstance(payload, dict) or payload.get("digest") != key:
            raise RealtimeStoreError("snapshot receipt digest 不匹配")
        try:
            computed = _snapshot_digest(
                normalize_code(payload.get("code")),
                normalize_freq(payload.get("freq")), payload.get("bars") or [])
        except RealtimeStoreError:
            raise
        if computed != key:
            raise RealtimeStoreError("snapshot receipt 内容校验失败")
        return payload


__all__ = [
    "DEFAULT_DB_PATH",
    "RealtimeBarStore",
    "RealtimeStoreError",
    "normalize_code",
    "normalize_freq",
]
