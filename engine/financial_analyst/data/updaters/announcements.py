"""Cninfo (巨潮) announcement index updater — zero token.

Pulls per-stock announcement metadata (title + type + date + cninfo URL)
into ``announcements.parquet``. **Not the announcement PDF body** — just the
index, so the agent layer can decide which PDFs are worth fetching.

Schema (one row per (code, announcement_id)):

    code              str    qlib upper-case
    announcement_id   str    cninfo internal id (primary key)
    title             str    公告标题
    type              str    公告分类 (e.g. "年度报告" / "关联交易")
    date              date   披露日
    url               str    cninfo detail page URL

Cninfo endpoint requires an ``orgId`` prefix in the form-data ``stock`` param
(the format changed in early 2026 — old ``code,plate`` no longer works).

Inspired by ``simonlin1212/a-stock-data`` v3.1 endpoint discovery.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

import pandas as pd
import requests


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_HEADERS = {
    "User-Agent": _UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.cninfo.com.cn/new/disclosure",
    "Origin": "https://www.cninfo.com.cn",
}

# cninfo authoritative stock-list endpoints. The naïve ``gssh0/gssz0/gsbj0``
# prefix+code formula only works for SSE main board + a subset of SZSE main —
# 深创业板 returns ``GD165627``-style ids, 科创板 + 北交所 use ``9900xxxxxx``.
# We must fetch the authoritative mapping once and lookup.
_STOCK_LIST_URLS = [
    # Misleadingly named — actually contains 深主板 + 创业板 + 科创板 + 沪主板 (~6200 stocks)
    "http://www.cninfo.com.cn/new/data/szse_stock.json",
    # 北交所 (separately maintained, ~570 stocks)
    "http://www.cninfo.com.cn/new/data/bj_stock.json",
]

# Cache: 6-digit code → orgId. Built lazily on first call, reused across calls
# in one process. ``_load_org_id_map`` clears + reloads on demand.
_ORG_ID_CACHE: dict[str, str] = {}

_DEDUP_COLS = ["code", "announcement_id"]


def _qlib_to_dcode(code: str) -> str:
    """Strip prefix → 6-digit."""
    c = code.strip().upper()
    return c[2:] if c[:2] in ("SH", "SZ", "BJ") else c


def _load_org_id_map(force: bool = False,
                     session: requests.Session = None,
                     timeout: float = 15.0) -> dict[str, str]:
    """Fetch + cache cninfo's authoritative 6-digit → orgId mapping.

    ~7000 entries total (深+沪+创业+科创+北交). One-time ~600 KB download per
    process. Returns the in-memory dict so callers can lookup orgs by code.
    """
    if _ORG_ID_CACHE and not force:
        return _ORG_ID_CACHE
    sess = session or requests
    for url in _STOCK_LIST_URLS:
        try:
            r = sess.get(url, headers={"User-Agent": _UA,
                                       "Referer": "https://www.cninfo.com.cn/new/disclosure"},
                         timeout=timeout)
            if r.status_code != 200:
                continue
            d = r.json()
            sl = d.get("stockList", d) if isinstance(d, dict) else d
            if not isinstance(sl, list):
                continue
            for item in sl:
                code = (item.get("code") or "").strip()
                org = (item.get("orgId") or "").strip()
                if code and org:
                    _ORG_ID_CACHE[code] = org
        except (requests.exceptions.RequestException, ValueError):
            continue
    return _ORG_ID_CACHE


def _lookup_org_id(code6: str, session: requests.Session = None) -> str:
    """Resolve 6-digit code to orgId via cached stock list.

    Falls back to the legacy prefix formula (``gssh0/gssz0/gsbj0``) only if
    lookup empty — for backward compatibility on old SSE main board codes
    where the formula does match.
    """
    cache = _load_org_id_map(session=session)
    if code6 in cache:
        return cache[code6]
    # Fallback formula (works for some SSE/SZSE main-board codes)
    if code6.startswith("6"):
        return f"gssh0{code6}"
    if code6[:1] in ("4", "8"):
        return f"gsbj0{code6}"
    return f"gssz0{code6}"


def _ts_to_date(ts) -> str:
    """Cninfo announcementTime is Unix ms int."""
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def _fetch_one(code: str, page_size: int = 30,
               session: requests.Session = None,
               timeout: float = 15.0) -> pd.DataFrame:
    """One stock, latest ``page_size`` announcements."""
    qcode = code.strip().upper()
    code6 = _qlib_to_dcode(qcode)
    org_id = _lookup_org_id(code6, session=session)
    payload = {
        "stock": f"{code6},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    sess = session or requests
    try:
        r = sess.post(_QUERY_URL, data=payload, headers=_HEADERS, timeout=timeout)
        if r.status_code != 200:
            return pd.DataFrame()
        d = r.json()
    except (requests.exceptions.RequestException, ValueError):
        return pd.DataFrame()

    items = d.get("announcements") or []
    if not items:
        return pd.DataFrame()
    out = []
    for it in items:
        ann_id = it.get("announcementId")
        if not ann_id:
            continue
        out.append({
            "code": qcode,
            "announcement_id": str(ann_id),
            "title": it.get("announcementTitle", "") or "",
            "type": it.get("announcementTypeName", "") or "",
            "date": _ts_to_date(it.get("announcementTime")),
            "url": (f"https://www.cninfo.com.cn/new/disclosure/detail"
                    f"?annoId={ann_id}"),
        })
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df


def update_announcements(
    parquet_root: Union[str, Path],
    codes: Iterable[str],
    page_size: int = 30,
    rate_sleep: float = 0.30,
    checkpoint_every: int = 100,
    dry_run: bool = False,
    progress: bool = True,
) -> dict:
    """Pull latest ``page_size`` announcement headers per stock.

    Cninfo is slower than 东财 datacenter (~0.5-1s per request), so
    ``rate_sleep`` defaults higher than other updaters. Full A-share (5500)
    runs in ~1-2 hours; csi300 in ~5 min.
    """
    codes_list = [c.strip().upper() for c in codes if c and c.strip()]
    parquet_root = Path(parquet_root)
    parquet_path = parquet_root / "announcements.parquet"

    if dry_run:
        n = 0
        if parquet_path.exists():
            try:
                n = len(pd.read_parquet(parquet_path, columns=["code"]))
            except Exception:
                pass
        return {
            "dry_run": True, "ok": True,
            "codes_total": len(codes_list), "codes_ok": 0,
            "rows_new": 0, "rows_total": n,
            "plan": (f"Would pull last {page_size} cninfo announcements per "
                     f"stock for {len(codes_list)} codes → {parquet_path}. "
                     f"ETA ~{len(codes_list) * (rate_sleep + 0.5):.0f}s."),
            "parquet_path": str(parquet_path),
        }

    if not codes_list:
        return {"ok": True, "codes_total": 0, "codes_ok": 0, "codes_failed": 0,
                "rows_new": 0, "rows_total": 0,
                "parquet_path": str(parquet_path)}

    parquet_root.mkdir(parents=True, exist_ok=True)
    old_df = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()

    if progress:
        print(f"[announcements] fetching {len(codes_list)} codes ...", flush=True)

    new_frames: list[pd.DataFrame] = []
    ok = failed = 0
    errors: list[dict] = []
    t0 = time.time()

    with requests.Session() as sess:
        for i, qcode in enumerate(codes_list, 1):
            df = _fetch_one(qcode, page_size=page_size, session=sess)
            if len(df) > 0:
                new_frames.append(df)
                ok += 1
            else:
                failed += 1
                if len(errors) < 20:
                    errors.append({"code": qcode, "reason": "empty"})

            if progress and (i % 50 == 0 or i == len(codes_list)):
                elapsed = time.time() - t0
                eta = (len(codes_list) - i) / i * elapsed if i > 0 else 0
                rows_so_far = sum(len(f) for f in new_frames)
                print(f"  {i}/{len(codes_list)} ok={ok} fail={failed} "
                      f"rows={rows_so_far} ETA {eta:.0f}s", flush=True)

            if (i % checkpoint_every == 0) and new_frames:
                old_df = _flush(new_frames, old_df, parquet_path)
                new_frames = []
            time.sleep(rate_sleep)

    if new_frames:
        old_df = _flush(new_frames, old_df, parquet_path)

    try:
        final = pd.read_parquet(parquet_path)
        n_total = len(final)
        date_range = (f"{final['date'].min()} → {final['date'].max()}"
                      if "date" in final.columns and n_total > 0 else "n/a")
    except Exception:
        n_total = 0
        date_range = "n/a"

    if progress:
        print(f"[announcements ✓] ok={ok}/{len(codes_list)} fail={failed} "
              f"rows={n_total} ({date_range}) 耗时 {time.time()-t0:.1f}s",
              flush=True)

    return {
        "ok": ok > 0,
        "codes_total": len(codes_list), "codes_ok": ok, "codes_failed": failed,
        "rows_total": n_total, "date_range": date_range,
        "errors": errors, "parquet_path": str(parquet_path),
    }


def _flush(new_frames: list[pd.DataFrame], old_df: pd.DataFrame,
           path: Path) -> pd.DataFrame:
    new_df = pd.concat(new_frames, ignore_index=True)
    combined = (pd.concat([old_df, new_df], ignore_index=True)
                if len(old_df) > 0 else new_df)
    combined = (combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
                        .sort_values(["code", "date"]).reset_index(drop=True))
    tmp = path.with_suffix(".tmp")
    combined.to_parquet(tmp, index=False)
    if path.exists():
        path.unlink()
    tmp.rename(path)
    return combined
