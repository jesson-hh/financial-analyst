"""LLM-driven skill code generation — meta-prompt fills template placeholders."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from financial_analyst.llm.client import LLMClient

from .schema import SkillProposal, SkillType
from .templates import AGENT_TEMPLATE, PRESET_TEMPLATE, TOOL_TEMPLATE
from .validator import validate_proposal


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _list_existing_agents() -> str:
    try:
        from financial_analyst.agent.registry import SubAgentRegistry
        return ", ".join(sorted(SubAgentRegistry.names()))
    except Exception:
        return "(unknown)"


def _list_existing_tools() -> str:
    try:
        from financial_analyst.buddy.tools import TOOL_REGISTRY
        return ", ".join(sorted(t.name for t in TOOL_REGISTRY))
    except Exception:
        return "(unknown)"


def _list_existing_presets() -> str:
    try:
        from pathlib import Path
        preset_dir = Path("config") / "swarm"
        if preset_dir.exists():
            return ", ".join(sorted(p.stem for p in preset_dir.glob("*.yaml")))
    except Exception:
        pass
    return "(unknown)"


META_PROMPT_TEMPLATE = """\
You are a code-generation assistant for the 觀瀾 (financial-analyst) A-share investment research system.

Your job: based on the user's description, generate a NEW capability (skill) for the system.

## Existing Capabilities (DO NOT duplicate)

### Registered Agents:
{existing_agents}

### Registered Tools:
{existing_tools}

### Swarm Presets:
{existing_presets}

## Skill Type Selection

First, decide which of these three skill types fits the request:

1. **agent** — a new analysis SubAgent (e.g. "biotech analyst" that understands FDA approval cycles).
   Agents are LLM-backed Python classes that take upstream data and return structured JSON.
2. **tool** — a new conversation tool for the buddy chat interface (e.g. "query convertible bonds").
   Tools are async Python functions that the LLM can call during conversation.
3. **preset** — a new workflow preset (swarm YAML) that chains existing agents into a DAG
   for a specific task (e.g. "high-dividend screening flow").

Choose the type that best matches the user's description.

## Template to Fill

Below is the code template for the chosen type. You MUST fill it EXACTLY — preserve
all imports, class structure, and function signatures. Only fill the placeholders
marked with {{curly braces}}.

{template}

## Output Format

Return a JSON object with these keys:

```json
{{
  "skill_type": "agent" | "tool" | "preset",
  "name": "kebab-case-identifier",
  "title": "Human-readable one-liner (Chinese OK)",
  "description": "What gap this fills and why it's needed",
  "confidence": "med",
  "placeholders": {{
    // All template placeholders filled in.
    // For agent: class_name, agent_name, output_schema_name, output_fields, system_prompt, execute_body
    // For tool: tool_name, title, run_params, run_body, description_cn, input_schema_json, cost_hint, confirm_required
    // For preset: preset_name, title, description, variables, agents
  }}
}}
```

RULES:
- output_fields must be valid Python Pydantic field definitions (indented 4 spaces, one per line)
- system_prompt must be a complete analyst persona prompt in Chinese, at least 30 lines
- execute_body must be a complete async method body (indented 8 spaces, calls LLMClient.for_agent, builds messages, calls client.chat, returns json.loads)
- For tools: cost_hint is one of "instant", "seconds", "minutes"; confirm_required is true/false
- For presets: variables is YAML-formatted (indented 2 spaces); agents is a YAML list of agent entries with name/deps/input_keys
- DO NOT invent agent names that don't exist in the registered list above (for preset deps)
- Generated code must be syntactically valid Python (agent/tool) or YAML (preset)

