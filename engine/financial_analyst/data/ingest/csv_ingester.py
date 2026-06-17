"""CSV -> Qlib binary ingester.

Accepts a glob of CSV files. Each row must have: code, date, OHLCV fields.
Schema is configurable via the constructor.

Two CSV layouts are supported:

1. **Long format**: one row per (code, date). Multiple codes in one or many CSV files.
2. **Per-code format**: one CSV per code (filename = code). Rows are (date, OHLCV).
"""
from __future__ import annotations

import glob
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from financial_analyst.data.ingest.base import BaseIngester, IngestResult
from financial_analyst.data.ingest.bin_writer import (
    write_calendar,
    write_field_bin,
    write_instruments,
)

log = logging.getLogger(__name__)

# Default mapping: our internal field name -> CSV column name (identity)
DEFAULT_OHLCV_MAP: Dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
    "amount": "amount",
}


class CsvIngester(BaseIngester):
    """Convert one or more CSV files into the Qlib binary layout.

    Parameters
    ----------
    path_glob:
        Shell-style glob, e.g. ``"G:/data/*.csv"``.
    code_col:
        Column holding the stock code (for long format). ``None`` if
        ``per_code_filenames=True``.
    date_col:
        Column holding the trade date.
    date_format:
        ``strftime`` format string (e.g. ``"%Y%m%d"``). ``None`` = pandas
        auto-infer.
    ohlcv_map:
        Mapping ``{our_field: csv_column}``. Merged on top of
        ``DEFAULT_OHLCV_MAP`` — only the keys you provide are overridden.
    per_code_filenames:
        If ``True``, the CSV filename stem (without ``.csv``) is used as the
        stock code; ``code_col`` is not needed in the rows.
    """

    def __init__(
        self,
        path_glob: str,
        code_col: Optional[str] = None,
        date_col: str = "trade_date",
        date_format: Optional[str] = None,
        ohlcv_map: Optional[Dict[str, str]] = None,
        per_code_filenames: bool = False,
    ) -> None:
        if not per_code_filenames and not code_col:
            raise ValueError("Must specify either code_col or per_code_filenames=True")

        self.path_glob = path_glob
        self.code_col = code_col
        self.date_col = date_col
        self.date_format = date_format
        self.ohlcv_map: Dict[str, str] = {**DEFAULT_OHLCV_MAP, **(ohlcv_map or {})}
        self.per_code_filenames = per_code_filenames

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_files(self) -> List[str]:
        return sorted(glob.glob(self.path_glob))

    def _read_all(self) -> pd.DataFrame:
        files = self._list_files()
        if not files:
            raise FileNotFoundError(f"No files matched: {self.path_glob}")

        dfs: List[pd.DataFrame] = []
        for f in files:
            df = pd.read_csv(f)
            if self.per_code_filenames:
                code = Path(f).stem.upper()
                df["__code__"] = code
            dfs.append(df)

        full = pd.concat(dfs, ignore_index=True)

        # Normalize code column -> __code__
        if not self.per_code_filenames:
            full["__code__"] = full[self.code_col].astype(str).str.upper()

        # Normalize date -> __date__
        if self.date_format:
            full["__date__"] = pd.to_datetime(
                full[self.date_col].astype(str), format=self.date_format
            )
        else:
            full["__date__"] = pd.to_datetime(full[self.date_col])

        return full

    # ------------------------------------------------------------------
    # BaseIngester interface
    # ------------------------------------------------------------------

    def discover(self) -> Dict[str, Any]:
        """Inspect the source without writing anything.

        Returns a summary dict with file count, row count, codes, date range,
        and which OHLCV fields are present / missing.
        """
        df = self._read_all()
        codes = sorted(df["__code__"].unique().tolist())
        dates = sorted(df["__date__"].unique().tolist())
        return {
            "n_files": len(self._list_files()),
            "n_rows": len(df),
            "n_codes": len(codes),
            "codes_sample": codes[:5] + (["..."] if len(codes) > 5 else []),
            "date_range": (
                [
                    pd.Timestamp(dates[0]).strftime("%Y-%m-%d"),
                    pd.Timestamp(dates[-1]).strftime("%Y-%m-%d"),
                ]
                if dates
                else []
            ),
            "fields_present": [
                tgt for tgt, src in self.ohlcv_map.items() if src in df.columns
            ],
            "fields_missing": [
                tgt for tgt, src in self.ohlcv_map.items() if src not in df.columns
            ],
        }

    def convert(self, target_root: Path) -> IngestResult:
        """Read all CSVs and write Qlib binary files under ``target_root``.

        Steps:
        1. Write ``calendars/day.txt`` from the union of all dates.
        2. For each code, write ``features/<code_lower>/<field>.day.bin`` for
           each OHLCV field present in the data.
        3. Write ``instruments/all.txt`` with per-code date ranges.

        Returns an :class:`IngestResult` with counts.
        """
        df = self._read_all()
        target_root = Path(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        # 1. Calendar: union of all trade dates
        all_dates = sorted({pd.Timestamp(d).normalize() for d in df["__date__"]})
        n_dates = write_calendar(target_root, all_dates)
        calendar_list = list(all_dates)

        # 2. Per-code: write each field
        codes = sorted(df["__code__"].unique().tolist())
        instrument_ranges = []
        fields_written: set = set()

        for code in codes:
            sub = df[df["__code__"] == code].sort_values("__date__")
            if sub.empty:
                continue

            start = pd.Timestamp(sub["__date__"].min()).normalize()
            end = pd.Timestamp(sub["__date__"].max()).normalize()
            instrument_ranges.append((code, start, end))

            for out_field, src_col in self.ohlcv_map.items():
                if src_col not in sub.columns:
                    continue
                series = sub.set_index("__date__")[src_col]
                series = pd.to_numeric(series, errors="coerce").dropna()
                if series.empty:
                    continue
                write_field_bin(target_root, code, out_field, calendar_list, series)
                fields_written.add(out_field)
                log.debug("Wrote %s / %s (%d rows)", code, out_field, len(series))

        # 3. Instruments file
        n_instruments = write_instruments(target_root, instrument_ranges)

        return IngestResult(
            n_instruments=n_instruments,
            n_dates=n_dates,
            n_fields=len(fields_written),
            target_root=target_root,
        )
