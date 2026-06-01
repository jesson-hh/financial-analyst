"""Tool registry for the Buddy conversational agent.

Each tool wraps an existing CLI helper. Tools expose:
    - ``name``: identifier the LLM uses in tool calls
    - ``description``: 1-2 sentences for the LLM to decide when to use it
    - ``input_schema``: JSON schema for tool arguments
    - ``run(**args) → ToolResult``: invokes the wrapped helper

Tool descriptions are written in BOTH Chinese and English so the LLM
can match user requests in either language.

Anthropic / OpenAI / Qwen all accept the same JSON-schema-shaped tool
list (LiteLLM normalises across providers).
"""
from __future__ import annotations
import asyncio
import functools
import json
import os
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from financial_analyst.data.code_norm import etf_exchange


def _disp_w(s: Any) -> int:
    """Terminal display width — CJK / fullwidth glyphs count as 2 columns.
    Python's f-string ``:<N`` aligns by character count, which mis-aligns
    Chinese tables (each Han character takes 2 columns but counts as 1 character).
    Use this for any tabular output that mixes Chinese names with numeric columns."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in str(s))


def _pad(s: Any, width: int) -> str:
    """Left-align ``s`` to a display width of ``width`` columns (CJK-aware)."""
    s = str(s)
    return s + " " * max(0, width - _disp_w(s))


def normalize_code(code: Any) -> str:
    """Normalise a stock/ETF code to the SH/SZ/BJ-prefixed form the loaders
    expect. Accepts bare 6-digit (300750/510300), prefixed (SZ300750), or
    suffixed (300750.SZ). Used by the desktop UI bridge which sends bare
    6-digit codes."""
    c = str(code).upper().strip()
    if "." in c:  # tushare suffix form 300750.SZ
        num, _, suf = c.partition(".")
        if suf in ("SH", "SZ", "BJ") and num.isdigit():
            return suf + num
        c = num
    if c[:2] in ("SH", "SZ", "BJ"):
        return c
    if c.isdigit() and len(c) == 6:
        ex = etf_exchange(c)
        if ex:
            return ex + c
        if c[0] == "6":
            return "SH" + c
        if c[0] in "03":
            return "SZ" + c
        if c[0] in "84":
            return "BJ" + c
    return c


def _pct_to_num(s: Any) -> Optional[float]:
    """'-0.30%' → -0.30 · 1.5 → 1.5 · '' → None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("%", "").replace(",", "")
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def brief_data(code: str) -> Dict[str, Any]:
    """Structured one-stock snapshot for the desktop UI's quick-view card.

    Best-effort: every field is independently guarded, missing ones stay
    ``None``. Real-time quote (xueqiu) is the primary source; falls back
    to nothing if the cookie is absent (UI shows what it can).
    """
    norm = normalize_code(code)
    d: Dict[str, Any] = {
        "code": norm, "name": None, "market": None, "industry": None, "mc": None,
        "price": None, "change": None, "deltaPct": None,
        "vol_ratio": None, "turn": None, "amp": None,
        "pe": None, "pb": None, "roe": None,
        "main_in": None, "prev_main_in": None, "inflow": None, "outflow": None,
        "market_status": None, "xq_bull": None,
        "news": [],
        "comments": [],   # Xueqiu community comments (locally cached; live refresh via /comments)
    }
    d["market"] = {"SH": "沪", "SZ": "深", "BJ": "北"}.get(norm[:2], "") + "市"

    # --- real-time quote (primary): Tencent — no cookie, fills most card
    #     fields (price/change/pe/pb/vol_ratio/turn/amp/mc) in ~120ms ---
    try:
        from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
        q = TencentQuoteCollector().quote(norm)
        if q:
            d["name"] = q.get("name")
            d["price"] = q.get("price")
            d["change"] = q.get("change")
            d["deltaPct"] = q.get("changePercent")
            d["vol_ratio"] = q.get("vol_ratio")
            d["turn"] = q.get("turnover_rate")
            d["amp"] = q.get("amplitude")
            d["pe"] = q.get("pe")
            d["pb"] = q.get("pb")
            if q.get("total_mv"):
                d["mc"] = f"{q['total_mv']:.0f}亿"
    except Exception:
        pass
    # market session (Tencent has no status field)
    try:
        from financial_analyst.buddy.alerts import market_session
        d["market_status"] = {"open": "交易中", "lunch": "午休",
                              "closed": "已收盘", "weekend": "休市"}.get(
            market_session(), None)
    except Exception:
        pass

    # --- industry ---
    try:
        ind = _tool_industry_show(norm)
        if not ind.is_error and ":" in ind.content:
            d["industry"] = ind.content.split(":", 1)[1].strip()
    except Exception:
        pass

    # --- PE / PB from daily basic ---
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        import pandas as pd
        loader = get_default_loader()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        start = (pd.Timestamp.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        db = loader.fetch_daily_basic(norm, start, today)
        if db is not None and not db.empty:
            b = db.iloc[-1]
            d["pe"] = b.get("pe_ttm")
            d["pb"] = b.get("pb")
            if d["mc"] is None and b.get("total_mv"):
                d["mc"] = f"{b.get('total_mv', 0) / 10000:.0f}亿"
    except Exception:
        pass

    # --- main fund flow (latest + previous snapshot) ---
    try:
        from financial_analyst.data.news_db import NewsDB
        ndb = NewsDB()
        hist = ndb.query_ths_fund_flow_history(norm, target="gegu", limit=2)
        ndb.close()
        if hist:
            d["main_in"] = _parse_cn_amount(hist[0].get("main_net"))
            if d["main_in"] is not None:
                d["main_in"] = round(d["main_in"] / 1e8, 2)  # → 亿
            inf = _parse_cn_amount(hist[0].get("inflow"))
            outf = _parse_cn_amount(hist[0].get("outflow"))
            d["inflow"] = round(inf / 1e8, 2) if inf is not None else None
            d["outflow"] = round(outf / 1e8, 2) if outf is not None else None
        if len(hist) > 1:
            pv = _parse_cn_amount(hist[1].get("main_net"))
            d["prev_main_in"] = round(pv / 1e8, 2) if pv is not None else None
    except Exception:
        pass

    # --- recent news (top 3, real from local news DB) ---
    try:
        from financial_analyst.data.news_db import NewsDB
        ndb = NewsDB()
        rows = ndb.query_news(code=norm, since_days=7, limit=3)
        ndb.close()
        for r in rows:
            ts = (r.get("ts") or "")[:10]
            src = (r.get("source") or "").strip()
            title = (r.get("title") or "").strip()
            if title:
                d["news"].append({"t": title, "s": f"{src} · {ts}".strip(" ·")})
    except Exception:
        pass

    # --- Xueqiu community comments (locally cached, instant; live via /comments?refresh=1) ---
    try:
        from financial_analyst.data.news_db import NewsDB
        ndb = NewsDB()
        posts = ndb.query_social_posts(norm, since_days=365, limit=5)
        ndb.close()
        d["comments"] = _format_social_posts(posts)
    except Exception:
        pass

    return d


def _format_social_posts(posts) -> list:
    """Map stored social_posts rows → UI comment items {author,text,likes,replies,url,ts}."""
    import json as _json
    out = []
    for p in posts or []:
        url = ""
        try:
            url = (_json.loads(p.get("raw") or "{}") or {}).get("url", "") or ""
        except Exception:
            pass
        out.append({
            "author": p.get("author") or "",
            "text": (p.get("content") or "").strip(),
            "likes": p.get("likes") or 0,
            "replies": p.get("comments_count") or 0,
            "url": url,
            "ts": (p.get("ts") or "")[:10],
        })
    return out


@functools.lru_cache(maxsize=1)
def _project_root() -> Path:
    """Locate the financial-analyst project root.

    The CLI was originally designed to be run from inside the source
    checkout (where ``memories/``, ``out/``, ``config/`` etc. live as
    siblings of ``src/``). When ``chat`` is launched from an arbitrary
    cwd, every ``subprocess.run([... financial-analyst ...])`` we spawn
    inherits that cwd and the CLI's relative-path lookups (``Path("memories")``,
    ``Path("out")``, ``Path("config")``) all fail with WinError 3.

    Resolution order:
    1. ``$FINANCIAL_ANALYST_HOME`` (env var override)
    2. The parent of ``src/`` for editable installs (most users)
    3. The current working directory (last-resort fallback)
    """
    env = os.environ.get("FINANCIAL_ANALYST_HOME")
    if env:
        p = Path(env)
        if p.exists():
            return p
    pkg = Path(__file__).resolve().parent.parent  # .../src/financial_analyst/buddy → .../src/financial_analyst
    candidate = pkg.parent.parent  # → repo root (sibling of src/)
    if (candidate / "memories").exists() or (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


@dataclass
class ToolResult:
    """Return value from a tool. ``content`` is what the LLM sees; ``side_effect``
    is freeform metadata for the REPL to display (e.g., file paths written).
    """
    content: str
    is_error: bool = False
    side_effect: Optional[Dict[str, Any]] = None


@dataclass
class Tool:
    """One LLM-callable tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    run: Callable[..., ToolResult]
    cost_hint: str = "instant"  # one of: instant | seconds | minutes
    confirm_required: bool = False  # ask user before running

    def to_anthropic_schema(self) -> Dict[str, Any]:
        """Render for Anthropic / Claude tool-use."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_schema(self) -> Dict[str, Any]:
        """Render for OpenAI / Qwen function-calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


# ---------------------------------------------------------------------------
# Tool implementations — each wraps a CLI helper
# ---------------------------------------------------------------------------


def _tool_update_data(codes: Optional[str] = None, mode: str = "quick") -> ToolResult:
    """Direct-connection incremental data update. Wraps ``financial-analyst data update`` CLI.

    Default quick: skip 5min + daily_basic, update only daily OHLCV. Very fast (~50ms/code).
    full: daily + 5min + today's daily_basic all together, ~120ms/code.

    `codes` controls scope:
      - None: all instruments (slow, ~5 min for 5500 codes)
      - "SH600519,SZ300750": explicit list
      - "all": explicitly request whole-market (same as None, just explicit)
    """
    cmd = ["financial-analyst", "data", "update"]
    if codes and codes.strip().lower() != "all":
        cmd += ["--codes", codes.strip()]
    if mode == "quick":
        cmd += ["--skip-5min", "--skip-basic"]
    root = _project_root()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              cwd=str(root))
    except subprocess.TimeoutExpired:
        return ToolResult("数据更新超时 (15min). 试试限定 codes 缩小范围.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(
            f"fa data update 失败 (exit {proc.returncode}):\n{proc.stderr[-500:]}",
            is_error=True,
        )
    # Output an LLM-friendly summary
    stdout = proc.stdout.strip()
    lines = stdout.splitlines()
    # Extract the [日线 ✓] / [5min ✓] / [daily_basic ✓] lines + the final total-time line
    keep = [l for l in lines if any(k in l for k in
                                     ("[日线", "[5min", "[daily_basic", "完成"))]
    summary = "\n".join(keep) if keep else stdout[-400:]
    return ToolResult(f"数据更新完成:\n{summary}",
                      side_effect={"cmd": " ".join(cmd)})


def _tool_report(code: str, asof: Optional[str] = None) -> ToolResult:
    """Run a full single-stock deep-dive report."""
    asof = asof or "today"  # CLI handles 'today' as None
    cmd = ["financial-analyst", "report", code]
    if asof and asof != "today":
        cmd += ["--asof", asof]
    root = _project_root()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              cwd=str(root))
    except subprocess.TimeoutExpired:
        return ToolResult("Report timed out after 15 minutes.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(
            f"Report failed (exit {proc.returncode}):\n{proc.stderr[-500:]}",
            is_error=True,
        )
    # Find the markdown output path
    asof_for_path = asof if asof and asof != "today" else None
    md_files = sorted((root / "out").glob(f"{code}_*.md"))
    if not md_files:
        return ToolResult(f"Report finished but no markdown found for {code}.")
    md_path = md_files[-1]
    body = md_path.read_text(encoding="utf-8", errors="replace")
    # Extract the executive summary (sections "一、综合评级" and "八、操作建议")
    import re
    summary_parts = []
    for sect in (r"## 一、综合评级.*?(?=## 二)", r"## 八、操作建议.*?(?=---|\Z)"):
        m = re.search(sect, body, re.DOTALL)
        if m:
            summary_parts.append(m.group(0).strip())
    summary = "\n\n".join(summary_parts) or body[:1500]
    return ToolResult(
        f"Report written to {md_path}.\n\nExec summary:\n{summary}",
        side_effect={"md_path": str(md_path)},
    )


def _tool_etf_report(code: str, asof: Optional[str] = None) -> ToolResult:
    """Run a full ETF deep-dive report (13-agent etf-deep-dive swarm)."""
    asof = asof or "today"  # CLI handles 'today' as None
    cmd = ["financial-analyst", "etf-report", code]
    if asof and asof != "today":
        cmd += ["--asof", asof]
    root = _project_root()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              cwd=str(root))
    except subprocess.TimeoutExpired:
        return ToolResult("ETF report timed out after 15 minutes.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(
            f"ETF report failed (exit {proc.returncode}):\n{proc.stderr[-500:]}",
            is_error=True,
        )
    md_files = sorted((root / "out").glob(f"{code}_*.md"))
    if not md_files:
        return ToolResult(f"ETF report finished but no markdown found for {code}.")
    md_path = md_files[-1]
    body = md_path.read_text(encoding="utf-8", errors="replace")
    import re
    summary_parts = []
    for sect in (r"## 一、综合评级.*?(?=## 二)", r"## 八、操作建议.*?(?=---|\Z)"):
        m = re.search(sect, body, re.DOTALL)
        if m:
            summary_parts.append(m.group(0).strip())
    summary = "\n\n".join(summary_parts) or body[:1500]
    return ToolResult(
        f"ETF report written to {md_path}.\n\nExec summary:\n{summary}",
        side_effect={"md_path": str(md_path)},
    )


def _tool_news_collect(sources: str = "kuaixun,longhu,sinafinance",
                       limit: int = 200,
                       code: Optional[str] = None) -> ToolResult:
    """Fetch fresh news from upstream sources into the local DB.

    ``sources`` is comma-separated. Public ones (no cookie): kuaixun /
    longhu / sinafinance / shareholders. Cookie-required (need Chrome
    extension): xueqiu-comments (needs code) / xueqiu-hot / xueqiu-earnings.
    """
    cmd = ["financial-analyst", "news-collect",
           "--sources", sources, "--limit", str(limit)]
    if code:
        cmd += ["--code", code]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300,
                              cwd=str(_project_root()))
    except subprocess.TimeoutExpired:
        return ToolResult("news-collect timed out (5 min limit).", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"news-collect failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-2000:])


def _news_staleness_note(rows: List[Dict[str, Any]],
                          stale_hours: float = 18.0) -> Optional[str]:
    """Return a warning string if the freshest row is older than
    ``stale_hours``, else None. Lets the LLM avoid presenting day-old
    sentiment as 'today'."""
    import datetime as _dt
    newest: Optional[_dt.datetime] = None
    for r in rows:
        ts = (r.get("ts") or "").strip()
        if not ts:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = _dt.datetime.strptime(ts[:len(fmt) + 2].strip(), fmt)
                if newest is None or dt > newest:
                    newest = dt
                break
            except ValueError:
                continue
    if newest is None:
        return None
    age_h = (_dt.datetime.now() - newest).total_seconds() / 3600.0
    if age_h > stale_hours:
        return (
            f"⚠ 数据偏旧: 最新一条是 {newest:%Y-%m-%d %H:%M} "
            f"({age_h:.0f} 小时前). 如要今日动态请先调 news_collect 刷新."
        )
    return None


def _tool_news_query(code: Optional[str] = None, days: int = 7,
                     fts: Optional[str] = None, limit: int = 10) -> ToolResult:
    """Query the local news DB. Either by stock code, by FTS keyword, or both.

    v1.7.5: prepends a staleness warning when the freshest matching row
    is older than ~18h, so the LLM knows to refresh before claiming
    'today's news'."""
    from financial_analyst.data.news_db import NewsDB
    db = NewsDB()
    if fts:
        rows = db.search_news(fts, limit=limit)
    else:
        rows = db.query_news(code=code, since_days=days, limit=limit)
    db.close()
    if not rows:
        return ToolResult(
            f"No news matching code={code!r} days={days} fts={fts!r}. "
            f"Database may be empty for this filter — run `news_collect` first to refresh."
        )
    lines = []
    stale = _news_staleness_note(rows)
    if stale:
        lines.append(stale)
    lines.append(f"Found {len(rows)} news entries:")
    for r in rows:
        ts = r.get("ts", "")
        title = (r.get("title") or "")[:80]
        src = r.get("source", "")
        content = (r.get("content") or "")[:200]
        lines.append(f"  [{ts}] {src}  {title}")
        if content:
            lines.append(f"    {content}")
    return ToolResult("\n".join(lines))


def _tool_watchlist_show(pid: str = "-1", refresh: bool = False) -> ToolResult:
    """Show the user's xueqiu watchlist (自选股 / 模拟组合).

    ``pid`` defaults to ``-1`` (全部). Built-in groups:
    ``-4`` 模拟 / ``-5`` 沪深 / ``-6`` 美股 / ``-7`` 港股 / ``-10`` 实盘.
    Pass ``refresh=True`` to re-fetch from xueqiu (otherwise reads the
    most recent cached snapshot)."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.xueqiu_watchlist import (
        XueqiuWatchlistCollector,
    )
    db = NewsDB()
    if refresh:
        try:
            items = XueqiuWatchlistCollector().fetch(pid=pid, limit=200)
            db.upsert_watchlist(items, group_pid=pid)
        except Exception as exc:
            db.close()
            return ToolResult(f"Refresh failed: {exc}", is_error=True)
    rows = db.query_watchlist(group_pid=pid)
    db.close()
    if not rows:
        return ToolResult(
            f"No watchlist data cached for group {pid!r}. "
            f"Run with refresh=True (needs xueqiu cookie via Chrome ext)."
        )
    snapshot_date = rows[0].get("snapshot_date") or "?"
    lines = [f"自选股 (group_pid={pid}, snapshot={snapshot_date}, n={len(rows)}):"]
    for r in rows:
        lines.append(
            f"  {_pad(r.get('symbol'), 10)} {_pad(r.get('name', '?'), 10)} "
            f"price={_pad(r.get('price') or '-', 8)} "
            f"chg={r.get('change_percent') or '-'}"
        )
    return ToolResult("\n".join(lines))


def _tool_fund_snapshot(refresh: bool = False) -> ToolResult:
    """Show the user's Danjuan Funds (蛋卷基金, danjuanfunds.com) total-assets snapshot.

    Needs danjuanfunds.com login via Chrome ext (separate cookie from xueqiu).
    Pass ``refresh=True`` to re-fetch."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.xueqiu_fund import (
        XueqiuFundSnapshotCollector,
    )
    db = NewsDB()
    if refresh:
        try:
            items = XueqiuFundSnapshotCollector().fetch()
            db.upsert_fund_snapshot(items)
        except Exception as exc:
            db.close()
            return ToolResult(
                f"Snapshot refresh failed: {exc}\n"
                f"(Hint: log in to danjuanfunds.com via the opencli Chrome extension.)",
                is_error=True,
            )
    rows = db.query_fund_snapshot()
    db.close()
    if not rows:
        return ToolResult(
            "No fund snapshot cached. Run with refresh=True (needs 蛋卷 cookie)."
        )
    lines = [f"蛋卷基金账户 ({len(rows)} accounts):"]
    for r in rows:
        lines.append(
            f"  [{r.get('account_name') or r.get('account_id')}] "
            f"总资产={r.get('total_assets') or '-'}  "
            f"可用={r.get('available_cash') or '-'}  "
            f"当日盈亏={r.get('daily_gain') or '-'}  "
            f"持仓盈亏={r.get('hold_gain') or '-'}"
        )
    return ToolResult("\n".join(lines))


def _tool_fund_holdings(account: str = "", refresh: bool = False) -> ToolResult:
    """Show the user's Danjuan Funds holdings detail. ``account`` filters to a sub-account.

    Needs danjuanfunds.com login via Chrome ext. Pass ``refresh=True``
    to re-fetch from upstream."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.xueqiu_fund import (
        XueqiuFundHoldingsCollector,
    )
    db = NewsDB()
    if refresh:
        try:
            items = XueqiuFundHoldingsCollector().fetch(account=account)
            db.upsert_fund_holdings(items)
        except Exception as exc:
            db.close()
            return ToolResult(
                f"Holdings refresh failed: {exc}\n"
                f"(Hint: log in to danjuanfunds.com via the opencli Chrome extension.)",
                is_error=True,
            )
    rows = db.query_fund_holdings(account_name=account)
    db.close()
    if not rows:
        return ToolResult(
            f"No fund holdings cached (account={account!r}). "
            f"Run with refresh=True (needs 蛋卷 cookie)."
        )
    lines = [f"蛋卷持仓 ({len(rows)} funds, sorted by market value):"]
    for r in rows:
        lines.append(
            f"  {_pad(r.get('fd_code'), 8)} {_pad(r.get('fd_name', '?'), 22)} "
            f"市值={_pad(r.get('market_value') or '-', 10)} "
            f"占比={_pad(r.get('market_percent') or '-', 6)} "
            f"当日={r.get('daily_gain') or '-'}  "
            f"累计={r.get('hold_gain') or '-'} ({r.get('hold_gain_rate') or '-'})"
        )
    return ToolResult("\n".join(lines))


def _tool_iwencai_search(question: str, limit: int = 20,
                          use_cache: bool = False) -> ToolResult:
    """iwencai (问财) natural-language stock screener. Pulls a result table via the
    ths-extra opencli plugin. ``columns`` are dynamic per question — caller interprets cells.

    Pass ``use_cache=True`` to read the most recent cached result for
    this question without hitting iwencai again.
    """
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.ths_extra import IWencaiCollector
    db = NewsDB()
    if use_cache:
        rows = db.query_iwencai(question, limit=limit)
        db.close()
        if rows:
            return _format_iwencai(rows, cached=True)
        # else fall through to live fetch
        db = NewsDB()
    try:
        items = IWencaiCollector().fetch(question, limit=limit)
    except Exception as exc:
        db.close()
        plugin_path = _project_root() / "opencli-plugin-ths-extra"
        return ToolResult(
            f"问财查询失败: {exc}\n"
            f"(Hint: 需要装 ths-extra plugin — "
            f"opencli plugin install file://{plugin_path.as_posix()})",
            is_error=True,
        )
    if not items:
        db.close()
        return ToolResult(f"问财返回空结果, 试试调整问法: {question!r}")
    db.upsert_iwencai(items)
    db.close()
    return _format_iwencai(items, cached=False)


def _format_iwencai(items: List[Dict[str, Any]], cached: bool) -> ToolResult:
    tag = "[cached]" if cached else "[live]"
    q = items[0].get("question") or "?"
    lines = [f"{tag} 问财查询: {q}  ({len(items)} 行)"]
    # Print header row (if columns extracted) once
    cols = items[0].get("columns") or ""
    if cols:
        lines.append(f"列: {cols}")
    for r in items:
        lines.append(f"  {r.get('cells', '')}")
    return ToolResult("\n".join(lines))


def _tool_ths_fund_flow(target: str = "gegu", limit: int = 30,
                          refresh: bool = True) -> ToolResult:
    """Tonghuashun fund-flow, target=gegu/gainian/hangye/ddzz."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.ths_extra import THSFundFlowCollector
    db = NewsDB()
    if refresh:
        try:
            items = THSFundFlowCollector().fetch(target=target, limit=limit)
            if items:
                db.upsert_ths_fund_flow(items)
        except Exception as exc:
            db.close()
            return ToolResult(f"fund-flow ({target}) 抓取失败: {exc}", is_error=True)
    rows = db.query_ths_fund_flow(target=target, limit=limit)
    db.close()
    if not rows:
        return ToolResult(
            f"无 fund-flow 数据 (target={target}) — refresh 可能失败, 或 ths-extra plugin 未装"
        )
    snap = rows[0].get("snapshot_ts", "?")
    titles = {
        "gegu": "个股主力净流入排行",
        "gainian": "概念板块资金流 (涨幅+主力净额)",
        "hangye": "行业板块资金流",
        "ddzz": "大单追踪 (实时大额成交流水)",
    }
    title = titles.get(target, target)
    lines = [f"同花顺 — {title} (snapshot={snap}, top {len(rows)}):"]
    if target == "gegu":
        lines.append(
            f"  {_pad('代码', 8)}{_pad('名称', 12)}{_pad('现价', 8)}{_pad('涨跌幅', 9)}"
            f"{_pad('换手率', 9)}{_pad('主力净流入', 13)}{_pad('成交额', 10)}"
        )
        for r in rows:
            lines.append(
                f"  {_pad(r.get('code') or '-', 8)}{_pad(r.get('name') or '-', 12)}"
                f"{_pad(r.get('price') or '-', 8)}{_pad(r.get('change_pct') or '-', 9)}"
                f"{_pad(r.get('turnover_pct') or '-', 9)}{_pad(r.get('main_net') or '-', 13)}"
                f"{_pad(r.get('total_amount') or '-', 10)}"
            )
    elif target in ("gainian", "hangye"):
        lines.append(
            f"  {_pad('板块代码', 10)}{_pad('名称', 16)}{_pad('涨跌幅', 9)}"
            f"{_pad('主力净额(亿)', 14)}{_pad('股数', 6)}{_pad('领涨股', 12)}"
        )
        for r in rows:
            lines.append(
                f"  {_pad(r.get('code') or '-', 10)}{_pad(r.get('name') or '-', 16)}"
                f"{_pad(r.get('change_pct') or '-', 9)}{_pad(r.get('main_net') or '-', 14)}"
                f"{_pad(r.get('num_stocks') or '-', 6)}{_pad(r.get('leader') or '-', 12)}"
            )
    elif target == "ddzz":
        lines.append(
            f"  {_pad('时间', 20)}{_pad('代码', 8)}{_pad('名称', 12)}"
            f"{_pad('价', 8)}{_pad('量', 8)}{_pad('额(万)', 10)}{_pad('性质', 6)}{_pad('涨幅', 8)}"
        )
        for r in rows:
            lines.append(
                f"  {_pad(r.get('trade_time') or '-', 20)}{_pad(r.get('code') or '-', 8)}"
                f"{_pad(r.get('name') or '-', 12)}{_pad(r.get('price') or '-', 8)}"
                f"{_pad(r.get('volume') or '-', 8)}{_pad(r.get('total_amount') or '-', 10)}"
                f"{_pad(r.get('direction') or '-', 6)}{_pad(r.get('change_pct') or '-', 8)}"
            )
    return ToolResult("\n".join(lines))


def _parse_cn_amount(s: Optional[str]) -> Optional[float]:
    """Parse Tonghuashun amount strings to a float (in yuan).

    '1.69亿' → 169000000.0 · '-3254.51万' → -32545100.0 · '100' → 100.0
    '' / '--' / None → None. Robust to stray spaces / % suffix.
    """
    if not s:
        return None
    t = str(s).strip().replace(",", "").replace("%", "")
    if t in ("", "--", "-"):
        return None
    mult = 1.0
    if t.endswith("亿"):
        mult, t = 1e8, t[:-1]
    elif t.endswith("万"):
        mult, t = 1e4, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def _fmt_yi(v: Optional[float]) -> str:
    """Format a yuan float back to a compact 亿 (100M) / 万 (10K) string."""
    if v is None:
        return "?"
    av = abs(v)
    if av >= 1e8:
        return f"{v/1e8:.2f}亿"
    if av >= 1e4:
        return f"{v/1e4:.0f}万"
    return f"{v:.0f}"


def _tool_fund_flow_change(code: str, target: str = "gegu",
                            limit: int = 5) -> ToolResult:
    """Tonghuashun fund-flow cross-snapshot comparison. Track how main-fund net inflow
    on one stock/sector changes over time.

    Requires multiple snapshots for this code in the ths_fund_flow table (only after
    several collect runs). target=gegu/gainian/hangye/ddzz."""
    from financial_analyst.data.news_db import NewsDB
    db = NewsDB()
    rows = db.query_ths_fund_flow_history(code, target=target, limit=limit)
    db.close()
    if not rows:
        return ToolResult(
            f"无 {code} 在 {target} 榜的历史数据. "
            f"先多次 news_collect --sources ths-{'fund-flow' if target=='gegu' else target} 积累快照."
        )
    if len(rows) == 1:
        r = rows[0]
        return ToolResult(
            f"{code} ({r.get('name','?')}) 仅 1 个快照 "
            f"[{r.get('snapshot_ts')}] 主力净流入={r.get('main_net','?')}. "
            f"再抓一次才能对比变化."
        )
    name = rows[0].get("name", "?")
    lines = [f"{code} ({name}) — {target} 榜主力净流入时间序列 (新→旧):"]
    series: List[tuple] = []
    for r in rows:
        ts = r.get("snapshot_ts", "?")
        mn_raw = r.get("main_net", "")
        mn = _parse_cn_amount(mn_raw)
        series.append((ts, mn, mn_raw))
        lines.append(f"  [{ts}] 主力净流入={mn_raw or '?'}  涨跌幅={r.get('change_pct','?')}")
    # Latest vs previous diff
    (ts0, v0, _), (ts1, v1, _) = series[0], series[1]
    if v0 is not None and v1 is not None:
        delta = v0 - v1
        arrow = "↑增" if delta > 0 else ("↓减" if delta < 0 else "持平")
        lines.append(
            f"\n最近变化 ({ts1} → {ts0}): {arrow} {_fmt_yi(abs(delta))} "
            f"({_fmt_yi(v1)} → {_fmt_yi(v0)})"
        )
    return ToolResult("\n".join(lines))


def _tool_ths_concept_board(mode: str = "new", limit: int = 30,
                              refresh: bool = True) -> ToolResult:
    """Tonghuashun concept boards. mode=new (newly minted concepts, default) / rank (gain ranking)."""
    from financial_analyst.data.news_db import NewsDB
    from financial_analyst.data.collectors.opencli.ths_extra import (
        THSConceptBoardCollector,
    )
    db = NewsDB()
    if refresh:
        try:
            items = THSConceptBoardCollector().fetch(mode=mode, limit=limit)
            if items:
                db.upsert_ths_concept_boards(items)
        except Exception as exc:
            db.close()
            return ToolResult(f"concept-board 抓取失败: {exc}", is_error=True)
    rows = db.query_ths_concept_boards(mode=mode, limit=limit)
    db.close()
    if not rows:
        return ToolResult(
            f"无 concept-board 数据 (mode={mode}). "
            f"mode=rank URL 尚未验证, 可能返回空."
        )
    snap = rows[0].get("snapshot_ts", "?")
    label = "新概念发布表" if mode == "new" else "涨幅榜"
    lines = [f"同花顺概念板块 — {label} (snapshot={snap}, {len(rows)} 条):"]
    for r in rows:
        if mode == "new":
            lines.append(
                f"  [{r.get('release_date', '?')}] {r.get('board_name', '?')} "
                f"({r.get('board_code', '?')}) — {r.get('num_stocks', '?')} 股"
            )
        else:
            lines.append(
                f"  {_pad(r.get('board_code', '?'), 8)}{_pad(r.get('board_name', '?'), 14)}"
                f"{_pad(r.get('change_pct', '?'), 8)}"
            )
    return ToolResult("\n".join(lines))


def _tool_alpha_bench(universe: str = "csi300_active",
                      since: str = "2024-06-01",
                      until: str = "2024-12-31",
                      top: int = 15, save: bool = True) -> ToolResult:
    """Run the alpha-zoo benchmark on a universe. Slow (~3 min on csi300)."""
    cmd = ["financial-analyst", "alpha", "bench",
           "--universe", universe, "--since", since, "--until", until,
           "--top", str(top)]
    if save:
        cmd.append("--save")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              cwd=str(_project_root()))
    except subprocess.TimeoutExpired:
        return ToolResult("alpha bench timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"alpha bench failed: {proc.stderr[-500:]}", is_error=True)
    # Return the top-K table from stdout
    out = proc.stdout
    # Truncate intermediate progress lines
    if "Alpha Bench" in out:
        out = out[out.find("Alpha Bench"):]
    return ToolResult(out[-3000:])


def _tool_alpha_snapshot(universe: str = "csi300_active",
                         asof: str = "2024-12-31",
                         top_n: int = 20) -> ToolResult:
    """Build a top-N alpha snapshot using the latest cached bench. Fast (<2 min)."""
    cmd = ["financial-analyst", "alpha", "snapshot", "auto",
           "--universe", universe, "--until", asof, "--top-n", str(top_n)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300,
                              cwd=str(_project_root()))
    except subprocess.TimeoutExpired:
        return ToolResult("alpha snapshot timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"alpha snapshot failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-2000:])


