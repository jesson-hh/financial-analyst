"""F10Collector — plug-in interface for populating f10/<code>/ drop-zone."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List


class BaseF10Collector(ABC):
    """Plug-in interface for F10 (公司公告/龙虎榜/大宗交易) collection.

    F10Collector pulls structured company data from pytdx, akshare, or web scrapers
    and writes per-stock text files into f10/<code>/<filename>.txt that f10-reader picks up.

    Typical use:
        - pytdx for 龙虎榜 (LHB) + 公司大事
        - akshare for 大宗交易
        - vendor APIs for research reports

    Implementations:
        - examples/custom_f10_collector.py uses pytdx
        - users can subclass and register via plugin discovery (v0.4 Phase B)
    """

    @abstractmethod
    def collect(self, code: str, days: int = 30, target_dir: Path = Path("f10")) -> List[Path]:
        """Collect F10 docs for one stock and write to `target_dir/<code>/<filename>.txt`.

        Args:
            code: stock code, e.g. "SH600519".
            days: lookback window for events (default 30).
            target_dir: root of the F10 drop-zone (default: `./f10/`).

        Returns:
            list of written file paths.
        """

    def supports(self, market: str) -> bool:
        return market == "a_share"
