"""Qlib binary-file read/write toolkit (vendored from G:/stocks/src/data/bin_writer.py).

No Qlib runtime dependency, operates directly on .bin files.

Format: ``[4-byte float32 start_index] + [float32 data array]``
Directory layout::

    {provider_uri}/
      calendars/{freq}.txt
      instruments/all.txt
      features/{instrument}/
        {field}.{freq}.bin

**Core safety function**: ``safe_merge_write()`` — read old → merge → write back,
**never loses historical data**. Incremental updates **MUST** go through it;
the raw ``write_bin`` overwrites everything and is only safe for full-import scenarios.

The toolkit was hardened after 2026-04-14 — that incident saw the same disk-write
logic copy-pasted across 4 scripts, one of which forgot the merge protection and
overwrote whole files, cutting 5500 stocks' 7 valuation-field histories from ~2500
days down to 6 days. Single entry-point + safe_merge_write enforcement: never
recurred since.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


# Windows reserved device names (cannot be used as directory names). See microsoft.com/.../naming-files
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
_QLIB_PREFIX = "_qlib_"


# ──────────────────────── Code ↔ directory name ────────────────────────


def code_to_fname(code: str) -> str:
    """Stock code → bin directory name (lowercase, handles Windows reserved names).

    Examples:
        >>> code_to_fname("SH600519")
        'sh600519'
        >>> code_to_fname("CON")   # Windows device name
        '_qlib_con'
    """
    if str(code).upper() in _RESERVED_NAMES:
        code = _QLIB_PREFIX + str(code)
    return str(code).lower()


def fname_to_code(fname: str) -> str:
    """bin directory name → stock code (UPPER)."""
    if fname.startswith(_QLIB_PREFIX):
        fname = fname[len(_QLIB_PREFIX):]
    return fname.upper()


# ──────────────────────── Calendar management ────────────────────────


def load_calendar(provider_uri: str, freq: str = "day") -> List[str]:
    """Load ``{provider_uri}/calendars/{freq}.txt``, returns a list of date strings (ascending)."""
    cal_path = Path(provider_uri) / "calendars" / f"{freq}.txt"
    if not cal_path.exists():
        return []
    with open(cal_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def build_calendar_index(calendar: List[str]) -> Dict[str, int]:
    """Calendar list → ``{date_str: position}``. Used by bin writers to convert dates to bin positions."""
    return {d: i for i, d in enumerate(calendar)}


def _is_weekend(date_str: str) -> bool:
    from datetime import date as _date
    try:
        y, m, d = map(int, date_str[:10].split("-"))
        return _date(y, m, d).weekday() >= 5
    except Exception:
        return False


def save_calendar(calendar: List[str], provider_uri: str, freq: str = "day") -> None:
    """Save the full calendar. The day frequency strictly filters weekend dates
    (A-share market doesn't trade weekends)."""
    if freq == "day":
        weekend = [d for d in calendar if _is_weekend(d)]
        if weekend:
            print(f"[bin_writer.save_calendar] blocked {len(weekend)} weekend dates: "
                  f"{weekend[:5]}...")
            calendar = [d for d in calendar if not _is_weekend(d)]
    cal_dir = Path(provider_uri) / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / f"{freq}.txt").write_text("\n".join(calendar) + "\n", encoding="utf-8")


def append_calendar(new_dates: List[str], provider_uri: str, freq: str = "day") -> int:
    """Append new dates to the calendar (auto-dedup + sort). Returns the number
    actually appended. The day frequency rejects weekends."""
    existing = load_calendar(provider_uri, freq)
    existing_set = set(existing)
    candidates = [d for d in new_dates if d not in existing_set]
    if freq == "day":
        weekend = [d for d in candidates if _is_weekend(d)]
        if weekend:
            print(f"[bin_writer.append_calendar] rejected {len(weekend)} weekend dates: "
                  f"{weekend[:5]}...")
        candidates = [d for d in candidates if not _is_weekend(d)]
    if not candidates:
        return 0
    save_calendar(sorted(existing + candidates), provider_uri, freq)
    return len(candidates)


# ──────────────────────── Instruments management ────────────────────────


def load_instruments(provider_uri: str, market: str = "all") -> Dict[str, Tuple[str, str]]:
    """Load ``instruments/{market}.txt``, returns ``{code: (start_date, end_date)}``."""
    path = Path(provider_uri) / "instruments" / f"{market}.txt"
    if not path.exists():
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                out[parts[0]] = (parts[1], parts[2])
    return out


