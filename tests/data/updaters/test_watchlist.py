"""Unit tests for financial_analyst.data.updaters.watchlist.

4 tests per spec §8:
  1. Happy path — fake zxg.blk with GBK-encoded codes; assert parquet has rows.
  2. No tdx_root — non-existent path raises FileNotFoundError with clear msg.
  3. Locked file — shutil.copy2 raises OSError; updater records source as failed,
     does not crash, stats reflect failure.
  4. Schema contract — re-read parquet, verify 4 columns + dtypes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from financial_analyst.data.updaters.watchlist import WATCHLIST_FIELDS, update_watchlist

# ──────────────────────── shared fixture bytes ─────────────────────────────────

# GBK-encoded .blk content matching real TDX format:
#   leading digit {0=SZ, 1=SH, 2=BJ} + 6/7-char code + CRLF
_FAKE_BLK_CONTENT = (
    "\r\n".join(
        [
            "1600519",   # SH600519 茅台
            "0002594",   # SZ002594 比亚迪
            "1601318",   # SH601318 平安
            "",          # blank line — must be skipped
            "2830779",   # BJ830779 sample
        ]
    ).encode("gbk")
    + b"\r\n"
)

_EXPECTED_CODES = ["SH600519", "SZ002594", "SH601318", "BJ830779"]


def _make_tdx_tree(root: Path) -> Path:
    """Create fake T0002/blocknew/zxg.blk under root; return the blocknew dir."""
    blk_dir = root / "T0002" / "blocknew"
    blk_dir.mkdir(parents=True)
    (blk_dir / "zxg.blk").write_bytes(_FAKE_BLK_CONTENT)
    return blk_dir


# ──────────────────────── tests ───────────────────────────────────────────────


def test_watchlist_happy_path(tmp_path):
    """Create fake T0002/blocknew/zxg.blk with GBK-encoded codes; run; assert parquet has rows."""
    tdx_root = tmp_path / "tdx"
    parquet_root = tmp_path / "parquet"
    _make_tdx_tree(tdx_root)

    stats = update_watchlist(parquet_root, tdx_root=tdx_root, log_progress=False)

    # Stats checks
    assert stats["total"] == 2, "total should count both zxg.blk and tjg.blk attempts"
    assert stats["ok"] == 1, "only zxg.blk exists"
    assert stats["failed"] == 1, "tjg.blk is missing → failed"
    assert stats["sources_found"] == ["zxg.blk"]
    assert stats["new_rows"] == len(_EXPECTED_CODES)

    # Parquet written
    out_path = parquet_root / "watchlist.parquet"
    assert out_path.exists(), "watchlist.parquet should have been written"

    df = pd.read_parquet(out_path)
    assert len(df) == len(_EXPECTED_CODES)

    # Codes are correct, order preserved, blanks dropped, prefix stripped
    assert list(df["code"]) == _EXPECTED_CODES

    # Positions are 0-based sequential
    assert list(df["position"]) == list(range(len(_EXPECTED_CODES)))

    # source_file column is uniform
    assert (df["source_file"] == "zxg.blk").all()

    # sync_time looks like a datetime string
    assert df["sync_time"].str.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}").all()


def test_watchlist_no_tdx_root(tmp_path):
    """tdx_root pointing to non-existent dir raises FileNotFoundError with clear msg."""
    parquet_root = tmp_path / "parquet"
    missing = tmp_path / "does_not_exist"

    with pytest.raises(FileNotFoundError) as exc_info:
        update_watchlist(parquet_root, tdx_root=missing, log_progress=False)

    msg = str(exc_info.value)
    assert "TDX root not found" in msg, f"Expected 'TDX root not found' in: {msg!r}"
    assert str(missing) in msg, f"Expected path in error message: {msg!r}"


def test_watchlist_locked_file(tmp_path, monkeypatch):
    """Mock shutil.copy2 to raise OSError; updater records source as failed but doesn't crash."""
    tdx_root = tmp_path / "tdx"
    parquet_root = tmp_path / "parquet"
    _make_tdx_tree(tdx_root)

    import shutil as _shutil

    original_copy2 = _shutil.copy2

    def _raise_for_zxg(src, dst, **kwargs):
        if Path(src).name == "zxg.blk":
            raise OSError("file locked by TDX client")
        return original_copy2(src, dst, **kwargs)

    with patch("financial_analyst.data.updaters.watchlist.shutil.copy2", side_effect=_raise_for_zxg):
        stats = update_watchlist(parquet_root, tdx_root=tdx_root, log_progress=False)

    # zxg.blk locked → failed; tjg.blk missing → also failed
    assert stats["total"] == 2
    assert stats["ok"] == 0
    assert stats["failed"] == 2
    assert stats["new_rows"] == 0
    assert stats["sources_found"] == []

    # No parquet written when no data at all
    out_path = parquet_root / "watchlist.parquet"
    assert not out_path.exists(), "no parquet should be written when all sources fail"


def test_watchlist_schema_contract(tmp_path):
    """Re-read parquet; verify 4 columns + dtypes (code str, source_file str, position int, sync_time str)."""
    tdx_root = tmp_path / "tdx"
    parquet_root = tmp_path / "parquet"
    _make_tdx_tree(tdx_root)

    update_watchlist(parquet_root, tdx_root=tdx_root, log_progress=False)

    out_path = parquet_root / "watchlist.parquet"
    assert out_path.exists()
    df = pd.read_parquet(out_path)

    # Exactly 4 columns in the right order
    assert list(df.columns) == WATCHLIST_FIELDS, (
        f"Column mismatch.\nExpected: {WATCHLIST_FIELDS}\nGot:      {list(df.columns)}"
    )

    # dtype contracts per §4.2
    assert df["code"].dtype == object, "code should be string/object"
    assert df["source_file"].dtype == object, "source_file should be string/object"
    assert df["position"].dtype in ("int64", "int32", int), (
        f"position should be int, got {df['position'].dtype}"
    )
    assert df["sync_time"].dtype == object, "sync_time should be string/object"

    # Spot-check known value
    row0 = df.iloc[0]
    assert row0["code"] == "SH600519"
    assert row0["source_file"] == "zxg.blk"
    assert row0["position"] == 0
