"""TDX F10 events updater — zero-token via pytdx direct.

Vendored from ``G:/stocks/src/data/tdx_f10_collector.py`` (research lab side),
with hardcoded ``G:/stocks/...`` paths replaced by ``news_data_root`` /
``parquet_root`` function parameters so it composes cleanly with
``financial_analyst.data.paths.get_data_paths()``.

Wire it from ``fa data update --include-f10`` (see ``data_cli.update_cmd``).

API::

    >>> from financial_analyst.data.updaters.f10 import update_f10, resolve_universe
    >>> from financial_analyst.data.paths import get_data_paths
    >>> p = get_data_paths()
    >>> codes = resolve_universe(p.parquet_root, "csi500")
    >>> stats = update_f10(p.news_data_root, p.parquet_root, codes)
    >>> stats
    {'total': 500, 'ok': 487, 'skipped': 13, 'failed': 0, 'new_rows': 1234,
     'index_path': '.../parquet/tdx_f10_index.parquet'}

Zero-token: pytdx connects directly to broker quote hosts (招商证券 / 东兴 /
华泰 等), no registration. Same network path as ``pytdx_kline`` / ``pytdx_pool``.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd


# Five high-value F10 categories — covers ~80% of analyst-relevant events
# while keeping per-stock fetch under ~5s. Caller can override.
KEY_CATEGORIES = ["最新提示", "公司大事", "研究报告", "主力追踪", "龙虎榜单"]

# All 15 categories TDX exposes via F10 (use ``categories=ALL_CATEGORIES``)
ALL_CATEGORIES = [
    "最新提示", "公司概况", "财务分析", "股东研究", "股本结构", "资本运作",
    "业内点评", "行业分析", "公司大事", "研究报告", "经营分析", "主力追踪",
    "分红扩股", "高层治理", "龙虎榜单",
]


# ──────────────────────── pytdx helpers ────────────────────────


def _qlib_to_tdx(code: str) -> tuple[int, str]:
    """Convert ``SH600519`` → ``(1, '600519')``, ``SZ000858`` → ``(0, '000858')``,
    ``BJ830779`` → ``(2, '830779')``."""
    code = code.upper()
    if code.startswith("SH"):
        return 1, code[2:]
    if code.startswith("SZ"):
        return 0, code[2:]
    if code.startswith("BJ"):
        return 2, code[2:]
    raise ValueError(f"Unknown code prefix: {code}")


def _connect_tdx(max_try: int = 8):
    """Try ``max_try`` hosts from pytdx's built-in pool; return first that connects."""
    from pytdx.config.hosts import hq_hosts
    from pytdx.hq import TdxHq_API

    api = TdxHq_API(heartbeat=False, auto_retry=True)
    for _name, host, port in hq_hosts[:max_try]:
        try:
            if api.connect(host, int(port), time_out=3):
                return api
        except Exception:
            continue
    raise RuntimeError("All TDX servers unreachable (tried 8). Check network / firewall.")


def fetch_f10_one(api, code: str, categories: Optional[List[str]] = None) -> dict:
    """Fetch one stock's F10 across requested categories.

    Returns ``{category_name: text}``. On failure to enumerate categories,
    returns ``{'_error': '...'}``. Individual category fetch errors get
    embedded as ``[fetch error: ...]`` markers in the value.
    """
    mkt, c = _qlib_to_tdx(code)
    categories = categories or KEY_CATEGORIES

    try:
        cats = api.get_company_info_category(mkt, c)
    except Exception as e:
        return {"_error": f"get_category failed: {e}"}
    if not cats:
        return {}

    result: dict[str, str] = {}
    for row in cats:
        name = row.get("name", "")
        if name not in categories:
            continue
        filename = row.get("filename", "")
        start = int(row.get("start", 0))
        length = int(row.get("length", 0))
        if length <= 0:
            continue
        try:
            # pytdx single fetch caps ~65535 bytes; segment for larger blobs.
            chunks: list[str] = []
            remain, offset = length, start
            while remain > 0:
                chunk = min(remain, 60000)
                seg = api.get_company_info_content(mkt, c, filename, offset, chunk)
                if not seg:
                    break
                chunks.append(seg)
                offset += chunk
                remain -= chunk
            result[name] = "".join(chunks)
        except Exception as e:
            result[name] = f"[fetch error: {e}]"
    return result


