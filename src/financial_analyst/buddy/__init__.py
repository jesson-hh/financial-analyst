"""Buddy — conversational front-end for financial-analyst.

Lets users drive the entire stack (reports / news / alpha bench /
chain-kb / etc.) via natural-language prompts. The LLM autonomously
picks tools, executes them, and chains follow-ups — Claude Code-style.

Entry point: ``financial-analyst chat`` (or ``financial-analyst buddy``).

Architecture:
    tools.py — registry of tool wrappers around our CLI helpers
    agent.py — conversational loop with LiteLLM tool-use
    repl.py  — prompt_toolkit REPL with Rich streaming output
"""
from financial_analyst.buddy.tools import (  # noqa: F401
    TOOL_REGISTRY, Tool, ToolResult, get_tool, list_tools,
)
from financial_analyst.buddy.agent import BuddyAgent, run_chat  # noqa: F401
