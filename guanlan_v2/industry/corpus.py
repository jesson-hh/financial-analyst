# -*- coding: utf-8 -*-
"""跨仓只读 G:\\stocks text_source(研报已解析库)。

env GL_TEXT_SOURCE_ROOT 可覆盖(仿 GL_F10_ROOT 先例);一切失败诚实 ok:False。
PIT 红线:只按 publish_ts 增量,不回改历史。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

_DEFAULT_ROOT = r"G:/stocks/stock_data/text_source"


def text_source_root() -> Path:
    return Path(os.environ.get("GL_TEXT_SOURCE_ROOT") or _DEFAULT_ROOT)


def _load_documents():
    import pandas as pd
    p = text_source_root() / "documents.parquet"
    if not p.exists():
        raise FileNotFoundError(f"documents.parquet 不存在: {p}")
    return pd.read_parquet(p)


def scan_new_docs(watermark: Optional[str], pool_codes: set, keywords: Iterable[str],
                  limit: Optional[int] = None) -> dict:
    try:
        df = _load_documents()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docs": [], "reason": f"语料库不可读: {exc}", "skipped_unparsed": 0}
    try:
        df = df.copy()
        df["publish_ts"] = df["publish_ts"].astype(str).str[:10]
        if watermark:
            df = df[df["publish_ts"] > str(watermark)[:10]]
        unparsed = df[(df.get("status") != "parsed") | (df.get("text_chars", 0) == 0)]
        df = df.drop(index=unparsed.index)
        kws = [k for k in (keywords or []) if k]

        def _hit(row) -> bool:
            if row.get("doc_type") == "industry_research":
                return True
            codes = {c.strip() for c in str(row.get("stock_codes") or "").replace(";", ",").split(",") if c.strip()}
            if codes & pool_codes:
                return True
            title = str(row.get("title") or "")
            return any(k in title for k in kws)

        keep = df[df.apply(_hit, axis=1)].sort_values("publish_ts")
        if limit:
            keep = keep.head(int(limit))
        cols = ["doc_id", "doc_type", "title", "org", "publish_ts", "text_path", "stock_codes"]
        docs = []
        for _, row in keep.iterrows():
            docs.append({c: (None if c not in row or row[c] is None else str(row[c])) for c in cols})
        return {"ok": True, "docs": docs, "reason": None, "skipped_unparsed": int(len(unparsed))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docs": [], "reason": f"扫描失败: {exc}", "skipped_unparsed": 0}


def read_doc_text(text_path: str, max_chars: int = 20000) -> str:
    txt = Path(text_path).read_text(encoding="utf-8", errors="replace")
    if len(txt) <= max_chars:
        return txt
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return txt[:head] + "\n…[中略]…\n" + txt[-tail:]


def corpus_freshness() -> dict:
    try:
        df = _load_documents()
        ts = df["publish_ts"].astype(str).str[:10]
        n_ind = int((df.get("doc_type") == "industry_research").sum())
        return {"ok": True, "latest_publish_ts": str(ts.max()), "n_docs": int(len(df)),
                "n_industry": n_ind, "reason": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latest_publish_ts": None, "n_docs": None, "n_industry": None,
                "reason": f"语料库不可读: {exc}"}
