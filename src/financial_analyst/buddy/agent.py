"""Conversational agent — tool-use loop driven by an LLM.

Architecture mirrors Claude Code's assistant: the LLM receives the user's
natural-language prompt + the tool registry, decides which tool(s) to
call, the runtime executes them, results feed back into the next LLM
turn, and the loop continues until the LLM emits a final text response.

```
User: "看看茅台最近怎么样"
  ↓
LLM (sees 13 tools) → tool_use: quote_lookup(code="SH600519")
  ↓
runtime executes → tool_result: "close=1280, PE=20..."
  ↓
LLM → tool_use: news_query(code="SH600519", days=7)
  ↓
runtime → tool_result: "5 news entries..."
  ↓
LLM → text: "茅台现价 1280, PE 20 处于历史低位, 最近一周..."
```

Provider note: uses LiteLLM under the hood, which normalises tool-use
across Anthropic / OpenAI / Qwen. Tool definitions are passed as the
Anthropic-style ``input_schema`` form; LiteLLM converts when needed.
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from financial_analyst.buddy.tools import (
    Tool, ToolResult, TOOL_REGISTRY, get_tool,
)
from financial_analyst.llm.client import LLMClient

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are 金融助手 — a conversational A-share research assistant.

You have access to {n_tools} tools that drive the underlying financial-analyst
stack: stock reports, news queries, alpha benchmarks, industry-chain lookups,
prior research timeline retrieval, market briefings, and more.

Behaviour rules:
1. Default response language: 中文 (Chinese). Switch to English only if the user does.
2. Be direct. Do not pad with greetings, apologies, or "I'd be happy to". Answer.
3. Pick the right tool — DO NOT call `run_report` (5-8 min) when the user just wants
   a quote. Match the tool to the question's scope (see routing cheat-sheet below).
4. If a tool errors, surface the error message verbatim and suggest a fix.
5. After tool execution, summarise in plain Chinese — DO NOT dump raw JSON or full
   markdown. Pick the 3-5 most important lines.
   - **引用标注 (§N)**: 关键数据点后紧跟 `[§N]` 标注它来自本轮第 N 个工具调用
     (N 从 1 开始, 按工具调用顺序). 例: "主力净流入 +4.8 亿[§2], 同行毛利率第一[§4]".
     不要在结尾集中列引用; 没有对应工具支撑的判断不标. 这让前端能把数据挂回来源.
6. When the user names a stock in Chinese, map to its code from common knowledge
   (茅台=SH600519, 五粮液=SZ000858, 比亚迪=SZ002594, 宁德时代=SZ300750), or ask.
7. For follow-ups, reuse prior tool results from history instead of re-fetching.
8. End with a brief next-step suggestion when useful ("需要我跑完整研报吗?").
9. **数据时效**: if a tool result starts with "⚠ 数据偏旧 ...", tell the user the data
   is stale and offer to refresh (news_collect) before drawing conclusions.

# Tool routing cheat-sheet (pick the NARROWEST tool that answers the question)

| 用户问法 | 用哪个工具 |
|---|---|
| "看下 X 怎么样" / "X 最近如何" / "了解一下 X" (宽泛) | **stock_brief(code)** — 一次拿行情+行业+链+新闻+情绪+资金流+上次研报. 不要再手动串 quote+chain+news! |
| "X 多少钱" / "X 现价" / 盘中实时 | **realtime_quote(code)** (盘中实时价/盘口) |
| "X 的 PE/PB/市值" / 估值 (日线即可) | **quote_lookup(code)** (日线 EOD) |
| "深度研报" / "完整分析" / "跑个研报" | **run_report(code)** (5-8 min, 贵, 会要确认) |
| "X 跌破 N 提醒我" / "涨到 N 告诉我" | **alert_add(code, kind, threshold)** — kind: price_below/price_above/pct_above/pct_below |
| "我设了哪些提醒" / "取消提醒" | **alert_list** / **alert_remove** |
| "主力今天买什么" / "资金流排行" | **ths_fund_flow(target='gegu')** |
| "概念主线在哪" / "板块轮动" / "哪个概念强" | **ths_fund_flow(target='gainian')** |
| "行业涨幅排行" | **ths_fund_flow(target='hangye')** |
| "盘中大单方向" | **ths_fund_flow(target='ddzz')** |
| "X 主力是加仓还是出逃" / "资金流变化" | **fund_flow_change(code)** (跨快照对比) |
| "PE<20 且 ROE>15% 的" / 自然语言选股 | **iwencai_search(question)** (问财) |
| "最新概念发布" | **ths_concept_board(mode='new')** |
| "X 所在产业链" / "上下游" / "同行" | **chain_for(code)** |
| "之前怎么看 X" / "上次评级" | **stocks_show(code)** |
| 板块/主线月度回顾 | **mainline_radar** |
| 盘前晨会 | **morning_brief** (slow, 全市场扫描 — 不要拿来答临时问题) |

# News / sentiment workflow
When user asks 新闻 / 情绪 / 雪球 / 今日动态:
- FIRST `news_query` (with code/fts filter) to read cached data.
- If empty/stale, `news_collect` to refresh, then `news_query` again. Source picks:
  - 大盘资讯 → "kuaixun,longhu,sinafinance"
  - 公开热度榜 (无登录) → "ths-hot"
  - 雪球单股评论 → "xueqiu-comments" + code  (需 cookie)
  - 雪球热门动态/帖子 → "xueqiu-hot-posts" / "xueqiu-feed"  (需 cookie)
- Do NOT use `morning_brief` for ad-hoc queries.

Tool-use guidance:
- Prefer ONE broad tool (stock_brief) over chaining 5 narrow ones — saves round-trips.
- Multi-step: emit tool calls in sequence; each result lands in your next-turn context.
- Confirmations: `cost_hint=minutes` tools may be gated by the runtime (permission mode).
  Just emit the call — the runtime handles the y/n prompt.

Tools (summarised):
{tool_list}
"""


