# -*- coding: utf-8 -*-
"""跨仓只读研报语料 —— 产业链种子包优先(2026-07-03 语料层重建后切换)。

正本入口(见 G:/stocks/strategy/research/2026-07-03-research-report-resource-overview.md):
- 种子包: wiki_source/raw/chain_logic/ai_industry_strong_3y/ai_chain_report_seed.parquet
  (近3年·标题强命中·机构研报口径,已 join catalog 元数据与 text_source 正文路径)
- 种子包是六条链合集(matched_themes),按框架 YAML meta.corpus.themes 白名单过滤,
  别让 LLM 花钱读锂电研报再输出空 JSON。
- 只读解析 txt(text_status=='parsed'),绝不碰 raw PDF(总览 PIT 规则)。

env GL_CHAIN_SEED 可覆盖种子包路径;一切失败诚实 ok:False。
PIT 红线:只按 publish_ts 增量,不回改历史。
水位含当日(>=,防同日晚回填漏扫);重复靠 exclude_doc_ids(已抽取集)剔除。
首跑无水位 → 回填窗 backfill_days(默认35天=聚合窗30天+缓冲),防全量3年烧钱。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

_DEFAULT_SEED = r"G:/stocks/stock_data/wiki_source/raw/chain_logic/ai_industry_strong_3y/ai_chain_report_seed.parquet"

# 列名映射:seed parquet → 下游契约(llmx/store 的 doc dict 键)
#   institution→org, report_kind→doc_type, text_status→status
_REQUIRED = {"doc_id", "report_kind", "title", "institution", "publish_ts",
             "text_path", "stock_codes", "text_status", "text_chars"}


def seed_path(explicit: Optional[str] = None) -> Path:
    """优先级:env GL_CHAIN_SEED(应急覆盖·测试隔离)> 框架 YAML meta.corpus.seed > 默认。"""
    return Path(os.environ.get("GL_CHAIN_SEED") or explicit or _DEFAULT_SEED)


def _load_seed(explicit: Optional[str] = None):
    import pandas as pd
    p = seed_path(explicit)
    if not p.exists():
        raise FileNotFoundError(f"种子包不存在: {p}")
    return pd.read_parquet(p)


def _themes_of(v) -> set:
    """matched_themes 单元格 → set(容忍 None/list/ndarray/str)。"""
    if v is None:
        return set()
    if isinstance(v, str):
        return {v} if v else set()
    try:
        return {str(x) for x in v}
    except TypeError:
        return set()


def scan_new_docs(watermark: Optional[str], pool_codes: set, keywords: Iterable[str],
                  limit: Optional[int] = None, exclude_doc_ids: Optional[set] = None,
                  seed: Optional[str] = None, themes: Optional[list] = None,
                  backfill_days: int = 35) -> dict:
    """扫描种子包新研报。pool_codes/keywords 保留签名兼容(种子包已按标题强命中框定范围,
    不再二次过滤);themes 为框架 YAML meta.corpus.themes 白名单(空=不过滤)。"""
    del pool_codes, keywords  # 种子包时代不再使用;保留参数免动 ingest 调用面
    try:
        df = _load_seed(seed)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docs": [], "reason": f"语料库不可读: {exc}", "skipped_unparsed": 0}
    try:
        import pandas as pd
        df = df.copy()
        # 校验必需列,防止 None 广播导致静默清空(T3 终审教训)
        missing = sorted(_REQUIRED - set(df.columns))
        if missing:
            return {"ok": False, "docs": [],
                    "reason": f"种子包缺列: {','.join(missing)}(schema 漂移,需在 corpus 加 rename 适配)",
                    "skipped_unparsed": 0}
        df["publish_ts"] = df["publish_ts"].astype(str).str[:10]
        # 水位:显式水位 >= 含当日;无水位 → 回填窗(防首跑全量3年)
        if watermark:
            cutoff = str(watermark)[:10]
        else:
            cutoff = str((pd.Timestamp.now() - pd.Timedelta(days=int(backfill_days))).date())
        df = df[df["publish_ts"] >= cutoff]
        # theme 白名单(种子包=六条链合集,别让 LLM 读别条链的研报)
        if themes and "matched_themes" in df.columns:
            want = set(themes)
            df = df[df["matched_themes"].map(lambda v: bool(_themes_of(v) & want))]
        unparsed = df[(df["text_status"] != "parsed") | (df["text_chars"].fillna(0).astype(float) <= 0)
                      | df["text_path"].isna()]
        df = df.drop(index=unparsed.index)
        df = df.drop_duplicates(subset=["doc_id"])
        if exclude_doc_ids:
            df = df[~df["doc_id"].astype(str).isin(exclude_doc_ids)]
        keep = df.sort_values("publish_ts")
        if limit:
            keep = keep.head(int(limit))
        docs = []
        for _, row in keep.iterrows():
            docs.append({
                "doc_id": str(row["doc_id"]),
                "doc_type": str(row["report_kind"]),
                "title": None if row["title"] is None else str(row["title"]),
                "org": None if row["institution"] is None else str(row["institution"]),
                "publish_ts": str(row["publish_ts"]),
                "text_path": str(row["text_path"]),
                "stock_codes": None if row["stock_codes"] is None else str(row["stock_codes"]),
            })
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


def corpus_freshness(seed: Optional[str] = None, themes: Optional[list] = None) -> dict:
    try:
        df = _load_seed(seed)
        missing = sorted({"publish_ts", "report_kind", "text_status"} - set(df.columns))
        if missing:
            return {"ok": False, "latest_publish_ts": None, "n_docs": None, "n_industry": None,
                    "reason": f"种子包缺列: {','.join(missing)}(schema 漂移,需在 corpus 加 rename 适配)"}
        parsed = df[df["text_status"] == "parsed"]
        if themes and "matched_themes" in df.columns:
            want = set(themes)
            parsed = parsed[parsed["matched_themes"].map(lambda v: bool(_themes_of(v) & want))]
        ts = parsed["publish_ts"].astype(str).str[:10]
        n_ind = int((parsed["report_kind"] == "industry_research").sum())
        return {"ok": True, "latest_publish_ts": (str(ts.max()) if len(ts) else None),
                "n_docs": int(len(parsed)), "n_industry": n_ind, "reason": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latest_publish_ts": None, "n_docs": None, "n_industry": None,
                "reason": f"语料库不可读: {exc}"}