Return ONLY JSON. No markdown, no commentary."""


class SkillGenerator:
    def __init__(self, memory_root: Optional[Path] = None):
        self._memory_root = memory_root or Path("memories")
        self._client: Optional[LLMClient] = None

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient.for_agent("skill-generator")
        return self._client

    # ── context gathering ──────────────────────────────────────────────

    def _read_memories(self) -> str:
        """Read all permanent memories as context for generation."""
        parts: list[str] = []
        for agent_dir in sorted(self._memory_root.glob("*")):
            if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
                continue
            agent_md = agent_dir / "agent.md"
            if agent_md.exists():
                try:
                    parts.append(f"## {agent_dir.name}/agent.md\n{agent_md.read_text(encoding='utf-8')[:3000]}")
                except Exception:
                    pass
        return "\n\n".join(parts) if parts else "(no memories found)"

    def _read_recent_outcomes(self, since_days: int = 30) -> str:
        """Read recent outcome data — wrong/partial verdicts as gap evidence."""
        out_dir = Path("out")
        if not out_dir.exists():
            return "(no outcomes found)"
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        cutoff = _dt.now(_tz.utc).timestamp() - since_days * 86400
        wrong_cases: list[str] = []
        for f in sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            if f.stat().st_mtime < cutoff:
                continue
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                verdict = data.get("verdict", "")
                if verdict in ("wrong", "partial"):
                    code = data.get("code", f.stem)
                    action = data.get("action", "?")
                    wrong_cases.append(
                        f"  {f.stem}: action={action} verdict={verdict} "
                        f"ret_5d={data.get('return_t5d', '?')} "
                        f"rating={data.get('rating_overall', '?')}"
                    )
            except Exception:
                pass
        if not wrong_cases:
            return "(no wrong/partial outcomes in recent window)"
        return f"Recent wrong/partial outcomes ({len(wrong_cases)} cases):\n" + "\n".join(wrong_cases[:30])

    # ── template selectors ─────────────────────────────────────────────

    def _select_template(self, skill_type: SkillType) -> str:
        if skill_type == SkillType.AGENT:
            return AGENT_TEMPLATE
        if skill_type == SkillType.TOOL:
            return TOOL_TEMPLATE
        if skill_type == SkillType.PRESET:
            return PRESET_TEMPLATE
        raise ValueError(f"Unknown skill type: {skill_type}")

    def _build_meta_prompt(self, skill_type: SkillType, extra_context: str = "") -> str:
        template = self._select_template(skill_type)
        prompt = META_PROMPT_TEMPLATE.format(
            existing_agents=_list_existing_agents(),
            existing_tools=_list_existing_tools(),
            existing_presets=_list_existing_presets(),
            template=template,
        )
        if extra_context:
            prompt += f"\n\n## System Context (memories, outcomes, gaps)\n{extra_context}"
        return prompt

    # ── main generation entry points ───────────────────────────────────

    async def generate_from_gap(
        self,
        gap_description: str,
        skill_type: SkillType,
        evidence: Optional[list[str]] = None,
        suggested_name: str = "",
        since_days: int = 30,
    ) -> SkillProposal:
        """Generate a skill from a capability gap detected by the introspector.

        Reads memories + recent outcomes to provide rich context, so the
        generated skill is tailored to the system's actual weaknesses.
        """
        memories = self._read_memories()
        outcomes = self._read_recent_outcomes(since_days)

        context = (
            f"### Why this skill is needed (capability gap)\n{gap_description}\n\n"
        )
        if evidence:
            context += f"### Evidence from outcomes\n" + "\n".join(f"- {e}" for e in evidence) + "\n\n"
        context += f"### Current System Memories\n{memories}\n\n"
        context += f"### Recent Outcome Weaknesses\n{outcomes}"

        meta_prompt = self._build_meta_prompt(skill_type, extra_context=context)

        name_hint = f' (suggested name: {suggested_name})' if suggested_name else ''
        user_prompt = (
            f"Generate a new {skill_type.value} skill to fill this capability gap:\n"
            f"  {gap_description}{name_hint}\n\n"
            "Use the System Context above to understand existing capabilities and "
            "recent weaknesses. The generated skill should specifically address "
            "the gap described. Do NOT duplicate existing agents/tools/presets."
        )

        messages = [
            {"role": "system", "content": meta_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await self.client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])
        return self._parse_response(raw, skill_type, gap_description, evidence or [], "dream_loop")

    async def generate(
        self,
        description: str,
        skill_type: Optional[SkillType] = None,
        supporting_cases: Optional[list[str]] = None,
    ) -> SkillProposal:
        if skill_type is None:
            skill_type = await self._detect_type(description)

        meta_prompt = self._build_meta_prompt(skill_type)
        user_prompt = f"Generate a new {skill_type.value} skill for: {description}"

        messages = [
            {"role": "system", "content": meta_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await self.client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])
        return self._parse_response(
            raw, skill_type, description,
            supporting_cases or [description],
            "user_cli",
        )

    def _parse_response(
        self,
        raw: dict,
        skill_type: SkillType,
        description: str,
        supporting_cases: list[str],
        trigger_source: str,
    ) -> SkillProposal:
        placeholders = raw.get("placeholders", {})
        code = self._fill_template(skill_type, placeholders)
        name = raw.get("name", "unnamed")

        proposal = SkillProposal(
            skill_type=skill_type,
            name=name,
            title=raw.get("title", description[:80]),
            description=raw.get("description", description),
            generated_code=code,
            confidence=raw.get("confidence", "med"),
            trigger_source=trigger_source,
            supporting_cases=supporting_cases,
            created_at=_now_iso(),
        )

        errors = validate_proposal(proposal)
        if errors:
            proposal.confidence = "low"
            proposal.description += f"\n\n⚠ Validation warnings:\n- " + "\n- ".join(errors)

        return proposal

    async def _detect_type(self, description: str) -> SkillType:
        meta_prompt = (
            "You are a skill classifier for 觀瀾 (financial-analyst). "
            "Given a user's description, decide which skill type to generate.\n\n"
            f"Existing agents: {_list_existing_agents()}\n"
            f"Existing tools: {_list_existing_tools()}\n"
            f"Existing presets: {_list_existing_presets()}\n\n"
            "Rules:\n"
            "- If the user wants a new ANALYSIS PERSPECTIVE or special-domain research → agent\n"
            "- If the user wants to QUERY DATA or perform an ACTION in chat → tool\n"
            "- If the user wants to COMPOSE existing agents into a workflow → preset\n"
            "- If the user wants something entirely new that needs both a new agent AND a tool → agent\n\n"
            "Return JSON: {\"skill_type\": \"agent\"|\"tool\"|\"preset\", \"reason\": \"...\"}"
        )
        messages = [
            {"role": "system", "content": meta_prompt},
            {"role": "user", "content": f"Classify: {description}"},
        ]
        response = await self.client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])
        st = raw.get("skill_type", "tool")
        try:
            return SkillType(st)
        except ValueError:
            return SkillType.TOOL

    def _fill_template(self, skill_type: SkillType, placeholders: dict) -> str:
        if skill_type == SkillType.AGENT:
            return AGENT_TEMPLATE.format(
                class_name=placeholders.get("class_name", "CustomAgent"),
                agent_name=placeholders.get("agent_name", "custom-agent"),
                output_schema_name=placeholders.get("output_schema_name", "CustomOutput"),
                output_fields=placeholders.get("output_fields", "    score: int = 0"),
                system_prompt=placeholders.get("system_prompt", "You are a custom analyst."),
                title=placeholders.get("title", "Custom Agent"),
                execute_body=placeholders.get("execute_body", "        return {}"),
            )
        if skill_type == SkillType.TOOL:
            return TOOL_TEMPLATE.format(
                tool_name=placeholders.get("tool_name", "custom_tool"),
                title=placeholders.get("title", "Custom Tool"),
                run_params=placeholders.get("run_params", ""),
                run_body=placeholders.get("run_body", "    return ToolResult(content='ok')"),
                description_cn=placeholders.get("description_cn", "Custom tool."),
                input_schema_json=placeholders.get("input_schema_json", "{}"),
                cost_hint=placeholders.get("cost_hint", "instant"),
                confirm_required=placeholders.get("confirm_required", "False"),
            )
        if skill_type == SkillType.PRESET:
            return PRESET_TEMPLATE.format(
                preset_name=placeholders.get("preset_name", "custom-preset"),
                title=placeholders.get("title", "Custom Preset"),
                description=placeholders.get("description", "Auto-generated preset."),
                variables=placeholders.get("variables", "  - name: code\n    type: string\n    required: true"),
                agents=placeholders.get("agents", "  - name: quote-fetcher\n    deps: []\n    input_keys: [code]"),
            )
        raise ValueError(f"Unknown skill type: {skill_type}")

    # ── patch skill ────────────────────────────────────────────────────

    async def patch_skill(
        self,
        skill_type: SkillType,
        skill_name: str,
        current_code: str,
        patch_description: str,
    ) -> str:
        """Patch an existing skill's code based on a description of what to change.

        Uses an LLM to produce an updated version of the code that incorporates
        the requested changes while preserving the overall structure.
        """
        meta_prompt = self._build_patch_prompt(skill_type, skill_name, current_code)
        user_prompt = (
            f"Apply this change to the {skill_type.value} '{skill_name}':\n"
            f"{patch_description}\n\n"
            "Return the COMPLETE updated code, not just a diff."
        )
        messages = [
            {"role": "system", "content": meta_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await self.client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])
        return raw.get("patched_code", current_code)

    def _build_patch_prompt(
        self,
        skill_type: SkillType,
        skill_name: str,
        current_code: str,
    ) -> str:
        return PATCH_PROMPT_TEMPLATE.format(
            skill_type=skill_type.value,
            skill_name=skill_name,
            current_code=current_code,
            existing_agents=_list_existing_agents(),
            existing_tools=_list_existing_tools(),
            existing_presets=_list_existing_presets(),
        )


PATCH_PROMPT_TEMPLATE = """\
You are a code-maintenance assistant for 觀瀾 (financial-analyst).

Your job: apply a specific change to an existing {skill_type} named '{skill_name}'.

## Existing Capabilities (for reference — do NOT duplicate)

### Registered Agents:
{existing_agents}

### Registered Tools:
{existing_tools}

### Swarm Presets:
{existing_presets}

## Current Code

```python
{current_code}
```

## Output Format

Return a JSON object:

```json
{{
  "patched_code": "<COMPLETE updated code>",
  "changes_summary": "One-line summary of what changed"
}}
```

RULES:
- Return the COMPLETE updated code, not a diff.
- Preserve all imports, class structure, and function signatures.
- Only change what's needed to address the patch description.
- The patched code must be syntactically valid Python (or YAML for presets).
- Do NOT add new capabilities beyond what the patch requests.
- Return ONLY JSON. No markdown, no commentary."""
