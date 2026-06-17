"""ask-agent: single-turn tool-using LLM dispatcher for ad-hoc questions."""
from __future__ import annotations
import json
from typing import Any, Dict, List
from financial_analyst.llm.client import LLMClient
from financial_analyst.ask.tools import TOOLS, TOOL_SCHEMAS
from financial_analyst.ask.schemas import AskOutput


SYSTEM_PROMPT = """You are the front-desk analyst for an A-share research workstation.

User asks a question in natural language. Decide:
1. Can I answer from past reports / memory / quick data lookups? → Use the provided tools.
2. Or does this require a full 13-agent deep-dive (slow, ~10 min)? → Set needs_full_report=true and suggested_code.

Available tools:
- list_past_reports() — recent research reports
- read_past_report(code, date_str?) — markdown content of a past report
- search_memory(query, agent?) — FTS5 across pitfalls/factor_insights/V1-V10/etc
- quick_quote(code) — fast latest quote (no LLM, no model)
- quick_factors(code) — 34 daily factors without LLM analysis
- list_dream_proposals() — staged memory proposals from /dream

Workflow:
1. Pick ONE OR MORE tools to call (you may call multiple in parallel via tool_calls).
2. Read the results.
3. Synthesize a concise markdown answer. Reference specific files/agents.

When user asks for a full deep-dive on a new stock or any "give me your analysis" question:
- Set needs_full_report=true, suggested_code=<code>, and answer="Recommend running /report <code> for a full 13-agent deep-dive."

NEVER fabricate data. If you can't find the answer with the tools, say so.

Output is JSON matching the AskOutput schema:
{
  "answer": "<markdown>",
  "actions_taken": ["<tool calls you made>"],
  "references": ["<files cited>"],
  "needs_full_report": false,
  "suggested_code": ""
}
"""


async def ask(query: str, llm_client: LLMClient = None) -> AskOutput:
    """Single-turn ask-agent. Calls tools, synthesizes an answer."""
    client = llm_client or LLMClient.for_agent("ask")

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    # Step 1: ask LLM to pick tools
    first_response = await client.chat(
        messages=messages,
        tools=TOOL_SCHEMAS,
        temperature=0.1,
    )
    msg = first_response["choices"][0]["message"]
    tool_calls = msg.get("tool_calls") or []
    actions_taken: List[str] = []
    references: List[str] = []
    tool_results: List[Dict[str, Any]] = []

    if tool_calls:
        # Step 2: execute each tool call
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except Exception:
                args = {}
            if name not in TOOLS:
                continue
            try:
                result = TOOLS[name](**args)
            except Exception as exc:
                result = {"error": str(exc)}
            actions_taken.append(f"{name}({args})")
            tool_results.append({"tool": name, "args": args, "result": result})

        # Step 3: re-prompt for the synthesized answer
        synth_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
            {
                "role": "user",
                "content": (
                    f"# Tool results\n```json\n{json.dumps(tool_results, default=str, ensure_ascii=False, indent=2)[:8000]}\n```\n\n"
                    "Now synthesize the answer. Return JSON per AskOutput schema."
                ),
            },
        ]
        final_response = await client.chat(
            messages=synth_messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = final_response["choices"][0]["message"].get("content", "{}")
    else:
        # No tool calls — model answered directly
        content = msg.get("content", "{}")

    # Parse the JSON envelope
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = {"answer": content, "needs_full_report": False}

    output = AskOutput(
        answer=parsed.get("answer", ""),
        actions_taken=actions_taken + parsed.get("actions_taken", []),
        references=parsed.get("references", []),
        needs_full_report=bool(parsed.get("needs_full_report", False)),
        suggested_code=str(parsed.get("suggested_code", "")),
    )
    return output
