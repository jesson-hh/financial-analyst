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
3. Pick the right tool — DO NOT call `run_report` (which takes 5-8 min) when the user
   just wants a price quote. Use `quote_lookup` for quick lookups.
4. Chain tools when needed: e.g. "看下茅台同行最近表现" → `chain_for(SH600519)` first
   to get peers, then `quote_lookup` for each.
5. If a tool errors, surface the error message verbatim and suggest a fix.
6. After tool execution, summarise the result in plain Chinese — DO NOT dump raw JSON
   or full markdown into the user's face. Pick the 3-5 most important lines.
7. When the user asks for "深度研报" / "完整研报" / "full report", call `run_report`
   (this is the expensive one — confirm with user first if cost matters).
8. When the user mentions a stock by name in Chinese, you may need to ask for the code,
   or guess based on common knowledge (e.g. 茅台 = SH600519, 五粮液 = SZ000858).
9. For follow-up questions, use prior tool results from the conversation history
   instead of re-fetching.
10. End each turn with a brief next-step suggestion when appropriate
    (e.g. "需要我跑完整研报吗?").

Tool-use guidance:
- Multi-step queries: emit multiple tool calls in sequence over turns. Don't try to do
  everything in one call.
- Streaming: each tool result lands in your context for the next turn.
- Confirmations: if a tool's `cost_hint` is `minutes`, the runtime may ask the user to
  confirm before running. Trust the runtime to handle this — just emit the tool call.

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
                 max_tool_iters: int = 8):
        self._system = system_prompt or _build_system_prompt()
        self.messages: List[Message] = []
        self.max_tool_iters = max_tool_iters
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
            try:
                response = await self._client.chat(
                    messages=[{"role": "system", "content": self._system}]
                    + [m.to_litellm() for m in self.messages],
                    tools=self._tool_schemas(),
                    temperature=0.2,
                )
            except Exception as e:
                yield TurnEvent("error", f"LLM call failed: {e}")
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

                try:
                    result = tool.run(**args)
                except TypeError as e:
                    result = ToolResult(f"Tool argument error: {e}", is_error=True)
                except Exception as e:
                    result = ToolResult(f"Tool failed: {type(e).__name__}: {e}", is_error=True)

                yield TurnEvent("tool_result", {
                    "name": name, "content": result.content,
                    "is_error": result.is_error,
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
                        f"Hit max tool-use iterations ({self.max_tool_iters}). "
                        "Possibly stuck in a loop; rephrase your question.")
        yield TurnEvent("done", None)


async def run_chat() -> None:
    """Launch the buddy REPL (importable for `financial-analyst chat`)."""
    from financial_analyst.buddy.repl import run_repl
    await run_repl()
