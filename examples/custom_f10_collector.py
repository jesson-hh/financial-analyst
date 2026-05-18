"""Example: F10 collector backed by pytdx.

Real implementation would pull from pytdx hosts. This stub shows the structure.
The G:\\stocks project has a complete implementation at scripts/tdx_f10_collector.py
that you can adapt.

To use:
    >>> from examples.custom_f10_collector import TdxF10Collector
    >>> collector = TdxF10Collector()
    >>> collector.collect("SH600519", days=30)
"""
from __future__ import annotations
from pathlib import Path
from typing import List
from financial_analyst.data.collectors.f10.base import BaseF10Collector


class TdxF10Collector(BaseF10Collector):
    """Stub for pytdx-backed F10 collector.

    Real implementation would call pytdx APIs:
        - get_finance_info() for 龙虎榜
        - get_company_info() for 公司大事
        - get_block_trade() for 大宗交易

    See G:\\stocks/scripts/tdx_f10_collector.py for a full reference impl.
    """

    def __init__(self, host: str = "180.153.18.171", port: int = 7709):
        self.host = host
        self.port = port
        # Real impl:
        # from pytdx.hq import TdxHq_API
        # self.api = TdxHq_API()
        # self.api.connect(host, port)

    def collect(self, code: str, days: int = 30, target_dir: Path = Path("f10")) -> List[Path]:
        target = Path(target_dir) / code.upper()
        target.mkdir(parents=True, exist_ok=True)

        # Stub: write a placeholder
        out = target / f"{code}_stub.txt"
        out.write_text(
            f"# F10 stub for {code}\n"
            "Replace this collector with a real pytdx integration.\n"
            "See G:/stocks/scripts/tdx_f10_collector.py for a reference implementation\n"
            "covering LHB (龙虎榜), 公司大事, 大宗交易.\n",
            encoding="utf-8",
        )
        return [out]
