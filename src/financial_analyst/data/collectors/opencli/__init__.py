"""OpenCLI-backed collectors — pull live A-share news/F10 from eastmoney/sinafinance/xueqiu.

Requires `opencli` CLI on PATH. Install:
    npm install -g @jackwener/opencli

Commands are all public (no login) for the kuaixun/longhu/holders/news ones.
xueqiu requires cookie (Chrome session) — see docs/xueqiu_setup.md.
"""
from financial_analyst.data.collectors.opencli.runner import run_opencli, is_opencli_available
from financial_analyst.data.collectors.opencli.eastmoney_kuaixun import EastmoneyKuaixunCollector
from financial_analyst.data.collectors.opencli.eastmoney_longhu import EastmoneyLonghuCollector
from financial_analyst.data.collectors.opencli.eastmoney_holders import EastmoneyHoldersCollector
from financial_analyst.data.collectors.opencli.sinafinance_news import SinafinanceNewsCollector
from financial_analyst.data.collectors.opencli.xueqiu_comments import XueqiuCommentsCollector
from financial_analyst.data.collectors.opencli.xueqiu_hot_stock import XueqiuHotStockCollector
from financial_analyst.data.collectors.opencli.xueqiu_earnings import XueqiuEarningsCollector

__all__ = [
    "run_opencli", "is_opencli_available",
    "EastmoneyKuaixunCollector", "EastmoneyLonghuCollector",
    "EastmoneyHoldersCollector", "SinafinanceNewsCollector",
    "XueqiuCommentsCollector", "XueqiuHotStockCollector", "XueqiuEarningsCollector",
]
