"""OpenCLI-backed collectors — pull live A-share news/F10 from eastmoney/sinafinance/xueqiu.

Requires `opencli` CLI on PATH. Install:
    npm install -g @jackwener/opencli

Commands are all public (no login) for the kuaixun/longhu/holders/news ones.
xueqiu requires cookie (Chrome session) — not in v1.1.
"""
from financial_analyst.data.collectors.opencli.runner import run_opencli, is_opencli_available
from financial_analyst.data.collectors.opencli.eastmoney_kuaixun import EastmoneyKuaixunCollector
from financial_analyst.data.collectors.opencli.eastmoney_longhu import EastmoneyLonghuCollector
from financial_analyst.data.collectors.opencli.eastmoney_holders import EastmoneyHoldersCollector
from financial_analyst.data.collectors.opencli.sinafinance_news import SinafinanceNewsCollector

__all__ = [
    "run_opencli", "is_opencli_available",
    "EastmoneyKuaixunCollector", "EastmoneyLonghuCollector",
    "EastmoneyHoldersCollector", "SinafinanceNewsCollector",
]
