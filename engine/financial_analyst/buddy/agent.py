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
| ETF "研报" / "分析 510300" / ETF 深度分析 (代码 5/15 开头) | **run_etf_report(code)** (ETF 专用 5-8 min, 会确认; 如 510300 / SH159915) |
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


def _conversation_lessons_path():
    """Buddy 专属经验文件路径。**必须落在 `_buddy/` 而非 `_shared/`** —— AgentMemory._collect_files
    只 glob `_shared/`、`<agent>/`、借用目录,`_buddy/` 不在其中,故不会被强载进每个研报 agent
    (这条经验只供 buddy 自己的 SYSTEM_PROMPT,经 _load_conversation_lessons 直读)。
    顺带一次性迁移历史落在 `_shared/` 的旧文件,杜绝残留仍被强载。"""
    from financial_analyst.memory_paths import default_memory_root
    root = default_memory_root()
    new = root / "_buddy" / "conversation_lessons.md"
    old = root / "_shared" / "conversation_lessons.md"
    if old.exists():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            if new.exists():
                merged = new.read_text(encoding="utf-8").rstrip() + "\n" + old.read_text(encoding="utf-8").lstrip()
                new.write_text(merged, encoding="utf-8")
                old.unlink()
            else:
                old.replace(new)
        except Exception:
            pass
    return new


def _load_conversation_lessons() -> str:
    """Cumulative lessons the user recorded via `/lesson <text>`. Read live every
    time the prompt is built, so a newly added lesson takes effect on the **next question**
    (no need to restart buddy)."""
    try:
        f = _conversation_lessons_path()
        if f.exists():
            txt = f.read_text(encoding="utf-8").strip()
            # Strip the file header (first line # + description block), keep only actual lesson lines
            lines = [l for l in txt.splitlines() if l.startswith("- [")]
            if lines:
                return "\n".join(lines)
    except Exception:
        pass
    return ""


def _build_system_prompt() -> str:
    base = SYSTEM_PROMPT.format(
        n_tools=len(TOOL_REGISTRY),
        tool_list=_format_tool_list(),
    )
    lessons = _load_conversation_lessons()
    if lessons:
        base += ("\n\n# 累计经验 (用户通过 /lesson 沉淀, 最新在最后)\n"
                 "对话开始前必读. 与历史 lesson 冲突的判断必须显式说明 \"为什么这次不同\".\n"
                 f"{lessons}\n")
    return base


@dataclass
class TurnEvent:
    """One event yielded as a turn unfolds. The REPL renders these as they arrive."""
    kind: str  # "text" | "tool_call" | "tool_result" | "error" | "done"
    payload: Any = None


def _budget_verdict(budget: int, spent: int, iteration: int, warned: bool) -> str:
    """turn 级 completion-token 预算判定。0=关;首轮(iteration=0)永不拦(至少答一次);
    ≥100%→stop(诚实显形停循环);≥80% 且未警告→warn(注入收敛提示)。"""
    if not budget or iteration <= 0:
        return "ok"
    if spent >= budget:
        return "stop"
    if not warned and spent >= int(budget * 0.8):
        return "warn"
    return "ok"


