"""TDX 自选股 watchlist updater — zero-token via local .blk file parse.

Reads ``{tdx_root}/T0002/blocknew/zxg.blk`` (自选股) and optionally
``tjg.blk`` (通用自选股, typically empty), normalises codes to Qlib format
(``SH600519`` / ``SZ000858`` / ``BJ830779``), and writes a consolidated
``watchlist.parquet`` under ``parquet_root``.

API::

    >>> from financial_analyst.data.updaters.watchlist import update_watchlist
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> stats = update_watchlist(p.parquet_root)
    >>> stats
    {'total': 34, 'ok': 34, 'failed': 0, 'new_rows': 34,
     'sources_found': ['zxg.blk'], 'output_path': '.../parquet/watchlist.parquet'}

Zero-token: reads directly from the TDX installation directory on disk.
No network calls. TDX client does not need to be running.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Union

import pandas as pd

# Bypass system proxy (Clash intercepts localhost — same pattern as f10.py / xdxr.py)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

log = logging.getLogger(__name__)

# ──────────────────────── constants ────────────────────────────────────────────

# Default TDX installation root on Windows.  Override via env TDX_ROOT or param.
_DEFAULT_TDX_ROOT = Path("D:/app/new_test2")

# Watchlist .blk files relative to {tdx_root}/T0002/blocknew/
_BLK_NAMES = ["zxg.blk", "tjg.blk"]

# ──────────────────────── schema ───────────────────────────────────────────────
# §4.2 watchlist.parquet field contract (4 columns).
# Full replace on every sync (list is small, <200 codes typical).

WATCHLIST_FIELDS = [
    "code",         # str  "SH600519"
    "source_file",  # str  "zxg.blk" / "tjg.blk"
    "position",     # int  0-based index within the .blk file
    "sync_time",    # str  "YYYY-MM-DD HH:MM:SS"
]

# Mapping from TDX leading digit → Qlib market prefix
_MKT_MAP = {"0": "SZ", "1": "SH", "2": "BJ"}


# ──────────────────────── parser ───────────────────────────────────────────────


def _parse_blk(content: bytes) -> list[str]:
    """Parse a TDX .blk file and return ordered list of Qlib-format codes.

    Format (empirical, from Phase-0 recon):
      - One code per line, CRLF or LF terminated
      - Leading market digit: ``1SH600519`` → SH (digit=1) + bare code ``600519``
        but the recon sample shows raw bytes ``313939393939390d0a`` = ``1999999``
        meaning the stored form is actually ``{digit}{6_or_7_char_code}`` without
        the SH/SZ text — the SH/SZ prefix in the summary was added by the parser.
      - Real stored line example (hex): ``31 36 30 35 34 39 39`` = ``1605499``
        → digit ``1`` (SH) + ``605499`` → ``SH605499``
      - Empty lines and whitespace-only lines are skipped.

    Args:
        content: Raw bytes of the .blk file.

    Returns:
        List of normalised Qlib-format codes, order preserved.
    """
    codes: list[str] = []
    for raw in content.split(b"\n"):
        line = raw.strip(b"\r\x00 ")
        if not line:
            continue
        try:
            s = line.decode("gbk", errors="replace").strip()
        except Exception:  # pragma: no cover — decode always returns str with errors=replace
            continue
        if not s:
            continue
        # Strip leading 0/1/2 market prefix if present
        if s[0] in _MKT_MAP:
            mkt_prefix = _MKT_MAP[s[0]]
            bare = s[1:]
            s = mkt_prefix + bare
        # Keep whatever we have — even sentinel codes like SH999999
        codes.append(s)
    return codes


# ──────────────────────── public API ───────────────────────────────────────────


def update_watchlist(
    parquet_root: Union[Path, str],
    tdx_root: Union[Path, str, None] = None,
    *,
    log_progress: bool = True,
) -> dict:
    """Sync TDX 自选股 from .blk files → parquet_root/watchlist.parquet.

    Args:
        parquet_root: Directory where ``watchlist.parquet`` will be written.
        tdx_root: Path to TDX installation root.  Resolved in priority order:
            1. ``tdx_root`` parameter (if not None)
            2. ``TDX_ROOT`` environment variable
            3. Default ``D:/app/new_test2``
        log_progress: Emit INFO-level log messages (set False in tests).

    Returns:
        Stats dict with keys: total, ok, failed, new_rows, sources_found, output_path.

    Raises:
        FileNotFoundError: If the resolved tdx_root does not exist.
    """
    parquet_root = Path(parquet_root)
    parquet_root.mkdir(parents=True, exist_ok=True)

    # ── resolve TDX root ──────────────────────────────────────────────────────
    if tdx_root is not None:
        tdx_path = Path(tdx_root)
    elif "TDX_ROOT" in os.environ:
        tdx_path = Path(os.environ["TDX_ROOT"])
    else:
        tdx_path = _DEFAULT_TDX_ROOT

    if not tdx_path.exists():
        raise FileNotFoundError(
            f"TDX root not found: {tdx_path}. "
            "Pass tdx_root= explicitly, set env TDX_ROOT, or ensure TDX is installed "
            f"at the default path ({_DEFAULT_TDX_ROOT})."
        )

    blk_dir = tdx_path / "T0002" / "blocknew"

    # ── stats ─────────────────────────────────────────────────────────────────
    stats: dict = {
        "total": 0,
        "ok": 0,
        "failed": 0,
        "new_rows": 0,
        "sources_found": [],
        "output_path": str(parquet_root / "watchlist.parquet"),
    }

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_rows: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="fa_watchlist_") as tmp_dir:
        tmp = Path(tmp_dir)

        for blk_name in _BLK_NAMES:
            src = blk_dir / blk_name
            stats["total"] += 1

            if not src.exists():
                if log_progress:
                    log.debug("watchlist: %s not found, skipping", src)
                stats["failed"] += 1
                continue

            # Copy to temp to avoid lock conflicts with running TDX client
            dst = tmp / blk_name
            try:
                shutil.copy2(src, dst)
            except OSError as exc:
                log.warning("watchlist: cannot copy %s → %s: %s", src, dst, exc)
                stats["failed"] += 1
                continue

            content = dst.read_bytes()
            codes = _parse_blk(content)

            rows = [
                {
                    "code": code,
                    "source_file": blk_name,
                    "position": i,
                    "sync_time": now_str,
                }
                for i, code in enumerate(codes)
            ]
            all_rows.extend(rows)
            stats["ok"] += 1
            stats["sources_found"].append(blk_name)

            if log_progress:
                log.info("watchlist: %s → %d codes", blk_name, len(codes))

    # ── write parquet (full replace) ─────────────────────────────────────────
    out_path = parquet_root / "watchlist.parquet"
    if all_rows:
        df = pd.DataFrame(all_rows, columns=WATCHLIST_FIELDS)
        # Ensure position is int (not object when rows come from list)
        df["position"] = df["position"].astype(int)
        df.to_parquet(out_path, index=False)
        stats["new_rows"] = len(df)
        if log_progress:
            log.info("watchlist: wrote %d rows → %s", len(df), out_path)
    else:
        if log_progress:
            log.warning("watchlist: no codes parsed from any .blk file — parquet not written")

    return stats
