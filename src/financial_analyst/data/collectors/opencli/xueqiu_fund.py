"""蛋卷基金 (danjuanfunds.com) snapshot + holdings collectors.

Both commands require a separate cookie session from xueqiu main site —
the user must be logged in to danjuanfunds.com via the Chrome extension.
When not logged in, opencli returns an exitCode-1 error which surfaces
as a ``RuntimeError("opencli exit 1: No fund accounts found …")`` from
our ``run_opencli`` wrapper. Catch it at the call site to give a clean
"please log in to 蛋卷" hint instead of a stack trace.
"""
from __future__ import annotations
from typing import Any, Dict, List
from financial_analyst.data.collectors.opencli.runner import run_opencli


class XueqiuFundSnapshotCollector:
    """Pull 蛋卷 total-assets / per-account summary.

    Returns a list of account dicts; the schema is documented in
    ``opencli xueqiu fund-snapshot --help -f yaml`` but in practice we
    pass each raw row straight into ``NewsDB.upsert_fund_snapshot`` and
    let that method handle the OpenAPI-style camelCase fields.
    """

    def fetch(self) -> List[Dict[str, Any]]:
        out = run_opencli("xueqiu", "fund-snapshot", timeout=60)
        # opencli sometimes wraps in {accounts: [...]} on this endpoint;
        # we accept both shapes.
        if isinstance(out, dict):
            return out.get("accounts") or out.get("data") or [out]
        return out or []


class XueqiuFundHoldingsCollector:
    """Pull 蛋卷 holdings detail. Returns
    ``list[{accountName, fdCode, fdName, marketValue, volume, dailyGain,
    holdGain, holdGainRate, marketPercent}]``.

    ``account`` filters to one sub-account (by name or id); empty = all.
    """

    def fetch(self, account: str = "") -> List[Dict[str, Any]]:
        args = ["xueqiu", "fund-holdings"]
        if account:
            args += ["--account", account]
        return run_opencli(*args, timeout=60) or []
