"""xueqiu comments collector (cookie-mode, requires Chrome login)."""
from __future__ import annotations
from typing import List
from financial_analyst.data.collectors.opencli.runner import run_opencli


class XueqiuCommentsCollector:
    """Pull stock discussion comments from xueqiu.com.

    Requires OpenCLI Chrome extension + user logged into xueqiu.com.
    See docs/xueqiu_setup.md.
    """

    def fetch(self, code: str, limit: int = 30) -> List[dict]:
        """Get comments for a stock. Returns list[{ts, author, content, likes, comments_count}]."""
        short = code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        return run_opencli(
            "xueqiu", "comments", short,
            "--limit", str(limit),
            timeout=90,
        )
