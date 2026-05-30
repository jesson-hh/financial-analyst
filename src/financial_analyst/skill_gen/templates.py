"""Code templates for auto-generated agents, tools, and swarm presets.

Each template uses Python str.format() placeholders. The LLM fills them in
via structured JSON output from the meta-prompt in generator.py.
"""

# ---------------------------------------------------------------------------
# Agent template — produces a complete SubAgent subclass
# ---------------------------------------------------------------------------
AGENT_TEMPLATE = '''\
"""Auto-generated agent: {agent_name} — {title}."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class {output_schema_name}(BaseModel):
{output_fields}


SYSTEM_PROMPT = """\\
{system_prompt}"""


class {class_name}(SubAgent[{output_schema_name}]):
    NAME = "{agent_name}"
    OUTPUT_SCHEMA = {output_schema_name}

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)
{execute_body}
'''

# ---------------------------------------------------------------------------
# Tool template — produces a Tool() call + run function
# ---------------------------------------------------------------------------
TOOL_TEMPLATE = '''\
"""Auto-generated tool: {tool_name} — {title}."""

import json
from typing import Any, Dict

from financial_analyst.buddy.tools import Tool, ToolResult, get_tool


async def _tool_{tool_name}({run_params}) -> ToolResult:
    """{title}."""
{run_body}


def register() -> Tool:
    return Tool(
        name="{tool_name}",
        description="{description_cn}",
        input_schema={input_schema_json},
        run=_tool_{tool_name},
        cost_hint="{cost_hint}",
        confirm_required={confirm_required},
    )
'''

# ---------------------------------------------------------------------------
# Swarm preset template — produces config/swarm/<name>.yaml
# ---------------------------------------------------------------------------
PRESET_TEMPLATE = """\
# Auto-generated preset: {preset_name}
# {title}
name: {preset_name}
description: {description}
variables:
{variables}
agents:
{agents}
"""