def save_instruments(instruments: Dict[str, Tuple[str, str]],
                     provider_uri: str, market: str = "all") -> None:
    """Save instruments. Format: ``CODE\\tSTART\\tEND``, sorted by code."""
    inst_dir = Path(provider_uri) / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{code}\t{start}\t{end}"
             for code, (start, end) in sorted(instruments.items())]
    (inst_dir / f"{market}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_instrument_range(code: str, start_date: str, end_date: str,
                            provider_uri: str, market: str = "all") -> None:
    """Extend one stock's (start, end) range. Used to sync the instruments file after incremental updates."""
    instruments = load_instruments(provider_uri, market)
    if code in instruments:
        old_start, old_end = instruments[code]
        start_date = min(start_date, old_start)
        end_date = max(end_date, old_end)
    instruments[code] = (start_date, end_date)
    save_instruments(instruments, provider_uri, market)


# ──────────────────────── Bin file I/O ────────────────────────


def _bin_path(instrument: str, field: str, freq: str, provider_uri: str) -> Path:
    return (Path(provider_uri) / "features" / code_to_fname(instrument)
            / f"{field.lower()}.{freq}.bin")


def read_bin(instrument: str, field: str, freq: str,
             provider_uri: str) -> Tuple[int, np.ndarray]:
    """Read a .bin file, returns ``(start_index, float32_array)``.

    Missing or empty file → ``(0, empty)``. start_index is the first valid position
    of this field in the calendar.
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    if not path.exists():
        return 0, np.array([], dtype="<f")
    raw = np.fromfile(str(path), dtype="<f")
    if len(raw) == 0:
        return 0, np.array([], dtype="<f")
    return int(raw[0]), raw[1:]


def write_bin(instrument: str, field: str, freq: str, provider_uri: str,
              start_index: int, values: np.ndarray) -> None:
    """**⚠ Overwrite mode — use with caution**. Replaces the entire old file.

    History before the new start_index will be truncated. For incremental updates,
    **always** use ``safe_merge_write``. Direct ``write_bin`` is only allowed when:
      - Full import (first write from a ZIP source)
      - The caller has already merged before calling (e.g. inside ``safe_merge_write``)
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.hstack([start_index, values]).astype("<f").tofile(str(path))


def safe_merge_write(instrument: str, field: str, freq: str, provider_uri: str,
                     positions, values) -> None:
    """**Safe merge write** — the only correct way to do incremental updates.

    Behaviour:
      - Old bin does not exist → write new data directly
      - All new positions inside old range → overwrite in place
      - New positions after old range → auto-extend the bin (gap filled with NaN)
      - New positions straddle the old range → merge into a new range, keep old
        data, new data overwrites overlapping positions

    Args:
        positions: list[int] of calendar positions to write (must be ascending)
        values: list[float] corresponding values (same length as positions)

    Examples:
        >>> safe_merge_write("SH600519", "pe_ttm", "day", PROVIDER_URI,
        ...                  positions=[8614, 8615, 8616],
        ...                  values=[20.13, 20.05, 19.98])
    """
    positions = list(positions)
    values = list(values)
    if not positions:
        return
    if len(positions) != len(values):
        raise ValueError(f"positions/values length mismatch: "
                         f"{len(positions)} vs {len(values)}")

    old_si, old_data = read_bin(instrument, field, freq, provider_uri)

    # Old bin empty: write directly
    if len(old_data) == 0:
        first_pos = positions[0]
        last_pos = positions[-1]
        arr = np.full(last_pos - first_pos + 1, np.nan, dtype=np.float32)
        for pos, val in zip(positions, values):
            arr[pos - first_pos] = val
        write_bin(instrument, field, freq, provider_uri, first_pos, arr)
        return

    old_ei = old_si + len(old_data) - 1
    new_first = positions[0]
    new_last = positions[-1]
    merged_si = min(old_si, new_first)
    merged_ei = max(old_ei, new_last)

    merged = np.full(merged_ei - merged_si + 1, np.nan, dtype=np.float32)
    merged[old_si - merged_si: old_si - merged_si + len(old_data)] = old_data
    for pos, val in zip(positions, values):
        merged[pos - merged_si] = val

    write_bin(instrument, field, freq, provider_uri, merged_si, merged)


def get_bin_range(instrument: str, field: str, freq: str,
                  provider_uri: str) -> Tuple[int, int]:
    """Quickly inspect a bin file's range ``(start_index, end_index)`` without reading data.

    Missing or empty file: ``(0, -1)`` (end < start = empty).
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    if not path.exists():
        return 0, -1
    file_size = path.stat().st_size
    n_values = (file_size // 4) - 1   # subtract header
    if n_values <= 0:
        return 0, -1
    with open(path, "rb") as f:
        start_index = int(np.frombuffer(f.read(4), dtype="<f")[0])
    return start_index, start_index + n_values - 1
