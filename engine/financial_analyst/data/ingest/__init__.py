"""Data ingestion layer — convert external data sources into the Qlib binary layout."""

from financial_analyst.data.ingest.base import BaseIngester, IngestResult
from financial_analyst.data.ingest.csv_ingester import CsvIngester
from financial_analyst.data.ingest.akshare_ingester import AkshareIngester
from financial_analyst.data.ingest.yfinance_ingester import YfinanceIngester

__all__ = [
    "BaseIngester",
    "IngestResult",
    "CsvIngester",
    "AkshareIngester",
    "YfinanceIngester",
]