def _tool_chain_for(code: str) -> ToolResult:
    """Look up which industry-chain products a stock belongs to."""
    from financial_analyst.data.loaders.chain_kb import ChainKBLoader
    loader = ChainKBLoader()
    ctx = loader.chain_context(code, max_peers=6)
    if ctx is None:
        return ToolResult(f"Stock {code} not in any chain product.")
    pp = ctx["primary_product"]
    lines = [
        f"{code} → primary product: {pp['id']} ({pp['display_name']})",
        f"  Chain: {pp['category']} layer={pp['layer']}",
        f"  Role: {pp['role_for_stock']} weight={pp['weight_for_stock']:+.2f}",
        f"  Summary: {pp['summary']}",
        f"  Upstream:   {ctx['upstream_products']}",
        f"  Downstream: {ctx['downstream_products']}",
        f"  Peers ({len(ctx['peer_codes'])}):",
    ]
    for r in ctx["peer_codes"]:
        lines.append(f"    {r['code']} {r['name']} ({r['role']}, w={r['weight']:+.2f})")
    if ctx["catalyst_md"]:
        lines.append(f"\n  Catalyst (excerpt):\n{ctx['catalyst_md'][:500]}")
    return ToolResult("\n".join(lines))


def _tool_stocks_show(code: str, tail: int = 2000) -> ToolResult:
    """Show the per-stock research timeline (your prior notes for this code)."""
    from financial_analyst.data.loaders.stock_timeline import StockTimelineLoader
    loader = StockTimelineLoader()
    text = loader.load_tail(code, max_chars=tail)
    if text is None:
        return ToolResult(f"No timeline file for {code}.")
    return ToolResult(text)