class BuddyAgent:
    """Conversational agent with tool-use. Reusable across turns; keeps
    conversation state in ``self.messages``."""

    AGENT_NAME = "buddy"

    def __init__(self, system_prompt: Optional[str] = None,
                 max_tool_iters: int = 15,
                 max_llm_retries: int = 2,
                 turn_token_budget: int = 0):
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
            turn_token_budget: turn 级 completion-token 预算闸,无人值守夜跑安全门
                (2026-07-12)。0=关(默认,行为逐字节不变)。耗尽后诚实截停工具循环,
                不静默截断答案。
        """
        self._system = system_prompt or _build_system_prompt()
        self.messages: List[Message] = []
        self.max_tool_iters = max_tool_iters
        self.max_llm_retries = max_llm_retries
        self.turn_token_budget = max(0, int(turn_token_budget))
        self._client = LLMClient.for_agent(self.AGENT_NAME)

    def reset(self) -> None:
        self.messages.clear()

    async def compact(self, transcript: Optional[str] = None) -> str:
        """Summarize the conversation into a short digest and replace history.

        Frees up context while keeping the gist (stocks discussed, conclusions,
        user preferences, open TODOs). If ``transcript`` is given (e.g. the
        frontend's rendered conversation) it is summarized instead of the
        in-memory message list — useful when the server was restarted and lost
        in-memory history. Returns the summary text ("" if nothing to compact).
        """
        if transcript and transcript.strip():
            convo = transcript.strip()
        elif self.messages:
            convo = "\n\n".join(
                f"[{m.role}] {m.content}" for m in self.messages if m.content
            )
        else:
            return ""
        convo = convo[:12000]  # cap prompt size
        resp = await self._client.chat(
            messages=[
                {"role": "system", "content": (
                    "你是对话压缩器。把下面这段 A 股研究对话压缩成简短中文摘要，"
                    "保留：讨论过的股票/板块及代码、关键结论与评级、用户偏好与约束、"
                    "未完成的待办。去掉寒暄与重复，直接输出摘要，不要前缀。"
                )},
                {"role": "user", "content": convo},
            ],
            temperature=0.2,
        )
        try:
            choice = resp["choices"][0]["message"]
            summary = (choice.get("content") if isinstance(choice, dict)
                       else getattr(choice, "content", "")) or ""
        except Exception:
            summary = ""
        summary = summary.strip()
        if summary:
            self.messages = [Message(
                role="user",
                content=f"（前情摘要，仅供延续对话参考）\n{summary}",
            )]
        return summary

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def _tool_schemas(self, allowed=None) -> List[Dict[str, Any]]:
        """LiteLLM normalises tool format internally, but the cleanest path
        is to pass OpenAI-style ``{"type": "function", "function": {...}}``
        which all backends (Anthropic / OpenAI / Qwen via DashScope) accept.

        ``allowed`` (set of tool names) 限制本轮 LLM 可见的工具; None=全部。
        模块边界 (如对话端只给研究类) 即靠这里裁掉因子类工具 — LLM 看不到就不会调。
        """
        return [t.to_openai_schema() for t in TOOL_REGISTRY
                if allowed is None or t.name in allowed]

    # Stale tool-result folding (token-budget guard). A tool result is appended
    # to history and then re-sent to the LLM on EVERY subsequent loop iteration;
    # a 2-4KB stock_brief re-sent across a 15-iteration turn bloats the request.
    # We keep the FRESH results (those after the last assistant message — the LLM
    # must reason about them exactly THIS turn) in full, and fold OLDER results
    # (already summarized in a prior assistant turn) to a head slice + a re-run
    # pointer. Red line: numbers are never altered — only a suffix is dropped, and
    # the full body already reached the UI via the tool_result TurnEvent; the tool
    # is deterministic + re-runnable, so the exact tail is one call away.
    _STALE_TOOL_CAP = 1200   # chars kept when folding a stale tool result
    _STALE_TOOL_MIN = 1600   # only fold results longer than this (small ones pass through)

    def _messages_for_llm(self) -> List[Dict[str, Any]]:
        """History serialized for the LLM, with stale tool results folded.

        Only the copy sent to the LLM is folded — ``self.messages`` keeps the
        full body (so reseed/compact and the UI see everything)."""
        last_asst = -1
        for i, m in enumerate(self.messages):
            if m.role == "assistant":
                last_asst = i
        out: List[Dict[str, Any]] = []
        for i, m in enumerate(self.messages):
            d = m.to_litellm()
            if m.role == "tool" and i < last_asst:
                content = d.get("content") or ""
                if len(content) > self._STALE_TOOL_MIN:
                    folded = dict(d)
                    dropped = len(content) - self._STALE_TOOL_CAP
                    folded["content"] = (
                        content[:self._STALE_TOOL_CAP]
                        + f"\n…[早前工具结果已折叠,省略 {dropped} 字符;"
                          f"需要完整数据请重新调用该工具(确定性可重跑)]"
                    )
                    d = folded
            out.append(d)
        return out

    async def run_turn(self, user_text: str,
                       confirm_callback=None,
                       allowed_tools=None) -> AsyncIterator[TurnEvent]:
        """Process one user turn. Yields TurnEvent objects as the LLM emits
        text + tool calls + tool results, until the LLM stops requesting tools.

        ``confirm_callback`` is an optional async function ``(tool_name, args)
        -> bool`` invoked before any tool whose ``confirm_required=True``
        runs. If the callback returns False, the tool is skipped and the
        LLM is told the user declined.
        """
        self.add_user(user_text)

        # Build the tool schema ONCE per turn (the allowed set is fixed for the
        # turn). The old code rebuilt the full ~8.6KB schema on every iteration —
        # token-neutral (it's a byte-stable prefix DeepSeek auto-caches) but
        # wasteful CPU. Building once also guarantees the prefix is identical
        # across iterations, which is what lets the prefix cache hit.
        tool_schemas = self._tool_schemas(allowed_tools)

        _budget_start = self._client.total_completion_tokens
        _budget_warned = False

        for iteration in range(self.max_tool_iters):
            spent = self._client.total_completion_tokens - _budget_start
            verdict = _budget_verdict(self.turn_token_budget, spent, iteration, _budget_warned)
            if verdict == "stop":
                yield TurnEvent("error",
                                f"token 预算耗尽({spent}/{self.turn_token_budget}):停止工具循环,"
                                f"以上为已完成部分(诚实截停,非完整答案)。")
                break
            if verdict == "warn":
                _budget_warned = True
                self.messages.append(Message(role="user", content=(
                    f"[系统:本轮 token 预算已用 {spent}/{self.turn_token_budget},"
                    f"请立即收敛——不要再发起新工具调用,直接给结论。]")))

            # v1.6.4: retry transient LLM failures (SSL hiccups, 5xx,
            # DashScope rate-limit blips) before giving up. Exponential
            # backoff: 0.8s, 2.4s.
            response = None
            last_exc: Optional[Exception] = None
            for attempt in range(self.max_llm_retries + 1):
                try:
                    response = await self._client.chat(
                        messages=[{"role": "system", "content": self._system}]
                        + self._messages_for_llm(),
                        tools=tool_schemas,
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

                # 模块边界 guard: allowed_tools 不为 None 时, 不在白名单的工具不执行。
                # 发 tool_result(is_error) 而非致命 error → LLM 收到"不可用"反馈后优雅改口
                # (告诉用户该能力在量化模块), 不让整轮报错。(_tool_schemas 已从 function 列表
                # 裁掉它; 但系统提示仍描述全部工具, LLM 可能仍尝试调 → 这里兜底拦住。)
                if allowed_tools is not None and name not in allowed_tools:
                    err = (f"工具 {name} 不在当前模块可用范围内 "
                           f"(本模块=研究/研报域; 因子炼制/评测请用量化模块)。")
                    yield TurnEvent("tool_result", {
                        "name": name, "content": err, "is_error": True, "side_effect": None,
                    })
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": f"[不可用] {err} 请直接告诉用户:该能力在「量化」模块, 本对话端不做因子炼制/评测。",
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
                    # quick-view card dict) so the SSE server can relay it to the UI.
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