def _save_f10(code: str, f10: dict, f10_dir: Path, save_date: str) -> List[dict]:
    """Write each category's text to ``{f10_dir}/{code.lower()}/{cat}_{date}.txt``
    and return index rows (hash, length, path)."""
    stock_dir = f10_dir / code.lower()
    stock_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for category, text in f10.items():
        if category.startswith("_") or not text:
            continue
        safe_cat = category.replace("/", "_").replace("\\", "_")
        path = stock_dir / f"{safe_cat}_{save_date}.txt"
        path.write_text(text, encoding="utf-8")
        rows.append(
            {
                "code": code,
                "category": category,
                "date": save_date,
                "length": len(text),
                "hash": hashlib.md5(text.encode("utf-8")).hexdigest(),
                "content_path": str(path),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return rows


# ──────────────────────── public API ────────────────────────


def update_f10(
    news_data_root: Union[str, Path],
    parquet_root: Union[str, Path],
    codes: List[str],
    categories: Optional[List[str]] = None,
    save_date: Optional[str] = None,
    skip_if_hash_same: bool = True,
    skip_if_scanned_today: bool = True,
    reconnect_every: int = 150,
    checkpoint_every: int = 200,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Fetch TDX F10 events for ``codes``, save raw ``.txt`` + update index parquet.

    Parameters
    ----------
    news_data_root
        Root for raw text files. ``tdx_f10/{code}/{cat}_{date}.txt`` lives under it.
    parquet_root
        Root for non-time-series Parquet. ``tdx_f10_index.parquet`` lives directly inside.
    codes
        Qlib-format codes (e.g. ``["SH600519", "SZ000858"]``).
    categories
        Which F10 categories to fetch. Defaults to ``KEY_CATEGORIES``.
    save_date
        Date stamp for filenames (``YYYYMMDD``). Defaults to today.
    skip_if_hash_same
        Drop rows whose text hash matches the previously-indexed copy (saves
        index churn when content unchanged).
    skip_if_scanned_today
        If a code already has rows with ``date == save_date`` in the index, skip
        it entirely. Enables idempotent re-runs after partial failures.
    reconnect_every
        Reconnect to TDX every N codes to avoid long-lived connection drops.
    checkpoint_every
        Persist intermediate index every N codes (crash safety).
    dry_run
        Plan-only mode. Returns counts + sample codes, no network calls.
    progress
        Print progress lines every 20 codes.

    Returns
    -------
    dict
        ``{total, ok, skipped, failed, new_rows, index_path}``. Matches
        the shape of ``pytdx_kline.update_daily_batch`` for CLI uniformity.
    """
    save_date = save_date or datetime.now().strftime("%Y%m%d")
    news_data_root = Path(news_data_root)
    parquet_root = Path(parquet_root)
    f10_dir = news_data_root / "tdx_f10"
    index_path = parquet_root / "tdx_f10_index.parquet"

    if dry_run:
        return {
            "total": len(codes),
            "ok": 0,
            "skipped": 0,
            "failed": 0,
            "dry_run": True,
            "plan": (
                f"Would fetch {len(categories or KEY_CATEGORIES)} F10 categories "
                f"for {len(codes)} codes → {f10_dir}; index → {index_path}"
            ),
            "sample_codes": codes[:5],
        }

    f10_dir.mkdir(parents=True, exist_ok=True)
    parquet_root.mkdir(parents=True, exist_ok=True)

    # Load existing index for hash dedup + today-already-scanned set
    old_idx = pd.DataFrame()
    if index_path.exists():
        try:
            old_idx = pd.read_parquet(index_path)
        except Exception:
            pass
    old_hash: dict = {}
    scanned_today: set = set()
    if len(old_idx) > 0 and "hash" in old_idx.columns:
        for _, r in old_idx.iterrows():
            old_hash[(r["code"], r["category"])] = r["hash"]
            if str(r.get("date", "")) == save_date:
                scanned_today.add(r["code"])

    if progress:
        print(f"[F10] connecting to TDX...", flush=True)
    api = _connect_tdx()
    if progress:
        print(f"[F10] connected, fetching {len(codes)} codes", flush=True)

    all_rows: list[dict] = []
    ok = failed = skipped = err_count = 0

    def _flush_checkpoint() -> None:
        if not all_rows:
            return
        new_idx = pd.DataFrame(all_rows)
        if len(old_idx) > 0:
            combined = pd.concat([old_idx, new_idx], ignore_index=True)
            combined = combined.sort_values("updated_at").drop_duplicates(
                subset=["code", "category"], keep="last"
            )
        else:
            combined = new_idx
        combined.to_parquet(index_path, index=False)

    for i, code in enumerate(codes):
        if skip_if_scanned_today and code in scanned_today:
            skipped += 1
            continue
        if i > 0 and i % reconnect_every == 0:
            try:
                api.disconnect()
            except Exception:
                pass
            try:
                api = _connect_tdx()
                if progress:
                    print(f"  [reconnect] @ {i}/{len(codes)}", flush=True)
            except Exception as e:
                if progress:
                    print(f"  [reconnect FAIL] {e} — terminating early", flush=True)
                break

        try:
            f10 = fetch_f10_one(api, code, categories)
            rows = _save_f10(code, f10, f10_dir, save_date)
            if skip_if_hash_same:
                rows = [r for r in rows if old_hash.get((r["code"], r["category"])) != r["hash"]]
            all_rows.extend(rows)
            ok += 1
        except Exception as e:
            failed += 1
            err_count += 1
            if progress and err_count <= 5:
                print(f"  [err] {code}: {e}", flush=True)
            # Auto-reconnect on persistent errors
            if err_count > 0 and err_count % 10 == 0:
                try:
                    api.disconnect()
                    api = _connect_tdx()
                    if progress:
                        print(f"  [err-reconnect] after {err_count} errors", flush=True)
                except Exception:
                    pass

        if progress and (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(codes)} ok={ok} skip={skipped} fail={failed}", flush=True)
        if (i + 1) % checkpoint_every == 0:
            _flush_checkpoint()

        time.sleep(0.1)

    try:
        api.disconnect()
    except Exception:
        pass

    _flush_checkpoint()

    return {
        "total": len(codes),
        "ok": ok,
        "skipped": skipped,
        "failed": failed,
        "new_rows": len(all_rows),
        "index_path": str(index_path),
    }


# ──────────────────────── universe resolution ────────────────────────


_UNIVERSE_INDEX_CODE = {
    "csi300": "000300.SH",
    "csi500": "000905.SH",
    "csi800": "000906.SH",
    "csi1000": "000852.SH",
}

# Constituent-code column candidates. The research-lab parquet (G:/stocks) ships
# Chinese headers (成分券代码); a bootstrapped dataset bundle may use English
# stock_code. Try known names in order, then a 6-digit-code heuristic.
_CODE_COL_CANDIDATES = ("stock_code", "成分券代码", "con_code", "code", "ts_code")


def _prefix_code(code: str) -> str:
    """Normalise a constituent code to qlib form (SH/SZ/BJ + 6 digits).
    Bare ``600000`` → ``SH600000``; already-prefixed codes pass through; an
    unrecognised leading digit is returned normalised but unprefixed."""
    c = str(code).strip().upper()
    if c[:2] in ("SH", "SZ", "BJ"):
        return c
    digits = c.split(".")[0]
    if not (len(digits) == 6 and digits.isdigit()):
        return c
    if digits[0] == "6":
        return "SH" + digits
    if digits[0] in ("0", "3"):
        return "SZ" + digits
    if digits[0] in ("4", "8"):
        return "BJ" + digits
    return c


def _detect_code_col(df: pd.DataFrame) -> Optional[str]:
    """Locate the constituent stock-code column across known schemas."""
    for name in _CODE_COL_CANDIDATES:
        if name in df.columns:
            return name
    best, best_n = None, -1  # heuristic: non-index col mostly 6-digit codes, max uniques
    for col in df.columns:
        if col in ("index_code", "index_name"):
            continue
        s = df[col].astype(str).str.strip()
        if len(s) and s.str.fullmatch(r"\d{6}(\.\w+)?").mean() > 0.8:
            n = s.nunique()
            if n > best_n:
                best, best_n = col, n
    return best


def resolve_universe(
    parquet_root: Union[str, Path],
    universe: str = "csi500",
) -> List[str]:
    """Resolve a universe label to a list of qlib codes (SH/SZ/BJ-prefixed).

    Supported labels: ``csi300`` / ``csi500`` / ``csi800`` / ``csi1000`` / ``all``.
    Membership is matched on the parquet's ``index_name`` column (clean English
    label) when present, else on ``index_code`` via ``_UNIVERSE_INDEX_CODE`` (with
    or without exchange suffix). The constituent-code column is auto-detected
    (Chinese 成分券代码 or English stock_code) and bare codes are exchange-prefixed.
    ``all`` returns every distinct constituent across the indices in the parquet.

    Requires ``parquet_root / index_constituents.parquet`` to be present
    (populated during ``fa init`` from the dataset bundle). If missing,
    raises ``FileNotFoundError`` — caller should fall back to
    ``--codes`` or run ``fa data bootstrap`` first.
    """
    parquet_root = Path(parquet_root)
    idx_path = parquet_root / "index_constituents.parquet"
    if not idx_path.exists():
        raise FileNotFoundError(
            f"index_constituents.parquet not found at {idx_path}. "
            f"Run `fa init` (or `fa data bootstrap`) to populate, or pass `--codes` directly."
        )
    df = pd.read_parquet(idx_path)

    code_col = _detect_code_col(df)
    if code_col is None:
        raise ValueError(
            f"index_constituents.parquet: no constituent-code column found "
            f"(looked for {_CODE_COL_CANDIDATES} + 6-digit heuristic), got {list(df.columns)}"
        )

    if universe == "all":
        codes = df[code_col]
    else:
        mask = None
        if "index_name" in df.columns:
            m = df["index_name"].astype(str).str.strip().str.lower() == universe.lower()
            if m.any():
                mask = m
        if mask is None and "index_code" in df.columns:
            target = _UNIVERSE_INDEX_CODE.get(universe)
            if target:
                ic = df["index_code"].astype(str).str.strip().str.upper()
                m = (ic == target.upper()) | (ic == target.split(".")[0].upper())
                if m.any():
                    mask = m
        if mask is None:
            if universe not in _UNIVERSE_INDEX_CODE:
                raise ValueError(
                    f"Unknown universe '{universe}'. Supported: "
                    f"{' / '.join(sorted(_UNIVERSE_INDEX_CODE))} / all (or pass `--codes` directly)."
                )
            return []  # known label but absent from this parquet
        codes = df[mask][code_col]

    return sorted({
        _prefix_code(c) for c in codes.astype(str)
        if str(c).strip() and str(c).strip().lower() != "nan"
    })