def _tool_industry_show(code: str) -> ToolResult:
    """Look up the Shenwan industry classification for a stock."""
    from financial_analyst.data.loaders.industry import IndustryLoader
    loader = IndustryLoader()
    industry = loader.get(code)
    return ToolResult(f"{code}: {industry}")


def _tool_dream_review() -> ToolResult:
    """List pending memory proposals from the dream loop."""
    import yaml
    from financial_analyst.memory_paths import default_memory_root
    proposed_root = default_memory_root() / "_proposed"
    if not proposed_root.exists():
        return ToolResult("No pending proposals. Run `dream run` to generate some.")
    files = sorted(proposed_root.rglob("*.md"))
    if not files:
        return ToolResult("_proposed/ is empty.")
    lines = [f"{len(files)} pending proposals:"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            fm = {}
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end > 0:
                    fm = yaml.safe_load(text[4:end]) or {}
            agent = f.parent.name
            slug = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
            lines.append(
                f"  [{fm.get('confidence', '?')}] {agent}/{slug}  ({len(fm.get('supporting_cases', []))} cases)"
            )
            lines.append(f"    title: {fm.get('title', '')}")
        except Exception:
            lines.append(f"  err: {f}")
    return ToolResult("\n".join(lines))


def _tool_mainline() -> ToolResult:
    """Run monthly industry-chain mainline radar (5-state per chain)."""
    try:
        proc = subprocess.run(
            ["financial-analyst", "mainline"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
            cwd=str(_project_root()),
        )
    except subprocess.TimeoutExpired:
        return ToolResult("mainline timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"mainline failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-3000:])


def _tool_brief() -> ToolResult:
    """Pre-market morning brief (overnight moves + day's watchlist)."""
    try:
        proc = subprocess.run(
            ["financial-analyst", "brief"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
            cwd=str(_project_root()),
        )
    except subprocess.TimeoutExpired:
        return ToolResult("brief timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"brief failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-3000:])


def _tool_overseas_radar() -> ToolResult:
    """v1.9.7: 国际市场 + 海外新闻传导雷达.

    3-agent swarm: overseas-market-scanner (隔夜美股/港股/VIX) +
    global-news-aggregator (海外格局判读) + macro-impact-analyzer
    (融合 + 写 actionable signals).
    """
    try:
        proc = subprocess.run(
            ["financial-analyst", "overseas-radar"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
            cwd=str(_project_root()),
        )
    except subprocess.TimeoutExpired:
        return ToolResult("overseas-radar timed out.", is_error=True)
    if proc.returncode != 0:
        return ToolResult(f"overseas-radar failed: {proc.stderr[-500:]}", is_error=True)
    return ToolResult(proc.stdout[-3000:])


def _tool_ask_quote(code: str) -> ToolResult:
    """Quick price/PE/PB lookup for a stock (no full report)."""
    from financial_analyst.data.loader_factory import get_default_loader
    import pandas as pd
    loader = get_default_loader()
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    df = loader.fetch_quote(code, start, today)
    if df is None or df.empty:
        return ToolResult(f"No quote data for {code}.", is_error=True)
    last = df.iloc[-1]
    db = loader.fetch_daily_basic(code, start, today)
    line = (
        f"{code}: close={last.get('close', 'N/A'):.2f} "
        f"vol={last.get('vol', last.get('volume', 0)):,.0f}"
    )
    if db is not None and not db.empty:
        b = db.iloc[-1]
        line += (
            f" | PE={b.get('pe_ttm', 'N/A')} PB={b.get('pb', 'N/A')} "
            f"mv={b.get('total_mv', 0)/10000:.0f}亿 turnover={b.get('turnover_rate', 0):.2f}%"
        )
    return ToolResult(line)


def _tool_realtime_quote(code: str) -> ToolResult:
    """Realtime quote. Goes through ``data/quote_fallback`` multi-source chain
    (tencent → xueqiu), returning the first source that succeeds. Unlike
    quote_lookup (daily/EOD), this is the intraday realtime price."""
    from financial_analyst.data.quote_fallback import fetch_realtime_quote
    try:
        src, q = fetch_realtime_quote(code)
    except RuntimeError as exc:
        return ToolResult(f"实时行情抓取失败: {exc}", is_error=True)
    if src == "tencent":
        return ToolResult(
            f"{q.get('name','?')} ({q.get('code', code)})\n"
            f"  现价 {q.get('price')}  涨跌 {q.get('change')} ({q.get('changePercent')}%)\n"
            f"  开 {q.get('open')} 高 {q.get('high')} 低 {q.get('low')} 昨收 {q.get('prevClose')}\n"
            f"  量比 {q.get('vol_ratio')}  换手 {q.get('turnover_rate')}%  振幅 {q.get('amplitude')}%\n"
            f"  PE {q.get('pe')}  PB {q.get('pb')}  总市值 {q.get('total_mv')}亿  成交额 {q.get('amount')}万",
            side_effect={"quote": q, "source": src},
        )
    # xueqiu shape
    return ToolResult(
        f"{q.get('name','?')} ({q.get('symbol', code)}) [{q.get('market_status','?')}]\n"
        f"  现价 {q.get('price','?')}  涨跌 {q.get('change','?')} ({q.get('changePercent','?')})\n"
        f"  开 {q.get('open','?')} 高 {q.get('high','?')} 低 {q.get('low','?')} 昨收 {q.get('prevClose','?')}\n"
        f"  量 {q.get('volume','?')}  额 {q.get('amount','?')}  换手 {q.get('turnover_rate','?')}\n"
        f"  市值 {q.get('marketCap','?')}  振幅 {q.get('amplitude','?')}  时间 {q.get('time','?')}",
        side_effect={"quote": q, "source": src},
    )


def _tool_quote_batch(codes: str) -> ToolResult:
    """Batch realtime quotes (Tencent → Xueqiu fallback). For the monitoring wall /
    multi-stock comparison. ``codes`` is comma-separated (e.g. 'SH600519,SZ300750,002594')."""
    from financial_analyst.data.quote_fallback import fetch_realtime_quotes
    code_list = [c.strip() for c in str(codes).replace("，", ",").split(",") if c.strip()]
    if not code_list:
        return ToolResult("没给股票代码", is_error=True)
    try:
        src, data = fetch_realtime_quotes(code_list)
    except RuntimeError as exc:
        return ToolResult(f"批量行情抓取失败: {exc}", is_error=True)
    if not data:
        return ToolResult("批量行情返回空 (代码可能无效)", is_error=True)
    lines = [f"批量行情 ({len(data)} 只):"]
    lines.append(f"  {_pad('代码', 10)}{_pad('名称', 12)}{_pad('现价', 9)}"
                 f"{_pad('涨跌幅', 9)}{_pad('量比', 7)}{_pad('换手', 7)}{_pad('PE', 8)}")
    for v in data.values():
        chg = v.get("changePercent")
        chg_s = f"{chg:+.2f}%" if chg is not None else "-"
        lines.append(
            f"  {_pad(v.get('code') or '-', 10)}{_pad(v.get('name') or '-', 12)}"
            f"{_pad(v.get('price') if v.get('price') is not None else '-', 9)}"
            f"{_pad(chg_s, 9)}{_pad(v.get('vol_ratio') if v.get('vol_ratio') is not None else '-', 7)}"
            f"{_pad((str(v.get('turnover_rate')) + '%') if v.get('turnover_rate') is not None else '-', 7)}"
            f"{_pad(v.get('pe') if v.get('pe') is not None else '-', 8)}"
        )
    return ToolResult("\n".join(lines), side_effect={"quotes": data})


def _tool_alert_add(code: str, kind: str, threshold: float, note: str = "") -> ToolResult:
    """Add one price-watch alert. kind: price_below (falls through) /
    price_above (rises above) / pct_above (gain breakout) / pct_below
    (drop breakout). threshold is a price or percent value."""
    from financial_analyst.buddy.alerts import AlertStore, VALID_KINDS
    if kind not in VALID_KINDS:
        return ToolResult(
            f"kind 必须是 {VALID_KINDS} 之一, 收到 {kind!r}", is_error=True,
        )
    store = AlertStore()
    try:
        rule = store.add(code, kind, threshold, note)
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    return ToolResult(
        f"✓ 已加盯盘提醒: {rule.describe()}\n"
        f"  (chat 开着 + /watch on 时后台自动盯, 触发弹提醒)"
    )


def _tool_alert_list() -> ToolResult:
    """List all price-watch alerts."""
    from financial_analyst.buddy.alerts import AlertStore
    store = AlertStore()
    rules = store.list()
    if not rules:
        return ToolResult("当前没有盯盘提醒. 加一条: '茅台跌破1200提醒我'")
    lines = [f"{len(rules)} 条盯盘提醒:"]
    for r in rules:
        fired = f"  [上次触发 {r.last_fired}]" if r.last_fired else ""
        lines.append(f"  [{r.id}] {r.describe()}{fired}")
    return ToolResult("\n".join(lines))


def _tool_alert_remove(rule_id: str) -> ToolResult:
    """Remove a price-watch alert. rule_id can be 'SH600519:price_below' or just 'SH600519' (deletes all rules for that stock)."""
    from financial_analyst.buddy.alerts import AlertStore
    store = AlertStore()
    ok = store.remove(rule_id)
    if ok:
        return ToolResult(f"✓ 已删除盯盘提醒: {rule_id}")
    return ToolResult(f"未找到匹配 {rule_id!r} 的提醒", is_error=True)


def _tool_stock_brief(code: str, news_days: int = 7) -> ToolResult:
    """One-shot multi-dimension snapshot of a stock — saves the LLM from
    chaining quote_lookup + chain_for + news_query + ths_fund_flow +
    stocks_show separately (which often blows the tool-iteration cap).

    Pulls (all from local cache / fast loaders — NO slow network scrape):
      行情 · 行业 · 产业链 · 近 N 日新闻 · 雪球评论情绪 · 资金流(若在榜) ·
      上次研报时间线.

    Each section is independently guarded so one missing source doesn't
    sink the whole brief.
    """
    lines = [f"# {code} 速览 (stock_brief)"]

    # --- Quote (realtime preferred: Tencent, same source as UI quick-view card; avoids LLM citing stale EOD price) ---
    try:
        rt = None
        try:
            from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
            rt = TencentQuoteCollector().quote(normalize_code(code))
        except Exception:
            rt = None
        if rt and rt.get("price") is not None:
            lines.append(
                f"\n## 行情 (实时)\n{code}: 现价={rt['price']} "
                f"涨跌={rt.get('changePercent')}% 量比={rt.get('vol_ratio')} "
                f"换手={rt.get('turnover_rate')}% 振幅={rt.get('amplitude')}% "
                f"PE={rt.get('pe')} PB={rt.get('pb')}"
            )
        else:
            q = _tool_ask_quote(code)
            lines.append(f"\n## 行情 (EOD)\n{q.content}")
    except Exception as exc:
        lines.append(f"\n## 行情\n  (取价失败: {exc})")

    # --- Industry + supply chain ---
    try:
        ind = _tool_industry_show(code)
        lines.append(f"\n## 行业\n{ind.content}")
    except Exception:
        pass
    try:
        chain = _tool_chain_for(code)
        # chain output can be long — keep first ~600 chars
        c = chain.content
        lines.append(f"\n## 产业链\n{c[:600]}{'…' if len(c) > 600 else ''}")
    except Exception:
        pass

    # --- Recent news (cache only) + freshness check ---
    try:
        from financial_analyst.data.news_db import NewsDB
        db = NewsDB()
        news = db.query_news(code=code, since_days=news_days, limit=5)
        social = db.query_social_posts(code, since_days=news_days, limit=5)
        # fund-flow if this code is on a cached leaderboard
        ff = db.query_ths_fund_flow_history(code, target="gegu", limit=1)
        db.close()
    except Exception:
        news, social, ff = [], [], []

    if news:
        stale = _news_staleness_note(news)
        lines.append("\n## 近期新闻")
        if stale:
            lines.append(f"  {stale}")
        for r in news:
            lines.append(f"  [{r.get('ts','')[:16]}] {(r.get('title') or '')[:70]}")
    if social:
        lines.append("\n## 雪球评论 (情绪)")
        for r in social:
            lines.append(f"  @{r.get('author','?')}: {(r.get('content') or '')[:70]}")
    if ff:
        r = ff[0]
        lines.append(
            f"\n## 资金流 (同花顺, {r.get('snapshot_ts','?')})\n"
            f"  主力净流入={r.get('main_net','?')} 涨跌幅={r.get('change_pct','?')}"
        )

    # --- Past report timeline ---
    try:
        tl = _tool_stocks_show(code, tail=600)
        if not tl.is_error and "No timeline" not in tl.content:
            c = tl.content
            lines.append(f"\n## 上次研报时间线\n{c[:600]}{'…' if len(c) > 600 else ''}")
    except Exception:
        pass

    lines.append(
        "\n[dim]需要深度研报跑 run_report; 需要刷新新闻跑 news_collect[/dim]"
    )
    # v1.9.0: attach structured snapshot in side_effect for the desktop
    # UI's quick-view card (the LLM only reads .content; the server reads
    # .side_effect["brief"]).
    structured = None
    try:
        structured = brief_data(code)
    except Exception:
        pass
    return ToolResult("\n".join(lines),
                      side_effect={"brief": structured} if structured else None)


def _tool_alpha_list(family: Optional[str] = None) -> ToolResult:
    """List registered alphas (or filter by family: alpha101 / gtja191 / qlib158)."""
    from financial_analyst.factors.zoo import list_alphas
    rows = list_alphas(family=family)
    lines = [f"{len(rows)} alphas registered ({family or 'all'}):"]
    for a in rows[:50]:
        lines.append(f"  {a.name:18s} [{a.family}] {a.description[:80]}")
    if len(rows) > 50:
        lines.append(f"  ... ({len(rows) - 50} more)")
    return ToolResult("\n".join(lines))


def _tool_alpha_show(name: str) -> ToolResult:
    """Show one alpha's formula + paper citation + description."""
    from financial_analyst.factors.zoo.registry import get
    try:
        spec = get(name)
    except KeyError as e:
        return ToolResult(str(e), is_error=True)
    return ToolResult(
        f"{spec.name} ({spec.family})\n"
        f"Paper: {spec.paper}\n"
        f"Description: {spec.description}\n"
        f"Formula: {spec.formula_text}"
    )


from financial_analyst.data.universe import resolve_universe_codes as _resolve_universe_codes


from financial_analyst.factors.zoo.expr import FACTOR_VOCAB as _FACTOR_VOCAB, compile_factor as _factor_compute


def _tool_factor_test(expr: str, universe: str = "csi300_active",
                      since: str = "2024-01-01", until: str = "2024-12-31",
                      fwd_days: int = 5, max_codes: int = 120) -> ToolResult:
    """On-the-fly test of a custom factor expression — actually computes RankIC / ICIR / IC / hit rate (reuses the alpha-zoo IC engine)."""
    import math
    expr = (expr or "").strip()
    if not expr:
        return ToolResult("factor_test: 缺少 expr (因子表达式)。", is_error=True)
    if "__" in expr or "import" in expr or "lambda" in expr:
        return ToolResult("factor_test: 表达式含非法 token (__ / import / lambda)。", is_error=True)
    try:
        import warnings
        import numpy as np
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel import PanelData
        from financial_analyst.factors.zoo.bench_runner import _forward_returns, bench_one
        from financial_analyst.factors.zoo.registry import AlphaSpec

        codes = _resolve_universe_codes(universe)
        if not codes:
            return ToolResult(f"factor_test: 找不到 universe '{universe}' (可试 csi300 / csi500 / csi300_active)。", is_error=True)
        codes = codes[: max(20, min(int(max_codes), len(codes)))]

        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind_loader = None

        panel = PanelData.from_loader(loader, codes, since, until, freq="day",
                                      industry_loader=ind_loader)
        spec = AlphaSpec(name="__custom__", family="custom",
                         description="user custom factor", formula_text=expr,
                         compute=_factor_compute(expr))
        fwd = _forward_returns(panel, int(fwd_days))
        with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
            warnings.simplefilter("ignore")
            res = bench_one(spec, panel, fwd)
    except Exception as e:
        return ToolResult(f"factor_test 失败: {type(e).__name__}: {e}\n可用 {_FACTOR_VOCAB}", is_error=True)

    if res.get("status") != "ok":
        return ToolResult(
            f"因子表达式无法计算 (status={res.get('status')}):\n  {res.get('error')}\n\n可用 {_FACTOR_VOCAB}",
            is_error=True)

    def fmt(x, d=4):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    rank_ic, rank_ir = res["rank_ic"], res["rank_ir"]
    state = res.get("state", "无数据")   # values: effective / mediocre / weak / dead (health class, same as alpha_bench)
    direction = "正向 (因子值高→未来涨)" if (rank_ic or 0) > 0 else "反向 (因子值高→未来跌)"
    hit = res.get("hit_rate")
    hit_s = f"{hit*100:.1f}%" if (hit is not None and hit == hit) else "—"
    return ToolResult(
        f"# 自定义因子测试\n"
        f"表达式: {expr}\n"
        f"universe: {universe} ({len(codes)} 只) · {since}~{until} · fwd={fwd_days}d · 面板 {panel.n_dates()} 天\n\n"
        f"RankIC = {fmt(rank_ic)} | RankICIR = {fmt(rank_ir, 3)} | IC = {fmt(res['ic'])} | ICIR = {fmt(res['ir'], 3)}\n"
        f"命中率 = {hit_s} | 有效天数 = {res['n_dates']} | 样本 = {res['n_obs']}\n\n"
        f"健康分类: 【{state}】 · 方向 {direction}\n"
        f"(分类口径: |RankICIR|≥0.5 且 |RankIC|≥0.02 = 有效; 0.3-0.5 = 一般; 0.15-0.3 = 偏弱; <0.15 = 失效)\n"
        f"注: 这是 {len(codes)} 只样本的快测, 严肃结论需扩 universe + 多窗口稳健性检验。"
    )


def _tool_alpha_compare(items, universe: str = "csi300_active",
                        since: str = "2024-01-01", until: str = "2024-12-31",
                        fwd_days: int = 5, max_codes: int = 120) -> ToolResult:
    """Side-by-side comparison of multiple factors (registered names like alpha019 or custom expressions): IC/ICIR/hit-rate/health class."""
    import re as _re
    import math
    if isinstance(items, str):
        items = [x.strip() for x in _re.split(r"[;\n]+", items) if x.strip()]
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    if len(items) < 2:
        return ToolResult("alpha_compare: 至少给 2 个因子 (已注册名如 alpha019, 或表达式如 rank(-delta(close,5)))。", is_error=True)
    items = items[:8]
    try:
        import warnings
        import numpy as np
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel import PanelData
        from financial_analyst.factors.zoo.bench_runner import _forward_returns, bench_one
        from financial_analyst.factors.zoo.registry import AlphaSpec, get as _get_alpha

        codes = _resolve_universe_codes(universe)
        if not codes:
            return ToolResult(f"alpha_compare: 找不到 universe '{universe}'。", is_error=True)
        codes = codes[: max(20, min(int(max_codes), len(codes)))]
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind_loader = None
        panel = PanelData.from_loader(loader, codes, since, until, freq="day", industry_loader=ind_loader)
        fwd = _forward_returns(panel, int(fwd_days))
        rows = []
        with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
            warnings.simplefilter("ignore")
            for it in items:
                try:
                    spec = _get_alpha(it); label = it
                except KeyError:
                    if "__" in it or "import" in it or "lambda" in it:
                        rows.append({"label": it[:36], "bad": "非法表达式"}); continue
                    spec = AlphaSpec(name="__c__", family="custom", description="", formula_text=it, compute=_factor_compute(it))
                    label = it[:40]
                r = bench_one(spec, panel, fwd); r["label"] = label
                rows.append(r)
    except Exception as e:
        return ToolResult(f"alpha_compare 失败: {type(e).__name__}: {e}\n可用 {_FACTOR_VOCAB}", is_error=True)

    def fmt(x, d=4):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    def keyf(r):
        v = r.get("rank_ir")
        return abs(v) if (v is not None and v == v) else -1.0

    ok = [r for r in rows if "bad" not in r]
    ok.sort(key=keyf, reverse=True)
    lines = [f"# 因子对比 · {universe} ({len(codes)} 只) · {since}~{until} · fwd={fwd_days}d · 面板 {panel.n_dates()} 天", ""]
    for r in ok:
        if r.get("status") != "ok":
            lines.append(f"· {r['label']} — 无法计算: {r.get('error', '')[:50]}")
            continue
        hit = r.get("hit_rate")
        hs = f"{hit*100:.0f}%" if (hit is not None and hit == hit) else "—"
        lines.append(f"· {r['label']}\n    RankIC={fmt(r['rank_ic'])} · RankICIR={fmt(r['rank_ir'],2)} · 命中={hs} · 状态【{r.get('state','')}】")
    for r in rows:
        if "bad" in r:
            lines.append(f"· {r['label']} — ⛔ {r['bad']}")
    if ok and ok[0].get("status") == "ok":
        b = ok[0]
        lines.append("")
        lines.append(f"→ 本组最强: {b['label']} (RankICIR={fmt(b['rank_ir'],2)}, 状态【{b.get('state')}】)")
    return ToolResult("\n".join(lines))


def _quick_ic(compute_fn, universe: str, since: str, until: str,
              fwd_days: int = 5, max_codes: int = 120) -> dict:
    """Quick cross-sectional IC of a compute fn (reuses factor_test's bench path)."""
    import warnings
    import numpy as np
    from financial_analyst.factors.zoo.panel import PanelData
    from financial_analyst.factors.zoo.bench_runner import _forward_returns, bench_one
    from financial_analyst.factors.zoo.registry import AlphaSpec
    # Import resolve_universe_codes lazily so monkeypatching the module attr takes effect
    from financial_analyst.data import universe as _univ_mod
    codes = _univ_mod.resolve_universe_codes(universe)
    if not codes:
        return {"status": "no_universe"}
    codes = codes[: max(20, min(int(max_codes), len(codes)))]
    # Import get_default_loader lazily so monkeypatching the module attr takes effect
    from financial_analyst.data import loader_factory as _lf_mod
    loader = _lf_mod.get_default_loader()
    try:
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
        ind = IndustryLoader() if industry_map_path().exists() else None
    except Exception:
        ind = None
    panel = PanelData.from_loader(loader, codes, since, until, freq="day", industry_loader=ind)
    spec = AlphaSpec(name="__forge__", family="custom", description="", formula_text="", compute=compute_fn)
    fwd = _forward_returns(panel, int(fwd_days))
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.simplefilter("ignore")
        return bench_one(spec, panel, fwd)


def _tool_alpha_forge(idea: str, save: bool = False, universe: str = "csi300_active",
                      since: str = "2024-01-01", until: str = "2024-12-31",
                      quick_eval: bool = True) -> ToolResult:
    """炼因子: 自然语言想法 → 截面因子表达式 + 快测 IC, 可入库 (之后可被 factor_report 引用)。"""
    import math
    from financial_analyst.factors import forge as _forge_mod
    fr = _forge_mod.forge_factor(idea)
    if fr.out_of_vocab:
        return ToolResult(f"这个想法当前价量 DSL 炼不了: {fr.error}\n"
                          f"(基本面字段→SP-B.1b, 事件信号→SP-B.2)", is_error=True)
    if not fr.compile_ok:
        return ToolResult(f"炼因子失败: {fr.error}", is_error=True)

    lines = [f"# 炼因子 · {fr.name}", f"原话: {idea}", "", "解析:"]
    lines += [f"  · {p.get('k')}: {p.get('v')}" for p in fr.parsed]
    lines += ["", f"公式: {fr.expr}", f"逻辑: {fr.rationale}"]

    kpis: dict = {}
    if quick_eval:
        from financial_analyst.factors.zoo.expr import compile_factor
        try:
            kpis = _quick_ic(compile_factor(fr.expr), universe, since, until)
            if kpis.get("status") == "ok":
                def f(x):
                    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.4f}"
                lines += ["", f"快测 IC ({universe}): RankIC={f(kpis.get('rank_ic'))} "
                          f"RankICIR={f(kpis.get('rank_ir'))} 命中={kpis.get('hit_rate')} 【{kpis.get('state')}】"]
            else:
                lines += ["", f"快测跳过 (universe={universe} 无法解析)"]
        # quick-eval failure is non-fatal: the forged expression is valid even if
        # the data load/IC eval fails — surface it inline, keep is_error=False.
        except Exception as e:
            lines += ["", f"快测失败: {type(e).__name__}: {e}"]

    if save:
        from financial_analyst.factors.forge import UserFactorStore
        entry = UserFactorStore().add({"name": fr.name, "family": "user", "expr": fr.expr,
                                       "description": fr.rationale[:60], "parsed": fr.parsed,
                                       "kpis": kpis if kpis.get("status") == "ok" else {}})
        lines += ["", f"✓ 已入库: {entry['name']} — 可 `factor_report {entry['name']}` 跑完整评测"]
    else:
        lines += ["", "(save=true 入库后可被 factor_report / alpha_compare 按名引用)"]
    return ToolResult("\n".join(lines))


def _tool_user_factors(remove: str = "") -> ToolResult:
    """列出已入库的 user 因子; remove=<name> 删除一个。"""
    from financial_analyst.factors.forge import UserFactorStore
    store = UserFactorStore()
    if remove:
        ok = store.remove(remove)
        return ToolResult(f"{'已删除' if ok else '未找到'} user 因子: {remove}")
    rows = store.list()
    if not rows:
        return ToolResult("暂无已入库 user 因子。用 alpha_forge(idea=..., save=true) 炼一个。")
    lines = [f"# 已入库 user 因子 ({len(rows)})"]
    for e in rows:
        k = e.get("kpis") or {}
        ric = k.get("rank_ic")
        lines.append(f"· {e['name']}  {e.get('expr','')}"
                     + (f"  (RankIC={ric:+.4f})" if isinstance(ric, (int, float)) else ""))
    return ToolResult("\n".join(lines))


def _tool_factor_report(expr_or_name: str, universe: str = "csi500",
                        freq: str = "month", start: str = None, end: str = None,
                        archive: bool = False, note: str = "") -> ToolResult:
    """完整单因子评测报告: IC全套 + 十分位 + 多空组合净值/Sharpe/回撤 (复用 factors.eval 引擎)。

    archive=True 时把本次运行 opt-in 持久化到研究档案 (runs.jsonl), 供 research_log
    看迭代趋势; note 是这次运行的备注。归档失败绝不拖垮报告主体。"""
    expr_or_name = (expr_or_name or "").strip()
    if not expr_or_name:
        return ToolResult("factor_report: 缺少 expr_or_name (因子名或表达式)。", is_error=True)
    if "__" in expr_or_name or "import" in expr_or_name or "lambda" in expr_or_name:
        return ToolResult("factor_report: 表达式含非法 token (__ / import / lambda)。", is_error=True)
    try:
        from financial_analyst.factors.eval import EvalConfig, factor_report
        cfg = EvalConfig(universe=universe, freq=freq, start=start, end=end)
        rpt = factor_report(expr_or_name, cfg)
    except Exception as e:
        return ToolResult(f"factor_report 失败: {type(e).__name__}: {e}", is_error=True)

    if rpt.status != "ok":
        return ToolResult(f"因子评测未完成 (status={rpt.status}): {rpt.error}", is_error=True)

    import math
    def f(x, d=3):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    ic, q, pf, ch, m = rpt.ic, rpt.quantile, rpt.portfolio, rpt.characteristics, rpt.meta
    lines = [
        f"# 因子评测 · {m.factor}",
        f"池 {m.universe} ({m.n_codes} 只) · {m.freq}频 · {m.start}~{m.end} · {m.n_dates} 期 · fwd={m.fwd_days}d",
        "",
        f"IC = {f(ic.ic_mean,4)} | ICIR = {f(ic.icir)} | t = {f(ic.ic_tstat,2)} | 命中 = {f(ic.ic_win_rate,2)}",
        f"RankIC = {f(ic.rank_ic_mean,4)} | RankICIR = {f(ic.rank_icir)}",
        f"十分位单调性 = {f(q.monotonicity,2)} | 多空价差(年化) = {f(q.long_short_spread,3)}",
        f"多空组合: 年化 = {f(pf.ann_return,3)} | Sharpe = {f(pf.sharpe,2)} | 最大回撤 = {f(pf.max_drawdown,3)} | 换手 = {f(pf.turnover,2)} | 胜率 = {f(pf.win_rate,2)}",
        f"覆盖率 = {f(ch.coverage,2)} | 自相关 = {f(ch.autocorr_1,2)} | 半衰期 = {'—' if ch.half_life < 0 else format(ch.half_life, '.0f')} 期",
    ]
    if rpt.warnings:
        lines.append("")
        lines += [f"⚠ {w}" for w in rpt.warnings]

    if archive:
        try:
            from financial_analyst.factors.research import ResearchArchive, record_from_report
            rec = ResearchArchive().append(record_from_report(rpt, note=note))
            lines.append(f"\n✓ 已归档 (id={rec.id})")
        except Exception as e:
            lines.append(f"\n⚠ 归档失败: {e}")
    return ToolResult("\n".join(lines))


_COMPOSE_METHOD_CN = {
    "equal": "等权",
    "ic_weighted": "IC 加权",
    "linear": "线性回归",
    "lgbm": "LightGBM",
}


def _tool_factor_compose(members=None, method: str = "lgbm",
                         universe: str = "csi300_active", freq: str = "month",
                         since: str = None, until: str = None,
                         train_frac: float = 0.6, goal: str = "",
                         archive: bool = False, note: str = "") -> ToolResult:
    """把 N(>=2) 个因子合成一个综合打分, 样本外(OOS)评测综合分并和各成员对比 → 判断合成是否增益。

    archive=True 时把本次合成运行 opt-in 持久化到研究档案 (runs.jsonl), 供 research_log
    看迭代趋势; note 是备注。归档失败绝不拖垮报告主体。"""
    import math
    import re as _re

    # 0) goal 给了 → 先 LLM 顾问出配方 (成员/方法)。
    goal = (goal or "").strip()
    if goal:
        from financial_analyst.factors.compose import advisor as _advisor_mod
        rec = _advisor_mod.compose_advisor(goal)
        if rec.status != "ok":
            return ToolResult(f"配方生成失败 (status={rec.status}): {rec.error}", is_error=True)
        members, method, train_frac = rec.members, rec.method, rec.train_frac

    # 1) 归一化 members (允许 list 或 ;/换行 分隔的字符串, 仿 alpha_compare)。
    if isinstance(members, str):
        members = [x.strip() for x in _re.split(r"[;\n]+", members) if x.strip()]
    members = [str(x).strip() for x in (members or []) if str(x).strip()]
    if len(members) < 2:
        return ToolResult(
            "factor_compose: 至少给 2 个因子 (已注册名如 alpha019, 或表达式如 rank(-delta(close,5)))。",
            is_error=True,
        )

    # 2) 跑合成 (compose_factors 永不抛, 失败由 status/error 表达)。
    try:
        from financial_analyst.factors.compose import compose_factors
        from financial_analyst.factors.eval import EvalConfig
        cfg = EvalConfig(universe=universe, freq=freq, start=since, end=until)
        res = compose_factors(members, config=cfg, method=method, train_frac=float(train_frac))
    except Exception as e:
        return ToolResult(f"factor_compose 失败: {type(e).__name__}: {e}", is_error=True)

    if res.status != "ok":
        _msg = {
            "too_few_factors": "有效成员不足 2 个",
            "empty_universe": f"universe '{universe}' 解析为空 (试 csi300_active / csi500)",
            "load_error": "行情加载失败",
            "fit_error": "权重拟合失败",
        }.get(res.status, res.status)
        body = f"因子合成未完成 ({_msg}): {res.error}"
        if res.warnings:
            body += "\n" + "\n".join(f"⚠ {w}" for w in res.warnings)
        return ToolResult(body, is_error=True)

    def f(x, d=3):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    method_cn = _COMPOSE_METHOD_CN.get(res.method, res.method)
    comp = res.composite
    ic = comp.ic if comp is not None else None
    pf = comp.portfolio if comp is not None else None

    lines = [
        f"# 多因子合成 · {method_cn}({res.method})",
        f"成员 ({len(res.members)}): {', '.join(res.members)}",
        f"池 {universe} · {freq}频 · train_frac={res.train_frac:.2f} "
        f"(训练 {res.n_train_dates} 期 / OOS 测试 {res.n_test_dates} 期)",
        "",
        "## 权重 / 重要度",
    ]
    wlabel = "重要度" if res.method == "lgbm" else "权重"
    for nm, w in res.weights.items():
        lines.append(f"  {_pad(nm, 40)} {wlabel}={f(w, 3)}")

    lines += [
        "",
        "## 综合分 OOS 表现",
        f"  RankIC = {f(ic.rank_ic_mean if ic is not None else None, 4)} | "
        f"RankICIR = {f(ic.rank_icir if ic is not None else None, 3)}",
        f"  多空组合: Sharpe = {f(pf.sharpe if pf is not None else None, 2)} | "
        f"年化 = {f(pf.ann_return if pf is not None else None, 3)} | "
        f"最大回撤 = {f(pf.max_drawdown if pf is not None else None, 3)}",
        "",
        "## 成员同窗 OOS 对比",
        f"  {_pad('因子', 40)}{_pad('RankIC', 12)}{_pad('Sharpe', 10)}",
    ]
    for m in res.member_oos:
        lines.append(f"  {_pad(m.name, 40)}{_pad(f(m.rank_ic, 4), 12)}{_pad(f(m.sharpe, 2), 10)}")

    lines += ["", f"→ 结论: {res.verdict}"]
    if res.warnings:
        lines.append("")
        lines += [f"⚠ {w}" for w in res.warnings]

    if archive:
        try:
            from financial_analyst.factors.research import ResearchArchive, record_from_compose
            rec = ResearchArchive().append(record_from_compose(res, note=note))
            lines.append(f"\n✓ 已归档 (id={rec.id})")
        except Exception as e:
            lines.append(f"\n⚠ 归档失败: {e}")
    try:
        from financial_analyst.factors.compose import advisor as _advisor_mod
        note_txt = _advisor_mod.interpret_compose(res)
        if note_txt:
            lines += ["", "## LLM 研判", note_txt]
    except Exception:
        pass
    return ToolResult("\n".join(lines))


# 研究档案渲染用的关键指标列 (扁平 metrics dict 里的键)。
_RLOG_KEYS = ("rank_icir", "rank_ic_mean", "icir", "ic_mean", "sharpe", "ann_return")


def _rlog_num(x, d: int = 3) -> str:
    """metrics 数值 → 紧凑字符串。None/NaN/非数 → '—'。"""
    import math
    if x is None:
        return "—"
    if isinstance(x, bool):  # bool 是 int 子类, 不当数值。
        return str(x)
    if isinstance(x, (int, float)):
        if isinstance(x, float) and math.isnan(x):
            return "—"
        return f"{x:+.{d}f}"
    return str(x)


def _rlog_metric_cells(metrics: dict) -> str:
    """把 metrics 里出现的关键指标拼成一行 (k=v 串), 只取 _RLOG_KEYS 里有的。"""
    cells = []
    for k in _RLOG_KEYS:
        if k in metrics:
            cells.append(f"{k}={_rlog_num(metrics[k])}")
    return " ".join(cells)


def _tool_research_log(target: str = "", compare: str = "") -> ToolResult:
    """列出/对比已归档的评测运行历史 (research archive), 看因子或合成模型迭代的指标趋势。

    - compare='id_a,id_b' → 两次运行关键指标并排 + 指标 diff (b - a)。
    - target=X (非空)     → X 标的的运行历史 (时间序, 看趋势)。
    - 都空                → 最近 20 条运行 (id/kind/target/freq + 关键指标 + note)。
    """
    from financial_analyst.factors.research import ResearchArchive
    arc = ResearchArchive()

    # --- 对比两次运行 -------------------------------------------------------
    if compare and "," in compare:
        id_a, _, id_b = compare.partition(",")
        id_a, id_b = id_a.strip(), id_b.strip()
        out = arc.compare(id_a, id_b)
        if "error" in out:
            return ToolResult(f"无法对比: {out['error']}", is_error=True)
        a, b = out["a"], out["b"]
        ta, tb = out["targets"]
        lines = [
            f"# 运行对比: {a['id']} → {b['id']}",
            f"  A [{a['id']}] {a['kind']} · {ta} · {a['freq']}",
            f"  B [{b['id']}] {b['kind']} · {tb} · {b['freq']}",
            "",
            f"  {_pad('指标', 16)}{_pad('A (' + a['id'] + ')', 14)}{_pad('B (' + b['id'] + ')', 14)}{_pad('Δ (B-A)', 12)}",
        ]
        diffs = out["metric_diffs"]
        am, bm = a["metrics"], b["metrics"]
        # 展示所有有 diff 的数值键 (按 _RLOG_KEYS 优先, 再补其余)。
        ordered = [k for k in _RLOG_KEYS if k in diffs]
        ordered += [k for k in sorted(diffs) if k not in ordered]
        for k in ordered:
            lines.append(
                f"  {_pad(k, 16)}{_pad(_rlog_num(am.get(k)), 14)}"
                f"{_pad(_rlog_num(bm.get(k)), 14)}{_pad(_rlog_num(diffs[k]), 12)}"
            )
        if not ordered:
            lines.append("  (两次运行无公共数值指标可对比)")
        return ToolResult("\n".join(lines))

    # --- 单标的历史 (时间序趋势) --------------------------------------------
    if target:
        recs = arc.history(target)
        if not recs:
            return ToolResult(f"无该标的的归档运行: {target!r}")
        lines = [f"# 运行历史: {target} ({len(recs)} 次, 时间序)"]
        for r in recs:
            cells = _rlog_metric_cells(r.metrics)
            ts = (r.timestamp or "")[:19]
            note = f"  // {r.note}" if r.note else ""
            lines.append(f"  [{r.id}] {ts} · {r.freq}  {cells}{note}")
        return ToolResult("\n".join(lines))

    # --- 最近 N 条 ----------------------------------------------------------
    recs = arc.list()
    if not recs:
        return ToolResult(
            "研究档案为空. 跑 factor_report / factor_compose 时带 archive=true 即可归档。"
        )
    recent = recs[-20:][::-1]  # 最近 20 条, 新→旧
    lines = [f"# 研究档案 (共 {len(recs)} 次, 显示最近 {len(recent)} 次):"]
    for r in recent:
        cells = _rlog_metric_cells(r.metrics)
        note = f"  // {r.note}" if r.note else ""
        lines.append(
            f"  [{r.id}] {_pad(r.kind, 8)}{_pad(r.target, 32)}{_pad(r.freq, 6)} {cells}{note}"
        )
    return ToolResult("\n".join(lines))


def _wisdom_store():
    """Resolve WisdomStore, honouring FA_WISDOM_ROOT (tests inject tmp)."""
    from pathlib import Path
    from financial_analyst.wisdom.store import WisdomStore
    root = os.environ.get("FA_WISDOM_ROOT")
    return WisdomStore(root=Path(root) if root else None)


def _tool_wisdom_review(action: str, card_id: Optional[str] = None,
                        reviewed_by: Optional[str] = None) -> ToolResult:
    """审阅视频经验草稿卡. action: list | approve | reject.

    - list: 列出待审 draft 卡 (按 quality_score 降序), 给出 id/标题/自评分/置信/互证.
    - approve: 把指定卡移入正式知识库 (approved), 之后 wisdom_search 可命中.
    - reject: 把指定卡移入 rejected (留痕防重复抽取).
    """
    store = _wisdom_store()
    if action == "list":
        drafts = sorted(store.list_by_status("draft"),
                        key=lambda c: c.quality_score, reverse=True)
        if not drafts:
            return ToolResult("没有待审经验草稿.")
        lines = [
            f"- {c.id} | score={c.quality_score:.2f} | {c.confidence} | {c.title}"
            f"{' | 互证 ' + ','.join(c.corroborates) if c.corroborates else ''}"
            for c in drafts
        ]
        return ToolResult("待审经验草稿 (按自评分降序):\n" + "\n".join(lines))
    if action in ("approve", "reject"):
        if not card_id:
            return ToolResult("approve/reject 需要 card_id", is_error=True)
        new_status = "approved" if action == "approve" else "rejected"
        try:
            store.set_status(card_id, new_status, reviewed_by=reviewed_by)
        except KeyError:
            return ToolResult(f"找不到经验卡 {card_id}", is_error=True)
        return ToolResult(f"{card_id} -> {new_status}")
    return ToolResult(f"未知 action: {action} (应为 list/approve/reject)", is_error=True)


def _tool_wisdom_search(query: str, tags: Optional[list] = None,
                        top_k: int = 5) -> ToolResult:
    """检索已沉淀的视频投资经验 (仅 approved). 返回相关经验卡正文供研判引用.

    query: 关键词 (子串匹配); tags: 可选标签过滤; top_k: 返回条数.
    """
    from financial_analyst.knowledge.local_markdown import LocalMarkdownKB
    store = _wisdom_store()
    kb = LocalMarkdownKB(store.root / "approved")
    hits = kb.query(query, top_k=top_k)
    if tags:
        hits = [h for h in hits if any(t in h["content"] for t in tags)]
    if not hits:
        return ToolResult(f"未找到匹配 {query!r} 的经验卡.")
    blocks = [f"### {h['path']}\n{h['content']}" for h in hits]
    return ToolResult("\n\n---\n\n".join(blocks))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _tool_event_report(expr_or_name: str, universe: str = "csi300_active",
                       start: str = None, end: str = None, horizons: str = "1,5,10,20") -> ToolResult:
    """事件研究: 把触发表达式 (cross/比较/ts_* 求值为布尔信号) 当事件, 统计事件后
    各 horizon 的前向收益 (原始+市场调整) / 胜率 / t值 / CAR / 逐年。截面因子请用 factor_report。"""
    expr_or_name = (expr_or_name or "").strip()
    if not expr_or_name:
        return ToolResult("event_report: 缺少 expr_or_name (触发表达式或因子名)。", is_error=True)
    if "__" in expr_or_name or "import" in expr_or_name or "lambda" in expr_or_name:
        return ToolResult("event_report: 表达式含非法 token (__ / import / lambda)。", is_error=True)
    try:
        hs = tuple(int(x) for x in str(horizons).split(",") if x.strip())
    except ValueError:
        hs = (1, 5, 10, 20)
    try:
        from financial_analyst.factors.eval import EvalConfig, event_report
        rpt = event_report(expr_or_name, EvalConfig(universe=universe, start=start, end=end),
                           horizons=hs or (1, 5, 10, 20))
    except Exception as e:
        return ToolResult(f"event_report 失败: {type(e).__name__}: {e}", is_error=True)
    if rpt.status != "ok":
        return ToolResult(f"事件研究未完成 (status={rpt.status}): {rpt.error}", is_error=True)

    import math

    def f(x, d=3):
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{d}f}"

    lines = [
        f"# 事件研究 · {rpt.factor}",
        f"池 {rpt.universe} ({rpt.n_codes} 只) · {rpt.start}~{rpt.end} · 事件数 {rpt.n_events} · 事件率 {rpt.event_rate:.1%}",
        "",
        "horizon | n | 平均收益 | 超额(减市场) | 胜率 | t值",
    ]
    for h in rpt.horizons:
        lines.append(f"{h.h}d | {h.n} | {f(h.mean_ret)} | {f(h.mean_excess)} | {f(h.win_rate,2)} | {f(h.t_stat,2)}")
    if rpt.by_year:
        lines += ["", "逐年(主5d超额): " + " · ".join(f"{y}:{f(m)}({n})" for y, n, m in rpt.by_year)]
    if rpt.warnings:
        lines += [""] + [f"⚠ {w}" for w in rpt.warnings]
    return ToolResult("\n".join(lines))


