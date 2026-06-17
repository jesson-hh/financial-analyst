"""OpenCLI-backed collectors — pull live A-share news/F10 from eastmoney/sinafinance/xueqiu/ths.

Requires `opencli` CLI on PATH. Install:
    npm install -g @jackwener/opencli

Commands are all public (no login) for the kuaixun/longhu/holders/news/ths ones.
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
from financial_analyst.data.collectors.opencli.xueqiu_feed import XueqiuFeedCollector
from financial_analyst.data.collectors.opencli.xueqiu_hot_posts import XueqiuHotPostsCollector
from financial_analyst.data.collectors.opencli.xueqiu_watchlist import (
    XueqiuWatchlistCollector, XueqiuGroupsCollector,
)
from financial_analyst.data.collectors.opencli.xueqiu_fund import (
    XueqiuFundSnapshotCollector, XueqiuFundHoldingsCollector,
)
from financial_analyst.data.collectors.opencli.ths_hot_rank import THSHotRankCollector
from financial_analyst.data.collectors.opencli.ths_extra import (
    IWencaiCollector, THSFundFlowCollector, THSConceptBoardCollector,
)
from financial_analyst.data.collectors.opencli.xueqiu_stock import XueqiuStockCollector

__all__ = [
    "run_opencli", "is_opencli_available",
    "EastmoneyKuaixunCollector", "EastmoneyLonghuCollector",
    "EastmoneyHoldersCollector", "SinafinanceNewsCollector",
    "XueqiuCommentsCollector", "XueqiuHotStockCollector", "XueqiuEarningsCollector",
    "XueqiuFeedCollector", "XueqiuHotPostsCollector",
    "XueqiuWatchlistCollector", "XueqiuGroupsCollector",
    "XueqiuFundSnapshotCollector", "XueqiuFundHoldingsCollector",
    "XueqiuStockCollector",
    "THSHotRankCollector",
    "IWencaiCollector", "THSFundFlowCollector", "THSConceptBoardCollector",
]
