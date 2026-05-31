"""EtfMetricsFetcher — tier-1 ETF metrics agent (premium/NAV/flow/TE/holdings)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent


class EtfMetricsOutput(BaseModel):
    code: str
    asof_date: str
    premium_discount: Dict[str, Any] = {}
    nav: Dict[str, Any] = {}
    flow: Dict[str, Any] = {}
    tracking_error: Dict[str, Any] = {}
    holdings: Dict[str, Any] = {}


class EtfMetricsFetcher(SubAgent[EtfMetricsOutput]):
    NAME = "etf-metrics-fetcher"
    OUTPUT_SCHEMA = EtfMetricsOutput

    def __init__(self, memory_root: Path, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        if self._loader is not None:
            return self._loader
        from financial_analyst.data.loaders.etf import ETFLoader
        return ETFLoader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code = inputs["code"]
        asof = inputs["asof_date"]
        loader = self._get_loader()

        premium_discount = loader.fetch_etf_premium_discount(code)

        nav_df = loader.fetch_etf_nav(code)
        nav_out: Dict[str, Any] = {}
        if nav_df is not None and not (hasattr(nav_df, "empty") and nav_df.empty):
            import pandas as pd
            if isinstance(nav_df, pd.DataFrame) and not nav_df.empty:
                last = nav_df.iloc[-1]
                nav_out["unit_nav"] = float(last["unit_nav"]) if "unit_nav" in last else None
                nav_out["recent"] = nav_df.tail(5).to_dict(orient="records")
            elif isinstance(nav_df, dict):
                nav_out = nav_df
        else:
            nav_out = {}

        flow = loader.fetch_etf_flow(code)
        # strip non-serialisable numpy arrays from flow if present
        if isinstance(flow, dict) and "share_series" in flow:
            series = flow.get("share_series")
            if series is not None:
                try:
                    flow = {k: v for k, v in flow.items() if k != "share_series"}
                    flow["share_series"] = list(series)
                except Exception:
                    flow = {k: v for k, v in flow.items() if k != "share_series"}

        tracking_error = loader.fetch_tracking_error(code)
        holdings = loader.fetch_etf_holdings(code)

        return {
            "code": code,
            "asof_date": asof,
            "premium_discount": premium_discount or {},
            "nav": nav_out,
            "flow": flow or {},
            "tracking_error": tracking_error or {},
            "holdings": holdings or {},
        }
