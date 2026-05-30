"""Autonomous skill generation — auto-create agents, tools, and swarm presets.

Hermes-style background review + curator lifecycle. Two modes:

  - ``auto``:   skills are created/deployed automatically after review
  - ``manual``: proposals go to _proposed/ for human approval

Usage::

    from financial_analyst.skill_gen import (
        SkillGenerator, SkillProposal, SkillType,
        BackgroundSkillReviewer, Curator,
        list_proposals, accept_proposal, reject_proposal,
        save_proposal, load_proposal, auto_accept_proposal,
        get_skill_mode, set_skill_mode,
    )

    # Manual generation (user-triggered)
    gen = SkillGenerator()
    proposal = await gen.generate("可转债分析工具")
    save_proposal(proposal)

    # Background review (Hermes-style, after every N turns)
    reviewer = BackgroundSkillReviewer(mode="auto")
    result = await reviewer.review(conversation_snapshot="...")

    # Curator (periodic maintenance)
    curator = Curator()
    summary = await curator.run_review()
"""

from .schema import SkillLifecycleState, SkillProposal, SkillType
from .generator import SkillGenerator
from .review import BackgroundSkillReviewer
from .curator import Curator
from .lifecycle import (
    accept_proposal,
    auto_accept_proposal,
    get_skill_mode,
    get_skills_root,
    list_active_skills,
    list_proposals,
    load_proposal,
    record_skill_patch,
    record_skill_usage,
    reject_proposal,
    save_proposal,
    set_skill_mode,
)
from .registry import build_skill_index, list_presets, search_presets
from .validator import validate_proposal

__all__ = [
    # Core
    "SkillGenerator",
    "SkillProposal",
    "SkillType",
    "SkillLifecycleState",
    # Hermes-style review
    "BackgroundSkillReviewer",
    "Curator",
    # Lifecycle
    "accept_proposal",
    "auto_accept_proposal",
    "get_skill_mode",
    "get_skills_root",
    "list_active_skills",
    "list_proposals",
    "load_proposal",
    "record_skill_patch",
    "record_skill_usage",
    "reject_proposal",
    "save_proposal",
    "set_skill_mode",
    "validate_proposal",
    # Registry
    "build_skill_index",
    "list_presets",
    "search_presets",
]
