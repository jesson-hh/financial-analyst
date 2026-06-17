"""每日信号包 (daily_signal_pack.parquet) 读取 — 交易盯盘台 P2.

research 端 (收盘后) 写, fa 端 (盘前/盘中) 读. 一行一股一日, append-only.
镜像 watch/store.py 的 parquet-root 解析: 与 watch_recommendations.parquet 同目录.
缺文件/坏 schema/空表 一律返回带契约列的空 DataFrame (容错, 同 B1/B2 纪律).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import pandas as pd

log = logging.getLogger(__name__)

#: 列序是契约的一部分, 不要重排. (P2 只 populate 前段 + f10_*, 其余可空)
DAILY_PACK_COLUMNS = [
    "code", "date",
    "fm_cluster", "fm_pct", "combo_pct",
    "f10_game_capital_net", "f10_event_flag", "f10_severity",
    "lgb_rank", "lgb_pct", "v4_rating", "v4_score",
    "board_total", "mainline_state", "report_summary",
]

_DEFAULT_FILENAME = "daily_signal_pack.parquet"


def default_signal_pack_path() -> Path:
    """与 watch/store.py 的推荐日志同目录 (fa 共享 parquet root)."""
    from financial_analyst.watch.store import default_recs_path

    return default_recs_path().parent / _DEFAULT_FILENAME


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_PACK_COLUMNS)


def load_signal_pack(path: Optional[Union[str, Path]] = None,
                     date: Optional[str] = None) -> pd.DataFrame:
    """读 pack. date=None → 取 pack 内最新日期那一批; 指定 date → 该日.

    缺文件/读失败/空 → 带契约列的空 DataFrame (调用方据 .empty 判定).
    """
    p = Path(path) if path is not None else default_signal_pack_path()
    if not p.exists():
        return _empty()
    try:
        df = pd.read_parquet(p)
    except Exception as exc:                       # 坏 parquet 不抛
        log.warning("load_signal_pack: 读 %s 失败: %s", p, exc)
        return _empty()
    if df.empty or "date" not in df.columns:
        return _empty()
    tgt = date or df["date"].max()
    out = df[df["date"] == tgt].copy()
    # 补齐缺列 (老 pack 可能没有新加的可空列)
    for c in DAILY_PACK_COLUMNS:
        if c not in out.columns:
            out[c] = None
    return out[DAILY_PACK_COLUMNS]


def pack_to_pool(pack: pd.DataFrame, top_n: int = 20,
                 min_combo_pct: float = 70.0, max_severity: int = 1):
    """从 pack 选盯盘池候选: 剔除 severity>max_severity 的负向票,
    combo_pct (无则 fm_pct) 达标, 按该列降序取 top_n → [WatchItem]."""
    from financial_analyst.watch.models import WatchItem
    if pack is None or pack.empty:
        return []
    df = pack.copy()
    sev = pd.to_numeric(df.get("f10_severity"), errors="coerce").fillna(0)
    df = df[sev <= max_severity]
    rank_col = "combo_pct" if df["combo_pct"].notna().any() else "fm_pct"
    score = pd.to_numeric(df[rank_col], errors="coerce")
    df = df.assign(_s=score)
    df = df[df["_s"] >= min_combo_pct].sort_values("_s", ascending=False)
    out = []
    for _, r in df.head(top_n).iterrows():
        out.append(WatchItem(code=str(r["code"])))
    return out


def pack_prior_for(code: str, pack: pd.DataFrame) -> dict:
    """取单股的 EOD 研究底牌 (P3 advisor 注入用). 无该 code → {}."""
    if pack is None or pack.empty:
        return {}
    rows = pack[pack["code"] == code]
    if rows.empty:
        return {}
    r = rows.iloc[0].to_dict()
    # 去掉 NaN/None, 只留有值的字段
    return {k: v for k, v in r.items()
            if v is not None and not (isinstance(v, float) and pd.isna(v))}


def format_eod_prior_context(prior: dict) -> str:
    """把 pack_prior_for(code) 的单股底牌渲染成盘中 advisor 的「今日 EOD 研究底牌」块.

    只渲染存在且有意义的字段 (prior 已剥 NaN); 无任何信号字段 (仅 code/date 或空)
    → 返回 '' (不注入, 字节不变纪律, 同 knowledge='').
    P2.5/P3 填 lgb/v4/board/主线/report 后自动进块 (按字段存在性渲染, 无需再改).
    """
    if not prior:
        return ""
    lines = []
    fc = prior.get("fm_cluster")
    if fc is not None:
        lines.append(f"- FM 簇: c{fc}")
    if prior.get("combo_pct") is not None:
        lines.append(f"- FM×rev20 combo 分位: {float(prior['combo_pct']):.0f}")
    if prior.get("fm_pct") is not None:
        lines.append(f"- FM 分位: {float(prior['fm_pct']):.0f}")
    if prior.get("lgb_rank") is not None:
        lp = prior.get("lgb_pct")
        lp_s = f" (分位 {float(lp) * 100:.0f}%)" if lp is not None else ""
        lines.append(f"- LGB 排名: {prior['lgb_rank']}{lp_s}")
    if prior.get("v4_rating") is not None:
        vs = prior.get("v4_score")
        vs_s = f" / 分 {vs}" if vs is not None else ""
        lines.append(f"- v4 评级: {prior['v4_rating']}{vs_s}")
    if prior.get("board_total") is not None:
        lines.append(f"- 首板 total: {prior['board_total']}")
    if prior.get("mainline_state") is not None:
        lines.append(f"- 主线雷达: {prior['mainline_state']}")
    gc = prior.get("f10_game_capital_net")
    if gc is not None:
        lines.append(f"- 知名游资 180d 净额: {gc} 亿")
    sev = prior.get("f10_severity")
    try:
        if sev is not None and float(sev) >= 1:
            lines.append(f"- ⚠ 近 7 日负向事件 severity: {int(float(sev))}")
    except (TypeError, ValueError):
        pass
    rs = prior.get("report_summary")
    if rs:
        lines.append(f"- 研报结论: {str(rs)[:120]}")
    if not lines:
        return ""
    return ("## 今日 EOD 研究底牌 (收盘后批量算, 盘中作先验, 与实时盘口/触发融合)\n"
            + "\n".join(lines))
