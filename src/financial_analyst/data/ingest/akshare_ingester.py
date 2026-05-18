"""AkshareIngester — stub for v0.4.

Free A-share data via akshare (no API key required). Not yet implemented.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from financial_analyst.data.ingest.base import BaseIngester, IngestResult


class AkshareIngester(BaseIngester):
    """Free A-share data via akshare (no API key). v0.4 implementation pending.

    Use :class:`~financial_analyst.data.ingest.CsvIngester` for now.
    """

    def __init__(self, market: str = "a_share", **kwargs: Any) -> None:
        self.market = market

    def discover(self) -> Dict[str, Any]:
        raise NotImplementedError(
            "AkshareIngester is reserved for v0.4. Use CsvIngester for now."
        )

    def convert(self, target_root: Path) -> IngestResult:
        raise NotImplementedError(
            "AkshareIngester is reserved for v0.4. Use CsvIngester for now."
        )
