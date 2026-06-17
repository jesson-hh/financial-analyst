"""Data collectors — fetch news/F10/etc into local drop-zones for untrusted readers.

These are OPTIONAL plug-ins. By default, news-reader and f10-reader load files
from `news/<code>/*.txt` and `f10/<code>/*.txt` (manual drop). A NewsCollector
or F10Collector can populate those directories automatically.
"""
from financial_analyst.data.collectors.news.base import BaseNewsCollector
from financial_analyst.data.collectors.f10.base import BaseF10Collector

__all__ = ["BaseNewsCollector", "BaseF10Collector"]
