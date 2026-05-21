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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


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
    # Extract the executive summary (sections 一 + 八)
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
            f"  {r.get('symbol'):<10} {r.get('name', '?'):<8} "
            f"price={r.get('price') or '-':<8} "
            f"chg={r.get('change_percent') or '-'}"
        )
    return ToolResult("\n".join(lines))


def _tool_fund_snapshot(refresh: bool = False) -> ToolResult:
    """Show the user's 蛋卷基金 (danjuanfunds.com) total-assets snapshot.

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
    """Show the user's 蛋卷基金 持仓明细. ``account`` filters to a sub-account.

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
            f"  {r.get('fd_code'):<8} {r.get('fd_name', '?'):<20} "
            f"市值={r.get('market_value') or '-':<10} "
            f"占比={r.get('market_percent') or '-':<6} "
            f"当日={r.get('daily_gain') or '-'}  "
            f"累计={r.get('hold_gain') or '-'} ({r.get('hold_gain_rate') or '-'})"
        )
    return ToolResult("\n".join(lines))


def _tool_iwencai_search(question: str, limit: int = 20,
                          use_cache: bool = False) -> ToolResult:
    """问财自然语言选股. Pulls a result table via the ths-extra opencli
    plugin. ``columns`` are dynamic per question — caller interprets cells.

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
        return ToolResult(
            f"问财查询失败: {exc}\n"
            f"(Hint: 需要装 ths-extra plugin — "
            f"opencli plugin install file://G:/financial-analyst/opencli-plugin-ths-extra)",
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
    """同花顺资金流, target=gegu/gainian/hangye/ddzz."""
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
        lines.append(f"  {'代码':<8}{'名称':<10}{'现价':<8}{'涨跌幅':<8}"
                     f"{'换手率':<8}{'主力净流入':<12}{'成交额':<10}")
        for r in rows:
            lines.append(
                f"  {(r.get('code') or '-'):<8}{(r.get('name') or '-'):<10}"
                f"{(r.get('price') or '-'):<8}{(r.get('change_pct') or '-'):<8}"
                f"{(r.get('turnover_pct') or '-'):<8}{(r.get('main_net') or '-'):<12}"
                f"{(r.get('total_amount') or '-'):<10}"
            )
    elif target in ("gainian", "hangye"):
        lines.append(f"  {'板块代码':<10}{'名称':<14}{'涨跌幅':<8}"
                     f"{'主力净额(亿)':<14}{'股数':<6}{'领涨股':<10}")
        for r in rows:
            lines.append(
                f"  {(r.get('code') or '-'):<10}{(r.get('name') or '-'):<14}"
                f"{(r.get('change_pct') or '-'):<8}{(r.get('main_net') or '-'):<14}"
                f"{(r.get('num_stocks') or '-'):<6}{(r.get('leader') or '-'):<10}"
            )
    elif target == "ddzz":
        lines.append(f"  {'时间':<20}{'代码':<8}{'名称':<10}"
                     f"{'价':<8}{'量':<8}{'额(万)':<10}{'性质':<6}{'涨幅':<8}")
        for r in rows:
            lines.append(
                f"  {(r.get('trade_time') or '-'):<20}{(r.get('code') or '-'):<8}"
                f"{(r.get('name') or '-'):<10}{(r.get('price') or '-'):<8}"
                f"{(r.get('volume') or '-'):<8}{(r.get('total_amount') or '-'):<10}"
                f"{(r.get('direction') or '-'):<6}{(r.get('change_pct') or '-'):<8}"
            )
    return ToolResult("\n".join(lines))


def _parse_cn_amount(s: Optional[str]) -> Optional[float]:
    """Parse 同花顺 amount strings to a float (in yuan).

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
    """Format a yuan float back to a compact 亿/万 string."""
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
    """同花顺资金流跨快照对比. 看一只股/板块的主力净流入随时间变化.

    需要 ths_fund_flow 表里有该 code 的多个 snapshot (多次 collect 后才有
    历史). target=gegu/gainian/hangye/ddzz."""
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
    """同花顺概念板块. mode=new (新概念发布表, 默认) / rank (涨幅榜)."""
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
                f"  {r.get('board_code', '?'):<8}{r.get('board_name', '?'):<10}"
                f"{r.get('change_pct', '?'):<8}"
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
    proposed_root = _project_root() / "memories" / "_proposed"
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
    """实时行情 (雪球). 不同于 quote_lookup (日线/EOD), 这是盘中实时价.
    需要雪球 cookie (Chrome ext)."""
    from financial_analyst.data.collectors.opencli.xueqiu_stock import XueqiuStockCollector
    try:
        d = XueqiuStockCollector().fetch(code)
    except Exception as exc:
        return ToolResult(f"实时行情抓取失败: {exc}", is_error=True)
    if not d:
        return ToolResult(f"无 {code} 实时行情 (代码错误 或 雪球 cookie 失效?)", is_error=True)
    return ToolResult(
        f"{d.get('name','?')} ({d.get('symbol', code)}) [{d.get('market_status','?')}]\n"
        f"  现价 {d.get('price','?')}  涨跌 {d.get('change','?')} ({d.get('changePercent','?')})\n"
        f"  开 {d.get('open','?')} 高 {d.get('high','?')} 低 {d.get('low','?')} 昨收 {d.get('prevClose','?')}\n"
        f"  量 {d.get('volume','?')}  额 {d.get('amount','?')}  换手 {d.get('turnover_rate','?')}\n"
        f"  市值 {d.get('marketCap','?')}  振幅 {d.get('amplitude','?')}  时间 {d.get('time','?')}"
    )


def _tool_alert_add(code: str, kind: str, threshold: float, note: str = "") -> ToolResult:
    """加一条盯盘提醒. kind: price_below(跌破)/price_above(涨破)/
    pct_above(涨幅突破)/pct_below(跌幅突破). threshold 价格或百分比数值."""
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
    """列出所有盯盘提醒."""
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
    """删盯盘提醒. rule_id 可以是 'SH600519:price_below' 或只给 'SH600519' (删该股全部)."""
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

    # --- 行情 ---
    try:
        q = _tool_ask_quote(code)
        lines.append(f"\n## 行情\n{q.content}")
    except Exception as exc:
        lines.append(f"\n## 行情\n  (取价失败: {exc})")

    # --- 行业 + 产业链 ---
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

    # --- 近期新闻 (cache only) + 时效 ---
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

    # --- 上次研报时间线 ---
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
    return ToolResult("\n".join(lines))


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


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


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
]


def get_tool(name: str) -> Optional[Tool]:
    for t in TOOL_REGISTRY:
        if t.name == name:
            return t
    return None


def list_tools() -> List[Tool]:
    return list(TOOL_REGISTRY)
