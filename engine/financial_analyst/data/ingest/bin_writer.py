from __future__ import annotations
import struct
from pathlib import Path
from typing import Iterable, List

import numpy as np
import pandas as pd


def write_calendar(root: Path, dates: Iterable[pd.Timestamp]) -> int:
    """Write sorted unique YYYY-MM-DD per line to calendars/day.txt.

    Returns the count of unique dates written.
    """
    sorted_unique = sorted({pd.Timestamp(d).normalize() for d in dates})
    cal_dir = root / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / "day.txt").write_text(
        "\n".join(d.strftime("%Y-%m-%d") for d in sorted_unique),
        encoding="utf-8",
    )
    return len(sorted_unique)


def write_instruments(
    root: Path,
    instrument_ranges: List[tuple],
) -> int:
    """Write CODE<TAB>start<TAB>end, one per instrument to instruments/all.txt.

    Parameters
    ----------
    instrument_ranges:
        List of (code, start_timestamp, end_timestamp) tuples.

    Returns the count of instruments written.
    """
    inst_dir = root / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{code.upper()}\t{start.strftime('%Y-%m-%d')}\t{end.strftime('%Y-%m-%d')}"
        for code, start, end in instrument_ranges
    ]
    (inst_dir / "all.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def write_field_bin(
    root: Path,
    code: str,
    field: str,
    calendar: List[pd.Timestamp],
    series: pd.Series,
) -> None:
    """Write a single <field>.day.bin under features/<code_lower>/.

    Binary format: [4-byte float32 start_index] + [float32 value array].

    ``series`` is indexed by trade_date (pd.DatetimeIndex). We align it to
    ``calendar``, write [start_index] + [float32 values from start_index up to
    last valid date inclusive]. Dates in the range with no data become NaN.
    """
    if series.empty:
        return

    first_date = pd.Timestamp(series.index.min()).normalize()
    last_date = pd.Timestamp(series.index.max()).normalize()
    cal_dates = [pd.Timestamp(d).normalize() for d in calendar]

    # Find start_index: first calendar slot >= first series date
    start_index = None
    for i, d in enumerate(cal_dates):
        if d >= first_date:
            start_index = i
            break
    if start_index is None:
        return

    # Find end_index: last calendar slot <= last series date
    end_index = None
    for i in range(len(cal_dates) - 1, -1, -1):
        if cal_dates[i] <= last_date:
            end_index = i
            break
    if end_index is None:
        return

    # Build float32 array from start_index..end_index inclusive, NaN for gaps
    series_by_date = {pd.Timestamp(d).normalize(): float(v) for d, v in series.items()}
    aligned = [
        series_by_date.get(cal_dates[i], float("nan"))
        for i in range(start_index, end_index + 1)
    ]

    code_dir = root / "features" / code.lower()
    code_dir.mkdir(parents=True, exist_ok=True)
    bin_path = code_dir / f"{field}.day.bin"
    with open(bin_path, "wb") as f:
        f.write(struct.pack("<f", float(start_index)))
        f.write(np.array(aligned, dtype=np.float32).tobytes())
