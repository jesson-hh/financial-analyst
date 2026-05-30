"""Skill registry — discovers deployed presets and builds the skill index
for injection into the Buddy agent's system prompt.

The index is re-built on every ``_build_system_prompt()`` call so newly
accepted presets take effect on the next agent session.

When the number of presets exceeds COMPACT_THRESHOLD (15), the system
prompt switches from inline descriptions to a compact name-only listing
+ ``lookup_skill`` tool for on-demand search.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

# Paths resolved relative to this file:
# registry.py → skill_gen/ → financial_analyst/ → src/ → <repo-root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "config" / "swarm"

# When preset count exceeds this, switch to compact mode (names only
# in system prompt + lookup_skill tool for on-demand search).
COMPACT_THRESHOLD = 15


def _load_all_presets(preset_dir: Optional[Path] = None) -> list[dict]:
    """Internal: parse all YAML presets, return list of metadata dicts."""
    pd = preset_dir or _PRESET_DIR
    if not pd.exists():
        return []
    result: list[dict] = []
    for yf in sorted(pd.glob("*.yaml")):
        try:
            spec = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        result.append({
            "name": spec.get("name") or yf.stem,
            "description": (spec.get("description") or "").strip(),
            "variables": spec.get("variables", []),
        })
    return result


# Chinese synonym map: common query terms → preset-name substrings.
# This lets users search in Chinese even when the preset name/description
# is primarily in English. Add entries as new presets are deployed.
_SYNONYMS: dict[str, list[str]] = {
    "晨会": ["morning-brief", "morning"],
    "早报": ["morning-brief", "morning"],
    "盘前": ["morning-brief", "morning"],
    "海外": ["overseas-radar", "overseas"],
    "美股": ["overseas-radar", "overseas"],
    "港股": ["overseas-radar", "overseas"],
    "主线": ["mainline-radar", "mainline"],
    "板块": ["mainline-radar", "mainline"],
    "轮动": ["mainline-radar", "mainline"],
    "深度": ["stock-deep-dive", "deep-dive"],
    "研报": ["stock-deep-dive", "deep-dive"],
    "个股": ["stock-deep-dive", "deep-dive"],
    "日内": ["intraday-review", "intraday"],
    "午盘": ["intraday-review", "intraday"],
    "午评": ["intraday-review", "intraday"],
    "盘中": ["intraday-review", "intraday"],
}


def search_presets(query: str, limit: int = 5,
                   preset_dir: Optional[Path] = None) -> list[dict]:
    """Keyword-search deployed presets. Returns ranked matches with
    name + description + variables, best match first.

    Includes Chinese synonym mapping so users can search with terms
    like "晨会", "美股", "深度" even when the preset metadata is in English.

    Scoring: exact name match = 100, name contains token = 30,
    description contains token = 10, synonym match = 40,
    repeated across tokens = additive.
    """
    if not query or not query.strip():
        return _load_all_presets(preset_dir)[:limit]

    raw_tokens = [t.strip().lower() for t in query.strip().split() if len(t.strip()) >= 1]
    if not raw_tokens:
        return _load_all_presets(preset_dir)[:limit]

    # Expand tokens with synonyms
    tokens: list[str] = list(raw_tokens)
    for tok in raw_tokens:
        for syn, expansions in _SYNONYMS.items():
            if tok in syn or syn in tok:
                tokens.extend(expansions)

    presets = _load_all_presets(preset_dir)
    scored: list[tuple[int, dict]] = []
    for p in presets:
        name_lower = p["name"].lower()
        desc_lower = p["description"].lower()
        score = 0
        for tok in tokens:
            if tok == name_lower:
                score += 100
            elif tok in name_lower:
                score += 30
            if tok in desc_lower:
                score += 10
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def build_skill_index(preset_dir: Optional[Path] = None) -> str:
    """Scan deployed presets and return a compact markdown string listing each
    preset with its one-line description.

    When preset count > COMPACT_THRESHOLD, returns a name-only listing
    (descriptions are available via ``lookup_skill`` tool).

    Returns ``""`` when no presets are deployed (nothing to inject).
    """
    presets = _load_all_presets(preset_dir)
    if not presets:
        return ""

    compact = len(presets) > COMPACT_THRESHOLD

    lines: list[str] = []
    for p in presets:
        if compact:
            lines.append(f"  - `{p['name']}`")
        else:
            lines.append(f"  - **{p['name']}**: {p['description']}")

    header = (
        "\n# 可用技能 / Available Preset Workflows\n"
        "预构建的多 Agent 分析工作流。"
    )
    if compact:
        header += (
            f"共 {len(presets)} 个技能（已超过直接展示阈值）。"
            "先用 `lookup_skill` 按关键词搜索匹配的技能，"
            "再用 `run_preset` 执行。\n"
            "技能列表（仅名称）：\n"
        )
    else:
        header += (
            "当用户问题匹配某个技能时，"
            "使用 `run_preset` 工具调用它（传入 preset 名称和需要的变量）。\n"
        )
    return header + "\n".join(lines) + "\n"


def list_presets(preset_dir: Optional[Path] = None) -> list[dict]:
    """Return metadata for every deployed preset (used by run_preset tool)."""
    return _load_all_presets(preset_dir)
