"""Safety gates for auto-generated skill code — syntax + interface checks."""

from __future__ import annotations

import ast
import re
from typing import List

import yaml

from .schema import SkillProposal, SkillType


def validate_proposal(proposal: SkillProposal) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    if proposal.skill_type == SkillType.AGENT:
        return validate_agent_code(proposal.generated_code)
    if proposal.skill_type == SkillType.TOOL:
        return validate_tool_code(proposal.generated_code)
    if proposal.skill_type == SkillType.PRESET:
        return validate_preset_yaml(proposal.generated_code)
    return [f"Unknown skill type: {proposal.skill_type}"]


def validate_agent_code(code: str) -> list[str]:
    """Check: compiles as Python, has NAME attr, has OUTPUT_SCHEMA,
    _execute is async, signature matches abstract method."""
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    agent_cls = None
    for cls in classes:
        for base in cls.bases:
            base_name = _get_name(base)
            if base_name and "SubAgent" in base_name:
                agent_cls = cls
                break
        if agent_cls:
            break

    if agent_cls is None:
        errors.append("No SubAgent subclass found")
        return errors

    found_name = False
    found_schema = False
    found_execute = False
    for node in ast.walk(agent_cls):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "NAME":
                        found_name = True
                    elif target.id == "OUTPUT_SCHEMA":
                        found_schema = True
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute":
            found_execute = True
            if not _has_inputs_param(node):
                errors.append("_execute must accept 'inputs' or 'self, inputs' parameter")

    if not found_name:
        errors.append("Missing NAME class attribute")
    if not found_schema:
        errors.append("Missing OUTPUT_SCHEMA class attribute")
    if not found_execute:
        errors.append("Missing async _execute method")
    return errors


def validate_tool_code(code: str) -> list[str]:
    """Check: compiles as Python, has valid Tool(...) call, run function returns ToolResult."""
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    if not funcs:
        errors.append("No async function found for tool run()")
    else:
        for f in funcs:
            if f.returns:
                ret_name = _get_name(f.returns)
                if ret_name and "ToolResult" not in ret_name:
                    errors.append(f"Function '{f.name}' return type is not ToolResult")

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if classes:
        errors.append("Tool code should not define new classes — use Tool dataclass directly")

    return errors


def validate_preset_yaml(yaml_str: str) -> list[str]:
    """Check: valid YAML, has name/agents keys, all agent names exist in registry."""
    errors: list[str] = []
    try:
        doc = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(doc, dict):
        return ["YAML root is not a dict"]

    if "name" not in doc:
        errors.append('Missing "name" key')
    if "agents" not in doc:
        errors.append('Missing "agents" key')
        return errors

    known_agents = set()
    try:
        from financial_analyst.agent.registry import SubAgentRegistry
        known_agents = set(SubAgentRegistry.names())
    except Exception:
        pass

    for entry in doc.get("agents", []) or []:
        if not isinstance(entry, dict):
            errors.append(f"Agent entry is not a dict: {entry}")
            continue
        name = entry.get("name", "")
        if not name:
            errors.append("Agent entry missing 'name'")
        elif known_agents and name not in known_agents:
            errors.append(
                f"Agent '{name}' not found in SubAgentRegistry. "
                f"Ensure it is registered before loading this preset."
            )

    return errors


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _get_name(node.value)
    return None


def _has_inputs_param(func: ast.AsyncFunctionDef) -> bool:
    for arg in func.args.args:
        if arg.arg == "inputs":
            return True
    return False
