"""Lightweight intent classifier for the desktop UI's per-turn label.

Pure regex — no LLM call. Mirrors the desktop mock's ``detectIntent`` so
the UI shows a meaningful chain title (资金流扫描 / 驱动归因 / …) before
the real agent's tool choices come back. Purely cosmetic: it does NOT
influence which tools the agent actually picks (the LLM still decides).
"""
from __future__ import annotations
import re

INTENT_LABELS = {
    "brief": "stock_brief",
    "fundflow": "资金流扫描",
    "why_move": "驱动归因",
    "compare": "同业对比",
    "technical": "技术面",
    "alert": "盯盘规则",
    "news": "消息面",
    "screen": "选股筛选",
    "market": "大盘研判",
}

_RULES = [
    ("alert", r"盯|提醒|跌破|涨破|到价|预警"),
    ("fundflow", r"资金|主力|龙虎|净流入|净流出|今天.*买|加仓|减仓|大单"),
    ("why_move", r"为什么.*[涨跌]|为啥|催化|驱动|怎么[涨跌]"),
    ("compare", r"对比|vs|比较|哪个好|谁更"),
    ("technical", r"技术|K线|均线|MA\d|压力位|支撑位|形态|布林"),
    ("screen", r"筛选|选股|问财|找出|哪些.*股|PE.*ROE|市盈"),
    ("news", r"新闻|消息|公告|资讯|舆情|情绪|雪球"),
    # 大盘/市场级研判 — 放末尾 (brief 默认之前). 收紧 (H4): 去掉 '行情'/'今天.*怎么样'/'市场'/'指数'
    # 等过泛词 — 否则个股问句 '茅台今天怎么样' 会被误路由到 market recipe (无 code, 答非所问).
    # 只留明确市场级词.
    ("market", r"大盘|沪指|上证|深成|创业板指|两市|涨停家数|涨停板|跌停家数|主线|题材|市场情绪|大盘.*怎么样"),
]


def classify(query: str) -> str:
    """Return an intent key (see INTENT_LABELS). Defaults to 'brief'."""
    q = query or ""
    for intent, pattern in _RULES:
        if re.search(pattern, q):
            return intent
    return "brief"


def label_for(intent: str) -> str:
    return INTENT_LABELS.get(intent, intent)
