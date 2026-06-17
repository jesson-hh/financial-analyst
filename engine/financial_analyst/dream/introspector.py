"""Introspector sub-agent: reads outcomes + memories, proposes memory updates.

NOT in the stock-deep-dive preset. Run via `financial-analyst dream` or TUI `/dream`.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List
from pydantic import BaseModel, Field
from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class Proposal(BaseModel):
    target_agent: str
    topic_slug: str
    title: str
    lesson_md: str
    confidence: str = Field(default="low", pattern="^(low|med|high)$")
    supporting_cases: List[str] = []
    reasoning: str = ""


class IntrospectionOutput(BaseModel):
    proposals: List[Proposal] = []
    summary: str = ""


SYSTEM_PROMPT = """You are an A-share research desk's introspector — a post-mortem analyst.

You receive:
- A list of historical predictions + their measured outcomes (correct / wrong / partial)
- The current agent memories (rules, pitfalls, factor insights)
- The introspector_rules.md (meta-rules for finding patterns)

Your job: find 1-3 systematic biases in WRONG or PARTIAL outcomes, and propose memory updates.

CRITICAL discipline:
1. Focus on WRONG verdicts. Hits are confirmations, misses teach you.
2. Need 3+ supporting cases for confidence=med, 6+ for high. Otherwise low.
3. The proposed rule must be ACTIONABLE — specify the agent + the trigger + the action.
4. DO NOT propose rules that contradict existing memory without explicit justification.
5. Each proposal targets ONE agent from: fundamental-analyst, technical-analyst, whale-analyst, quant-analyst, bull-advocate, bear-advocate, risk-officer, report-writer.
6. Output JSON only matching IntrospectionOutput schema.
7. If outcomes are too few (<3 wrong cases) → return empty proposals list with summary "insufficient outcomes for proposals".

The `lesson_md` field should be a markdown document following this template:
```
# <Title>

## Why this rule exists
<3-5 cases that show the pattern, with dates + outcomes>

## The rule
<concrete trigger condition + action>

## How to apply
<when this fires, what should change in the agent's reasoning>
```

Return JSON ONLY. No prose, no commentary outside the JSON envelope.
"""


class Introspector(SubAgent[IntrospectionOutput]):
    NAME = "introspector"
    OUTPUT_SCHEMA = IntrospectionOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        outcomes = inputs.get("outcomes", [])
        outcomes_serialized = json.dumps(outcomes, default=str, ensure_ascii=False, indent=2)

        client = LLMClient.for_agent(self.NAME)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# Memory\n" + self.memory.load_all()},
            {"role": "user", "content": (
                f"# Outcomes ({len(outcomes)} records)\n```json\n{outcomes_serialized}\n```\n\n"
                "Find systematic biases. Return JSON per schema."
            )},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return json.loads(response["choices"][0]["message"]["content"])