@dataclass
class Message:
    """One message in the conversation. Keeps the original LLM payload as `raw`
    so we can pass it back through LiteLLM verbatim on the next turn.
    """
    role: str  # "user" | "assistant" | "tool"
    content: Any  # str for user/assistant text; list of blocks for tool turns
    raw: Optional[Dict[str, Any]] = None

    def to_litellm(self) -> Dict[str, Any]:
        if self.raw is not None:
            return self.raw
        return {"role": self.role, "content": self.content}


def _format_tool_list() -> str:
    lines = []
    for t in TOOL_REGISTRY:
        cost = f" [{t.cost_hint}]" if t.cost_hint != "instant" else ""
        lines.append(f"  - {t.name}{cost}: {t.description.split('.')[0]}.")
    return "\n".join(lines)


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(
        n_tools=len(TOOL_REGISTRY),
        tool_list=_format_tool_list(),
    )


@dataclass
class TurnEvent:
    """One event yielded as a turn unfolds. The REPL renders these as they arrive."""
    kind: str  # "text" | "tool_call" | "tool_result" | "error" | "done"
    payload: Any = None


class BuddyAgent:
    """Conversational agent with tool-use. Reusable across turns; keeps
    conversation state in ``self.messages``."""

    AGENT_NAME = "buddy"

    def __init__(self, system_prompt: Optional[str] = None,
                 max_tool_iters: int = 15,
                 max_llm_retries: int = 2):
        """
        Args:
            max_tool_iters: Cap on tool-use rounds per turn. Bumped from
                8 → 15 in v1.6.4 after observing complex multi-step
                queries (cross-stock comparisons, chained news →
                summary) hitting the old ceiling without producing
                final text.
            max_llm_retries: Auto-retry the LLM call this many times
                on transient network failures (SSL, timeout, 5xx).
                Default 2. Set to 0 to disable retries.
        """
        self._system = system_prompt or _build_system_prompt()
        self.messages: List[Message] = []
        self.max_tool_iters = max_tool_iters
        self.max_llm_retries = max_llm_retries
        self._client = LLMClient.for_agent(self.AGENT_NAME)

    def reset(self) -> None:
        self.messages.clear()

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def _tool_schemas(self) -> List[Dict[str, Any]]:
        """LiteLLM normalises tool format internally, but the cleanest path
        is to pass OpenAI-style ``{"type": "function", "function": {...}}``
        which all backends (Anthropic / OpenAI / Qwen via DashScope) accept.
        """
        return [t.to_openai_schema() for t in TOOL_REGISTRY]

    async def run_turn(self, user_text: str,
                       confirm_callback=None) -> AsyncIterator[TurnEvent]:
        """Process one user turn. Yields TurnEvent objects as the LLM emits
        text + tool calls + tool results, until the LLM stops requesting tools.

        ``confirm_callback`` is an optional async function ``(tool_name, args)
        -> bool`` invoked before any tool whose ``confirm_required=True``
        runs. If the callback returns False, the tool is skipped and the
        LLM is told the user declined.
        """
        self.add_user(user_text)

        for iteration in range(self.max_tool_iters):
            # v1.6.4: retry transient LLM failures (SSL hiccups, 5xx,
            # DashScope rate-limit blips) before giving up. Exponential
            # backoff: 0.8s, 2.4s.
            response = None
            last_exc: Optional[Exception] = None
            for attempt in range(self.max_llm_retries + 1):
                try:
                    response = await self._client.chat(
                        messages=[{"role": "system", "content": self._system}]
                        + [m.to_litellm() for m in self.messages],
                        tools=self._tool_schemas(),
                        temperature=0.2,
                    )
                    break
                except Exception as e:
                    last_exc = e
                    if attempt >= self.max_llm_retries:
                        break
                    # Don't retry on cancellation
                    if isinstance(e, asyncio.CancelledError):
                        raise
                    backoff = 0.8 * (3 ** attempt)
                    log.warning(
                        "LLM call attempt %d failed: %s — retrying in %.1fs",
                        attempt + 1, e, backoff,
                    )
                    await asyncio.sleep(backoff)

            if response is None:
                # All retries exhausted — emit a clear error AND a done
                # event so the REPL's finalizer can print its end-of-turn
                # marker correctly (v1.6.3 done marker logic uses
                # error_count to pick the right message).
                err = f"LLM 调用失败 (重试 {self.max_llm_retries} 次): {type(last_exc).__name__}: {last_exc}"
                yield TurnEvent("error", err)
                yield TurnEvent("done", None)
                return

            # LiteLLM returns OpenAI-compat envelope; the message has
            # `content` (string or list) and optionally `tool_calls`.
            choice = response["choices"][0]["message"]
            raw = dict(choice) if isinstance(choice, dict) else {
                "role": "assistant",
                "content": getattr(choice, "content", ""),
                "tool_calls": getattr(choice, "tool_calls", None),
            }
            text = raw.get("content") or ""
            tool_calls = raw.get("tool_calls") or []

            # Normalise tool_calls — LiteLLM may return objects or dicts
            normalised_calls = []
            for tc in tool_calls:
                if hasattr(tc, "function"):  # OpenAI object form
                    normalised_calls.append({
                        "id": getattr(tc, "id", ""),
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })
                elif isinstance(tc, dict):
                    fn = tc.get("function", {})
                    normalised_calls.append({
                        "id": tc.get("id", ""),
                        "name": fn.get("name") or tc.get("name", ""),
                        "arguments": fn.get("arguments") or tc.get("arguments", "{}"),
                    })

            # Save the assistant message verbatim (with tool_calls intact)
            asst_msg = {"role": "assistant", "content": text}
            if normalised_calls:
                asst_msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": c["arguments"] if isinstance(c["arguments"], str)
                                         else json.dumps(c["arguments"], ensure_ascii=False),
                        },
                    }
                    for c in normalised_calls
                ]
            self.messages.append(Message(role="assistant", content=text, raw=asst_msg))

            if text:
                yield TurnEvent("text", text)

            if not normalised_calls:
                # LLM done — no more tools wanted.
                yield TurnEvent("done", None)
                return

            # Execute each tool call.
            tool_result_messages = []
            for call in normalised_calls:
                name = call["name"]
                args_raw = call["arguments"]
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                except json.JSONDecodeError:
                    args = {}

                yield TurnEvent("tool_call", {"name": name, "args": args})

                tool = get_tool(name)
                if tool is None:
                    err = f"Unknown tool: {name}"
                    yield TurnEvent("error", err)
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": err,
                    })
                    continue

                # Confirmation gate
                if tool.confirm_required and confirm_callback is not None:
                    ok = await confirm_callback(name, args)
                    if not ok:
                        tool_result_messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": "User declined to run this tool.",
                        })
                        continue

                # v1.5.4: run synchronous tool callables on a worker thread
                # so subprocess-based tools (run_report, alpha_bench,
                # mainline_radar, morning_brief — minutes-long) don't block
                # the asyncio event loop. While they run, the K-line
                # animator task keeps ticking the spinner so the user can
                # see something IS happening.
                import asyncio as _asyncio
                try:
                    result = await _asyncio.to_thread(tool.run, **args)
                except TypeError as e:
                    result = ToolResult(f"Tool argument error: {e}", is_error=True)
                except Exception as e:
                    result = ToolResult(f"Tool failed: {type(e).__name__}: {e}", is_error=True)

                yield TurnEvent("tool_result", {
                    "name": name, "content": result.content,
                    "is_error": result.is_error,
                    # v1.9.0: forward structured side_effect (e.g. stock_brief's
                    # 速览 card dict) so the SSE server can relay it to the UI.
                    "side_effect": result.side_effect,
                })
                tool_result_messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result.content if not result.is_error
                               else f"[ERROR] {result.content}",
                })

            # Append tool results to message history for the next iteration.
            for tr in tool_result_messages:
                self.messages.append(Message(role="tool", content=tr["content"], raw=tr))

        # Loop exhausted — too many tool iterations.
        yield TurnEvent("error",
                        f"达到 tool 调用上限 ({self.max_tool_iters} 次). "
                        f"LLM 在工具循环里转圈, 没收敛到答案. "
                        f"建议: 再问一句 '前面的结果总结一下' 让它写正文, "
                        f"或换更具体的 prompt.")
        yield TurnEvent("done", None)


async def run_chat() -> None:
    """Launch the buddy REPL (importable for `financial-analyst chat`)."""
    from financial_analyst.buddy.repl import run_repl
    await run_repl()
