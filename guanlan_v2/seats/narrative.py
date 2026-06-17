"""叙事卡池 + 按日 PIT 浮出 + 大盘日产物读取(纯逻辑,无 FastAPI 依赖)。

红线:绝不取 as_of/date > D(PIT);无料返回空/None,绝不补 mock。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Optional

DEFAULT_WINDOWS = {"研报": 60, "新闻": 10, "复盘": 30}
DEFAULT_K = 6


def _d(s) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s)[:10])
    except Exception:  # noqa: BLE001
        return None


def surface_narratives(pool, code, industry, as_of, k=DEFAULT_K, windows=None):
    """选出 as_of≤D、关联本票/行业、在各 kind 新近度窗口内的叙事卡,按 as_of 倒序取 topK。无料→[]。"""
    windows = windows or DEFAULT_WINDOWS
    dd = _d(as_of)
    if dd is None:
        return []
    code = str(code or "")
    out = []
    for c in pool or []:
        ad = _d(c.get("as_of"))
        if ad is None or ad > dd:           # PIT:无日期或未来 → 丢
            continue
        codes = [str(x) for x in (c.get("codes") or [])]
        rel = (code and code in codes) or (industry and c.get("industry") == industry)
        if not rel:
            continue
        if (dd - ad).days > windows.get(c.get("kind"), 30):   # 超新近度窗口 → 丢
            continue
        out.append(c)
    out.sort(key=lambda c: _d(c.get("as_of")) or _date.min, reverse=True)
    return out[: max(0, int(k))]


def build_pool(archive_cards, reports):
    """GL 档案叙事卡 + out/ 研报 → 统一池(丢无 as_of / 非 narrative)。"""
    pool = []
    for c in archive_cards or []:
        if not (c.get("type") == "card" and c.get("tier") == "narrative"):
            continue
        if not _d(c.get("as_of")):
            continue
        pool.append({
            "id": c.get("id"), "as_of": str(c.get("as_of"))[:10],
            "codes": [str(x) for x in (c.get("codes") or [])],
            "industry": c.get("industry") or "", "kind": c.get("kind") or "复盘",
            "title": c.get("title") or "", "insight": c.get("insight") or c.get("verdict") or "",
            "source": c.get("source") or {}, "path": c.get("path"),
        })
    for r in reports or []:
        if not _d(r.get("as_of")):
            continue
        pool.append({
            "id": r.get("id"), "as_of": str(r.get("as_of"))[:10],
            "codes": [str(x) for x in (r.get("codes") or [])],
            "industry": r.get("industry") or "", "kind": r.get("kind") or "研报",
            "title": r.get("title") or "", "insight": r.get("insight") or "",
            "source": {"from": r.get("from") or ""}, "path": r.get("path"),
        })
    return pool


def regime_asof(date, breadth_df):
    """大盘日产物(PIT):取 breadth_df 中 ≤date 末行拼点评;无则 None。绝不取 >date 行。"""
    if breadth_df is None or len(breadth_df) == 0:
        return None
    dd = _d(date)
    if dd is None:
        return None
    import pandas as pd
    ts = pd.Timestamp(dd)
    idx = breadth_df.index
    if getattr(idx, "tz", None) is not None:
        ts = ts.tz_localize(idx.tz)
    sub = breadth_df[idx <= ts]
    if len(sub) == 0:
        return None
    row = sub.iloc[-1]
    day = str(sub.index[-1].date())
    num_cols = [c for c in breadth_df.columns if pd.api.types.is_numeric_dtype(breadth_df[c])]
    if not num_cols:
        return f"大盘·{day}(无数值列)"
    parts = [f"{c}={row[c]:.3f}" for c in num_cols[:3] if pd.notna(row[c])]
    return (f"大盘·截至{day}:" + " ".join(parts)) if parts else f"大盘·{day}"