TOOL_REGISTRY: List[Tool] = [
    Tool(
        name="run_report",
        description=(
            "Run a complete single-stock deep-dive research report (中文研报). "
            "Takes 5-8 minutes. Outputs star rating, target price, stop loss, "
            "position size, bull/bear arguments, and risk officer review. "
            "Use this when the user asks for a deep analysis / 研报 / full report. "
            "DO NOT use this if the user just wants a quick price quote."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stock code in Qlib format, e.g. SH600519 / SZ002594 / BJ430489."},
                "asof": {"type": "string", "description": "As-of date YYYY-MM-DD (default: today). Use the most recent trading day for current analysis."},
            },
            "required": ["code"],
        },
        run=_tool_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="run_etf_report",
        description=(
            "Run a complete ETF deep-dive research report (中文 ETF 研报). "
            "Takes 5-8 minutes. Outputs a 5-dim rating (持仓/技术/资金流-申赎/"
            "估值-折溢价/风控), target/stop, premium-discount, tracking error, "
            "holdings concentration. Use ONLY for ETF codes (5/15 开头, e.g. "
            "510300 / SH510300 / 159915). For stocks use run_report. "
            "DO NOT use for a quick price quote (use realtime_quote)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "ETF code, e.g. 510300 / SH510300 / 159915 / SZ159915."},
                "asof": {"type": "string", "description": "As-of date YYYY-MM-DD (default: today)."},
            },
            "required": ["code"],
        },
        run=_tool_etf_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="quote_lookup",
        description=(
            "Quick price / PE / PB / market cap / turnover lookup for one stock. "
            "Instant. Use for 看下 X 现在多少钱 / PE 多少 / 市值 type quick questions."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_ask_quote,
    ),
    Tool(
        name="stock_brief",
        description=(
            "ONE-SHOT 多维速览: 行情+行业+产业链+近期新闻+雪球情绪+资金流+上次研报, "
            "一次返回. 当用户问 '看下 X 怎么样' / 'X 最近如何' / '帮我了解 X' 这种"
            "宽泛问题时, 优先用这个, 而不是分别调 quote_lookup/chain_for/news_query — "
            "省 round-trip. 全部读本地 cache + 快速 loader, 不触发慢网络抓取."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "news_days": {"type": "integer", "default": 7},
            },
            "required": ["code"],
        },
        run=_tool_stock_brief,
        cost_hint="seconds",
    ),
    Tool(
        name="news_query",
        description=(
            "Query the local news database (SQLite + FTS5). "
            "Can filter by stock code, by FTS keyword, or both. "
            "Use when the user asks about recent news for a stock or theme. "
            "If results are empty, call `news_collect` FIRST to refresh the DB, "
            "THEN query again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Optional stock code filter (omit for market-wide)"},
                "days": {"type": "integer", "default": 7},
                "fts": {"type": "string", "description": "Optional full-text-search keyword (Chinese supported)"},
                "limit": {"type": "integer", "default": 10},
            },
        },
        run=_tool_news_query,
    ),
    Tool(
        name="news_collect",
        description=(
            "Pull fresh news from upstream into the local DB. "
            "Use when `news_query` returns empty, or when user explicitly "
            "asks for 最新 / 今日 / 今天 news, or wants 雪球 sentiment. "
            "Sources: kuaixun (东方财富快讯) / longhu (龙虎榜) / sinafinance / "
            "shareholders / xueqiu-comments (per-stock retail sentiment, needs code) / "
            "xueqiu-hot (xueqiu trending tickers) / xueqiu-hot-posts (xueqiu platform-wide trending posts) / "
            "xueqiu-feed (followed-user timeline) / xueqiu-earnings / "
            "ths-hot (同花顺热股榜, public no-login, tag-rich). Takes 30-120s."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sources": {
                    "type": "string",
                    "description": "Comma-separated source list. Public (no auth): kuaixun,longhu,sinafinance,shareholders,ths-hot. Cookie-mode (needs Chrome ext): xueqiu-comments,xueqiu-hot,xueqiu-hot-posts,xueqiu-feed,xueqiu-earnings.",
                    "default": "kuaixun,longhu,sinafinance",
                },
                "limit": {"type": "integer", "default": 200, "description": "Max items per source"},
                "code": {"type": "string", "description": "Required only for xueqiu-comments (per-stock)."},
            },
        },
        run=_tool_news_collect,
        cost_hint="seconds",
    ),
    Tool(
        name="alpha_bench",
        description=(
            "Run the full 442-alpha bench against a universe over a date range. "
            "Outputs top-N alphas by |rank_IR|. Takes ~3 minutes on csi300. "
            "Use when the user wants to find the strongest alphas right now."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-06-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "top": {"type": "integer", "default": 15},
            },
        },
        run=_tool_alpha_bench,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="alpha_snapshot",
        description=(
            "Build a top-N alpha snapshot for a universe using the latest "
            "cached bench. Reports will automatically use this snapshot. "
            "Use when the user wants to refresh the alpha signals before "
            "running reports."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "universe": {"type": "string", "default": "csi300_active"},
                "asof": {"type": "string", "default": "2024-12-31"},
                "top_n": {"type": "integer", "default": 20},
            },
        },
        run=_tool_alpha_snapshot,
        cost_hint="seconds",
    ),
    Tool(
        name="alpha_list",
        description="List all registered alphas (or filter by family: alpha101 / gtja191 / qlib158).",
        input_schema={
            "type": "object",
            "properties": {"family": {"type": "string", "enum": ["alpha101", "gtja191", "qlib158", None]}},
        },
        run=_tool_alpha_list,
    ),
    Tool(
        name="alpha_show",
        description="Show one alpha's formula + paper citation + description.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Alpha name, e.g. gtja089, alpha040, qlib_VSTD60"}},
            "required": ["name"],
        },
        run=_tool_alpha_show,
    ),
    Tool(
        name="factor_test",
        description=(
            "现搭现测一个【自定义因子表达式】, 真算它的横截面 RankIC / ICIR / IC / 命中率 "
            "(复用 alpha-zoo 的 PanelData + IC 引擎, 不是估算)。用于用户说「帮我做/测一个新因子」、"
            "「xx 这个想法的 IC 怎么样」等。expr 必须用下面的字段+算子拼 (Python 语法), 例如:\n"
            "  rank(-delta(close,5))                     # 5日反转\n"
            "  rank(-delta(close,5)) * (volume/ts_mean(volume,5))   # 5日反转×量比\n"
            "  -1*correlation(rank(volume), rank(returns), 10)\n"
            "可用 — " + _FACTOR_VOCAB + "\n"
            "默认在 csi300_active 取前 120 只 (~30-60s); 想更全可调 max_codes / universe / 时间窗。"
            "结果是某一段历史的快测, 严肃结论需多窗口稳健性检验。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expr": {"type": "string", "description": "因子表达式, 用 close/volume/rank/delta 等拼, 如 rank(-delta(close,5))"},
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-01-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "fwd_days": {"type": "integer", "default": 5, "description": "未来 N 日收益做标签"},
                "max_codes": {"type": "integer", "default": 120, "description": "取样股票数上限 (越大越准越慢)"},
            },
            "required": ["expr"],
        },
        run=_tool_factor_test,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="alpha_compare",
        description=(
            "并排对比 2-8 个因子的 RankIC / ICIR / 命中率 / 健康分类(有效/一般/偏弱/失效), "
            "在同一 universe+时间窗上一次性算完。items 每项可以是【已注册因子名】(如 alpha019, gtja074) "
            "或【自定义表达式】(如 rank(-delta(close,5)))。用于用户问「A 因子和 B 因子哪个强」、"
            "「我这几个想法对比一下」。比逐个 factor_test 更省事 (共享一次面板加载)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "因子列表, 每项为已注册名或表达式, 如 [\"alpha019\", \"rank(-delta(close,5))\"]"},
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-01-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "fwd_days": {"type": "integer", "default": 5},
                "max_codes": {"type": "integer", "default": 120},
            },
            "required": ["items"],
        },
        run=_tool_alpha_compare,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="alpha_forge",
        description=(
            "炼因子: 把一句【自然语言因子想法】炼成截面因子表达式 (用价量 DSL) + 快测它的 IC。"
            "用于用户说「帮我把 xx 想法做成因子」「炼一个 yy 因子」。save=true 入库后该因子可被 "
            "factor_report / alpha_compare 按名引用。注: 当前只支持价量类想法 (动量/反转/量价/波动); "
            "基本面(股息/估值/市值)或事件型(连续/金叉/突破)暂不支持, 会提示。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "idea": {"type": "string", "description": "自然语言因子想法, 如 '5日反转' / '放量上涨' / '低波动'"},
                "save": {"type": "boolean", "default": False, "description": "true=炼好入库 (可被 factor_report 引用)"},
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-01-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "quick_eval": {"type": "boolean", "default": True},
            },
            "required": ["idea"],
        },
        run=_tool_alpha_forge,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="user_factors",
        description="列出已入库的 user 因子 (名/表达式/IC); remove=<name> 删除一个。",
        input_schema={
            "type": "object",
            "properties": {"remove": {"type": "string", "description": "要删除的 user 因子名 (留空=只列出)"}},
        },
        run=_tool_user_factors,
        cost_hint="fast",
    ),
    Tool(
        name="factor_report",
        description=(
            "对一个因子 (已注册名如 alpha019, 或表达式如 rank(-delta(close,5))) 跑【完整单因子评测】: "
            "IC/ICIR/t值/命中率 + IC衰减 + 十分位单调性 + 多空组合净值(年化/Sharpe/最大回撤/换手/胜率)。"
            "比 factor_test 更全 (factor_test 只给 IC, 这个给组合回测)。用于「这因子到底行不行」「给我一份完整评测」。"
            "默认 csi500 月频近 2 年。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expr_or_name": {"type": "string", "description": "因子名或表达式, 如 alpha019 或 rank(-delta(close,5))"},
                "universe": {"type": "string", "default": "csi500", "description": "csi300/csi500/csi800/all/csi300_active 或自选 .txt"},
                "freq": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                "start": {"type": "string", "description": "起始日 YYYY-MM-DD, 缺省今天往前 2 年"},
                "end": {"type": "string", "description": "结束日 YYYY-MM-DD, 缺省今天"},
                "archive": {"type": "boolean", "default": False,
                            "description": "True=把本次评测归档到研究档案 (runs.jsonl), 供 research_log 看迭代趋势"},
                "note": {"type": "string", "description": "归档时的备注 (这次为什么跑/想看什么)"},
            },
            "required": ["expr_or_name"],
        },
        run=_tool_factor_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="event_report",
        description=(
            "对一个【事件触发表达式】(用 cross/比较/ts_* 求值为布尔信号, 如 "
            "cross(close, sma(close,20)) 突破20日线, 或 ts_min((close>delay(close,1))*1.0,3) 连续3天涨) "
            "跑【事件研究】: 每次触发后 1/5/10/20 日的平均收益 + 市场调整超额 + 胜率 + t值 + 逐年。"
            "事件型因子 (金叉/突破/连续/放量) 用这个; 连续打分因子用 factor_report。默认 csi300_active 近2年。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expr_or_name": {"type": "string", "description": "触发表达式或注册名, 如 cross(close, sma(close,20))"},
                "universe": {"type": "string", "default": "csi300_active", "description": "csi300/csi500/csi800/all/csi300_active"},
                "start": {"type": "string", "description": "起始日 YYYY-MM-DD, 缺省今天往前 2 年"},
                "end": {"type": "string", "description": "结束日 YYYY-MM-DD, 缺省今天"},
                "horizons": {"type": "string", "default": "1,5,10,20", "description": "逗号分隔的前向天数"},
            },
            "required": ["expr_or_name"],
        },
        run=_tool_event_report,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="factor_compose",
        description=(
            "把 N(>=2) 个因子【合成】成一个综合打分模型, 在【样本外(OOS)】评测综合分 "
            "(RankIC/ICIR/Sharpe/年化/最大回撤), 并和每个成员的同窗 OOS 指标对比, "
            "回答「合成是否比单因子更强 / 有没有增益」。method 决定怎么组合: "
            "equal=等权, ic_weighted=按训练段 IC 加权, linear=线性回归拟合, lgbm=LightGBM(默认, 非线性)。"
            "用于用户说「把这几个因子合成」「做个多因子模型」「这几个因子组合起来效果如何」。"
            "区别于 alpha_compare (只并排比单因子, 不合成)。默认 csi300_active 月频, 前 60% 调仓期训练。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "≥2 因子名或表达式, 如 [\"alpha019\", \"rank(-delta(close,5))\"]",
                },
                "method": {
                    "type": "string",
                    "enum": ["equal", "ic_weighted", "linear", "lgbm"],
                    "default": "lgbm",
                },
                "universe": {"type": "string", "default": "csi300_active"},
                "freq": {"type": "string", "enum": ["day", "week", "month"], "default": "month"},
                "since": {"type": "string", "description": "起始日 YYYY-MM-DD, 缺省今天往前 2 年"},
                "until": {"type": "string", "description": "结束日 YYYY-MM-DD, 缺省今天"},
                "train_frac": {"type": "number", "default": 0.6,
                               "description": "训练段调仓日占比 (前段拟合权重, 余段 OOS 评测)"},
                "archive": {"type": "boolean", "default": False,
                            "description": "True=把本次合成归档到研究档案 (runs.jsonl), 供 research_log 看迭代趋势"},
                "note": {"type": "string", "description": "归档时的备注 (这次为什么跑/想看什么)"},
                "goal": {"type": "string",
                         "description": "自然语言目标 (如 '低回撤动量反转组合'); 给了就让 LLM 顾问自动配成员+方法, 可不传 members"},
            },
            "required": [],
        },
        run=_tool_factor_compose,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="research_log",
        description=(
            "列出/对比已归档的评测运行历史 (research archive), 看因子或合成模型迭代的指标趋势。"
            "用于「看我的研究历史」「最近评测过哪些因子」「对比这两次评测」「这因子改了之后好了没」。"
            "compare='id_a,id_b' → 两次运行关键指标并排 + diff; target=因子名/标的 → 该标的运行历史 (时间序); "
            "都不给 → 列最近 20 次运行。归档由 factor_report / factor_compose 带 archive=true 写入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": "标的 (因子名或合成 target), 看其运行历史趋势; 留空=列最近 20 次"},
                "compare": {"type": "string",
                            "description": "'id_a,id_b' 两个运行 id 逗号分隔, 出指标 diff (如 'r0001,r0002')"},
            },
        },
        run=_tool_research_log,
        cost_hint="fast",
    ),
    Tool(
        name="chain_for",
        description=(
            "Look up which industry-chain product a stock belongs to. "
            "Returns the primary product (anchor / data_supported / llm_inferred role), "
            "upstream / downstream products, peer stocks, and chain catalyst. "
            "Use when the user asks about 产业链 / 上下游 / 同行."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_chain_for,
    ),
    Tool(
        name="stocks_show",
        description=(
            "Show the user's prior research timeline for a stock (markdown). "
            "Use when the user asks 之前怎么看 X / 上次 X 评级 / X 历史研报."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "tail": {"type": "integer", "default": 2000},
            },
            "required": ["code"],
        },
        run=_tool_stocks_show,
    ),
    Tool(
        name="industry_show",
        description="Look up the Shenwan industry classification for a stock (e.g. 白酒 / 银行 / 电气设备).",
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_industry_show,
    ),
    Tool(
        name="dream_review",
        description="List pending memory proposals from the introspection loop. Use when the user asks 反思 / 教训 / proposal.",
        input_schema={"type": "object", "properties": {}},
        run=_tool_dream_review,
    ),
    Tool(
        name="mainline_radar",
        description=(
            "Run monthly industry-chain mainline radar: classifies each chain "
            "into mainline / initiation / revival / decay / cold. "
            "Use when the user asks which sectors are leading / 主线 / 板块轮动."
        ),
        input_schema={"type": "object", "properties": {}},
        run=_tool_mainline,
        cost_hint="seconds",
    ),
    Tool(
        name="morning_brief",
        description="Generate the pre-market morning brief (overnight moves + day's watchlist).",
        input_schema={"type": "object", "properties": {}},
        run=_tool_brief,
        cost_hint="seconds",
    ),
    Tool(
        name="overseas_radar",
        description=(
            "v1.9.7: 国际市场 + 海外新闻传导雷达. 拉隔夜美股/港股/VIX, "
            "判读 risk_tone (risk_on/off/mixed), 写明日 A 股 actionable signals. "
            "用户问海外 / 美股影响 / 港股 / 全球宏观 时调."
        ),
        input_schema={"type": "object", "properties": {}},
        run=_tool_overseas_radar,
        cost_hint="seconds",
    ),
    Tool(
        name="watchlist_show",
        description=(
            "Show the user's xueqiu 自选股 (or 模拟组合). "
            "Use when user asks 我的自选 / 关注列表 / watchlist. "
            "Default reads the latest cached snapshot — pass refresh=True "
            "to pull live from xueqiu (needs cookie via Chrome ext)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pid": {
                    "type": "string",
                    "description": "Group pid. -1=全部 (default), -4=模拟, -5=沪深, -6=美股, -7=港股, -10=实盘.",
                    "default": "-1",
                },
                "refresh": {"type": "boolean", "default": False},
            },
        },
        run=_tool_watchlist_show,
    ),
    Tool(
        name="fund_snapshot",
        description=(
            "Show the user's 蛋卷基金 total-assets snapshot (账户总览). "
            "Use when user asks 蛋卷 / 基金账户 / 总资产. "
            "Needs danjuanfunds.com login via Chrome ext."
        ),
        input_schema={
            "type": "object",
            "properties": {"refresh": {"type": "boolean", "default": False}},
        },
        run=_tool_fund_snapshot,
    ),
    Tool(
        name="fund_holdings",
        description=(
            "Show the user's 蛋卷基金 持仓明细 (per-fund market value / gain). "
            "Use when user asks 我的基金持仓 / 基金明细. ``account`` filters to "
            "a sub-account (by name). Needs danjuanfunds.com login."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "default": ""},
                "refresh": {"type": "boolean", "default": False},
            },
        },
        run=_tool_fund_holdings,
    ),
    Tool(
        name="iwencai_search",
        description=(
            "同花顺问财: 自然语言选股 (ths-extra opencli plugin). "
            "Use when user asks a structured screening question like "
            "'PE 最低的 20 只白酒' / '机构持仓>50% 且 ROE>15% 的科技股'. "
            "Result columns are dynamic per question."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "自然语言查询"},
                "limit": {"type": "integer", "default": 20},
                "use_cache": {"type": "boolean", "default": False,
                              "description": "Read cached result without re-querying"},
            },
            "required": ["question"],
        },
        run=_tool_iwencai_search,
        cost_hint="seconds",
    ),
    Tool(
        name="ths_fund_flow",
        description=(
            "同花顺资金流排行. ``target`` 切换四种榜: "
            "gegu (个股主力净流入), gainian (概念板块涨幅+主力净额), "
            "hangye (行业板块涨幅+主力净额), ddzz (大单追踪/实时大额成交流水). "
            "Use 'gainian' for 概念主线 / 板块轮动 type queries; "
            "'hangye' for 行业涨幅; 'ddzz' for 主力盘中大单买卖."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["gegu", "gainian", "hangye", "ddzz"],
                    "default": "gegu",
                },
                "limit": {"type": "integer", "default": 30},
                "refresh": {"type": "boolean", "default": True},
            },
        },
        run=_tool_ths_fund_flow,
        cost_hint="seconds",
    ),
    Tool(
        name="ths_concept_board",
        description=(
            "同花顺概念板块. mode='new' (default) lists newly minted concepts "
            "with their release date (e.g. '2026一季报预增' '雅下水电概念'). "
            "mode='rank' is the change-% leaderboard (URL still being verified)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["new", "rank"], "default": "new"},
                "limit": {"type": "integer", "default": 30},
                "refresh": {"type": "boolean", "default": True},
            },
        },
        run=_tool_ths_concept_board,
        cost_hint="seconds",
    ),
    Tool(
        name="fund_flow_change",
        description=(
            "同花顺资金流跨快照对比 — 一只股/板块的主力净流入随时间怎么变. "
            "Use when user asks 资金流变化 / 主力是在加仓还是出逃 / 对比昨天. "
            "Reads cached ths_fund_flow snapshots (需先多次采集积累历史)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "股票或板块代码"},
                "target": {
                    "type": "string",
                    "enum": ["gegu", "gainian", "hangye", "ddzz"],
                    "default": "gegu",
                },
                "limit": {"type": "integer", "default": 5,
                          "description": "对比最近 N 个快照"},
            },
            "required": ["code"],
        },
        run=_tool_fund_flow_change,
        cost_hint="instant",
    ),
    Tool(
        name="realtime_quote",
        description=(
            "实时行情 (雪球, 盘中实时价/涨跌幅/量/换手/市值/盘口状态). "
            "区别于 quote_lookup (日线 EOD). Use when user wants 现在多少钱 / "
            "盘中价 / 实时. 需要雪球 cookie."
        ),
        input_schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
        run=_tool_realtime_quote,
        cost_hint="seconds",
    ),
    Tool(
        name="quote_batch",
        description=(
            "批量实时行情 (腾讯, 无 cookie, 一次几十只 ~120ms). 用于多股对比 / "
            "自选股监控墙 / '看下这几只'. codes 逗号分隔. 比逐只 realtime_quote 快得多."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "codes": {"type": "string", "description": "逗号分隔代码, 如 SH600519,SZ300750,002594"},
            },
            "required": ["codes"],
        },
        run=_tool_quote_batch,
        cost_hint="seconds",
    ),
    Tool(
        name="update_data",
        description=(
            "增量更新行情数据 (pytdx 主站直连 + 腾讯实时, 不需要 Tushare token). "
            "当用户说 '更新数据' / '拉昨天' / '同步行情' / '把 X 数据补到最新' 时调用. "
            "默认 quick 模式只更日线 (省时), full 模式含 5min + daily_basic. "
            "codes 可以是 'SH600519,SZ300750' / 'all' (全市场) / None (LLM 从上下文挑)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "codes": {
                    "type": "string",
                    "description": (
                        "逗号分隔的股票代码, e.g. 'SH600519,SZ300750'. "
                        "用 'all' 表示全市场 (5500+ 只, 5-10 min). "
                        "不传则更新所有 instruments."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["quick", "full"],
                    "description": (
                        "quick=只更日线 (~10s/百只), full=日线+5min+daily_basic (~30s/百只). 默认 quick."
                    ),
                },
            },
            "required": [],
        },
        run=lambda codes=None, mode="quick": _tool_update_data(codes, mode),
        cost_hint="seconds",   # quick mode: seconds; full mode: minutes — default is quick
        confirm_required=False,
    ),
    Tool(
        name="alert_add",
        description=(
            "加盯盘提醒. 当用户说 'X 跌破 N 提醒我' / 'X 涨到 N 告诉我' / "
            "'X 涨幅超 N% 提醒' 时调用. kind: price_below=跌破价格, "
            "price_above=涨破价格, pct_above=涨幅突破(threshold 如 5 表示+5%), "
            "pct_below=跌幅突破(如 -5 表示跌5%). 提醒在 /watch on 后台盯盘时触发."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["price_below", "price_above", "pct_above", "pct_below"],
                },
                "threshold": {"type": "number"},
                "note": {"type": "string", "default": ""},
            },
            "required": ["code", "kind", "threshold"],
        },
        run=_tool_alert_add,
    ),
    Tool(
        name="alert_list",
        description="列出所有盯盘提醒. 当用户问 '我设了哪些提醒' / '盯盘列表' 时调用.",
        input_schema={"type": "object", "properties": {}},
        run=_tool_alert_list,
    ),
    Tool(
        name="alert_remove",
        description=(
            "删盯盘提醒. rule_id 形如 'SH600519:price_below', 或只给 'SH600519' "
            "删该股全部提醒. 当用户说 '取消 X 的提醒' 时调用."
        ),
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "string"}},
            "required": ["rule_id"],
        },
        run=_tool_alert_remove,
    ),
    Tool(
        name="wisdom_review",
        description="审阅视频经验草稿卡 (list/approve/reject). approve 后进正式知识库.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "approve", "reject"]},
                "card_id": {"type": "string", "description": "approve/reject 时必填, 如 EV-013"},
                "reviewed_by": {"type": "string", "description": "审阅人, 可选"},
            },
            "required": ["action"],
        },
        run=_tool_wisdom_review,
        cost_hint="seconds",
        confirm_required=True,
    ),
    Tool(
        name="wisdom_search",
        description="检索已沉淀的视频投资经验 (仅已批准). 研判/研报引用 UP 主经验时用.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        run=_tool_wisdom_search,
        cost_hint="seconds",
        confirm_required=False,
    ),
]


def get_tool(name: str) -> Optional[Tool]:
    for t in TOOL_REGISTRY:
        if t.name == name:
            return t
    return None


def list_tools() -> List[Tool]:
    return list(TOOL_REGISTRY)
