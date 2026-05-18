"""Mainline classifier — pre-computed sector panel -> status groups.

Reads a parquet with columns: datetime, industry, status, ex_60d, ex_20d,
ex_10d, top10_ratio_60d, lu_count_60d_sum, lu_max_mv_60d_mean, ...

Outputs structured data with:
- status_groups: dict of status -> list of industries (top 20 by ex_60d)
- just_become_mainline: industries that switched initiation -> mainline TODAY (★ golden signal)
- just_become_decay: mainline -> decay switches (pullback candidates)
- meta: as_of date + alpha summary numbers
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent


DEFAULT_PANEL_PATH = "G:/stocks/strategy/mainline/monthly_mainlines_panel.parquet"

STATUS_ORDER = ["mainline", "revival", "initiation", "decay", "cold", "neutral"]

COLS_SHOW = [
    "industry", "status",
    "ex_60d", "ex_20d", "ex_10d",
    "top10_ratio_60d", "lu_count_60d_sum", "lu_max_mv_60d_mean",
    "just_switched", "prev_status",
]


class IndustryRecord(BaseModel):
    model_config = {"extra": "allow"}
    industry: str
    status: str
    ex_60d: Optional[float] = None
    ex_20d: Optional[float] = None
    ex_10d: Optional[float] = None
    top10_ratio_60d: Optional[float] = None
    lu_count_60d_sum: Optional[float] = None
    lu_max_mv_60d_mean: Optional[float] = None
    just_switched: bool = False
    prev_status: Optional[str] = None


class MainlineOutput(BaseModel):
    as_of: str
    panel_path: str
    status_groups: Dict[str, List[IndustryRecord]] = {}
    just_become_mainline: List[IndustryRecord] = []   # ★ golden signal
    just_become_decay: List[IndustryRecord] = []      # pullback candidates
    alpha_summary: Dict[str, str] = {}


class MainlineClassifier(SubAgent[MainlineOutput]):
    NAME = "mainline-classifier"
    OUTPUT_SCHEMA = MainlineOutput

    def __init__(self, memory_root, panel_path: Optional[str] = None):
        super().__init__(memory_root=memory_root)
        self._panel_path = panel_path or os.environ.get("FA_MAINLINE_PANEL", DEFAULT_PANEL_PATH)

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        asof = inputs.get("asof_date")
        path = Path(inputs.get("panel_path") or self._panel_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Mainline panel not found at {path}. Set FA_MAINLINE_PANEL or pass panel_path. "
                f"Compute it with G:\\stocks/strategy/mainline/compute_monthly_mainlines.py first."
            )

        panel = pd.read_parquet(path)
        panel["datetime"] = pd.to_datetime(panel["datetime"])
        avail = sorted(panel["datetime"].unique())
        if not avail:
            raise ValueError(f"Empty panel at {path}")

        if asof:
            target = pd.Timestamp(asof)
            if target not in avail:
                target = max(d for d in avail if d <= target)
        else:
            target = max(avail)

        pos = avail.index(target)
        prev = avail[pos - 1] if pos > 0 else None

        today = panel[panel["datetime"] == target].copy()
        yesterday = panel[panel["datetime"] == prev].copy() if prev is not None else pd.DataFrame()

        if len(yesterday) > 0:
            yest_status = yesterday.set_index("industry")["status"]
            today["prev_status"] = today["industry"].map(yest_status)
            today["just_switched"] = today["prev_status"] != today["status"]
        else:
            today["prev_status"] = None
            today["just_switched"] = False

        status_groups: Dict[str, List[Dict]] = {}
        for status in STATUS_ORDER:
            sub = today[today["status"] == status].sort_values("ex_60d", ascending=False).head(20)
            available_cols = [c for c in COLS_SHOW if c in sub.columns]
            status_groups[status] = sub[available_cols].round(2).to_dict("records")

        init_to_main = today[(today["prev_status"] == "initiation") & (today["status"] == "mainline")]
        main_to_decay = today[(today["prev_status"] == "mainline") & (today["status"] == "decay")]
        available = [c for c in COLS_SHOW if c in today.columns]

        return {
            "as_of": target.strftime("%Y-%m-%d"),
            "panel_path": str(path),
            "status_groups": status_groups,
            "just_become_mainline": init_to_main[available].round(2).to_dict("records"),
            "just_become_decay": main_to_decay[available].round(2).to_dict("records"),
            "alpha_summary": {
                "mainline": "fwd_60d +4.05pp 胜率 68% (n=364) — 真主线 ★★★",
                "initiation": "fwd_60d +1.43pp 胜率 57% — 启动期, 等切换 mainline 加仓",
                "revival": "60d 仍正 + 近 20d 深回调 — V4 立讯模式候选",
                "decay": "v1 误名: 实际是主线短期回调点 (revival 候选), 不要恐慌",
                "cold": "fwd_60d -0.96pp 胜率 41% — 真冷门, 回避",
                "init_to_main_switch": "★金信号: fwd_60d +5.54pp 胜率 87% (n=15)",
                "anti_signal": "⚠ mainline + lu_max_mv≥500亿 → fwd_60d -1.5pp (主升后期, 不追高)",
            },
        }
