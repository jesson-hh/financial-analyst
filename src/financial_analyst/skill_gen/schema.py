"""SkillProposal dataclass + SkillType enum — mirrors memories/_proposed pattern.

Hermes-style: every skill tracks its provenance (who created it), lifecycle
state (active/stale/archived), and can be pinned to exempt from curator aging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    PRESET = "preset"


class SkillLifecycleState(str, Enum):
    """Curator-managed lifecycle states (Hermes pattern)."""
    ACTIVE = "active"        # recently used or created
    STALE = "stale"          # unused for > stale_after_days
    ARCHIVED = "archived"    # stale for > archive_after_days → moved to .archive/


@dataclass
class SkillProposal:
    skill_type: SkillType
    name: str
    title: str
    description: str
    generated_code: str
    target_path: str = ""
    confidence: str = "med"
    trigger_source: str = "user_cli"       # "user_cli" | "dream_loop" | "background_review"
    supporting_cases: list[str] = field(default_factory=list)
    created_at: str = ""

    # ── Hermes-style provenance + lifecycle ──
    created_by: str = "user"               # "user" | "agent"
    lifecycle_state: str = "active"        # "active" | "stale" | "archived"
    last_activity_at: str = ""             # ISO timestamp of last use/patch
    pinned: bool = False                   # if True, curator skips this skill
