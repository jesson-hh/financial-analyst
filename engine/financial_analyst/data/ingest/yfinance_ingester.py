"""YfinanceIngester — stub for v0.4.

International equity data via yfinance. Not yet implemented.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

from financial_analyst.data.ingest.base import BaseIngester, IngestResult


class YfinanceIngester(BaseIngester):
    """International equity data via yfinance. v0.4 implementation pending.

    Use :class:`~financial_analyst.data.ingest.CsvIngester` for now.
    """

    def __init__(
        self,
        tickers: Union[str, List[str]],
        **kwargs: Any,
    ) -> None:
        self.tickers = [tickers] if isinstance(tickers, str) else list(tickers)

    def discover(self) -> Dict[str, Any]:
        raise NotImplementedError("YfinanceIngester is reserved for v0.4.")

    def convert(self, target_root: Path) -> IngestResult:
        raise NotImplementedError("YfinanceIngester is reserved for v0.4.")
