"""Collectors for the ths-extra opencli plugin.

The plugin lives at ``opencli-plugin-ths-extra/`` in this repo. Install
once with::

    opencli plugin install file://G:\\financial-analyst\\opencli-plugin-ths-extra

Then these three collectors call into the plugin commands via
``run_opencli('ths-extra', '<command>', ...)``.

If opencli is missing OR the plugin isn't installed, ``run_opencli``
raises and we propagate so callers get a clear "install instructions"
error.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from financial_analyst.data.collectors.opencli.runner import (
    run_opencli, is_opencli_available,
)


def _plugin_dir() -> str:
    """Best-effort path to the bundled ths-extra plugin dir."""
    # .../src/financial_analyst/data/collectors/opencli/ths_extra.py
    #   → repo root is 5 parents up, plugin is a sibling of src/
    root = Path(__file__).resolve().parents[5]
    return str(root / "opencli-plugin-ths-extra")


def install_hint() -> str:
    """Single source of truth for the 'how to install ths-extra' message.
    Tools surface this verbatim when a ths-extra command fails."""
    return (
        f"opencli plugin install file://{_plugin_dir()}"
    )


class ThsExtraNotInstalled(RuntimeError):
    """Raised when opencli is present but the ths-extra plugin isn't,
    or opencli itself is missing. Carries a copy-pasteable fix."""


def _run_ths_extra(*args: str, timeout: int = 60):
    """Wrap run_opencli('ths-extra', ...) with friendly diagnostics.

    Distinguishes three failure modes:
      1. opencli not on PATH       → install npm pkg
      2. ths-extra command unknown → install the plugin
      3. other runtime errors      → propagate verbatim

    The ``is_opencli_available()`` check runs only on the error path so
    the happy path (and tests that mock ``run_opencli``) don't depend on
    opencli actually being installed.
    """
    try:
        return run_opencli("ths-extra", *args, timeout=timeout)
    except RuntimeError as exc:
        msg = str(exc).lower()
        # opencli itself missing?
        if not is_opencli_available() or "not found on path" in msg:
            raise ThsExtraNotInstalled(
                "opencli 未安装. 先装 opencli, 再装 ths-extra plugin:\n"
                "  npm install -g @jackwener/opencli\n"
                f"  {install_hint()}"
            ) from exc
        # opencli present but plugin not installed?
        if ("unknown command" in msg or "unknown site" in msg
                or ("ths-extra" in msg and "not" in msg)):
            raise ThsExtraNotInstalled(
                "ths-extra plugin 未安装. 一次性安装命令 (复制粘贴):\n"
                f"  {install_hint()}\n"
                "装完 `opencli list | findstr ths-extra` 应能看到."
            ) from exc
        raise


class IWencaiCollector:
    """同花顺问财 (iwencai) — natural-language stock screener.

    Returns raw cell rows for the result table. The column schema is
    dynamic (per question) so we can't normalise here — caller stores
    ``cells`` verbatim and the LLM interprets per query.
    """

    def fetch(self, question: str, limit: int = 20) -> List[Dict[str, Any]]:
        raw = _run_ths_extra(
            "iwencai", question,
            "--limit", str(limit),
            timeout=60,
        ) or []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        items: List[Dict[str, Any]] = []
        for i, r in enumerate(raw):
            items.append({
                "question": question,
                "row_index": i,
                "columns": r.get("columns") or "",
                "cells": r.get("cells") or "",
                "snapshot_ts": now,
            })
        return items


class THSFundFlowCollector:
    """同花顺资金流: gegu (个股) / gainian (概念) / hangye (行业) / ddzz (大单追踪).

    Same DB table (``ths_fund_flow``) absorbs all four — ``target``
    column distinguishes them so query / report code can filter by
    leaderboard kind.

    Returned schema (always present, may be empty per target):
      target, code, name, price, change_pct, turnover_pct, inflow,
      outflow, main_net, total_amount, num_stocks, leader, trade_time,
      direction, volume, url, raw_cells, snapshot_ts.
    """

    VALID_TARGETS = {"gegu", "gainian", "hangye", "ddzz"}

    def fetch(self, target: str = "gegu", page_no: int = 1, limit: int = 30,
              sort_by: str = "zdf") -> List[Dict[str, Any]]:
        if target not in self.VALID_TARGETS:
            raise ValueError(f"target must be one of {self.VALID_TARGETS}, got {target!r}")
        raw = _run_ths_extra(
            "fund-flow",
            "--target", target,
            "--page_no", str(page_no),
            "--limit", str(limit),
            "--sort_by", sort_by,
            timeout=60,
        ) or []
        snapshot = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        def _valid(r: Dict[str, Any]) -> bool:
            code = r.get("code") or ""
            name = r.get("name") or ""
            # Treat the 10jqka '--' empty-cell sentinel as missing.
            valid_code = code and code != "--"
            valid_name = name and name != "--"
            return bool(valid_code or valid_name)
        return [{**r, "snapshot_ts": snapshot} for r in raw if _valid(r)]


class THSConceptBoardCollector:
    """同花顺新概念发布表 / 概念板块涨幅榜.

    ``mode='new'`` (default) → newly-minted concept list (date + name + num stocks).
    ``mode='rank'`` → ranked concept-board leaderboard (URL still being
    verified; may return empty).
    """

    def fetch(self, mode: str = "new", limit: int = 30,
              page_no: int = 1) -> List[Dict[str, Any]]:
        raw = _run_ths_extra(
            "concept-board",
            "--mode", mode,
            "--limit", str(limit),
            "--page_no", str(page_no),
            timeout=60,
        ) or []
        snapshot = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [{**r, "snapshot_ts": snapshot, "mode": mode} for r in raw if r.get("board_name")]
