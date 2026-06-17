"""NewsCollector — plug-in interface for populating news/<code>/ drop-zone."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List


class BaseNewsCollector(ABC):
    """Plug-in interface for news collection.

    A NewsCollector reads from some external source (Tushare news API, RSS,
    web scrape, etc.) and writes per-stock text files into news/<code>/<filename>.txt
    that news-reader picks up. We never invoke the LLM here — that's news-reader's job.

    Implementations:
        - examples/custom_news_collector.py uses Tushare's news API
        - users can subclass and register via plugin discovery (v0.4 Phase B)
    """

    @abstractmethod
    def collect(self, code: str, days: int = 7, target_dir: Path = Path("news")) -> List[Path]:
        """Collect news for one stock and write to `target_dir/<code>/<filename>.txt`.

        Args:
            code: stock code, e.g. "SH600519".
            days: lookback window in calendar days.
            target_dir: root of the news drop-zone (default: `./news/`).

        Returns:
            list of written file paths.
        """

    def supports(self, market: str) -> bool:
        """Override to declare which markets this collector handles. Default: A-shares only."""
        return market == "a_share"
